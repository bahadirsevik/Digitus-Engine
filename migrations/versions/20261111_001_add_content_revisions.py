"""add_content_revisions

Kullanıcı geri bildirimli içerik revizyonu (SEO "Revize Et"; ileride sosyal):
- content_revisions tablosu (önizleme → kabul; lease + attempt_token; idempotency)
- seo_geo_contents.revision_count (kabulde artar; export kimliği + kaynak-sürüm kontrolü)

IDEMPOTENT: baseline squash canlı `Base.metadata.create_all` kullandığı için yeni
kurulumda tablo/kolon zaten oluşur; her parça varlık kontrolüyle eklenir, yeniden
çalıştırmak no-op'tur. Downgrade yalnız var olanı düşürür.

Revision ID: 20261111_001
Revises: 20261029_001
Create Date: 2026-11-11
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261111_001"
down_revision: Union[str, None] = "20261029_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "content_revisions"


def _inspector():
    return sa.inspect(op.get_bind())


def upgrade() -> None:
    insp = _inspector()
    seo_cols = {c["name"] for c in insp.get_columns("seo_geo_contents")}
    if "revision_count" not in seo_cols:
        op.add_column(
            "seo_geo_contents",
            sa.Column("revision_count", sa.Integer(), nullable=False, server_default="0"),
        )

    if _TABLE not in insp.get_table_names():
        op.create_table(
            _TABLE,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("channel", sa.String(length=20), nullable=False),
            sa.Column("source_content_id", sa.Integer(), nullable=False),
            sa.Column(
                "scoring_run_id", sa.Integer(),
                sa.ForeignKey("scoring_runs.id", ondelete="CASCADE"), nullable=False,
            ),
            sa.Column(
                "brand_profile_id", sa.Integer(),
                sa.ForeignKey("brand_profiles.id", ondelete="CASCADE"), nullable=False,
            ),
            sa.Column("feedback", sa.Text(), nullable=False),
            sa.Column("scope", sa.JSON(), nullable=False),
            sa.Column("source_version", sa.Integer(), nullable=False),
            sa.Column("proposed_payload", sa.JSON(), nullable=True),
            sa.Column("previous_payload", sa.JSON(), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="generating"),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("attempt_token", sa.String(length=64), nullable=False),
            sa.Column("idempotency_key", sa.String(length=64), nullable=True),
            sa.Column("task_id", sa.String(length=155), nullable=True),
            sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        )

    indexes = {i["name"] for i in _inspector().get_indexes(_TABLE)}
    if "ix_content_revisions_id" not in indexes:
        op.create_index("ix_content_revisions_id", _TABLE, ["id"])
    if "uq_content_revision_generating" not in indexes:
        op.create_index(
            "uq_content_revision_generating", _TABLE, ["channel", "source_content_id"],
            unique=True, postgresql_where=sa.text("status = 'generating'"),
        )
    if "uq_content_revision_idempotency" not in indexes:
        op.create_index(
            "uq_content_revision_idempotency", _TABLE,
            ["channel", "source_content_id", "idempotency_key"],
            unique=True, postgresql_where=sa.text("idempotency_key IS NOT NULL"),
        )
    if "idx_content_revision_source" not in indexes:
        op.create_index(
            "idx_content_revision_source", _TABLE, ["channel", "source_content_id", "status"],
        )


def downgrade() -> None:
    insp = _inspector()
    if _TABLE in insp.get_table_names():
        op.drop_table(_TABLE)
    if "revision_count" in {c["name"] for c in insp.get_columns("seo_geo_contents")}:
        op.drop_column("seo_geo_contents", "revision_count")
