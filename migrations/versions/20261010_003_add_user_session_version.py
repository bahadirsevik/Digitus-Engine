"""users.session_version — parola degisiminde eski oturumlari DB'den iptal etme.

Oturum yuku (Redis) giris aninda kullanicinin `session_version` degerini
tasir; kapi her istekte DB'deki degerle karsilastirir. Parola degisimi bu
sayaci artirir, boylece Redis'teki eski kayitlar silinemese bile reddedilir.

Mevcut satirlar server_default='0' ile 0 surumunde baslar; surum alani
olmayan eski oturum yukleri de 0 kabul edilir (gecis kesintisiz).

IDEMPOTENT: baseline squash canli `Base.metadata.create_all` kullandigi icin
yeni kurulumda kolon zaten vardir; kolon-varlik kontrolu yapilir.

Revision ID: 20261010_003
Revises: 20261010_002
Create Date: 2026-10-10
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261010_003"
down_revision: Union[str, None] = "20261010_002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "users"
_COLUMN = "session_version"


def _users_columns() -> set:
    inspector = sa.inspect(op.get_bind())
    if _TABLE not in set(inspector.get_table_names()):
        return set()
    return {column["name"] for column in inspector.get_columns(_TABLE)}


def upgrade() -> None:
    columns = _users_columns()
    if not columns or _COLUMN in columns:
        return
    op.add_column(
        _TABLE,
        sa.Column(
            _COLUMN,
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )


def downgrade() -> None:
    if _COLUMN in _users_columns():
        op.drop_column(_TABLE, _COLUMN)
