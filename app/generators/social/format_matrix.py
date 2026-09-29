# -*- coding: utf-8 -*-
"""Canonical Platform, Content Format ve Süre Matrisi (Single Source of Truth).

Bu modül sosyal brief akışında desteklenen platformlar, formatlar, süre profilleri
ve preset'lerinin tek ve değişmez (immutable) kanonik tanımıdır.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, Optional, Tuple

from app.schemas.social_brief import (
    DurationPresetResponse,
    SocialFormatMatrixLimitsResponse,
    SocialFormatMatrixResponse,
    SocialFormatOptionResponse,
    SocialPlatformOptionResponse,
)

FORMAT_MATRIX_VERSION = "v1"

# Akış limitleri
MIN_KEYWORDS = 1
MAX_KEYWORDS = 5
MIN_TARGETS = 1
MAX_TARGETS = 6


@dataclass(frozen=True)
class DurationPresetDef:
    """Tekil süre ön ayarı tanımı."""
    id: str
    label: str
    min_sec: int
    max_sec: int


@dataclass(frozen=True)
class FormatDef:
    """Platforma özel içerik formatı tanımı."""
    id: str
    label: str
    requires_duration: bool
    media_mode: Optional[str] = None
    duration_profile: Optional[str] = None
    duration_presets: Tuple[DurationPresetDef, ...] = ()


@dataclass(frozen=True)
class PlatformDef:
    """Sosyal platform tanımı."""
    id: str
    label: str
    formats: Tuple[FormatDef, ...]


# ==================== SÜRE PROFİLLERİ (CANONICAL PRESETS) ====================

SHORT_VIDEO_PRESETS: Tuple[DurationPresetDef, ...] = (
    DurationPresetDef(id="short_1_15", label="1–15 saniye", min_sec=1, max_sec=15),
    DurationPresetDef(id="short_16_30", label="16–30 saniye", min_sec=16, max_sec=30),
    DurationPresetDef(id="short_31_60", label="31–60 saniye", min_sec=31, max_sec=60),
    DurationPresetDef(id="short_61_90", label="61–90 saniye", min_sec=61, max_sec=90),
)

LONG_VIDEO_PRESETS: Tuple[DurationPresetDef, ...] = (
    DurationPresetDef(id="long_60_180", label="60–180 saniye", min_sec=60, max_sec=180),
    DurationPresetDef(id="long_181_300", label="181–300 saniye", min_sec=181, max_sec=300),
    DurationPresetDef(id="long_301_600", label="301–600 saniye", min_sec=301, max_sec=600),
)

X_VIDEO_PRESETS: Tuple[DurationPresetDef, ...] = (
    DurationPresetDef(id="x_1_15", label="1–15 saniye", min_sec=1, max_sec=15),
    DurationPresetDef(id="x_16_30", label="16–30 saniye", min_sec=16, max_sec=30),
    DurationPresetDef(id="x_31_60", label="31–60 saniye", min_sec=31, max_sec=60),
    DurationPresetDef(id="x_61_90", label="61–90 saniye", min_sec=61, max_sec=90),
    DurationPresetDef(id="x_91_140", label="91–140 saniye", min_sec=91, max_sec=140),
)

_raw_duration_profiles: dict[str, Tuple[DurationPresetDef, ...]] = {
    "short_video": SHORT_VIDEO_PRESETS,
    "long_video": LONG_VIDEO_PRESETS,
    "x_video": X_VIDEO_PRESETS,
}
DURATION_PROFILES: Mapping[str, Tuple[DurationPresetDef, ...]] = MappingProxyType(_raw_duration_profiles)
del _raw_duration_profiles


# ==================== CANONICAL PLATFORM/FORMAT DEFINITIONS ====================

CANONICAL_PLATFORMS: Tuple[PlatformDef, ...] = (
    PlatformDef(
        id="instagram",
        label="Instagram",
        formats=(
            FormatDef(
                id="post",
                label="Post",
                requires_duration=False,
                media_mode=None,
                duration_profile=None,
                duration_presets=(),
            ),
            FormatDef(
                id="carousel",
                label="Carousel",
                requires_duration=False,
                media_mode=None,
                duration_profile=None,
                duration_presets=(),
            ),
            FormatDef(
                id="reels",
                label="Reels",
                requires_duration=True,
                media_mode=None,
                duration_profile="short_video",
                duration_presets=SHORT_VIDEO_PRESETS,
            ),
            FormatDef(
                id="story",
                label="Story (statik)",
                requires_duration=False,
                media_mode="static",
                duration_profile=None,
                duration_presets=(),
            ),
        ),
    ),
    PlatformDef(
        id="tiktok",
        label="TikTok",
        formats=(
            FormatDef(
                id="short",
                label="Short",
                requires_duration=True,
                media_mode=None,
                duration_profile="short_video",
                duration_presets=SHORT_VIDEO_PRESETS,
            ),
        ),
    ),
    PlatformDef(
        id="twitter",
        label="X",
        formats=(
            FormatDef(
                id="post",
                label="Post",
                requires_duration=False,
                media_mode=None,
                duration_profile=None,
                duration_presets=(),
            ),
            FormatDef(
                id="thread",
                label="Thread",
                requires_duration=False,
                media_mode=None,
                duration_profile=None,
                duration_presets=(),
            ),
            FormatDef(
                id="video",
                label="Video",
                requires_duration=True,
                media_mode=None,
                duration_profile="x_video",
                duration_presets=X_VIDEO_PRESETS,
            ),
        ),
    ),
    PlatformDef(
        id="linkedin",
        label="LinkedIn",
        formats=(
            FormatDef(
                id="post",
                label="Post",
                requires_duration=False,
                media_mode=None,
                duration_profile=None,
                duration_presets=(),
            ),
            FormatDef(
                id="carousel",
                label="Carousel",
                requires_duration=False,
                media_mode=None,
                duration_profile=None,
                duration_presets=(),
            ),
            FormatDef(
                id="video",
                label="Video",
                requires_duration=True,
                media_mode=None,
                duration_profile="long_video",
                duration_presets=LONG_VIDEO_PRESETS,
            ),
        ),
    ),
    PlatformDef(
        id="youtube",
        label="YouTube",
        formats=(
            FormatDef(
                id="short",
                label="Short",
                requires_duration=True,
                media_mode=None,
                duration_profile="short_video",
                duration_presets=SHORT_VIDEO_PRESETS,
            ),
            FormatDef(
                id="video",
                label="Video",
                requires_duration=True,
                media_mode=None,
                duration_profile="long_video",
                duration_presets=LONG_VIDEO_PRESETS,
            ),
        ),
    ),
)


# ==================== INTEGRITY VALIDATION AT IMPORT TIME ====================

def _validate_canonical_matrix_integrity() -> None:
    """Canonical matrix ve preset tanımlarını modül yüklenirken doğrular.

    Sessiz override veya geçersiz süre aralığı tanımlanmasını fail-closed engeller.
    """
    seen_preset_ids: set[str] = set()

    for profile_name, presets in DURATION_PROFILES.items():
        intervals: list[tuple[int, int]] = []
        for p in presets:
            # 1. Preset ID küresel olarak tekil olmalı
            if p.id in seen_preset_ids:
                raise ValueError(f"Duplicate duration preset ID across profiles: {p.id}")
            seen_preset_ids.add(p.id)

            # 2. min >= 1 ve max >= min
            if p.min_sec < 1:
                raise ValueError(f"Preset {p.id} min_sec 1'den küçük olamaz: {p.min_sec}")
            if p.max_sec < p.min_sec:
                raise ValueError(f"Preset {p.id} max_sec ({p.max_sec}) min_sec'ten ({p.min_sec}) küçük olamaz")

            # X video 140s üst sınırı
            if profile_name == "x_video" and p.max_sec > 140:
                raise ValueError(f"x_video preset {p.id} max_sec 140'ı aşamaz: {p.max_sec}")

            # Long video 600s üst sınırı
            if profile_name == "long_video" and p.max_sec > 600:
                raise ValueError(f"long_video preset {p.id} max_sec 600'ü aşamaz: {p.max_sec}")

            intervals.append((p.min_sec, p.max_sec))

        # 3. Profil içi aralıklar çakışmamalı
        sorted_intervals = sorted(intervals, key=lambda x: x[0])
        for i in range(len(sorted_intervals) - 1):
            curr_start, curr_end = sorted_intervals[i]
            next_start, _ = sorted_intervals[i + 1]
            if curr_end >= next_start:
                raise ValueError(
                    f"Profile '{profile_name}' içinde çakışan aralıklar tespit edildi: "
                    f"[{curr_start}, {curr_end}] ve [{next_start}, ...]"
                )

    seen_platforms: set[str] = set()
    for plat in CANONICAL_PLATFORMS:
        if plat.id in seen_platforms:
            raise ValueError(f"Duplicate platform ID: {plat.id}")
        seen_platforms.add(plat.id)

        seen_formats: set[str] = set()
        for fmt in plat.formats:
            if fmt.id in seen_formats:
                raise ValueError(f"Duplicate format ID in platform '{plat.id}': {fmt.id}")
            seen_formats.add(fmt.id)

            # Video formatı doğrulaması
            if fmt.id in ("reels", "short", "video"):
                if not fmt.requires_duration:
                    raise ValueError(f"Video format '{plat.id}/{fmt.id}' requires_duration=True olmalıdır")
                if not fmt.duration_profile or not fmt.duration_presets:
                    raise ValueError(f"Video format '{plat.id}/{fmt.id}' duration_profile ve presets taşımalıdır")
            else:
                if fmt.requires_duration:
                    raise ValueError(f"Non-video format '{plat.id}/{fmt.id}' requires_duration=False olmalıdır")
                if fmt.duration_profile is not None or len(fmt.duration_presets) > 0:
                    raise ValueError(f"Non-video format '{plat.id}/{fmt.id}' duration profile/preset içeremez")

            # Story özel medya modu
            if fmt.id == "story":
                if fmt.media_mode != "static":
                    raise ValueError(f"Story format '{plat.id}/{fmt.id}' media_mode='static' olmalıdır")


_validate_canonical_matrix_integrity()


# ==================== INTERNAL LOOKUP MAPS ====================

_raw_lookup_map: dict[Tuple[str, str], FormatDef] = {}
for _plat in CANONICAL_PLATFORMS:
    for _fmt in _plat.formats:
        _raw_lookup_map[(_plat.id, _fmt.id)] = _fmt
_LOOKUP_MAP: Mapping[Tuple[str, str], FormatDef] = MappingProxyType(_raw_lookup_map)
del _raw_lookup_map

_raw_preset_map: dict[str, DurationPresetDef] = {}
for _presets in DURATION_PROFILES.values():
    for _p in _presets:
        _raw_preset_map[_p.id] = _p
_PRESET_MAP: Mapping[str, DurationPresetDef] = MappingProxyType(_raw_preset_map)
del _raw_preset_map


# ==================== PUBLIC LOOKUP HELPERS ====================

def get_platform_format(platform: str, content_format: str) -> Optional[FormatDef]:
    """Platform ve format çiftini kanonik matristen exact-match olarak sorgular.

    Bilinmeyen, büyük/küçük harf farklılığı veya boşluk içeren değerlerde
    coercion veya fallback yapmaz; doğrudan None döner (fail-closed).
    """
    if not isinstance(platform, str) or not isinstance(content_format, str):
        return None
    return _LOOKUP_MAP.get((platform, content_format))


def get_duration_preset(preset_id: str) -> Optional[DurationPresetDef]:
    """Preset ID'ye göre süre ön ayarını exact-match olarak sorgular.

    Bilinmeyen, büyük/küçük harf farklılığı veya boşluk içeren değerlerde
    coercion veya fallback yapmaz; doğrudan None döner (fail-closed).
    """
    if not isinstance(preset_id, str):
        return None
    return _PRESET_MAP.get(preset_id)


def get_format_matrix() -> SocialFormatMatrixResponse:
    """Kanonik platform-format matrisini yeni ve bağımsız bir response modeli olarak üretir.

    Döndürülen model üzerindeki olası mutasyonlar kanonik veri yapısını etkilemez.
    """
    platform_responses: list[SocialPlatformOptionResponse] = []
    for plat in CANONICAL_PLATFORMS:
        format_responses: list[SocialFormatOptionResponse] = []
        for fmt in plat.formats:
            preset_responses = [
                DurationPresetResponse(
                    id=p.id,
                    label=p.label,
                    min_sec=p.min_sec,
                    max_sec=p.max_sec,
                )
                for p in fmt.duration_presets
            ]
            format_responses.append(
                SocialFormatOptionResponse(
                    id=fmt.id,
                    label=fmt.label,
                    requires_duration=fmt.requires_duration,
                    media_mode=fmt.media_mode,
                    duration_profile=fmt.duration_profile,
                    duration_presets=preset_responses,
                )
            )
        platform_responses.append(
            SocialPlatformOptionResponse(
                id=plat.id,
                label=plat.label,
                formats=format_responses,
            )
        )

    return SocialFormatMatrixResponse(
        version=FORMAT_MATRIX_VERSION,
        limits=SocialFormatMatrixLimitsResponse(
            min_keywords=MIN_KEYWORDS,
            max_keywords=MAX_KEYWORDS,
            min_targets=MIN_TARGETS,
            max_targets=MAX_TARGETS,
        ),
        platforms=platform_responses,
    )
