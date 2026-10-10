# -*- coding: utf-8 -*-
"""Giris kapisi (`require_login`) — router seviyesindeki oturum zorunlulugu.

Iki sozlesme birlikte dogrulanir:

1. LOGIN_ENABLED=false iken kapi DB oturumu ACMAZ ve oturum deposuna (Redis)
   GITMEZ — cookie gonderilse bile. (FastAPI Depends'leri govdeden once
   cozdugu icin bayrak kontrolunu govdeye tasimak yetmez; kapi yalniz
   `Request`'e baglidir.)
2. LOGIN_ENABLED=true iken davranis fail-closed: oturum yok/gecersiz -> 401,
   oturum deposu yok -> 503, gecici parola -> 403 PASSWORD_CHANGE_REQUIRED.

Test ortaminda Redis YOK: oturum deposu bellek ici bir sahteyle degistirilir.
Bu dosya yalniz gercek HTTP istekleri (TestClient) uzerinden calisir.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone

import pytest

from app.config import settings
from app.core.passwords import hash_password
from app.core.sessions import SessionBackendUnavailable
from app.database.models import User
from app.dependencies import get_db
from app.main import app

PROTECTED = "/api/v1/generation/social/format-matrix"
PASSWORD = "temp-password-123"
NEW_PASSWORD = "brand-new-password-456"


# ───────────────────────────── yardimcilar ─────────────────────────────


class FakeSessionStore:
    """`app.core.sessions` fonksiyonlarinin bellek ici karsiligi."""

    def __init__(self) -> None:
        self.sessions: dict[str, dict] = {}
        self.calls: list[str] = []
        self.unavailable = False
        self.forbid = False
        # True ise YALNIZ silme islemleri (revoke_*) depo hatasi verir; okuma
        # ve olusturma calisir (Redis'te silme basarisiz, okuma basarili).
        self.fail_deletes = False

    def _enter(self, name: str) -> None:
        self.calls.append(name)
        if self.forbid:
            raise AssertionError(f"Oturum deposuna dokunulmamaliydi: {name}")
        if self.unavailable:
            raise SessionBackendUnavailable("test: depo yok")
        if self.fail_deletes and name.startswith("revoke_"):
            raise SessionBackendUnavailable("test: silme basarisiz")

    def create_session(
        self, *, user_id, email, ip=None, user_agent=None, session_version=0
    ):
        self._enter("create_session")
        token = secrets.token_urlsafe(16)
        self.sessions[token] = {
            "user_id": user_id,
            "email": email,
            "session_version": session_version,
        }
        return token

    def get_session(self, token, *, refresh=True):
        self._enter("get_session")
        return self.sessions.get(token)

    def revoke_session(self, token):
        self._enter("revoke_session")
        self.sessions.pop(token, None)

    def revoke_all_sessions(self, user_id):
        self._enter("revoke_all_sessions")
        doomed = [t for t, p in self.sessions.items() if p["user_id"] == user_id]
        for token in doomed:
            del self.sessions[token]
        return len(doomed)

    def is_login_blocked(self, email, ip):
        return False

    def register_failed_login(self, email, ip):
        return 1

    def clear_failed_logins(self, email, ip):
        return None


class DbSpy:
    """`get_db` override'i: test oturumunu verir, acilis/kapanisi sayar."""

    def __init__(self, session) -> None:
        self.session = session
        self.opened = 0
        self.closed = 0
        self.raise_on_open = False

    def reset(self) -> None:
        self.opened = 0
        self.closed = 0

    def provider(self):
        def _provider():
            self.opened += 1
            if self.raise_on_open:
                raise AssertionError("DB oturumu acilmamaliydi (get_db)")
            try:
                yield self.session
            finally:
                self.closed += 1

        return _provider


@pytest.fixture
def store(monkeypatch):
    fake = FakeSessionStore()
    monkeypatch.setattr("app.core.login.get_session", fake.get_session)
    # Kapi, surumu eskimis oturumu en iyi caba ile siler.
    monkeypatch.setattr("app.core.login.revoke_session", fake.revoke_session)
    for name in (
        "create_session",
        "revoke_session",
        "revoke_all_sessions",
        "is_login_blocked",
        "register_failed_login",
        "clear_failed_logins",
    ):
        monkeypatch.setattr(f"app.api.v1.auth.{name}", getattr(fake, name))
    return fake


@pytest.fixture
def db_spy(db_session, monkeypatch):
    spy = DbSpy(db_session)
    monkeypatch.setitem(app.dependency_overrides, get_db, spy.provider())
    return spy


@pytest.fixture
def make_user(db_session):
    def _make(
        email="ada@example.test",
        password=PASSWORD,
        *,
        must_change_password=False,
        is_active=True,
        deleted=False,
    ) -> User:
        user = User(
            email=email,
            password_hash=hash_password(password),
            full_name="Ada Test",
            is_active=is_active,
            must_change_password=must_change_password,
            deleted_at=datetime.now(timezone.utc) if deleted else None,
        )
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)
        return user

    return _make


def _login(client, email="ada@example.test", password=PASSWORD):
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    assert client.cookies.get(settings.SESSION_COOKIE_NAME)
    return resp


# ───────────────────── LOGIN_ENABLED=false: hicbir sey dokunulmaz ─────────────────────


class TestGateDisabled:
    @pytest.fixture(autouse=True)
    def _disabled(self, monkeypatch, store, db_spy):
        monkeypatch.setattr(settings, "LOGIN_ENABLED", False)
        store.forbid = True
        db_spy.raise_on_open = True

    def test_no_cookie_touches_neither_db_nor_session_store(self, client, store, db_spy):
        resp = client.get(PROTECTED)
        assert resp.status_code == 200
        assert db_spy.opened == 0
        assert store.calls == []

    def test_stale_session_cookie_touches_neither_db_nor_session_store(
        self, client, store, db_spy
    ):
        client.cookies.set(settings.SESSION_COOKIE_NAME, "eski-bir-oturum-token")
        resp = client.get(PROTECTED)
        assert resp.status_code == 200
        assert db_spy.opened == 0
        assert store.calls == []

    def test_auth_status_reports_login_not_required(self, client):
        resp = client.get("/api/v1/auth/status")
        assert resp.status_code == 200
        assert resp.json() == {"login_required": False}


# ───────────────────── LOGIN_ENABLED=true: fail-closed ─────────────────────


class TestGateEnabled:
    @pytest.fixture(autouse=True)
    def _enabled(self, monkeypatch, store, db_spy):
        monkeypatch.setattr(settings, "LOGIN_ENABLED", True)

    def test_no_cookie_is_401_without_touching_db_or_store(self, client, store, db_spy):
        resp = client.get(PROTECTED)
        assert resp.status_code == 401
        assert resp.json()["detail"]["code"] == "NOT_AUTHENTICATED"
        assert db_spy.opened == 0
        assert store.calls == []

    def test_garbage_token_is_401(self, client, store, db_spy):
        client.cookies.set(settings.SESSION_COOKIE_NAME, "bu-token-hic-uretilmedi")
        resp = client.get(PROTECTED)
        assert resp.status_code == 401
        assert resp.json()["detail"]["code"] == "NOT_AUTHENTICATED"
        assert store.calls == ["get_session"]
        assert db_spy.opened == 0

    def test_removed_or_expired_session_is_401(self, client, store, db_spy, make_user):
        make_user()
        _login(client)
        assert client.get(PROTECTED).status_code == 200

        store.sessions.clear()  # TTL dolumu / sunucu tarafi iptal
        resp = client.get(PROTECTED)
        assert resp.status_code == 401
        assert resp.json()["detail"]["code"] == "NOT_AUTHENTICATED"

    def test_valid_session_is_200_and_db_session_closed(
        self, client, store, db_spy, make_user
    ):
        make_user()
        _login(client)
        db_spy.reset()

        resp = client.get(PROTECTED)
        assert resp.status_code == 200
        assert resp.json()["version"] == "v1"
        # Kapi override'li saglayiciyi kullandi ve oturumu kapatti.
        assert db_spy.opened == 1
        assert db_spy.closed == 1

    def test_deactivated_user_is_401_and_db_session_closed(
        self, client, store, db_spy, make_user, db_session
    ):
        user = make_user()
        _login(client)
        user.is_active = False
        db_session.commit()
        db_spy.reset()

        resp = client.get(PROTECTED)
        assert resp.status_code == 401
        assert resp.json()["detail"]["code"] == "NOT_AUTHENTICATED"
        assert db_spy.opened == 1
        assert db_spy.closed == 1

    def test_soft_deleted_user_is_401(self, client, store, db_spy, make_user, db_session):
        user = make_user()
        _login(client)
        user.deleted_at = datetime.now(timezone.utc)
        db_session.commit()

        assert client.get(PROTECTED).status_code == 401

    def test_session_backend_unavailable_is_503(self, client, store, db_spy, make_user):
        make_user()
        _login(client)
        store.unavailable = True
        db_spy.reset()

        resp = client.get(PROTECTED)
        assert resp.status_code == 503
        assert resp.json()["detail"]["code"] == "SESSION_BACKEND_UNAVAILABLE"
        assert db_spy.opened == 0  # depo yoksa DB'ye hic inilmez

    def test_auth_status_is_public_and_reports_login_required(self, client):
        resp = client.get("/api/v1/auth/status")
        assert resp.status_code == 200
        assert resp.json() == {"login_required": True}

    def test_me_requires_session(self, client):
        resp = client.get("/api/v1/auth/me")
        assert resp.status_code == 401


# ───────────────────── gecici parola (must_change_password) ─────────────────────


class TestMustChangePassword:
    @pytest.fixture(autouse=True)
    def _enabled(self, monkeypatch, store, db_spy):
        monkeypatch.setattr(settings, "LOGIN_ENABLED", True)

    def test_protected_endpoint_is_blocked_with_403(self, client, store, db_spy, make_user):
        make_user(must_change_password=True)
        resp = client.post(
            "/api/v1/auth/login", json={"email": "ada@example.test", "password": PASSWORD}
        )
        assert resp.status_code == 200
        assert resp.json()["must_change_password"] is True
        db_spy.reset()

        blocked = client.get(PROTECTED)
        assert blocked.status_code == 403
        assert blocked.json()["detail"]["code"] == "PASSWORD_CHANGE_REQUIRED"
        # Kapi DB oturumunu actiysa kapatmis olmali (403 yolu).
        assert db_spy.opened == db_spy.closed == 1

    def test_auth_endpoints_stay_reachable_and_password_change_unlocks(
        self, client, store, db_spy, make_user, db_session
    ):
        make_user(must_change_password=True)
        client.post(
            "/api/v1/auth/login", json={"email": "ada@example.test", "password": PASSWORD}
        )
        assert client.get(PROTECTED).status_code == 403

        # Kapiya bagli olmayan auth uclari acik.
        assert client.get("/api/v1/auth/status").status_code == 200
        me = client.get("/api/v1/auth/me")
        assert me.status_code == 200
        assert me.json()["must_change_password"] is True

        # Yanlis mevcut parola reddedilir.
        wrong = client.post(
            "/api/v1/auth/change-password",
            json={"current_password": "yanlis-parola-xyz", "new_password": NEW_PASSWORD},
        )
        assert wrong.status_code == 400
        assert wrong.json()["detail"]["code"] == "INVALID_CURRENT_PASSWORD"

        # Dogru mevcut parola ile degisim basarili; tum oturumlar kapanir.
        changed = client.post(
            "/api/v1/auth/change-password",
            json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
        )
        assert changed.status_code == 204
        assert store.sessions == {}
        db_session.expire_all()
        user = db_session.query(User).filter(User.email == "ada@example.test").one()
        assert user.must_change_password is False

        # Eski oturum artik gecersiz; yeni parola ile giris veri uclarini acar.
        assert client.get(PROTECTED).status_code == 401
        _login(client, password=NEW_PASSWORD)
        assert client.get(PROTECTED).status_code == 200

    def test_logout_stays_reachable(self, client, store, db_spy, make_user):
        make_user(must_change_password=True)
        client.post(
            "/api/v1/auth/login", json={"email": "ada@example.test", "password": PASSWORD}
        )
        assert client.get(PROTECTED).status_code == 403

        resp = client.post("/api/v1/auth/logout")
        assert resp.status_code == 204
        assert store.sessions == {}
        assert client.get(PROTECTED).status_code == 401
