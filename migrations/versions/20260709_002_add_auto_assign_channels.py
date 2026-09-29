"""add_auto_assign_channels

Revision ID: 20260709_002
Revises: 20260709_001
Create Date: 2026-07-09
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260709_002"
down_revision: Union[str, None] = "20260709_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    existing_columns = {
        column["name"]
        for column in sa.inspect(bind).get_columns("scoring_runs")
    }
    if "auto_assign_channels" not in existing_columns:
        op.add_column(
            "scoring_runs",
            sa.Column(
                "auto_assign_channels",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    existing_columns = {
        column["name"]
        for column in sa.inspect(bind).get_columns("scoring_runs")
    }
    if "auto_assign_channels" in existing_columns:
        op.drop_column("scoring_runs", "auto_assign_channels")
