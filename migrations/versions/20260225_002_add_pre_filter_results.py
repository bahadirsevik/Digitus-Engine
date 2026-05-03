"""Add pre_filter_results table + IntentAnalysis.source column

Revision ID: 20260225_002
Revises: 20260225_001_merge
Create Date: 2026-02-25

New table:
- pre_filter_results: AI pre-filter sonuçları (kanal-özel filtreleme)

Modified table:
- intent_analysis: 'source' column added ('ai' or 'transfer')
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = '20260225_prefilter'
down_revision = '20260225_merge'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. PreFilterResult tablosu
    op.create_table(
        'pre_filter_results',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('scoring_run_id', sa.Integer(), nullable=True),
        sa.Column('keyword_id', sa.Integer(), nullable=True),
        sa.Column('channel', sa.String(20), nullable=False),
        sa.Column('is_kept', sa.Boolean(), nullable=False,
                  server_default=sa.text('true')),
        sa.Column('label', sa.String(50), nullable=True),
        sa.Column('ai_reasoning', sa.Text(), nullable=True),
        sa.Column('extra_data', sa.JSON(), nullable=True),
        sa.Column('transfer_channel', sa.String(20), nullable=True),
        sa.Column('is_fallback', sa.Boolean(),
                  server_default=sa.text('false')),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['scoring_run_id'], ['scoring_runs.id'],
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['keyword_id'], ['keywords.id'],
                                ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )

    # Indexes
    op.create_index('ix_pre_filter_results_id',
                    'pre_filter_results', ['id'])
    op.create_index('idx_pre_filter_run_channel',
                    'pre_filter_results', ['scoring_run_id', 'channel'])
    # Cross-channel transfer query composite index
    op.create_index('idx_pre_filter_transfer',
                    'pre_filter_results',
                    ['scoring_run_id', 'channel', 'is_kept', 'transfer_channel'])
    # Unique constraint
    op.create_unique_constraint('uq_pre_filter',
                    'pre_filter_results',
                    ['scoring_run_id', 'keyword_id', 'channel'])

    # 2. IntentAnalysis tablosuna 'source' kolonu
    op.add_column('intent_analysis',
                  sa.Column('source', sa.String(20),
                            server_default=sa.text("'ai'")))


def downgrade() -> None:
    op.drop_column('intent_analysis', 'source')
    op.drop_index('idx_pre_filter_transfer', table_name='pre_filter_results')
    op.drop_index('idx_pre_filter_run_channel', table_name='pre_filter_results')
    op.drop_index('ix_pre_filter_results_id', table_name='pre_filter_results')
    op.drop_table('pre_filter_results')
