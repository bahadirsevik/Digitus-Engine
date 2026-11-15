"""Faz 3 veri zenginlestirme: arama gorunurlugu, YouTube, Google profil kimligi.

Ag ve Celery YOK: arama, YouTube ve Places adaptorleri sahtelenir.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.core.digital_audit import report_doc, research, runner, service, visibility
from app.database.models import DigitalAudit
from app.integrations import places, serpapi, youtube

SOCIAL = {"section": "contact", "check": "contact.social_links", "label": "Sosyal hesaplar", "state": "observed",
          "assessment": "info", "value": {"accounts": {"youtube": "https://www.youtube.com/@marka"}},
          "note": None, "source": None, "limits": []}
TITLE = {"section": "seo", "check": "seo.titles", "label": "Sayfa başlıkları", "state": "observed",
         "assessment": "ok", "value": {"home_title": "Rakip Klinik"}, "note": None, "source": None, "limits": []}
CHANNEL = {"status": "ok", "url": "u", "title": "Marka TV", "subscribers": 1200, "video_count": 40,
           "total_views": 90000, "last_upload": "2026-11-01", "uploads_90d": 3, "recent_avg_views": 850,
           "recent_sample": 20}


def _organic(domains, page=0):
    return [{"position": page * 10 + i + 1, "domain": d, "title": f"Başlık {d}", "shown": f"https://{d}"}
            for i, d in enumerate(domains)]


class FakeSerp:
    """Sayfa 1: rakip 2. sirada; sayfa 2: marka 14. sirada. 'yok' kelimesinde ikisi de yok."""

    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def __call__(self, query, location, page=0):
        self.calls.append((query, page))
        if self.fail:
            raise serpapi.SerpError("quota")
        filler = [f"site{i}.test" for i in range(10)]
        if "yok" in query:
            domains = filler
        elif page == 0:
            domains = ["dizin.test", "rakip.test"] + filler[:8]
        else:
            domains = filler[:3] + ["marka.test"] + filler[3:9]
        return {"organic": _organic(domains, page), "ads": ["rakip.test"] if page == 0 else [],
                "local_pack": True, "knowledge_panel": True, "location_used": "Istanbul,Turkey"}


@pytest.fixture
def dispatched(monkeypatch):
    from app.tasks import digital_audit_tasks

    calls = []
    monkeypatch.setattr(digital_audit_tasks.run_digital_audit_task, "apply_async",
                        lambda args=None, task_id=None, **kw: calls.append({"args": args, "task_id": task_id}))
    return calls


@pytest.fixture
def services(monkeypatch):
    """Site taramasi, arama, YouTube ve Places sahte: hicbir test aga cikmaz."""
    fake = FakeSerp()
    monkeypatch.setattr(runner, "run_target", lambda url, own: {
        "status": "completed", "pages_checked": 3, "findings": [SOCIAL if own else TITLE], "error_message": None})
    monkeypatch.setattr(serpapi, "is_configured", lambda: True)
    monkeypatch.setattr(serpapi, "search", fake)
    monkeypatch.setattr(youtube, "channel_summary", lambda url, now=None: dict(CHANNEL))
    monkeypatch.setattr(places, "is_configured", lambda: True)
    monkeypatch.setattr(places, "find_place_id", lambda query, site: "PLACE_OWN" if site == "marka.test" else None)
    return fake


def start(client, ws, visibility_request=None, **body):
    payload = {"competitor_urls": ["rakip.test"], **body}
    if visibility_request is not None:
        payload["visibility"] = visibility_request
    return client.post(f"/api/v1/digital-audit/audits?brand_profile_id={ws.id}", json=payload)


def run(db, dispatched):
    return service.run_audit(db, dispatched[-1]["args"][0], dispatched[-1]["task_id"])


def detail(client, audit_id, ws):
    return client.get(f"/api/v1/digital-audit/audits/{audit_id}?brand_profile_id={ws.id}").json()


def test_audit_without_visibility_request_spends_no_queries(client, db_session, make_workspace, dispatched, services):
    ws = make_workspace("Marka", company_url="https://marka.test")
    audit_id = start(client, ws).json()["audit_id"]
    assert run(db_session, dispatched) == "completed"
    assert services.calls == [] and detail(client, audit_id, ws)["visibility"] is None


def test_visibility_records_ranks_ads_brand_and_scope(client, db_session, make_workspace, dispatched, services):
    ws = make_workspace("Marka", company_url="https://marka.test")
    audit_id = start(client, ws, {"keywords": ["diş hekimi", " Diş  Hekimi ", "yok kelime"],
                                  "location": "Izmir, Turkey", "brand_name": "Marka Klinik"}).json()["audit_id"]
    assert run(db_session, dispatched) == "completed"
    body = detail(client, audit_id, ws)
    assert body["scope"]["visibility"]["keywords"] == ["diş hekimi", "yok kelime"]  # tekrar eden kelime bir kez
    data = body["visibility"]
    serp = data["serp"]
    first, missing = serp["keywords"]
    assert first["positions"] == {"marka.test": 14, "rakip.test": 2} and first["checked_top"] == 20
    assert first["ads"] == ["rakip.test"] and first["local_pack"] is True
    # "Gozlenmedi" olculen derinlikle birlikte saklanir
    assert missing["positions"] == {"marka.test": None, "rakip.test": None} and missing["checked_top"] == 20
    assert serp["brand"]["own_position"] is None and serp["brand"]["other_ads"] == ["rakip.test"]
    assert [r["query"] for r in serp["reputation"]] == ["Marka Klinik şikayet", "Marka Klinik yorum"]
    # Konum, cihaz, tarih ve harcanan sorgu acik
    assert serp["location_used"] == "Istanbul,Turkey" and serp["device"] == "desktop" and data["measured_at"]
    assert serp["queries_used"] == 7 == len(services.calls) and serp["query_limit"] == visibility.MAX_SERP_QUERIES
    assert data["youtube"]["channels"]["marka.test"]["title"] == "Marka TV"
    assert data["youtube"]["channels"]["rakip.test"]["status"] == "not_observed"
    assert body["targets"][0]["findings"] == [SOCIAL]  # site denetimi bulgulari degismez


def test_second_page_is_not_bought_when_every_site_is_on_page_one(client, db_session, make_workspace, dispatched,
                                                                  services, monkeypatch):
    monkeypatch.setattr(serpapi, "search", lambda q, loc, page=0: services.calls.append((q, page)) or {
        "organic": _organic(["marka.test", "rakip.test"]), "ads": [], "local_pack": False,
        "knowledge_panel": False, "location_used": "x"})
    ws = make_workspace("Marka", company_url="https://marka.test")
    audit_id = start(client, ws, {"keywords": ["diş hekimi"]}).json()["audit_id"]
    run(db_session, dispatched)
    row = detail(client, audit_id, ws)["visibility"]["serp"]["keywords"][0]
    assert services.calls == [("diş hekimi", 0)] and row["checked_top"] == 10 and row["ads"] == []


def test_query_ceiling_is_enforced(client, db_session, make_workspace, dispatched, services):
    ws = make_workspace("Marka", company_url="https://marka.test")
    too_many = {"keywords": [f"kelime {i}" for i in range(visibility.MAX_KEYWORDS + 1)]}
    assert start(client, ws, too_many).status_code == 400 and dispatched == []
    words = [f"yok {i}" for i in range(visibility.MAX_KEYWORDS)]
    start(client, ws, {"keywords": words, "brand_name": "Marka"})
    run(db_session, dispatched)
    assert len(services.calls) == visibility.MAX_SERP_QUERIES == 23


def test_search_outage_does_not_break_the_audit(client, db_session, make_workspace, dispatched, services, monkeypatch):
    """Servis kesilse cekirdek calisir; kesik servise sorgu yagdirilmaz."""
    down = FakeSerp(fail=True)
    monkeypatch.setattr(serpapi, "search", down)
    ws = make_workspace("Marka", company_url="https://marka.test")
    audit_id = start(client, ws, {"keywords": [f"k{i}" for i in range(8)], "brand_name": "Marka"}).json()["audit_id"]
    assert run(db_session, dispatched) == "completed"
    body = detail(client, audit_id, ws)
    assert body["status"] == "completed" and body["targets"][0]["findings"] == [SOCIAL]
    serp = body["visibility"]["serp"]
    assert serp["status"] == "failed" and all(row.get("error") for row in serp["keywords"])
    assert len(down.calls) == visibility.MAX_CONSECUTIVE_FAILURES
    assert body["visibility"]["youtube"]["status"] == "ok"  # diger bolumler yine alinir
    assert "quota" not in json.dumps(body)


def test_unconfigured_services_are_reported_as_unavailable(client, db_session, make_workspace, dispatched, services,
                                                           monkeypatch):
    monkeypatch.setattr(serpapi, "is_configured", lambda: False)
    monkeypatch.setattr(places, "is_configured", lambda: False)
    ws = make_workspace("Marka", company_url="https://marka.test")
    audit_id = start(client, ws, {"keywords": ["diş hekimi"]}).json()["audit_id"]
    assert run(db_session, dispatched) == "completed"
    data = detail(client, audit_id, ws)["visibility"]
    assert data["serp"]["status"] == "unavailable" and data["places"]["status"] == "unavailable"
    assert services.calls == []


def test_only_place_id_is_stored_and_live_details_are_not(client, db_session, make_workspace, dispatched, services,
                                                          monkeypatch):
    """Plan 3.3: veritabanina yalniz isletme kimligi yazilir; puan/ad/adres ekranda taze cekilir."""
    live = {"available": True, "name": "GIZLI_AD", "rating": 4.7, "rating_count": 321, "address": "GIZLI_ADRES"}
    monkeypatch.setattr(places, "live_details", lambda place_id: {**live, "asked": place_id})
    ws, other = make_workspace("Marka", company_url="https://marka.test"), make_workspace("Diğer")
    audit_id = start(client, ws, {"keywords": [], "brand_name": "Marka"}).json()["audit_id"]
    run(db_session, dispatched)
    stored = detail(client, audit_id, ws)["visibility"]["places"]["profiles"]
    assert stored["marka.test"] == {"status": "matched", "place_id": "PLACE_OWN",
                                    "maps_url": "https://www.google.com/maps/place/?q=place_id:PLACE_OWN"}
    assert stored["rakip.test"]["status"] == "not_matched"

    url = f"/api/v1/digital-audit/audits/{audit_id}/places?brand_profile_id="
    fresh = client.get(f"{url}{ws.id}").json()["profiles"]
    assert fresh == {"marka.test": {**live, "asked": "PLACE_OWN"}}
    assert client.get(f"{url}{other.id}").status_code == 404

    db_session.expire_all()
    audit = db_session.get(DigitalAudit, audit_id)
    saved = json.dumps(audit.visibility, ensure_ascii=False)
    assert "GIZLI" not in saved and "4.7" not in saved
    # Rapor dosyasina yalniz harita baglantisi girer; yapay zekaya giden bulgularda profil yok
    text = repr(report_doc.build_blocks(ws, audit, None))
    assert "place_id:PLACE_OWN" in text and "GIZLI" not in text
    assert "PLACE_OWN" not in json.dumps(research.audit_facts(audit))


def test_report_and_synthesis_facts_carry_ranks_with_conditions(client, db_session, make_workspace, dispatched,
                                                                services):
    ws = make_workspace("Marka", company_url="https://marka.test")
    audit_id = start(client, ws, {"keywords": ["diş hekimi", "yok kelime"], "brand_name": "Marka"}).json()["audit_id"]
    run(db_session, dispatched)
    audit = db_session.get(DigitalAudit, audit_id)
    text = repr(report_doc.build_blocks(ws, audit, None))
    assert "İlk 20'de gözlenmedi" in text and "Istanbul,Turkey" in text and "Anlık ölçümdür" in text
    assert "kampanyanın kapalı olduğu anlamına gelmez" in text and "Marka TV" in text
    facts = research.audit_facts(audit)["arama_gorunurlugu"]
    assert facts["kelimeler"][0]["siralar"] == {"marka.test": 14, "rakip.test": 2}
    res = client.get(f"/api/v1/digital-audit/export?brand_profile_id={ws.id}&audit_id={audit_id}&format=pdf")
    assert res.status_code == 200 and res.content.startswith(b"%PDF")


def test_late_worker_cannot_write_visibility_after_reconcile(client, db_session, make_workspace, dispatched,
                                                             services, monkeypatch):
    ws = make_workspace("Marka", company_url="https://marka.test")
    audit_id = start(client, ws, {"keywords": ["diş hekimi"]}).json()["audit_id"]

    def slow_youtube(url, now=None):
        audit = db_session.get(DigitalAudit, audit_id)
        audit.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=5)
        db_session.commit()
        service.reconcile_expired(db_session, ws.id)
        return dict(CHANNEL)

    monkeypatch.setattr(youtube, "channel_summary", slow_youtube)
    assert run(db_session, dispatched) == "superseded"
    body = detail(client, audit_id, ws)
    assert body["status"] == "partial"  # site sonuclari korunur
    assert "serp" in body["visibility"] and "youtube" not in body["visibility"]  # kaydedilen bolum kalir, gec gelen yazilmaz
