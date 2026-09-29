# -*- coding: utf-8 -*-
"""Screening dispatch orkestrasyonu (plan §7.1-7.2).

Sözleşme:
- Her assignment dispatch'i BİR `ChannelAssignmentAttempt` satırı yaratır
  (off dahil): parent task'ın kimliği, modu ve onaylı cap'leri orada
  denetlenebilir kalır. Off attempt'inin cap'leri NULL'dur — ledger
  harcaması yalnız cap'i onaylanmış attempt'lerde mümkündür.
- Shadow/assistive'de attempt ile `CorpusScreeningJob` AYNI transaction'da
  yaratılır ve birbirine bağlanır; hiçbir job sahipsiz doğmaz.
- Evren, bağlam ve sözleşme job'a MÜHÜRLENİR; worker canlı tablo okumaz.
- Celery teslimi commit SONRASI yapılır. Shadow'da teslim hatası
  assignment'ı DÜŞÜRMEZ (audit işi), yalnız job `failed` olur.
- Faz 1 kapsamı SHADOW'dur; assistive dispatch (parent'ın taramayı
  beklemesi + callback CAS'i) açılana kadar tipli olarak REDDEDİLİR —
  sessizce shadow'a düşülmez.
"""
from __future__ import annotations

import uuid
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, Optional, Tuple

from loguru import logger

from app.core.screening.attempt_state import (
    active_attempt,
    reconcile_stale_attempts,
    try_finish_attempt,
)
from app.core.screening.candidate_union import (
    PRODUCTION_SCREENING_CONTRACT,
    SCREENING_APPLIED_CHANNELS_V3,
)
from app.core.screening.identity import screening_runner_contract
from app.core.screening.preflight import (
    MODE_ASSISTIVE,
    MODE_MULTIPLIERS,
    MODE_OFF,
    MODE_SHADOW,
    PreflightError,
    build_assignment_preflight,
    resolve_screening_mode,
)
from app.database.models import (
    ChannelAssignmentAttempt,
    CorpusScreeningJob,
    TaskResult,
)

SCREENING_TASK_TYPE = "corpus_screening"


def create_attempt(db, run, *, parent_task_id: str, mode: str,
                   relevance_coefficient: Optional[float],
                   preflight: Optional[Dict[str, Any]] = None,
                   requested_policy_version: Optional[int] = None,
                   requested_anchor_version: Optional[int] = None,
                   requested_strategy_version: Optional[int] = None,
                   manifest: Optional[Dict[str, Any]] = None
                   ) -> ChannelAssignmentAttempt:
    """Attempt satırı — COMMIT ETMEZ (çağıranın transaction'ında)."""
    applied, counterfactual = MODE_MULTIPLIERS[mode]
    attempt = ChannelAssignmentAttempt(
        parent_task_id=parent_task_id,
        scoring_run_id=run.id,
        brand_profile_id=run.brand_profile_id,
        status="pending",
        phase="screening" if mode != MODE_OFF else "assignment",
        screening_mode=mode,
        applied_candidate_multiplier=applied,
        counterfactual_target_multiplier=counterfactual,
        relevance_coefficient=relevance_coefficient,
        applied_screening_channels=(list(SCREENING_APPLIED_CHANNELS_V3)
                                    if mode != MODE_OFF else None),
        requested_policy_version=requested_policy_version,
        requested_anchor_version=requested_anchor_version,
        requested_strategy_version=requested_strategy_version,
        requested_assignment_version=getattr(
            run, "channel_assignment_version", None),
        manifest=manifest,
        assignment_task_id=parent_task_id,
        assignment_dispatch_state="pending",
    )
    if preflight is not None and mode != MODE_OFF:
        # Muhurlu kapsam = preflight'in hesapladigi AKTIF kapsam
        attempt.applied_screening_channels = list(
            preflight.get("applied_screening_channels")
            or SCREENING_APPLIED_CHANNELS_V3)
    if preflight is not None:
        # Cap OTORİTESİ bu satırdır; ledger DB'den okur ve beklenen
        # değerle karşılaştırır (onaydan sonra değişirse harcama durur)
        attempt.preflight_sha256 = preflight["preflight_sha256"]
        attempt.approved_screening_cap_usd = preflight[
            "screening_hard_cap_usd"]
        attempt.approved_downstream_cap_usd = preflight[
            "downstream_hard_cap_usd"]
        attempt.context_sha256 = preflight["context_sha256"]
        # Codex 10. tur #5: counterfactual'ın okuyacağı rank/relevance
        # mührü dispatch ANINDA sabitlenir (parent paralel tazeleme yapsa
        # bile ölçüm zamanlamaya göre değişemez)
        attempt.channel_rank_snapshot_sha256 = preflight[
            "channel_rank_snapshot_sha256"]
        attempt.relevance_rows_sha256 = preflight["relevance_rows_sha256"]
    db.add(attempt)
    db.flush()
    return attempt


def find_reusable_job(db, *, identity_sha256: str
                      ) -> Optional[CorpusScreeningJob]:
    """AYNI kimlikte DOGRULANMIS completed job (plan §5.6 reuse anahtari).

    `uq_screening_job_completed_identity` zaten ayni kimlikte tek completed
    satira izin verir; reuse UYGULANMAZSA sistem ayni taramayi yeniden
    ucretle kosup finalize'da bu indekse carpar (canli UI kosusunda
    olculdu). Reuse: saglayici cagrisi YOK, ucret YOK.
    """
    return (db.query(CorpusScreeningJob)
            .filter(CorpusScreeningJob.screening_input_identity_sha256
                    == identity_sha256,
                    CorpusScreeningJob.status == "completed")
            .order_by(CorpusScreeningJob.id.desc())
            .first())


def create_screening_job(db, run, *, attempt: ChannelAssignmentAttempt,
                         preflight: Dict[str, Any],
                         parent_task_id: str) -> CorpusScreeningJob:
    """Mühürlü job satırı — COMMIT ETMEZ; attempt'e bağlanır."""
    contract = PRODUCTION_SCREENING_CONTRACT
    job = CorpusScreeningJob(
        scoring_run_id=run.id,
        brand_profile_id=run.brand_profile_id,
        created_by_parent_task_id=parent_task_id,
        status="pending",
        provider=contract["provider"],
        model=contract["model"],
        prompt_version=contract["prompt_version"],
        temperature=contract["temperature"],
        batch_size=int(contract["batch_size"]),
        view_salts=list(contract["view_salts"]),
        applied_screening_channels=list(
            preflight.get("applied_screening_channels")
            or SCREENING_APPLIED_CHANNELS_V3),
        screening_input_identity_sha256=preflight[
            "screening_input_identity_sha256"],
        universe_sha256=preflight["universe_sha256"],
        context_sha256=preflight["context_sha256"],
        input_snapshot={"rows": preflight["universe_rows"]},
        screening_context={"fields": dict(preflight["context"]["fields"])},
        runner_contract=screening_runner_contract(),
        strategy_fingerprint=preflight.get("strategy_fingerprint"),
        dispatch_policy_version=preflight.get("policy_version"),
        dispatch_anchor_version=preflight.get("anchor_version"),
        dispatch_strategy_version=preflight.get("strategy_version"),
        planned_requests=int(preflight["planned_screening_requests"]),
    )
    db.add(job)
    db.flush()
    attempt.screening_job_id = job.id
    return job


def plan_dispatch(db, run, workspace, *, parent_task_id: str,
                  relevance_coefficient: Optional[float] = None,
                  requested_policy_version: Optional[int] = None,
                  requested_anchor_version: Optional[int] = None,
                  requested_strategy_version: Optional[int] = None,
                  manifest: Optional[Dict[str, Any]] = None,
                  approved_screening_mode: Optional[str] = None,
                  approved_preflight_sha256: Optional[str] = None,
                  approved_screening_hard_cap_usd: Optional[float] = None,
                  forced_mode: Optional[str] = None,
                  settings=None
                  ) -> Tuple[ChannelAssignmentAttempt,
                             Optional[CorpusScreeningJob],
                             Optional[Dict[str, Any]]]:
    """Attempt (+shadow'da job) üretir; COMMIT ETMEZ.

    Çağıran workspace ve run satır kilitlerini ZATEN almış olmalıdır
    (plan §7.1 kilit sırası: workspace → scoring_run).

    Ücretli tarama YALNIZ kullanıcının gördüğü preflight ONAYIYLA başlar
    (Codex 10. tur #4): `approved_screening_mode` + `approved_preflight_
    sha256` sunucuda yeniden hesaplanan paketle karşılaştırılır.
    """
    from app.config import settings as default_settings

    settings = settings or default_settings
    # Sahipsiz kalmış aktif attempt aktif-attempt unique'ini kilitler
    reconcile_stale_attempts(db, run.id)
    blocking = active_attempt(db, run.id)
    if blocking is not None:
        raise PreflightError(
            "ATTEMPT_ALREADY_ACTIVE",
            f"run {run.id} için aktif atama denemesi var "
            f"(attempt {blocking.id}, {blocking.status}) — ikinci dispatch "
            f"açılmaz")
    # Sunucu kontrollu akis: modu `auto_trigger` belirler (kullanici
    # tercihi degil). Operator onayi verilmisse o otoritedir.
    mode = forced_mode or resolve_screening_mode(run, settings)
    if approved_screening_mode is not None:
        # Kullanıcının ONAYLADIĞI mod otoritedir; run tercihi arada
        # değişmişse sessizce başka bir moda geçilmez
        if approved_screening_mode not in (MODE_OFF, MODE_SHADOW,
                                           MODE_ASSISTIVE):
            raise PreflightError("SCREENING_MODE_INVALID",
                                 f"geçersiz mod: {approved_screening_mode!r}")
        mode = approved_screening_mode
    if mode == MODE_ASSISTIVE:
        # Assistive canli secimi DEGISTIRIR: yalniz bayrak acik +
        # allowlist'li workspace'te acilir (fail-closed). Sessizce
        # shadow'a DUSULMEZ; sebep tipli doner ve cagiran baseline'a
        # dusuruleceginde denetime yazar.
        from app.core.screening.auto_trigger import assistive_allowed

        if not assistive_allowed(workspace, settings):
            raise PreflightError(
                "ASSISTIVE_NOT_ENABLED",
                "assistive yalnizca allowlist'li workspace'te acilir")
    if mode == MODE_OFF:
        attempt = create_attempt(
            db, run, parent_task_id=parent_task_id, mode=MODE_OFF,
            relevance_coefficient=relevance_coefficient,
            requested_policy_version=requested_policy_version,
            requested_anchor_version=requested_anchor_version,
            requested_strategy_version=requested_strategy_version,
            manifest=manifest)
        return attempt, None, None

    preflight = build_assignment_preflight(
        db, run, workspace, mode=mode,
        relevance_coefficient=relevance_coefficient, settings=settings)
    # SUNUCU KONTROLLU AKIS: kullanici onayi YOKTUR — sinir yonetici
    # cap'leridir ve preflight icinde UYGULANIR (SCREENING_CAP_EXCEEDS_
    # LIMIT / DOWNSTREAM_ESTIMATE_EXCEEDS_LIMIT). Operator acikca bir mod
    # ya da SHA gonderdiyse eski onay sozlesmesi aynen gecerlidir.
    auto_dispatch = (approved_screening_mode is None
                     and approved_preflight_sha256 is None
                     and approved_screening_hard_cap_usd is None)
    if not auto_dispatch:
        _verify_approval(preflight, sha=approved_preflight_sha256,
                         screening_cap=approved_screening_hard_cap_usd)
    attempt = create_attempt(
        db, run, parent_task_id=parent_task_id, mode=mode,
        relevance_coefficient=relevance_coefficient, preflight=preflight,
        requested_policy_version=requested_policy_version,
        requested_anchor_version=requested_anchor_version,
        requested_strategy_version=requested_strategy_version,
        manifest=manifest)
    reusable = find_reusable_job(
        db, identity_sha256=preflight["screening_input_identity_sha256"])
    if reusable is not None:
        # UCRETSIZ REUSE: yeni job ACILMAZ, cocuk task KUYRUGA VERILMEZ,
        # saglayiciya HIC gidilmez. Attempt mevcut kararlara baglanir.
        attempt.screening_job_id = reusable.id
        logger.info(
            f"screening reuse: run {run.id} attempt {attempt.id} -> job "
            f"{reusable.id} (kimlik "
            f"{preflight['screening_input_identity_sha256'][:12]}...)")
        return attempt, None, {**preflight, "reused_screening_job_id":
                               reusable.id}
    job = create_screening_job(db, run, attempt=attempt,
                               preflight=preflight,
                               parent_task_id=parent_task_id)
    return attempt, job, preflight


def _verify_approval(preflight: Dict[str, Any], *, sha: Optional[str],
                     screening_cap: Optional[float]) -> None:
    """Onay ile sunucunun hesabı BİREBİR uyuşmalı (Codex 10/15. tur).

    Onay YALNIZ ledger'ın gerçekten uyguladığı screening cap'ine bağlanır
    (`enforced_hard_cap_usd`). `combined_exposure_usd` bilgi amaçlıdır:
    downstream Gemini hattı ledger'a bağlanana kadar kullanıcıya
    "onayladığın tavan" olarak sunulamaz.
    """
    if not sha:
        raise PreflightError(
            "PREFLIGHT_APPROVAL_REQUIRED",
            "ücretli tarama için preflight onayı (preflight_sha256) "
            "gönderilmedi — kullanıcı maliyeti görmeden başlatılmaz")
    if sha != preflight["preflight_sha256"]:
        raise PreflightError(
            "PREFLIGHT_MISMATCH",
            f"onaylanan preflight {sha[:12]}... güncel hesapla "
            f"({preflight['preflight_sha256'][:12]}...) uyuşmuyor — "
            f"girdiler değişti, yeni onay gerekir")
    # Codex 11. tur #5: cap ZORUNLU — SHA doğru ama kullanıcının gördüğü
    # tutar taşınmıyorsa "neyi onayladı" sorusu cevapsız kalır
    if screening_cap is None:
        raise PreflightError(
            "PREFLIGHT_CAP_REQUIRED",
            "onaylanan screening cap'i (approved_screening_hard_cap_usd) "
            "gönderilmedi — maliyet onayı eksik")
    approved = Decimal(str(screening_cap)).quantize(Decimal("0.000001"),
                                                    rounding=ROUND_HALF_UP)
    computed = Decimal(str(preflight["enforced_hard_cap_usd"])).quantize(
        Decimal("0.000001"), rounding=ROUND_HALF_UP)
    if approved != computed:
        raise PreflightError(
            "PREFLIGHT_CAP_MISMATCH",
            f"onaylanan screening cap'i ${approved} != hesaplanan "
            f"${computed}")


def enqueue_screening_child(db, *, attempt: ChannelAssignmentAttempt,
                            job: CorpusScreeningJob,
                            deferred_parent: Optional[Dict[str, Any]] = None
                            ) -> Optional[str]:
    """Commit SONRASI çocuğu kuyruğa verir (shadow: non-blocking).

    Teslim hatası shadow'da assignment'ı DÜŞÜRMEZ; job tipli `failed`
    olur ve canlı havuz etkilenmez (plan §7.2).
    """
    from app.tasks.screening_tasks import run_corpus_screening_task

    task_id = str(uuid.uuid4())
    # Child kaydı UI progress'i ve stale-task denetimi için tutulur;
    # yaşam döngüsünü `corpus_screening.run` yazar (Codex 10. tur #6)
    child = TaskResult(
        task_id=task_id, task_type=SCREENING_TASK_TYPE,
        scoring_run_id=job.scoring_run_id, status="pending", progress=0,
        result_data={"screening_job_id": job.id,
                     "assignment_attempt_id": attempt.id,
                     "screening_mode": attempt.screening_mode,
                     "parent_task_id": attempt.parent_task_id,
                     "deferred_parent": bool(deferred_parent)})
    db.add(child)
    job.task_id = task_id
    db.commit()
    try:
        run_corpus_screening_task.apply_async(
            kwargs={
                "job_id": job.id,
                "attempt_id": attempt.id,
                # Cap'ler METİN taşınır: onaylanan değer ledger'da DB ile
                # birebir karşılaştırılır (float yuvarlaması onay bozmasın)
                "expected_screening_cap_usd": str(
                    attempt.approved_screening_cap_usd),
                "expected_downstream_cap_usd": str(
                    attempt.approved_downstream_cap_usd),
                # ASSISTIVE: parent atama tarama BITTIKTEN sonra kuyruga
                # verilir (aksi halde havuz tarama tamamlanmadan kurulur)
                "deferred_parent": deferred_parent,
            },
            task_id=task_id, queue="corpus_screening")
    except Exception as exc:  # noqa: BLE001
        logger.error(f"corpus_screening enqueue hatası (job {job.id}): {exc}")
        job.status = "failed"
        job.error_code = "ENQUEUE_FAILED"
        job.error_message = str(exc)[:2000]
        child.status = "failed"
        child.error_message = str(exc)[:2000]
        db.commit()
        # Codex 11. tur #6: tarama hiç başlamadı — parent bitmişse attempt
        # AÇIK KALMAMALI (bir sonraki dispatch'i bloke eder)
        try_finish_attempt(db, attempt_id=attempt.id,
                           error_code="SCREENING_ENQUEUE_FAILED",
                           error_message=str(exc))
        return None
    return task_id


def fail_attempt(db, parent_task_id: str, *, code: str,
                 message: str) -> None:
    """Dispatch yarıda kalırsa attempt AÇIK BIRAKILMAZ.

    Aktif attempt partial unique indeksi run'ı kilitler; başarısız
    dispatch'in satırı `failed` yapılmazsa kullanıcı bir daha
    başlatamazdı.
    """
    attempt = (db.query(ChannelAssignmentAttempt)
               .filter(ChannelAssignmentAttempt.parent_task_id
                       == parent_task_id)
               .first())
    if attempt is None:
        return
    attempt.status = "failed"
    attempt.error_code = code
    attempt.error_message = (message or "")[:2000]
    if attempt.screening_job_id:
        job = db.get(CorpusScreeningJob, attempt.screening_job_id)
        if job is not None and job.status in ("pending", "running"):
            job.status = "failed"
            job.error_code = code
            job.error_message = (message or "")[:2000]
    db.commit()


__all__ = ["MODE_OFF", "MODE_SHADOW", "MODE_ASSISTIVE", "SCREENING_TASK_TYPE",
           "create_attempt", "create_screening_job", "enqueue_screening_child",
           "fail_attempt", "plan_dispatch"]
