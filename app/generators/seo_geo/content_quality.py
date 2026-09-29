"""SEO+GEO icerik kalitesi yardimcilari.

Uc sorunu cozer (hepsi deterministik, AI'a guvenmez):

1. Internal link havuzu: AI'nin uydurdugu path'ler yerine crawl'dan gelen
   GERCEK site URL'leri. Prompt'a izinli liste verilir; AI listeden sapsa
   bile backend secimi dogrular ve gecersizse havuzdan gercek URL'ye cevirir.
2. External link format kontrolu: canli HTTP kontrolu YAPILMAZ (yavaslatir,
   rate-limit uretir); scheme/domain/placeholder kontrolu yapilir. Gecersiz
   link tutulmaz — uydurma link, eksik linkten daha zararlidir.
3. Brand context filtreleme: marka profilindeki urun/kullanim alani listeleri
   keyword'un konusuna gore suzulur; sac taragi makalesine dis fircasi
   sizmasin (Run #8'de GEO checker'in kendisi bu sizintiyi raporlamisti).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from app.core.site_analyzer.turkish_normalizer import normalize_turkish

logger = logging.getLogger(__name__)

# AI'larin uydurma external link'lerde kullandigi tipik placeholder domain'ler
_PLACEHOLDER_DOMAINS = (
    "example.com", "example.org", "site.com", "website.com", "domain.com",
    "guvenilir-kaynak.com", "kaynak.com", "ornek.com", "orneksite.com",
)

# Token eslesmesinde kullanilacak minimum govde uzunlugu
_MIN_STEM_LEN = 3


# ─────────────────────────────────────────────────────────────────
# Internal link havuzu
# ─────────────────────────────────────────────────────────────────

def build_internal_link_pool(brand_profile) -> List[Dict[str, str]]:
    """Confirmed profilin crawl edilen sayfalarindan link havuzu kurar.

    Yalnizca status=200 donen gercek sayfalar alinir; company_url her zaman
    fallback olarak havuzda bulunur.
    """
    pool: List[Dict[str, str]] = []
    seen: set = set()

    def _add(url: Optional[str], title: str = "") -> None:
        if not url or not isinstance(url, str):
            return
        url = url.strip().rstrip("/")
        if not url:
            return
        # Semasiz kayitli URL'leri (or. company_url="vepafirca.com.tr") normalize et
        if "://" not in url:
            url = f"https://{url}"
        # Ayni sayfa farkli sema/query/fragment ile iki kez girmesin
        # (dedup anahtari: gercekten host+path)
        try:
            parsed = urlparse(url)
            key = f"{parsed.netloc.lower()}{(parsed.path or '').rstrip('/')}"
        except ValueError:
            key = url.split("://", 1)[-1].lower()
        if key in seen:
            return
        seen.add(key)
        pool.append({"url": url, "title": (title or "").strip()})

    if brand_profile is not None:
        source_pages = brand_profile.source_pages or []
        if isinstance(source_pages, list):
            for page in source_pages:
                if not isinstance(page, dict):
                    continue
                # _page_summaries status'u her zaman yazar (bilinmiyorsa 0);
                # yalnizca dogrulanmis canli sayfalar (200) havuza girer.
                if page.get("status") != 200:
                    continue
                _add(page.get("url"), page.get("title") or "")
        _add(getattr(brand_profile, "company_url", None), "Ana sayfa")

    return pool


def build_link_pool_context(pool: List[Dict[str, str]]) -> str:
    """Prompt'a eklenecek izinli internal link listesi blogu."""
    if not pool:
        return ""
    lines = "\n".join(
        f"- {p['url']}" + (f"  ({p['title'][:60]})" if p.get("title") else "")
        for p in pool[:20]
    )
    return f"""
# İZİNLİ INTERNAL LINK LİSTESİ
internal_link_suggestion alanı için YALNIZCA aşağıdaki gerçek URL'lerden birini
aynen kullan. Listede olmayan bir path/URL üretme:
{lines}"""


def _url_path(url: str) -> str:
    """URL'nin karsilastirilabilir path govdesi ('' → '/')."""
    try:
        parsed = urlparse(url if "//" in url else f"https://x/{url.lstrip('/')}")
        return (parsed.path or "/").rstrip("/") or "/"
    except ValueError:
        return "/"


def validate_internal_link(content: Dict[str, Any], pool: List[Dict[str, str]]) -> Dict[str, Any]:
    """AI'nin sectigi internal linki havuza karsi dogrular.

    - Havuz bossa dokunulmaz (eski davranis korunur).
    - Secim havuzdaki bir URL ile (tam veya path bazinda) eslesiyorsa
      kanonik havuz URL'sine normalize edilir.
    - Eslesmiyorsa (hallusinasyon) havuzdaki ilk gercek URL ile degistirilir;
      anchor metni korunur.
    """
    if not pool:
        return content

    suggestion = content.get("internal_link_suggestion")
    if isinstance(suggestion, str) and suggestion.strip():
        s = suggestion.strip().rstrip("/")
        s_path = _url_path(s)
        for p in pool:
            if s == p["url"] or (s_path != "/" and s_path == _url_path(p["url"])):
                content["internal_link_suggestion"] = p["url"]
                return content
        logger.info(
            f"Internal link havuzda yok, degistirildi: {suggestion!r} -> {pool[0]['url']!r}"
        )
    content["internal_link_suggestion"] = pool[0]["url"]
    if not content.get("internal_link_anchor"):
        content["internal_link_anchor"] = pool[0].get("title") or pool[0]["url"]
    return content


# ─────────────────────────────────────────────────────────────────
# External link format kontrolu (canli HTTP kontrolu bilincli olarak yok)
# ─────────────────────────────────────────────────────────────────

def is_valid_external_url(url: Any) -> bool:
    if not url or not isinstance(url, str):
        return False
    url = url.strip()
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.netloc or "").lower().split(":")[0]
    if not host or "." not in host:
        return False
    if any(host == d or host.endswith("." + d) for d in _PLACEHOLDER_DOMAINS):
        return False
    return True


def validate_external_link(content: Dict[str, Any]) -> Dict[str, Any]:
    """Gecersiz/uydurma external linki temizler.

    Uydurma link yayinlanmaktansa alanin bos kalmasi tercih edilir;
    eksiklik compliance'ta gorunur ve revizyon turunda ele alinabilir.
    """
    url = content.get("external_link_url")
    if url and not is_valid_external_url(url):
        logger.info(f"Gecersiz external link temizlendi: {url!r}")
        content["external_link_url"] = None
        content["external_link_anchor"] = None
    return content


# ─────────────────────────────────────────────────────────────────
# Brand context filtreleme
# ─────────────────────────────────────────────────────────────────

def _stems(text: str) -> set:
    """Turkce normalize edilmis, kaba govdelenmis token seti."""
    tokens = normalize_turkish(text or "").split()
    return {t[:6] if len(t) > 6 else t for t in tokens if len(t) >= _MIN_STEM_LEN}


def _common_prefix_len(x: str, y: str) -> int:
    n = 0
    for cx, cy in zip(x, y):
        if cx != cy:
            break
        n += 1
    return n


def _stem_overlap(a: set, b: set) -> bool:
    """Iki govde kumesi ortusuyor mu?

    Tam/onek eslesmesine ek olarak >=4 karakterlik ortak onek de eslesme
    sayilir — Turkce unsuz yumusamasini yakalar ("tarak" vs "taragi"),
    kisa jenerik govdelerde ("dis" vs "sac") yanlis eslesme uretmez.
    """
    for x in a:
        for y in b:
            if x == y or x.startswith(y) or y.startswith(x):
                return True
            if _common_prefix_len(x, y) >= 4:
                return True
    return False


def filter_brand_items_by_keyword(keyword: str, items: Optional[List[str]]) -> List[str]:
    """Listeden yalnizca keyword'un konusuyla ortusen kalemleri birakir.

    'sac acma taragi' icin 'Dogal sac fircasi' ve 'Tarak' kalir,
    'Dis fircasi' elenir. Hicbiri eslesmezse BOS liste doner —
    cagiran taraf o kategoriyi prompt'a hic koymaz (konu disi sizinti
    riskini sifirlamak, alakasiz baglam vermekten iyidir).
    """
    if not items:
        return []
    kw_stems = _stems(keyword)
    if not kw_stems:
        return list(items)
    return [
        item for item in items
        if isinstance(item, str) and _stem_overlap(kw_stems, _stems(item))
    ]
