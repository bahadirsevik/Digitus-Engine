# -*- coding: utf-8 -*-
"""Tek Work Item Generate → Quality → En Fazla Bir Repair Yürütücüsü (F1-G.5.3).

Bu modül SocialContentWorkItem birimi için izole ve DB'siz içerik üretim hattını yürütür:
1. Girdi ve prompt-grounding parite doğrulaması (0 AI çağrısı).
2. İlk üretim denemesi (tam 1 AI çağrısı).
3. İlk otoriter kalite değerlendirmesi (repair_attempted=False):
   - accept: Temiz içerik kabul edilir (repaired=False, ai_calls_used=1).
   - repair: Tek birleşik düzeltme adımına geçilir.
   - reject: Beklenmeyen sözleşme ihlali (fail-closed hata).
4. Tek birleşik düzeltme denemesi (tam 1 ek AI çağrısı, toplam 2):
   - repair_once adaptörü çalıştırılır.
5. İkinci otoriter kalite değerlendirmesi (repair_attempted=True):
   - accept: Düzeltilmiş içerik kabul edilir (repaired=True, ai_calls_used=2).
   - reject: Reddedilir, content=None güvencesiyle döner (repaired=True, ai_calls_used=2).
   - repair: İkinci tamir kesinlikle yasaktır (fail-closed hata).
6. Kesinlikle en fazla 2 AI çağrısı yapılabilir; otomatik retry veya döngü bulunmaz.
7. DB, ORM veya HTTP katmanlarına dokunmaz (yalnız tip tespiti için
   `celery.exceptions.SoftTimeLimitExceeded` import edilir; broker/queue
   bağımlılığı YOKTUR).
8. K8 (plan_social_brief_akisi.md §4): ilk içeriğin TEK sorunu süre
   uyumsuzluğuysa ve repair_once sağlayıcı/çıktı hatasıyla başarısız
   olursa, orijinal grounding-temiz içerik `duration_mismatch` uyarısıyla
   accepted döner (repaired=True, ai_calls_used=2) — tamamen kaybedilmez.
9. `celery.exceptions.SoftTimeLimitExceeded` hiçbir zaman sıradan bir
   sağlayıcı/geçersiz-çıktı hatasına dönüştürülmez; her broad except
   bloğundan önce açıkça yeniden fırlatılır.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from celery.exceptions import SoftTimeLimitExceeded

from app.core.social.content_contract import ValidatedSocialContent
from app.core.social.content_quality import (
    SocialContentGroundingContext,
    SocialContentQualityDecision,
    SocialContentQualityError,
    evaluate_social_content_quality,
)
from app.core.social.content_worker_input import SocialContentWorkItem
from app.generators.social.brief_content_generator import (
    SocialBriefContentGenerator,
    SocialContentGenerationError,
    SocialContentRepairError,
)
from app.generators.social.brief_content_prompt import (
    SocialContentPromptError,
    validate_grounding_prompt_parity,
    validate_social_content_prompt_input,
)

# ==================== SABİT REASON KODLARI ====================

CONTENT_INPUT_INVALID = "content_input_invalid"
CONTENT_PROVIDER_ERROR = "content_provider_error"
CONTENT_OUTPUT_INVALID = "content_output_invalid"
CONTENT_QUALITY_INVALID = "content_quality_invalid"
CONTENT_REPAIR_INPUT_INVALID = "content_repair_input_invalid"
CONTENT_REPAIR_PROVIDER_ERROR = "content_repair_provider_error"
CONTENT_REPAIR_OUTPUT_INVALID = "content_repair_output_invalid"
CONTENT_EXECUTION_INCONSISTENT = "content_execution_inconsistent"


# ==================== DOMAIN EXCEPTIONS ====================


class SocialContentItemExecutionError(Exception):
    """Tek bir sosyal içerik work item yürütme domain hatası (fail-closed)."""

    def __init__(
        self,
        message: str,
        *,
        error_code: str = "CONTENT_ITEM_EXECUTION_FAILED",
        reason_code: str,
        ai_calls_used: int = 0,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.reason_code = reason_code
        self.ai_calls_used = ai_calls_used

    def __str__(self) -> str:
        return f"[{self.error_code}:{self.reason_code}] {self.message} (ai_calls_used={self.ai_calls_used})"


# ==================== RESULT DTO (IMMUTABLE) ====================


@dataclass(frozen=True)
class SocialContentItemExecutionResult:
    """Tekil bir sosyal içerik work item'ının yürütme nihai sonucu (immutable)."""

    attempt_id: int
    idea_id: int
    status: str  # "accepted" | "rejected"
    content: ValidatedSocialContent | None
    quality_decision: SocialContentQualityDecision
    repaired: bool
    ai_calls_used: int

    def __post_init__(self) -> None:
        calls = self.ai_calls_used if (type(self.ai_calls_used) is int and not isinstance(self.ai_calls_used, bool)) else 0

        if (
            isinstance(self.attempt_id, bool)
            or type(self.attempt_id) is not int
            or self.attempt_id <= 0
        ):
            raise SocialContentItemExecutionError(
                "Geçersiz attempt_id.",
                reason_code=CONTENT_EXECUTION_INCONSISTENT,
                ai_calls_used=calls,
            )
        if (
            isinstance(self.idea_id, bool)
            or type(self.idea_id) is not int
            or self.idea_id <= 0
        ):
            raise SocialContentItemExecutionError(
                "Geçersiz idea_id.",
                reason_code=CONTENT_EXECUTION_INCONSISTENT,
                ai_calls_used=calls,
            )
        if self.status not in ("accepted", "rejected"):
            raise SocialContentItemExecutionError(
                "status 'accepted' veya 'rejected' olmalıdır.",
                reason_code=CONTENT_EXECUTION_INCONSISTENT,
                ai_calls_used=calls,
            )
        if type(self.quality_decision) is not SocialContentQualityDecision:
            raise SocialContentItemExecutionError(
                "quality_decision exact SocialContentQualityDecision olmalıdır.",
                reason_code=CONTENT_EXECUTION_INCONSISTENT,
                ai_calls_used=calls,
            )
        if type(self.repaired) is not bool:
            raise SocialContentItemExecutionError(
                "repaired exact bool olmalıdır.",
                reason_code=CONTENT_EXECUTION_INCONSISTENT,
                ai_calls_used=calls,
            )
        if (
            isinstance(self.ai_calls_used, bool)
            or type(self.ai_calls_used) is not int
            or self.ai_calls_used not in (1, 2)
        ):
            raise SocialContentItemExecutionError(
                "ai_calls_used 1 veya 2 olmalıdır.",
                reason_code=CONTENT_EXECUTION_INCONSISTENT,
                ai_calls_used=0,
            )

        if self.status == "accepted":
            if type(self.content) is not ValidatedSocialContent:
                raise SocialContentItemExecutionError(
                    "accepted durumunda content ValidatedSocialContent olmalıdır.",
                    reason_code=CONTENT_EXECUTION_INCONSISTENT,
                    ai_calls_used=self.ai_calls_used,
                )
            if self.quality_decision.action != "accept":
                raise SocialContentItemExecutionError(
                    "accepted durumunda quality_decision action 'accept' olmalıdır.",
                    reason_code=CONTENT_EXECUTION_INCONSISTENT,
                    ai_calls_used=self.ai_calls_used,
                )
        elif self.status == "rejected":
            if self.content is not None:
                raise SocialContentItemExecutionError(
                    "rejected durumunda content kesinlikle None olmalıdır.",
                    reason_code=CONTENT_EXECUTION_INCONSISTENT,
                    ai_calls_used=self.ai_calls_used,
                )
            if self.quality_decision.action != "reject":
                raise SocialContentItemExecutionError(
                    "rejected durumunda quality_decision action 'reject' olmalıdır.",
                    reason_code=CONTENT_EXECUTION_INCONSISTENT,
                    ai_calls_used=self.ai_calls_used,
                )

        if not self.repaired:
            if self.ai_calls_used != 1:
                raise SocialContentItemExecutionError(
                    "repaired=False durumunda ai_calls_used tam olarak 1 olmalıdır.",
                    reason_code=CONTENT_EXECUTION_INCONSISTENT,
                    ai_calls_used=self.ai_calls_used,
                )
            if self.quality_decision.repair_attempted is not False:
                raise SocialContentItemExecutionError(
                    "repaired=False durumunda quality_decision.repair_attempted False olmalıdır.",
                    reason_code=CONTENT_EXECUTION_INCONSISTENT,
                    ai_calls_used=self.ai_calls_used,
                )
        else:
            if self.ai_calls_used != 2:
                raise SocialContentItemExecutionError(
                    "repaired=True durumunda ai_calls_used tam olarak 2 olmalıdır.",
                    reason_code=CONTENT_EXECUTION_INCONSISTENT,
                    ai_calls_used=self.ai_calls_used,
                )
            if self.quality_decision.repair_attempted is not True:
                raise SocialContentItemExecutionError(
                    "repaired=True durumunda quality_decision.repair_attempted True olmalıdır.",
                    reason_code=CONTENT_EXECUTION_INCONSISTENT,
                    ai_calls_used=self.ai_calls_used,
                )


# ==================== ORCHESTRATOR ====================


def execute_social_content_work_item(
    *,
    ai_service: Any,
    work_item: SocialContentWorkItem,
) -> SocialContentItemExecutionResult:
    """Tek bir sosyal içerik work item'ını generate -> quality -> max 1 repair hattıyla yürütür.

    Args:
        ai_service: Yapay zeka servis nesnesi (AIService veya uyumlu adaptör).
        work_item: Doğrulanmış tekil içerik çalışma birimi (SocialContentWorkItem).

    Returns:
        SocialContentItemExecutionResult: 'accepted' veya 'rejected' statülü nihai sonuç.

    Raises:
        SocialContentItemExecutionError: Giriş, sağlayıcı, doğrulama veya tutarlılık hatası.
    """
    # ----------------------------------------------------
    # Faz 0: Önkoşul ve Parite Doğrulaması (0 AI Çağrısı)
    # ----------------------------------------------------
    if ai_service is None:
        raise SocialContentItemExecutionError(
            "ai_service boş olamaz.",
            error_code="CONTENT_ITEM_EXECUTION_FAILED",
            reason_code=CONTENT_INPUT_INVALID,
            ai_calls_used=0,
        )

    if type(work_item) is not SocialContentWorkItem:
        raise SocialContentItemExecutionError(
            "work_item exact SocialContentWorkItem olmalıdır.",
            error_code="CONTENT_ITEM_EXECUTION_FAILED",
            reason_code=CONTENT_INPUT_INVALID,
            ai_calls_used=0,
        )

    try:
        validate_social_content_prompt_input(work_item.prompt_input)
    except SocialContentPromptError:
        raise SocialContentItemExecutionError(
            "prompt_input doğrulaması başarısız oldu.",
            error_code="CONTENT_ITEM_EXECUTION_FAILED",
            reason_code=CONTENT_INPUT_INVALID,
            ai_calls_used=0,
        ) from None

    if type(work_item.grounding_context) is not SocialContentGroundingContext:
        raise SocialContentItemExecutionError(
            "grounding_context exact SocialContentGroundingContext olmalıdır.",
            error_code="CONTENT_ITEM_EXECUTION_FAILED",
            reason_code=CONTENT_INPUT_INVALID,
            ai_calls_used=0,
        )

    try:
        validate_grounding_prompt_parity(
            work_item.grounding_context,
            work_item.prompt_input,
        )
    except SocialContentPromptError:
        raise SocialContentItemExecutionError(
            "Grounding context ile prompt input paritesi doğrulanamadı.",
            error_code="CONTENT_ITEM_EXECUTION_FAILED",
            reason_code=CONTENT_INPUT_INVALID,
            ai_calls_used=0,
        ) from None

    if work_item.idea_id != work_item.prompt_input.idea_id:
        raise SocialContentItemExecutionError(
            "work_item idea_id ile prompt_input idea_id uyuşmuyor.",
            error_code="CONTENT_ITEM_EXECUTION_FAILED",
            reason_code=CONTENT_INPUT_INVALID,
            ai_calls_used=0,
        )

    # ----------------------------------------------------
    # Faz 1: İlk İçerik Üretimi (Tam 1 AI Çağrısı)
    # ----------------------------------------------------
    generator = SocialBriefContentGenerator(ai_service)
    try:
        initial_ai_result = generator.generate(work_item.prompt_input)
    except SocialContentGenerationError as exc:
        if exc.error_code == "CONTENT_PROVIDER_ERROR":
            mapped_reason = CONTENT_PROVIDER_ERROR
            calls = 1
        elif exc.error_code == "CONTENT_OUTPUT_INVALID":
            mapped_reason = CONTENT_OUTPUT_INVALID
            calls = 1
        elif exc.error_code in ("CONTENT_PROMPT_INVALID_INPUT", "CONTENT_PROMPT_INVALID_TARGET"):
            mapped_reason = CONTENT_INPUT_INVALID
            calls = 0
        else:
            mapped_reason = CONTENT_EXECUTION_INCONSISTENT
            calls = 1

        raise SocialContentItemExecutionError(
            "İçerik üretimi sırasında hata oluştu.",
            error_code="CONTENT_ITEM_EXECUTION_FAILED",
            reason_code=mapped_reason,
            ai_calls_used=calls,
        ) from None
    except SoftTimeLimitExceeded:
        # Celery yumuşak zaman sınırı sıradan bir sağlayıcı hatası GİBİ
        # yutulamaz; olduğu gibi yukarı fırlatılmalıdır (CLAUDE.md §11).
        raise
    except Exception:
        raise SocialContentItemExecutionError(
            "İçerik üretimi sırasında beklenmeyen sağlayıcı hatası oluştu.",
            error_code="CONTENT_ITEM_EXECUTION_FAILED",
            reason_code=CONTENT_PROVIDER_ERROR,
            ai_calls_used=1,
        ) from None

    if (
        initial_ai_result.ai_calls_used != 1
        or initial_ai_result.attempt_id != work_item.prompt_input.attempt_id
        or initial_ai_result.idea_id != work_item.idea_id
        or type(initial_ai_result.content) is not ValidatedSocialContent
    ):
        raise SocialContentItemExecutionError(
            "İçerik üretim sonucu tutarsız.",
            error_code="CONTENT_ITEM_EXECUTION_FAILED",
            reason_code=CONTENT_EXECUTION_INCONSISTENT,
            ai_calls_used=1,
        )

    # ----------------------------------------------------
    # Faz 2: İlk Otoriter Kalite Değerlendirmesi
    # ----------------------------------------------------
    try:
        initial_decision = evaluate_social_content_quality(
            initial_ai_result.content,
            work_item.grounding_context,
            repair_attempted=False,
        )
    except SocialContentQualityError:
        raise SocialContentItemExecutionError(
            "İlk kalite değerlendirmesi başarısız oldu.",
            error_code="CONTENT_ITEM_EXECUTION_FAILED",
            reason_code=CONTENT_QUALITY_INVALID,
            ai_calls_used=1,
        ) from None

    if initial_decision.action == "accept":
        return SocialContentItemExecutionResult(
            attempt_id=work_item.prompt_input.attempt_id,
            idea_id=work_item.idea_id,
            status="accepted",
            content=initial_ai_result.content,
            quality_decision=initial_decision,
            repaired=False,
            ai_calls_used=1,
        )

    if initial_decision.action == "reject":
        raise SocialContentItemExecutionError(
            "İlk kalite değerlendirmesinde beklenmeyen ret kararı verildi.",
            error_code="CONTENT_ITEM_EXECUTION_FAILED",
            reason_code=CONTENT_EXECUTION_INCONSISTENT,
            ai_calls_used=1,
        )

    if initial_decision.action != "repair":
        raise SocialContentItemExecutionError(
            "İlk kalite değerlendirmesinde bilinmeyen eylem kararı verildi.",
            error_code="CONTENT_ITEM_EXECUTION_FAILED",
            reason_code=CONTENT_EXECUTION_INCONSISTENT,
            ai_calls_used=1,
        )

    # ----------------------------------------------------
    # Faz 3: Tek Birleşik Düzeltme Denemesi (1 Ek AI Çağrısı, Toplam 2)
    # ----------------------------------------------------
    try:
        repair_ai_result = generator.repair_once(
            prompt_input=work_item.prompt_input,
            original_content=initial_ai_result.content,
            grounding_context=work_item.grounding_context,
        )
    except SocialContentRepairError as exc:
        if exc.error_code in ("CONTENT_REPAIR_PROVIDER_ERROR", "CONTENT_REPAIR_OUTPUT_INVALID"):
            mapped_reason = (
                CONTENT_REPAIR_PROVIDER_ERROR
                if exc.error_code == "CONTENT_REPAIR_PROVIDER_ERROR"
                else CONTENT_REPAIR_OUTPUT_INVALID
            )
            # K8 (plan_social_brief_akisi.md §4): repair hakkı tektir. Eğer
            # ilk içeriğin TEK kalite sorunu süre uyumsuzluğuysa (grounding
            # zaten temiz) ve repair_once sağlayıcı/çıktı hatasıyla
            # başarısız olduysa, ORİJİNAL grounding-temiz içerik süre
            # mismatch uyarısıyla KORUNUR — tamamen kaybedilmez. Grounding
            # veya başka bir sert sorun varsa bu dal uygulanmaz (aşağı
            # fail-closed hata akışı devam eder).
            if initial_decision.reason_codes == ("duration_mismatch",):
                fallback_decision = evaluate_social_content_quality(
                    initial_ai_result.content,
                    work_item.grounding_context,
                    repair_attempted=True,
                )
                if fallback_decision.action == "accept":
                    return SocialContentItemExecutionResult(
                        attempt_id=work_item.prompt_input.attempt_id,
                        idea_id=work_item.idea_id,
                        status="accepted",
                        content=initial_ai_result.content,
                        quality_decision=fallback_decision,
                        repaired=True,
                        ai_calls_used=2,
                    )

            raise SocialContentItemExecutionError(
                "İçerik düzeltme sırasında hata oluştu.",
                error_code="CONTENT_ITEM_EXECUTION_FAILED",
                reason_code=mapped_reason,
                ai_calls_used=2,
            ) from None
        if exc.error_code in (
            "CONTENT_REPAIR_INVALID_INPUT",
            "CONTENT_REPAIR_NOT_REQUIRED",
            "CONTENT_REPAIR_PROMPT_ERROR",
        ):
            mapped_reason = CONTENT_REPAIR_INPUT_INVALID
            calls = 1
        else:
            mapped_reason = CONTENT_EXECUTION_INCONSISTENT
            calls = 2

        raise SocialContentItemExecutionError(
            "İçerik düzeltme sırasında hata oluştu.",
            error_code="CONTENT_ITEM_EXECUTION_FAILED",
            reason_code=mapped_reason,
            ai_calls_used=calls,
        ) from None
    except SoftTimeLimitExceeded:
        # Celery yumuşak zaman sınırı sıradan bir sağlayıcı hatası GİBİ
        # yutulamaz; olduğu gibi yukarı fırlatılmalıdır (CLAUDE.md §11).
        raise
    except Exception:
        raise SocialContentItemExecutionError(
            "İçerik düzeltme sırasında beklenmeyen sağlayıcı hatası oluştu.",
            error_code="CONTENT_ITEM_EXECUTION_FAILED",
            reason_code=CONTENT_REPAIR_PROVIDER_ERROR,
            ai_calls_used=2,
        ) from None

    if (
        repair_ai_result.ai_calls_used != 1
        or repair_ai_result.attempt_id != work_item.prompt_input.attempt_id
        or repair_ai_result.idea_id != work_item.idea_id
        or type(repair_ai_result.content) is not ValidatedSocialContent
        or type(repair_ai_result.quality_decision) is not SocialContentQualityDecision
    ):
        raise SocialContentItemExecutionError(
            "Düzeltme sonucu tutarsız.",
            error_code="CONTENT_ITEM_EXECUTION_FAILED",
            reason_code=CONTENT_EXECUTION_INCONSISTENT,
            ai_calls_used=2,
        )

    # ----------------------------------------------------
    # Faz 4: Düzeltme Sonrası İkinci Otoriter Kalite Değerlendirmesi
    # ----------------------------------------------------
    try:
        authoritative_repaired_decision = evaluate_social_content_quality(
            repair_ai_result.content,
            work_item.grounding_context,
            repair_attempted=True,
        )
    except SocialContentQualityError:
        raise SocialContentItemExecutionError(
            "Düzeltilmiş içerik kalite değerlendirmesi başarısız oldu.",
            error_code="CONTENT_ITEM_EXECUTION_FAILED",
            reason_code=CONTENT_QUALITY_INVALID,
            ai_calls_used=2,
        ) from None

    if (
        repair_ai_result.quality_decision.action != authoritative_repaired_decision.action
        or repair_ai_result.quality_decision.reason_codes != authoritative_repaired_decision.reason_codes
        or repair_ai_result.quality_decision.claims != authoritative_repaired_decision.claims
        or repair_ai_result.quality_decision.warnings != authoritative_repaired_decision.warnings
        or repair_ai_result.quality_decision.grounding_clean != authoritative_repaired_decision.grounding_clean
        or repair_ai_result.quality_decision.duration_acceptable != authoritative_repaired_decision.duration_acceptable
        or repair_ai_result.quality_decision.repair_attempted != authoritative_repaired_decision.repair_attempted
    ):
        raise SocialContentItemExecutionError(
            "Düzeltme kalite kararı otoriter değerlendirme ile uyuşmuyor.",
            error_code="CONTENT_ITEM_EXECUTION_FAILED",
            reason_code=CONTENT_EXECUTION_INCONSISTENT,
            ai_calls_used=2,
        )

    if authoritative_repaired_decision.action == "repair":
        raise SocialContentItemExecutionError(
            "Düzeltme sonrası tekrar tamir talep edilemez.",
            error_code="CONTENT_ITEM_EXECUTION_FAILED",
            reason_code=CONTENT_EXECUTION_INCONSISTENT,
            ai_calls_used=2,
        )

    if authoritative_repaired_decision.action == "accept":
        return SocialContentItemExecutionResult(
            attempt_id=work_item.prompt_input.attempt_id,
            idea_id=work_item.idea_id,
            status="accepted",
            content=repair_ai_result.content,
            quality_decision=authoritative_repaired_decision,
            repaired=True,
            ai_calls_used=2,
        )

    if authoritative_repaired_decision.action == "reject":
        return SocialContentItemExecutionResult(
            attempt_id=work_item.prompt_input.attempt_id,
            idea_id=work_item.idea_id,
            status="rejected",
            content=None,
            quality_decision=authoritative_repaired_decision,
            repaired=True,
            ai_calls_used=2,
        )

    raise SocialContentItemExecutionError(
        "Düzeltme sonrası bilinmeyen kalite kararı verildi.",
        error_code="CONTENT_ITEM_EXECUTION_FAILED",
        reason_code=CONTENT_EXECUTION_INCONSISTENT,
        ai_calls_used=2,
    )
