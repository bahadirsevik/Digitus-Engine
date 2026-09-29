"""
Website crawler for brand profile extraction.
Crawls homepage + key pages, extracts clean text content.
"""
import logging
import re
from typing import List, Dict, Optional
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# Crawl limits
MAX_PAGES_PER_SITE = 5
REQUEST_TIMEOUT = 15  # seconds
MAX_CONTENT_LENGTH = 50_000  # chars per page

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


def _encoding_is_supported(response) -> tuple:
    """(ok, encoding) — desteklenmeyen kodlamada icerik KULLANILMAZ."""
    enc = (response.headers.get("content-encoding") or "").strip().lower()
    return (enc in SUPPORTED_CONTENT_ENCODINGS), enc


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


class SiteCrawler:
    """Crawls a website and extracts text content from key pages."""

    def __init__(self, timeout: int = REQUEST_TIMEOUT):
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

    def crawl_site(self, url: str) -> Dict:
        """
        Crawl a site: homepage + up to MAX_PAGES_PER_SITE relevant pages.

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
        url = self._normalize_url(url)
        base_domain = urlparse(url).netloc
        result = {
            "base_url": url,
            "pages": [],
            "sitemap_urls": [],
            "error": None,
        }

        try:
            # Step 1: Crawl homepage
            homepage = self._fetch_page(url)
            if not homepage:
                # SERT hata: ag/kodlama/icerik-tipi. Devam edilemez.
                result["error"] = "Homepage fetch failed"
                return result
            # JS ile render edilen ana sayfalar ("Yukleniyor...") icerik tasimaz;
            # yine de LINK ve SITEMAP kaynagi olarak kullanilir.
            if homepage.get("quality", {}).get("usable", True):
                result["pages"].append(homepage)
            else:
                result["skipped_low_quality"] = result.get("skipped_low_quality", 0) + 1

            # Step 2: Try sitemap.xml
            sitemap_urls = self._try_sitemap(url)
            result["sitemap_urls"] = sitemap_urls[:20]  # Keep first 20 for reference

            # Step 3: Find candidate pages from homepage links
            internal_links = self._extract_internal_links(
                homepage["html"], url, base_domain
            )

            # Step 4: Prioritize pages by slug matching
            candidate_urls = self._prioritize_links(internal_links, sitemap_urls)

            # Step 5: Crawl top candidate pages (up to MAX_PAGES_PER_SITE - 1)
            for page_url in candidate_urls[: MAX_PAGES_PER_SITE - 1]:
                page = self._fetch_page(page_url)
                if not page:
                    continue
                if page.get("quality", {}).get("usable", True):
                    result["pages"].append(page)
                else:
                    result["skipped_low_quality"] = result.get("skipped_low_quality", 0) + 1

            # Hicbir sayfa icerik kalitesini gecemediyse bunu ACIKCA bildir:
            # sessizce bos icerik dondurup AI'in beyandan uydurmasina izin verilmez.
            if not result["pages"]:
                result["error"] = "crawl_content_unusable: no page passed quality gate"

        except Exception as e:
            logger.error(f"Crawl error for {url}: {e}")
            result["error"] = str(e)

        # Remove raw HTML from final output (keep only text)
        for page in result["pages"]:
            page.pop("html", None)

        return result

    def _normalize_url(self, url: str) -> str:
        """Ensure URL has scheme."""
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        return url.rstrip("/")

    def _fetch_page(self, url: str) -> Optional[Dict]:
        """Fetch a single page and extract text."""
        try:
            with httpx.Client(
                timeout=self.timeout,
                follow_redirects=True,
                verify=False,
            ) as client:
                response = client.get(url, headers=self.headers)

            if response.status_code != 200:
                logger.warning(f"Non-200 status for {url}: {response.status_code}")
                return None

            ok_enc, enc = _encoding_is_supported(response)
            if not ok_enc:
                # FAIL-CLOSED: cozulemeyen icerik ASLA downstream'e (AI'a) gitmez.
                logger.warning(
                    "Unsupported Content-Encoding %r for %s — sayfa REDDEDILDI", enc, url)
                return None

            content_type = response.headers.get("content-type", "")
            if "text/html" not in content_type:
                return None

            html = response.text[:MAX_CONTENT_LENGTH]
            soup = BeautifulSoup(html, "lxml")

            # Remove noise elements
            for tag in soup(["nav", "footer", "header", "script", "style",
                            "noscript", "iframe", "svg"]):
                tag.decompose()

            # Try to find main content
            main = soup.find("main") or soup.find("article") or soup.find("body")
            text = main.get_text(separator="\n", strip=True) if main else ""

            # Clean up whitespace
            text = re.sub(r"\n{3,}", "\n\n", text)
            text = text[:MAX_CONTENT_LENGTH]

            # Kalite SONUCU dondurulur, sayfa burada ATILMAZ.
            # Ayrim onemli:
            #   * SERT hata (200 disi, desteklenmeyen kodlama, html olmayan) -> None
            #   * Kalite hatasi -> sayfa DONER ama `quality.usable=False` olur.
            # Boylece JS ile render edilen bir ANA SAYFA icerik olarak kullanilmaz,
            # fakat linkleri/sitemap'i uzerinden ic sayfalara ulasmak MUMKUN kalir.
            quality = assess_text_quality(text)
            if not quality["usable"]:
                logger.info(
                    "Sayfa metni icerik olarak kullanilamaz (%s) %s — chars=%s repl=%s ctrl=%s",
                    quality["reason"], url, quality["chars"],
                    quality["replacement_ratio"], quality["control_ratio"])

            title = soup.title.string.strip() if soup.title and soup.title.string else ""

            return {
                "url": str(response.url),
                "title": title,
                "text": text,
                "status": response.status_code,
                "quality": quality,          # icerik olarak kullanilabilir mi?
                "html": html,  # Temporary, removed before return
            }

        except Exception as e:
            logger.warning(f"Fetch error for {url}: {e}")
            return None

    def _try_sitemap(self, base_url: str) -> List[str]:
        """Try to fetch and parse sitemap.xml."""
        sitemap_url = f"{base_url}/sitemap.xml"
        try:
            with httpx.Client(
                timeout=self.timeout,
                follow_redirects=True,
                verify=False,
            ) as client:
                response = client.get(sitemap_url, headers=self.headers)

            if response.status_code != 200:
                return []

            ok_enc, enc = _encoding_is_supported(response)
            if not ok_enc:
                logger.warning(
                    "Unsupported Content-Encoding %r for sitemap %s — REDDEDILDI",
                    enc, sitemap_url)
                return []

            soup = BeautifulSoup(response.text, "lxml-xml")
            urls = [loc.text for loc in soup.find_all("loc")]
            return urls

        except Exception as e:
            logger.debug(f"Sitemap fetch failed for {base_url}: {e}")
            return []

    def _extract_internal_links(
        self, html: str, base_url: str, base_domain: str
    ) -> List[str]:
        """Extract internal links from HTML."""
        soup = BeautifulSoup(html, "lxml")
        links = set()

        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"]
            full_url = urljoin(base_url, href)
            parsed = urlparse(full_url)

            # Only same domain, only http(s)
            if parsed.netloc == base_domain and parsed.scheme in ("http", "https"):
                clean_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")
                if clean_url != base_url:
                    links.add(clean_url)

        return list(links)

    def _prioritize_links(
        self, internal_links: List[str], sitemap_urls: List[str]
    ) -> List[str]:
        """
        Prioritize links by matching priority slugs.
        Returns sorted list of URLs to crawl.
        """
        scored = []

        all_urls = set(internal_links + sitemap_urls)

        for url in all_urls:
            path = urlparse(url).path.lower()
            score = 0

            for slug in PRIORITY_SLUGS:
                if slug in path:
                    score += 10
                    break

            # Prefer shorter paths (likely category pages, not individual products)
            depth = path.count("/")
            if depth <= 2:
                score += 3
            elif depth >= 4:
                score -= 2

            scored.append((score, url))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [url for _, url in scored]
