# -*- coding: utf-8 -*-
"""Sosyal İçerik Saf Grounding, Süre ve Birleşik Repair Karar Motoru (F1-G.3).

Bu modül doğrulanmış sosyal içerik nesneleri (ValidatedSocialContent) üzerinde
saf (pure), deterministik ve fail-closed kalite değerlendirmesi yaparak
kabul (accept), birleşik düzeltme (repair) veya ret (reject) kararını üretir.

Kurallar (plan_social_brief_akisi.md rev.4 §6, §9):
- DB/ORM ve Pydantic KESİNLİKLE kullanılmaz.
- Yapay zeka veya harici ağ çağrısı YAPILMAZ.
- Logger'a kullanıcı içeriği, iddia (claim) veya hassas veri YAZILMAZ.
- DTO'lar dondurulmuş (frozen dataclass) ve immutable'dır.
- app/generators/ads/validators.py içindeki find_ungrounded_claims yeniden kullanılır.
- Caption ve hook metinleri taranır; format_payload bu fazda taranmaz.
- Grounding whitelist yalnızca product_facts ve kullanıcının sağladığı trusted_brand_usp'dir.
- brand_context whitelist'e DAHİL EDİLMEZ.
- primary_keyword yalnızca dar ve tam kelime muafiyeti (exempt_keywords) için kullanılır.
- İlk üretimde ungrounded claim veya duration mismatch varsa TEK birleşik "repair" kararı üretilir.
- Repair sonrasında claim devam ediyorsa "reject" edilir.
- Repair sonrasında grounding temiz ancak süre hâlâ tutmuyorsa "accept" edilir ve "duration_mismatch" warning'e eklenir.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.social.content_contract import (
    ValidatedCarouselPayload,
    ValidatedSocialContent,
    ValidatedThreadPayload,
    ValidatedVideoPayload,
)
from app.generators.ads.validators import find_ungrounded_claims


# ==================== HATA SINIFI ====================

class SocialContentQualityError(ValueError):
    """Sosyal içerik kalite değerlendirme hatası."""

    def __init__(
        self,
        message: str,
        *,
        error_code: str = "CONTENT_QUALITY_INVALID_INPUT",
        field: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.field = field

    def __str__(self) -> str:
        parts = [f"[{self.error_code}]", self.message]
        if self.field is not None:
            parts.append(f"(field={self.field})")
        return " ".join(parts)

    def __repr__(self) -> str:
        return (
            f"SocialContentQualityError(error_code={self.error_code!r}, "
            f"message={self.message!r}, field={self.field!r})"
        )


# ==================== DTO'LAR ====================

@dataclass(frozen=True)
class SocialContentGroundingContext:
    """Sosyal içerik grounding ve muafiyet bağlamı (immutable).

    Whitelist yalnızca confirmed product_facts ve kullanıcının açıkça sağladığı
    trusted_brand_usp'den kurulur. Generic USP veya brand_context kabul edilmez.
    """

    primary_keyword: str
    product_facts: str | None = None
    trusted_brand_usp: str | None = None

    def __post_init__(self) -> None:
        # primary_keyword validation
        pk = self.primary_keyword
        if type(pk) is not str:
            raise SocialContentQualityError(
                "primary_keyword string olmalıdır.",
                error_code="CONTENT_QUALITY_INVALID_INPUT",
                field="primary_keyword",
            )
        if len(pk) == 0 or pk != pk.strip():
            raise SocialContentQualityError(
                "primary_keyword boş olamaz ve başında/sonunda boşluk içeremez.",
                error_code="CONTENT_QUALITY_INVALID_INPUT",
                field="primary_keyword",
            )
        if len(pk) > 200:
            raise SocialContentQualityError(
                "primary_keyword en fazla 200 karakter olabilir.",
                error_code="CONTENT_QUALITY_INVALID_INPUT",
                field="primary_keyword",
            )

        # product_facts validation
        pf = self.product_facts
        if pf is not None:
            if type(pf) is not str:
                raise SocialContentQualityError(
                    "product_facts string veya None olmalıdır.",
                    error_code="CONTENT_QUALITY_INVALID_INPUT",
                    field="product_facts",
                )
            if len(pf) == 0 or pf != pf.strip():
                raise SocialContentQualityError(
                    "product_facts boş olamaz ve başında/sonunda boşluk içeremez.",
                    error_code="CONTENT_QUALITY_INVALID_INPUT",
                    field="product_facts",
                )
            if len(pf) > 5000:
                raise SocialContentQualityError(
                    "product_facts en fazla 5000 karakter olabilir.",
                    error_code="CONTENT_QUALITY_INVALID_INPUT",
                    field="product_facts",
                )

        # trusted_brand_usp validation
        usp = self.trusted_brand_usp
        if usp is not None:
            if type(usp) is not str:
                raise SocialContentQualityError(
                    "trusted_brand_usp string veya None olmalıdır.",
                    error_code="CONTENT_QUALITY_INVALID_INPUT",
                    field="trusted_brand_usp",
                )
            if len(usp) == 0 or usp != usp.strip():
                raise SocialContentQualityError(
                    "trusted_brand_usp boş olamaz ve başında/sonunda boşluk içeremez.",
                    error_code="CONTENT_QUALITY_INVALID_INPUT",
                    field="trusted_brand_usp",
                )
            if len(usp) > 5000:
                raise SocialContentQualityError(
                    "trusted_brand_usp en fazla 5000 karakter olabilir.",
                    error_code="CONTENT_QUALITY_INVALID_INPUT",
                    field="trusted_brand_usp",
                )

    @property
    def grounding_facts(self) -> str:
        """Deterministik birleştirilmiş grounding gerçekleri."""
        if self.product_facts and self.trusted_brand_usp:
            return f"{self.product_facts}\n{self.trusted_brand_usp}"
        if self.product_facts:
            return self.product_facts
        if self.trusted_brand_usp:
            return self.trusted_brand_usp
        return ""


@dataclass(frozen=True)
class SocialContentQualityDecision:
    """Saf kalite değerlendirmesi ve birleşik repair karar DTO'su (immutable)."""

    action: str
    reason_codes: tuple[str, ...]
    claims: tuple[str, ...]
    warnings: tuple[str, ...]
    grounding_clean: bool
    duration_acceptable: bool
    repair_attempted: bool

    def __post_init__(self) -> None:
        if self.action not in ("accept", "repair", "reject"):
            raise SocialContentQualityError(
                "Geçersiz action değeri.",
                error_code="CONTENT_QUALITY_INVALID_INPUT",
                field="action",
            )
        if not isinstance(self.reason_codes, tuple):
            raise SocialContentQualityError(
                "reason_codes tuple olmalıdır.",
                error_code="CONTENT_QUALITY_INVALID_INPUT",
                field="reason_codes",
            )
        for rc in self.reason_codes:
            if rc not in ("ungrounded_claim", "duration_mismatch"):
                raise SocialContentQualityError(
                    "Geçersiz reason_code değeri.",
                    error_code="CONTENT_QUALITY_INVALID_INPUT",
                    field="reason_codes",
                )
        if not isinstance(self.claims, tuple):
            raise SocialContentQualityError(
                "claims tuple olmalıdır.",
                error_code="CONTENT_QUALITY_INVALID_INPUT",
                field="claims",
            )
        if not isinstance(self.warnings, tuple):
            raise SocialContentQualityError(
                "warnings tuple olmalıdır.",
                error_code="CONTENT_QUALITY_INVALID_INPUT",
                field="warnings",
            )
        if type(self.grounding_clean) is not bool:
            raise SocialContentQualityError(
                "grounding_clean bool olmalıdır.",
                error_code="CONTENT_QUALITY_INVALID_INPUT",
                field="grounding_clean",
            )
        if type(self.duration_acceptable) is not bool:
            raise SocialContentQualityError(
                "duration_acceptable bool olmalıdır.",
                error_code="CONTENT_QUALITY_INVALID_INPUT",
                field="duration_acceptable",
            )
        if type(self.repair_attempted) is not bool:
            raise SocialContentQualityError(
                "repair_attempted bool olmalıdır.",
                error_code="CONTENT_QUALITY_INVALID_INPUT",
                field="repair_attempted",
            )


# ==================== KONTROL VE KARAR FONKSİYONLARI ====================

def _validate_content_consistency(content: ValidatedSocialContent) -> None:
    """ValidatedSocialContent nesnesinin karar motoru için temel tutarlılığını denetler."""
    ds = content.duration_status
    if type(ds) is not str or ds not in ("valid", "mismatch", "not_applicable"):
        raise SocialContentQualityError(
            "Geçersiz duration_status değeri.",
            error_code="CONTENT_QUALITY_INCONSISTENT_CONTENT",
            field="duration_status",
        )

    fp = content.format_payload
    if fp is not None and not isinstance(
        fp, (ValidatedVideoPayload, ValidatedCarouselPayload, ValidatedThreadPayload)
    ):
        raise SocialContentQualityError(
            "Geçersiz format_payload tipi.",
            error_code="CONTENT_QUALITY_INCONSISTENT_CONTENT",
            field="format_payload",
        )

    is_video = isinstance(fp, ValidatedVideoPayload)
    dur = content.actual_duration_sec

    if is_video:
        if ds == "not_applicable":
            raise SocialContentQualityError(
                "Video formatı not_applicable duration_status içeremez.",
                error_code="CONTENT_QUALITY_INCONSISTENT_CONTENT",
                field="duration_status",
            )
        if isinstance(dur, bool) or type(dur) is not int or dur <= 0:
            raise SocialContentQualityError(
                "Video formatı pozitif tamsayı actual_duration_sec içermelidir.",
                error_code="CONTENT_QUALITY_INCONSISTENT_CONTENT",
                field="actual_duration_sec",
            )
    else:
        if ds != "not_applicable":
            raise SocialContentQualityError(
                "Non-video format not_applicable dışında duration_status içeremez.",
                error_code="CONTENT_QUALITY_INCONSISTENT_CONTENT",
                field="duration_status",
            )
        if dur is not None:
            raise SocialContentQualityError(
                "Non-video format actual_duration_sec içeremez.",
                error_code="CONTENT_QUALITY_INCONSISTENT_CONTENT",
                field="actual_duration_sec",
            )

    if not isinstance(content.hooks, tuple):
        raise SocialContentQualityError(
            "hooks tuple olmalıdır.",
            error_code="CONTENT_QUALITY_INCONSISTENT_CONTENT",
            field="hooks",
        )
    if not isinstance(content.caption, str):
        raise SocialContentQualityError(
            "caption string olmalıdır.",
            error_code="CONTENT_QUALITY_INCONSISTENT_CONTENT",
            field="caption",
        )


def find_social_content_ungrounded_claims(
    content: ValidatedSocialContent,
    context: SocialContentGroundingContext,
) -> tuple[str, ...]:
    """Caption ve hook metinlerindeki desteksiz iddiaları bulur.

    Kurallar:
    - Yalnızca content.caption ve hook.text alanları taranır.
    - format_payload içindeki alanlar (scene, on_screen_text, voiceover, slides, posts) taranmaz.
    - grounding_facts: confirmed product_facts + trusted_brand_usp (veya boş string).
    - exempt_keywords: [context.primary_keyword].
    - Çıkan iddialar ilk görülme sırasını koruyarak tekilleştirilir.
    - Hata durumunda güvenli fail-closed SocialContentQualityError fırlatılır.
    """
    if type(content) is not ValidatedSocialContent:
        raise SocialContentQualityError(
            "content exact ValidatedSocialContent olmalıdır.",
            error_code="CONTENT_QUALITY_INVALID_INPUT",
            field="content",
        )
    if type(context) is not SocialContentGroundingContext:
        raise SocialContentQualityError(
            "context exact SocialContentGroundingContext olmalıdır.",
            error_code="CONTENT_QUALITY_INVALID_INPUT",
            field="context",
        )

    grounding_facts = context.grounding_facts
    exempt_keywords = [context.primary_keyword]

    try:
        raw_claims: list[str] = []
        for hook in content.hooks:
            if hasattr(hook, "text") and isinstance(hook.text, str):
                hook_claims = find_ungrounded_claims(
                    hook.text,
                    grounding_facts=grounding_facts,
                    exempt_keywords=exempt_keywords,
                )
                raw_claims.extend(hook_claims)

        if isinstance(content.caption, str):
            caption_claims = find_ungrounded_claims(
                content.caption,
                grounding_facts=grounding_facts,
                exempt_keywords=exempt_keywords,
            )
            raw_claims.extend(caption_claims)
    except Exception as exc:
        raise SocialContentQualityError(
            "İddia taraması sırasında beklenmeyen bir hata oluştu.",
            error_code="CONTENT_QUALITY_GROUNDING_ERROR",
            field="claims",
        ) from exc

    # Tekilleştir ve sırayı koru
    seen: set[str] = set()
    unique_claims: list[str] = []
    for c in raw_claims:
        if c not in seen:
            seen.add(c)
            unique_claims.append(c)

    return tuple(unique_claims)


def evaluate_social_content_quality(
    content: ValidatedSocialContent,
    context: SocialContentGroundingContext,
    *,
    repair_attempted: bool,
) -> SocialContentQualityDecision:
    """Doğrulanmış sosyal içerik için saf grounding, süre ve birleşik repair kararını üretir.

    Karar Matrisi:
    - İlk üretim (repair_attempted=False):
      - Clean + valid/not_applicable -> action="accept", reason_codes=()
      - Ungrounded claim var -> action="repair", reason_codes=("ungrounded_claim",)
      - Duration mismatch var -> action="repair", reason_codes=("duration_mismatch",)
      - Hem claim hem mismatch -> action="repair", reason_codes=("ungrounded_claim", "duration_mismatch")
    - Repair sonrası (repair_attempted=True):
      - Clean + valid/not_applicable -> action="accept", reason_codes=()
      - Clean + duration mismatch -> action="accept", reason_codes=("duration_mismatch",), warnings'e "duration_mismatch" eklenir
      - Ungrounded claim devam ediyor -> action="reject", reason_codes=("ungrounded_claim",) veya ("ungrounded_claim", "duration_mismatch")
    """
    if type(content) is not ValidatedSocialContent:
        raise SocialContentQualityError(
            "content exact ValidatedSocialContent olmalıdır.",
            error_code="CONTENT_QUALITY_INVALID_INPUT",
            field="content",
        )
    if type(context) is not SocialContentGroundingContext:
        raise SocialContentQualityError(
            "context exact SocialContentGroundingContext olmalıdır.",
            error_code="CONTENT_QUALITY_INVALID_INPUT",
            field="context",
        )
    if type(repair_attempted) is not bool:
        raise SocialContentQualityError(
            "repair_attempted exact bool olmalıdır.",
            error_code="CONTENT_QUALITY_INVALID_INPUT",
            field="repair_attempted",
        )

    _validate_content_consistency(content)

    claims = find_social_content_ungrounded_claims(content, context)
    grounding_clean = (len(claims) == 0)
    is_duration_mismatch = (content.duration_status == "mismatch")

    # Mevcut soft warning'leri koru (örn. voiceover_duration_mismatch)
    warnings_list = list(content.validation_warnings or ())

    if not repair_attempted:
        # İLK ÜRETİM (repair_attempted=False)
        if grounding_clean and not is_duration_mismatch:
            action = "accept"
            reason_codes: tuple[str, ...] = ()
            duration_acceptable = True
        elif not grounding_clean and not is_duration_mismatch:
            action = "repair"
            reason_codes = ("ungrounded_claim",)
            duration_acceptable = True
        elif grounding_clean and is_duration_mismatch:
            action = "repair"
            reason_codes = ("duration_mismatch",)
            duration_acceptable = False
        else:
            action = "repair"
            reason_codes = ("ungrounded_claim", "duration_mismatch")
            duration_acceptable = False
    else:
        # REPAIR SONRASI (repair_attempted=True)
        if not grounding_clean:
            # Claim devam ediyor -> kesinlikle reject
            action = "reject"
            if is_duration_mismatch:
                reason_codes = ("ungrounded_claim", "duration_mismatch")
                duration_acceptable = False
                if "duration_mismatch" not in warnings_list:
                    warnings_list.append("duration_mismatch")
            else:
                reason_codes = ("ungrounded_claim",)
                duration_acceptable = True
        else:
            # Grounding temiz
            if not is_duration_mismatch:
                action = "accept"
                reason_codes = ()
                duration_acceptable = True
            else:
                # Süre hâlâ mismatch -> accept + warning
                action = "accept"
                reason_codes = ("duration_mismatch",)
                duration_acceptable = False
                if "duration_mismatch" not in warnings_list:
                    warnings_list.append("duration_mismatch")

    return SocialContentQualityDecision(
        action=action,
        reason_codes=reason_codes,
        claims=claims,
        warnings=tuple(warnings_list),
        grounding_clean=grounding_clean,
        duration_acceptable=duration_acceptable,
        repair_attempted=repair_attempted,
    )
