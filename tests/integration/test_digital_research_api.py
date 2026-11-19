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
        self.prompts = {}

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
        self.prompts[self.stage] = prompt
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
    assert client.put(url, json={"overrides": ["sector:0"]}).json()["claim_overrides"] == ["sector:0"]
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


# --- karar degisince bayatlama ---------------------------------------------

def _urls(report_id, ws):
    base = f"/api/v1/digital-audit/research/{report_id}"
    return {name: f"{base}/{name}?brand_profile_id={ws.id}" for name in ("claims", "briefs", "synthesis")}


def _blocks(db, ws, report_id):
    from app.core.digital_audit import report_doc

    db.expire_all()
    return repr(report_doc.build_blocks(ws, None, db.get(DigitalResearch, report_id)))


def test_rejected_claim_makes_synthesis_stale_until_refreshed(client, db_session, make_workspace, dispatched):
    ws = make_workspace("Marka")
    report_id = completed_report(client, db_session, ws, dispatched)
    url = _urls(report_id, ws)
    assert get(client, report_id, ws).json()["synthesis_stale"] is False
    assert "Acil hasta" in _blocks(db_session, ws, report_id)

    res = client.put(url["claims"], json={"overrides": ["sector:0"]}).json()
    assert res["synthesis_stale"] is True
    # Eski sentez rapora girmez, uzerinden brief uretilemez
    assert "Acil hasta" not in _blocks(db_session, ws, report_id)
    assert client.post(url["briefs"], json={"segments": [0], "channels": ["meta"]}).status_code == 409
    # Karar geri alinirsa sentez yeniden gecerlidir
    assert client.put(url["claims"], json={"overrides": []}).json()["synthesis_stale"] is False
    client.put(url["claims"], json={"overrides": ["sector:0"]})

    assert client.post(url["synthesis"]).status_code == 202
    assert client.post(url["synthesis"]).status_code == 409  # yenileme suruyor
    ai = FakeAI()
    assert run(db_session, dispatched, ai) == "completed"
    assert "45.000" not in ai.prompts["audit_synthesis"]  # reddedilen rakam modele gitmez
    assert ai.grounded_count == 0  # arastirma tekrarlanmaz, yalniz sentez
    body = get(client, report_id, ws).json()
    assert body["synthesis_stale"] is False and body["sections"]["sector"]["status"] == "ok"
    assert "Acil hasta" in _blocks(db_session, ws, report_id)


def test_refreshing_synthesis_drops_old_briefs(client, db_session, make_workspace, dispatched):
    ws = make_workspace("Marka")
    report_id = completed_report(client, db_session, ws, dispatched)
    url = _urls(report_id, ws)
    client.post(url["briefs"], json={"segments": [0], "channels": ["meta"]})
    assert run(db_session, dispatched) == "completed"
    client.put(url["claims"], json={"overrides": ["sector:0"]})
    body = get(client, report_id, ws).json()
    assert body["briefs_stale"] is True and "Dişiniz mi ağrıyor?" not in _blocks(db_session, ws, report_id)
    client.post(url["synthesis"])
    assert run(db_session, dispatched) == "completed"
    assert get(client, report_id, ws).json()["briefs"] is None


def test_decision_changed_during_generation_leaves_result_stale(client, db_session, make_workspace, dispatched,
                                                                monkeypatch):
    """Brief uretilirken analist karar degistirirse, eski kararla uretilen sonuc guncel sayilmaz."""
    ws = make_workspace("Marka")
    report_id = completed_report(client, db_session, ws, dispatched)
    url = _urls(report_id, ws)
    client.post(url["briefs"], json={"segments": [0], "channels": ["meta"]})
    real = research.generate_briefs

    def slow_briefs(*args, **kwargs):
        assert client.put(url["claims"], json={"overrides": ["sector:0"]}).status_code == 200
        return real(*args, **kwargs)

    monkeypatch.setattr(research, "generate_briefs", slow_briefs)
    ai = FakeAI()
    assert run(db_session, dispatched, ai) == "completed"
    assert "45.000" in ai.prompts["audit_briefs"]  # uretim eski kararla yapildi
    body = get(client, report_id, ws).json()
    assert body["briefs"]["status"] == "ok" and body["briefs_stale"] is True and body["synthesis_stale"] is True
    assert "Dişiniz mi ağrıyor?" not in _blocks(db_session, ws, report_id)


def test_failed_synthesis_can_be_retried(client, db_session, make_workspace, dispatched):
    ws = make_workspace("Marka")
    report_id = start(client, ws).json()["research_id"]
    assert run(db_session, dispatched, FakeAI(fail_on={"audit_synthesis"})) == "partial"
    assert client.post(_urls(report_id, ws)["synthesis"]).status_code == 202
    assert run(db_session, dispatched) == "completed"
    assert len(get(client, report_id, ws).json()["sections"]["synthesis"]["segments"]) == 2


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
    assert client.post(f"/api/v1/digital-audit/research/{report_id}/synthesis?{other}").status_code == 404
    assert client.get(f"/api/v1/digital-audit/export?{other}&research_id={report_id}").status_code == 404
    assert client.get(f"/api/v1/digital-audit/research?{other}").json() == {"reports": []}
    assert client.get("/api/v1/digital-audit/research?brand_profile_id=999999").status_code == 404
    assert start(client, ws_b).json()["reused"] is False  # A'nin arastirmasi B'yi engellemez


def test_changed_brief_or_evidence_makes_synthesis_stale(client, db_session, make_workspace, dispatched):
    ws = make_workspace("Marka")
    audit = DigitalAudit(brand_profile_id=ws.id, status="completed",
                         answers={"business_model": {"value": "E-ticaret", "note": None},
                                  "crm": {"value": "Evet", "note": None}})
    db_session.add(audit)
    db_session.commit()
    report_id = start(client, ws).json()["research_id"]
    ai = FakeAI()
    assert run(db_session, dispatched, ai) == "completed"
    url = _urls(report_id, ws)
    assert get(client, report_id, ws).json()["synthesis_stale"] is False
    # Brif, beyan ve dogrulanmis veri modele AYRI etiketlerle gider
    prompt = ai.prompts["audit_synthesis"]
    assert '"analist_brifi": {"İş modeli nedir?": {"cevap": "E-ticaret"}}' in prompt
    assert '"musteri_beyani": {"CRM ya da talep takip yöntemi var mı?": {"cevap": "Evet"}}' in prompt
    assert "dogrulanmis_hesap_verisi\"" not in prompt

    answers = f"/api/v1/digital-audit/audits/{audit.id}/answers?brand_profile_id={ws.id}"
    evidence = {"source": "HubSpot paneli", "checked_at": "2026-11-17", "verified_by": "Analist A",
                "account": "hub-123"}
    body = {"business_model": {"value": "E-ticaret"}, "crm": {"value": "Evet", "evidence": evidence}}
    assert client.put(answers, json={"answers": body}).status_code == 200
    stale = get(client, report_id, ws).json()
    assert stale["synthesis_stale"] is True
    assert "Acil hasta" not in _blocks(db_session, ws, report_id)
    assert client.post(url["briefs"], json={"segments": [0], "channels": ["meta"]}).status_code == 409

    assert client.post(url["synthesis"]).status_code == 202
    ai = FakeAI()
    assert run(db_session, dispatched, ai) == "completed"
    assert get(client, report_id, ws).json()["synthesis_stale"] is False
    prompt = ai.prompts["audit_synthesis"]
    assert '"dogrulanmis_hesap_verisi": {"CRM ya da talep takip yöntemi var mı?"' in prompt
    assert "HubSpot paneli" in prompt
    # Dogrulayan kisi ve hesap bilgisi yapay zekaya gitmez
    assert "Analist A" not in prompt and "hub-123" not in prompt


def test_output_without_fingerprint_needs_refresh(client, db_session, make_workspace, dispatched):
    ws = make_workspace("Marka")
    db_session.add(DigitalAudit(brand_profile_id=ws.id, status="completed"))
    db_session.commit()
    report_id = completed_report(client, db_session, ws, dispatched)
    report = db_session.get(DigitalResearch, report_id)
    old = dict(report.sections)
    old["synthesis"] = {k: v for k, v in old["synthesis"].items() if k != "answers_sha"}  # eski kayit
    report.sections = old
    db_session.commit()
    assert get(client, report_id, ws).json()["synthesis_stale"] is True
    assert client.post(_urls(report_id, ws)["synthesis"]).status_code == 202
    ai = FakeAI()
    assert run(db_session, dispatched, ai) == "completed"
    assert ai.grounded_count == 0  # arastirma tekrarlanmaz
    assert get(client, report_id, ws).json()["synthesis_stale"] is False


def test_brief_made_with_other_answers_stays_stale_after_revert(client, db_session, make_workspace, dispatched):
    ws = make_workspace("Marka")
    brief_a = {"business_model": {"value": "E-ticaret", "note": None}}
    audit = DigitalAudit(brand_profile_id=ws.id, status="completed", answers=brief_a)
    db_session.add(audit)
    db_session.commit()
    report_id = completed_report(client, db_session, ws, dispatched)
    url = _urls(report_id, ws)
    answers = f"/api/v1/digital-audit/audits/{audit.id}/answers?brand_profile_id={ws.id}"
    assert client.post(url["briefs"], json={"segments": [0], "channels": ["meta"]}).status_code == 202
    # Brief kuyruktayken brif B olur, worker B ile uretir
    assert client.put(answers, json={"answers": {"business_model": {"value": "B2B satış"}}}).status_code == 200
    assert run(db_session, dispatched) == "completed"
    # Brif A'ya doner: sentez yeniden guncel, B ile uretilmis brief degil
    assert client.put(answers, json={"answers": brief_a}).status_code == 200
    body = get(client, report_id, ws).json()
    assert body["synthesis_stale"] is False and body["briefs_stale"] is True
    assert "Dişiniz mi ağrıyor?" not in _blocks(db_session, ws, report_id)


# --- karar odakli rapor (strateji) -------------------------------------------

def _finding(check, label, assessment, state="observed", value=None):
    return {"section": check.split(".")[0], "check": check, "label": label, "state": state,
            "assessment": assessment, "value": value, "note": None, "source": None, "limits": []}


def _ref(cat, kind):
    return next(c["id"] for c in cat if c["kind"].startswith(kind))


def _strategy_payload(cat):
    own, rival, seg = _ref(cat, "Ölçüm (marka"), _ref(cat, "Rakip gözlemi"), _ref(cat, "Hipotez")
    return {
        "summary": [{"finding": "Sayfa başlıkları eksik", "meaning": "Aramada görünürlük kaybı", "refs": [own]},
                    {"finding": "Uydurma bulgu", "meaning": "dayanaksız", "refs": ["B999"]}],
        "section_notes": [{"section": "seo", "finding": "Başlık eksiği var", "impact": "Tıklama düşer",
                           "action": "Başlıkları tamamla", "refs": [own]}],
        "competition": [{"text": "Rakipte başlıklar tam", "refs": [own, rival]}],
        "swot": {"strengths": [], "weaknesses": [{"text": "Başlıklar eksik", "refs": [own]}],
                 "opportunities": [{"text": "Acil hasta segmenti", "refs": [seg]}], "threats": []},
        "opportunities": [{"text": "Soru içerikleri", "type": "içerik", "contribution": "Organik talep",
                           "priority_reason": "Düşük maliyet", "refs": [seg]}],
        "decisions": [{"question": "Blog açılsın mı?", "options": ["Evet", "Hayır"], "needed_info": "Kaynak planı",
                       "owner_role": "Müşteri", "refs": [seg]}],
        "actions": [
            {"period": "0-30", "action": "Sayfa başlıklarını tamamla", "priority": "P1", "priority_reason": "Temel",
             "dependency": "", "owner_role": "SEO uzmanı", "effort": "1-2 gün", "kpi": "Eksik başlık sayısı",
             "contribution": "Görünürlük", "kind": "seo", "channel": "google_search", "publication": "", "refs": [own]},
            {"period": "30-60", "action": "Acil hasta için arama reklamı aç", "priority": "P2",
             "priority_reason": "Talep var", "dependency": "", "owner_role": "Reklam uzmanı", "effort": "3 gün",
             "kpi": "Form sayısı", "contribution": "Talep", "kind": "reklam", "channel": "google_search", "publication": "sponsorlu",
             "refs": [seg]},
            {"period": "0-30", "action": "Dayanaksız aksiyon", "priority": "P1", "priority_reason": "", "dependency": "",
             "owner_role": "", "effort": "", "kpi": "", "contribution": "", "kind": "teknik", "channel": "", "publication": "", "refs": []},
        ],
    }


def strategy_ready(client, db, ws, dispatched, monkeypatch, answers=None):
    from app.core.digital_audit import strategy

    audit = DigitalAudit(brand_profile_id=ws.id, status="completed", answers=answers or {
        "business_model": {"value": "Yerel hizmet / mağaza", "note": None},
        "main_conversion": {"value": "Randevu formu", "note": None}})
    audit.targets = [
        DigitalAuditTarget(position=0, kind="own", url="https://marka.test", status="completed", pages_checked=10,
                           findings=[_finding("seo.titles", "Sayfa başlıkları", "warning", value={"missing": 3})]),
        DigitalAuditTarget(position=1, kind="competitor", url="https://rakip.test", status="completed", pages_checked=10,
                           findings=[_finding("seo.titles", "Sayfa başlıkları", "ok", value={"missing": 0})])]
    db.add(audit)
    db.commit()
    report_id = completed_report(client, db, ws, dispatched)
    cat = strategy.catalogue(ws, audit, db.get(DigitalResearch, report_id))
    monkeypatch.setitem(PAYLOADS, "audit_strategy", _strategy_payload(cat))
    url = f"/api/v1/digital-audit/research/{report_id}/strategy?brand_profile_id={ws.id}"
    assert client.post(url).status_code == 202
    assert client.post(url).status_code == 409  # cift tiklama ikinci ucretli gorevi acmaz
    ai = FakeAI()
    assert run(db, dispatched, ai) == "completed"
    return audit, report_id, url, ai


def _view_text(db, ws, report_id, view):
    from app.core.digital_audit import report_doc

    db.expire_all()
    report = db.get(DigitalResearch, report_id)
    audit = db.get(DigitalAudit, report.audit_id) if report.audit_id else None
    return repr(report_doc.build_blocks(ws, audit, report, view))


def test_strategy_keeps_only_grounded_items_and_holds_restricted_ads(client, db_session, make_workspace,
                                                                      dispatched, monkeypatch):
    ws = make_workspace("Marka")
    audit, report_id, url, ai = strategy_ready(client, db_session, ws, dispatched, monkeypatch)
    body = get(client, report_id, ws).json()
    record = body["sections"]["strategy"]
    draft = record["draft"]
    assert body["strategy_state"] == "draft" and record["version"] == 1 and record["approval"] is None
    assert len(dispatched) == 2  # arastirma + tek strateji gorevi
    # Dayanagi olmayan / gecersiz referansli madde atilir
    assert [s["finding"] for s in draft["summary"]] == ["Sayfa başlıkları eksik"]
    assert draft["dropped"] == 2
    ids = {c["id"] for c in record["catalogue"]}
    for items in [draft[k] for k in ("summary", "section_notes", "competition", "opportunities", "decisions", "actions")]:
        assert all(item["refs"] and set(item["refs"]) <= ids for item in items)
    # Kaynakta bulunmayan rakam modele gitmez; bulunan gider; brif baglami gider
    prompt = ai.prompts["audit_strategy"]
    assert "3,2 milyar" not in prompt and "45.000" in prompt and "Randevu formu" in prompt
    # Kisit bulunan sektorde ucretli reklam plana GIRMEZ, karar noktasi olur
    assert [a["action"] for a in draft["actions"]] == ["Sayfa başlıklarını tamamla"]
    held = [d for d in draft["decisions"] if d.get("held_action")]
    assert len(held) == 1 and held[0]["ad_status"] == "koşullu" and "Google Arama Ağı" in held[0]["question"]
    text = _view_text(db_session, ws, report_id, "executive")
    assert "TASLAK: analist onayı yok" in text and "Ücretli reklam (Google Arama Ağı) başlatılsın mı?" in text
    assert "Uydurma bulgu" not in text and "Yeterli kanıt yok; madde üretilmedi." in text  # bos SWOT kutusu doldurulmaz
    assert "Kreatif brief" not in _view_text(db_session, ws, report_id, "detail")
    assert "Başlıkları tamamla" in _view_text(db_session, ws, report_id, "detail")  # bolum yorumu


def test_analyst_edits_approves_and_can_clear_a_held_ad(client, db_session, make_workspace, dispatched, monkeypatch):
    ws = make_workspace("Marka")
    audit, report_id, url, _ = strategy_ready(client, db_session, ws, dispatched, monkeypatch)
    draft = get(client, report_id, ws).json()["sections"]["strategy"]["draft"]
    approve = url.replace("/strategy?", "/strategy/approve?")

    edited = json.loads(json.dumps(draft))
    edited["actions"][0]["owner_role"] = "Ajans SEO ekibi"
    assert client.put(url, json={"version": 2, "revision": 0, "content": edited}).status_code == 409  # surum uyusmuyor
    res = client.put(url, json={"version": 1, "revision": 0, "content": edited})
    assert res.status_code == 200 and res.json()["strategy_state"] == "draft"
    assert res.json()["sections"]["strategy"]["edited"]["actions"][0]["owner_role"] == "Ajans SEO ekibi"
    # Dayanaksiz madde analistten de kabul edilmez
    bad = json.loads(json.dumps(edited))
    bad["actions"][0]["refs"] = []
    assert client.put(url, json={"version": 1, "revision": 1, "content": bad}).status_code == 400

    res = client.post(approve, json={"version": 1, "revision": 1, "approved_by": "Analist A"})
    assert res.status_code == 200 and res.json()["strategy_state"] == "approved"
    assert "Analist onaylı (Analist A" in _view_text(db_session, ws, report_id, "executive")
    # Onaydan sonraki duzeltme onayi dusurur
    held = next(d for d in edited["decisions"] if d.get("held_action"))
    edited["decisions"].remove(held)
    edited["actions"].append({**held["held_action"], "ad_status": "önerilebilir", "ad_basis": "Baro görüşü 12.11.2026"})
    res = client.put(url, json={"version": 1, "revision": 1, "content": edited}).json()
    assert res["strategy_state"] == "draft" and res["sections"]["strategy"]["approval"] is None
    actions = res["sections"]["strategy"]["edited"]["actions"]
    assert actions[-1]["ad_basis"] == "Baro görüşü 12.11.2026" and not any(
        d.get("held_action") for d in res["sections"]["strategy"]["edited"]["decisions"])
    # Dayanak yazilmadan plana alinamaz: yeniden karar noktasina doner
    edited["actions"][-1]["ad_basis"] = ""
    res = client.put(url, json={"version": 1, "revision": 2, "content": edited}).json()["sections"]["strategy"]["edited"]
    assert len(res["actions"]) == 1 and any(d.get("held_action") for d in res["decisions"])


def test_changed_inputs_drop_strategy_and_approval(client, db_session, make_workspace, dispatched, monkeypatch):
    ws = make_workspace("Marka")
    audit, report_id, url, _ = strategy_ready(client, db_session, ws, dispatched, monkeypatch)
    approve = url.replace("/strategy?", "/strategy/approve?")
    assert client.post(approve, json={"version": 1, "revision": 0, "approved_by": "Analist A"}).json()["strategy_state"] == "approved"

    answers = f"/api/v1/digital-audit/audits/{audit.id}/answers?brand_profile_id={ws.id}"
    assert client.put(answers, json={"answers": {"business_model": {"value": "E-ticaret"},
                                                 "main_conversion": {"value": "Online satış"}}}).status_code == 200
    assert get(client, report_id, ws).json()["strategy_state"] == "stale"
    text = _view_text(db_session, ws, report_id, "executive")
    assert "GÜNCEL DEĞİL" in text and "Sayfa başlıkları eksik" not in text and "Analist onaylı" not in text
    assert client.post(approve, json={"version": 1, "revision": 0, "approved_by": "Analist A"}).status_code == 409
    assert client.put(url, json={"version": 1, "revision": 0, "content": {}}).status_code == 409

    # Segmentler yenilenir, strateji yeniden uretilir: yeni brif modele gider, eski onay tasinmaz
    assert client.post(_urls(report_id, ws)["synthesis"]).status_code == 202
    assert run(db_session, dispatched) == "completed"
    assert client.post(url).status_code == 202
    ai = FakeAI()
    assert run(db_session, dispatched, ai) == "completed"
    body = get(client, report_id, ws).json()
    assert body["strategy_state"] == "draft" and body["sections"]["strategy"]["version"] == 2
    assert "Online satış" in ai.prompts["audit_strategy"] and "Randevu formu" not in ai.prompts["audit_strategy"]


def test_input_changed_during_generation_leaves_strategy_stale(client, db_session, make_workspace, dispatched,
                                                               monkeypatch):
    from app.core.digital_audit import strategy

    ws = make_workspace("Marka")
    audit, report_id, url, _ = strategy_ready(client, db_session, ws, dispatched, monkeypatch)
    real = strategy.generate

    def slow(ai, workspace, audit_row, report, usage):
        out = real(ai, workspace, audit_row, report, usage)
        answers = f"/api/v1/digital-audit/audits/{audit.id}/answers?brand_profile_id={ws.id}"
        assert client.put(answers, json={"answers": {"business_model": {"value": "B2B satış"}}}).status_code == 200
        return out

    monkeypatch.setattr(strategy, "generate", slow)
    assert client.post(url).status_code == 202
    assert run(db_session, dispatched) == "completed"
    assert get(client, report_id, ws).json()["strategy_state"] == "stale"


def test_failed_regeneration_keeps_previous_strategy(client, db_session, make_workspace, dispatched, monkeypatch):
    ws = make_workspace("Marka")
    audit, report_id, url, _ = strategy_ready(client, db_session, ws, dispatched, monkeypatch)
    assert client.post(url).status_code == 202
    run(db_session, dispatched, FakeAI(fail_on={"audit_strategy"}))
    record = get(client, report_id, ws).json()["sections"]["strategy"]
    assert record["status"] == "ok" and record["version"] == 1 and record["last_error"]
    assert record["draft"]["summary"][0]["finding"] == "Sayfa başlıkları eksik"


def test_strategy_views_share_context_and_respect_isolation(client, db_session, make_workspace, dispatched, monkeypatch):
    ws, other = make_workspace("Marka"), make_workspace("Diger")
    audit, report_id, url, _ = strategy_ready(client, db_session, ws, dispatched, monkeypatch)
    assert client.post(_urls(report_id, ws)["briefs"], json={"segments": [0], "channels": ["meta"]}).status_code == 202
    assert run(db_session, dispatched) == "completed"
    base = f"/api/v1/digital-audit/export?brand_profile_id={ws.id}&research_id={report_id}"
    for view in ("executive", "detail", "implementation"):
        assert client.get(f"{base}&view={view}&format=docx").content.startswith(b"PK")
        assert client.get(f"{base}&view={view}&format=pdf").content.startswith(b"%PDF")
        assert f"v1" in _view_text(db_session, ws, report_id, view)  # ucu ayni strateji surumu
    # Kisitli sektorde reklam fikri "yayina hazir degil" basligina gider; yalniz uyari eklenmez
    text = _view_text(db_session, ws, report_id, "implementation")
    assert "Karar bekleyen reklam fikirleri (yayına hazır değil)" in text and "Uygulanabilir reklam fikirleri" not in text
    # Gonderme yalniz karsiligi varsa yapilir; marka olgulari "hipotez" etiketiyle dogrulanmis sayilmaz
    assert "İlgili karar yönetici raporunun karar noktalarındadır." in text
    assert "Yayın öncesi teyit edilecek marka ifadeleri" in text and "[Hipotez] etiketi bu olguları" in text
    assert "Öneri durumu: koşullu" in text
    # Baska denetimle sessizce birlestirilmez
    newer = DigitalAudit(brand_profile_id=ws.id, status="completed")
    db_session.add(newer)
    db_session.commit()
    assert client.get(f"{base}&view=executive&audit_id={newer.id}").status_code == 409
    assert client.get(f"/api/v1/digital-audit/export?brand_profile_id={ws.id}&audit_id={audit.id}&view=executive").status_code == 400
    # Bayat brief uygulama belgesine girmez
    answers = f"/api/v1/digital-audit/audits/{audit.id}/answers?brand_profile_id={ws.id}"
    client.put(answers, json={"answers": {"business_model": {"value": "E-ticaret"}}})
    assert "Dişiniz mi ağrıyor?" not in _view_text(db_session, ws, report_id, "implementation")
    # Baska calisma strateji uretemez, duzeltemez, onaylayamaz, indiremez
    foreign = url.replace(f"brand_profile_id={ws.id}", f"brand_profile_id={other.id}")
    assert client.post(foreign).status_code == 404
    assert client.put(foreign, json={"version": 1, "revision": 0, "content": {}}).status_code == 404
    assert client.post(foreign.replace("/strategy?", "/strategy/approve?"),
                       json={"version": 1, "revision": 0, "approved_by": "X Y"}).status_code == 404
    assert client.get(base.replace(f"brand_profile_id={ws.id}", f"brand_profile_id={other.id}") + "&view=executive").status_code == 404


CAT = [
    {"id": "B1", "kind": "Ölçüm (marka sitesi)", "text": "marka.test: Görsel alt metinleri: İyi · 11 / 64 görselde alt metni eksik",
     "site": "marka.test", "state": "gözlendi", "assessment": "iyi", "limits": []},
    {"id": "B2", "kind": "Dış kaynak (reklam kısıtı)", "text": "Ücretli öne çıkarma yasak"},
    {"id": "B3", "kind": "Ölçüm (marka sitesi)", "text": "marka.test: GA4 izi: Görülmedi", "site": "marka.test",
     "state": "gözlenmedi", "assessment": "değerlendirilmedi", "limits": ["Etiket yöneticisinin içinden yüklenen izler görülmez."]},
    {"id": "B4", "kind": "Rakip gözlemi", "text": "rakip.test/tr: Açılış hızı (mobil): Sorun · performans 32/100",
     "site": "rakip.test/tr", "state": "gözlendi", "assessment": "sorun", "limits": []},
    {"id": "B5", "kind": "Ölçüm (marka sitesi)", "text": "marka.test: Açılış hızı (mobil): Uyarı · performans 79/100",
     "site": "marka.test", "state": "gözlendi", "assessment": "uyarı", "limits": []},
]


def _plan(base, strict=False, **fields):
    from app.core.digital_audit import strategy

    action = {"period": "0-30", "priority": "P1", "refs": ["B1"], "action": "İçerik paylaş", "kind": "içerik",
              "channel": "", "publication": "", **fields}
    out = strategy.normalize({"actions": [action]}, CAT, base, strict=strict)
    return out["actions"], [d for d in out["decisions"] if d.get("held_action")]


def test_paid_ads_are_held_but_organic_work_is_not_turned_into_an_ad_question():
    # Tur "reklam" ise ya da etiket/metin acikca ucretli reklami anlatiyorsa: bekletilir
    for fields in ({"kind": "reklam", "channel": "meta"},
                   {"kind": "reklam"},                                               # kanal ve yayin turu bos
                   {"kind": "seo", "action": "Google Ads kampanyası başlat"},        # tur etiketiyle asilamaz
                   {"kind": "içerik", "action": "Remarketing listesi kur", "publication": "organik"},
                   {"kind": "içerik", "channel": "meta", "publication": "sponsorlu"}):
        kept, held = _plan("koşullu", **fields)
        assert kept == [] and len(held) == 1 and held[0]["refs"] == ["B1", "B2"], fields
        assert _plan("değerlendirilmedi", **fields)[0] == []
    # Celiski analiste ACIKCA yazilir; reklam turundeki soru ayridir
    assert "Sınıflandırma çelişkili (tür “SEO”, ama metin ücretli reklamdan söz ediyor)" in _plan(
        "koşullu", kind="seo", action="Google Ads kampanyası başlat")[1][0]["question"]
    assert _plan("koşullu", kind="reklam", channel="meta")[1][0]["question"].startswith("Ücretli reklam (Instagram / Facebook)")
    # Pilotta gorulen hata: SEO/icerik isine "Google Arama Agi" yazilmisti. Reklam sorusu OLMAZ;
    # yanlis kanal kaldirilir ve bu analiste soylenir.
    for kind, text in (("seo", "'ataköy diş hekimi' aramaları için hizmet sayfalarını yerel SEO kriterlerine göre optimize etmek"),
                       ("teknik", "Eksik 1 meta açıklamasını ve 11 görsel alt metnini tamamlamak"),
                       ("içerik", "Dentofobiye yönelik mevzuata uygun bilgilendirici içerikler hazırlayıp blogda yayımlamak")):
        kept, held = _plan("koşullu", kind=kind, action=text, channel="google_search", publication="sponsorlu" if False else "")
        assert held == [] and kept[0]["channel"] == "" and "kanal kaldırıldı" in kept[0]["class_note"], kind
        assert kept[0]["publication"] == ("organik" if kind == "içerik" else "")
    # Organik sosyal icerik kanaliyla planda kalir; kisit yoksa reklam da kalir
    kept, held = _plan("koşullu", channel="meta", publication="organik")
    assert held == [] and kept[0]["channel"] == "meta" and kept[0]["class_note"] == ""
    assert _plan("önerilebilir", kind="reklam", channel="meta")[0][0]["ad_status"] == "önerilebilir"
    # Tur zorunlu
    assert _plan("koşullu", kind="")[0] == []
    # Blog / web sitesi stratejiye ozgu bir yerdir; SEO isinde de not uretmeden kalir, reklam agi sayilmaz
    for kind in ("içerik", "seo", "teknik"):
        kept, held = _plan("koşullu", kind=kind, channel="website")
        assert held == [] and kept[0]["channel"] == "website" and kept[0]["class_note"] == ""
    assert "website" not in research.CHANNELS  # reklam brieflerinin kanal sozlesmesi degismedi


def test_references_must_actually_support_the_claim():
    from app.core.digital_audit import strategy

    def swot(key, text, refs):
        out = strategy.normalize({"swot": {key: [{"text": text, "refs": refs}]}}, CAT, "koşullu", strict=False)
        return out["swot"][key], out["dropped"]

    # "Iyi" kontrol zayif yon diye sunulamaz (uyari); firsat olarak yazilabilir
    weak, _ = swot("weaknesses", "11 görselde alt metin eksikliğinin bulunması", ["B1"])
    assert "değerlendirmesi “İyi”" in weak[0]["warnings"][0]
    assert swot("opportunities", "Genel durum iyi; örneklemde iyileştirilebilecek 11 görsel var", ["B1"])[0][0]["warnings"] == []
    # "Gorulmedi" + sinir, kesin yokluk gibi yazilamaz
    absent, _ = swot("weaknesses", "Hiçbir web analitik verisi toplanamamaktadır", ["B3"])
    assert any("kesin yokluk değildir" in w and "Etiket yöneticisinin" in w for w in absent[0]["warnings"])
    assert swot("weaknesses", "Sayfa kodunda GA4 izi görülmedi; GTM yapılandırması kontrol edilmeli", ["B3"])[0][0]["warnings"] == []
    # Kosullu cumle yokluk iddiasi degildir (pilotta yanlis alarm vermisti); kesin iddia yine yakalanir
    assert swot("weaknesses", "Veri toplama altyapısı doğrulanmadan pazarlama performansı izlenemez.", ["B3"])[0][0]["warnings"] == []
    assert swot("weaknesses", "Altyapı doğrulanmadan karar verilmemeli. Kullanıcı hareketleri takip edilememektedir.",
                ["B3"])[0][0]["warnings"]
    # Metinde adi gecen sitenin dayanagi yoksa madde atilir (analistten gelirse reddedilir)
    text = "Mobil hızda marka.test 79/100 ile rakip.test (32/100) sitesinden yüksektir"
    assert swot("strengths", text, ["B5"]) == ([], 1)
    kept, dropped = swot("strengths", text, ["B5", "B4"])
    assert dropped == 0 and kept[0]["warnings"] == []  # gosterilen dayanakta gecen skor uyari uretmez
    with pytest.raises(strategy.StrategyInputError):
        strategy.normalize({"swot": {"strengths": [{"text": text, "refs": ["B5"]}]}}, CAT, "koşullu", strict=True)
    # Arama sirasi satiri birden cok siteyi tasir: o satir iki sitenin de dayanagidir
    serp = CAT + [{"id": "B6", "kind": "Ölçüm (arama görünürlüğü)", "text": "“diş” araması: marka.test 3; rakip.test 7",
                   "sites": ["marka.test", "rakip.test"]}]
    out = strategy.normalize({"competition": [{"text": "marka.test ve rakip.test aynı aramada görüldü", "refs": ["B6"]}]},
                             serp, "koşullu", strict=False)
    assert len(out["competition"]) == 1 and out["dropped"] == 0
    # "Iyi" kontrolun sayisi, baska bir uyarili dayanagin yaninda da zayif yon diye tasinamaz
    mixed, _ = swot("weaknesses", "Mobil hız 79/100 ve 11 görselde alt metin eksikliği", ["B5", "B1"])
    assert any("değerlendirmesi “İyi”" in w for w in mixed[0]["warnings"])


def test_draft_with_open_warnings_cannot_be_approved(client, db_session, make_workspace, dispatched, monkeypatch):
    ws = make_workspace("Marka")
    audit, report_id, url, _ = strategy_ready(client, db_session, ws, dispatched, monkeypatch)
    approve = url.replace("/strategy?", "/strategy/approve?")
    draft = get(client, report_id, ws).json()["sections"]["strategy"]["draft"]
    assert draft["actions"][0]["kind"] == "seo" and "kanal kaldırıldı" in draft["actions"][0]["class_note"]
    bad = json.loads(json.dumps(draft))
    bad["summary"][0]["meaning"] = "Dönüşüm %40 artar"  # dayanakta olmayan rakam
    res = client.put(url, json={"version": 1, "revision": 0, "content": bad})
    assert res.status_code == 200 and res.json()["sections"]["strategy"]["edited"]["summary"][0]["warnings"]
    assert client.post(approve, json={"version": 1, "revision": 1, "approved_by": "Analist A"}).status_code == 400
    assert client.put(url, json={"version": 1, "revision": 1, "content": draft}).status_code == 200
    assert client.post(approve, json={"version": 1, "revision": 2, "approved_by": "Analist A"}).json()[
        "strategy_state"] == "approved"


def test_analyst_cannot_approve_text_changed_by_someone_else(client, db_session, make_workspace, dispatched,
                                                             monkeypatch):
    ws = make_workspace("Marka")
    audit, report_id, url, _ = strategy_ready(client, db_session, ws, dispatched, monkeypatch)
    approve = url.replace("/strategy?", "/strategy/approve?")
    seen_by_b = get(client, report_id, ws).json()["sections"]["strategy"]
    edited = json.loads(json.dumps(seen_by_b["draft"]))
    edited["summary"][0]["meaning"] = "A'nın yeni metni"
    assert client.put(url, json={"version": 1, "revision": 0, "content": edited}).status_code == 200
    # B eski ekranindan: ne onaylayabilir ne A'nin metnini ezebilir
    assert client.post(approve, json={"version": 1, "revision": seen_by_b["revision"],
                                      "approved_by": "Analist B"}).status_code == 409
    assert client.put(url, json={"version": 1, "revision": 0, "content": seen_by_b["draft"]}).status_code == 409
    body = get(client, report_id, ws).json()
    assert body["strategy_state"] == "draft" and body["sections"]["strategy"]["revision"] == 1
    assert body["sections"]["strategy"]["edited"]["summary"][0]["meaning"] == "A'nın yeni metni"
    res = client.post(approve, json={"version": 1, "revision": 1, "approved_by": "Analist B"}).json()
    assert res["strategy_state"] == "approved" and res["sections"]["strategy"]["approval"]["revision"] == 1


def test_input_outside_model_catalogue_still_makes_strategy_stale(client, db_session, make_workspace, dispatched,
                                                                  monkeypatch):
    from app.core.digital_audit import strategy

    monkeypatch.setattr(strategy, "MAX_CATALOGUE", 7)  # kisit+brif+rakam+segment (6) + tek olcum satiri
    ws = make_workspace("Marka")
    audit, report_id, url, ai = strategy_ready(client, db_session, ws, dispatched, monkeypatch)
    record = get(client, report_id, ws).json()["sections"]["strategy"]
    kinds = [c["kind"] for c in record["catalogue"]]
    # Kisit ve brif kesilmez; rakip olcumu disarida kalir ve bu acik eksik olarak yazilir
    assert "Dış kaynak (reklam kısıtı)" in kinds and "Analist brifi" in kinds and "Rakip gözlemi" not in kinds
    assert "Ücretli öne çıkarma yasak" in ai.prompts["audit_strategy"]
    assert any("taslak üretiminde kullanılmadı" in gap for gap in record["gaps"])
    approve = url.replace("/strategy?", "/strategy/approve?")
    assert client.post(approve, json={"version": 1, "revision": 0, "approved_by": "Analist A"}).json()[
        "strategy_state"] == "approved"
    # Modele GITMEYEN satir degisir: strateji ve onay yine de guncelligini kaybeder
    rival = db_session.query(DigitalAuditTarget).filter_by(audit_id=audit.id, kind="competitor").one()
    rival.findings = [_finding("seo.titles", "Sayfa başlıkları", "warning", value={"missing": 9})]
    db_session.commit()
    assert get(client, report_id, ws).json()["strategy_state"] == "stale"


def test_ecommerce_rows_reach_no_model_unless_the_site_is_a_shop(client, db_session, make_workspace, dispatched,
                                                                 monkeypatch):
    from app.core.digital_audit import report_doc, strategy

    ws = make_workspace("Marka")
    audit, report_id, url, ai = strategy_ready(client, db_session, ws, dispatched, monkeypatch)
    shop_row = _finding("ecommerce.platform", "E-ticaret altyapısı", "info", state="not_observed")
    audit.targets[0].findings = [*audit.targets[0].findings, shop_row]
    db_session.commit()
    report = db_session.get(DigitalResearch, report_id)
    full = strategy.catalogue(ws, audit, report)
    assert any(c.get("check") == "ecommerce.platform" for c in full)  # parmak izi tam listeden
    assert not any(c.get("check") == "ecommerce.platform" for c in strategy.model_catalogue(full, report_doc.is_ecommerce(audit)))
    assert "E-ticaret" not in json.dumps(research.audit_facts(audit), ensure_ascii=False)  # segment sentezi yolu
    audit.answers = {**audit.answers, "business_model": {"value": "E-ticaret", "note": None}}
    assert "E-ticaret altyapısı" in json.dumps(research.audit_facts(audit), ensure_ascii=False)


def test_paid_ad_suggestions_are_withheld_in_every_document_when_the_sector_is_restricted(
        client, db_session, make_workspace, dispatched, monkeypatch):
    synthesis = {**SYNTHESIS, "google_ads": {"keyword_groups": [{"group": "Acil", "keywords": ["acil diş hekimi"]}],
                                             "negatives": ["ücretsiz"], "note": ""},
                 "meta_audiences": [{"segment": "Acil hasta", "location": "İstanbul", "age_hypothesis": "25-45",
                                     "interests": ["diş sağlığı"], "note": ""}]}
    monkeypatch.setitem(PAYLOADS, "audit_synthesis", synthesis)
    ws = make_workspace("Marka")
    report_id = completed_report(client, db_session, ws, dispatched)  # SECTOR'da reklam kisiti var
    for text in (_blocks(db_session, ws, report_id), _view_text(db_session, ws, report_id, "detail")):
        assert "Google Ads önerisi" not in text and "acil diş hekimi" not in text
        assert "Meta hedef kitle önerisi" not in text and "25-45" not in text
        assert "uygunluk kararı verilmeden Google Ads ve Meta hedefleme önerileri bu raporda yayımlanmaz" in text
    # Kisit bulunmayan sektorde oneriler eskisi gibi yayimlanir
    monkeypatch.setitem(PAYLOADS, "audit_sector_research", {**SECTOR, "restrictions": []})
    other = make_workspace("Diger")
    free_id = completed_report(client, db_session, other, dispatched)
    assert "Google Ads önerisi [Hipotez]" in _blocks(db_session, other, free_id)


def test_open_gaps_follow_current_scope_without_regenerating(client, db_session, make_workspace, dispatched, monkeypatch):
    ws = make_workspace("Marka")
    audit, report_id, url, _ = strategy_ready(client, db_session, ws, dispatched, monkeypatch)
    # Eski kapsamla donmus bir satir: kayitta durur ama ekrana ve belgeye GITMEZ
    report = db_session.get(DigitalResearch, report_id)
    record = dict(report.sections["strategy"])
    record["gaps"] = ["Müşteriye sorulacak 8 hesap sorusu cevaplanmadı.", "Dayanak listesi uzun olduğu için 3 ölçüm satırı "
                      "taslak üretiminde kullanılmadı."]
    report.sections = {**report.sections, "strategy": record}
    db_session.commit()
    shown = get(client, report_id, ws).json()
    assert shown["strategy_state"] == "draft" and len(dispatched) == 2  # bayatlamaz, yeni gorev yok
    gaps = shown["sections"]["strategy"]["gaps"]
    assert not any("hesap sorusu" in g for g in gaps) and any("Arama görünürlüğü ölçülmedi" in g for g in gaps)
    assert any("taslak üretiminde kullanılmadı" in g for g in gaps)  # uretime ozgu not korunur
    text = _view_text(db_session, ws, report_id, "executive")
    assert "hesap sorusu" not in text and "Arama görünürlüğü ölçülmedi." in text
