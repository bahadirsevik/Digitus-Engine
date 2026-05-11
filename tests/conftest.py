"""
Pytest fixtures for Digitus Engine tests.

Test infrastructure:
- Real Postgres via docker-compose.test.yml (no SQLite mocking).
- Database is migrated once per session (alembic upgrade head).
- Each test truncates all tables before running.
- Factory helpers for BrandProfile / Keyword / ScoringRun.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Generator

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture(scope="session")
def project_root() -> Path:
    return PROJECT_ROOT


@pytest.fixture(scope="session")
def app_dir(project_root: Path) -> Path:
    return project_root / "app"


@pytest.fixture(scope="session")
def db_engine():
    """Provide the SQLAlchemy engine bound to the test database.

    Migration is expected to have been applied by the container entrypoint
    (`alembic upgrade head`) before pytest starts. If running outside the
    container, ensure DATABASE_URL points to a migrated test DB.
    """
    from app.database.connection import engine

    yield engine


@pytest.fixture(scope="function")
def db_session(db_engine) -> Generator[Session, None, None]:
    """A per-test SQLAlchemy session. Truncates all tables before each test.

    Truncation order is delegated to a single TRUNCATE ... CASCADE so we
    don't need to track FK dependency order manually.
    """
    from app.database.connection import SessionLocal
    from app.database.models import Base

    # Use .tables (dict) instead of .sorted_tables, because the BrandProfile
    # ↔ ScoringRun pair has a (legacy DEPRECATED) cycle that confuses topo-sort.
    # TRUNCATE ... CASCADE handles ordering for us.
    table_names = list(Base.metadata.tables.keys())
    quoted = ", ".join(f'"{name}"' for name in table_names)

    with db_engine.connect() as conn:
        conn.execute(text(f"TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE"))
        conn.commit()

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db_session):
    """FastAPI TestClient with the get_db dependency wired to the test session.

    Also disables `verify_api_key` so workspace-isolation tests don't have
    to juggle the API key header. Auth behaviour itself is covered in
    `tests/integration/test_auth.py` (plan2 P4 madde 11).
    """
    from fastapi.testclient import TestClient

    from app.core.security import verify_api_key
    from app.database.connection import get_db
    from app.main import app

    def _override_get_db():
        try:
            yield db_session
        finally:
            pass

    async def _noop_verify_api_key():
        return None

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[verify_api_key] = _noop_verify_api_key
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(verify_api_key, None)


# --- Factory helpers -------------------------------------------------------


@pytest.fixture
def make_workspace(db_session):
    """Factory: create a BrandProfile (workspace) with sensible defaults."""
    from app.database.models import BrandProfile

    created: list[BrandProfile] = []

    def _make(name: str = "Test Workspace", **kwargs) -> BrandProfile:
        defaults = dict(
            name=name,
            company_url=kwargs.pop("company_url", f"https://{name.lower().replace(' ', '-')}.test"),
            preliminary_info=kwargs.pop("preliminary_info", None),
            suggested_keywords=kwargs.pop("suggested_keywords", None),
            is_system_default=kwargs.pop("is_system_default", False),
            status=kwargs.pop("status", "confirmed"),
        )
        defaults.update(kwargs)
        workspace = BrandProfile(**defaults)
        db_session.add(workspace)
        db_session.commit()
        db_session.refresh(workspace)
        created.append(workspace)
        return workspace

    return _make


@pytest.fixture
def make_keyword(db_session):
    """Factory: create a Keyword and optionally link it to a workspace via WorkspaceKeyword."""
    from app.database.models import Keyword, WorkspaceKeyword

    def _make(
        text_value: str = "test keyword",
        *,
        brand_profile_id: int | None = None,
        data_source: str = "csv",
        volume: int = 100,
        competition: float = 0.5,
        trend_3m: float = 0.0,
        trend_12m: float = 0.0,
        **kwargs,
    ) -> Keyword:
        keyword = Keyword(
            keyword=text_value,
            normalized_keyword=text_value.lower(),
            volume=volume,
            competition=competition,
            trend_3m=trend_3m,
            trend_12m=trend_12m,
            **kwargs,
        )
        db_session.add(keyword)
        db_session.commit()
        db_session.refresh(keyword)
        if brand_profile_id is not None:
            link = WorkspaceKeyword(
                brand_profile_id=brand_profile_id,
                keyword_id=keyword.id,
                data_source=data_source,
                snapshot_volume=volume,
                snapshot_competition=competition,
                snapshot_trend_3m=trend_3m,
                snapshot_trend_12m=trend_12m,
            )
            db_session.add(link)
            db_session.commit()
        return keyword

    return _make


@pytest.fixture
def make_scoring_run(db_session):
    """Factory: create a ScoringRun in a workspace."""
    from app.database.models import ScoringRun

    def _make(
        *,
        brand_profile_id: int,
        name: str = "Test Run",
        status: str = "pending",
        ads_capacity: int = 10,
        seo_capacity: int = 10,
        social_capacity: int = 10,
        **kwargs,
    ) -> ScoringRun:
        # Model column is `run_name`, not `name` — accept either alias for ergonomics.
        run = ScoringRun(
            run_name=name,
            brand_profile_id=brand_profile_id,
            status=status,
            ads_capacity=ads_capacity,
            seo_capacity=seo_capacity,
            social_capacity=social_capacity,
            **kwargs,
        )
        db_session.add(run)
        db_session.commit()
        db_session.refresh(run)
        return run

    return _make
