"""corpus_screening_jobs: execution lease (tek sahipli claim)

Codex 10. tur #3: `task_id` bir worker LEASE'i degildir — ayni Celery
task'inin mukerrer teslimi iki worker'a da claim kazandirabiliyordu.
Lease alanlari ile `pending -> running` gecisinin TEK kazanani olur;
devralma yalnizca lease suresi dolunca (onceki worker hard time limit ile
olmus sayilir) mumkundur.

Repo kurali: baseline squash canli `create_all` kullandigi icin additive
migration'lar IDEMPOTENT yazilir (kolon varlik kontrolu).

Revision ID: 20260731_001
Revises: 20260730_003
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260731_001"
down_revision: Union[str, None] = "20260730_003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "corpus_screening_jobs"
COLUMNS = {
    "execution_lease_id": sa.Column("execution_lease_id", sa.String(64),
                                    nullable=True),
    "execution_lease_expires_at": sa.Column(
        "execution_lease_expires_at", sa.DateTime(timezone=True),
        nullable=True),
    "execution_attempt": sa.Column("execution_attempt", sa.Integer(),
                                   nullable=False, server_default="0"),
}


def _existing_columns(bind) -> set:
    inspector = sa.inspect(bind)
    if TABLE not in inspector.get_table_names():
        return set()
    return {col["name"] for col in inspector.get_columns(TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    existing = _existing_columns(bind)
    if not existing:            # tablo yoksa (kismi kurulum) dokunma
        return
    for name, column in COLUMNS.items():
        if name not in existing:
            op.add_column(TABLE, column)


def downgrade() -> None:
    bind = op.get_bind()
    existing = _existing_columns(bind)
    for name in COLUMNS:
        if name in existing:
            op.drop_column(TABLE, name)
