"""Faz 1-5: Google Ads uclari pazari (geo/dil) SUNUCUDA calismadan alir.

enrich + keyword-ideas-by-url: istekteki language_id/geo_target_id yok sayilir (422 degil),
sahte saglayici calisma pazariyla cagrilir, cevap Google'a gonderilen pazari tasir.
Gercek Google Ads cagrisi yok.
"""
import json
from types import SimpleNamespace

import pytest

from app.api.v1 import google_ads
from app.api.v1.google_ads import UrlSeedSourceStatus

ENRICH = "/api/v1/google-ads/enrich"
URL_SEED = "/api/v1/google-ads/keyword-ideas-by-url"
DE, RU, BERLIN = "2276", "1031", "1003854"


class _FakeSvc:
    def __init__(self):
        self.calls = []

    def enrich_keywords(self, customer_id, seeds, max_results, language_id, geo_target_id):
        self.calls.append(("enrich", language_id, geo_target_id))
        idea = SimpleNamespace(
            keyword="kaufen", avg_monthly_searches=500, competition_index=10,
            competition_score=0.1, trend_3m=1.0, trend_12m=2.0, cpc_low=0.1, cpc_high=0.4,
        )
        return [idea], False, None

    def keyword_ideas_by_url(self, **kwargs):
        self.calls.append(("url", kwargs["language_id"], kwargs["geo_target_id"]))
        idea = SimpleNamespace(
            keyword="kaufen", monthly_volume=500, trend_3m=0.0, trend_12m=0.0, competition=0.1
        )
        return [idea], False, None


class _FakeRedis:
    def __init__(self, cached=None):
        self.cached = cached
        self.setex_calls = []

    def get(self, key):
        return self.cached

    def setex(self, key, ttl, value):
        self.setex_calls.append((key, ttl, value))


@pytest.fixture
def svc(monkeypatch):
    fake = _FakeSvc()
    monkeypatch.setattr(google_ads, "_get_service", lambda: fake)
    monkeypatch.setattr(google_ads, "_get_redis_client", lambda: None)
    monkeypatch.setattr(
        google_ads,
        "_inspect_url_for_seed",
        lambda url: (UrlSeedSourceStatus(reachable=True, http_status=200, final_url=url), []),
    )
    return fake


def _enrich_body(ws, **extra):
    return {"customer_id": "123", "seeds": ["kaufen"], "brand_profile_id": ws.id, **extra}


def _url_body(ws, **extra):
    return {"url": "https://example.de", "customer_id": "123", "brand_profile_id": ws.id, **extra}


def test_request_geo_lang_are_ignored_and_workspace_market_is_sent(client, make_workspace, svc):
    ws = make_workspace(name="de ru", default_geo_target_id=DE, default_language_id=RU)
    stale = {"language_id": "1037", "geo_target_id": "2792"}  # eski istemci: 422 DEGIL, yok sayilir

    r1 = client.post(ENRICH, json=_enrich_body(ws, **stale))
    r2 = client.post(URL_SEED, json=_url_body(ws, **stale))

    assert r1.status_code == 200, r1.text
    assert r2.status_code == 200, r2.text
    assert svc.calls == [("enrich", RU, DE), ("url", RU, DE)]


def test_city_workspace_forwards_city_id_unchanged(client, make_workspace, svc):
    ws = make_workspace(name="berlin", default_geo_target_id=BERLIN, default_language_id=RU)

    assert client.post(ENRICH, json=_enrich_body(ws)).status_code == 200
    assert client.post(URL_SEED, json=_url_body(ws)).status_code == 200

    assert svc.calls == [("enrich", RU, BERLIN), ("url", RU, BERLIN)]


def test_legacy_language_alias_is_resolved(client, make_workspace, svc):
    ws = make_workspace(name="alias", default_geo_target_id="2792", default_language_id="1055")

    assert client.post(ENRICH, json=_enrich_body(ws)).status_code == 200

    assert svc.calls == [("enrich", "1037", "2792")]


@pytest.mark.parametrize("path,body", [(ENRICH, _enrich_body), (URL_SEED, _url_body)])
def test_null_market_workspace_is_409_before_any_google_call(
    client, make_workspace, svc, path, body
):
    ws = make_workspace(name="pazarsiz", default_geo_target_id=None, default_language_id=None)

    r = client.post(path, json=body(ws))

    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "MARKET_NOT_SET"
    assert svc.calls == []


def test_responses_carry_sent_market(client, make_workspace, svc):
    ws = make_workspace(name="berlin ru", default_geo_target_id=BERLIN, default_language_id=RU)

    for path, body in ((ENRICH, _enrich_body(ws)), (URL_SEED, _url_body(ws))):
        data = client.post(path, json=body).json()
        assert data["geo_target_id"] == BERLIN
        assert data["language_id"] == RU
        assert data["market_label"] == "Berlin, Almanya · Rusça"


def test_url_seed_cached_branch_returns_market_too(client, make_workspace, svc, monkeypatch):
    ws = make_workspace(name="de cache", default_geo_target_id=DE, default_language_id=RU)
    cached = json.dumps({
        "ideas": [{"keyword": "kaufen", "monthly_volume": 9, "trend_3m": 0.0,
                   "trend_12m": 0.0, "competition": 0.2}],
        "total": 1, "warnings": [], "source_status": None,
    })
    monkeypatch.setattr(google_ads, "_get_redis_client", lambda: _FakeRedis(cached))

    data = client.post(URL_SEED, json=_url_body(ws)).json()

    assert data["cached"] is True and svc.calls == []
    assert (data["geo_target_id"], data["language_id"]) == (DE, RU)
    assert data["market_label"] == "Almanya (DE) · Rusça"


def test_url_seed_cache_key_embeds_workspace_market(client, make_workspace, svc, monkeypatch):
    ws = make_workspace(name="de key", default_geo_target_id=DE, default_language_id=RU)
    redis = _FakeRedis()
    monkeypatch.setattr(google_ads, "_get_redis_client", lambda: redis)

    data = client.post(URL_SEED, json=_url_body(ws, language_id="1037", geo_target_id="2792")).json()

    assert f":{RU}:{DE}:" in data["cache_key"]
    assert len(redis.setex_calls) == 1


def test_enrich_requires_brand_profile_id(client, svc):
    r = client.post(ENRICH, json={"customer_id": "123", "seeds": ["kaufen"]})

    assert r.status_code == 422
    assert svc.calls == []


def test_enrich_unknown_workspace_is_404(client, svc):
    r = client.post(ENRICH, json={"customer_id": "123", "seeds": ["kaufen"], "brand_profile_id": 999999})

    assert r.status_code == 404
    assert svc.calls == []


def test_url_seed_few_ideas_hint_only_for_non_turkish(client, make_workspace, svc):
    hint = "Site metni hedef dilden farklıysa"
    ru = make_workspace(name="hint ru", default_geo_target_id=DE, default_language_id=RU)
    tr = make_workspace(name="hint tr", default_geo_target_id="2792", default_language_id="1037")

    assert any(hint in w for w in client.post(URL_SEED, json=_url_body(ru)).json()["warnings"])
    assert not any(hint in w for w in client.post(URL_SEED, json=_url_body(tr)).json()["warnings"])
