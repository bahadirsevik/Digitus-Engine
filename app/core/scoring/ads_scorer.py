"""
ADS SKORLAMA - The ROI Hunter (v2)

Formül:
    N_ADS = 40*H - w_rekabet*R_N + w_trend*min(max(T3, 0), 1)

Kaynak: Digitus_Engine_v2_Skorlama_Algoritmalari.md Bölüm 4.
    - 40*H (talep): Ana sürücü; hacim persentili, doyum eğrisi
    - -15*R_N (maliyet freni): Reklamveren yoğunluğu cezası (orta doz)
    - +5*tazelik: Yalnız pozitif T3 ödüllenir, +%100 üzeri sabitlenir.
      Negatif trend CEZALANDIRILMAZ (düşük H ve Aşama 2 görevini yapar).
"""
from typing import Any, Dict, List

from app.core.constants import (
    ADS_W_COMPETITION,
    ADS_W_TREND,
    SCORE_W_VOLUME,
)
from app.core.scoring.normalizer import derive_stage1_variables


def calculate_ads_score(h: float, competition: float, trend_3m: float) -> float:
    """
    Tek kelime için N_ADS hesaplar.

    Args:
        h: Hacim persentili [0, 1] (liste-göreli)
        competition: R_N [0, 1]
        trend_3m: 3 aylık trend, ondalık oran (kırpılmış)

    Returns:
        N_ADS (float, [-15, 45] aralığında)
    """
    freshness = min(max(trend_3m, 0.0), 1.0)
    score = (
        SCORE_W_VOLUME * h
        - ADS_W_COMPETITION * competition
        + ADS_W_TREND * freshness
    )
    return round(score, 4)


def calculate_bulk_ads_scores(keywords: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Kelime listesi için ADS skorlarını hesaplar ve sıralar.

    Kelimeler ön temizlikten geçmiş olmalı (trendler ondalık oran).
    Türetilmiş değişkenler (h) yoksa liste üzerinde hesaplanır.
    """
    if keywords and "h" not in keywords[0]:
        derive_stage1_variables(keywords)

    results = []
    for kw in keywords:
        score = calculate_ads_score(
            h=kw["h"],
            competition=float(kw["competition_score"]),
            trend_3m=float(kw["trend_3m"]),
        )
        results.append({
            "keyword_id": kw["id"],
            "ads_score": score,
            "_volume": int(kw["monthly_volume"]),
            "_keyword": str(kw.get("keyword", "")),
        })

    # Deterministik üçlü tie-break: skor -> hacim -> alfabetik
    results.sort(key=lambda x: (-x["ads_score"], -x["_volume"], x["_keyword"]))

    for rank, item in enumerate(results, 1):
        item["ads_rank"] = rank
        del item["_volume"], item["_keyword"]

    return results
