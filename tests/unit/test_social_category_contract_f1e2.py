# -*- coding: utf-8 -*-
"""Unit tests for Saf Kategori Çıktı Sözleşmesi ve Fail-Closed Doğrulayıcı (F1-E.2).

Tüm testler saf birim testidir (DB, network, Celery, AI bağımlılığı yoktur).
Tüm başarı ve ret kuralları, schema eşleşmesi ve deep-copy izolasyonu doğrulanır.
"""
from __future__ import annotations

import inspect
import json
import sys
from dataclasses import FrozenInstanceError

import pytest

from app.core.social.category_contract import (
    CANONICAL_CATEGORY_TYPES,
    REQUIRED_CATEGORY_FIELDS,
    SocialCategoryOutputValidationError,
    ValidatedSocialCategory,
    get_social_category_response_schema,
    validate_social_category_output,
)


def _make_valid_category_dict(
    name: str = "Eğitici Seri",
    cat_type: str = "educational",
    desc: str = "Açıklama metni.",
    score: float = 0.85,
    kw_ids: list[int] | None = None,
) -> dict:
    return {
        "category_name": name,
        "category_type": cat_type,
        "description": desc,
        "relevance_score": score,
        "suggested_keyword_ids": kw_ids if kw_ids is not None else [10, 11],
    }


def _make_valid_json(categories: list[dict]) -> str:
    return json.dumps({"categories": categories}, ensure_ascii=False)


# ==================== BAŞARI TESTLERİ ====================

def test_01_two_valid_categories_accepted():
    """1. İki geçerli kategori başarıyla ayrıştırılır ve doğrulanır."""
    raw = _make_valid_json([
        _make_valid_category_dict("Kategori Bir", "educational", kw_ids=[10]),
        _make_valid_category_dict("Kategori İki", "product_benefit", kw_ids=[11]),
    ])
    res = validate_social_category_output(raw, allowed_keyword_ids={10, 11}, max_categories=4)

    assert isinstance(res, tuple)
    assert len(res) == 2
    assert res[0].category_name == "Kategori Bir"
    assert res[0].category_type == "educational"
    assert res[0].relevance_score == 0.85
    assert res[0].suggested_keyword_ids == (10,)
    assert res[1].category_name == "Kategori İki"
    assert res[1].category_type == "product_benefit"
    assert res[1].suggested_keyword_ids == (11,)


def test_02_max_categories_count_accepted():
    """2. max_categories sınırına eşit sayıda kategori kabul edilir."""
    cats = [
        _make_valid_category_dict(f"Kategori {i}", "community", kw_ids=[10])
        for i in range(1, 6)
    ]
    raw = _make_valid_json(cats)
    res = validate_social_category_output(raw, allowed_keyword_ids={10}, max_categories=5)

    assert len(res) == 5
    assert res[4].category_name == "Kategori 5"


def test_03_relevance_score_integer_1_converted_to_float_1_0():
    """3. relevance_score int JSON değeri 1'in float 1.0 olarak dönmesi."""
    raw = _make_valid_json([
        _make_valid_category_dict("Kat 1", "educational", score=1, kw_ids=[10]),
        _make_valid_category_dict("Kat 2", "social_proof", score=0, kw_ids=[10]),
    ])
    res = validate_social_category_output(raw, allowed_keyword_ids={10}, max_categories=4)

    assert isinstance(res[0].relevance_score, float)
    assert res[0].relevance_score == 1.0
    assert isinstance(res[1].relevance_score, float)
    assert res[1].relevance_score == 0.0


def test_04_results_are_frozen():
    """4. ValidatedSocialCategory ve suggested_keyword_ids immutable'dır (frozen)."""
    raw = _make_valid_json([
        _make_valid_category_dict("Kat 1", "educational", kw_ids=[10, 11]),
        _make_valid_category_dict("Kat 2", "brand_story", kw_ids=[10]),
    ])
    res = validate_social_category_output(raw, allowed_keyword_ids={10, 11}, max_categories=4)

    with pytest.raises(FrozenInstanceError):
        res[0].category_name = "Yeni İsim"

    assert isinstance(res[0].suggested_keyword_ids, tuple)


def test_05_keyword_id_order_preserved():
    """5. suggested_keyword_ids listesindeki sıra korunur."""
    raw = _make_valid_json([
        _make_valid_category_dict("Kat 1", "trending", kw_ids=[15, 12, 18]),
        _make_valid_category_dict("Kat 2", "educational", kw_ids=[12, 15]),
    ])
    res = validate_social_category_output(raw, allowed_keyword_ids={12, 15, 18}, max_categories=4)

    assert res[0].suggested_keyword_ids == (15, 12, 18)
    assert res[1].suggested_keyword_ids == (12, 15)


def test_06_schema_and_validator_fields_match():
    """6. Gemini response schema ile validator alanları ve enum değerleri birebir eşleşir."""
    schema = get_social_category_response_schema()

    assert schema["type"] == "object"
    assert schema["required"] == ["categories"]
    assert schema["additionalProperties"] is False

    cat_props = schema["properties"]["categories"]["items"]["properties"]
    cat_required = set(schema["properties"]["categories"]["items"]["required"])

    assert cat_required == REQUIRED_CATEGORY_FIELDS
    assert set(cat_props.keys()) == REQUIRED_CATEGORY_FIELDS
    assert cat_props["category_type"]["enum"] == list(CANONICAL_CATEGORY_TYPES)
    assert schema["properties"]["categories"]["minItems"] == 2
    assert schema["properties"]["categories"]["maxItems"] == 6


def test_07_schema_deep_copy_isolation_and_mutation_protection():
    """7. Schema dışarıdan mutate edilemez, her erişimde deep-copy izolasyonu sağlanır."""
    import app.core.social.category_contract as mod

    s1 = get_social_category_response_schema()
    s1["properties"]["extra_field"] = {"type": "string"}

    s2 = get_social_category_response_schema()
    assert "extra_field" not in s2["properties"]

    s_attr1 = mod.SOCIAL_CATEGORY_RESPONSE_SCHEMA
    s_attr1["mutated"] = True

    s_attr2 = mod.SOCIAL_CATEGORY_RESPONSE_SCHEMA
    assert "mutated" not in s_attr2


# ==================== RET TESTLERİ ====================

def test_08_invalid_json_syntax_rejected():
    """8. Geçersiz JSON sözdizimi INVALID_JSON üretir."""
    with pytest.raises(SocialCategoryOutputValidationError) as exc:
        validate_social_category_output("{invalid_json: 123", allowed_keyword_ids={10}, max_categories=4)
    assert exc.value.error_code == "INVALID_JSON"


def test_09_markdown_fenced_json_recovered_and_accepted():
    """9. Markdown fence içeren JSON artık TEK kurtarma denemesiyle kabul edilir.

    GÜNCELLEME (CLAUDE.md §11): önceki davranış (fail-closed reddet) katı bare
    json.loads tuzağıydı; artık app/core/channel/ai_json.py kurtarma zincirinden
    TEK deneme yapılır. Bu dar kapı yalnız fence-sarmalı metne uygulanır;
    prose-sarmalı metin (bkz. test_10) hâlâ reddedilir.
    """
    raw = "```json\n" + _make_valid_json([
        _make_valid_category_dict("Kat 1", "educational", kw_ids=[10]),
        _make_valid_category_dict("Kat 2", "educational", kw_ids=[10]),
    ]) + "\n```"
    res = validate_social_category_output(raw, allowed_keyword_ids={10}, max_categories=4)
    assert len(res) == 2
    assert res[0].category_name == "Kat 1"


def test_10_leading_and_trailing_prose_rejected():
    """10. JSON öncesi veya sonrası prose/metin INVALID_JSON ile reddedilir."""
    valid = _make_valid_json([
        _make_valid_category_dict("Kat 1", "educational", kw_ids=[10]),
        _make_valid_category_dict("Kat 2", "educational", kw_ids=[10]),
    ])
    # Leading prose
    with pytest.raises(SocialCategoryOutputValidationError) as exc:
        validate_social_category_output("İşte kategoriler: " + valid, allowed_keyword_ids={10}, max_categories=4)
    assert exc.value.error_code == "INVALID_JSON"

    # Trailing prose
    with pytest.raises(SocialCategoryOutputValidationError) as exc:
        validate_social_category_output(valid + "\nUmarım beğenirsiniz.", allowed_keyword_ids={10}, max_categories=4)
    assert exc.value.error_code == "INVALID_JSON"


def test_11_duplicate_json_key_rejected():
    """11. Duplicate JSON key INVALID_JSON ile reddedilir."""
    raw = '{"categories": [{"category_name": "A", "category_name": "B", "category_type": "educational", "description": "desc", "relevance_score": 0.8, "suggested_keyword_ids": [10]}, {"category_name": "C", "category_type": "educational", "description": "desc", "relevance_score": 0.8, "suggested_keyword_ids": [10]}]}'
    with pytest.raises(SocialCategoryOutputValidationError) as exc:
        validate_social_category_output(raw, allowed_keyword_ids={10}, max_categories=4)
    assert exc.value.error_code == "INVALID_JSON"


def test_12_nan_and_infinity_rejected():
    """12. NaN ve Infinity sabitleri INVALID_JSON ile reddedilir."""
    raw_nan = '{"categories": [{"category_name": "A", "category_type": "educational", "description": "desc", "relevance_score": NaN, "suggested_keyword_ids": [10]}, {"category_name": "B", "category_type": "educational", "description": "desc", "relevance_score": 0.8, "suggested_keyword_ids": [10]}]}'
    with pytest.raises(SocialCategoryOutputValidationError) as exc:
        validate_social_category_output(raw_nan, allowed_keyword_ids={10}, max_categories=4)
    assert exc.value.error_code == "INVALID_JSON"

    raw_inf = '{"categories": [{"category_name": "A", "category_type": "educational", "description": "desc", "relevance_score": Infinity, "suggested_keyword_ids": [10]}, {"category_name": "B", "category_type": "educational", "description": "desc", "relevance_score": 0.8, "suggested_keyword_ids": [10]}]}'
    with pytest.raises(SocialCategoryOutputValidationError) as exc:
        validate_social_category_output(raw_inf, allowed_keyword_ids={10}, max_categories=4)
    assert exc.value.error_code == "INVALID_JSON"


def test_13_root_is_list_rejected():
    """13. Kök nesne liste olduğunda INVALID_ROOT üretir."""
    raw = json.dumps([_make_valid_category_dict("Kat 1"), _make_valid_category_dict("Kat 2")])
    with pytest.raises(SocialCategoryOutputValidationError) as exc:
        validate_social_category_output(raw, allowed_keyword_ids={10, 11}, max_categories=4)
    assert exc.value.error_code == "INVALID_ROOT"


def test_14_missing_categories_key_rejected():
    """14. Kök nesnede 'categories' alanı eksikse INVALID_ROOT üretir."""
    raw = json.dumps({"items": [_make_valid_category_dict("Kat 1"), _make_valid_category_dict("Kat 2")]})
    with pytest.raises(SocialCategoryOutputValidationError) as exc:
        validate_social_category_output(raw, allowed_keyword_ids={10, 11}, max_categories=4)
    assert exc.value.error_code == "INVALID_ROOT"
    assert exc.value.field == "categories"


def test_15_extra_root_field_rejected():
    """15. Kök nesnede beklenmeyen ek alan varsa INVALID_ROOT üretir."""
    raw = json.dumps({
        "categories": [_make_valid_category_dict("Kat 1"), _make_valid_category_dict("Kat 2")],
        "total": 2,
    })
    with pytest.raises(SocialCategoryOutputValidationError) as exc:
        validate_social_category_output(raw, allowed_keyword_ids={10, 11}, max_categories=4)
    assert exc.value.error_code == "INVALID_ROOT"
    assert exc.value.field == "total"


def test_16_zero_and_one_category_rejected():
    """16. 0 veya 1 kategori INVALID_CATEGORY_COUNT ile reddedilir."""
    # 0 kategori
    raw_0 = _make_valid_json([])
    with pytest.raises(SocialCategoryOutputValidationError) as exc:
        validate_social_category_output(raw_0, allowed_keyword_ids={10}, max_categories=4)
    assert exc.value.error_code == "INVALID_CATEGORY_COUNT"

    # 1 kategori
    raw_1 = _make_valid_json([_make_valid_category_dict("Kat 1", kw_ids=[10])])
    with pytest.raises(SocialCategoryOutputValidationError) as exc:
        validate_social_category_output(raw_1, allowed_keyword_ids={10}, max_categories=4)
    assert exc.value.error_code == "INVALID_CATEGORY_COUNT"


def test_17_category_count_exceeding_max_categories_rejected():
    """17. max_categories üzerindeki kategori sayısı INVALID_CATEGORY_COUNT ile reddedilir (kesilmez)."""
    cats = [_make_valid_category_dict(f"Kat {i}", kw_ids=[10]) for i in range(1, 5)]
    raw = _make_valid_json(cats)  # 4 kategori
    with pytest.raises(SocialCategoryOutputValidationError) as exc:
        validate_social_category_output(raw, allowed_keyword_ids={10}, max_categories=3)
    assert exc.value.error_code == "INVALID_CATEGORY_COUNT"


def test_18_missing_and_extra_category_fields_rejected():
    """18. Eksik veya fazla kategori alanı INVALID_CATEGORY ile reddedilir."""
    # Eksik alan (description eksik)
    c1 = _make_valid_category_dict("Kat 1")
    del c1["description"]
    c2 = _make_valid_category_dict("Kat 2")
    raw_missing = _make_valid_json([c1, c2])
    with pytest.raises(SocialCategoryOutputValidationError) as exc:
        validate_social_category_output(raw_missing, allowed_keyword_ids={10, 11}, max_categories=4)
    assert exc.value.error_code == "INVALID_CATEGORY"
    assert exc.value.field == "description"
    assert exc.value.category_index == 0

    # Fazla alan (extra_field ekli)
    c3 = _make_valid_category_dict("Kat 1")
    c3["extra_field"] = "unexpected"
    raw_extra = _make_valid_json([c3, c2])
    with pytest.raises(SocialCategoryOutputValidationError) as exc:
        validate_social_category_output(raw_extra, allowed_keyword_ids={10, 11}, max_categories=4)
    assert exc.value.error_code == "INVALID_CATEGORY"
    assert exc.value.field == "extra_field"
    assert exc.value.category_index == 0


def test_19_empty_and_whitespace_category_name_rejected():
    """19. Boş veya başta/sonda boşluk içeren category_name INVALID_CATEGORY ile reddedilir."""
    # Boş metin
    raw_empty = _make_valid_json([
        _make_valid_category_dict("", kw_ids=[10]),
        _make_valid_category_dict("Kat 2", kw_ids=[10]),
    ])
    with pytest.raises(SocialCategoryOutputValidationError) as exc:
        validate_social_category_output(raw_empty, allowed_keyword_ids={10}, max_categories=4)
    assert exc.value.error_code == "INVALID_CATEGORY"
    assert exc.value.field == "category_name"

    # Başta boşluk
    raw_lead = _make_valid_json([
        _make_valid_category_dict(" Kat 1", kw_ids=[10]),
        _make_valid_category_dict("Kat 2", kw_ids=[10]),
    ])
    with pytest.raises(SocialCategoryOutputValidationError) as exc:
        validate_social_category_output(raw_lead, allowed_keyword_ids={10}, max_categories=4)
    assert exc.value.error_code == "INVALID_CATEGORY"
    assert exc.value.field == "category_name"

    # Sonda boşluk
    raw_trail = _make_valid_json([
        _make_valid_category_dict("Kat 1 ", kw_ids=[10]),
        _make_valid_category_dict("Kat 2", kw_ids=[10]),
    ])
    with pytest.raises(SocialCategoryOutputValidationError) as exc:
        validate_social_category_output(raw_trail, allowed_keyword_ids={10}, max_categories=4)
    assert exc.value.error_code == "INVALID_CATEGORY"
    assert exc.value.field == "category_name"


def test_20_over_100_chars_category_name_rejected():
    """20. 100 karakter üzeri category_name INVALID_CATEGORY ile reddedilir."""
    long_name = "K" * 101
    raw = _make_valid_json([
        _make_valid_category_dict(long_name, kw_ids=[10]),
        _make_valid_category_dict("Kat 2", kw_ids=[10]),
    ])
    with pytest.raises(SocialCategoryOutputValidationError) as exc:
        validate_social_category_output(raw, allowed_keyword_ids={10}, max_categories=4)
    assert exc.value.error_code == "INVALID_CATEGORY"
    assert exc.value.field == "category_name"


def test_21_casefold_duplicate_category_name_rejected():
    """21. Unicode casefold ile aynı olan mükerrer adlar DUPLICATE_CATEGORY_NAME üretir."""
    raw = _make_valid_json([
        _make_valid_category_dict("Eğitim Serisi", kw_ids=[10]),
        _make_valid_category_dict("eğitim serisi", kw_ids=[10]),
    ])
    with pytest.raises(SocialCategoryOutputValidationError) as exc:
        validate_social_category_output(raw, allowed_keyword_ids={10}, max_categories=4)
    assert exc.value.error_code == "DUPLICATE_CATEGORY_NAME"
    assert exc.value.field == "category_name"
    assert exc.value.category_index == 1


def test_22_unknown_category_type_rejected():
    """22. Bilinmeyen category_type dönüştürülmez veya atılmaz; fail-closed reddedilir."""
    raw = _make_valid_json([
        _make_valid_category_dict("Kat 1", "unknown_type", kw_ids=[10]),
        _make_valid_category_dict("Kat 2", "educational", kw_ids=[10]),
    ])
    with pytest.raises(SocialCategoryOutputValidationError) as exc:
        validate_social_category_output(raw, allowed_keyword_ids={10}, max_categories=4)
    assert exc.value.error_code == "INVALID_CATEGORY"
    assert exc.value.field == "category_type"
    assert exc.value.category_index == 0


def test_23_empty_and_whitespace_description_rejected():
    """23. Boş veya başta/sonda boşluk içeren description INVALID_CATEGORY üretir."""
    # Boş açıklama
    raw_empty = _make_valid_json([
        _make_valid_category_dict("Kat 1", desc="", kw_ids=[10]),
        _make_valid_category_dict("Kat 2", kw_ids=[10]),
    ])
    with pytest.raises(SocialCategoryOutputValidationError) as exc:
        validate_social_category_output(raw_empty, allowed_keyword_ids={10}, max_categories=4)
    assert exc.value.error_code == "INVALID_CATEGORY"
    assert exc.value.field == "description"

    # Başta boşluk
    raw_lead = _make_valid_json([
        _make_valid_category_dict("Kat 1", desc=" Açıklama", kw_ids=[10]),
        _make_valid_category_dict("Kat 2", kw_ids=[10]),
    ])
    with pytest.raises(SocialCategoryOutputValidationError) as exc:
        validate_social_category_output(raw_lead, allowed_keyword_ids={10}, max_categories=4)
    assert exc.value.error_code == "INVALID_CATEGORY"
    assert exc.value.field == "description"


def test_24_over_2000_chars_description_rejected():
    """24. 2000 karakter üzeri description INVALID_CATEGORY üretir."""
    long_desc = "D" * 2001
    raw = _make_valid_json([
        _make_valid_category_dict("Kat 1", desc=long_desc, kw_ids=[10]),
        _make_valid_category_dict("Kat 2", kw_ids=[10]),
    ])
    with pytest.raises(SocialCategoryOutputValidationError) as exc:
        validate_social_category_output(raw, allowed_keyword_ids={10}, max_categories=4)
    assert exc.value.error_code == "INVALID_CATEGORY"
    assert exc.value.field == "description"


def test_25_relevance_score_invalid_types_and_range_rejected():
    """25. relevance_score bool, string, null veya aralık dışı olduğunda INVALID_CATEGORY üretir."""
    # bool (True)
    raw_bool = _make_valid_json([
        _make_valid_category_dict("Kat 1", score=True, kw_ids=[10]),
        _make_valid_category_dict("Kat 2", kw_ids=[10]),
    ])
    with pytest.raises(SocialCategoryOutputValidationError) as exc:
        validate_social_category_output(raw_bool, allowed_keyword_ids={10}, max_categories=4)
    assert exc.value.error_code == "INVALID_CATEGORY"
    assert exc.value.field == "relevance_score"

    # string
    raw_str = _make_valid_json([
        _make_valid_category_dict("Kat 1", score="0.85", kw_ids=[10]),
        _make_valid_category_dict("Kat 2", kw_ids=[10]),
    ])
    with pytest.raises(SocialCategoryOutputValidationError) as exc:
        validate_social_category_output(raw_str, allowed_keyword_ids={10}, max_categories=4)
    assert exc.value.error_code == "INVALID_CATEGORY"
    assert exc.value.field == "relevance_score"

    # < 0.0
    raw_neg = _make_valid_json([
        _make_valid_category_dict("Kat 1", score=-0.1, kw_ids=[10]),
        _make_valid_category_dict("Kat 2", kw_ids=[10]),
    ])
    with pytest.raises(SocialCategoryOutputValidationError) as exc:
        validate_social_category_output(raw_neg, allowed_keyword_ids={10}, max_categories=4)
    assert exc.value.error_code == "INVALID_CATEGORY"
    assert exc.value.field == "relevance_score"

    # > 1.0
    raw_high = _make_valid_json([
        _make_valid_category_dict("Kat 1", score=1.05, kw_ids=[10]),
        _make_valid_category_dict("Kat 2", kw_ids=[10]),
    ])
    with pytest.raises(SocialCategoryOutputValidationError) as exc:
        validate_social_category_output(raw_high, allowed_keyword_ids={10}, max_categories=4)
    assert exc.value.error_code == "INVALID_CATEGORY"
    assert exc.value.field == "relevance_score"


def test_26_empty_suggested_keyword_ids_rejected():
    """26. suggested_keyword_ids boş dizi olduğunda INVALID_KEYWORD_REFERENCE üretir."""
    raw = _make_valid_json([
        _make_valid_category_dict("Kat 1", kw_ids=[]),
        _make_valid_category_dict("Kat 2", kw_ids=[10]),
    ])
    with pytest.raises(SocialCategoryOutputValidationError) as exc:
        validate_social_category_output(raw, allowed_keyword_ids={10}, max_categories=4)
    assert exc.value.error_code == "INVALID_KEYWORD_REFERENCE"
    assert exc.value.field == "suggested_keyword_ids"


def test_27_keyword_id_invalid_types_and_negative_rejected():
    """27. Keyword ID bool, float, string veya <= 0 olduğunda INVALID_KEYWORD_REFERENCE üretir."""
    for bad_id in [True, 12.5, "12", 0, -1]:
        raw = _make_valid_json([
            _make_valid_category_dict("Kat 1", kw_ids=[bad_id]),
            _make_valid_category_dict("Kat 2", kw_ids=[10]),
        ])
        with pytest.raises(SocialCategoryOutputValidationError) as exc:
            validate_social_category_output(raw, allowed_keyword_ids={10, 12}, max_categories=4)
        assert exc.value.error_code == "INVALID_KEYWORD_REFERENCE"
        assert exc.value.field == "suggested_keyword_ids"


def test_28_duplicate_keyword_id_in_category_rejected():
    """28. Kategori içinde mükerrer keyword ID bulunması INVALID_KEYWORD_REFERENCE üretir (sessizce tekilleştirilmez)."""
    raw = _make_valid_json([
        _make_valid_category_dict("Kat 1", kw_ids=[10, 11, 10]),
        _make_valid_category_dict("Kat 2", kw_ids=[10]),
    ])
    with pytest.raises(SocialCategoryOutputValidationError) as exc:
        validate_social_category_output(raw, allowed_keyword_ids={10, 11}, max_categories=4)
    assert exc.value.error_code == "INVALID_KEYWORD_REFERENCE"
    assert exc.value.field == "suggested_keyword_ids"


def test_29_out_of_brief_keyword_id_rejected():
    """29. Brief dışı keyword ID görüldüğünde tüm çıktı reddedilir (kategori sessizce atılmaz)."""
    raw = _make_valid_json([
        _make_valid_category_dict("Kat 1", kw_ids=[10, 99]),
        _make_valid_category_dict("Kat 2", kw_ids=[10]),
    ])
    with pytest.raises(SocialCategoryOutputValidationError) as exc:
        validate_social_category_output(raw, allowed_keyword_ids={10, 11}, max_categories=4)
    assert exc.value.error_code == "INVALID_KEYWORD_REFERENCE"
    assert exc.value.field == "suggested_keyword_ids"


def test_30_allowed_keyword_ids_empty_or_malformed_rejected():
    """30. allowed_keyword_ids boş veya bozuk ise INVALID_KEYWORD_REFERENCE üretir."""
    raw = _make_valid_json([
        _make_valid_category_dict("Kat 1", kw_ids=[10]),
        _make_valid_category_dict("Kat 2", kw_ids=[10]),
    ])
    # Boş koleksiyon
    with pytest.raises(SocialCategoryOutputValidationError) as exc:
        validate_social_category_output(raw, allowed_keyword_ids=[], max_categories=4)
    assert exc.value.error_code == "INVALID_KEYWORD_REFERENCE"
    assert exc.value.field == "allowed_keyword_ids"

    # Bozuk id içeren koleksiyon (negatif / string / bool)
    with pytest.raises(SocialCategoryOutputValidationError) as exc:
        validate_social_category_output(raw, allowed_keyword_ids=[-5], max_categories=4)
    assert exc.value.error_code == "INVALID_KEYWORD_REFERENCE"
    assert exc.value.field == "allowed_keyword_ids"

    with pytest.raises(SocialCategoryOutputValidationError) as exc:
        validate_social_category_output(raw, allowed_keyword_ids=[True], max_categories=4)
    assert exc.value.error_code == "INVALID_KEYWORD_REFERENCE"


def test_31_max_categories_invalid_types_and_bounds_rejected():
    """31. max_categories bool, string, <2 veya >6 olduğunda INVALID_CATEGORY_COUNT üretir."""
    raw = _make_valid_json([
        _make_valid_category_dict("Kat 1", kw_ids=[10]),
        _make_valid_category_dict("Kat 2", kw_ids=[10]),
    ])
    for bad_max in [True, "4", 1, 7, 0]:
        with pytest.raises(SocialCategoryOutputValidationError) as exc:
            validate_social_category_output(raw, allowed_keyword_ids={10}, max_categories=bad_max)
        assert exc.value.error_code == "INVALID_CATEGORY_COUNT"
        assert exc.value.field == "max_categories"


def test_32_no_framework_or_db_dependencies():
    """32. category_contract modülü FastAPI, SQLAlchemy veya AIService bağımlılığı taşımaz."""
    import app.core.social.category_contract as mod

    src = inspect.getsource(mod)
    assert "fastapi" not in src.lower()
    assert "sqlalchemy" not in src.lower()
    assert "aiservice" not in src.lower()
    assert "get_ai" not in src.lower()


def test_33_relevance_score_huge_positive_int_overflow_safe():
    """33. relevance_score = 10 ** 10000: SocialCategoryOutputValidationError, INVALID_CATEGORY, field relevance_score, doğru category_index."""
    sys.set_int_max_str_digits(20000)
    try:
        raw = _make_valid_json([
            _make_valid_category_dict("Kat 1", score=10 ** 10000, kw_ids=[10]),
            _make_valid_category_dict("Kat 2", kw_ids=[10]),
        ])
        with pytest.raises(SocialCategoryOutputValidationError) as exc:
            validate_social_category_output(raw, allowed_keyword_ids={10}, max_categories=4)
        assert exc.value.error_code == "INVALID_CATEGORY"
        assert exc.value.field == "relevance_score"
        assert exc.value.category_index == 0
    finally:
        sys.set_int_max_str_digits(4300)


def test_34_relevance_score_huge_negative_int_overflow_safe():
    """34. relevance_score = -(10 ** 10000): aynı güvenli domain hatası."""
    sys.set_int_max_str_digits(20000)
    try:
        raw = _make_valid_json([
            _make_valid_category_dict("Kat 1", kw_ids=[10]),
            _make_valid_category_dict("Kat 2", score=-(10 ** 10000), kw_ids=[10]),
        ])
        with pytest.raises(SocialCategoryOutputValidationError) as exc:
            validate_social_category_output(raw, allowed_keyword_ids={10}, max_categories=4)
        assert exc.value.error_code == "INVALID_CATEGORY"
        assert exc.value.field == "relevance_score"
        assert exc.value.category_index == 1
    finally:
        sys.set_int_max_str_digits(4300)


def test_35_relevance_score_json_1e10000_fails_closed():
    """35. JSON sayısı 1e10000: INVALID_CATEGORY üretir, raw OverflowError oluşmaz."""
    raw = '{"categories": [{"category_name": "Kat 1", "category_type": "educational", "description": "desc", "relevance_score": 1e10000, "suggested_keyword_ids": [10]}, {"category_name": "Kat 2", "category_type": "educational", "description": "desc", "relevance_score": 0.8, "suggested_keyword_ids": [10]}]}'
    with pytest.raises(SocialCategoryOutputValidationError) as exc:
        validate_social_category_output(raw, allowed_keyword_ids={10}, max_categories=4)
    assert exc.value.error_code == "INVALID_CATEGORY"
    assert exc.value.field == "relevance_score"
    assert exc.value.category_index == 0


def test_36_normal_scores_0_1_and_half_accepted():
    """36. Normal 0, 1, 0.5 değerleri hâlâ kabul edilir ve float döner."""
    raw = _make_valid_json([
        _make_valid_category_dict("Kat 1", score=0, kw_ids=[10]),
        _make_valid_category_dict("Kat 2", score=0.5, kw_ids=[10]),
        _make_valid_category_dict("Kat 3", score=1, kw_ids=[10]),
    ])
    res = validate_social_category_output(raw, allowed_keyword_ids={10}, max_categories=4)
    assert len(res) == 3
    assert res[0].relevance_score == 0.0
    assert isinstance(res[0].relevance_score, float)
    assert res[1].relevance_score == 0.5
    assert isinstance(res[1].relevance_score, float)
    assert res[2].relevance_score == 1.0
    assert isinstance(res[2].relevance_score, float)


def test_37_unknown_category_type_does_not_leak_secret_marker():
    """37. Bilinmeyen category_type değeri 'SECRET_MARKER' olduğunda: exception string içinde SECRET_MARKER bulunmaz."""
    raw = _make_valid_json([
        _make_valid_category_dict("Kat 1", cat_type="SECRET_MARKER", kw_ids=[10]),
        _make_valid_category_dict("Kat 2", kw_ids=[10]),
    ])
    with pytest.raises(SocialCategoryOutputValidationError) as exc:
        validate_social_category_output(raw, allowed_keyword_ids={10}, max_categories=4)
    assert exc.value.error_code == "INVALID_CATEGORY"
    assert exc.value.field == "category_type"
    assert exc.value.category_index == 0
    err_str = str(exc.value)
    assert "SECRET_MARKER" not in err_str
    assert "SECRET_MARKER" not in exc.value.message


def test_38_duplicate_category_name_does_not_leak_secret_marker():
    """38. Duplicate category_name 'SECRET_MARKER' olduğunda: exception string içinde SECRET_MARKER bulunmaz."""
    raw = _make_valid_json([
        _make_valid_category_dict("SECRET_MARKER", kw_ids=[10]),
        _make_valid_category_dict("secret_marker", kw_ids=[10]),
    ])
    with pytest.raises(SocialCategoryOutputValidationError) as exc:
        validate_social_category_output(raw, allowed_keyword_ids={10}, max_categories=4)
    assert exc.value.error_code == "DUPLICATE_CATEGORY_NAME"
    assert exc.value.field == "category_name"
    assert exc.value.category_index == 1
    err_str = str(exc.value)
    assert "SECRET_MARKER" not in err_str
    assert "secret_marker" not in err_str
    assert "SECRET_MARKER" not in exc.value.message


def test_39_out_of_brief_and_invalid_keyword_id_does_not_leak_raw_value():
    """39. Brief dışı veya geçersiz keyword girdisinde hata mesajı ham değeri içermez."""
    # Out of brief ID
    raw_out = _make_valid_json([
        _make_valid_category_dict("Kat 1", kw_ids=[10, 99999]),
        _make_valid_category_dict("Kat 2", kw_ids=[10]),
    ])
    with pytest.raises(SocialCategoryOutputValidationError) as exc_out:
        validate_social_category_output(raw_out, allowed_keyword_ids={10}, max_categories=4)
    assert exc_out.value.error_code == "INVALID_KEYWORD_REFERENCE"
    assert "99999" not in str(exc_out.value)
    assert "99999" not in exc_out.value.message

    # Duplicate keyword ID
    raw_dup = _make_valid_json([
        _make_valid_category_dict("Kat 1", kw_ids=[10, 88888, 88888]),
        _make_valid_category_dict("Kat 2", kw_ids=[10]),
    ])
    with pytest.raises(SocialCategoryOutputValidationError) as exc_dup:
        validate_social_category_output(raw_dup, allowed_keyword_ids={10, 88888}, max_categories=4)
    assert exc_dup.value.error_code == "INVALID_KEYWORD_REFERENCE"
    assert "88888" not in str(exc_dup.value)
    assert "88888" not in exc_dup.value.message


def test_40_error_contract_attributes_preserved():
    """40. Mevcut error_code, field ve category_index davranışları korunur."""
    raw = _make_valid_json([
        _make_valid_category_dict("Kat 1", kw_ids=[10]),
        _make_valid_category_dict("Kat 2", cat_type="non_existent", kw_ids=[10]),
    ])
    with pytest.raises(SocialCategoryOutputValidationError) as exc:
        validate_social_category_output(raw, allowed_keyword_ids={10}, max_categories=4)

    assert exc.value.error_code == "INVALID_CATEGORY"
    assert exc.value.field == "category_type"
    assert exc.value.category_index == 1
    assert isinstance(exc.value.message, str)
    assert len(exc.value.message) > 0
