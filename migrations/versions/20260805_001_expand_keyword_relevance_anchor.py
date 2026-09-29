"""keyword_relevance.matched_anchor alanini TEXT'e genislet.

Zengin profil/strateji anchor'lari 500 karakteri asabildigi icin relevance
hesabi tamamlandiktan sonra VARCHAR(500) yaziminda run fail oluyordu.

Revision ID: 20260805_001
Revises: 20260731_002
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260805_001"
down_revision: Union[str, None] = "20260731_002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "keyword_relevance"
COLUMN = "matched_anchor"


def _column_type(bind):
    inspector = sa.inspect(bind)
    if TABLE not in inspector.get_table_names():
        return None
    for column in inspector.get_columns(TABLE):
        if column["name"] == COLUMN:
            return column["type"]
    return None


def upgrade() -> None:
    bind = op.get_bind()
    current = _column_type(bind)
    if current is None or isinstance(current, sa.Text):
        return
    op.alter_column(
        TABLE,
        COLUMN,
        existing_type=current,
        type_=sa.Text(),
        existing_nullable=True,
    )


def downgrade() -> None:
    bind = op.get_bind()
    current = _column_type(bind)
    if current is None or (
        isinstance(current, sa.String) and current.length == 500
    ):
        return
    # Downgrade'in tip daraltmasinda mevcut uzun veriyi acikca kirp.
    bind.execute(sa.text(
        f"UPDATE {TABLE} SET {COLUMN} = LEFT({COLUMN}, 500) "
        f"WHERE LENGTH({COLUMN}) > 500"
    ))
    op.alter_column(
        TABLE,
        COLUMN,
        existing_type=current,
        type_=sa.String(length=500),
        existing_nullable=True,
    )
