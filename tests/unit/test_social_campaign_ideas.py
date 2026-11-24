# -*- coding: utf-8 -*-
"""Kampanya fikri: prompt, sözleşme, üretici (plan_sosyal_kampanya_akisi.md §4, Faz 2)."""
from __future__ import annotations

import dataclasses
import json
from types import SimpleNamespace

import pytest

from app.core.social.campaign_spec import validate_campaign_spec
from app.core.social.idea_contract import (
    build_social_idea_response_schema,
    filter_social_idea_output,
)
from app.generators.social import SocialBriefIdeaGenerator, build_social_idea_prompt
from app.generators.social.brief_category_prompt import build_social_category_prompt
from tests.unit.test_social_brief_idea_generator_f1f3 import (
    FakeAIService,
    _make_prompt_input,
    _make_valid_idea_dict,
)


def _spec(**over) -> dict:
    base = dict(mode="suggest", usage="paid", objective="sales", destination="whatsapp",
                promoted_item="Başlangıç paketi", audience=None, focus_note=None,
                disallowed_offer_types=["percent_discount"], offer_draft=None)
    base.update(over)
    return validate_campaign_spec(SimpleNamespace(**base))


SUGGEST = _spec()
DEFINED = _spec(
    mode="defined", disallowed_offer_types=[],
    offer_draft=SimpleNamespace(type="percent_discount", value="%20", applies_to="Başlangıç paketi",
                                conditions=None, ends=None),
)


def _idea(**over) -> dict:
    campaign = dict(offer_type="bundle", cta_type="message",
                    offer_rationale="Birlikte kullanılan ürünler varsa değerlendirilebilir.")
    campaign.update({k: over.pop(k) for k in list(over) if k in campaign})
    return {**_make_valid_idea_dict(**over), **campaign}


def _filter(ideas: list[dict], spec=SUGGEST):
    pi = _make_prompt_input()
    return filter_social_idea_output(
        json.dumps({"ideas": ideas}, ensure_ascii=False),
        target_specs=pi.target_specs, allowed_keyword_ids=(1, 2), campaign=spec,
    )


# ---------- organik akış korunur (kabul testi 1) ----------

def test_organic_prompt_and_schema_have_no_campaign_trace():
    pi = _make_prompt_input()
    prompt = build_social_idea_prompt(pi)
    assert "campaign" not in prompt and "KAMPANYA" not in prompt
    schema = build_social_idea_response_schema(target_specs=pi.target_specs, allowed_keyword_ids=(1, 2))
    assert len(schema["properties"]["ideas"]["items"]["required"]) == 7
    # Organik sözleşme kampanya alanını hâlâ fazlalık sayar
    res = filter_social_idea_output(
        json.dumps({"ideas": [_idea()]}), target_specs=pi.target_specs, allowed_keyword_ids=(1, 2))
    assert res.ideas == () and res.invalid_dropped == 1


# ---------- prompt + şema ----------

def test_campaign_prompt_carries_context_but_never_the_offer_value():
    prompt = build_social_idea_prompt(dataclasses.replace(_make_prompt_input(), campaign=DEFINED))
    assert "KAMPANYA MODU" in prompt
    assert '"allowed_offer_types"' in prompt and '"percent_discount"' in prompt
    assert '"allowed_cta_types"' in prompt and '"message"' in prompt
    assert "Başlangıç paketi" in prompt
    assert "%20" not in prompt  # rakamı yalnız içerik adımı görür
    assert '"assigned_angles"' in prompt and "açıları SIRAYLA kullan" in prompt


def test_consecutive_category_calls_get_different_starting_angles():
    from app.generators.social.brief_idea_prompt import CAMPAIGN_ANGLES, assigned_campaign_angles

    a, b = assigned_campaign_angles(155, 3), assigned_campaign_angles(156, 3)
    assert len(set(a)) == 3 and len(set(b)) == 3 and a[0] != b[0]
    assert set(a) | set(b) == set(CAMPAIGN_ANGLES)  # 6 fikirde dört açının hepsi kullanılır


def test_campaign_schema_limits_offer_and_cta_enums():
    pi = _make_prompt_input()
    item = build_social_idea_response_schema(
        target_specs=pi.target_specs, allowed_keyword_ids=(1, 2), campaign=SUGGEST,
    )["properties"]["ideas"]["items"]
    assert item["properties"]["offer_type"]["enum"] == ["bundle", "free_shipping", "gift"]
    assert item["properties"]["cta_type"]["enum"] == ["message"]
    assert set(item["required"]) >= {"offer_type", "offer_rationale", "cta_type"}


def test_category_prompt_campaign_block_only_for_campaign_brief():
    from tests.unit.test_social_brief_category_generator_f1e3 import _make_start

    start = _make_start()
    organic = build_social_category_prompt(start)
    assert "campaign" not in organic and "KAMPANYA" not in organic
    campaign = build_social_category_prompt(dataclasses.replace(start, campaign_spec=DEFINED))
    assert campaign.startswith(organic.split("<INPUT_JSON>")[0])
    assert "KAMPANYA AÇISIDIR" in campaign and "%20" not in campaign


# ---------- sözleşme: uymayan fikir tek tek atılır ----------

def test_valid_campaign_idea_keeps_payload():
    res = _filter([_idea()])
    assert len(res.ideas) == 1
    assert res.ideas[0].campaign_payload == {
        "offer_type": "bundle", "cta_type": "message",
        "offer_rationale": "Birlikte kullanılan ürünler varsa değerlendirilebilir.",
    }


@pytest.mark.parametrize("over", [
    {"offer_type": "percent_discount"},                 # brief'te yasaklanan tür
    {"offer_type": "none"},                             # "teklifsiz" yalnız içerik adımında
    {"cta_type": "buy"},                                # kabul testi 10: WhatsApp'ta "satın al" yok
    {"offer_rationale": "  "},
    {"offer_rationale": "x" * 501},
    {"offer_rationale": "Bu paket satışları kesin artırır."},
    {"offer_rationale": "Sepet tutarını daha çok kazandırır."},
    {"idea_title": "Pakette %20 indirim"},
    {"idea_description": "2 al 1 öde kurgusu."},
    {"idea_description": "Sepette 100 TL fırsat."},
    {"idea_title": "Pakette [indirim oranı] fırsatı"},  # yer tutucu
    {"idea_title": "Pakette 100₺ indirim"},
    {"idea_title": "Pakette ₺100 indirim"},
    {"idea_title": "Pakette yüzde yirmi indirim"},
    {"idea_title": "İki al bir öde kampanyası"},
    {"idea_description": "ÜÇ AL İKİ ÖDE fırsatı."},
    {"idea_description": "Sepette elli lira fırsat."},
])
def test_off_campaign_idea_is_dropped_and_good_one_kept(over):
    res = _filter([_idea(**over), _idea(idea_title="Sağlam fikir")])
    assert [i.idea_title for i in res.ideas] == ["Sağlam fikir"]
    assert res.invalid_dropped == 1


def test_missing_campaign_field_drops_idea():
    assert _filter([_make_valid_idea_dict()]).invalid_dropped == 1


def test_defined_mode_accepts_only_the_draft_offer_type():
    res = _filter([_idea(offer_type="bundle"), _idea(offer_type="percent_discount")], spec=DEFINED)
    assert [i.campaign_payload["offer_type"] for i in res.ideas] == ["percent_discount"]


@pytest.mark.parametrize("title", [
    "iPhone 15 için 3 kullanım senaryosu",
    "İki kişilik koltuk için üç yerleşim fikri",
    "Yüzde yüz pamuk tişört bakımı",
    "On adımda cilt bakımı: bir serum, iki kural",
])
def test_keyword_number_is_not_an_offer_number(title):
    assert len(_filter([_idea(idea_title=title)]).ideas) == 1


# ---------- üretici uçtan uca ----------

def test_generator_returns_campaign_ideas_and_retry_prompt_mentions_campaign():
    good = json.dumps({"ideas": [_idea()]}, ensure_ascii=False)
    ai = FakeAIService(responses=["not json", good])
    result = SocialBriefIdeaGenerator(ai).generate(
        dataclasses.replace(_make_prompt_input(), campaign=SUGGEST))
    assert result.ideas[0].campaign_payload["cta_type"] == "message"
    assert ai.call_count == 2
    assert "KAMPANYA MODU" in ai.call_args[1]["prompt"].split("ÖNEMLİ DÜZELTME NOTU")[1]
