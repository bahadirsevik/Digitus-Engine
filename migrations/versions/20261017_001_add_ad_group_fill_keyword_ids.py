"""add_ad_group_fill_keyword_ids

15 KW teslim standardi (plan_ads_15kw_standardi §4): ad_groups.fill_keyword_ids
(nullable JSON) — gruplayicinin 15 KW'ye tamamlamak icin yerlestirdigi/tasidigi
KW id'leri. Backfill YOK: NULL = bilinmiyor (eski kayit).

IDEMPOTENT: baseline squash canli `Base.metadata.create_all` kullandigi icin
yeni kurulumda kolon zaten olusur; kolon-varlik kontrolu yapilir, yeniden
calistirmak no-op'tur.

Revision ID: 20261017_001
Revises: 20261011_001
Create Date: 2026-10-17
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261017_001"
down_revision: Union[str, None] = "20261011_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "ad_groups"
_COLUMN = "fill_keyword_ids"


def _has_column() -> bool:
    inspector = sa.inspect(op.get_bind())
    return _COLUMN in {column["name"] for column in inspector.get_columns(_TABLE)}


def upgrade() -> None:
    if not _has_column():
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.JSON(), nullable=True))


def downgrade() -> None:
    if _has_column():
        op.drop_column(_TABLE, _COLUMN)
