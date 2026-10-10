# -*- coding: utf-8 -*-
"""Sosyal brief kategori/fikir/içerik: Gemini'ye GİDEN gerçek response_schema.

Canlı hata: kategori çağrısı `400 INVALID_ARGUMENT: Unknown name
"additional_properties" at generation_config.response_schema`. Bu testler gerçek
GeminiService + gerçek google-genai SDK dönüşüm zincirini koşar; yalnız en alttaki
HTTP gönderimi (`_request`) sahtedir (ağ çağrısı YOK). Böylece doğrulanan şey SDK'nın
sağlayıcıya yazdığı istek gövdesidir, üreticiye verilen Python dict'i değil.

Katılık kaybı yok: `additionalProperties: false`, izinli kimlik enum'ları ve post
`null` zorunluluğu yerel doğrulayıcılarda — aynı sahte sağlayıcıyla kanıtlanır.
"""
from __future__ import annotations

import copy
import json
import os
from typing import Any

import pytest
from google import genai
from google.genai import _api_client

from app.core.social.category_contract import get_social_category_response_schema
from app.core.social.content_contract import (
    ContentTargetSpec,
    build_social_content_response_schema,
)
from app.core.social.idea_contract import IdeaTargetSpec, build_social_idea_response_schema
from app.core.social.provider_schema import ProviderSchemaError, to_gemini_response_schema
from app.generators.ai_service import GeminiService
from app.generators.social.brief_category_generator import (
    SocialBriefCategoryGenerator,
    SocialCategoryGenerationError,
)
from app.generators.social.brief_content_generator import (
    SocialBriefContentGenerator,
    SocialContentGenerationError,
)
from app.generators.social.brief_idea_generator import (
    SocialBriefIdeaGenerator,
    SocialIdeaGenerationError,
)
from app.generators.social.format_matrix import CANONICAL_PLATFORMS
from tests.unit.test_social_brief_category_generator_f1e3 import (
    _make_start,
    _make_valid_category_json,
)
from tests.unit.test_social_brief_content_generator_f1g2 import (
    _make_prompt_input as _make_content_input,
)
from tests.unit.test_social_brief_content_generator_f1g2 import (
    _make_valid_post_response_dict,
    _make_valid_video_response_dict,
)
from tests.unit.test_social_brief_idea_generator_f1f3 import (
    _make_prompt_input as _make_idea_input,
)
from tests.unit.test_social_brief_idea_generator_f1f3 import (
    _make_valid_ideas_json,
)

# Gemini API Schema nesnesinin tel formatındaki alanları (generativelanguage
# v1beta Schema). `additional_properties` bu listede YOKTUR — canlı 400'ün nedeni.
GEMINI_WIRE_SCHEMA_FIELDS = frozenset({
    "type", "format", "title", "description", "nullable", "enum",
    "max_items", "min_items", "properties", "required", "min_properties",
    "max_properties", "min_length", "max_length", "pattern", "example",
    "any_of", "property_ordering", "default", "items", "minimum", "maximum",
})
GEMINI_WIRE_TYPES = frozenset({"STRING", "NUMBER", "INTEGER", "BOOLEAN", "ARRAY", "OBJECT"})


class _CapturingGemini:
    """Gerçek GeminiService; SDK'nın HTTP gönderimi yakalanır ve yanıt taklit edilir."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []
        client = genai.Client(api_key="test-key-no-network")
        # En alt HTTP katmanı: _build_request dahil tüm SDK dönüşümü koşar.
        client.models._api_client._request = self._request
        self.service = GeminiService(api_key="test-key-no-network")
        self.service._client = client
        self.service._client_pid = os.getpid()

    def _request(self, http_request, http_options=None, stream=False):
        assert http_request.method == "post"
        assert http_request.url.endswith(":generateContent")
        # Tel formatı: SDK'nın gövdesini JSON'a çevirip geri oku.
        self.requests.append(json.loads(json.dumps(http_request.data)))
        body = {
            "candidates": [{
                "content": {"role": "model", "parts": [{"text": self.responses.pop(0)}]},
                "finishReason": "STOP",
            }],
            "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1, "totalTokenCount": 2},
        }
        return _api_client.HttpResponse(headers={}, response_stream=[json.dumps(body)])

    def sent_schemas(self) -> list[dict[str, Any]]:
        out = []
        for req in self.requests:
            cfg = req["generationConfig"]
            assert cfg["responseMimeType"] == "application/json"
            out.append(cfg["responseSchema"])
        return out


def _walk(node: Any, path: str = "$"):
    yield path, node
    for name, sub in (node.get("properties") or {}).items():
        yield from _walk(sub, f"{path}.{name}")
    if "items" in node:
        yield from _walk(node["items"], f"{path}[]")
    for i, sub in enumerate(node.get("any_of") or []):
        yield from _walk(sub, f"{path}|{i}")


def _assert_gemini_wire_compatible(schema: dict[str, Any]) -> None:
    assert "additional_properties" not in json.dumps(schema)
    assert "additionalProperties" not in json.dumps(schema)
    for path, node in _walk(schema):
        unknown = set(node) - GEMINI_WIRE_SCHEMA_FIELDS
        assert not unknown, f"{path}: Gemini'nin tanımadığı alan(lar) {unknown}"
        assert node.get("type") in GEMINI_WIRE_TYPES, f"{path}: tip {node.get('type')!r}"
        if "enum" in node:
            assert node["type"] == "STRING", f"{path}: enum yalnız STRING'de"
            assert all(isinstance(v, str) for v in node["enum"]), path
        if "required" in node:
            assert set(node["required"]) <= set(node.get("properties", {})), path


def _all_content_specs() -> list[ContentTargetSpec]:
    specs = []
    for plat in CANONICAL_PLATFORMS:
        for fmt in plat.formats:
            if fmt.requires_duration:
                preset = fmt.duration_presets[0]
                specs.append(ContentTargetSpec(
                    len(specs) + 1, plat.id, fmt.id, preset.id, preset.min_sec, preset.max_sec))
            else:
                specs.append(ContentTargetSpec(len(specs) + 1, plat.id, fmt.id))
    return specs


# ==================== GERÇEK İSTEK GÖVDESİ: ÜÇ AŞAMA ====================

def test_category_request_schema_is_gemini_compatible():
    fake = _CapturingGemini([_make_valid_category_json()])
    result = SocialBriefCategoryGenerator(fake.service).generate(_make_start())

    assert len(result.categories) == 2
    [schema] = fake.sent_schemas()
    _assert_gemini_wire_compatible(schema)
    cats = schema["properties"]["categories"]
    # Şema üst sınırı statik 6; brief'in max_categories sınırı yerel doğrulayıcıda.
    assert (cats["min_items"], cats["max_items"]) == (2, 6)
    assert cats["items"]["properties"]["category_type"]["enum"]


def test_idea_request_schema_is_gemini_compatible():
    fake = _CapturingGemini([_make_valid_ideas_json()])
    result = SocialBriefIdeaGenerator(fake.service).generate(_make_idea_input())

    assert len(result.ideas) == 3
    [schema] = fake.sent_schemas()
    _assert_gemini_wire_compatible(schema)
    item = schema["properties"]["ideas"]["items"]["properties"]
    # Tamsayı enum Gemini'de YOK: izinli kimlikler açıklamaya taşınır,
    # denetim yerel doğrulayıcıda (bkz. katılık testleri).
    assert item["target_id"]["type"] == "INTEGER"
    assert "İzin verilen değerler: 1, 2." in item["target_id"]["description"]
    assert "İzin verilen değerler: 1, 2." in item["primary_keyword_id"]["description"]
    assert item["target_platform"]["enum"] == ["instagram", "twitter"]


@pytest.mark.parametrize("fmt,response", [
    ("reels", _make_valid_video_response_dict()),
    ("post", _make_valid_post_response_dict()),
])
def test_content_request_schema_is_gemini_compatible(fmt, response):
    spec = (ContentTargetSpec(1, "instagram", "reels", "short_16_30", 16, 30)
            if fmt == "reels" else ContentTargetSpec(1, "instagram", "post"))
    fake = _CapturingGemini([json.dumps(response, ensure_ascii=False)])
    result = SocialBriefContentGenerator(fake.service).generate(
        _make_content_input(target_spec=spec))

    assert result.ai_calls_used == 1
    [schema] = fake.sent_schemas()
    _assert_gemini_wire_compatible(schema)
    payload = schema["properties"]["format_payload"]
    if fmt == "post":
        assert result.content.format_payload is None
        assert payload["type"] == "STRING" and payload["nullable"] is True
    else:
        assert payload["type"] == "OBJECT"


@pytest.mark.parametrize("spec", _all_content_specs(), ids=lambda s: f"{s.platform}-{s.content_format}")
def test_every_canonical_content_target_serializes_compatibly(spec):
    """Tüm platform/format hedefleri SDK'dan yerel hata almadan uyumlu gövde üretir."""
    fake = _CapturingGemini([json.dumps({})])
    fake.service.complete_json(
        "x", max_tokens=10, temperature=None,
        response_schema=to_gemini_response_schema(build_social_content_response_schema(spec)))
    _assert_gemini_wire_compatible(fake.sent_schemas()[0])


def test_every_canonical_idea_target_serializes_compatibly():
    specs = []
    for plat in CANONICAL_PLATFORMS:
        for fmt in plat.formats:
            specs.append(IdeaTargetSpec(len(specs) + 1, plat.id, fmt.id, 1))
    for start in range(0, len(specs), 6):
        fake = _CapturingGemini([json.dumps({})])
        fake.service.complete_json(
            "x", max_tokens=10, temperature=None,
            response_schema=to_gemini_response_schema(build_social_idea_response_schema(
                target_specs=tuple(specs[start:start + 6]), allowed_keyword_ids=(7, 8))))
        _assert_gemini_wire_compatible(fake.sent_schemas()[0])


# ==================== KATILIK YEREL DOĞRULAYICIDA KALIR ====================

def test_category_extra_field_still_rejected_locally():
    extra = json.loads(_make_valid_category_json())
    extra["categories"][0]["extra"] = "x"
    fake = _CapturingGemini([json.dumps(extra)] * 2)
    with pytest.raises(SocialCategoryGenerationError) as exc:
        SocialBriefCategoryGenerator(fake.service).generate(_make_start())
    assert exc.value.error_code == "CATEGORY_OUTPUT_INVALID"
    assert len(fake.requests) == 2


@pytest.mark.parametrize("mutate,counter", [
    (lambda d: d.update(extra="x"), "invalid_dropped"),
    (lambda d: d.update(target_id=99), "off_brief_dropped"),
    (lambda d: d.update(primary_keyword_id=99), "off_brief_dropped"),
], ids=["extra_field", "target_not_allowed", "keyword_not_allowed"])
def test_idea_strictness_still_enforced_locally(mutate, counter):
    """Şema sağlayıcıya gevşek gider; katılık yerel doğrulayıcıda kalır. Plan §3.4:
    uymayan fikir ATILIR (sayılır), diğerleri korunur."""
    ideas = json.loads(_make_valid_ideas_json())["ideas"]
    mutate(ideas[0])
    bad = json.dumps({"ideas": ideas})
    fake = _CapturingGemini([bad, bad])
    res = SocialBriefIdeaGenerator(fake.service).generate(_make_idea_input())
    assert len(res.ideas) == len(ideas) - 1
    assert getattr(res, counter) == 1
    assert len(fake.requests) == 1


def test_idea_all_invalid_still_fails_closed_locally():
    ideas = json.loads(_make_valid_ideas_json())["ideas"]
    for idea in ideas:
        idea["target_id"] = 99
    bad = json.dumps({"ideas": ideas})
    fake = _CapturingGemini([bad, bad])
    with pytest.raises(SocialIdeaGenerationError) as exc:
        SocialBriefIdeaGenerator(fake.service).generate(_make_idea_input())
    assert exc.value.error_code == "IDEA_OUTPUT_INVALID"
    assert len(fake.requests) == 2


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(extra="x"),
    lambda d: d["hooks"][0].update(extra="x"),
    lambda d: d.update(format_payload="metin"),
], ids=["root_extra", "hook_extra", "post_payload_not_null"])
def test_content_strictness_still_enforced_locally(mutate):
    resp = _make_valid_post_response_dict()
    mutate(resp)
    fake = _CapturingGemini([json.dumps(resp, ensure_ascii=False)])
    with pytest.raises(SocialContentGenerationError) as exc:
        SocialBriefContentGenerator(fake.service).generate(
            _make_content_input(target_spec=ContentTargetSpec(1, "instagram", "post")))
    assert exc.value.error_code == "CONTENT_OUTPUT_INVALID"


# ==================== DÖNÜŞTÜRÜCÜ SÖZLEŞMESİ ====================

def test_converter_does_not_mutate_canonical_schema():
    canonical = get_social_category_response_schema()
    before = copy.deepcopy(canonical)
    provider = to_gemini_response_schema(canonical)
    assert canonical == before
    assert canonical["additionalProperties"] is False
    assert "additionalProperties" not in provider
    provider["properties"]["categories"]["items"]["properties"]["x"] = {}
    assert "x" not in canonical["properties"]["categories"]["items"]["properties"]


@pytest.mark.parametrize("bad", [
    {"type": "object", "properties": {"a": {"type": "string"}}, "additionalProperties": True},
    {"type": "object", "properties": {"a": {"type": "string", "const": "x"}}},
    {"type": "object", "properties": {"a": {"$ref": "#/defs/x"}}},
    {"type": "object", "properties": {"a": {"description": "tipsiz"}}},
], ids=["additional_true", "const", "ref", "untyped"])
def test_converter_fails_closed_on_unsupported_constructs(bad):
    with pytest.raises(ProviderSchemaError):
        to_gemini_response_schema(bad)
