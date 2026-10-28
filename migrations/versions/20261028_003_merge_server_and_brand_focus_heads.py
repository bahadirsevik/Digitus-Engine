"""merge_server_and_brand_focus_heads

Iki ayri daldan gelen migration ucunu birlestirir (sema degisikligi YOK):
- deploy/auth-lean-merge : ... -> 20261020_001 (sunucunun 28.10 itibariyla head'i)
- deploy/lean-taslak     : 20261017_001 -> 20261028_001 (brand_profiles.crawl_scope)
                           -> 20261028_002 (parent_brand_profile_id + archived_with_parent)

20261028_001 sunucudaki 20261020_001 merge'unden ONCE ayrildigi icin birlesimde alembic
iki head gorur ve uygulama acilisindaki `alembic upgrade head` durur. Bu revizyon tek
head'e indirir.

Revision ID: 20261028_003
Revises: 20261020_001, 20261028_002
Create Date: 2026-10-28
"""
from __future__ import annotations

from typing import Sequence, Union


revision: str = "20261028_003"
down_revision: Union[str, Sequence[str], None] = ("20261020_001", "20261028_002")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
