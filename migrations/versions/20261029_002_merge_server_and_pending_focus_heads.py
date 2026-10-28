"""merge_server_and_pending_focus_heads

Iki ayri daldan gelen migration ucunu birlestirir (sema degisikligi YOK):
- deploy/auth-lean-merge : ... -> 20261028_003 (sunucu + marka/odak birlesimi)
- deploy/lean-taslak     : 20261028_002 -> 20261029_001 (brand_profiles.pending_focus_*)

20261029_001 lean'de 20261028_002'ye bagli; sunucu dalinda 20261028_003 de ayni revizyondan
turedigi icin birlesimde alembic iki head gorur ve uygulama acilisindaki
`alembic upgrade head` durur. Bu revizyon tek head'e indirir.

Revision ID: 20261029_002
Revises: 20261028_003, 20261029_001
Create Date: 2026-10-29
"""
from __future__ import annotations

from typing import Sequence, Union


revision: str = "20261029_002"
down_revision: Union[str, Sequence[str], None] = ("20261028_003", "20261029_001")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
