"""workspace_phase_c_constraints

Revision ID: 20260508_002
Revises: 20260508_001
Create Date: 2026-05-08

Phase C constraint hardening after Phase B backfill/merge.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260508_002"
down_revision: Union[str, None] = "20260508_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _assert_clean(conn) -> None:
    null_names = conn.execute(sa.text("""
        SELECT COUNT(*) FROM brand_profiles WHERE name IS NULL OR btrim(name) = ''
    """)).scalar_one()
    if null_names:
        conn.execute(sa.text("""
            UPDATE brand_profiles
            SET name = CONCAT('Workspace ', id::text)
            WHERE name IS NULL OR btrim(name) = ''
        """))

    null_normalized = conn.execute(sa.text("""
        SELECT COUNT(*) FROM keywords WHERE normalized_keyword IS NULL OR btrim(normalized_keyword) = ''
    """)).scalar_one()
    if null_normalized:
        raise RuntimeError("Phase C blocked: keywords.normalized_keyword still has NULL/empty values")

    duplicate_normalized = conn.execute(sa.text("""
        SELECT COUNT(*)
        FROM (
            SELECT normalized_keyword
            FROM keywords
            GROUP BY normalized_keyword
            HAVING COUNT(*) > 1
        ) dup
    """)).scalar_one()
    if duplicate_normalized:
        raise RuntimeError("Phase C blocked: duplicate keywords.normalized_keyword values remain")

    system_default_count = conn.execute(sa.text("""
        SELECT COUNT(*) FROM brand_profiles WHERE is_system_default = TRUE
    """)).scalar_one()
    if system_default_count > 1:
        raise RuntimeError("Phase C blocked: more than one system default workspace exists")


def upgrade() -> None:
    conn = op.get_bind()
    _assert_clean(conn)

    op.alter_column("brand_profiles", "name", existing_type=sa.String(length=200), nullable=False)
    op.alter_column("keywords", "normalized_keyword", existing_type=sa.String(length=500), nullable=False)

    op.drop_index("idx_keywords_normalized", table_name="keywords")
    op.create_index("uq_keywords_normalized", "keywords", ["normalized_keyword"], unique=True)
    op.create_index(
        "uq_brand_profiles_system_default",
        "brand_profiles",
        ["is_system_default"],
        unique=True,
        postgresql_where=sa.text("is_system_default = TRUE"),
    )


def downgrade() -> None:
    op.drop_index("uq_brand_profiles_system_default", table_name="brand_profiles")
    op.drop_index("uq_keywords_normalized", table_name="keywords")
    op.create_index("idx_keywords_normalized", "keywords", ["normalized_keyword"], unique=False)
    op.alter_column("keywords", "normalized_keyword", existing_type=sa.String(length=500), nullable=True)
    op.alter_column("brand_profiles", "name", existing_type=sa.String(length=200), nullable=True)
