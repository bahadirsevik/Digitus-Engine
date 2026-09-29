"""
Türetilmiş değişken hesaplama (Skorlama v2).

Kaynak: Digitus_Engine_v2_Skorlama_Algoritmalari.md Bölüm 3.

Tüm değişkenler SKORLANAN LİSTEYE görelidir (H, Ln, MB) — asla listeler
arası cache'lenmez; aynı kelime farklı run'larda farklı değer alabilir.
Girdi trendleri ondalık oran ve [-1, +3] kırpılmış olmalıdır
(ön temizlik: score_engine._normalize_scoring_metrics).
"""
import math
from bisect import bisect_left, bisect_right
from typing import Any, Dict, List

from app.core.constants import LN_EQUAL_VOLUME_VALUE


def clip(value: float, lower: float, upper: float) -> float:
    """Değeri [lower, upper] aralığına kırpar."""
    return max(lower, min(upper, value))


def avg_rank_percentile(values: List[float]) -> List[float]:
    """
    Ortalama-sıra persentili: H(x) = (count(<x) + count(<=x)) / (2n).

    Eşit değerler eşit puan alır (tie-aware). Sonuçlar (0, 1) aralığındadır;
    n=1 için 0.5 döner. Girdi sırası korunur.
    """
    n = len(values)
    if n == 0:
        return []
    sorted_values = sorted(values)
    return [
        (bisect_left(sorted_values, v) + bisect_right(sorted_values, v)) / (2 * n)
        for v in values
    ]


def normalized_log_volume(volumes: List[float]) -> List[float]:
    """
    Ln = (log10(V) - min(log10 V)) / (max(log10 V) - min(log10 V)) — [0, 1].

    Persentilden farkı: hacim uçları arasındaki gerçek mesafeyi korur.
    Guard: tüm hacimler eşitse (n=1 dahil) payda 0 olur -> herkese
    LN_EQUAL_VOLUME_VALUE (sıralama zaten etkilenmez).
    """
    if not volumes:
        return []
    logs = [math.log10(max(float(v), 1.0)) for v in volumes]
    lo, hi = min(logs), max(logs)
    if hi - lo <= 0:
        return [LN_EQUAL_VOLUME_VALUE] * len(volumes)
    return [(value - lo) / (hi - lo) for value in logs]


def combined_trend_trk(trend_3m: float, trend_12m: float) -> float:
    """TrK = clip((2*T3 + T12) / 3, -1, +1) — 3 aylık çift ağırlıklı kombine trend."""
    return clip((2.0 * trend_3m + trend_12m) / 3.0, -1.0, 1.0)


def derive_stage1_variables(keywords: List[Dict[str, Any]]) -> None:
    """
    Aşama 1 türetilmiş değişkenlerini (h, ln, trk, mb) dict'lere yerinde ekler.

    Beklenen alanlar: monthly_volume (int > 0), trend_3m / trend_12m
    (ondalık oran, kırpılmış). MB = avg_rank_percentile(V * max(T3, 0)):
    'bu konuyu kaç YENİ insan aramaya başladı' ölçüsü.
    """
    if not keywords:
        return
    volumes = [float(kw["monthly_volume"]) for kw in keywords]
    h_values = avg_rank_percentile(volumes)
    ln_values = normalized_log_volume(volumes)
    mb_inputs = [
        float(kw["monthly_volume"]) * max(float(kw["trend_3m"]), 0.0)
        for kw in keywords
    ]
    mb_values = avg_rank_percentile(mb_inputs)

    for kw, h, ln, mb in zip(keywords, h_values, ln_values, mb_values):
        kw["h"] = round(h, 6)
        kw["ln"] = round(ln, 6)
        kw["mb"] = round(mb, 6)
        kw["trk"] = round(
            combined_trend_trk(float(kw["trend_3m"]), float(kw["trend_12m"])), 6
        )
