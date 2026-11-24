"""add_digital_audits

Dijital varlık denetimi (plan: istekler/dijital_varlik_analizi_plan.md):
- digital_audits (workspace'e bağlı çalışma; ScoringRun'dan bağımsız; lease)
- digital_audit_targets (marka + rakip siteleri ve bulguları)

IDEMPOTENT: baseline squash canlı `Base.metadata.create_all` kullandığı için yeni
kurulumda tablolar zaten oluşur; her parça varlık kontrolüyle eklenir.

Revision ID: 20261114_001
Revises: 20261111_001
Create Date: 2026-11-14
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261114_001"
down_revision: Union[str, None] = "20261111_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _inspector():
    return sa.inspect(op.get_bind())


def upgrade() -> None:
    tables = _inspector().get_table_names()
    if "digital_audits" not in tables:
        op.create_table(
            "digital_audits",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "brand_profile_id", sa.Integer(),
                sa.ForeignKey("brand_profiles.id", ondelete="CASCADE"), nullable=False,
            ),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
            sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("scope", sa.JSON(), nullable=True),
            sa.Column("answers", sa.JSON(), nullable=True),
            sa.Column("task_id", sa.String(length=155), nullable=True, unique=True),
            sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        )
    if "digital_audit_targets" not in tables:
        op.create_table(
            "digital_audit_targets",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "audit_id", sa.Integer(),
                sa.ForeignKey("digital_audits.id", ondelete="CASCADE"), nullable=False,
            ),
            sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("kind", sa.String(length=12), nullable=False),
            sa.Column("url", sa.String(length=500), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
            sa.Column("pages_checked", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("findings", sa.JSON(), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        )

    audit_idx = {i["name"] for i in _inspector().get_indexes("digital_audits")}
    if "ix_digital_audits_id" not in audit_idx:
        op.create_index("ix_digital_audits_id", "digital_audits", ["id"])
    if "uq_digital_audit_active" not in audit_idx:
        op.create_index(
            "uq_digital_audit_active", "digital_audits", ["brand_profile_id"],
            unique=True, postgresql_where=sa.text("status IN ('pending', 'running')"),
        )
    if "idx_digital_audit_workspace" not in audit_idx:
        op.create_index("idx_digital_audit_workspace", "digital_audits", ["brand_profile_id", "created_at"])
    target_idx = {i["name"] for i in _inspector().get_indexes("digital_audit_targets")}
    if "ix_digital_audit_targets_id" not in target_idx:
        op.create_index("ix_digital_audit_targets_id", "digital_audit_targets", ["id"])
    if "ix_digital_audit_targets_audit_id" not in target_idx:
        op.create_index("ix_digital_audit_targets_audit_id", "digital_audit_targets", ["audit_id"])


def downgrade() -> None:
    tables = _inspector().get_table_names()
    if "digital_audit_targets" in tables:
        op.drop_table("digital_audit_targets")
    if "digital_audits" in tables:
        op.drop_table("digital_audits")
