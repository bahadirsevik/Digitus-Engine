"""Dry-run checks for workspace Phase B migration.

Prints duplicate and FK-impact counts before running
20260508_001_workspace_phase_b_backfill.py in production.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402


FK_TABLES = [
    ("keyword_scores", "keyword_id"),
    ("channel_candidates", "keyword_id"),
    ("intent_analysis", "keyword_id"),
    ("pre_filter_results", "keyword_id"),
    ("channel_pools", "keyword_id"),
    ("keyword_relevance", "keyword_id"),
    ("workspace_keywords", "keyword_id"),
    ("content_outputs", "keyword_id"),
    ("seo_geo_contents", "keyword_id"),
    ("social_ideas", "keyword_id"),
]


def _database_url() -> str:
    return os.getenv("DATABASE_URL") or settings.database_url


def main() -> int:
    engine = create_engine(_database_url())
    with engine.connect() as conn:
        duplicate_summary = conn.execute(text("""
            SELECT COUNT(*) AS duplicate_groups, COALESCE(SUM(cnt - 1), 0) AS rows_to_merge
            FROM (
                SELECT normalized_keyword, COUNT(*) AS cnt
                FROM keywords
                WHERE normalized_keyword IS NOT NULL
                GROUP BY normalized_keyword
                HAVING COUNT(*) > 1
            ) sub
        """)).mappings().one()

        print("Workspace Phase B dry-run")
        print("=========================")
        print(f"duplicate_groups: {duplicate_summary['duplicate_groups']}")
        print(f"rows_to_merge:    {duplicate_summary['rows_to_merge']}")
        print()

        print("FK rows touching duplicate keywords:")
        for table_name, column_name in FK_TABLES:
            count = conn.execute(text(f"""
                SELECT COUNT(*)
                FROM {table_name}
                WHERE {column_name} IN (
                    SELECT id
                    FROM (
                        SELECT id,
                               ROW_NUMBER() OVER (
                                   PARTITION BY normalized_keyword
                                   ORDER BY created_at ASC NULLS LAST, id ASC
                               ) AS rn
                        FROM keywords
                        WHERE normalized_keyword IS NOT NULL
                    ) ranked
                    WHERE rn > 1
                )
            """)).scalar_one()
            print(f"- {table_name}.{column_name}: {count}")

        ad_group_json = conn.execute(text("""
            SELECT COUNT(*) FROM ad_groups WHERE target_keyword_ids IS NOT NULL
        """)).scalar_one()
        social_json = conn.execute(text("""
            SELECT COUNT(*) FROM social_categories WHERE suggested_keyword_ids IS NOT NULL
        """)).scalar_one()
        system_defaults = conn.execute(text("""
            SELECT COUNT(*) FROM brand_profiles WHERE is_system_default = TRUE
        """)).scalar_one()

        print()
        print(f"ad_groups.target_keyword_ids rows:          {ad_group_json}")
        print(f"social_categories.suggested_keyword_ids rows: {social_json}")
        print(f"system_default_workspace_count:             {system_defaults}")

        if system_defaults > 1:
            print("ERROR: more than one system default workspace exists; resolve manually before Phase B.")
            return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
