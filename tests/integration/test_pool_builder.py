"""
Integration test: PoolBuilder.build_candidate_pools() respects capacity*3 cap.

Uses a real Postgres session (docker-compose.test.yml).
Creates a run with small capacities, populates 30 KeywordScore rows,
calls build_candidate_pools(), and asserts ChannelCandidate counts
are <= capacity*3 (not the global constant ADS_POOL_SIZE=120 etc.).
"""
from decimal import Decimal

import pytest

from app.core.constants import ADS_POOL_SIZE, SEO_POOL_SIZE, SOCIAL_POOL_SIZE
from app.database.models import ChannelCandidate, KeywordScore


def _seed_scores(db, run_id: int, keyword_ids: list[int]) -> None:
    """Insert a KeywordScore row for every keyword in the run."""
    for rank, kw_id in enumerate(keyword_ids, 1):
        score_val = Decimal(str(round(100.0 / rank, 4)))
        db.add(
            KeywordScore(
                scoring_run_id=run_id,
                keyword_id=kw_id,
                ads_score=score_val,
                seo_score=score_val,
                social_score=score_val,
                ads_rank=rank,
                seo_rank=rank,
                social_rank=rank,
            )
        )
    db.commit()


def test_pool_builder_small_capacity(db_session, make_workspace, make_keyword, make_scoring_run):
    """Small capacity run: each channel's candidate count == capacity*3, not the global constant."""
    from app.core.channel.pool_builder import PoolBuilder

    ws = make_workspace()
    # ads=5, seo=8, social=6 → caps: ADS=15, SEO=24, SOCIAL=18
    run = make_scoring_run(
        brand_profile_id=ws.id,
        ads_capacity=5,
        seo_capacity=8,
        social_capacity=6,
        enable_ads=True,
        enable_seo=True,
        enable_social=True,
        status="scored",
    )

    # Create 30 keywords — more than any cap
    kw_ids = [make_keyword(f"keyword-{i}", brand_profile_id=ws.id).id for i in range(30)]
    _seed_scores(db_session, run.id, kw_ids)

    PoolBuilder(db_session).build_candidate_pools(run.id)

    candidates = (
        db_session.query(ChannelCandidate)
        .filter(ChannelCandidate.scoring_run_id == run.id)
        .all()
    )
    by_channel: dict[str, int] = {}
    for c in candidates:
        by_channel[c.channel] = by_channel.get(c.channel, 0) + 1

    assert by_channel.get("ADS", 0) == 15, f"ADS: expected 15, got {by_channel.get('ADS', 0)}"
    assert by_channel.get("SEO", 0) == 24, f"SEO: expected 24, got {by_channel.get('SEO', 0)}"
    assert by_channel.get("SOCIAL", 0) == 18, f"SOCIAL: expected 18, got {by_channel.get('SOCIAL', 0)}"


def test_pool_builder_large_capacity_capped_at_constant(
    db_session, make_workspace, make_keyword, make_scoring_run
):
    """Large capacity run: candidate count must not exceed the global ADS/SEO/SOCIAL_POOL_SIZE."""
    from app.core.channel.pool_builder import PoolBuilder

    ws = make_workspace()
    # ads=100 → 100*3=300 > ADS_POOL_SIZE(120) → cap at 120 but only 30 keywords exist → 30
    run = make_scoring_run(
        brand_profile_id=ws.id,
        ads_capacity=100,
        seo_capacity=100,
        social_capacity=100,
        enable_ads=True,
        enable_seo=True,
        enable_social=True,
        status="scored",
    )

    kw_ids = [make_keyword(f"big-kw-{i}", brand_profile_id=ws.id).id for i in range(30)]
    _seed_scores(db_session, run.id, kw_ids)

    PoolBuilder(db_session).build_candidate_pools(run.id)

    candidates = (
        db_session.query(ChannelCandidate)
        .filter(ChannelCandidate.scoring_run_id == run.id)
        .all()
    )
    by_channel: dict[str, int] = {}
    for c in candidates:
        by_channel[c.channel] = by_channel.get(c.channel, 0) + 1

    # Only 30 keywords exist so each channel gets at most 30
    assert by_channel.get("ADS", 0) <= ADS_POOL_SIZE
    assert by_channel.get("SEO", 0) <= SEO_POOL_SIZE
    assert by_channel.get("SOCIAL", 0) <= SOCIAL_POOL_SIZE
    # And since we have exactly 30, each channel gets exactly 30
    assert by_channel.get("ADS", 0) == 30
    assert by_channel.get("SEO", 0) == 30
    assert by_channel.get("SOCIAL", 0) == 30


def test_pool_builder_multiplier_widens_window(
    db_session, make_workspace, make_keyword, make_scoring_run, monkeypatch
):
    """Deney anahtarı CANDIDATE_POOL_MULTIPLIER pencereyi çarpar.

    ads=5 → normal pencere min(120, 15)=15; multiplier=4 →
    min(480, 60)=60 ama evrende 30 kelime var → 30 aday.
    SEO=8 → normalde 24; ×4 → min(240, 96)=96 → 30.
    Default (1) davranışını test_pool_builder_small_capacity korur.
    """
    from app.config import settings
    from app.core.channel.pool_builder import PoolBuilder

    monkeypatch.setattr(settings, "CANDIDATE_POOL_MULTIPLIER", 4)

    ws = make_workspace()
    run = make_scoring_run(
        brand_profile_id=ws.id,
        ads_capacity=5,
        seo_capacity=8,
        social_capacity=6,
        enable_ads=True,
        enable_seo=True,
        enable_social=True,
        status="scored",
    )

    kw_ids = [make_keyword(f"mult-kw-{i}", brand_profile_id=ws.id).id for i in range(30)]
    _seed_scores(db_session, run.id, kw_ids)

    PoolBuilder(db_session).build_candidate_pools(run.id)

    candidates = (
        db_session.query(ChannelCandidate)
        .filter(ChannelCandidate.scoring_run_id == run.id)
        .all()
    )
    by_channel: dict[str, int] = {}
    for c in candidates:
        by_channel[c.channel] = by_channel.get(c.channel, 0) + 1

    # ×4 pencereler (60/96/72) evren boyutuna (30) çarpar
    assert by_channel.get("ADS", 0) == 30
    assert by_channel.get("SEO", 0) == 30
    assert by_channel.get("SOCIAL", 0) == 30
