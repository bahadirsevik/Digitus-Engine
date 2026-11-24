"""Dijital denetim: engel tespiti, sayfa sinyalleri, sayfa turu ve bulgu kurallari.

Ag YOK: SiteFetcher / TLS / RDAP / PageSpeed sahteleriyle calisir.
"""
import pytest

from app.core.digital_audit import runner, site_fetch
from app.core.digital_audit.signals import classify_page, detect_block, extract_signals
from app.core.digital_audit.site_fetch import BLOCKED, ERROR, OK, PageResponse, registrable_domain

FILLER = "<p>" + ("Kliniğimiz hakkında ayrıntılı bilgi. " * 40) + "</p>"

HOME = f"""<html lang="tr"><head><title>Örnek Klinik</title>
<meta name="description" content="Örnek klinik açıklaması">
<meta name="viewport" content="width=device-width">
<script>(function(w){{w.dataLayer=[]}})(window);/* GTM-ABC1234 */</script>
<script type="application/ld+json">{{"@type": "Dentist"}}</script></head><body>
<nav><a href="/iletisim-sayfasi">İletişim</a><a href="/biz">Hakkımızda</a>
<a href="/gizlilik">KVKK Aydınlatma Metni</a><a href="/yazilar">Blog</a></nav>
<h1>Örnek Klinik</h1>{FILLER}
<a href="tel:+902120000000">Ara</a><a href="https://wa.me/905320000000">WhatsApp</a>
<a href="mailto:info@ornek.com">Mail</a>
<a href="https://www.instagram.com/ornekklinik/">Instagram</a>
<a href="/kvkk-casino-bonus-2026/">en iyi slot siteleri</a>
<form role="search"><input type="search"></form>
<form><input type="text"><input type="tel"><textarea></textarea></form>
<img src="a.jpg" alt="Klinik"><img src="b.jpg">
</body></html>"""


def page(title, h1):
    return f"<html><head><title>{title}</title><meta name='viewport' content='x'></head><body><h1>{h1}</h1>{FILLER}</body></html>"


PAGES = {
    "https://ornek.com/": HOME,
    "https://ornek.com/iletisim-sayfasi": page("İletişim", "Bize Ulaşın"),
    "https://ornek.com/biz": page("Hakkımızda", "Hakkımızda"),
    "https://ornek.com/gizlilik": page("KVKK", "KVKK Aydınlatma Metni"),
    "https://ornek.com/yazilar": page("Blog", "Blog") .replace("<h1>", "<time datetime='2026-10-01'></time><h1>"),
    "https://ornek.com/kvkk-casino-bonus-2026/": page("Casino bonus", "En iyi slot siteleri"),
}


class FakeFetcher:
    """SiteFetcher sahtesi: `pages` sozlugunden okur."""
    pages = PAGES
    home_response = None

    def __init__(self, origin, budget, delay=0):
        self.robots_present = True

    def get_html(self, url):
        if type(self).home_response is not None and url.rstrip("/") == "https://ornek.com":
            return type(self).home_response
        html = self.pages.get(url)
        return PageResponse(OK, url, html=html) if html else PageResponse(ERROR, url, reason="http_404")

    def check_link(self, url):
        return {"state": "broken", "status": 404} if "kirik" in url else {"state": "ok"}


class FakeCrawler:
    def _try_sitemap(self, origin, budget):
        return ["https://ornek.com/a", "https://ornek.com/b"]


@pytest.fixture
def fake_net(monkeypatch):
    FakeFetcher.pages, FakeFetcher.home_response = PAGES, None
    monkeypatch.setattr(runner, "SiteFetcher", FakeFetcher)
    monkeypatch.setattr(runner, "SiteCrawler", FakeCrawler)
    monkeypatch.setattr(site_fetch, "tls_info", lambda host: {"valid": True, "expires": "2027-06-01", "days_left": 200})
    monkeypatch.setattr(site_fetch, "domain_info", lambda host: {"available": False, "reason": "http_404"})
    monkeypatch.setattr(runner, "run_pagespeed", lambda url, strategy: {"available": False, "reason": "not_configured"})
    return FakeFetcher


def by_check(result):
    return {f["check"]: f for f in result["findings"]}


# --- engel tespiti ---------------------------------------------------------

def test_challenge_page_is_detected():
    html = "<html><head><title>Just a moment...</title></head><body>Checking your browser</body></html>"
    assert detect_block(html) == "challenge_page"


def test_turkish_challenge_page_is_detected():
    assert detect_block("<title>Hepsiburada | Güvenlik</title><body>Güvenlik doğrulaması</body>") == "challenge_page"


def test_normal_page_mentioning_captcha_is_not_a_block():
    assert detect_block(f"<html><body>{FILLER} Formumuz reCAPTCHA ile korunur.</body></html>") is None


# --- sayfa turu ------------------------------------------------------------

def test_url_pattern_alone_does_not_classify():
    """Pilot dersi: adresinde 'kvkk' gecen kumar sayfasi KVKK sayfasi sayilmaz."""
    result = classify_page("https://ornek.com/kvkk-casino-bonus-2026/", ["en iyi slot siteleri"])
    assert result["type"] is None
    assert result["candidate"] == "kvkk"


def test_anchor_text_classifies_even_with_opaque_url():
    result = classify_page("https://ornek.com/biz", ["Hakkımızda"], in_nav=True)
    assert result["type"] == "hakkimizda"
    assert "baglanti_metni" in result["evidence"] and "menu" in result["evidence"]


def test_page_content_classifies():
    signals = extract_signals(page("Bize Ulaşın", "İletişim"), "https://ornek.com/x")
    assert classify_page("https://ornek.com/x", [], signals=signals)["type"] == "iletisim"


# --- sayfa sinyalleri ------------------------------------------------------

def test_extract_signals_reads_raw_html():
    s = extract_signals(HOME, "https://ornek.com/")
    assert s["title"] == "Örnek Klinik" and s["viewport"] and not s["js_thin"]
    assert s["tracker_ids"]["gtm"] == ["GTM-ABC1234"] and s["tracker_ids"]["ga4"] == []
    assert s["tel"] == ["tel:+902120000000"] and s["whatsapp"] == ["https://wa.me/905320000000"]
    assert s["emails"] == ["info@ornek.com"]
    assert "instagram" in s["social"] and s["ld_types"] == ["Dentist"]
    assert [f["is_search"] for f in s["forms"]] == [True, False]
    assert s["img_total"] == 2 and s["img_no_alt"] == 1
    nav = [l["text"] for l in s["links"] if l["nav"]]
    assert "İletişim" in nav and "en iyi slot siteleri" not in nav


def test_js_rendered_page_is_flagged_thin():
    html = "<html><head><title>App</title></head><body><div id='root'></div><script>" + "x" * 6000 + "</script></body></html>"
    assert extract_signals(html, "https://app.test/")["js_thin"] is True


TINY_SHELL = '<html><head><title>App</title></head><body><div id="root"></div><script src="/app.js"></script></body></html>'


def test_tiny_js_shell_is_flagged_thin():
    """Bos uygulama koku + tek harici script: HTML kucuk olsa da icerik okunamamistir."""
    assert len(TINY_SHELL) < 200
    assert extract_signals(TINY_SHELL, "https://app.test/")["js_thin"] is True


def test_short_static_page_without_scripts_is_not_thin():
    html = "<html><body><p>Yakında açılıyoruz.</p></body></html>"
    assert extract_signals(html, "https://x.test/")["js_thin"] is False


def test_registrable_domain():
    assert registrable_domain("www.ornek.com") == "ornek.com"
    assert registrable_domain("www.ornek.com.tr") == "ornek.com.tr"
    assert registrable_domain("blog.ornek.com") == "ornek.com"


# --- bulgu kurallari -------------------------------------------------------

def test_readable_site_produces_every_on_page_check(fake_net):
    result = runner.run_target("https://ornek.com/", own=True)
    checks = by_check(result)
    assert result["status"] == "completed"
    assert {c for _, c, _ in runner.ON_PAGE_CHECKS} <= set(checks)
    assert checks["site.reachable"]["state"] == "observed"
    assert checks["tracking.gtm"]["state"] == "observed"
    # Okunabilir sayfada bulunmayan etiket "gozlenmedi"dir ve sinir notu tasir
    assert checks["tracking.ga4"]["state"] == "not_observed" and checks["tracking.ga4"]["limits"]
    assert checks["site.cta"]["value"] == {"phone": True, "whatsapp": True, "forms": 1}
    assert checks["seo.sitemap"]["value"] == {"urls": 2, "capped": False}
    assert checks["seo.blog"]["value"]["newest_date"] == "2026-10-01"
    assert checks["site.speed_mobile"]["state"] == "unknown"  # anahtar yok: "sorun" degil


def test_page_types_use_anchor_and_content_not_url(fake_net):
    types = by_check(runner.run_target("https://ornek.com/", own=True))["site.structure"]["value"]["page_types"]
    assert {"iletisim", "hakkimizda", "kvkk", "blog"} <= set(types)
    # KVKK olarak secilen sayfa kumar sayfasi degil, menudeki gercek sayfa
    assert types["kvkk"]["url"] == "https://ornek.com/gizlilik"


def test_blocked_site_never_reports_absence(fake_net):
    """Engel sayfasi icerik sayilmaz; hicbir sayfa ici kontrol 'yok' demez."""
    fake_net.home_response = PageResponse(BLOCKED, "https://ornek.com/", http_status=403, reason="http_403")
    result = runner.run_target("https://ornek.com/", own=True)
    checks = by_check(result)
    assert result["status"] == "blocked" and result["pages_checked"] == 0
    assert checks["site.reachable"]["state"] == "blocked"
    on_page = [checks[c] for _, c, _ in runner.ON_PAGE_CHECKS]
    assert {f["state"] for f in on_page} == {"blocked"}
    assert all(f["assessment"] is None and f["value"] is None for f in on_page)
    assert checks["site.ssl"]["state"] == "observed"  # ag bulgulari engelden bagimsiz
    assert "site.speed_mobile" not in checks


def test_challenge_page_content_is_not_used(fake_net, monkeypatch):
    """200 donen dogrulama sayfasinin basligi site basligi olarak raporlanmaz."""
    monkeypatch.setattr(site_fetch, "safe_fetch", lambda *a, **k: site_fetch_result(
        "<html><head><title>Just a moment...</title></head><body>Verify you are human</body></html>"))
    response = _fetcher_with_robots([]).get_html("https://ornek.com/")
    assert response.status == BLOCKED and response.html == ""


def site_fetch_result(html):
    from app.core.url_guard import FetchResult
    return FetchResult(final_url="https://ornek.com/", status=200, content_type="text/html", content_encoding="", text=html)


def test_js_rendered_site_reports_unknown_not_absent(fake_net):
    thin = "<html><head><title>App</title></head><body><div id='root'></div><script>" + "x" * 6000 + "</script></body></html>"
    fake_net.pages = {"https://ornek.com/": thin}
    checks = by_check(runner.run_target("https://ornek.com/", own=False))
    on_page = [checks[c] for _, c, _ in runner.ON_PAGE_CHECKS]
    assert "not_observed" not in {f["state"] for f in on_page}
    assert checks["contact.phone"]["state"] == "unknown"
    assert checks["tracking.ga4"]["state"] == "unknown"
    assert checks["seo.titles"]["state"] == "observed"  # gorulen sey yine gozlemdir
    assert not any(f["assessment"] in ("warning", "problem") for f in on_page if f["state"] == "unknown")


def test_tiny_js_shell_reports_unknown_not_absent(fake_net):
    fake_net.pages = {"https://ornek.com/": TINY_SHELL}
    checks = by_check(runner.run_target("https://ornek.com/", own=False))
    assert checks["contact.phone"]["state"] == "unknown"
    assert checks["tracking.ga4"]["state"] == "unknown"
    assert checks["site.cta"]["state"] == "unknown" and checks["site.cta"]["assessment"] is None
    assert "not_observed" not in {checks[c]["state"] for _, c, _ in runner.ON_PAGE_CHECKS}


def _fetcher_with_robots(lines):
    from urllib.robotparser import RobotFileParser

    fetcher = site_fetch.SiteFetcher.__new__(site_fetch.SiteFetcher)
    fetcher.delay, fetcher.budget, fetcher.headers = 0, None, {}
    fetcher._robots = RobotFileParser()
    fetcher._robots.parse(lines)
    return fetcher


def test_link_check_sends_no_request_to_robots_disallowed_url(monkeypatch):
    calls = []
    monkeypatch.setattr(site_fetch, "safe_fetch",
                        lambda url, **kw: calls.append(url) or site_fetch_result("<html></html>"))
    fetcher = _fetcher_with_robots(["User-agent: *", "Disallow: /private"])
    assert fetcher.check_link("https://ornek.com/private/rapor") == {"state": "skipped"}
    assert calls == []
    assert fetcher.check_link("https://ornek.com/acik")["state"] == "ok"
    assert calls == ["https://ornek.com/acik"]


def test_link_check_covers_only_internal_links(fake_net, monkeypatch):
    checked = []
    monkeypatch.setattr(FakeFetcher, "check_link", lambda self, url: checked.append(url) or {"state": "ok"})
    fake_net.pages = {**PAGES, "https://ornek.com/": HOME.replace("</body>", '<a href="/okunmayan">x</a></body>')}
    runner.run_target("https://ornek.com/", own=True)
    # Ana sayfadaki Instagram baglantisina istek atilmaz; yalniz site ici adres denetlenir
    assert checked == ["https://ornek.com/okunmayan"]


def test_robots_skipped_links_are_not_counted_as_checked(fake_net, monkeypatch):
    monkeypatch.setattr(FakeFetcher, "check_link", lambda self, url: {"state": "skipped"})
    links = by_check(runner.run_target("https://ornek.com/", own=True))["site.broken_links"]
    assert links["state"] == "unknown"  # hic denetlenemedi: "kirik yok" denmez


def test_unreachable_site_is_failed_and_unknown(fake_net):
    fake_net.home_response = PageResponse(ERROR, "https://ornek.com/", http_status=500, reason="http_500")
    result = runner.run_target("https://ornek.com/", own=True)
    checks = by_check(result)
    assert result["status"] == "failed"
    assert checks["site.reachable"]["state"] == "error"
    assert {checks[c]["state"] for _, c, _ in runner.ON_PAGE_CHECKS} == {"unknown"}


def test_broken_link_is_reported(fake_net):
    fake_net.pages = {**PAGES, "https://ornek.com/": HOME.replace("</body>", '<a href="/kirik-sayfa">x</a></body>')}
    links = by_check(runner.run_target("https://ornek.com/", own=True))["site.broken_links"]
    assert links["assessment"] == "problem"
    assert links["value"]["broken"] == [{"url": "https://ornek.com/kirik-sayfa", "status": 404}]


def test_empty_sitemap_result_is_unknown_not_missing(fake_net, monkeypatch):
    monkeypatch.setattr(FakeCrawler, "_try_sitemap", lambda self, origin, budget: [])
    sitemap = by_check(runner.run_target("https://ornek.com/", own=True))["seo.sitemap"]
    assert sitemap["state"] == "unknown" and sitemap["assessment"] is None


def test_unknown_findings_carry_no_value(fake_net):
    thin = "<html><head><title>App</title></head><body><div id='root'></div><script>" + "x" * 6000 + "</script></body></html>"
    fake_net.pages = {"https://ornek.com/": thin}
    findings = runner.run_target("https://ornek.com/", own=False)["findings"]
    assert all(f["value"] is None for f in findings if f["state"] == "unknown")


def test_microsoft_is_not_an_ecommerce_platform():
    html = f"<html><body>{FILLER}<script src='https://ajax.aspnetcdn.com/microsoft.js'></script></body></html>"
    assert extract_signals(html, "https://ornek.com/")["platforms"] == []


def test_competitor_gets_only_mobile_speed(fake_net):
    checks = by_check(runner.run_target("https://ornek.com/", own=False))
    assert "site.speed_mobile" in checks and "site.speed_desktop" not in checks
