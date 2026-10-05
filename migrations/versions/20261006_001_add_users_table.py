"""Add users table (e-posta + parola ile giris).

Tek tablo eklenir: users. MEVCUT HICBIR TABLO DEGISMEZ, hicbir tabloya
kolon eklenmez. Bu sayede geri donus (downgrade) veri kaybi yaratmaz:
yalniz bu is kapsaminda olusturulan tablo dusurulur.

Oturumlar Redis'te tutulur, bu yuzden oturum tablosu YOKTUR.

Revision ID: 20261006_001
Revises: 20260923_001
Create Date: 2026-10-06
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261006_001"
down_revision: Union[str, None] = "20260923_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "users"


def _tables(inspector) -> set[str]:
    return set(inspector.get_table_names())


def _indexes(inspector, table: str) -> set[str]:
    return {i["name"] for i in inspector.get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if TABLE in _tables(inspector):
        # Idempotent: tablo zaten varsa dokunma (repo konvansiyonu).
        return

    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), primary_key=True),
        # 320 = RFC'nin pratik e-posta ust siniri (64 local + @ + 255 domain).
        # Degerler DAIMA kucuk harfe normalize edilerek yazilir; UNIQUE
        # kisitinin buyuk/kucuk harf yuzunden atlatilmasi boylece onlenir.
        sa.Column("email", sa.String(length=320), nullable=False),
        # argon2id hash'i ~97 karakter; 255 ileride parametre artsa da yeter.
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("full_name", sa.String(length=200), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        # Soft delete — repo genelindeki desen (bkz. brand_profiles.deleted_at).
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )

    inspector = sa.inspect(bind)
    if "idx_users_email" not in _indexes(inspector, TABLE):
        op.create_index("idx_users_email", TABLE, ["email"])


def downgrade() -> None:
    """
    Tabloyu dusurur. Yalniz bu migration'in olusturdugu users tablosu
    silinir; baska hicbir veri etkilenmez.
    """
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if TABLE not in _tables(inspector):
        return

    if "idx_users_email" in _indexes(inspector, TABLE):
        op.drop_index("idx_users_email", table_name=TABLE)

    op.drop_table(TABLE)
