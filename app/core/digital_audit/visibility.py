"""Faz 3 veri zenginlestirme: arama gorunurlugu (SERP), YouTube, Google isletme profili.

Denetimin istege bagli son adimidir; site denetimi sonuclarini DEGISTIRMEZ ve hicbir
servisin kesilmesi denetimi basarisiz yapmaz (bolum 'unavailable' / 'failed' gorunur).

Kurallar (plan 3.2, 3.3, 3.5, 3.9, 8):
- Her olcum konum, cihaz ve tarihle birlikte saklanir; anlik olcumdur.
- "Gozlenmedi" "yok" degildir: "ilk N sonucta gozlenmedi", "bu sorguda reklam gorulmedi".
- Ucretli sorgu tavani: MAX_SERP_QUERIES. Asilamaz.
- Places: veritabanina yalniz isletme kimligi (place_id) yazilir (integrations/places.py).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional
from urllib.parse import urlparse

from loguru import logger

from app.core.digital_audit.signals import site_key
from app.integrations import places, serpapi, youtube

MAX_KEYWORDS = 10
PAGES_PER_KEYWORD = 2  # sayfa basina ~10 sonuc: "ilk 20"
BRAND_QUERIES = 3      # marka adi + "sikayet" + "yorum"
MAX_SERP_QUERIES = MAX_KEYWORDS * PAGES_PER_KEYWORD + BRAND_QUERIES
MAX_CONSECUTIVE_FAILURES = 3
DEFAULT_LOCATION = "Istanbul, Turkey"
QUERY_FAILED = "Sorgu sonuç vermedi (kota ya da servis hatası)."


class VisibilityInputError(ValueError):
    """Gecersiz gorunurluk istegi."""


class LeaseLost(Exception):
    """Denetim baska bir worker'a gecti ya da kapatildi; calisma durur."""


def clean_config(keywords: List[str], location: Optional[str], brand_name: Optional[str]) -> Dict:
    """Baslangicta dondurulan olcum kapsami."""
    cleaned: List[str] = []
    for raw in keywords or []:
        keyword = " ".join((raw or "").split())[:80]
        if keyword and keyword.casefold() not in {k.casefold() for k in cleaned}:
            cleaned.append(keyword)
    if len(cleaned) > MAX_KEYWORDS:
        raise VisibilityInputError(f"En fazla {MAX_KEYWORDS} anahtar kelime ölçülebilir.")
    return {"keywords": cleaned, "location": (location or "").strip()[:120] or DEFAULT_LOCATION,
            "brand_name": (brand_name or "").strip()[:120] or None,
            "limits": {"max_serp_queries": MAX_SERP_QUERIES, "results_per_keyword": PAGES_PER_KEYWORD * serpapi.PAGE_SIZE}}


def _value(target, check: str) -> Dict:
    for item in target.findings or []:
        if item.get("check") == check and isinstance(item.get("value"), dict):
            return item["value"]
    return {}


# --- arama gorunurlugu ------------------------------------------------------

def _serp(config: Dict, sites: List[str], own: str, tick: Callable[[], None]) -> Dict:
    if not config["keywords"] and not config["brand_name"]:
        return {"status": "unavailable", "reason": "Anahtar kelime ya da marka adı girilmedi."}
    if not serpapi.is_configured():
        return {"status": "unavailable", "reason": "Arama servisi yapılandırılmamış (API anahtarı yok)."}
    state = {"used": 0, "failures": 0, "location_used": None}

    def query(text: str, page: int = 0) -> Dict:
        if state["used"] >= MAX_SERP_QUERIES:
            raise serpapi.SerpError("query_limit")
        if state["failures"] >= MAX_CONSECUTIVE_FAILURES:
            raise serpapi.SerpError("service_down")  # servis kesik: kalan sorgular harcanmaz
        tick()
        state["used"] += 1
        try:
            result = serpapi.search(text, config["location"], page)
        except serpapi.SerpError as exc:
            state["failures"] += 1
            logger.warning(f"Arama sorgusu başarısız: {exc}")
            raise
        state["failures"] = 0
        state["location_used"] = result.get("location_used") or state["location_used"]
        return result

    keywords = []
    for keyword in config["keywords"]:
        row: Dict = {"keyword": keyword, "positions": {}, "ads": [], "local_pack": False, "checked_top": 0, "top": []}
        organic: List[Dict] = []
        try:
            for page in range(PAGES_PER_KEYWORD):
                result = query(keyword, page)
                organic += result["organic"]
                row["checked_top"] = (page + 1) * serpapi.PAGE_SIZE
                if page == 0:
                    row["ads"], row["local_pack"] = result["ads"], result["local_pack"]
                    row["ads_unresolved"] = result.get("ads_unresolved", 0)
                # Butun siteler bulunduysa ikinci sayfa sorgusu harcanmaz
                if all(serpapi.position_of(organic, site) for site in sites) or not result["organic"]:
                    break
        except serpapi.SerpError:
            if not organic:
                row["error"] = QUERY_FAILED
        row["positions"] = {site: serpapi.position_of(organic, site) for site in sites}
        # Adresi okunamayan sonuc bir siteye ait olabilir: "gozlenmedi" kesin yokluk degildir
        row["unresolved"] = sum(1 for item in organic if not item["domain"])
        row["top"] = [item["domain"] for item in organic[:serpapi.PAGE_SIZE] if item["domain"]]
        keywords.append(row)

    brand, reputation = None, []
    if config["brand_name"]:
        name = config["brand_name"]
        try:
            result = query(name)
            brand = {"query": name, "own_position": serpapi.position_of(result["organic"], own),
                     "knowledge_panel": result["knowledge_panel"],
                     "unresolved": sum(1 for item in result["organic"] if not item["domain"]),
                     "ads_unresolved": result.get("ads_unresolved", 0),
                     "other_ads": [d for d in result["ads"] if d != own and not d.endswith("." + own)]}
        except serpapi.SerpError:
            brand = {"query": name, "error": QUERY_FAILED}
        for suffix in ("şikayet", "yorum"):
            entry: Dict = {"query": f"{name} {suffix}", "results": []}
            try:
                result = query(entry["query"])
                entry["results"] = [
                    {k: item[k] for k in ("position", "title", "domain", "shown")}
                    for item in result["organic"] if item["domain"] != own and not item["domain"].endswith("." + own)]
            except serpapi.SerpError:
                entry["error"] = QUERY_FAILED
            reputation.append(entry)

    measured = [row for row in keywords if not row.get("error")]
    ok = bool(measured) or bool(brand and not brand.get("error"))
    return {"status": "ok" if ok else "failed", "reason": None if ok else QUERY_FAILED,
            "keywords": keywords, "brand": brand, "reputation": reputation,
            "queries_used": state["used"], "query_limit": MAX_SERP_QUERIES,
            "location_used": state["location_used"], "device": serpapi.DEVICE}


# --- youtube ----------------------------------------------------------------

def _youtube(targets) -> Dict:
    channels = {}
    for target in targets:
        site = site_key(urlparse(target.url).hostname)
        if target.status != "completed":
            channels[site] = {"status": "unknown", "reason": "Site okunamadığı için kanal bağlantısı aranamadı."}
            continue
        url = (_value(target, "contact.social_links").get("accounts") or {}).get("youtube")
        channels[site] = youtube.channel_summary(url) if url else {
            "status": "not_observed", "reason": "Sitede YouTube bağlantısı görülmedi (kanal yok demek değildir)."}
    return {"status": "ok", "channels": channels}


# --- google isletme profili -------------------------------------------------

def _places(config: Dict, targets) -> Dict:
    if not places.is_configured():
        return {"status": "unavailable", "reason": "Google profil erişimi yapılandırılmamış (API anahtarı yok)."}
    profiles = {}
    for target in targets:
        site = site_key(urlparse(target.url).hostname)
        name = config["brand_name"] if target.kind == "own" else None
        query = name or _value(target, "seo.titles").get("home_title") or site
        try:
            place_id = places.find_place_id(query[:150], site)
        except Exception as exc:
            logger.warning(f"Places araması başarısız ({site}): {type(exc).__name__}")
            profiles[site] = {"status": "failed", "reason": "Google profil araması yapılamadı."}
            continue
        profiles[site] = ({"status": "matched", "place_id": place_id, "maps_url": places.maps_link(place_id)}
                          if place_id else
                          {"status": "not_matched",
                           "reason": "Web sitesi bu alan adı olan bir profil bulunamadı (profil yok demek değildir)."})
    return {"status": "ok", "profiles": profiles}


def run(config: Dict, targets, tick: Callable[[Optional[Dict]], None]) -> Dict:
    """Bolumleri sirayla calistirir; her bolum bitince `tick(sonuc)` ile kaydedilir.

    `tick()` kirayi yeniler; denetim artik bu worker'da degilse LeaseLost firlatir.
    """
    sites = [site_key(urlparse(t.url).hostname) for t in targets]
    own = next((site_key(urlparse(t.url).hostname) for t in targets if t.kind == "own"), sites[0])
    out: Dict = {"measured_at": datetime.now(timezone.utc).isoformat(), "location": config["location"], "sites": sites}
    steps = (("serp", lambda: _serp(config, sites, own, lambda: tick(None))),
             ("youtube", lambda: _youtube(targets)),
             ("places", lambda: _places(config, targets)))
    from celery.exceptions import SoftTimeLimitExceeded

    for name, step in steps:
        try:
            out[name] = step()
        except (LeaseLost, SoftTimeLimitExceeded):
            raise
        except Exception:
            logger.exception(f"Görünürlük adımı başarısız: {name}")
            out[name] = {"status": "failed", "reason": "Bu bölüm alınırken beklenmeyen bir hata oluştu."}
        tick(dict(out))
    return out


def live_profiles(visibility: Optional[Dict]) -> Dict:
    """Eslesen profillerin TAZE bilgisi (ekran icin). Saklanmaz; plan 3.3."""
    profiles = ((visibility or {}).get("places") or {}).get("profiles") or {}
    return {site: places.live_details(p["place_id"]) for site, p in profiles.items() if p.get("place_id")}
