"""channel_assignment_attempts: `publishing` dispatch durumu

Codex 27. tur #3: ertelenmis parent atamasi once `publishing` olur, broker
yayimi basarili olunca `sent` yazilir. Process tam arada olurse parent
hicbir zaman kuyruga girmez ve CAS yuzunden bir daha denenemezdi; lease
dolunca reconciler AYNI task ID ile yeniden yayimlar.

Repo kurali: baseline squash canli `create_all` kullandigi icin additive
migration'lar IDEMPOTENT yazilir (kisit varlik kontrolu).

Revision ID: 20260731_002
Revises: 20260731_001
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260731_002"
down_revision: Union[str, None] = "20260731_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "channel_assignment_attempts"
CONSTRAINT = "ck_assignment_attempt_dispatch_state"
NEW_STATES = "('pending', 'publishing', 'sent', 'started', 'finished')"
OLD_STATES = "('pending', 'sent', 'started', 'finished')"


def _has_table(bind) -> bool:
    return TABLE in sa.inspect(bind).get_table_names()


def _has_constraint(bind) -> bool:
    row = bind.execute(sa.text(
        "SELECT 1 FROM pg_constraint WHERE conname = :name"),
        {"name": CONSTRAINT}).first()
    return row is not None


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind):
        return
    if _has_constraint(bind):
        op.drop_constraint(CONSTRAINT, TABLE, type_="check")
    op.create_check_constraint(
        CONSTRAINT, TABLE, f"assignment_dispatch_state IN {NEW_STATES}")


def downgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind):
        return
    # Geri alirken yeni durum kalmamali (aksi halde kisit kurulamaz)
    bind.execute(sa.text(
        f"UPDATE {TABLE} SET assignment_dispatch_state = 'pending' "
        f"WHERE assignment_dispatch_state = 'publishing'"))
    if _has_constraint(bind):
        op.drop_constraint(CONSTRAINT, TABLE, type_="check")
    op.create_check_constraint(
        CONSTRAINT, TABLE, f"assignment_dispatch_state IN {OLD_STATES}")
