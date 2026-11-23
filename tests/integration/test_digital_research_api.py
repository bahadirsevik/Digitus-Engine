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


def _seen(client, report_id, ws, revision, approved_by):
    """Onay govdesi: analistin o anda ekranda gordugu belgenin parmak iziyle."""
    return {"version": 1, "revision": revision, "approved_by": approved_by,
            "review_sha": get(client, report_id, ws).json()["strategy_review_sha"] or "yok-yok-yok"}


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

    res = client.post(approve, json=_seen(client, report_id, ws, 1, "Analist A"))
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
    assert client.post(approve, json=_seen(client, report_id, ws, 0, "Analist A")).json()["strategy_state"] == "approved"

    answers = f"/api/v1/digital-audit/audits/{audit.id}/answers?brand_profile_id={ws.id}"
    assert client.put(answers, json={"answers": {"business_model": {"value": "E-ticaret"},
                                                 "main_conversion": {"value": "Online satış"}}}).status_code == 200
    assert get(client, report_id, ws).json()["strategy_state"] == "stale"
    text = _view_text(db_session, ws, report_id, "executive")
    assert "GÜNCEL DEĞİL" in text and "Sayfa başlıkları eksik" not in text and "Analist onaylı" not in text
    assert client.post(approve, json=_seen(client, report_id, ws, 0, "Analist A")).status_code == 409
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
                       json=_seen(client, report_id, ws, 0, "X Y")).status_code == 404
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
    assert client.post(approve, json=_seen(client, report_id, ws, 1, "Analist A")).status_code == 400
    assert client.put(url, json={"version": 1, "revision": 1, "content": draft}).status_code == 200
    assert client.post(approve, json=_seen(client, report_id, ws, 2, "Analist A")).json()[
        "strategy_state"] == "approved"


def test_analyst_cannot_approve_text_changed_by_someone_else(client, db_session, make_workspace, dispatched,
                                                             monkeypatch):
    ws = make_workspace("Marka")
    audit, report_id, url, _ = strategy_ready(client, db_session, ws, dispatched, monkeypatch)
    approve = url.replace("/strategy?", "/strategy/approve?")
    seen_by_b = get(client, report_id, ws).json()["sections"]["strategy"]
    screen_of_b = _seen(client, report_id, ws, seen_by_b["revision"], "Analist B")  # B'nin actigi ekran
    edited = json.loads(json.dumps(seen_by_b["draft"]))
    edited["summary"][0]["meaning"] = "A'nın yeni metni"
    assert client.put(url, json={"version": 1, "revision": 0, "content": edited}).status_code == 200
    # B eski ekranindan: ne onaylayabilir ne A'nin metnini ezebilir
    assert client.post(approve, json=screen_of_b).status_code == 409
    assert client.put(url, json={"version": 1, "revision": 0, "content": seen_by_b["draft"]}).status_code == 409
    body = get(client, report_id, ws).json()
    assert body["strategy_state"] == "draft" and body["sections"]["strategy"]["revision"] == 1
    assert body["sections"]["strategy"]["edited"]["summary"][0]["meaning"] == "A'nın yeni metni"
    res = client.post(approve, json=_seen(client, report_id, ws, 1, "Analist B")).json()
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
    assert client.post(approve, json=_seen(client, report_id, ws, 0, "Analist A")).json()[
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


# --- G1-G4: bolum yorumu zorunlulugu, reklam karari, kapsam etiketi, mevzuat teyidi ----------------

def test_every_section_with_data_needs_a_note_and_gaps_are_shown(client, db_session, make_workspace, dispatched,
                                                                 monkeypatch):
    ws = make_workspace("Marka")
    audit, report_id, url, ai = strategy_ready(client, db_session, ws, dispatched, monkeypatch)
    # Model, verisi olan her bolum icin yorum yazmak zorunda oldugunu ve hangileri oldugunu bilir
    prompt = ai.prompts["audit_strategy"]
    assert "YORUMU ZORUNLU BÖLÜMLER: seo, competitors, sector, audience, segments" in prompt
    draft = get(client, report_id, ws).json()["sections"]["strategy"]["draft"]
    gaps = {g["section"]: g["reason"] for g in draft["note_gaps"]}
    # Sahte yanit yalniz SEO yorumu yazdi: verisi olan diger bolumler acikca eksik gorunur
    assert "seo" not in gaps and gaps["sector"] == gaps["audience"] == gaps["competitors"] == "veri var, yorum üretilmedi"
    assert gaps["youtube"] == gaps["site"] == "bu bölüm için ölçülmüş veri yok" and "ecommerce" not in gaps
    text = _view_text(db_session, ws, report_id, "detail")
    assert "Bölüm yorumu yok (Sektör): veri var, yorum üretilmedi." in text
    assert "Bölüm yorumu yok (YouTube): bu bölüm için ölçülmüş veri yok." in text
    # Yeni bolumler icin yorum kabul edilir ve yerine yazilir
    edited = json.loads(json.dumps(draft))
    seg = next(c["id"] for c in get(client, report_id, ws).json()["sections"]["strategy"]["catalogue"]
               if c.get("section") == "segments")
    edited["section_notes"].append({"section": "segments", "finding": "Acil hasta segmenti öne çıkıyor",
                                    "impact": "Mesaj önceliği", "action": "Segmenti görüşmeyle doğrula", "refs": [seg]})
    res = client.put(url, json={"version": 1, "revision": 0, "content": edited}).json()
    assert "segments" not in {g["section"] for g in res["sections"]["strategy"]["edited"]["note_gaps"]}
    assert "Segmenti görüşmeyle doğrula" in _view_text(db_session, ws, report_id, "detail")


def test_catalogue_adds_youtube_reputation_and_demand_rows_at_the_end():
    from types import SimpleNamespace

    from app.core.digital_audit import strategy

    visibility = {"sites": ["marka.test"], "serp": {"status": "ok", "keywords": [], "reputation": [
        {"query": "Marka şikayet", "results": [{"title": "Genel şikayet sayfası", "position": 1, "domain": "x.test"}]}]},
        "youtube": {"channels": {"marka.test": {"status": "ok", "title": "Marka", "subscribers": 1, "video_count": 11,
                                                "last_upload": "2026-10-22", "recent_sample": 11, "recent_avg_views": 268}}}}
    audit = SimpleNamespace(answers={}, visibility=visibility, targets=[SimpleNamespace(
        url="https://marka.test", kind="own", pages_checked=10,
        findings=[_finding("seo.titles", "Sayfa başlıkları", "ok", value={"missing": 0})])])
    report = SimpleNamespace(claim_overrides=[], sections={"demand": {"status": "ok", "keywords": [{"keyword": "a"}], "by_month": [
        {"month": "Ocak", "searches": 300, "index": 1.1}, {"month": "Ekim", "searches": 200, "index": 0.9}]}})
    cat = strategy.catalogue(SimpleNamespace(name="Marka", company_url="https://marka.test"), audit, report)
    assert [(c["id"], c["section"]) for c in cat] == [("B1", "seo"), ("B2", "youtube"), ("B3", "reputation"), ("B4", "demand")]
    assert "abone 1, 11 video" in cat[1]["text"] and "içerikleri okunmadı" in cat[2]["text"]
    assert "en yüksek Ocak (300), en düşük Ekim (200)" in cat[3]["text"]


def test_pending_ad_ideas_get_a_decision_point_and_two_scenarios(client, db_session, make_workspace, dispatched,
                                                                monkeypatch):
    briefs = json.loads(json.dumps(BRIEFS))
    briefs["ad_ideas"].append({**briefs["ad_ideas"][0], "channel": "Instagram / Facebook", "message": "Sosyal reklam"})
    monkeypatch.setitem(PAYLOADS, "audit_briefs", briefs)
    ws = make_workspace("Marka")
    audit, report_id, url, _ = strategy_ready(client, db_session, ws, dispatched, monkeypatch)
    before = _view_text(db_session, ws, report_id, "executive")
    assert "Ücretli reklam (Instagram / Facebook)" not in before
    assert client.post(_urls(report_id, ws)["briefs"], json={"segments": [0], "channels": ["meta", "google_search"]}).status_code == 202
    assert run(db_session, dispatched) == "completed"
    assert get(client, report_id, ws).json()["strategy_state"] == "draft"  # brief uretimi stratejiyi bayatlatmaz
    text = _view_text(db_session, ws, report_id, "executive")
    # Briefteki bekleyen kanal icin karar eklenir; stratejinin zaten beklettigi kanal (Google Arama Agi) tekrarlanmaz
    assert "Ücretli reklam (Instagram / Facebook) başlatılsın mı? Uygulama belgesinde bu kanal için 1 reklam fikri" in text
    # Her karar iki yerde gecer (ozet listesi + karar tablosu), ayni numarayla; fazladan kopya yok
    assert text.count("Ücretli reklam (Google Arama Ağı) başlatılsın mı?") == 2
    assert text.count("Ücretli reklam (Instagram / Facebook) başlatılsın mı?") == 2
    assert "Ücretli reklam: iki senaryo" in text and "izinli senaryo" in text and "izinsiz senaryo" in text
    assert "İlgili karar yönetici raporunun karar noktalarındadır." in _view_text(db_session, ws, report_id, "implementation")
    detail = _view_text(db_session, ws, report_id, "detail")
    # Hesap verisinin toplanmamis olmasi "erisim yok" diye yazilmaz
    assert "Reklam hazırlığı [Gözlem]" in detail and "Hesap verisi bu analize alınmadı; performans değerlendirilmedi" in detail
    assert "Hesap erişimi yok" not in detail


def test_audience_insights_carry_scope_and_segments_their_basis(client, db_session, make_workspace, dispatched,
                                                                monkeypatch):
    import sys

    monkeypatch.setattr(sys.modules[__name__], "SOURCE_TEXT", SOURCE_TEXT + " Marka kliniğinde iki saat bekledim.")
    audience = {**AUDIENCE, "needs": AUDIENCE["needs"] + [
        # Kaynakta marka gecmiyor; marka adini tema metnine MODEL yazmis: iliski dogrulanamaz
        {"theme": "Marka kliniğinde bekleme süresi", "quote": "Randevu almak çok zor", "source_name": "Forum"},
        # Kaynakta birebir dogrulanan alintida marka adi geciyor: gercek kaynak-marka iliskisi
        {"theme": "Bekleme süresi şikâyeti", "quote": "Marka kliniğinde iki saat bekledim", "source_name": "Forum"}]}
    monkeypatch.setitem(PAYLOADS, "audit_audience_research", audience)
    ws = make_workspace("Marka")
    ai = FakeAI()
    report_id = start(client, ws).json()["research_id"]
    assert run(db_session, dispatched, ai) == "completed"
    text = _blocks(db_session, ws, report_id)
    assert "[Sektör geneli] Randevu bulma" in text and "[Marka hakkında] Bekleme süresi şikâyeti" in text
    # Dogrulanmamis marka atfi rapora girmez (ne marka ne sektor etiketiyle) ve sayisi yazilir
    assert "Marka kliniğinde bekleme süresi" not in text
    assert "1 içgörü, markaya atıf yaptığı hâlde bu ilişki kaynakta doğrulanamadığı için rapora alınmadı" in text
    # Ayni icgoru segment sentezine ve strateji dayanaklarina da gitmez
    assert "Marka kliniğinde bekleme süresi" not in ai.prompts["audit_synthesis"]
    from app.core.digital_audit import strategy

    kinds = {c["text"]: c["kind"] for c in strategy.catalogue(ws, None, db_session.get(DigitalResearch, report_id))}
    assert "Marka kliniğinde bekleme süresi" not in kinds
    assert kinds["Bekleme süresi şikâyeti"] == "Marka hakkında (kaynakta doğrulanan alıntı)"
    assert kinds["Randevu bulma"] == "Sektör örneklemi (hedef kitle)"
    assert "Dayanak türü: [Hipotez] sektör kaynaklarından türetilmiş yorum; müşteri verisiyle doğrulanmadı" in text


def test_approval_covers_everything_the_document_prints(client, db_session, make_workspace, dispatched, monkeypatch):
    briefs = json.loads(json.dumps(BRIEFS))
    briefs["ad_ideas"].append({**briefs["ad_ideas"][0], "channel": "Instagram / Facebook"})
    monkeypatch.setitem(PAYLOADS, "audit_briefs", briefs)
    ws = make_workspace("Marka")
    audit, report_id, url, _ = strategy_ready(client, db_session, ws, dispatched, monkeypatch)
    approve = url.replace("/strategy?", "/strategy/approve?")
    verify = f"/api/v1/digital-audit/research/{report_id}/restrictions/0/verification?brand_profile_id={ws.id}"
    full = {"primary_source_url": "https://www.resmigazete.gov.tr/x", "regulation_date": "2025-11-05",
            "verified_by": "Analist A", "verified_at": "2026-11-18"}

    def approved():
        assert client.post(approve, json=_seen(client, report_id, ws, 0, "Analist A")).json()["strategy_state"] == "approved"

    approved()
    # 1) Onaydan sonra mevzuat teyidi eklenir: rapora yeni icerik girer, onay duser (strateji bayatlamaz)
    assert client.put(verify, json=full).json()["strategy_state"] == "draft"
    assert "Analist onaylı" not in _view_text(db_session, ws, report_id, "executive")
    approved()
    # 2) Teyit kaldirilir: yine yeniden inceleme gerekir
    assert client.put(verify, json={}).json()["strategy_state"] == "draft"
    approved()
    # 3) Onaydan sonra brief uretilir: yonetici raporuna yeni reklam karari eklenir, onay duser
    assert client.post(_urls(report_id, ws)["briefs"], json={"segments": [0], "channels": ["meta"]}).status_code == 202
    assert run(db_session, dispatched) == "completed"
    body = get(client, report_id, ws).json()
    assert body["strategy_state"] == "draft" and len(dispatched) == 3  # yeni strateji uretimi gerekmedi
    text = _view_text(db_session, ws, report_id, "executive")
    assert "Ücretli reklam (Instagram / Facebook) başlatılsın mı?" in text and "TASLAK: analist onayı yok" in text
    approved()
    assert "Analist onaylı (Analist A" in _view_text(db_session, ws, report_id, "executive")


def test_regulation_is_never_marked_verified_without_a_complete_analyst_record(client, db_session, make_workspace,
                                                                             dispatched, monkeypatch):
    import sys

    module = sys.modules[__name__]
    monkeypatch.setattr(module, "SOURCE_TEXT", SOURCE_TEXT + " Bu alanda 1219 sayılı Kanun yürürlüktedir.")
    sector = {**SECTOR, "claims": SECTOR["claims"] + [
        {"claim": "Kanun numarası", "value": "1219", "unit": "sayılı Kanun", "year": "", "geography": "Türkiye",
         "source_name": "Mevzuat"}]}
    monkeypatch.setitem(PAYLOADS, "audit_sector_research", sector)
    ws, other = make_workspace("Marka"), make_workspace("Diger")
    audit, report_id, url, _ = strategy_ready(client, db_session, ws, dispatched, monkeypatch)
    put = f"/api/v1/digital-audit/research/{report_id}/restrictions/0/verification?brand_profile_id={ws.id}"
    text = _blocks(db_session, ws, report_id)
    # Varsayilan: teyit yok. Kanun numarasi pazar tablosunda degil, mevzuat referanslarinda
    assert "Birincil kaynaktan teyit edilmedi" in text and "1 kısıttan 0 tanesi birincil kaynaktan" in text
    assert "Teyit edildi" not in text
    assert "Mevzuat referansları [Kaynak]" in text and "Kanun numarası: 1219 sayılı Kanun" in text
    table = next(b for b in __import__("app.core.digital_audit.report_doc", fromlist=["x"]).build_blocks(
        ws, None, db_session.get(DigitalResearch, report_id)) if b[0] == "table" and b[1][0] == "Bilgi")
    assert [row[0] for row in table[2]] == ["Diş hekimi sayısı"]
    box = _view_text(db_session, ws, report_id, "executive")
    assert "Mevzuat: marka için ne anlama geliyor" in box and "0 tanesi birincil kaynaktan teyit edildi, 1 tanesi teyit bekliyor" in box
    assert "Markanın yayındaki reklam ve içerikleri bilinmiyor" in box

    full = {"primary_source_url": "https://www.resmigazete.gov.tr/eskiler/2025/x.htm", "regulation_date": "2025-11-05",
            "verified_by": "Analist A", "verified_at": "2026-11-18"}
    for bad in ({"primary_source_url": full["primary_source_url"]},            # yarim kayit
                {**full, "primary_source_url": "resmigazete.gov.tr"},         # adres degil
                {**full, "regulation_date": "05.11.2025"}, {**full, "verified_at": "2999-01-01"}):
        assert client.put(put, json=bad).status_code == 400
    assert "Teyit edildi" not in _blocks(db_session, ws, report_id)
    assert client.put(put.replace("/0/", "/7/"), json=full).status_code == 400
    assert client.put(put.replace(f"brand_profile_id={ws.id}", f"brand_profile_id={other.id}"), json=full).status_code == 404

    res = client.put(put, json=full)
    assert res.status_code == 200 and res.json()["sections"]["sector"]["restrictions"][0]["verification"] == full
    assert res.json()["strategy_state"] == "draft"  # teyit kaydi stratejiyi bayatlatmaz
    text = _blocks(db_session, ws, report_id)
    assert "Teyit edildi: https://www.resmigazete.gov.tr/eskiler/2025/x.htm (mevzuat tarihi 2025-11-05; teyit eden Analist A, 2026-11-18)" in text
    assert "1 kısıttan 1 tanesi birincil kaynaktan" in text
    # Bos kayit teyidi kaldirir
    assert client.put(put, json={}).status_code == 200
    assert "Teyit edildi" not in _blocks(db_session, ws, report_id)


def test_stale_screen_cannot_approve_a_regulation_record_it_never_saw(client, db_session, make_workspace, dispatched,
                                                                     monkeypatch):
    ws = make_workspace("Marka")
    audit, report_id, url, _ = strategy_ready(client, db_session, ws, dispatched, monkeypatch)
    approve = url.replace("/strategy?", "/strategy/approve?")
    verify = f"/api/v1/digital-audit/research/{report_id}/restrictions/0/verification?brand_profile_id={ws.id}"
    screen_of_a = _seen(client, report_id, ws, 0, "Analist A")  # A ekrani acar
    # B bu arada mevzuat teyidini degistirir; stratejinin surum ve revizyonu AYNI kalir
    assert client.put(verify, json={"primary_source_url": "https://www.resmigazete.gov.tr/x", "regulation_date": "2025-11-05",
                                    "verified_by": "Analist B", "verified_at": "2026-11-18"}).status_code == 200
    record = get(client, report_id, ws).json()["sections"]["strategy"]
    assert (record["version"], record["revision"]) == (1, 0)
    # A'nin eski ekranindan gelen onay reddedilir: gormedigi teyidi onaylayamaz
    assert client.post(approve, json=screen_of_a).status_code == 409
    assert get(client, report_id, ws).json()["strategy_state"] == "draft"
    # Guncel icerigi gorunce onaylayabilir
    assert client.post(approve, json=_seen(client, report_id, ws, 0, "Analist A")).json()["strategy_state"] == "approved"
    assert client.post(approve, json={"version": 1, "revision": 0, "approved_by": "Analist A"}).status_code == 422


def test_regenerated_brief_text_drops_the_approval_even_with_the_same_decisions(client, db_session, make_workspace,
                                                                                dispatched, monkeypatch):
    ws = make_workspace("Marka")
    audit, report_id, url, _ = strategy_ready(client, db_session, ws, dispatched, monkeypatch)
    approve = url.replace("/strategy?", "/strategy/approve?")
    request = {"segments": [0], "channels": ["meta"]}
    assert client.post(_urls(report_id, ws)["briefs"], json=request).status_code == 202
    assert run(db_session, dispatched) == "completed"
    assert client.post(approve, json=_seen(client, report_id, ws, 0, "Analist A")).json()["strategy_state"] == "approved"
    questions = [d for d in _view_text(db_session, ws, report_id, "executive").split("başlatılsın mı?")]
    # Ayni segment, ayni kanal, ayni sayida fikir; yalniz reklam METNI farkli
    changed = json.loads(json.dumps(BRIEFS))
    changed["ad_ideas"][0]["message"] = "İncelenmemiş yeni reklam metni"
    monkeypatch.setitem(PAYLOADS, "audit_briefs", changed)
    assert client.post(_urls(report_id, ws)["briefs"], json=request).status_code == 202
    assert run(db_session, dispatched) == "completed"
    after = _view_text(db_session, ws, report_id, "executive")
    assert len(after.split("başlatılsın mı?")) == len(questions)  # karar sorulari degismedi
    assert "İncelenmemiş yeni reklam metni" in _view_text(db_session, ws, report_id, "implementation")
    # Belgede incelenmemis metin var: onay duser
    assert get(client, report_id, ws).json()["strategy_state"] == "draft"
    assert "TASLAK: analist onayı yok" in _view_text(db_session, ws, report_id, "implementation")


# --- G7 ve sonrasi: kaynak duzeni, gruplu eksikler, SEO aksiyon tablosu, kapak, uyum listesi ---------

def test_source_type_is_never_guessed_and_conflicting_numbers_are_flagged(client, db_session, make_workspace,
                                                                         dispatched, monkeypatch):
    import sys

    assert research.source_type("https://www.saglik.gov.tr/x") == "resmî (alan adına göre)"
    assert research.source_type("https://ajans-blogu.com/x") == research.source_type("https://oda.org.tr/x") == "belirlenmedi"
    monkeypatch.setattr(sys.modules[__name__], "SOURCE_TEXT",
                        SOURCE_TEXT + " Başka bir kaynağa göre 2023 yılında 51 000 diş hekimi vardı.")
    sector = {**SECTOR, "claims": SECTOR["claims"] + [
        {"claim": "Toplam diş hekimi sayısı", "value": "51.000", "unit": "kişi", "year": "2023", "geography": "Türkiye",
         "source_name": "Blog"}]}
    monkeypatch.setitem(PAYLOADS, "audit_sector_research", sector)
    ws, other = make_workspace("Marka"), make_workspace("Diger")
    audit, report_id, url, _ = strategy_ready(client, db_session, ws, dispatched, monkeypatch)
    approve = url.replace("/strategy?", "/strategy/approve?")
    review = f"/api/v1/digital-audit/research/{report_id}/claim-review?brand_profile_id={ws.id}"
    text = _blocks(db_session, ws, report_id)
    assert "2 rakamın kaynak türü belirlenmedi" in text
    assert "Karşılaştırılacak değerler (tanım, yıl ya da kapsam farklı olabilir)" in text
    assert "Aynı şeyi ölçüp ölçmedikleri doğrulanmadı" in text
    assert client.post(approve, json=_seen(client, report_id, ws, 0, "Analist A")).json()["strategy_state"] == "approved"

    assert client.put(review, json={"claim_id": "sector:0", "source_type": "çok güvenilir"}).status_code == 400
    assert client.put(review, json={"claim_id": "sector:99", "source_type": "ikincil"}).status_code == 400
    assert client.put(review.replace(f"brand_profile_id={ws.id}", f"brand_profile_id={other.id}"),
                      json={"claim_id": "sector:0", "source_type": "ikincil"}).status_code == 404
    res = client.put(review, json={"claim_id": "sector:0", "source_type": "birincil",
                                   "note": "Meslek odası verisi; diğer kaynak tahmin içeriyor."})
    assert res.status_code == 200 and res.json()["strategy_state"] == "draft"  # belgeye yeni icerik girdi: onay dustu
    text = _blocks(db_session, ws, report_id)
    assert "1 rakamın kaynak türü belirlenmedi" in text and "birincil" in text
    assert "Açıklama: Meslek odası verisi; diğer kaynak tahmin içeriyor." in text and "analist açıklaması bekliyor" not in text
    # Kaynak listesi artik duz adres degil: ad, adres, tur, kontrol tarihi, bolum
    report = db_session.get(DigitalResearch, report_id)
    from app.core.digital_audit import report_doc

    table = next(b for b in report_doc.build_blocks(ws, None, report) if b[0] == "table" and b[1][0] == "Kaynak")
    assert table[1] == ["Kaynak", "Adres", "Tür", "Kontrol tarihi", "Kullanıldığı bölüm"]
    assert table[2][0][0] == "kaynak.test" and "belirlenmedi; HTML" in table[2][0][2] and table[2][0][4] in ("Sektör", "Hedef kitle")


def test_documents_have_cover_contents_grouped_gaps_and_seo_actions(client, db_session, make_workspace, dispatched,
                                                                   monkeypatch):
    from app.core.digital_audit import report_doc

    ws = make_workspace("Marka")
    audit, report_id, url, _ = strategy_ready(client, db_session, ws, dispatched, monkeypatch)
    assert client.post(_urls(report_id, ws)["briefs"], json={"segments": [0], "channels": ["meta"]}).status_code == 202
    assert run(db_session, dispatched) == "completed"
    db_session.expire_all()
    report = db_session.get(DigitalResearch, report_id)
    audit = db_session.get(DigitalAudit, report.audit_id)

    blocks = report_doc.build_blocks(ws, audit, report, "executive")
    kinds = [b[0] for b in blocks]
    # Kapak sayfasi: kunye + icindekiler, ardindan sayfa sonu; ana bolumler numarali ve icindekilerle ayni
    cut = kinds.index("pagebreak")
    assert ("h2", "İçindekiler") in blocks[:cut] and not any(k == "h1" for k in kinds[:cut])
    contents = blocks[cut - 1][1]
    assert contents[0] == "1. Yönetici özeti" and contents == [b[1] for b in blocks if b[0] == "h1"]
    cover = next(b for b in blocks if b[0] == "table" and b[1][0] == "Künye")
    assert ["Hazırlayan", "Digitus"] in cover[2] and ["İnceleyen analist", "Henüz incelenmedi"] in cover[2]
    # Eksikler uc grupta, sorumlu rol ve etkilenen bolumle
    gaps = next(b for b in blocks if b[0] == "table" and b[1][0] == "Grup")
    assert gaps[1] == ["Grup", "Eksik / istenecek", "Sorumlu (rol)", "Etkilenen bölüm"]
    groups = [row[0] for row in gaps[2]]
    assert groups == sorted(groups, key=["Müşteriden istenecek", "Hesap erişimiyle çekilecek", "Ajans tarafında doğrulanacak"].index)
    assert any("1 mevzuat kısıtı birincil kaynaktan teyit edilmedi." in row[1] for row in gaps[2])
    assert any(row[0] == "Hesap erişimiyle çekilecek" and "bu analize alınmadı" in row[1] for row in gaps[2])
    # Firsatlarda oncelik sutunu; rekabet bolumu sonuc cumlesi yoksa bunu soyler
    opportunities = next(b for b in blocks if b[0] == "table" and b[1][0] == "Fırsat")
    assert opportunities[1][2] == "Öncelik" and opportunities[2][0][2] == "belirtilmedi"
    assert "Sonuç cümlesi: bu taslakta üretilmedi." in repr(blocks)

    detail = report_doc.build_blocks(ws, audit, report, "detail")
    seo = next(b for b in detail if b[0] == "table" and b[1][0] == "Kontrol (marka sitesi)")
    assert seo[1] == ["Kontrol (marka sitesi)", "Durum", "Bulgu", "Etki", "Önerilen aksiyon"]
    assert seo[2] == [["Sayfa başlıkları", "Uyarı", "3 eksik (incelenen 10 sayfada)",
                       "Google'da görünen ilk satır budur; eksikse sayfa aramada zayıf kalır.",
                       "Her sayfaya kendine özgü, açıklayıcı bir başlık yazıyoruz."]]
    for fmt, magic in (("docx", b"PK"), ("pdf", b"%PDF")):  # sayfa sonu iki bicimde de cizilir
        assert report_doc.render(ws, audit, report, fmt, "executive")[0].startswith(magic)
    # Her briefte dort maddelik uyum listesi
    text = repr(report_doc.build_blocks(ws, audit, report, "implementation"))
    for item in ("Uyum 1/4, vaat ve garanti: otomatik taramada işaretlenmedi; analist okuması gerekir",
                 "Uyum 2/4, üstünlük iddiası:", "Uyum 3/4, sektör kuralları:", "Uyum 4/4, yayın türü: sponsorlu"):
        assert item in text


def test_strategy_accepts_opportunity_priority_and_competition_conclusion(client, db_session, make_workspace,
                                                                          dispatched, monkeypatch):
    ws = make_workspace("Marka")
    audit, report_id, url, _ = strategy_ready(client, db_session, ws, dispatched, monkeypatch)
    record = get(client, report_id, ws).json()["sections"]["strategy"]
    own = next(c["id"] for c in record["catalogue"] if c["kind"].startswith("Ölçüm (marka"))
    rival = next(c["id"] for c in record["catalogue"] if c["kind"] == "Rakip gözlemi")
    edited = json.loads(json.dumps(record["draft"]))
    edited["opportunities"][0]["priority"] = "p2"
    edited["competition_conclusion"] = [{"text": "Rakipte başlık eksiği yok; marka bu açığı kapatmalı", "refs": [own, rival]},
                                        {"text": "İkinci sonuç", "refs": [own]}]
    res = client.put(url, json={"version": 1, "revision": 0, "content": edited}).json()["sections"]["strategy"]["edited"]
    assert res["opportunities"][0]["priority"] == "P2" and len(res["competition_conclusion"]) == 1  # tek sonuc cumlesi
    text = _view_text(db_session, ws, report_id, "executive")
    assert "Sonuç (rakiplerin boşlukları ve markanın yararlanma yolu) [Hipotez]: Rakipte başlık eksiği yok" in text


def test_source_read_date_does_not_move_when_later_stages_run(client, db_session, make_workspace, dispatched, monkeypatch):
    from app.core.digital_audit import report_doc

    def source_dates(report_id, ws):
        db_session.expire_all()
        table = next(b for b in report_doc.build_blocks(ws, None, db_session.get(DigitalResearch, report_id))
                     if b[0] == "table" and b[1][0] == "Kaynak")
        return {row[3] for row in table[2]}

    ws = make_workspace("Marka")
    audit, report_id, url, _ = strategy_ready(client, db_session, ws, dispatched, monkeypatch)
    report = db_session.get(DigitalResearch, report_id)
    # Okuma tarihi bolumle birlikte saklanir
    assert report.sections["sector"]["sources_checked_at"] == datetime.now(timezone.utc).date().isoformat()
    sections = {**report.sections}
    for name in ("sector", "audience"):
        sections[name] = {**sections[name], "sources_checked_at": "2026-11-01"}  # kaynaklar 1 Kasim'da okundu
    report.sections = sections
    report.completed_at = datetime(2026, 11, 1, tzinfo=timezone.utc)
    db_session.commit()
    # Sonradan brief uretilir: kaydin tamamlanma tarihi degisir, kaynaklar YENIDEN OKUNMAZ
    assert client.post(_urls(report_id, ws)["briefs"], json={"segments": [0], "channels": ["meta"]}).status_code == 202
    assert run(db_session, dispatched) == "completed"
    assert db_session.get(DigitalResearch, report_id).completed_at.date() != datetime(2026, 11, 1).date()
    assert source_dates(report_id, ws) == {"01.11.2026"}
    # Okuma tarihi saklanmamis eski kayit: sonraki uretimin tarihi kullanilmaz, bilinmedigi yazilir
    report = db_session.get(DigitalResearch, report_id)
    report.sections = {**report.sections, **{n: {k: v for k, v in report.sections[n].items() if k != "sources_checked_at"}
                                             for n in ("sector", "audience")}}
    db_session.commit()
    (label,) = source_dates(report_id, ws)
    assert label.startswith("kaydedilmemiş (araştırma başlangıcı ")


def test_segments_carry_intent_hesitation_and_validation_method(client, db_session, make_workspace, dispatched,
                                                               monkeypatch):
    ws = make_workspace("Marka")
    report_id = completed_report(client, db_session, ws, dispatched)
    text = _blocks(db_session, ws, report_id)  # eski semayla uretilmis segment: alan bos, uydurulmaz
    assert "Arama niyeti: belirtilmedi" in text and "Doğrulama yöntemi: belirtilmedi" in text
    synthesis = json.loads(json.dumps(SYNTHESIS))
    synthesis["segments"][0].update(search_intent="acil diş hekimi araması", hesitation="Randevu bulamama",
                                    validation_method="müşteri görüşmesi")
    monkeypatch.setitem(PAYLOADS, "audit_synthesis", synthesis)
    ai = FakeAI()
    assert client.post(_urls(report_id, ws)["synthesis"]).status_code == 202
    assert run(db_session, dispatched, ai) == "completed"
    assert "validation_method" in ai.prompts["audit_synthesis"]
    text = _blocks(db_session, ws, report_id)
    assert "Arama niyeti: acil diş hekimi araması" in text and "Tereddüt: Randevu bulamama" in text
    assert "Doğrulama yöntemi: müşteri görüşmesi" in text


def test_sector_meanings_content_plan_and_kpi_table(client, db_session, make_workspace, dispatched, monkeypatch):
    ws = make_workspace("Marka")
    audit, report_id, url, _ = strategy_ready(client, db_session, ws, dispatched, monkeypatch)
    record = get(client, report_id, ws).json()["sections"]["strategy"]
    number = next(c["id"] for c in record["catalogue"] if c["kind"] == "Dış kaynak (rakam)")
    assert any(c["kind"] == "Dış kaynak (sektör hizmetleri)" for c in record["catalogue"])  # alt bolumun dayanagi
    detail = _view_text(db_session, ws, report_id, "detail")
    assert detail.count("Marka için anlamı: bu taslakta üretilmedi.") >= 2  # verisi olan alt bolumde eksik gorunur
    edited = json.loads(json.dumps(record["draft"]))
    meaning = {"topic": "numbers", "text": "Hekim sayısı yüksek; yerel görünürlük ayrıştırıcı olur", "refs": [number]}
    for bad in ([{**meaning, "topic": "olmayan"}], [meaning, meaning], [{**meaning, "refs": []}]):
        assert client.put(url, json={"version": 1, "revision": 0, "content": {**edited, "sector_meanings": bad}}).status_code == 400
    edited["sector_meanings"] = [meaning]
    assert client.put(url, json={"version": 1, "revision": 0, "content": edited}).status_code == 200
    detail = _view_text(db_session, ws, report_id, "detail")
    assert "Marka için anlamı [Hipotez]: Hekim sayısı yüksek; yerel görünürlük ayrıştırıcı olur" in detail

    assert client.post(_urls(report_id, ws)["briefs"], json={"segments": [0], "channels": ["meta"]}).status_code == 202
    assert run(db_session, dispatched) == "completed"
    from app.core.digital_audit import report_doc

    db_session.expire_all()
    report = db_session.get(DigitalResearch, report_id)
    blocks = report_doc.build_blocks(ws, db_session.get(DigitalAudit, report.audit_id), report, "implementation")
    plan = next(b for b in blocks if b[0] == "table" and b[1][0] == "Dönem")
    assert plan[1] == ["Dönem", "İçerik", "Yer", "Yayın türü", "Sorumlu (rol)"]
    assert any("Dişiniz mi ağrıyor?" in row[1] and row[0] == "Karara bağlı" for row in plan[2])  # kisitli sektorde brief
    kpi = next(b for b in blocks if b[0] == "table" and b[1][0] == "Gösterge / test hipotezi")
    assert ["Eksik başlık sayısı", "Sayfa başlıklarını tamamla", "0-30 gün", "ölçülmedi"] in kpi[2]
    assert all(row[3] == "ölçülmedi" for row in kpi[2])  # baslangic degeri uydurulmaz


def test_content_plan_does_not_depend_on_briefs_and_prints_the_real_publication_type(client, db_session, make_workspace,
                                                                                     dispatched, monkeypatch):
    from app.core.digital_audit import report_doc

    def tables(view="implementation"):
        db_session.expire_all()
        report = db_session.get(DigitalResearch, report_id)
        blocks = report_doc.build_blocks(ws, db_session.get(DigitalAudit, report.audit_id), report, view)
        return {b[1][0]: b[2] for b in blocks if b[0] == "table"}, repr(blocks)

    ws = make_workspace("Marka")
    audit, report_id, url, _ = strategy_ready(client, db_session, ws, dispatched, monkeypatch)
    record = get(client, report_id, ws).json()["sections"]["strategy"]
    edited = json.loads(json.dumps(record["draft"]))
    held = next(d for d in edited["decisions"] if d.get("held_action"))
    edited["decisions"].remove(held)
    # Analist dayanak yazarak sponsorlu aksiyonu plana alir; ayrica organik bir icerik aksiyonu ekler
    edited["actions"].append({**held["held_action"], "ad_status": "önerilebilir", "ad_basis": "Hukuk görüşü 12.11.2026"})
    edited["actions"].append({**edited["actions"][0], "action": "Blog yazısı yayımla", "kind": "içerik",
                              "channel": "website", "publication": "organik", "kpi": "Blog ziyareti"})
    assert client.put(url, json={"version": 1, "revision": 0, "content": edited}).status_code == 200

    found, text = tables()
    # Brief HIC uretilmedi: plan ve KPI yine belgede; yalniz brief satirlari yok
    assert "Seçilmiş segmentler için brief üretilmedi." in text
    plan = {row[1]: row for row in found["Dönem"]}
    assert plan["Blog yazısı yayımla"][2:4] == ["Web sitesi / blog", "organik"]
    # Sponsorlu aksiyon "organik" diye yazilmaz; uygunluk durumu stratejideki kararla ayni
    paid = plan["Acil hasta için arama reklamı aç"]
    assert paid[3] == "sponsorlu (öneri durumu: önerilebilir; analist dayanağı: Hukuk görüşü 12.11.2026)"
    assert not any("Dişiniz mi ağrıyor?" in row[1] for row in found["Dönem"])
    assert ["Blog ziyareti", "Blog yazısı yayımla", "0-30 gün", "ölçülmedi"] in found["Gösterge / test hipotezi"]

    # Brief uretilince brief satirlari eklenir; strateji satirlari yerinde kalir
    assert client.post(_urls(report_id, ws)["briefs"], json={"segments": [0], "channels": ["meta"]}).status_code == 202
    assert run(db_session, dispatched) == "completed"
    found, _ = tables()
    assert any("Dişiniz mi ağrıyor?" in row[1] for row in found["Dönem"]) and "Blog yazısı yayımla" in {r[1] for r in found["Dönem"]}
    # Arama agindaki brief organik sayilmaz
    from app.core.digital_audit import report_views
    assert report_views._brief_publication({"channel": "Google Arama Ağı"}, "x").startswith("sponsorlu")
    assert report_views._brief_publication({"channel": "Instagram / Facebook"}, "x") == "belirtilmedi (x)"


def test_analyst_can_exclude_a_segment_stage_opportunity_with_a_reason(client, db_session, make_workspace, dispatched,
                                                                       monkeypatch):
    ws, other = make_workspace("Marka"), make_workspace("Diger")
    audit, report_id, url, _ = strategy_ready(client, db_session, ws, dispatched, monkeypatch)
    approve = url.replace("/strategy?", "/strategy/approve?")
    path = "/api/v1/digital-audit/research/{}/synthesis/opportunities/{}/exclusion?brand_profile_id={}"
    assert "Soru içerikleri (dayanak: soru aramaları)" in _blocks(db_session, ws, report_id)
    assert client.post(approve, json=_seen(client, report_id, ws, 0, "Analist A")).json()["strategy_state"] == "approved"
    body = {"reason": "Harita sırası ölçülmedi; iddianın dayanağı yok.", "excluded_by": "Analist A"}
    assert client.put(path.format(report_id, 0, other.id), json=body).status_code == 404
    assert client.put(path.format(report_id, 9, ws.id), json=body).status_code == 400
    assert client.put(path.format(report_id, 0, ws.id), json={**body, "reason": "kısa"}).status_code == 400
    assert client.put(path.format(report_id, 0, ws.id), json={**body, "excluded_by": ""}).status_code == 400
    res = client.put(path.format(report_id, 0, ws.id), json=body)
    # Belge degisti: onay duser. Uretimler BAYATLAMAZ (madde dayanak ya da brief girdisi degil): yeni AI gerekmez
    assert res.status_code == 200 and res.json()["strategy_state"] == "draft"
    assert not res.json()["synthesis_stale"] and not res.json()["briefs_stale"]
    text = _blocks(db_session, ws, report_id)
    assert "Soru içerikleri (dayanak" not in text
    assert "Analist bir fırsat maddesini rapordan çıkardı. Gerekçe: Harita sırası ölçülmedi; iddianın dayanağı yok. (Analist A)" in text
    stored = db_session.get(DigitalResearch, report_id).sections["synthesis"]["opportunities"][0]
    assert stored["text"] == "Soru içerikleri" and stored["excluded"]["by"] == "Analist A"  # kayit silinmez
    # Bos gerekce cikarmayi geri alir
    assert client.put(path.format(report_id, 0, ws.id), json={}).status_code == 200
    assert "Soru içerikleri (dayanak: soru aramaları)" in _blocks(db_session, ws, report_id)
