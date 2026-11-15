"""Faz 3 adaptorleri: arama sonucu ayristirma, YouTube kanal ozeti, Places eslestirme. Ag YOK."""
from datetime import datetime, timezone

import pytest

from app.config import settings
from app.integrations import places, serpapi, youtube


class Response:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


def test_serp_page_is_parsed_from_displayed_address(monkeypatch):
    """Google sonuc baglantisi sifreli gelir; alan adi gorunen adres satirindan okunur."""
    sent = {}
    payload = {
        "organic_results": [
            {"position": 1, "title": "A", "link": "https://www.google.com.tr/goto?url=X",
             "displayed_link": "https://www.Marka.test › tedavi"},
            {"position": 2, "title": "B", "displayed_link": "6.1 B+ görüntüleme"},  # adres icermeyen satir
            {"position": 3, "title": "C", "displayed_link": "https://blog.rakip.test › yazi"},
        ],
        "ads": [{"displayed_link": "https://www.rakip.test › randevu"}, {"displayed_link": "https://www.rakip.test"}],
        "local_results": {"places": [{"title": "x"}]},
        "search_parameters": {"location_used": "Istanbul,Turkey"},
    }
    monkeypatch.setattr(settings, "SERPAPI_API_KEY", "k")
    monkeypatch.setattr(serpapi.httpx, "get", lambda url, **kw: sent.update(kw["params"]) or Response(payload))
    page = serpapi.search("diş hekimi", "Istanbul, Turkey", page=1)
    assert sent["start"] == 10 and sent["device"] == "desktop" and sent["location"] == "Istanbul, Turkey"
    assert [(o["position"], o["domain"]) for o in page["organic"]] == [(11, "marka.test"), (12, ""), (13, "blog.rakip.test")]
    assert page["ads"] == ["rakip.test"] and page["local_pack"] is True and page["knowledge_panel"] is False
    assert serpapi.position_of(page["organic"], "marka.test") == 11
    assert serpapi.position_of(page["organic"], "rakip.test") == 13  # alt alan adi ayni site sayilir
    assert serpapi.position_of(page["organic"], "arka.test") is None


def test_serp_errors_are_raised_not_read_as_empty_results(monkeypatch):
    monkeypatch.setattr(settings, "SERPAPI_API_KEY", None)
    with pytest.raises(serpapi.SerpError):
        serpapi.search("q", "loc")
    monkeypatch.setattr(settings, "SERPAPI_API_KEY", "k")
    monkeypatch.setattr(serpapi.httpx, "get", lambda url, **kw: Response({"error": "Your account has run out of searches."}))
    with pytest.raises(serpapi.SerpError):
        serpapi.search("q", "loc")


def test_youtube_summary_counts_recent_uploads_and_views(monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_CLOUD_API_KEY", "k")
    asked = []

    def fake_get(url, params=None, timeout=None):
        asked.append((url.rsplit("/", 1)[-1], params))
        if url.endswith("channels"):
            return Response({"items": [{"snippet": {"title": "Marka TV"},
                                        "statistics": {"subscriberCount": "1200", "videoCount": "40", "viewCount": "90000"},
                                        "contentDetails": {"relatedPlaylists": {"uploads": "UU1"}}}]})
        if url.endswith("playlistItems"):
            return Response({"items": [{"contentDetails": {"videoId": "a"}}, {"contentDetails": {"videoId": "b"}}]})
        return Response({"items": [
            {"snippet": {"publishedAt": "2026-11-01T10:00:00Z"}, "statistics": {"viewCount": "1000"}},
            {"snippet": {"publishedAt": "2026-03-01T10:00:00Z"}, "statistics": {"viewCount": "500"}}]})

    monkeypatch.setattr(youtube.httpx, "get", fake_get)
    out = youtube.channel_summary("https://www.youtube.com/@marka", now=datetime(2026, 11, 15, tzinfo=timezone.utc))
    assert asked[0][1]["forHandle"] == "@marka"
    assert out["status"] == "ok" and out["subscribers"] == 1200 and out["last_upload"] == "2026-11-01"
    assert out["uploads_90d"] == 1 and out["recent_avg_views"] == 750 and out["recent_sample"] == 2


@pytest.mark.parametrize("url", ["https://www.youtube.com/c/marka", "https://www.youtube.com/watch?v=1", ""])
def test_youtube_unresolvable_address_is_not_a_missing_channel(monkeypatch, url):
    monkeypatch.setattr(settings, "GOOGLE_CLOUD_API_KEY", "k")
    assert youtube.channel_summary(url)["status"] == "unavailable"


def test_place_is_matched_by_website_not_by_name(monkeypatch):
    """Ayni adli isletmeler karismasin: yalniz web sitesi bu alan adi olan profil kabul edilir."""
    monkeypatch.setattr(settings, "GOOGLE_CLOUD_API_KEY", "k")
    sent = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        sent.update(headers)
        return Response({"places": [{"id": "P1", "websiteUri": "https://baska.test/"},
                                    {"id": "P2", "websiteUri": "https://www.marka.test/iletisim"}, {"id": "P3"}]})

    monkeypatch.setattr(places.httpx, "post", fake_post)
    assert places.find_place_id("Marka Klinik", "marka.test") == "P2"
    assert places.find_place_id("Marka Klinik", "yok.test") is None
    assert sent["X-Goog-FieldMask"] == "places.id,places.websiteUri"  # eslestirme icin gerekenden fazlasi istenmez
