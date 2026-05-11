"""
Add social media tables.

Adds:
- social_categories: Content category types (educational, trending, etc.)
- social_ideas: Content ideas with platform and format
- social_contents: Full content packages with JSONB hooks

Revision ID: 20260204_001
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers
revision = '20260204_001_add_social_media_tables'
down_revision = '20260203_002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. social_categories table
    op.create_table(
        'social_categories',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('scoring_run_id', sa.Integer(), nullable=False),
        sa.Column('category_name', sa.String(100), nullable=False),
        sa.Column('category_type', sa.String(50), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('relevance_score', sa.Float(), server_default='0.0'),
        sa.Column('suggested_keyword_ids', JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['scoring_run_id'], ['scoring_runs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_social_categories_id', 'social_categories', ['id'])
    op.create_index('ix_social_categories_scoring_run_id', 'social_categories', ['scoring_run_id'])

    # 2. social_ideas table
    op.create_table(
        'social_ideas',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('category_id', sa.Integer(), nullable=False),
        sa.Column('keyword_id', sa.Integer(), nullable=True),
        sa.Column('idea_title', sa.String(200), nullable=False),
        sa.Column('idea_description', sa.Text(), nullable=True),
        sa.Column('target_platform', sa.String(50), nullable=True),
        sa.Column('content_format', sa.String(50), nullable=True),
        sa.Column('trend_alignment', sa.Float(), server_default='0.0'),
        sa.Column('is_selected', sa.Boolean(), server_default='false'),
        sa.Column('regeneration_count', sa.Integer(), server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['category_id'], ['social_categories.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['keyword_id'], ['keywords.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_social_ideas_id', 'social_ideas', ['id'])
    op.create_index('ix_social_ideas_category_id', 'social_ideas', ['category_id'])
    op.create_index('ix_social_ideas_is_selected', 'social_ideas', ['is_selected'])

    # 3. social_contents table with JSONB hooks
    op.create_table(
        'social_contents',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('idea_id', sa.Integer(), nullable=False),
        sa.Column('content_output_id', sa.Integer(), nullable=True),
        
        # JSONB hooks - flexible, extensible
        sa.Column('hooks', JSONB(astext_type=sa.Text()), nullable=True),
        
        # Main content
        sa.Column('caption', sa.Text(), nullable=False),
        sa.Column('scenario', sa.Text(), nullable=True),
        sa.Column('visual_suggestion', sa.Text(), nullable=True),
        sa.Column('video_concept', sa.Text(), nullable=True),
        
        # CTA and hashtags
        sa.Column('cta_text', sa.String(200), nullable=True),
        sa.Column('hashtags', JSONB(astext_type=sa.Text()), nullable=True),
        
        # Platform optimization (industry standard, not user-specific)
        sa.Column('industry_posting_suggestion', sa.String(150), nullable=True),
        sa.Column('platform_notes', sa.Text(), nullable=True),
        
        sa.Column('regeneration_count', sa.Integer(), server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        
        sa.ForeignKeyConstraint(['idea_id'], ['social_ideas.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['content_output_id'], ['content_outputs.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_social_contents_id', 'social_contents', ['id'])
    op.create_index('ix_social_contents_idea_id', 'social_contents', ['idea_id'])


def downgrade() -> None:
    op.drop_table('social_contents')
    op.drop_table('social_ideas')
    op.drop_table('social_categories')
