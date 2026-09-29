# -*- coding: utf-8 -*-
"""Sosyal Brief İçerik Üretim Orkestrasyon Servisi (F1-G.5.5).

Bu modül Worker Input Hazırlığı -> DB'siz Çoklu Batch Yürütme -> Heartbeat
-> Atomik Persistence + Finalization zincirini transaction-safe biçimde koordine eder:

Temel Kurallar:
- Celery task veya API endpoint İÇERMEZ.
- AI çağrısı sırasında hiçbir DB session veya row lock AÇIK TUTULMAZ.
- Preparation, heartbeat ve persistence+finalization ayrı bağımsız transaction'larda çalışır.
- Tek bir session bütün işlem boyunca taşınmaz (session_factory kullanılır).
- Completed replay yolunda AI çağrısı, heartbeat ve DB yazımı yapılmaz; DB doğrulaması yapılır.
- Başarısızlık durumunda fail-closed failure finalization bağımsız transaction ile yapılır.
- Hata mesajlarında ve warning nesnelerinde asla ham AI çıktısı, SQL veya provider hata detayı sızdırılmaz.
- temperature=None ayarı korunur, gerçek ağ çağrısı yapılmaz.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy.orm import Session

from app.core.social.content_batch_execution import (
    SocialContentBatchExecutionError,
    SocialContentBatchExecutionResult,
    SocialContentBatchWarning,
    execute_social_content_batch,
)
from app.core.social.content_item_execution import SocialContentItemExecutionError
from app.core.social.content_persistence import (
    SocialContentPersistenceError,
    persist_social_content,
    validate_social_content_attempt_coverage,
)
from app.core.social.content_worker_input import (
    SocialContentWorkerInputError,
    SocialContentWorkerPreparation,
    prepare_social_content_worker_inputs,
)
from app.generators.social.attempt_state import (
    ALLOWED_CONTENTS_FAILURE_REASONS,
    AttemptNotClaimableError,
    AttemptNotWritableError,
    finalize_contents_attempt_failure,
    finish_attempt,
    heartbeat_attempt,
)


class SocialContentOrchestrationError(ValueError):
    """İçerik üretim orkestrasyon hatası."""

    def __init__(
        self,
        message: str,
        *,
        error_code: str = "CONTENT_ORCHESTRATION_FAILED",
        reason_code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.reason_code = reason_code


class SocialContentHeartbeatError(ValueError):
    """Heartbeat sırasında oluşan hata."""

    def __init__(self, message: str = "Heartbeat başarısız oldu.") -> None:
        super().__init__(message)
        self.error_code = "CONTENT_HEARTBEAT_FAILED"


@dataclass(frozen=True)
class SocialContentOrchestrationResult:
    """İçerik üretim orkestrasyon nihai sonucu (immutable)."""

    attempt_id: int
    brief_id: int
    scoring_run_id: int
    status: str
    requested_idea_ids: tuple[int, ...]
    already_present_idea_ids: tuple[int, ...]
    persisted_idea_ids: tuple[int, ...]
    unresolved_idea_ids: tuple[int, ...]
    warnings: tuple[SocialContentBatchWarning, ...]
    ai_calls_used: int
    replayed: bool

    def __post_init__(self) -> None:
        # 1. attempt_id, brief_id, scoring_run_id
        for field_name in ("attempt_id", "brief_id", "scoring_run_id"):
            val = getattr(self, field_name)
            if isinstance(val, bool) or type(val) is not int or val <= 0:
                raise SocialContentOrchestrationError(
                    f"Geçersiz {field_name}.",
                    error_code="CONTENT_ORCHESTRATION_INVALID_RESULT",
                )

        # 2. status & replayed
        if self.status not in ("completed", "partial", "failed"):
            raise SocialContentOrchestrationError(
                "status 'completed', 'partial' veya 'failed' olmalıdır.",
                error_code="CONTENT_ORCHESTRATION_INVALID_RESULT",
            )
        if type(self.replayed) is not bool:
            raise SocialContentOrchestrationError(
                "replayed exact bool olmalıdır.",
                error_code="CONTENT_ORCHESTRATION_INVALID_RESULT",
            )

        # 3. requested_idea_ids
        if type(self.requested_idea_ids) is not tuple:
            raise SocialContentOrchestrationError(
                "requested_idea_ids tuple olmalıdır.",
                error_code="CONTENT_ORCHESTRATION_INVALID_RESULT",
            )
        if not (1 <= len(self.requested_idea_ids) <= 30):
            raise SocialContentOrchestrationError(
                "requested_idea_ids 1 ile 30 arasında eleman içermelidir.",
                error_code="CONTENT_ORCHESTRATION_INVALID_RESULT",
            )
        if len(set(self.requested_idea_ids)) != len(self.requested_idea_ids):
            raise SocialContentOrchestrationError(
                "requested_idea_ids benzersiz olmalıdır.",
                error_code="CONTENT_ORCHESTRATION_INVALID_RESULT",
            )
        for i in self.requested_idea_ids:
            if isinstance(i, bool) or type(i) is not int or i <= 0:
                raise SocialContentOrchestrationError(
                    "requested_idea_ids pozitif tamsayılar içermelidir.",
                    error_code="CONTENT_ORCHESTRATION_INVALID_RESULT",
                )

        req_set = set(self.requested_idea_ids)

        # 4. Partition kümeleri doğrulaması
        for field_name in (
            "already_present_idea_ids",
            "persisted_idea_ids",
            "unresolved_idea_ids",
        ):
            val = getattr(self, field_name)
            if type(val) is not tuple:
                raise SocialContentOrchestrationError(
                    f"{field_name} tuple olmalıdır.",
                    error_code="CONTENT_ORCHESTRATION_INVALID_RESULT",
                )
            if len(set(val)) != len(val):
                raise SocialContentOrchestrationError(
                    f"{field_name} benzersiz olmalıdır.",
                    error_code="CONTENT_ORCHESTRATION_INVALID_RESULT",
                )
            v_set = set(val)
            if not v_set.issubset(req_set):
                raise SocialContentOrchestrationError(
                    f"{field_name} requested_idea_ids kümesinin alt kümesi olmalıdır.",
                    error_code="CONTENT_ORCHESTRATION_INVALID_RESULT",
                )
            expected_order = tuple(i for i in self.requested_idea_ids if i in v_set)
            if val != expected_order:
                raise SocialContentOrchestrationError(
                    f"{field_name} requested sırasını korumalıdır.",
                    error_code="CONTENT_ORCHESTRATION_INVALID_RESULT",
                )

        pres_set = set(self.already_present_idea_ids)
        pers_set = set(self.persisted_idea_ids)
        unres_set = set(self.unresolved_idea_ids)

        # Ayrıklık (pairwise disjointness)
        if pres_set & pers_set or pres_set & unres_set or pers_set & unres_set:
            raise SocialContentOrchestrationError(
                "already_present, persisted ve unresolved kümeleri kesişemez.",
                error_code="CONTENT_ORCHESTRATION_INVALID_RESULT",
            )

        # Tam bölüntüleme (exact partition)
        if (pres_set | pers_set | unres_set) != req_set:
            raise SocialContentOrchestrationError(
                "already_present, persisted ve unresolved kümeleri requested kümesini eksiksiz kapsamalıdır.",
                error_code="CONTENT_ORCHESTRATION_INVALID_RESULT",
            )

        # 5. warnings
        if type(self.warnings) is not tuple:
            raise SocialContentOrchestrationError(
                "warnings tuple olmalıdır.",
                error_code="CONTENT_ORCHESTRATION_INVALID_RESULT",
            )
        for w in self.warnings:
            if type(w) is not SocialContentBatchWarning:
                raise SocialContentOrchestrationError(
                    "warnings elemanları exact SocialContentBatchWarning olmalıdır.",
                    error_code="CONTENT_ORCHESTRATION_INVALID_RESULT",
                )
        warning_ids = tuple(w.idea_id for w in self.warnings)
        if warning_ids != self.unresolved_idea_ids:
            raise SocialContentOrchestrationError(
                "warnings idea ID'leri unresolved_idea_ids ile birebir aynı sırada olmalıdır.",
                error_code="CONTENT_ORCHESTRATION_INVALID_RESULT",
            )

        # 6. ai_calls_used
        if (
            isinstance(self.ai_calls_used, bool)
            or type(self.ai_calls_used) is not int
            or self.ai_calls_used < 0
        ):
            raise SocialContentOrchestrationError(
                "ai_calls_used negatif olmayan tamsayı olmalıdır.",
                error_code="CONTENT_ORCHESTRATION_INVALID_RESULT",
            )
        max_possible = 2 * (len(pers_set) + len(unres_set))
        if self.ai_calls_used > max_possible:
            raise SocialContentOrchestrationError(
                "ai_calls_used teorik maksimum çağrı sınırını aşamaz.",
                error_code="CONTENT_ORCHESTRATION_INVALID_RESULT",
            )

        # 7. Replay ve Status ilişkileri
        if self.replayed:
            if self.status != "completed":
                raise SocialContentOrchestrationError(
                    "replayed=True durumunda status 'completed' olmalıdır.",
                    error_code="CONTENT_ORCHESTRATION_INVALID_RESULT",
                )
            if self.persisted_idea_ids != ():
                raise SocialContentOrchestrationError(
                    "replayed=True durumunda persisted_idea_ids boş olmalıdır.",
                    error_code="CONTENT_ORCHESTRATION_INVALID_RESULT",
                )
            if self.already_present_idea_ids != self.requested_idea_ids:
                raise SocialContentOrchestrationError(
                    "replayed=True durumunda already_present_idea_ids requested_idea_ids ile aynı olmalıdır.",
                    error_code="CONTENT_ORCHESTRATION_INVALID_RESULT",
                )
            if self.unresolved_idea_ids != () or self.warnings != ():
                raise SocialContentOrchestrationError(
                    "replayed=True durumunda unresolved_idea_ids ve warnings boş olmalıdır.",
                    error_code="CONTENT_ORCHESTRATION_INVALID_RESULT",
                )
            if self.ai_calls_used != 0:
                raise SocialContentOrchestrationError(
                    "replayed=True durumunda ai_calls_used 0 olmalıdır.",
                    error_code="CONTENT_ORCHESTRATION_INVALID_RESULT",
                )

        successful_set = pres_set | pers_set
        if self.status == "completed":
            if len(self.unresolved_idea_ids) != 0:
                raise SocialContentOrchestrationError(
                    "completed durumunda unresolved_idea_ids boş olmalıdır.",
                    error_code="CONTENT_ORCHESTRATION_INVALID_RESULT",
                )
        elif self.status == "partial":
            if len(successful_set) == 0 or len(self.unresolved_idea_ids) == 0:
                raise SocialContentOrchestrationError(
                    "partial durumunda hem başarılı hem unresolved eleman bulunmalıdır.",
                    error_code="CONTENT_ORCHESTRATION_INVALID_RESULT",
                )
        elif self.status == "failed":
            if len(successful_set) != 0:
                raise SocialContentOrchestrationError(
                    "failed durumunda hiçbir başarılı fikir bulunmamalıdır.",
                    error_code="CONTENT_ORCHESTRATION_INVALID_RESULT",
                )
            if len(self.unresolved_idea_ids) == 0:
                raise SocialContentOrchestrationError(
                    "failed durumunda unresolved_idea_ids boş olamaz.",
                    error_code="CONTENT_ORCHESTRATION_INVALID_RESULT",
                )


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
    # SoftTimeLimitExceeded normalde bu noktaya ulaşmaz (execute_social_content_batch
    # kendi içinde zarif biçimde absorbe eder); yalnız on_item_finished callback'i
    # (heartbeat) gibi batch dışı bir noktadan sızarsa buraya düşer. Sıradan bir
    # hata gibi ele alınmaz: attempt promptly 'failed' yapılır, lease süresine
    # kadar açık bırakılmaz (CLAUDE.md §11 + plan_social_brief_akisi.md §6).
    if isinstance(exc, SoftTimeLimitExceeded):
        return ("worker_lost", "İşlem zaman bütçesini aştı.")

    cause = getattr(exc, "__cause__", None)
    if isinstance(cause, (AttemptNotWritableError, AttemptNotClaimableError)):
        code_lower = (cause.error_code or "").lower()
        if code_lower in ("lease_expired", "worker_lost"):
            return ("worker_lost", "İşlem lease süresi doldu.")
        if code_lower in ALLOWED_CONTENTS_FAILURE_REASONS:
            return (code_lower, "İşlem durumu yazılamaz durumda.")

    if isinstance(exc, SocialContentBatchExecutionError):
        return ("content_input_invalid", "Batch girdi doğrulaması başarısız oldu.")
    if isinstance(exc, SocialContentWorkerInputError):
        return ("content_input_invalid", "İçerik hazırlık verileri doğrulanamadı.")
    if isinstance(exc, SocialContentItemExecutionError):
        rc = (
            exc.reason_code
            if exc.reason_code in ALLOWED_CONTENTS_FAILURE_REASONS
            else "content_execution_inconsistent"
        )
        return (rc, "İçerik yürütme hatası oluştu.")
    if isinstance(exc, AttemptNotWritableError):
        code_lower = (exc.error_code or "").lower()
        if code_lower in ("lease_expired", "worker_lost"):
            return ("worker_lost", "İşlem lease süresi doldu.")
        if code_lower in ALLOWED_CONTENTS_FAILURE_REASONS:
            return (code_lower, "İşlem durumu yazılamaz durumda.")
        return ("content_persistence_failed", "İşlem durumu yazılamaz durumda.")
    if isinstance(exc, AttemptNotClaimableError):
        code_lower = (exc.error_code or "").lower()
        if code_lower in ("lease_expired", "worker_lost"):
            return ("worker_lost", "İşlem sahiplenilemedi.")
        if code_lower in ALLOWED_CONTENTS_FAILURE_REASONS:
            return (code_lower, "İşlem sahiplenilemedi.")
        return ("content_persistence_failed", "İşlem sahiplenilemedi.")
    if isinstance(exc, SocialContentPersistenceError):
        return ("content_persistence_failed", "İçerik veritabanına kaydedilemedi.")
    if isinstance(exc, SocialContentHeartbeatError):
        return ("content_heartbeat_failed", "İşlem heartbeat güncellenemedi.")
    return (
        "content_persistence_failed",
        "Sosyal içerik üretim süreci beklenmeyen bir hatayla sonlandı.",
    )


def _finalize_failure(
    session_factory: Callable[[], Session],
    *,
    attempt_id: int,
    task_id: str,
    exc: Exception,
    now: datetime,
) -> None:
    """Hata durumunda bağımsız transaction ile attempt'i canonical kilit sırasıyla failed durumuna geçirir."""
    fail_session = session_factory()
    try:
        reason_code, safe_message = _map_failure_reason(exc)
        finalize_contents_attempt_failure(
            fail_session,
            attempt_id=attempt_id,
            task_id=task_id,
            reason_code=reason_code,
            error_message=safe_message,
            now=now,
        )
        fail_session.commit()
    except Exception:
        # Failure finalization kendi hatasıyla asıl hatayı maskelememeli
        fail_session.rollback()
    finally:
        fail_session.close()


def run_social_content_generation(
    *,
    session_factory: Callable[[], Session],
    ai_service: Any,
    attempt_id: int,
    task_id: str,
    now_provider: Any = None,
    batch_clock: Any = None,
) -> SocialContentOrchestrationResult:
    """Sosyal içerik üretim akışını transaction sınırlarına uygun biçimde orkestre eder.

    Adımlar:
    1. Girdi doğrulama ve zaman sağlayıcı çözümü.
    2. Aşama A (Worker Input Preparation Transaction):
       - Attempt claim edilir, coverage request snapshot ve DB zinciri doğrulanır.
       - Zaten tamamlanmışsa (already_completed=True) 0 AI çağrısıyla replay döner.
    3. Aşama B (DB'siz AI Üretimi ve Heartbeat):
       - Session kapalıyken execute_social_content_batch çalıştırılır.
       - execute_social_content_batch kendi içinde deterministik bir zaman
         bütçesi (`CONTENT_BATCH_TIME_BUDGET_SECONDS`) uygular ve soft
         Celery zaman sınırına ulaşmadan yeni item başlatmayı durdurur;
         bu sayede zaten kabul edilmiş içerikler normal partial yolundan
         Aşama C'de persist edilir. `batch_clock` test edilebilirlik için
         bu bütçe kontrolüne enjekte edilen zaman kaynağıdır (opsiyonel).
       - Her tamamlanan work item için ayrı transaction ile heartbeat gönderilir.
    4. Aşama C (Persistence ve Finalization Transaction):
       - Tek transaction içinde tüm accepted içerikler persist edilir.
       - Parite doğrulanır ve finish_attempt ile attempt uygun terminal duruma çekilir.
    5. Hata durumunda bağımsız transaction ile fail-closed failure finalization yapılır.
       `SoftTimeLimitExceeded` bu noktaya normalde ULAŞMAZ (batch executor
       kendi içinde zarif biçimde absorbe eder); yine de bir yerden (örn.
       heartbeat callback'i) sızarsa, sıradan bir hata gibi ele alınıp
       attempt promptly 'failed' yapılır — lease süresine kadar açık
       bırakılmaz (bkz. `_map_failure_reason`).
    """
    if not callable(session_factory):
        raise SocialContentOrchestrationError(
            "session_factory callable olmalıdır.",
            error_code="CONTENT_ORCHESTRATION_INVALID_INPUT",
        )
    if ai_service is None:
        raise SocialContentOrchestrationError(
            "ai_service boş olamaz.",
            error_code="CONTENT_ORCHESTRATION_INVALID_INPUT",
        )
    if isinstance(attempt_id, bool) or not isinstance(attempt_id, int) or attempt_id <= 0:
        raise SocialContentOrchestrationError(
            "attempt_id pozitif tamsayı olmalıdır.",
            error_code="CONTENT_ORCHESTRATION_INVALID_INPUT",
        )
    if not isinstance(task_id, str) or not task_id.strip():
        raise SocialContentOrchestrationError(
            "task_id boş olmayan bir string olmalıdır.",
            error_code="CONTENT_ORCHESTRATION_INVALID_INPUT",
        )

    current_time = _resolve_now(now_provider)

    # -------------------------------------------------------------
    # 1. TRANSACTION A: WORKER INPUT PREPARATION & CLAIM
    # -------------------------------------------------------------
    prep_session = session_factory()
    try:
        preparation = prepare_social_content_worker_inputs(
            prep_session,
            attempt_id=attempt_id,
            task_id=task_id,
            now=current_time,
        )
        prep_session.commit()
    except Exception as exc:
        prep_session.rollback()
        _finalize_failure(
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
    # 2. COMPLETED REPLAY YOLU (0 AI, 0 Heartbeat, 0 Persistence)
    # -------------------------------------------------------------
    if preparation.already_completed:
        return SocialContentOrchestrationResult(
            attempt_id=attempt_id,
            brief_id=preparation.brief_id,
            scoring_run_id=preparation.scoring_run_id,
            status="completed",
            requested_idea_ids=preparation.requested_idea_ids,
            already_present_idea_ids=preparation.requested_idea_ids,
            persisted_idea_ids=(),
            unresolved_idea_ids=(),
            warnings=(),
            ai_calls_used=0,
            replayed=True,
        )

    # -------------------------------------------------------------
    # 3. AŞAMA B: DB'SİZ BATCH EXECUTION VE HEARTBEAT
    # -------------------------------------------------------------
    def on_item_finished(finished_idea_id: int) -> None:
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
            raise AttemptNotWritableError(
                "Worker sahipliği doğrulanamadı.", error_code="TASK_MISMATCH"
            ) from exc
        except Exception as exc:
            hb_session.rollback()
            if isinstance(exc, AttemptNotWritableError):
                raise
            raise SocialContentHeartbeatError("Heartbeat güncellenemedi.") from exc
        finally:
            hb_session.close()

    try:
        batch_result = execute_social_content_batch(
            ai_service=ai_service,
            preparation=preparation,
            on_item_finished=on_item_finished,
            clock=batch_clock,
        )
    except Exception as exc:
        _finalize_failure(
            session_factory,
            attempt_id=attempt_id,
            task_id=task_id,
            exc=exc,
            now=_resolve_now(now_provider),
        )
        raise

    # -------------------------------------------------------------
    # 4. TRANSACTION C: ATOMİK PERSISTENCE VE FINALIZATION
    # -------------------------------------------------------------
    persist_session = session_factory()
    try:
        persisted_ids_list: list[int] = []
        for accepted in batch_result.accepted_results:
            persisted_content = persist_social_content(
                persist_session,
                brief_id=preparation.brief_id,
                attempt_id=attempt_id,
                idea_id=accepted.idea_id,
                content=accepted.content,
                quality_decision=accepted.quality_decision,
                task_id=task_id,
                now=_resolve_now(now_provider),
            )
            if (
                persisted_content.attempt_id != attempt_id
                or persisted_content.brief_id != preparation.brief_id
                or persisted_content.idea_id != accepted.idea_id
            ):
                raise SocialContentPersistenceError(
                    "Persist edilen içerik kimlik doğrulaması başarısız.",
                    error_code="CONTENT_PERSISTENCE_FAILED",
                    idea_id=accepted.idea_id,
                    brief_id=preparation.brief_id,
                )
            persisted_ids_list.append(accepted.idea_id)

        persisted_idea_ids = tuple(
            i for i in preparation.requested_idea_ids if i in set(persisted_ids_list)
        )

        # Persistence sonrası parite kontrolleri
        if set(persisted_idea_ids) != set(batch_result.accepted_idea_ids):
            raise SocialContentPersistenceError(
                "Persist edilen ID'ler accepted_idea_ids ile eşleşmiyor.",
                error_code="CONTENT_PERSISTENCE_FAILED",
            )

        warning_dicts = [
            {
                "idea_id": w.idea_id,
                "reason_code": w.reason_code,
                "claims": list(w.claims),
                "ai_calls_used": w.ai_calls_used,
            }
            for w in batch_result.warnings
        ]

        if batch_result.status == "completed":
            finish_attempt(
                persist_session,
                attempt_id=attempt_id,
                task_id=task_id,
                status="completed",
                reason_code=None,
                error_message=None,
                warnings=(),
                coverage=None,  # contents_request_v1 snapshot'ını koru
                now=_resolve_now(now_provider),
            )
        elif batch_result.status == "partial":
            finish_attempt(
                persist_session,
                attempt_id=attempt_id,
                task_id=task_id,
                status="partial",
                reason_code="content_partial",
                error_message="Bazı sosyal içerikler üretilemedi.",
                warnings=warning_dicts,
                coverage=None,
                now=_resolve_now(now_provider),
            )
        elif batch_result.status == "failed":
            finish_attempt(
                persist_session,
                attempt_id=attempt_id,
                task_id=task_id,
                status="failed",
                reason_code="content_generation_failed",
                error_message="Sosyal içerik üretimi başarısız oldu.",
                warnings=warning_dicts,
                coverage=None,
                now=_resolve_now(now_provider),
            )
        else:
            raise SocialContentPersistenceError(
                f"Bilinmeyen batch status: {batch_result.status}",
                error_code="CONTENT_PERSISTENCE_FAILED",
            )

        # 3. Final DB coverage doğrulaması (commit öncesi)
        expected_successful_ids = tuple(
            i for i in preparation.requested_idea_ids
            if i in (set(preparation.already_present_idea_ids) | set(persisted_idea_ids))
        )
        expected_unresolved_ids = tuple(
            i for i in preparation.requested_idea_ids
            if i in set(batch_result.unresolved_idea_ids)
        )

        validate_social_content_attempt_coverage(
            persist_session,
            brief_id=preparation.brief_id,
            requested_idea_ids=preparation.requested_idea_ids,
            expected_successful_idea_ids=expected_successful_ids,
            expected_unresolved_idea_ids=expected_unresolved_ids,
        )

        # 4. Result DTO'sunu oluştur ve bütün invariant'larını commit öncesi çalıştır
        orchestration_result = SocialContentOrchestrationResult(
            attempt_id=attempt_id,
            brief_id=preparation.brief_id,
            scoring_run_id=preparation.scoring_run_id,
            status=batch_result.status,
            requested_idea_ids=preparation.requested_idea_ids,
            already_present_idea_ids=preparation.already_present_idea_ids,
            persisted_idea_ids=persisted_idea_ids,
            unresolved_idea_ids=batch_result.unresolved_idea_ids,
            warnings=batch_result.warnings,
            ai_calls_used=batch_result.ai_calls_used,
            replayed=False,
        )

        # 5. Yalnızca tüm kontroller başarılı olduktan sonra commit et
        persist_session.commit()

        # 6. Önceden oluşturulmuş sonucu döndür
        return orchestration_result
    except Exception as exc:
        persist_session.rollback()
        _finalize_failure(
            session_factory,
            attempt_id=attempt_id,
            task_id=task_id,
            exc=exc,
            now=_resolve_now(now_provider),
        )
        raise
    finally:
        persist_session.close()
