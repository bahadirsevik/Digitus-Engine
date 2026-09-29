# -*- coding: utf-8 -*-
"""Üretim screening koşucusu: checkpoint/resume + kalıcı ledger (plan §5.4).

Codex 9. tur sertleştirmeleri:
- Rezervasyon birimi GERÇEK HTTP denemesidir (`provider.screen_batch`),
  mantıksal batch değil: `run_screening` içindeki parse/transient/
  missing-ID/single retry turlarının HER BİRİ ayrı kalıcı rezervasyon ve
  ayrı inflight slotu alır. `actual_requests` gerçek çağrı sayısıdır.
- Özet (maliyet, unresolved, ihlal, çağrı) DB'den YENİDEN üretilir;
  process belleğindeki sayaç otorite DEĞİLDİR (resume doğruluğu).
- Settle sonrası checkpoint yazılamadan çökme: "kapanmış rezervasyon +
  checkpoint yok" durumu tipli `RESULT_LOST_AFTER_SETTLE` ile fallback
  olur — sessiz ÜCRETLİ retry YAPILMAZ.
- Job DB-CAS ile claim edilir; sahiplik (attempt↔job↔run↔workspace,
  task_id) provider çağrısından ÖNCE doğrulanır.
- Dondurulmuş girdi mühürleri (context SHA, evren SHA, checkpoint
  sözleşme hash'i, payload SHA) harcamadan ÖNCE fail-closed doğrulanır.

Batch başına sıra: checkpoint → (kayıp-sonuç kontrolü) → provider çağrısı
(her HTTP denemesi: rezervasyon → slot → çağrı → settle/ceiling) →
doğrulanmış payload'lı checkpoint. Kararlar YALNIZ tüm görünümler
kapıdan geçince TEK transaction'da yazılır.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy import func as sa_func
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.core.screening.attempt_state import (
    PHASE_SCREENING,
    start_attempt,
)
from app.core.screening.candidate_union import (
    PRODUCTION_SCREENING_CONTRACT,
    SCREENING_APPLIED_CHANNELS_V3,
)
from app.core.screening.context import (
    ScreeningContextMissing,
    screening_context_from_snapshot,
)
from app.core.screening.ensemble import PLAN_SALTS, merge_multi_views, sticky_plans
from app.core.screening.identity import (
    screening_runner_contract,
    universe_sha256,
)
from app.core.screening.inflight import NullInflightLimiter
from app.core.telemetry.ai_cost_budget import (
    BUDGET_SCREENING,
    STATE_RESERVED,
    AiCostLedger,
)
from app.database.models import (
    AiCostReservation,
    ChannelAssignmentAttempt,
    CorpusScreeningBatchCheckpoint,
    CorpusScreeningDecision,
    CorpusScreeningJob,
)

CHANNELS = ("ADS", "SEO", "SOCIAL")
JOB_STATUS_RUNNING = "running"
JOB_STATUS_COMPLETED = "completed"
JOB_STATUS_FALLBACK = "fallback"
JOB_TERMINAL_STATES = ("completed", "failed", "fallback", "not_needed")
MAX_UNRESOLVED_RATIO = 0.01
# Lease, task hard time limit'inden GÜVENLİ MARJLA uzun: süre dolmuşsa
# önceki worker kesinlikle ölmüştür (Celery 1200s'te öldürür)
EXECUTION_LEASE_SECONDS = 1500


class ScreeningRunError(RuntimeError):
    """Koşu sözleşmesi ihlali — job fail/fallback olur."""


class ScreeningResultLost(ScreeningRunError):
    """Ücret ödendi ama sonuç kaydedilemedi — sessiz retry YASAK."""

    error_code = "RESULT_LOST_AFTER_SETTLE"


class RunnerContractDrift(ScreeningRunError):
    """Job'ın MÜHÜRLÜ runner sözleşmesi canlı kodla uyuşmuyor."""

    error_code = "RUNNER_CONTRACT_DRIFT"


class ScreeningIdentityConflict(ScreeningRunError):
    """Ayni kimlikte ZATEN completed job var — sonuc reuse edilmeli."""

    error_code = "SCREENING_IDENTITY_CONFLICT"


class ScreeningJobNotClaimable(ScreeningRunError):
    """Job başka bir worker tarafından claim edilmiş/terminal."""

    error_code = "JOB_NOT_CLAIMABLE"


def _sha(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True,
                   separators=(",", ":")).encode("utf-8")).hexdigest()


def batch_hash(batch: List[Dict[str, Any]]) -> str:
    """Batch kimliği: SIRALI keyword id listesi (sıra da sözleşmedir)."""
    return _sha([int(item["id"]) for item in batch])


def request_id_for(job_id: int, view: str, bhash: str) -> str:
    """Batch kimliği; gerçek HTTP denemeleri `:c{n}` ile ayrışır."""
    return f"scr:{job_id}:{view}:{bhash[:16]}"


def contract_sha(job: CorpusScreeningJob) -> str:
    """Checkpoint reuse sözleşmesi — TÜM davranış pinlerini kapsar.

    Codex 9. tur #5: yalnız prompt_version/model/batch_size YETMEZ;
    provider, temperature, şema/reason-code/prompt gövdesi ve runner
    davranış sabitleri de değişirse eski checkpoint YENİDEN KULLANILAMAZ.
    """
    return _sha({
        "provider": job.provider,
        "model": job.model,
        "prompt_version": job.prompt_version,
        "temperature": str(job.temperature),
        "batch_size": int(job.batch_size),
        "view_salts": list(job.view_salts or []),
        "prompt_template_sha256":
            PRODUCTION_SCREENING_CONTRACT["prompt_template_sha256"],
        "response_schema_sha256":
            PRODUCTION_SCREENING_CONTRACT["response_schema_sha256"],
        "reason_codes_sha256":
            PRODUCTION_SCREENING_CONTRACT["reason_codes_sha256"],
        "reason_code_version":
            PRODUCTION_SCREENING_CONTRACT["reason_code_version"],
        "runner_contract": job.runner_contract or screening_runner_contract(),
    })


def verify_checkpoint(checkpoint, expected_contract: str) -> None:
    """Checkpoint mührü — TEK doğrulama noktası (Codex 13. tur #3).

    Hem ana faz hem `#single` checkpoint'leri için; reuse, bütçe/resolved
    yeniden kurulumu ve finalize ÖNCESİNDE aynı kapıdan geçer. Aksi halde
    bozulmuş bir tekil checkpoint kelimeyi "çözülmüş" gösterebilir veya
    retry kotasını sessizce tüketebilirdi.
    """
    if checkpoint.request_contract_sha256 != expected_contract:
        raise ScreeningRunError(
            f"checkpoint sözleşmesi uyuşmuyor "
            f"({checkpoint.view}#{checkpoint.batch_ordinal}) — reuse "
            f"REDDEDİLDİ")
    if _sha(checkpoint.payload) != checkpoint.payload_sha256:
        raise ScreeningRunError(
            f"checkpoint payload mührü bozuk "
            f"({checkpoint.view}#{checkpoint.batch_ordinal})")


def _rows_from_snapshot(job: CorpusScreeningJob) -> List[Dict[str, Any]]:
    """Dondurulmuş evren; `keyword_score_id` ZORUNLU (harcamadan önce)."""
    rows = (job.input_snapshot or {}).get("rows")
    if not isinstance(rows, list) or not rows:
        raise ScreeningRunError(
            "job.input_snapshot.rows boş — worker canlı tablo OKUMAZ")
    universe, seen = [], set()
    for row in rows:
        kid = row.get("keyword_id")
        text = row.get("keyword")
        score_id = row.get("keyword_score_id")
        if not isinstance(kid, int) or isinstance(kid, bool) or not text:
            raise ScreeningRunError(f"geçersiz snapshot satırı: {row!r}")
        if not isinstance(score_id, int) or isinstance(score_id, bool):
            raise ScreeningRunError(
                f"keyword {kid} için keyword_score_id yok — kararlar "
                f"yazılamaz, harcama BAŞLAMAZ")
        if kid in seen:
            raise ScreeningRunError(f"snapshot'ta tekrarlanan id: {kid}")
        seen.add(kid)
        universe.append({"id": kid, "keyword": str(text)})
    return universe


def _score_id_map(job: CorpusScreeningJob) -> Dict[int, int]:
    return {int(r["keyword_id"]): int(r["keyword_score_id"])
            for r in (job.input_snapshot or {}).get("rows", [])}


def _results_from_payload(payload: Dict[str, Any]) -> List[Any]:
    rows = (payload or {}).get("results")
    if not isinstance(rows, list):
        raise ScreeningRunError("checkpoint payload'ında 'results' yok")
    return [SimpleNamespace(
        keyword_id=int(r["keyword_id"]), keyword=r.get("keyword", ""),
        ads_fit=r.get("ads_fit"), seo_fit=r.get("seo_fit"),
        social_fit=r.get("social_fit"),
        reason_codes=r.get("reason_codes") or {},
        unresolved=bool(r.get("unresolved")),
        membership_violations=r.get("membership_violations") or [])
        for r in rows]


def _usage_totals(usage) -> Dict[str, int]:
    if usage is None:
        return {}
    return {key: int(getattr(usage, key, 0) or 0) for key in
            ("prompt_tokens", "completion_tokens", "thoughts_tokens",
             "cache_hit_tokens", "cache_miss_tokens")}


def _usage_missing(totals: Dict[str, int]) -> bool:
    """Token kanıtı yoksa usage YOK sayılır (ceiling-charge yolu)."""
    return not any(int(v or 0) > 0 for v in (totals or {}).values())


class LedgerGuardedProvider:
    """Her GERÇEK HTTP denemesini ayrı rezerve/settle eden sarmalayıcı.

    Codex 9. tur #1: `run_screening` bir batch için birden çok istek
    yapabilir (parse/transient/missing-ID/single retry). Rezervasyonu
    batch seviyesinde tutmak, tek isteklik tavan altında N istek
    harcanmasına izin veriyordu.
    """

    def __init__(self, inner, *, ledger: AiCostLedger, limiter,
                 base_request_id: str, model: str, provider_name: str,
                 sleep_fn=time.sleep):
        self._inner = inner
        self._ledger = ledger
        self._limiter = limiter
        self._base = base_request_id
        self._model = model
        self._provider_name = provider_name
        self._sleep = sleep_fn
        self.http_calls = 0
        self.ceiling_charges = 0
        self.cost_usd = 0.0

    # `run_screening` bu iki yüzeyi kullanır
    @property
    def model(self):
        return self._inner.model

    @property
    def collector(self):
        return getattr(self._inner, "collector", None)

    def prompt_size_bytes(self, context, keywords):
        return self._inner.prompt_size_bytes(context, keywords)

    def close(self):
        close = getattr(self._inner, "close", None)
        if callable(close):
            close()

    def screen_batch(self, context, keywords):
        from app.core.screening.providers import ScreeningProviderError
        from app.core.screening.runner import ScreeningBudget, compute_cost_usd

        self.http_calls += 1
        request_id = f"{self._base}:c{self.http_calls}"
        prompt_bytes = self._inner.prompt_size_bytes(context, keywords)
        probe = ScreeningBudget(model=self._model, max_requests=None,
                                max_cost_usd=None)
        ceiling = probe.per_request_ceiling(prompt_bytes)
        # auto_attempt: çökmüş denemenin tavandan yakılmış satırı DURUR,
        # yeniden deneme kendi satırını alır (denetim izi bozulmaz)
        reservation_id = self._ledger.reserve(
            kind=BUDGET_SCREENING, request_id=request_id,
            ceiling_usd=ceiling, auto_attempt=True,
            stage="corpus_screening",
            provider=self._provider_name, model=self._model)
        try:
            with self._limiter.slot(sleep_fn=self._sleep):
                result = self._inner.screen_batch(context, keywords)
        except ScreeningProviderError as exc:
            # Parse/boş-içerik hatalarında token ZATEN faturalandı
            self._close(reservation_id, getattr(exc, "usage", None),
                        compute_cost_usd)
            raise
        except Exception:
            self._ledger.charge_ceiling(reservation_id)
            self.ceiling_charges += 1
            raise
        self._close(reservation_id, result.usage, compute_cost_usd)
        return result

    def _close(self, reservation_id: int, usage, compute_cost_usd) -> None:
        totals = _usage_totals(usage)
        if _usage_missing(totals):
            self._ledger.charge_ceiling(reservation_id)
            self.ceiling_charges += 1
            return
        cost = compute_cost_usd(self._model, totals)
        if cost is None:
            self._ledger.charge_ceiling(reservation_id)
            self.ceiling_charges += 1
            return
        self._ledger.settle(reservation_id, cost)
        self.cost_usd += float(cost)


def claim_job(session, *, job_id: int, task_id: Optional[str],
              attempt: ChannelAssignmentAttempt) -> CorpusScreeningJob:
    """TEK SAHİPLİ claim: `pending -> running` bir kazanan (10. tur #3).

    `task_id` bir worker LEASE'i DEĞİLDİR: aynı Celery task'ının mükerrer
    teslimi iki worker'a birden claim kazandırabiliyordu. Sahiplik artık
    satır kilidi + execution lease ile verilir:
      - `pending` satır: CAS ile tek kazanan, yeni lease açılır.
      - `running` satır: lease HÂLÂ GEÇERLİYSE claim REDDEDİLİR (mükerrer
        teslim), süresi dolmuşsa (önceki worker hard time limit ile ölmüş
        sayılır) devralınır ve `execution_attempt` artar.
    """
    job = session.execute(
        select(CorpusScreeningJob)
        .where(CorpusScreeningJob.id == job_id)
        .with_for_update()
    ).scalars().first()
    if job is None:
        raise ScreeningRunError(f"screening job {job_id} yok")
    if job.status in JOB_TERMINAL_STATES:
        raise ScreeningJobNotClaimable(
            f"job {job_id} terminal durumda ({job.status}) — yeniden "
            f"koşulamaz")
    if job.scoring_run_id != attempt.scoring_run_id             or job.brand_profile_id != attempt.brand_profile_id:
        raise ScreeningRunError(
            f"job {job_id} attempt {attempt.id} ile aynı run/workspace'te "
            f"değil — sahiplik reddi")
    if attempt.screening_job_id not in (None, job_id):
        raise ScreeningRunError(
            f"attempt {attempt.id} başka bir job'a bağlı "
            f"({attempt.screening_job_id})")
    if task_id is not None and job.task_id not in (None, task_id):
        raise ScreeningJobNotClaimable(
            f"job {job_id} başka task'a ait ({job.task_id}) — mükerrer "
            f"teslim reddedildi")
    now = datetime.now(timezone.utc)
    if job.status == JOB_STATUS_RUNNING:
        expires = job.execution_lease_expires_at
        if expires is not None and expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if job.execution_lease_id and (expires is None or expires > now):
            raise ScreeningJobNotClaimable(
                f"job {job_id} için CANLI execution lease var "
                f"({job.execution_lease_id}, bitiş {expires}) — eşzamanlı "
                f"mükerrer çalıştırma reddedildi")
    job.status = JOB_STATUS_RUNNING
    job.task_id = task_id if task_id is not None else job.task_id
    job.started_at = job.started_at or now
    job.execution_lease_id = uuid.uuid4().hex
    job.execution_lease_expires_at = now + timedelta(
        seconds=EXECUTION_LEASE_SECONDS)
    job.execution_attempt = int(job.execution_attempt or 0) + 1
    session.commit()
    session.refresh(job)
    return job


def verify_runner_contract(job: CorpusScreeningJob) -> None:
    """Kaydedilmiş sözleşme CANLI kodla birebir olmalı (13. tur #2).

    Job v1 sözleşmesiyle oluşturulup deploy sonrası v2 koduyla koşarsa,
    `contract_sha()` kaydedilmiş v1'i hash'lediği için eski checkpoint'ler
    kabul edilir ama devamı v2 davranışıyla üretilirdi: aynı kimlik altında
    İKİ FARKLI runner davranışı karışırdı. Harcamadan önce fail-closed.
    """
    live = screening_runner_contract()
    stored = job.runner_contract
    if stored is None:
        raise RunnerContractDrift(
            f"job {job.id} runner sözleşmesi MÜHÜRLENMEMİŞ — canlı kodla "
            f"karşılaştırılamaz (fail-closed)")
    if dict(stored) != dict(live):
        diff = sorted(k for k in set(stored) | set(live)
                      if stored.get(k) != live.get(k))
        raise RunnerContractDrift(
            f"job {job.id} runner sözleşmesi canlı koddan FARKLI "
            f"(alanlar: {diff}) — eski job yeni davranışla koşturulmaz")


def _verify_frozen_inputs(job: CorpusScreeningJob,
                          universe: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Mühürlü girdiler harcamadan ÖNCE doğrulanır (Codex 9. tur #5)."""
    try:
        context = screening_context_from_snapshot(
            (job.screening_context or {}).get("fields")
            or job.screening_context)
    except ScreeningContextMissing as exc:
        raise ScreeningRunError(f"bağlam snapshot'ı geçersiz: {exc}") from exc
    if context["context_sha256"] != job.context_sha256:
        raise ScreeningRunError(
            f"BAĞLAM MÜHRÜ UYUŞMUYOR: snapshot {context['context_sha256'][:12]}"
            f"... != job {str(job.context_sha256)[:12]}...")
    rows = [{"keyword_id": k["id"], "text": k["keyword"]} for k in universe]
    computed_universe = universe_sha256(rows)
    if computed_universe != job.universe_sha256:
        raise ScreeningRunError(
            f"EVREN MÜHRÜ UYUŞMUYOR: snapshot {computed_universe[:12]}... "
            f"!= job {str(job.universe_sha256)[:12]}...")
    return context


def run_screening_job(
    session_factory: Callable[[], Any],
    *,
    job_id: int,
    ledger: AiCostLedger,
    provider_factory: Optional[Callable[[], Any]] = None,
    limiter=None,
    sleep_fn=time.sleep,
    task_id: Optional[str] = None,
    concurrency: int = 1,
) -> Dict[str, Any]:
    """Bir screening job'ını checkpoint'li ve bütçe frenli koşturur."""
    limiter = limiter or NullInflightLimiter()
    session = session_factory()
    try:
        attempt = session.get(ChannelAssignmentAttempt, ledger.attempt_id)
        if attempt is None:
            raise ScreeningRunError(
                f"attempt {ledger.attempt_id} yok — sahiplik doğrulanamaz")
        job = claim_job(session, job_id=job_id, task_id=task_id,
                        attempt=attempt)
        # Codex 10. tur #1: ledger YALNIZ `running` sahipte harcar
        start_attempt(session, attempt_id=attempt.id, phase=PHASE_SCREENING)
        verify_runner_contract(job)
        universe = _rows_from_snapshot(job)
        job_snapshot_rows = list((job.input_snapshot or {}).get("rows") or [])
        context_doc = _verify_frozen_inputs(job, universe)
        job_pk = job.id
        provider_name = job.provider
        model = job.model
        prompt_version = job.prompt_version
        batch_size = int(job.batch_size)
        salts = list(job.view_salts or PLAN_SALTS)
        expected_contract = contract_sha(job)
    finally:
        session.close()

    from app.core.screening.runner import ScreeningContext

    ctx = ScreeningContext(
        product_definition=context_doc["fields"]["product_definition"],
        content_strategy=context_doc["fields"]["content_strategy"],
        social_mode=context_doc["fields"]["social_mode"],
        target_audience=context_doc["fields"]["target_audience"])

    # Tekil retry ÖNCELİĞİ deterministiktir: (kanal-bağımsız en iyi rank,
    # keyword_id). Snapshot'ta rank varsa kullanılır, yoksa id sırası.
    rank_by_id = {}
    for row in (job_snapshot_rows or []):
        ranks = [v for v in (row.get("ranks") or {}).values()
                 if isinstance(v, int)]
        rank_by_id[int(row["keyword_id"])] = min(ranks) if ranks else 10 ** 9

    plans = sticky_plans(universe, batch_size, salts=salts)
    units = [(view, ordinal, batch)
             for view, plan in zip(salts, plans)
             for ordinal, batch in enumerate(plan, start=1)]

    # Codex 12. tur #1: tekil retry PARALEL batch fazının İÇİNDE yapılmaz.
    # Paralel fazda hakkı hangi kelimenin kullanacağı provider gecikmesine
    # bağlı olurdu ve bütçe resume'da sıfırlanırdı. Bunun yerine batch'ler
    # bittikten sonra GÖRÜNÜM başına SIRALI, öncelik sıralı ve HER ÇAĞRISI
    # AYRI CHECKPOINT'li bir faz koşar (kota checkpoint'lerden yeniden
    # kurulur, ikinci kez ücret ödenmez).
    from app.core.screening.runner import SingleRetryBudget

    def _process(unit):
        view, ordinal, batch = unit
        return _process_batch(
            session_factory, job_pk=job_pk, view=view, ordinal=ordinal,
            batch=batch, ctx=ctx, ledger=ledger, limiter=limiter,
            provider_factory=provider_factory, model=model,
            provider_name=provider_name, prompt_version=prompt_version,
            batch_size=batch_size, expected_contract=expected_contract,
            sleep_fn=sleep_fn,
            single_retry_budget=SingleRetryBudget(limit=0))

    if concurrency and concurrency > 1:
        with ThreadPoolExecutor(max_workers=int(concurrency)) as pool:
            outcomes = list(pool.map(_process, units))
    else:
        outcomes = [_process(unit) for unit in units]

    single_outcomes = _run_single_retry_phase(
        session_factory, job_pk=job_pk, salts=salts, universe=universe,
        ctx=ctx, ledger=ledger, limiter=limiter,
        provider_factory=provider_factory, model=model,
        provider_name=provider_name, prompt_version=prompt_version,
        batch_size=batch_size, expected_contract=expected_contract,
        sleep_fn=sleep_fn, rank_by_id=rank_by_id)

    return finalize_job(
        session_factory, job_pk, salts, universe,
        attempt_id=ledger.attempt_id,
        extra={"checkpoint_reused": sum(1 for o in outcomes + single_outcomes
                                        if o == "reused"),
               "single_retry_calls": sum(1 for o in single_outcomes
                                         if o == "computed")})


def _process_batch(session_factory, *, job_pk, view, ordinal, batch, ctx,
                   ledger, limiter, provider_factory, model, provider_name,
                   prompt_version, batch_size, expected_contract, sleep_fn,
                   single_retry_budget=None, phase="batch",
                   missing_retry_rounds=None):
    bhash = batch_hash(batch)
    base_request_id = request_id_for(job_pk, view, bhash)

    # ── 1) checkpoint kontrolü (mühür doğrulamalı) ──────────────────
    session = session_factory()
    try:
        existing = session.execute(
            select(CorpusScreeningBatchCheckpoint).where(
                CorpusScreeningBatchCheckpoint.screening_job_id == job_pk,
                CorpusScreeningBatchCheckpoint.view == view,
                CorpusScreeningBatchCheckpoint.batch_hash == bhash)
        ).scalars().first()
        if existing is not None and existing.state == "completed":
            verify_checkpoint(existing, expected_contract)
            return "reused"
        # ── 2) ücret ödendi ama sonuç kayıp mı? ─────────────────────
        spent = session.execute(
            select(sa_func.count(AiCostReservation.id)).where(
                AiCostReservation.budget_owner_attempt_id
                == ledger.attempt_id,
                AiCostReservation.request_id.like(f"{base_request_id}:%"),
                AiCostReservation.state != STATE_RESERVED)
        ).scalar_one()
        if spent:
            raise ScreeningResultLost(
                f"{view}#{ordinal}: {spent} kapanmış rezervasyon var ama "
                f"checkpoint YOK — ücret ödenmiş, sonuç kayıp; sessiz "
                f"ücretli retry YAPILMAZ")
        open_rows = session.execute(
            select(AiCostReservation.id).where(
                AiCostReservation.budget_owner_attempt_id
                == ledger.attempt_id,
                AiCostReservation.request_id.like(f"{base_request_id}:%"),
                AiCostReservation.state == STATE_RESERVED)
        ).scalars().all()
    finally:
        session.close()

    for rid in open_rows:           # önceki denemenin açık rezervasyonu
        ledger.charge_ceiling(rid)

    # ── 3) provider çağrısı: HER HTTP denemesi ayrı rezervasyon ─────
    from app.core.screening.runner import run_screening

    inner = (provider_factory() if provider_factory
             else _default_provider(model, prompt_version))
    guarded = LedgerGuardedProvider(
        inner, ledger=ledger, limiter=limiter,
        base_request_id=base_request_id, model=model,
        provider_name=provider_name, sleep_fn=sleep_fn)
    try:
        result = run_screening(guarded, ctx, batch, seed=0,
                               batch_plan=[batch], batch_size=batch_size,
                               sleep_fn=sleep_fn,
                               single_retry_budget=single_retry_budget,
                               missing_retry_rounds=missing_retry_rounds)
    finally:
        guarded.close()

    # ── 4) doğrulanmış payload ile ATOMİK checkpoint ────────────────
    payload = {"results": [r.as_dict() for r in result.results],
               "stats": result.stats, "usage": result.usage,
               "phase": phase}
    session = session_factory()
    try:
        row = session.execute(
            select(CorpusScreeningBatchCheckpoint).where(
                CorpusScreeningBatchCheckpoint.screening_job_id == job_pk,
                CorpusScreeningBatchCheckpoint.view == view,
                CorpusScreeningBatchCheckpoint.batch_hash == bhash)
        ).scalars().first()
        if row is None:
            row = CorpusScreeningBatchCheckpoint(
                screening_job_id=job_pk, view=view, batch_ordinal=ordinal,
                batch_hash=bhash, request_contract_sha256=expected_contract)
            session.add(row)
        row.state = "completed"
        row.payload = payload
        row.payload_sha256 = _sha(payload)
        row.logical_request_id = base_request_id
        row.attempt = guarded.http_calls
        row.usage = result.usage
        row.cost_usd = guarded.cost_usd
        row.completed_at = datetime.now(timezone.utc)
        session.commit()
    finally:
        session.close()
    return "computed"


SINGLE_RETRY_VIEW_SUFFIX = "#single"


def base_view(view: str) -> str:
    """Tekil retry checkpoint'leri AYRI view etiketinde tutulur.

    Aynı etiket kullanılırsa tek kelimelik batch'in hash'i ana fazdaki
    batch ile ÇAKIŞIR ve retry sessizce "checkpoint reuse" olur.
    """
    return (view[:-len(SINGLE_RETRY_VIEW_SUFFIX)]
            if view.endswith(SINGLE_RETRY_VIEW_SUFFIX) else view)


def _single_retry_state(session, job_pk: int, view: str,
                        expected_contract: str):
    """(çözülmüş id kümesi, bu görünümde harcanmış tekil retry sayısı).

    Kota checkpoint'lerden YENİDEN KURULUR: resume'da bütçe sıfırlanmaz
    (Codex 12. tur #1).
    """
    rows = session.execute(
        select(CorpusScreeningBatchCheckpoint)
        .where(CorpusScreeningBatchCheckpoint.screening_job_id == job_pk,
               CorpusScreeningBatchCheckpoint.view.in_(
                   (view, f"{view}{SINGLE_RETRY_VIEW_SUFFIX}")),
               CorpusScreeningBatchCheckpoint.state == "completed")
    ).scalars().all()
    resolved, spent = set(), 0
    for row in rows:
        # Bütçe ve resolved kümesi YALNIZ mührü doğrulanmış
        # checkpoint'lerden kurulur (Codex 13. tur #3)
        verify_checkpoint(row, expected_contract)
        payload = row.payload or {}
        if (payload.get("phase") == "single_retry"):
            spent += 1
        for item in (payload.get("results") or []):
            if not item.get("unresolved"):
                resolved.add(int(item["keyword_id"]))
    return resolved, spent


def _run_single_retry_phase(session_factory, *, job_pk, salts, universe,
                            ctx, ledger, limiter, provider_factory, model,
                            provider_name, prompt_version, batch_size,
                            expected_contract, sleep_fn, rank_by_id):
    """Görünüm başına SIRALI tekil retry fazı (her çağrı checkpoint'li)."""
    from app.core.screening.runner import (
        SINGLE_RETRY_LIMIT,
        SingleRetryBudget,
    )

    by_id = {int(k["id"]): k for k in universe}
    outcomes = []
    for view in salts:
        session = session_factory()
        try:
            resolved, spent = _single_retry_state(
                session, job_pk, view, expected_contract)
        finally:
            session.close()
        pending = [kid for kid in by_id if kid not in resolved]
        # Deterministik öncelik: sağlayıcı gecikmesi sırayı BELİRLEMEZ
        pending.sort(key=lambda kid: (rank_by_id.get(kid, 10 ** 9), kid))
        for kid in pending:
            if spent >= SINGLE_RETRY_LIMIT:
                break
            spent += 1
            outcomes.append(_process_batch(
                session_factory, job_pk=job_pk,
                view=f"{view}{SINGLE_RETRY_VIEW_SUFFIX}",
                ordinal=10_000 + spent,
                batch=[by_id[kid]], ctx=ctx, ledger=ledger, limiter=limiter,
                provider_factory=provider_factory, model=model,
                provider_name=provider_name, prompt_version=prompt_version,
                batch_size=batch_size, expected_contract=expected_contract,
                sleep_fn=sleep_fn, phase="single_retry",
                # Tek kelimelik istekte eksik-ID turu yok; iç tekil-retry
                # de kapalıdır — BU FAZ zaten tekil retry'dır (aksi halde
                # her birim iki çağrı yapar ve kota iki kat harcanırdı)
                missing_retry_rounds=0,
                single_retry_budget=SingleRetryBudget(limit=0)))
    return outcomes


def _default_provider(model: str, prompt_version: str):
    from app.core.screening.bakeoff_inputs import make_provider

    return make_provider(model, prompt_version=prompt_version)


def _recompute_stats(session, job_id: int, attempt_id: int,
                     checkpoints) -> Dict[str, Any]:
    """Özet DB'den yeniden üretilir (Codex 9. tur #2).

    Resume'da bellek sayacı YALNIZ yeni koşulan batch'leri bilir; maliyet,
    çağrı sayısı, unresolved ve ihlaller TÜM checkpoint + ledger
    kayıtlarından hesaplanır.
    """
    prefix = f"scr:{job_id}:"
    rows = session.execute(
        select(AiCostReservation.state,
               sa_func.count(AiCostReservation.id),
               sa_func.coalesce(sa_func.sum(
                   sa_func.coalesce(AiCostReservation.actual_usd,
                                    AiCostReservation.ceiling_usd)), 0))
        .where(AiCostReservation.budget_owner_attempt_id == attempt_id,
               AiCostReservation.request_id.like(f"{prefix}%"))
        .group_by(AiCostReservation.state)
    ).all()
    requests = sum(int(c) for _s, c, _a in rows)
    cost = sum(float(a) for _s, _c, a in rows)
    ceiling_charges = sum(int(c) for s, c, _a in rows
                          if s == "ceiling_charged")
    violations = 0
    for cp in checkpoints:
        stats = (cp.payload or {}).get("stats") or {}
        cv = stats.get("contract_violations") or {}
        violations += sum(v for v in cv.values() if isinstance(v, int))
    # `unresolved` BURADA sayılmaz: tekil retry fazı aynı kelime için
    # ikinci bir checkpoint yazar ve ham toplam çözülmüş kelimeyi hâlâ
    # unresolved sayardı — nihai sayım finalize'daki birleştirmeden gelir
    return {"provider_calls": requests, "cost_usd": round(cost, 6),
            "ceiling_charges": ceiling_charges,
            "contract_violations": violations}


def finalize_job(session_factory, job_id: int, salts: List[str],
                 universe: List[Dict[str, Any]], *, attempt_id: int,
                 extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Kapılar + kararların TEK transaction'da yazımı (plan §5.4)."""
    session = session_factory()
    try:
        job = session.get(CorpusScreeningJob, job_id)
        checkpoints = session.execute(
            select(CorpusScreeningBatchCheckpoint).where(
                CorpusScreeningBatchCheckpoint.screening_job_id == job_id,
                CorpusScreeningBatchCheckpoint.state == "completed")
        ).scalars().all()
        # Kararlar YALNIZ mührü doğrulanmış checkpoint'lerden üretilir
        expected_contract = contract_sha(job)
        for cp in checkpoints:
            verify_checkpoint(cp, expected_contract)
        stats = _recompute_stats(session, job_id, attempt_id, checkpoints)
        stats.update(extra or {})

        # Tekil retry fazı aynı kelime için İKİNCİ bir checkpoint yazar;
        # birleştirmede ÇÖZÜLMÜŞ sonuç unresolved olanı EZER (Codex 12. #1)
        views: Dict[str, Dict[int, Any]] = {salt: {} for salt in salts}
        for cp in checkpoints:
            view = base_view(cp.view)
            if view not in views:
                continue
            for row in _results_from_payload(cp.payload):
                current = views[view].get(row.keyword_id)
                if current is None or (current.unresolved
                                       and not row.unresolved):
                    views[view][row.keyword_id] = row
        views = {salt: sorted(rows.values(), key=lambda r: r.keyword_id)
                 for salt, rows in views.items()}

        stats["unresolved"] = sum(
            1 for salt in salts for row in views[salt] if row.unresolved)

        universe_ids = {int(k["id"]) for k in universe}
        coverage_ok = all(
            {r.keyword_id for r in views[salt]} == universe_ids
            for salt in salts)
        resolved_counts = [len({r.keyword_id for r in views[salt]}
                               & universe_ids) for salt in salts]
        total = len(universe_ids) * max(len(salts), 1)
        unresolved_ratio = (stats["unresolved"] / total) if total else 1.0

        failure = None
        if not coverage_ok:
            failure = "COVERAGE_INCOMPLETE"
        elif unresolved_ratio > MAX_UNRESOLVED_RATIO:
            failure = "UNRESOLVED_ABOVE_THRESHOLD"
        elif stats["contract_violations"] > 0:
            failure = "CONTRACT_VIOLATION"

        job.actual_requests = stats["provider_calls"]
        job.ceiling_charges = stats["ceiling_charges"]
        job.cost_usd = stats["cost_usd"]
        job.coverage_resolved = min(resolved_counts) if resolved_counts else 0
        job.unresolved_count = stats["unresolved"]
        job.contract_violations = stats["contract_violations"]
        job.completed_at = datetime.now(timezone.utc)

        if failure:
            job.status = JOB_STATUS_FALLBACK
            job.error_code = failure
            job.error_message = (
                f"kapı ihlali: coverage_ok={coverage_ok} "
                f"unresolved_ratio={unresolved_ratio:.4f} "
                f"violations={stats['contract_violations']}")
            session.commit()
            return {"status": JOB_STATUS_FALLBACK, "reason": failure,
                    **stats}

        # Kararlar IMMUTABLE: varsa DOKUNULMAZ (idempotent yeniden koşum)
        existing = session.execute(
            select(sa_func.count(CorpusScreeningDecision.id)).where(
                CorpusScreeningDecision.screening_job_id == job_id)
        ).scalar_one()
        merged = merge_multi_views({salt: views[salt] for salt in salts})
        expected_rows = len(merged) * len(CHANNELS)
        if existing:
            if existing != expected_rows:
                raise ScreeningRunError(
                    f"karar sayısı tutarsız: DB {existing} != beklenen "
                    f"{expected_rows} — immutable tablo yeniden yazılmaz")
        else:
            score_ids = _score_id_map(job)
            reason_by_view = _reason_lookup(checkpoints, salts)
            for res in merged:
                for channel in CHANNELS:
                    key = channel.lower()
                    raw_a = (res.raw_views.get(salts[0]) or {})
                    raw_b = (res.raw_views.get(salts[-1]) or {})
                    session.add(CorpusScreeningDecision(
                        screening_job_id=job_id,
                        keyword_score_id=score_ids[res.keyword_id],
                        keyword_id=res.keyword_id, channel=channel,
                        view_a_fit=raw_a.get(key),
                        view_a_reason=reason_by_view.get(
                            (salts[0], res.keyword_id, key)),
                        view_a_unresolved=salts[0] in res.unresolved_views,
                        view_b_fit=raw_b.get(key),
                        view_b_reason=reason_by_view.get(
                            (salts[-1], res.keyword_id, key)),
                        view_b_unresolved=salts[-1] in res.unresolved_views,
                        merged_fit=res.mean_fit.get(key),
                        passing=bool(res.passing.get(key)),
                        disagreement=bool(res.context_disagreement.get(key)),
                        uncertain=bool(res.uncertain),
                        contract_violations=_violations_for(
                            checkpoints, res.keyword_id)))
        job.status = JOB_STATUS_COMPLETED
        job.error_code = None
        job.error_message = None
        try:
            session.commit()
        except IntegrityError as exc:
            # `uq_screening_job_completed_identity`: ayni kimlikte zaten
            # dogrulanmis bir job var. Bu UNEXPECTED degil, TIPLI bir
            # durumdur; dogru davranis reuse'dur (dispatch bunu artik
            # onceden yapar — burasi yaris/eski kosu icin guvenlik agi).
            session.rollback()
            raise ScreeningIdentityConflict(
                f"job {job_id}: ayni screening kimliginde zaten completed "
                f"bir job var — sonuc REUSE edilmeli ({exc.orig})") from exc
        return {"status": JOB_STATUS_COMPLETED, "decisions": expected_rows,
                "applied_screening_channels": list(
                    job.applied_screening_channels
                    or SCREENING_APPLIED_CHANNELS_V3),
                **stats}
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _reason_lookup(checkpoints, salts) -> Dict[tuple, Optional[str]]:
    """(view, keyword_id, channel) → reason code (audit alanı)."""
    out: Dict[tuple, Optional[str]] = {}
    for cp in checkpoints:
        for row in ((cp.payload or {}).get("results") or []):
            codes = row.get("reason_codes") or {}
            for channel in ("ads", "seo", "social"):
                out[(cp.view, int(row["keyword_id"]), channel)] = \
                    codes.get(channel)
    return out


def _violations_for(checkpoints, keyword_id: int) -> Optional[List[str]]:
    hits: List[str] = []
    for cp in checkpoints:
        for row in ((cp.payload or {}).get("results") or []):
            if int(row["keyword_id"]) == keyword_id:
                hits.extend(row.get("membership_violations") or [])
    return hits or None
