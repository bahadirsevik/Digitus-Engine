# -*- coding: utf-8 -*-
"""Saf Fikir Çıktı Sözleşmesi ve Fail-Closed Doğrulayıcı (F1-F.2).

Bu modül sosyal brief akışında AI'dan dönecek fikir (idea) JSON çıktısının
katı ve fail-closed biçimde doğrulanmasını, immutable DTO'lara dönüştürülmesini
ve Gemini response schema'sının dinamik ve izole olarak üretilmesini sağlar.

Kurallar (plan_social_brief_akisi.md rev.4 §3 & §5):
- Stdlib, canonical format matrix ve paylaşılan AI JSON kurtarma (ai_json) dışında
  harici kütüphane kullanmaz. Önce strict json.loads; yalnız markdown fence ile
  sarılı çıktı ai_json.parse_ai_json_object ile tek kez kurtarılır.
- DB/ORM/Pydantic/AI bağımlılığı içermez.
- Fikir aşamasında hook, caption, hashtag veya senaryo bulunmaz; bunlar içerik aşamasına aittir.
- Bilinmeyen/uymayan fikir sessizce çevrilmez ve fallback üretilmez. Üretim yolu
  (filter_social_idea_output) uymayan fikri tek tek ATAR ve sayar; yalnız yazım
  normalizasyonu yapılır ("X" -> twitter, büyük/küçük harf). Yapısal bozukluk
  (JSON değil, 'ideas' listesi yok) kategori düzeyi hatadır.
- Hata mesajlarında hiçbir ham AI metni, başlık, açıklama veya ham ID sızdırılmaz.
"""
from __future__ import annotations

import copy
import json
import math
from dataclasses import dataclass
from typing import Any

from app.core.channel.ai_json import AIJsonParseError, parse_ai_json_object
from app.generators.social.format_matrix import get_platform_format


class SocialIdeaOutputValidationError(ValueError):
    """Fikir AI çıktısı ve sözleşme doğrulama hatası."""

    def __init__(
        self,
        message: str,
        *,
        error_code: str,
        field: str | None = None,
        idea_index: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.field = field
        self.idea_index = idea_index

    def __str__(self) -> str:
        parts = [f"[{self.error_code}]", self.message]
        if self.field is not None:
            parts.append(f"(field={self.field})")
        if self.idea_index is not None:
            parts.append(f"(index={self.idea_index})")
        return " ".join(parts)

    def __repr__(self) -> str:
        return (
            f"SocialIdeaOutputValidationError(error_code={self.error_code!r}, "
            f"message={self.message!r}, field={self.field!r}, "
            f"idea_index={self.idea_index!r})"
        )


@dataclass(frozen=True)
class IdeaTargetSpec:
    """Brief hedefi ve fikir üretim kotası spesifikasyonu."""

    target_id: int
    platform: str
    content_format: str
    requested_count: int


@dataclass(frozen=True)
class ValidatedSocialIdea:
    """Doğrulanmış ve dondurulmuş sosyal içerik fikir nesnesi."""

    target_id: int
    primary_keyword_id: int
    idea_title: str
    idea_description: str
    target_platform: str
    content_format: str
    trend_alignment: float


REQUIRED_IDEA_FIELDS: frozenset[str] = frozenset({
    "target_id",
    "primary_keyword_id",
    "idea_title",
    "idea_description",
    "target_platform",
    "content_format",
    "trend_alignment",
})


def validate_idea_target_specs(target_specs: tuple[IdeaTargetSpec, ...] | Any) -> None:
    """target_specs koleksiyonunu fail-closed kurallarla doğrular."""
    if type(target_specs) is not tuple:
        raise SocialIdeaOutputValidationError(
            "target_specs must be a tuple.",
            error_code="IDEA_CONTRACT_INVALID_TARGETS",
            field="target_specs",
        )
    if len(target_specs) < 1 or len(target_specs) > 6:
        raise SocialIdeaOutputValidationError(
            "target_specs must contain between 1 and 6 targets.",
            error_code="IDEA_CONTRACT_INVALID_TARGETS",
            field="target_specs",
        )

    seen_targets: set[int] = set()
    total_quota = 0

    for spec in target_specs:
        if type(spec) is not IdeaTargetSpec:
            raise SocialIdeaOutputValidationError(
                "Each item in target_specs must be an IdeaTargetSpec instance.",
                error_code="IDEA_CONTRACT_INVALID_TARGETS",
                field="target_specs",
            )
        if isinstance(spec.target_id, bool) or type(spec.target_id) is not int or spec.target_id <= 0:
            raise SocialIdeaOutputValidationError(
                "target_id must be a positive integer.",
                error_code="IDEA_CONTRACT_INVALID_TARGETS",
                field="target_id",
            )
        if spec.target_id in seen_targets:
            raise SocialIdeaOutputValidationError(
                "Duplicate target ID detected in target_specs.",
                error_code="IDEA_CONTRACT_DUPLICATE_TARGET",
                field="target_specs",
            )
        seen_targets.add(spec.target_id)

        if isinstance(spec.requested_count, bool) or type(spec.requested_count) is not int or spec.requested_count <= 0:
            raise SocialIdeaOutputValidationError(
                "requested_count must be a positive integer.",
                error_code="IDEA_CONTRACT_INVALID_QUOTA",
                field="requested_count",
            )
        total_quota += spec.requested_count

        if type(spec.platform) is not str or len(spec.platform) == 0 or spec.platform != spec.platform.strip():
            raise SocialIdeaOutputValidationError(
                "platform must be a non-empty trimmed string.",
                error_code="IDEA_CONTRACT_INVALID_PLATFORM_FORMAT",
                field="platform",
            )
        if type(spec.content_format) is not str or len(spec.content_format) == 0 or spec.content_format != spec.content_format.strip():
            raise SocialIdeaOutputValidationError(
                "content_format must be a non-empty trimmed string.",
                error_code="IDEA_CONTRACT_INVALID_PLATFORM_FORMAT",
                field="content_format",
            )
        if get_platform_format(spec.platform, spec.content_format) is None:
            raise SocialIdeaOutputValidationError(
                "Platform and format combination is not in canonical format matrix.",
                error_code="IDEA_CONTRACT_INVALID_PLATFORM_FORMAT",
                field="content_format",
            )

    if total_quota < 1 or total_quota > 30:
        raise SocialIdeaOutputValidationError(
            "Total requested ideas count must be between 1 and 30.",
            error_code="IDEA_CONTRACT_INVALID_QUOTA",
            field="requested_count",
        )


# Backward compatibility alias
_validate_target_specs = validate_idea_target_specs


def _validate_allowed_keyword_ids(allowed_keyword_ids: Any) -> None:
    """allowed_keyword_ids koleksiyonunu fail-closed kurallarla doğrular."""
    if type(allowed_keyword_ids) is not tuple:
        raise SocialIdeaOutputValidationError(
            "allowed_keyword_ids must be a tuple.",
            error_code="IDEA_CONTRACT_INVALID_KEYWORDS",
            field="allowed_keyword_ids",
        )
    if len(allowed_keyword_ids) < 1 or len(allowed_keyword_ids) > 5:
        raise SocialIdeaOutputValidationError(
            "allowed_keyword_ids must contain between 1 and 5 keywords.",
            error_code="IDEA_CONTRACT_INVALID_KEYWORDS",
            field="allowed_keyword_ids",
        )

    seen_kw: set[int] = set()
    for kw_id in allowed_keyword_ids:
        if isinstance(kw_id, bool) or type(kw_id) is not int or kw_id <= 0:
            raise SocialIdeaOutputValidationError(
                "Keyword ID must be a positive integer.",
                error_code="IDEA_CONTRACT_INVALID_KEYWORDS",
                field="allowed_keyword_ids",
            )
        if kw_id in seen_kw:
            raise SocialIdeaOutputValidationError(
                "Duplicate keyword ID detected in allowed_keyword_ids.",
                error_code="IDEA_CONTRACT_DUPLICATE_KEYWORD",
                field="allowed_keyword_ids",
            )
        seen_kw.add(kw_id)


def _parse_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """JSON nesnesindeki duplicate anahtarları tespit edip fail-closed reddeder."""
    res: dict[str, Any] = {}
    for k, v in pairs:
        if k in res:
            raise ValueError(f"Duplicate JSON key")
        res[k] = v
    return res


def _reject_constant(c: str) -> None:
    """NaN, Infinity veya -Infinity gibi JSON dışı sabitleri reddeder."""
    raise ValueError(f"JSON constant not allowed: {c}")


def build_social_idea_response_schema(
    *,
    target_specs: tuple[IdeaTargetSpec, ...],
    allowed_keyword_ids: tuple[int, ...],
) -> dict[str, Any]:
    """Gemini API için dinamik, izole ve derin-kopyalı response schema üretir."""
    validate_idea_target_specs(target_specs)
    _validate_allowed_keyword_ids(allowed_keyword_ids)

    total_requested = sum(spec.requested_count for spec in target_specs)
    allowed_target_ids = [spec.target_id for spec in target_specs]
    allowed_kw_ids = list(allowed_keyword_ids)

    seen_plat: set[str] = set()
    unique_platforms: list[str] = []
    for spec in target_specs:
        if spec.platform not in seen_plat:
            seen_plat.add(spec.platform)
            unique_platforms.append(spec.platform)

    seen_fmt: set[str] = set()
    unique_formats: list[str] = []
    for spec in target_specs:
        if spec.content_format not in seen_fmt:
            seen_fmt.add(spec.content_format)
            unique_formats.append(spec.content_format)

    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "ideas": {
                "type": "array",
                "minItems": total_requested,
                "maxItems": total_requested,
                "items": {
                    "type": "object",
                    "properties": {
                        "target_id": {
                            "type": "integer",
                            "enum": allowed_target_ids,
                            "description": "Brief hedef kimliği",
                        },
                        "primary_keyword_id": {
                            "type": "integer",
                            "enum": allowed_kw_ids,
                            "description": "Fikrinin bağlandığı brief anahtar kelime kimliği",
                        },
                        "idea_title": {
                            "type": "string",
                            "description": "Fikir başlığı (en fazla 200 karakter)",
                        },
                        "idea_description": {
                            "type": "string",
                            "description": "Fikir açıklaması ve stratejik gerekçe (en fazla 2000 karakter)",
                        },
                        "target_platform": {
                            "type": "string",
                            "enum": unique_platforms,
                            "description": "Kanonik platform",
                        },
                        "content_format": {
                            "type": "string",
                            "enum": unique_formats,
                            "description": "Kanonik içerik formatı",
                        },
                        "trend_alignment": {
                            "type": "number",
                            "description": "0.0 ile 1.0 arasında trend uyum skoru",
                        },
                    },
                    "required": [
                        "target_id",
                        "primary_keyword_id",
                        "idea_title",
                        "idea_description",
                        "target_platform",
                        "content_format",
                        "trend_alignment",
                    ],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["ideas"],
        "additionalProperties": False,
    }
    return copy.deepcopy(schema)


# Yazım normalizasyonu (plan §3.4): yalnız büyük/küçük harf, baştaki/sondaki
# boşluk ve bilinen platform takma adları. Uymayan değer ÇEVRİLMEZ; fikir atılır.
_PLATFORM_SPELLING_ALIASES: dict[str, str] = {
    "x": "twitter",
    "twitter": "twitter",
}

# Brief dışı (off-brief) sayılan fikir hata kodları: fikir biçimce geçerli ama
# brief'in hedef / kelime / platform-format kümesine uymuyor.
OFF_BRIEF_ERROR_CODES: frozenset[str] = frozenset({
    "IDEA_OUTPUT_TARGET_NOT_ALLOWED",
    "IDEA_OUTPUT_KEYWORD_NOT_ALLOWED",
    "IDEA_OUTPUT_TARGET_MISMATCH",
})


def normalize_idea_platform_spelling(value: str) -> str:
    """Platform yazımını normalize eder ("X"/"x"/"Twitter" -> "twitter")."""
    lowered = value.strip().lower()
    return _PLATFORM_SPELLING_ALIASES.get(lowered, lowered)


def normalize_idea_format_spelling(value: str) -> str:
    """Format yazımını normalize eder (yalnız boşluk ve büyük/küçük harf)."""
    return value.strip().lower()


@dataclass(frozen=True)
class SocialIdeaFilterResult:
    """Fikir-bazlı süzme sonucu (immutable).

    - ideas: brief içinde kalan ve hedef kotası içinde tutulan fikirler (çıktı sırasıyla).
    - off_brief_dropped: brief dışı hedef/kelime/platform-format nedeniyle atılanlar.
    - invalid_dropped: alan/tip/metin/skor kuralına uymadığı için atılanlar.
    - over_quota_dropped: hedef kotasını aşan (ilk N tutulduktan sonra kalan) fikirler.
    """

    ideas: tuple[ValidatedSocialIdea, ...]
    off_brief_dropped: int
    invalid_dropped: int
    over_quota_dropped: int

    @property
    def total_dropped(self) -> int:
        return self.off_brief_dropped + self.invalid_dropped + self.over_quota_dropped


def _parse_social_idea_output_root(raw_json: Any) -> list[Any]:
    """Ham çıktının yapısal (kategori düzeyi) doğrulaması.

    JSON değil, kök nesne değil, 'ideas' listesi yok veya kökte beklenmeyen alan
    varsa SocialIdeaOutputValidationError fırlatır. Fikirlerin kendisine bakmaz.
    """
    if type(raw_json) is not str:
        raise SocialIdeaOutputValidationError(
            "raw_json must be a string.",
            error_code="IDEA_OUTPUT_INVALID_JSON",
        )

    try:
        parsed = json.loads(
            raw_json,
            object_pairs_hook=_parse_pairs,
            parse_constant=_reject_constant,
        )
    except (json.JSONDecodeError, ValueError) as exc:
        # Kategori/içerik sözleşmeleriyle aynı dar kapı: yalnız markdown fence
        # ile SARILMIŞ metin paylaşılan AI JSON kurtarma zincirinden TEK kez
        # geçer (CLAUDE.md: çıplak json.loads yok). Prose-sarmalı veya başka
        # bozuk metin kurtarılmaz; bu yolda duplicate-key/NaN reddi uygulanmaz.
        stripped = raw_json.strip()
        if not (stripped.startswith("```") or stripped.endswith("```")):
            raise SocialIdeaOutputValidationError(
                "Invalid JSON format or syntax.",
                error_code="IDEA_OUTPUT_INVALID_JSON",
            ) from exc
        try:
            parsed = parse_ai_json_object(raw_json, required_fields=("ideas",))
        except AIJsonParseError as recovery_exc:
            raise SocialIdeaOutputValidationError(
                "Invalid JSON format or syntax.",
                error_code="IDEA_OUTPUT_INVALID_JSON",
            ) from recovery_exc

    if type(parsed) is not dict:
        raise SocialIdeaOutputValidationError(
            "Root JSON must be an object.",
            error_code="IDEA_OUTPUT_INVALID_ROOT",
        )

    root_keys = set(parsed.keys())
    if "ideas" not in root_keys:
        raise SocialIdeaOutputValidationError(
            "Root JSON must contain 'ideas' field.",
            error_code="IDEA_OUTPUT_INVALID_ROOT",
            field="ideas",
        )

    extra_root_keys = root_keys - {"ideas"}
    if extra_root_keys:
        first_extra = sorted(extra_root_keys)[0]
        raise SocialIdeaOutputValidationError(
            "Unexpected field in root JSON.",
            error_code="IDEA_OUTPUT_UNEXPECTED_FIELD",
            field=first_extra,
        )

    raw_ideas = parsed["ideas"]
    if type(raw_ideas) is not list:
        raise SocialIdeaOutputValidationError(
            "'ideas' field must be a list.",
            error_code="IDEA_OUTPUT_INVALID_ROOT",
            field="ideas",
        )
    return raw_ideas


def validate_idea_against_brief(
    idea: Any,
    *,
    idea_index: int,
    spec_by_id: dict[int, IdeaTargetSpec],
    allowed_keyword_ids: frozenset[int] | set[int],
) -> ValidatedSocialIdea:
    """Tek bir fikri brief'e karşı kapalı-güvenli doğrular (plan §3.3 merkezi doğrulayıcı).

    Gemini ve DeepSeek çıktıları aynı doğrulayıcıdan geçer. Uymayan fikir
    SocialIdeaOutputValidationError ile reddedilir; çağıran onu ATAR, çevirmez.
    Yalnız yazım normalizasyonu yapılır (platform/format büyük-küçük harf, "X" -> twitter).
    """
    idx = idea_index
    if type(idea) is not dict:
        raise SocialIdeaOutputValidationError(
            "Idea item must be a JSON object.",
            error_code="IDEA_OUTPUT_INVALID_IDEA",
            idea_index=idx,
        )

    idea_keys = set(idea.keys())
    extra_keys = idea_keys - REQUIRED_IDEA_FIELDS
    if extra_keys:
        first_extra = sorted(extra_keys)[0]
        raise SocialIdeaOutputValidationError(
            "Unexpected field in idea object.",
            error_code="IDEA_OUTPUT_UNEXPECTED_FIELD",
            field=first_extra,
            idea_index=idx,
        )

    missing_keys = REQUIRED_IDEA_FIELDS - idea_keys
    if missing_keys:
        first_missing = sorted(missing_keys)[0]
        raise SocialIdeaOutputValidationError(
            "Missing required field in idea object.",
            error_code="IDEA_OUTPUT_INVALID_IDEA",
            field=first_missing,
            idea_index=idx,
        )

    # 1. target_id
    tid = idea["target_id"]
    if isinstance(tid, bool) or type(tid) is not int or tid <= 0:
        raise SocialIdeaOutputValidationError(
            "Invalid target ID format.",
            error_code="IDEA_OUTPUT_INVALID_IDEA",
            field="target_id",
            idea_index=idx,
        )
    if tid not in spec_by_id:
        raise SocialIdeaOutputValidationError(
            "Target ID is not in allowed target specs.",
            error_code="IDEA_OUTPUT_TARGET_NOT_ALLOWED",
            field="target_id",
            idea_index=idx,
        )

    # 2. primary_keyword_id
    kid = idea["primary_keyword_id"]
    if isinstance(kid, bool) or type(kid) is not int or kid <= 0:
        raise SocialIdeaOutputValidationError(
            "Invalid primary keyword ID format.",
            error_code="IDEA_OUTPUT_INVALID_IDEA",
            field="primary_keyword_id",
            idea_index=idx,
        )
    if kid not in allowed_keyword_ids:
        raise SocialIdeaOutputValidationError(
            "Primary keyword ID is not in allowed keyword list.",
            error_code="IDEA_OUTPUT_KEYWORD_NOT_ALLOWED",
            field="primary_keyword_id",
            idea_index=idx,
        )

    # 3. target_platform (yalnız yazım normalizasyonu; çeviri yok)
    plat = idea["target_platform"]
    if type(plat) is not str:
        raise SocialIdeaOutputValidationError(
            "target_platform must be a string.",
            error_code="IDEA_OUTPUT_INVALID_IDEA",
            field="target_platform",
            idea_index=idx,
        )
    spec = spec_by_id[tid]
    if normalize_idea_platform_spelling(plat) != spec.platform:
        raise SocialIdeaOutputValidationError(
            "target_platform does not match target specification.",
            error_code="IDEA_OUTPUT_TARGET_MISMATCH",
            field="target_platform",
            idea_index=idx,
        )

    # 4. content_format
    fmt = idea["content_format"]
    if type(fmt) is not str:
        raise SocialIdeaOutputValidationError(
            "content_format must be a string.",
            error_code="IDEA_OUTPUT_INVALID_IDEA",
            field="content_format",
            idea_index=idx,
        )
    if normalize_idea_format_spelling(fmt) != spec.content_format:
        raise SocialIdeaOutputValidationError(
            "content_format does not match target specification.",
            error_code="IDEA_OUTPUT_TARGET_MISMATCH",
            field="content_format",
            idea_index=idx,
        )

    # 5. idea_title
    title = idea["idea_title"]
    if type(title) is not str:
        raise SocialIdeaOutputValidationError(
            "idea_title must be a string.",
            error_code="IDEA_OUTPUT_INVALID_TEXT",
            field="idea_title",
            idea_index=idx,
        )
    if len(title) == 0 or title.strip() == "":
        raise SocialIdeaOutputValidationError(
            "idea_title cannot be empty or whitespace only.",
            error_code="IDEA_OUTPUT_INVALID_TEXT",
            field="idea_title",
            idea_index=idx,
        )
    if title != title.strip():
        raise SocialIdeaOutputValidationError(
            "idea_title cannot have leading or trailing whitespace.",
            error_code="IDEA_OUTPUT_INVALID_TEXT",
            field="idea_title",
            idea_index=idx,
        )
    if len(title) > 200:
        raise SocialIdeaOutputValidationError(
            "idea_title exceeds maximum length of 200 characters.",
            error_code="IDEA_OUTPUT_INVALID_TEXT",
            field="idea_title",
            idea_index=idx,
        )

    # 6. idea_description
    desc = idea["idea_description"]
    if type(desc) is not str:
        raise SocialIdeaOutputValidationError(
            "idea_description must be a string.",
            error_code="IDEA_OUTPUT_INVALID_TEXT",
            field="idea_description",
            idea_index=idx,
        )
    if len(desc) == 0 or desc.strip() == "":
        raise SocialIdeaOutputValidationError(
            "idea_description cannot be empty or whitespace only.",
            error_code="IDEA_OUTPUT_INVALID_TEXT",
            field="idea_description",
            idea_index=idx,
        )
    if desc != desc.strip():
        raise SocialIdeaOutputValidationError(
            "idea_description cannot have leading or trailing whitespace.",
            error_code="IDEA_OUTPUT_INVALID_TEXT",
            field="idea_description",
            idea_index=idx,
        )
    if len(desc) > 2000:
        raise SocialIdeaOutputValidationError(
            "idea_description exceeds maximum length of 2000 characters.",
            error_code="IDEA_OUTPUT_INVALID_TEXT",
            field="idea_description",
            idea_index=idx,
        )

    # 7. trend_alignment
    raw_score = idea["trend_alignment"]
    if isinstance(raw_score, bool) or type(raw_score) not in (int, float):
        raise SocialIdeaOutputValidationError(
            "trend_alignment must be a number.",
            error_code="IDEA_OUTPUT_INVALID_SCORE",
            field="trend_alignment",
            idea_index=idx,
        )
    try:
        score = float(raw_score)
    except (OverflowError, ValueError, TypeError):
        raise SocialIdeaOutputValidationError(
            "trend_alignment score cannot be converted to float.",
            error_code="IDEA_OUTPUT_INVALID_SCORE",
            field="trend_alignment",
            idea_index=idx,
        )
    if not math.isfinite(score):
        raise SocialIdeaOutputValidationError(
            "trend_alignment must be a finite number.",
            error_code="IDEA_OUTPUT_INVALID_SCORE",
            field="trend_alignment",
            idea_index=idx,
        )
    if score < 0.0 or score > 1.0:
        raise SocialIdeaOutputValidationError(
            "trend_alignment must be between 0.0 and 1.0.",
            error_code="IDEA_OUTPUT_INVALID_SCORE",
            field="trend_alignment",
            idea_index=idx,
        )

    return ValidatedSocialIdea(
        target_id=tid,
        primary_keyword_id=kid,
        idea_title=title,
        idea_description=desc,
        # Kanonik değerler hedeften alınır (yazım normalizasyonu sonrası birebir eşit)
        target_platform=spec.platform,
        content_format=spec.content_format,
        trend_alignment=score,
    )


def filter_social_idea_output(
    raw_json: str,
    *,
    target_specs: tuple[IdeaTargetSpec, ...],
    allowed_keyword_ids: tuple[int, ...],
) -> SocialIdeaFilterResult:
    """AI fikir çıktısını fikir-bazlı süzer (plan §3.4: uymayan fikir atılır, çevrilmez).

    - Yapısal bozukluk (JSON değil, 'ideas' listesi yok) kategori düzeyi hatadır:
      SocialIdeaOutputValidationError fırlatılır.
    - Geçersiz / brief dışı fikir tek tek atılır ve sayılır.
    - Bir hedef için kotadan fazla geçerli fikir gelirse çıktı sırasındaki ilk N
      tutulur, kalanı over_quota_dropped olarak sayılır.
    - Eksik hedefler hata değildir; brief geneli tamamlama turu tamamlar.
    """
    validate_idea_target_specs(target_specs)
    _validate_allowed_keyword_ids(allowed_keyword_ids)

    raw_ideas = _parse_social_idea_output_root(raw_json)

    spec_by_id = {spec.target_id: spec for spec in target_specs}
    allowed_kw_set = frozenset(allowed_keyword_ids)
    kept_per_target: dict[int, int] = {spec.target_id: 0 for spec in target_specs}

    kept: list[ValidatedSocialIdea] = []
    off_brief = 0
    invalid = 0
    over_quota = 0

    for idx, raw_idea in enumerate(raw_ideas):
        try:
            item = validate_idea_against_brief(
                raw_idea,
                idea_index=idx,
                spec_by_id=spec_by_id,
                allowed_keyword_ids=allowed_kw_set,
            )
        except SocialIdeaOutputValidationError as exc:
            if exc.error_code in OFF_BRIEF_ERROR_CODES:
                off_brief += 1
            else:
                invalid += 1
            continue

        if kept_per_target[item.target_id] >= spec_by_id[item.target_id].requested_count:
            over_quota += 1
            continue
        kept_per_target[item.target_id] += 1
        kept.append(item)

    return SocialIdeaFilterResult(
        ideas=tuple(kept),
        off_brief_dropped=off_brief,
        invalid_dropped=invalid,
        over_quota_dropped=over_quota,
    )


def validate_social_idea_output(
    raw_json: str,
    *,
    target_specs: tuple[IdeaTargetSpec, ...],
    allowed_keyword_ids: tuple[int, ...],
) -> tuple[ValidatedSocialIdea, ...]:
    """Tam-uyum (strict) doğrulayıcı: ilk uymayan fikirde veya kota uyuşmazlığında hata verir.

    Üretim yolu bunun yerine ``filter_social_idea_output`` kullanır (uymayan fikir
    atılır, geri kalanı korunur). Bu fonksiyon bir çıktının sözleşmeye TAMAMEN
    uyduğunu kanıtlamak isteyen kontroller içindir ve aynı merkezi doğrulayıcıyı
    (``validate_idea_against_brief``) paylaşır.
    """
    validate_idea_target_specs(target_specs)
    _validate_allowed_keyword_ids(allowed_keyword_ids)

    raw_ideas = _parse_social_idea_output_root(raw_json)

    spec_by_id = {spec.target_id: spec for spec in target_specs}
    allowed_kw_set = frozenset(allowed_keyword_ids)

    validated_ideas: list[ValidatedSocialIdea] = [
        validate_idea_against_brief(
            idea,
            idea_index=idx,
            spec_by_id=spec_by_id,
            allowed_keyword_ids=allowed_kw_set,
        )
        for idx, idea in enumerate(raw_ideas)
    ]

    # Kota kontrolü
    total_expected = sum(spec.requested_count for spec in target_specs)
    if len(validated_ideas) != total_expected:
        raise SocialIdeaOutputValidationError(
            "Total ideas count does not match requested quota.",
            error_code="IDEA_OUTPUT_QUOTA_MISMATCH",
            field="ideas",
        )

    target_counts: dict[int, int] = {spec.target_id: 0 for spec in target_specs}
    for item in validated_ideas:
        target_counts[item.target_id] += 1

    for spec in target_specs:
        if target_counts[spec.target_id] != spec.requested_count:
            raise SocialIdeaOutputValidationError(
                "Idea count for target does not match requested quota.",
                error_code="IDEA_OUTPUT_QUOTA_MISMATCH",
                field="target_id",
            )

    return tuple(validated_ideas)
