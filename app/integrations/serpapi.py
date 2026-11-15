"""SerpApi (Google arama sonucu) adaptoru. Ucretli/kotali: her cagri bir sorgu harcar."""
from __future__ import annotations

import re
from typing import Dict, List, Optional
from urllib.parse import urlparse

import httpx

from app.config import settings

DEVICE = "desktop"
PAGE_SIZE = 10
_HOST = re.compile(r"[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}")


class SerpError(RuntimeError):
    """Sorgu sonuc vermedi (anahtar yok, kota, ag, servis hatasi)."""


def is_configured() -> bool:
    return bool(settings.SERPAPI_API_KEY)


def _domain(item: Dict) -> str:
    # Google sonuc baglantisini sifreli veriyor (/goto?url=...); alan adi gorunen adres
    # satirindan okunur. Satir bazen adres icermez: o zaman alan adi bos kalir (Faz 0 pilotu).
    shown = (item.get("displayed_link") or "").split(" ")[0]
    host = urlparse(shown if "://" in shown else f"https://{shown}").hostname or ""
    host = host.lower().removeprefix("www.")
    # "6.1 B+ görüntüleme" gibi satirlar alan adi degildir
    return host if _HOST.fullmatch(host) else ""


def search(query: str, location: str, page: int = 0) -> Dict:
    """Tek sonuc sayfasi: {organic: [{position, domain, title, shown}], ads: [domain],
    local_pack, knowledge_panel, location_used}. position sayfalar arasi kesintisizdir."""
    if not is_configured():
        raise SerpError("not_configured")
    try:
        response = httpx.get(settings.SERPAPI_BASE_URL, timeout=60, params={
            "engine": "google", "q": query, "location": location, "google_domain": "google.com.tr",
            "gl": "tr", "hl": "tr", "device": DEVICE, "start": page * PAGE_SIZE,
            "api_key": settings.SERPAPI_API_KEY,
        })
        data = response.json()
    except Exception as exc:
        raise SerpError(type(exc).__name__) from exc
    organic: List[Dict] = [
        {"position": page * PAGE_SIZE + index + 1, "domain": _domain(item),
         "title": (item.get("title") or "")[:200], "shown": (item.get("displayed_link") or "")[:200]}
        for index, item in enumerate(data.get("organic_results") or [])
    ]
    # "Sonuc bulunamadi" da SerpApi'de error alanidir; organik sonuc yoksa hata sayilir.
    if data.get("error") and not organic:
        raise SerpError(str(data["error"])[:120])
    local = data.get("local_results")
    return {
        "organic": organic,
        "ads": list(dict.fromkeys(d for d in (_domain(ad) for ad in data.get("ads") or []) if d)),
        "local_pack": bool(local.get("places") if isinstance(local, dict) else local),
        "knowledge_panel": bool(data.get("knowledge_graph")),
        "location_used": (data.get("search_parameters") or {}).get("location_used"),
    }


def position_of(organic: List[Dict], site: str) -> Optional[int]:
    """Sitenin (ya da alt alan adinin) ilk gorundugu sira."""
    for item in organic:
        if item["domain"] == site or item["domain"].endswith("." + site):
            return item["position"]
    return None
