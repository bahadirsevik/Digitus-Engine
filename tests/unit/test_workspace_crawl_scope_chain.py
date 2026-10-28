"""crawl_scope zinciri (plan_marka_alt_sayfa.md Faz 2): olustur -> analiz -> cache
kullanimi -> cache gecersiz -> yeniden crawl -> yeniden analiz.

Kabul sarti: `page` workspace HICBIR yolda sessizce `site` crawl'una dusmez.
Gercek test DB'si; AI ve crawler sahte (ag/ucretli cagri yok). Sahte extractor
aldigi kapsami KAYDEDER.
"""
from __future__ import annotations

import pytest

import app.core.site_analyzer.profile_extractor as extractor_module
import app.generators.ai_service as ai_service_module
from app.api.v1 import brand_profile as brand_profile_api
from app.core.site_analyzer.analysis_attempt import lock_workspace_row, start_attempt
from app.core.site_analyzer.profile_extractor import resolve_site_content
from app.database.connection import SessionLocal
from app.database.models import BrandProfile

# kalite kapisindan gecen gercekci uzunlukta cache
GOOD_CACHE = (
    "Hisse analiz platformu. Borsa Istanbul hisseleri icin teknik ve temel "
    "analiz araclari sunuyoruz. Portfoy takibi, hisse tarama, finansal "
    "tablo analizi ve gercek zamanli fiyat verisi ozelliklerimiz vardir. "
    "Yatirimcilar icin karar destek raporlari uretiyoruz."
)
BAD_CACHE = "Yukleniyor..."  # kalite kapisindan gecmez -> yeniden crawl
FRESH = GOOD_CACHE + " Taze crawl icerigi."


def _profile() -> dict:
    return {
        "company_name": "Acme",
        "sector": "Finansal teknoloji",
        "brand_summary": "Hisse analiz platformu",
        "target_audience": "Yatirimcilar",
        "products": ["Analiz Platformu"],
        "services": [],
        "use_cases": ["Borsa takibi"],
        "problems_solved": ["Dagitik veri"],
        "brand_terms": ["Acme"],
        "exclude_themes": [],
        "anchor_texts": ["Analiz Platformu"],
    }


class FakeAI:
    collector = None

    def close(self):
        return None


class RecordingExtractor:
    crawl_scopes: list = []
    extract_scopes: list = []

    def __init__(self, _ai):
        pass

    @classmethod
    def reset(cls):
        cls.crawl_scopes = []
        cls.extract_scopes = []

    def crawl_for_profile_content(self, _url, crawl_scope="site"):
        type(self).crawl_scopes.append(crawl_scope)
        return {"error": None, "site_content": FRESH, "source_pages": ["https://fresh.test"]}

    def extract_profile_from_site_content(self, _content, crawl_scope="site"):
        type(self).extract_scopes.append(crawl_scope)
        return _profile()

    def suggest_keywords(self, _content, **_kwargs):
        return ["kw one", "kw two"]

    def suggest_keywords_from_profile(self, _site, _profile_data, **_kwargs):
        return ["kw one", "kw two"]

    def extract_profile_from_keywords(self, _site, _keywords, **_kwargs):
        return _profile()

    def validate_with_competitors(self, _profile_data, _urls):
        return {"competitors": [], "consistency_score": 1.0}


@pytest.fixture(autouse=True)
def fake_stack(monkeypatch):
    RecordingExtractor.reset()
    monkeypatch.setattr(ai_service_module, "get_ai_service", lambda **_kwargs: FakeAI())
    monkeypatch.setattr(extractor_module, "ProfileExtractor", RecordingExtractor)
    yield
    RecordingExtractor.reset()


def _token(workspace_id: int) -> str:
    """Gercek dispatch kalibi: kilit -> running -> yeni token -> commit."""
    session = SessionLocal()
    try:
        workspace = lock_workspace_row(session, workspace_id)
        workspace.status = "running"
        token = start_attempt(workspace)
        session.commit()
        return token
    finally:
        session.close()


def _make(make_workspace, scope: str, cache):
    return make_workspace(
        status="pending",
        onboarding_flow="profile_first",
        profile_data=_profile(),
        crawl_content_cache=cache,
        crawl_scope=scope,
    )


# ── olusturma ──────────────────────────────────────────────────────


@pytest.fixture
def no_dispatch(monkeypatch):
    monkeypatch.setattr(brand_profile_api, "_run_profile_analysis_first", lambda **_kw: None)
    monkeypatch.setattr(brand_profile_api, "_run_keyword_suggestion", lambda **_kw: None)


def _create(client, db_session, **body):
    resp = client.post("/api/v1/brand-profile/workspaces", json=body)
    return resp


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://example.test/hizmetler/ameliyat", "page"),
        ("example.test/hizmetler/ameliyat", "page"),
        ("https://example.test", "site"),
        ("https://example.test/", "site"),
        ("https://example.test/?utm=1#x", "site"),
        ("example.test", "site"),
    ],
)
def test_create_derives_scope_from_url(client, db_session, no_dispatch, url, expected):
    resp = _create(client, db_session, company_url=url, flow_version="profile_first")
    assert resp.status_code == 201
    assert resp.json()["crawl_scope"] == expected
    row = db_session.get(BrandProfile, resp.json()["id"])
    assert row.crawl_scope == expected


def test_create_explicit_scope_wins_over_url(client, db_session, no_dispatch):
    resp = _create(
        client, db_session,
        company_url="https://example.test/hizmetler/ameliyat", crawl_scope="site",
    )
    assert resp.status_code == 201
    assert resp.json()["crawl_scope"] == "site"
    assert db_session.get(BrandProfile, resp.json()["id"]).crawl_scope == "site"


def test_create_rejects_invalid_scope(client, db_session, no_dispatch):
    resp = _create(client, db_session, company_url="https://example.test", crawl_scope="all")
    assert resp.status_code == 422


def test_list_and_detail_carry_scope(client, db_session, no_dispatch):
    created = _create(
        client, db_session, company_url="https://example.test/a/b", flow_version="profile_first"
    ).json()
    listed = client.get("/api/v1/brand-profile/workspaces").json()
    assert [w["crawl_scope"] for w in listed if w["id"] == created["id"]] == ["page"]
    detail = client.get(f"/api/v1/brand-profile/workspaces/{created['id']}").json()
    assert detail["crawl_scope"] == "page"


def test_new_row_default_is_site(make_workspace):
    assert make_workspace().crawl_scope == "site"


# ── arka plan: kapsam KILITLI satirdan okunur ────────────────────────


@pytest.mark.parametrize("scope", ["page", "site"])
def test_profile_analysis_first_carries_scope_to_crawl_and_extract(scope, make_workspace):
    ws = _make(make_workspace, scope, None)
    brand_profile_api._run_profile_analysis_first(
        workspace_id=ws.id, company_url=ws.company_url,
        competitor_urls=[], attempt_id=_token(ws.id),
    )
    assert RecordingExtractor.crawl_scopes == [scope]
    assert RecordingExtractor.extract_scopes == [scope]


def test_page_missing_service_error_reaches_user_outside_debug(make_workspace, monkeypatch):
    from app.config import settings
    from app.core.site_analyzer.crawler import CrawlContentUnusable
    from app.core.site_analyzer.profile_extractor import PROFILE_MISSING_SERVICE_ERROR

    def _raise(self, _content, crawl_scope="site"):
        raise CrawlContentUnusable(PROFILE_MISSING_SERVICE_ERROR)

    monkeypatch.setattr(settings, "DEBUG", False)
    monkeypatch.setattr(RecordingExtractor, "extract_profile_from_site_content", _raise)
    ws = _make(make_workspace, "page", None)
    brand_profile_api._run_profile_analysis_first(
        workspace_id=ws.id, company_url=ws.company_url,
        competitor_urls=[], attempt_id=_token(ws.id),
    )
    session = SessionLocal()
    try:
        row = session.get(BrandProfile, ws.id)
        assert row.status == "failed"
        assert "profile_missing_service" in row.error_message
    finally:
        session.close()


@pytest.mark.parametrize("scope", ["page", "site"])
def test_legacy_keyword_suggestion_carries_scope_to_crawl(scope, make_workspace):
    ws = _make(make_workspace, scope, None)
    brand_profile_api._run_keyword_suggestion(
        workspace_id=ws.id, company_url=ws.company_url,
        competitor_urls=[], attempt_id=_token(ws.id),
    )
    assert RecordingExtractor.crawl_scopes == [scope]


def _run_from_profile(ws_id):
    brand_profile_api._run_keyword_suggestion_from_profile(ws_id, attempt_id=_token(ws_id))


def _run_from_keywords(ws_id):
    brand_profile_api._run_profile_from_keywords(
        workspace_id=ws_id, keywords=["kw one", "kw two"], attempt_id=_token(ws_id),
    )


@pytest.mark.parametrize("run", [_run_from_profile, _run_from_keywords])
@pytest.mark.parametrize("scope", ["page", "site"])
def test_usable_cache_means_no_crawl(run, scope, make_workspace):
    ws = _make(make_workspace, scope, GOOD_CACHE)
    run(ws.id)
    assert RecordingExtractor.crawl_scopes == []


@pytest.mark.parametrize("run", [_run_from_profile, _run_from_keywords])
@pytest.mark.parametrize("scope", ["page", "site"])
def test_unusable_cache_recrawls_with_workspace_scope(run, scope, make_workspace):
    ws = _make(make_workspace, scope, BAD_CACHE)
    run(ws.id)
    assert RecordingExtractor.crawl_scopes == [scope]


# ── resolve_site_content: varsayilan yok ───────────────────────────


def test_resolve_site_content_requires_scope():
    with pytest.raises(TypeError):
        resolve_site_content(RecordingExtractor(None), "https://example.test", BAD_CACHE)


def test_resolve_site_content_passes_scope_on_recrawl():
    content, pages, error = resolve_site_content(
        RecordingExtractor(None), "https://example.test/a", BAD_CACHE, crawl_scope="page"
    )
    assert error is None and content == FRESH and pages
    assert RecordingExtractor.crawl_scopes == ["page"]
