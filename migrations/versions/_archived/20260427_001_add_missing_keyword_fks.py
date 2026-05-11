"""Add missing keyword foreign keys

Revision ID: 20260427_001
Revises: 20260424_001
Create Date: 2026-04-27
"""
from alembic import op


revision = "20260427_001"
down_revision = "20260424_001"
branch_labels = None
depends_on = None


KEYWORD_FKS = [
    (
        "fk_channel_pools_keyword_id",
        "channel_pools",
        "keyword_id",
    ),
    (
        "fk_intent_analysis_keyword_id",
        "intent_analysis",
        "keyword_id",
    ),
    (
        "fk_channel_candidates_keyword_id",
        "channel_candidates",
        "keyword_id",
    ),
    (
        "fk_keyword_scores_keyword_id",
        "keyword_scores",
        "keyword_id",
    ),
]


def upgrade() -> None:
    for constraint_name, table_name, column_name in KEYWORD_FKS:
        op.execute(
            f"""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1
                    FROM pg_constraint
                    WHERE conname = '{constraint_name}'
                      AND conrelid = '{table_name}'::regclass
                ) THEN
                    ALTER TABLE {table_name}
                    ADD CONSTRAINT {constraint_name}
                    FOREIGN KEY ({column_name})
                    REFERENCES keywords (id)
                    ON DELETE CASCADE;
                END IF;
            END
            $$;
            """
        )


def downgrade() -> None:
    for constraint_name, table_name, _ in reversed(KEYWORD_FKS):
        op.execute(
            f"ALTER TABLE {table_name} DROP CONSTRAINT IF EXISTS {constraint_name}"
        )
