"""
Dependency injection for FastAPI.
"""
from typing import Generator
from sqlalchemy.orm import Session

from app.database.connection import SessionLocal
from app.generators.ai_service import get_ai_service, AIService


def get_db() -> Generator[Session, None, None]:
    """
    Database session dependency.
    Creates a new session for each request and closes it after.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_ai() -> Generator[AIService, None, None]:
    """
    AI service dependency + istek-scoped usage collector (Codex v8-3).

    Sync endpoint'lerin AI çağrıları da ai_usage_events kapsamına girer;
    run/workspace kimliği bilinen endpoint'ler collector üzerine
    scoring_run_id/brand_profile_id atar (record anında okunur — AI
    çağrısından ÖNCE atanmalı). Finalize istek bitiminde garantidir.
    """
    from app.config import settings
    from app.core.telemetry import UsageCollector

    ai = get_ai_service(api_key=settings.GEMINI_API_KEY)
    collector = UsageCollector()
    if hasattr(ai, "collector"):
        ai.collector = collector
    try:
        yield ai
    finally:
        collector.finalize()
        # Plan G: istek-scope shutdown — client'ı ROOT servis kapatır
        # (wrapper'lar kapatamaz); istek bitti, in-flight çağrı kalmadı
        closer = getattr(ai, "close", None)
        if callable(closer):
            try:
                closer()
            except Exception:
                pass
