# -*- coding: utf-8 -*-
"""Sosyal Brief Fikir Üretim Adaptörü (F1-F.3).

Bu modül SocialBriefIdeaGenerator sınıfını sunar:
- AIService üzerinden scoped("social_brief_ideas") kullanır.
- Kategori bazında tek AI çağrısı yapar.
- Girdi doğrulama hatasında 0 AI çağrısı yapar.
- Çıktı fikir-bazlı süzülür (plan §3.4): brief dışı / geçersiz fikir tek tek
  ATILIR ve sayılır, geçerliler korunur; hedef kotasını aşan fazlalık (ilk N
  tutulur) atılır. Yalnız yazım normalizasyonu yapılır ("X" -> twitter).
- Yapısal bozukluk (JSON değil, 'ideas' listesi yok) veya hiç geçerli fikir
  kalmaması durumunda tam 1 kez retry yapar.
- İki yanıt da kullanılamazsa IDEA_OUTPUT_INVALID fırlatır (kategori düzeyi hata).
- Provider exception durumunda IDEA_PROVIDER_ERROR fırlatır ve retry yapmaz.
- Kesinlikle fallback fikir üretmez; platform/format çevirisi yapmaz.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.social.idea_contract import (
    SocialIdeaFilterResult,
    SocialIdeaOutputValidationError,
    ValidatedSocialIdea,
    build_social_idea_response_schema,
    filter_social_idea_output,
)
from app.core.social.provider_schema import to_gemini_response_schema
from app.generators.ai_service import logical_request, mark_attempt_failed, scoped
from app.generators.social.brief_idea_prompt import (
    SocialIdeaPromptError,
    SocialIdeaPromptInput,
    build_idea_retry_correction,
    build_social_idea_prompt,
)


class SocialIdeaGenerationError(ValueError):
    """Sosyal fikir üretim domain hatası."""

    def __init__(
        self,
        message: str,
        *,
        error_code: str,
        validation_error_code: str | None = None,
        ai_calls_used: int = 0,
        off_brief_dropped: int = 0,
        invalid_dropped: int = 0,
        over_quota_dropped: int = 0,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.validation_error_code = validation_error_code
        # Telemetri: başarısız kategori çağrısında harcanan çağrı ve atılan fikirler
        # (çağrı tavanı ve deneme metrikleri için; ham çıktı taşınmaz)
        self.ai_calls_used = ai_calls_used
        self.off_brief_dropped = off_brief_dropped
        self.invalid_dropped = invalid_dropped
        self.over_quota_dropped = over_quota_dropped

    def __str__(self) -> str:
        parts = [f"[{self.error_code}]", self.message]
        if self.validation_error_code is not None:
            parts.append(f"(validation_error_code={self.validation_error_code})")
        return " ".join(parts)


@dataclass(frozen=True)
class SocialIdeaAIResult:
    """Fikir AI üretimi nihai sonucu (immutable)."""

    attempt_id: int
    category_id: int
    ideas: tuple[ValidatedSocialIdea, ...]
    ai_calls_used: int
    # Fikir-bazlı süzme sayaçları (tüm çağrılar toplamı) ve JSON düzeltme tekrarı
    off_brief_dropped: int = 0
    invalid_dropped: int = 0
    over_quota_dropped: int = 0
    retry_used: int = 0


class SocialBriefIdeaGenerator:
    """Sosyal brief fikir üretim adaptörü."""

    def __init__(self, ai_service: Any) -> None:
        self.ai = scoped(ai_service, "social_brief_ideas")

    def generate(
        self,
        prompt_input: SocialIdeaPromptInput,
    ) -> SocialIdeaAIResult:
        """Kategori için fikir üretimini gerçekleştirir ve doğrulanmış sonuç döner.

        Args:
            prompt_input: Dondurulmuş prompt girdi nesnesi.

        Returns:
            SocialIdeaAIResult: Doğrulanmış fikirler ve harcanan AI çağrı sayısı.

        Raises:
            SocialIdeaGenerationError: Girdi hatası, sağlayıcı hatası veya geçersiz çıktı.
        """
        # 1. Girdi doğrulaması (0 AI çağrısı)
        try:
            prompt = build_social_idea_prompt(prompt_input)
        except SocialIdeaPromptError as exc:
            raise SocialIdeaGenerationError(
                exc.message,
                error_code=exc.error_code,
            ) from exc

        sorted_keywords = sorted(prompt_input.keywords, key=lambda k: k.position)
        allowed_keyword_ids = tuple(kw.keyword_id for kw in sorted_keywords)

        response_schema = to_gemini_response_schema(
            build_social_idea_response_schema(
                target_specs=prompt_input.target_specs,
                allowed_keyword_ids=allowed_keyword_ids,
            )
        )

        retry_prompt = prompt + "\n\n" + build_idea_retry_correction()

        ai_calls_used = 0
        last_validation_error_code: str | None = None
        off_brief_total = 0
        invalid_total = 0
        over_quota_total = 0

        # 2. Mantıksal istek ve en fazla 1 retry döngüsü (toplam en fazla 2 çağrı)
        with logical_request(self.ai):
            for attempt_idx in range(2):
                current_prompt = prompt if attempt_idx == 0 else retry_prompt
                ai_calls_used += 1

                try:
                    # max_tokens=4000: Kategori başına 1-6 fikir ve 2000 char description
                    # için geniş ve güvenli sabit sınır
                    raw_response = self.ai.complete_json(
                        current_prompt,
                        max_tokens=4000,
                        # Gemini 3 ailesinde sampling ayarini modele birak.
                        # AI service, None degerinde temperature alanini API'ye
                        # hic gondermez.
                        temperature=None,
                        response_schema=response_schema,
                    )
                except Exception as exc:
                    # Provider exception: ek retry veya fallback yapılmaz
                    raise SocialIdeaGenerationError(
                        "Yapay zeka sağlayıcı hatası oluştu.",
                        error_code="IDEA_PROVIDER_ERROR",
                        ai_calls_used=ai_calls_used,
                        off_brief_dropped=off_brief_total,
                        invalid_dropped=invalid_total,
                        over_quota_dropped=over_quota_total,
                    ) from exc

                try:
                    filtered: SocialIdeaFilterResult = filter_social_idea_output(
                        raw_response,
                        target_specs=prompt_input.target_specs,
                        allowed_keyword_ids=allowed_keyword_ids,
                    )
                except SocialIdeaOutputValidationError as val_exc:
                    # Yapısal (kategori düzeyi) bozukluk: 1 düzeltme tekrarı hakkı
                    last_validation_error_code = val_exc.error_code
                    reason_parts = [val_exc.error_code]
                    if val_exc.field:
                        reason_parts.append(f"field={val_exc.field}")
                    # Güvenli telemetry failure işareti (raw çıktı/marka/başlık sızmaz)
                    mark_attempt_failed(self.ai, ":".join(reason_parts))
                    continue

                off_brief_total += filtered.off_brief_dropped
                invalid_total += filtered.invalid_dropped
                over_quota_total += filtered.over_quota_dropped

                if not filtered.ideas:
                    # JSON okundu ama brief içinde tek geçerli fikir yok: 1 tekrar hakkı
                    last_validation_error_code = "IDEA_OUTPUT_NO_VALID_IDEAS"
                    mark_attempt_failed(
                        self.ai,
                        f"IDEA_OUTPUT_NO_VALID_IDEAS:dropped={filtered.total_dropped}",
                    )
                    continue

                return SocialIdeaAIResult(
                    attempt_id=prompt_input.attempt_id,
                    category_id=prompt_input.category_id,
                    ideas=filtered.ideas,
                    ai_calls_used=ai_calls_used,
                    off_brief_dropped=off_brief_total,
                    invalid_dropped=invalid_total,
                    over_quota_dropped=over_quota_total,
                    retry_used=1 if ai_calls_used > 1 else 0,
                )

        # 2 deneme de kullanılamadığında fail-closed ret (kategori düzeyi hata)
        raise SocialIdeaGenerationError(
            "Yapay zeka fikir çıktısı doğrulanamadı.",
            error_code="IDEA_OUTPUT_INVALID",
            validation_error_code=last_validation_error_code,
            ai_calls_used=ai_calls_used,
            off_brief_dropped=off_brief_total,
            invalid_dropped=invalid_total,
            over_quota_dropped=over_quota_total,
        )
