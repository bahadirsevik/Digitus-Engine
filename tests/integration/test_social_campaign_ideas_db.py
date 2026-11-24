# -*- coding: utf-8 -*-
"""Kampanya fikri DB yolu: worker girdisi, kayıt, retry, okuma (plan Faz 2, kabul testi 6)."""
from __future__ import annotations

import dataclasses
from datetime import timedelta

from app.core.social.idea_persistence import persist_social_ideas
from app.core.social.idea_read import load_social_idea_result
from app.core.social.idea_retry_persistence import persist_social_idea_retry_results
from app.core.social.idea_retry_worker_input import prepare_social_idea_retry_worker_inputs
from app.core.social.idea_worker_input import prepare_social_idea_worker_inputs
from app.database.models import SocialIdea
from tests.integration import test_social_idea_persistence_f1f5 as ideas_h
from tests.integration import test_social_idea_retry_persistence_f1f7_7 as retry_h
from tests.integration import test_social_idea_retry_worker_input_f1f7_5 as retry_wi_h
from tests.integration import test_social_idea_worker_input_f1f6a as wi_h
from tests.integration.test_social_idea_worker_input_f1f6a import enable_flag  # noqa: F401

SPEC = {
    "spec_version": "campaign_v1", "mode": "suggest", "usage": "paid", "objective": "sales",
    "destination": "whatsapp", "promoted_item": "Başlangıç paketi", "audience": None,
    "focus_note": None, "disallowed_offer_types": ["percent_discount"], "offer_draft": None,
}
PAYLOAD = {"offer_type": "bundle", "cta_type": "message",
           "offer_rationale": "Birlikte kullanılan ürünler varsa değerlendirilebilir."}


def _with_payload(results):
    return tuple(
        dataclasses.replace(r, ideas=tuple(dataclasses.replace(i, campaign_payload=PAYLOAD) for i in r.ideas))
        for r in results
    )


def test_worker_input_carries_campaign_spec(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    _, _, brief, _, _, _, _, attempt = wi_h._setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="t-c1", claim=False
    )
    brief.campaign_spec = SPEC
    db_session.flush()
    prep = prepare_social_idea_worker_inputs(db_session, attempt_id=attempt.id, task_id="t-c1", now=wi_h.T0)
    assert prep.prompt_inputs and all(p.campaign == SPEC for p in prep.prompt_inputs)


def test_retry_worker_input_carries_campaign_spec(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    _, _, brief, _, _, _, _, _, retry_att = retry_wi_h._setup_ready_retry_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="t-c2"
    )
    brief.campaign_spec = SPEC
    db_session.flush()
    prep = prepare_social_idea_retry_worker_inputs(
        db_session, attempt_id=retry_att.id, task_id="t-c2", now=retry_wi_h.T0
    )
    assert prep.prompt_inputs and all(p.campaign == SPEC for p in prep.prompt_inputs)


def test_persist_writes_campaign_payload_and_read_returns_it(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    ws, _, brief, _, targets, kws, start, attempt = ideas_h._setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="t-c3"
    )
    results = ideas_h._build_valid_category_results(start.plan, attempt.id, targets, kws)
    persist_social_ideas(
        db_session, attempt_id=attempt.id, task_id="t-c3", category_results=_with_payload(results)
    )
    db_session.commit()

    rows = db_session.query(SocialIdea).filter_by(brief_id=brief.id).all()
    assert rows and all(r.campaign_payload == PAYLOAD for r in rows)

    read = load_social_idea_result(
        db_session, brief_id=brief.id, attempt_id=attempt.id, brand_profile_id=ws.id
    )
    assert read.ideas and all(i.campaign_payload == PAYLOAD for i in read.ideas)


def test_organic_persist_leaves_campaign_payload_null(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    _, _, brief, _, targets, kws, start, attempt = ideas_h._setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="t-c4"
    )
    results = ideas_h._build_valid_category_results(start.plan, attempt.id, targets, kws)
    persist_social_ideas(db_session, attempt_id=attempt.id, task_id="t-c4", category_results=results)
    db_session.commit()
    rows = db_session.query(SocialIdea).filter_by(brief_id=brief.id).all()
    assert rows and all(r.campaign_payload is None for r in rows)


def test_retry_persist_writes_campaign_payload(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    _, _, kws, brief, _, targets, _, start, retry_att = retry_h._setup_ready_retry_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="t-c5",
        persist_first_target=True, targets_data=[("instagram", "post"), ("twitter", "thread")],
        num_categories=1,
    )
    results = retry_h._build_valid_retry_results(start.plan, retry_att.id, targets, kws)
    res = persist_social_idea_retry_results(
        db_session, attempt_id=retry_att.id, task_id="t-c5",
        category_results=_with_payload(results), now=retry_h.T0 + timedelta(seconds=60),
    )
    new_ids = [i.id for i in res.newly_persisted_ideas]
    rows = db_session.query(SocialIdea).filter(SocialIdea.id.in_(new_ids)).all()
    assert rows and all(r.campaign_payload == PAYLOAD for r in rows)
