"""Add default relevance coefficient to scoring_runs

Revision ID: 20260322_001
Revises: 20260319_001
Create Date: 2026-03-22
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers
revision = "20260322_001"
down_revision = "20260319_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("scoring_runs")}

    if "default_relevance_coefficient" not in cols:
        op.add_column(
            "scoring_runs",
            sa.Column(
                "default_relevance_coefficient",
                sa.Numeric(4, 2),
                nullable=False,
                server_default=sa.text("1.0"),
            ),
        )

    # Defensive backfill for rows that may remain null in edge deployments.
    op.execute(
        sa.text(
            "UPDATE scoring_runs "
            "SET default_relevance_coefficient = 1.0 "
            "WHERE default_relevance_coefficient IS NULL"
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("scoring_runs")}

    if "default_relevance_coefficient" in cols:
        op.drop_column("scoring_runs", "default_relevance_coefficient")
