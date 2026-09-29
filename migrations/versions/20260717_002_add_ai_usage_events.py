"""Add ai_usage_events table and ScoringRun.execution_manifest (plan C).

Not: Baseline squash canlı Base.metadata.create_all kullanır — yeni kurulumda
tablo/kolon baseline'da zaten oluşur; idempotent varlık kontrolü şarttır.

Revision ID: 20260717_002
Revises: 20260717_001
Create Date: 2026-07-17
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260717_002"
down_revision: Union[str, None] = "20260717_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "ai_usage_events" not in inspector.get_table_names():
        op.create_table(
            "ai_usage_events",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "scoring_run_id", sa.Integer(),
                sa.ForeignKey("scoring_runs.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "brand_profile_id", sa.Integer(),
                sa.ForeignKey("brand_profiles.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("task_id", sa.String(length=64), nullable=True),
            sa.Column("request_id", sa.String(length=64), nullable=False),
            sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("stage", sa.String(length=60), nullable=False),
            sa.Column("model", sa.String(length=100), nullable=False),
            sa.Column("prompt_tokens", sa.Integer(), nullable=True),
            sa.Column("candidates_tokens", sa.Integer(), nullable=True),
            sa.Column("thoughts_tokens", sa.Integer(), nullable=True),
            sa.Column("total_tokens", sa.Integer(), nullable=True),
            sa.Column("finish_reason", sa.String(length=40), nullable=True),
            sa.Column("latency_ms", sa.Integer(), nullable=True),
            sa.Column("retry_reason", sa.String(length=255), nullable=True),
            sa.Column("cache_status", sa.String(length=20), nullable=True),
            sa.Column("price_snapshot", sa.JSON(), nullable=True),
            sa.Column(
                "created_at", sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
            ),
            sa.UniqueConstraint(
                "request_id", "attempt", name="uq_ai_usage_request_attempt"
            ),
        )
        op.create_index(
            "idx_ai_usage_run_created", "ai_usage_events",
            ["scoring_run_id", "created_at"],
        )
        op.create_index("idx_ai_usage_task", "ai_usage_events", ["task_id"])
        op.create_index(
            "idx_ai_usage_stage_model", "ai_usage_events", ["stage", "model"]
        )

    existing = {c["name"] for c in inspector.get_columns("scoring_runs")}
    if "execution_manifest" not in existing:
        op.add_column(
            "scoring_runs",
            sa.Column("execution_manifest", sa.JSON(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {c["name"] for c in inspector.get_columns("scoring_runs")}
    if "execution_manifest" in existing:
        op.drop_column("scoring_runs", "execution_manifest")
    if "ai_usage_events" in inspector.get_table_names():
        op.drop_table("ai_usage_events")
