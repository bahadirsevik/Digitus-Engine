"""Merge multiple Alembic heads into single head

Revision ID: 20260225_001_merge
Revises: 20260204_001_add_social_media_tables, 20260205_001_add_task_results
Create Date: 2026-02-25
"""

# revision identifiers
revision = '20260225_merge'
down_revision = ('20260204_001_add_social_media_tables', '20260205_001_add_task_results')
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Merge heads — no schema changes needed."""
    pass


def downgrade() -> None:
    pass
