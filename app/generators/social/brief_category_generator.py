# -*- coding: utf-8 -*-
"""Sosyal Brief Kategori Üretim Adaptörü (F1-E.3).

Bu modül SocialBriefCategoryGenerator sınıfını sunar.
- AIService üzerinden scoped("social_brief_categories") kullanır.
- Replay koruması sağlar (attempt_created=False ise 0 AI çağrısı).
- İlk doğrulama başarısız olursa tam olarak 1 kez retry yapar.
- İki yanıt da geçersizse CATEGORY_OUTPUT_INVALID fırlatır.
- Provider exception durumunda CATEGORY_PROVIDER_ERROR fırlatır.
- Kesinlikle fallback kategori üretmez veya sessiz truncation yapmaz.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.social.category_contract import (
    SocialCategoryOutputValidationError,
    ValidatedSocialCategory,
    get_social_category_response_schema,
    validate_social_category_output,
)
from app.core.social.category_flow import SocialCategoryGenerationStart
from app.core.social.provider_schema import to_gemini_response_schema
from app.generators.ai_service import logical_request, mark_attempt_failed, scoped
from app.generators.social.brief_category_prompt import (
    SocialCategoryPromptError,
    build_category_retry_correction,
    build_social_category_prompt,
)


class SocialCategoryGenerationError(ValueError):
    """Sosyal kategori üretim domain hatası."""

    def __init__(
        self,
        message: str,
        *,
        error_code: str,
        validation_error_code: str | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.validation_error_code = validation_error_code

    def __str__(self) -> str:
        parts = [f"[{self.error_code}]", self.message]
        if self.validation_error_code is not None:
            parts.append(f"(validation_error_code={self.validation_error_code})")
        return " ".join(parts)


@dataclass(frozen=True)
class SocialCategoryAIResult:
    """Kategori AI üretimi nihai sonucu (immutable)."""

    attempt_id: int
    categories: tuple[ValidatedSocialCategory, ...]
    ai_calls_used: int


class SocialBriefCategoryGenerator:
    """Sosyal brief kategori üretim adaptörü."""

    def __init__(self, ai_service: Any) -> None:
        self.ai = scoped(ai_service, "social_brief_categories")

    def generate(
        self,
        start: SocialCategoryGenerationStart,
    ) -> SocialCategoryAIResult:
        """Kategori üretimini gerçekleştirir ve doğrulanmış sonuç döner.

        Args:
            start: Preflight aşamasından dönen dondurulmuş başlangıç verisi.

        Returns:
            SocialCategoryAIResult: Doğrulanmış kategoriler ve harcanan AI çağrı sayısı.

        Raises:
            SocialCategoryGenerationError: Replay engeli, sağlayıcı hatası veya geçersiz çıktı.
        """
        # 1. Replay ve preflight kontrolleri (0 AI çağrısı)
        if not start.attempt_created:
            raise SocialCategoryGenerationError(
                "Replay edilmiş attempt için kategori üretimi yapılamaz.",
                error_code="CATEGORY_GENERATION_NOT_ALLOWED",
            )

        if (
            start.max_categories is None
            or isinstance(start.max_categories, bool)
            or not isinstance(start.max_categories, int)
            or start.max_categories < 2
            or start.max_categories > 6
        ):
            raise SocialCategoryGenerationError(
                "Geçersiz veya eksik max_categories değeri.",
                error_code="CATEGORY_GENERATION_NOT_ALLOWED",
            )

        if start.attempt_status != "pending":
            raise SocialCategoryGenerationError(
                "Attempt pending durumunda olmadığından üretim yapılamaz.",
                error_code="CATEGORY_GENERATION_NOT_ALLOWED",
            )

        if not start.keywords or len(start.keywords) == 0:
            raise SocialCategoryGenerationError(
                "Keyword snapshot listesi boş olamaz.",
                error_code="CATEGORY_GENERATION_NOT_ALLOWED",
            )

        allowed_keyword_ids: set[int] = set()
        for kw in start.keywords:
            if (
                isinstance(kw.keyword_id, bool)
                or not isinstance(kw.keyword_id, int)
                or kw.keyword_id <= 0
            ):
                raise SocialCategoryGenerationError(
                    "Keyword snapshot verisi geçersiz veya bozuk.",
                    error_code="CATEGORY_GENERATION_NOT_ALLOWED",
                )
            if not kw.keyword_snapshot or not kw.keyword_snapshot.strip():
                raise SocialCategoryGenerationError(
                    "Keyword snapshot verisi geçersiz veya bozuk.",
                    error_code="CATEGORY_GENERATION_NOT_ALLOWED",
                )
            allowed_keyword_ids.add(kw.keyword_id)

        # 2. Prompt oluşturma
        try:
            prompt = build_social_category_prompt(start)
        except SocialCategoryPromptError as exc:
            raise SocialCategoryGenerationError(
                exc.message,
                error_code=exc.error_code,
            ) from exc

        retry_prompt = prompt + "\n\n" + build_category_retry_correction(start.max_categories)

        ai_calls_used = 0
        last_validation_error: SocialCategoryOutputValidationError | None = None

        # 3. Mantıksal istek ve en fazla 1 retry döngüsü (toplam en fazla 2 çağrı)
        with logical_request(self.ai):
            for attempt_idx in range(2):
                current_prompt = prompt if attempt_idx == 0 else retry_prompt
                ai_calls_used += 1

                try:
                    raw_response = self.ai.complete_json(
                        current_prompt,
                        max_tokens=4000,
                        # Gemini 3 ailesinde sampling ayarini modele birak.
                        # AI service, None degerinde temperature alanini API'ye
                        # hic gondermez.
                        temperature=None,
                        response_schema=to_gemini_response_schema(
                            get_social_category_response_schema()
                        ),
                    )
                except Exception as exc:
                    # Provider exception: ek retry veya fallback yapılmaz
                    raise SocialCategoryGenerationError(
                        "Yapay zeka sağlayıcı hatası oluştu.",
                        error_code="CATEGORY_PROVIDER_ERROR",
                    ) from exc

                try:
                    categories = validate_social_category_output(
                        raw_response,
                        allowed_keyword_ids=allowed_keyword_ids,
                        max_categories=start.max_categories,
                    )
                    return SocialCategoryAIResult(
                        attempt_id=start.attempt_id,
                        categories=categories,
                        ai_calls_used=ai_calls_used,
                    )
                except SocialCategoryOutputValidationError as val_exc:
                    last_validation_error = val_exc
                    # Güvenli telemetry failure işareti (raw çıktı/marka sızmaz)
                    reason_parts = [val_exc.error_code]
                    if val_exc.field:
                        reason_parts.append(f"field={val_exc.field}")
                    if val_exc.category_index is not None:
                        reason_parts.append(f"index={val_exc.category_index}")
                    mark_attempt_failed(self.ai, ":".join(reason_parts))
                    continue

        # 2 deneme de başarısız olduğunda fail-closed ret
        validation_code = last_validation_error.error_code if last_validation_error else None
        raise SocialCategoryGenerationError(
            "Yapay zeka kategori çıktısı doğrulanamadı.",
            error_code="CATEGORY_OUTPUT_INVALID",
            validation_error_code=validation_code,
        )
