"""add_digital_research

Dijital denetim Faz 2 (plan: istekler/dijital_varlik_analizi_plan.md):
- digital_research_reports (kaynaklı araştırma, segmentler, briefler; lease)

IDEMPOTENT: baseline squash canlı `Base.metadata.create_all` kullandığı için yeni
kurulumda tablo zaten oluşur; her parça varlık kontrolüyle eklenir.

Revision ID: 20261115_001
Revises: 20261114_001
Create Date: 2026-11-15
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261115_001"
down_revision: Union[str, None] = "20261114_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "digital_research_reports"


def _inspector():
    return sa.inspect(op.get_bind())


def upgrade() -> None:
    if TABLE not in _inspector().get_table_names():
        op.create_table(
            TABLE,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "brand_profile_id", sa.Integer(),
                sa.ForeignKey("brand_profiles.id", ondelete="CASCADE"), nullable=False,
            ),
            sa.Column(
                "audit_id", sa.Integer(),
                sa.ForeignKey("digital_audits.id", ondelete="SET NULL"), nullable=True,
            ),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
            sa.Column("stage", sa.String(length=20), nullable=False, server_default="research"),
            sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("inputs", sa.JSON(), nullable=True),
            sa.Column("sections", sa.JSON(), nullable=True),
            sa.Column("sections_done", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("briefs", sa.JSON(), nullable=True),
            sa.Column("claim_overrides", sa.JSON(), nullable=True),
            sa.Column("usage", sa.JSON(), nullable=True),
            sa.Column("task_id", sa.String(length=155), nullable=True, unique=True),
            sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        )

    indexes = {i["name"] for i in _inspector().get_indexes(TABLE)}
    if "ix_digital_research_reports_id" not in indexes:
        op.create_index("ix_digital_research_reports_id", TABLE, ["id"])
    if "uq_digital_research_active" not in indexes:
        op.create_index(
            "uq_digital_research_active", TABLE, ["brand_profile_id"],
            unique=True, postgresql_where=sa.text("status IN ('pending', 'running')"),
        )
    if "idx_digital_research_workspace" not in indexes:
        op.create_index("idx_digital_research_workspace", TABLE, ["brand_profile_id", "created_at"])


def downgrade() -> None:
    if TABLE in _inspector().get_table_names():
        op.drop_table(TABLE)
