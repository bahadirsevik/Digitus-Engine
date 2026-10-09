from copy import deepcopy

import pytest

from app.core.policy.competitor_discovery import (
    apply_competitor_term_decisions,
    canonical_profile_fingerprint,
    fresh_channel_run_count,
    persist_suggestions,
)
from app.core.policy.competitor_policy import approved_competitor_terms
from app.database.models import ChannelPool, TaskResult


def _profile(**overrides):
    value = {
        "company_name": "Örnek AŞ",
        "sector": "finans teknolojisi",
        "brand_summary": "Yatırım araştırma platformu",
        "products": ["Hisse Analizi", "Portföy Takibi"],
        "services": ["Piyasa verisi"],
        "target_audience": "Bireysel yatırımcılar",
    }
    value.update(overrides)
    return value


def test_canonical_profile_fingerprint_is_order_independent(make_workspace):
    first = make_workspace(
        "F1",
        company_url="https://www.example.com/path",
        competitor_urls=["https://b.com", "https://a.com"],
        profile_data=_profile(products=["B", "A"]),
        default_geo_target_id="2792",
        default_language_id="1037",
    )
    second = make_workspace(
        "F2",
        company_url="example.com",
        competitor_urls=["https://a.com/x", "https://b.com"],
        profile_data=_profile(products=["A", "B"]),
        default_geo_target_id="2792",
        default_language_id="1037",
    )
    assert canonical_profile_fingerprint(first) == canonical_profile_fingerprint(second)
    second.profile_data = {**second.profile_data, "irrelevant_metadata": "ignored"}
    assert canonical_profile_fingerprint(first) == canonical_profile_fingerprint(second)
    second.profile_data = {**second.profile_data, "sector": "bankacılık"}
    assert canonical_profile_fingerprint(first) != canonical_profile_fingerprint(second)


def test_suggestion_is_not_approved_and_source_urls_is_explicit(make_workspace):
    workspace = make_workspace("Suggestions", profile_data=_profile())
    added = persist_suggestions(
        workspace,
        [{
            "discovery_id": "d1",
            "term": "RakipCo",
            "candidate_domain": "rakip.co",
            "candidate_url": "https://rakip.co",
            "rationale": "Doğrudan rakip",
            "confidence": "high",
            "verification_status": "direct",
            "evidence_urls": [],
        }],
        canonical_profile_fingerprint(workspace),
    )
    assert added[0]["source_urls"] == []
    assert added[0]["status"] == "suggested"
    assert approved_competitor_terms(workspace) == []


def test_rejected_domain_is_not_suggested_again(make_workspace):
    workspace = make_workspace(
        "Rejected memory",
        profile_data=_profile(),
        competitor_terms=[{
            "discovery_id": "old",
            "term": "Rakip",
            "source": "ai_discovery",
            "status": "rejected",
            "source_urls": [],
            "manual_approved": False,
            "candidate_domain": "rakip.com",
        }],
    )
    added = persist_suggestions(
        workspace,
        [{
            "discovery_id": "new",
            "term": "Rakip Yeni Ad",
            "candidate_domain": "rakip.com",
            "candidate_url": "https://rakip.com",
            "confidence": "high",
            "verification_status": "direct",
        }],
        canonical_profile_fingerprint(workspace),
    )
    assert added == []


def test_edited_discovery_approval_updates_same_record(db_session, make_workspace):
    workspace = make_workspace(
        "Decisions",
        profile_data=_profile(),
        competitor_terms=[{
            "discovery_id": "d1",
            "term": "Rakip Anonim Şirketi",
            "source": "ai_discovery",
            "status": "suggested",
            "source_urls": [],
            "manual_approved": False,
            "candidate_domain": "rakip.com",
        }],
    )
    summary = apply_competitor_term_decisions(db_session, workspace, [{
        "discovery_id": "d1", "decision": "approved", "term": "Rakip"
    }])
    assert summary["policy_changed"] is True
    assert len(workspace.competitor_terms) == 1
    assert workspace.competitor_terms[0]["term"] == "Rakip"
    assert workspace.competitor_terms[0]["status"] == "approved"


def test_discovery_merge_leaves_no_duplicate(db_session, make_workspace):
    workspace = make_workspace(
        "Merge",
        profile_data=_profile(),
        competitor_terms=[
            {
                "term": "Rakip",
                "source": "user",
                "status": "approved",
                "source_urls": [],
                "manual_approved": True,
            },
            {
                "discovery_id": "d1",
                "term": "Rakip AŞ",
                "source": "ai_discovery",
                "status": "suggested",
                "source_urls": [],
                "manual_approved": False,
                "candidate_domain": "rakip.com",
            },
        ],
    )
    apply_competitor_term_decisions(db_session, workspace, [{
        "discovery_id": "d1", "decision": "approved", "term": "Rakip"
    }])
    assert [entry["term"] for entry in workspace.competitor_terms] == ["Rakip"]
    assert workspace.competitor_terms[0]["discovery_matches"][0]["discovery_id"] == "d1"


def test_rejected_collision_does_not_remove_existing_manual_approval(
    db_session, make_workspace
):
    workspace = make_workspace(
        "Reject merge",
        profile_data=_profile(),
        competitor_terms=[
            {
                "term": "Rakip",
                "source": "user",
                "status": "approved",
                "source_urls": [],
                "manual_approved": True,
            },
            {
                "discovery_id": "d1",
                "term": "Rakip AŞ",
                "source": "ai_discovery",
                "status": "suggested",
                "source_urls": [],
                "manual_approved": False,
                "candidate_domain": "rakip.com",
            },
        ],
    )
    apply_competitor_term_decisions(db_session, workspace, [{
        "discovery_id": "d1", "decision": "rejected", "term": "Rakip"
    }])
    assert len(workspace.competitor_terms) == 1
    assert workspace.competitor_terms[0]["status"] == "approved"
    assert workspace.competitor_terms[0]["manual_approved"] is True
    assert workspace.competitor_terms[0]["discovery_rejections"][0]["discovery_id"] == "d1"


def test_duplicate_terms_in_one_bulk_request_are_rejected(db_session, make_workspace):
    workspace = make_workspace("Duplicate", profile_data=_profile())
    with pytest.raises(ValueError, match="DUPLICATE_COMPETITOR_TERM"):
        apply_competitor_term_decisions(db_session, workspace, [
            {"discovery_id": "a", "decision": "approved", "term": "Rakip"},
            {"discovery_id": "b", "decision": "approved", "term": "rakip"},
        ])


def test_fresh_count_uses_central_skip_relevance_semantics(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    workspace = make_workspace("Fresh", profile_data=_profile(), anchor_version=2)
    keyword = make_keyword("finans", brand_profile_id=workspace.id)
    fresh_skip = make_scoring_run(
        brand_profile_id=workspace.id,
        status="channel_assigned",
        skip_relevance=True,
        relevance_anchor_version=1,
    )
    stale_policy = make_scoring_run(
        brand_profile_id=workspace.id,
        status="channel_assigned",
        skip_relevance=True,
        channel_pool_policy_version=0,
    )
    no_pool = make_scoring_run(
        brand_profile_id=workspace.id,
        status="channel_assigned",
        skip_relevance=True,
    )
    db_session.add_all([
        ChannelPool(scoring_run_id=fresh_skip.id, keyword_id=keyword.id, channel="ADS", final_rank=1),
        ChannelPool(scoring_run_id=stale_policy.id, keyword_id=keyword.id, channel="SEO", final_rank=1),
    ])
    db_session.commit()
    assert no_pool.id
    assert fresh_channel_run_count(db_session, workspace.id) == 1


def test_policy_payload_exposes_discovery_metadata(client, make_workspace):
    workspace = make_workspace(
        "Payload",
        profile_data=_profile(),
        competitor_terms=[{
            "discovery_id": "d1",
            "term": "Rakip",
            "source": "ai_discovery",
            "status": "suggested",
            "source_urls": [],
            "manual_approved": False,
            "candidate_domain": "rakip.com",
            "candidate_url": "https://rakip.com",
            "confidence": "high",
            "profile_fingerprint": "old",
        }],
    )
    response = client.get(f"/api/v1/brand-profile/workspaces/{workspace.id}/policy")
    assert response.status_code == 200
    data = response.json()
    assert data["competitor_terms"][0]["candidate_domain"] == "rakip.com"
    assert data["discovery_impact"]["has_stale_suggestions"] is True


def test_decisions_reject_stale_profile(client, db_session, make_workspace):
    workspace = make_workspace("Stale", profile_data=_profile())
    fingerprint = canonical_profile_fingerprint(workspace)
    workspace.competitor_terms = [{
        "discovery_id": "d1",
        "term": "Rakip",
        "source": "ai_discovery",
        "status": "suggested",
        "source_urls": [],
        "manual_approved": False,
        "candidate_domain": "rakip.com",
        "profile_fingerprint": fingerprint,
    }]
    # API çağrısından önce discovery girdisini değiştir.
    workspace.profile_data = {**deepcopy(workspace.profile_data), "sector": "başka"}
    db_session.commit()
    response = client.post(
        f"/api/v1/brand-profile/workspaces/{workspace.id}/policy/competitor-discovery/decisions",
        json={"decisions": [{
            "discovery_id": "d1", "decision": "approved", "term": "Rakip"
        }]},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "DISCOVERY_STALE"


def test_approved_competitor_can_be_removed_after_profile_change(
    client, db_session, make_workspace,
):
    workspace = make_workspace("Approved stale", profile_data=_profile())
    fingerprint = canonical_profile_fingerprint(workspace)
    workspace.competitor_terms = [{
        "discovery_id": "approved-1",
        "term": "Rakip",
        "source": "ai_discovery",
        "status": "approved",
        "source_urls": [],
        "manual_approved": True,
        "candidate_domain": "rakip.com",
        "profile_fingerprint": fingerprint,
    }]
    workspace.profile_data = {**deepcopy(workspace.profile_data), "sector": "başka"}
    db_session.commit()

    response = client.post(
        f"/api/v1/brand-profile/workspaces/{workspace.id}/policy/competitor-discovery/decisions",
        json={"decisions": [{
            "discovery_id": "approved-1", "decision": "rejected", "term": "Rakip"
        }]},
    )
    assert response.status_code == 200
    entry = response.json()["competitor_terms"][0]
    assert entry["status"] == "rejected"


def test_policy_mutation_blocked_while_keyword_generation_running(
    client, db_session, make_workspace,
):
    """Keyword üretimi TaskResult'suz BackgroundTask — status='running' iken
    politika mutasyonu (keşif kararı dahil) 409 almalı, yoksa üretim
    sürerken onaylanan rakip o koşunun prompt/son-filtresine giremez
    (Codex bulgusu, 22.07)."""
    workspace = make_workspace(
        "Running race",
        status="running",
        onboarding_flow="profile_first",
        profile_data=_profile(),
    )
    fingerprint = canonical_profile_fingerprint(workspace)
    workspace.competitor_terms = [{
        "discovery_id": "d-race",
        "term": "Rakip",
        "source": "ai_discovery",
        "status": "suggested",
        "source_urls": [],
        "manual_approved": False,
        "candidate_domain": "rakip.com",
        "profile_fingerprint": fingerprint,
    }]
    db_session.commit()

    decisions = client.post(
        f"/api/v1/brand-profile/workspaces/{workspace.id}/policy/competitor-discovery/decisions",
        json={"decisions": [{
            "discovery_id": "d-race", "decision": "approved", "term": "Rakip"
        }]},
    )
    assert decisions.status_code == 409
    assert decisions.json()["detail"]["code"] == "ANALYSIS_RUNNING"

    channel_policy = client.put(
        f"/api/v1/brand-profile/workspaces/{workspace.id}/policy/competitor-policy",
        json={"ads": "allow", "seo": "block", "social": "block"},
    )
    assert channel_policy.status_code == 409
    assert channel_policy.json()["detail"]["code"] == "ANALYSIS_RUNNING"

    # Üretim bitince (keywords_review) aynı karar sorunsuz geçer.
    workspace.status = "keywords_review"
    db_session.commit()
    ok = client.post(
        f"/api/v1/brand-profile/workspaces/{workspace.id}/policy/competitor-discovery/decisions",
        json={"decisions": [{
            "discovery_id": "d-race", "decision": "approved", "term": "Rakip"
        }]},
    )
    assert ok.status_code == 200


def test_discovery_can_start_in_competitor_review(
    client, monkeypatch, make_workspace,
):
    from app.tasks.competitor_discovery_tasks import run_competitor_discovery_task

    dispatched = []
    monkeypatch.setattr(
        run_competitor_discovery_task,
        "apply_async",
        lambda args, task_id: dispatched.append((args, task_id)),
    )
    workspace = make_workspace(
        "Discovery onboarding",
        status="competitor_review",
        onboarding_flow="profile_first",
        profile_data=_profile(),
    )
    response = client.post(
        f"/api/v1/brand-profile/workspaces/{workspace.id}/policy/competitor-discovery",
        json={"force_refresh": False},
    )
    assert response.status_code == 202
    assert dispatched and dispatched[0][0][0] == workspace.id


def test_worker_profile_change_drops_candidates(
    db_session, make_workspace, monkeypatch
):
    import app.core.policy.competitor_discovery as discovery_mod
    import app.generators.ai_service as ai_mod
    from app.database.connection import SessionLocal
    from app.database.models import BrandProfile
    from app.tasks.competitor_discovery_tasks import run_competitor_discovery_task

    workspace = make_workspace("Worker race", profile_data=_profile())
    fingerprint = canonical_profile_fingerprint(workspace)
    task_id = "discovery-race"
    db_session.add(TaskResult(
        task_id=task_id,
        task_type="competitor_discovery",
        brand_profile_id=workspace.id,
        status="pending",
        progress=0,
        result_data={"profile_fingerprint": fingerprint},
    ))
    db_session.commit()

    class FakeAI:
        collector = None

        def close(self):
            pass

    monkeypatch.setattr(ai_mod, "get_ai_service", lambda **kwargs: FakeAI())
    monkeypatch.setattr(discovery_mod, "discover_raw_candidates", lambda ai, ws: {
        "candidates": [], "search_queries": [], "evidence_urls": [],
        "fallback_used": False,
    })

    def change_profile(ai, ws, raw):
        other = SessionLocal()
        try:
            row = other.get(BrandProfile, ws.id)
            row.profile_data = {**row.profile_data, "sector": "değişti"}
            other.commit()
        finally:
            other.close()
        return [{
            "discovery_id": "d-race",
            "term": "Race Rakip",
            "candidate_domain": "race-rakip.com",
        }]

    monkeypatch.setattr(discovery_mod, "verify_discovered_candidates", change_profile)
    result = run_competitor_discovery_task.apply(
        args=[workspace.id, fingerprint], task_id=task_id
    ).get()
    assert result["code"] == "PROFILE_CHANGED"
    db_session.expire_all()
    saved = db_session.query(TaskResult).filter_by(task_id=task_id).one()
    refreshed = db_session.get(BrandProfile, workspace.id)
    assert saved.status == "failed"
    assert saved.result_data["code"] == "PROFILE_CHANGED"
    assert not any(
        entry.get("discovery_id") == "d-race"
        for entry in (refreshed.competitor_terms or [])
    )
