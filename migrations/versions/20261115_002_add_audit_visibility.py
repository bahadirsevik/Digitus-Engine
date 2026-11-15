"""add_audit_visibility

Dijital denetim Faz 3 (plan: istekler/dijital_varlik_analizi_plan.md):
- digital_audits.visibility (arama gorunurlugu, YouTube, Google profil kimlikleri)

IDEMPOTENT: baseline squash canli `Base.metadata.create_all` kullandigi icin yeni
kurulumda kolon zaten olusur; varlik kontroluyle eklenir.

Revision ID: 20261115_002
Revises: 20261115_001
Create Date: 2026-11-15
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261115_002"
down_revision: Union[str, None] = "20261115_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE, COLUMN = "digital_audits", "visibility"


def _has_column() -> bool:
    return COLUMN in {c["name"] for c in sa.inspect(op.get_bind()).get_columns(TABLE)}


def upgrade() -> None:
    if not _has_column():
        op.add_column(TABLE, sa.Column(COLUMN, sa.JSON(), nullable=True))


def downgrade() -> None:
    if _has_column():
        op.drop_column(TABLE, COLUMN)
