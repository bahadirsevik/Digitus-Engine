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


def _refuse_non_test_database() -> None:
    """GÜVENLİK GUARD'I: pytest yalnızca TEST veritabanına karşı çalışabilir.

    2026-07-07: prod DB, pytest'in DATABASE_URL=prod işaret eden bir ortamda
    çalıştırılması sonucu TRUNCATE edildi (db_session fixture'ı her testte
    tüm tabloları truncate eder). Bu guard, veritabanı adı veya host 'test'
    içermiyorsa pytest'i daha TOPLAMA aşamasında, hiçbir DB bağlantısı
    kurulmadan durdurur. Doğru kullanım:
        docker-compose -f docker-compose.test.yml run --rm test_app pytest tests/
    """
    from urllib.parse import urlparse

    url = os.environ.get("DATABASE_URL", "")
    if not url:
        try:
            from app.config import settings
            url = settings.database_url
        except Exception:
            return  # config yoksa DB'ye bağlanan testler zaten çalışamaz

    parsed = urlparse(url)
    dbname = (parsed.path or "").lstrip("/")
    host = parsed.hostname or ""
    if "test" not in dbname.lower() and "test" not in host.lower():
        pytest.exit(
            "GÜVENLİK: DATABASE_URL test veritabanına işaret etmiyor "
            f"(db={dbname!r}, host={host!r}). db_session fixture'ı tüm "
            "tabloları TRUNCATE ettiği için pytest durduruldu. "
            "docker-compose.test.yml ile çalıştırın.",
            returncode=2,
        )


_refuse_non_test_database()


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
        monthly_volume: int = 100,
        competition_score: float = 0.5,
        trend_3m: float = 0.0,
        trend_12m: float = 0.0,
        **kwargs,
    ) -> Keyword:
        keyword = Keyword(
            keyword=text_value,
            normalized_keyword=text_value.lower(),
            monthly_volume=monthly_volume,
            competition_score=competition_score,
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
                monthly_volume=monthly_volume,
                competition_score=competition_score,
                trend_3m=trend_3m,
                trend_12m=trend_12m,
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
        # Plan v13 freshness sözleşmesi: elle kurulan run'lar varsayılan
        # olarak TAZE sayılır (workspace'in güncel policy/anchor sürümünü
        # taşır) — aksi halde her generation/export testi POLICY_STALE 409
        # alırdı. Stale senaryoları sürümü AÇIKÇA uyumsuz geçirerek kurulur.
        from app.database.models import BrandProfile as _BP

        workspace = db_session.get(_BP, brand_profile_id)
        if workspace is not None:
            kwargs.setdefault(
                "channel_pool_policy_version", workspace.policy_version or 1
            )
            kwargs.setdefault(
                "relevance_anchor_version", workspace.anchor_version or 1
            )
            alg = kwargs.get("algorithm_version", "v3")
            if (
                alg == "v3"
                and status in ("channel_assigned", "completed")
                and getattr(workspace, "status", None) == "confirmed"
                and not getattr(workspace, "deleted_at", None)
            ):
                from app.core.engine.context import (
                    build_firm_profile,
                    firm_block,
                    firm_block_sha256,
                )
                prof_dict = build_firm_profile(workspace)
                fb_sha = firm_block_sha256(firm_block(prof_dict))
                manifest = dict(kwargs.get("execution_manifest") or {})
                if "engine_v3" not in manifest:
                    manifest["engine_v3"] = {
                        "firm_block_sha256": fb_sha,
                        "algorithm_versions": {},
                        "models": {},
                        "prompt_shas": {},
                    }
                kwargs["execution_manifest"] = manifest

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


@pytest.fixture
def make_keyword_score(db_session):
    """Factory: create a KeywordScore row for a scoring run."""
    from app.database.models import KeywordScore

    def _make(
        *,
        scoring_run_id: int,
        keyword_id: int,
        ads_score=None,
        seo_score=None,
        social_score=None,
        ads_rank=None,
        seo_rank=None,
        social_rank=None,
        **kwargs,
    ) -> KeywordScore:
        score = KeywordScore(
            scoring_run_id=scoring_run_id,
            keyword_id=keyword_id,
            ads_score=ads_score,
            seo_score=seo_score,
            social_score=social_score,
            ads_rank=ads_rank,
            seo_rank=seo_rank,
            social_rank=social_rank,
            **kwargs,
        )
        db_session.add(score)
        db_session.commit()
        db_session.refresh(score)
        return score

    return _make
