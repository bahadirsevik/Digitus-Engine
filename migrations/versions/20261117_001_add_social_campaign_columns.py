"""add_social_campaign_columns

Sosyal brief kampanya modu (plan_sosyal_kampanya_akisi.md §3):
social_briefs.campaign_spec (JSON), social_ideas.campaign_payload (JSON),
social_contents.campaign_payload (JSON), social_contents.image_prompt (TEXT).
Hepsi nullable; NULL = organik (bugunku) davranis.

IDEMPOTENT: baseline squash canli `Base.metadata.create_all` kullandigi icin
yeni kurulumda kolonlar zaten olusur; her parca varlik kontroluyle eklenir,
yeniden calistirmak no-op'tur. Downgrade yalniz var olani dusurur.

Revision ID: 20261117_001
Revises: 20261115_002
Create Date: 2026-11-17
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261117_001"
down_revision: Union[str, None] = "20261115_002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLUMNS = (
    ("social_briefs", "campaign_spec", sa.JSON),
    ("social_ideas", "campaign_payload", sa.JSON),
    ("social_contents", "campaign_payload", sa.JSON),
    ("social_contents", "image_prompt", sa.Text),
)


def _has_column(table: str, name: str) -> bool:
    return name in {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    for table, name, type_ in _COLUMNS:
        if not _has_column(table, name):
            op.add_column(table, sa.Column(name, type_(), nullable=True))


def downgrade() -> None:
    for table, name, _ in _COLUMNS:
        if _has_column(table, name):
            op.drop_column(table, name)
