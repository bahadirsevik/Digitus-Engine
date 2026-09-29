"""add_workspace_profile_flow_fields

Revision ID: 20260707_001
Revises: 20260518_001
Create Date: 2026-07-07
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260707_001"
down_revision: Union[str, None] = "20260518_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    existing_columns = {
        column["name"]
        for column in sa.inspect(bind).get_columns("brand_profiles")
    }
    if "excluded_info" not in existing_columns:
        op.add_column("brand_profiles", sa.Column("excluded_info", sa.Text(), nullable=True))
    if "crawl_content_cache" not in existing_columns:
        op.add_column("brand_profiles", sa.Column("crawl_content_cache", sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    existing_columns = {
        column["name"]
        for column in sa.inspect(bind).get_columns("brand_profiles")
    }
    if "crawl_content_cache" in existing_columns:
        op.drop_column("brand_profiles", "crawl_content_cache")
    if "excluded_info" in existing_columns:
        op.drop_column("brand_profiles", "excluded_info")
