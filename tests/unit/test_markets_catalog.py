"""Pazar katalogu (app/core/markets.py): ulke/dil tablosu, etiket kurali, arama, workspace_market."""
import csv
import gzip
from collections import Counter
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.core import markets


def _catalog_rows():
    with gzip.open(markets.CATALOG_PATH, "rt", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def test_countries_unique_and_in_catalog_as_country_type():
    ids = [c["geo_target_id"] for c in markets.COUNTRIES]
    codes = [c["code"] for c in markets.COUNTRIES]
    assert len(ids) == len(set(ids)) and len(codes) == len(set(codes))
    assert len(ids) >= 60
    by_id = {r["Criteria ID"]: r for r in _catalog_rows()}
    for c in markets.COUNTRIES:
        row = by_id[c["geo_target_id"]]
        assert row["Target Type"] == "Country"
        assert row["Country Code"] == c["code"]
    assert {c["code"] for c in markets.COUNTRIES} >= {"TR", "GB", "DE", "RU", "UA", "US", "AE"}
    gb = next(c for c in markets.COUNTRIES if c["code"] == "GB")
    assert gb["geo_target_id"] == "2826"  # Birlesik Krallik = GB (UK degil)


def test_country_id_formula_sanity():
    # Istisna yok: 2000 + ISO sayisal her ulkede geo_target_id'ye esit
    for c in markets.COUNTRIES:
        assert 2000 + c["iso_numeric"] == int(c["geo_target_id"]), c["code"]


def test_catalog_only_keeps_expected_types_and_countries():
    rows = _catalog_rows()
    assert set(rows[0].keys()) == set(markets.CATALOG_COLUMNS)
    assert {r["Target Type"] for r in rows} <= set(markets.CATALOG_TARGET_TYPES)
    assert {r["Country Code"] for r in rows} <= {c["code"] for c in markets.COUNTRIES}
    ids = [r["Criteria ID"] for r in rows]
    assert len(ids) == len(set(ids))


def test_languages_exactly_five():
    assert {x["language_id"] for x in markets.LANGUAGES} == {"1037", "1031", "1036", "1000", "1001"}
    assert len(markets.LANGUAGES) == 5
    for x in markets.LANGUAGES:
        assert set(x) == {"language_id", "code", "name_tr", "name_native"}
    assert markets.language_name("1031") == "Rusça"
    assert markets.language_name("9999") is None
    assert markets.is_valid_language("1055")  # alias -> 1037
    assert not markets.is_valid_language("1056")


def test_label_rule_country_city_region():
    assert markets.location_label("2276") == "Almanya (DE) — Ülke"
    assert markets.location_label("1003854") == "Berlin, Almanya — Şehir"
    # bolge adi sehir adindan farkliysa araya girer
    assert markets.location_label("1004363") == "Frankfurt am Main, Hessen, Almanya — Şehir"
    # il/bolge tipi
    assert markets.location_label("21069") == "Istanbul, Türkiye — İl/Bölge"
    assert markets.location_label("20226") == "Berlin, Almanya — İl/Bölge"
    assert markets.location_label("123456789").startswith("Tanımlanamayan mevcut seçim")


def test_same_named_cities_in_one_country_get_distinct_labels():
    rows = [r for r in _catalog_rows() if r["Target Type"] == "City"]
    by_key = {}
    for r in rows:
        by_key.setdefault((r["Country Code"], r["Name"]), []).append(r)
    # ayni ulkede ayni ada sahip, FARKLI kanonik adli (farkli bolge) sehirler
    pairs = [v for v in by_key.values() if len({r["Canonical Name"] for r in v}) >= 2]
    assert pairs, "gercek veride ayni adli iki sehir bulunmali"
    pair = pairs[0]
    labels = {markets.location_label(r["Criteria ID"]) for r in pair}
    assert len(labels) >= 2, (pair, labels)


def test_search_never_lists_the_same_label_twice():
    # Google'da Albury'nin ayni kanonik adli iki ID'si var; ikisi de gecerli, listede biri gorunur
    albury = [r for r in _catalog_rows() if r["Name"] == "Albury" and r["Country Code"] == "AU"]
    assert len(albury) >= 2 and all(markets.is_valid_location(r["Criteria ID"]) for r in albury)
    items = markets.search_locations("albury", country_code="AU")
    assert len([i for i in items if i["label"] == "Albury, New South Wales, Avustralya — Şehir"]) == 1
    for q in ("san", "new", "melton"):
        got = [i["label"] for i in markets.search_locations(q, limit=20)]
        assert len(got) == len(set(got)), q


@pytest.mark.parametrize("name,min_kinds", [
    ("istanbul", {"city", "region"}),
    ("berlin", {"city", "region"}),
    ("moscow", {"city", "region"}),
    ("kyiv", {"city"}),
    ("munich", {"city"}),
])
def test_known_cities_present_with_their_own_kinds(name, min_kinds):
    kinds = {i["kind"] for i in markets.search_locations(name, limit=20) if i["name"].lower() == name}
    assert min_kinds <= kinds, (name, kinds)


def test_search_turkish_case_insensitive_and_country_filter():
    a = markets.search_locations("istanbul")
    b = markets.search_locations("İSTANBUL")
    assert a == b and a
    assert {i["kind"] for i in a if i["name"] == "Istanbul"} == {"city", "region"}

    only_de = markets.search_locations("berlin", country_code="de")
    assert only_de and {i["country_code"] for i in only_de} == {"DE"}
    assert any(i["country_code"] == "US" for i in markets.search_locations("berlin"))


def test_search_limit_ordering_and_short_query():
    assert len(markets.search_locations("san", limit=20)) == 20
    items = markets.search_locations("almanya")
    assert items[0]["kind"] == "country" and items[0]["geo_target_id"] == "2276"
    assert [i["kind"] for i in markets.search_locations("germany")][0] == "country"

    short = markets.search_locations("a")
    assert short and all(i["kind"] == "country" for i in short)
    assert all(i["country_code"] == "TR" for i in markets.search_locations("", country_code="TR"))
    assert len(markets.search_locations("", country_code="TR")) == 1
    _, total = markets.search_locations_page("san", limit=5)
    assert total > 5
    assert markets.search_locations("zzzzqqq") == []


def test_market_label_country_city_unknown():
    ws = lambda g, l: SimpleNamespace(default_geo_target_id=g, default_language_id=l)  # noqa: E731
    assert markets.market_label(ws("2276", "1031")) == "Almanya (DE) · Rusça"
    assert markets.market_label(ws("1003854", "1031")) == "Berlin, Almanya · Rusça"
    assert markets.market_label(ws("999999999", "1031")) == "Tanımlanamayan mevcut seçim (999999999) · Rusça"
    assert markets.market_label(ws("2792", "7777")) == "Türkiye (TR) · Tanımlanamayan mevcut seçim (7777)"


def test_workspace_market_null_is_409_and_alias_resolved():
    for g, l in ((None, "1037"), ("2792", None), ("", "1037"), (None, None)):
        with pytest.raises(HTTPException) as ei:
            markets.workspace_market(SimpleNamespace(default_geo_target_id=g, default_language_id=l))
        assert ei.value.status_code == 409
        assert ei.value.detail == {"code": "MARKET_NOT_SET", "message": "Önce hedef konum ve dil seçin."}
    ws = SimpleNamespace(default_geo_target_id="2792", default_language_id="1055")
    assert markets.workspace_market(ws) == ("2792", "1037")


def test_validate_market_input():
    assert markets.validate_market_input(None, None) == (None, None)  # verilmeyen deger denetlenmez
    assert markets.validate_market_input("2276", "1031") == ("2276", "1031")
    assert markets.validate_market_input("1003854", "1055") == ("1003854", "1037")  # alias kanonik
    # Codex Faz 0 P2: kaynak-yolu / bosluklu girdi kabul edilir ama KANONIK doner (kolon String(20)/String(10))
    assert markets.validate_market_input("geoTargetConstants/1003854", " languageConstants/1055 ") == (
        "1003854", "1037",
    )
    assert markets.validate_market_input(" 2792 ", " 1037 ") == ("2792", "1037")
    with pytest.raises(HTTPException):
        markets.validate_market_input("", "1037")  # bos metin = gecersiz konum
    with pytest.raises(HTTPException) as e1:
        markets.validate_market_input("12345", "1037")
    assert e1.value.status_code == 422 and "default_geo_target_id" in e1.value.detail
    with pytest.raises(HTTPException) as e2:
        markets.validate_market_input("2792", "1056")
    assert e2.value.status_code == 422 and "default_language_id" in e2.value.detail


def test_missing_catalog_file_raises_clear_error(monkeypatch, tmp_path):
    monkeypatch.setattr(markets, "_catalog", None)
    monkeypatch.setattr(markets, "CATALOG_PATH", tmp_path / "yok.csv.gz")
    with pytest.raises(markets.MarketCatalogError):
        markets.is_valid_location("1003854")
    assert markets.is_valid_location("2276")  # ulke katalog dosyasi gerektirmez
