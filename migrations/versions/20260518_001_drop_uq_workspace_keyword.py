"""drop_uq_workspace_keyword

Revision ID: 20260518_001
Revises: 20260513_001
Create Date: 2026-05-18

Aynı keyword'ün farklı metrik snapshot'larının aynı workspace'e
bağımsız kayıt olarak eklenebilmesi için uq_workspace_keyword
unique constraint kaldırılıyor.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "20260518_001"
down_revision: Union[str, None] = "20260513_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE workspace_keywords DROP CONSTRAINT IF EXISTS uq_workspace_keyword"
    )


def downgrade() -> None:
    op.create_unique_constraint(
        "uq_workspace_keyword", "workspace_keywords", ["brand_profile_id", "keyword_id"]
    )
