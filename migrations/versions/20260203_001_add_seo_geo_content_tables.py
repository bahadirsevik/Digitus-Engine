"""add_seo_geo_content_tables

Revision ID: 20260203_001
Revises: 
Create Date: 2026-02-03

SEO+GEO İçerik Motoru için 3 yeni tablo:
- seo_geo_contents
- seo_compliance_results
- geo_compliance_results
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '20260203_001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ### seo_geo_contents tablosu ###
    op.create_table('seo_geo_contents',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('content_output_id', sa.Integer(), nullable=True),
        sa.Column('keyword_id', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(length=100), nullable=False),
        sa.Column('url_suggestion', sa.String(length=200), nullable=True),
        sa.Column('intro_paragraph', sa.Text(), nullable=False),
        sa.Column('body_content', sa.Text(), nullable=False),
        sa.Column('subheadings', sa.JSON(), nullable=True),
        sa.Column('body_sections', sa.JSON(), nullable=True),
        sa.Column('bullet_points', sa.JSON(), nullable=True),
        sa.Column('internal_link_anchor', sa.String(length=200), nullable=True),
        sa.Column('internal_link_url', sa.String(length=500), nullable=True),
        sa.Column('external_link_anchor', sa.String(length=200), nullable=True),
        sa.Column('external_link_url', sa.String(length=500), nullable=True),
        sa.Column('meta_description', sa.String(length=160), nullable=True),
        sa.Column('word_count', sa.Integer(), nullable=True),
        sa.Column('subheading_count', sa.Integer(), nullable=True),
        sa.Column('keyword_count', sa.Integer(), nullable=True),
        sa.Column('keyword_density', sa.Numeric(precision=4, scale=2), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['content_output_id'], ['content_outputs.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['keyword_id'], ['keywords.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_seo_geo_contents_id'), 'seo_geo_contents', ['id'], unique=False)

    # ### seo_compliance_results tablosu ###
    op.create_table('seo_compliance_results',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('seo_geo_content_id', sa.Integer(), nullable=False),
        sa.Column('title_has_keyword', sa.Boolean(), nullable=True),
        sa.Column('title_length_ok', sa.Boolean(), nullable=True),
        sa.Column('url_has_keyword', sa.Boolean(), nullable=True),
        sa.Column('intro_keyword_count', sa.Integer(), nullable=True),
        sa.Column('word_count_in_range', sa.Boolean(), nullable=True),
        sa.Column('subheading_count_ok', sa.Boolean(), nullable=True),
        sa.Column('subheadings_have_kw', sa.Boolean(), nullable=True),
        sa.Column('has_internal_link', sa.Boolean(), nullable=True),
        sa.Column('has_external_link', sa.Boolean(), nullable=True),
        sa.Column('has_bullet_list', sa.Boolean(), nullable=True),
        sa.Column('sentences_readable', sa.Boolean(), nullable=True),
        sa.Column('total_passed', sa.Integer(), nullable=True),
        sa.Column('total_score', sa.Numeric(precision=3, scale=2), nullable=True),
        sa.Column('improvement_notes', sa.Text(), nullable=True),
        sa.Column('checked_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['seo_geo_content_id'], ['seo_geo_contents.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_seo_compliance_results_id'), 'seo_compliance_results', ['id'], unique=False)

    # ### geo_compliance_results tablosu ###
    op.create_table('geo_compliance_results',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('seo_geo_content_id', sa.Integer(), nullable=False),
        sa.Column('intro_answers_question', sa.Boolean(), nullable=True),
        sa.Column('snippet_extractable', sa.Boolean(), nullable=True),
        sa.Column('info_hierarchy_strong', sa.Boolean(), nullable=True),
        sa.Column('tone_is_informative', sa.Boolean(), nullable=True),
        sa.Column('no_fluff_content', sa.Boolean(), nullable=True),
        sa.Column('direct_answer_present', sa.Boolean(), nullable=True),
        sa.Column('has_verifiable_info', sa.Boolean(), nullable=True),
        sa.Column('total_passed', sa.Integer(), nullable=True),
        sa.Column('total_score', sa.Numeric(precision=3, scale=2), nullable=True),
        sa.Column('ai_snippet_preview', sa.Text(), nullable=True),
        sa.Column('improvement_notes', sa.Text(), nullable=True),
        sa.Column('checked_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['seo_geo_content_id'], ['seo_geo_contents.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_geo_compliance_results_id'), 'geo_compliance_results', ['id'], unique=False)


def downgrade() -> None:
    # Drop tables in reverse order (dependencies first)
    op.drop_index(op.f('ix_geo_compliance_results_id'), table_name='geo_compliance_results')
    op.drop_table('geo_compliance_results')
    
    op.drop_index(op.f('ix_seo_compliance_results_id'), table_name='seo_compliance_results')
    op.drop_table('seo_compliance_results')
    
    op.drop_index(op.f('ix_seo_geo_contents_id'), table_name='seo_geo_contents')
    op.drop_table('seo_geo_contents')
