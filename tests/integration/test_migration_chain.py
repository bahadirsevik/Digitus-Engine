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
from sqlalchemy import Text, inspect, text


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
    # Social Brief akisi (20260923_001)
    "social_briefs",
    "social_brief_keywords",
    "social_brief_targets",
    "social_generation_attempts",
    "task_results",
    "brand_profiles",
    "keyword_relevance",
    "workspace_keywords",
    # Corpus screening ureti model (20260730_001)
    "channel_assignment_attempts",
    "corpus_screening_jobs",
    "corpus_screening_decisions",
    "corpus_screening_batch_checkpoints",
    "corpus_candidate_selections",
    "ai_cost_reservations",
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


def test_keyword_relevance_anchor_is_text(fresh_schema, db_engine):
    """Profil anchor'i 500 karakteri asinca relevance yazimi dusmemeli."""
    columns = {
        column["name"]: column
        for column in inspect(db_engine).get_columns("keyword_relevance")
    }
    assert isinstance(columns["matched_anchor"]["type"], Text)


def test_workspace_keyword_uniqueness_absent(fresh_schema, db_engine):
    """workspace_keywords must NOT have a unique constraint on (brand_profile_id, keyword_id).

    Business rule (20260518_001): aynı keyword farklı metrik snapshot'larıyla
    ayrı WorkspaceKeyword satırı olarak kaydedilebilir; uq_workspace_keyword kaldırıldı.
    """
    inspector = inspect(db_engine)
    unique_constraints = inspector.get_unique_constraints("workspace_keywords")
    target_subset = {"brand_profile_id", "keyword_id"}
    found = any(
        target_subset.issubset(set(uc.get("column_names", [])))
        for uc in unique_constraints
    )
    assert not found, (
        f"uq_workspace_keyword must be absent (dropped in 20260518_001). "
        f"unique_constraints={unique_constraints}"
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


def test_corpus_screening_migration_is_idempotent(fresh_schema, db_engine):
    """Idempotent kalip (repo kurali): baseline squash canli create_all
    kullandigi icin migration'in ikinci kez degerlendirilmesi YENI nesne
    uretmemeli ve patlamamalidir."""
    import importlib.util
    from pathlib import Path

    path = (Path(__file__).resolve().parents[2] / "migrations" / "versions"
            / "20260730_001_add_corpus_screening.py")
    spec = importlib.util.spec_from_file_location("mig_20260730_001", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    inspector = inspect(db_engine)
    before_tables = set(inspector.get_table_names())
    before_indexes = {
        t: {i["name"] for i in inspector.get_indexes(t)}
        for t in ("corpus_screening_jobs", "corpus_candidate_selections",
                  "channel_candidates", "channel_assignment_attempts")
    }

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    with db_engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            module.upgrade()          # IKINCI kez — hicbir sey degismemeli

    inspector = inspect(db_engine)
    assert set(inspector.get_table_names()) == before_tables
    for table, names in before_indexes.items():
        assert {i["name"] for i in inspector.get_indexes(table)} == names


def test_partial_unique_indexes_have_predicates(fresh_schema, db_engine):
    """Reuse/aktiflik kisitlari PARTIAL unique olmali (tam unique degil):
    ayni kimlikte failed/fallback kayitlari birikebilmeli."""
    with db_engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT indexname, indexdef FROM pg_indexes WHERE indexname IN "
            "('uq_screening_job_active_identity', "
            " 'uq_screening_job_completed_identity', "
            " 'uq_assignment_attempt_active_run')"
        )).fetchall()
    found = {name: definition for name, definition in rows}
    assert len(found) == 3, f"eksik partial index: {sorted(found)}"
    for name, definition in found.items():
        assert "UNIQUE" in definition.upper(), name
        assert "WHERE" in definition.upper(), f"{name} predicate tasimiyor"


def test_money_columns_are_numeric(fresh_schema, db_engine):
    """Codex kisiti: para alanlari Numeric/Decimal (float DEGIL)."""
    inspector = inspect(db_engine)
    checks = {
        "ai_cost_reservations": ("ceiling_usd", "actual_usd"),
        "corpus_screening_jobs": ("cost_usd",),
        "channel_assignment_attempts": ("approved_screening_cap_usd",
                                        "approved_downstream_cap_usd"),
        "corpus_screening_batch_checkpoints": ("cost_usd",),
    }
    for table, columns in checks.items():
        types = {c["name"]: str(c["type"]).upper()
                 for c in inspector.get_columns(table)}
        for column in columns:
            assert "NUMERIC" in types[column], f"{table}.{column}: {types[column]}"
