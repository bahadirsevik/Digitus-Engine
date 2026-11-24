"""A saved campaign offer is authoritative across clients and worker writes."""
from dataclasses import replace

import pytest

from app.core.social.content_persistence import SocialContentPersistenceError
from app.database.models import SocialContent, SocialGenerationAttempt
from tests.integration import test_social_campaign_content_db as campaign_h
from tests.integration import test_social_content_orchestration_f1g5_5 as orch_h
from tests.integration.test_social_campaign_content_db import enable_flag  # noqa: F401


def _saved_campaign(db_session, make_workspace, make_scoring_run, make_keyword, *, terms=None):
    ws, run, brief, _, _, _, ideas = campaign_h.pre_h._setup_full_social_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=3,
    )
    run.algorithm_version = "v3"
    campaign_h._make_campaign(db_session, brief, ideas)
    terms = terms or campaign_h.TERMS
    db_session.add(SocialContent(
        idea_id=ideas[0].id, brief_id=brief.id, caption=campaign_h.GOOD,
        image_prompt=campaign_h.IMAGE,
        campaign_payload={"offer_terms": terms, "cta_type": "message", "usage": "paid"},
    ))
    db_session.commit()
    return ws, brief, ideas


@pytest.mark.parametrize("changed", [
    {"value": "%30"}, {"applies_to": "Tüm ürünler"},
    {"conditions": "Yeni koşul"}, {"ends": "2099-12-31"}, {"type": "none"},
])
def test_api_rejects_different_offer_before_attempt_creation(
    client, db_session, make_workspace, make_scoring_run, make_keyword, enable_flag, changed,
):
    ws, brief, ideas = _saved_campaign(db_session, make_workspace, make_scoring_run, make_keyword)
    count = db_session.query(SocialGenerationAttempt).count()
    response = client.post(
        f"/api/v1/generation/social/briefs/{brief.id}/contents/async",
        params={"brand_profile_id": ws.id},
        json={"idempotency_key": "another-client", "idea_ids": [ideas[1].id],
              "offer_terms": {**campaign_h.TERMS, **changed}},
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "CONTENT_OFFER_LOCKED"
    assert db_session.query(SocialGenerationAttempt).count() == count


def test_same_offer_is_accepted_and_exposed_in_brief_state(
    client, db_session, make_workspace, make_scoring_run, make_keyword, enable_flag,
):
    ws, brief, ideas = _saved_campaign(db_session, make_workspace, make_scoring_run, make_keyword)
    state = client.get(f"/api/v1/generation/social/briefs/{brief.id}/state",
                       params={"brand_profile_id": ws.id})
    assert state.status_code == 200, state.text
    assert state.json()["locked_offer_terms"] == campaign_h.TERMS
    start = campaign_h._begin(db_session, ws, brief,
                             campaign_h._request(ideas[1:], **campaign_h.TERMS))
    assert db_session.get(SocialGenerationAttempt, start.attempt_id).coverage["request"]["offer_terms"] \
        == campaign_h.TERMS


def test_saved_no_offer_cannot_be_replaced(
    client, db_session, make_workspace, make_scoring_run, make_keyword, enable_flag,
):
    from app.core.social.campaign_spec import NO_OFFER_TERMS

    ws, brief, ideas = _saved_campaign(
        db_session, make_workspace, make_scoring_run, make_keyword, terms=NO_OFFER_TERMS,
    )
    with pytest.raises(campaign_h.SocialContentFlowError) as exc:
        campaign_h._begin(db_session, ws, brief, campaign_h._request(ideas[1:], **campaign_h.TERMS))
    assert exc.value.error_code == "CONTENT_OFFER_LOCKED"
    db_session.rollback()
    start = campaign_h._begin(db_session, ws, brief, campaign_h._request(ideas[1:], type="none"))
    assert db_session.get(SocialGenerationAttempt, start.attempt_id).coverage["request"]["offer_terms"] \
        == NO_OFFER_TERMS


def test_worker_cannot_save_an_offer_different_from_first_content(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag,
):
    ctx = orch_h._setup_content_orchestration_env(
        db_session, make_workspace, make_scoring_run, make_keyword,
        num_ideas=2, task_id="offer-lock-worker", product_facts=None, trusted_brand_usp=None,
    )
    brief, attempt, ideas = ctx["brief"], ctx["attempt"], ctx["ideas"]
    campaign_h._make_campaign(db_session, brief, ideas)
    attempt.coverage = {**attempt.coverage,
                        "request": {**attempt.coverage["request"], "offer_terms": campaign_h.TERMS}}
    db_session.add(SocialContent(
        idea_id=ideas[0].id, brief_id=brief.id, caption="First saved offer",
        image_prompt=campaign_h.IMAGE,
        campaign_payload={"offer_terms": {**campaign_h.TERMS, "value": "%25"}},
    ))
    db_session.commit()
    with pytest.raises(SocialContentPersistenceError) as exc:
        orch_h._persist_content_for_test(
            db_session, brief_id=brief.id, attempt_id=attempt.id, idea_id=ideas[1].id,
            task_id="offer-lock-worker",
            content=replace(orch_h._make_valid_content_object(), image_prompt=campaign_h.IMAGE),
        )
    assert exc.value.field == "campaign_payload"
    assert db_session.query(SocialContent).filter_by(idea_id=ideas[1].id).count() == 0
