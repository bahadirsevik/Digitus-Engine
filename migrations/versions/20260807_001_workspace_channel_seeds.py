"""workspace_channel_seeds: yapisal kanal seed kaydi (plan9 Asama A).

Onboarding'de onaylanan 10 keyword bugune kadar `suggested_keywords` duz
metin listesinde tutuluyordu ve kanal tercihi tasiyamiyordu. Olculen sonuc
(makro F1 0.590 -> 0.771) bu tercihlerin ayri ve yapisal veri olarak
saklanmasini gerektiriyor.

IDEMPOTENT: baseline squash canli `Base.metadata.create_all` kullandigi
icin yeni kurulumda tablo zaten olusmus olabilir (repo kalibi).

Revision ID: 20260807_001
Revises: 20260805_001
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260807_001"
down_revision: Union[str, None] = "20260805_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "workspace_channel_seeds"
CONSTRAINT = "ck_workspace_channel_seed_exclusive"
# IKI YONLU: her satir ya en az bir kanal ya da not_suitable tasimali.
EXCLUSIVE_SQL = (
    "(not_suitable AND json_array_length(channels) = 0) OR "
    "(NOT not_suitable AND json_array_length(channels) > 0)"
)


def _has_table(bind) -> bool:
    return TABLE in sa.inspect(bind).get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind):
        return
    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("brand_profile_id", sa.Integer(), nullable=False),
        sa.Column("keyword_id", sa.Integer(), nullable=True),
        sa.Column("keyword", sa.String(length=500), nullable=False),
        sa.Column("canonical_keyword", sa.String(length=500), nullable=False),
        sa.Column("channels", sa.JSON(), nullable=False),
        sa.Column("not_suitable", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
        sa.Column("replaced_original_keyword", sa.String(length=500),
                  nullable=True),
        sa.Column("labelled_by", sa.String(length=120), nullable=True),
        sa.Column("labelled_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=True),
        sa.Column("source", sa.String(length=50), nullable=False,
                  server_default="patron_onboarding_channel_seed"),
        sa.ForeignKeyConstraint(["brand_profile_id"], ["brand_profiles.id"],
                                ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["keyword_id"], ["keywords.id"],
                                ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("brand_profile_id", "canonical_keyword",
                            name="uq_workspace_channel_seed"),
        # `json` tipinde esitlik operatoru yok -> uzunluk fonksiyonu
        sa.CheckConstraint(EXCLUSIVE_SQL, name=CONSTRAINT),
    )
    op.create_index("idx_workspace_channel_seed_ws", TABLE,
                    ["brand_profile_id"])
    op.create_index(op.f("ix_workspace_channel_seeds_id"), TABLE, ["id"])


def downgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind):
        return
    op.drop_index(op.f("ix_workspace_channel_seeds_id"), table_name=TABLE)
    op.drop_index("idx_workspace_channel_seed_ws", table_name=TABLE)
    op.drop_table(TABLE)
