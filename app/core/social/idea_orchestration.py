# -*- coding: utf-8 -*-
"""Sosyal Brief Fikir Üretim Orkestrasyon Servisi (F1-F.6b).

Bu modül Claim -> Worker Input Hazırlığı -> Kategori Bazlı AI Üretimi -> Heartbeat
-> Atomik Persistence zincirini transaction-safe biçimde koordine eder.

Temel Kurallar:
- Celery task veya API endpoint İÇERMEZ.
- AI çağrısı sırasında hiçbir DB session veya row lock AÇIK TUTULMAZ.
- Claim, worker input, heartbeat ve persistence ayrı transaction'larda çalışır.
- Tek bir session bütün işlem boyunca taşınmaz (session_factory kullanılır).
- Completed replay yolunda AI çağrısı yapılmaz, completed DB doğrulaması yapılır.
- Başarısızlık durumunda fail-closed failure finalization yapılır.
- Hata mesajlarında asla ham AI çıktısı, SQL veya provider hata detayı sızdırılmaz.
- temperature=None ayarı korunur, gerçek ağ çağrısı yapılmaz.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.core.social.idea_persistence import (
    PersistedSocialIdeasResult,
    SocialIdeaPersistenceError,
    persist_social_ideas,
)
from app.core.social.idea_worker_input import (
    SocialIdeaWorkerInputError,
    SocialIdeaWorkerPreparation,
    prepare_social_idea_worker_inputs,
)
from app.generators.social.attempt_state import (
    AttemptNotClaimableError,
    AttemptNotWritableError,
    claim_ideas_attempt_for_worker,
    finalize_ideas_attempt_failure,
    finish_attempt,
    heartbeat_attempt,
)
from app.generators.social.brief_idea_generator import (
    SocialBriefIdeaGenerator,
    SocialIdeaAIResult,
    SocialIdeaGenerationError,
)
from app.core.social.idea_contract import IdeaTargetSpec, ValidatedSocialIdea
from app.generators.social.brief_idea_prompt import SocialIdeaPromptInput


class SocialIdeaOrchestrationError(ValueError):
    """Fikir üretim orkestrasyon hatası."""

    def __init__(
        self,
        message: str,
        *,
        error_code: str = "IDEA_ORCHESTRATION_FAILED",
        reason_code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.reason_code = reason_code


class SocialIdeaHeartbeatError(ValueError):
    """Heartbeat sırasında oluşan hata."""

    def __init__(self, message: str = "Heartbeat başarısız oldu.") -> None:
        super().__init__(message)
        self.error_code = "IDEA_HEARTBEAT_FAILED"


@dataclass(frozen=True)
class SocialIdeaOrchestrationResult:
    """Fikir üretim orkestrasyon sonucu (immutable)."""

    attempt_id: int
    brief_id: int
    scoring_run_id: int
    status: str
    total_ideas: int
    ai_calls_used: int
    replayed: bool


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
    elif isinstance(exc, SocialIdeaWorkerInputError):
        return (
            "idea_input_invalid",
            "Fikir üretimi girdi doğrulaması başarısız oldu.",
        )
    elif isinstance(exc, SocialIdeaHeartbeatError):
        return (
            "idea_heartbeat_failed",
            "İşlem kalp atışı (heartbeat) güncellenemedi.",
        )
    elif isinstance(exc, SocialIdeaPersistenceError):
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

    # Genel fallback
    return (
        "idea_persistence_failed",
        "Fikir üretim süreci beklenmeyen bir hatayla sonlandı.",
    )


def _finalize_failure(
    session_factory: Callable[[], Session],
    *,
    attempt_id: int,
    task_id: str,
    exc: Exception,
    now: datetime,
) -> None:
    """Hata durumunda bağımsız transaction ile attempt'i canonical kilit sırasıyla failed durumuna geçirir.

    Kurallar:
    - Doğrudan attempt tablosuna FOR UPDATE sorgusu yapmaz.
    - finalize_ideas_attempt_failure yardımcısını kullanır (ScoringRun -> SocialBrief -> Attempt).
    - Failure finalization kendi hatasıyla asıl hatayı maskelemez (try-except içinde korunur).
    """
    fail_session = session_factory()
    try:
        reason_code, safe_message = _map_failure_reason(exc)
        finalize_ideas_attempt_failure(
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


# Bir kategori isteği (ilk tur veya tamamlama) en fazla 2 AI çağrısı harcar:
# 1 ana çağrı + JSON okuma/doğrulama için 1 düzeltme tekrarı.
MAX_AI_CALLS_PER_CATEGORY_REQUEST = 2

# Deneme başına sabit AI çağrı tavanı (plan §5.4):
#   ilk tur  : en fazla 6 kategori x 2 çağrı = 12
#   tamamlama: TEK tur, KATEGORİ BAŞINA TEK istek. Tur hem brief genelinde eksik
#              hedefleri hem de hiç fikir almamış kategorileri (K4) kapsar; aynı
#              kategoriye düşen talepler tek istekte birleşir. İstek sayısı
#              <= kategori sayısı <= 6 -> en fazla 6 x 2 = 12
# Toplam 24. Tavanı aşacak bir istek hiç başlatılmaz; hedef/kategori eksik kalır.
IDEA_ATTEMPT_MAX_AI_CALLS = 24

# Kategori düzeyinde tolere edilen üretim hataları: deneme durmaz, o kategorinin
# hedefleri eksik kalır. Diğer (girdi/sözleşme) hataları denemeyi fail-closed kapatır.
CATEGORY_LEVEL_GENERATION_ERRORS: frozenset[str] = frozenset({
    "IDEA_PROVIDER_ERROR",
    "IDEA_OUTPUT_INVALID",
})


@dataclass
class _IdeaAttemptMetrics:
    """Deneme ölçümleri (plan §3 Ölçüm). Ham AI metni taşımaz; yalnız sayaçlar."""

    ai_calls_used: int = 0
    off_brief_dropped: int = 0
    invalid_dropped: int = 0
    over_quota_dropped: int = 0
    retry_used: int = 0
    topup_used: int = 0
    topup_requests: int = 0
    # Tamamlama turunda ilk turdan 0 fikirle çıkan (boş) kategoriye giden istek sayısı
    # (eksik hedefle birleşmiş istekler dahil). topup_requests'in alt kümesidir.
    category_topup_requests: int = 0
    failed_requests: int = 0
    budget_skipped: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "ai_calls_used": self.ai_calls_used,
            "off_brief_dropped": self.off_brief_dropped,
            "invalid_dropped": self.invalid_dropped,
            "over_quota_dropped": self.over_quota_dropped,
            "retry_used": self.retry_used,
            "topup_used": self.topup_used,
            "topup_requests": self.topup_requests,
            "category_topup_requests": self.category_topup_requests,
            "failed_requests": self.failed_requests,
            "budget_skipped": self.budget_skipped,
        }


def _heartbeat_or_abort(
    session_factory: Callable[[], Session],
    *,
    attempt_id: int,
    task_id: str,
    now_provider: Any,
) -> None:
    """Ayrı transaction ile heartbeat yazar; başarısızlıkta denemeyi kapatıp hata fırlatır.

    Lease süresi dolduysa AttemptNotWritableError(WORKER_LOST) fırlatılır (attempt
    heartbeat tarafından zaten failed/worker_lost yapılmıştır).
    """
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
        hb_exc = SocialIdeaHeartbeatError("Heartbeat güncellenemedi.")
        _finalize_failure(
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
            else SocialIdeaHeartbeatError("Heartbeat güncellenemedi.")
        )
        _finalize_failure(
            session_factory,
            attempt_id=attempt_id,
            task_id=task_id,
            exc=hb_exc,
            now=_resolve_now(now_provider),
        )
        raise hb_exc from exc
    finally:
        hb_session.close()


def _build_topup_prompt_inputs(
    prompt_inputs: tuple[SocialIdeaPromptInput, ...],
    ideas_by_category: dict[int, list[ValidatedSocialIdea]],
) -> tuple[SocialIdeaPromptInput, ...]:
    """Tek tamamlama turunun isteklerini kurar (plan §3.5 + K4).

    K4 iki garantiyi birlikte ister: her hedef >= 1 fikir VE her kategori >= 1 fikir.
    Tur ikisini de kapsar:

    1. Eksik hedef = tüm kategorilerde toplam kabul edilen fikri 0 olan plan hedefi
       (plan hedef sırasıyla). Hedef, onu pozitif kotayla planlayan kategoriler
       içinde (plan sırası) önce henüz talep almamış BOŞ kategoriye, yoksa herhangi
       bir boş kategoriye verilir — tek fikir hem hedefi hem kategoriyi doldurur;
       boş aday yoksa planlayan İLK kategoriye verilir.
    2. Boş kategori = kabul edilen fikri 0 olan plan kategorisi. 1. adımda talep
       almamışsa, kendi planındaki İLK pozitif kotalı hedef için 1 fikir istenir.

    - Kategori başına TEK istek (talepler birleşir), talep başına 1 fikir; istek
      sayısı <= kategori sayısı olduğu için çağrı tavanı değişmez.
    - Her talep (kategori, hedef) çifti planda pozitif kotalıdır ve o çiftte 0
      kabul vardır; kota asla aşılmaz.
    - Kategori x hedef boşlukları (kota altı) tamamlanmaz.
    """
    accepted_per_target: dict[int, int] = {}
    accepted_per_category: dict[int, int] = {}
    for cat_id, ideas in ideas_by_category.items():
        accepted_per_category[cat_id] = accepted_per_category.get(cat_id, 0) + len(ideas)
        for idea in ideas:
            accepted_per_target[idea.target_id] = accepted_per_target.get(idea.target_id, 0) + 1

    empty_categories = {
        p.category_id for p in prompt_inputs if accepted_per_category.get(p.category_id, 0) == 0
    }

    # Plan hedef sırası: kategoriler plan sırasıyla, her kategoride spec sırasıyla ilk görülüş
    target_order: list[int] = []
    planners_by_target: dict[int, list[int]] = {}
    for p_input in prompt_inputs:
        for spec in p_input.target_specs:
            if spec.requested_count <= 0:
                continue
            if spec.target_id not in planners_by_target:
                planners_by_target[spec.target_id] = []
                target_order.append(spec.target_id)
            planners_by_target[spec.target_id].append(p_input.category_id)

    requested_pairs: dict[int, set[int]] = {}
    for tid in target_order:
        if accepted_per_target.get(tid, 0) > 0:
            continue
        planners = planners_by_target[tid]
        # Öncelik: talep almamış boş kategori > boş kategori > planlayan ilk kategori
        chosen = next(
            (cid for cid in planners if cid in empty_categories and cid not in requested_pairs),
            next((cid for cid in planners if cid in empty_categories), planners[0]),
        )
        requested_pairs.setdefault(chosen, set()).add(tid)

    for p_input in prompt_inputs:
        cid = p_input.category_id
        if cid not in empty_categories or cid in requested_pairs:
            continue
        first_spec = next((s for s in p_input.target_specs if s.requested_count > 0), None)
        if first_spec is not None:
            requested_pairs[cid] = {first_spec.target_id}

    topups: list[SocialIdeaPromptInput] = []
    for p_input in prompt_inputs:
        wanted = requested_pairs.get(p_input.category_id)
        if not wanted:
            continue
        specs = tuple(
            replace(spec, requested_count=1)
            for spec in p_input.target_specs
            if spec.target_id in wanted and spec.requested_count > 0
        )
        if specs:
            topups.append(replace(p_input, target_specs=specs))
    return tuple(topups)


def run_social_idea_generation(
    *,
    session_factory: Callable[[], Session],
    ai_service: Any,
    attempt_id: int,
    task_id: str,
    now_provider: Any = None,
) -> SocialIdeaOrchestrationResult:
    """Sosyal fikir üretim akışını transaction sınırlarına uygun biçimde orkestre eder.

    Adımlar:
    1. Girdi doğrulama ve zaman sağlayıcı çözümü.
    2. Aşama A (Claim Transaction):
       - Yeni session açılır.
       - claim_ideas_attempt_for_worker çağrılır.
       - Commit edilir ve session kapatılır.
    3. Aşama B (Worker Input Transaction):
       - Yeni session açılır.
       - prepare_social_idea_worker_inputs çağrılır.
       - İmmutable DTO'lar session dışına çıkarılır.
       - Commit edilir ve session kapatılır.
    4. Completed Replay kontrolü:
       - already_completed ise AI çağrısı yapılmaz, doğrudan Replay Persistence doğrulaması yapılır.
    5. Aşama C (AI Üretimi ve Heartbeat):
       - DB session kapalıyken çalışır.
       - SocialBriefIdeaGenerator her kategori için bir kez çağrılır; kategori düzeyi
         AI hatası denemeyi durdurmaz (o kategorinin hedefleri eksik kalır).
       - Eksik hedefler ve boş kategoriler için tek bir sınırlı tamamlama turu yapılır.
       - Her AI isteği ardından ayrı bir Heartbeat Transaction ile lease uzatılır.
       - Toplam çağrı IDEA_ATTEMPT_MAX_AI_CALLS tavanını aşmaz.
    6. Aşama D (Persistence Transaction):
       - Yeni session açılır.
       - persist_social_ideas çağrılır (completed / partial; hiç fikir yoksa failed).
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
        attempt, brief, scoring_run, already_completed = claim_ideas_attempt_for_worker(
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
        _finalize_failure(
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
        prep = prepare_social_idea_worker_inputs(
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
    # 3. COMPLETED REPLAY YOLU
    # -------------------------------------------------------------
    if already_completed:
        replay_session = session_factory()
        try:
            persisted = persist_social_ideas(
                replay_session,
                attempt_id=attempt_id,
                task_id=task_id,
                category_results=(),
                now=_resolve_now(now_provider),
            )
            replay_session.commit()
            return SocialIdeaOrchestrationResult(
                attempt_id=attempt_id,
                brief_id=persisted.brief_id,
                scoring_run_id=persisted.scoring_run_id,
                status="completed",
                total_ideas=persisted.total_ideas,
                ai_calls_used=0,
                replayed=True,
            )
        except Exception as exc:
            replay_session.rollback()
            # Completed replay DB anomalisi varsa başarı gibi raporlanmaz
            # Ancak completed attempt failed durumuna çevrilmez
            raise
        finally:
            replay_session.close()

    # -------------------------------------------------------------
    # 4. AŞAMA C: AI ÜRETİMİ VE HEARTBEAT (DB TRANSACTION AÇIK DEĞİL)
    # -------------------------------------------------------------
    # Plan §3.4–§3.6 + K4/K7:
    # - Bir kategorinin AI çağrısı (tek düzeltme tekrarından sonra) başarısız olursa
    #   deneme DURMAZ; o kategorinin hedefleri eksik kalır.
    # - Tüm kategoriler bittikten sonra TEK ve sınırlı bir tamamlama turu yapılır:
    #   brief genelinde eksik hedefler ve hiç fikir almamış kategoriler (K4),
    #   kategori başına tek çağrıyla istenir (bkz. _build_topup_prompt_inputs).
    # - Heartbeat / lease / worker_lost hataları ve girdi hataları denemeyi durdurur.
    # - Hiç fikir kalmazsa deneme failed olur; sahte yedek fikir üretilmez.
    generator = SocialBriefIdeaGenerator(ai_service)
    ideas_by_category: dict[int, list[ValidatedSocialIdea]] = {
        p.category_id: [] for p in prompt_inputs
    }
    calls_by_category: dict[int, int] = {p.category_id: 0 for p in prompt_inputs}
    metrics = _IdeaAttemptMetrics()
    last_generation_error: SocialIdeaGenerationError | None = None

    def _run_category_request(p_input: SocialIdeaPromptInput) -> bool:
        """Tek kategori isteğini çalıştırır; kategori düzeyi hatada False döner."""
        nonlocal last_generation_error
        if metrics.ai_calls_used + MAX_AI_CALLS_PER_CATEGORY_REQUEST > IDEA_ATTEMPT_MAX_AI_CALLS:
            # Deneme başına sabit çağrı tavanı (plan §5.4): aşılmaz, hedef eksik kalır.
            metrics.budget_skipped += 1
            return False

        # AI çağrısı: Kesinlikle hiçbir DB session açık değilken çalışır
        try:
            cat_result = generator.generate(p_input)
        except SocialIdeaGenerationError as exc:
            if exc.error_code not in CATEGORY_LEVEL_GENERATION_ERRORS:
                # Girdi/sözleşme hatası: denemenin tamamı fail-closed kapanır
                _finalize_failure(
                    session_factory,
                    attempt_id=attempt_id,
                    task_id=task_id,
                    exc=exc,
                    now=_resolve_now(now_provider),
                )
                raise
            last_generation_error = exc
            used = exc.ai_calls_used if isinstance(exc.ai_calls_used, int) else 0
            metrics.ai_calls_used += used
            calls_by_category[p_input.category_id] += used
            metrics.retry_used += 1 if used > 1 else 0
            metrics.off_brief_dropped += exc.off_brief_dropped
            metrics.invalid_dropped += exc.invalid_dropped
            metrics.over_quota_dropped += exc.over_quota_dropped
            metrics.failed_requests += 1
            if used > 0:
                _heartbeat_or_abort(
                    session_factory,
                    attempt_id=attempt_id,
                    task_id=task_id,
                    now_provider=now_provider,
                )
            return False
        except Exception as exc:
            _finalize_failure(
                session_factory,
                attempt_id=attempt_id,
                task_id=task_id,
                exc=exc,
                now=_resolve_now(now_provider),
            )
            raise

        ideas_by_category[p_input.category_id].extend(cat_result.ideas)
        calls_by_category[p_input.category_id] += cat_result.ai_calls_used
        metrics.ai_calls_used += cat_result.ai_calls_used
        metrics.retry_used += cat_result.retry_used
        metrics.off_brief_dropped += cat_result.off_brief_dropped
        metrics.invalid_dropped += cat_result.invalid_dropped
        metrics.over_quota_dropped += cat_result.over_quota_dropped

        # Her AI isteği sonrasında ayrı Heartbeat Transaction
        _heartbeat_or_abort(
            session_factory,
            attempt_id=attempt_id,
            task_id=task_id,
            now_provider=now_provider,
        )
        return True

    # 4a. İlk tur: kategori başına tek istek
    for p_input in prompt_inputs:
        _run_category_request(p_input)

    # 4b. Brief geneli tamamlama turu (tek tur, kategori başına tek istek):
    #     eksik hedefler + hiç fikir almamış kategoriler (K4)
    topup_inputs = _build_topup_prompt_inputs(prompt_inputs, ideas_by_category)
    if topup_inputs:
        metrics.topup_used = 1
        empty_before_topup = {cid for cid, ideas in ideas_by_category.items() if not ideas}
        for topup_input in topup_inputs:
            metrics.topup_requests += 1
            if topup_input.category_id in empty_before_topup:
                metrics.category_topup_requests += 1
            _run_category_request(topup_input)

    # 4c. Hiç fikir yoksa: sahte yedek yok, deneme failed (K7)
    total_accepted = sum(len(v) for v in ideas_by_category.values())
    if total_accepted == 0:
        failure_exc: Exception = last_generation_error or SocialIdeaGenerationError(
            "Yapay zeka fikir çıktısı doğrulanamadı.",
            error_code="IDEA_OUTPUT_INVALID",
        )
        _finalize_failure(
            session_factory,
            attempt_id=attempt_id,
            task_id=task_id,
            exc=failure_exc,
            now=_resolve_now(now_provider),
        )
        raise failure_exc

    category_results = tuple(
        SocialIdeaAIResult(
            attempt_id=attempt_id,
            category_id=p.category_id,
            ideas=tuple(ideas_by_category[p.category_id]),
            ai_calls_used=calls_by_category[p.category_id],
        )
        for p in prompt_inputs
        if ideas_by_category[p.category_id]
    )

    # -------------------------------------------------------------
    # 5. TRANSACTION D: PERSISTENCE TRANSACTION
    # -------------------------------------------------------------
    persist_session = session_factory()
    try:
        persisted = persist_social_ideas(
            persist_session,
            attempt_id=attempt_id,
            task_id=task_id,
            category_results=category_results,
            metrics=metrics.as_dict(),
            now=_resolve_now(now_provider),
        )
        persist_session.commit()
        return SocialIdeaOrchestrationResult(
            attempt_id=attempt_id,
            brief_id=persisted.brief_id,
            scoring_run_id=persisted.scoring_run_id,
            status=persisted.status,
            total_ideas=persisted.total_ideas,
            ai_calls_used=metrics.ai_calls_used,
            replayed=persisted.already_completed,
        )
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
