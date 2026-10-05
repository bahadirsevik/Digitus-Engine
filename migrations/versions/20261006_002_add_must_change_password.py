"""users.must_change_password — ilk giriste parola degistirme zorunlulugu.

Gecici parola ile acilan kullanicilar giris yaptiktan sonra parolalarini
degistirmeden hicbir veri ucuna erisemez (bkz. app/core/login.py).

Tek kolon eklenir, mevcut veri etkilenmez. server_default='false' sayesinde
var olan satirlar (varsa) zorunluluk ALMAZ.

Revision ID: 20261006_002
Revises: 20261006_001
Create Date: 2026-10-06
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261006_002"
down_revision: Union[str, None] = "20261006_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "users"
COLUMN = "must_change_password"


def _columns(inspector, table: str) -> set[str]:
    return {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if TABLE not in set(inspector.get_table_names()):
        return
    if COLUMN in _columns(inspector, TABLE):
        return

    op.add_column(
        TABLE,
        sa.Column(
            COLUMN,
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if TABLE not in set(inspector.get_table_names()):
        return
    if COLUMN not in _columns(inspector, TABLE):
        return

    op.drop_column(TABLE, COLUMN)
