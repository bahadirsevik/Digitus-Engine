"""add_profile_analysis_attempt_id

Profil analizinde eski sonucun yeni profili ezmesi (plan_yapilacaklar.md 2.2):
- brand_profiles.analysis_attempt_id : her arka plan profil analizi
  baslatmasinin urettigi UUID (String(36), nullable). Task'in butun
  status/profile yazimlari ve janitor yazimlari bu token'a kosulludur;
  token degismis bir attempt (yeni koşu basladi ya da janitor failed yapti)
  artik hicbir sey yazamaz.

IDEMPOTENT: baseline squash canli `Base.metadata.create_all` kullandigi icin
yeni kurulumda kolon zaten vardir; kolon-varlik kontrolu yapilir.

Revision ID: 20261010_001
Revises: 20260923_001
Create Date: 2026-10-10
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20261010_001"
down_revision: Union[str, None] = "20260923_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "brand_profiles"
_COLUMN = "analysis_attempt_id"


def _existing_columns() -> set:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(_TABLE)}


def upgrade() -> None:
    if _COLUMN not in _existing_columns():
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.String(length=36), nullable=True))


def downgrade() -> None:
    if _COLUMN in _existing_columns():
        op.drop_column(_TABLE, _COLUMN)
