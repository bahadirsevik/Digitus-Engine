"""Add brand_profiles, keyword_relevance tables + ScoringRun URL columns

Revision ID: 20260313_001
Revises: 20260225_prefilter
Create Date: 2026-03-13

New tables:
- brand_profiles: Firma profili (site crawl ile AI çıkarımı)
- keyword_relevance: Keyword-marka embedding similarity skorları

Modified table:
- scoring_runs: company_url, competitor_urls columns added
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSON


# revision identifiers
revision = '20260313_001'
down_revision = '20260225_prefilter'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # --- scoring_runs: add URL columns ---
    scoring_cols = {c["name"] for c in inspector.get_columns("scoring_runs")}
    if "company_url" not in scoring_cols:
        op.add_column("scoring_runs", sa.Column("company_url", sa.String(500), nullable=True))
    if "competitor_urls" not in scoring_cols:
        op.add_column("scoring_runs", sa.Column("competitor_urls", JSON, nullable=True))

    # --- brand_profiles table ---
    if not inspector.has_table("brand_profiles"):
        op.create_table(
            "brand_profiles",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "scoring_run_id",
                sa.Integer(),
                sa.ForeignKey("scoring_runs.id", ondelete="CASCADE"),
                nullable=False,
                unique=True,
            ),
            sa.Column("company_url", sa.String(500), nullable=False),
            sa.Column("competitor_urls", JSON, nullable=True),
            sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
            sa.Column("profile_data", JSON, nullable=True),
            sa.Column("validation_data", JSON, nullable=True),
            sa.Column("source_pages", JSON, nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
    brand_indexes = {i["name"] for i in inspector.get_indexes("brand_profiles")}
    if "ix_brand_profiles_id" not in brand_indexes:
        op.create_index("ix_brand_profiles_id", "brand_profiles", ["id"])

    # --- keyword_relevance table ---
    if not inspector.has_table("keyword_relevance"):
        op.create_table(
            "keyword_relevance",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("scoring_run_id", sa.Integer(), sa.ForeignKey("scoring_runs.id", ondelete="CASCADE"), nullable=False),
            sa.Column("keyword_id", sa.Integer(), sa.ForeignKey("keywords.id", ondelete="CASCADE"), nullable=False),
            sa.Column("relevance_score", sa.Numeric(4, 3), nullable=False),
            sa.Column("matched_anchor", sa.String(500), nullable=True),
            sa.Column("method", sa.String(20), nullable=False, server_default="embedding"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )

    relevance_indexes = {i["name"] for i in inspector.get_indexes("keyword_relevance")}
    if "ix_keyword_relevance_id" not in relevance_indexes:
        op.create_index("ix_keyword_relevance_id", "keyword_relevance", ["id"])
    if "idx_keyword_relevance_run" not in relevance_indexes:
        op.create_index("idx_keyword_relevance_run", "keyword_relevance", ["scoring_run_id"])

    uq_names = {u["name"] for u in inspector.get_unique_constraints("keyword_relevance")}
    if "uq_keyword_relevance" not in uq_names:
        op.create_unique_constraint("uq_keyword_relevance", "keyword_relevance", ["scoring_run_id", "keyword_id"])


def downgrade() -> None:
    op.drop_table('keyword_relevance')
    op.drop_table('brand_profiles')
    op.drop_column('scoring_runs', 'competitor_urls')
    op.drop_column('scoring_runs', 'company_url')
