"""
Website crawler for brand profile extraction.
Crawls homepage + key pages, extracts clean text content.
"""
import json
import logging
import re
from typing import List, Dict, Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from app.core.url_guard import (
    HTML_TYPES,
    FetchBudget,
    FetchBudgetExceeded,
    FetchResult,
    HttpStatusError,
    TlsVerificationError,
    UnsafeUrlError,
    safe_fetch,
)

logger = logging.getLogger(__name__)

# Crawl limits
MAX_PAGES_PER_SITE = 5
REQUEST_TIMEOUT = 15  # seconds
MAX_CONTENT_LENGTH = 50_000  # chars per page — CIKARILAN METIN siniri
# Ham HTML yalniz bellek guvenligi icin sinirlanir. Metin siniri HTML'e
# uygulanmamali: agir temali sitelerde <head> (CSS/JS) 30K+ karakter tutar ve
# asil icerik (<main>) 50K'dan sonra baslar; HTML'i kesmek icerigi hic
# gormeden atar (proteinim.com: <main> 75.683. karakterde, metin 96 karakter
# kaliyordu -> "no page passed quality gate").
MAX_HTML_LENGTH = 2_000_000
CRAWL_SCOPES = ("site", "page")
MAX_CHILD_SITEMAPS = 3   # sitemapindex tek seviye, en fazla bu kadar alt harita
MAX_SITEMAP_URLS = 200   # toplanan sitemap URL tavani
XML_TYPES = {"application/xml", "text/xml"}
TEXT_TYPES = {"text/plain"}
BOT_BLOCK_STATUSES = {401, 403, 429, 503}

# --- Icerik-kodlama guvenligi (fail-closed) --------------------------------
# YALNIZ bu kodlamalar ilan edilir ve kabul edilir. `br` (brotli) BILINCLI olarak
# DISARIDA: httpx onu ancak `brotli` paketi kuruluysa cozebilir; kurulu degilse
# `response.text` SESSIZCE ikili cop doner ve bu cop AI'a gider.
SUPPORTED_CONTENT_ENCODINGS = {"", "identity", "gzip", "deflate"}
ACCEPT_ENCODING_HEADER = "gzip, deflate"

# --- Cikarilan metin kalite esikleri ---------------------------------------
MIN_USABLE_TEXT_CHARS = 200      # bundan kisa metin profil cikarimi icin anlamsiz
MAX_REPLACEMENT_RATIO = 0.02     # U+FFFD orani (cozulememis bayt gostergesi)
MAX_CONTROL_RATIO = 0.02         # yazdirilamaz kontrol karakteri orani
CONTROL_WHITELIST = "\n\r\t"     # normal metinde beklenen kontrol karakterleri


class UnsupportedContentEncoding(RuntimeError):
    """Sunucu, desteklenmeyen bir Content-Encoding ile yanit verdi (fail-closed)."""


class CrawlContentUnusable(RuntimeError):
    """Cikarilan site metni kalite kapisini gecemedi; AI'a GONDERILEMEZ.

    Bu istisna bilincli olarak sert: kullanilamaz icerigin `preliminary_info`
    (kullanici beyani) ile sessizce telafi edilmesini engeller.
    """


def _encoding_is_supported(result: FetchResult) -> bool:
    """Desteklenmeyen kodlamada icerik KULLANILMAZ (fail-closed)."""
    return result.content_encoding in SUPPORTED_CONTENT_ENCODINGS


def dedupe_lines(text: str) -> str:
    """Bosluk-normalize + buyuk/kucuk harf duyarsiz satir tekrarlarini ayiklar."""
    seen, out = set(), []
    for line in (text or "").split("\n"):
        line = re.sub(r"\s+", " ", line).strip()
        key = line.casefold()
        if line and key not in seen:
            seen.add(key)
            out.append(line)
    return "\n".join(out)


def assess_text_quality(text: str) -> Dict:
    """Cikarilan metnin kullanilabilirligini olcer.

    Cozulememis (or. brotli) icerik `response.text` icinde U+FFFD ve kontrol
    karakterleriyle dolu bir dizeye donusur; bu, gozle bakilmadikca fark edilmez.
    """
    text = text or ""
    n = len(text)
    if n == 0:
        return {"usable": False, "reason": "empty", "chars": 0,
                "replacement_ratio": 0.0, "control_ratio": 0.0}
    sample = text[:5000]
    m = len(sample)
    repl = sample.count("�") / m
    ctrl = sum(1 for ch in sample
               if ord(ch) < 32 and ch not in CONTROL_WHITELIST) / m
    if repl > MAX_REPLACEMENT_RATIO:
        reason = "binary_or_undecoded"
    elif ctrl > MAX_CONTROL_RATIO:
        reason = "control_characters"
    elif n < MIN_USABLE_TEXT_CHARS:
        reason = "too_short"
    else:
        reason = None
    return {"usable": reason is None, "reason": reason, "chars": n,
            "replacement_ratio": round(repl, 4), "control_ratio": round(ctrl, 4)}

# Priority slugs for finding relevant pages
PRIORITY_SLUGS = [
    "hakkimizda", "hakkinda", "about", "about-us",
    "urunler", "urunlerimiz", "products", "product",
    "hizmetler", "hizmetlerimiz", "services",
    "kategoriler", "kategori", "category", "categories",
    "shop", "magaza", "store",
]

# Kurumsal/yasal/hesap sayfalari urun-hizmet anlatmaz; aday sirasi deterministik
# oldugundan bunlar one gecerse urun sayfalari HER SEFERINDE kacar (canli: noksel).
# Yol PARCASI eslesir (alt-dize degil: "dashboard" != "board").
LOW_PRIORITY_SEGMENTS = {
    "history", "tarihce", "board", "yonetim", "investor", "yatirimci",
    "newsroom", "news", "haber", "haberler", "basin", "press",
    "contact", "contacts", "iletisim", "gdpr", "kvkk", "privacy", "gizlilik",
    "cerez", "cookie", "cookies", "terms", "sozlesme", "policy", "politika",
    "login", "giris", "uye", "account", "hesap", "sepet", "cart", "checkout",
    "kariyer", "career", "careers",
}




def _site_key(host: Optional[str]) -> str:
    """Ayni-site anahtari: kucuk harf + bas `www.` yok sayilir."""
    host = (host or "").lower()
    return host[4:] if host.startswith("www.") else host


def _is_same_site_url(url: str, base_url: str) -> bool:
    parsed = urlparse(url)
    return (parsed.scheme in ("http", "https")
            and _site_key(parsed.hostname) == _site_key(urlparse(base_url).hostname))


def _page_key(url: str) -> tuple:
    """Sayfa kimligi: www/sema/sorgu/fragment/sondaki `/` yok sayilir."""
    parsed = urlparse(url)
    return _site_key(parsed.hostname), parsed.path.rstrip("/") or "/"


# --- JSON-LD ---------------------------------------------------------------
# Organization/LocalBusiness/Corporation, Product, Service ve yakin turevleri
_LD_ENTITY_HINTS = ("organization", "business", "corporation", "product", "service")


def _ld_text(value) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", value)).strip()


def _as_list(value) -> list:
    return value if isinstance(value, list) else ([] if value is None else [value])


def _walk_json_ld(node, out: List[str], depth: int = 0) -> None:
    """JSON-LD agacindan ad/aciklama/SSS/breadcrumb satirlarini toplar."""
    if depth > 8:
        return
    if isinstance(node, list):
        for item in node:
            _walk_json_ld(item, out, depth + 1)
        return
    if not isinstance(node, dict):
        return
    types = [t.lower() for t in _as_list(node.get("@type")) if isinstance(t, str)]
    if any(hint in t for t in types for hint in _LD_ENTITY_HINTS):
        out += [_ld_text(node.get("name")), _ld_text(node.get("description"))]
    if "faqpage" in types:
        for question in _as_list(node.get("mainEntity")):
            if isinstance(question, dict):
                out.append(_ld_text(question.get("name")))
                for answer in _as_list(question.get("acceptedAnswer")):
                    if isinstance(answer, dict):
                        out.append(_ld_text(answer.get("text")))
    if "breadcrumblist" in types:
        for element in _as_list(node.get("itemListElement")):
            if isinstance(element, dict):
                item = element.get("item")
                out.append(_ld_text(element.get("name")
                                    or (item.get("name") if isinstance(item, dict) else None)))
    for value in node.values():
        if isinstance(value, (dict, list)):
            _walk_json_ld(value, out, depth + 1)


class SiteCrawler:
    """Crawls a website and extracts text content from key pages."""

    def __init__(self, timeout: int = REQUEST_TIMEOUT):
        # `timeout` geriye uyumluluk icin tutulur; gercek timeout'lari url_guard
        # (istek basina) ve FetchBudget (toplam sure) belirler.
        self.timeout = timeout
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,*/*;q=0.8"
            ),
            "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.5",
            "Accept-Encoding": ACCEPT_ENCODING_HEADER,
            "DNT": "1",
            "Upgrade-Insecure-Requests": "1",
        }

    def crawl_site(self, url: str, scope: str = "site") -> Dict:
        """
        Crawl a site.

        scope="site": girilen URL + origin'den robots/sitemap + oncelikli sayfalar
                      (toplam MAX_PAGES_PER_SITE).
        scope="page": YALNIZ girilen sayfa (robots/sitemap/ek sayfa YOK); redirect
                      sonrasi normalize edilmis yol farkliysa hata.

        Tum indirmeler tek bir FetchBudget'i paylasir (12 istek / 45 sn).

        Returns:
            {
                "base_url": "https://vepa.com.tr",
                "pages": [
                    {"url": "...", "title": "...", "text": "...", "status": 200},
                    ...
                ],
                "sitemap_urls": [...],
                "error": null
            }
        """
        if scope not in CRAWL_SCOPES:
            raise ValueError(f"crawl scope gecersiz: {scope!r} (beklenen: {CRAWL_SCOPES})")
        url = self._normalize_url(url)
        budget = FetchBudget()
        result = {
            "base_url": url,
            "pages": [],
            "sitemap_urls": [],
            "error": None,
        }
        collected: List[Dict] = []

        try:
            # Step 1: Girilen sayfa. SERT hata (ag/TLS/HTTP/kodlama/icerik-tipi)
            # devam edilemez; kullanici mesaji hata turune gore secilir.
            try:
                first = self._fetch_page_raw(url, budget)
            except Exception as e:
                logger.warning(f"Crawl first page failed for {url}: {e}")
                result["error"] = self._fetch_error_message(e)
                return result
            if scope == "page" and _page_key(first["url"]) != _page_key(url):
                result["error"] = (
                    "crawl_redirect_mismatch: Girilen sayfa başka adrese yönlendiriyor")
                return result
            # JS ile render edilen sayfalar ("Yukleniyor...") icerik tasimaz;
            # yine de LINK ve SITEMAP kaynagi olarak kullanilir.
            collected.append(first)

            if scope == "site":
                try:
                    self._collect_site_pages(first, collected, result, budget)
                except FetchBudgetExceeded:
                    logger.info("Crawl butcesi doldu (%s): eldeki icerikle devam", url)

            # Kalite kapisi: bozuk bayt sayfa bazinda, "cok kisa" TOPLAM kaynak
            # metni uzerinde. Hicbiri gecemezse ACIKCA bildir: sessizce bos icerik
            # dondurup AI'in beyandan uydurmasina izin verilmez.
            self._apply_quality_gate(collected, result)

        except Exception as e:
            logger.error(f"Crawl error for {url}: {e}")
            result["error"] = str(e)
            result["pages"] = []

        # Remove raw HTML from final output (keep only text)
        for page in collected:
            page.pop("html", None)
            page.pop("spa", None)

        return result

    def _collect_site_pages(
        self, first: Dict, collected: List[Dict], result: Dict, budget: FetchBudget
    ) -> None:
        """Site modu: origin sitemap'i + ic linkler -> oncelikli ek sayfalar."""
        parsed = urlparse(first["url"])
        origin = f"{parsed.scheme}://{parsed.netloc}"

        # Step 2: robots.txt / sitemap — HER ZAMAN origin'den, girilen yoldan degil
        sitemap_urls = self._try_sitemap(origin, budget)
        result["sitemap_urls"] = sitemap_urls[:20]  # Keep first 20 for reference

        # Step 3: Find candidate pages from page links
        internal_links = self._extract_internal_links(
            first["html"], first["url"], parsed.hostname
        )

        # Step 4: Prioritize pages by slug matching (ilk sayfa tekrar indirilmez)
        seen = {_page_key(first["url"])}
        candidates = []
        for page_url in self._prioritize_links(internal_links, sitemap_urls):
            key = _page_key(page_url)
            if key not in seen:
                seen.add(key)
                candidates.append(page_url)

        # Step 5: Crawl top candidate pages (up to MAX_PAGES_PER_SITE - 1)
        for page_url in candidates[: MAX_PAGES_PER_SITE - 1]:
            page = self._fetch_page(page_url, budget)
            if page:
                collected.append(page)

    def _apply_quality_gate(self, collected: List[Dict], result: Dict) -> None:
        def source_usable(pages: List[Dict]) -> bool:
            text = dedupe_lines("\n".join(p["text"] for p in pages))
            return assess_text_quality(text)["usable"]

        # Kisa sayfalar yalnizca toplam metin icin GEREKIYORSA alinir
        usable = [p for p in collected if p["quality"]["usable"]]
        pool = [p for p in collected if p["quality"]["reason"] in (None, "too_short")]
        pages = usable if source_usable(usable) else pool
        if source_usable(pages):
            result["pages"] = pages
            if len(collected) > len(pages):
                result["skipped_low_quality"] = len(collected) - len(pages)
            return

        if any(p.get("spa") for p in collected):
            result["error"] = (
                "crawl_content_unusable: Site içeriği JavaScript ile yükleniyor, okunamadı")
        else:
            bad = next((p["quality"]["reason"] for p in collected
                        if p["quality"]["reason"] not in (None, "too_short")), None)
            result["error"] = (
                f"crawl_content_unusable: Site metni okunamadı ({bad})" if bad
                else "crawl_content_unusable: Sayfa metni çok kısa, hizmet/ürün bilgisi bulunamadı")

    @staticmethod
    def _fetch_error_message(exc: Exception) -> str:
        if isinstance(exc, FetchBudgetExceeded):
            return "crawl_budget_exceeded: Site zamanında yanıt vermedi"
        if isinstance(exc, HttpStatusError):
            if exc.status_code in BOT_BLOCK_STATUSES:
                return ("crawl_blocked: Site bot koruması nedeniyle erişime kapalı "
                        f"(HTTP {exc.status_code})")
            return f"crawl_http_error: Sayfa açılamadı (HTTP {exc.status_code})"
        if isinstance(exc, TlsVerificationError):
            return "crawl_tls_error: Sertifika doğrulanamadı"
        if isinstance(exc, UnsupportedContentEncoding):
            return "crawl_fetch_failed: Sayfa kodlaması desteklenmiyor"
        if isinstance(exc, UnsafeUrlError):
            return f"crawl_fetch_failed: {exc}"  # SSRF dahil, guard mesajlari Turkce
        return "crawl_fetch_failed: Sayfa alınamadı"

    def _normalize_url(self, url: str) -> str:
        """Ensure URL has scheme."""
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        return url.rstrip("/")

    def _fetch_page(self, url: str, budget: Optional[FetchBudget] = None) -> Optional[Dict]:
        """Fetch a single page; sert hatada None (butce asimi disinda)."""
        try:
            return self._fetch_page_raw(url, budget)
        except FetchBudgetExceeded:
            raise
        except Exception as e:
            logger.warning(f"Fetch error for {url}: {e}")
            return None

    def _fetch_page_raw(self, url: str, budget: Optional[FetchBudget] = None) -> Dict:
        """Fetch a single page and extract text; hata durumunda istisna firlatir."""
        response = safe_fetch(
            url, allowed_types=HTML_TYPES, headers=self.headers,
            max_bytes=MAX_HTML_LENGTH, budget=budget,
        )
        if not _encoding_is_supported(response):
            # FAIL-CLOSED: cozulemeyen icerik ASLA downstream'e (AI'a) gitmez.
            raise UnsupportedContentEncoding(
                f"Unsupported Content-Encoding {response.content_encoding!r} for {url}")

        html = response.text
        soup = BeautifulSoup(html, "lxml")

        # Meta/JSON-LD, script'ler silinmeden ONCE okunur (SPA siteler bilgiyi
        # buraya koyar; govde yalniz "Yukleniyor..." olabilir).
        meta_lines = self._extract_metadata(soup)
        title = soup.title.string.strip() if soup.title and soup.title.string else ""

        # Remove noise elements
        for tag in soup(["nav", "footer", "header", "script", "style",
                        "noscript", "iframe", "svg"]):
            tag.decompose()

        # Try to find main content
        main = soup.find("main") or soup.find("article") or soup.find("body")
        body_text = main.get_text(separator="\n", strip=True) if main else ""
        heading_lines = ([h.get_text(" ", strip=True) for h in main.find_all(["h1", "h2", "h3"])]
                         if main else [])

        # Meta + basliklar + govde tek metinde; tekrarlar bir kez sayilir.
        text = dedupe_lines("\n".join(meta_lines + heading_lines + [body_text]))
        text = text[:MAX_CONTENT_LENGTH]

        # Kalite SONUCU dondurulur, sayfa burada ATILMAZ.
        # Ayrim onemli:
        #   * SERT hata (HTTP hatasi, desteklenmeyen kodlama, html olmayan) -> istisna
        #   * Kalite hatasi -> sayfa DONER ama `quality.usable=False` olur.
        # Boylece JS ile render edilen bir ANA SAYFA icerik olarak kullanilmaz,
        # fakat linkleri/sitemap'i uzerinden ic sayfalara ulasmak MUMKUN kalir.
        quality = assess_text_quality(text)
        if not quality["usable"]:
            logger.info(
                "Sayfa metni icerik olarak kullanilamaz (%s) %s — chars=%s repl=%s ctrl=%s",
                quality["reason"], url, quality["chars"],
                quality["replacement_ratio"], quality["control_ratio"])

        return {
            "url": response.final_url,
            "title": title,
            "text": text,
            "status": response.status,
            "quality": quality,          # icerik olarak kullanilabilir mi?
            "spa": self._looks_like_spa(soup, html, body_text),  # Temporary
            "html": html,  # Temporary, removed before return
        }

    @staticmethod
    def _extract_metadata(soup: BeautifulSoup) -> List[str]:
        """title, meta/og aciklamalari ve JSON-LD adlari/aciklamalari."""
        lines = [soup.title.string] if soup.title and soup.title.string else []
        for attr, key in (("name", "description"), ("property", "og:title"),
                          ("property", "og:description"), ("property", "og:site_name")):
            tag = soup.find("meta", attrs={attr: key})
            if tag and tag.get("content"):
                lines.append(tag["content"])
        for tag in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(tag.string or tag.get_text() or "", strict=False)
            except (ValueError, RecursionError):
                continue  # bozuk JSON-LD atlanir
            _walk_json_ld(data, lines)
        return lines

    @staticmethod
    def _looks_like_spa(soup: BeautifulSoup, html: str, body_text: str) -> bool:
        """Istemci tarafi render isaretleri (yalniz 'icerik yetersiz' hatasini secmek icin)."""
        if "/_next/static/" in html:
            return True
        if len(body_text) < MIN_USABLE_TEXT_CHARS and re.search(
                r"y[üu]kleniyor|loading", body_text, re.IGNORECASE):
            return True
        for root_id in ("root", "app", "__next"):
            root = soup.find(id=root_id)
            if root is not None and not root.get_text(strip=True):
                return True
        return False

    def _get(self, url: str, types, budget: Optional[FetchBudget]) -> Optional[FetchResult]:
        """robots/sitemap indirmesi: hata/desteklenmeyen kodlamada None, butce asimi firlatir."""
        try:
            response = safe_fetch(
                url, allowed_types=types, headers=self.headers,
                max_bytes=MAX_HTML_LENGTH, budget=budget,
            )
        except FetchBudgetExceeded:
            raise
        except Exception as e:
            logger.debug(f"Fetch failed for {url}: {e}")
            return None
        if not _encoding_is_supported(response):
            logger.warning(
                "Unsupported Content-Encoding %r for %s — REDDEDILDI",
                response.content_encoding, url)
            return None
        return response

    def _get_xml(self, url: str, budget: Optional[FetchBudget]) -> Optional[BeautifulSoup]:
        response = self._get(url, XML_TYPES, budget)
        return BeautifulSoup(response.text, "lxml-xml") if response else None

    def _try_sitemap(self, base_url: str, budget: Optional[FetchBudget] = None) -> List[str]:
        """robots.txt `Sitemap:` satirlari (yoksa /sitemap.xml); sitemapindex TEK seviye.

        `base_url` origin'dir (scheme://host). Ayni-site disi URL'ler atilir.
        """
        urls: List[str] = []
        try:
            robots = self._get(f"{base_url}/robots.txt", TEXT_TYPES, budget)
            declared = re.findall(r"^\s*sitemap:\s*(\S+)", robots.text if robots else "",
                                  re.IGNORECASE | re.MULTILINE)
            docs = ([u for u in declared if _is_same_site_url(u, base_url)][:MAX_CHILD_SITEMAPS]
                    or [f"{base_url}/sitemap.xml"])
            children_left = MAX_CHILD_SITEMAPS
            for doc_url in docs:
                soup = self._get_xml(doc_url, budget)
                if soup is None:
                    continue
                if soup.find("sitemapindex"):
                    children = [loc.text.strip() for sm in soup.find_all("sitemap")
                                if (loc := sm.find("loc"))]
                    children = [c for c in children if _is_same_site_url(c, base_url)]
                    children = children[:children_left]
                    children_left -= len(children)
                    # Alt harita XML'i sayfa DEGIL; yalniz icindeki URL'ler alinir
                    for child_url in children:
                        child = self._get_xml(child_url, budget)
                        if child is not None and not child.find("sitemapindex"):
                            urls += self._urlset_locs(child)
                else:
                    urls += self._urlset_locs(soup)
        except FetchBudgetExceeded:
            logger.info("Sitemap taramasi butce nedeniyle durdu (%s)", base_url)
        urls = [u for u in dict.fromkeys(urls) if _is_same_site_url(u, base_url)]
        return urls[:MAX_SITEMAP_URLS]

    @staticmethod
    def _urlset_locs(soup: BeautifulSoup) -> List[str]:
        return [loc.text.strip() for url in soup.find_all("url") if (loc := url.find("loc"))]

    def _extract_internal_links(
        self, html: str, base_url: str, base_host: Optional[str]
    ) -> List[str]:
        """Extract internal links from HTML (www ile cipak alan ayni site)."""
        soup = BeautifulSoup(html, "lxml")
        links = {}

        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"]
            full_url = urljoin(base_url, href)
            parsed = urlparse(full_url)

            # Only same site, only http(s)
            if (parsed.scheme in ("http", "https")
                    and _site_key(parsed.hostname) == _site_key(base_host)):
                # Sondaki `/` KORUNUR: silmek `/x/` kullanan sitelerde her sayfaya
                # bir redirect ekler ve 12 isteklik butceyi yarilar.
                clean_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
                if _page_key(clean_url) != _page_key(base_url):
                    links[clean_url] = None

        return list(links)

    def _prioritize_links(
        self, internal_links: List[str], sitemap_urls: List[str]
    ) -> List[str]:
        """
        Prioritize links by matching priority slugs.
        Returns sorted list of URLs to crawl.
        """
        scored = []

        all_urls = dict.fromkeys(internal_links + sitemap_urls)  # sirayi korur

        for url in all_urls:
            path = urlparse(url).path.lower()
            score = 0

            for slug in PRIORITY_SLUGS:
                if slug in path:
                    score += 10
                    break

            if LOW_PRIORITY_SEGMENTS & set(re.split(r"[/\-_.]+", path)):
                score -= 10

            # Prefer shorter paths (likely category pages, not individual products)
            depth = path.count("/")
            if depth <= 2:
                score += 3
            elif depth >= 4:
                score -= 2

            scored.append((score, url))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [url for _, url in scored]
