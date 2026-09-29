"""Add Social Brief flow models and constraints (plan Faz F1-A).

Tables:
- social_briefs
- social_brief_keywords
- social_brief_targets
- social_generation_attempts

Existing model extensions:
- social_categories (brief_id)
- social_ideas (brief_id, brief_target_id)
- social_contents (brief_id, format_payload, duration_status, actual_duration_sec, validation_warnings)

Partial unique indexes:
- uq_social_content_idea_brief: UNIQUE(idea_id) WHERE brief_id IS NOT NULL
- uq_social_gen_attempt_active: UNIQUE(brief_id, stage) WHERE status IN ('pending', 'running')

Revision ID: 20260923_001
Revises: 20260922_001
Create Date: 2026-09-23
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260923_001"
down_revision: Union[str, None] = "20260922_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables(inspector) -> set[str]:
    return set(inspector.get_table_names())


def _columns(inspector, table: str) -> set[str]:
    return {c["name"] for c in inspector.get_columns(table)}


def _indexes(inspector, table: str) -> set[str]:
    return {i["name"] for i in inspector.get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = _tables(inspector)

    # 1. social_briefs
    if "social_briefs" not in tables:
        op.create_table(
            "social_briefs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "scoring_run_id",
                sa.Integer(),
                sa.ForeignKey("scoring_runs.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("brand_name_snapshot", sa.String(length=200), nullable=True),
            sa.Column("brand_context_snapshot", sa.Text(), nullable=True),
            sa.Column(
                "channel_assignment_version",
                sa.Integer(),
                nullable=False,
                server_default="1",
            ),
            sa.Column(
                "format_matrix_version",
                sa.String(length=50),
                nullable=False,
                server_default="v1",
            ),
            sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "is_stale",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
            ),
        )
        op.create_index(
            "idx_social_briefs_run_stale",
            "social_briefs",
            ["scoring_run_id", "is_stale"],
        )
        op.create_index(
            "ix_social_briefs_scoring_run_id",
            "social_briefs",
            ["scoring_run_id"],
        )

    # 2. social_brief_keywords
    inspector = sa.inspect(bind)
    tables = _tables(inspector)
    if "social_brief_keywords" not in tables:
        op.create_table(
            "social_brief_keywords",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "brief_id",
                sa.Integer(),
                sa.ForeignKey("social_briefs.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "keyword_id",
                sa.Integer(),
                sa.ForeignKey("keywords.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("keyword_snapshot", sa.String(length=255), nullable=False),
            sa.Column(
                "position", sa.Integer(), nullable=False, server_default="0"
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
            ),
            sa.UniqueConstraint(
                "brief_id", "keyword_id", name="uq_social_brief_keyword"
            ),
        )
        op.create_index(
            "ix_social_brief_keywords_brief_id",
            "social_brief_keywords",
            ["brief_id"],
        )
        op.create_index(
            "ix_social_brief_keywords_keyword_id",
            "social_brief_keywords",
            ["keyword_id"],
        )

    # 3. social_brief_targets
    if "social_brief_targets" not in tables:
        op.create_table(
            "social_brief_targets",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "brief_id",
                sa.Integer(),
                sa.ForeignKey("social_briefs.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("platform", sa.String(length=50), nullable=False),
            sa.Column("content_format", sa.String(length=50), nullable=False),
            sa.Column("duration_preset_id", sa.String(length=50), nullable=True),
            sa.Column("duration_min_sec", sa.Integer(), nullable=True),
            sa.Column("duration_max_sec", sa.Integer(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
            ),
            sa.UniqueConstraint(
                "brief_id",
                "platform",
                "content_format",
                name="uq_social_brief_target_platform_format",
            ),
            sa.CheckConstraint(
                "((duration_min_sec IS NULL AND duration_max_sec IS NULL) OR "
                "(duration_min_sec IS NOT NULL AND duration_max_sec IS NOT NULL AND "
                "duration_min_sec >= 1 AND duration_max_sec >= duration_min_sec))",
                name="ck_social_brief_targets_duration_bounds",
            ),
            sa.CheckConstraint(
                "((content_format IN ('video', 'reels', 'short') AND "
                "duration_preset_id IS NOT NULL AND duration_min_sec IS NOT NULL AND duration_max_sec IS NOT NULL) OR "
                "(content_format NOT IN ('video', 'reels', 'short') AND "
                "duration_preset_id IS NULL AND duration_min_sec IS NULL AND duration_max_sec IS NULL))",
                name="ck_social_brief_targets_format_duration",
            ),
        )
        op.create_index(
            "ix_social_brief_targets_brief_id",
            "social_brief_targets",
            ["brief_id"],
        )

    # 4. social_generation_attempts
    if "social_generation_attempts" not in tables:
        op.create_table(
            "social_generation_attempts",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "brief_id",
                sa.Integer(),
                sa.ForeignKey("social_briefs.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("stage", sa.String(length=50), nullable=False),
            sa.Column("idempotency_key", sa.String(length=128), nullable=False),
            sa.Column(
                "status",
                sa.String(length=30),
                nullable=False,
                server_default="pending",
            ),
            sa.Column("requested_target_ids", sa.JSON(), nullable=True),
            sa.Column("requested_idea_ids", sa.JSON(), nullable=True),
            sa.Column("coverage", sa.JSON(), nullable=True),
            sa.Column("warnings", sa.JSON(), nullable=True),
            sa.Column(
                "task_id", sa.String(length=155), nullable=True, unique=True
            ),
            sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
            ),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("reason_code", sa.String(length=50), nullable=True),
            sa.UniqueConstraint(
                "brief_id",
                "stage",
                "idempotency_key",
                name="uq_social_gen_attempt_idempotency",
            ),
        )
        op.create_index(
            "uq_social_gen_attempt_active",
            "social_generation_attempts",
            ["brief_id", "stage"],
            unique=True,
            postgresql_where=sa.text("status IN ('pending', 'running')"),
        )
        op.create_index(
            "idx_social_gen_attempt_brief_status",
            "social_generation_attempts",
            ["brief_id", "status"],
        )

    # 5. Mevcut tablolara kolon ekleri (varlık kontrollü)
    inspector = sa.inspect(bind)
    cat_cols = _columns(inspector, "social_categories")
    if "brief_id" not in cat_cols:
        op.add_column(
            "social_categories",
            sa.Column(
                "brief_id",
                sa.Integer(),
                sa.ForeignKey("social_briefs.id", ondelete="CASCADE"),
                nullable=True,
            ),
        )
        op.create_index(
            "ix_social_categories_brief_id", "social_categories", ["brief_id"]
        )

    idea_cols = _columns(inspector, "social_ideas")
    if "brief_id" not in idea_cols:
        op.add_column(
            "social_ideas",
            sa.Column(
                "brief_id",
                sa.Integer(),
                sa.ForeignKey("social_briefs.id", ondelete="CASCADE"),
                nullable=True,
            ),
        )
        op.create_index("ix_social_ideas_brief_id", "social_ideas", ["brief_id"])
    if "brief_target_id" not in idea_cols:
        op.add_column(
            "social_ideas",
            sa.Column(
                "brief_target_id",
                sa.Integer(),
                sa.ForeignKey("social_brief_targets.id", ondelete="SET NULL"),
                nullable=True,
            ),
        )
        op.create_index(
            "ix_social_ideas_brief_target_id", "social_ideas", ["brief_target_id"]
        )

    content_cols = _columns(inspector, "social_contents")
    if "brief_id" not in content_cols:
        op.add_column(
            "social_contents",
            sa.Column(
                "brief_id",
                sa.Integer(),
                sa.ForeignKey("social_briefs.id", ondelete="CASCADE"),
                nullable=True,
            ),
        )
        op.create_index(
            "ix_social_contents_brief_id", "social_contents", ["brief_id"]
        )
    if "format_payload" not in content_cols:
        op.add_column(
            "social_contents",
            sa.Column("format_payload", sa.JSON(), nullable=True),
        )
    if "duration_status" not in content_cols:
        op.add_column(
            "social_contents",
            sa.Column("duration_status", sa.String(length=30), nullable=True),
        )
    if "actual_duration_sec" not in content_cols:
        op.add_column(
            "social_contents",
            sa.Column("actual_duration_sec", sa.Integer(), nullable=True),
        )
    if "validation_warnings" not in content_cols:
        op.add_column(
            "social_contents",
            sa.Column("validation_warnings", sa.JSON(), nullable=True),
        )

    # 6. Partial unique index for SocialContent
    content_indexes = _indexes(inspector, "social_contents")
    if "uq_social_content_idea_brief" not in content_indexes:
        op.create_index(
            "uq_social_content_idea_brief",
            "social_contents",
            ["idea_id"],
            unique=True,
            postgresql_where=sa.text("brief_id IS NOT NULL"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = _tables(inspector)

    if "social_contents" in tables:
        c_indexes = _indexes(inspector, "social_contents")
        if "uq_social_content_idea_brief" in c_indexes:
            op.drop_index("uq_social_content_idea_brief", table_name="social_contents")
        c_cols = _columns(inspector, "social_contents")
        for col in [
            "validation_warnings",
            "actual_duration_sec",
            "duration_status",
            "format_payload",
        ]:
            if col in c_cols:
                op.drop_column("social_contents", col)
        if "brief_id" in c_cols:
            if "ix_social_contents_brief_id" in c_indexes:
                op.drop_index("ix_social_contents_brief_id", table_name="social_contents")
            op.drop_column("social_contents", "brief_id")

    if "social_ideas" in tables:
        i_indexes = _indexes(inspector, "social_ideas")
        i_cols = _columns(inspector, "social_ideas")
        if "brief_target_id" in i_cols:
            if "ix_social_ideas_brief_target_id" in i_indexes:
                op.drop_index("ix_social_ideas_brief_target_id", table_name="social_ideas")
            op.drop_column("social_ideas", "brief_target_id")
        if "brief_id" in i_cols:
            if "ix_social_ideas_brief_id" in i_indexes:
                op.drop_index("ix_social_ideas_brief_id", table_name="social_ideas")
            op.drop_column("social_ideas", "brief_id")

    if "social_categories" in tables:
        cat_indexes = _indexes(inspector, "social_categories")
        cat_cols = _columns(inspector, "social_categories")
        if "brief_id" in cat_cols:
            if "ix_social_categories_brief_id" in cat_indexes:
                op.drop_index("ix_social_categories_brief_id", table_name="social_categories")
            op.drop_column("social_categories", "brief_id")

    for tbl in [
        "social_generation_attempts",
        "social_brief_targets",
        "social_brief_keywords",
        "social_briefs",
    ]:
        if tbl in tables:
            op.drop_table(tbl)
