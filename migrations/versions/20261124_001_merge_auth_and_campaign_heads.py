"""merge_auth_and_campaign_heads

Iki ayri daldan gelen migration ucunu birlestirir (sema degisikligi YOK):
- deploy/auth-lean-merge : ... -> 20261029_002 (onceki birlesim)
- deploy/lean-taslak     : 20261029_001 -> ... -> 20261123_001 (dijital denetim, revizyon, kampanya)

Iki head varken uygulama acilisindaki `alembic upgrade head` durur; bu revizyon tek head'e indirir.

Revision ID: 20261124_001
Revises: 20261029_002, 20261123_001
Create Date: 2026-11-24
"""
from __future__ import annotations

from typing import Sequence, Union


revision: str = "20261124_001"
down_revision: Union[str, Sequence[str], None] = ("20261029_002", "20261123_001")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
