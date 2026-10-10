"""Profil janitörü (P7 Adım 6) — startup'ta takılı profilleri failed işaretler.

Kilitlenen davranışlar: eşikten eski running/pending → failed + mesaj;
taze running dokunulmaz; confirmed dokunulmaz; yaş updated_at üzerinden.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.core.site_analyzer.stuck_janitor import (
    STUCK_ERROR_MESSAGE,
    fail_if_stuck,
    fail_stuck_profiles,
)
from app.database.models import BrandProfile


def _set_updated_at(db_session, profile_id: int, when: datetime) -> None:
    # onupdate tetiklenmesin diye ham SQL ile geçmişe çek
    db_session.execute(
        text("UPDATE brand_profiles SET updated_at = :ts WHERE id = :pid"),
        {"ts": when, "pid": profile_id},
    )
    db_session.commit()


def test_old_running_marked_failed(db_session, make_workspace):
    ws = make_workspace(name="janitor-old", company_url="https://jan-old.example")
    ws.status = "running"
    db_session.commit()
    _set_updated_at(db_session, ws.id, datetime.now(timezone.utc) - timedelta(minutes=45))

    count = fail_stuck_profiles(db_session, stale_minutes=15)

    db_session.expire_all()
    row = db_session.query(BrandProfile).filter(BrandProfile.id == ws.id).one()
    assert count == 1
    assert row.status == "failed"
    assert row.error_message == STUCK_ERROR_MESSAGE


def test_fresh_running_untouched(db_session, make_workspace):
    ws = make_workspace(name="janitor-fresh", company_url="https://jan-fresh.example")
    ws.status = "running"
    db_session.commit()
    # updated_at şimdi (commit onupdate'i tetikledi) — eşik altında

    count = fail_stuck_profiles(db_session, stale_minutes=15)

    db_session.expire_all()
    row = db_session.query(BrandProfile).filter(BrandProfile.id == ws.id).one()
    assert count == 0
    assert row.status == "running"


def test_confirmed_untouched_even_if_old(db_session, make_workspace):
    ws = make_workspace(name="janitor-confirmed", company_url="https://jan-conf.example")
    ws.status = "confirmed"
    db_session.commit()
    _set_updated_at(db_session, ws.id, datetime.now(timezone.utc) - timedelta(hours=5))

    count = fail_stuck_profiles(db_session, stale_minutes=15)

    db_session.expire_all()
    row = db_session.query(BrandProfile).filter(BrandProfile.id == ws.id).one()
    assert count == 0
    assert row.status == "confirmed"


def test_old_pending_marked_failed(db_session, make_workspace):
    ws = make_workspace(name="janitor-pending", company_url="https://jan-pend.example")
    ws.status = "pending"
    db_session.commit()
    _set_updated_at(db_session, ws.id, datetime.now(timezone.utc) - timedelta(minutes=30))

    count = fail_stuck_profiles(db_session, stale_minutes=15)

    db_session.expire_all()
    row = db_session.query(BrandProfile).filter(BrandProfile.id == ws.id).one()
    assert count == 1
    assert row.status == "failed"


# ---------------------------------------------------------------------------
# fail_if_stuck — lazy (okuma anındaki) tekil-satır kontrolü.
#
# Startup janitörü tek seferlik olduğu için restart eşik anında olmazsa,
# taze görünüp atlanan bir satır bir daha ASLA taranmaz. fail_if_stuck aynı
# eşik kuralını get_profile/get_workspace okumasında uygular.
# ---------------------------------------------------------------------------


def test_fail_if_stuck_flips_old_running(db_session, make_workspace):
    ws = make_workspace(name="janitor-lazy-old", company_url="https://jan-lazy-old.example")
    ws.status = "running"
    db_session.commit()
    _set_updated_at(db_session, ws.id, datetime.now(timezone.utc) - timedelta(minutes=45))
    db_session.refresh(ws)

    result = fail_if_stuck(db_session, ws, stale_minutes=15)

    db_session.expire_all()
    row = db_session.query(BrandProfile).filter(BrandProfile.id == ws.id).one()
    assert result is True
    assert row.status == "failed"
    assert row.error_message == STUCK_ERROR_MESSAGE


def test_fail_if_stuck_leaves_fresh_running_untouched(db_session, make_workspace):
    ws = make_workspace(name="janitor-lazy-fresh", company_url="https://jan-lazy-fresh.example")
    ws.status = "running"
    db_session.commit()
    # updated_at şimdi (commit onupdate'i tetikledi) — eşik altında
    db_session.refresh(ws)

    result = fail_if_stuck(db_session, ws, stale_minutes=15)

    db_session.expire_all()
    row = db_session.query(BrandProfile).filter(BrandProfile.id == ws.id).one()
    assert result is False
    assert row.status == "running"


def test_fail_if_stuck_leaves_confirmed_untouched_even_if_old(db_session, make_workspace):
    ws = make_workspace(name="janitor-lazy-confirmed", company_url="https://jan-lazy-conf.example")
    ws.status = "confirmed"
    db_session.commit()
    _set_updated_at(db_session, ws.id, datetime.now(timezone.utc) - timedelta(hours=5))
    db_session.refresh(ws)

    result = fail_if_stuck(db_session, ws, stale_minutes=15)

    db_session.expire_all()
    row = db_session.query(BrandProfile).filter(BrandProfile.id == ws.id).one()
    assert result is False
    assert row.status == "confirmed"
