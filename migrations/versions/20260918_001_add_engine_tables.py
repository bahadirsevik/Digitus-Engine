"""Motor v3: engine_stage_results + engine_selections.

plan_algoritma_entegrasyonu.md §4 — kilitli ADS/SEO/SOCIAL motorlarinin
uretim tablolari. MEVCUT tablolara ve migration'lara DOKUNULMAZ; yalniz iki
yeni tablo eklenir.

Not: Baseline squash (20260511_001) canli Base.metadata.create_all kullanir;
yeni kurulumda tablolar baseline'da zaten olusur. Bu yuzden migration
tablo-varlik kontrolu ile IDEMPOTENT yazilmistir.

Revision ID: 20260918_001
Revises: 20260807_001
Create Date: 2026-09-18
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260918_001"
down_revision: Union[str, None] = "20260807_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = ("engine_stage_results", "engine_selections")


def upgrade() -> None:
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())

    if "engine_stage_results" not in existing:
        op.create_table(
            "engine_stage_results",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("scoring_run_id", sa.Integer(), nullable=False),
            sa.Column("stage", sa.String(length=40), nullable=False),
            sa.Column("scope_type", sa.String(length=20), nullable=False),
            sa.Column("scope_key", sa.String(length=120), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("model", sa.String(length=60), nullable=False),
            sa.Column("prompt_sha", sa.String(length=64), nullable=False),
            sa.Column("firm_block_sha256", sa.String(length=64), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True),
                      server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["scoring_run_id"], ["scoring_runs.id"],
                                    ondelete="CASCADE"),
            sa.UniqueConstraint("scoring_run_id", "stage", "scope_type",
                                "scope_key",
                                name="uq_engine_stage_results_scope"),
            sa.CheckConstraint(
                "scope_type IN ('keyword', 'family', 'url_group', 'run')",
                name="ck_engine_stage_results_scope_type"),
        )
        op.create_index("idx_engine_stage_results_run_stage",
                        "engine_stage_results", ["scoring_run_id", "stage"])
        op.create_index(op.f("ix_engine_stage_results_id"),
                        "engine_stage_results", ["id"])

    if "engine_selections" not in existing:
        op.create_table(
            "engine_selections",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("scoring_run_id", sa.Integer(), nullable=False),
            sa.Column("keyword_id", sa.Integer(), nullable=False),
            sa.Column("channel", sa.String(length=20), nullable=False),
            sa.Column("algorithm_rank", sa.Integer(), nullable=False),
            sa.Column("scores", sa.JSON(), nullable=True),
            sa.Column("pool_class", sa.String(length=20), nullable=True),
            sa.Column("family_id", sa.String(length=120), nullable=True),
            sa.Column("priority", sa.String(length=30), nullable=True),
            sa.Column("final_rank", sa.Integer(), nullable=True),
            sa.Column("exclude_reason", sa.String(length=60), nullable=True),
            sa.Column("policy_version", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True),
                      server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["scoring_run_id"], ["scoring_runs.id"],
                                    ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["keyword_id"], ["keywords.id"],
                                    ondelete="CASCADE"),
            sa.UniqueConstraint("scoring_run_id", "keyword_id", "channel",
                                name="uq_engine_selections_run_keyword_channel"),
            sa.CheckConstraint("channel IN ('ADS', 'SEO', 'SOCIAL')",
                               name="ck_engine_selections_channel"),
            sa.CheckConstraint(
                "(final_rank IS NULL) <> (exclude_reason IS NULL)",
                name="ck_engine_selections_selected_xor_excluded"),
        )
        op.create_index("idx_engine_selections_run_channel_rank",
                        "engine_selections",
                        ["scoring_run_id", "channel", "algorithm_rank"])
        op.create_index(op.f("ix_engine_selections_id"),
                        "engine_selections", ["id"])


def downgrade() -> None:
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())
    for table in _TABLES:
        if table in existing:
            op.drop_table(table)
