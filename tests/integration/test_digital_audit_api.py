"""Dijital denetim: cift baslatma, worker kesintisi, kismi sonuc, workspace izolasyonu.

Ag ve Celery YOK: `runner.run_target` ve `apply_async` sahtelenir.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.digital_audit import runner, service
from app.database.models import DigitalAudit, DigitalAuditTarget, ScoringRun

FINDING = {"section": "seo", "check": "seo.titles", "label": "Sayfa başlıkları", "state": "observed",
           "assessment": "ok", "value": {"missing": 0}, "note": None, "source": None, "limits": []}
OK_RESULT = {"status": "completed", "pages_checked": 3, "findings": [FINDING], "error_message": None}


@pytest.fixture
def dispatched(monkeypatch):
    """Celery'ye gonderilen gorevleri kaydeder (kuyruk yok)."""
    from app.tasks import digital_audit_tasks

    calls = []
    monkeypatch.setattr(
        digital_audit_tasks.run_digital_audit_task, "apply_async",
        lambda args=None, task_id=None, **kw: calls.append({"args": args, "task_id": task_id}),
    )
    return calls


def start(client, ws, **body):
    return client.post(f"/api/v1/digital-audit/audits?brand_profile_id={ws.id}",
                       json={"competitor_urls": [], **body})


def get(client, audit_id, ws):
    return client.get(f"/api/v1/digital-audit/audits/{audit_id}?brand_profile_id={ws.id}")


def expire(db, audit_id):
    audit = db.get(DigitalAudit, audit_id)
    audit.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=5)
    db.commit()


# --- baslatma --------------------------------------------------------------

def test_start_creates_audit_without_scoring_run(client, db_session, make_workspace, dispatched):
    ws = make_workspace("Marka", company_url="https://marka.test")
    res = start(client, ws, competitor_urls=["rakip1.test", "https://www.marka.test/", "rakip1.test/x"])
    assert res.status_code == 202 and res.json()["reused"] is False
    audit = db_session.get(DigitalAudit, res.json()["audit_id"])
    assert [(t.kind, t.url) for t in audit.targets] == [("own", "https://marka.test"), ("competitor", "https://rakip1.test")]
    assert dispatched == [{"args": [audit.id], "task_id": audit.task_id}]
    assert db_session.query(ScoringRun).count() == 0  # skorlamadan bagimsiz


def test_double_start_returns_same_audit_and_dispatches_once(client, db_session, make_workspace, dispatched):
    ws = make_workspace("Marka")
    first, second = start(client, ws).json(), start(client, ws).json()
    assert second["audit_id"] == first["audit_id"] and second["reused"] is True
    assert len(dispatched) == 1
    assert db_session.query(DigitalAudit).count() == 1


def test_database_refuses_second_active_audit(db_session, make_workspace):
    """Es zamanli iki istek yarisinda garanti uygulama degil, DB indeksidir."""
    ws = make_workspace("Marka")
    db_session.add(DigitalAudit(brand_profile_id=ws.id, status="running"))
    db_session.commit()
    db_session.add(DigitalAudit(brand_profile_id=ws.id, status="pending"))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_start_validates_input(client, make_workspace, dispatched):
    ws = make_workspace("Marka")
    too_many = start(client, ws, competitor_urls=[f"r{i}.test" for i in range(4)])
    assert too_many.status_code == 400
    assert start(client, ws, own_url="adres-degil").status_code == 400
    assert dispatched == []


def test_broker_failure_marks_audit_failed(client, db_session, make_workspace, monkeypatch):
    from app.tasks import digital_audit_tasks

    def boom(**kw):
        raise RuntimeError("redis down")

    monkeypatch.setattr(digital_audit_tasks.run_digital_audit_task, "apply_async", boom)
    ws = make_workspace("Marka")
    assert start(client, ws).status_code == 502
    audit = db_session.query(DigitalAudit).one()
    assert audit.status == "failed"
    # Basarisiz kayit yeni baslatmayi engellemez
    monkeypatch.setattr(digital_audit_tasks.run_digital_audit_task, "apply_async", lambda **kw: None)
    assert start(client, ws).json()["reused"] is False


# --- calistirma ------------------------------------------------------------

def test_run_completes_and_saves_findings(client, db_session, make_workspace, dispatched, monkeypatch):
    ws = make_workspace("Marka")
    audit_id = start(client, ws, competitor_urls=["rakip.test"]).json()["audit_id"]
    monkeypatch.setattr(runner, "run_target", lambda url, own: {**OK_RESULT, "status": "completed" if own else "blocked"})
    assert service.run_audit(db_session, audit_id, dispatched[0]["task_id"]) == "completed"
    body = get(client, audit_id, ws).json()
    assert body["status"] == "completed" and body["progress"] == 100
    assert [t["status"] for t in body["targets"]] == ["completed", "blocked"]
    assert body["targets"][0]["findings"] == [FINDING]
    assert body["questions"]


def test_duplicate_delivery_is_skipped(client, db_session, make_workspace, dispatched, monkeypatch):
    ws = make_workspace("Marka")
    audit_id = start(client, ws).json()["audit_id"]
    calls = []
    monkeypatch.setattr(runner, "run_target", lambda url, own: calls.append(url) or OK_RESULT)
    task_id = dispatched[0]["task_id"]
    assert service.run_audit(db_session, audit_id, task_id) == "completed"
    assert service.run_audit(db_session, audit_id, task_id) == "skipped"
    assert service.run_audit(db_session, audit_id, "baska-task") == "skipped"
    assert len(calls) == 1


def test_partial_results_survive_a_failing_target(client, db_session, make_workspace, dispatched, monkeypatch):
    ws = make_workspace("Marka")
    audit_id = start(client, ws, competitor_urls=["rakip.test"]).json()["audit_id"]

    def fake(url, own):
        if not own:
            raise RuntimeError("beklenmeyen")
        return OK_RESULT

    monkeypatch.setattr(runner, "run_target", fake)
    assert service.run_audit(db_session, audit_id, dispatched[0]["task_id"]) == "partial"
    body = get(client, audit_id, ws).json()
    assert body["status"] == "partial"
    assert body["targets"][0]["status"] == "completed" and body["targets"][0]["findings"] == [FINDING]
    assert body["targets"][1]["status"] == "failed" and body["targets"][1]["findings"] == []


def test_all_targets_failing_is_failed(client, db_session, make_workspace, dispatched, monkeypatch):
    ws = make_workspace("Marka")
    audit_id = start(client, ws).json()["audit_id"]
    monkeypatch.setattr(runner, "run_target", lambda url, own: {**OK_RESULT, "status": "failed", "findings": []})
    assert service.run_audit(db_session, audit_id, dispatched[0]["task_id"]) == "failed"


# --- worker kesintisi ------------------------------------------------------

def test_dead_worker_before_any_result_becomes_failed(client, db_session, make_workspace, dispatched):
    ws = make_workspace("Marka")
    audit_id = start(client, ws).json()["audit_id"]
    assert get(client, audit_id, ws).json()["status"] == "pending"  # kira dolmadan dokunulmaz
    expire(db_session, audit_id)
    body = get(client, audit_id, ws).json()
    assert body["status"] == "failed" and "zamanında tamamlanmadı" in body["error_message"]


def test_dead_worker_keeps_results_collected_so_far(client, db_session, make_workspace, dispatched):
    ws = make_workspace("Marka")
    audit_id = start(client, ws, competitor_urls=["rakip.test"]).json()["audit_id"]
    audit = db_session.get(DigitalAudit, audit_id)
    audit.status = "running"
    audit.targets[0].status, audit.targets[0].findings = "completed", [FINDING]
    db_session.commit()
    expire(db_session, audit_id)

    listing = client.get(f"/api/v1/digital-audit/audits?brand_profile_id={ws.id}").json()["audits"]
    assert listing[0]["status"] == "partial"
    body = get(client, audit_id, ws).json()
    assert body["targets"][0]["findings"] == [FINDING] and body["targets"][1]["status"] == "pending"
    # Takili denetim yeni baslatmayi engellemez
    again = start(client, ws).json()
    assert again["reused"] is False and again["audit_id"] != audit_id


def test_late_worker_cannot_write_after_reconcile(client, db_session, make_workspace, dispatched, monkeypatch):
    """Kirasi dolup kapatilan denetime gec kalan worker sonuc yazamaz."""
    ws = make_workspace("Marka")
    audit_id = start(client, ws).json()["audit_id"]

    def slow(url, own):
        expire(db_session, audit_id)
        service.reconcile_expired(db_session, ws.id)  # bu arada okuma ani uzlastirmasi calisti
        return OK_RESULT

    monkeypatch.setattr(runner, "run_target", slow)
    assert service.run_audit(db_session, audit_id, dispatched[0]["task_id"]) == "superseded"
    db_session.expire_all()
    audit = db_session.get(DigitalAudit, audit_id)
    assert audit.status == "failed"
    assert audit.targets[0].status == "pending" and audit.targets[0].findings is None


# --- workspace izolasyonu --------------------------------------------------

def test_other_workspace_cannot_see_or_change_audit(client, db_session, make_workspace, dispatched):
    owner, other = make_workspace("Sahip"), make_workspace("Diger")
    audit_id = start(client, owner).json()["audit_id"]
    assert get(client, audit_id, other).status_code == 404
    put = client.put(f"/api/v1/digital-audit/audits/{audit_id}/answers?brand_profile_id={other.id}",
                     json={"answers": {"crm": {"value": "Evet"}}})
    assert put.status_code == 404
    assert client.get(f"/api/v1/digital-audit/audits?brand_profile_id={other.id}").json() == {"audits": []}
    assert db_session.get(DigitalAudit, audit_id).answers is None
    # Digerinin aktif denetimi bu workspace'in baslatmasini etkilemez
    assert start(client, other).json()["reused"] is False


def test_missing_or_archived_workspace_is_404(client, db_session, make_workspace, dispatched):
    ws = make_workspace("Marka")
    audit_id = start(client, ws).json()["audit_id"]
    assert client.get("/api/v1/digital-audit/audits?brand_profile_id=999999").status_code == 404
    ws.deleted_at = datetime.now(timezone.utc)
    db_session.commit()
    assert get(client, audit_id, ws).status_code == 404
    assert start(client, ws).status_code == 404


def test_workspace_delete_cascades(db_session, make_workspace, client, dispatched):
    ws = make_workspace("Marka")
    start(client, ws)
    db_session.delete(ws)
    db_session.commit()
    assert db_session.query(DigitalAudit).count() == 0 and db_session.query(DigitalAuditTarget).count() == 0


# --- soru formu ------------------------------------------------------------

def test_answers_are_stored_apart_from_findings(client, db_session, make_workspace, dispatched, monkeypatch):
    ws = make_workspace("Marka")
    audit_id = start(client, ws).json()["audit_id"]
    monkeypatch.setattr(runner, "run_target", lambda url, own: OK_RESULT)
    service.run_audit(db_session, audit_id, dispatched[0]["task_id"])
    url = f"/api/v1/digital-audit/audits/{audit_id}/answers?brand_profile_id={ws.id}"
    res = client.put(url, json={"answers": {"crm": {"value": "Evet", "note": "HubSpot"}, "remarketing": {"value": ""}}})
    assert res.status_code == 200 and res.json()["answers"] == {"crm": {"value": "Evet", "note": "HubSpot"}}
    body = get(client, audit_id, ws).json()
    assert body["answers"]["crm"]["note"] == "HubSpot"
    assert body["targets"][0]["findings"] == [FINDING]  # otomatik bulgu degismedi
    assert client.put(url, json={"answers": {"olmayan_soru": {"value": "x"}}}).status_code == 400
