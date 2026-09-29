"""workspace_phase_a_structural

Revision ID: f08125d66a33
Revises: 20260427_001
Create Date: 2026-05-02 23:09:38.509020

Phase A migration - all new columns NULL-able / with defaults.
No UNIQUE constraints on new columns yet (Phase C).
No NOT NULL constraints on new columns yet (Phase C).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'f08125d66a33'
down_revision: Union[str, None] = '20260427_001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ==================== BRAND_PROFILES ====================

    # 1. Add new workspace columns
    op.add_column('brand_profiles', sa.Column('name', sa.String(200), nullable=True))
    op.add_column('brand_profiles', sa.Column('preliminary_info', sa.Text(), nullable=True))
    op.add_column('brand_profiles', sa.Column('suggested_keywords', postgresql.JSON(), nullable=True))
    op.add_column('brand_profiles', sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('brand_profiles', sa.Column('default_geo_target_id', sa.String(20), nullable=True))
    op.add_column('brand_profiles', sa.Column('default_language_id', sa.String(10), nullable=True))
    op.add_column('brand_profiles', sa.Column(
        'is_system_default',
        sa.Boolean(),
        nullable=False,
        server_default=sa.text('false'),
    ))

    # 2. Drop old FK constraint (CASCADE) and recreate with SET NULL + nullable + non-unique
    op.drop_constraint('brand_profiles_scoring_run_id_fkey', 'brand_profiles', type_='foreignkey')
    op.drop_constraint('brand_profiles_scoring_run_id_key', 'brand_profiles', type_='unique')
    op.create_foreign_key(
        'brand_profiles_scoring_run_id_fkey',
        'brand_profiles',
        'scoring_runs',
        ['scoring_run_id'],
        ['id'],
        ondelete='SET NULL',
    )
    op.alter_column('brand_profiles', 'scoring_run_id', nullable=True)

    # ==================== SCORING_RUNS ====================

    # 1. brand_profile_id FK (RESTRICT — workspace silinirse run silinemez)
    op.add_column('scoring_runs', sa.Column('brand_profile_id', sa.Integer(), nullable=True))
    op.create_foreign_key(
        'fk_scoring_runs_brand_profile',
        'scoring_runs',
        'brand_profiles',
        ['brand_profile_id'],
        ['id'],
        ondelete='RESTRICT',
    )

    # 2. enable_* flags
    op.add_column('scoring_runs', sa.Column(
        'enable_ads', sa.Boolean(), nullable=False, server_default=sa.text('true')
    ))
    op.add_column('scoring_runs', sa.Column(
        'enable_seo', sa.Boolean(), nullable=False, server_default=sa.text('true')
    ))
    op.add_column('scoring_runs', sa.Column(
        'enable_social', sa.Boolean(), nullable=False, server_default=sa.text('true')
    ))

    # 3. keyword_selection_mode + limit + selected_ids
    op.add_column('scoring_runs', sa.Column(
        'keyword_selection_mode', sa.String(20), nullable=False, server_default=sa.text("'all'")
    ))
    op.add_column('scoring_runs', sa.Column('keyword_limit', sa.Integer(), nullable=True))
    op.add_column('scoring_runs', sa.Column('selected_keyword_ids', postgresql.JSON(), nullable=True))

    # 4. skip_relevance
    op.add_column('scoring_runs', sa.Column(
        'skip_relevance', sa.Boolean(), nullable=False, server_default=sa.text('false')
    ))

    # 5. relevance timestamps
    op.add_column('scoring_runs', sa.Column('relevance_started_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('scoring_runs', sa.Column('relevance_completed_at', sa.DateTime(timezone=True), nullable=True))

    # 6. Check constraint for selection mode
    op.create_check_constraint(
        'ck_scoring_runs_selection_mode',
        'scoring_runs',
        "keyword_selection_mode IN ('all', 'top_n', 'specific')",
    )

    # ==================== KEYWORDS ====================

    op.add_column('keywords', sa.Column('normalized_keyword', sa.String(500), nullable=True))
    op.create_index('idx_keywords_normalized', 'keywords', ['normalized_keyword'], unique=False)

    # ==================== KEYWORD_SCORES ====================

    op.add_column('keyword_scores', sa.Column('metrics_snapshot', postgresql.JSON(), nullable=True))

    # ==================== CONTENT_OUTPUTS ====================

    op.add_column('content_outputs', sa.Column(
        'is_stale', sa.Boolean(), nullable=False, server_default=sa.text('false')
    ))

    # ==================== WORKSPACE_KEYWORDS (NEW TABLE) ====================

    op.create_table(
        'workspace_keywords',
        sa.Column('id', sa.Integer(), primary_key=True, index=True),
        sa.Column('brand_profile_id', sa.Integer(), sa.ForeignKey('brand_profiles.id', ondelete='CASCADE'), nullable=False),
        sa.Column('keyword_id', sa.Integer(), sa.ForeignKey('keywords.id', ondelete='CASCADE'), nullable=False),
        sa.Column('monthly_volume', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('trend_3m', sa.Numeric(7, 2), nullable=False, server_default=sa.text('0')),
        sa.Column('trend_12m', sa.Numeric(7, 2), nullable=False, server_default=sa.text('0')),
        sa.Column('competition_score', sa.Numeric(3, 2), nullable=False, server_default=sa.text('0.50')),
        sa.Column('data_source', sa.String(20), nullable=False, server_default=sa.text("'csv'")),
        sa.Column('sector', sa.String(200), nullable=True),
        sa.Column('target_market', sa.String(200), nullable=True),
        sa.Column('geo_target_id', sa.String(20), nullable=True),
        sa.Column('language_id', sa.String(10), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('imported_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
    )

    # Unique constraint
    op.create_unique_constraint('uq_workspace_keyword', 'workspace_keywords', ['brand_profile_id', 'keyword_id'])

    # Indexes
    op.create_index('idx_workspace_kw_workspace', 'workspace_keywords', ['brand_profile_id'])
    op.create_index('idx_workspace_kw_keyword', 'workspace_keywords', ['keyword_id'])
    op.create_index('idx_workspace_kw_volume', 'workspace_keywords', ['brand_profile_id', 'monthly_volume'])


def downgrade() -> None:
    # ==================== WORKSPACE_KEYWORDS ====================
    op.drop_index('idx_workspace_kw_volume', table_name='workspace_keywords')
    op.drop_index('idx_workspace_kw_keyword', table_name='workspace_keywords')
    op.drop_index('idx_workspace_kw_workspace', table_name='workspace_keywords')
    op.drop_constraint('uq_workspace_keyword', 'workspace_keywords', type_='unique')
    op.drop_table('workspace_keywords')

    # ==================== CONTENT_OUTPUTS ====================
    op.drop_column('content_outputs', 'is_stale')

    # ==================== KEYWORD_SCORES ====================
    op.drop_column('keyword_scores', 'metrics_snapshot')

    # ==================== KEYWORDS ====================
    op.drop_index('idx_keywords_normalized', table_name='keywords')
    op.drop_column('keywords', 'normalized_keyword')

    # ==================== SCORING_RUNS ====================
    op.drop_constraint('ck_scoring_runs_selection_mode', 'scoring_runs', type_='check')
    op.drop_column('scoring_runs', 'relevance_completed_at')
    op.drop_column('scoring_runs', 'relevance_started_at')
    op.drop_column('scoring_runs', 'skip_relevance')
    op.drop_column('scoring_runs', 'selected_keyword_ids')
    op.drop_column('scoring_runs', 'keyword_limit')
    op.drop_column('scoring_runs', 'keyword_selection_mode')
    op.drop_column('scoring_runs', 'enable_social')
    op.drop_column('scoring_runs', 'enable_seo')
    op.drop_column('scoring_runs', 'enable_ads')
    op.drop_constraint('fk_scoring_runs_brand_profile', 'scoring_runs', type_='foreignkey')
    op.drop_column('scoring_runs', 'brand_profile_id')

    # ==================== BRAND_PROFILES ====================
    op.drop_column('brand_profiles', 'is_system_default')
    op.drop_column('brand_profiles', 'default_language_id')
    op.drop_column('brand_profiles', 'default_geo_target_id')
    op.drop_column('brand_profiles', 'deleted_at')
    op.drop_column('brand_profiles', 'suggested_keywords')
    op.drop_column('brand_profiles', 'preliminary_info')
    op.drop_column('brand_profiles', 'name')

    # Restore old FK constraint
    op.alter_column('brand_profiles', 'scoring_run_id', nullable=False)
    op.drop_constraint('brand_profiles_scoring_run_id_fkey', 'brand_profiles', type_='foreignkey')
    op.create_unique_constraint('brand_profiles_scoring_run_id_key', 'brand_profiles', ['scoring_run_id'])
    op.create_foreign_key(
        'brand_profiles_scoring_run_id_fkey',
        'brand_profiles',
        'scoring_runs',
        ['scoring_run_id'],
        ['id'],
        ondelete='CASCADE',
    )