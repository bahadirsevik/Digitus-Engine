"""
Google Ads monthly_volumes_raw -> trend_3m / trend_12m donusumu.

KRITIK: Google Ads API 'month' alani takvim ayi degil enum dondurur:
  JANUARY=2, FEBRUARY=3, ..., DECEMBER=13
  Dogru takvim ayi = enum_degeri - 1
"""
from __future__ import annotations

from statistics import mean
from typing import List, Dict, Any, Tuple


MIN_PREV_AVG_FOR_TREND = 50   # Bu esik altinda trend 0.0 atanir (bucket gurultuso)
NUMERIC_MAX = 99999.99
NUMERIC_MIN = -9999.99


def _enum_to_calendar_month(enum_month: int) -> int:
    """Google Ads MonthOfYear enum -> takvim ayi (1-12)."""
    return enum_month - 1


def compute_trends(
    monthly_volumes_raw: List[Dict[str, Any]],
    min_prev_avg: int = MIN_PREV_AVG_FOR_TREND,
) -> Tuple[float, float]:
    """
    monthly_volumes_raw: [{"year": int, "month": int(enum), "monthly_searches": int}, ...]
    Returns: (trend_3m, trend_12m) — Numeric(7,2) siniri icinde
    """
    # Edge case guard: gecersiz veya eksik veri
    valid = [
        m for m in monthly_volumes_raw
        if isinstance(m.get("month"), int)
        and m["month"] >= 2                          # UNSPECIFIED=0, UNKNOWN=1 disla
        and m.get("monthly_searches") is not None
    ]

    if len(valid) < 12:
        return 0.0, 0.0

    # Kronolojik sira (eski -> yeni)
    sorted_vols = sorted(
        valid,
        key=lambda x: x["year"] * 100 + _enum_to_calendar_month(x["month"])
    )
    volumes = [m["monthly_searches"] for m in sorted_vols[-12:]]  # Son 12 ay

    last3_avg = mean(volumes[-3:])
    prev3_avg = mean(volumes[-6:-3])
    last6_avg = mean(volumes[-6:])
    prev6_avg = mean(volumes[-12:-6])

    trend_3m = (
        (last3_avg / max(prev3_avg, 1)) * 100 - 100
        if prev3_avg >= min_prev_avg
        else 0.0
    )
    trend_12m = (
        (last6_avg / max(prev6_avg, 1)) * 100 - 100
        if prev6_avg >= min_prev_avg
        else 0.0
    )

    def clamp(v: float) -> float:
        return round(max(NUMERIC_MIN, min(NUMERIC_MAX, v)), 2)

    return clamp(trend_3m), clamp(trend_12m)
