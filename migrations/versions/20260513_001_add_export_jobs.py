"""add_export_jobs

Revision ID: 20260513_001
Revises: 20260511_001
Create Date: 2026-05-13

Plan2 §P2 — ExportJob table: DB-backed export status replaces in-memory dict.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260513_001"
down_revision: Union[str, None] = "20260511_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from sqlalchemy import inspect as sa_inspect

    bind = op.get_bind()
    if "export_jobs" in sa_inspect(bind).get_table_names():
        return  # baseline squash already created it via Base.metadata.create_all()

    op.create_table(
        "export_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "brand_profile_id",
            sa.Integer,
            sa.ForeignKey("brand_profiles.id"),
            nullable=False,
        ),
        sa.Column(
            "scoring_run_id",
            sa.Integer,
            sa.ForeignKey("scoring_runs.id"),
            nullable=True,
        ),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("progress", sa.Integer, nullable=False, server_default="0"),
        sa.Column("format", sa.String(10), nullable=True),
        sa.Column("sections", sa.JSON, nullable=True),
        sa.Column("include_stale_content", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("file_name", sa.String(500), nullable=True),
        sa.Column("filepath", sa.String(1000), nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'completed', 'failed')",
            name="ck_export_jobs_status",
        ),
    )
    op.create_index("idx_export_jobs_workspace", "export_jobs", ["brand_profile_id"])
    op.create_index("idx_export_jobs_run", "export_jobs", ["scoring_run_id"])
    op.create_index("idx_export_jobs_status", "export_jobs", ["status"])


def downgrade() -> None:
    from sqlalchemy import inspect as sa_inspect

    bind = op.get_bind()
    if "export_jobs" not in sa_inspect(bind).get_table_names():
        return

    op.drop_index("idx_export_jobs_status", table_name="export_jobs")
    op.drop_index("idx_export_jobs_run", table_name="export_jobs")
    op.drop_index("idx_export_jobs_workspace", table_name="export_jobs")
    op.drop_table("export_jobs")
