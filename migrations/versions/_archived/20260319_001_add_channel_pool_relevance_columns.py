"""Add relevance and adjusted score columns to channel_pools

Revision ID: 20260319_001
Revises: 20260313_001
Create Date: 2026-03-19
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers
revision = "20260319_001"
down_revision = "20260313_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("channel_pools")}

    if "relevance_score" not in cols:
        op.add_column(
            "channel_pools",
            sa.Column("relevance_score", sa.Numeric(4, 3), nullable=True),
        )

    if "adjusted_score" not in cols:
        op.add_column(
            "channel_pools",
            sa.Column("adjusted_score", sa.Numeric(15, 4), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("channel_pools")}

    if "adjusted_score" in cols:
        op.drop_column("channel_pools", "adjusted_score")

    if "relevance_score" in cols:
        op.drop_column("channel_pools", "relevance_score")
