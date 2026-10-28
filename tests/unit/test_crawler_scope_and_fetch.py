# -*- coding: utf-8 -*-
"""Marka alt sayfa plani Faz 1: guvenli genel fetch + butce + meta cikarimi + sitemap + scope.

AG YOK: DNS `socket.getaddrinfo` ile public IP'ye sabitlenir, HTTP `httpx.MockTransport`
ile taklit edilir (httpx.Client fabrikasi yamalanir — test_crawler_encoding_guard.py ile ayni yol).
"""
import socket
import ssl

import httpx
import pytest

import app.core.site_analyzer.crawler as crawler_mod
from app.core import url_guard
from app.core.site_analyzer.crawler import SiteCrawler, assess_text_quality, dedupe_lines
from app.core.site_analyzer.profile_extractor import (
    ProfileExtractor,
    missing_service_info,
    prepared_source_text,
    resolve_site_content,
)
from app.core.url_guard import (
    FetchBudget,
    FetchBudgetExceeded,
    HttpStatusError,
    TlsVerificationError,
    UnsafeUrlError,
    safe_fetch,
    safe_fetch_html,
)

DESC = ("Imece Mobil, tarim arazileri icin toprak analizi hizmeti sunar; numune alma, "
        "laboratuvar sonucu ve gubreleme onerisini tek raporda toplar.")
SPA_HTML = f"""<html><head>
<title>Toprak Analizi Hizmeti | Imece Mobil</title>
<meta name="description" content="{DESC}">
<meta property="og:description" content="{DESC}">
<meta property="og:site_name" content="Imece Mobil">
<script type="application/ld+json">
{{"@context":"https://schema.org","@graph":[
 {{"@type":"Organization","name":"Imece Mobil Tarim Teknolojileri",
   "description":"Dijital tarim ve toprak analizi cozumleri"}},
 {{"@type":"BreadcrumbList","itemListElement":[{{"@type":"ListItem","name":"Hizmetler"}}]}},
 {{"@type":"FAQPage","mainEntity":[{{"@type":"Question","name":"Numune nasil alinir?",
   "acceptedAnswer":{{"@type":"Answer","text":"<p>Arazinin 10 noktasindan alinir.</p>"}}}}]}}
]}}
</script>
<script src="/_next/static/chunks/main.js"></script>
</head><body><div id="__next"><main>Yükleniyor...</main></div></body></html>"""

GOOD = ("<html><body><main><h1>Hizmetlerimiz</h1><p>"
        + "Dis klinigimiz implant, ortodonti ve zirkonyum kaplama tedavileri sunar. " * 6
        + "</p></main></body></html>")


@pytest.fixture(autouse=True)
def _dns(monkeypatch):
    """`internal*` hostlari ozel IP'ye, gerisi public IP'ye cozulur."""
    def fake(host, *a, **k):
        ip = "10.0.0.5" if str(host).startswith("internal") else "93.184.216.34"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]
    monkeypatch.setattr(socket, "getaddrinfo", fake)


class Site:
    """URL -> yanit tablosu; yapilan her istegi kaydeder."""

    def __init__(self, routes=None):
        self.routes = routes or {}
        self.requests = []
        self.timeouts = []

    def handler(self, request):
        url = str(request.url)
        self.requests.append(url)
        self.timeouts.append(request.extensions.get("timeout"))
        route = self.routes.get(url)
        if route is None:
            return httpx.Response(404, headers={"content-type": "text/html"}, text="yok")
        if isinstance(route, Exception):
            raise route
        if isinstance(route, httpx.Response):
            return route
        ctype, body = route
        return httpx.Response(200, headers={"content-type": ctype}, text=body)

    def install(self, monkeypatch):
        real = httpx.Client

        def client(**kwargs):
            kwargs.pop("verify", None)
            kwargs["transport"] = httpx.MockTransport(self.handler)
            return real(**kwargs)

        monkeypatch.setattr(httpx, "Client", client)
        return self


def html(body=GOOD):
    return ("text/html; charset=utf-8", body)


def xml(body):
    return ("application/xml", body)


def urlset(*locs):
    return xml('<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
               + "".join(f"<url><loc>{u}</loc></url>" for u in locs) + "</urlset>")


def sitemapindex(*locs):
    return xml('<?xml version="1.0"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
               + "".join(f"<sitemap><loc>{u}</loc></sitemap>" for u in locs) + "</sitemapindex>")


# --------------------------------------------------------------- meta / JSON-LD / dedup
def test_spa_page_text_comes_from_meta_and_json_ld(monkeypatch):
    site = Site({"https://imece.test/hizmetler/toprak-analizi-hizmeti": html(SPA_HTML)}).install(monkeypatch)
    res = SiteCrawler().crawl_site("https://imece.test/hizmetler/toprak-analizi-hizmeti", scope="page")
    assert res["error"] is None
    text = res["pages"][0]["text"]
    assert "toprak analizi hizmeti sunar" in text
    assert "Imece Mobil Tarim Teknolojileri" in text
    assert "Numune nasil alinir?" in text and "Arazinin 10 noktasindan alinir." in text
    assert "<p>" not in text
    assert "Hizmetler" in text                      # breadcrumb
    # meta description + og:description ayni cumle: TEK kez
    assert text.count("toprak analizi hizmeti sunar") == 1
    assert len(site.requests) == 1


def test_invalid_json_ld_is_skipped():
    from bs4 import BeautifulSoup
    soup = BeautifulSoup('<html><head><title>T</title><script type="application/ld+json">{bozuk'
                         "</script></head><body></body></html>", "lxml")
    assert SiteCrawler._extract_metadata(soup) == ["T"]


def test_dedupe_lines_is_whitespace_and_case_insensitive():
    out = dedupe_lines("Merhaba  Dunya\nmerhaba dunya\n\n  MERHABA   DUNYA \nBaska satir")
    assert out == "Merhaba Dunya\nBaska satir"


# --------------------------------------------------------------- yeterlilik kapisi
def test_gate_ignores_page_header_and_url_lines():
    prepared = ("## Sayfa: " + "Cok Uzun Bir Sayfa Basligi " * 10
                + "\nURL: https://example.test/" + "cok/uzun/yol/" * 20
                + "\n\nKisa metin.\n\n---\n\n## Sayfa: Baska\nURL: https://example.test/b\n\nKisa metin.")
    assert len(prepared) > 400
    assert prepared_source_text(prepared) == "Kisa metin."
    assert assess_text_quality(prepared_source_text(prepared))["reason"] == "too_short"


def test_crawl_for_profile_content_rejects_short_text_with_long_url_and_title(monkeypatch):
    ex = ProfileExtractor(None)
    page = {"url": "https://example.test/" + "x/" * 200, "title": "T" * 300,
            "text": "Kisa metin.", "status": 200}
    monkeypatch.setattr(ex.crawler, "crawl_site",
                        lambda url, scope="site": {"pages": [page], "error": None})
    out = ex.crawl_for_profile_content("https://example.test")
    assert out["site_content"] == ""
    assert out["error"] == "crawl_content_unusable: too_short"


def test_crawl_for_profile_content_passes_scope(monkeypatch):
    ex = ProfileExtractor(None)
    seen = []
    monkeypatch.setattr(ex.crawler, "crawl_site",
                        lambda url, scope="site": seen.append(scope) or {"pages": [], "error": "x"})
    ex.crawl_for_profile_content("https://example.test", crawl_scope="page")
    assert seen == ["page"]


def test_cache_gate_ignores_format_lines():
    class Ex:
        calls = 0

        def crawl_for_profile_content(self, url):
            Ex.calls += 1
            return {"site_content": "", "source_pages": [], "error": "crawl_content_unusable: too_short"}

    cached = "## Sayfa: " + "B" * 300 + "\nURL: https://example.test/" + "y" * 300 + "\n\nKisa."
    content, pages, error = resolve_site_content(Ex(), "https://example.test", cached)
    assert Ex.calls == 1 and error and content == ""


def test_crawl_short_text_fails_even_with_long_url(monkeypatch):
    url = "https://site.test/" + "/".join(["cok-uzun-yol"] * 30)
    Site({url: html("<html><head><title>Baslik</title></head><body><main>Kisa metin.</main></body></html>")}
         ).install(monkeypatch)
    res = SiteCrawler().crawl_site(url, scope="page")
    assert res["pages"] == []
    assert res["error"].startswith("crawl_content_unusable:") and "çok kısa" in res["error"]


def test_spa_shell_error_message(monkeypatch):
    Site({"https://site.test": html("<html><body><main>Yükleniyor...</main></body></html>")}).install(monkeypatch)
    res = SiteCrawler().crawl_site("https://site.test", scope="page")
    assert res["error"] == "crawl_content_unusable: Site içeriği JavaScript ile yükleniyor, okunamadı"


def test_short_pages_are_summed_when_needed(monkeypatch):
    a = "Birinci sayfa icerigi. " * 5     # ~115 karakter
    b = "Ikinci sayfa farkli icerik. " * 5
    page = lambda t, links="": html(f"<html><body><main>{links}<p>{t}</p></main></body></html>")
    Site({
        "https://site.test": page(a, '<a href="/hizmetler">h</a>'),
        "https://site.test/hizmetler": page(b),
    }).install(monkeypatch)
    res = SiteCrawler().crawl_site("https://site.test")
    assert res["error"] is None and len(res["pages"]) == 2


# --------------------------------------------------------------- sitemap
def test_sitemapindex_one_level_max_three_children_and_same_site(monkeypatch):
    site = Site({
        "https://site.test/robots.txt": ("text/plain", "User-agent: *\nDisallow:\n"),
        "https://site.test/sitemap.xml": sitemapindex(
            *[f"https://www.site.test/sm{i}.xml" for i in range(1, 6)]),
        "https://www.site.test/sm1.xml": urlset("https://www.site.test/hizmetler",
                                               "https://baska-alan.test/spam"),
        "https://www.site.test/sm2.xml": urlset("https://site.test/hakkimizda"),
        "https://www.site.test/sm3.xml": sitemapindex("https://www.site.test/derin.xml"),
        "https://www.site.test/sm4.xml": urlset("https://site.test/sm4-url"),
    }).install(monkeypatch)
    urls = SiteCrawler()._try_sitemap("https://site.test", FetchBudget())
    assert urls == ["https://www.site.test/hizmetler", "https://site.test/hakkimizda"]
    assert not any(u.endswith(".xml") for u in urls)             # alt harita sayfa degil
    assert "https://www.site.test/sm4.xml" not in site.requests  # en fazla 3 alt harita
    assert "https://www.site.test/derin.xml" not in site.requests  # tek seviye


def test_sitemap_url_cap(monkeypatch):
    Site({"https://site.test/sitemap.xml": urlset(
        *[f"https://site.test/u{i}" for i in range(300)])}).install(monkeypatch)
    assert len(SiteCrawler()._try_sitemap("https://site.test", FetchBudget())) == 200


def test_robots_sitemap_line_is_used_case_insensitively(monkeypatch):
    site = Site({
        "https://site.test/robots.txt": ("text/plain", "User-agent: *\nSITEMAP: https://site.test/ozel-harita.xml\n"),
        "https://site.test/ozel-harita.xml": urlset("https://site.test/hizmetler"),
    }).install(monkeypatch)
    assert SiteCrawler()._try_sitemap("https://site.test", FetchBudget()) == ["https://site.test/hizmetler"]
    assert "https://site.test/sitemap.xml" not in site.requests


def test_deep_url_site_mode_uses_origin_sitemap_not_input_path(monkeypatch):
    site = Site({
        "https://site.test/a/b/derin": html(GOOD),
        "https://site.test/sitemap.xml": urlset("https://site.test/hizmetler"),
        "https://site.test/hizmetler": html(GOOD.replace("Dis klinigimiz", "Hizmet sayfamiz")),
    }).install(monkeypatch)
    res = SiteCrawler().crawl_site("https://site.test/a/b/derin")
    assert "https://site.test/robots.txt" in site.requests
    assert "https://site.test/sitemap.xml" in site.requests
    assert not any("/a/b/derin/" in u for u in site.requests)
    assert res["sitemap_urls"] == ["https://site.test/hizmetler"]
    assert len(res["pages"]) == 2


def test_www_and_bare_host_are_same_site_for_links():
    links = SiteCrawler()._extract_internal_links(
        '<a href="https://www.site.test/hizmetler">a</a><a href="/iletisim">b</a>'
        '<a href="https://baska.test/x">c</a>', "https://site.test", "site.test")
    assert links == ["https://www.site.test/hizmetler", "https://site.test/iletisim"]


def test_internal_links_keep_trailing_slash_and_skip_self():
    """`/x/` linki `/x`'e cevrilirse her sayfa redirect'le 2 istek yer (canli: drsemrasezgin)."""
    links = SiteCrawler()._extract_internal_links(
        '<a href="/hizmetler/implant/">a</a><a href="/">b</a><a href="https://www.site.test/">c</a>',
        "https://site.test", "site.test")
    assert links == ["https://site.test/hizmetler/implant/"]


# --------------------------------------------------------------- page modu
def test_page_mode_makes_exactly_one_request(monkeypatch):
    site = Site({"https://site.test/hizmet": html(GOOD)}).install(monkeypatch)
    res = SiteCrawler().crawl_site("https://site.test/hizmet", scope="page")
    assert res["error"] is None and len(res["pages"]) == 1 and res["sitemap_urls"] == []
    assert site.requests == ["https://site.test/hizmet"]


def test_invalid_scope_raises():
    with pytest.raises(ValueError):
        SiteCrawler().crawl_site("https://site.test", scope="section")


@pytest.mark.parametrize("start,hop", [
    ("http://site.test/hizmet", "https://site.test/hizmet"),            # http -> https
    ("https://site.test/hizmet", "https://www.site.test/hizmet"),       # www ekleme
    ("https://www.site.test/hizmet", "https://site.test/hizmet"),       # www kaldirma
    ("https://site.test/hizmet", "https://site.test/hizmet/"),          # sondaki /
    ("https://SITE.test/hizmet?utm=1", "https://site.test/hizmet"),     # host harfi + sorgu
])
def test_page_mode_accepts_normalized_redirects(monkeypatch, start, hop):
    site = Site({
        str(httpx.URL(start)): httpx.Response(301, headers={"location": hop}),
        hop: html(GOOD),
    }).install(monkeypatch)
    res = SiteCrawler().crawl_site(start, scope="page")
    assert res["error"] is None, res["error"]
    assert len(site.requests) == 2                                      # redirect adimi da istek


def test_page_key_ignores_scheme_www_query_fragment_trailing_slash():
    from app.core.site_analyzer.crawler import _page_key
    assert _page_key("https://SITE.test/hizmet/?a=1#x") == _page_key("http://www.site.test/hizmet")
    assert _page_key("https://site.test") == _page_key("https://site.test/")
    assert _page_key("https://site.test/a") != _page_key("https://site.test/b")


@pytest.mark.parametrize("target", ["https://site.test/", "https://site.test/baska-hizmet"])
def test_page_mode_rejects_redirect_to_other_path(monkeypatch, target):
    Site({
        "https://site.test/hizmet": httpx.Response(302, headers={"location": target}),
        target: html(GOOD),
    }).install(monkeypatch)
    res = SiteCrawler().crawl_site("https://site.test/hizmet", scope="page")
    assert res["pages"] == []
    assert "Girilen sayfa başka adrese yönlendiriyor" in res["error"]


# --------------------------------------------------------------- hata mesajlari
def test_http_403_message(monkeypatch):
    Site({"https://site.test": httpx.Response(403, headers={"content-type": "text/html"}, text="no")}
         ).install(monkeypatch)
    res = SiteCrawler().crawl_site("https://site.test")
    assert res["pages"] == []
    assert "Site bot koruması nedeniyle erişime kapalı (HTTP 403)" in res["error"]


def test_http_500_is_not_reported_as_bot_block(monkeypatch):
    Site({"https://site.test": httpx.Response(500, headers={"content-type": "text/html"}, text="x")}
         ).install(monkeypatch)
    assert "bot koruması" not in SiteCrawler().crawl_site("https://site.test")["error"]


def _tls_chain():
    exc = httpx.ConnectError("baglanti hatasi")
    exc.__cause__ = ssl.SSLCertVerificationError("sertifika")
    return exc


@pytest.mark.parametrize("exc", [
    httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed"),
    _tls_chain(),
])
def test_tls_failure_message(monkeypatch, exc):
    Site({"https://site.test": exc}).install(monkeypatch)
    res = SiteCrawler().crawl_site("https://site.test")
    assert "Sertifika doğrulanamadı" in res["error"]


def test_ssrf_block_gives_turkish_security_message(monkeypatch):
    Site({"https://site.test": httpx.Response(302, headers={"location": "http://internal.test/admin"})}
         ).install(monkeypatch)
    res = SiteCrawler().crawl_site("https://site.test")
    assert "Güvenlik nedeniyle" in res["error"]


# --------------------------------------------------------------- butce
def test_budget_mid_crawl_keeps_collected_pages(monkeypatch):
    site = Site({
        "https://site.test": html(GOOD.replace("<main>", '<main><a href="/hizmetler">h</a>')),
        "https://site.test/hizmetler": html(GOOD),
    }).install(monkeypatch)
    monkeypatch.setattr(crawler_mod, "FetchBudget", lambda: FetchBudget(max_requests=3))
    res = SiteCrawler().crawl_site("https://site.test")
    assert res["error"] is None and len(res["pages"]) == 1
    assert len(site.requests) == 3                                      # ana sayfa + robots + sitemap


def test_budget_counts_redirect_hops_and_refuses_before_sending(monkeypatch):
    site = Site({
        "https://site.test/a": httpx.Response(301, headers={"location": "https://site.test/b"}),
        "https://site.test/b": httpx.Response(301, headers={"location": "https://site.test/c"}),
        "https://site.test/c": html(GOOD),
    }).install(monkeypatch)
    with pytest.raises(FetchBudgetExceeded):
        safe_fetch("https://site.test/a", allowed_types={"text/html"}, budget=FetchBudget(max_requests=2))
    assert site.requests == ["https://site.test/a", "https://site.test/b"]   # 3. adim GONDERILMEDI
    assert issubclass(FetchBudgetExceeded, UnsafeUrlError)


def test_thirteenth_request_is_refused(monkeypatch):
    site = Site({"https://site.test/x": html(GOOD)}).install(monkeypatch)
    budget = FetchBudget()
    for _ in range(12):
        safe_fetch("https://site.test/x", allowed_types={"text/html"}, budget=budget)
    with pytest.raises(FetchBudgetExceeded):
        safe_fetch("https://site.test/x", allowed_types={"text/html"}, budget=budget)
    assert len(site.requests) == 12


def test_timeout_is_clipped_to_remaining_budget_time(monkeypatch):
    site = Site({"https://site.test/x": html(GOOD)}).install(monkeypatch)
    safe_fetch("https://site.test/x", allowed_types={"text/html"}, budget=FetchBudget())
    assert site.timeouts[-1]["read"] == url_guard.READ_TIMEOUT
    assert site.timeouts[-1]["connect"] == url_guard.CONNECT_TIMEOUT

    budget = FetchBudget()
    budget._deadline = url_guard.time.monotonic() + 2.0                # 2 sn kaldi
    safe_fetch("https://site.test/x", allowed_types={"text/html"}, budget=budget)
    assert site.timeouts[-1]["read"] <= 2.0 and site.timeouts[-1]["connect"] <= 2.0

    budget._deadline = url_guard.time.monotonic() - 1                   # sure doldu
    n = len(site.requests)
    with pytest.raises(FetchBudgetExceeded):
        safe_fetch("https://site.test/x", allowed_types={"text/html"}, budget=budget)
    assert len(site.requests) == n


def test_slow_drip_body_cannot_exceed_total_time(monkeypatch):
    """read timeout parca basina; sure govde okunurken dolarsa indirme kesilir."""
    budget = FetchBudget()

    class Drip(httpx.SyncByteStream):
        def __iter__(self):
            yield b"<html><body>"
            budget._deadline = url_guard.time.monotonic() - 1           # okuma sirasinda sure doldu
            yield b"<main>gec gelen parca</main></body></html>"

    Site({"https://site.test/x": httpx.Response(
        200, headers={"content-type": "text/html"}, stream=Drip())}).install(monkeypatch)
    with pytest.raises(FetchBudgetExceeded):
        safe_fetch("https://site.test/x", allowed_types={"text/html"}, budget=budget)


def test_low_priority_corporate_pages_rank_after_product_pages():
    """Deterministik siralamada kurumsal/yasal sayfalar urun sayfalarini itmesin (canli: noksel)."""
    order = SiteCrawler()._prioritize_links(
        ["https://s.test/history", "https://s.test/board", "https://s.test/investor-relations",
         "https://s.test/spiral-welded-pipe", "https://s.test/dashboard-boru"], [])
    assert order[:2] == ["https://s.test/spiral-welded-pipe", "https://s.test/dashboard-boru"]


class _FakeAI:
    def __init__(self, profile):
        self.profile, self.prompts = profile, []

    def complete_json(self, prompt, **kw):
        import json as _json
        self.prompts.append(prompt)
        return _json.dumps(self.profile)


EMPTY_SERVICE = {"company_name": "X A.S.", "sector": "Tarim", "brand_summary": "s",
                 "products": [], "services": [], "target_audience": "", "use_cases": [],
                 "problems_solved": [], "brand_terms": [], "exclude_themes": [],
                 "suggested_keywords": []}
CONTENT = "## Sayfa: Kunye\nURL: https://s.test/kunye\n\n" + "Firma kunyesi ve adres bilgisi satiri. " * 10


def _extractor(profile):
    ex = ProfileExtractor.__new__(ProfileExtractor)
    ex.ai_service = _FakeAI(profile)
    return ex


def test_cache_path_page_scope_rejects_empty_service_and_adds_rules():
    from app.core.site_analyzer.crawler import CrawlContentUnusable
    from app.core.site_analyzer.profile_extractor import PAGE_SCOPE_RULES
    ex = _extractor(EMPTY_SERVICE)
    with pytest.raises(CrawlContentUnusable, match="profile_missing_service"):
        ex.extract_profile_from_site_content(CONTENT, crawl_scope="page")
    assert ex.ai_service.prompts[-1].endswith(PAGE_SCOPE_RULES)


def test_site_scope_prompt_unchanged_and_empty_service_allowed():
    from app.core.site_analyzer.profile_extractor import PROFILE_EXTRACTION_PROMPT
    ex = _extractor(EMPTY_SERVICE)
    profile = ex.extract_profile_from_site_content(CONTENT)
    assert profile["company_name"] == "X A.S."
    assert ex.ai_service.prompts[-1] == PROFILE_EXTRACTION_PROMPT.format(
        site_content=CONTENT, preliminary_info="(kullanıcı tarafından sağlanmadı)")


def test_site_content_gate_ignores_format_lines():
    """Cache'ten profil cikarimi da `## Sayfa:`/`URL:` satirlarini saymaz."""
    from app.core.site_analyzer.crawler import CrawlContentUnusable
    padded = "## Sayfa: " + "Uzun Baslik " * 20 + "\nURL: https://site.test/" + "a" * 200 + "\n\nkisa"
    extractor = ProfileExtractor.__new__(ProfileExtractor)
    with pytest.raises(CrawlContentUnusable):
        extractor.extract_profile_from_site_content(padded)


# --------------------------------------------------------------- safe_fetch genel
def test_safe_fetch_xml_and_plain_text(monkeypatch):
    Site({
        "https://site.test/s.xml": ("text/xml; charset=utf-8", "<urlset/>"),
        "https://site.test/robots.txt": ("text/plain", "Sitemap: https://site.test/s.xml"),
    }).install(monkeypatch)
    res = safe_fetch("https://site.test/s.xml", allowed_types={"application/xml", "text/xml"})
    assert (res.final_url, res.status, res.content_type, res.text) == (
        "https://site.test/s.xml", 200, "text/xml", "<urlset/>")
    assert res.content_encoding == ""
    assert safe_fetch("https://site.test/robots.txt", allowed_types={"text/plain"}).text.startswith("Sitemap:")
    with pytest.raises(UnsafeUrlError):
        safe_fetch("https://site.test/robots.txt", allowed_types={"application/xml"})


def test_safe_fetch_does_not_reject_content_encoding(monkeypatch):
    Site({"https://site.test/x": httpx.Response(
        200, headers={"content-type": "text/html", "content-encoding": "identity"}, text="ok")}
    ).install(monkeypatch)
    assert safe_fetch("https://site.test/x", allowed_types={"text/html"}).content_encoding == "identity"


def test_safe_fetch_html_contract_is_unchanged(monkeypatch):
    Site({
        "https://site.test/ok": html("<p>merhaba</p>"),
        "https://site.test/xml": xml("<a/>"),
        "https://site.test/loop": httpx.Response(302, headers={"location": "https://site.test/loop"}),
        "https://site.test/internal": httpx.Response(302, headers={"location": "http://internal.test/"}),
    }).install(monkeypatch)
    assert safe_fetch_html("https://site.test/ok") == "<p>merhaba</p>"
    with pytest.raises(UnsafeUrlError, match="^Yalnızca HTML içerik desteklenir$"):
        safe_fetch_html("https://site.test/xml")
    with pytest.raises(UnsafeUrlError, match="^URL erişilemedi veya hata döndü: HTTP 404$") as e:
        safe_fetch_html("https://site.test/yok")
    assert isinstance(e.value, HttpStatusError) and e.value.status_code == 404
    with pytest.raises(UnsafeUrlError, match="^URL yönlendirme döngüsü içeriyor$"):
        safe_fetch_html("https://site.test/loop")
    with pytest.raises(UnsafeUrlError, match="iç ağ"):
        safe_fetch_html("https://site.test/internal")


def test_tls_error_is_distinct_subclass(monkeypatch):
    Site({"https://site.test/x": httpx.ConnectError("CERTIFICATE_VERIFY_FAILED")}).install(monkeypatch)
    with pytest.raises(TlsVerificationError):
        safe_fetch_html("https://site.test/x")


# --------------------------------------------------------------- page kapsaminda hizmet zorunlulugu
def test_missing_service_info():
    assert missing_service_info({"products": [], "services": []}, "page") is True
    assert missing_service_info({}, "page") is True
    assert missing_service_info({"products": [], "services": ["Toprak analizi"]}, "page") is False
    assert missing_service_info({"products": [], "services": []}, "site") is False


def test_extract_profile_page_scope_without_service_returns_error(monkeypatch):
    import json

    class AI:
        def complete_json(self, *a, **k):
            return json.dumps({"company_name": "Imece", "sector": "Tarim", "products": [], "services": []})

    ex = ProfileExtractor(AI())
    page = {"url": "https://site.test/x", "title": "T", "text": "Uzun organizasyon aciklamasi. " * 20,
            "status": 200}
    seen = []
    monkeypatch.setattr(ex.crawler, "crawl_site",
                        lambda url, scope="site": seen.append(scope) or {"pages": [page], "error": None})
    out = ex.extract_profile("https://site.test/x", crawl_scope="page")
    assert seen == ["page"]
    assert out["profile"] is None
    assert out["error"] == "profile_missing_service: Sayfada hizmet/ürün açıklaması bulunamadı"
    assert out["source_pages"][0]["url"] == "https://site.test/x"
    # site kapsaminda ayni profil hata DEGIL
    assert ex.extract_profile("https://site.test/x")["error"] is None
