"""merge_auth_and_profile_attempt_heads

Iki ayri daldan gelen migration ucunu birlestirir (sema degisikligi YOK):
- feat/auth-login : 20261006_001 (users) -> 20261006_002 (must_change_password)
- deploy/lean-taslak : 20261010_001 (brand_profiles.analysis_attempt_id)

Ikisi de 20260923_001'den ayrildigi icin birlesimde alembic iki head gorur ve
`alembic upgrade head` durur. Bu revizyon tek head'e indirir.

Revision ID: 20261010_002
Revises: 20261006_002, 20261010_001
Create Date: 2026-10-10
"""
from __future__ import annotations

from typing import Sequence, Union


revision: str = "20261010_002"
down_revision: Union[str, Sequence[str], None] = ("20261006_002", "20261010_001")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
