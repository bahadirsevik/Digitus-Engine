"""
Migration chain smoke test (P0/C1, plan2 §P0 madde 2).

Guarantees that `alembic upgrade head` runs cleanly from an empty schema
all the way through every migration revision present in `migrations/versions/`.

Why this exists:
- The production startup command currently uses `init_db + alembic stamp head`,
  which bypasses real migrations. If any migration in the chain is broken,
  it is invisible until we move to real `alembic upgrade head` (plan2 §P3).
- This test runs `upgrade head` against a fresh DB (the test DB, which the
  container entrypoint already migrated; here we additionally drop + re-upgrade
  to be self-contained).
- If chain is broken, this test fails first and P0 cannot proceed
  (plan2 §P0 madde 2: "Eğer chain kırıksa P0'ın hiçbir maddesi ilerlemez").
"""
from __future__ import annotations

import subprocess

import pytest
from sqlalchemy import inspect, text


# Tables that must exist after a full migration to HEAD.
# Source: every `__tablename__` in app/database/models.py.
EXPECTED_TABLES = {
    "keywords",
    "scoring_runs",
    "keyword_scores",
    "channel_candidates",
    "intent_analysis",
    "pre_filter_results",
    "channel_pools",
    "content_outputs",
    "compliance_checks",
    "seo_geo_contents",
    "seo_compliance_results",
    "geo_compliance_results",
    "ad_groups",
    "ad_headlines",
    "ad_descriptions",
    "negative_keywords",
    "social_categories",
    "social_ideas",
    "social_contents",
    "task_results",
    "brand_profiles",
    "keyword_relevance",
    "workspace_keywords",
    # Alembic's own bookkeeping table:
    "alembic_version",
}


@pytest.fixture(scope="module")
def fresh_schema(db_engine):
    """Drop all tables and re-run `alembic upgrade head` from scratch.

    Uses the same DATABASE_URL as the rest of the suite (already pointing at
    the test container's Postgres). After this fixture runs we restore the
    schema via the same upgrade, so subsequent tests see a migrated DB.
    """
    # Drop everything (including the alembic_version bookkeeping table).
    with db_engine.connect() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        conn.commit()

    # Run real Alembic upgrade against the empty schema.
    result = subprocess.run(
        ["alembic", "upgrade", "head"],
        capture_output=True,
        text=True,
        cwd="/app",
    )
    assert result.returncode == 0, (
        f"alembic upgrade head failed:\n"
        f"STDOUT:\n{result.stdout}\n"
        f"STDERR:\n{result.stderr}"
    )
    yield


def test_alembic_upgrade_head_from_empty_db(fresh_schema, db_engine):
    """Every expected table must exist after upgrade head on an empty DB."""
    inspector = inspect(db_engine)
    actual_tables = set(inspector.get_table_names())
    missing = EXPECTED_TABLES - actual_tables
    assert not missing, (
        f"Migration chain ran but the following tables are missing: {sorted(missing)}. "
        f"Actual tables: {sorted(actual_tables)}"
    )


def test_alembic_version_is_head(fresh_schema, db_engine):
    """After upgrade, alembic_version table must contain exactly one row."""
    with db_engine.connect() as conn:
        rows = conn.execute(text("SELECT version_num FROM alembic_version")).fetchall()
    assert len(rows) == 1, f"Expected exactly one alembic_version row, got {rows}"
    assert rows[0][0], "alembic_version row has empty version_num"


def test_workspace_keyword_uniqueness_present(fresh_schema, db_engine):
    """workspace_keywords must enforce uniqueness so the same keyword cannot
    be linked to a workspace twice via the same FK pair.

    Note: the model currently declares UNIQUE(brand_profile_id, keyword_id);
    the archived migration 20260509_001 originally also included data_source
    in the uniqueness key. Today's model is the source of truth — if business
    rules later require (brand_profile_id, keyword_id, data_source), update
    the model and let this test guide the migration.
    """
    inspector = inspect(db_engine)
    unique_constraints = inspector.get_unique_constraints("workspace_keywords")
    target_subset = {"brand_profile_id", "keyword_id"}
    found = any(
        target_subset.issubset(set(uc.get("column_names", [])))
        for uc in unique_constraints
    )
    assert found, (
        f"Expected uniqueness over at least (brand_profile_id, keyword_id) "
        f"on workspace_keywords. unique_constraints={unique_constraints}"
    )


def test_workspace_keyword_data_source_check_constraint(fresh_schema, db_engine):
    """data_source allowed-value CHECK constraint must exist."""
    from sqlalchemy import text

    with db_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT conname FROM pg_constraint "
                "WHERE conrelid = 'workspace_keywords'::regclass AND contype = 'c'"
            )
        ).fetchall()
    names = {r[0] for r in rows}
    assert "ck_workspace_keywords_data_source" in names, (
        f"Missing CHECK constraint on workspace_keywords.data_source. "
        f"Found: {names}"
    )
