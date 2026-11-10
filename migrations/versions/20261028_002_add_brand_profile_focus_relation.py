"""add_brand_profile_focus_relation

Marka -> odak (alt sayfa) iliskisi (plan_marka_alt_sayfa.md Faz 3a):
brand_profiles.parent_brand_profile_id (nullable FK -> brand_profiles.id, index) ve
brand_profiles.archived_with_parent (BOOLEAN NOT NULL, server_default false).

IDEMPOTENT: baseline squash canli `Base.metadata.create_all` kullandigi icin
yeni kurulumda kolon/index/FK zaten olusur; her parca varlik kontroluyle eklenir,
yeniden calistirmak no-op'tur. Downgrade yalniz var olani dusurur.

Revision ID: 20261028_002
Revises: 20261028_001
Create Date: 2026-10-28
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261028_002"
down_revision: Union[str, None] = "20261028_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "brand_profiles"
_PARENT = "parent_brand_profile_id"
_ARCHIVED = "archived_with_parent"
_INDEX = "ix_brand_profiles_parent_brand_profile_id"
_FK = "fk_brand_profiles_parent_brand_profile_id"


def _inspector():
    return sa.inspect(op.get_bind())


def _has_column(name: str) -> bool:
    return name in {c["name"] for c in _inspector().get_columns(_TABLE)}


def _has_index() -> bool:
    return _INDEX in {i["name"] for i in _inspector().get_indexes(_TABLE)}


def _parent_fk_names() -> list:
    return [
        fk["name"]
        for fk in _inspector().get_foreign_keys(_TABLE)
        if fk["constrained_columns"] == [_PARENT] and fk["name"]
    ]


def upgrade() -> None:
    if not _has_column(_PARENT):
        op.add_column(_TABLE, sa.Column(_PARENT, sa.Integer(), nullable=True))
    if not _parent_fk_names():
        op.create_foreign_key(_FK, _TABLE, _TABLE, [_PARENT], ["id"])
    if not _has_index():
        op.create_index(_INDEX, _TABLE, [_PARENT])
    if not _has_column(_ARCHIVED):
        op.add_column(
            _TABLE,
            sa.Column(_ARCHIVED, sa.Boolean(), nullable=False, server_default="false"),
        )


def downgrade() -> None:
    if _has_column(_ARCHIVED):
        op.drop_column(_TABLE, _ARCHIVED)
    if _has_index():
        op.drop_index(_INDEX, table_name=_TABLE)
    for name in _parent_fk_names():
        op.drop_constraint(name, _TABLE, type_="foreignkey")
    if _has_column(_PARENT):
        op.drop_column(_TABLE, _PARENT)
