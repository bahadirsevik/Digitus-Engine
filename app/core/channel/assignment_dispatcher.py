"""Shared channel-assignment task dispatch helpers."""
from __future__ import annotations

import uuid
from decimal import Decimal
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.core.scoring.state_machine import (
    begin_channel_assignment,
    restore_status_after_enqueue_failure,
)
from app.database.models import ScoringRun, TaskResult

CHANNEL_ASSIGNMENT_TASK_TYPE = "channel_assignment"


class ChannelAssignmentPreconditionError(ValueError):
    """Tipli dispatch ön-koşul hatası (Codex Faz C #5) — API katmanı 409
    {code, message} sözleşmesine çevirir; düz ValueError 500/404'e
    karışmasın."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")
GENERATION_TASK_TYPES = ("seo_content", "ads", "social", "social_content")
ACTIVE_TASK_STATUSES = ("pending", "running")


def get_active_channel_assignment_task(db: Session, scoring_run_id: int) -> Optional[TaskResult]:
    return (
        db.query(TaskResult)
        .filter(TaskResult.scoring_run_id == scoring_run_id)
        .filter(TaskResult.task_type == CHANNEL_ASSIGNMENT_TASK_TYPE)
        .filter(TaskResult.status.in_(ACTIVE_TASK_STATUSES))
        .order_by(TaskResult.created_at.desc())
        .first()
    )


def get_active_generation_task(db: Session, scoring_run_id: int) -> Optional[TaskResult]:
    return (
        db.query(TaskResult)
        .filter(TaskResult.scoring_run_id == scoring_run_id)
        .filter(TaskResult.task_type.in_(GENERATION_TASK_TYPES))
        .filter(TaskResult.status.in_(ACTIVE_TASK_STATUSES))
        .order_by(TaskResult.created_at.desc())
        .first()
    )


def _specificity_fingerprint(profile) -> Optional[str]:
    """Ozgulluk politikasinin kunye parmak izi (gecersizse None)."""
    from app.core.policy.specificity import policy_fingerprint

    return policy_fingerprint(profile.profile_data or {})


def _policy_snapshot(db: Session, run: ScoringRun) -> Dict[str, Any]:
    """Dispatch anındaki profil/policy durumu (Codex v18-1) — fail-open."""
    try:
        from app.core.policy import (
            approved_competitor_terms,
            approved_topic_terms,
            competitor_policy_for,
        )
        from app.database.models import BrandProfile

        profile = db.get(BrandProfile, run.brand_profile_id) \
            if run.brand_profile_id else None
        if profile is None:
            return {}
        return {
            "profile_exclude_themes": sorted(
                (profile.profile_data or {}).get("exclude_themes") or []
            ),
            # Faz B/C: koruma listesi de dispatch-anı politika zemininin
            # parçasıdır — benchmark karşılaştırmaları (patron_compare) aksi
            # halde "neden elenmedi" sorusunu yanıtlayamaz.
            "profile_protected_themes": sorted(
                (profile.profile_data or {}).get("protected_themes") or []
            ),
            # Ozgulluk politikasi kunyesi: cekirdek listesi degisirse havuz
            # BAYAT sayilir (policy_version zaten artar, fingerprint bunu
            # dispatch aninda GORUNUR kilar). Politika yok/gecersizse None.
            "specificity_policy_fingerprint": _specificity_fingerprint(profile),
            "competitor_terms_approved": sorted(
                approved_competitor_terms(profile)
            ),
            "competitor_channel_policy": competitor_policy_for(profile),
            "topic_terms_approved": sorted(approved_topic_terms(profile)),
        }
    except Exception:  # manifest telemetri niteliğinde — dispatch'i düşürmez
        return {}


def _set_task_failed(db: Session, task_id: str, error_message: str) -> None:
    task = db.query(TaskResult).filter(TaskResult.task_id == task_id).first()
    if task:
        task.status = "failed"
        task.error_message = error_message
        db.commit()


def _rollback_status(db: Session, run_id: int, previous_status: str) -> None:
    restore_status_after_enqueue_failure(
        db,
        run_id,
        expected_status="channel_assigning",
        restore_status=previous_status,
    )


def set_task_failed(db: Session, task_id: str, error_message: str) -> None:
    """Public sarmalayici (ertelenmis parent hatasi icin)."""
    _set_task_failed(db, task_id, error_message)


def rollback_assignment_status(db: Session, run_id, previous_status) -> None:
    """Public sarmalayici: enqueue basarisizsa run durumu geri alinir."""
    if run_id is None or not previous_status:
        return
    _rollback_status(db, int(run_id), previous_status)


def republish_stale_deferred_parents(db: Session, run_id: int
                                     ) -> Optional[Dict[str, Any]]:
    """Yayim penceresinde kaybolmus ertelenmis parent'i YENIDEN yayimlar.

    Assistive'de parent, tarama bitince `publishing` durumuna alinip
    broker'a verilir. Process tam arada olurse parent hicbir zaman
    kuyruga girmez ve CAS yuzunden bir daha denenmezdi (Codex 27. tur #3).
    Lease dolunca payload attempt manifestinden okunur ve AYNI task ID ile
    yeniden yayimlanir — mukerrer teslim parent claim'iyle zaten guvenli.
    """
    from app.core.screening.attempt_state import (
        claim_parent_release,
        mark_parent_published,
        stale_parent_payloads,
    )
    from app.tasks.intent_tasks import run_channel_assignment_task

    for payload in stale_parent_payloads(db, run_id):
        task_id = payload.get("parent_task_id")
        # Yayim hakki YINE CAS'ten alinir: iki reconciler yarissa bile
        # parent tek kez yayimlanir
        dispatch = claim_parent_release(db, payload=payload)
        if dispatch is None:
            continue
        try:
            run_channel_assignment_task.apply_async(
                args=dispatch["args"], kwargs=dispatch["kwargs"],
                task_id=task_id)
        except Exception as exc:  # noqa: BLE001
            import logging as _logging

            _logging.getLogger(__name__).error(
                "ertelenmis parent yeniden yayimlanamadi (%s): %s",
                task_id, exc)
            continue
        mark_parent_published(db, attempt_id=payload.get("attempt_id"))
        return {"task_id": task_id, "status": "pending",
                "scoring_run_id": run_id, "already_active": True,
                "republished": True,
                "effective_relevance_coefficient": float(
                    dispatch["args"][1])}
    return None


def republish_all_stale_deferred_parents(db: Session) -> Dict[str, Any]:
    """Republish every expired deferred parent without a new user dispatch."""
    from app.core.screening.attempt_state import (
        ACTIVE_STATES,
        PARENT_PUBLISH_LEASE_SECONDS,
    )
    from app.database.models import ChannelAssignmentAttempt

    cutoff = datetime.now(timezone.utc) - timedelta(
        seconds=PARENT_PUBLISH_LEASE_SECONDS)
    run_ids = [
        int(row[0])
        for row in (
            db.query(ChannelAssignmentAttempt.scoring_run_id)
            .filter(
                ChannelAssignmentAttempt.status.in_(ACTIVE_STATES),
                ChannelAssignmentAttempt.assignment_dispatch_state
                == "publishing",
                ChannelAssignmentAttempt.dispatch_last_at < cutoff,
            )
            .distinct()
            .all()
        )
    ]
    republished = []
    for run_id in run_ids:
        result = republish_stale_deferred_parents(db, run_id)
        if result is not None:
            republished.append(result)
    return {
        "scanned_runs": len(run_ids),
        "republished": len(republished),
        "task_ids": [row["task_id"] for row in republished],
    }


def enqueue_channel_assignment(
    db: Session,
    run: ScoringRun,
    *,
    relevance_coefficient: Optional[float] = None,
    from_status: Optional[str] = None,
    approved_screening_mode: Optional[str] = None,
    approved_preflight_sha256: Optional[str] = None,
    approved_screening_hard_cap_usd: Optional[float] = None,
) -> Dict[str, Any]:
    """Create and enqueue the channel-assignment Celery task.

    This helper owns the pending TaskResult, state transition, and Celery enqueue
    so manual and automatic assignment paths cannot diverge.
    """
    # Motor v3 (plan_algoritma_entegrasyonu.md K13 / madde 3+6): kilitli
    # motor (Faz 2-7) henüz devrede değil ve v3 run'da screening_mode
    # zorunlu 'off'tur. TÜM diğer ön-koşullardan (workspace kilidi, ücret
    # kapısı, reconcile, manifest yazımı...) ÖNCE yan etkisiz erken red —
    # eski v2 hattı SESSİZCE çağrılmaz.
    # Eski motor (v2/v2_1) run'ı salt-okunurdur: dispatcher sınırı API
    # kapısını TEKRAR uygular (otomatik zincirler — execute, relevance
    # sonrası auto-assign — de buradan geçer). Workspace kilidi, reconcile,
    # stale parent yeniden yayımı, screening kararı (decide_screening_mode),
    # attempt/maliyet rezervasyonu, TaskResult ve Celery enqueue'dan ÖNCE.
    from app.core.engine_version_gate import (
        LEGACY_RUN_READ_ONLY,
        is_legacy_run,
        legacy_run_message,
        run_algorithm_version,
    )

    if is_legacy_run(run):
        raise ChannelAssignmentPreconditionError(
            LEGACY_RUN_READ_ONLY,
            legacy_run_message(run_algorithm_version(run)),
        )

    _engine_version = getattr(run, "algorithm_version", "v2") or "v2"
    if _engine_version == "v3":
        if approved_screening_mode in ("shadow", "assistive"):
            raise ChannelAssignmentPreconditionError(
                "ENGINE_V3_SCREENING_NOT_SUPPORTED",
                "v3 koşularında screening_mode zorunlu 'off' (K13) — "
                "shadow/assistive desteklenmiyor.",
            )

    from app.tasks.intent_tasks import run_channel_assignment_task

    # Plan v13 kilit protokolü: dispatch, mutasyon endpoint'leriyle AYNI
    # workspace satır kilidini alır (workspace lock → durum/task kontrolü →
    # task oluştur → commit → enqueue). Sürüm snapshot'ları kilit altında
    # okunur ve DEĞİŞMEZ task argümanı olarak taşınır (manifest sonraki
    # dispatch'lerde değişebilir — worker neyle başladığını argümandan bilir).
    from app.config import settings as _settings
    from app.database.models import BrandProfile as _BrandProfile

    workspace = None
    if run.brand_profile_id:
        workspace = (
            db.query(_BrandProfile)
            .filter(_BrandProfile.id == run.brand_profile_id)
            .with_for_update()
            .first()
        )

    # Plan §7.1 kilit sirasi: workspace -> scoring_run. Run kilidi ADS/SOCIAL
    # generation dispatch'leriyle AYNI satiri serilestirdigi icin eszamanli
    # assignment/generation yarisinda yalniz biri task olusturabilir.
    # Codex 10. tur #9: `populate_existing()` OLMADAN identity-map'teki eski
    # kopya kullanilirdi — kilit alinir ama okunan veri bayat kalirdi.
    locked_run = (db.query(ScoringRun)
                  .filter(ScoringRun.id == run.id)
                  .populate_existing()
                  .with_for_update()
                  .first())
    if locked_run is not None:
        run = locked_run

    # ÜCRET KAPISI (deneme workspace'i): kanal ataması GERÇEK sağlayıcı
    # çağrısı yapar. Sağlayıcıya gitmeden önce izinli kanal/algoritma
    # birleşimi, başarılı koşu kotası ve DELİNEMEZ toplam maliyet sınırı
    # ölçülür. Kapı, task/attempt/screening job oluşturulmadan ÖNCE kapanır.
    if workspace is not None:
        from app.core.trial_authorization import check_run_allowed

        _trial_problem = check_run_allowed(db, workspace, run,
                                           stage="channel_assignment")
        if _trial_problem is not None:
            raise ChannelAssignmentPreconditionError(
                _trial_problem["code"], _trial_problem["message"])

    # Codex 11. tur #3 + 12. tur #3: aktif-task guard'ından ve manifest
    # yazımından ÖNCE stale finalizasyon. Hard-kill sonrası `running`
    # kalmış task run'ı sonsuza dek "aktif" göstermemeli; reconcile run
    # durumunu da kurtarabilir (bayat `channel_assigning`).
    from app.core.screening.attempt_state import (
        reconcile_stale_attempts as _reconcile_stale,
    )

    status_before_reconcile = run.status
    if _reconcile_stale(db, run.id):
        db.refresh(run)
    # Yayim penceresinde kaybolmus ertelenmis parent varsa YENI dispatch
    # acilmaz: eski task AYNI kimlikle yeniden yayimlanir
    republished = republish_stale_deferred_parents(db, run.id)
    if republished is not None:
        return republished
    reconcile_changed_status = run.status != status_before_reconcile

    requested_policy_version = (
        int(workspace.policy_version or 1) if workspace is not None else None
    )
    requested_anchor_version = (
        int(workspace.anchor_version or 1) if workspace is not None else None
    )

    # v2.1 (plan Faz C): dispatch-anı ONAYLI strateji snapshot'ı + sürümü.
    # v2 run'larda bilgi amaçlı taşınır; v2_1 run onaylı strateji olmadan
    # DISPATCH EDİLMEZ (sonda STRATEGY_CHANGED ile boşa yanmasın).
    algorithm_version = getattr(run, "algorithm_version", "v2") or "v2"
    strategy_snapshot = None
    requested_strategy_version = None
    if algorithm_version != "v3":
        from app.core.policy.channel_strategy import strategy_snapshot_for_run
        strategy_snapshot = (
            strategy_snapshot_for_run(workspace) if workspace is not None else None
        )
        if strategy_snapshot is not None:
            # Codex Faz D #1: ADS hedef kitlesi de dispatch-anı prompt-context
            # snapshot'ının parçasıdır (worker canlı profili OKUMAZ)
            profile = getattr(workspace, "profile_data", None) or {}
            audience = str(profile.get("target_audience") or "").strip()
            strategy_snapshot = {
                **strategy_snapshot,
                "target_audience": audience or None,
            }
        requested_strategy_version = (
            strategy_snapshot["strategy_version"]
            if strategy_snapshot is not None else None
        )
    if algorithm_version == "v2_1":
        if strategy_snapshot is None:
            raise ChannelAssignmentPreconditionError(
                "STRATEGY_REQUIRED",
                "v2_1 koşusu için onaylı kanal stratejisi şart — Marka "
                "Profili > Kanal Stratejisi bölümünden onaylayın.",
            )
        # Dispatcher, API kapısını TEKRAR doğrular (run oluşturulduktan sonra
        # strateji authority'ye çevrilmiş olabilir). Kural aynı: authority
        # yalnız SOCIAL kapalı koşuda serbest.
        if (strategy_snapshot.get("social_mode") == "authority"
                and bool(getattr(run, "enable_social", True))):
            raise ChannelAssignmentPreconditionError(
                "SOCIAL_AUTHORITY_EXPERIMENTAL",
                "authority modu SOCIAL kanalında deneyseldir; v2_1 koşusu "
                "yalnızca enable_social=false ile başlatılabilir "
                "(sessizce hype koşulmaz).",
            )
    relevance_required = bool(
        _settings.ENABLE_RELEVANCE_RERANK and not run.skip_relevance
    )
    recompute_relevance = bool(
        workspace is not None
        and relevance_required
        and (
            run.relevance_anchor_version is None
            or run.relevance_anchor_version != requested_anchor_version
        )
    )

    # Run manifest (plan C + Codex v8-5): SHA önce runtime .git okumasından
    # (bind-mount'lu dev'de build arg'ı bayatlıyordu — manifest kodu iki
    # commit geriden gösterdi), yoksa Docker build arg'ından
    from app.core.constants import (
        BRAND_FILTER_PROMPT_VERSION,
        MAX_EXPANSION_AI_BATCHES as _MAX_EXPANSION_AI_BATCHES,
        MAX_EXPANSION_ROUNDS as _MAX_EXPANSION_ROUNDS,
        PROMPT_CONFIG_VERSION,
    )
    from app.core.gitinfo import resolve_git_sha
    from app.core.policy.review import canonical_anchor_fingerprint
    run.execution_manifest = {
        # Deneme yetkisi künyesi KORUNUR: manifest burada baştan yazılır ve
        # aksi halde authorization_id/slot_id/snapshot SHA kaybolurdu.
        **({"trial_authorization": _trial_manifest}
           if (_trial_manifest := (run.execution_manifest or {}).get(
               "trial_authorization")) else {}),
        "git_sha": resolve_git_sha() or _settings.APP_GIT_SHA,
        "model": _settings.GEMINI_MODEL,
        "model_map": dict(_settings.AI_STAGE_MODELS or {}),
        "thinking_level": _settings.GEMINI_THINKING_LEVEL,
        "prompt_config_version": PROMPT_CONFIG_VERSION,
        # Marka filtresi sözleşmesi ayrı sürümlenir (Faz C): koşunun hangi
        # brand-filter prompt/çıktı sözleşmesiyle yapıldığı denetlenebilir
        "brand_filter_prompt_version": BRAND_FILTER_PROMPT_VERSION,
        # Deney anahtarı görünürlüğü: pencere çarpanı 1 değilse bu bir
        # deney koşusudur — raporda açıkça görünür
        "candidate_pool_multiplier": int(
            _settings.CANDIDATE_POOL_MULTIPLIER or 1),
        # Codex v18-1: DISPATCH-ANI profil/policy snapshot'ı — rapor
        # anındaki durum değil, koşunun gerçekten hangi dışlamalarla
        # yapıldığı denetlenebilir olmalı (benchmark kapısı bunu okur)
        "policy_snapshot": _policy_snapshot(db, run),
        # Plan v13: sürüm/parmak izi denetlenebilirliği (kanıtlı backfill
        # ve model-değişimi tanıma için)
        "requested_policy_version": requested_policy_version,
        "requested_anchor_version": requested_anchor_version,
        "anchor_hash": canonical_anchor_fingerprint(
            (workspace.profile_data or {}).get("anchor_texts")
            if workspace is not None else None
        ),
        "recompute_relevance": recompute_relevance,
        "algorithm_version": algorithm_version,
        **({
            "strategy_snapshot": strategy_snapshot,
            "requested_strategy_version": requested_strategy_version,
        } if algorithm_version != "v3" else {}),
        # Plan Faz E: aday modu / relevance / expansion davranışı manifest'te
        # AÇIK alanlardır — ilk v2_1 sürümü production penceresini korur
        # (full-universe ertelenmiş patron kararı, plan §12)
        "candidate_mode": "current_window",
        "relevance_enabled": relevance_required,
        "expansion_policy": {
            "max_rounds": _MAX_EXPANSION_ROUNDS,
            "ai_budget": _MAX_EXPANSION_AI_BATCHES,
        },
    }

    active = get_active_channel_assignment_task(db, run.id)
    if active:
        return {
            "task_id": active.task_id,
            "status": active.status,
            "scoring_run_id": run.id,
            "already_active": True,
            "effective_relevance_coefficient": float(
                (active.result_data or {}).get(
                    "effective_relevance_coefficient",
                    relevance_coefficient
                    if relevance_coefficient is not None
                    else run.default_relevance_coefficient,
                )
            ),
        }

    effective_relevance_coefficient = float(
        relevance_coefficient
        if relevance_coefficient is not None
        else run.default_relevance_coefficient
    )
    # `from_status` çağıranın sözleşmesidir; YALNIZ reconcile durumu
    # değiştirdiyse DB otorite olur (bayat parametre geçersiz transition
    # üretmesin — Codex 12. tur #3)
    previous_status = (run.status if reconcile_changed_status
                       else (from_status or run.status))

    from app.core.scoring.state_machine import _is_valid_transition
    if not _is_valid_transition(previous_status, "channel_assigning"):
        raise ChannelAssignmentPreconditionError(
            "RUN_STATUS_NOT_ASSIGNABLE",
            f"Run {run.id} durumu '{previous_status}' kanal atamasına uygun değil — önce skorlama tamamlanmalıdır.",
        )

    task_id = str(uuid.uuid4())

    # Corpus screening (plan §7.1): parent TaskResult + attempt (+ shadow'da
    # mühürlü screening job) AYNI transaction'da doğar — sahipsiz job veya
    # attempt'siz parent oluşamaz. Bayrak kapalıyken mod her zaman `off`
    # ve davranış bugünküyle birebir aynıdır (job yok, cap yok).
    from app.core.screening.dispatch import (
        create_attempt as _create_attempt,
        enqueue_screening_child,
        fail_attempt,
        plan_dispatch,
    )
    from app.core.screening.preflight import (
        MODE_ASSISTIVE,
        MODE_OFF,
        PreflightError,
        resolve_screening_mode,
    )

    # SUNUCU KONTROLLU AKIS (revize plan §3): operator acikca bir mod
    # onaylamadiysa modu SUNUCU belirler (kill switch -> allowlist ->
    # varsayilan/override -> algoritma -> strateji). Kullaniciya hicbir
    # kapi HATA olarak donmez; sebep denetime yazilir.
    from app.core.screening.auto_trigger import (
        CANDIDATE_CANARY_CONTRACT_VERSION,
        candidate_canary_run_count,
        decide_screening_mode,
    )

    if algorithm_version == "v3":
        quantum = Decimal("0.000001")
        total_cap = Decimal(str(_settings.ENGINE_V3_HARD_CAP_USD))
        screening_cap = quantum
        downstream_cap = total_cap - quantum
        if screening_cap + downstream_cap != total_cap:
            raise ChannelAssignmentPreconditionError(
                "ENGINE_V3_CAP_MISMATCH",
                "v3 sert tavan kuantum toplam hatası",
            )
        auto_dispatch = True
        screening_mode = MODE_OFF
        screening_skipped_reason = "engine_v3_screening_off"
        attempt = _create_attempt(
            db, run, parent_task_id=task_id, mode=MODE_OFF,
            relevance_coefficient=effective_relevance_coefficient,
            requested_policy_version=requested_policy_version,
            requested_anchor_version=requested_anchor_version,
            manifest={**(run.execution_manifest or {}),
                      "screening_skipped_reason": screening_skipped_reason})
        attempt.approved_screening_cap_usd = screening_cap
        attempt.approved_downstream_cap_usd = downstream_cap
        screening_job = None
        preflight = {
            "combined_exposure_usd": float(total_cap),
            "enforced_hard_cap_usd": float(total_cap),
        }
    else:
        auto_dispatch = approved_screening_mode is None
        auto_reason = None
        if auto_dispatch:
            screening_mode, auto_reason = decide_screening_mode(
                run, workspace, settings=_settings, db=db)
        else:
            screening_mode = approved_screening_mode
        if auto_dispatch and screening_mode == MODE_ASSISTIVE:
            run.execution_manifest = {
                **(run.execution_manifest or {}),
                "candidate_canary_contract_version":
                    CANDIDATE_CANARY_CONTRACT_VERSION,
                "candidate_canary_workspace_run_ordinal":
                    candidate_canary_run_count(db, workspace) + 1,
            }
        screening_skipped_reason = auto_reason
        try:
            attempt, screening_job, preflight = plan_dispatch(
                db, run, workspace, parent_task_id=task_id,
                forced_mode=screening_mode if auto_dispatch else None,
                relevance_coefficient=effective_relevance_coefficient,
                requested_policy_version=requested_policy_version,
                requested_anchor_version=requested_anchor_version,
                requested_strategy_version=requested_strategy_version,
                # Kapi sebebi DENETIME yazilir (revize plan §3.1): kullanici
                # yalnizca normal atamasini gorur, sebep attempt manifestinde
                # ve parent task result_data'sinda kalir
                manifest=({**(run.execution_manifest or {}),
                           "screening_skipped_reason": auto_reason}
                          if auto_reason else run.execution_manifest),
                approved_screening_mode=approved_screening_mode,
                approved_preflight_sha256=approved_preflight_sha256,
                approved_screening_hard_cap_usd=(
                    approved_screening_hard_cap_usd),
                settings=_settings)
        except PreflightError as exc:
            # Aktif attempt bir MOD sorunu değil, YARIŞ korumasıdır: her modda
            # tipli 409 olur (sessizce ikinci dispatch açılmaz)
            if exc.code == "ATTEMPT_ALREADY_ACTIVE":
                # Yaris korumasi HER modda tipli 409'dur
                raise ChannelAssignmentPreconditionError(
                    exc.code, exc.message) from exc
            if not auto_dispatch and screening_mode == MODE_ASSISTIVE:
                # Operator ACIKCA assistive onayladiysa sessizce baseline'a
                # dusulmez: neyi onayladigi ile ne kostugu ayrisamaz
                raise ChannelAssignmentPreconditionError(
                    exc.code, exc.message) from exc
            if approved_screening_mode is not None:
                # Kullanıcı BU koşuyu açıkça onayladıysa sessizce off'a
                # düşülmez — neyi onayladığı ile ne koştuğu ayrışamaz
                raise ChannelAssignmentPreconditionError(
                    exc.code, exc.message) from exc
            # Shadow YALNIZ denetim işidir: canlı sonucu etkilemediği için
            # atama durdurulmaz; sebep attempt manifestinde ve parent task
            # result_data'sında GÖRÜNÜR kalır (sessiz düşüş değil).
            import logging as _logging

            _logging.getLogger(__name__).warning(
                "otomatik tarama atlandı (%s): %s — baseline sürüyor",
                exc.code, exc.message)
            # Preflight SATIR EKLEMEDEN reddeder (yalnız okur) — rollback
            # gerekmez ve yapılmamalıdır: manifest ataması geri alınırdı.
            screening_skipped_reason = exc.code
            attempt = _create_attempt(
                db, run, parent_task_id=task_id, mode=MODE_OFF,
                relevance_coefficient=effective_relevance_coefficient,
                requested_policy_version=requested_policy_version,
                requested_anchor_version=requested_anchor_version,
                requested_strategy_version=requested_strategy_version,
                manifest={**(run.execution_manifest or {}),
                          "screening_skipped_reason": exc.code})
            screening_job = None
            preflight = None

    task = TaskResult(
        task_id=task_id,
        task_type=CHANNEL_ASSIGNMENT_TASK_TYPE,
        scoring_run_id=run.id,
        status="pending",
        progress=0,
        result_data={
            "effective_relevance_coefficient": effective_relevance_coefficient,
            "phase": "screening" if screening_job is not None else "assignment",
            "screening_mode": attempt.screening_mode,
            "assignment_attempt_id": attempt.id,
            "screening_job_id": (
                screening_job.id if screening_job
                else (preflight or {}).get("reused_screening_job_id")),
            "screening_reused": bool(
                (preflight or {}).get("reused_screening_job_id")),
            "screening_skipped_reason": screening_skipped_reason,
            "auto_dispatch": auto_dispatch,
            # Screening ve downstream cap'leri aynı attempt ledger'ında
            # uygulanır; preflight kimliği iki sınırı da taşır.
            "combined_exposure_usd": (preflight["combined_exposure_usd"]
                                      if preflight else None),
            "enforced_hard_cap_usd": (preflight["enforced_hard_cap_usd"]
                                      if preflight else None),
        },
    )
    db.add(task)
    db.commit()

    # CAS + TÜM yan etkiler (ContentOutput/ADS/SOCIAL stale + version artışı)
    # TEK transaction'da (Codex v8-1: transition_atomic yan etkisizdi —
    # production yolunda version artmıyor, ADS/SOCIAL stale olmuyordu).
    # Enqueue aşağıda başarısız olursa status geri alınır ama stale işaretleri
    # ve version artışı kalır — fail-safe yön (içerik bayat sayılır).
    try:
        transitioned = begin_channel_assignment(
            db,
            run,
            from_status=previous_status,
        )
    except Exception:
        _set_task_failed(db, task_id, "Kanal atamasına geçiş yapılamadı.")
        fail_attempt(db, task_id, code="TRANSITION_FAILED",
                     message="Kanal atamasına geçiş yapılamadı.")
        raise

    if not transitioned:
        _set_task_failed(db, task_id, "Kanal ataması zaten farklı bir aşamaya geçti.")
        fail_attempt(db, task_id, code="TRANSITION_LOST",
                     message="Kanal ataması zaten farklı bir aşamaya geçti.")
        return {
            "task_id": task_id,
            "status": "failed",
            "scoring_run_id": run.id,
            "already_active": False,
            "effective_relevance_coefficient": effective_relevance_coefficient,
        }

    parent_args = [run.id, effective_relevance_coefficient]
    parent_kwargs = {
        "requested_policy_version": requested_policy_version,
        "requested_anchor_version": requested_anchor_version,
        "recompute_relevance": recompute_relevance,
        **({"requested_strategy_version": requested_strategy_version} if algorithm_version != "v3" else {}),
    }
    # ASSISTIVE SIRASI: union canliya ancak tarama TAMAMLANMISSA
    # uygulanabilir. Parent hemen kosarsa job `completed` olmadigi icin
    # her zaman baseline'a duserdi -> parent, cocuk bitince salinir.
    defer_parent = (attempt.screening_mode == MODE_ASSISTIVE
                    and screening_job is not None)
    if not defer_parent:
        try:
            run_channel_assignment_task.apply_async(
                args=parent_args, kwargs=parent_kwargs, task_id=task_id)
        except Exception as exc:
            _set_task_failed(db, task_id, str(exc))
            fail_attempt(db, task_id, code="ENQUEUE_FAILED",
                         message=str(exc))
            _rollback_status(db, run.id, previous_status)
            raise

    screening_task_id = None
    reused_job_id = (preflight or {}).get("reused_screening_job_id")
    # ASSISTIVE'de karsi-olgu YAZILMAZ (Codex 27. tur #1): `is_applied=false`
    # satirlari parent'in applied audit'iyle ayni anahtari tuketir ve union
    # sessizce baseline'a duserdi. Reuse'da kararlar ZATEN completed job'ta;
    # assistive dogrudan onlari kullanir.
    if reused_job_id and attempt.screening_mode == MODE_ASSISTIVE:
        import logging as _logging

        _logging.getLogger(__name__).info(
            "assistive reuse: karsi-olgu YAZILMAZ (job %s) — audit'i parent "
            "yazar", reused_job_id)
    elif reused_job_id:
        # Kararlar ZATEN var: saglayici cagrisi YOK, ucret YOK.
        # Olcum (counterfactual) bu attempt icin yeniden uretilir.
        try:
            from app.core.screening.counterfactual import (
                materialize_counterfactual,
            )

            materialize_counterfactual(db, job_id=reused_job_id,
                                       attempt_id=attempt.id)
        except Exception as exc:  # noqa: BLE001
            import logging as _logging

            _logging.getLogger(__name__).warning(
                "reuse counterfactual uretilemedi (job %s): %s",
                reused_job_id, exc)
    deferred_payload = None
    if defer_parent:
        # Payload KALICI: publish penceresinde process olurse reconciler
        # AYNI task ID ile yeniden yayimlayabilsin (Codex 27. tur #3)
        from app.core.screening.attempt_state import (
            build_deferred_parent_payload,
        )

        deferred_payload = build_deferred_parent_payload(
            attempt,
            previous_status=previous_status,
            recompute_relevance=recompute_relevance,
        )
        attempt.manifest = {**(attempt.manifest or {}),
                            "deferred_parent": deferred_payload}
        db.commit()
    if screening_job is not None:
        # Shadow fan-out: assignment ZATEN kuyruğa girdi; tarama ayrı
        # kuyrukta paralel koşar ve canlı havuza DOKUNMAZ. Teslim hatası
        # yalnız job'ı failed yapar (plan §7.2).
        # Assistive: parent PAYLOAD'u cocuga verilir, cocuk bitince salinir.
        screening_task_id = enqueue_screening_child(
            db, attempt=attempt, job=screening_job,
            deferred_parent=(deferred_payload if defer_parent else None))
        if defer_parent and screening_task_id is None:
            # Cocuk kuyruga GIREMEDI: parent sonsuza kadar beklemez —
            # tarama olmadan BASELINE kosar (sebep denetimde)
            screening_skipped_reason = "SCREENING_ENQUEUE_FAILED"
            try:
                run_channel_assignment_task.apply_async(
                    args=parent_args, kwargs=parent_kwargs, task_id=task_id)
            except Exception as exc:
                _set_task_failed(db, task_id, str(exc))
                fail_attempt(db, task_id, code="ENQUEUE_FAILED",
                             message=str(exc))
                _rollback_status(db, run.id, previous_status)
                raise

    return {
        "task_id": task_id,
        "status": "pending",
        "scoring_run_id": run.id,
        "already_active": False,
        "effective_relevance_coefficient": effective_relevance_coefficient,
        "screening_mode": attempt.screening_mode,
        "assignment_attempt_id": attempt.id,
        "screening_job_id": (screening_job.id if screening_job
                             else reused_job_id),
        "screening_task_id": screening_task_id,
        "screening_reused": bool(reused_job_id),
        "screening_skipped_reason": screening_skipped_reason,
        "auto_dispatch": auto_dispatch,
        "parent_deferred": bool(defer_parent and screening_task_id),
    }
