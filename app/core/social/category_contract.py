# -*- coding: utf-8 -*-
"""Saf Kategori Çıktı Sözleşmesi ve Fail-Closed Doğrulayıcı (F1-E.2).

Bu modül kategori AI çıktısının hangi yapıda olması gerektiğini tanımlayan saf
response schema ve fail-closed doğrulayıcı katmanını sunar.

Kurallar:
- Harici framework, veritabanı ORM veya yapay zeka servisi bağımlılığı taşımaz (saf Python stdlib).
- Canonical kategori tipleri kapalı allowlist ile yönetilir.
- Bilinmeyen tip sessizce educational'a çevrilmez, fail-closed hata üretir.
- Önce strict json.loads denenir (duplicate key, trailing comma, NaN, Infinity reddedilir);
  yalnızca bu ilk deneme başarısız olursa (örn. markdown fence) `app/core/channel/ai_json.py`
  kurtarma zincirinden TEK bir kurtarma denemesi yapılır (CLAUDE.md §11: AI çıktısında
  çiplak json.loads kullanılmaz). Kurtarma yolunda duplicate-key/NaN/Infinity reddi uygulanmaz.
- Sonuçlar ve şemalar immutable'dır (frozen dataclass, deep copy koruması).
"""
from __future__ import annotations

import copy
import json
import math
from collections.abc import Collection
from dataclasses import dataclass
from typing import Any

from app.core.channel.ai_json import AIJsonParseError, parse_ai_json_object

# Kanonik kategori tipleri (kapalı allowlist)
CANONICAL_CATEGORY_TYPES: tuple[str, ...] = (
    "educational",
    "product_benefit",
    "social_proof",
    "brand_story",
    "community",
    "trending",
)

_CANONICAL_CATEGORY_SET: frozenset[str] = frozenset(CANONICAL_CATEGORY_TYPES)

# Kategori nesnesinde izin verilen zorunlu alanlar
REQUIRED_CATEGORY_FIELDS: frozenset[str] = frozenset({
    "category_name",
    "category_type",
    "description",
    "relevance_score",
    "suggested_keyword_ids",
})


class SocialCategoryOutputValidationError(ValueError):
    """Kategori AI çıktısı doğrulama hatası."""

    def __init__(
        self,
        message: str,
        *,
        error_code: str,
        field: str | None = None,
        category_index: int | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.field = field
        self.category_index = category_index

    def __str__(self) -> str:
        parts = [f"[{self.error_code}]", self.message]
        if self.field is not None:
            parts.append(f"(field={self.field})")
        if self.category_index is not None:
            parts.append(f"(index={self.category_index})")
        return " ".join(parts)


@dataclass(frozen=True)
class ValidatedSocialCategory:
    """Doğrulanmış ve dondurulmuş kategori çıktısı."""

    category_name: str
    category_type: str
    description: str
    relevance_score: float
    suggested_keyword_ids: tuple[int, ...]


# Gemini yapılandırılmış çıktı şablonu (statik maksimum 6 kategori)
_SOCIAL_CATEGORY_RESPONSE_SCHEMA_RAW: dict[str, Any] = {
    "type": "object",
    "properties": {
        "categories": {
            "type": "array",
            "minItems": 2,
            "maxItems": 6,
            "items": {
                "type": "object",
                "properties": {
                    "category_name": {
                        "type": "string",
                        "description": "Kategori başlığı (en fazla 100 karakter)",
                    },
                    "category_type": {
                        "type": "string",
                        "enum": list(CANONICAL_CATEGORY_TYPES),
                        "description": "Kanonik kategori tipi",
                    },
                    "description": {
                        "type": "string",
                        "description": "Kategori açıklaması ve stratejik gerekçe (en fazla 2000 karakter)",
                    },
                    "relevance_score": {
                        "type": "number",
                        "description": "0.0 ile 1.0 arasında uygunluk skoru",
                    },
                    "suggested_keyword_ids": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "minItems": 1,
                        "description": "Kategoriyle eşleşen brief keyword ID listesi",
                    },
                },
                "required": [
                    "category_name",
                    "category_type",
                    "description",
                    "relevance_score",
                    "suggested_keyword_ids",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["categories"],
    "additionalProperties": False,
}


def get_social_category_response_schema() -> dict[str, Any]:
    """Gemini yapılandırılmış çıktı şemasının izole bir derin kopyasını döner."""
    return copy.deepcopy(_SOCIAL_CATEGORY_RESPONSE_SCHEMA_RAW)


def __getattr__(name: str) -> Any:
    """SOCIAL_CATEGORY_RESPONSE_SCHEMA erişildiğinde dış mutation'a karşı izole kopya döner."""
    if name == "SOCIAL_CATEGORY_RESPONSE_SCHEMA":
        return get_social_category_response_schema()
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")


def __dir__() -> list[str]:
    return sorted(list(globals().keys()) + ["SOCIAL_CATEGORY_RESPONSE_SCHEMA"])


def _parse_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """JSON nesnesindeki duplicate anahtarları tespit edip reddeder."""
    res: dict[str, Any] = {}
    for k, v in pairs:
        if k in res:
            raise ValueError(f"Duplicate JSON key: {k}")
        res[k] = v
    return res


def _reject_constant(c: str) -> None:
    """NaN, Infinity veya -Infinity gibi JSON dışı sabitleri reddeder."""
    raise ValueError(f"JSON constant not allowed: {c}")


def validate_social_category_output(
    raw_json: str,
    *,
    allowed_keyword_ids: Collection[int],
    max_categories: int,
) -> tuple[ValidatedSocialCategory, ...]:
    """Kategori AI çıktısını sıkı fail-closed kurallarıyla doğrular.

    Args:
        raw_json: Modelden dönen ham JSON metni (yalnız str).
        allowed_keyword_ids: Brief'e ait izinli pozitif anahtar kelime kimlikleri.
        max_categories: Bu çağrıda izin verilen maksimum kategori sayısı (2-6).

    Returns:
        tuple[ValidatedSocialCategory, ...]: Doğrulanmış dondurulmuş kategori listesi.

    Raises:
        SocialCategoryOutputValidationError: Format, şema, sınır veya referans hatası.
    """
    # 1. max_categories parametre doğrulaması
    if (
        isinstance(max_categories, bool)
        or not isinstance(max_categories, int)
        or max_categories < 2
        or max_categories > 6
    ):
        raise SocialCategoryOutputValidationError(
            "max_categories 2 ile 6 arasında bir tamsayı olmalıdır.",
            error_code="INVALID_CATEGORY_COUNT",
            field="max_categories",
        )

    # 2. allowed_keyword_ids parametre doğrulaması
    if (
        isinstance(allowed_keyword_ids, (str, bytes))
        or not isinstance(allowed_keyword_ids, Collection)
        or len(allowed_keyword_ids) == 0
    ):
        raise SocialCategoryOutputValidationError(
            "allowed_keyword_ids boş olamaz ve bir koleksiyon olmalıdır.",
            error_code="INVALID_KEYWORD_REFERENCE",
            field="allowed_keyword_ids",
        )

    allowed_set: set[int] = set()
    for kw_id in allowed_keyword_ids:
        if isinstance(kw_id, bool) or not isinstance(kw_id, int) or kw_id <= 0:
            raise SocialCategoryOutputValidationError(
                "allowed_keyword_ids pozitif strict int değerler içermelidir.",
                error_code="INVALID_KEYWORD_REFERENCE",
                field="allowed_keyword_ids",
            )
        allowed_set.add(kw_id)

    # 3. raw_json strict ayrıştırma
    if not isinstance(raw_json, str):
        raise SocialCategoryOutputValidationError(
            "raw_json str tipinde olmalıdır.",
            error_code="INVALID_JSON",
        )

    try:
        parsed = json.loads(
            raw_json,
            object_pairs_hook=_parse_pairs,
            parse_constant=_reject_constant,
        )
    except (json.JSONDecodeError, ValueError) as strict_exc:
        # Sıkı ayrıştırma başarısız oldu. Yalnızca metin markdown fence ile
        # SARILMIŞSA (baştan/sondan ```), TEK kurtarma denemesi paylaşılan AI
        # JSON kurtarma zincirinden yapılır. Bu dar kapı bilinçlidir:
        # prose-sarmalı (leading/trailing prose) veya başka türlü malformed
        # metin kurtarılmaya ÇALIŞILMAZ (mevcut fail-closed sözleşme
        # korunur); bu yolda duplicate-key/NaN/Infinity reddi de UYGULANMAZ.
        stripped_raw_json = raw_json.strip()
        if not (stripped_raw_json.startswith("```") or stripped_raw_json.endswith("```")):
            raise SocialCategoryOutputValidationError(
                "Geçersiz JSON formatı veya sözdizimi.",
                error_code="INVALID_JSON",
            ) from strict_exc
        try:
            parsed = parse_ai_json_object(raw_json, required_fields=("categories",))
        except AIJsonParseError as recovery_exc:
            raise SocialCategoryOutputValidationError(
                "Geçersiz JSON formatı veya sözdizimi.",
                error_code="INVALID_JSON",
            ) from recovery_exc

    # 4. Kök nesne doğrulaması
    if not isinstance(parsed, dict):
        raise SocialCategoryOutputValidationError(
            "Kök JSON bir nesne olmalıdır.",
            error_code="INVALID_ROOT",
        )

    root_keys = set(parsed.keys())
    if "categories" not in root_keys:
        raise SocialCategoryOutputValidationError(
            "Kök JSON 'categories' alanını içermelidir.",
            error_code="INVALID_ROOT",
            field="categories",
        )

    extra_root_keys = root_keys - {"categories"}
    if extra_root_keys:
        field = sorted(extra_root_keys)[0]
        raise SocialCategoryOutputValidationError(
            f"Kök JSON nesnesinde beklenmeyen alan: '{field}'.",
            error_code="INVALID_ROOT",
            field=field,
        )

    categories_raw = parsed["categories"]
    if not isinstance(categories_raw, list):
        raise SocialCategoryOutputValidationError(
            "'categories' alanı bir liste olmalıdır.",
            error_code="INVALID_ROOT",
            field="categories",
        )

    # 5. Kategori sayısı sınır doğrulaması (2 <= len <= max_categories)
    count = len(categories_raw)
    if count < 2 or count > max_categories:
        raise SocialCategoryOutputValidationError(
            f"Kategori sayısı 2 ile {max_categories} arasında olmalıdır (alınan: {count}).",
            error_code="INVALID_CATEGORY_COUNT",
            field="categories",
        )

    # 6. Her bir kategorinin ayrıntılı doğrulanması
    seen_category_names: set[str] = set()
    validated_categories: list[ValidatedSocialCategory] = []

    for idx, item in enumerate(categories_raw):
        if not isinstance(item, dict):
            raise SocialCategoryOutputValidationError(
                f"Kategori öğesi bir JSON nesnesi olmalıdır (index {idx}).",
                error_code="INVALID_CATEGORY",
                category_index=idx,
            )

        cat_keys = set(item.keys())
        missing_fields = REQUIRED_CATEGORY_FIELDS - cat_keys
        if missing_fields:
            field = sorted(missing_fields)[0]
            raise SocialCategoryOutputValidationError(
                f"Kategori nesnesinde zorunlu alan eksik: '{field}' (index {idx}).",
                error_code="INVALID_CATEGORY",
                field=field,
                category_index=idx,
            )

        extra_fields = cat_keys - REQUIRED_CATEGORY_FIELDS
        if extra_fields:
            field = sorted(extra_fields)[0]
            raise SocialCategoryOutputValidationError(
                f"Kategori nesnesinde beklenmeyen alan: '{field}' (index {idx}).",
                error_code="INVALID_CATEGORY",
                field=field,
                category_index=idx,
            )

        # 6a. category_name
        cat_name = item["category_name"]
        if (
            isinstance(cat_name, bool)
            or not isinstance(cat_name, str)
            or len(cat_name) == 0
            or cat_name != cat_name.strip()
            or len(cat_name) > 100
        ):
            raise SocialCategoryOutputValidationError(
                f"Kategori adı başta/sonda boşluk olmayan 1-100 karakter arası bir metin olmalıdır (index {idx}).",
                error_code="INVALID_CATEGORY",
                field="category_name",
                category_index=idx,
            )

        # Unicode-aware casefold ile duplicate kontrolü (dinamik adı mesaja yazmaz)
        casefolded_name = cat_name.casefold()
        if casefolded_name in seen_category_names:
            raise SocialCategoryOutputValidationError(
                "Kategori adı tekrarlanamaz.",
                error_code="DUPLICATE_CATEGORY_NAME",
                field="category_name",
                category_index=idx,
            )
        seen_category_names.add(casefolded_name)

        # 6b. category_type (dinamik tipi mesaja yazmaz)
        cat_type = item["category_type"]
        if (
            isinstance(cat_type, bool)
            or not isinstance(cat_type, str)
            or cat_type not in _CANONICAL_CATEGORY_SET
        ):
            raise SocialCategoryOutputValidationError(
                "Kategori tipi izin verilen değerlerden biri olmalıdır.",
                error_code="INVALID_CATEGORY",
                field="category_type",
                category_index=idx,
            )

        # 6c. description
        cat_desc = item["description"]
        if (
            isinstance(cat_desc, bool)
            or not isinstance(cat_desc, str)
            or len(cat_desc) == 0
            or cat_desc != cat_desc.strip()
            or len(cat_desc) > 2000
        ):
            raise SocialCategoryOutputValidationError(
                f"Açıklama başta/sonda boşluk olmayan 1-2000 karakter arası bir metin olmalıdır (index {idx}).",
                error_code="INVALID_CATEGORY",
                field="description",
                category_index=idx,
            )

        # 6d. relevance_score (güvenli taşma koruması, math.isnan/isinf öncesi float dönüşümü)
        score = item["relevance_score"]
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            raise SocialCategoryOutputValidationError(
                "relevance_score 0.0 ile 1.0 arasında bir sayı olmalıdır.",
                error_code="INVALID_CATEGORY",
                field="relevance_score",
                category_index=idx,
            )

        try:
            score_float = float(score)
        except (OverflowError, ValueError, TypeError):
            raise SocialCategoryOutputValidationError(
                "relevance_score geçerli bir sonlu sayı olmalıdır.",
                error_code="INVALID_CATEGORY",
                field="relevance_score",
                category_index=idx,
            )

        if not math.isfinite(score_float):
            raise SocialCategoryOutputValidationError(
                "relevance_score sonlu bir sayı olmalıdır.",
                error_code="INVALID_CATEGORY",
                field="relevance_score",
                category_index=idx,
            )

        if not (0.0 <= score_float <= 1.0):
            raise SocialCategoryOutputValidationError(
                "relevance_score 0.0 ile 1.0 arasında bir sayı olmalıdır.",
                error_code="INVALID_CATEGORY",
                field="relevance_score",
                category_index=idx,
            )

        # 6e. suggested_keyword_ids
        kw_ids_raw = item["suggested_keyword_ids"]
        if not isinstance(kw_ids_raw, list) or len(kw_ids_raw) == 0:
            raise SocialCategoryOutputValidationError(
                f"suggested_keyword_ids en az bir eleman içeren bir dizi olmalıdır (index {idx}).",
                error_code="INVALID_KEYWORD_REFERENCE",
                field="suggested_keyword_ids",
                category_index=idx,
            )

        seen_kw_ids: set[int] = set()
        clean_kw_ids: list[int] = []

        for kw_idx, kw_val in enumerate(kw_ids_raw):
            if isinstance(kw_val, bool) or not isinstance(kw_val, int) or kw_val <= 0:
                raise SocialCategoryOutputValidationError(
                    "Önerilen keyword ID pozitif strict tamsayı olmalıdır.",
                    error_code="INVALID_KEYWORD_REFERENCE",
                    field="suggested_keyword_ids",
                    category_index=idx,
                )

            if kw_val in seen_kw_ids:
                raise SocialCategoryOutputValidationError(
                    "Önerilen keyword ID listesinde mükerrer değer bulunamaz.",
                    error_code="INVALID_KEYWORD_REFERENCE",
                    field="suggested_keyword_ids",
                    category_index=idx,
                )
            seen_kw_ids.add(kw_val)

            if kw_val not in allowed_set:
                raise SocialCategoryOutputValidationError(
                    "Önerilen keyword ID brief havuzunda bulunamadı.",
                    error_code="INVALID_KEYWORD_REFERENCE",
                    field="suggested_keyword_ids",
                    category_index=idx,
                )
            clean_kw_ids.append(kw_val)

        validated_categories.append(
            ValidatedSocialCategory(
                category_name=cat_name,
                category_type=cat_type,
                description=cat_desc,
                relevance_score=score_float,
                suggested_keyword_ids=tuple(clean_kw_ids),
            )
        )

    return tuple(validated_categories)
