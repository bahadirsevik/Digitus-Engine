"""fix_turkish_google_ads_language_id

Revision ID: 20260509_002
Revises: 20260509_001
Create Date: 2026-05-09
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260509_002"
down_revision: Union[str, None] = "20260509_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(sa.text("""
        UPDATE brand_profiles
        SET default_language_id = '1037'
        WHERE default_language_id = '1055'
    """))
    op.execute(sa.text("""
        UPDATE workspace_keywords
        SET language_id = '1037'
        WHERE language_id = '1055'
    """))


def downgrade() -> None:
    op.execute(sa.text("""
        UPDATE brand_profiles
        SET default_language_id = '1055'
        WHERE default_language_id = '1037'
    """))
    op.execute(sa.text("""
        UPDATE workspace_keywords
        SET language_id = '1055'
        WHERE language_id = '1037'
    """))
