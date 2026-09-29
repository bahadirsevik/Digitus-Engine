"""Motor v3: scoring_runs.algorithm_version server default degerini 'v3' yap.

ADR-004 ve V3-only sozlesmesi geregince yeni analiz kayitlari yalnizca 'v3'
algoritmasi ile olusturulur. Bu migration, scoring_runs.algorithm_version
kolonunun server_default degerini 'v3' olarak gunceller. Tarihsel satirlarin
algorithm_version degerlerine dokunulmaz (idempotent).

Revision ID: 20260922_001
Revises: 20260918_002
Create Date: 2026-09-22
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260922_001"
down_revision: Union[str, None] = "20260918_002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    cols = {c["name"]: c for c in inspector.get_columns("scoring_runs")}
    col = cols.get("algorithm_version")
    if col is not None:
        curr_default = str(col.get("default") or "")
        # Idempotent: zaten 'v3' ise tekrar alter etme
        if "'v3'" in curr_default or curr_default == "v3":
            return
        op.alter_column(
            "scoring_runs",
            "algorithm_version",
            server_default="v3",
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    cols = {c["name"]: c for c in inspector.get_columns("scoring_runs")}
    col = cols.get("algorithm_version")
    if col is not None:
        op.alter_column(
            "scoring_runs",
            "algorithm_version",
            server_default="v2",
        )
