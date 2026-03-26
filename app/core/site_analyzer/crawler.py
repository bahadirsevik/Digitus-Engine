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
                "Mozilla/5.0 (compatible; DigitusEngine/1.0; "
                "+https://digitus.io/bot)"
            ),
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.5",
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
                result["error"] = "Homepage fetch failed"
                return result
            result["pages"].append(homepage)

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
                if page:
                    result["pages"].append(page)

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

            title = soup.title.string.strip() if soup.title and soup.title.string else ""

            return {
                "url": str(response.url),
                "title": title,
                "text": text,
                "status": response.status_code,
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
