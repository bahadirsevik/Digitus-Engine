import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.api.v1.google_ads import _url_seed_cache_key
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
    base = _url_seed_cache_key("123", "https://example.com", "1055", "2792", 100, 0, False)

    assert base != _url_seed_cache_key("123", "https://example.com", "1055", "2792", 300, 0, False)
    assert base != _url_seed_cache_key("123", "https://example.com", "1055", "2792", 100, 50, False)
    assert base != _url_seed_cache_key("123", "https://example.com", "1055", "2792", 100, 0, True)
