# -*- coding: utf-8 -*-
"""`corpus_screening.run` — ayrı kuyrukta çalışan tarama task'ı (plan §7.5).

Neden ayrı task/kuyruk: tarama uzun sürer (evren × 2 görünüm) ve normal
export/generation kuyruğunu aç bırakmamalıdır. Soft/hard limit tarama
zarfına göre ayarlanır; soft timeout provider'ı kapatır ve assistive
akışı baseline fallback'e geçirir.

Task İNCE bir sarmalayıcıdır: davranış `app/core/screening/
production_runner.py` içinde yaşar ve doğrudan test edilir.
"""
from __future__ import annotations

from datetime import datetime, timezone

from celery.exceptions import SoftTimeLimitExceeded
from loguru import logger

from app.tasks.celery_app import celery_app

CORPUS_SCREENING_QUEUE = "corpus_screening"
SOFT_TIME_LIMIT = 1080
HARD_TIME_LIMIT = 1200


def _mark_job(session, job_id: int, *, status: str, error_code: str,
              error_message: str, task_id: str = None) -> int:
    """Terminal job GERİYE DÜŞÜRÜLMEZ (Codex 9. tur #4).

    İkinci broker teslimi ilk çalışan/bitmiş job'ı `failed` yapamaz:
    yalnız pending/running satır ve (varsa) AYNI task sahibi güncellenir.
    """
    from sqlalchemy import update

    from app.core.screening.production_runner import JOB_TERMINAL_STATES
    from app.database.models import CorpusScreeningJob

    stmt = (update(CorpusScreeningJob)
            .where(CorpusScreeningJob.id == job_id,
                   CorpusScreeningJob.status.notin_(JOB_TERMINAL_STATES))
            .values(status=status, error_code=error_code,
                    error_message=(error_message or "")[:2000],
                    completed_at=datetime.now(timezone.utc)))
    if task_id is not None:
        stmt = stmt.where(
            (CorpusScreeningJob.task_id.is_(None))
            | (CorpusScreeningJob.task_id == task_id))
    result = session.execute(stmt)
    session.commit()
    return int(result.rowcount or 0)


def _release_deferred_parent(payload) -> bool:
    """Tarama bitti: ertelenmis parent atamasini kuyruga ver.

    Assistive'de parent, tarama tamamlanmadan kosarsa union HICBIR ZAMAN
    uygulanamazdi (job `completed` degil -> baseline). Bu yuzden parent
    dispatch'i buraya ertelenir. Idempotent: `pending -> sent` CAS'i
    ikinci broker teslimini eler. Broker hatasinda parent task ve attempt
    fail-closed kapatilir, run durumu geri alinir.
    """
    if not payload:
        return False
    from app.core.screening.attempt_state import (
        claim_parent_release,
        mark_parent_published,
    )
    from app.database.connection import SessionLocal
    from app.tasks.intent_tasks import run_channel_assignment_task

    parent_task_id = payload.get("parent_task_id")
    session = SessionLocal()
    try:
        # FAIL-CLOSED: payload attempt ile birebir eslesmiyorsa (ya da
        # tarama terminal degilse) parent HIC kuyruga verilmez
        dispatch = claim_parent_release(session, payload=payload)
        if dispatch is None:
            logger.info(f"ertelenmis parent salinmadi ({parent_task_id}): "
                        f"claim reddedildi")
            return False
        try:
            run_channel_assignment_task.apply_async(
                args=dispatch["args"],
                kwargs=dispatch["kwargs"],
                task_id=parent_task_id)
            mark_parent_published(session,
                                  attempt_id=payload.get("attempt_id"))
            return True
        except Exception as exc:  # noqa: BLE001
            logger.error(f"ertelenmis parent kuyruga verilemedi "
                         f"({parent_task_id}): {exc}")
            from app.core.channel.assignment_dispatcher import (
                rollback_assignment_status,
                set_task_failed,
            )
            from app.core.screening.dispatch import fail_attempt

            set_task_failed(session, parent_task_id, str(exc))
            fail_attempt(session, parent_task_id, code="ENQUEUE_FAILED",
                         message=str(exc))
            previous = dispatch["previous_status"]
            if previous:
                rollback_assignment_status(
                    session, payload.get("scoring_run_id"), previous)
            return False
    finally:
        session.close()


def _refuse_legacy_screening(*, job_id: int, attempt_id: int, task_id,
                             deferred_parent) -> dict | None:
    """Attempt eski motor (v2/v2_1) run'ına aitse taramayı fail-closed kapatır.

    Sağlayıcı/ledger/limiter hiç kurulmaz. Dönüş: red sonucu (dict) ya da
    run eski değilse None (normal akış sürer).
    """
    from app.core.engine_version_gate import (
        LEGACY_RUN_READ_ONLY,
        legacy_run_by_id,
        legacy_run_message,
        run_algorithm_version,
    )
    from app.core.screening.attempt_state import (
        claim_parent_release,
        try_finish_attempt,
    )
    from app.database.connection import SessionLocal
    from app.database.models import ChannelAssignmentAttempt

    session = SessionLocal()
    try:
        attempt = session.get(ChannelAssignmentAttempt, attempt_id) \
            if attempt_id else None
        run = legacy_run_by_id(
            session, attempt.scoring_run_id if attempt is not None else None)
        if run is None:
            return None
        version = run_algorithm_version(run)
        message = legacy_run_message(version)
        logger.warning(f"corpus_screening reddedildi ({LEGACY_RUN_READ_ONLY}):"
                       f" job {job_id}, run {run.id} ({version})")
        _mark_job(session, job_id, status="failed",
                  error_code=LEGACY_RUN_READ_ONLY, error_message=message,
                  task_id=task_id)
        _mark_child_task(session, task_id, status="failed", progress=100,
                         error_message=message,
                         result_data={"status": "failed",
                                      "reason": LEGACY_RUN_READ_ONLY})
        if deferred_parent:
            # Ertelenmis parent KUYRUGA VERILMEZ: yayim hakki CAS ile alinir
            # ve broker-hatasi kalibiyla kapatilir (task+attempt failed,
            # run durumu geri alinir)
            dispatch = claim_parent_release(session, payload=deferred_parent)
            if dispatch is not None:
                from app.core.channel.assignment_dispatcher import (
                    rollback_assignment_status,
                    set_task_failed,
                )
                from app.core.screening.dispatch import fail_attempt

                parent_task_id = deferred_parent.get("parent_task_id")
                set_task_failed(session, parent_task_id, message)
                fail_attempt(session, parent_task_id,
                             code=LEGACY_RUN_READ_ONLY, message=message)
                previous = dispatch["previous_status"]
                if previous:
                    rollback_assignment_status(
                        session, deferred_parent.get("scoring_run_id"),
                        previous)
        try_finish_attempt(session, attempt_id=attempt_id,
                           error_code=LEGACY_RUN_READ_ONLY,
                           error_message=message)
        return {"status": "failed", "reason": LEGACY_RUN_READ_ONLY,
                "algorithm_version": version}
    finally:
        session.close()


@celery_app.task(name="corpus_screening.reconcile_deferred_parents")
def reconcile_deferred_parents_task():
    """Recover broker publishes lost after the DB ``publishing`` commit."""
    from app.core.channel.assignment_dispatcher import (
        republish_all_stale_deferred_parents,
    )
    from app.database.connection import SessionLocal

    session = SessionLocal()
    try:
        return republish_all_stale_deferred_parents(session)
    finally:
        session.close()


def _attempt_is_assistive(attempt_id) -> bool:
    """Attempt assistive mi? (bilinmiyorsa False — shadow davranisi)."""
    from app.database.connection import SessionLocal
    from app.database.models import ChannelAssignmentAttempt

    if not attempt_id:
        return False
    session = SessionLocal()
    try:
        attempt = session.get(ChannelAssignmentAttempt, attempt_id)
        return bool(attempt is not None
                    and attempt.screening_mode == "assistive")
    finally:
        session.close()


def _materialize_counterfactual(job_id: int,
                                attempt_id: int = None) -> dict:
    """Shadow ölçümü: 3B karşı-olgu satırları (canlı havuza DOKUNMAZ).

    Ölçüm yazılamazsa tarama sonucu (ücreti ödenmiş kararlar) GEÇERSİZ
    SAYILMAZ — job `completed` kalır, sebep tipli olarak raporlanır.
    """
    from app.core.screening.counterfactual import (
        CounterfactualError,
        materialize_counterfactual,
    )
    from app.database.connection import SessionLocal

    session = SessionLocal()
    try:
        return materialize_counterfactual(session, job_id=job_id,
                                          attempt_id=attempt_id)
    except CounterfactualError as exc:
        session.rollback()
        logger.error(f"counterfactual materyalizasyon başarısız "
                     f"(job {job_id}): {exc}")
        return {"status": "failed", "reason": exc.code,
                "message": exc.message}
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        logger.exception(f"counterfactual beklenmeyen hata (job {job_id})")
        return {"status": "failed", "reason": "UNEXPECTED",
                "message": str(exc)[:500]}
    finally:
        session.close()


CHILD_ACTIVE_STATES = ("pending", "running")


def _mark_child_task(session, task_id, *, status, progress=None,
                     result_data=None, error_message=None) -> bool:
    """Child `TaskResult` yaşam döngüsü — ATOMİK (Codex 13. tur #1).

    Kayıt satır kilidiyle okunur ve YALNIZ aktifse (`pending|running`)
    yazılır: gecikmiş mükerrer teslim, tamamlanmış child kaydını (hata
    kolları dahil) HİÇBİR koşulda bozamaz. Okuma-sonra-yazma yarışı satır
    kilidiyle kapatılır; `only_active` opsiyonel DEĞİLDİR.
    """
    from app.database.models import TaskResult

    if not task_id:
        return False
    row = (session.query(TaskResult)
           .filter(TaskResult.task_id == task_id)
           .with_for_update()
           .first())
    if row is None:
        session.rollback()
        return False
    if row.status not in CHILD_ACTIVE_STATES:
        session.rollback()          # terminal kayıt geriye taşınmaz
        return False
    row.status = status
    if progress is not None:
        row.progress = progress
    if error_message is not None:
        row.error_message = (error_message or "")[:2000]
    if result_data is not None:
        row.result_data = {**(row.result_data or {}), **result_data}
    session.commit()
    return True


def _child_task_status(job_status: str) -> str:
    """Job durumu -> TaskResult durumu (iki yüzey ÇELİŞMEZ)."""
    if job_status == "completed":
        return "completed"
    if job_status in ("fallback", "not_needed"):
        # Fallback bir HATA değil: baseline sürüyor, iş "tamamlandı" ama
        # sonucu uygulanmadı — UI bunu result_data'daki reason'dan okur
        return "completed"
    return "failed"


def _status_for(exc) -> str:
    """Sonuç kaybı/kapı ihlali FALLBACK'tir (baseline sürer); sözleşme
    ihlali FAILED (tipli, sessiz devam YOK)."""
    from app.core.screening.production_runner import (
        ScreeningIdentityConflict,
        ScreeningResultLost,
    )

    # Kimlik catismasi: kararlar ZATEN var, baseline surer -> fallback
    return ("fallback"
            if isinstance(exc, (ScreeningResultLost, ScreeningIdentityConflict))
            else "failed")


# `shared_task` KULLANILMAZ (canli smoke testi bulgusu): FastAPI/uvicorn
# process'inde `-A` verilmedigi icin shared_task cagri aninda DEFAULT
# Celery app'ine baglaniyor (broker=None -> amqp://localhost -> ECONNREFUSED)
# ve shadow cocugu hic kuyruga girmiyordu. Proje app'ine ACIK baglama sart.
@celery_app.task(name="corpus_screening.run", bind=True,
                 queue=CORPUS_SCREENING_QUEUE,
                 soft_time_limit=SOFT_TIME_LIMIT, time_limit=HARD_TIME_LIMIT)
def run_corpus_screening_task(self, *, job_id: int, attempt_id: int,
                              expected_screening_cap_usd: str,
                              expected_downstream_cap_usd: str,
                              deferred_parent: dict = None):
    """Tarama işini koşturur; hata hâlinde job'ı TİPLİ olarak kapatır.

    Cap'ler string (Decimal-güvenli) taşınır: preflight'ta onaylanan
    değerler ledger'da DB ile birebir doğrulanır (fail-closed).
    """
    from app.core.screening.attempt_state import try_finish_attempt
    from app.core.screening.inflight import InflightUnavailable, build_limiter
    from app.core.screening.production_runner import (
        ScreeningRunError,
        run_screening_job,
    )
    from app.core.telemetry.ai_cost_budget import AiCostLedger, BudgetError
    from app.config import settings
    from app.database.connection import SessionLocal

    # Eski motor run'ı salt-okunur (task sınırı): ledger, limiter ve
    # sağlayıcı çağrısı BAŞLAMAZ; job + child task tipli sebeple kapanır.
    # Ertelenmiş parent kuyruğa VERİLMEZ — broker-hatası kalıbıyla
    # fail-closed kapatılır ve run durumu geri alınır.
    legacy = _refuse_legacy_screening(
        job_id=job_id, attempt_id=attempt_id,
        task_id=getattr(self.request, "id", None),
        deferred_parent=deferred_parent)
    if legacy is not None:
        return legacy

    ledger = AiCostLedger(
        SessionLocal, attempt_id=attempt_id,
        expected_screening_cap_usd=expected_screening_cap_usd,
        expected_downstream_cap_usd=expected_downstream_cap_usd)
    try:
        limiter = build_limiter(
            settings.REDIS_URL,
            limit=settings.CORPUS_SCREENING_GLOBAL_INFLIGHT)
    except InflightUnavailable as exc:
        # Sınırsız çağrıya DÜŞÜLMEZ: job fallback, parent baseline sürer
        logger.error(f"corpus_screening limiter yok (job {job_id}): {exc}")
        session = SessionLocal()
        try:
            _mark_job(session, job_id, status="fallback",
                      error_code="INFLIGHT_LIMITER_UNAVAILABLE",
                      error_message=str(exc),
                      task_id=getattr(self.request, "id", None))
            _mark_child_task(
                session, getattr(self.request, "id", None),
                status="completed", progress=100,
                result_data={"status": "fallback",
                             "reason": "INFLIGHT_LIMITER_UNAVAILABLE"})
            try_finish_attempt(session, attempt_id=attempt_id)
        finally:
            session.close()
        _release_deferred_parent(deferred_parent)
        return {"status": "fallback",
                "reason": "INFLIGHT_LIMITER_UNAVAILABLE"}

    task_id = getattr(self.request, "id", None)
    session = SessionLocal()
    try:
        # Codex 12. tur #2: terminal child kaydı GERİYE taşınmaz
        _mark_child_task(session, task_id, status="running", progress=5,
                         result_data={"screening_job_id": job_id})
    finally:
        session.close()

    try:
        result = run_screening_job(
            SessionLocal, job_id=job_id, ledger=ledger, limiter=limiter,
            # Sahiplik: yalnız bu task'a ait job claim edilebilir
            task_id=task_id,
            concurrency=settings.CORPUS_SCREENING_CONCURRENCY)
        if result.get("status") == "completed":
            # ASSISTIVE'de audit satirlarini PARENT yazar (is_applied=true).
            # Burada `is_applied=false` karsi-olgu yazmak ayni
            # (run, attempt, keyword, kanal, action) anahtarini tuketir ve
            # parent'in canli yazimini `uq_candidate_selection` ile
            # dusururdu (Codex 24. tur #1). Shadow davranisi AYNEN kalir.
            if _attempt_is_assistive(attempt_id):
                result["counterfactual"] = {
                    "status": "skipped",
                    "reason": "APPLIED_BY_PARENT",
                    "message": "assistive: audit satirlarini parent yazar"}
            else:
                result["counterfactual"] = _materialize_counterfactual(
                    job_id, attempt_id=attempt_id)
        session = SessionLocal()
        try:
            _mark_child_task(
                session, task_id,
                status=_child_task_status(result.get("status", "failed")),
                progress=100, result_data=result)
            # Parent zaten bittiyse attempt'i KAPATAN taraf budur
            try_finish_attempt(session, attempt_id=attempt_id)
        finally:
            session.close()
        # Tarama bitti (basarili ya da degil): parent SIMDI kosabilir
        _release_deferred_parent(deferred_parent)
        return result
    except SoftTimeLimitExceeded as exc:
        logger.warning(f"corpus_screening soft timeout (job {job_id})")
        session = SessionLocal()
        try:
            _mark_job(session, job_id, status="fallback",
                      error_code="SCREENING_SOFT_TIMEOUT",
                      error_message=str(exc),
                      task_id=task_id)
            _mark_child_task(session, task_id, status="completed",
                             progress=100,
                             result_data={"status": "fallback",
                                          "reason": "SCREENING_SOFT_TIMEOUT"})
            try_finish_attempt(session, attempt_id=attempt_id)
        finally:
            session.close()
        _release_deferred_parent(deferred_parent)
        return {"status": "fallback", "reason": "SCREENING_SOFT_TIMEOUT"}
    except (ScreeningRunError, BudgetError) as exc:
        logger.error(f"corpus_screening hata (job {job_id}): {exc}")
        job_status = _status_for(exc)
        session = SessionLocal()
        try:
            _mark_job(session, job_id, status=job_status,
                      error_code=getattr(exc, "error_code",
                                         type(exc).__name__),
                      error_message=str(exc), task_id=task_id)
            _mark_child_task(
                session, task_id, status=_child_task_status(job_status),
                progress=100, error_message=str(exc),
                result_data={"status": job_status,
                             "reason": getattr(exc, "error_code",
                                               type(exc).__name__)})
            try_finish_attempt(session, attempt_id=attempt_id)
        finally:
            session.close()
        _release_deferred_parent(deferred_parent)
        # Codex 10. tur #6: iki yüzey ÇELİŞMEZ — job fallback ise dönüş de
        # fallback'tir (eskiden job fallback iken task "failed" diyordu)
        return {"status": job_status,
                "reason": getattr(exc, "error_code", type(exc).__name__)}
    except Exception as exc:  # noqa: BLE001
        logger.exception(f"corpus_screening beklenmeyen hata (job {job_id})")
        session = SessionLocal()
        try:
            _mark_job(session, job_id, status="failed",
                      error_code="UNEXPECTED", error_message=str(exc),
                      task_id=task_id)
            _mark_child_task(session, task_id, status="failed", progress=100,
                             error_message=str(exc))
            try_finish_attempt(session, attempt_id=attempt_id)
        finally:
            session.close()
        # Beklenmeyen hatada da parent kosmali: tarama olmadan BASELINE
        _release_deferred_parent(deferred_parent)
        raise
