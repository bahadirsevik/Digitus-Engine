import os
import sys
import json
from types import SimpleNamespace

from fastapi import HTTPException, Response

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import app.api.v1.google_ads as google_ads_api
import app.core.workspace as workspace_core
from app.api.v1.google_ads import (
    URL_SEED_CACHE_TTL_SECONDS,
    _keyword_seed_hash,
    _inspect_url_for_seed,
    _url_seed_cache_key,
    UrlSeedRequest,
    UrlSeedSourceStatus,
    keyword_ideas_by_url,
)
from app.database.crud import _legacy_keyword_data_source
from app.integrations.google_ads.service import GoogleAdsService
from app.schemas.keyword import KeywordCreate


class _Seed:
    def __init__(self):
        self.url = None
        self.keywords = []


class _Request:
    def __init__(self):
        self.customer_id = None
        self.language = None
        self.geo_target_constants = []
        self.page_size = None
        self.include_adult_keywords = None
        self.keyword_plan_network = None
        self.keyword_seed = _Seed()
        self.url_seed = _Seed()
        self.keyword_and_url_seed = _Seed()


class _PathService:
    def language_constant_path(self, language_id):
        return f"languageConstants/{language_id}"

    def geo_target_constant_path(self, geo_id):
        return f"geoTargetConstants/{geo_id}"


class _IdeaService:
    def __init__(self, response_rows):
        self.response_rows = response_rows
        self.last_request = None

    def generate_keyword_ideas(self, request):
        self.last_request = request
        return self.response_rows


class _Client:
    def __init__(self, idea_service):
        self.idea_service = idea_service
        self.enums = SimpleNamespace(
            KeywordPlanNetworkEnum=SimpleNamespace(GOOGLE_SEARCH="GOOGLE_SEARCH")
        )

    def get_type(self, name):
        assert name == "GenerateKeywordIdeasRequest"
        return _Request()

    def get_service(self, name):
        if name == "KeywordPlanIdeaService":
            return self.idea_service
        return _PathService()


def _row(text, avg=120, competition_index=37):
    metrics = SimpleNamespace(
        avg_monthly_searches=avg,
        competition_index=competition_index,
        monthly_search_volumes=[],
    )
    return SimpleNamespace(text=text, keyword_idea_metrics=metrics)


class _FakeRedis:
    def __init__(self, cached=None):
        self.cached = cached
        self.setex_calls = []

    def get(self, key):
        return self.cached

    def setex(self, key, ttl, value):
        self.setex_calls.append((key, ttl, value))


class _UrlIdea:
    def __init__(self, keyword="borsa takip"):
        self.keyword = keyword
        self.monthly_volume = 1000
        self.trend_3m = 0.0
        self.trend_12m = 0.0
        self.competition = 0.5


class _UrlSeedService:
    def __init__(self, ideas):
        self.ideas = ideas
        self.calls = 0

    def keyword_ideas_by_url(self, **kwargs):
        self.calls += 1
        return self.ideas, False, None


def _patch_url_seed_dependencies(monkeypatch, *, redis_client, ideas):
    service = _UrlSeedService(ideas)
    workspace = SimpleNamespace(
        id=77,
        created_at=None,
        default_language_id="1037",
        default_geo_target_id="2792",
        suggested_keywords=["hisse analizi"],
    )
    monkeypatch.setattr(workspace_core, "verify_workspace", lambda db, workspace_id: workspace)
    monkeypatch.setattr(google_ads_api, "_get_service", lambda: service)
    monkeypatch.setattr(google_ads_api, "_get_redis_client", lambda: redis_client)
    monkeypatch.setattr(
        google_ads_api,
        "_inspect_url_for_seed",
        lambda url: (
            UrlSeedSourceStatus(
                reachable=False,
                http_status=403,
                final_url=url,
                warning="URL sunucu kontrolunde HTTP 403 dondu",
            ),
            ["URL sunucu kontrolunde HTTP 403 dondu"],
        ),
    )
    return service


def test_url_seed_request_uses_url_seed_without_keyword_seed():
    idea_service = _IdeaService([_row("organik sampuan")])
    svc = GoogleAdsService(SimpleNamespace(GOOGLE_ADS_LANGUAGE_ID="1055", GOOGLE_ADS_GEO_TARGET_ID="2792"))
    svc._build_client = lambda: _Client(idea_service)

    ideas, truncated, reason = svc.keyword_ideas_by_url(
        customer_id="123",
        url="https://example.com",
        max_results=10,
        language_id="1055",
        geo_target_id="2792",
    )

    request = idea_service.last_request
    assert request.url_seed.url == "https://example.com"
    assert request.keyword_and_url_seed.url is None
    assert ideas[0].keyword == "organik sampuan"
    assert ideas[0].competition == 0.37
    assert truncated is False
    assert reason is None


def test_url_seed_request_can_include_workspace_keyword_seeds():
    idea_service = _IdeaService([_row("sac bakim")])
    svc = GoogleAdsService(SimpleNamespace(GOOGLE_ADS_LANGUAGE_ID="1055", GOOGLE_ADS_GEO_TARGET_ID="2792"))
    svc._build_client = lambda: _Client(idea_service)

    svc.keyword_ideas_by_url(
        customer_id="123",
        url="https://example.com",
        include_keyword_seed=True,
        keyword_seeds=["sampuan", "sac kremi"],
    )

    request = idea_service.last_request
    assert request.keyword_and_url_seed.url == "https://example.com"
    assert request.keyword_and_url_seed.keywords == ["sampuan", "sac kremi"]
    assert request.url_seed.url is None


def test_keyword_create_accepts_workspace_sources():
    kw = KeywordCreate(keyword="sac bakim", data_source="url_seed", geo_target_id="2792", language_id="1055")

    assert kw.data_source == "url_seed"
    assert kw.geo_target_id == "2792"
    assert kw.language_id == "1055"


def test_non_legacy_sources_do_not_hit_keyword_table_constraint():
    assert _legacy_keyword_data_source("csv") == "csv"
    assert _legacy_keyword_data_source("google_ads_api") == "google_ads_api"
    assert _legacy_keyword_data_source("url_seed") == "csv"
    assert _legacy_keyword_data_source("manual") == "csv"


def test_url_seed_cache_key_includes_result_shape_params():
    # Signature: (customer_id, workspace_token, url, language_id, geo_target_id,
    #            max_results, min_volume, include_keyword_seed)
    base = _url_seed_cache_key("123", "ws-token", "https://example.com", "1055", "2792", 100, 0, False)

    assert base != _url_seed_cache_key("123", "ws-token", "https://example.com", "1055", "2792", 300, 0, False)
    assert base != _url_seed_cache_key("123", "ws-token", "https://example.com", "1055", "2792", 100, 50, False)
    assert base != _url_seed_cache_key("123", "ws-token", "https://example.com", "1055", "2792", 100, 0, True)
    # Workspace token must scope the key — Codex commit c10fc88 (URL seed cache scoping fix)
    assert base != _url_seed_cache_key("123", "other-ws", "https://example.com", "1055", "2792", 100, 0, False)


def test_url_seed_cache_ttl_is_24_hours():
    assert URL_SEED_CACHE_TTL_SECONDS == 24 * 60 * 60


def test_keyword_seed_hash_is_normalized_and_order_insensitive():
    assert _keyword_seed_hash([" Hisse Analiz ", "hisse analiz"]) == _keyword_seed_hash(["hisse analiz"])
    assert _keyword_seed_hash(["borsa takip", "teknik analiz"]) == _keyword_seed_hash(
        ["TEKNIK ANALIZ", "borsa takip"]
    )
    assert _keyword_seed_hash([]) == "empty"


def test_url_seed_cache_key_changes_when_keyword_seeds_change():
    key_a = _url_seed_cache_key(
        "123",
        "ws-token",
        "https://example.com",
        "1055",
        "2792",
        100,
        0,
        True,
        _keyword_seed_hash(["borsa takip"]),
    )
    key_b = _url_seed_cache_key(
        "123",
        "ws-token",
        "https://example.com",
        "1055",
        "2792",
        100,
        0,
        True,
        _keyword_seed_hash(["temettu takip"]),
    )
    assert key_a != key_b

    url_only_a = _url_seed_cache_key("123", "ws-token", "https://example.com", "1055", "2792", 100, 0, False, "a")
    url_only_b = _url_seed_cache_key("123", "ws-token", "https://example.com", "1055", "2792", 100, 0, False, "b")
    assert url_only_a == url_only_b


def test_url_seed_public_http_error_becomes_warning(monkeypatch):
    class _Response:
        is_redirect = False
        has_redirect_location = False
        status_code = 403
        url = "https://example.com/"

    class _Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url):
            return _Response()

    monkeypatch.setattr(google_ads_api, "_assert_url_host_is_public", lambda url: None)
    monkeypatch.setattr(google_ads_api.httpx, "Client", _Client)

    status, warnings = _inspect_url_for_seed("https://example.com/")

    assert status.reachable is False
    assert status.http_status == 403
    assert warnings


def test_url_seed_timeout_becomes_warning(monkeypatch):
    class _Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url):
            raise TimeoutError("timed out")

    monkeypatch.setattr(google_ads_api, "_assert_url_host_is_public", lambda url: None)
    monkeypatch.setattr(google_ads_api.httpx, "Client", _Client)

    status, warnings = _inspect_url_for_seed("https://example.com/")

    assert status.reachable is False
    assert "timed out" in status.warning
    assert warnings == [status.warning]


def test_url_seed_private_host_stays_hard_fail():
    try:
        _inspect_url_for_seed("http://127.0.0.1/")
    except HTTPException as exc:
        assert exc.status_code == 422
    else:
        raise AssertionError("private host should be blocked")


def test_url_seed_empty_result_is_not_cached(monkeypatch):
    redis_client = _FakeRedis()
    service = _patch_url_seed_dependencies(monkeypatch, redis_client=redis_client, ideas=[])

    result = keyword_ideas_by_url(
        UrlSeedRequest(url="https://example.com", brand_profile_id=77, customer_id="123"),
        Response(),
        db=None,
    )

    assert service.calls == 1
    assert result.ideas == []
    assert result.warnings
    assert redis_client.setex_calls == []


def test_url_seed_warning_with_ideas_is_cached(monkeypatch):
    redis_client = _FakeRedis()
    _patch_url_seed_dependencies(monkeypatch, redis_client=redis_client, ideas=[_UrlIdea()])

    result = keyword_ideas_by_url(
        UrlSeedRequest(url="https://example.com", brand_profile_id=77, customer_id="123"),
        Response(),
        db=None,
    )

    assert result.ideas[0].keyword == "borsa takip"
    assert result.warnings
    assert len(redis_client.setex_calls) == 1
    _, ttl, payload = redis_client.setex_calls[0]
    cached = json.loads(payload)
    assert ttl == URL_SEED_CACHE_TTL_SECONDS
    assert cached["warnings"] == result.warnings
    assert cached["source_status"]["warning"] == result.source_status.warning


def test_url_seed_cache_hit_preserves_warning_and_source_status(monkeypatch):
    cached_payload = json.dumps(
        {
            "ideas": [
                {
                    "keyword": "hisse takip",
                    "monthly_volume": 200,
                    "trend_3m": 0.0,
                    "trend_12m": 0.0,
                    "competition": 0.4,
                }
            ],
            "total": 1,
            "warnings": ["URL sunucu kontrolunde HTTP 403 dondu"],
            "source_status": {
                "reachable": False,
                "http_status": 403,
                "final_url": "https://example.com",
                "warning": "URL sunucu kontrolunde HTTP 403 dondu",
            },
        }
    )
    redis_client = _FakeRedis(cached=cached_payload)
    service = _patch_url_seed_dependencies(monkeypatch, redis_client=redis_client, ideas=[_UrlIdea()])

    result = keyword_ideas_by_url(
        UrlSeedRequest(url="https://example.com", brand_profile_id=77, customer_id="123"),
        Response(),
        db=None,
    )

    assert service.calls == 0
    assert result.cached is True
    assert result.warnings == ["URL sunucu kontrolunde HTTP 403 dondu"]
    assert result.source_status.http_status == 403


def test_url_seed_redirect_to_private_host_stays_hard_fail(monkeypatch):
    """Public URL private adrese yönlendirirse zincir HARD FAIL kalmalı (SSRF).

    _inspect_url_for_seed her redirect adımında _assert_url_host_is_public
    çağırır; yumuşatma yalnızca HTTP/ağ hataları içindir, redirect-SSRF değil.
    """
    class _RedirectResponse:
        is_redirect = True
        has_redirect_location = True
        status_code = 302
        url = "https://public-site.example/"
        headers = {"location": "http://127.0.0.1:8000/internal"}

    class _Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url):
            return _RedirectResponse()

    calls = []
    real_assert = google_ads_api._assert_url_host_is_public

    def tracking_assert(url):
        calls.append(url)
        if "127.0.0.1" in url:
            # Gerçek guard'ın private host davranışı
            raise HTTPException(status_code=422, detail="private host engellendi")
        return None  # ilk (public) hop geçer

    monkeypatch.setattr(google_ads_api, "_assert_url_host_is_public", tracking_assert)
    monkeypatch.setattr(google_ads_api.httpx, "Client", _Client)

    try:
        _inspect_url_for_seed("https://public-site.example/")
    except HTTPException as exc:
        assert exc.status_code == 422
    else:
        raise AssertionError("redirect-to-private hard fail olmalıydı")

    # Guard her iki hop için de çağrıldı: önce public URL, sonra private hedef
    assert len(calls) == 2
    assert "127.0.0.1" in calls[1]
