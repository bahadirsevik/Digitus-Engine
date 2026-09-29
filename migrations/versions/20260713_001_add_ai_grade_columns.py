"""Add AI grade columns for scoring v2 stage-2 contracts.

Skorlama v2 (Digitus_Engine_v2_Skorlama_Algoritmalari.md) Aşama 2:
- intent_analysis.gt / .ga : SEO niyet dereceleri (G_T satın alma niyeti,
  G_A ürün-kategori araması); seçim anında N_SEO'ya puan olarak eklenir
- pre_filter_results.ai_class : ADS 2/1/-1, SOCIAL 0-3 sınıfı;
  final havuz sıralamasında birincil anahtar
- channel_pools.pool_label : 'rising_opportunity' (Yükselen Fırsat) etiketi

Not: Baseline squash (20260511_001) canlı Base.metadata.create_all kullanır;
yeni kurulumda kolonlar baseline'da zaten oluşur. Bu yüzden tüm additive
migration'lar gibi kolon-varlık kontrolüyle idempotent yazılmıştır.

Revision ID: 20260713_001
Revises: 20260712_001
Create Date: 2026-07-13
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260713_001"
down_revision: Union[str, None] = "20260712_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLUMNS = {
    "intent_analysis": (
        ("gt", sa.Boolean()),
        ("ga", sa.Boolean()),
    ),
    "pre_filter_results": (
        ("ai_class", sa.SmallInteger()),
    ),
    "channel_pools": (
        ("pool_label", sa.String(length=30)),
    ),
}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table, columns in _COLUMNS.items():
        existing = {column["name"] for column in inspector.get_columns(table)}
        for name, col_type in columns:
            if name not in existing:
                op.add_column(table, sa.Column(name, col_type, nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table, columns in _COLUMNS.items():
        existing = {column["name"] for column in inspector.get_columns(table)}
        for name, _col_type in columns:
            if name in existing:
                op.drop_column(table, name)
