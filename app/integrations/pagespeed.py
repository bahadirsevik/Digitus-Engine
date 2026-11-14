"""Google PageSpeed Insights adaptoru (laboratuvar olcumu + varsa saha verisi)."""
from __future__ import annotations

from typing import Dict

import httpx

from app.config import settings

PSI_URL = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
_LAB_METRICS = ("first-contentful-paint", "largest-contentful-paint", "total-blocking-time",
                "cumulative-layout-shift", "speed-index")


def run_pagespeed(url: str, strategy: str = "mobile") -> Dict:
    """{'available': bool, ...}. Anahtar yoksa ya da cagri basarisizsa available=False + reason."""
    key = settings.GOOGLE_CLOUD_API_KEY
    if not key:
        return {"available": False, "reason": "not_configured"}
    try:
        response = httpx.get(
            PSI_URL, timeout=90,
            params=[("url", url), ("strategy", strategy), ("key", key),
                    ("category", "performance"), ("category", "seo"),
                    ("category", "accessibility"), ("category", "best-practices")],
        )
        data = response.json()
    except Exception as exc:
        return {"available": False, "reason": type(exc).__name__}
    if "error" in data or "lighthouseResult" not in data:
        return {"available": False, "reason": str((data.get("error") or {}).get("status") or "no_result")}
    lighthouse = data["lighthouseResult"]
    audits = lighthouse.get("audits") or {}
    field = (data.get("loadingExperience") or {}).get("metrics") or {}
    return {
        "available": True,
        "strategy": strategy,
        "scores": {name: round((cat.get("score") or 0) * 100)
                   for name, cat in (lighthouse.get("categories") or {}).items()},
        "lab": {m: audits[m].get("displayValue") for m in _LAB_METRICS if m in audits},
        # Saha (gercek kullanici) verisi cogu kucuk sitede yoktur; yoklugu sorun degildir.
        "field": {m: v.get("category") for m, v in field.items()},
    }
