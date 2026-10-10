"""Integration tests for Social Brief Flow Phase F1-B (Staleness propagation & brief chain).

Covers all F1-B requirements:
a. Reassignment: brief + category + idea + content becomes stale;
   run.channel_assignment_version increments; brief snapshot version remains unchanged.
b. Legacy chain: rows with brief_id IS NULL become stale across category/idea/content.
c. Workspace invalidation (invalidate_workspace_outputs):
   brief chains for matching runs become stale; run version unchanged; no commit performed.
d. Workspace isolation: invalidation on workspace A does not affect workspace B.
e. Algorithm version filter: only matching algorithm_version runs have brief chain marked stale.
f. Keyword refresh wrapper (mark_workspace_content_stale):
   all runs' brief chains in workspace become stale; other workspaces unaffected; single commit; version unchanged.
g. Single-run wrapper (mark_run_content_stale):
   only target run's brief chain marked stale; other runs unaffected; single commit; version unchanged.
h. Rollback behavior: _mark_run_outputs_stale does not commit, rollback restores fresh state.
i. Idempotence: calling stale multiple times does not raise and preserves stale state.
j. ADS and ContentOutput non-regression: AdGenerationSet and ContentOutput become stale alongside brief chain.
"""
from __future__ import annotations

import pytest
from app.core.scoring.state_machine import (
    _mark_run_outputs_stale,
    begin_channel_assignment,
    invalidate_workspace_outputs,
    mark_run_content_stale,
    mark_workspace_content_stale,
    transition,
)
from app.database.models import (
    AdGenerationSet,
    BrandProfile,
    ContentOutput,
    Keyword,
    ScoringRun,
    SocialBrief,
    SocialCategory,
    SocialContent,
    SocialIdea,
)


def _create_brief_chain(db, run_id: int, channel_assignment_version: int = 1):
    brief = SocialBrief(
        scoring_run_id=run_id,
        brand_name_snapshot="Test Brand",
        brand_context_snapshot="Test Context",
        channel_assignment_version=channel_assignment_version,
        format_matrix_version="v1",
        is_stale=False,
    )
    db.add(brief)
    db.flush()

    cat = SocialCategory(
        scoring_run_id=run_id,
        brief_id=brief.id,
        category_name="Eğitim",
        category_type="educational",
        is_stale=False,
    )
    db.add(cat)
    db.flush()

    idea = SocialIdea(
        category_id=cat.id,
        brief_id=brief.id,
        idea_title="Fikir 1",
        target_platform="instagram",
        content_format="reels",
        is_stale=False,
    )
    db.add(idea)
    db.flush()

    content = SocialContent(
        idea_id=idea.id,
        brief_id=brief.id,
        caption="Brief Reel Caption",
        is_stale=False,
    )
    db.add(content)
    db.flush()

    return brief, cat, idea, content


def _create_legacy_chain(db, run_id: int):
    cat = SocialCategory(
        scoring_run_id=run_id,
        brief_id=None,
        category_name="Legacy Eğitim",
        category_type="educational",
        is_stale=False,
    )
    db.add(cat)
    db.flush()

    idea = SocialIdea(
        category_id=cat.id,
        brief_id=None,
        idea_title="Legacy Fikir",
        target_platform="instagram",
        content_format="post",
        is_stale=False,
    )
    db.add(idea)
    db.flush()

    content = SocialContent(
        idea_id=idea.id,
        brief_id=None,
        caption="Legacy Caption",
        is_stale=False,
    )
    db.add(content)
    db.flush()

    return cat, idea, content


class TestSocialBriefStalenessF1B:
    def test_reassignment_stales_brief_chain_and_bumps_run_version(
        self, db_session, make_workspace, make_scoring_run
    ):
        """Scenario a: transition to channel_assigning marks brief + full child chain stale,
        increments ScoringRun.channel_assignment_version, and preserves SocialBrief's
        channel_assignment_version snapshot."""
        ws = make_workspace("Stale WS")
        run = make_scoring_run(
            brand_profile_id=ws.id,
            status="channel_assigned",
            channel_assignment_version=1,
        )
        brief, cat, idea, content = _create_brief_chain(
            db_session, run.id, channel_assignment_version=1
        )
        db_session.commit()

        brief_id = brief.id
        cat_id = cat.id
        idea_id = idea.id
        content_id = content.id

        # Trigger channel reassignment transition
        transition(db_session, run, "channel_assigning")

        db_session.expire_all()
        refreshed_run = db_session.get(ScoringRun, run.id)
        refreshed_brief = db_session.get(SocialBrief, brief_id)
        refreshed_cat = db_session.get(SocialCategory, cat_id)
        refreshed_idea = db_session.get(SocialIdea, idea_id)
        refreshed_content = db_session.get(SocialContent, content_id)

        assert refreshed_run.status == "channel_assigning"
        assert refreshed_run.channel_assignment_version == 2
        # Snapshot on brief is untouched
        assert refreshed_brief.channel_assignment_version == 1
        # Entire social brief chain is stale
        assert refreshed_brief.is_stale is True
        assert refreshed_cat.is_stale is True
        assert refreshed_idea.is_stale is True
        assert refreshed_content.is_stale is True

    def test_reassignment_stales_legacy_chain(
        self, db_session, make_workspace, make_scoring_run
    ):
        """Scenario b: legacy rows with brief_id IS NULL become stale across category/idea/content."""
        ws = make_workspace("Legacy Stale WS")
        run = make_scoring_run(
            brand_profile_id=ws.id,
            status="channel_assigned",
            channel_assignment_version=1,
        )
        cat, idea, content = _create_legacy_chain(db_session, run.id)
        db_session.commit()

        cat_id = cat.id
        idea_id = idea.id
        content_id = content.id

        transition(db_session, run, "channel_assigning")

        db_session.expire_all()
        refreshed_cat = db_session.get(SocialCategory, cat_id)
        refreshed_idea = db_session.get(SocialIdea, idea_id)
        refreshed_content = db_session.get(SocialContent, content_id)

        assert refreshed_cat.brief_id is None
        assert refreshed_cat.is_stale is True
        assert refreshed_idea.brief_id is None
        assert refreshed_idea.is_stale is True
        assert refreshed_content.brief_id is None
        assert refreshed_content.is_stale is True

    def test_begin_channel_assignment_stales_brief_chain(
        self, db_session, make_workspace, make_scoring_run
    ):
        """Dispatcher path: begin_channel_assignment CAS executes side effects in same transaction."""
        ws = make_workspace("Dispatcher CAS WS")
        run = make_scoring_run(
            brand_profile_id=ws.id,
            status="scored",
            channel_assignment_version=1,
        )
        brief, cat, idea, content = _create_brief_chain(
            db_session, run.id, channel_assignment_version=1
        )
        db_session.commit()

        success = begin_channel_assignment(db_session, run, from_status="scored")
        assert success is True

        db_session.expire_all()
        refreshed_run = db_session.get(ScoringRun, run.id)
        refreshed_brief = db_session.get(SocialBrief, brief.id)
        refreshed_cat = db_session.get(SocialCategory, cat.id)
        refreshed_idea = db_session.get(SocialIdea, idea.id)
        refreshed_content = db_session.get(SocialContent, content.id)

        assert refreshed_run.status == "channel_assigning"
        assert refreshed_run.channel_assignment_version == 2
        assert refreshed_brief.channel_assignment_version == 1
        assert refreshed_brief.is_stale is True
        assert refreshed_cat.is_stale is True
        assert refreshed_idea.is_stale is True
        assert refreshed_content.is_stale is True

    def test_workspace_invalidation_stales_brief_chain_without_version_bump(
        self, db_session, make_workspace, make_scoring_run
    ):
        """Scenario c: invalidate_workspace_outputs marks brief chain stale without bumping channel_assignment_version,
        and does not commit on its own."""
        ws = make_workspace("Invalidate WS")
        run = make_scoring_run(
            brand_profile_id=ws.id,
            status="channel_assigned",
            channel_assignment_version=1,
        )
        brief, cat, idea, content = _create_brief_chain(
            db_session, run.id, channel_assignment_version=1
        )
        db_session.commit()

        stale_count = invalidate_workspace_outputs(db_session, ws.id)
        assert stale_count >= 4  # brief + category + idea + content

        # Check in current uncommitted session
        assert db_session.get(SocialBrief, brief.id).is_stale is True
        assert db_session.get(SocialCategory, cat.id).is_stale is True
        assert db_session.get(SocialIdea, idea.id).is_stale is True
        assert db_session.get(SocialContent, content.id).is_stale is True

        db_session.commit()
        db_session.expire_all()

        refreshed_run = db_session.get(ScoringRun, run.id)
        assert refreshed_run.channel_assignment_version == 1  # Version must NOT change
        assert db_session.get(SocialBrief, brief.id).is_stale is True

    def test_workspace_isolation(
        self, db_session, make_workspace, make_scoring_run
    ):
        """Scenario d: invalidation on workspace A does not affect workspace B."""
        ws_a = make_workspace("Workspace A")
        ws_b = make_workspace("Workspace B")

        run_a = make_scoring_run(brand_profile_id=ws_a.id, status="channel_assigned")
        run_b = make_scoring_run(brand_profile_id=ws_b.id, status="channel_assigned")

        brief_a, cat_a, idea_a, content_a = _create_brief_chain(db_session, run_a.id)
        brief_b, cat_b, idea_b, content_b = _create_brief_chain(db_session, run_b.id)
        db_session.commit()

        invalidate_workspace_outputs(db_session, ws_a.id)
        db_session.commit()
        db_session.expire_all()

        # Workspace A is stale
        assert db_session.get(SocialBrief, brief_a.id).is_stale is True
        assert db_session.get(SocialCategory, cat_a.id).is_stale is True
        assert db_session.get(SocialIdea, idea_a.id).is_stale is True
        assert db_session.get(SocialContent, content_a.id).is_stale is True

        # Workspace B is FRESH
        assert db_session.get(SocialBrief, brief_b.id).is_stale is False
        assert db_session.get(SocialCategory, cat_b.id).is_stale is False
        assert db_session.get(SocialIdea, idea_b.id).is_stale is False
        assert db_session.get(SocialContent, content_b.id).is_stale is False

    def test_algorithm_version_filter(
        self, db_session, make_workspace, make_scoring_run
    ):
        """Scenario e: only_algorithm_version filter selectively marks only matching runs."""
        ws = make_workspace("Alg Filter WS")
        run_v2_1 = make_scoring_run(
            brand_profile_id=ws.id,
            status="channel_assigned",
            algorithm_version="v2_1",
        )
        run_v3 = make_scoring_run(
            brand_profile_id=ws.id,
            status="channel_assigned",
            algorithm_version="v3",
        )

        brief_v2_1, cat_v2_1, idea_v2_1, content_v2_1 = _create_brief_chain(
            db_session, run_v2_1.id
        )
        brief_v3, cat_v3, idea_v3, content_v3 = _create_brief_chain(
            db_session, run_v3.id
        )
        db_session.commit()

        # Invalidate only v2_1
        invalidate_workspace_outputs(db_session, ws.id, only_algorithm_version="v2_1")
        db_session.commit()
        db_session.expire_all()

        # v2_1 is stale
        assert db_session.get(SocialBrief, brief_v2_1.id).is_stale is True
        assert db_session.get(SocialCategory, cat_v2_1.id).is_stale is True
        assert db_session.get(SocialIdea, idea_v2_1.id).is_stale is True
        assert db_session.get(SocialContent, content_v2_1.id).is_stale is True

        # v3 is FRESH
        assert db_session.get(SocialBrief, brief_v3.id).is_stale is False
        assert db_session.get(SocialCategory, cat_v3.id).is_stale is False
        assert db_session.get(SocialIdea, idea_v3.id).is_stale is False
        assert db_session.get(SocialContent, content_v3.id).is_stale is False

    def test_mark_workspace_content_stale_wrapper(
        self, db_session, make_workspace, make_scoring_run
    ):
        """Scenario f: mark_workspace_content_stale stales all runs in workspace, commits,
        does not affect other workspaces, and does not bump version."""
        ws = make_workspace("Keyword Refresh WS")
        ws_other = make_workspace("Other WS")

        run1 = make_scoring_run(
            brand_profile_id=ws.id, status="channel_assigned", channel_assignment_version=1
        )
        run2 = make_scoring_run(
            brand_profile_id=ws.id, status="channel_assigned", channel_assignment_version=2
        )
        run_other = make_scoring_run(
            brand_profile_id=ws_other.id, status="channel_assigned", channel_assignment_version=1
        )

        b1, c1, i1, ct1 = _create_brief_chain(db_session, run1.id)
        b2, c2, i2, ct2 = _create_brief_chain(db_session, run2.id)
        bo, co, io, cto = _create_brief_chain(db_session, run_other.id)
        db_session.commit()

        stale_count = mark_workspace_content_stale(db_session, ws.id)
        assert stale_count >= 8  # 4 from run1 + 4 from run2

        db_session.expire_all()

        # Both runs in ws are stale
        assert db_session.get(SocialBrief, b1.id).is_stale is True
        assert db_session.get(SocialContent, ct1.id).is_stale is True
        assert db_session.get(SocialBrief, b2.id).is_stale is True
        assert db_session.get(SocialContent, ct2.id).is_stale is True

        # Other workspace is untouched
        assert db_session.get(SocialBrief, bo.id).is_stale is False
        assert db_session.get(SocialContent, cto.id).is_stale is False

        # Versions are NOT bumped
        assert db_session.get(ScoringRun, run1.id).channel_assignment_version == 1
        assert db_session.get(ScoringRun, run2.id).channel_assignment_version == 2

    def test_mark_workspace_content_stale_empty_runs(
        self, db_session, make_workspace
    ):
        """Safe return when workspace has no runs."""
        ws = make_workspace("Empty Runs WS")
        stale_count = mark_workspace_content_stale(db_session, ws.id)
        assert stale_count == 0

    def test_mark_run_content_stale_wrapper(
        self, db_session, make_workspace, make_scoring_run
    ):
        """Scenario g: mark_run_content_stale affects only target run and commits without bumping version."""
        ws = make_workspace("Manual Pool Change WS")
        run1 = make_scoring_run(
            brand_profile_id=ws.id, status="channel_assigned", channel_assignment_version=1
        )
        run2 = make_scoring_run(
            brand_profile_id=ws.id, status="channel_assigned", channel_assignment_version=1
        )

        b1, c1, i1, ct1 = _create_brief_chain(db_session, run1.id)
        b2, c2, i2, ct2 = _create_brief_chain(db_session, run2.id)
        db_session.commit()

        stale_count = mark_run_content_stale(db_session, run1.id)
        assert stale_count >= 4

        db_session.expire_all()

        # Run 1 is stale
        assert db_session.get(SocialBrief, b1.id).is_stale is True
        assert db_session.get(SocialCategory, c1.id).is_stale is True
        assert db_session.get(SocialIdea, i1.id).is_stale is True
        assert db_session.get(SocialContent, ct1.id).is_stale is True
        assert db_session.get(ScoringRun, run1.id).channel_assignment_version == 1

        # Run 2 is FRESH
        assert db_session.get(SocialBrief, b2.id).is_stale is False
        assert db_session.get(SocialCategory, c2.id).is_stale is False
        assert db_session.get(SocialIdea, i2.id).is_stale is False
        assert db_session.get(SocialContent, ct2.id).is_stale is False

    def test_rollback_behavior_leaves_brief_chain_fresh(
        self, db_session, make_workspace, make_scoring_run
    ):
        """Scenario h: _mark_run_outputs_stale does not commit, rollback restores fresh state."""
        ws = make_workspace("Rollback WS")
        run = make_scoring_run(brand_profile_id=ws.id, status="channel_assigned")
        brief, cat, idea, content = _create_brief_chain(db_session, run.id)
        db_session.commit()

        _mark_run_outputs_stale(db_session, run.id)
        # Explicit rollback before commit
        db_session.rollback()

        db_session.expire_all()
        assert db_session.get(SocialBrief, brief.id).is_stale is False
        assert db_session.get(SocialCategory, cat.id).is_stale is False
        assert db_session.get(SocialIdea, idea.id).is_stale is False
        assert db_session.get(SocialContent, content.id).is_stale is False

    def test_idempotence_and_repeated_invalidation(
        self, db_session, make_workspace, make_scoring_run
    ):
        """Scenario i: calling invalidation multiple times on already stale records does not fail."""
        ws = make_workspace("Idempotent WS")
        run = make_scoring_run(brand_profile_id=ws.id, status="channel_assigned")
        brief, cat, idea, content = _create_brief_chain(db_session, run.id)
        db_session.commit()

        # First call
        count1 = _mark_run_outputs_stale(db_session, run.id)
        db_session.commit()
        assert count1 >= 4

        # Second call
        count2 = _mark_run_outputs_stale(db_session, run.id)
        db_session.commit()
        assert count2 >= 4

        db_session.expire_all()
        assert db_session.get(SocialBrief, brief.id).is_stale is True
        assert db_session.get(SocialCategory, cat.id).is_stale is True
        assert db_session.get(SocialIdea, idea.id).is_stale is True
        assert db_session.get(SocialContent, content.id).is_stale is True

    def test_ads_and_content_output_non_regression(
        self, db_session, make_workspace, make_scoring_run, make_keyword
    ):
        """Scenario j: AdGenerationSet and ContentOutput become stale alongside brief chain."""
        ws = make_workspace("Non Regression WS")
        run = make_scoring_run(brand_profile_id=ws.id, status="channel_assigned")

        kw = make_keyword("test kw", brand_profile_id=ws.id)
        content_out = ContentOutput(
            scoring_run_id=run.id,
            keyword_id=kw.id,
            channel="SEO",
            content_type="blog_post",
            content_data={"title": "Test Title"},
            is_stale=False,
        )
        db_session.add(content_out)

        ad_set = AdGenerationSet(
            scoring_run_id=run.id,
            version_number=1,
            status="active",
            is_stale=False,
        )
        db_session.add(ad_set)

        brief, cat, idea, content = _create_brief_chain(db_session, run.id)
        db_session.commit()

        _mark_run_outputs_stale(db_session, run.id)
        db_session.commit()
        db_session.expire_all()

        assert db_session.get(ContentOutput, content_out.id).is_stale is True
        assert db_session.get(AdGenerationSet, ad_set.id).is_stale is True
        assert db_session.get(SocialBrief, brief.id).is_stale is True
        assert db_session.get(SocialCategory, cat.id).is_stale is True
        assert db_session.get(SocialIdea, idea.id).is_stale is True
        assert db_session.get(SocialContent, content.id).is_stale is True

    def test_workspace_keyword_refresh_atomic_rollback_on_staleness_failure(
        self, client, db_session, make_workspace, make_keyword, monkeypatch
    ):
        """F1-B.1: In refresh_workspace_keywords, keyword mutation and mark_workspace_content_stale
        run in the SAME transaction. If mark_workspace_content_stale raises an exception, the
        try/except block rolls back the session, ensuring old WorkspaceKeyword records remain intact."""
        from app.database.models import WorkspaceKeyword

        ws = make_workspace("Refresh Rollback WS")
        kw1 = make_keyword("refresh kw 1", brand_profile_id=ws.id, monthly_volume=100)
        kw2 = make_keyword("refresh kw 2", brand_profile_id=ws.id, monthly_volume=200)

        original_wk_ids = {
            wk.keyword_id: (wk.id, wk.monthly_volume)
            for wk in db_session.query(WorkspaceKeyword).filter_by(brand_profile_id=ws.id).all()
        }
        assert len(original_wk_ids) == 2

        # Mock Google Ads service so enrich_keywords returns empty ideas (preserving existing keywords)
        class MockGoogleAdsService:
            def enrich_keywords(self, **kwargs):
                return [], 0, 0

        monkeypatch.setattr("app.api.v1.google_ads._get_service", lambda: MockGoogleAdsService())

        # Monkeypatch mark_workspace_content_stale to raise RuntimeError
        def _failing_workspace_stale(db, workspace_id):
            raise RuntimeError("Simulated workspace staleness failure")

        monkeypatch.setattr(
            "app.core.scoring.state_machine.mark_workspace_content_stale",
            _failing_workspace_stale,
        )

        with pytest.raises(RuntimeError, match="Simulated workspace staleness failure"):
            client.post(
                f"/api/v1/brand-profile/workspaces/{ws.id}/keywords/refresh",
                json={"customer_id": "1234567890"},
            )

        # Roll back test session to reset in-memory state
        db_session.rollback()
        db_session.expire_all()

        # Verify old WorkspaceKeywords are preserved and unchanged
        wks_after = {
            wk.keyword_id: (wk.id, wk.monthly_volume)
            for wk in db_session.query(WorkspaceKeyword).filter_by(brand_profile_id=ws.id).all()
        }
        assert wks_after == original_wk_ids

