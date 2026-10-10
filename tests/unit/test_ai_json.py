"""Dayanıklı AI JSON parser (`parse_ai_json_list`) birim testleri.

Kaynak: plan5 Faz A. Kabul kriteri "her bozuk JSON tamir edilir" DEĞİL;
kurtarılabilenler kurtarılıyor, kurtarılamayanda temiz hata/boş çökme yok.
"""
import json

import pytest

from app.core.channel.ai_json import parse_ai_json_list, AIJsonParseError


def test_plain_list():
    out = parse_ai_json_list('[{"keyword_id":1,"is_brand_relevant":true}]')
    assert out == [{"keyword_id": 1, "is_brand_relevant": True}]


def test_results_wrapper():
    raw = '{"results":[{"keyword_id":1},{"keyword_id":2}]}'
    out = parse_ai_json_list(raw)
    assert [o["keyword_id"] for o in out] == [1, 2]


def test_markdown_fence():
    raw = '```json\n{"results":[{"keyword_id":5}]}\n```'
    assert parse_ai_json_list(raw) == [{"keyword_id": 5}]


def test_trailing_comma():
    raw = '{"results":[{"keyword_id":1},]}'
    assert parse_ai_json_list(raw) == [{"keyword_id": 1}]


def test_smart_quotes_normalized():
    raw = '{“results”:[{“keyword_id”:1,“matched_exclude_theme”:“kripto”}]}'
    out = parse_ai_json_list(raw)
    assert out[0]["keyword_id"] == 1
    assert out[0]["matched_exclude_theme"] == "kripto"


def test_unbalanced_braces_closed():
    # Array ve obje kapanmamış → kapatma denemesiyle kurtarılır
    raw = '{"results":[{"keyword_id":1,"is_brand_relevant":true}'
    out = parse_ai_json_list(raw)
    assert out and out[0]["keyword_id"] == 1


def test_truncated_array_partial_recovery():
    # "Unterminated string" senaryosu: son obje yarıda kesildi.
    # Kesim noktasına kadarki tam objeler kurtarılmalı.
    raw = (
        '{"results":['
        '{"keyword_id":1,"is_brand_relevant":true,"matched_exclude_theme":""},'
        '{"keyword_id":2,"is_brand_relevant":false,"matched_exclude_theme":"kripto"},'
        '{"keyword_id":3,"is_brand_relevant":false,"matched_exclude_theme":"foreks ka'
    )
    out = parse_ai_json_list(raw)
    ids = [o.get("keyword_id") for o in out]
    assert 1 in ids and 2 in ids  # ilk iki tam obje kurtarıldı
    assert len(out) >= 2


def test_unescaped_quote_partial_recovery():
    # reason içinde kaçışsız tırnak 3. objeyi bozar; 1-2 yine kurtarılmalı.
    raw = (
        '{"results":['
        '{"keyword_id":1,"is_brand_relevant":true},'
        '{"keyword_id":2,"is_brand_relevant":false},'
        '{"keyword_id":3,"reason":"kullanicinin "hisse" niyeti","is_brand_relevant":false}'
        ']}'
    )
    out = parse_ai_json_list(raw)
    ids = {o.get("keyword_id") for o in out}
    assert 1 in ids and 2 in ids


def test_empty_raises():
    with pytest.raises(AIJsonParseError):
        parse_ai_json_list("")
    with pytest.raises(AIJsonParseError):
        parse_ai_json_list("   ")


def test_unrecoverable_no_object_raises():
    with pytest.raises(AIJsonParseError):
        parse_ai_json_list("bu tamamen düz metin, hiç json yok")


def test_non_dict_items_filtered():
    raw = '{"results":[{"keyword_id":1}, "cop", 42, {"keyword_id":2}]}'
    out = parse_ai_json_list(raw)
    assert [o["keyword_id"] for o in out] == [1, 2]


def test_single_object_becomes_list():
    out = parse_ai_json_list('{"keyword_id":9,"is_brand_relevant":true}')
    assert out == [{"keyword_id": 9, "is_brand_relevant": True}]


def test_roundtrip_real_shape():
    payload = {"results": [
        {"keyword_id": i, "is_brand_relevant": i % 2 == 0, "matched_exclude_theme": ""}
        for i in range(1, 6)
    ]}
    out = parse_ai_json_list(json.dumps(payload))
    assert len(out) == 5
