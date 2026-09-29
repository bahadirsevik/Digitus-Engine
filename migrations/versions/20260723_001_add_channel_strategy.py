"""v2.1 kanal stratejisi sözleşmesi (plan Faz C).

- brand_profiles: channel_strategy (JSON), strategy_version (int, default 0)
- scoring_runs: algorithm_version ('v2'|'v2_1', default 'v2' — mevcut run'lar
  semantik olarak v2 kabul edilir, yeniden yorumlanmaz),
  channel_pool_strategy_version (nullable — yalnız başarılı v2_1 finalize yazar)
- intent_analysis: strategy_fit (nullable Boolean — S_G; gt yeniden KULLANILMAZ)

Backfill YOK: skorlar/AI sonuçları/havuzlar yeniden hesaplanmaz (plan §6).
İdempotent kalıp: baseline squash canlı create_all kullandığından her kolon
varlık-kontrollü eklenir (repo kuralı; örnek: 20260718_001).

Revision ID: 20260723_001
Revises: 20260718_001
Create Date: 2026-07-23
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260723_001"
down_revision: Union[str, None] = "20260718_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ALGO_CK = "ck_scoring_runs_algorithm_version"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    bp_cols = {c["name"] for c in inspector.get_columns("brand_profiles")}
    if "channel_strategy" not in bp_cols:
        op.add_column(
            "brand_profiles", sa.Column("channel_strategy", sa.JSON(), nullable=True)
        )
    if "strategy_version" not in bp_cols:
        op.add_column(
            "brand_profiles",
            sa.Column(
                "strategy_version", sa.Integer(), nullable=False,
                server_default="0",
            ),
        )

    run_cols = {c["name"] for c in inspector.get_columns("scoring_runs")}
    if "algorithm_version" not in run_cols:
        op.add_column(
            "scoring_runs",
            sa.Column(
                "algorithm_version", sa.String(10), nullable=False,
                server_default="v2",
            ),
        )
    if "channel_pool_strategy_version" not in run_cols:
        op.add_column(
            "scoring_runs",
            sa.Column("channel_pool_strategy_version", sa.Integer(), nullable=True),
        )
    existing_cks = {
        ck["name"] for ck in inspector.get_check_constraints("scoring_runs")
    }
    if _ALGO_CK not in existing_cks:
        op.create_check_constraint(
            _ALGO_CK, "scoring_runs", "algorithm_version IN ('v2', 'v2_1')"
        )

    ia_cols = {c["name"] for c in inspector.get_columns("intent_analysis")}
    if "strategy_fit" not in ia_cols:
        op.add_column(
            "intent_analysis",
            sa.Column("strategy_fit", sa.Boolean(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    ia_cols = {c["name"] for c in inspector.get_columns("intent_analysis")}
    if "strategy_fit" in ia_cols:
        op.drop_column("intent_analysis", "strategy_fit")

    existing_cks = {
        ck["name"] for ck in inspector.get_check_constraints("scoring_runs")
    }
    if _ALGO_CK in existing_cks:
        op.drop_constraint(_ALGO_CK, "scoring_runs", type_="check")
    run_cols = {c["name"] for c in inspector.get_columns("scoring_runs")}
    if "channel_pool_strategy_version" in run_cols:
        op.drop_column("scoring_runs", "channel_pool_strategy_version")
    if "algorithm_version" in run_cols:
        op.drop_column("scoring_runs", "algorithm_version")

    bp_cols = {c["name"] for c in inspector.get_columns("brand_profiles")}
    if "strategy_version" in bp_cols:
        op.drop_column("brand_profiles", "strategy_version")
    if "channel_strategy" in bp_cols:
        op.drop_column("brand_profiles", "channel_strategy")
