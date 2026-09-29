"""Add AdGenerationSet versioning for ADS generation (plan Faz E).

Davranış: her başarılı ADS üretimi ayrı set; run başına tek 'active';
export/dashboard yalnız active + non-stale okur; eski setler silinmez.

Migration sırası (Codex review kararı — NOT NULL dahil):
1. ad_generation_sets tablosu + ad_groups.generation_set_id (nullable) ekle
2. Mevcut AdGroup'ları olan her run için version=1 'active' legacy set yarat
   (idempotent: set VARSA "tamamen atla" DEĞİL — null generation_set_id'li
   TÜM gruplar her durumda mevcut legacy sete bağlanır)
3. NULL kalmadığını doğrula
4. generation_set_id'yi NOT NULL yap

Not: baseline squash canlı Base.metadata.create_all kullanır — yeni
kurulumda tablo/kolon baseline'da zaten oluşur; her adım varlık kontrollü.

Revision ID: 20260716_001
Revises: 20260713_001
Create Date: 2026-07-16
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260716_001"
down_revision: Union[str, None] = "20260713_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # 1a. Tablo (varlık kontrollü)
    if "ad_generation_sets" not in inspector.get_table_names():
        op.create_table(
            "ad_generation_sets",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "scoring_run_id",
                sa.Integer(),
                sa.ForeignKey("scoring_runs.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("task_id", sa.String(length=155), nullable=True, unique=True),
            sa.Column("version_number", sa.Integer(), nullable=False),
            sa.Column(
                "status", sa.String(length=20),
                nullable=False, server_default="generating",
            ),
            sa.Column(
                "is_stale", sa.Boolean(),
                nullable=False, server_default=sa.text("false"),
            ),
            sa.Column("request_snapshot", sa.JSON(), nullable=True),
            sa.Column("groups_count", sa.Integer(), nullable=True),
            sa.Column("failed_groups", sa.Integer(), nullable=True),
            sa.Column("warnings", sa.JSON(), nullable=True),
            sa.Column(
                "created_at", sa.DateTime(timezone=True),
                server_default=sa.func.now(),
            ),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint(
                "scoring_run_id", "version_number",
                name="uq_ad_generation_set_version",
            ),
        )
        op.create_index(
            "uq_ad_generation_set_single_active",
            "ad_generation_sets",
            ["scoring_run_id"],
            unique=True,
            postgresql_where=sa.text("status = 'active'"),
        )
        op.create_index(
            "idx_ad_generation_set_run_status",
            "ad_generation_sets",
            ["scoring_run_id", "status"],
        )

    # 1b. ad_groups.generation_set_id (nullable — backfill'e kadar)
    ad_group_columns = {c["name"] for c in inspector.get_columns("ad_groups")}
    if "generation_set_id" not in ad_group_columns:
        op.add_column(
            "ad_groups",
            sa.Column(
                "generation_set_id",
                sa.Integer(),
                sa.ForeignKey("ad_generation_sets.id", ondelete="CASCADE"),
                nullable=True,
            ),
        )
        op.create_index(
            "ix_ad_groups_generation_set_id", "ad_groups", ["generation_set_id"]
        )

    # 2. Backfill: null generation_set_id'li grupları olan her run için
    #    legacy set bul-ya-da-yarat, TÜM null grupları bağla.
    run_ids = [
        row[0]
        for row in bind.execute(sa.text(
            "SELECT DISTINCT scoring_run_id FROM ad_groups "
            "WHERE generation_set_id IS NULL"
        )).fetchall()
    ]
    for run_id in run_ids:
        existing_set_id = bind.execute(sa.text(
            "SELECT id FROM ad_generation_sets "
            "WHERE scoring_run_id = :r AND version_number = 1"
        ), {"r": run_id}).scalar()
        if existing_set_id is None:
            # Legacy set: version 1, active (bu run'da başka aktif yoksa),
            # task_id NULL, snapshot legacy işaretli
            has_active = bind.execute(sa.text(
                "SELECT 1 FROM ad_generation_sets "
                "WHERE scoring_run_id = :r AND status = 'active'"
            ), {"r": run_id}).scalar()
            status = "archived" if has_active else "active"
            existing_set_id = bind.execute(sa.text(
                "INSERT INTO ad_generation_sets "
                "(scoring_run_id, task_id, version_number, status, is_stale, "
                " request_snapshot, groups_count) "
                "VALUES (:r, NULL, 1, :s, false, '{\"legacy\": true}'::json, "
                "        (SELECT count(*) FROM ad_groups "
                "         WHERE scoring_run_id = :r AND generation_set_id IS NULL)) "
                "RETURNING id"
            ), {"r": run_id, "s": status}).scalar()
        bind.execute(sa.text(
            "UPDATE ad_groups SET generation_set_id = :sid "
            "WHERE scoring_run_id = :r AND generation_set_id IS NULL"
        ), {"sid": existing_set_id, "r": run_id})

    # 3. Doğrulama: NULL kalmamalı
    remaining = bind.execute(sa.text(
        "SELECT count(*) FROM ad_groups WHERE generation_set_id IS NULL"
    )).scalar()
    if remaining:
        raise RuntimeError(
            f"AdGroup backfill eksik: {remaining} satır generation_set_id NULL"
        )

    # 4. NOT NULL (varlık/nullable kontrolüyle idempotent)
    inspector = sa.inspect(bind)  # kolon eklendiyse yeniden yükle
    col = next(
        c for c in inspector.get_columns("ad_groups")
        if c["name"] == "generation_set_id"
    )
    if col.get("nullable", True):
        op.alter_column(
            "ad_groups", "generation_set_id",
            existing_type=sa.Integer(), nullable=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    ad_group_columns = {c["name"] for c in inspector.get_columns("ad_groups")}
    if "generation_set_id" in ad_group_columns:
        op.drop_index("ix_ad_groups_generation_set_id", table_name="ad_groups")
        op.drop_column("ad_groups", "generation_set_id")
    if "ad_generation_sets" in inspector.get_table_names():
        op.drop_table("ad_generation_sets")
