"""Add Google Ads RSA tables

Revision ID: 20260203_002
Revises: 20260203_001
Create Date: 2026-02-03

Tables:
- ad_groups: Keyword groups for RSA
- ad_headlines: RSA headlines (max 30 chars)
- ad_descriptions: RSA descriptions (max 90 chars)
- negative_keywords: Negative keywords per group
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = '20260203_002'
down_revision = '20260203_001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ad_groups table
    op.create_table(
        'ad_groups',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('content_output_id', sa.Integer(), nullable=True),
        sa.Column('scoring_run_id', sa.Integer(), nullable=False),
        sa.Column('group_name', sa.String(200), nullable=False),
        sa.Column('group_theme', sa.Text(), nullable=True),
        sa.Column('target_keyword_ids', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('target_keywords', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('headlines_generated', sa.Integer(), nullable=True, default=0),
        sa.Column('headlines_eliminated', sa.Integer(), nullable=True, default=0),
        sa.Column('dki_converted_count', sa.Integer(), nullable=True, default=0),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['content_output_id'], ['content_outputs.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['scoring_run_id'], ['scoring_runs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ad_groups_id'), 'ad_groups', ['id'], unique=False)
    
    # ad_headlines table
    op.create_table(
        'ad_headlines',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('ad_group_id', sa.Integer(), nullable=False),
        sa.Column('headline_text', sa.String(30), nullable=False),
        sa.Column('headline_type', sa.String(20), nullable=True),
        sa.Column('position_preference', sa.String(20), nullable=True, default='any'),
        sa.Column('is_dki', sa.Boolean(), nullable=True, default=False),
        sa.Column('sort_order', sa.Integer(), nullable=True, default=0),
        sa.Column('validation_action', sa.String(20), nullable=True),
        sa.Column('original_length', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['ad_group_id'], ['ad_groups.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ad_headlines_id'), 'ad_headlines', ['id'], unique=False)
    
    # ad_descriptions table
    op.create_table(
        'ad_descriptions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('ad_group_id', sa.Integer(), nullable=False),
        sa.Column('description_text', sa.String(90), nullable=False),
        sa.Column('description_type', sa.String(20), nullable=True),
        sa.Column('position_preference', sa.String(20), nullable=True, default='any'),
        sa.Column('sort_order', sa.Integer(), nullable=True, default=0),
        sa.Column('validation_action', sa.String(20), nullable=True),
        sa.ForeignKeyConstraint(['ad_group_id'], ['ad_groups.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ad_descriptions_id'), 'ad_descriptions', ['id'], unique=False)
    
    # negative_keywords table
    op.create_table(
        'negative_keywords',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('ad_group_id', sa.Integer(), nullable=False),
        sa.Column('keyword', sa.String(200), nullable=False),
        sa.Column('match_type', sa.String(20), nullable=True, default='phrase'),
        sa.Column('category', sa.String(50), nullable=True),
        sa.Column('reason', sa.String(500), nullable=True),
        sa.ForeignKeyConstraint(['ad_group_id'], ['ad_groups.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_negative_keywords_id'), 'negative_keywords', ['id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_negative_keywords_id'), table_name='negative_keywords')
    op.drop_table('negative_keywords')
    
    op.drop_index(op.f('ix_ad_descriptions_id'), table_name='ad_descriptions')
    op.drop_table('ad_descriptions')
    
    op.drop_index(op.f('ix_ad_headlines_id'), table_name='ad_headlines')
    op.drop_table('ad_headlines')
    
    op.drop_index(op.f('ix_ad_groups_id'), table_name='ad_groups')
    op.drop_table('ad_groups')
