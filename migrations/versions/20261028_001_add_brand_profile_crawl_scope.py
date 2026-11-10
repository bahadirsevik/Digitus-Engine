"""add_brand_profile_crawl_scope

Workspace crawl kapsami (plan_marka_alt_sayfa.md Faz 2): brand_profiles.crawl_scope
('site' | 'page', NOT NULL, server_default 'site'). Mevcut satirlar 'site' olur
(bugunku davranis korunur).

IDEMPOTENT: baseline squash canli `Base.metadata.create_all` kullandigi icin
yeni kurulumda kolon zaten olusur; kolon-varlik kontrolu yapilir, yeniden
calistirmak no-op'tur.

Revision ID: 20261028_001
Revises: 20261017_001
Create Date: 2026-10-28
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261028_001"
down_revision: Union[str, None] = "20261017_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "brand_profiles"
_COLUMN = "crawl_scope"


def _has_column() -> bool:
    inspector = sa.inspect(op.get_bind())
    return _COLUMN in {column["name"] for column in inspector.get_columns(_TABLE)}


def upgrade() -> None:
    if not _has_column():
        op.add_column(
            _TABLE,
            sa.Column(
                _COLUMN, sa.String(length=10), nullable=False, server_default="site"
            ),
        )


def downgrade() -> None:
    if _has_column():
        op.drop_column(_TABLE, _COLUMN)
