"""Ledger denetim alanı: observed_actual_usd (Codex 7. tur #3).

`actual_usd <= ceiling_usd` CHECK'i muhasebeyi korur; sağlayıcı sınırı
delinip gerçek maliyet tavanı aşarsa muhasebe tavanı yakar ama GERÇEK
harcama kaybolmamalıdır. `observed_actual_usd` bu denetim alanıdır ve
cap hesabına GİRMEZ.

İdempotent (repo kuralı): kolon/CHECK varlık-kontrollü eklenir.

Revision ID: 20260730_003
Revises: 20260730_002
Create Date: 2026-07-30
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260730_003"
down_revision: Union[str, None] = "20260730_002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "ai_cost_reservations"
CHECK = "ck_ai_cost_reservation_observed_nonneg"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if TABLE not in set(inspector.get_table_names()):
        return
    columns = {c["name"] for c in inspector.get_columns(TABLE)}
    if "observed_actual_usd" not in columns:
        op.add_column(TABLE, sa.Column("observed_actual_usd",
                                       sa.Numeric(12, 6), nullable=True))
    checks = {c["name"] for c in sa.inspect(bind).get_check_constraints(TABLE)}
    if CHECK not in checks:
        op.create_check_constraint(
            CHECK, TABLE,
            "observed_actual_usd IS NULL OR observed_actual_usd >= 0")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if TABLE not in set(inspector.get_table_names()):
        return
    if CHECK in {c["name"] for c in inspector.get_check_constraints(TABLE)}:
        op.drop_constraint(CHECK, TABLE, type_="check")
    if "observed_actual_usd" in {c["name"] for c
                                 in sa.inspect(bind).get_columns(TABLE)}:
        op.drop_column(TABLE, "observed_actual_usd")
