"""Merge migration heads: auth (users) chain + lean-taslak chain.

Sunucu dalı (feat/auth-login) 20260923_001'den sonra kullanıcı tablolarını
(20261006_001/002) ekledi; deploy/lean-taslak aynı noktadan sonra kendi
zincirini (20261010_001 ... 20261029_001) ekledi. İki dal birleşince iki head
oluşur ve `alembic upgrade head` (prod app açılışı) başarısız olur. Bu boş
merge revizyonu iki head'i birleştirir; şema değişikliği yoktur.

Revision ID: 20261110_001
Revises: 20261006_002, 20261029_001
Create Date: 2026-11-10
"""
from typing import Sequence, Union

revision: str = "20261110_001"
down_revision: Union[str, Sequence[str], None] = ("20261006_002", "20261029_001")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
