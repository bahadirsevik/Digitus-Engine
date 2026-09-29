"""add_seo_checklist_fields

Sirket SEO/GEO checklist entegrasyonu (plan: checklist + 500-600 kelime):
- seo_geo_contents.faq_items        : [{"question","answer"}] (FAQ schema verisi)
- seo_geo_contents.image_alt_texts  : ["..."] (gorsel alt-text onerileri)
- seo_compliance_results.checks_json: check() ciktisindaki kriter listesi
  (yeni checklist kriterlerinin KALICI kaydi — kolon-bazli 11 kriter alani
  eski satirlar icin aynen durur, yeni kriterler bu JSON'da yasar)

Revision ID: 20260712_001
Revises: 20260709_002
Create Date: 2026-07-12
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260712_001"
down_revision: Union[str, None] = "20260709_002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLUMNS = {
    "seo_geo_contents": ("faq_items", "image_alt_texts"),
    "seo_compliance_results": ("checks_json",),
}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table, columns in _COLUMNS.items():
        existing = {column["name"] for column in inspector.get_columns(table)}
        for column in columns:
            if column not in existing:
                op.add_column(table, sa.Column(column, sa.JSON(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table, columns in _COLUMNS.items():
        existing = {column["name"] for column in inspector.get_columns(table)}
        for column in columns:
            if column in existing:
                op.drop_column(table, column)
