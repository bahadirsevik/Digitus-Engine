"""Integration tests for /api/v1/dashboard/workspace-summary (P5.1)."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _disable_redis(monkeypatch):
    """Bypass Redis for every test in this module.

    The summary endpoint reads/writes Redis when available; in CI we want a
    deterministic miss + write that we can ignore. Returning None forces both
    paths to no-op.
    """
    from app.api.v1 import dashboard as dashboard_module
    from app.api.v1 import google_ads as gads_module

    monkeypatch.setattr(gads_module, "_get_redis_client", lambda: None)
    monkeypatch.setattr(dashboard_module, "_get_redis_client", lambda: None)
    monkeypatch.setattr(dashboard_module, "_gads_health", lambda *, refresh: ("ok", None))


def test_summary_without_brand_profile_returns_empty(client):
    res = client.get("/api/v1/dashboard/workspace-summary")
    assert res.status_code == 200
    body = res.json()
    assert body["workspace"] is None
    assert body["active_run"] is None
    assert body["runs"] == []
    assert body["next_action"]["key"] == "create_workspace"
    assert body["health"]["api"] == "ok"


def test_summary_with_workspace_no_keywords(client, make_workspace):
    ws = make_workspace(status="draft")
    res = client.get(f"/api/v1/dashboard/workspace-summary?brand_profile_id={ws.id}")
    assert res.status_code == 200
    body = res.json()
    assert body["workspace"]["id"] == ws.id
    assert body["next_action"]["key"] == "confirm_profile"


def test_summary_confirmed_no_keywords_returns_create_keywords(
    client, make_workspace
):
    ws = make_workspace(status="confirmed", profile_data={"anchor_texts": ["x"]})
    res = client.get(f"/api/v1/dashboard/workspace-summary?brand_profile_id={ws.id}")
    assert res.status_code == 200
    body = res.json()
    assert body["next_action"]["key"] == "create_keywords"


def test_summary_confirmed_with_keywords_no_run_returns_start_scoring(
    client, make_workspace, make_keyword
):
    ws = make_workspace(status="confirmed", profile_data={"anchor_texts": ["x"]})
    make_keyword("kw1", brand_profile_id=ws.id)
    res = client.get(f"/api/v1/dashboard/workspace-summary?brand_profile_id={ws.id}")
    assert res.status_code == 200
    body = res.json()
    assert body["keyword_count"] == 1
    assert body["next_action"]["key"] == "start_scoring"


def test_summary_with_active_run_id_override(
    client, make_workspace, make_keyword, make_scoring_run
):
    ws = make_workspace(status="confirmed", profile_data={"anchor_texts": ["x"]})
    make_keyword("kw1", brand_profile_id=ws.id)
    older = make_scoring_run(brand_profile_id=ws.id, status="pending")
    newer = make_scoring_run(brand_profile_id=ws.id, status="failed")

    # default picks latest (newer) — should be failed
    res = client.get(f"/api/v1/dashboard/workspace-summary?brand_profile_id={ws.id}")
    assert res.json()["active_run"]["id"] == newer.id
    assert res.json()["next_action"]["key"] == "check_failed_run"

    # explicit override picks older
    res2 = client.get(
        f"/api/v1/dashboard/workspace-summary"
        f"?brand_profile_id={ws.id}&active_run_id={older.id}"
    )
    assert res2.json()["active_run"]["id"] == older.id


def _relevance_step(body):
    return next(s for s in body["pipeline"] if s["key"] == "relevance")


def test_summary_v3_run_relevance_step_is_not_applicable(
    client, make_workspace, make_keyword, make_scoring_run
):
    ws = make_workspace(status="confirmed", profile_data={"anchor_texts": ["x"]})
    make_keyword("kw1", brand_profile_id=ws.id)
    make_scoring_run(brand_profile_id=ws.id, status="scored", algorithm_version="v3")

    res = client.get(f"/api/v1/dashboard/workspace-summary?brand_profile_id={ws.id}")
    step = _relevance_step(res.json())
    assert step["state"] == "skipped"
    assert step["detail"] == "V3'te kullanılmaz"
    assert step["path"] is None


def test_summary_legacy_run_relevance_step_unchanged(
    client, make_workspace, make_keyword, make_scoring_run
):
    ws = make_workspace(status="confirmed", profile_data={"anchor_texts": ["x"]})
    make_keyword("kw1", brand_profile_id=ws.id)
    run = make_scoring_run(brand_profile_id=ws.id, status="scored", algorithm_version="v2")

    res = client.get(f"/api/v1/dashboard/workspace-summary?brand_profile_id={ws.id}")
    step = _relevance_step(res.json())
    assert step["state"] == "pending"
    assert step["detail"] == "bekliyor"
    assert step["path"] == f"/relevance?run_id={run.id}"


def test_summary_cross_workspace_run_returns_404(
    client, make_workspace, make_scoring_run
):
    ws1 = make_workspace(name="ws1", status="confirmed", profile_data={"x": 1})
    ws2 = make_workspace(name="ws2", status="confirmed", profile_data={"x": 1})
    run_in_ws2 = make_scoring_run(brand_profile_id=ws2.id)

    res = client.get(
        f"/api/v1/dashboard/workspace-summary"
        f"?brand_profile_id={ws1.id}&active_run_id={run_in_ws2.id}"
    )
    assert res.status_code == 404


def test_summary_unknown_workspace_returns_404(client):
    res = client.get("/api/v1/dashboard/workspace-summary?brand_profile_id=999999")
    assert res.status_code == 404


def test_summary_health_field_includes_google_ads(client):
    res = client.get("/api/v1/dashboard/workspace-summary")
    assert res.json()["health"]["google_ads"] == "ok"


def _ads_review_setup(db_session, make_workspace, make_keyword, make_scoring_run, *, set_status):
    from decimal import Decimal

    from app.database.models import AdGenerationSet, ChannelPool
    from app.generators.claim_review import ads_set_warnings

    ws = make_workspace(status="confirmed", profile_data={"anchor_texts": ["x"]})
    kw = make_keyword("kw1", brand_profile_id=ws.id)
    run = make_scoring_run(brand_profile_id=ws.id, status="channel_assigned")
    db_session.add(
        ChannelPool(
            scoring_run_id=run.id, keyword_id=kw.id, channel="ADS",
            final_rank=1, adjusted_score=Decimal("1.0"),
        )
    )
    gen_set = AdGenerationSet(
        scoring_run_id=run.id, version_number=1, status=set_status, groups_count=1,
        warnings=ads_set_warnings([], [{"kind": "risk", "category": "guarantee", "span": "garanti"}], []),
    )
    db_session.add(gen_set)
    db_session.commit()
    return ws, run, gen_set


def test_summary_ads_draft_in_review_sets_cue_and_next_action(
    client, db_session, make_workspace, make_keyword, make_scoring_run
):
    ws, run, gen_set = _ads_review_setup(
        db_session, make_workspace, make_keyword, make_scoring_run, set_status="draft"
    )
    body = client.get(
        f"/api/v1/dashboard/workspace-summary?brand_profile_id={ws.id}&active_run_id={run.id}"
    ).json()
    assert body["channels"]["ADS"]["review_pending"] is True
    assert body["channels"]["ADS"]["review_set_id"] == gen_set.id
    assert body["channels"]["SEO"]["review_pending"] is False
    assert body["next_action"]["key"] == "review_ads_draft"


def test_summary_ads_active_flagged_set_has_no_review_cue(
    client, db_session, make_workspace, make_keyword, make_scoring_run
):
    ws, run, _ = _ads_review_setup(
        db_session, make_workspace, make_keyword, make_scoring_run, set_status="active"
    )
    body = client.get(
        f"/api/v1/dashboard/workspace-summary?brand_profile_id={ws.id}&active_run_id={run.id}"
    ).json()
    assert body["channels"]["ADS"]["review_pending"] is False
    assert body["next_action"]["key"] != "review_ads_draft"
