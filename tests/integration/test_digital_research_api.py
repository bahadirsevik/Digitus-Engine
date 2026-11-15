"""Arastirma (Faz 2): cift baslatma, worker kesintisi, kismi sonuc, izolasyon, brief ve rapor.

Ag, Celery ve yapay zeka YOK: `apply_async`, kaynak indirme ve AI sahtelenir.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.core.digital_audit import research, research_service, source_check
from app.database.models import DigitalAudit, DigitalAuditTarget, DigitalResearch, ScoringRun
from app.generators.ai_service import GroundedCompletion

SOURCE_TEXT = "Türkiye'de 2023 yılında 45 000 diş hekimi görev yaptı. Randevu almak çok zor diyen hastalar var."
SECTOR = {"definition": ["Özel diş sağlığı"], "services": ["İmplant"], "growth": "Talep artıyor",
          "developments": [], "restrictions": [{"text": "Ücretli öne çıkarma yasak", "source_name": "Yönetmelik"}],
          "directories": [], "not_found": [],
          "claims": [{"claim": "Diş hekimi sayısı", "value": "45.000", "unit": "kişi", "year": "2023",
                      "geography": "Türkiye", "source_name": "TDB"},
                     {"claim": "Pazar", "value": "3,2 milyar", "unit": "USD", "year": "2024",
                      "geography": "Türkiye", "source_name": "Blog"}]}
AUDIENCE = {"needs": [{"theme": "Randevu bulma", "quote": "Randevu almak çok zor", "source_name": "Forum"}],
            "questions": ["İmplant ağrır mı?"], "hesitations": [], "trust": ["Hekim deneyimi"], "not_found": []}
SYNTHESIS = {"segments": [{"name": "Acil hasta", "need": "Hızlı randevu", "stage": "karar", "evidence": "e",
                           "assumption": "a", "value_proposition": "Aynı hafta randevu", "messages": ["Bugün arayın"]},
                          {"name": "Araştıran hasta", "need": "Bilgi", "stage": "değerlendirme", "evidence": "e",
                           "assumption": "a", "value_proposition": "Açık bilgi", "messages": ["Sorularınızı yanıtlıyoruz"]}],
             "opportunities": [{"text": "Soru içerikleri", "evidence": "soru aramaları"}],
             "google_ads": {"keyword_groups": [], "negatives": [], "note": ""}, "meta_audiences": []}
BRIEFS = {"ad_ideas": [{"segment": "Acil hasta", "channel": "Google Arama Ağı", "insight": "i", "offer": "o",
                        "message": "Aynı hafta randevu", "cta": "Ara", "test_hypothesis": "h"}],
          "creative_briefs": [{"segment": "Acil hasta", "channel": "Instagram / Facebook", "format": "9:16",
                               "duration": "15 sn", "hook": "Dişiniz mi ağrıyor?", "visual_direction": "klinik",
                               "shot_list": ["giriş"], "copy": "Randevu alın", "proof_needed": ["hekim onayı"],
                               "ab_variant": "soru ile aç"}]}


PAYLOADS = {"audit_sector_research": SECTOR, "audit_audience_research": AUDIENCE,
            "audit_synthesis": SYNTHESIS, "audit_briefs": BRIEFS}


class FakeAI:
    """Aramali cagri serbest metin + kaynak dondurur; JSON cagrisi asamanin yukunu."""

    def __init__(self, no_sources=(), fail_on=()):
        self.no_sources, self.fail_on = set(no_sources), set(fail_on)
        self.grounded_count = 0

    def for_stage(self, stage, **kw):
        self.stage = stage
        return self

    def complete_grounded(self, prompt, max_tokens=4000, response_schema=None, **kw):
        self.grounded_count += 1
        if self.stage in self.fail_on:
            raise RuntimeError("provider down")
        if self.stage in self.no_sources:  # model aramadi
            return GroundedCompletion(text="hafızadan", search_queries=[], evidence_urls=[])
        return GroundedCompletion(text="araştırma metni", search_queries=["q"],
                                  evidence_urls=[f"https://kaynak.test/{self.stage}"])

    def complete_json(self, prompt, max_tokens=6000, response_schema=None, **kw):
        if self.stage in self.fail_on:
            raise RuntimeError("provider down")
        return json.dumps(PAYLOADS[self.stage], ensure_ascii=False)


@pytest.fixture
def dispatched(monkeypatch):
    from app.tasks import digital_audit_tasks

    calls = []
    monkeypatch.setattr(
        digital_audit_tasks.run_digital_research_task, "apply_async",
        lambda args=None, task_id=None, **kw: calls.append({"args": args, "task_id": task_id}),
    )
    return calls


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """Kaynak indirme ve Google Ads sahte: hicbir test aga cikmaz."""
    monkeypatch.setattr(source_check, "fetch_sources", lambda urls, budget: [
        {"url": u, "final_url": u, "kind": "html", "text": SOURCE_TEXT} for u in dict.fromkeys(urls)])
    monkeypatch.setattr(research, "collect_demand",
                        lambda db, ws, ads=None: {"status": "unavailable", "reason": "Google Ads bağlantısı tanımlı değil."})


def start(client, ws, **body):
    return client.post(f"/api/v1/digital-audit/research?brand_profile_id={ws.id}",
                       json={"sector": "Diş kliniği", "geography": "İstanbul", **body})


def get(client, report_id, ws):
    return client.get(f"/api/v1/digital-audit/research/{report_id}?brand_profile_id={ws.id}")


def run(db, dispatched, ai=None, index=-1):
    call = dispatched[index]
    return research_service.run_research(db, call["args"][0], call["task_id"], ai or FakeAI())


def expire(db, report_id):
    report = db.get(DigitalResearch, report_id)
    report.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=5)
    db.commit()


# --- baslatma --------------------------------------------------------------

def test_start_needs_no_scoring_run_and_links_latest_audit(client, db_session, make_workspace, dispatched):
    ws = make_workspace("Marka")
    audit = DigitalAudit(brand_profile_id=ws.id, status="completed")
    db_session.add(audit)
    db_session.commit()
    res = start(client, ws)
    assert res.status_code == 202 and res.json()["reused"] is False
    report = db_session.get(DigitalResearch, res.json()["research_id"])
    assert report.audit_id == audit.id and report.inputs["sector"] == "Diş kliniği"
    assert dispatched == [{"args": [report.id], "task_id": report.task_id}]
    assert db_session.query(ScoringRun).count() == 0


def test_double_start_returns_same_report_and_dispatches_once(client, db_session, make_workspace, dispatched):
    ws = make_workspace("Marka")
    first, second = start(client, ws).json(), start(client, ws).json()
    assert second["research_id"] == first["research_id"] and second["reused"] is True
    assert len(dispatched) == 1 and db_session.query(DigitalResearch).count() == 1


def test_start_validates_input(client, make_workspace, dispatched):
    ws = make_workspace("Marka")
    assert start(client, ws, sector="  ").status_code in (400, 422)
    assert dispatched == []


def test_broker_failure_marks_report_failed(client, db_session, make_workspace, monkeypatch):
    from app.tasks import digital_audit_tasks

    def boom(**kw):
        raise RuntimeError("redis down")

    monkeypatch.setattr(digital_audit_tasks.run_digital_research_task, "apply_async", boom)
    ws = make_workspace("Marka")
    assert start(client, ws).status_code == 502
    assert db_session.query(DigitalResearch).one().status == "failed"


# --- calistirma ------------------------------------------------------------

def test_run_completes_with_verified_and_unverified_claims(client, db_session, make_workspace, dispatched):
    ws = make_workspace("Marka")
    report_id = start(client, ws).json()["research_id"]
    assert run(db_session, dispatched) == "completed"
    body = get(client, report_id, ws).json()
    assert body["status"] == "completed" and body["progress"] == 100
    sector = body["sections"]["sector"]
    assert sector["verified"] is True
    assert [c["check"]["status"] for c in sector["claims"]] == ["found", "not_found"]
    assert sector["claims"][0]["check"]["context"]
    assert body["sections"]["audience"]["needs"][0]["quote_found"] is True
    assert body["sections"]["demand"]["status"] == "unavailable"  # Google Ads yoksa arastirma yine tamamlanir
    assert len(body["sections"]["synthesis"]["segments"]) == 2
    assert body["usage"] == {"grounded_calls": 2, "json_calls": 3, "search_queries": 2, "sources_fetched": 2}


def test_duplicate_delivery_is_skipped(client, db_session, make_workspace, dispatched):
    ws = make_workspace("Marka")
    start(client, ws)
    ai = FakeAI()
    assert run(db_session, dispatched, ai) == "completed"
    assert run(db_session, dispatched, ai) == "skipped"
    assert ai.grounded_count == 2


def test_search_without_sources_fails_that_section_only(client, db_session, make_workspace, dispatched):
    """Kaynak donmeyen aramali cagri basarisiz sayilir; diger bolumler korunur."""
    ws = make_workspace("Marka")
    report_id = start(client, ws).json()["research_id"]
    ai = FakeAI(no_sources={"audit_sector_research"})  # sektor: iki deneme de kaynaksiz
    assert run(db_session, dispatched, ai) == "partial"
    sections = get(client, report_id, ws).json()["sections"]
    assert sections["sector"]["status"] == "failed" and "kaynak" in sections["sector"]["error"].lower()
    assert sections["audience"]["status"] == "ok" and sections["synthesis"]["status"] == "ok"


def test_all_research_failing_is_failed_without_synthesis(client, db_session, make_workspace, dispatched):
    ws = make_workspace("Marka")
    report_id = start(client, ws).json()["research_id"]
    ai = FakeAI(fail_on={"audit_sector_research", "audit_audience_research"})
    assert run(db_session, dispatched, ai) == "failed"
    body = get(client, report_id, ws).json()
    assert body["status"] == "failed" and body["sections"]["synthesis"]["status"] == "failed"
    assert ai.grounded_count == 4  # her arastirma bir kez yeniden denendi
    assert "provider down" not in json.dumps(body)  # ic hata metni kullaniciya sizmaz


# --- worker kesintisi ------------------------------------------------------

def test_stale_report_without_sections_becomes_failed(client, db_session, make_workspace, dispatched):
    ws = make_workspace("Marka")
    report_id = start(client, ws).json()["research_id"]
    expire(db_session, report_id)
    body = get(client, report_id, ws).json()
    assert body["status"] == "failed" and "zamanında" in body["error_message"]
    assert start(client, ws).json()["reused"] is False  # yenisi baslatilabilir


def test_worker_death_keeps_saved_sections_as_partial(client, db_session, make_workspace, dispatched, monkeypatch):
    """Worker sentezde olurse sektor ve hedef kitle bolumleri kaybolmaz."""
    ws = make_workspace("Marka")
    report_id = start(client, ws).json()["research_id"]

    class WorkerDied(BaseException):
        pass

    def die(*args, **kwargs):
        raise WorkerDied()

    monkeypatch.setattr(research, "synthesize", die)
    with pytest.raises(WorkerDied):
        run(db_session, dispatched)
    db_session.rollback()
    assert db_session.get(DigitalResearch, report_id).status == "running"
    expire(db_session, report_id)
    body = get(client, report_id, ws).json()
    assert body["status"] == "partial"
    assert body["sections"]["sector"]["status"] == "ok" and body["sections"]["audience"]["status"] == "ok"
    assert "synthesis" not in body["sections"]


def test_late_worker_cannot_write_after_reconcile(client, db_session, make_workspace, dispatched, monkeypatch):
    ws = make_workspace("Marka")
    report_id = start(client, ws).json()["research_id"]
    real = research.research_sector

    def slow_sector(ai, inputs, usage):
        # Worker sektor aramasindayken kira doluyor ve okuma ani raporu kapatiyor
        expire(db_session, report_id)
        research_service.reconcile_expired(db_session, ws.id)
        return real(ai, inputs, usage)

    monkeypatch.setattr(research, "research_sector", slow_sector)
    assert run(db_session, dispatched) == "superseded"
    report = db_session.get(DigitalResearch, report_id)
    db_session.refresh(report)
    assert report.status == "failed" and not (report.sections or {}).get("sector")


# --- iddia cikarma + briefler ----------------------------------------------

def completed_report(client, db_session, ws, dispatched):
    report_id = start(client, ws).json()["research_id"]
    assert run(db_session, dispatched) == "completed"
    return report_id


def test_analyst_can_override_a_claim_decision(client, db_session, make_workspace, dispatched):
    ws = make_workspace("Marka")
    report_id = completed_report(client, db_session, ws, dispatched)
    url = f"/api/v1/digital-audit/research/{report_id}/claims?brand_profile_id={ws.id}"
    assert client.put(url, json={"overrides": ["sector:9"]}).status_code == 400
    assert client.put(url, json={"overrides": ["sector:1"]}).status_code == 400  # kaynakta bulunamayan alinamaz
    assert client.put(url, json={"overrides": ["sector:0"]}).json() == {"claim_overrides": ["sector:0"]}
    report = db_session.get(DigitalResearch, report_id)
    db_session.refresh(report)
    assert research.verified_claims(report.sections["sector"], report.claim_overrides) == []
    assert report.sections["sector"]["claims"][0]["check"]["status"] == "found"  # bulgu degismez


def test_brief_flow(client, db_session, make_workspace, dispatched):
    ws = make_workspace("Marka")
    report_id = completed_report(client, db_session, ws, dispatched)
    url = f"/api/v1/digital-audit/research/{report_id}/briefs?brand_profile_id={ws.id}"
    assert client.post(url, json={"segments": [5], "channels": ["meta"]}).status_code == 400
    assert client.post(url, json={"segments": [0], "channels": ["faks"]}).status_code == 400
    res = client.post(url, json={"segments": [0], "channels": ["google_search", "meta"]})
    assert res.status_code == 202 and res.json()["status"] == "pending"
    assert len(dispatched) == 2
    assert client.post(url, json={"segments": [0], "channels": ["meta"]}).status_code == 409  # brief suruyor
    assert run(db_session, dispatched) == "completed"
    body = get(client, report_id, ws).json()
    assert body["status"] == "completed" and body["briefs"]["status"] == "ok"
    assert body["briefs"]["selection"] == {"segments": [0], "channels": ["google_search", "meta"]}
    # Sektorde reklam kisiti bulundu: her brief uyari tasir
    assert body["briefs"]["ad_ideas"][0]["restriction_note"]
    assert body["briefs"]["creative_briefs"][0]["restriction_note"]
    assert body["sections"]["sector"]["status"] == "ok"  # arastirma bolumleri yerinde


def test_failed_brief_generation_keeps_research(client, db_session, make_workspace, dispatched):
    ws = make_workspace("Marka")
    report_id = completed_report(client, db_session, ws, dispatched)
    client.post(f"/api/v1/digital-audit/research/{report_id}/briefs?brand_profile_id={ws.id}",
                json={"segments": [0], "channels": ["meta"]})
    assert run(db_session, dispatched, FakeAI(fail_on={"audit_briefs"})) == "completed"
    body = get(client, report_id, ws).json()
    assert body["briefs"]["status"] == "failed" and len(body["sections"]["synthesis"]["segments"]) == 2


# --- rapor dosyasi ----------------------------------------------------------

def test_export_without_scoring_run(client, db_session, make_workspace, dispatched):
    ws = make_workspace("Marka")
    audit = DigitalAudit(brand_profile_id=ws.id, status="completed", answers={"crm": {"value": "Evet", "note": None}})
    audit.targets = [DigitalAuditTarget(position=0, kind="own", url="https://marka.test", status="completed", findings=[
        {"section": "seo", "check": "seo.titles", "label": "Sayfa başlıkları", "state": "observed",
         "assessment": "ok", "value": None, "note": None, "source": None, "limits": []}])]
    db_session.add(audit)
    db_session.commit()
    report_id = completed_report(client, db_session, ws, dispatched)
    base = f"/api/v1/digital-audit/export?brand_profile_id={ws.id}"
    docx = client.get(f"{base}&audit_id={audit.id}&research_id={report_id}&format=docx")
    assert docx.status_code == 200 and docx.content.startswith(b"PK")
    assert "attachment" in docx.headers["content-disposition"]
    pdf = client.get(f"{base}&research_id={report_id}&format=pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert client.get(base).status_code == 400
    assert client.get(f"{base}&research_id={report_id}&format=xlsx").status_code == 422
    assert db_session.query(ScoringRun).count() == 0


# --- workspace izolasyonu ---------------------------------------------------

def test_other_workspace_cannot_reach_report(client, db_session, make_workspace, dispatched):
    ws_a, ws_b = make_workspace("A"), make_workspace("B")
    report_id = completed_report(client, db_session, ws_a, dispatched)
    other = f"brand_profile_id={ws_b.id}"
    assert client.get(f"/api/v1/digital-audit/research/{report_id}?{other}").status_code == 404
    assert client.put(f"/api/v1/digital-audit/research/{report_id}/claims?{other}", json={"overrides": []}).status_code == 404
    assert client.post(f"/api/v1/digital-audit/research/{report_id}/briefs?{other}",
                       json={"segments": [0], "channels": ["meta"]}).status_code == 404
    assert client.get(f"/api/v1/digital-audit/export?{other}&research_id={report_id}").status_code == 404
    assert client.get(f"/api/v1/digital-audit/research?{other}").json() == {"reports": []}
    assert client.get("/api/v1/digital-audit/research?brand_profile_id=999999").status_code == 404
    assert start(client, ws_b).json()["reused"] is False  # A'nin arastirmasi B'yi engellemez
