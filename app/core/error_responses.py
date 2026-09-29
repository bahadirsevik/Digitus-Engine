"""Helpers for safe public error responses."""
from loguru import logger

from app.config import settings


def safe_500_detail(error: Exception, fallback: str) -> str:
    """Log internal exceptions while hiding details outside debug mode."""
    logger.exception(fallback)
    return str(error) if settings.DEBUG else fallback
