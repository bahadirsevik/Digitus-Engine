"""add_brand_profile_pending_focus

Marka onayindan sonra kendiliginden acilacak alt sayfa istegi (plan_marka_alt_sayfa.md Faz 3e):
brand_profiles.pending_focus_url (String(2048), nullable) ve
brand_profiles.pending_focus_error (String(500), nullable).

IDEMPOTENT: baseline squash canli `Base.metadata.create_all` kullandigi icin
yeni kurulumda kolonlar zaten olusur; her parca varlik kontroluyle eklenir,
yeniden calistirmak no-op'tur. Downgrade yalniz var olani dusurur.

Revision ID: 20261029_001
Revises: 20261028_002
Create Date: 2026-10-29
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261029_001"
down_revision: Union[str, None] = "20261028_002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "brand_profiles"
_COLUMNS = (("pending_focus_url", 2048), ("pending_focus_error", 500))


def _has_column(name: str) -> bool:
    return name in {c["name"] for c in sa.inspect(op.get_bind()).get_columns(_TABLE)}


def upgrade() -> None:
    for name, length in _COLUMNS:
        if not _has_column(name):
            op.add_column(_TABLE, sa.Column(name, sa.String(length), nullable=True))


def downgrade() -> None:
    for name, _ in _COLUMNS:
        if _has_column(name):
            op.drop_column(_TABLE, name)
