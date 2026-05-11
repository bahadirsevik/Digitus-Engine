"""Add hot-path composite indexes (Phase 1)

Kod icerisindeki en sik vurulan sorgulari hizlandirir. Tum indeksler
PostgreSQL CONCURRENTLY ile olusturulur; boylece production tablolarinda
kilit olusturmadan calisir.

Kapsam:
- keyword_scores(scoring_run_id, {ads,seo,social}_rank)
- channel_pools(scoring_run_id, channel, final_rank)
- channel_candidates(scoring_run_id, channel, rank_in_channel)
- intent_analysis(scoring_run_id, channel, keyword_id) WHERE is_passed
  (partial)

Revision ID: 20260424_001
Revises: 20260416_001
Create Date: 2026-04-24
"""
from alembic import op


# revision identifiers
revision = "20260424_001"
down_revision = "20260416_001"
branch_labels = None
depends_on = None


INDEXES = [
    (
        "idx_keyword_scores_run_ads_rank",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_keyword_scores_run_ads_rank "
        "ON keyword_scores (scoring_run_id, ads_rank)",
    ),
    (
        "idx_keyword_scores_run_seo_rank",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_keyword_scores_run_seo_rank "
        "ON keyword_scores (scoring_run_id, seo_rank)",
    ),
    (
        "idx_keyword_scores_run_social_rank",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_keyword_scores_run_social_rank "
        "ON keyword_scores (scoring_run_id, social_rank)",
    ),
    (
        "idx_channel_pools_run_channel_rank",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_channel_pools_run_channel_rank "
        "ON channel_pools (scoring_run_id, channel, final_rank)",
    ),
    (
        "idx_channel_candidates_run_ch_rank",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_channel_candidates_run_ch_rank "
        "ON channel_candidates (scoring_run_id, channel, rank_in_channel)",
    ),
    (
        "idx_intent_analysis_passed_lookup",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_intent_analysis_passed_lookup "
        "ON intent_analysis (scoring_run_id, channel, keyword_id) "
        "WHERE is_passed = true",
    ),
]


def upgrade() -> None:
    # CREATE INDEX CONCURRENTLY transaction icinde calismaz.
    # autocommit_block() ile her DDL kendi baslangic/bitisini yonetir.
    with op.get_context().autocommit_block():
        for _, sql in INDEXES:
            op.execute(sql)


def downgrade() -> None:
    # DROP INDEX CONCURRENTLY da autocommit block gerektirir.
    with op.get_context().autocommit_block():
        for name, _ in INDEXES:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
