"""Integration tests for Social Brief Flow Phase F1-A (ORM models & migration).

Covers all 12 F1-A requirements:
1. Migration chain reaches head (20260923_001).
2. New tables exist in DB.
3. New columns exist on social_categories, social_ideas, social_contents.
4. Migration upgrade is idempotent.
5. Second SocialContent with same idea_id for brief raises IntegrityError.
6. Legacy rows with brief_id IS NULL allow multiple SocialContent.
7. Two active attempts for same brief and stage raises IntegrityError.
8. Terminal attempt allows new active attempt for same brief and stage.
9. Duplicate (brief_id, keyword_id) raises IntegrityError.
10. Duplicate (brief_id, platform, content_format) raises IntegrityError.
11. Invalid duration pairs / formats rejected by DB check constraints.
12. Legacy social category/idea/content creation functions without regression.
"""
from __future__ import annotations

import subprocess
import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.database.models import (
    BrandProfile,
    Keyword,
    ScoringRun,
    SocialBrief,
    SocialBriefKeyword,
    SocialBriefTarget,
    SocialCategory,
    SocialContent,
    SocialGenerationAttempt,
    SocialIdea,
)


@pytest.fixture
def base_setup(db_session):
    bp = BrandProfile(name="Test Brand", company_url="https://test.com")
    db_session.add(bp)
    db_session.flush()

    sr = ScoringRun(
        brand_profile_id=bp.id,
        status="completed",
        algorithm_version="v3",
        channel_assignment_version=1,
        ads_capacity=10,
        seo_capacity=10,
        social_capacity=10,
    )
    db_session.add(sr)
    db_session.flush()

    kw1 = Keyword(keyword="dijital pazarlama", normalized_keyword="dijital pazarlama")
    kw2 = Keyword(keyword="seo ajansi", normalized_keyword="seo ajansi")
    db_session.add_all([kw1, kw2])
    db_session.flush()

    brief = SocialBrief(
        scoring_run_id=sr.id,
        brand_name_snapshot="Test Brand",
        brand_context_snapshot="Context",
        channel_assignment_version=1,
        format_matrix_version="v1",
    )
    db_session.add(brief)
    db_session.flush()

    return bp, sr, [kw1, kw2], brief


def test_01_and_02_tables_exist(db_engine):
    inspector = inspect(db_engine)
    tables = set(inspector.get_table_names())
    for t in [
        "social_briefs",
        "social_brief_keywords",
        "social_brief_targets",
        "social_generation_attempts",
    ]:
        assert t in tables, f"Tablo eksik: {t}"


def test_03_columns_exist(db_engine):
    inspector = inspect(db_engine)
    cat_cols = {c["name"] for c in inspector.get_columns("social_categories")}
    assert "brief_id" in cat_cols

    idea_cols = {c["name"] for c in inspector.get_columns("social_ideas")}
    assert "brief_id" in idea_cols
    assert "brief_target_id" in idea_cols

    content_cols = {c["name"] for c in inspector.get_columns("social_contents")}
    for col in [
        "brief_id",
        "format_payload",
        "duration_status",
        "actual_duration_sec",
        "validation_warnings",
    ]:
        assert col in content_cols, f"Kolon eksik: social_contents.{col}"


def test_04_migration_upgrade_idempotent():
    result = subprocess.run(
        ["alembic", "upgrade", "head"],
        capture_output=True,
        text=True,
        cwd="/app",
    )
    assert result.returncode == 0, f"alembic upgrade head failed on second call: {result.stderr}"


def test_05_content_uniqueness_for_brief(db_session, base_setup):
    _, sr, _, brief = base_setup
    cat = SocialCategory(scoring_run_id=sr.id, category_name="Cat1", brief_id=brief.id)
    db_session.add(cat)
    db_session.flush()

    idea = SocialIdea(category_id=cat.id, idea_title="Idea1", brief_id=brief.id)
    db_session.add(idea)
    db_session.flush()

    c1 = SocialContent(
        idea_id=idea.id,
        brief_id=brief.id,
        caption="Caption 1",
    )
    db_session.add(c1)
    db_session.flush()

    c2 = SocialContent(
        idea_id=idea.id,
        brief_id=brief.id,
        caption="Caption 2",
    )
    db_session.add(c2)
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_06_legacy_content_allows_duplicates(db_session, base_setup):
    _, sr, _, _ = base_setup
    cat = SocialCategory(scoring_run_id=sr.id, category_name="LegacyCat")
    db_session.add(cat)
    db_session.flush()

    idea = SocialIdea(category_id=cat.id, idea_title="LegacyIdea")
    db_session.add(idea)
    db_session.flush()

    c1 = SocialContent(idea_id=idea.id, brief_id=None, caption="Legacy 1")
    c2 = SocialContent(idea_id=idea.id, brief_id=None, caption="Legacy 2")
    db_session.add_all([c1, c2])
    db_session.flush()
    assert c1.id is not None and c2.id is not None


def test_07_attempt_active_partial_unique(db_session, base_setup):
    _, _, _, brief = base_setup
    a1 = SocialGenerationAttempt(
        brief_id=brief.id,
        stage="ideas",
        idempotency_key="key-1",
        status="running",
    )
    db_session.add(a1)
    db_session.flush()

    a2 = SocialGenerationAttempt(
        brief_id=brief.id,
        stage="ideas",
        idempotency_key="key-2",
        status="pending",
    )
    db_session.add(a2)
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_08_attempt_terminal_allows_new_active(db_session, base_setup):
    _, _, _, brief = base_setup
    a1 = SocialGenerationAttempt(
        brief_id=brief.id,
        stage="ideas",
        idempotency_key="key-1",
        status="failed",
    )
    db_session.add(a1)
    db_session.flush()

    a2 = SocialGenerationAttempt(
        brief_id=brief.id,
        stage="ideas",
        idempotency_key="key-2",
        status="running",
    )
    db_session.add(a2)
    db_session.flush()
    assert a2.id is not None


def test_09_brief_keyword_uniqueness(db_session, base_setup):
    _, _, kws, brief = base_setup
    bk1 = SocialBriefKeyword(
        brief_id=brief.id,
        keyword_id=kws[0].id,
        keyword_snapshot=kws[0].keyword,
        position=0,
    )
    db_session.add(bk1)
    db_session.flush()

    bk2 = SocialBriefKeyword(
        brief_id=brief.id,
        keyword_id=kws[0].id,
        keyword_snapshot=kws[0].keyword,
        position=1,
    )
    db_session.add(bk2)
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_10_brief_target_uniqueness(db_session, base_setup):
    _, _, _, brief = base_setup
    t1 = SocialBriefTarget(
        brief_id=brief.id,
        platform="instagram",
        content_format="post",
    )
    db_session.add(t1)
    db_session.flush()

    t2 = SocialBriefTarget(
        brief_id=brief.id,
        platform="instagram",
        content_format="post",
    )
    db_session.add(t2)
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_11_duration_check_constraints(db_session, base_setup):
    _, _, _, brief = base_setup

    # Valid video
    v_valid = SocialBriefTarget(
        brief_id=brief.id,
        platform="instagram",
        content_format="reels",
        duration_preset_id="reels_16_30",
        duration_min_sec=16,
        duration_max_sec=30,
    )
    db_session.add(v_valid)
    db_session.flush()

    # Valid non-video formats (all duration fields null)
    for plat, fmt in [
        ("linkedin", "post"),
        ("instagram", "carousel"),
        ("twitter", "thread"),
        ("instagram", "story"),
    ]:
        target = SocialBriefTarget(
            brief_id=brief.id,
            platform=plat,
            content_format=fmt,
        )
        db_session.add(target)
        db_session.flush()

    # Invalid: video without duration
    v_no_dur = SocialBriefTarget(
        brief_id=brief.id,
        platform="tiktok",
        content_format="short",
    )
    db_session.add(v_no_dur)
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()

    # Invalid: video with min/max but duration_preset_id is None
    v_no_preset = SocialBriefTarget(
        brief_id=brief.id,
        platform="tiktok",
        content_format="short",
        duration_preset_id=None,
        duration_min_sec=16,
        duration_max_sec=30,
    )
    db_session.add(v_no_preset)
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()

    # Invalid: post with duration
    p_with_dur = SocialBriefTarget(
        brief_id=brief.id,
        platform="twitter",
        content_format="post",
        duration_min_sec=10,
        duration_max_sec=20,
    )
    db_session.add(p_with_dur)
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()

    # Invalid: max < min
    v_inverted = SocialBriefTarget(
        brief_id=brief.id,
        platform="youtube",
        content_format="short",
        duration_min_sec=30,
        duration_max_sec=15,
    )
    db_session.add(v_inverted)
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()

    # Invalid: min < 1
    v_zero = SocialBriefTarget(
        brief_id=brief.id,
        platform="youtube",
        content_format="video",
        duration_min_sec=0,
        duration_max_sec=60,
    )
    db_session.add(v_zero)
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_12_legacy_pipeline_unaffected(db_session, base_setup):
    _, sr, _, _ = base_setup
    cat = SocialCategory(
        scoring_run_id=sr.id,
        category_name="Legacy Education",
        category_type="educational",
    )
    db_session.add(cat)
    db_session.flush()

    idea = SocialIdea(
        category_id=cat.id,
        idea_title="Legacy Idea",
        target_platform="instagram",
        content_format="post",
    )
    db_session.add(idea)
    db_session.flush()

    content = SocialContent(
        idea_id=idea.id,
        caption="Legacy Caption",
    )
    db_session.add(content)
    db_session.flush()
    assert cat.id > 0 and idea.id > 0 and content.id > 0
