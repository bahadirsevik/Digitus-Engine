# -*- coding: utf-8 -*-
"""Kampanya içeriği: prompt, şema, görsel prompt'u, kalite kararı, onarım (plan Faz 3; kabul 1,3,4,5,7,11)."""
from __future__ import annotations

import dataclasses
import json

import pytest

from app.core.social.campaign_spec import NO_OFFER_TERMS, content_campaign_context
from app.core.social.content_contract import (
    build_social_content_response_schema,
    validate_social_content_output,
)
from app.core.social.content_item_execution import (
    SocialContentItemExecutionError,
    execute_social_content_work_item,
)
from app.core.social.content_quality import (
    SocialContentGroundingContext,
    evaluate_social_content_quality,
)
from app.core.social.content_worker_input import SocialContentWorkItem
from app.core.social.content_contract import ContentTargetSpec
from app.generators.social.brief_content_generator import (
    SocialBriefContentGenerator,
    SocialContentGenerationError,
)
from app.generators.social.brief_content_prompt import build_social_content_prompt
from tests.unit.test_social_content_item_execution_f1g5_3 import (
    FakeAIService,
    _make_prompt_input,
    _make_valid_post_response_dict,
)

SPEC = {
    "spec_version": "campaign_v1", "mode": "suggest", "usage": "paid", "objective": "sales",
    "destination": "whatsapp", "promoted_item": "Başlangıç paketi", "audience": None,
    "focus_note": None, "disallowed_offer_types": [], "offer_draft": None,
}
IDEA_PAYLOAD = {"offer_type": "percent_discount", "cta_type": "message",
                "offer_rationale": "Yeni müşteri denemesi için değerlendirilebilir."}
TERMS = {"type": "percent_discount", "value": "%20", "applies_to": "Başlangıç paketi",
         "conditions": None, "ends": None}
POST = ContentTargetSpec(target_id=1, platform="instagram", content_format="post",
                         duration_preset_id=None, duration_min_sec=None, duration_max_sec=None)
IMAGE = "Two products on a marble table, soft daylight, empty space on the left for text"
OVERLAY = {"headline": "Bakım rutininizi sadeleştirin", "benefit": "Cildinize uygun üç adımlık bakım.",
           "cta": "Mesaj yazın"}


def _work_item(terms=TERMS, **prompt_over) -> SocialContentWorkItem:
    pi = dataclasses.replace(
        _make_prompt_input(target_spec=POST, product_facts=None, trusted_brand_usp=None, **prompt_over),
        campaign=content_campaign_context(SPEC, IDEA_PAYLOAD, terms),
    )
    return SocialContentWorkItem(
        idea_id=pi.idea_id, prompt_input=pi,
        grounding_context=SocialContentGroundingContext(
            primary_keyword=pi.primary_keyword.keyword, offer_terms=terms),
    )


def _resp(caption: str, **over) -> str:
    data = {**_make_valid_post_response_dict(caption=caption), "image_prompt": IMAGE, "overlay_text": OVERLAY}
    data.update(over)
    return json.dumps(data, ensure_ascii=False)


GOOD = "Başlangıç paketinde %20 indirim seni bekliyor."
BAD = "Başlangıç paketinde %30 indirim seni bekliyor."


# ---------- organik akış korunur (kabul testi 1) ----------

def test_organic_prompt_schema_and_quality_have_no_campaign_trace():
    pi = _make_prompt_input(target_spec=POST)
    prompt = build_social_content_prompt(pi)
    assert "campaign" not in prompt and "KAMPANYA" not in prompt and "image_prompt" not in prompt
    schema = build_social_content_response_schema(POST)
    assert "image_prompt" not in schema["properties"] and "image_prompt" not in schema["required"]
    # Organik içerikte teklif denetimi hiç çalışmaz: "%30 indirim" eskisi gibi yalnız iddia denetimine girer
    content = validate_social_content_output(_make_valid_post_response_dict(caption=BAD), POST)
    decision = evaluate_social_content_quality(
        content, SocialContentGroundingContext(primary_keyword="organik cilt bakımı"), repair_attempted=False)
    assert decision.offer_violations == () and "offer_mismatch" not in decision.reason_codes


def test_organic_output_with_image_prompt_is_invalid():
    ai = FakeAIService(responses=[_resp("Düz metin.")])
    with pytest.raises(SocialContentGenerationError) as exc:
        SocialBriefContentGenerator(ai).generate(_make_prompt_input(target_spec=POST))
    assert exc.value.error_code == "CONTENT_OUTPUT_INVALID"


# ---------- prompt + şema ----------

def test_campaign_prompt_carries_exact_terms_and_no_idea_offer_fields():
    terms = {**TERMS, "conditions": "500 TL üzeri, kod: YAZ20", "ends": "2026-11-30"}
    prompt = build_social_content_prompt(_work_item(terms).prompt_input)
    assert "KAMPANYA MODU" in prompt and '"value": "%20"' in prompt
    assert "500 TL üzeri, kod: YAZ20" in prompt and "30 Kasım 2026" in prompt
    assert '"cta_type": "message"' in prompt and "mesaj yazmaya yönlendir" in prompt
    assert "ÜCRETLİ REKLAM" in prompt
    # Fikrin teklif mekanizması içerik prompt'una hiç girmez (tek kaynak: offer_terms)
    assert "offer_rationale" not in prompt and '"offer_type"' not in prompt


def test_no_offer_prompt_forbids_offer_language():
    prompt = build_social_content_prompt(_work_item(dict(NO_OFFER_TERMS)).prompt_input)
    assert "TEKLİFSİZ İÇERİK" in prompt and '"type": "none"' in prompt
    assert "%20" not in prompt and "offer_rationale" not in prompt


def test_campaign_schema_requires_image_prompt():
    schema = build_social_content_response_schema(POST, campaign=True)
    assert "image_prompt" in schema["properties"] and "image_prompt" in schema["required"]


# ---------- görsel prompt'u (kabul testi 11) ----------

def test_image_prompt_with_numbers_is_accepted_and_ratio_added_by_code():
    ai = FakeAIService(responses=[_resp(GOOD, image_prompt="Two products, 4:5 framing, 3 soft lights")])
    content = SocialBriefContentGenerator(ai).generate(_work_item().prompt_input).content
    assert content.image_prompt == "Two products, 4:5 framing, 3 soft lights Aspect ratio 4:5."
    assert '"image_prompt"' in json.dumps(ai.call_args[0]["response_schema"])


def test_static_campaign_content_drops_filler_video_concept():
    ai = FakeAIService(responses=[_resp(GOOD, video_concept="Statik post olduğu için video planlanmamıştır.")])
    content = SocialBriefContentGenerator(ai).generate(_work_item().prompt_input).content
    assert content.format_payload is None and content.video_concept is None


def test_campaign_prompt_asks_for_concrete_cta_and_no_package_scope_claims():
    prompt = build_social_content_prompt(_work_item().prompt_input)
    assert "CTA SOMUT olsun" in prompt
    assert "pakete veya teklif kapsamına DAHİL" in prompt and "saniyeler içinde" in prompt
    assert '"campaign.included_features" doluysa' in prompt


def test_static_campaign_requires_overlay_and_keeps_it():
    ai = FakeAIService(responses=[_resp(GOOD)])
    content = SocialBriefContentGenerator(ai).generate(_work_item().prompt_input).content
    assert content.overlay_text == OVERLAY
    assert '"overlay_text"' in json.dumps(ai.call_args[0]["response_schema"])

    missing = json.loads(_resp(GOOD))
    del missing["overlay_text"]
    with pytest.raises(SocialContentGenerationError) as exc:
        SocialBriefContentGenerator(FakeAIService(responses=[json.dumps(missing)])).generate(
            _work_item().prompt_input)
    assert exc.value.error_code == "CONTENT_OUTPUT_INVALID"


def test_overlay_is_too_long_or_incomplete_is_invalid():
    for bad in ({**OVERLAY, "headline": "x" * 71}, {"headline": "a", "benefit": "b"}):
        with pytest.raises(Exception):
            validate_social_content_output({**json.loads(_resp(GOOD)), "overlay_text": bad}, target_spec=POST)


def test_organic_output_with_overlay_is_invalid():
    data = {**_make_valid_post_response_dict(caption="Düz metin."), "overlay_text": OVERLAY}
    with pytest.raises(SocialContentGenerationError):
        SocialBriefContentGenerator(FakeAIService(responses=[json.dumps(data)])).generate(
            _make_prompt_input(target_spec=POST))


def test_overlay_texts_are_offer_checked():
    ai = FakeAIService(responses=[_resp(GOOD, overlay_text={**OVERLAY, "headline": "Şimdi %30 indirim"}),
                                  _resp(GOOD, overlay_text={**OVERLAY, "headline": "Şimdi %30 indirim"})])
    result = execute_social_content_work_item(ai_service=ai, work_item=_work_item())
    assert result.status == "rejected" and "offer_not_in_terms:%30" in result.quality_decision.offer_violations
    # Onarım girdisi düzeltilecek görsel üstü metni taşır
    assert '"headline": "Şimdi %30 indirim"' in ai.call_args[1]["prompt"]


def test_overlay_display_builds_offer_lines_from_locked_terms():
    from app.core.social.campaign_spec import overlay_display

    payload = {"offer_terms": {**TERMS, "conditions": "ilk aboneliğe özel", "ends": "2026-11-30"}}
    shown = overlay_display(OVERLAY, payload)
    assert shown["offer_line"] == "Başlangıç paketi için %20"
    assert shown["terms_line"] == "ilk aboneliğe özel · 30 Kasım 2026 tarihine kadar"
    none = overlay_display(OVERLAY, {"offer_terms": dict(NO_OFFER_TERMS)})
    assert none["offer_line"] is None and none["terms_line"] is None and none["cta"] == "Mesaj yazın"
    assert overlay_display(None, payload) is None


def test_offer_closing_card_rule_only_with_offer_and_overlay_only_static():
    with_offer = build_social_content_prompt(_work_item().prompt_input)
    no_offer = build_social_content_prompt(_work_item(dict(NO_OFFER_TERMS)).prompt_input)
    # Kapanış kartını sunucu yazar; süre metnin kelime sayısından ("Başlangıç paketi için %20" = 4)
    assert "kapanış kartı" in with_offer and "4 kelime" in with_offer and "en az 2 sn" in with_offer
    # yayın notları brief'in hedef/kitlesinden sapmaz; açılış açıya bağlı (her iki modda)
    for prompt in (with_offer, no_offer):
        assert "CTA butonu seçimi dahil" in prompt and "AÇIYA GÖRE AÇILIŞ" in prompt
    assert "AYNI eylemi anlatmalı (mesaj yazmaya yönlendir" in with_offer
    assert "kapanış kartı" not in no_offer and "yalnız ürün mesajı ve CTA" in no_offer
    assert '"overlay_text"' in with_offer
    reels = ContentTargetSpec(target_id=1, platform="youtube", content_format="short",
                              duration_preset_id="short_16_30", duration_min_sec=16, duration_max_sec=30)
    reels_input = dataclasses.replace(_work_item().prompt_input, target_spec=reels)
    assert '"overlay_text"' not in build_social_content_prompt(reels_input)


def test_video_closing_card_is_built_from_locked_terms():
    from tests.unit.test_social_brief_content_generator_f1g2 import _make_valid_video_response_dict

    reels = ContentTargetSpec(target_id=1, platform="instagram", content_format="reels",
                              duration_preset_id="short_16_30", duration_min_sec=16, duration_max_sec=30)
    data = {**_make_valid_video_response_dict(), "image_prompt": IMAGE}
    data["format_payload"]["segments"][-1]["on_screen_text"] = "Son gün: %20 indirim!"
    terms = {**TERMS, "conditions": "ilk siparişe özel", "ends": "2026-11-30"}
    pi = dataclasses.replace(_work_item(terms).prompt_input, target_spec=reels)

    content = SocialBriefContentGenerator(
        FakeAIService(responses=[json.dumps(data, ensure_ascii=False)])).generate(pi).content
    segments = content.format_payload.segments
    assert segments[-1].on_screen_text == \
        "Başlangıç paketi için %20\nilk siparişe özel\n30 Kasım 2026 tarihine kadar"
    assert segments[0].on_screen_text == "3 Altın Kural"  # diğer sahneler modelin
    # Teklifsiz içerikte kapanışa dokunulmaz
    pi_none = dataclasses.replace(_work_item(dict(NO_OFFER_TERMS)).prompt_input, target_spec=reels)
    data["format_payload"]["segments"][-1]["on_screen_text"] = "Adım adım uygulama"
    content = SocialBriefContentGenerator(
        FakeAIService(responses=[json.dumps(data, ensure_ascii=False)])).generate(pi_none).content
    assert content.format_payload.segments[-1].on_screen_text == "Adım adım uygulama"


def test_dense_on_screen_text_is_a_campaign_warning_only():
    from app.core.social.content_quality import on_screen_text_dense
    from app.core.social.content_contract import ValidatedVideoPayload, ValidatedVideoSegment

    def content(text):
        seg = ValidatedVideoSegment(start_sec=9, end_sec=14, scene="s", on_screen_text=text, voiceover="v")
        return dataclasses.replace(
            validate_social_content_output(json.loads(_resp(GOOD)), target_spec=POST),
            format_payload=ValidatedVideoPayload(kind="video", segments=(seg,)))
    assert on_screen_text_dense(content(" ".join(["kelime"] * 15)))
    assert not on_screen_text_dense(content("Pro yıllık planda %25 indirim"))


def test_campaign_output_without_image_prompt_is_invalid():
    ai = FakeAIService(responses=[json.dumps(_make_valid_post_response_dict(caption=GOOD))])
    with pytest.raises(SocialContentGenerationError) as exc:
        SocialBriefContentGenerator(ai).generate(_work_item().prompt_input)
    assert exc.value.error_code == "CONTENT_OUTPUT_INVALID"


# ---------- teklif tutarlılığı: onarım ve ret (kabul testleri 3, 4, 7) ----------

def test_matching_offer_accepted_in_one_call_without_claim_warning():
    ai = FakeAIService(responses=[_resp(GOOD)])
    result = execute_social_content_work_item(ai_service=ai, work_item=_work_item())
    assert result.status == "accepted" and result.ai_calls_used == 1
    # Onaylı %20 genel iddia denetiminde uyarı üretmez
    assert not any(w.startswith("claim_") for w in result.quality_decision.warnings)


def test_same_text_without_offer_whitelist_would_be_flagged():
    content = validate_social_content_output(_make_valid_post_response_dict(caption=GOOD), POST)
    decision = evaluate_social_content_quality(
        content, SocialContentGroundingContext(primary_keyword="organik cilt bakımı"), repair_attempted=True)
    assert any(w.startswith("claim_") for w in decision.warnings)


def test_included_features_ground_package_claims():
    content = validate_social_content_output(
        _make_valid_post_response_dict(caption="Pakette %90 isabetli günlük analiz var."), POST)

    def _claims(features):
        decision = evaluate_social_content_quality(
            content, SocialContentGroundingContext(primary_keyword="organik cilt bakımı",
                                                   included_features=features), repair_attempted=True)
        return [w for w in decision.warnings if w.startswith("claim_")]

    assert _claims(None)  # dayanaksız sayısal iddia
    assert not _claims("%90 isabetli günlük analiz")  # kullanıcı onaylı pakete dahil özellik


def test_invented_offer_is_repaired_with_same_terms_in_repair_prompt():
    ai = FakeAIService(responses=[_resp(BAD), _resp(GOOD)])
    result = execute_social_content_work_item(ai_service=ai, work_item=_work_item())
    assert result.status == "accepted" and result.repaired is True and result.ai_calls_used == 2
    assert "%30" not in result.content.caption
    repair_prompt = ai.call_args[1]["prompt"]
    assert '"value": "%20"' in repair_prompt and "offer_not_in_terms:%30" in repair_prompt
    assert "TEKLİF TUTARSIZLIĞINI DÜZELT" in repair_prompt


def test_offer_still_wrong_after_repair_is_rejected_not_saved():
    ai = FakeAIService(responses=[_resp(BAD), _resp(BAD)])
    result = execute_social_content_work_item(ai_service=ai, work_item=_work_item())
    assert result.status == "rejected" and result.content is None and result.ai_calls_used == 2
    # Uydurma %30 aynı anda genel iddia bulgusudur; reddi getiren teklif çelişkisidir
    assert "offer_mismatch" in result.quality_decision.reason_codes
    assert result.quality_decision.offer_violations == ("offer_not_in_terms:%30",)


def test_repair_provider_error_does_not_keep_offer_violating_content():
    ai = FakeAIService(responses=[_resp(BAD), RuntimeError("provider down")])
    with pytest.raises(SocialContentItemExecutionError):
        execute_social_content_work_item(ai_service=ai, work_item=_work_item())


def test_scope_conflict_repaired_and_missing_scope_is_review_warning():
    ai = FakeAIService(responses=[_resp("Tüm ürünlerde %20 indirim!"), _resp(GOOD)])
    assert execute_social_content_work_item(ai_service=ai, work_item=_work_item()).repaired is True
    ai = FakeAIService(responses=[_resp("Şimdi %20 indirim seni bekliyor.")])
    result = execute_social_content_work_item(ai_service=ai, work_item=_work_item())
    assert result.status == "accepted" and result.ai_calls_used == 1
    assert "offer_scope_review" in result.quality_decision.warnings


def test_no_offer_mode_with_discount_idea_title_rejects_offer_language():
    # plan §10c-1: başlığında "indirim" geçen fikir + teklifsiz devam
    item = _work_item(dict(NO_OFFER_TERMS), idea_title="Başlangıç paketinde indirim duyurusu")
    ai = FakeAIService(responses=[_resp("Sezon indirimi başladı!"), _resp("Başlangıç paketini tanıyın.")])
    result = execute_social_content_work_item(ai_service=ai, work_item=item)
    assert result.status == "accepted" and result.repaired is True
    ai = FakeAIService(responses=[_resp("Sezon indirimi başladı!"), _resp("İndirim devam ediyor.")])
    result = execute_social_content_work_item(ai_service=ai, work_item=item)
    assert result.status == "rejected"
    assert result.quality_decision.offer_violations[0].startswith("offer_in_no_offer:")


def test_end_date_written_explicitly_or_repaired():
    terms = {**TERMS, "ends": "2026-11-30"}
    ai = FakeAIService(responses=[_resp(GOOD + " Bugün son!"), _resp(GOOD + " 30 Kasım'a kadar.")])
    result = execute_social_content_work_item(ai_service=ai, work_item=_work_item(terms))
    assert result.status == "accepted" and result.repaired is True
    assert "offer_relative_urgency" in ai.call_args[1]["prompt"]
    assert "offer_end_date_missing" in ai.call_args[1]["prompt"]
