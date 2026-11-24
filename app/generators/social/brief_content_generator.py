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

from dataclasses import dataclass, replace
from typing import Any

from celery.exceptions import SoftTimeLimitExceeded

from app.core.social.campaign_spec import closing_card_text, finalize_image_prompt
from app.core.social.content_contract import (
    OVERLAY_FIELD,
    OVERLAY_FORMATS,
    ValidatedVideoPayload,
    CONTENT_CONTRACT_INVALID_TEXT,
    IMAGE_PROMPT_FIELD,
    IMAGE_PROMPT_MAX_LENGTH,
    SocialContentOutputValidationError,
    ValidatedSocialContent,
    _render_legacy_scenario_from_payload,
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
from app.generators.output_language import with_output_block
from app.generators.social.brief_content_prompt import (
    SocialContentPromptError,
    SocialContentPromptInput,
    build_social_content_prompt,
    build_social_content_repair_prompt,
    validate_grounding_prompt_parity,
)

MAX_CONTENT_TOKENS: int = 12000


def _apply_image_prompt_rule(
    content: ValidatedSocialContent, prompt_input: SocialContentPromptInput
) -> ValidatedSocialContent:
    """Kampanya içeriğinde image_prompt ZORUNLU (oranı kod ekler); organik içerikte YASAK.
    overlay_text: kampanya statik formatında ZORUNLU, diğer kampanya formatlarında atılır, organikte YASAK."""
    if prompt_input.campaign is None:
        if content.overlay_text is not None:
            raise SocialContentOutputValidationError(
                "overlay_text is only allowed for campaign content.",
                error_code=CONTENT_CONTRACT_INVALID_TEXT,
                field=OVERLAY_FIELD,
            )
        if content.image_prompt is None:
            return content
        raise SocialContentOutputValidationError(
            "image_prompt is only allowed for campaign content.",
            error_code=CONTENT_CONTRACT_INVALID_TEXT,
            field=IMAGE_PROMPT_FIELD,
        )
    if content.image_prompt is None:
        raise SocialContentOutputValidationError(
            "image_prompt is required for campaign content.",
            error_code=CONTENT_CONTRACT_INVALID_TEXT,
            field=IMAGE_PROMPT_FIELD,
        )
    fmt = prompt_input.target_spec.content_format
    if fmt in OVERLAY_FORMATS and content.overlay_text is None:
        raise SocialContentOutputValidationError(
            "overlay_text is required for static campaign content.",
            error_code=CONTENT_CONTRACT_INVALID_TEXT,
            field=OVERLAY_FIELD,
        )
    overlay_text = content.overlay_text if fmt in OVERLAY_FORMATS else None
    final = finalize_image_prompt(content.image_prompt, fmt)
    if len(final) > IMAGE_PROMPT_MAX_LENGTH:
        raise SocialContentOutputValidationError(
            "image_prompt is too long.",
            error_code=CONTENT_CONTRACT_INVALID_TEXT,
            field=IMAGE_PROMPT_FIELD,
        )
    # Model statik formatta video_concept'e "video planlanmamıştır" dolgusu yazıyor; video yoksa boş
    payload = content.format_payload
    is_video = isinstance(payload, ValidatedVideoPayload)
    video_concept = content.video_concept if is_video else None
    closing = closing_card_text(prompt_input.campaign.get("offer_terms"))
    if is_video and closing and payload.segments:
        # Kapanış kartının teklif/şart/tarih metni kilitli koşullardan; sahne ve seslendirme modelin
        last = replace(payload.segments[-1], on_screen_text=closing)
        payload = replace(payload, segments=(*payload.segments[:-1], last))
    # scenario payload'dan türetilir; kayıttaki kanonik karşılaştırma ikisinin eşleşmesini ister
    return replace(content, image_prompt=final, video_concept=video_concept, overlay_text=overlay_text,
                   format_payload=payload, scenario=_render_legacy_scenario_from_payload(payload))


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

    def __init__(self, ai_service: Any, output_ctx: Any = None) -> None:
        self.ai = scoped(ai_service, "social_brief_contents")
        self._output_ctx = output_ctx  # None -> Türkçe/Seçilmedi

    def generate(
        self,
        prompt_input: SocialContentPromptInput,
        extra_instructions: str = "",
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
            # extra_instructions: kullanıcı revizyon bloğu (yalnız revizyon akışı verir)
            prompt = with_output_block(
                build_social_content_prompt(prompt_input) + extra_instructions, self._output_ctx
            )
        except SocialContentPromptError as exc:
            raise SocialContentGenerationError(
                exc.message,
                error_code=exc.error_code,
            ) from exc

        response_schema = to_gemini_response_schema(
            build_social_content_response_schema(
                prompt_input.target_spec, campaign=prompt_input.campaign is not None
            )
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
                validated_content = _apply_image_prompt_rule(
                    validate_social_content_output(raw_response, target_spec=prompt_input.target_spec),
                    prompt_input,
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
            repair_prompt = with_output_block(
                build_social_content_repair_prompt(
                    prompt_input=prompt_input,
                    original_content=original_content,
                    grounding_context=grounding_context,
                    quality_decision=initial_decision,
                ),
                self._output_ctx,
            )
        except SocialContentPromptError as p_exc:
            err_code = p_exc.error_code if p_exc.error_code.startswith("CONTENT_REPAIR_") else "CONTENT_REPAIR_PROMPT_ERROR"
            raise SocialContentRepairError(
                p_exc.message,
                error_code=err_code,
            ) from p_exc

        response_schema = to_gemini_response_schema(
            build_social_content_response_schema(
                prompt_input.target_spec, campaign=prompt_input.campaign is not None
            )
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
                repaired_content = _apply_image_prompt_rule(
                    validate_social_content_output(raw_response, target_spec=prompt_input.target_spec),
                    prompt_input,
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
