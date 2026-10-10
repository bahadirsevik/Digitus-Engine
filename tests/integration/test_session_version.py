# -*- coding: utf-8 -*-
"""S-1: parola degisimi eski oturumlari Redis'e BAGLI OLMADAN kesmeli.

Mekanizma: `users.session_version`. Oturum yuku giris aninda kullanicinin
surumunu tasir; kapi (`require_login`) ve `resolve_current_user` her istekte DB
surumuyle karsilastirir. Parola degisimi/sifirlamasi surumu parola ile AYNI
transaction'da artirir. Redis'te silme basarisiz olsa bile (TTL kayan pencere
oldugu icin aktif oturum sonsuza kadar yasayabilirdi) eski oturum reddedilir.

Yalniz gercek HTTP istekleri (TestClient) + bellek ici oturum deposu sahtesi
kullanilir; sahte `test_login_gate` ile ortaktir.
"""
from __future__ import annotations

import sys

import pytest
from sqlalchemy import text

from app.config import settings
from app.core.passwords import verify_password
from app.core.sessions import SessionBackendUnavailable
from app.database.models import User

from tests.integration.test_login_gate import (  # noqa: F401  (fixture olarak kullanilir)
    NEW_PASSWORD,
    PASSWORD,
    PROTECTED,
    DbSpy,
    FakeSessionStore,
    _login,
    db_spy,
    make_user,
    store,
)

EMAIL = "ada@example.test"
ME = "/api/v1/auth/me"
CHANGE = "/api/v1/auth/change-password"


def _change(client, current=PASSWORD, new=NEW_PASSWORD):
    return client.post(
        CHANGE, json={"current_password": current, "new_password": new}
    )


def _fresh(db_session, email=EMAIL) -> User:
    db_session.expire_all()
    return db_session.query(User).filter(User.email == email).one()


def _session_token(client) -> str:
    return client.cookies.get(settings.SESSION_COOKIE_NAME)


@pytest.fixture
def enabled(monkeypatch, store, db_spy):
    monkeypatch.setattr(settings, "LOGIN_ENABLED", True)


# ───────────────────── surum oturum yukune yazilir ─────────────────────


class TestVersionFlowsIntoSession:
    def test_login_stores_the_current_user_version(
        self, enabled, client, store, make_user, db_session
    ):
        user = make_user()
        user.session_version = 3
        db_session.commit()

        _login(client)

        assert store.sessions[_session_token(client)]["session_version"] == 3
        assert client.get(PROTECTED).status_code == 200
        assert client.get(ME).status_code == 200

    def test_login_after_password_change_carries_new_version(
        self, enabled, client, store, make_user, db_session
    ):
        make_user()
        _login(client)
        old_token = _session_token(client)
        store.fail_deletes = True  # eski oturum depoda KALIR
        assert _change(client).status_code == 204
        store.fail_deletes = False

        _login(client, password=NEW_PASSWORD)

        new_token = _session_token(client)
        assert new_token != old_token
        assert store.sessions[new_token]["session_version"] == 1
        assert client.get(PROTECTED).status_code == 200
        assert client.get(ME).status_code == 200


# ───────────────────── giris ile parola degisimi yarisi ─────────────────────


def _interleave_password_change(monkeypatch, db_engine, user_id, new_hash):
    """
    `login` icindeki parola dogrulamasi BITTIKTEN HEMEN SONRA, AYRI bir DB
    oturumunda (kendi baglantisi) change-password'un yaptigini yapar: yeni
    hash + session_version + 1, commit. Boylece giris istegi dogrulamayi ESKI
    parolayla gecmis, ama commit'ine baska bir transaction'in degisikligi
    girmis olur. Tek atimliktir.
    """
    from sqlalchemy.orm import sessionmaker

    real_verify = verify_password
    fired = {"n": 0}

    def verify_then_change(plain, hashed):
        ok = real_verify(plain, hashed)
        if fired["n"] == 0:
            fired["n"] += 1
            other = sessionmaker(bind=db_engine)()
            try:
                other.execute(
                    text(
                        "UPDATE users SET password_hash = :h, "
                        "session_version = session_version + 1 WHERE id = :i"
                    ),
                    {"h": new_hash, "i": user_id},
                )
                other.commit()
            finally:
                other.close()
        return ok

    monkeypatch.setattr("app.api.v1.auth.verify_password", verify_then_change)
    return fired


class TestLoginRacingPasswordChange:
    def test_login_started_with_old_password_never_gets_a_valid_session(
        self, enabled, client, store, make_user, db_session, db_engine, monkeypatch
    ):
        from app.core.passwords import hash_password

        user = make_user()
        new_hash = hash_password(NEW_PASSWORD)
        fired = _interleave_password_change(monkeypatch, db_engine, user.id, new_hash)

        # Giris ESKI parolayla baslar; dogrulamadan sonra parola degisir.
        resp = client.post(
            "/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD}
        )

        assert fired["n"] == 1  # araya girme gercekten gerceklesti
        assert resp.status_code == 200, resp.text
        token = _session_token(client)
        # Oturum DOGRULAMA anindaki surumu tasir, degisim sonrasini degil.
        assert store.sessions[token]["session_version"] == 0
        persisted = _fresh(db_session)
        assert persisted.session_version == 1
        assert persisted.password_hash == new_hash
        # Kapi ve /auth/me bu oturumu reddeder.
        guarded = client.get(PROTECTED)
        assert guarded.status_code == 401
        assert guarded.json()["detail"]["code"] == "NOT_AUTHENTICATED"
        assert client.get(ME).status_code == 401

    def test_rehash_does_not_overwrite_a_concurrently_changed_password(
        self, enabled, client, store, make_user, db_session, db_engine, monkeypatch
    ):
        from app.core.passwords import hash_password

        user = make_user()
        new_hash = hash_password(NEW_PASSWORD)
        monkeypatch.setattr("app.api.v1.auth.needs_rehash", lambda h: True)
        fired = _interleave_password_change(monkeypatch, db_engine, user.id, new_hash)

        resp = client.post(
            "/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD}
        )

        assert fired["n"] == 1
        assert resp.status_code == 200, resp.text
        persisted = _fresh(db_session)
        # Yeni parolanin hash'i oldugu gibi duruyor: eski parolanin yeniden
        # hash'i onu ezmedi.
        assert persisted.password_hash == new_hash
        assert verify_password(NEW_PASSWORD, persisted.password_hash)
        assert not verify_password(PASSWORD, persisted.password_hash)
        assert persisted.session_version == 1

    def test_uncontended_rehash_still_upgrades_the_hash(
        self, enabled, client, store, make_user, db_session, monkeypatch
    ):
        user = make_user()
        old_hash = user.password_hash
        monkeypatch.setattr("app.api.v1.auth.needs_rehash", lambda h: True)

        _login(client)

        persisted = _fresh(db_session)
        assert persisted.password_hash != old_hash
        assert verify_password(PASSWORD, persisted.password_hash)
        assert persisted.last_login_at is not None
        assert persisted.session_version == 0
        assert store.sessions[_session_token(client)]["session_version"] == 0
        assert client.get(PROTECTED).status_code == 200
        assert client.get(ME).status_code == 200


# ───────────────────── ayni transaction ─────────────────────


class TestPasswordAndVersionShareOneTransaction:
    def test_hash_and_version_are_committed_together(
        self, enabled, client, store, make_user, db_session, monkeypatch
    ):
        user = make_user(must_change_password=True)
        user_id, old_hash = user.id, user.password_hash
        _login(client)

        real_commit = db_session.commit
        seen = {"commits": 0, "in_txn": None}

        def spy_commit():
            seen["commits"] += 1
            db_session.flush()
            # Commit'ten HEMEN ONCE, ayni transaction icinde: ikisi de yazilmis.
            seen["in_txn"] = tuple(
                db_session.execute(
                    text(
                        "SELECT password_hash, session_version, must_change_password "
                        "FROM users WHERE id = :i"
                    ),
                    {"i": user_id},
                ).one()
            )
            return real_commit()

        monkeypatch.setattr(db_session, "commit", spy_commit)

        assert _change(client).status_code == 204

        assert seen["commits"] == 1  # tek commit
        in_hash, in_version, in_must = seen["in_txn"]
        assert in_hash != old_hash and verify_password(NEW_PASSWORD, in_hash)
        assert in_version == 1
        assert in_must is False

        persisted = _fresh(db_session)
        assert persisted.password_hash == in_hash
        assert persisted.session_version == 1
        assert persisted.must_change_password is False

    def test_failed_commit_persists_neither_and_revokes_nothing(
        self, enabled, client, store, make_user, db_session, monkeypatch
    ):
        user = make_user(must_change_password=True)
        old_hash = user.password_hash
        _login(client)

        def boom():
            raise RuntimeError("commit patladi")

        with monkeypatch.context() as patch:
            patch.setattr(db_session, "commit", boom)
            with pytest.raises(RuntimeError, match="commit patladi"):
                _change(client)
        db_session.rollback()  # gercek get_db'nin kapanista yaptigi

        persisted = _fresh(db_session)
        assert persisted.password_hash == old_hash
        assert persisted.session_version == 0
        assert persisted.must_change_password is True
        # Basarisiz degisimde oturumlar kapatilmadi ve eski oturum hala gecerli.
        assert "revoke_all_sessions" not in store.calls
        assert client.get(ME).status_code == 200


# ───────────────────── Redis silmesi basarisiz: eski oturum reddedilir ─────────────────────


class TestOldSessionRejectedWhenStoreDeleteFails:
    def test_old_token_remains_in_store_but_is_rejected_everywhere(
        self, enabled, client, store, make_user, db_session
    ):
        make_user()
        _login(client)
        old_token = _session_token(client)
        assert client.get(PROTECTED).status_code == 200

        store.fail_deletes = True
        assert _change(client).status_code == 204

        # Silme basarisiz: kayit depoda duruyor...
        assert old_token in store.sessions
        assert "revoke_all_sessions" in store.calls
        # ...ama kapi (router) ve resolve_current_user (/auth/me) reddediyor.
        guarded = client.get(PROTECTED)
        assert guarded.status_code == 401
        assert guarded.json()["detail"]["code"] == "NOT_AUTHENTICATED"
        me = client.get(ME)
        assert me.status_code == 401
        assert me.json()["detail"]["code"] == "NOT_AUTHENTICATED"
        # Reddetme depo hatasini 500'e cevirmedi ve kayit hala orada.
        assert old_token in store.sessions

    def test_rejected_stale_session_is_dropped_best_effort_once_store_recovers(
        self, enabled, client, store, make_user
    ):
        make_user()
        _login(client)
        old_token = _session_token(client)
        store.fail_deletes = True
        assert _change(client).status_code == 204
        assert old_token in store.sessions

        store.fail_deletes = False
        assert client.get(PROTECTED).status_code == 401
        assert old_token not in store.sessions

    def test_stale_session_delete_error_never_becomes_a_500(
        self, enabled, client, store, make_user
    ):
        make_user()
        _login(client)
        store.fail_deletes = True
        assert _change(client).status_code == 204

        # Gate'in en-iyi-caba silmesi de patliyor (fail_deletes hala acik).
        assert client.get(PROTECTED).status_code == 401
        assert client.get(ME).status_code == 401


# ───────────────────── eski (surumsuz) oturum yukleri ─────────────────────


class TestLegacyPayloadWithoutVersion:
    def test_legacy_payload_is_valid_while_user_version_is_zero(
        self, enabled, client, store, make_user
    ):
        make_user()
        _login(client)
        token = _session_token(client)
        del store.sessions[token]["session_version"]  # alan eklenmeden once acilmis

        assert client.get(PROTECTED).status_code == 200
        assert client.get(ME).status_code == 200

    def test_legacy_payload_is_rejected_after_first_password_change(
        self, enabled, client, store, make_user
    ):
        make_user()
        _login(client)
        token = _session_token(client)
        del store.sessions[token]["session_version"]
        assert client.get(PROTECTED).status_code == 200

        store.fail_deletes = True  # eski oturum depoda kalsin
        assert _change(client).status_code == 204

        assert token in store.sessions
        assert client.get(PROTECTED).status_code == 401
        assert client.get(ME).status_code == 401

    def test_legacy_payload_is_rejected_when_user_version_is_nonzero(
        self, enabled, client, store, make_user, db_session
    ):
        user = make_user()
        user.session_version = 1
        db_session.commit()
        store.sessions["legacy-token"] = {"user_id": user.id, "email": user.email}
        client.cookies.set(settings.SESSION_COOKIE_NAME, "legacy-token")

        assert client.get(PROTECTED).status_code == 401
        assert client.get(ME).status_code == 401

    @pytest.mark.parametrize("bad", ["0", None, True, 1.5, [0]])
    def test_malformed_version_value_is_rejected(
        self, enabled, client, store, make_user, bad
    ):
        user = make_user()
        store.sessions["odd-token"] = {
            "user_id": user.id,
            "email": user.email,
            "session_version": bad,
        }
        client.cookies.set(settings.SESSION_COOKIE_NAME, "odd-token")

        assert client.get(PROTECTED).status_code == 401
        assert client.get(ME).status_code == 401


# ───────────────────── LOGIN_ENABLED=false degismedi ─────────────────────


class TestGateDisabledStaysUntouched:
    @pytest.fixture(autouse=True)
    def _disabled(self, monkeypatch, store, db_spy):
        monkeypatch.setattr(settings, "LOGIN_ENABLED", False)
        store.forbid = True
        db_spy.raise_on_open = True

    def test_protected_endpoint_without_cookie(self, client, store, db_spy):
        assert client.get(PROTECTED).status_code == 200
        assert db_spy.opened == 0
        assert store.calls == []

    def test_protected_endpoint_with_stale_version_cookie(self, client, store, db_spy):
        store.sessions["eski"] = {"user_id": 1, "email": "x", "session_version": 99}
        client.cookies.set(settings.SESSION_COOKIE_NAME, "eski")
        assert client.get(PROTECTED).status_code == 200
        assert db_spy.opened == 0
        assert store.calls == []


# ───────────────────── CLI parola yollari ─────────────────────


def _run_cli(monkeypatch, db_session, *argv) -> int:
    from app.cli import create_user

    monkeypatch.setattr(create_user, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(sys, "argv", ["create_user", *argv])
    return create_user.main()


class TestCliPasswordPaths:
    def test_reset_password_bumps_version_with_the_hash(
        self, monkeypatch, db_session, make_user
    ):
        user = make_user()
        old_hash = user.password_hash

        code = _run_cli(
            monkeypatch,
            db_session,
            "--email", EMAIL,
            "--reset-password",
            "--password", NEW_PASSWORD,
            "--no-must-change",
        )

        assert code == 0
        persisted = _fresh(db_session)
        assert persisted.password_hash != old_hash
        assert verify_password(NEW_PASSWORD, persisted.password_hash)
        assert persisted.session_version == 1

    def test_reset_invalidates_old_session_even_if_redis_cleanup_fails(
        self, enabled, monkeypatch, client, store, db_session, make_user
    ):
        make_user()
        _login(client)
        old_token = _session_token(client)

        def redis_down(user_id):
            raise SessionBackendUnavailable("redis yok")

        monkeypatch.setattr("app.core.sessions.revoke_all_sessions", redis_down)
        code = _run_cli(
            monkeypatch,
            db_session,
            "--email", EMAIL,
            "--reset-password",
            "--password", NEW_PASSWORD,
        )

        assert code == 0
        assert old_token in store.sessions
        assert client.get(PROTECTED).status_code == 401
        assert client.get(ME).status_code == 401

    def test_deactivate_bumps_version(self, monkeypatch, db_session, make_user):
        make_user()
        monkeypatch.setattr("app.core.sessions.revoke_all_sessions", lambda uid: 0)

        code = _run_cli(monkeypatch, db_session, "--email", EMAIL, "--deactivate")

        assert code == 0
        persisted = _fresh(db_session)
        assert persisted.is_active is False
        assert persisted.session_version == 1

    def test_new_user_starts_at_version_zero(self, monkeypatch, db_session):
        code = _run_cli(
            monkeypatch,
            db_session,
            "--email", "yeni@example.test",
            "--password", NEW_PASSWORD,
        )

        assert code == 0
        assert _fresh(db_session, "yeni@example.test").session_version == 0
