"""workspace_phase_b_backfill

Revision ID: 20260508_001
Revises: f08125d66a33
Create Date: 2026-05-08

Phase B data migration:
- normalize existing keywords
- merge duplicate normalized keywords
- create a system default workspace when needed
- backfill workspace_keywords from historical scoring data and active keywords
"""
from __future__ import annotations

import re
import unicodedata
from typing import Iterable, Sequence, Union

from alembic import op
from sqlalchemy import text


revision: str = "20260508_001"
down_revision: Union[str, None] = "f08125d66a33"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TURKISH_MAP = str.maketrans({
    "ç": "c", "Ç": "c", "ğ": "g", "Ğ": "g",
    "ı": "i", "I": "i", "İ": "i", "i": "i",
    "ö": "o", "Ö": "o", "ş": "s", "Ş": "s",
    "ü": "u", "Ü": "u",
})


def _normalize_keyword_v1(text_value: str | None) -> str:
    if not text_value:
        return ""
    value = text_value.translate(_TURKISH_MAP)
    value = value.lower().strip()
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"[^a-z0-9\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def _rows(conn, sql: str, params: dict | None = None):
    return conn.execute(text(sql), params or {}).fetchall()


def _delete_conflicting_fk_rows(
    conn,
    table_name: str,
    keyword_col: str,
    canonical_id: int,
    dup_id: int,
    scope_cols: Iterable[str],
) -> None:
    scope_match = " AND ".join(f"src.{col} = canon.{col}" for col in scope_cols)
    if scope_match:
        scope_match = " AND " + scope_match

    conn.execute(text(f"""
        DELETE FROM {table_name} AS src
        USING {table_name} AS canon
        WHERE src.{keyword_col} = :dup_id
          AND canon.{keyword_col} = :canonical_id
          {scope_match}
    """), {"dup_id": dup_id, "canonical_id": canonical_id})


def _update_keyword_fk(conn, table_name: str, keyword_col: str, canonical_id: int, dup_id: int) -> None:
    conn.execute(text(f"""
        UPDATE {table_name}
        SET {keyword_col} = :canonical_id
        WHERE {keyword_col} = :dup_id
    """), {"canonical_id": canonical_id, "dup_id": dup_id})


def _remap_json_id_arrays(conn, canonical_id: int, dup_id: int) -> None:
    conn.execute(text("""
        UPDATE ad_groups
        SET target_keyword_ids = (
            SELECT COALESCE(jsonb_agg(DISTINCT CASE
                WHEN value ~ '^[0-9]+$' AND value::int = :dup_id THEN to_jsonb(:canonical_id::int)
                ELSE to_jsonb(value::int)
            END), '[]'::jsonb)::json
            FROM jsonb_array_elements_text(COALESCE(target_keyword_ids::jsonb, '[]'::jsonb)) AS value
            WHERE value ~ '^[0-9]+$'
        )
        WHERE target_keyword_ids IS NOT NULL
    """), {"canonical_id": canonical_id, "dup_id": dup_id})

    conn.execute(text("""
        UPDATE social_categories
        SET suggested_keyword_ids = (
            SELECT COALESCE(jsonb_agg(DISTINCT CASE
                WHEN value ~ '^[0-9]+$' AND value::int = :dup_id THEN to_jsonb(:canonical_id::int)
                ELSE to_jsonb(value::int)
            END), '[]'::jsonb)::json
            FROM jsonb_array_elements_text(COALESCE(suggested_keyword_ids::jsonb, '[]'::jsonb)) AS value
            WHERE value ~ '^[0-9]+$'
        )
        WHERE suggested_keyword_ids IS NOT NULL
    """), {"canonical_id": canonical_id, "dup_id": dup_id})


def _merge_keyword(conn, canonical_id: int, dup_id: int) -> None:
    scoped_tables = [
        ("keyword_scores", "keyword_id", ("scoring_run_id",)),
        ("channel_candidates", "keyword_id", ("scoring_run_id", "channel")),
        ("intent_analysis", "keyword_id", ("scoring_run_id", "channel")),
        ("pre_filter_results", "keyword_id", ("scoring_run_id", "channel")),
        ("channel_pools", "keyword_id", ("scoring_run_id", "channel")),
        ("keyword_relevance", "keyword_id", ("scoring_run_id",)),
        ("workspace_keywords", "keyword_id", ("brand_profile_id",)),
    ]
    simple_tables = [
        ("content_outputs", "keyword_id"),
        ("seo_geo_contents", "keyword_id"),
        ("social_ideas", "keyword_id"),
    ]

    for table_name, keyword_col, scope_cols in scoped_tables:
        _delete_conflicting_fk_rows(conn, table_name, keyword_col, canonical_id, dup_id, scope_cols)
        _update_keyword_fk(conn, table_name, keyword_col, canonical_id, dup_id)

    for table_name, keyword_col in simple_tables:
        _update_keyword_fk(conn, table_name, keyword_col, canonical_id, dup_id)

    _remap_json_id_arrays(conn, canonical_id, dup_id)

    conn.execute(text("DELETE FROM keywords WHERE id = :dup_id"), {"dup_id": dup_id})


def upgrade() -> None:
    conn = op.get_bind()

    # 1. Create exactly one system default workspace if none exists.
    existing_default = conn.execute(text("""
        SELECT id FROM brand_profiles WHERE is_system_default = TRUE LIMIT 1
    """)).scalar()
    if existing_default is None:
        default_ws_id = conn.execute(text("""
            INSERT INTO brand_profiles (
                name, company_url, status, profile_data,
                default_geo_target_id, default_language_id,
                is_system_default, created_at, updated_at
            )
            VALUES (
                'Default Workspace (Migration)',
                'https://default.local',
                'confirmed',
                '{}'::json,
                '2792',
                '1055',
                TRUE,
                now(),
                now()
            )
            RETURNING id
        """)).scalar_one()
    else:
        default_ws_id = existing_default

    # 2. Fill normalized_keyword using the runtime-compatible v1 normalizer.
    for kw_id, keyword in _rows(conn, "SELECT id, keyword FROM keywords"):
        conn.execute(
            text("UPDATE keywords SET normalized_keyword = :normalized WHERE id = :id"),
            {"normalized": _normalize_keyword_v1(keyword), "id": kw_id},
        )

    # 3. Merge duplicate normalized keywords, oldest row wins.
    duplicate_groups = _rows(conn, """
        SELECT normalized_keyword, ARRAY_AGG(id ORDER BY created_at ASC, id ASC) AS ids
        FROM keywords
        WHERE normalized_keyword IS NOT NULL
        GROUP BY normalized_keyword
        HAVING COUNT(*) > 1
    """)
    for _, ids in duplicate_groups:
        canonical_id = ids[0]
        for dup_id in ids[1:]:
            _merge_keyword(conn, canonical_id, dup_id)

    # 4. Ensure all remaining keywords have normalized text after merges.
    conn.execute(text("""
        UPDATE keywords
        SET normalized_keyword = CONCAT('keyword-', id::text)
        WHERE normalized_keyword IS NULL OR normalized_keyword = ''
    """))

    # 5. Backfill WorkspaceKeyword from historical scoring runs.
    conn.execute(text("""
        INSERT INTO workspace_keywords (
            brand_profile_id, keyword_id,
            monthly_volume, trend_3m, trend_12m, competition_score,
            data_source, sector, target_market, imported_at
        )
        SELECT DISTINCT ON (sr.brand_profile_id, ks.keyword_id)
            sr.brand_profile_id,
            ks.keyword_id,
            COALESCE((ks.metrics_snapshot->>'monthly_volume')::int, k.monthly_volume, 0),
            COALESCE((ks.metrics_snapshot->>'trend_3m')::numeric, k.trend_3m, 0),
            COALESCE((ks.metrics_snapshot->>'trend_12m')::numeric, k.trend_12m, 0),
            COALESCE((ks.metrics_snapshot->>'competition_score')::numeric, k.competition_score, 0.50),
            COALESCE(k.data_source, 'csv'),
            k.sector,
            k.target_market,
            COALESCE(k.created_at, now())
        FROM keyword_scores ks
        JOIN keywords k ON k.id = ks.keyword_id
        JOIN scoring_runs sr ON sr.id = ks.scoring_run_id
        WHERE sr.brand_profile_id IS NOT NULL
        ORDER BY sr.brand_profile_id, ks.keyword_id, ks.calculated_at DESC NULLS LAST
        ON CONFLICT (brand_profile_id, keyword_id) DO NOTHING
    """))

    # 6. Add every active legacy keyword to the default workspace.
    conn.execute(text("""
        INSERT INTO workspace_keywords (
            brand_profile_id, keyword_id,
            monthly_volume, trend_3m, trend_12m, competition_score,
            data_source, sector, target_market, imported_at
        )
        SELECT
            :default_ws_id,
            k.id,
            COALESCE(k.monthly_volume, 0),
            COALESCE(k.trend_3m, 0),
            COALESCE(k.trend_12m, 0),
            COALESCE(k.competition_score, 0.50),
            COALESCE(k.data_source, 'csv'),
            k.sector,
            k.target_market,
            COALESCE(k.created_at, now())
        FROM keywords k
        WHERE k.is_active = TRUE
        ON CONFLICT (brand_profile_id, keyword_id) DO NOTHING
    """), {"default_ws_id": default_ws_id})


def downgrade() -> None:
    # Data merge/backfill is intentionally not reversible.
    pass
