"""Add competitor/topic/capability policy columns and SOCIAL is_stale.

Kalite+maliyet planı A+B (plan_kalite_maliyet.md):
- brand_profiles.competitor_terms : [{term, status: suggested|approved|rejected,
  source: domain|user, created_at}] — YALNIZ approved filtreye girer
- brand_profiles.competitor_policy : {ads|seo|social: block|allow} (default block)
- brand_profiles.topic_policy : {excluded_terms: [...], excluded_aliases: [...]}
- brand_profiles.capabilities : {key: {value, source, approved_at, approved_by}}
- social_categories/ideas/contents.is_stale : içerik staleness (kanal
  reassignment) — parent-child invariant tüketici sorgularında doğrulanır

Not: Baseline squash (20260511_001) canlı Base.metadata.create_all kullanır;
yeni kurulumda kolonlar baseline'da zaten oluşur. Bu yüzden tüm additive
migration'lar gibi kolon-varlık kontrolüyle idempotent yazılmıştır.

Revision ID: 20260717_001
Revises: 20260716_001
Create Date: 2026-07-17
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260717_001"
down_revision: Union[str, None] = "20260716_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_JSON_COLUMNS = {
    "brand_profiles": (
        "competitor_terms",
        "competitor_policy",
        "topic_policy",
        "capabilities",
    ),
}

_STALE_TABLES = ("social_categories", "social_ideas", "social_contents")


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    for table, columns in _JSON_COLUMNS.items():
        existing = {column["name"] for column in inspector.get_columns(table)}
        for name in columns:
            if name not in existing:
                op.add_column(table, sa.Column(name, sa.JSON(), nullable=True))

    for table in _STALE_TABLES:
        existing = {column["name"] for column in inspector.get_columns(table)}
        if "is_stale" not in existing:
            op.add_column(
                table,
                sa.Column(
                    "is_stale",
                    sa.Boolean(),
                    nullable=False,
                    server_default="false",
                ),
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    for table in _STALE_TABLES:
        existing = {column["name"] for column in inspector.get_columns(table)}
        if "is_stale" in existing:
            op.drop_column(table, "is_stale")

    for table, columns in _JSON_COLUMNS.items():
        existing = {column["name"] for column in inspector.get_columns(table)}
        for name in columns:
            if name in existing:
                op.drop_column(table, name)
