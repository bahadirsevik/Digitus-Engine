# -*- coding: utf-8 -*-
"""Saf Formata Özel İçerik Sözleşmesi ve Süre Doğrulayıcısı (Mikro Faz F1-G.1 / F1-G.1a).

Bu modül sosyal brief akışında formata özel tam içerik (hooks, caption, hashtags,
format_payload, duration validation vb.) çıktısının katı ve fail-closed biçimde
doğrulanmasını, derinlemesine dondurulmuş (deeply immutable) DTO'lara dönüştürülmesini,
Gemini response schema'sının dinamik ve izole olarak üretilmesini ve JSON persistence
serializer'ını sağlar.

Kurallar (plan_social_brief_akisi.md rev.4 §4 & §9):
- DB/ORM/Pydantic/AI bağımlılığı içermez.
- Stdlib ve canonical format matrix yardımcıları dışında harici kütüphane kullanmaz.
- Immutable dataclass ve tuple kullanır; canonical DTO içinde hiçbir mutable dict/list barındırmaz.
- Girdileri sessizce düzeltmez; metin alanları doğrulama ÖNCESİ kırpılır (strip),
  yalnızca kırpma sonrası boş kalan metin reddedilir (CLAUDE.md §11 AI çıktısı
  toleransı — baştan/sondan boşluğun tek başına reddedilmesi KALDIRILDI).
- Sayısal stringleri sayıya çevirmez.
- Fallback içerik üretmez, kısmi geçerli sonuç döndürmez.
- Hata mesajlarında hiçbir ham AI çıktısı, caption, hook, hashtag, ID veya kullanıcı metni sızdırmaz.
- Dev tamsayıları (10**10000 vb.) scenario veya exception string'ine dönüştürmeden güvenle sınırlar.
- JSON ayrıştırma önce sıkı (duplicate key / NaN / Infinity reddeden) modda denenir;
  yalnızca bu başarısız olursa (örn. markdown fence) `app/core/channel/ai_json.py`
  kurtarma zincirinden (`parse_ai_json_object`) TEK bir kurtarma denemesi yapılır
  (CLAUDE.md §11: AI çıktısında çiplak `json.loads` kullanılmaz).
"""
from __future__ import annotations

import copy
import json
import math
from dataclasses import dataclass
from typing import Any, Mapping

from app.core.channel.ai_json import AIJsonParseError, parse_ai_json_object
from app.generators.social.format_matrix import (
    get_duration_preset,
    get_platform_format,
)


class SocialContentOutputValidationError(ValueError):
    """Sosyal içerik çıktısı ve sözleşme doğrulama hatası."""

    def __init__(
        self,
        message: str,
        *,
        error_code: str,
        field: str | None = None,
        item_index: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.field = field
        self.item_index = item_index

    def __str__(self) -> str:
        parts = [f"[{self.error_code}]", self.message]
        if self.field is not None:
            parts.append(f"(field={self.field})")
        if self.item_index is not None:
            parts.append(f"(item_index={self.item_index})")
        return " ".join(parts)

    def __repr__(self) -> str:
        return (
            f"SocialContentOutputValidationError(error_code={self.error_code!r}, "
            f"message={self.message!r}, field={self.field!r}, "
            f"item_index={self.item_index!r})"
        )


# Sabit Hata Kodları
CONTENT_CONTRACT_INVALID_INPUT = "CONTENT_CONTRACT_INVALID_INPUT"
CONTENT_CONTRACT_INVALID_TARGET = "CONTENT_CONTRACT_INVALID_TARGET"
CONTENT_CONTRACT_INVALID_FORMAT_PAYLOAD = "CONTENT_CONTRACT_INVALID_FORMAT_PAYLOAD"
CONTENT_CONTRACT_INVALID_TIMELINE = "CONTENT_CONTRACT_INVALID_TIMELINE"
CONTENT_CONTRACT_INVALID_DURATION = "CONTENT_CONTRACT_INVALID_DURATION"
CONTENT_CONTRACT_INVALID_TEXT = "CONTENT_CONTRACT_INVALID_TEXT"
CONTENT_CONTRACT_INVALID_HASHTAGS = "CONTENT_CONTRACT_INVALID_HASHTAGS"

# Platform Caption Limitleri
PLATFORM_CAPTION_LIMITS: Mapping[str, int] = {
    "instagram": 2200,
    "tiktok": 2200,
    "twitter": 280,
    "linkedin": 3000,
    "youtube": 5000,
}

# Hook Stilleri
ALLOWED_HOOK_STYLES: frozenset[str] = frozenset({
    "question",
    "shocking",
    "relatable",
    "curiosity",
})

# Voiceover Süre Uyarısı Sabitleri
VOICEOVER_WARNING_REASON_CODE: str = "voiceover_duration_mismatch"
VOICEOVER_MIN_WORDS_PER_SEC: float = 0.8
VOICEOVER_MAX_WORDS_PER_SEC: float = 3.5

# Alan İsimleri
REQUIRED_ROOT_FIELDS: frozenset[str] = frozenset({
    "hooks",
    "caption",
    "cta_text",
    "hashtags",
    "format_payload",
})

OPTIONAL_ROOT_FIELDS: frozenset[str] = frozenset({
    "visual_suggestion",
    "video_concept",
    "industry_posting_suggestion",
    "platform_notes",
})

ALLOWED_ROOT_FIELDS: frozenset[str] = REQUIRED_ROOT_FIELDS | OPTIONAL_ROOT_FIELDS

REQUIRED_HOOK_FIELDS: frozenset[str] = frozenset({"text", "style"})
ALLOWED_HOOK_FIELDS: frozenset[str] = frozenset({"text", "style", "ab_score"})

REQUIRED_SEGMENT_FIELDS: frozenset[str] = frozenset({
    "start_sec",
    "end_sec",
    "scene",
    "on_screen_text",
    "voiceover",
})

REQUIRED_SLIDE_FIELDS: frozenset[str] = frozenset({
    "position",
    "headline",
    "body",
    "visual_direction",
})

REQUIRED_POST_FIELDS: frozenset[str] = frozenset({
    "position",
    "text",
})


@dataclass(frozen=True)
class ContentTargetSpec:
    """Brief hedefi ve süre kısıtı spesifikasyonu."""

    target_id: int
    platform: str
    content_format: str
    duration_preset_id: str | None = None
    duration_min_sec: int | None = None
    duration_max_sec: int | None = None

    def __post_init__(self) -> None:
        validate_content_target_spec(self)


def validate_content_target_spec(spec: Any) -> None:
    """ContentTargetSpec nesnesini fail-closed kurallarla doğrular."""
    if not isinstance(spec, ContentTargetSpec):
        raise SocialContentOutputValidationError(
            "target_spec must be a ContentTargetSpec instance.",
            error_code=CONTENT_CONTRACT_INVALID_TARGET,
            field="target_spec",
        )

    # 1. target_id: pozitif strict int, bool reddedilir
    if isinstance(spec.target_id, bool) or type(spec.target_id) is not int or spec.target_id <= 0:
        raise SocialContentOutputValidationError(
            "target_id must be a positive integer.",
            error_code=CONTENT_CONTRACT_INVALID_TARGET,
            field="target_id",
        )

    # 2. platform ve content_format
    if type(spec.platform) is not str or len(spec.platform) == 0 or spec.platform != spec.platform.strip():
        raise SocialContentOutputValidationError(
            "platform must be a non-empty trimmed string.",
            error_code=CONTENT_CONTRACT_INVALID_TARGET,
            field="platform",
        )
    if (
        type(spec.content_format) is not str
        or len(spec.content_format) == 0
        or spec.content_format != spec.content_format.strip()
    ):
        raise SocialContentOutputValidationError(
            "content_format must be a non-empty trimmed string.",
            error_code=CONTENT_CONTRACT_INVALID_TARGET,
            field="content_format",
        )

    fmt_def = get_platform_format(spec.platform, spec.content_format)
    if fmt_def is None:
        raise SocialContentOutputValidationError(
            "Platform and format combination is not in canonical format matrix.",
            error_code=CONTENT_CONTRACT_INVALID_TARGET,
            field="content_format",
        )

    # 3. Süre kuralları
    if fmt_def.requires_duration:
        if (
            spec.duration_preset_id is None
            or spec.duration_min_sec is None
            or spec.duration_max_sec is None
        ):
            raise SocialContentOutputValidationError(
                "Video format requires duration preset, min_sec and max_sec.",
                error_code=CONTENT_CONTRACT_INVALID_DURATION,
                field="duration_preset_id",
            )
        if (
            type(spec.duration_preset_id) is not str
            or len(spec.duration_preset_id) == 0
            or spec.duration_preset_id != spec.duration_preset_id.strip()
        ):
            raise SocialContentOutputValidationError(
                "duration_preset_id must be a non-empty trimmed string.",
                error_code=CONTENT_CONTRACT_INVALID_DURATION,
                field="duration_preset_id",
            )
        preset_def = get_duration_preset(spec.duration_preset_id)
        if preset_def is None or preset_def not in fmt_def.duration_presets:
            raise SocialContentOutputValidationError(
                "duration_preset_id does not belong to canonical format profile.",
                error_code=CONTENT_CONTRACT_INVALID_DURATION,
                field="duration_preset_id",
            )
        if isinstance(spec.duration_min_sec, bool) or type(spec.duration_min_sec) is not int:
            raise SocialContentOutputValidationError(
                "duration_min_sec must be an integer.",
                error_code=CONTENT_CONTRACT_INVALID_DURATION,
                field="duration_min_sec",
            )
        if isinstance(spec.duration_max_sec, bool) or type(spec.duration_max_sec) is not int:
            raise SocialContentOutputValidationError(
                "duration_max_sec must be an integer.",
                error_code=CONTENT_CONTRACT_INVALID_DURATION,
                field="duration_max_sec",
            )
        if spec.duration_min_sec != preset_def.min_sec or spec.duration_max_sec != preset_def.max_sec:
            raise SocialContentOutputValidationError(
                "duration_min_sec and duration_max_sec must match canonical preset.",
                error_code=CONTENT_CONTRACT_INVALID_DURATION,
                field="duration_min_sec",
            )
    else:
        if (
            spec.duration_preset_id is not None
            or spec.duration_min_sec is not None
            or spec.duration_max_sec is not None
        ):
            raise SocialContentOutputValidationError(
                "Non-video format cannot have duration preset or limits.",
                error_code=CONTENT_CONTRACT_INVALID_DURATION,
                field="duration_preset_id",
            )


@dataclass(frozen=True)
class ValidatedHook:
    """Doğrulanmış sosyal içerik kancası (hook)."""

    text: str
    style: str
    ab_score: float | None = None


# ==================== DERİNLEMESİNE DONDURULMUŞ FORMAT PAYLOAD DTO'LARI ====================

@dataclass(frozen=True)
class ValidatedVideoSegment:
    """Doğrulanmış tekil video zaman segmenti."""

    start_sec: int
    end_sec: int
    scene: str
    on_screen_text: str
    voiceover: str


@dataclass(frozen=True)
class ValidatedVideoPayload:
    """Doğrulanmış ve dondurulmuş video format payload'u."""

    kind: str
    segments: tuple[ValidatedVideoSegment, ...]


@dataclass(frozen=True)
class ValidatedCarouselSlide:
    """Doğrulanmış tekil carousel slaytı."""

    position: int
    headline: str
    body: str
    visual_direction: str


@dataclass(frozen=True)
class ValidatedCarouselPayload:
    """Doğrulanmış ve dondurulmuş carousel format payload'u."""

    kind: str
    slides: tuple[ValidatedCarouselSlide, ...]


@dataclass(frozen=True)
class ValidatedThreadPost:
    """Doğrulanmış tekil thread tweeti."""

    position: int
    text: str


@dataclass(frozen=True)
class ValidatedThreadPayload:
    """Doğrulanmış ve dondurulmuş thread format payload'u."""

    kind: str
    posts: tuple[ValidatedThreadPost, ...]


@dataclass(frozen=True)
class ValidatedSocialContent:
    """Doğrulanmış ve dondurulmuş formata özel sosyal içerik nesnesi."""

    hooks: tuple[ValidatedHook, ...]
    caption: str
    format_payload: ValidatedVideoPayload | ValidatedCarouselPayload | ValidatedThreadPayload | None
    visual_suggestion: str | None
    video_concept: str | None
    cta_text: str
    hashtags: tuple[str, ...]
    industry_posting_suggestion: str | None
    platform_notes: str | None
    duration_status: str
    actual_duration_sec: int | None
    validation_warnings: tuple[str, ...]
    scenario: str | None


# ==================== JSON PERSISTENCE SERIALIZER ====================

def serialize_content_format_payload(
    payload: ValidatedVideoPayload
    | ValidatedCarouselPayload
    | ValidatedThreadPayload
    | None,
) -> dict[str, Any] | None:
    """Doğrulanmış immutable format payload DTO'sunu JSON serialization için plain dict/list yapısına dönüştürür.

    Her çağrıda yeni ve bağımsız bir veri ağacı döner; DTO'yu değiştirmez ve dönen dict'in
    değiştirilmesi DTO'yu etkilemez.
    """
    if payload is None:
        return None

    if isinstance(payload, ValidatedVideoPayload):
        return {
            "kind": "video",
            "segments": [
                {
                    "start_sec": seg.start_sec,
                    "end_sec": seg.end_sec,
                    "scene": seg.scene,
                    "on_screen_text": seg.on_screen_text,
                    "voiceover": seg.voiceover,
                }
                for seg in payload.segments
            ],
        }

    if isinstance(payload, ValidatedCarouselPayload):
        return {
            "kind": "carousel",
            "slides": [
                {
                    "position": slide.position,
                    "headline": slide.headline,
                    "body": slide.body,
                    "visual_direction": slide.visual_direction,
                }
                for slide in payload.slides
            ],
        }

    if isinstance(payload, ValidatedThreadPayload):
        return {
            "kind": "thread",
            "posts": [
                {
                    "position": post.position,
                    "text": post.text,
                }
                for post in payload.posts
            ],
        }

    raise SocialContentOutputValidationError(
        "Invalid format payload type for serialization.",
        error_code=CONTENT_CONTRACT_INVALID_FORMAT_PAYLOAD,
        field="format_payload",
    )


# ==================== SCENARIO API ====================

def _render_legacy_scenario_from_payload(
    payload: ValidatedVideoPayload | ValidatedCarouselPayload | ValidatedThreadPayload | None,
) -> str | None:
    """Saf immutable payload DTO'sundan deterministik okunabilir düz metin senaryosu üretir."""
    if payload is None:
        return None

    if isinstance(payload, ValidatedVideoPayload):
        if not payload.segments:
            return None
        lines = []
        for seg in payload.segments:
            lines.append(
                f"[{seg.start_sec:02d}-{seg.end_sec:02d}s] Sahne: {seg.scene}\n"
                f"Ekranda: {seg.on_screen_text}\n"
                f"Ses: {seg.voiceover}"
            )
        return "\n\n".join(lines)

    if isinstance(payload, ValidatedCarouselPayload):
        if not payload.slides:
            return None
        lines = []
        for slide in payload.slides:
            lines.append(
                f"[Slide {slide.position}] {slide.headline}\n"
                f"Metin: {slide.body}\n"
                f"Görsel: {slide.visual_direction}"
            )
        return "\n\n".join(lines)

    if isinstance(payload, ValidatedThreadPayload):
        if not payload.posts:
            return None
        lines = []
        for post in payload.posts:
            lines.append(f"[{post.position}] {post.text}")
        return "\n\n".join(lines)

    raise SocialContentOutputValidationError(
        "Invalid format payload type for scenario rendering.",
        error_code=CONTENT_CONTRACT_INVALID_FORMAT_PAYLOAD,
        field="format_payload",
    )


def render_legacy_scenario(
    validated_content: ValidatedSocialContent,
) -> str | None:
    """Doğrulanmış ValidatedSocialContent nesnesinden geriye dönük okunabilir düz metin senaryosu döner.

    Yalnız ValidatedSocialContent kabul eder; ham dict, list veya None fail-closed reddedilir.
    """
    if not isinstance(validated_content, ValidatedSocialContent):
        raise SocialContentOutputValidationError(
            "validated_content must be a ValidatedSocialContent instance.",
            error_code=CONTENT_CONTRACT_INVALID_INPUT,
            field="validated_content",
        )
    return validated_content.scenario


def _parse_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """JSON nesnesindeki duplicate anahtarları tespit edip fail-closed reddeder."""
    res: dict[str, Any] = {}
    for k, v in pairs:
        if k in res:
            raise ValueError(f"Duplicate JSON key detected")
        res[k] = v
    return res


def _reject_constant(c: str) -> None:
    """NaN, Infinity veya -Infinity gibi JSON dışı sabitleri reddeder."""
    raise ValueError(f"JSON constant not allowed")


def build_social_content_response_schema(
    target_spec: ContentTargetSpec,
) -> dict[str, Any]:
    """Gemini API için formata özel dinamik, izole ve deep-copy güvenli JSON schema üretir."""
    validate_content_target_spec(target_spec)

    fmt = target_spec.content_format

    if fmt in ("video", "reels", "short"):
        payload_schema: dict[str, Any] = {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": ["video"],
                    "description": "Video yük tipi ('video')",
                },
                "segments": {
                    "type": "array",
                    "description": "Video zaman segmentleri",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "properties": {
                            "start_sec": {
                                "type": "integer",
                                "description": "Segment başlangıç saniyesi (ilk segment 0 olmalıdır)",
                            },
                            "end_sec": {
                                "type": "integer",
                                "description": "Segment bitiş saniyesi",
                            },
                            "scene": {
                                "type": "string",
                                "description": "Sahne ve görsel plan açıklaması",
                            },
                            "on_screen_text": {
                                "type": "string",
                                "description": "Ekranda belirecek metin",
                            },
                            "voiceover": {
                                "type": "string",
                                "description": "Seslendirme metni",
                            },
                        },
                        "required": [
                            "start_sec",
                            "end_sec",
                            "scene",
                            "on_screen_text",
                            "voiceover",
                        ],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["kind", "segments"],
            "additionalProperties": False,
        }
    elif fmt == "carousel":
        payload_schema = {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": ["carousel"],
                    "description": "Carousel yük tipi ('carousel')",
                },
                "slides": {
                    "type": "array",
                    "description": "Carousel slaytları",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "properties": {
                            "position": {
                                "type": "integer",
                                "description": "Slayt sırası (1'den başlar)",
                            },
                            "headline": {
                                "type": "string",
                                "description": "Slayt başlığı",
                            },
                            "body": {
                                "type": "string",
                                "description": "Slayt metni",
                            },
                            "visual_direction": {
                                "type": "string",
                                "description": "Görsel kompozisyon yönlendirmesi",
                            },
                        },
                        "required": [
                            "position",
                            "headline",
                            "body",
                            "visual_direction",
                        ],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["kind", "slides"],
            "additionalProperties": False,
        }
    elif fmt == "thread":
        payload_schema = {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": ["thread"],
                    "description": "Thread yük tipi ('thread')",
                },
                "posts": {
                    "type": "array",
                    "description": "Thread tweetleri",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "properties": {
                            "position": {
                                "type": "integer",
                                "description": "Post sırası (1'den başlar)",
                            },
                            "text": {
                                "type": "string",
                                "description": "Tweet metni (en fazla 280 karakter)",
                            },
                        },
                        "required": ["position", "text"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["kind", "posts"],
            "additionalProperties": False,
        }
    else:
        # post veya story
        payload_schema = {
            "type": "null",
            "description": "Post ve statik story için format_payload null olmalıdır",
        }

    caption_limit = PLATFORM_CAPTION_LIMITS.get(target_spec.platform, 2200)

    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "hooks": {
                "type": "array",
                "description": "Kancalar (en az 1 adet)",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {
                            "type": "string",
                            "description": "Hook metni (en fazla 500 karakter)",
                        },
                        "style": {
                            "type": "string",
                            "enum": ["question", "shocking", "relatable", "curiosity"],
                            "description": "Hook kancalama stili",
                        },
                        "ab_score": {
                            "type": "number",
                            "description": "A/B test potansiyel skoru (0.0-1.0 arası opsiyonel)",
                        },
                    },
                    "required": ["text", "style"],
                    "additionalProperties": False,
                },
            },
            "caption": {
                "type": "string",
                "description": f"Ana içerik metni ({target_spec.platform} için en fazla {caption_limit} karakter)",
            },
            "cta_text": {
                "type": "string",
                "description": "Harekete geçirici mesaj",
            },
            "hashtags": {
                "type": "array",
                "description": "Etiketler (# işareti olmadan, 5-20 adet)",
                "minItems": 5,
                "maxItems": 20,
                "items": {
                    "type": "string",
                },
            },
            "format_payload": payload_schema,
            "visual_suggestion": {
                "type": "string",
                "description": "Görsel stili ve kompozisyon önerisi (opsiyonel)",
            },
            "video_concept": {
                "type": "string",
                "description": "B-roll, kamera açıları ve geçiş önerileri (opsiyonel)",
            },
            "industry_posting_suggestion": {
                "type": "string",
                "description": "Sektörel yayınlama zamanı genel önerisi (opsiyonel)",
            },
            "platform_notes": {
                "type": "string",
                "description": "Platform optimizasyon notları (opsiyonel)",
            },
        },
        "required": [
            "hooks",
            "caption",
            "cta_text",
            "hashtags",
            "format_payload",
        ],
        "additionalProperties": False,
    }

    return copy.deepcopy(schema)


def validate_social_content_output(
    raw: str | dict[str, Any],
    target_spec: ContentTargetSpec,
) -> ValidatedSocialContent:
    """AI sosyal içerik çıktısını sıkı fail-closed kurallarıyla doğrular."""
    validate_content_target_spec(target_spec)

    # 1. Girdi ayrıştırma ve strict JSON doğrulama
    if type(raw) is str:
        try:
            parsed = json.loads(
                raw,
                object_pairs_hook=_parse_pairs,
                parse_constant=_reject_constant,
            )
        except (json.JSONDecodeError, ValueError, OverflowError) as exc:
            # Sıkı ayrıştırma başarısız oldu. Yalnızca metin markdown fence
            # ile SARILMIŞSA (baştan/sondan ```), TEK kurtarma denemesi
            # paylaşılan AI JSON kurtarma zincirinden yapılır. Bu dar kapı
            # bilinçlidir: prose-sarmalı veya başka türlü malformed metin
            # kurtarılmaya ÇALIŞILMAZ (mevcut fail-closed sözleşme korunur);
            # bu yolda duplicate-key/NaN/Infinity reddi de UYGULANMAZ.
            stripped_raw = raw.strip()
            if not (stripped_raw.startswith("```") or stripped_raw.endswith("```")):
                raise SocialContentOutputValidationError(
                    "Invalid JSON format or syntax.",
                    error_code=CONTENT_CONTRACT_INVALID_INPUT,
                ) from exc
            try:
                parsed = parse_ai_json_object(raw, required_fields=tuple(REQUIRED_ROOT_FIELDS))
            except AIJsonParseError as recovery_exc:
                raise SocialContentOutputValidationError(
                    "Invalid JSON format or syntax.",
                    error_code=CONTENT_CONTRACT_INVALID_INPUT,
                ) from recovery_exc
    elif type(raw) is dict:
        parsed = raw
    else:
        raise SocialContentOutputValidationError(
            "raw must be a JSON string or dict.",
            error_code=CONTENT_CONTRACT_INVALID_INPUT,
        )

    if type(parsed) is not dict:
        raise SocialContentOutputValidationError(
            "Root JSON must be an object.",
            error_code=CONTENT_CONTRACT_INVALID_INPUT,
        )

    root_keys = set(parsed.keys())

    # Bilinmeyen alan kontrolü
    extra_keys = root_keys - ALLOWED_ROOT_FIELDS
    if extra_keys:
        first_extra = sorted(extra_keys)[0]
        raise SocialContentOutputValidationError(
            "Unexpected field in root JSON.",
            error_code=CONTENT_CONTRACT_INVALID_INPUT,
            field=first_extra,
        )

    # Eksik zorunlu alan kontrolü
    missing_keys = REQUIRED_ROOT_FIELDS - root_keys
    if missing_keys:
        first_missing = sorted(missing_keys)[0]
        raise SocialContentOutputValidationError(
            "Missing required field in root JSON.",
            error_code=CONTENT_CONTRACT_INVALID_INPUT,
            field=first_missing,
        )

    # 2. caption kontrolü
    caption = parsed["caption"]
    if type(caption) is not str:
        raise SocialContentOutputValidationError(
            "caption must be a string.",
            error_code=CONTENT_CONTRACT_INVALID_TEXT,
            field="caption",
        )
    # Çevresel boşluk kabul edilir ve doğrulama öncesi kırpılır; yalnız
    # kırpma sonrası boş kalan metin reddedilir (bare AI output whitespace
    # katı reddi kaldırıldı — CLAUDE.md §11 AI çıktısı toleransı).
    caption = caption.strip()
    if len(caption) == 0:
        raise SocialContentOutputValidationError(
            "caption cannot be empty or whitespace only.",
            error_code=CONTENT_CONTRACT_INVALID_TEXT,
            field="caption",
        )
    max_caption_len = PLATFORM_CAPTION_LIMITS.get(target_spec.platform, 2200)
    if len(caption) > max_caption_len:
        raise SocialContentOutputValidationError(
            "Caption exceeds maximum allowed length for platform.",
            error_code=CONTENT_CONTRACT_INVALID_TEXT,
            field="caption",
        )

    # 3. cta_text kontrolü
    cta_text = parsed["cta_text"]
    if type(cta_text) is not str:
        raise SocialContentOutputValidationError(
            "cta_text must be a string.",
            error_code=CONTENT_CONTRACT_INVALID_TEXT,
            field="cta_text",
        )
    cta_text = cta_text.strip()
    if len(cta_text) == 0:
        raise SocialContentOutputValidationError(
            "cta_text cannot be empty or whitespace only.",
            error_code=CONTENT_CONTRACT_INVALID_TEXT,
            field="cta_text",
        )

    # 4. hooks kontrolü
    raw_hooks = parsed["hooks"]
    if type(raw_hooks) is not list:
        raise SocialContentOutputValidationError(
            "hooks must be a list.",
            error_code=CONTENT_CONTRACT_INVALID_TEXT,
            field="hooks",
        )
    if len(raw_hooks) < 1:
        raise SocialContentOutputValidationError(
            "hooks must contain at least 1 hook.",
            error_code=CONTENT_CONTRACT_INVALID_TEXT,
            field="hooks",
        )

    validated_hooks: list[ValidatedHook] = []
    for h_idx, hook in enumerate(raw_hooks):
        if type(hook) is not dict:
            raise SocialContentOutputValidationError(
                "Hook item must be an object.",
                error_code=CONTENT_CONTRACT_INVALID_TEXT,
                field="hooks",
                item_index=h_idx,
            )
        hook_keys = set(hook.keys())
        extra_h_keys = hook_keys - ALLOWED_HOOK_FIELDS
        if extra_h_keys:
            first_extra_h = sorted(extra_h_keys)[0]
            raise SocialContentOutputValidationError(
                "Unexpected field in hook object.",
                error_code=CONTENT_CONTRACT_INVALID_TEXT,
                field=first_extra_h,
                item_index=h_idx,
            )
        missing_h_keys = REQUIRED_HOOK_FIELDS - hook_keys
        if missing_h_keys:
            first_missing_h = sorted(missing_h_keys)[0]
            raise SocialContentOutputValidationError(
                "Missing required field in hook object.",
                error_code=CONTENT_CONTRACT_INVALID_TEXT,
                field=first_missing_h,
                item_index=h_idx,
            )

        # hook.text
        h_text = hook["text"]
        if type(h_text) is not str:
            raise SocialContentOutputValidationError(
                "Hook text must be a string.",
                error_code=CONTENT_CONTRACT_INVALID_TEXT,
                field="hooks.text",
                item_index=h_idx,
            )
        h_text = h_text.strip()
        if len(h_text) == 0:
            raise SocialContentOutputValidationError(
                "Hook text cannot be empty or whitespace only.",
                error_code=CONTENT_CONTRACT_INVALID_TEXT,
                field="hooks.text",
                item_index=h_idx,
            )
        if len(h_text) > 500:
            raise SocialContentOutputValidationError(
                "Hook text exceeds maximum length of 500 characters.",
                error_code=CONTENT_CONTRACT_INVALID_TEXT,
                field="hooks.text",
                item_index=h_idx,
            )

        # hook.style
        h_style = hook["style"]
        if type(h_style) is not str or h_style not in ALLOWED_HOOK_STYLES:
            raise SocialContentOutputValidationError(
                "Invalid hook style.",
                error_code=CONTENT_CONTRACT_INVALID_TEXT,
                field="hooks.style",
                item_index=h_idx,
            )

        # hook.ab_score
        h_ab = hook.get("ab_score")
        val_ab: float | None = None
        if h_ab is not None:
            if isinstance(h_ab, bool) or type(h_ab) not in (int, float):
                raise SocialContentOutputValidationError(
                    "ab_score must be a finite number between 0.0 and 1.0.",
                    error_code=CONTENT_CONTRACT_INVALID_TEXT,
                    field="hooks.ab_score",
                    item_index=h_idx,
                )
            try:
                flt_ab = float(h_ab)
            except (OverflowError, ValueError, TypeError):
                raise SocialContentOutputValidationError(
                    "ab_score cannot be converted to float.",
                    error_code=CONTENT_CONTRACT_INVALID_TEXT,
                    field="hooks.ab_score",
                    item_index=h_idx,
                )
            if not math.isfinite(flt_ab) or flt_ab < 0.0 or flt_ab > 1.0:
                raise SocialContentOutputValidationError(
                    "ab_score must be a finite number between 0.0 and 1.0.",
                    error_code=CONTENT_CONTRACT_INVALID_TEXT,
                    field="hooks.ab_score",
                    item_index=h_idx,
                )
            val_ab = flt_ab

        validated_hooks.append(
            ValidatedHook(
                text=h_text,
                style=h_style,
                ab_score=val_ab,
            )
        )

    # 5. hashtags kontrolü
    raw_hashtags = parsed["hashtags"]
    if type(raw_hashtags) is not list:
        raise SocialContentOutputValidationError(
            "hashtags must be a list.",
            error_code=CONTENT_CONTRACT_INVALID_HASHTAGS,
            field="hashtags",
        )
    if len(raw_hashtags) < 5 or len(raw_hashtags) > 20:
        raise SocialContentOutputValidationError(
            "hashtags must contain between 5 and 20 items.",
            error_code=CONTENT_CONTRACT_INVALID_HASHTAGS,
            field="hashtags",
        )

    seen_tags: set[str] = set()
    cleaned_hashtags: list[str] = []
    for tag_idx, tag in enumerate(raw_hashtags):
        if type(tag) is not str:
            raise SocialContentOutputValidationError(
                "Hashtag item must be a string.",
                error_code=CONTENT_CONTRACT_INVALID_HASHTAGS,
                field="hashtags",
                item_index=tag_idx,
            )
        tag = tag.strip()
        if len(tag) == 0:
            raise SocialContentOutputValidationError(
                "Hashtag item cannot be empty or whitespace only.",
                error_code=CONTENT_CONTRACT_INVALID_HASHTAGS,
                field="hashtags",
                item_index=tag_idx,
            )
        if tag.startswith("#"):
            raise SocialContentOutputValidationError(
                "Hashtag item must not start with '#' symbol.",
                error_code=CONTENT_CONTRACT_INVALID_HASHTAGS,
                field="hashtags",
                item_index=tag_idx,
            )
        if tag in seen_tags:
            raise SocialContentOutputValidationError(
                "Duplicate hashtag detected.",
                error_code=CONTENT_CONTRACT_INVALID_HASHTAGS,
                field="hashtags",
                item_index=tag_idx,
            )
        seen_tags.add(tag)
        cleaned_hashtags.append(tag)

    # 6. Opsiyonel metin alanları
    optional_fields: dict[str, str | None] = {}
    for opt_field in (
        "visual_suggestion",
        "video_concept",
        "industry_posting_suggestion",
        "platform_notes",
    ):
        opt_val = parsed.get(opt_field)
        if opt_val is not None:
            if type(opt_val) is not str:
                raise SocialContentOutputValidationError(
                    "Optional field must be a string or null.",
                    error_code=CONTENT_CONTRACT_INVALID_TEXT,
                    field=opt_field,
                )
            opt_val = opt_val.strip()
            if len(opt_val) == 0:
                raise SocialContentOutputValidationError(
                    "Optional field cannot be empty or whitespace only.",
                    error_code=CONTENT_CONTRACT_INVALID_TEXT,
                    field=opt_field,
                )
        optional_fields[opt_field] = opt_val

    # 7. Formata özel format_payload ve süre doğrulaması
    fmt = target_spec.content_format
    raw_payload = parsed.get("format_payload")
    duration_status: str
    actual_duration_sec: int | None
    validation_warnings: list[str] = []
    payload_dto: ValidatedVideoPayload | ValidatedCarouselPayload | ValidatedThreadPayload | None

    if fmt in ("video", "reels", "short"):
        if type(raw_payload) is not dict:
            raise SocialContentOutputValidationError(
                "Video format requires a format_payload object.",
                error_code=CONTENT_CONTRACT_INVALID_FORMAT_PAYLOAD,
                field="format_payload",
            )
        payload_keys = set(raw_payload.keys())
        expected_payload_keys = {"kind", "segments"}
        if payload_keys != expected_payload_keys:
            first_diff = sorted(payload_keys ^ expected_payload_keys)[0]
            raise SocialContentOutputValidationError(
                "format_payload must have exactly 'kind' and 'segments' fields.",
                error_code=CONTENT_CONTRACT_INVALID_FORMAT_PAYLOAD,
                field=f"format_payload.{first_diff}",
            )
        if raw_payload["kind"] != "video":
            raise SocialContentOutputValidationError(
                "format_payload kind must be 'video'.",
                error_code=CONTENT_CONTRACT_INVALID_FORMAT_PAYLOAD,
                field="format_payload.kind",
            )
        segments = raw_payload["segments"]
        if type(segments) is not list or len(segments) == 0:
            raise SocialContentOutputValidationError(
                "segments must be a non-empty list.",
                error_code=CONTENT_CONTRACT_INVALID_FORMAT_PAYLOAD,
                field="format_payload.segments",
            )

        fmt_def = get_platform_format(target_spec.platform, target_spec.content_format)
        profile_max_sec = max(p.max_sec for p in fmt_def.duration_presets) if fmt_def else 600

        prev_end = 0
        total_voiceover_words = 0
        validated_segments: list[ValidatedVideoSegment] = []

        for s_idx, seg in enumerate(segments):
            if type(seg) is not dict:
                raise SocialContentOutputValidationError(
                    "Segment item must be an object.",
                    error_code=CONTENT_CONTRACT_INVALID_FORMAT_PAYLOAD,
                    field="format_payload.segments",
                    item_index=s_idx,
                )
            seg_keys = set(seg.keys())
            if seg_keys != REQUIRED_SEGMENT_FIELDS:
                first_diff_seg = sorted(seg_keys ^ REQUIRED_SEGMENT_FIELDS)[0]
                raise SocialContentOutputValidationError(
                    "Segment must contain exactly required fields.",
                    error_code=CONTENT_CONTRACT_INVALID_FORMAT_PAYLOAD,
                    field=f"segments.{first_diff_seg}",
                    item_index=s_idx,
                )

            s_start = seg["start_sec"]
            s_end = seg["end_sec"]

            if isinstance(s_start, bool) or type(s_start) is not int:
                raise SocialContentOutputValidationError(
                    "start_sec must be an integer.",
                    error_code=CONTENT_CONTRACT_INVALID_TIMELINE,
                    field="start_sec",
                    item_index=s_idx,
                )
            if isinstance(s_end, bool) or type(s_end) is not int:
                raise SocialContentOutputValidationError(
                    "end_sec must be an integer.",
                    error_code=CONTENT_CONTRACT_INVALID_TIMELINE,
                    field="end_sec",
                    item_index=s_idx,
                )

            if s_start < 0:
                raise SocialContentOutputValidationError(
                    "Segment start_sec cannot be negative.",
                    error_code=CONTENT_CONTRACT_INVALID_TIMELINE,
                    field="start_sec",
                    item_index=s_idx,
                )
            if s_end <= 0:
                raise SocialContentOutputValidationError(
                    "Segment end_sec must be positive.",
                    error_code=CONTENT_CONTRACT_INVALID_TIMELINE,
                    field="end_sec",
                    item_index=s_idx,
                )

            if s_idx == 0 and s_start != 0:
                raise SocialContentOutputValidationError(
                    "First video segment must start at 0 seconds.",
                    error_code=CONTENT_CONTRACT_INVALID_TIMELINE,
                    field="start_sec",
                    item_index=0,
                )
            if s_end <= s_start:
                raise SocialContentOutputValidationError(
                    "Segment end_sec must be strictly greater than start_sec.",
                    error_code=CONTENT_CONTRACT_INVALID_TIMELINE,
                    field="end_sec",
                    item_index=s_idx,
                )
            if s_start != prev_end:
                raise SocialContentOutputValidationError(
                    "Video segment timeline contains a gap or overlap.",
                    error_code=CONTENT_CONTRACT_INVALID_TIMELINE,
                    field="start_sec",
                    item_index=s_idx,
                )

            # Canonical profil üst sınırı kontrolü (dev tamsayılar dahil güvenli durdurma)
            if s_end > profile_max_sec:
                raise SocialContentOutputValidationError(
                    "Video duration exceeds canonical format profile upper bound.",
                    error_code=CONTENT_CONTRACT_INVALID_DURATION,
                    field="end_sec",
                    item_index=s_idx,
                )

            prev_end = s_end

            # scene, on_screen_text, voiceover metin kontrolleri
            for text_field in ("scene", "on_screen_text", "voiceover"):
                t_val = seg[text_field]
                if type(t_val) is not str:
                    raise SocialContentOutputValidationError(
                        "Segment text field must be a string.",
                        error_code=CONTENT_CONTRACT_INVALID_TEXT,
                        field=f"segments.{text_field}",
                        item_index=s_idx,
                    )
                t_val = t_val.strip()
                if len(t_val) == 0:
                    raise SocialContentOutputValidationError(
                        "Segment text field cannot be empty or whitespace only.",
                        error_code=CONTENT_CONTRACT_INVALID_TEXT,
                        field=f"segments.{text_field}",
                        item_index=s_idx,
                    )
                # Kırpılmış değeri geri yaz: ValidatedVideoSegment aşağıda seg[...]
                # üzerinden okur, t_val local değişkeni değil.
                seg[text_field] = t_val
                if text_field == "voiceover":
                    total_voiceover_words += len(t_val.split())

            validated_segments.append(
                ValidatedVideoSegment(
                    start_sec=s_start,
                    end_sec=s_end,
                    scene=seg["scene"],
                    on_screen_text=seg["on_screen_text"],
                    voiceover=seg["voiceover"],
                )
            )

        actual_duration_sec = segments[-1]["end_sec"]
        min_sec = target_spec.duration_min_sec
        max_sec = target_spec.duration_max_sec

        if min_sec is not None and max_sec is not None and min_sec <= actual_duration_sec <= max_sec:
            duration_status = "valid"
        else:
            duration_status = "mismatch"

        # Voiceover süre uyarısı kontrolü
        if actual_duration_sec > 0:
            wps = total_voiceover_words / actual_duration_sec
            if wps < VOICEOVER_MIN_WORDS_PER_SEC or wps > VOICEOVER_MAX_WORDS_PER_SEC:
                validation_warnings.append(VOICEOVER_WARNING_REASON_CODE)

        payload_dto = ValidatedVideoPayload(
            kind="video",
            segments=tuple(validated_segments),
        )

    elif fmt == "carousel":
        if type(raw_payload) is not dict:
            raise SocialContentOutputValidationError(
                "Carousel format requires a format_payload object.",
                error_code=CONTENT_CONTRACT_INVALID_FORMAT_PAYLOAD,
                field="format_payload",
            )
        payload_keys = set(raw_payload.keys())
        expected_payload_keys = {"kind", "slides"}
        if payload_keys != expected_payload_keys:
            first_diff = sorted(payload_keys ^ expected_payload_keys)[0]
            raise SocialContentOutputValidationError(
                "format_payload must have exactly 'kind' and 'slides' fields.",
                error_code=CONTENT_CONTRACT_INVALID_FORMAT_PAYLOAD,
                field=f"format_payload.{first_diff}",
            )
        if raw_payload["kind"] != "carousel":
            raise SocialContentOutputValidationError(
                "format_payload kind must be 'carousel'.",
                error_code=CONTENT_CONTRACT_INVALID_FORMAT_PAYLOAD,
                field="format_payload.kind",
            )
        slides = raw_payload["slides"]
        if type(slides) is not list or len(slides) == 0:
            raise SocialContentOutputValidationError(
                "slides must be a non-empty list.",
                error_code=CONTENT_CONTRACT_INVALID_FORMAT_PAYLOAD,
                field="format_payload.slides",
            )

        validated_slides: list[ValidatedCarouselSlide] = []

        for sl_idx, slide in enumerate(slides):
            if type(slide) is not dict:
                raise SocialContentOutputValidationError(
                    "Slide item must be an object.",
                    error_code=CONTENT_CONTRACT_INVALID_FORMAT_PAYLOAD,
                    field="format_payload.slides",
                    item_index=sl_idx,
                )
            sl_keys = set(slide.keys())
            if sl_keys != REQUIRED_SLIDE_FIELDS:
                first_diff_sl = sorted(sl_keys ^ REQUIRED_SLIDE_FIELDS)[0]
                raise SocialContentOutputValidationError(
                    "Slide must contain exactly required fields.",
                    error_code=CONTENT_CONTRACT_INVALID_FORMAT_PAYLOAD,
                    field=f"slides.{first_diff_sl}",
                    item_index=sl_idx,
                )
            pos = slide["position"]
            if isinstance(pos, bool) or type(pos) is not int:
                raise SocialContentOutputValidationError(
                    "Slide position must be an integer.",
                    error_code=CONTENT_CONTRACT_INVALID_FORMAT_PAYLOAD,
                    field="position",
                    item_index=sl_idx,
                )
            if pos != sl_idx + 1:
                raise SocialContentOutputValidationError(
                    "Slides must be sequentially numbered starting from 1.",
                    error_code=CONTENT_CONTRACT_INVALID_FORMAT_PAYLOAD,
                    field="position",
                    item_index=sl_idx,
                )
            for sl_text in ("headline", "body", "visual_direction"):
                sl_val = slide[sl_text]
                if type(sl_val) is not str:
                    raise SocialContentOutputValidationError(
                        "Slide text field must be a string.",
                        error_code=CONTENT_CONTRACT_INVALID_TEXT,
                        field=f"slides.{sl_text}",
                        item_index=sl_idx,
                    )
                sl_val = sl_val.strip()
                if len(sl_val) == 0:
                    raise SocialContentOutputValidationError(
                        "Slide text field cannot be empty or whitespace only.",
                        error_code=CONTENT_CONTRACT_INVALID_TEXT,
                        field=f"slides.{sl_text}",
                        item_index=sl_idx,
                    )
                # Kırpılmış değeri geri yaz: ValidatedCarouselSlide aşağıda
                # slide[...] üzerinden okur, sl_val local değişkeni değil.
                slide[sl_text] = sl_val

            validated_slides.append(
                ValidatedCarouselSlide(
                    position=pos,
                    headline=slide["headline"],
                    body=slide["body"],
                    visual_direction=slide["visual_direction"],
                )
            )

        duration_status = "not_applicable"
        actual_duration_sec = None
        payload_dto = ValidatedCarouselPayload(
            kind="carousel",
            slides=tuple(validated_slides),
        )

    elif fmt == "thread":
        if type(raw_payload) is not dict:
            raise SocialContentOutputValidationError(
                "Thread format requires a format_payload object.",
                error_code=CONTENT_CONTRACT_INVALID_FORMAT_PAYLOAD,
                field="format_payload",
            )
        payload_keys = set(raw_payload.keys())
        expected_payload_keys = {"kind", "posts"}
        if payload_keys != expected_payload_keys:
            first_diff = sorted(payload_keys ^ expected_payload_keys)[0]
            raise SocialContentOutputValidationError(
                "format_payload must have exactly 'kind' and 'posts' fields.",
                error_code=CONTENT_CONTRACT_INVALID_FORMAT_PAYLOAD,
                field=f"format_payload.{first_diff}",
            )
        if raw_payload["kind"] != "thread":
            raise SocialContentOutputValidationError(
                "format_payload kind must be 'thread'.",
                error_code=CONTENT_CONTRACT_INVALID_FORMAT_PAYLOAD,
                field="format_payload.kind",
            )
        posts = raw_payload["posts"]
        if type(posts) is not list or len(posts) == 0:
            raise SocialContentOutputValidationError(
                "posts must be a non-empty list.",
                error_code=CONTENT_CONTRACT_INVALID_FORMAT_PAYLOAD,
                field="format_payload.posts",
            )

        validated_posts: list[ValidatedThreadPost] = []

        for p_idx, post in enumerate(posts):
            if type(post) is not dict:
                raise SocialContentOutputValidationError(
                    "Post item must be an object.",
                    error_code=CONTENT_CONTRACT_INVALID_FORMAT_PAYLOAD,
                    field="format_payload.posts",
                    item_index=p_idx,
                )
            p_keys = set(post.keys())
            if p_keys != REQUIRED_POST_FIELDS:
                first_diff_p = sorted(p_keys ^ REQUIRED_POST_FIELDS)[0]
                raise SocialContentOutputValidationError(
                    "Post must contain exactly required fields.",
                    error_code=CONTENT_CONTRACT_INVALID_FORMAT_PAYLOAD,
                    field=f"posts.{first_diff_p}",
                    item_index=p_idx,
                )
            p_pos = post["position"]
            if isinstance(p_pos, bool) or type(p_pos) is not int:
                raise SocialContentOutputValidationError(
                    "Post position must be an integer.",
                    error_code=CONTENT_CONTRACT_INVALID_FORMAT_PAYLOAD,
                    field="position",
                    item_index=p_idx,
                )
            if p_pos != p_idx + 1:
                raise SocialContentOutputValidationError(
                    "Posts must be sequentially numbered starting from 1.",
                    error_code=CONTENT_CONTRACT_INVALID_FORMAT_PAYLOAD,
                    field="position",
                    item_index=p_idx,
                )
            p_text = post["text"]
            if type(p_text) is not str:
                raise SocialContentOutputValidationError(
                    "Post text must be a string.",
                    error_code=CONTENT_CONTRACT_INVALID_TEXT,
                    field="posts.text",
                    item_index=p_idx,
                )
            p_text = p_text.strip()
            if len(p_text) == 0:
                raise SocialContentOutputValidationError(
                    "Post text cannot be empty or whitespace only.",
                    error_code=CONTENT_CONTRACT_INVALID_TEXT,
                    field="posts.text",
                    item_index=p_idx,
                )
            if len(p_text) > 280:
                raise SocialContentOutputValidationError(
                    "Thread post exceeds maximum length of 280 characters.",
                    error_code=CONTENT_CONTRACT_INVALID_TEXT,
                    field="posts.text",
                    item_index=p_idx,
                )

            validated_posts.append(
                ValidatedThreadPost(
                    position=p_pos,
                    text=p_text,
                )
            )

        duration_status = "not_applicable"
        actual_duration_sec = None
        payload_dto = ValidatedThreadPayload(
            kind="thread",
            posts=tuple(validated_posts),
        )

    else:
        # post veya story
        if raw_payload is not None:
            raise SocialContentOutputValidationError(
                "Post and static story formats must not include format_payload.",
                error_code=CONTENT_CONTRACT_INVALID_FORMAT_PAYLOAD,
                field="format_payload",
            )
        duration_status = "not_applicable"
        actual_duration_sec = None
        payload_dto = None

    scenario_text = _render_legacy_scenario_from_payload(payload_dto)

    return ValidatedSocialContent(
        hooks=tuple(validated_hooks),
        caption=caption,
        format_payload=payload_dto,
        visual_suggestion=optional_fields["visual_suggestion"],
        video_concept=optional_fields["video_concept"],
        cta_text=cta_text,
        hashtags=tuple(cleaned_hashtags),
        industry_posting_suggestion=optional_fields["industry_posting_suggestion"],
        platform_notes=optional_fields["platform_notes"],
        duration_status=duration_status,
        actual_duration_sec=actual_duration_sec,
        validation_warnings=tuple(validation_warnings),
        scenario=scenario_text,
    )
