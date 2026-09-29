# -*- coding: utf-8 -*-
"""Saf Social Brief Request Doğrulayıcısı (Domain Validator).

Bu modül SocialBrief oluşturma isteklerinin canonical matris ve domain kurallarına
uygunluğunu doğrular. Saf (pure) fonksiyondur; DB, ORM, oturum veya HTTP bağımlılığı içermez.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from app.generators.social.format_matrix import (
    FORMAT_MATRIX_VERSION,
    MAX_KEYWORDS,
    MAX_TARGETS,
    MIN_KEYWORDS,
    MIN_TARGETS,
    get_duration_preset,
    get_platform_format,
)
from app.schemas.social_brief import (
    SocialBriefCreateRequest,
    SocialBriefTargetCreateRequest,
)


class SocialBriefValidationError(ValueError):
    """Sosyal brief domain doğrulama hatası (HTTP bağımsız)."""

    def __init__(self, error_code: str, message: str, field: Optional[str] = None):
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.field = field

    def __repr__(self) -> str:
        return f"SocialBriefValidationError(error_code={self.error_code!r}, field={self.field!r}, message={self.message!r})"


@dataclass(frozen=True)
class ValidatedSocialBriefTarget:
    """Doğrulanmış ve çözümlenmiş tekil brief hedefi (immutable)."""
    platform: str
    content_format: str
    duration_preset_id: Optional[str]
    duration_min_sec: Optional[int]
    duration_max_sec: Optional[int]


@dataclass(frozen=True)
class ValidatedSocialBriefInput:
    """Doğrulanmış sosyal brief girdisi (immutable)."""
    scoring_run_id: int
    keyword_ids: Tuple[int, ...]
    targets: Tuple[ValidatedSocialBriefTarget, ...]
    brand_name: Optional[str]
    brand_context: Optional[str]
    format_matrix_version: str


def validate_social_brief_request(
    request: SocialBriefCreateRequest,
) -> ValidatedSocialBriefInput:
    """SocialBrief oluşturma isteğini canonical matris ve domain kurallarına göre doğrular.

    Kurallar:
    - scoring_run_id pozitif tam sayı olmalıdır (bool reddedilir).
    - keyword_ids 1-5 adet, pozitif tam sayı ve benzersiz olmalıdır (bool reddedilir, sıra korunur).
    - targets 1-6 adet ve benzersiz (platform, content_format) çifti olmalıdır (sıra korunur).
    - Platform ve format canonical matriste tam eşleşmelidir (coercion/trim/lower yapılmaz).
    - Video formatlarında geçerli ve ilgili profile ait duration_preset_id zorunludur.
    - Video olmayan formatlarda duration_preset_id mutlaka None olmalıdır.

    Returns:
        ValidatedSocialBriefInput: Doğrulanmış, çözümlenmiş ve değişmez sonuç nesnesi.

    Raises:
        SocialBriefValidationError: Herhangi bir domain kuralı ihlal edildiğinde.
    """
    # 1. scoring_run_id savunma derinliği kontrolü
    if (
        isinstance(request.scoring_run_id, bool)
        or not isinstance(request.scoring_run_id, int)
        or request.scoring_run_id <= 0
    ):
        raise SocialBriefValidationError(
            error_code="INVALID_TARGET_VALUE",
            message="scoring_run_id pozitif bir tam sayı olmalıdır.",
            field="scoring_run_id",
        )

    # 2. keyword_ids kontrolleri (adet, tip, benzersizlik, sıra koruma)
    keyword_count = len(request.keyword_ids)
    if keyword_count < MIN_KEYWORDS or keyword_count > MAX_KEYWORDS:
        raise SocialBriefValidationError(
            error_code="INVALID_KEYWORD_COUNT",
            message=f"Keyword sayısı {MIN_KEYWORDS} ile {MAX_KEYWORDS} arasında olmalıdır (verilen: {keyword_count}).",
            field="keyword_ids",
        )

    seen_keywords: set[int] = set()
    for kid in request.keyword_ids:
        if isinstance(kid, bool) or not isinstance(kid, int) or kid <= 0:
            raise SocialBriefValidationError(
                error_code="INVALID_KEYWORD_ID",
                message=f"Geçersiz keyword ID: {kid}. Pozitif bir tam sayı olmalıdır.",
                field="keyword_ids",
            )
        if kid in seen_keywords:
            raise SocialBriefValidationError(
                error_code="DUPLICATE_KEYWORD",
                message=f"Tekrarlanan keyword ID: {kid}",
                field="keyword_ids",
            )
        seen_keywords.add(kid)

    # 3. targets kontrolleri (adet, format, süre, benzersizlik, sıra koruma)
    target_count = len(request.targets)
    if target_count < MIN_TARGETS or target_count > MAX_TARGETS:
        raise SocialBriefValidationError(
            error_code="INVALID_TARGET_COUNT",
            message=f"Hedef (target) sayısı {MIN_TARGETS} ile {MAX_TARGETS} arasında olmalıdır (verilen: {target_count}).",
            field="targets",
        )

    seen_target_keys: set[Tuple[str, str]] = set()
    validated_targets: list[ValidatedSocialBriefTarget] = []

    for idx, t in enumerate(request.targets):
        if (
            not isinstance(t, SocialBriefTargetCreateRequest)
            or not isinstance(t.platform, str)
            or not isinstance(t.content_format, str)
        ):
            raise SocialBriefValidationError(
                error_code="INVALID_TARGET_VALUE",
                message="Hedef platform ve format geçerli bir metin olmalıdır.",
                field=f"targets[{idx}]",
            )

        target_key = (t.platform, t.content_format)
        if target_key in seen_target_keys:
            raise SocialBriefValidationError(
                error_code="DUPLICATE_TARGET",
                message=f"Tekrarlanan hedef kombinasyonu: {t.platform}/{t.content_format}",
                field=f"targets[{idx}]",
            )
        seen_target_keys.add(target_key)

        fmt = get_platform_format(t.platform, t.content_format)
        if fmt is None:
            raise SocialBriefValidationError(
                error_code="UNSUPPORTED_PLATFORM_FORMAT",
                message=f"Desteklenmeyen platform ve format kombinasyonu: {t.platform}/{t.content_format}",
                field=f"targets[{idx}]",
            )

        if fmt.requires_duration:
            if t.duration_preset_id is None:
                raise SocialBriefValidationError(
                    error_code="DURATION_PRESET_REQUIRED",
                    message=f"Video formatı ({t.platform}/{t.content_format}) için duration_preset_id zorunludur.",
                    field=f"targets[{idx}].duration_preset_id",
                )

            preset = get_duration_preset(t.duration_preset_id)
            if preset is None:
                raise SocialBriefValidationError(
                    error_code="INVALID_DURATION_PRESET",
                    message=f"Geçersiz süre ön ayarı: '{t.duration_preset_id}'",
                    field=f"targets[{idx}].duration_preset_id",
                )

            allowed_preset_ids = {p.id for p in fmt.duration_presets}
            if preset.id not in allowed_preset_ids:
                raise SocialBriefValidationError(
                    error_code="DURATION_PROFILE_MISMATCH",
                    message=(
                        f"Süre ön ayarı '{t.duration_preset_id}' formatın ({t.platform}/{t.content_format}) "
                        f"süre profili ({fmt.duration_profile}) ile uyumsuzdur."
                    ),
                    field=f"targets[{idx}].duration_preset_id",
                )

            validated_targets.append(
                ValidatedSocialBriefTarget(
                    platform=t.platform,
                    content_format=t.content_format,
                    duration_preset_id=preset.id,
                    duration_min_sec=preset.min_sec,
                    duration_max_sec=preset.max_sec,
                )
            )
        else:
            if t.duration_preset_id is not None:
                raise SocialBriefValidationError(
                    error_code="DURATION_NOT_ALLOWED",
                    message=f"Video olmayan format ({t.platform}/{t.content_format}) için süre ön ayarı verilemez.",
                    field=f"targets[{idx}].duration_preset_id",
                )

            validated_targets.append(
                ValidatedSocialBriefTarget(
                    platform=t.platform,
                    content_format=t.content_format,
                    duration_preset_id=None,
                    duration_min_sec=None,
                    duration_max_sec=None,
                )
            )

    return ValidatedSocialBriefInput(
        scoring_run_id=request.scoring_run_id,
        keyword_ids=tuple(request.keyword_ids),
        targets=tuple(validated_targets),
        brand_name=request.brand_name,
        brand_context=request.brand_context,
        format_matrix_version=FORMAT_MATRIX_VERSION,
    )
