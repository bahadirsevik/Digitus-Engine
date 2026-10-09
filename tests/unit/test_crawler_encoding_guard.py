# -*- coding: utf-8 -*-
"""Crawler icerik-kodlama ve metin-kalite kapilari (fail-closed).

Arka plan: `brotli` paketi kurulu olmadigi halde `Accept-Encoding: br` ilan edilmisti.
Brotli sunan siteler `response.text` icinde IKILI COP donduruyor, bu cop sessizce
AI'a gidiyor ve model `preliminary_info` (kullanici beyani) uzerinden MAKUL AMA
KANITSIZ bir profil uretiyordu. Hata gozle bakilmadikca fark edilmiyordu.
"""
import gzip as _gzip
import zlib as _zlib

import httpx
import pytest

from app.core.site_analyzer.crawler import (
    ACCEPT_ENCODING_HEADER,
    CONTROL_WHITELIST,
    SUPPORTED_CONTENT_ENCODINGS,
    CrawlContentUnusable,
    SiteCrawler,
    assess_text_quality,
)

HTML = (
    "<html><head><title>Klinik</title></head><body><main>"
    + ("Dis klinigimiz implant, ortodonti ve zirkonyum kaplama tedavileri sunar. " * 12)
    + "</main></body></html>"
)


# --------------------------------------------------------------- baslik sozlesmesi
def test_accept_encoding_declares_only_supported_codecs():
    """Desteklenmeyen kodlama ILAN EDILMEZ — sorunun kaynagi buydu."""
    declared = {p.strip() for p in ACCEPT_ENCODING_HEADER.split(",")}
    assert declared == {"gzip", "deflate"}
    assert "br" not in declared
    assert declared <= SUPPORTED_CONTENT_ENCODINGS


def test_crawler_instance_sends_the_safe_header():
    assert SiteCrawler().headers["Accept-Encoding"] == ACCEPT_ENCODING_HEADER
    assert "br" not in SiteCrawler().headers["Accept-Encoding"]


# --------------------------------------------------------------- metin kalite kapisi
def test_quality_accepts_real_text():
    q = assess_text_quality("Dis klinigimiz implant ve ortodonti sunar. " * 10)
    assert q["usable"] is True and q["reason"] is None


def test_quality_rejects_binary_garbage():
    """Cozulememis brotli, U+FFFD ile dolu bir dizeye donusur."""
    q = assess_text_quality("�� ��q9IY@v�g�" * 40)
    assert q["usable"] is False
    assert q["reason"] == "binary_or_undecoded"


def test_quality_rejects_control_characters():
    q = assess_text_quality(("abc\x00\x01\x02def" * 80))
    assert q["usable"] is False
    assert q["reason"] == "control_characters"


def test_quality_rejects_js_shell_too_short():
    """JS ile render edilen siteler yalniz 'Yukleniyor...' dondurur."""
    q = assess_text_quality("Yukleniyor...")
    assert q["usable"] is False and q["reason"] == "too_short"


def test_quality_allows_normal_whitespace():
    assert set(CONTROL_WHITELIST) == {"\n", "\r", "\t"}
    q = assess_text_quality("Basliklar\n\nParagraf metni.\tDevam. " * 20)
    assert q["usable"] is True


# --------------------------------------------------------------- fetch kapilari
def _gzipped(text: str) -> bytes:
    return _gzip.compress(text.encode("utf-8"))


def _deflated(text: str) -> bytes:
    return _zlib.compress(text.encode("utf-8"))


def _crawler_with(handler):
    crawler = SiteCrawler()
    transport = httpx.MockTransport(handler)
    real_client = httpx.Client

    def _client(**kwargs):
        kwargs.pop("verify", None)
        kwargs["transport"] = transport
        return real_client(**kwargs)

    return crawler, _client


def test_fetch_page_accepts_gzip(monkeypatch):
    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/html",
                                            "content-encoding": "gzip"},
                              content=_gzipped(HTML))
    crawler, client = _crawler_with(handler)
    monkeypatch.setattr(httpx, "Client", client)
    page = crawler._fetch_page("https://example.test/")
    assert page is not None and "implant" in page["text"]


def test_fetch_page_accepts_deflate(monkeypatch):
    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/html",
                                            "content-encoding": "deflate"},
                              content=_deflated(HTML))
    crawler, client = _crawler_with(handler)
    monkeypatch.setattr(httpx, "Client", client)
    assert crawler._fetch_page("https://example.test/") is not None


def test_fetch_page_rejects_brotli(monkeypatch):
    """Sunucu yine de `br` donerse sayfa REDDEDILIR (fail-closed)."""
    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/html",
                                            "content-encoding": "br"}, text=HTML)
    crawler, client = _crawler_with(handler)
    monkeypatch.setattr(httpx, "Client", client)
    assert crawler._fetch_page("https://example.test/") is None


def test_fetch_page_marks_binary_body_unusable(monkeypatch):
    """Kodlama basligi temiz olsa bile cop metin ICERIK olarak kullanilamaz.

    Tasarim: `_fetch_page` SERT hatada None doner; kalite hatasinda sayfayi
    dondurur ama `quality.usable=False` isaretler. Boylece sayfa gezinme
    (link/sitemap) icin kullanilabilir, ICERIK olarak kullanilamaz.
    """
    garbage = "<html><body><main>" + ("���q9IY" * 200) + "</main></body></html>"
    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/html"}, text=garbage)
    crawler, client = _crawler_with(handler)
    monkeypatch.setattr(httpx, "Client", client)
    page = crawler._fetch_page("https://example.test/")
    assert page is not None
    assert page["quality"]["usable"] is False
    assert page["quality"]["reason"] == "binary_or_undecoded"


def test_crawl_site_excludes_low_quality_pages(monkeypatch):
    """JS kabugu ana sayfa ICERIGE girmez ama tarama iptal OLMAZ."""
    shell = "<html><body><main>Yukleniyor...</main></body></html>"
    good = ("<html><body><main>"
            + ("Hizmetlerimiz arasinda implant ve ortodonti tedavisi bulunur. " * 12)
            + "</main></body></html>")

    def handler(request):
        if request.url.path in ("", "/"):
            return httpx.Response(200, headers={"content-type": "text/html"}, text=shell)
        if "sitemap" in request.url.path:
            return httpx.Response(
                200, headers={"content-type": "application/xml"},
                text='<?xml version="1.0"?><urlset><url><loc>https://example.test/hizmetler</loc>'
                     "</url></urlset>")
        return httpx.Response(200, headers={"content-type": "text/html"}, text=good)

    crawler, client = _crawler_with(handler)
    monkeypatch.setattr(httpx, "Client", client)
    res = crawler.crawl_site("https://example.test/")
    assert res["error"] is None
    assert res["skipped_low_quality"] >= 1          # ana sayfa elendi
    assert len(res["pages"]) >= 1                   # ic sayfa kurtarildi
    assert all(p["quality"]["usable"] for p in res["pages"])


def test_crawl_site_fails_closed_when_no_page_is_usable(monkeypatch):
    """Hicbir sayfa kaliteyi gecemezse ACIK hata doner, sessiz bos icerik DEGIL."""
    shell = "<html><body><main>Yukleniyor...</main></body></html>"

    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/html"}, text=shell)

    crawler, client = _crawler_with(handler)
    monkeypatch.setattr(httpx, "Client", client)
    res = crawler.crawl_site("https://example.test/")
    assert res["pages"] == []
    assert "crawl_content_unusable" in (res["error"] or "")


# --------------------------------------------------------------- sitemap ayni kapi
def test_sitemap_rejects_unsupported_encoding(monkeypatch):
    sitemap = ('<?xml version="1.0"?><urlset><url><loc>https://example.test/a</loc></url></urlset>')

    def handler(request):
        return httpx.Response(200, headers={"content-type": "application/xml",
                                            "content-encoding": "br"}, text=sitemap)
    crawler, client = _crawler_with(handler)
    monkeypatch.setattr(httpx, "Client", client)
    assert crawler._try_sitemap("https://example.test") == []


def test_sitemap_accepts_gzip(monkeypatch):
    sitemap = ('<?xml version="1.0"?><urlset><url><loc>https://example.test/a</loc></url></urlset>')

    def handler(request):
        return httpx.Response(200, headers={"content-type": "application/xml",
                                            "content-encoding": "gzip"},
                              content=_gzipped(sitemap))
    crawler, client = _crawler_with(handler)
    monkeypatch.setattr(httpx, "Client", client)
    assert crawler._try_sitemap("https://example.test") == ["https://example.test/a"]


# --------------------------------------------------------------- AI'a sizma yok
def test_profile_extraction_refuses_unusable_content():
    """Kullanilamaz icerik `preliminary_info` ile TELAFI EDILEMEZ."""
    from app.core.site_analyzer.profile_extractor import ProfileExtractor

    class _NeverCalledAI:
        def complete_json(self, *a, **k):  # pragma: no cover
            raise AssertionError("Kullanilamaz icerikte AI CAGRILMAMALIYDI")

    ex = ProfileExtractor(_NeverCalledAI())
    with pytest.raises(CrawlContentUnusable):
        ex.extract_profile_from_site_content(
            "���q9IY" * 200,
            preliminary_info="Sektor: Dis Klinigi | Tanim: Dis Klinigi",
        )


def test_profile_extraction_refuses_js_shell():
    from app.core.site_analyzer.profile_extractor import ProfileExtractor

    class _NeverCalledAI:
        def complete_json(self, *a, **k):  # pragma: no cover
            raise AssertionError("Kullanilamaz icerikte AI CAGRILMAMALIYDI")

    ex = ProfileExtractor(_NeverCalledAI())
    with pytest.raises(CrawlContentUnusable):
        ex.extract_profile_from_site_content("Yukleniyor...", preliminary_info="Sektor: X")
