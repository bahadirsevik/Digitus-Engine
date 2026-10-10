"""
API key authentication tests (plan2 §P4.11).

Covers:
- Feature flag OFF (API_KEY not set): all requests pass through
- Feature flag ON (API_KEY set): missing header → 401, wrong key → 401, correct key → 200
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client_with_real_auth(db_session):
    """TestClient that does NOT override verify_api_key — lets the real check run."""
    from app.database.connection import get_db
    from app.main import app

    def _override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.pop(get_db, None)


class TestApiKeyDisabled:
    """When API_KEY env is unset, all requests pass through without a header."""

    def test_request_without_header_succeeds(self, client_with_real_auth, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "API_KEY", None)
        resp = client_with_real_auth.get("/health")
        assert resp.status_code == 200

    def test_request_with_any_header_value_succeeds(self, client_with_real_auth, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "API_KEY", None)
        resp = client_with_real_auth.get("/health", headers={"X-API-Key": "anything"})
        assert resp.status_code == 200


class TestApiKeyEnabled:
    """When API_KEY is set, only matching header passes."""

    VALID_KEY = "test-secret-key-abc123"

    def test_missing_header_returns_401(self, client_with_real_auth, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "API_KEY", self.VALID_KEY)
        resp = client_with_real_auth.get("/api/v1/keywords/", params={"brand_profile_id": 1})
        assert resp.status_code == 401

    def test_wrong_key_returns_401(self, client_with_real_auth, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "API_KEY", self.VALID_KEY)
        resp = client_with_real_auth.get(
            "/api/v1/keywords/",
            params={"brand_profile_id": 1},
            headers={"X-API-Key": "wrong-key"},
        )
        assert resp.status_code == 401
        assert "Invalid or missing API key" in resp.json().get("detail", "")

    def test_correct_key_passes_auth(self, client_with_real_auth, monkeypatch, make_workspace):
        from app.config import settings

        monkeypatch.setattr(settings, "API_KEY", self.VALID_KEY)
        ws = make_workspace(name="AuthTest")
        resp = client_with_real_auth.get(
            "/api/v1/keywords/",
            params={"brand_profile_id": ws.id},
            headers={"X-API-Key": self.VALID_KEY},
        )
        # 200 means auth passed (may be empty list, but not 401)
        assert resp.status_code == 200

    def test_empty_string_key_returns_401(self, client_with_real_auth, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "API_KEY", self.VALID_KEY)
        resp = client_with_real_auth.get(
            "/api/v1/keywords/",
            params={"brand_profile_id": 1},
            headers={"X-API-Key": ""},
        )
        assert resp.status_code == 401

    def test_www_authenticate_header_present_on_401(self, client_with_real_auth, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "API_KEY", self.VALID_KEY)
        resp = client_with_real_auth.get("/api/v1/keywords/", params={"brand_profile_id": 1})
        assert resp.status_code == 401
        assert "WWW-Authenticate" in resp.headers
