"""merge_session_version_and_ads_heads

Iki ayri daldan gelen migration ucunu birlestirir (sema degisikligi YOK):
- deploy/auth-lean-merge : 20261010_002 -> 20261010_003 (users.session_version)
- deploy/lean-taslak     : 20261010_001 -> 20261011_001 (ai_usage provider kolonlari)
                           -> 20261017_001 (ad_groups.fill_keyword_ids)

Ikisi de 20261010_001'den sonra ayrildigi icin birlesimde alembic iki head gorur ve
uygulama acilisindaki `alembic upgrade head` durur. Bu revizyon tek head'e indirir.

Revision ID: 20261020_001
Revises: 20261010_003, 20261017_001
Create Date: 2026-10-20
"""
from __future__ import annotations

from typing import Sequence, Union


revision: str = "20261020_001"
down_revision: Union[str, Sequence[str], None] = ("20261010_003", "20261017_001")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
