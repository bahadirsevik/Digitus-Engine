"""Ham HTML'den denetim sinyalleri: engel tespiti, sayfa sinyalleri, sayfa turu.

Saf fonksiyonlar (ag yok). Pilot dersleri (istekler/dijital_varlik_analizi_plan.md §10):
engel sayfasi icerik sayilmaz; sayfa turu yalniz URL kalibindan cikarilmaz.
"""
from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from app.core.site_analyzer.turkish_normalizer import normalize_turkish

BLOCK_STATUSES = frozenset({401, 403, 429, 503})
# Dogrulama/engel sayfasi isaretleri. Yalniz KISA sayfalarda aranir: iletisim
# formunda reCAPTCHA olan normal bir sayfa engel sayilmamali.
_BLOCK_MARKERS = re.compile(
    r"just a moment|attention required|checking your browser|verify you are human|"
    r"are you a human|unusual traffic|access denied|request blocked|captcha|"
    r"robot degil|guvenlik dogrulamasi|unsupported client|enable javascript and cookies",
)
BLOCK_PAGE_MAX_TEXT = 600
# Metni neredeyse bos ve script yukleyen sayfa: icerik JavaScript ile ciziliyor.
# HTML boyutuna BAKILMAZ: bos uygulama koku + tek harici script 100 karakterlik de olabilir.
JS_THIN_MAX_TEXT = 300

_TRACKER_IDS = {
    "ga4": r"\bG-[A-Z0-9]{8,12}\b",
    "gtm": r"\bGTM-[A-Z0-9]{5,9}\b",
    "google_ads": r"\bAW-\d{8,12}\b",
}
_TRACKER_FLAGS = {
    "meta_pixel": r"fbq\(\s*['\"]init['\"]|connect\.facebook\.net/[^\"']*fbevents",
    "tiktok_pixel": r"analytics\.tiktok\.com|ttq\.load",
    "linkedin_insight": r"snap\.licdn\.com",
}
_SOCIAL = {
    "instagram": r"instagram\.com/(?!p/|reel/|explore/)[\w.]+",
    "facebook": r"facebook\.com/(?!sharer|share|dialog|tr\b)[\w.\-]+",
    "youtube": r"youtube\.com/(@|channel/|c/|user/)[\w.\-]+",
    "tiktok": r"tiktok\.com/@[\w.]+",
    "linkedin": r"linkedin\.com/(company|in)/[\w\-%.]+",
    "x": r"(?:twitter|x)\.com/(?!intent|share|home)[\w]+",
}
_CMP = re.compile(r"cookiebot|onetrust|cookieyes|iubenda|complianz|cookie-law-info|cookie-notice|efilli", re.I)
_PLATFORMS = {
    "shopify": r"cdn\.shopify\.com", "woocommerce": r"woocommerce", "ikas": r"ikas(cdn|\.com)",
    "ticimax": r"ticimax", "ideasoft": r"ideasoft|ideacdn", "tsoft": r"tsoft|t-soft",
}
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")

# Sayfa turleri: (URL kaliplari, baglanti metni/icerik anahtar kelimeleri) — hepsi
# normalize_turkish sonrasi (kucuk harf, ASCII) eslesir.
PAGE_TYPES: Dict[str, tuple] = {
    "iletisim": (r"iletisim|contact|bize-ulasin", r"\b(iletisim|bize ulasin|contact|ulasim)\b"),
    "hakkimizda": (r"hakkimizda|hakkinda|hakkimda|about|kurumsal", r"\b(hakkimizda|hakkimda|hakkinda|about|kurumsal|biz kimiz)\b"),
    "hizmet": (r"hizmet|tedavi|service|urunlerimiz|cozum", r"\b(hizmetler|hizmetlerimiz|tedaviler|tedavilerimiz|services|cozumler)\b"),
    "blog": (r"blog|makale|haber|yazilar", r"\b(blog|makaleler|haberler|yazilar)\b"),
    "kvkk": (r"kvkk|gizlilik|privacy|cerez|cookie|aydinlatma", r"\b(kvkk|gizlilik|privacy|cerez politikasi|aydinlatma metni)\b"),
    "randevu": (r"randevu|appointment|booking|rezervasyon", r"\b(randevu|appointment|rezervasyon)\b"),
    "fiyat": (r"fiyat|price|pricing|ucret", r"\b(fiyatlar|fiyat listesi|ucretler|pricing|prices)\b"),
    "sss": (r"\bsss\b|faq|sikca-sorulan", r"\b(sss|sikca sorulan|faq)\b"),
}


def site_key(host: Optional[str]) -> str:
    return (host or "").lower().removeprefix("www.")


def same_site(url: str, base_url: str) -> bool:
    return bool(urlparse(url).hostname) and site_key(urlparse(url).hostname) == site_key(urlparse(base_url).hostname)


def _visible_text(soup: BeautifulSoup) -> str:
    for tag in soup(["script", "style", "noscript", "template"]):
        tag.extract()
    return soup.get_text(" ", strip=True)


def detect_block(html: str) -> Optional[str]:
    """200 donen ama aslinda engel/dogrulama sayfasi olan yaniti tanir."""
    soup = BeautifulSoup(html or "", "lxml")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    text = _visible_text(soup)
    if len(text) > BLOCK_PAGE_MAX_TEXT:
        return None
    if _BLOCK_MARKERS.search(normalize_turkish(f"{title} {text}")):
        return "challenge_page"
    return None


def extract_signals(html: str, url: str) -> Dict:
    """Tek sayfanin ham HTML'inden sinyaller. Script/form TEMIZLENMEDEN okunur."""
    soup = BeautifulSoup(html or "", "lxml")
    nav_urls = set()
    for nav in soup.find_all(["nav", "header"]):
        for a in nav.find_all("a", href=True):
            nav_urls.add(urljoin(url, a["href"]).split("#")[0])

    links: List[Dict] = []
    hrefs: List[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        hrefs.append(href)
        if href.startswith(("tel:", "mailto:", "javascript:", "#", "whatsapp:")):
            continue
        absolute = urljoin(url, href).split("#")[0]
        if not absolute.startswith(("http://", "https://")):
            continue
        links.append({"url": absolute, "text": a.get_text(" ", strip=True)[:120],
                      "nav": absolute in nav_urls, "internal": same_site(absolute, url)})

    forms = []
    for form in soup.find_all("form"):
        types = [(i.get("type") or i.name or "").lower() for i in form.find_all(["input", "textarea", "select"])]
        visible = [t for t in types if t not in ("hidden", "submit", "button")]
        forms.append({"fields": visible[:20],
                      "is_search": "search" in types or form.get("role") == "search",
                      "is_newsletter": visible.count("email") == 1 and len(visible) <= 2})

    external = [l["url"] for l in links if not l["internal"]]
    social = {}
    for name, pattern in _SOCIAL.items():
        found = sorted({u for u in external if re.search(pattern, u, re.I)})
        if found:
            social[name] = found[:3]

    ld_types, dates = set(), set()
    for tag in soup.find_all("script", type="application/ld+json"):
        raw = tag.string or tag.get_text() or ""
        ld_types.update(re.findall(r'"@type"\s*:\s*"([^"]+)"', raw))
        dates.update(_DATE.findall(" ".join(re.findall(r'"datePublished"\s*:\s*"([^"]+)"', raw))))
    for t in soup.find_all("time"):
        dates.update(_DATE.findall(t.get("datetime") or ""))

    meta = lambda name: (soup.find("meta", attrs={"name": re.compile(f"^{name}$", re.I)}) or {}).get("content", "") or ""
    images = soup.find_all("img")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    h1 = [h.get_text(" ", strip=True) for h in soup.find_all("h1")]
    raw = html or ""
    has_script = soup.find("script") is not None
    text = _visible_text(soup)  # soup'u degistirir: en SONDA

    return {
        "url": url,
        "title": title,
        "meta_description": meta("description").strip(),
        "h1": h1[:5],
        "lang": (soup.html.get("lang") if soup.html else None),
        "viewport": bool(meta("viewport")),
        "noindex": "noindex" in meta("robots").lower(),
        "canonical": bool(soup.find("link", rel="canonical")),
        "text_len": len(text),
        "html_len": len(raw),
        "js_thin": len(text) < JS_THIN_MAX_TEXT and has_script,
        "links": links,
        "forms": forms,
        "tel": sorted({h for h in hrefs if h.startswith("tel:")})[:5],
        "whatsapp": sorted({h for h in hrefs if re.search(r"wa\.me/|api\.whatsapp\.com|whatsapp://", h)})[:3],
        "emails": sorted({h[7:].split("?")[0].lower() for h in hrefs if h.startswith("mailto:")}
                         | {e.lower() for e in _EMAIL.findall(text)})[:10],
        "social": social,
        "tracker_ids": {k: sorted(set(re.findall(p, raw)))[:3] for k, p in _TRACKER_IDS.items()},
        "tracker_flags": {k: bool(re.search(p, raw)) for k, p in _TRACKER_FLAGS.items()},
        "cmp": sorted({m.lower() for m in _CMP.findall(raw)}),
        "ld_types": sorted(ld_types),
        "dates": sorted(dates),
        "generator": meta("generator") or None,
        "platforms": sorted(k for k, p in _PLATFORMS.items() if re.search(p, raw, re.I)),
        "cart_link": any(re.search(r"/(sepet|cart|basket)\b", l["url"], re.I) for l in links),
        "img_total": len(images),
        "img_no_alt": sum(1 for i in images if not (i.get("alt") or "").strip()),
    }


def classify_page(url: str, anchor_texts: Iterable[str] = (), in_nav: bool = False,
                  signals: Optional[Dict] = None) -> Dict:
    """Sayfa turu: URL kalibi (1) + baglanti metni (2) + sayfa icerigi (2) puani.

    Tur ancak puan >= 2 ise kabul edilir: yalniz URL kalibi YETMEZ (pilotta
    kumar sayfalari adres kalibi yuzunden "KVKK" sanilmisti). Menu baglantisi
    olmak tek basina tur belirlemez, kanit olarak eklenir.
    """
    path = normalize_turkish(urlparse(url).path.replace("-", " ").replace("/", " "))
    raw_path = urlparse(url).path.lower()
    anchors = normalize_turkish(" | ".join(anchor_texts))
    content = normalize_turkish(" ".join([(signals or {}).get("title", "")] + (signals or {}).get("h1", [])))
    best = {"type": None, "score": 0, "evidence": []}
    for name, (url_pat, text_pat) in PAGE_TYPES.items():
        evidence, score = [], 0
        if re.search(url_pat, raw_path) or re.search(text_pat, path):
            evidence.append("url"); score += 1
        if anchors and re.search(text_pat, anchors):
            evidence.append("baglanti_metni"); score += 2
        if content and re.search(text_pat, content):
            evidence.append("sayfa_icerigi"); score += 2
        if score > best["score"]:
            best = {"type": name, "score": score, "evidence": evidence}
    if best["score"] < 2:
        return {"type": None, "score": best["score"], "evidence": best["evidence"], "candidate": best["type"]}
    if in_nav:
        best["evidence"].append("menu")
    best["candidate"] = best["type"]
    return best
