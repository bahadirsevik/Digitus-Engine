"""Faz 2 Claude-idiomatik içerik prompt'ları (K16 tek atış) — mühür + sadakat testleri.

DB'siz. Prompt'lar benchmark/idiomatic_prompts/ altında SHA ile mühürlüdür; Faz 1
sonuçları görüldükten sonra değiştirilemez.
"""
from __future__ import annotations

import hashlib
import json
import re
import string
from collections import Counter
from pathlib import Path

import pytest

from app.core.benchmark.reference_characterization import verify_seal
from app.generators.ads.prompt_templates import ADS_RSA_GENERATION_PROMPT
from app.generators.seo_geo.prompt_templates import SEO_GEO_GENERATION_PROMPT
from app.generators.social.brief_content_prompt import _PROMPT_TEMPLATE as SOCIAL_TEMPLATE

DIR = Path(__file__).resolve().parents[2] / "benchmark" / "idiomatic_prompts"
MANIFEST = json.loads((DIR / "manifest.json").read_text(encoding="utf-8"))

SOURCES = {
    "seo_generation": SEO_GEO_GENERATION_PROMPT,
    "ads_rsa": ADS_RSA_GENERATION_PROMPT,
    "social_brief_contents": SOCIAL_TEMPLATE,
}

# İş kuralı rakamları (stage başına açıkça listelenmiş): kaynakta VE idiomatik metinde
# bulunmalı; idiomatik metin ayrıca kaynakta olmayan YENİ rakam içeremez.
RULE_NUMBERS = {
    "seo_generation": ["70", "155", "160", "20", "40", "60", "200", "100", "95", "90",
                       "85", "80", "75", "5", "3", "2", "11", "7"],
    "ads_rsa": ["30", "90", "15", "10", "3", "2", "4"],
    "social_brief_contents": ["500", "0.0", "1.0", "5", "20", "4", "1"],
}
# Kelime/ifade düzeyinde zorunlu iş kuralları (kaynakta da geçtiği doğrulanır).
RULE_PHRASES = {
    "seo_generation": ["≤70 karakter", "≥2 kez", "faq_items", "image_alt_texts", "üçte ikisi",
                       "uydurma", "UYDURMA", "TAM {section_count} alt başlık", "≤20 kelime"],
    "ads_rsa": ["30 KARAKTER VEYA DAHA KISA", "90 KARAKTER VEYA DAHA KISA", "en fazla 3 başlıkta",
                "{{KeyWord:Laptoplar}}", "10 adet negatif kelime üret", "Kazanç/getiri garantisi",
                "YALNIZCA yukarıdaki marka bilgilerinde"],
    "social_brief_contents": ["en fazla 500 karakter", "En az 5, en fazla 20", "'#' sembolü KESİNLİKLE OLMAMALIDIR",
                              "question", "shocking", "relatable", "curiosity", "scenario",
                              "validation_warnings", "CLAIM-FREE GROUNDING", "trusted_brand_usp",
                              "{caption_limit}", "{format_instructions}"],
}
NUM = re.compile(r"\d+(?:\.\d+)?")


def _block(stage: str) -> str:
    raw = (DIR / MANIFEST["stages"][stage]["file"]).read_bytes().decode("utf-8")
    assert "\r" not in raw
    m = re.search(r"<!-- PROMPT_BEGIN -->\n(.*?)\n<!-- PROMPT_END -->", raw, re.S)
    assert m, stage
    return m.group(1)


def _vars(tpl: str) -> set[str]:
    return {f for _, f, _, _ in string.Formatter().parse(tpl) if f}


STAGES = sorted(SOURCES)


def test_manifest_seal_verifies():
    verify_seal(MANIFEST)
    assert MANIFEST["version"] == "1" and MANIFEST["plan_rev"] == 6
    assert sorted(MANIFEST["stages"]) == STAGES


def test_manifest_seal_detects_tamper():
    bad = json.loads(json.dumps(MANIFEST))
    bad["stages"]["ads_rsa"]["prompt_sha256"] = "0" * 64
    with pytest.raises(Exception):
        verify_seal(bad)


@pytest.mark.parametrize("stage", STAGES)
def test_prompt_and_source_hashes_match_manifest(stage):
    entry = MANIFEST["stages"][stage]
    assert hashlib.sha256(_block(stage).encode("utf-8")).hexdigest() == entry["prompt_sha256"]
    assert hashlib.sha256(SOURCES[stage].encode("utf-8")).hexdigest() == entry["source_sha256"]


@pytest.mark.parametrize("stage", STAGES)
def test_variable_set_identical_to_source(stage):
    tpl = _block(stage)
    assert _vars(tpl) == _vars(SOURCES[stage])
    assert sorted(_vars(tpl)) == MANIFEST["stages"][stage]["variables"]


@pytest.mark.parametrize("stage", STAGES)
def test_renders_without_keyerror(stage):
    tpl = _block(stage)
    out = tpl.format(**{v: f"<{v}>" for v in _vars(tpl)})
    for v in _vars(tpl):
        assert f"<{v}>" in out


@pytest.mark.parametrize("stage", STAGES)
def test_business_numbers_preserved_and_no_new_numbers(stage):
    src, new = SOURCES[stage], _block(stage)
    for n in RULE_NUMBERS[stage]:
        assert re.search(rf"(?<![\d.]){re.escape(n)}(?![\d])", src), f"kaynakta yok: {n}"
        assert re.search(rf"(?<![\d.]){re.escape(n)}(?![\d])", new), f"idiomatikte yok: {n}"
    # kural eklenmedi/gevşetilmedi: yeni rakam kümesi kaynağın alt kümesi
    assert set(NUM.findall(new)) <= set(NUM.findall(src)), set(NUM.findall(new)) - set(NUM.findall(src))


@pytest.mark.parametrize("stage", STAGES)
def test_rule_phrases_preserved(stage):
    for p in RULE_PHRASES[stage]:
        assert p in SOURCES[stage], f"kaynakta yok: {p}"
        assert p in _block(stage), f"idiomatikte yok: {p}"


def test_structured_sections_in_order():
    for stage in STAGES:
        t = _block(stage)
        pos = [t.index(f"<{tag}>") for tag in ("role", "context", "facts", "rules", "task", "output")]
        assert pos == sorted(pos)
        assert t.rstrip().endswith("</output>")


def test_social_input_json_boundary_single_pair():
    t = _block("social_brief_contents")
    assert t.count("<INPUT_JSON>") == 1 and t.count("</INPUT_JSON>") == 1
    out = t.format(input_json="{}", format_instructions="x", platform="instagram", caption_limit=2200)
    assert out.count("<INPUT_JSON>") == 1 and out.count("</INPUT_JSON>") == 1


def test_ads_limit_multiset_not_weaker():
    # 30 ve 90 karakter limiti kaynaktaki kadar sık vurgulanmalı (gevşetme yok)
    src, new = SOURCES["ads_rsa"], _block("ads_rsa")
    for n in ("30", "90"):
        assert Counter(re.findall(rf"\b{n}\b", new))[n] >= 2
        assert Counter(re.findall(rf"\b{n}\b", src))[n] >= 2


def test_ads_dki_braces_render_single():
    out = _block("ads_rsa").format(**{v: "x" for v in _vars(_block("ads_rsa"))})
    assert "{KeyWord:Laptoplar}" in out and "{{" not in out
