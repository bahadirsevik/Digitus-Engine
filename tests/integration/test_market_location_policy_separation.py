"""Hedef konum ≠ konum politikası (plan_pazar_dil_secimi.md Faz 2-j).

Çalışmanın hedef konumu/dili (pazar) profilin `LOCATION_PROFILE_KEYS` alanlarına
(filtre modu, odak şehirler, muaf terimler) ASLA yazılmaz: ne analizde, ne öneri
kelimelerde, ne de boş çalışmada pazar değişince. Pazar yalnız prompt bağlamına
girer. Tüm AI çağrıları sahte — ÜCRETLİ ÇAĞRI YOK.
"""
import json

import pytest

import app.core.site_analyzer.profile_extractor as extractor_module
import app.generators.ai_service as ai_service_module
from app.api.v1 import brand_profile as brand_profile_api
from app.api.v1.brand_profile import LOCATION_PROFILE_KEYS

BERLIN, RU = "1003854", "1031"
DE_COUNTRY = "2276"
BP = "/api/v1/brand-profile"

CACHE = (
    "Klinik Berlin saç ekimi merkezi. Saç ekimi, sakal ekimi ve kaş ekimi "
    "işlemleri sunuyoruz. Uzman doktor kadrosu ile hastalarımıza danışmanlık "
    "veriyoruz. Randevu ve fiyat bilgisi için bizimle iletişime geçin."
)

LOCATION_STATE = {
    "location_filter_mode": "none",
    "focus_cities": [],
    "location_exempt_terms": ["klinik berlin"],
}


def _profile(**extra):
    base = {
        "company_name": "Klinik", "sector": "Saglik", "brand_summary": "Sac ekimi",
        "target_audience": "Hastalar", "products": [], "services": ["Sac ekimi"],
        "use_cases": ["Sac dokulmesi"], "problems_solved": ["Kellik"],
        "brand_terms": ["klinik"], "exclude_themes": ["kozmetik"],
        "anchor_texts": ["Sac ekimi"],
    }
    base.update(extra)
    return base


class FakeAI:
    collector = None

    def close(self):
        return None


class RecordingExtractor:
    """Sahte extractor; öneri çağrılarının pazar bağlamını kaydeder."""

    contexts = []

    def __init__(self, _ai):
        pass

    def crawl_for_profile_content(self, _url, crawl_scope="site"):
        return {"error": None, "site_content": CACHE, "source_pages": ["https://klinik.test"]}

    def extract_profile_from_site_content(self, _content, crawl_scope="site"):
        return _profile()  # AI profili konum alanı taşımaz

    def suggest_keywords_from_profile(self, _site, _profile_data, **kwargs):
        type(self).contexts.append(kwargs.get("output_ctx"))
        return ["sac ekimi", "sakal ekimi"]

    def suggest_keywords(self, _content, **kwargs):
        type(self).contexts.append(kwargs.get("output_ctx"))
        return ["sac ekimi"]


@pytest.fixture(autouse=True)
def fakes(monkeypatch):
    RecordingExtractor.contexts = []
    monkeypatch.setattr(ai_service_module, "get_ai_service", lambda **_k: FakeAI())
    monkeypatch.setattr(extractor_module, "ProfileExtractor", RecordingExtractor)
    monkeypatch.setattr(brand_profile_api, "UsageCollector", lambda **_k: None)


def _location_slice(profile_data):
    return {k: profile_data.get(k) for k in LOCATION_PROFILE_KEYS}


def _no_market_leak(profile_data):
    blob = json.dumps(profile_data, ensure_ascii=False)
    assert BERLIN not in blob and RU not in blob and "Almanya" not in blob
    # pazar şehir olarak odak şehirlere de, modlara da girmedi
    assert profile_data.get("focus_cities") in (None, [])
    assert profile_data.get("location_filter_mode") in (None, "none")


def test_analysis_and_keyword_suggestion_do_not_write_target_location(
    db_session, make_workspace,
):
    ws = make_workspace(
        "Berlin Ru", status="pending", onboarding_flow="profile_first",
        default_geo_target_id=BERLIN, default_language_id=RU,
        profile_data=dict(LOCATION_STATE), crawl_content_cache=CACHE,
    )
    before = _location_slice(ws.profile_data)

    brand_profile_api._run_profile_analysis_first(
        workspace_id=ws.id, company_url="https://klinik.test", competitor_urls=[],
    )
    db_session.expire_all()
    db_session.refresh(ws)
    assert ws.status == "profile_review"
    assert _location_slice(ws.profile_data) == before  # analiz yalnız taşıdı, hedef konum yazmadı
    _no_market_leak(ws.profile_data)

    brand_profile_api._run_keyword_suggestion_from_profile(ws.id)
    db_session.expire_all()
    db_session.refresh(ws)
    assert ws.suggested_keywords
    assert _location_slice(ws.profile_data) == before
    _no_market_leak(ws.profile_data)

    # Pazar yalnız öneri prompt bağlamına girdi (kilitli çalışma satırından)
    ctx = RecordingExtractor.contexts[-1]
    assert (ctx.language, ctx.location) == ("Rusça", "Berlin, Almanya — Şehir")


def test_keyword_first_suggestion_gets_market_context(db_session, make_workspace):
    ws = make_workspace(
        "Berlin Ru 2", status="pending", default_geo_target_id=BERLIN, default_language_id=RU,
    )
    brand_profile_api._run_keyword_suggestion(
        workspace_id=ws.id, company_url="https://klinik.test", competitor_urls=[],
    )
    ctx = RecordingExtractor.contexts[-1]
    assert (ctx.language, ctx.location) == ("Rusça", "Berlin, Almanya — Şehir")


def test_market_change_on_empty_workspace_leaves_location_keys(
    client, db_session, make_workspace,
):
    ws = make_workspace(
        "Berlin Ru 3", status="profile_review", onboarding_flow="profile_first",
        default_geo_target_id="2792", default_language_id="1037",
        profile_data=_profile(**LOCATION_STATE),
    )
    before = _location_slice(ws.profile_data)

    r = client.put(f"{BP}/workspaces/{ws.id}/profile/approve", json={
        "rerun_keywords": False,
        "default_geo_target_id": BERLIN, "default_language_id": RU,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["default_geo_target_id"], body["default_language_id"]) == (BERLIN, RU)

    db_session.expire_all()
    db_session.refresh(ws)
    assert _location_slice(ws.profile_data) == before
    _no_market_leak(ws.profile_data)

    # ülke seviyesine geçiş de aynı: hedef konum politikaya yazılmaz
    r = client.put(f"{BP}/workspaces/{ws.id}/profile/approve", json={
        "rerun_keywords": False,
        "default_geo_target_id": DE_COUNTRY, "default_language_id": RU,
    })
    assert r.status_code == 200, r.text
    db_session.expire_all()
    db_session.refresh(ws)
    assert _location_slice(ws.profile_data) == before
    _no_market_leak(ws.profile_data)
