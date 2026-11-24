"""YouTube Data API adaptoru: herkese acik kanal ve son video sayaclari."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

import httpx

from app.config import settings

API = "https://www.googleapis.com/youtube/v3"
RECENT_VIDEOS = 20
_CHANNEL_URL = re.compile(r"youtube\.com/(@|channel/|user/|c/)([\w.\-]+)", re.IGNORECASE)


def _get(path: str, **params) -> Dict:
    data = httpx.get(f"{API}/{path}", params={**params, "key": settings.GOOGLE_CLOUD_API_KEY}, timeout=30).json()
    if "error" in data:
        raise RuntimeError(str((data["error"] or {}).get("status") or "error"))
    return data


def _lookup(url: str) -> Optional[Dict]:
    match = _CHANNEL_URL.search(url or "")
    if not match:
        return None
    kind, name = match.group(1).lower(), match.group(2)
    if kind == "c/":
        return None  # ozel adres (/c/ad) API ile cozulemez
    return {"@": {"forHandle": "@" + name}, "channel/": {"id": name}, "user/": {"forUsername": name}}[kind]


def channel_summary(url: str, now: Optional[datetime] = None) -> Dict:
    """{'status': ok|unavailable|failed, ...}. Sayaclar YouTube'un gosterdigi kadardir
    (abone sayisi yuvarlanmis gelir, gizlenmis olabilir)."""
    if not settings.GOOGLE_CLOUD_API_KEY:
        return {"status": "unavailable", "reason": "YouTube erişimi yapılandırılmamış (API anahtarı yok)."}
    lookup = _lookup(url)
    if not lookup:
        return {"status": "unavailable", "reason": "Kanal adresi çözülemedi; elle kontrol edin.", "url": url}
    try:
        items = _get("channels", part="snippet,statistics,contentDetails", **lookup).get("items") or []
        if not items:
            return {"status": "unavailable", "reason": "Kanal bulunamadı.", "url": url}
        channel = items[0]
        stats = channel.get("statistics") or {}
        uploads = channel["contentDetails"]["relatedPlaylists"]["uploads"]
        entries = _get("playlistItems", part="contentDetails", playlistId=uploads,
                       maxResults=RECENT_VIDEOS).get("items") or []
        ids = [e["contentDetails"]["videoId"] for e in entries]
        videos = _get("videos", part="snippet,statistics", id=",".join(ids)).get("items") if ids else []
    except Exception as exc:
        return {"status": "failed", "reason": f"YouTube verisi alınamadı ({type(exc).__name__}: {str(exc)[:60]}).", "url": url}
    dates = sorted(v["snippet"]["publishedAt"][:10] for v in videos)
    views = [int((v.get("statistics") or {}).get("viewCount", 0)) for v in videos]
    cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=90)).date().isoformat()
    return {
        "status": "ok", "url": url, "title": channel["snippet"]["title"],
        "subscribers": None if stats.get("hiddenSubscriberCount") else int(stats.get("subscriberCount", 0)),
        "video_count": int(stats.get("videoCount", 0)), "total_views": int(stats.get("viewCount", 0)),
        "last_upload": dates[-1] if dates else None,
        "uploads_90d": sum(1 for d in dates if d >= cutoff),
        # Formul acik: son N videonun izlenme ortalamasi (begeni/yorum karistirilmaz)
        "recent_avg_views": round(sum(views) / len(views)) if views else None,
        "recent_sample": len(videos),
    }
