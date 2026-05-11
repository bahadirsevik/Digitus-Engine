"""workspace_keyword_source_constraint

Revision ID: 20260509_001
Revises: 20260508_002
Create Date: 2026-05-09
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260509_001"
down_revision: Union[str, None] = "20260508_002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("""
        UPDATE workspace_keywords
        SET data_source = 'csv'
        WHERE data_source NOT IN ('csv', 'google_ads_api', 'url_seed', 'manual')
    """))
    op.create_check_constraint(
        "ck_workspace_keywords_data_source",
        "workspace_keywords",
        "data_source IN ('csv', 'google_ads_api', 'url_seed', 'manual')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_workspace_keywords_data_source",
        "workspace_keywords",
        type_="check",
    )
