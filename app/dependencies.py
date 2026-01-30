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


def get_ai() -> AIService:
    """
    AI service dependency.
    Returns the configured AI service based on environment.
    """
    from app.config import settings
    return get_ai_service(api_key=settings.GEMINI_API_KEY)
