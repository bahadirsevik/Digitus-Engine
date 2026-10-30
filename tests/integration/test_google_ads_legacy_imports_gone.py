"""Workspace'siz legacy Google Ads importlari kapatildi (410 Gone).

- /google-ads/campaigns/keywords/import: gosterimler aylik hacim degildir.
- /google-ads/import: brand_profile_id olmadan global keywords tablosuna yaziyordu.
Her ikisi de servis kurmadan, Google Ads cagrisi ve DB yazimi yapmadan reddeder.
"""
import pytest

from app.api.v1 import google_ads
from app.config import settings
from app.database.models import Keyword
from app.integrations.google_ads.service import (
    CAMPAIGN_IMPORT_DISABLED_DETAIL,
    KEYWORD_IMPORT_DISABLED_DETAIL,
    GoogleAdsService,
)

WORKSPACE_IMPORT_PATH = "/api/v1/keywords/import"


def _must_not_run(*args, **kwargs):
    raise AssertionError("Google Ads cagrisi / DB yazimi yapilmamali")


def _assert_gone(client, db_session, monkeypatch, path, body, expected_detail):
    monkeypatch.setattr(google_ads, "_get_service", _must_not_run)
    monkeypatch.setattr("app.database.crud.create_keywords_bulk", _must_not_run)

    resp = client.post(path, json=body)

    assert resp.status_code == 410
    assert resp.json()["detail"] == expected_detail
    assert WORKSPACE_IMPORT_PATH in resp.json()["detail"]
    assert db_session.query(Keyword).count() == 0


def test_redirect_target_workspace_import_route_exists():
    from app.main import app

    assert any(getattr(r, "path", None) == WORKSPACE_IMPORT_PATH for r in app.routes)


def test_campaign_import_endpoint_returns_410_without_google_ads_or_db_write(
    client, db_session, monkeypatch
):
    _assert_gone(
        client,
        db_session,
        monkeypatch,
        "/api/v1/google-ads/campaigns/keywords/import",
        {"customer_id": "1234567890", "date_range": "ALL_TIME"},
        CAMPAIGN_IMPORT_DISABLED_DETAIL,
    )


def test_keyword_import_endpoint_returns_410_without_google_ads_or_db_write(
    client, db_session, monkeypatch
):
    _assert_gone(
        client,
        db_session,
        monkeypatch,
        "/api/v1/google-ads/import",
        {"customer_id": "1234567890", "seeds": ["seo ajansi"]},
        KEYWORD_IMPORT_DISABLED_DETAIL,
    )


def test_service_import_campaign_keywords_refuses_before_fetch_or_write(monkeypatch):
    svc = GoogleAdsService(settings)
    monkeypatch.setattr(svc, "get_campaign_keywords", _must_not_run)
    monkeypatch.setattr("app.database.crud.create_keywords_bulk", _must_not_run)

    with pytest.raises(ValueError, match="aylik arama hacmi degildir"):
        svc.import_campaign_keywords(
            db=object(), customer_id="1234567890", date_range="ALL_TIME", limit=10
        )


def test_service_import_keywords_refuses_before_fetch_or_write(monkeypatch):
    svc = GoogleAdsService(settings)
    monkeypatch.setattr(svc, "enrich_keywords", _must_not_run)
    monkeypatch.setattr("app.database.crud.create_keywords_bulk", _must_not_run)

    with pytest.raises(ValueError, match="workspace'siz legacy"):
        svc.import_keywords(db=object(), customer_id="1234567890", seeds=["seo ajansi"])
