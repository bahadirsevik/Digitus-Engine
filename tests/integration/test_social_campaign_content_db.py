# -*- coding: utf-8 -*-
"""Kampanya içeriği DB yolu: istek doğrulama, üretim+kayıt, revizyon (plan Faz 3; kabul 3, 5, 8)."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.core.social.campaign_spec import business_today
from app.core.social.content_flow import SocialContentFlowError, begin_social_content_generation
from app.core.social.content_orchestration import run_social_content_generation
from app.database.connection import SessionLocal
from app.database.models import SocialContent, SocialGenerationAttempt
from app.schemas.social_brief import SocialBriefContentsGenerateRequest
from tests.integration import test_social_content_orchestration_f1g5_5 as orch_h
from tests.integration import test_social_content_preflight_f1g5_7_1 as pre_h
from tests.integration import test_social_content_revision as rev_h
from tests.integration.test_social_content_orchestration_f1g5_5 import enable_flag  # noqa: F401

SPEC = {
    "spec_version": "campaign_v1", "mode": "suggest", "usage": "paid", "objective": "sales",
    "destination": "whatsapp", "promoted_item": "Başlangıç paketi", "audience": None,
    "focus_note": None, "disallowed_offer_types": [], "offer_draft": None,
}
TERMS = {"type": "percent_discount", "value": "%20", "applies_to": "Başlangıç paketi",
         "conditions": None, "ends": None}
IMAGE = "Two products on a marble table, soft daylight, empty space on the left for text"
OVERLAY = {"headline": "Başlangıç paketiyle tanışın", "benefit": "Üç adımlık bakım rutini.",
           "cta": "Mesaj yazın"}
GOOD = "Başlangıç paketinde %20 indirim seni bekliyor."
BAD = "Başlangıç paketinde %30 indirim seni bekliyor."


def _idea_payload(offer_type="percent_discount"):
    return {"offer_type": offer_type, "cta_type": "message",
            "offer_rationale": "Yeni müşteri denemesi için değerlendirilebilir."}


def _make_campaign(db_session, brief, ideas, offer_types=None):
    brief.campaign_spec = SPEC
    for idea, offer_type in zip(ideas, offer_types or ["percent_discount"] * len(ideas)):
        idea.campaign_payload = _idea_payload(offer_type)
    db_session.commit()


def _request(ideas, key="camp-key-1", **offer):
    return SocialBriefContentsGenerateRequest.model_validate(
        {"idempotency_key": key, "idea_ids": [i.id for i in ideas], **({"offer_terms": offer} if offer else {})},
        strict=True,
    )


def _begin(db_session, ws, brief, request):
    return begin_social_content_generation(
        db_session, brief_id=brief.id, brand_profile_id=ws.id, request=request, now=pre_h.T0)


# ---------- istek doğrulama (kabul testi 5) ----------

def test_campaign_request_rules(db_session, make_workspace, make_scoring_run, make_keyword, enable_flag):
    ws, _, brief, _, _, _, ideas = pre_h._setup_full_social_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=2)

    # Organik brief: snapshot bugünküyle aynı (3 anahtar); teklif gönderilirse 400
    start = _begin(db_session, ws, brief, _request(ideas, key="organic-1"))
    attempt = db_session.get(SocialGenerationAttempt, start.attempt_id)
    assert set(attempt.coverage["request"]) == {"idea_ids", "product_facts", "trusted_brand_usp"}
    db_session.rollback()
    with pytest.raises(SocialContentFlowError) as exc:
        _begin(db_session, ws, brief, _request(ideas, key="organic-2", type="none"))
    assert exc.value.error_code == "CONTENT_OFFER_NOT_ALLOWED"
    db_session.rollback()

    _make_campaign(db_session, brief, ideas, ["percent_discount", "bundle"])

    def _code(request):
        with pytest.raises(SocialContentFlowError) as err:
            _begin(db_session, ws, brief, request)
        db_session.rollback()
        return err.value.error_code

    # Koşul eksikse üretim başlamaz
    assert _code(_request(ideas)) == "CONTENT_OFFER_TERMS_REQUIRED"
    assert _code(_request(ideas, type="percent_discount", value="%20")) == "CONTENT_OFFER_TERMS_INVALID"
    # Tür farklıysa 400 (ikinci fikir "bundle")
    assert _code(_request(ideas, type="percent_discount", value="%20", applies_to="Paket")) \
        == "CONTENT_OFFER_TYPE_MISMATCH"
    # Geçmiş bitiş tarihi
    past = (business_today() - timedelta(days=1)).isoformat()
    assert _code(_request(ideas[:1], type="percent_discount", value="%20", applies_to="Paket", ends=past)) \
        == "CONTENT_OFFER_EXPIRED"

    # Teklifsiz devam: indirim/bundle fikirleriyle birlikte kabul edilir
    start = _begin(db_session, ws, brief, _request(ideas, key="none-1", type="none"))
    attempt = db_session.get(SocialGenerationAttempt, start.attempt_id)
    assert attempt.coverage["request"]["offer_terms"]["type"] == "none"
    db_session.rollback()

    # Geçerli teklif snapshot'a aynen yazılır; aynı anahtar + farklı teklif reddedilir
    today = date.today().isoformat() if date.today() >= business_today() else business_today().isoformat()
    ok = _request(ideas[:1], key="ok-1", type="percent_discount", value=" %20 ", applies_to="Başlangıç paketi",
                  conditions="500 TL üzeri", ends=today)
    start = _begin(db_session, ws, brief, ok)
    db_session.commit()
    attempt = db_session.get(SocialGenerationAttempt, start.attempt_id)
    assert attempt.coverage["request"]["offer_terms"] == {
        "type": "percent_discount", "value": "%20", "applies_to": "Başlangıç paketi",
        "conditions": "500 TL üzeri", "ends": today,
    }
    assert _begin(db_session, ws, brief, ok).replayed is True
    changed = _request(ideas[:1], key="ok-1", type="percent_discount", value="%25", applies_to="Başlangıç paketi",
                       conditions="500 TL üzeri", ends=today)
    assert _code(changed) == "CONTENT_ATTEMPT_REQUEST_MISMATCH"


def test_missing_offer_terms_is_http_400_with_user_message(
    client, db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    ws, brief, content = _campaign_source(db_session, make_workspace, make_scoring_run, make_keyword)
    res = client.post(
        f"/api/v1/generation/social/briefs/{brief.id}/contents/async",
        params={"brand_profile_id": ws.id},
        json={"idempotency_key": "http-1", "idea_ids": [content.idea_id]},
    )
    assert res.status_code == 400, res.text
    detail = res.json()["detail"]
    assert detail["code"] == "CONTENT_OFFER_TERMS_REQUIRED" and "teklifsiz devam et" in detail["message"]


# ---------- üretim + kayıt (kabul testi 3) ----------

def test_wrong_offer_not_saved_while_other_content_is(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    ctx = orch_h._setup_content_orchestration_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=2, task_id="task-camp-1",
        product_facts=None, trusted_brand_usp=None,
    )
    brief, attempt, ideas = ctx["brief"], ctx["attempt"], ctx["ideas"]
    _make_campaign(db_session, brief, ideas)
    attempt.coverage = {**attempt.coverage, "request": {**attempt.coverage["request"], "offer_terms": TERMS}}
    db_session.commit()

    def _post(caption):
        return {**orch_h._make_valid_post_dict(caption=caption), "image_prompt": IMAGE, "overlay_text": OVERLAY}

    ai = orch_h.SmartMockAIService(responses=[_post(GOOD), _post(BAD), _post(BAD)])
    result = run_social_content_generation(
        session_factory=SessionLocal, ai_service=ai, attempt_id=attempt.id, task_id="task-camp-1",
        now_provider=lambda: orch_h.T0,
    )

    assert result.status == "partial" and result.ai_calls_used == 3
    assert result.persisted_idea_ids == (ideas[0].id,) and result.unresolved_idea_ids == (ideas[1].id,)
    [warning] = result.warnings
    assert warning.reason_code == "offer_mismatch" and warning.claims == ("offer_not_in_terms:%30",)
    # İlk prompt kullanıcının koşulunu taşır, fikrin teklif gerekçesini taşımaz
    assert '"value": "%20"' in ai.calls[0]["prompt"] and "offer_rationale" not in ai.calls[0]["prompt"]
    # Onarım prompt'unda aynı koşul var (kabul testi 7)
    assert '"value": "%20"' in ai.calls[2]["prompt"] and "offer_violations" in ai.calls[2]["prompt"]

    db_session.expire_all()
    [row] = db_session.query(SocialContent).filter_by(brief_id=brief.id).all()
    assert row.idea_id == ideas[0].id
    assert row.campaign_payload == {"offer_terms": TERMS, "cta_type": "message", "usage": "paid"}
    assert row.image_prompt == IMAGE + " Aspect ratio 4:5."
    db_attempt = db_session.get(SocialGenerationAttempt, attempt.id)
    assert db_attempt.warnings[0]["reason_code"] == "offer_mismatch"

    # Okuma ucu: içerik yanıtı teklif özeti + görsel prompt'unu, uyarı ihlal ayrıntısını taşır
    from app.api.v1.generation import social_content_read_to_response
    from app.core.social.content_read import load_social_content_result

    read = load_social_content_result(
        db_session, brief_id=brief.id, attempt_id=attempt.id, brand_profile_id=ctx["workspace"].id)
    response = social_content_read_to_response(read)
    assert response.contents[0].image_prompt == row.image_prompt
    assert response.contents[0].campaign_payload["offer_terms"]["value"] == "%20"
    assert response.warnings[0].reason_code == "offer_mismatch"


def test_included_features_are_grounding_in_worker(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    ctx = orch_h._setup_content_orchestration_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=1, task_id="task-camp-2",
        product_facts=None, trusted_brand_usp=None,
    )
    brief, attempt, ideas = ctx["brief"], ctx["attempt"], ctx["ideas"]
    _make_campaign(db_session, brief, ideas)
    brief.campaign_spec = {**SPEC, "included_features": "%90 isabetli günlük analiz"}
    attempt.coverage = {**attempt.coverage, "request": {**attempt.coverage["request"], "offer_terms": TERMS}}
    db_session.commit()

    post = {**orch_h._make_valid_post_dict(caption=GOOD + " Pakette %90 isabetli günlük analiz var."),
            "image_prompt": IMAGE, "overlay_text": OVERLAY}
    ai = orch_h.SmartMockAIService(responses=[post])
    result = run_social_content_generation(
        session_factory=SessionLocal, ai_service=ai, attempt_id=attempt.id, task_id="task-camp-2",
        now_provider=lambda: orch_h.T0,
    )
    assert result.status == "completed", result
    assert '"included_features": "%90 isabetli günlük analiz"' in ai.calls[0]["prompt"]
    # Onaylı özellik dayanak sayılır: kayıtta doğrulanmamış iddia uyarısı yok
    db_session.expire_all()
    [row] = db_session.query(SocialContent).filter_by(brief_id=brief.id).all()
    assert not [w for w in row.validation_warnings or [] if w.startswith("claim_")], row.validation_warnings


def test_campaign_video_with_server_closing_card_is_saved_after_repair(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    ctx = orch_h._setup_content_orchestration_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=1, task_id="task-camp-3",
        content_format="reels", duration_preset_id="short_1_15", product_facts=None, trusted_brand_usp=None,
    )
    brief, attempt, ideas = ctx["brief"], ctx["attempt"], ctx["ideas"]
    _make_campaign(db_session, brief, ideas)
    terms = {**TERMS, "conditions": "ilk siparişe özel"}
    attempt.coverage = {**attempt.coverage, "request": {**attempt.coverage["request"], "offer_terms": terms}}
    db_session.commit()

    def _video(caption):
        return {
            "hooks": [{"text": "Başlangıç paketini tanıyın", "style": "curiosity"}],
            "caption": caption, "cta_text": "Mesaj yazın", "hashtags": ["ciltbakimi", "dogalkozmetik", "nemlendirici", "organikurunler", "guzellik"],
            "format_payload": {"kind": "video", "segments": [
                {"start_sec": 0, "end_sec": 7, "scene": "Ürün yakın plan", "on_screen_text": "Üç adımlık bakım",
                 "voiceover": "Başlangıç paketiyle bakım rutininizi kurun."},
                {"start_sec": 7, "end_sec": 14, "scene": "Kapanış kartı", "on_screen_text": "Son gün: %20 indirim!",
                 "voiceover": "Başlangıç paketinde indirim var, bize yazın."},
            ]},
            "visual_suggestion": None, "video_concept": "Sade geçişler", "industry_posting_suggestion": None,
            "platform_notes": None, "image_prompt": IMAGE,
        }

    ai = orch_h.SmartMockAIService(responses=[_video(BAD), _video(GOOD)])  # ilk üretim -> onarım
    result = run_social_content_generation(
        session_factory=SessionLocal, ai_service=ai, attempt_id=attempt.id, task_id="task-camp-3",
        now_provider=lambda: orch_h.T0,
    )
    assert result.status == "completed" and result.ai_calls_used == 2, result

    closing = "Başlangıç paketi için %20\nilk siparişe özel"
    db_session.expire_all()
    [row] = db_session.query(SocialContent).filter_by(brief_id=brief.id).all()
    assert row.format_payload["segments"][-1]["on_screen_text"] == closing
    assert f"Ekranda: {closing}" in row.scenario and "Son gün" not in row.scenario


# ---------- revizyon (kabul testi 8) ----------

def _campaign_source(db_session, make_workspace, make_scoring_run, make_keyword, terms=TERMS):
    from app.database.models import SocialBrief, SocialIdea

    ws, run, brief, content = rev_h._source(db_session, make_workspace, make_scoring_run, make_keyword,
                                            name="Kampanya Rev")
    brief = db_session.get(SocialBrief, brief.id)
    brief.campaign_spec = SPEC
    db_session.get(SocialIdea, content.idea_id).campaign_payload = _idea_payload()
    content.caption = GOOD
    content.image_prompt = IMAGE + " Aspect ratio 4:5."
    content.overlay_text = OVERLAY
    content.campaign_payload = {"offer_terms": terms, "cta_type": "message", "usage": "paid"}
    db_session.commit()
    return ws, brief, content


def _ai(caption, **over):
    return rev_h._ai_content(caption=caption, image_prompt="A completely different image prompt",
                             overlay_text={**OVERLAY, "headline": "Yepyeni başlık"}, **over)


def test_text_revision_keeps_image_prompt_and_offer_snapshot(
    client, db_session, make_workspace, make_scoring_run, make_keyword, monkeypatch, enable_flag
):
    ws, brief, content = _campaign_source(db_session, make_workspace, make_scoring_run, make_keyword)
    image_before, payload_before = content.image_prompt, dict(content.campaign_payload)

    res, sent = rev_h._start(client, ws, brief, content, monkeypatch, scope=("text",),
                             feedback="Metni daha samimi yaz")
    assert res.status_code == 202, res.text
    ai = rev_h.RevisionAI(_ai("Başlangıç paketinde %20 indirim; hemen yazın."))
    assert rev_h._run_worker(monkeypatch, sent[-1], ai)["status"] == "ready"
    assert '"value": "%20"' in ai.prompts[0]  # revizyon aynı teklif snapshot'ını kullanır
    [item] = rev_h._list(client, ws, brief)
    assert "image_prompt" not in item["changes"]

    accept = client.post(f"/api/v1/generation/social/revisions/{item['id']}/accept",
                         params={"brand_profile_id": ws.id})
    assert accept.status_code == 200, accept.text
    db_session.expire_all()
    row = db_session.get(SocialContent, content.id)
    assert row.caption == "Başlangıç paketinde %20 indirim; hemen yazın."
    assert row.image_prompt == image_before and row.campaign_payload == payload_before
    assert row.overlay_text["headline"] == "Yepyeni başlık"  # görsel üstü metin "metin" kapsamında


def test_production_revision_can_change_image_prompt(
    client, db_session, make_workspace, make_scoring_run, make_keyword, monkeypatch, enable_flag
):
    ws, brief, content = _campaign_source(db_session, make_workspace, make_scoring_run, make_keyword)
    _, sent = rev_h._start(client, ws, brief, content, monkeypatch, scope=("production",),
                           feedback="Görseli daha sade yap")
    assert rev_h._run_worker(monkeypatch, sent[-1], rev_h.RevisionAI(_ai(GOOD)))["status"] == "ready"
    [item] = rev_h._list(client, ws, brief)
    assert item["proposed"]["image_prompt"] == "A completely different image prompt Aspect ratio 4:5."
    assert item["proposed"]["caption"] == GOOD  # metin kapsam dışı: korunur
    # görsel üstü metin korunur; teklif/şart satırları kilitli tekliften eklenir
    assert item["proposed"]["overlay_text"] == {
        **OVERLAY, "offer_line": "Başlangıç paketi için %20", "terms_line": None}


def test_legacy_content_text_revision_preview_shows_offer_lines(
    client, db_session, make_workspace, make_scoring_run, make_keyword, monkeypatch, enable_flag
):
    # Görsel üstü metni olmayan eski içerik: öneri ilk kez overlay üretir, önizleme tam teslimi gösterir
    ws, brief, content = _campaign_source(db_session, make_workspace, make_scoring_run, make_keyword)
    content.overlay_text = None
    db_session.commit()
    _, sent = rev_h._start(client, ws, brief, content, monkeypatch, scope=("text",), feedback="Metni kısalt")
    assert rev_h._run_worker(monkeypatch, sent[-1], rev_h.RevisionAI(_ai(GOOD)))["status"] == "ready"
    [item] = rev_h._list(client, ws, brief)
    assert item["proposed"]["overlay_text"] == {
        **OVERLAY, "headline": "Yepyeni başlık", "offer_line": "Başlangıç paketi için %20", "terms_line": None}


def test_revision_proposal_violating_offer_is_rejected(
    client, db_session, make_workspace, make_scoring_run, make_keyword, monkeypatch, enable_flag
):
    ws, brief, content = _campaign_source(db_session, make_workspace, make_scoring_run, make_keyword)
    _, sent = rev_h._start(client, ws, brief, content, monkeypatch, scope=("text",),
                           feedback="İndirimi %30 yap")
    ai = rev_h.RevisionAI(_ai(BAD), _ai(BAD))
    result = rev_h._run_worker(monkeypatch, sent[-1], ai)
    assert result["status"] == "failed" and result["reason"] == "revision_rejected", result
    db_session.expire_all()
    assert db_session.get(SocialContent, content.id).caption == GOOD


def test_revision_does_not_start_for_expired_offer(
    client, db_session, make_workspace, make_scoring_run, make_keyword, monkeypatch, enable_flag
):
    past = (business_today() - timedelta(days=1)).isoformat()
    ws, brief, content = _campaign_source(
        db_session, make_workspace, make_scoring_run, make_keyword, terms={**TERMS, "ends": past})
    res, sent = rev_h._start(client, ws, brief, content, monkeypatch, scope=("text",))
    assert res.status_code == 400 and res.json()["detail"]["code"] == "REVISION_OFFER_EXPIRED"
    assert sent == []


def test_content_history_returns_image_prompt_and_offer_snapshot(
    client, db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    ws, brief, content = _campaign_source(db_session, make_workspace, make_scoring_run, make_keyword)
    res = client.get("/api/v1/generation/social/contents/history",
                     params={"brand_profile_id": ws.id, "brief_id": brief.id})
    assert res.status_code == 200, res.text
    [item] = res.json()["items"]
    assert item["image_prompt"] == IMAGE + " Aspect ratio 4:5."
    assert item["campaign_payload"]["offer_terms"] == TERMS
    assert item["overlay_text"] == {**OVERLAY, "offer_line": "Başlangıç paketi için %20", "terms_line": None}
