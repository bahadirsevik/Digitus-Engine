"""Add ScoringRun.channel_assignment_version (Codex A+B review, finding 1).

SOCIAL üretimi uzun AI çağrısı sırasında reassignment olursa çıktı
is_stale=false ("fresh") doğamaz: üretim başında versiyon snapshot'ı
alınır, kayıttan önce run kilitlenip karşılaştırılır.

Revision ID: 20260717_003
Revises: 20260717_002
Create Date: 2026-07-17
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260717_003"
down_revision: Union[str, None] = "20260717_002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {c["name"] for c in inspector.get_columns("scoring_runs")}
    if "channel_assignment_version" not in existing:
        op.add_column(
            "scoring_runs",
            sa.Column(
                "channel_assignment_version",
                sa.Integer(),
                nullable=False,
                server_default="1",
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {c["name"] for c in inspector.get_columns("scoring_runs")}
    if "channel_assignment_version" in existing:
        op.drop_column("scoring_runs", "channel_assignment_version")
