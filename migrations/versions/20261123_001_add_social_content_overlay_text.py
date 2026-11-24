"""add_social_content_overlay_text

Kampanya statik icerigi (post/story) icin gorsel ustu metin:
social_contents.overlay_text (JSON {headline, benefit, cta}). Nullable; NULL = yok.

IDEMPOTENT: baseline squash canli `Base.metadata.create_all` kullandigi icin
kolon yeni kurulumda zaten olusur; varlik kontroluyle eklenir.

Revision ID: 20261123_001
Revises: 20261117_001
Create Date: 2026-11-23
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261123_001"
down_revision: Union[str, None] = "20261117_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, name: str) -> bool:
    return name in {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if not _has_column("social_contents", "overlay_text"):
        op.add_column("social_contents", sa.Column("overlay_text", sa.JSON(), nullable=True))


def downgrade() -> None:
    if _has_column("social_contents", "overlay_text"):
        op.drop_column("social_contents", "overlay_text")
