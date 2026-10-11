# -*- coding: utf-8 -*-
"""Anthropic şema dönüştürücüsü: additionalProperties zorunluluğu + Gemini paritesi.

DB'siz. `jsonschema` imajda yok; küçük yapısal denetçi (tip/required/enum/
additionalProperties/items/minItems/anyOf) kabul-ret karşılaştırması için yeterli.
"""
from __future__ import annotations

import copy

import pytest

from app.core.engine.ads.prompts import FUNNEL_SCHEMA
from app.core.screening.contract import RESPONSE_SCHEMA as SCREENING_SCHEMA
from app.core.social.anthropic_schema import (
    SCHEMA_TRANSFORMER_VERSION,
    to_anthropic_response_schema,
)
from app.core.social.category_contract import get_social_category_response_schema
from app.core.social.provider_schema import ProviderSchemaError, to_gemini_response_schema


def _type_ok(t, v):
    return {
        "string": isinstance(v, str),
        "integer": isinstance(v, int) and not isinstance(v, bool),
        "number": isinstance(v, (int, float)) and not isinstance(v, bool),
        "boolean": isinstance(v, bool),
        "array": isinstance(v, list),
        "object": isinstance(v, dict),
        "null": v is None,
    }[t]


def _accepts(schema, v) -> bool:
    if schema.get("nullable") and v is None:
        return True
    if "anyOf" in schema:
        return any(_accepts(s, v) for s in schema["anyOf"])
    t = schema.get("type")
    types = t if isinstance(t, list) else ([t] if t else [])
    if types and not any(_type_ok(x, v) for x in types):
        return False
    if "enum" in schema and v not in schema["enum"]:
        return False
    if isinstance(v, dict):
        props = schema.get("properties", {})
        if any(r not in v for r in schema.get("required", [])):
            return False
        if schema.get("additionalProperties") is False and set(v) - set(props):
            return False
        return all(_accepts(props[k], x) for k, x in v.items() if k in props)
    if isinstance(v, list):
        if len(v) < schema.get("minItems", 0) or len(v) > schema.get("maxItems", 10**9):
            return False
        return all(_accepts(schema["items"], x) for x in v) if "items" in schema else True
    return True


def _walk_objects(node):
    if isinstance(node, dict):
        if node.get("type") == "object":
            yield node
        for k in ("properties", "$defs"):
            for sub in node.get(k, {}).values():
                yield from _walk_objects(sub)
        if "items" in node:
            yield from _walk_objects(node["items"])
        for k in ("anyOf", "allOf"):
            for sub in node.get(k, []):
                yield from _walk_objects(sub)


def test_version_constant():
    assert SCHEMA_TRANSFORMER_VERSION == "1"


def test_additional_properties_false_everywhere_and_required_kept():
    src = {
        "type": "object",
        "propertyOrdering": ["a", "b"],
        "properties": {
            "a": {"type": "string"},
            "b": {"type": "array", "items": {
                "type": "object", "properties": {"x": {"type": "integer"}},
                "required": ["x"]}},
            "c": {"anyOf": [{"type": "object", "properties": {}}, {"type": "null"}]},
        },
        "required": ["a"],           # b, c optional: OLDUĞU GİBİ kalmalı
        "$defs": {"d": {"type": "object", "properties": {}}},
    }
    orig = copy.deepcopy(src)
    out = to_anthropic_response_schema(src)
    assert src == orig                              # kanonik değişmedi
    assert "propertyOrdering" not in out
    assert out["required"] == ["a"]
    assert out["properties"]["b"]["items"]["required"] == ["x"]
    objs = list(_walk_objects(out))
    assert len(objs) == 4     # kök, b.items, c|0, $defs.d
    assert all(o["additionalProperties"] is False for o in objs)


def test_nullable_becomes_type_union():
    out = to_anthropic_response_schema(
        {"type": "object", "properties": {"n": {"type": "string", "nullable": True}}})
    assert out["properties"]["n"] == {"type": ["string", "null"]}


@pytest.mark.parametrize("bad", [
    [],
    {"type": "object", "properties": {"a": "string"}},
    {"type": "object", "properties": {"a": {"description": "tipsiz"}}},
    {"type": "object", "additionalProperties": True},
    {"description": "tipsiz kök"},
    {"nullable": True, "description": "tipsiz nullable"},
    {"type": "array", "items": {"type": "object"}, "anyOf": []},
])
def test_unknown_shapes_fail_closed(bad):
    with pytest.raises(ProviderSchemaError):
        to_anthropic_response_schema(bad)


def _funnel_row(**kw):
    row = {"id": 1, "funnel": "commercial", "brand_type": "yok", "relevance": 0.5}
    row.update(kw)
    return row


_PAYLOADS = {
    "funnel": (FUNNEL_SCHEMA, [
        ({"results": [_funnel_row()]}, True),
        ({"results": []}, True),
        ({}, False),
        ({"results": [_funnel_row(funnel="bogus")]}, False),
        ({"results": [_funnel_row(id="1")]}, False),
        ({"results": [{"id": 1}]}, False),
        ({"results": "x"}, False),
    ]),
    "screening": (SCREENING_SCHEMA, [
        ({"results": [{"id": 1, "ads_fit": 1, "seo_fit": 0, "social_fit": 2,
                       "reason_codes": {"ads": "a", "seo": "b", "social": "c"}}]}, True),
        ({"results": [{"id": 1, "ads_fit": 1, "seo_fit": 0, "social_fit": 2,
                       "reason_codes": {"ads": "a"}}]}, False),
        ({"results": [{"id": 1, "ads_fit": 1.5, "seo_fit": 0, "social_fit": 2,
                       "reason_codes": {"ads": "a", "seo": "b", "social": "c"}}]}, False),
        ({"result": []}, False),
    ]),
    "category": (get_social_category_response_schema(), [
        ({"categories": [
            {"category_name": "a", "category_type": "educational", "description": "d",
             "relevance_score": 0.9, "suggested_keyword_ids": [1]}] * 2}, True),
        ({"categories": [
            {"category_name": "a", "category_type": "educational", "description": "d",
             "relevance_score": 0.9, "suggested_keyword_ids": [1]}]}, False),   # minItems
        ({"categories": [
            {"category_name": "a", "category_type": "educational", "description": "d",
             "relevance_score": "x", "suggested_keyword_ids": [1]}] * 2}, False),
        ({"categories": [{"category_name": "a"}] * 2}, False),
    ]),
}


@pytest.mark.parametrize("name", sorted(_PAYLOADS))
def test_accept_reject_parity_with_canonical_and_gemini(name):
    canonical, cases = _PAYLOADS[name]
    before = copy.deepcopy(canonical)
    anth = to_anthropic_response_schema(canonical)
    gem = to_gemini_response_schema(canonical)
    assert canonical == before
    for payload, expected in cases:
        assert _accepts(canonical, payload) is expected, payload
        assert _accepts(anth, payload) is expected, payload
        assert _accepts(gem, payload) is expected, payload


def test_extra_field_is_the_only_known_divergence():
    # Gemini dönüşümü additionalProperties'i düşürür (yerelde denetlenir);
    # Anthropic dönüşümü katıdır. Not: FUNNEL/SCREENING kanoniği additionalProperties
    # koymaz (kanonik fazlalığı kabul eder); Anthropic bunları da ZORLA kapatır.
    schema = get_social_category_response_schema()
    payload = {"categories": _PAYLOADS["category"][1][0][0]["categories"], "ekstra": 1}
    assert _accepts(schema, payload) is False
    assert _accepts(to_anthropic_response_schema(schema), payload) is False
    assert _accepts(to_gemini_response_schema(schema), payload) is True
    extra = {"results": [], "ekstra": 1}
    assert _accepts(FUNNEL_SCHEMA, extra) is True
    assert _accepts(to_anthropic_response_schema(FUNNEL_SCHEMA), extra) is False
