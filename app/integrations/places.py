"""Google Places (herkese acik isletme profili) adaptoru.

KULLANIM KURALI (plan 3.3) bu dosyada uygulanir:
- `find_place_id` yalniz isletme kimligini (place_id) dondurur; SAKLANABILEN tek alan budur.
- `live_details` ekran icin taze veri ceker; donen icerik veritabanina YAZILMAZ,
  yapay zekaya GONDERILMEZ, rapor dosyasina GIRMEZ (rapora yalniz harita baglantisi girer).
"""
from __future__ import annotations

from typing import Dict, Optional
from urllib.parse import urlparse

import httpx

from app.config import settings

API = "https://places.googleapis.com/v1"
_DETAIL_FIELDS = ("displayName,formattedAddress,nationalPhoneNumber,websiteUri,rating,userRatingCount,"
                  "primaryTypeDisplayName,businessStatus,regularOpeningHours.weekdayDescriptions,googleMapsUri")


def is_configured() -> bool:
    return bool(settings.GOOGLE_CLOUD_API_KEY)


def maps_link(place_id: str) -> str:
    return f"https://www.google.com/maps/place/?q=place_id:{place_id}"


def _host(url: str) -> str:
    return (urlparse(url or "").hostname or "").lower().removeprefix("www.")


def find_place_id(query: str, site: str) -> Optional[str]:
    """Sorguyla bulunan isletmelerden WEB SITESI bu alan adi olanin kimligi.

    Yalniz ada gore eslestirme yapilmaz: ayni adli isletmeler ve subeler karisir.
    """
    response = httpx.post(
        f"{API}/places:searchText", timeout=30,
        json={"textQuery": query, "languageCode": "tr", "regionCode": "TR", "maxResultCount": 5},
        headers={"X-Goog-Api-Key": settings.GOOGLE_CLOUD_API_KEY, "X-Goog-FieldMask": "places.id,places.websiteUri"},
    )
    data = response.json()
    if "error" in data:
        raise RuntimeError(str((data["error"] or {}).get("status") or "error"))
    for place in data.get("places") or []:
        host = _host(place.get("websiteUri"))
        if host and (host == site or host.endswith("." + site)):
            return place.get("id")
    return None


def live_details(place_id: str) -> Dict:
    """Ekranda gosterilmek uzere taze profil bilgisi. SAKLANMAZ."""
    try:
        data = httpx.get(
            f"{API}/places/{place_id}", timeout=30, params={"languageCode": "tr"},
            headers={"X-Goog-Api-Key": settings.GOOGLE_CLOUD_API_KEY, "X-Goog-FieldMask": _DETAIL_FIELDS},
        ).json()
    except Exception as exc:
        return {"available": False, "reason": type(exc).__name__}
    if "error" in data:
        return {"available": False, "reason": str((data["error"] or {}).get("status") or "error")}
    return {
        "available": True,
        "name": (data.get("displayName") or {}).get("text"),
        "address": data.get("formattedAddress"), "phone": data.get("nationalPhoneNumber"),
        "website": data.get("websiteUri"), "rating": data.get("rating"),
        "rating_count": data.get("userRatingCount"),
        "category": (data.get("primaryTypeDisplayName") or {}).get("text"),
        "business_status": data.get("businessStatus"),
        "hours": (data.get("regularOpeningHours") or {}).get("weekdayDescriptions") or [],
        "maps_url": data.get("googleMapsUri") or maps_link(place_id),
    }
