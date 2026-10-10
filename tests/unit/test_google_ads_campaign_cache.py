from dataclasses import dataclass

import pytest

from app.api.v1 import google_ads


@dataclass
class _Campaign:
    campaign_id: str
    campaign_name: str
    status: str


@dataclass
class _CampaignKeyword:
    keyword: str
    match_type: str
    campaign_name: str
    campaign_id: str
    ad_group_name: str
    impressions: int
    clicks: int
    cost: float
    avg_cpc: float
    ctr: float


class _Service:
    def __init__(self):
        self.campaign_calls = 0
        self.keyword_calls = 0

    def list_campaigns(self, customer_id):
        self.campaign_calls += 1
        return [_Campaign("111", f"Campaign {customer_id}", "ENABLED")]

    def get_campaign_keywords(self, **kwargs):
        self.keyword_calls += 1
        return [
            _CampaignKeyword(
                keyword="seo ajansi",
                match_type="EXACT",
                campaign_name="Search",
                campaign_id=kwargs.get("campaign_id") or "all",
                ad_group_name="Core",
                impressions=100,
                clicks=10,
                cost=25.0,
                avg_cpc=2.5,
                ctr=0.1,
            )
        ]


def test_campaigns_cache_hit_skips_google_ads(monkeypatch):
    service = _Service()
    monkeypatch.setattr(google_ads, "_get_service", lambda: service)
    monkeypatch.setattr(
        google_ads,
        "_read_json_cache",
        lambda key: {
            "campaigns": [
                {"campaign_id": "cached", "campaign_name": "Cached Campaign", "status": "PAUSED"}
            ]
        },
    )
    monkeypatch.setattr(google_ads, "_write_json_cache", lambda *args, **kwargs: None)

    result = google_ads.list_campaigns(customer_id="123", refresh=False)

    assert service.campaign_calls == 0
    assert result[0].campaign_id == "cached"


def test_campaigns_cache_miss_writes_successful_response(monkeypatch):
    service = _Service()
    writes = []
    monkeypatch.setattr(google_ads, "_get_service", lambda: service)
    monkeypatch.setattr(google_ads, "_read_json_cache", lambda key: None)
    monkeypatch.setattr(google_ads, "_write_json_cache", lambda *args: writes.append(args))

    result = google_ads.list_campaigns(customer_id="123", refresh=False)

    assert service.campaign_calls == 1
    assert result[0].campaign_id == "111"
    assert writes[0][0] == "gads:campaigns:123"
    assert writes[0][2] == google_ads.GOOGLE_ADS_CAMPAIGN_CACHE_TTL_SECONDS


def test_campaigns_refresh_bypasses_cache(monkeypatch):
    service = _Service()
    monkeypatch.setattr(google_ads, "_get_service", lambda: service)
    monkeypatch.setattr(google_ads, "_read_json_cache", lambda key: {"campaigns": []})
    monkeypatch.setattr(google_ads, "_write_json_cache", lambda *args, **kwargs: None)

    result = google_ads.list_campaigns(customer_id="123", refresh=True)

    assert service.campaign_calls == 1
    assert result[0].campaign_name == "Campaign 123"


def test_google_ads_errors_are_not_cached(monkeypatch):
    writes = []

    class FailingService:
        def list_campaigns(self, customer_id):
            raise RuntimeError("quota")

    monkeypatch.setattr(google_ads, "_get_service", lambda: FailingService())
    monkeypatch.setattr(google_ads, "_read_json_cache", lambda key: None)
    monkeypatch.setattr(google_ads, "_write_json_cache", lambda *args: writes.append(args))

    with pytest.raises(Exception):
        google_ads.list_campaigns(customer_id="123", refresh=False)

    assert writes == []


def test_campaign_keywords_cache_key_uses_filters(monkeypatch):
    service = _Service()
    writes = []
    monkeypatch.setattr(google_ads, "_get_service", lambda: service)
    monkeypatch.setattr(google_ads, "_read_json_cache", lambda key: None)
    monkeypatch.setattr(google_ads, "_write_json_cache", lambda *args: writes.append(args))

    google_ads.list_campaign_keywords(
        customer_id="123",
        campaign_id="456",
        min_impressions=10,
        date_range="LAST_30_DAYS",
        limit=50,
        refresh=False,
    )

    assert writes[0][0] == "gads:campaign_kw:123:456:LAST_30_DAYS:50:10"
