"""Production'da DEBUG=true reddedilmeli (`Settings.check_production_security`)."""
import pytest

from app.config import Settings

_PROD = dict(
    APP_ENV="production",
    SECRET_KEY="x" * 32,
    POSTGRES_PASSWORD="strong-pass",
    API_KEY="k" * 32,
    CORS_ORIGINS="https://app.example.com",
)


def test_production_rejects_debug_true():
    with pytest.raises(ValueError, match="DEBUG"):
        Settings(DEBUG=True, **_PROD)


def test_production_accepts_debug_false():
    assert Settings(DEBUG=False, **_PROD).DEBUG is False

