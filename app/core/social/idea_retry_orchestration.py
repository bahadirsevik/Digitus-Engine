# -*- coding: utf-8 -*-
"""Sosyal Brief Fikir Tekrar Deneme (ideas_retry) Orkestrasyon Servisi (F1-F.7.8).

Bu modül Claim -> Worker Input Hazırlığı -> Kategori Bazlı AI Üretimi -> Heartbeat
-> Atomik Persistence zincirini transaction-safe biçimde koordine eder.

Temel Kurallar:
- Celery task veya API endpoint İÇERMEZ.
- AI çağrısı sırasında hiçbir DB session veya row lock AÇIK TUTULMAZ.
- Claim, worker input, heartbeat ve persistence ayrı transaction'larda çalışır.
- Tek bir session bütün işlem boyunca taşınmaz (session_factory kullanılır).
- Completed replay yolunda AI çağrısı yapılmaz, completed DB doğrulaması yapılır.
- Başarısızlık durumunda fail-closed failure finalization yapılır.
- İlk kategori(ler) başarılı olup sonraki kategori hata verirse, elde edilen sonuçlar
  persist edilerek attempt partial olarak finalize edilir.
- İlk AI çağrısı başarısız olursa attempt failed yapılır ve hata fırlatılır.
- Heartbeat veya persistence hatasında asla partial uydurulmaz; attempt failed yapılır.
- Hata mesajlarında asla ham AI çıktısı, SQL veya provider hata detayı sızdırılmaz.
- temperature=None ayarı korunur, gerçek ağ çağrısı yapılmaz.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.core.social.idea_retry_persistence import (
    PersistedSocialIdeaRetryResult,
    SocialIdeaRetryPersistenceError,
    persist_social_idea_retry_results,
)
from app.core.social.idea_retry_worker_input import (
    SocialIdeaRetryWorkerInputError,
    SocialIdeaRetryWorkerPreparation,
    prepare_social_idea_retry_worker_inputs,
)
from app.generators.social.attempt_state import (
    AttemptNotClaimableError,
    AttemptNotWritableError,
    claim_ideas_retry_attempt_for_worker,
    finalize_ideas_retry_attempt_failure,
    heartbeat_attempt,
)
from app.generators.social.brief_idea_generator import (
    SocialBriefIdeaGenerator,
    SocialIdeaAIResult,
    SocialIdeaGenerationError,
)

logger = logging.getLogger(__name__)

# Retry denemesi başına sabit AI çağrı tavanı: retry planı kategori başına TEK istek
# üretir (eksik hedef + boş kategori talepleri birleşir) -> en fazla 6 istek x 2 çağrı
# (1 ana + 1 JSON düzeltme tekrarı) = 12. Tavanı aşacak istek hiç başlatılmaz.
MAX_AI_CALLS_PER_RETRY_CATEGORY_REQUEST = 2
IDEA_RETRY_ATTEMPT_MAX_AI_CALLS = 12


class SocialIdeaRetryOrchestrationError(ValueError):
    """Fikir tekrar deneme orkestrasyon hatası."""

    def __init__(
        self,
        message: str,
        *,
        error_code: str = "IDEA_RETRY_ORCHESTRATION_FAILED",
        reason_code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.reason_code = reason_code


class SocialIdeaRetryHeartbeatError(ValueError):
    """Retry heartbeat sırasında oluşan hata."""

    def __init__(self, message: str = "Heartbeat başarısız oldu.") -> None:
        super().__init__(message)
        self.error_code = "IDEA_HEARTBEAT_FAILED"


@dataclass(frozen=True)
class SocialIdeaRetryOrchestrationResult:
    """Fikir tekrar deneme orkestrasyon sonucu (immutable)."""

    attempt_id: int
    brief_id: int
    scoring_run_id: int
    status: str
    newly_persisted_count: int
    accepted_target_ids: tuple[int, ...]
    unfilled_target_ids: tuple[int, ...]
    ai_calls_used: int
    replayed: bool
    # K4: bu denemede hâlâ fikir almamış boş kategoriler (legacy snapshot'ta daima boş)
    unfilled_category_ids: tuple[int, ...] = ()


def _resolve_now(now_provider: Any) -> datetime:
    """now_provider argümanından timezone-aware UTC datetime üretir."""
    if callable(now_provider):
        dt = now_provider()
    elif isinstance(now_provider, datetime):
        dt = now_provider
    elif now_provider is None:
        dt = datetime.now(timezone.utc)
    else:
        raise ValueError("now_provider callable, datetime veya None olmalıdır.")

    if not isinstance(dt, datetime) or dt.tzinfo is None:
        raise ValueError("now_provider timezone-aware datetime üretmelidir.")
    return dt.astimezone(timezone.utc)


def _map_failure_reason(exc: Exception) -> tuple[str, str]:
    """İstisnayı allowlist içindeki sabit reason_code ve temiz hata mesajına eşler."""
    if isinstance(exc, SocialIdeaGenerationError):
        if exc.error_code == "IDEA_PROVIDER_ERROR":
            return (
                "idea_provider_error",
                "Yapay zeka servisi sağlayıcı hatası verdi.",
            )
        elif exc.error_code in ("IDEA_OUTPUT_INVALID", "IDEA_SCHEMA_ERROR"):
            return (
                "idea_output_invalid",
                "Yapay zeka çıktısı doğrulama kurallarına uymadı.",
            )
        elif exc.error_code in ("IDEA_INPUT_INVALID", "IDEA_GENERATION_NOT_ALLOWED"):
            return (
                "idea_input_invalid",
                "Fikir üretimi girdi doğrulaması başarısız oldu.",
            )
        return (
            "idea_output_invalid",
            "Yapay zeka çıktısı doğrulama kurallarına uymadı.",
        )
    elif isinstance(exc, SocialIdeaRetryWorkerInputError):
        return (
            "idea_input_invalid",
            "Fikir üretimi girdi doğrulaması başarısız oldu.",
        )
    elif isinstance(exc, SocialIdeaRetryHeartbeatError):
        return (
            "idea_heartbeat_failed",
            "İşlem kalp atışı (heartbeat) güncellenemedi.",
        )
    elif isinstance(exc, SocialIdeaRetryPersistenceError):
        return (
            "idea_persistence_failed",
            "Fikirlerin veritabanına kaydı başarısız oldu.",
        )
    elif isinstance(exc, AttemptNotWritableError):
        code_lower = (exc.error_code or "").lower()
        if code_lower in ("worker_lost", "brief_stale", "assignment_changed"):
            msg = (
                "İşlem lease süresi doldu."
                if code_lower == "worker_lost"
                else "Brief güncelliğini yitirdi."
                if code_lower == "brief_stale"
                else "Kanal atama sürümü değişti."
            )
            return (code_lower, msg)
        return (
            "idea_persistence_failed",
            "İşlem durumu yazılamaz durumda.",
        )

    return (
        "idea_persistence_failed",
        "Fikir üretim süreci beklenmeyen bir hatayla sonlandı.",
    )


def _finalize_retry_failure(
    session_factory: Callable[[], Session],
    *,
    attempt_id: int,
    task_id: str,
    exc: Exception,
    now: datetime,
) -> None:
    """Hata durumunda bağımsız transaction ile retry attempt'ini canonical kilit sırasıyla failed yapar."""
    fail_session = session_factory()
    try:
        reason_code, safe_message = _map_failure_reason(exc)
        finalize_ideas_retry_attempt_failure(
            fail_session,
            attempt_id=attempt_id,
            task_id=task_id,
            reason_code=reason_code,
            error_message=safe_message,
            now=now,
        )
        fail_session.commit()
    except Exception:
        fail_session.rollback()
    finally:
        fail_session.close()


def _retry_heartbeat_or_abort(
    session_factory: Callable[[], Session],
    *,
    attempt_id: int,
    task_id: str,
    now_provider: Any,
) -> None:
    """Ayrı transaction ile heartbeat yazar; başarısızlıkta retry attempt'i kapatıp hata fırlatır."""
    hb_session = session_factory()
    try:
        heartbeat_attempt(
            hb_session,
            attempt_id=attempt_id,
            task_id=task_id,
            now=_resolve_now(now_provider),
        )
        hb_session.commit()
    except AttemptNotClaimableError as exc:
        if getattr(exc, "error_code", None) == "LEASE_EXPIRED":
            hb_session.commit()
            raise AttemptNotWritableError(
                "İşlem lease süresi doldu.", error_code="WORKER_LOST"
            ) from exc
        hb_session.rollback()
        hb_exc = SocialIdeaRetryHeartbeatError("Heartbeat güncellenemedi.")
        _finalize_retry_failure(
            session_factory,
            attempt_id=attempt_id,
            task_id=task_id,
            exc=hb_exc,
            now=_resolve_now(now_provider),
        )
        raise hb_exc from exc
    except Exception as exc:
        hb_session.rollback()
        hb_exc = (
            exc
            if isinstance(exc, AttemptNotWritableError)
            else SocialIdeaRetryHeartbeatError("Heartbeat güncellenemedi.")
        )
        _finalize_retry_failure(
            session_factory,
            attempt_id=attempt_id,
            task_id=task_id,
            exc=hb_exc,
            now=_resolve_now(now_provider),
        )
        raise hb_exc from exc
    finally:
        hb_session.close()


def run_social_idea_retry_generation(
    *,
    session_factory: Callable[[], Session],
    ai_service: Any,
    attempt_id: int,
    task_id: str,
    now_provider: Any = None,
) -> SocialIdeaRetryOrchestrationResult:
    """Sosyal fikir tekrar deneme (ideas_retry) akışını transaction sınırlarına uygun biçimde orkestre eder.

    Adımlar:
    1. Girdi doğrulama ve zaman sağlayıcı çözümü.
    2. Aşama A (Claim Transaction):
       - Yeni session açılır.
       - claim_ideas_retry_attempt_for_worker çağrılır.
       - Commit edilir ve session kapatılır.
    3. Aşama B (Worker Input Transaction):
       - Yeni session açılır.
       - prepare_social_idea_retry_worker_inputs çağrılır.
       - İmmutable DTO'lar session dışına çıkarılır.
       - Commit edilir ve session kapatılır.
    4. Completed Replay kontrolü:
       - already_completed ise AI çağrısı yapılmaz, doğrudan Replay Persistence doğrulaması yapılır.
    5. Aşama C (AI Üretimi ve Heartbeat):
       - DB session kapalıyken çalışır.
       - SocialBriefIdeaGenerator her eksik kategori planı için çağrılır.
       - İlk kategori(ler) başarılı olup sonraki başarısız olursa, başarılı sonuçlar persist
         edilerek partial attempt finalize edilir.
       - İlk AI çağrısı başarısız olursa attempt failed yapılır ve hata fırlatılır.
       - Her başarılı kategori ardından ayrı bir Heartbeat Transaction ile lease uzatılır.
    6. Aşama D (Persistence Transaction):
       - Yeni session açılır.
       - persist_social_idea_retry_results çağrılır.
       - Commit edilir ve session kapatılır.
    """
    now = _resolve_now(now_provider)

    if not callable(session_factory):
        raise ValueError("session_factory çağrılabilir bir fonksiyon olmalıdır.")
    if ai_service is None:
        raise ValueError("ai_service boş olamaz.")
    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError("task_id boş olmayan bir string olmalıdır.")
    if isinstance(attempt_id, bool) or not isinstance(attempt_id, int) or attempt_id <= 0:
        raise ValueError("attempt_id pozitif bir tamsayı olmalıdır.")

    # -------------------------------------------------------------
    # 1. TRANSACTION A: CLAIM TRANSACTION
    # -------------------------------------------------------------
    claim_session = session_factory()
    try:
        attempt, brief, scoring_run, already_completed = claim_ideas_retry_attempt_for_worker(
            claim_session,
            attempt_id=attempt_id,
            task_id=task_id,
            now=now,
        )
        brief_id = brief.id
        scoring_run_id = scoring_run.id
        claim_session.commit()
    except Exception as exc:
        claim_session.rollback()
        _finalize_retry_failure(
            session_factory,
            attempt_id=attempt_id,
            task_id=task_id,
            exc=exc,
            now=_resolve_now(now_provider),
        )
        raise
    finally:
        claim_session.close()

    # -------------------------------------------------------------
    # 2. TRANSACTION B: WORKER INPUT PREPARATION
    # -------------------------------------------------------------
    prep_session = session_factory()
    try:
        prep = prepare_social_idea_retry_worker_inputs(
            prep_session,
            attempt_id=attempt_id,
            task_id=task_id,
            now=_resolve_now(now_provider),
        )
        brief_id = prep.brief_id
        scoring_run_id = prep.scoring_run_id
        already_completed = prep.already_completed
        prompt_inputs = prep.prompt_inputs
        prep_session.commit()
    except Exception as exc:
        prep_session.rollback()
        _finalize_retry_failure(
            session_factory,
            attempt_id=attempt_id,
            task_id=task_id,
            exc=exc,
            now=_resolve_now(now_provider),
        )
        raise
    finally:
        prep_session.close()

    # -------------------------------------------------------------
    # 3. COMPLETED REPLAY YOLU
    # -------------------------------------------------------------
    if already_completed:
        replay_session = session_factory()
        try:
            persisted = persist_social_idea_retry_results(
                replay_session,
                attempt_id=attempt_id,
                task_id=task_id,
                category_results=(),
                now=_resolve_now(now_provider),
            )
            replay_session.commit()
            return SocialIdeaRetryOrchestrationResult(
                attempt_id=attempt_id,
                brief_id=persisted.brief_id,
                scoring_run_id=persisted.scoring_run_id,
                status="completed",
                newly_persisted_count=0,
                accepted_target_ids=persisted.accepted_target_ids,
                unfilled_target_ids=(),
                ai_calls_used=0,
                replayed=True,
            )
        except Exception:
            replay_session.rollback()
            raise
        finally:
            replay_session.close()

    # -------------------------------------------------------------
    # 4. AŞAMA C: AI ÜRETİMİ VE HEARTBEAT (DB TRANSACTION AÇIK DEĞİL)
    # -------------------------------------------------------------
    generator = SocialBriefIdeaGenerator(ai_service)
    category_results: list[SocialIdeaAIResult] = []
    total_ai_calls = 0

    last_generation_error: SocialIdeaGenerationError | None = None
    metrics: dict[str, int] = {
        "ai_calls_used": 0,
        "off_brief_dropped": 0,
        "invalid_dropped": 0,
        "over_quota_dropped": 0,
        "retry_used": 0,
        "failed_requests": 0,
        "budget_skipped": 0,
    }

    for p_input in prompt_inputs:
        if total_ai_calls + MAX_AI_CALLS_PER_RETRY_CATEGORY_REQUEST > IDEA_RETRY_ATTEMPT_MAX_AI_CALLS:
            metrics["budget_skipped"] += 1
            continue
        try:
            cat_result = generator.generate(p_input)
        except SocialIdeaGenerationError as exc:
            if exc.error_code not in ("IDEA_PROVIDER_ERROR", "IDEA_OUTPUT_INVALID"):
                # Girdi/sözleşme hatası: retry attempt'in tamamı fail-closed kapanır
                _finalize_retry_failure(
                    session_factory,
                    attempt_id=attempt_id,
                    task_id=task_id,
                    exc=exc,
                    now=_resolve_now(now_provider),
                )
                raise
            # Kategori düzeyi AI hatası (plan §3.6): diğer kategoriler devam eder;
            # bu kategorinin talepleri target_unfilled / category_unfilled kalır. Sahte yedek yok.
            last_generation_error = exc
            used = exc.ai_calls_used if isinstance(exc.ai_calls_used, int) else 0
            total_ai_calls += used
            metrics["ai_calls_used"] += used
            metrics["retry_used"] += 1 if used > 1 else 0
            metrics["off_brief_dropped"] += exc.off_brief_dropped
            metrics["invalid_dropped"] += exc.invalid_dropped
            metrics["over_quota_dropped"] += exc.over_quota_dropped
            metrics["failed_requests"] += 1
            logger.warning(
                f"Retry orchestration AI failure for category {p_input.category_id}; "
                f"continuing with remaining categories."
            )
            if used > 0:
                _retry_heartbeat_or_abort(
                    session_factory,
                    attempt_id=attempt_id,
                    task_id=task_id,
                    now_provider=now_provider,
                )
            continue
        except Exception as exc:
            _finalize_retry_failure(
                session_factory,
                attempt_id=attempt_id,
                task_id=task_id,
                exc=exc,
                now=_resolve_now(now_provider),
            )
            raise

        metrics["ai_calls_used"] += cat_result.ai_calls_used
        metrics["retry_used"] += cat_result.retry_used
        metrics["off_brief_dropped"] += cat_result.off_brief_dropped
        metrics["invalid_dropped"] += cat_result.invalid_dropped
        metrics["over_quota_dropped"] += cat_result.over_quota_dropped
        category_results.append(cat_result)
        total_ai_calls += cat_result.ai_calls_used

        # Her başarılı kategori sonrasında ayrı Heartbeat Transaction
        _retry_heartbeat_or_abort(
            session_factory,
            attempt_id=attempt_id,
            task_id=task_id,
            now_provider=now_provider,
        )

    # Hiç fikir alınamadıysa: sahte yedek yok, retry attempt failed (K7)
    if len(category_results) == 0:
        failure_exc: Exception = last_generation_error or SocialIdeaGenerationError(
            "Yapay zeka fikir çıktısı doğrulanamadı.",
            error_code="IDEA_OUTPUT_INVALID",
        )
        _finalize_retry_failure(
            session_factory,
            attempt_id=attempt_id,
            task_id=task_id,
            exc=failure_exc,
            now=_resolve_now(now_provider),
        )
        raise failure_exc

    # -------------------------------------------------------------
    # 5. TRANSACTION D: PERSISTENCE TRANSACTION
    # -------------------------------------------------------------
    persist_session = session_factory()
    try:
        persisted = persist_social_idea_retry_results(
            persist_session,
            attempt_id=attempt_id,
            task_id=task_id,
            category_results=tuple(category_results),
            ai_calls_used=total_ai_calls,
            metrics=metrics,
            now=_resolve_now(now_provider),
        )
        persist_session.commit()
        return SocialIdeaRetryOrchestrationResult(
            attempt_id=attempt_id,
            brief_id=persisted.brief_id,
            scoring_run_id=persisted.scoring_run_id,
            status=persisted.status,
            newly_persisted_count=persisted.newly_persisted_count,
            accepted_target_ids=persisted.accepted_target_ids,
            unfilled_target_ids=persisted.unfilled_target_ids,
            ai_calls_used=total_ai_calls,
            replayed=persisted.already_completed,
            unfilled_category_ids=persisted.unfilled_category_ids,
        )
    except Exception as exc:
        persist_session.rollback()
        _finalize_retry_failure(
            session_factory,
            attempt_id=attempt_id,
            task_id=task_id,
            exc=exc,
            now=_resolve_now(now_provider),
        )
        raise
    finally:
        persist_session.close()
