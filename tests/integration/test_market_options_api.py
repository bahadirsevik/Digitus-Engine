"""GET /brand-profile/market-options(+/locations) ve yazma yollarinda pazar dogrulamasi (422)."""
from app.api.v1 import brand_profile as brand_profile_api
from app.database.models import BrandProfile

BASE = "/api/v1/brand-profile"


def _no_background(monkeypatch):
    for name in ("_run_profile_analysis_first", "_run_keyword_suggestion", "_run_keyword_suggestion_from_profile"):
        monkeypatch.setattr(brand_profile_api, name, lambda **kw: None)


def test_market_options_shape(client):
    r = client.get(f"{BASE}/market-options")
    assert r.status_code == 200
    body = r.json()
    assert len(body["countries"]) >= 60
    de = next(c for c in body["countries"] if c["code"] == "DE")
    assert de == {"geo_target_id": "2276", "code": "DE", "name_tr": "Almanya", "label": "Almanya (DE) — Ülke"}
    assert [x["language_id"] for x in body["languages"]] == ["1037", "1031", "1036", "1000", "1001"]
    assert set(body["default"]) == {"geo_target_id", "language_id"}
    assert body["default"] == {"geo_target_id": "2792", "language_id": "1037"}


def test_locations_search_shape_limit_and_filter(client):
    r = client.get(f"{BASE}/market-options/locations", params={"q": "istanbul"})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == len(body["items"]) >= 2
    assert set(body["items"][0]) == {"geo_target_id", "label", "kind", "country_code", "name"}
    assert {i["kind"] for i in body["items"] if i["name"] == "Istanbul"} == {"city", "region"}

    big = client.get(f"{BASE}/market-options/locations", params={"q": "san"}).json()
    assert len(big["items"]) == 20 and big["total"] > 20

    de = client.get(f"{BASE}/market-options/locations", params={"q": "berlin", "country": "DE"}).json()
    assert de["items"] and {i["country_code"] for i in de["items"]} == {"DE"}

    short = client.get(f"{BASE}/market-options/locations", params={"q": "b"}).json()
    assert short["items"] and {i["kind"] for i in short["items"]} == {"country"}


def test_create_workspace_unknown_geo_is_422(client, db_session, monkeypatch):
    _no_background(monkeypatch)
    before = db_session.query(BrandProfile).count()
    r = client.post(f"{BASE}/workspaces", json={
        "company_url": "https://klinik.test", "default_geo_target_id": "999999999",
    })
    assert r.status_code == 422
    assert "default_geo_target_id" in r.json()["detail"]
    bad_lang = client.post(f"{BASE}/workspaces", json={
        "company_url": "https://klinik.test", "default_language_id": "1056",
    })
    assert bad_lang.status_code == 422 and "default_language_id" in bad_lang.json()["detail"]
    assert db_session.query(BrandProfile).count() == before


def test_create_workspace_valid_city_is_stored_as_given(client, monkeypatch):
    _no_background(monkeypatch)
    r = client.post(f"{BASE}/workspaces", json={
        "company_url": "https://klinik.test",
        "default_geo_target_id": "1003854",  # Berlin (sehir)
        "default_language_id": "1031",
    })
    assert r.status_code == 201
    body = r.json()
    assert body["default_geo_target_id"] == "1003854"
    assert body["default_language_id"] == "1031"


def test_create_workspace_stores_canonical_ids(client, db_session, monkeypatch):
    """Codex Faz 0 P2: 'geoTargetConstants/1003854' (26 krk) String(20) kolona sigmaz; kanonik yazilir."""
    _no_background(monkeypatch)
    r = client.post(f"{BASE}/workspaces", json={
        "company_url": "https://klinik-kanonik.test",
        "default_geo_target_id": "geoTargetConstants/1003854",
        "default_language_id": " languageConstants/1055 ",
    })
    assert r.status_code == 201, r.text
    body = r.json()
    assert (body["default_geo_target_id"], body["default_language_id"]) == ("1003854", "1037")
    row = db_session.get(BrandProfile, body["id"])
    assert (row.default_geo_target_id, row.default_language_id) == ("1003854", "1037")


def test_create_workspace_default_stays_tr(client, monkeypatch):
    _no_background(monkeypatch)
    body = client.post(f"{BASE}/workspaces", json={"company_url": "https://eski.test"}).json()
    assert (body["default_geo_target_id"], body["default_language_id"]) == ("2792", "1037")


def test_profile_approve_validates_market(client, db_session, make_workspace, monkeypatch):
    _no_background(monkeypatch)
    ws = make_workspace(status="profile_review", onboarding_flow="profile_first",
                        profile_data={"anchor_texts": ["a"], "products": ["a"]})
    url = f"{BASE}/workspaces/{ws.id}/profile/approve"
    bad = client.put(url, json={"default_language_id": "1056", "rerun_keywords": False})
    assert bad.status_code == 422 and "default_language_id" in bad.json()["detail"]
    bad_geo = client.put(url, json={"default_geo_target_id": "42", "rerun_keywords": False})
    assert bad_geo.status_code == 422
    db_session.expire_all()
    assert db_session.get(BrandProfile, ws.id).default_language_id != "1056"

    ok = client.put(url, json={
        "default_geo_target_id": "1003854", "default_language_id": "1000", "rerun_keywords": False,
    })
    assert ok.status_code == 200
    assert ok.json()["default_geo_target_id"] == "1003854"


def test_profile_approve_stores_canonical_ids(client, db_session, make_workspace, monkeypatch):
    """Codex Faz 0 P2: onayda da ham kaynak-yolu/bosluk degil kanonik ID yazilir."""
    _no_background(monkeypatch)
    ws = make_workspace(status="profile_review", onboarding_flow="profile_first",
                        profile_data={"anchor_texts": ["a"], "products": ["a"]})
    ok = client.put(f"{BASE}/workspaces/{ws.id}/profile/approve", json={
        "default_geo_target_id": " geoTargetConstants/2276 ",
        "default_language_id": "languageConstants/1055",
        "rerun_keywords": False,
    })
    assert ok.status_code == 200, ok.text
    db_session.expire_all()
    row = db_session.get(BrandProfile, ws.id)
    assert (row.default_geo_target_id, row.default_language_id) == ("2276", "1037")


def test_read_returns_legacy_unknown_ids_unchanged(client, make_workspace):
    ws = make_workspace(default_geo_target_id="1234567", default_language_id="1055")
    body = client.get(f"{BASE}/workspaces/{ws.id}").json()
    assert (body["default_geo_target_id"], body["default_language_id"]) == ("1234567", "1055")
