"""add_onboarding_flow

Revision ID: 20260709_001
Revises: 20260707_001
Create Date: 2026-07-09
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260709_001"
down_revision: Union[str, None] = "20260707_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    existing_columns = {
        column["name"]
        for column in sa.inspect(bind).get_columns("brand_profiles")
    }
    if "onboarding_flow" not in existing_columns:
        op.add_column(
            "brand_profiles",
            sa.Column(
                "onboarding_flow",
                sa.String(length=20),
                nullable=False,
                server_default="legacy",
            ),
        )
    if "profile_approved_at" not in existing_columns:
        op.add_column(
            "brand_profiles",
            sa.Column("profile_approved_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    existing_columns = {
        column["name"]
        for column in sa.inspect(bind).get_columns("brand_profiles")
    }
    if "profile_approved_at" in existing_columns:
        op.drop_column("brand_profiles", "profile_approved_at")
    if "onboarding_flow" in existing_columns:
        op.drop_column("brand_profiles", "onboarding_flow")
