# -*- coding: utf-8 -*-
"""Unit tests for pure idea contract and fail-closed validator (F1-F.2).

Doğrulanan test grupları:
A. Geçerli çıktı:
   - Birden fazla target ve farklı kotalar
   - Doğru target/keyword/platform/format eşleşmesi
   - Çıktı sırasının korunması
   - Frozen dataclass davranışı (mutation koruması)

B. Target specs:
   - Tuple dışı değer (list/set)
   - Boş ve 7 öğeli tuple
   - Forged/non-dataclass öğe
   - Bool/string/float/0/negatif target_id
   - Duplicate target_id
   - Geçersiz requested_count (0, negatif, bool, str)
   - Toplam 30 kabulü
   - Toplam 31 reddi (6 target ile 5*5 + 6 = 31)
   - Canonical olmayan platform/format ("X", "Instagram", " instagram ", "tiktok/post")
   - Geçerli canonical çiftler

C. Keywords:
   - Tuple dışı değer
   - Boş ve 6 öğeli tuple
   - Bool/string/float/0/negatif ID
   - Duplicate keyword ID
   - Ham ID mesajda sızdırılmaz

D. JSON:
   - Malformed JSON
   - Markdown fence
   - Leading/trailing açıklama
   - Trailing comma
   - Duplicate key
   - NaN ve Infinity
   - Root list
   - Eksik ideas
   - Extra root field
   - Idea extra field (hook, caption, reasoning vb.)
   - Eksik zorunlu alan

E. Referans güvenliği:
   - Brief dışı target_id
   - Brief dışı primary_keyword_id
   - Target'a uymayan platform
   - Target'a uymayan format
   - 'X' değerinin reddedilmesi ve 'twitter' değerinin kabulü
   - Uppercase/whitespace değerlerin reddedilmesi
   - Ham ID ve değerlerin mesajda sızdırılmaması

F. Metin ve skor:
   - Boş/whitespace title ve description
   - Başında/sonunda whitespace olan title ve description
   - 200/2000 sınırlarının tam kabulü
   - 201/2001 karakter reddi
   - Bool/string/null score reddi
   - NaN/Infinity reddi
   - 10**10000 ve -(10**10000) taşmalarının güvenli reddi
   - 0 ve 1 int/float kabulü
   - Aralık dışı değerler (-0.1, 1.1) reddi

G. Kota:
   - Toplam idea sayısı eksik/fazla
   - Target kotası eksik/fazla
   - Bütün target'ların tam kotayla kapsanması
   - Sonuçta kısmi liste veya fallback oluşmaması

H. Schema:
   - Enum değerleri doğruluğu
   - minItems=maxItems=toplam kota
   - required ve additionalProperties kuralları
   - İki schema çağrısı arasında mutation izolasyonu
"""
from __future__ import annotations

import json
import pytest

from app.core.social import (
    IdeaTargetSpec,
    SocialIdeaFilterResult,
    SocialIdeaOutputValidationError,
    ValidatedSocialIdea,
    build_social_idea_response_schema,
    filter_social_idea_output,
    validate_social_idea_output,
)


# ==================== YARDIMCI VERİLER ====================

DEFAULT_TARGETS = (
    IdeaTargetSpec(target_id=1, platform="instagram", content_format="carousel", requested_count=2),
    IdeaTargetSpec(target_id=2, platform="twitter", content_format="thread", requested_count=1),
)
DEFAULT_KEYWORDS = (10, 20, 30)


def _make_idea_dict(
    *,
    target_id: int = 1,
    primary_keyword_id: int = 10,
    idea_title: str = "Test Title",
    idea_description: str = "Test Description",
    target_platform: str = "instagram",
    content_format: str = "carousel",
    trend_alignment: float = 0.8,
) -> dict:
    return {
        "target_id": target_id,
        "primary_keyword_id": primary_keyword_id,
        "idea_title": idea_title,
        "idea_description": idea_description,
        "target_platform": target_platform,
        "content_format": content_format,
        "trend_alignment": trend_alignment,
    }


def _make_json(ideas: list[dict]) -> str:
    return json.dumps({"ideas": ideas})


# ==================== GRUP A: GEÇERLİ ÇIKTI ====================

def test_a01_valid_multiple_targets_and_quotas():
    """A1. Birden fazla target ve farklı kotalar başarıyla doğrulanır."""
    ideas_data = [
        _make_idea_dict(target_id=1, primary_keyword_id=10, idea_title="Idea 1", target_platform="instagram", content_format="carousel"),
        _make_idea_dict(target_id=1, primary_keyword_id=20, idea_title="Idea 2", target_platform="instagram", content_format="carousel"),
        _make_idea_dict(target_id=2, primary_keyword_id=30, idea_title="Idea 3", target_platform="twitter", content_format="thread"),
    ]
    raw = _make_json(ideas_data)
    result = validate_social_idea_output(raw, target_specs=DEFAULT_TARGETS, allowed_keyword_ids=DEFAULT_KEYWORDS)

    assert len(result) == 3
    assert isinstance(result, tuple)
    assert result[0].idea_title == "Idea 1"
    assert result[1].idea_title == "Idea 2"
    assert result[2].idea_title == "Idea 3"
    assert result[0].trend_alignment == 0.8


def test_a02_output_order_preserved():
    """A2. Modelden gelen çıktı sırası sonuç tuple'ında korunur."""
    ideas_data = [
        _make_idea_dict(target_id=2, primary_keyword_id=30, idea_title="First", target_platform="twitter", content_format="thread"),
        _make_idea_dict(target_id=1, primary_keyword_id=10, idea_title="Second", target_platform="instagram", content_format="carousel"),
        _make_idea_dict(target_id=1, primary_keyword_id=20, idea_title="Third", target_platform="instagram", content_format="carousel"),
    ]
    # target 2 (1 adet), target 1 (2 adet)
    specs = (
        IdeaTargetSpec(target_id=2, platform="twitter", content_format="thread", requested_count=1),
        IdeaTargetSpec(target_id=1, platform="instagram", content_format="carousel", requested_count=2),
    )
    raw = _make_json(ideas_data)
    result = validate_social_idea_output(raw, target_specs=specs, allowed_keyword_ids=DEFAULT_KEYWORDS)

    assert [r.idea_title for r in result] == ["First", "Second", "Third"]


def test_a03_frozen_dataclass_immutability():
    """A3. ValidatedSocialIdea ve IdeaTargetSpec immutable/frozen olmalıdır."""
    spec = IdeaTargetSpec(target_id=1, platform="instagram", content_format="post", requested_count=1)
    with pytest.raises(Exception):
        spec.target_id = 99  # type: ignore

    ideas_data = [_make_idea_dict(target_id=1, primary_keyword_id=10, target_platform="instagram", content_format="post")]
    result = validate_social_idea_output(_make_json(ideas_data), target_specs=(spec,), allowed_keyword_ids=(10,))
    with pytest.raises(Exception):
        result[0].idea_title = "Mutated"  # type: ignore


# ==================== GRUP B: TARGET SPECS ====================

def test_b01_target_specs_not_tuple_rejected():
    """B1. target_specs tuple değilse reddedilir."""
    with pytest.raises(SocialIdeaOutputValidationError) as exc:
        validate_social_idea_output("{}", target_specs=[DEFAULT_TARGETS[0]], allowed_keyword_ids=DEFAULT_KEYWORDS)  # type: ignore
    assert exc.value.error_code == "IDEA_CONTRACT_INVALID_TARGETS"
    assert exc.value.field == "target_specs"


def test_b02_target_specs_empty_and_exceeding_seven_rejected():
    """B2. target_specs boş veya 7 öğeli ise reddedilir."""
    with pytest.raises(SocialIdeaOutputValidationError) as exc1:
        validate_social_idea_output("{}", target_specs=(), allowed_keyword_ids=DEFAULT_KEYWORDS)
    assert exc1.value.error_code == "IDEA_CONTRACT_INVALID_TARGETS"
    assert exc1.value.field == "target_specs"

    seven_specs = tuple(
        IdeaTargetSpec(target_id=i, platform="instagram", content_format="post", requested_count=1)
        for i in range(1, 8)
    )
    with pytest.raises(SocialIdeaOutputValidationError) as exc2:
        validate_social_idea_output("{}", target_specs=seven_specs, allowed_keyword_ids=DEFAULT_KEYWORDS)
    assert exc2.value.error_code == "IDEA_CONTRACT_INVALID_TARGETS"
    assert exc2.value.field == "target_specs"


def test_b03_target_specs_non_dataclass_item_rejected():
    """B3. target_specs içinde IdeaTargetSpec olmayan öğe reddedilir."""
    with pytest.raises(SocialIdeaOutputValidationError) as exc:
        validate_social_idea_output("{}", target_specs=({"target_id": 1},), allowed_keyword_ids=DEFAULT_KEYWORDS)  # type: ignore
    assert exc.value.error_code == "IDEA_CONTRACT_INVALID_TARGETS"
    assert exc.value.field == "target_specs"


def test_b04_invalid_target_ids_rejected():
    """B4. Bool/string/float/0/negatif target_id reddedilir."""
    invalids = [True, False, "1", 1.5, 0, -1, None]
    for inv in invalids:
        spec = IdeaTargetSpec(target_id=inv, platform="instagram", content_format="post", requested_count=1)  # type: ignore
        with pytest.raises(SocialIdeaOutputValidationError) as exc:
            validate_social_idea_output("{}", target_specs=(spec,), allowed_keyword_ids=DEFAULT_KEYWORDS)
        assert exc.value.error_code == "IDEA_CONTRACT_INVALID_TARGETS"


def test_b05_duplicate_target_id_rejected():
    """B5. Mükerrer target_id reddedilir."""
    specs = (
        IdeaTargetSpec(target_id=1, platform="instagram", content_format="post", requested_count=1),
        IdeaTargetSpec(target_id=1, platform="instagram", content_format="carousel", requested_count=1),
    )
    with pytest.raises(SocialIdeaOutputValidationError) as exc:
        validate_social_idea_output("{}", target_specs=specs, allowed_keyword_ids=DEFAULT_KEYWORDS)
    assert exc.value.error_code == "IDEA_CONTRACT_DUPLICATE_TARGET"
    assert exc.value.field == "target_specs"


def test_b06_invalid_requested_count_rejected():
    """B6. 0, negatif, bool, float requested_count reddedilir."""
    invalids = [0, -1, True, False, 2.5, "3"]
    for inv in invalids:
        spec = IdeaTargetSpec(target_id=1, platform="instagram", content_format="post", requested_count=inv)  # type: ignore
        with pytest.raises(SocialIdeaOutputValidationError) as exc:
            validate_social_idea_output("{}", target_specs=(spec,), allowed_keyword_ids=DEFAULT_KEYWORDS)
        assert exc.value.error_code == "IDEA_CONTRACT_INVALID_QUOTA"
        assert exc.value.field == "requested_count"


def test_b07_total_quota_thirty_accepted_thirty_one_rejected():
    """B7. Toplam 30 kota kabul edilir, 31 kota IDEA_CONTRACT_INVALID_QUOTA ile reddedilir."""
    # 6 target * 5 = 30 -> geçerli
    specs_30 = tuple(
        IdeaTargetSpec(target_id=i, platform="instagram", content_format="post", requested_count=5)
        for i in range(1, 7)
    )
    schema_30 = build_social_idea_response_schema(target_specs=specs_30, allowed_keyword_ids=DEFAULT_KEYWORDS)
    assert schema_30["properties"]["ideas"]["minItems"] == 30

    # 6 target: 5*5 + 6 = 31 -> 31 kota reddedilir
    specs_31 = (
        IdeaTargetSpec(target_id=1, platform="instagram", content_format="post", requested_count=6),
        IdeaTargetSpec(target_id=2, platform="instagram", content_format="post", requested_count=5),
        IdeaTargetSpec(target_id=3, platform="instagram", content_format="post", requested_count=5),
        IdeaTargetSpec(target_id=4, platform="instagram", content_format="post", requested_count=5),
        IdeaTargetSpec(target_id=5, platform="instagram", content_format="post", requested_count=5),
        IdeaTargetSpec(target_id=6, platform="instagram", content_format="post", requested_count=5),
    )
    with pytest.raises(SocialIdeaOutputValidationError) as exc:
        build_social_idea_response_schema(target_specs=specs_31, allowed_keyword_ids=DEFAULT_KEYWORDS)
    assert exc.value.error_code == "IDEA_CONTRACT_INVALID_QUOTA"
    assert exc.value.field == "requested_count"


def test_b08_non_canonical_platform_format_rejected():
    """B8. Canonical olmayan platform ve format kombinasyonları reddedilir."""
    non_canonical = [
        ("X", "post"),            # Canonical 'twitter'
        ("Instagram", "post"),    # Küçük harf olmalı
        (" instagram ", "post"),  # Trim edilmemiş
        ("tiktok", "carousel"),   # TikTok'ta carousel yok
        ("twitter", "reels"),     # Twitter'da reels yok
        ("", "post"),             # Boş platform
        ("instagram", ""),        # Boş format
    ]
    for plat, fmt in non_canonical:
        spec = IdeaTargetSpec(target_id=1, platform=plat, content_format=fmt, requested_count=1)
        with pytest.raises(SocialIdeaOutputValidationError) as exc:
            validate_social_idea_output("{}", target_specs=(spec,), allowed_keyword_ids=DEFAULT_KEYWORDS)
        assert exc.value.error_code == "IDEA_CONTRACT_INVALID_PLATFORM_FORMAT"


def test_b09_valid_canonical_pairs():
    """B9. Kanonik platform-format çiftleri kabul edilir."""
    canonicals = [
        ("instagram", "post"),
        ("instagram", "carousel"),
        ("instagram", "reels"),
        ("instagram", "story"),
        ("tiktok", "short"),
        ("twitter", "post"),
        ("twitter", "thread"),
        ("twitter", "video"),
        ("linkedin", "post"),
        ("linkedin", "carousel"),
        ("linkedin", "video"),
        ("youtube", "short"),
        ("youtube", "video"),
    ]
    for i, (plat, fmt) in enumerate(canonicals, start=1):
        spec = IdeaTargetSpec(target_id=1, platform=plat, content_format=fmt, requested_count=1)
        schema = build_social_idea_response_schema(target_specs=(spec,), allowed_keyword_ids=(10,))
        assert schema["type"] == "object"


# ==================== GRUP C: KEYWORDS ====================

def test_c01_keywords_not_tuple_rejected():
    """C1. allowed_keyword_ids tuple değilse reddedilir."""
    with pytest.raises(SocialIdeaOutputValidationError) as exc:
        validate_social_idea_output("{}", target_specs=DEFAULT_TARGETS, allowed_keyword_ids=[10, 20])  # type: ignore
    assert exc.value.error_code == "IDEA_CONTRACT_INVALID_KEYWORDS"
    assert exc.value.field == "allowed_keyword_ids"


def test_c02_keywords_empty_and_six_items_rejected():
    """C2. allowed_keyword_ids boş veya 6 elemanlı ise reddedilir."""
    with pytest.raises(SocialIdeaOutputValidationError) as exc1:
        validate_social_idea_output("{}", target_specs=DEFAULT_TARGETS, allowed_keyword_ids=())
    assert exc1.value.error_code == "IDEA_CONTRACT_INVALID_KEYWORDS"

    with pytest.raises(SocialIdeaOutputValidationError) as exc2:
        validate_social_idea_output("{}", target_specs=DEFAULT_TARGETS, allowed_keyword_ids=(1, 2, 3, 4, 5, 6))
    assert exc2.value.error_code == "IDEA_CONTRACT_INVALID_KEYWORDS"


def test_c03_invalid_keyword_ids_rejected():
    """C3. Bool/string/float/0/negatif keyword ID'ler reddedilir."""
    invalids = [True, False, "10", 10.5, 0, -5, None]
    for inv in invalids:
        with pytest.raises(SocialIdeaOutputValidationError) as exc:
            validate_social_idea_output("{}", target_specs=DEFAULT_TARGETS, allowed_keyword_ids=(inv,))  # type: ignore
        assert exc.value.error_code == "IDEA_CONTRACT_INVALID_KEYWORDS"


def test_c04_duplicate_keyword_id_rejected():
    """C4. Duplicate keyword ID reddedilir."""
    with pytest.raises(SocialIdeaOutputValidationError) as exc:
        validate_social_idea_output("{}", target_specs=DEFAULT_TARGETS, allowed_keyword_ids=(10, 20, 10))
    assert exc.value.error_code == "IDEA_CONTRACT_DUPLICATE_KEYWORD"
    assert exc.value.field == "allowed_keyword_ids"
    assert "10" not in exc.value.message  # Ham ID sızdırılmaz


# ==================== GRUP D: JSON PARSING ====================

def test_d01_malformed_json_rejected():
    """D1. Malformed JSON IDEA_OUTPUT_INVALID_JSON ile reddedilir."""
    with pytest.raises(SocialIdeaOutputValidationError) as exc:
        validate_social_idea_output("{bad json", target_specs=DEFAULT_TARGETS, allowed_keyword_ids=DEFAULT_KEYWORDS)
    assert exc.value.error_code == "IDEA_OUTPUT_INVALID_JSON"


def test_d02_markdown_fence_recovered_and_accepted():
    """D2. Markdown fence ile sarılı geçerli JSON ai_json zinciriyle kurtarılır."""
    valid = _make_json([
        _make_idea_dict(target_id=1, primary_keyword_id=10, idea_title="Idea 1", target_platform="instagram", content_format="carousel"),
        _make_idea_dict(target_id=1, primary_keyword_id=20, idea_title="Idea 2", target_platform="instagram", content_format="carousel"),
        _make_idea_dict(target_id=2, primary_keyword_id=30, idea_title="Idea 3", target_platform="twitter", content_format="thread"),
    ])
    raw = "```json\n" + valid + "\n```"
    assert validate_social_idea_output(
        raw, target_specs=DEFAULT_TARGETS, allowed_keyword_ids=DEFAULT_KEYWORDS
    ) == validate_social_idea_output(
        valid, target_specs=DEFAULT_TARGETS, allowed_keyword_ids=DEFAULT_KEYWORDS
    )


def test_d02b_markdown_fence_with_broken_json_rejected():
    """D2b. Fence içindeki JSON kurtarılamayacak kadar bozuksa reddedilir."""
    raw = "```json\n{bad json\n```"
    with pytest.raises(SocialIdeaOutputValidationError) as exc:
        validate_social_idea_output(raw, target_specs=DEFAULT_TARGETS, allowed_keyword_ids=DEFAULT_KEYWORDS)
    assert exc.value.error_code == "IDEA_OUTPUT_INVALID_JSON"


def test_d03_leading_trailing_explanation_rejected():
    """D3. JSON öncesi veya sonrası açıklama reddedilir."""
    raw = "Here is the result: " + _make_json([_make_idea_dict()])
    with pytest.raises(SocialIdeaOutputValidationError) as exc:
        validate_social_idea_output(raw, target_specs=DEFAULT_TARGETS, allowed_keyword_ids=DEFAULT_KEYWORDS)
    assert exc.value.error_code == "IDEA_OUTPUT_INVALID_JSON"


def test_d04_trailing_comma_rejected():
    """D4. Trailing comma içeren JSON reddedilir."""
    raw = '{"ideas": [' + json.dumps(_make_idea_dict()) + ',]}'
    with pytest.raises(SocialIdeaOutputValidationError) as exc:
        validate_social_idea_output(raw, target_specs=DEFAULT_TARGETS, allowed_keyword_ids=DEFAULT_KEYWORDS)
    assert exc.value.error_code == "IDEA_OUTPUT_INVALID_JSON"


def test_d05_duplicate_key_rejected():
    """D5. Duplicate key içeren JSON reddedilir."""
    idea_str = json.dumps(_make_idea_dict())
    raw = f'{{"ideas": [{idea_str}], "ideas": [{idea_str}]}}'
    with pytest.raises(SocialIdeaOutputValidationError) as exc:
        validate_social_idea_output(raw, target_specs=DEFAULT_TARGETS, allowed_keyword_ids=DEFAULT_KEYWORDS)
    assert exc.value.error_code == "IDEA_OUTPUT_INVALID_JSON"


def test_d06_nan_and_infinity_rejected():
    """D6. NaN ve Infinity sabitleri reddedilir."""
    raw_nan = '{"ideas": [{"target_id": 1, "primary_keyword_id": 10, "idea_title": "T", "idea_description": "D", "target_platform": "instagram", "content_format": "carousel", "trend_alignment": NaN}]}'
    with pytest.raises(SocialIdeaOutputValidationError) as exc:
        validate_social_idea_output(raw_nan, target_specs=DEFAULT_TARGETS, allowed_keyword_ids=DEFAULT_KEYWORDS)
    assert exc.value.error_code == "IDEA_OUTPUT_INVALID_JSON"


def test_d07_root_not_dict_or_missing_ideas_rejected():
    """D7. Root nesne list ise veya 'ideas' eksikse reddedilir."""
    with pytest.raises(SocialIdeaOutputValidationError) as exc1:
        validate_social_idea_output("[]", target_specs=DEFAULT_TARGETS, allowed_keyword_ids=DEFAULT_KEYWORDS)
    assert exc1.value.error_code == "IDEA_OUTPUT_INVALID_ROOT"

    with pytest.raises(SocialIdeaOutputValidationError) as exc2:
        validate_social_idea_output('{"other": []}', target_specs=DEFAULT_TARGETS, allowed_keyword_ids=DEFAULT_KEYWORDS)
    assert exc2.value.error_code == "IDEA_OUTPUT_INVALID_ROOT"
    assert exc2.value.field == "ideas"


def test_d08_extra_root_field_rejected():
    """D8. Root seviyesinde fazladan alan IDEA_OUTPUT_UNEXPECTED_FIELD ile reddedilir."""
    raw = '{"ideas": [], "extra_field": "unwanted"}'
    with pytest.raises(SocialIdeaOutputValidationError) as exc:
        validate_social_idea_output(raw, target_specs=DEFAULT_TARGETS, allowed_keyword_ids=DEFAULT_KEYWORDS)
    assert exc.value.error_code == "IDEA_OUTPUT_UNEXPECTED_FIELD"
    assert exc.value.field == "extra_field"


def test_d09_idea_extra_field_rejected():
    """D9. Idea içinde fazladan alan (hook, caption, reasoning vb.) reddedilir."""
    spec = (IdeaTargetSpec(target_id=1, platform="instagram", content_format="carousel", requested_count=1),)
    idea_with_hook = _make_idea_dict()
    idea_with_hook["hook"] = "Catchy Hook"

    with pytest.raises(SocialIdeaOutputValidationError) as exc:
        validate_social_idea_output(_make_json([idea_with_hook]), target_specs=spec, allowed_keyword_ids=DEFAULT_KEYWORDS)
    assert exc.value.error_code == "IDEA_OUTPUT_UNEXPECTED_FIELD"
    assert exc.value.field == "hook"
    assert exc.value.idea_index == 0


def test_d10_missing_required_idea_fields_rejected():
    """D10. Zorunlu idea alanlarından herhangi biri eksikse reddedilir."""
    spec = (IdeaTargetSpec(target_id=1, platform="instagram", content_format="carousel", requested_count=1),)
    required_fields = [
        "target_id", "primary_keyword_id", "idea_title", "idea_description",
        "target_platform", "content_format", "trend_alignment"
    ]
    for field in required_fields:
        idea_dict = _make_idea_dict()
        del idea_dict[field]
        with pytest.raises(SocialIdeaOutputValidationError) as exc:
            validate_social_idea_output(_make_json([idea_dict]), target_specs=spec, allowed_keyword_ids=DEFAULT_KEYWORDS)
        assert exc.value.error_code == "IDEA_OUTPUT_INVALID_IDEA"
        assert exc.value.field == field
        assert exc.value.idea_index == 0


# ==================== GRUP E: REFERANS GÜVENLİĞİ ====================

def test_e01_target_not_allowed_rejected():
    """E1. Brief dışı target_id IDEA_OUTPUT_TARGET_NOT_ALLOWED ile reddedilir."""
    spec = (IdeaTargetSpec(target_id=1, platform="instagram", content_format="carousel", requested_count=1),)
    idea_dict = _make_idea_dict(target_id=999)
    with pytest.raises(SocialIdeaOutputValidationError) as exc:
        validate_social_idea_output(_make_json([idea_dict]), target_specs=spec, allowed_keyword_ids=DEFAULT_KEYWORDS)
    assert exc.value.error_code == "IDEA_OUTPUT_TARGET_NOT_ALLOWED"
    assert exc.value.field == "target_id"
    assert "999" not in exc.value.message


def test_e02_keyword_not_allowed_rejected():
    """E2. Brief dışı primary_keyword_id IDEA_OUTPUT_KEYWORD_NOT_ALLOWED ile reddedilir."""
    spec = (IdeaTargetSpec(target_id=1, platform="instagram", content_format="carousel", requested_count=1),)
    idea_dict = _make_idea_dict(primary_keyword_id=9999)
    with pytest.raises(SocialIdeaOutputValidationError) as exc:
        validate_social_idea_output(_make_json([idea_dict]), target_specs=spec, allowed_keyword_ids=DEFAULT_KEYWORDS)
    assert exc.value.error_code == "IDEA_OUTPUT_KEYWORD_NOT_ALLOWED"
    assert exc.value.field == "primary_keyword_id"
    assert "9999" not in exc.value.message


def test_e03_platform_mismatch_rejected():
    """E3. Target'a uymayan target_platform IDEA_OUTPUT_TARGET_MISMATCH ile reddedilir."""
    spec = (IdeaTargetSpec(target_id=1, platform="instagram", content_format="carousel", requested_count=1),)
    idea_dict = _make_idea_dict(target_platform="twitter", content_format="carousel")
    with pytest.raises(SocialIdeaOutputValidationError) as exc:
        validate_social_idea_output(_make_json([idea_dict]), target_specs=spec, allowed_keyword_ids=DEFAULT_KEYWORDS)
    assert exc.value.error_code == "IDEA_OUTPUT_TARGET_MISMATCH"
    assert exc.value.field == "target_platform"


def test_e04_format_mismatch_rejected():
    """E4. Target'a uymayan content_format IDEA_OUTPUT_TARGET_MISMATCH ile reddedilir."""
    spec = (IdeaTargetSpec(target_id=1, platform="instagram", content_format="carousel", requested_count=1),)
    idea_dict = _make_idea_dict(target_platform="instagram", content_format="reels")
    with pytest.raises(SocialIdeaOutputValidationError) as exc:
        validate_social_idea_output(_make_json([idea_dict]), target_specs=spec, allowed_keyword_ids=DEFAULT_KEYWORDS)
    assert exc.value.error_code == "IDEA_OUTPUT_TARGET_MISMATCH"
    assert exc.value.field == "content_format"


def test_e05_x_spelling_normalised_to_twitter():
    """E5. Plan §3.4: yalnız yazım normalizasyonu — 'X'/'x'/'Twitter' -> 'twitter'.
    Başka bir platform (ör. instagram) twitter hedefine ÇEVRİLMEZ."""
    spec = (IdeaTargetSpec(target_id=1, platform="twitter", content_format="thread", requested_count=1),)

    for spelled in ("X", "x", "Twitter", "twitter", " X "):
        idea = _make_idea_dict(target_id=1, target_platform=spelled, content_format="thread")
        res = validate_social_idea_output(_make_json([idea]), target_specs=spec, allowed_keyword_ids=DEFAULT_KEYWORDS)
        assert res[0].target_platform == "twitter"

    idea_ig = _make_idea_dict(target_id=1, target_platform="instagram", content_format="thread")
    with pytest.raises(SocialIdeaOutputValidationError) as exc:
        validate_social_idea_output(_make_json([idea_ig]), target_specs=spec, allowed_keyword_ids=DEFAULT_KEYWORDS)
    assert exc.value.error_code == "IDEA_OUTPUT_TARGET_MISMATCH"


def test_e06_case_and_whitespace_normalised_but_unknown_values_rejected():
    """E6. Büyük/küçük harf ve baş/son boşluk normalize edilir (kanonik değer yazılır);
    bilinmeyen platform/format çevrilmez ve reddedilir."""
    spec = (IdeaTargetSpec(target_id=1, platform="instagram", content_format="post", requested_count=1),)
    normalisable = [
        ("INSTAGRAM", "post"),
        (" instagram ", "post"),
        ("instagram", "POST"),
        ("instagram", "post "),
    ]
    for plat, fmt in normalisable:
        idea_dict = _make_idea_dict(target_platform=plat, content_format=fmt)
        res = validate_social_idea_output(_make_json([idea_dict]), target_specs=spec, allowed_keyword_ids=DEFAULT_KEYWORDS)
        assert res[0].target_platform == "instagram"
        assert res[0].content_format == "post"

    unknown = [("insta", "post"), ("instagram", "reel-post"), ("facebook", "post")]
    for plat, fmt in unknown:
        idea_dict = _make_idea_dict(target_platform=plat, content_format=fmt)
        with pytest.raises(SocialIdeaOutputValidationError) as exc:
            validate_social_idea_output(_make_json([idea_dict]), target_specs=spec, allowed_keyword_ids=DEFAULT_KEYWORDS)
        assert exc.value.error_code == "IDEA_OUTPUT_TARGET_MISMATCH"


# ==================== GRUP F: METİN VE SKOR ====================

def test_f01_empty_and_whitespace_text_rejected():
    """F1. Boş veya whitespace-only title ve description reddedilir."""
    spec = (IdeaTargetSpec(target_id=1, platform="instagram", content_format="carousel", requested_count=1),)

    # Boş title
    with pytest.raises(SocialIdeaOutputValidationError) as exc1:
        validate_social_idea_output(_make_json([_make_idea_dict(idea_title="")]), target_specs=spec, allowed_keyword_ids=DEFAULT_KEYWORDS)
    assert exc1.value.error_code == "IDEA_OUTPUT_INVALID_TEXT"
    assert exc1.value.field == "idea_title"

    # Whitespace title
    with pytest.raises(SocialIdeaOutputValidationError) as exc2:
        validate_social_idea_output(_make_json([_make_idea_dict(idea_title="   ")]), target_specs=spec, allowed_keyword_ids=DEFAULT_KEYWORDS)
    assert exc2.value.error_code == "IDEA_OUTPUT_INVALID_TEXT"
    assert exc2.value.field == "idea_title"

    # Boş description
    with pytest.raises(SocialIdeaOutputValidationError) as exc3:
        validate_social_idea_output(_make_json([_make_idea_dict(idea_description="")]), target_specs=spec, allowed_keyword_ids=DEFAULT_KEYWORDS)
    assert exc3.value.error_code == "IDEA_OUTPUT_INVALID_TEXT"
    assert exc3.value.field == "idea_description"


def test_f02_leading_trailing_whitespace_rejected():
    """F2. Başında veya sonunda boşluk olan title ve description reddedilir (sessiz strip yapılmaz)."""
    spec = (IdeaTargetSpec(target_id=1, platform="instagram", content_format="carousel", requested_count=1),)

    with pytest.raises(SocialIdeaOutputValidationError) as exc1:
        validate_social_idea_output(_make_json([_make_idea_dict(idea_title=" Leading")]), target_specs=spec, allowed_keyword_ids=DEFAULT_KEYWORDS)
    assert exc1.value.error_code == "IDEA_OUTPUT_INVALID_TEXT"

    with pytest.raises(SocialIdeaOutputValidationError) as exc2:
        validate_social_idea_output(_make_json([_make_idea_dict(idea_description="Trailing ")]), target_specs=spec, allowed_keyword_ids=DEFAULT_KEYWORDS)
    assert exc2.value.error_code == "IDEA_OUTPUT_INVALID_TEXT"


def test_f03_length_boundaries_200_and_2000():
    """F3. 200 karakter title ve 2000 karakter description tam sınırda kabul edilir; 201 ve 2001 reddedilir."""
    spec = (IdeaTargetSpec(target_id=1, platform="instagram", content_format="carousel", requested_count=1),)

    valid_title_200 = "T" * 200
    valid_desc_2000 = "D" * 2000
    res = validate_social_idea_output(
        _make_json([_make_idea_dict(idea_title=valid_title_200, idea_description=valid_desc_2000)]),
        target_specs=spec,
        allowed_keyword_ids=DEFAULT_KEYWORDS,
    )
    assert len(res[0].idea_title) == 200
    assert len(res[0].idea_description) == 2000

    # 201 title reddi
    with pytest.raises(SocialIdeaOutputValidationError) as exc1:
        validate_social_idea_output(
            _make_json([_make_idea_dict(idea_title="T" * 201)]),
            target_specs=spec,
            allowed_keyword_ids=DEFAULT_KEYWORDS,
        )
    assert exc1.value.error_code == "IDEA_OUTPUT_INVALID_TEXT"
    assert exc1.value.field == "idea_title"

    # 2001 description reddi
    with pytest.raises(SocialIdeaOutputValidationError) as exc2:
        validate_social_idea_output(
            _make_json([_make_idea_dict(idea_description="D" * 2001)]),
            target_specs=spec,
            allowed_keyword_ids=DEFAULT_KEYWORDS,
        )
    assert exc2.value.error_code == "IDEA_OUTPUT_INVALID_TEXT"
    assert exc2.value.field == "idea_description"


def test_f04_invalid_score_types_rejected():
    """F4. Bool/string/null trend_alignment reddedilir."""
    spec = (IdeaTargetSpec(target_id=1, platform="instagram", content_format="carousel", requested_count=1),)
    invalids = [True, False, "0.5", None, [0.5]]
    for inv in invalids:
        idea_dict = _make_idea_dict()
        idea_dict["trend_alignment"] = inv
        with pytest.raises(SocialIdeaOutputValidationError) as exc:
            validate_social_idea_output(_make_json([idea_dict]), target_specs=spec, allowed_keyword_ids=DEFAULT_KEYWORDS)
        assert exc.value.error_code == "IDEA_OUTPUT_INVALID_SCORE"
        assert exc.value.field == "trend_alignment"


def test_f05_score_overflow_safety():
    """F5. 10**10000 ve -(10**10000) taşmaları güvenli fail-closed IDEA_OUTPUT_INVALID_SCORE ile reddedilir."""
    import sys
    spec = (IdeaTargetSpec(target_id=1, platform="instagram", content_format="carousel", requested_count=1),)
    sys.set_int_max_str_digits(20000)
    try:
        huge_int = 10 ** 10000
        idea_dict = _make_idea_dict()
        idea_dict["trend_alignment"] = huge_int

        with pytest.raises(SocialIdeaOutputValidationError) as exc1:
            validate_social_idea_output(_make_json([idea_dict]), target_specs=spec, allowed_keyword_ids=DEFAULT_KEYWORDS)
        assert exc1.value.error_code == "IDEA_OUTPUT_INVALID_SCORE"
        assert exc1.value.field == "trend_alignment"
        assert exc1.value.idea_index == 0

        idea_dict["trend_alignment"] = -huge_int
        with pytest.raises(SocialIdeaOutputValidationError) as exc2:
            validate_social_idea_output(_make_json([idea_dict]), target_specs=spec, allowed_keyword_ids=DEFAULT_KEYWORDS)
        assert exc2.value.error_code == "IDEA_OUTPUT_INVALID_SCORE"
        assert exc2.value.field == "trend_alignment"
        assert exc2.value.idea_index == 0
    finally:
        sys.set_int_max_str_digits(4300)

    # 1e10000 (float inf) testi
    raw_inf = '{"ideas": [{"target_id": 1, "primary_keyword_id": 10, "idea_title": "T", "idea_description": "D", "target_platform": "instagram", "content_format": "carousel", "trend_alignment": 1e10000}]}'
    with pytest.raises(SocialIdeaOutputValidationError) as exc3:
        validate_social_idea_output(raw_inf, target_specs=spec, allowed_keyword_ids=DEFAULT_KEYWORDS)
    assert exc3.value.error_code == "IDEA_OUTPUT_INVALID_SCORE"
    assert exc3.value.field == "trend_alignment"
    assert exc3.value.idea_index == 0


def test_f06_score_zero_one_boundaries_and_out_of_range():
    """F6. 0 ve 1 kabul edilir; -0.1 ve 1.1 reddedilir."""
    spec = (IdeaTargetSpec(target_id=1, platform="instagram", content_format="carousel", requested_count=1),)

    # 0 ve 1 int/float kabulü
    for s in (0, 1, 0.0, 1.0):
        idea_dict = _make_idea_dict(trend_alignment=s)
        res = validate_social_idea_output(_make_json([idea_dict]), target_specs=spec, allowed_keyword_ids=DEFAULT_KEYWORDS)
        assert res[0].trend_alignment == float(s)
        assert isinstance(res[0].trend_alignment, float)

    # -0.1 ve 1.1 reddi
    for bad_s in (-0.1, 1.1, -1.0, 2.0):
        idea_dict = _make_idea_dict(trend_alignment=bad_s)
        with pytest.raises(SocialIdeaOutputValidationError) as exc:
            validate_social_idea_output(_make_json([idea_dict]), target_specs=spec, allowed_keyword_ids=DEFAULT_KEYWORDS)
        assert exc.value.error_code == "IDEA_OUTPUT_INVALID_SCORE"


# ==================== GRUP G: KOTA ====================

def test_g01_total_idea_count_mismatch_rejected():
    """G1. Toplam idea sayısı eksik veya fazla olduğunda IDEA_OUTPUT_QUOTA_MISMATCH üretilir."""
    # Toplam 3 bekleniyor
    raw_2_ideas = _make_json([
        _make_idea_dict(target_id=1, idea_title="Idea 1"),
        _make_idea_dict(target_id=1, idea_title="Idea 2"),
    ])
    with pytest.raises(SocialIdeaOutputValidationError) as exc1:
        validate_social_idea_output(raw_2_ideas, target_specs=DEFAULT_TARGETS, allowed_keyword_ids=DEFAULT_KEYWORDS)
    assert exc1.value.error_code == "IDEA_OUTPUT_QUOTA_MISMATCH"
    assert exc1.value.field == "ideas"

    raw_4_ideas = _make_json([
        _make_idea_dict(target_id=1, idea_title="Idea 1"),
        _make_idea_dict(target_id=1, idea_title="Idea 2"),
        _make_idea_dict(target_id=2, idea_title="Idea 3", target_platform="twitter", content_format="thread"),
        _make_idea_dict(target_id=2, idea_title="Idea 4", target_platform="twitter", content_format="thread"),
    ])
    with pytest.raises(SocialIdeaOutputValidationError) as exc2:
        validate_social_idea_output(raw_4_ideas, target_specs=DEFAULT_TARGETS, allowed_keyword_ids=DEFAULT_KEYWORDS)
    assert exc2.value.error_code == "IDEA_OUTPUT_QUOTA_MISMATCH"
    assert exc2.value.field == "ideas"


def test_g02_target_quota_mismatch_rejected():
    """G2. Toplam sayı tutsa bile target bazlı kota dağılımı eşleşmezse reddedilir."""
    # Toplam 3 idea, fakat target 1 için 3 tane, target 2 için 0 tane gelirse
    raw = _make_json([
        _make_idea_dict(target_id=1, idea_title="Idea 1"),
        _make_idea_dict(target_id=1, idea_title="Idea 2"),
        _make_idea_dict(target_id=1, idea_title="Idea 3"),
    ])
    with pytest.raises(SocialIdeaOutputValidationError) as exc:
        validate_social_idea_output(raw, target_specs=DEFAULT_TARGETS, allowed_keyword_ids=DEFAULT_KEYWORDS)
    assert exc.value.error_code == "IDEA_OUTPUT_QUOTA_MISMATCH"
    assert exc.value.field == "target_id"


def test_g03_no_partial_or_fallback_on_failure():
    """G3. Herhangi bir fikir geçersizse hiçbir kısmi liste veya fallback üretilmez."""
    ideas_data = [
        _make_idea_dict(target_id=1, idea_title="Valid Idea 1"),
        _make_idea_dict(target_id=1, idea_title=""),  # Geçersiz!
        _make_idea_dict(target_id=2, idea_title="Valid Idea 3", target_platform="twitter", content_format="thread"),
    ]
    with pytest.raises(SocialIdeaOutputValidationError) as exc:
        validate_social_idea_output(_make_json(ideas_data), target_specs=DEFAULT_TARGETS, allowed_keyword_ids=DEFAULT_KEYWORDS)
    assert exc.value.error_code == "IDEA_OUTPUT_INVALID_TEXT"


# ==================== GRUP H: RESPONSE SCHEMA ====================

def test_h01_schema_structure_and_enums():
    """H1. build_social_idea_response_schema doğru enum, minItems ve required alanları üretir."""
    schema = build_social_idea_response_schema(target_specs=DEFAULT_TARGETS, allowed_keyword_ids=DEFAULT_KEYWORDS)

    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["ideas"]

    ideas_prop = schema["properties"]["ideas"]
    assert ideas_prop["type"] == "array"
    assert ideas_prop["minItems"] == 3
    assert ideas_prop["maxItems"] == 3

    item_props = ideas_prop["items"]["properties"]
    assert item_props["target_id"]["enum"] == [1, 2]
    assert item_props["primary_keyword_id"]["enum"] == [10, 20, 30]
    assert item_props["target_platform"]["enum"] == ["instagram", "twitter"]
    assert item_props["content_format"]["enum"] == ["carousel", "thread"]

    assert ideas_prop["items"]["additionalProperties"] is False
    assert set(ideas_prop["items"]["required"]) == {
        "target_id", "primary_keyword_id", "idea_title", "idea_description",
        "target_platform", "content_format", "trend_alignment"
    }


def test_h02_schema_mutation_isolation():
    """H2. Dışarıdan şema sözlüğü mutate edilirse sonraki çağrılar bundan etkilenmez."""
    schema1 = build_social_idea_response_schema(target_specs=DEFAULT_TARGETS, allowed_keyword_ids=DEFAULT_KEYWORDS)
    schema1["properties"]["ideas"]["minItems"] = 999
    schema1["properties"]["ideas"]["items"]["properties"]["target_id"]["enum"].append(999)

    schema2 = build_social_idea_response_schema(target_specs=DEFAULT_TARGETS, allowed_keyword_ids=DEFAULT_KEYWORDS)
    assert schema2["properties"]["ideas"]["minItems"] == 3
    assert 999 not in schema2["properties"]["ideas"]["items"]["properties"]["target_id"]["enum"]


# ==================== GRUP I: FİKİR-BAZLI SÜZME (plan §3.4, F3) ====================

def test_i01_mixed_output_keeps_only_in_brief_ideas():
    """I1. Karışık çıktıda yalnız brief içindeki fikirler tutulur; diğerleri atılır ve sayılır."""
    ideas_data = [
        _make_idea_dict(target_id=1, idea_title="Keep 1"),
        _make_idea_dict(target_id=99, idea_title="Fake target"),                     # off-brief
        _make_idea_dict(target_id=1, primary_keyword_id=999, idea_title="Fake kw"),  # off-brief
        _make_idea_dict(target_id=1, target_platform="instagram", content_format="thread", idea_title="IG thread"),  # off-brief
        _make_idea_dict(target_id=1, idea_title=""),                                 # invalid
        _make_idea_dict(target_id=2, idea_title="Keep 2", target_platform="X", content_format="Thread"),
    ]
    res = filter_social_idea_output(_make_json(ideas_data), target_specs=DEFAULT_TARGETS, allowed_keyword_ids=DEFAULT_KEYWORDS)

    assert isinstance(res, SocialIdeaFilterResult)
    assert [i.idea_title for i in res.ideas] == ["Keep 1", "Keep 2"]
    # "X"/"Thread" kanonik yazıma normalize edildi, çevrilmedi
    assert (res.ideas[1].target_platform, res.ideas[1].content_format) == ("twitter", "thread")
    assert res.off_brief_dropped == 3
    assert res.invalid_dropped == 1
    assert res.over_quota_dropped == 0
    assert res.total_dropped == 4
    for idea in res.ideas:
        assert idea.target_id in {1, 2}
        assert idea.primary_keyword_id in DEFAULT_KEYWORDS


def test_i02_instagram_thread_dropped_not_converted():
    """I2. instagram/thread (matriste olmayan kombinasyon) atılır; instagram/carousel'e çevrilmez."""
    ideas_data = [
        _make_idea_dict(target_id=1, target_platform="instagram", content_format="thread"),
    ]
    res = filter_social_idea_output(_make_json(ideas_data), target_specs=DEFAULT_TARGETS, allowed_keyword_ids=DEFAULT_KEYWORDS)
    assert res.ideas == ()
    assert res.off_brief_dropped == 1


def test_i03_unknown_platform_not_converted():
    """I3. Bilinmeyen platform ('facebook') hiçbir hedefe çevrilmez, atılır."""
    ideas_data = [
        _make_idea_dict(target_id=2, target_platform="facebook", content_format="thread"),
        _make_idea_dict(target_id=1, target_platform="linkedin", content_format="carousel"),
    ]
    res = filter_social_idea_output(_make_json(ideas_data), target_specs=DEFAULT_TARGETS, allowed_keyword_ids=DEFAULT_KEYWORDS)
    assert res.ideas == ()
    assert res.off_brief_dropped == 2


def test_i04_over_quota_keeps_first_n_deterministically():
    """I4. Hedef kotası aşılırsa çıktı sırasındaki ilk N tutulur, kalanı over_quota_dropped."""
    ideas_data = [
        _make_idea_dict(target_id=1, idea_title="A"),
        _make_idea_dict(target_id=1, idea_title="B"),
        _make_idea_dict(target_id=1, idea_title="C"),  # kota 2 -> atılır
        _make_idea_dict(target_id=2, idea_title="D", target_platform="twitter", content_format="thread"),
        _make_idea_dict(target_id=2, idea_title="E", target_platform="twitter", content_format="thread"),  # kota 1 -> atılır
    ]
    res = filter_social_idea_output(_make_json(ideas_data), target_specs=DEFAULT_TARGETS, allowed_keyword_ids=DEFAULT_KEYWORDS)
    assert [i.idea_title for i in res.ideas] == ["A", "B", "D"]
    assert res.over_quota_dropped == 2
    assert res.off_brief_dropped == 0


def test_i05_missing_targets_are_not_an_error():
    """I5. Eksik hedef süzmede hata değildir (brief geneli tamamlama turu tamamlar)."""
    ideas_data = [_make_idea_dict(target_id=1, idea_title="Only one")]
    res = filter_social_idea_output(_make_json(ideas_data), target_specs=DEFAULT_TARGETS, allowed_keyword_ids=DEFAULT_KEYWORDS)
    assert len(res.ideas) == 1


@pytest.mark.parametrize("raw", [
    "not json",
    "[]",
    json.dumps({"items": []}),
    json.dumps({"ideas": {}}),
    json.dumps({"ideas": [], "extra": 1}),
])
def test_i06_structural_failure_is_category_level_error(raw):
    """I6. JSON değil / 'ideas' listesi yok -> kategori düzeyi hata (fikir atma değil)."""
    with pytest.raises(SocialIdeaOutputValidationError):
        filter_social_idea_output(raw, target_specs=DEFAULT_TARGETS, allowed_keyword_ids=DEFAULT_KEYWORDS)


def test_i07_filter_and_strict_share_same_validator():
    """I7. Süzme ve katı doğrulayıcı aynı merkezi doğrulayıcıyı kullanır: katı modun
    reddettiği her fikir süzmede de atılır (Gemini/DeepSeek ayrımı yok)."""
    bad_ideas = [
        _make_idea_dict(target_id=99),
        _make_idea_dict(primary_keyword_id=999),
        _make_idea_dict(target_platform="tiktok"),
        _make_idea_dict(trend_alignment=1.5),
        {**_make_idea_dict(), "hook": "x"},
    ]
    for bad in bad_ideas:
        with pytest.raises(SocialIdeaOutputValidationError):
            validate_social_idea_output(_make_json([bad]), target_specs=DEFAULT_TARGETS, allowed_keyword_ids=DEFAULT_KEYWORDS)
        res = filter_social_idea_output(_make_json([bad]), target_specs=DEFAULT_TARGETS, allowed_keyword_ids=DEFAULT_KEYWORDS)
        assert res.ideas == ()
        assert res.total_dropped == 1
