# -*- coding: utf-8 -*-
"""Sosyal Brief İçerik Üretim Adaptörü (F1-G.2).

Bu modül SocialBriefContentGenerator sınıfını sunar:
- AIService üzerinden scoped("social_brief_contents") kullanır.
- Fikir bazında tek AI çağrısı yapar (ai_calls_used=1).
- Girdi/hedef doğrulama hatasında 0 AI çağrısı yapar.
- Kendi kendine retry yapmaz (tek çağrılı fail-closed).
- Provider exception durumunda CONTENT_PROVIDER_ERROR fırlatır ve retry yapmaz.
- Çıktı doğrulama hatasında mark_attempt_failed telemetrisi işletir ve CONTENT_OUTPUT_INVALID fırlatır.
- Kesinlikle fallback içerik üretmez, sessiz alan dönüştürmesi veya truncation yapmaz.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from celery.exceptions import SoftTimeLimitExceeded

from app.core.social.content_contract import (
    SocialContentOutputValidationError,
    ValidatedSocialContent,
    build_social_content_response_schema,
    validate_social_content_output,
)
from app.core.social.provider_schema import to_gemini_response_schema
from app.core.social.content_quality import (
    SocialContentGroundingContext,
    SocialContentQualityDecision,
    SocialContentQualityError,
    evaluate_social_content_quality,
)
from app.generators.ai_service import logical_request, mark_attempt_failed, scoped
from app.generators.social.brief_content_prompt import (
    SocialContentPromptError,
    SocialContentPromptInput,
    build_social_content_prompt,
    build_social_content_repair_prompt,
    validate_grounding_prompt_parity,
)

MAX_CONTENT_TOKENS: int = 12000


class SocialContentGenerationError(ValueError):
    """Sosyal içerik üretim domain hatası."""

    def __init__(
        self,
        message: str,
        *,
        error_code: str,
        validation_error_code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.validation_error_code = validation_error_code

    def __str__(self) -> str:
        parts = [f"[{self.error_code}]", self.message]
        if self.validation_error_code is not None:
            parts.append(f"(validation_error_code={self.validation_error_code})")
        return " ".join(parts)


class SocialContentRepairError(SocialContentGenerationError):
    """Sosyal içerik düzeltme (repair) domain hatası."""


@dataclass(frozen=True)
class SocialContentAIResult:
    """İçerik AI üretimi nihai sonucu (immutable)."""

    attempt_id: int
    idea_id: int
    content: ValidatedSocialContent
    ai_calls_used: int


@dataclass(frozen=True)
class SocialContentRepairAIResult:
    """İçerik AI tekil düzeltme (repair) nihai sonucu (immutable)."""

    attempt_id: int
    idea_id: int
    content: ValidatedSocialContent
    quality_decision: SocialContentQualityDecision
    ai_calls_used: int


class SocialBriefContentGenerator:
    """Sosyal brief içerik üretim adaptörü."""

    def __init__(self, ai_service: Any) -> None:
        self.ai = scoped(ai_service, "social_brief_contents")

    def generate(
        self,
        prompt_input: SocialContentPromptInput,
    ) -> SocialContentAIResult:
        """Fikir için içerik üretimini gerçekleştirir ve doğrulanmış sonuç döner.

        Args:
            prompt_input: Dondurulmuş prompt girdi nesnesi.

        Returns:
            SocialContentAIResult: Doğrulanmış içerik ve harcanan AI çağrı sayısı.

        Raises:
            SocialContentGenerationError: Girdi hatası, sağlayıcı hatası veya geçersiz çıktı.
        """
        # 1. Girdi ve target doğrulaması (0 AI çağrısı)
        try:
            prompt = build_social_content_prompt(prompt_input)
        except SocialContentPromptError as exc:
            raise SocialContentGenerationError(
                exc.message,
                error_code=exc.error_code,
            ) from exc

        response_schema = to_gemini_response_schema(
            build_social_content_response_schema(prompt_input.target_spec)
        )

        ai_calls_used = 0

        # 2. Mantıksal istek ve tekil AI çağrısı (tam 1 çağrı, otomatik retry yok)
        with logical_request(self.ai):
            ai_calls_used += 1
            try:
                raw_response = self.ai.complete_json(
                    prompt,
                    max_tokens=MAX_CONTENT_TOKENS,
                    # Gemini sampling ayarı: None değerinde temperature parametresi
                    # API isteğine hiç gönderilmez. Kesinlikle 0 gönderilmemelidir.
                    temperature=None,
                    response_schema=response_schema,
                )
            except SoftTimeLimitExceeded:
                # Celery yumuşak zaman sınırı: normal sağlayıcı hatası GİBİ
                # yutulamaz, olduğu gibi yukarı fırlatılmalıdır (CLAUDE.md §11).
                raise
            except Exception as exc:
                # Provider exception: ek retry veya fallback yapılmaz
                raise SocialContentGenerationError(
                    "Yapay zeka sağlayıcı hatası oluştu.",
                    error_code="CONTENT_PROVIDER_ERROR",
                ) from exc

            try:
                validated_content = validate_social_content_output(
                    raw_response,
                    target_spec=prompt_input.target_spec,
                )
                return SocialContentAIResult(
                    attempt_id=prompt_input.attempt_id,
                    idea_id=prompt_input.idea_id,
                    content=validated_content,
                    ai_calls_used=ai_calls_used,
                )
            except SocialContentOutputValidationError as val_exc:
                # Güvenli telemetry failure işareti (raw çıktı/başlık/kullanıcı metni sızmaz)
                reason_parts = [val_exc.error_code]
                if val_exc.field:
                    reason_parts.append(f"field={val_exc.field}")
                if val_exc.item_index is not None:
                    reason_parts.append(f"index={val_exc.item_index}")
                mark_attempt_failed(self.ai, ":".join(reason_parts))

                raise SocialContentGenerationError(
                    "Yapay zeka içerik çıktısı doğrulanamadı.",
                    error_code="CONTENT_OUTPUT_INVALID",
                    validation_error_code=val_exc.error_code,
                ) from val_exc

    def repair_once(
        self,
        prompt_input: SocialContentPromptInput,
        original_content: ValidatedSocialContent,
        grounding_context: SocialContentGroundingContext,
    ) -> SocialContentRepairAIResult:
        """Sorunlu sosyal içerik için tek çağrılı ve birleşik repair işlemini gerçekleştirir.

        Args:
            prompt_input: Dondurulmuş prompt girdi nesnesi.
            original_content: İlk üretimden çıkan doğrulanmış orijinal içerik.
            grounding_context: Otoriter grounding ve muafiyet bağlamı.

        Returns:
            SocialContentRepairAIResult: Doğrulanmış tamir çıktısı, kalite kararı ve harcanan AI çağrı sayısı (tam 1).

        Raises:
            SocialContentRepairError: Girdi hatası, düzeltme gerekmeme durumu, sağlayıcı hatası veya çıktı doğrulama hatası.
        """
        # 1. Girdi DTO tipleri doğrulaması (0 AI çağrısı)
        if type(prompt_input) is not SocialContentPromptInput:
            raise SocialContentRepairError(
                "prompt_input exact SocialContentPromptInput olmalıdır.",
                error_code="CONTENT_REPAIR_INVALID_INPUT",
            )
        if type(original_content) is not ValidatedSocialContent:
            raise SocialContentRepairError(
                "original_content exact ValidatedSocialContent olmalıdır.",
                error_code="CONTENT_REPAIR_INVALID_INPUT",
            )
        if type(grounding_context) is not SocialContentGroundingContext:
            raise SocialContentRepairError(
                "grounding_context exact SocialContentGroundingContext olmalıdır.",
                error_code="CONTENT_REPAIR_INVALID_INPUT",
            )

        # 1.5. Grounding context ile prompt input paritesi (0 AI çağrısı)
        try:
            validate_grounding_prompt_parity(grounding_context, prompt_input)
        except SocialContentPromptError as p_exc:
            raise SocialContentRepairError(
                p_exc.message,
                error_code="CONTENT_REPAIR_INVALID_INPUT",
            ) from p_exc

        # 2. Otoriter ilk kalite kararını içeride yeniden hesapla (forged kararları engelle, 0 AI çağrısı)
        try:
            initial_decision = evaluate_social_content_quality(
                original_content,
                grounding_context,
                repair_attempted=False,
            )
        except SocialContentQualityError as q_exc:
            raise SocialContentRepairError(
                q_exc.message,
                error_code="CONTENT_REPAIR_INVALID_INPUT",
            ) from q_exc

        # 3. Düzeltme gerekli mi? (Gerekli değilse 0 AI çağrısıyla dur)
        if initial_decision.action != "repair":
            raise SocialContentRepairError(
                "İçerik düzeltme gerektirmiyor (action 'repair' değil).",
                error_code="CONTENT_REPAIR_NOT_REQUIRED",
            )

        # 4. Tek birleşik repair promptunu oluştur (0 AI çağrısı)
        try:
            repair_prompt = build_social_content_repair_prompt(
                prompt_input=prompt_input,
                original_content=original_content,
                grounding_context=grounding_context,
                quality_decision=initial_decision,
            )
        except SocialContentPromptError as p_exc:
            err_code = p_exc.error_code if p_exc.error_code.startswith("CONTENT_REPAIR_") else "CONTENT_REPAIR_PROMPT_ERROR"
            raise SocialContentRepairError(
                p_exc.message,
                error_code=err_code,
            ) from p_exc

        response_schema = to_gemini_response_schema(
            build_social_content_response_schema(prompt_input.target_spec)
        )

        ai_calls_used = 0

        # 5. Mantıksal istek ve tekil AI çağrısı (tam 1 çağrı, otomatik retry yok)
        with logical_request(self.ai):
            ai_calls_used += 1
            try:
                raw_response = self.ai.complete_json(
                    repair_prompt,
                    max_tokens=MAX_CONTENT_TOKENS,
                    temperature=None,
                    response_schema=response_schema,
                )
            except SoftTimeLimitExceeded:
                # Celery yumuşak zaman sınırı: normal sağlayıcı hatası GİBİ
                # yutulamaz, olduğu gibi yukarı fırlatılmalıdır (CLAUDE.md §11).
                raise
            except Exception as exc:
                raise SocialContentRepairError(
                    "Yapay zeka sağlayıcı hatası oluştu.",
                    error_code="CONTENT_REPAIR_PROVIDER_ERROR",
                ) from exc

            # 6. Çıktı yapısal doğrulaması
            try:
                repaired_content = validate_social_content_output(
                    raw_response,
                    target_spec=prompt_input.target_spec,
                )
            except SocialContentOutputValidationError as val_exc:
                reason_parts = [val_exc.error_code]
                if val_exc.field:
                    reason_parts.append(f"field={val_exc.field}")
                if val_exc.item_index is not None:
                    reason_parts.append(f"index={val_exc.item_index}")
                mark_attempt_failed(self.ai, ":".join(reason_parts))

                raise SocialContentRepairError(
                    "Yapay zeka düzeltme çıktısı doğrulanamadı.",
                    error_code="CONTENT_REPAIR_OUTPUT_INVALID",
                    validation_error_code=val_exc.error_code,
                ) from val_exc

            # 7. Post-repair kalite ve süre değerlendirmesi (repair_attempted=True)
            repaired_decision = evaluate_social_content_quality(
                repaired_content,
                grounding_context,
                repair_attempted=True,
            )

            # Ne olursa olsun ikinci bir AI çağrısı yapılmaz (ai_calls_used=1)
            return SocialContentRepairAIResult(
                attempt_id=prompt_input.attempt_id,
                idea_id=prompt_input.idea_id,
                content=repaired_content,
                quality_decision=repaired_decision,
                ai_calls_used=ai_calls_used,
            )
