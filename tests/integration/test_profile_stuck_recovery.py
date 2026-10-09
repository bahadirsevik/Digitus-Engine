"""Takılı profil kurtarma — end-to-end (lazy read-time janitör).

Bug: site profil analizi FastAPI BackgroundTasks ile web process içinde
koşar. App restart ederse in-process görev sessizce ölür ama BrandProfile
`running`/`pending` statüsünde kalır. Startup janitörü (fail_stuck_profiles)
tek seferlik çalıştığı ve TAZE satırları kasıtlı atladığı için, restart eşik
anında olmazsa (ör. 10:00 running / 10:02 restart) satır bir daha asla
taranmaz — kullanıcı sonsuza dek "işleniyor" ekranında kalır.

Bu test, frontend'in loading statülerinde poll ettiği
GET /brand-profile/workspaces/{id} okumasının (BrandProfile.tsx →
workspaceApi.get) bu satırı OKUMA anında failed'a çevirdiğini ve kullanıcının
"yeniden dene" mesajını gördüğünü uçtan uca doğrular.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.core.site_analyzer.stuck_janitor import STUCK_ERROR_MESSAGE
from app.database.models import BrandProfile


def _set_updated_at(db_session, profile_id: int, when: datetime) -> None:
    # onupdate tetiklenmesin diye ham SQL ile geçmişe çek (test_profile_janitor.py ile aynı desen)
    db_session.execute(
        text("UPDATE brand_profiles SET updated_at = :ts WHERE id = :pid"),
        {"ts": when, "pid": profile_id},
    )
    db_session.commit()


def test_stuck_running_workspace_recovers_on_read(client, db_session, make_workspace):
    """Restart sonrası takılı kalan running workspace, GET okumasında failed'a döner."""
    ws = make_workspace(name="stuck-recovery", status="running")
    # Startup janitörünün eşiği geçtiği (10 dk önce restart oldu, eşik 15 dk)
    # senaryosunu simüle et: satır eşikten (PROFILE_STALE_MINUTES=15) eski.
    _set_updated_at(db_session, ws.id, datetime.now(timezone.utc) - timedelta(minutes=45))

    res = client.get(f"/api/v1/brand-profile/workspaces/{ws.id}")

    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "failed"

    # WorkspaceResponse şeması error_message taşımıyor; retry mesajı DB
    # satırında doğrulanır (BrandProfileResponse'un okuduğu aynı alan).
    # client aynı db_session'ı kullanıyor; identity map bayat kalmasın diye
    # expire_all ile taze satırı zorla (bkz. test_profile_janitor.py deseni).
    db_session.expire_all()
    row = db_session.query(BrandProfile).filter(BrandProfile.id == ws.id).one()
    assert row.error_message == STUCK_ERROR_MESSAGE


def test_fresh_running_workspace_stays_running_on_read(client, db_session, make_workspace):
    """Henüz eşiği geçmemiş running workspace okuma sırasında bozulmamalı."""
    ws = make_workspace(name="fresh-running", status="running")
    # updated_at şimdi (make_workspace commit'i onupdate'i tetikledi) — eşik altında

    res = client.get(f"/api/v1/brand-profile/workspaces/{ws.id}")

    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "running"


def test_stuck_profile_recovers_via_run_profile_endpoint(
    client, db_session, make_workspace, make_scoring_run
):
    """Aynı kurtarma GET /runs/{run_id}/profile üzerinden de çalışmalı."""
    ws = make_workspace(name="stuck-recovery-run", status="running")
    run = make_scoring_run(brand_profile_id=ws.id)
    _set_updated_at(db_session, ws.id, datetime.now(timezone.utc) - timedelta(minutes=45))

    res = client.get(
        f"/api/v1/brand-profile/runs/{run.id}/profile?brand_profile_id={ws.id}"
    )

    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "failed"
    assert body["error_message"] == STUCK_ERROR_MESSAGE
