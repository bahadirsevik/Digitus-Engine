"""Add data_source to keywords and keyword_source_filter to scoring_runs

Revision ID: 20260416_001
Revises: 20260322_001
Create Date: 2026-04-16
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers
revision = "20260416_001"
down_revision = "20260322_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # keywords tablosu
    op.add_column(
        "keywords",
        sa.Column("data_source", sa.String(20), nullable=False,
                  server_default="csv")
    )
    op.create_index("ix_keywords_data_source", "keywords", ["data_source"])
    op.create_check_constraint(
        "ck_keywords_data_source",
        "keywords",
        "data_source IN ('csv', 'google_ads_api')"
    )

    # scoring_runs tablosu
    op.add_column(
        "scoring_runs",
        sa.Column("keyword_source_filter", sa.String(20), nullable=True)
    )
    op.create_check_constraint(
        "ck_scoring_runs_source_filter",
        "scoring_runs",
        "keyword_source_filter IS NULL "
        "OR keyword_source_filter IN ('csv', 'google_ads_api')"
    )


def downgrade() -> None:
    op.drop_constraint("ck_scoring_runs_source_filter", "scoring_runs", type_="check")
    op.drop_column("scoring_runs", "keyword_source_filter")

    op.drop_constraint("ck_keywords_data_source", "keywords", type_="check")
    op.drop_index("ix_keywords_data_source", "keywords")
    op.drop_column("keywords", "data_source")
