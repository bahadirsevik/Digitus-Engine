"""20261011_001: ai_usage_events provider kolonlari + genisletilmis CHECK.

Sema `alembic upgrade head` / create_all ile zaten guncel; migration'in
yeniden calistirilmasi no-op olmali.
"""
import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect

_COLS = {"provider", "cache_read_tokens", "cache_creation_tokens",
         "queue_wait_ms", "http_attempts"}


def _load():
    path = (Path(__file__).resolve().parents[2] / "migrations" / "versions"
            / "20261011_001_add_ai_usage_provider_columns.py")
    spec = importlib.util.spec_from_file_location("mig_20261011_001", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _ck_text(db_engine):
    cks = inspect(db_engine).get_check_constraints("corpus_screening_jobs")
    return next(c["sqltext"] for c in cks if c["name"] == "ck_screening_job_provider")


def test_columns_and_check_present(db_engine):
    cols = {c["name"] for c in inspect(db_engine).get_columns("ai_usage_events")}
    assert _COLS <= cols
    assert "anthropic" in _ck_text(db_engine)


def test_upgrade_twice_is_noop(db_engine):
    module = _load()
    before = {c["name"]: str(c["type"])
              for c in inspect(db_engine).get_columns("ai_usage_events")}
    for _ in range(2):
        with db_engine.begin() as conn:
            with Operations.context(MigrationContext.configure(conn)):
                module.upgrade()
    after = {c["name"]: str(c["type"])
             for c in inspect(db_engine).get_columns("ai_usage_events")}
    assert after == before
    assert "anthropic" in _ck_text(db_engine)
    assert module.revision == "20261011_001" and module.down_revision == "20261010_001"
