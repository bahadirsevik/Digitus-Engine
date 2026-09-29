"""
SOCIAL SKORLAMA - The Hype Tracker (v2)

Formül:
    N_SOC = 20*H + 50*MB

Kaynak: Digitus_Engine_v2_Skorlama_Algoritmalari.md Bölüm 6.
    - 50*MB (mutlak büyüme, ana sürücü): 'kaç YENİ insan bu konuyu
      konuşmaya başladı' — V*max(T3,0) çarpımının persentili
    - 20*H (kitle tabanı): İkincil; sosyalde ivme esastır
    - T12 BİLİNÇLİ olarak dışarıda: 12 aylık pencere 'spike cesetlerini'
      yukarı taşıyıp isabeti düşürdü (doküman ölçümü)
    - Rekabet terimi YOK: reklam açık artırması sosyal görünürlükle ilgisiz
"""
from typing import Any, Dict, List

from app.core.constants import SOCIAL_W_H, SOCIAL_W_MB
from app.core.scoring.normalizer import derive_stage1_variables


def calculate_social_score(h: float, mb: float) -> float:
    """
    Tek kelime için N_SOC hesaplar.

    Args:
        h: Hacim persentili [0, 1] (liste-göreli)
        mb: Mutlak büyüme persentili [0, 1] (liste-göreli)

    Returns:
        N_SOC (float, (0, 70) aralığında)
    """
    score = SOCIAL_W_H * h + SOCIAL_W_MB * mb
    return round(score, 4)


def calculate_bulk_social_scores(keywords: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Kelime listesi için SOCIAL skorlarını hesaplar ve sıralar.

    Kelimeler ön temizlikten geçmiş olmalı (trendler ondalık oran).
    Türetilmiş değişkenler (h, mb) yoksa liste üzerinde hesaplanır.
    """
    if keywords and "mb" not in keywords[0]:
        derive_stage1_variables(keywords)

    results = []
    for kw in keywords:
        score = calculate_social_score(h=kw["h"], mb=kw["mb"])
        results.append({
            "keyword_id": kw["id"],
            "social_score": score,
            "_volume": int(kw["monthly_volume"]),
            "_keyword": str(kw.get("keyword", "")),
        })

    # Deterministik üçlü tie-break: skor -> hacim -> alfabetik
    results.sort(key=lambda x: (-x["social_score"], -x["_volume"], x["_keyword"]))

    for rank, item in enumerate(results, 1):
        item["social_rank"] = rank
        del item["_volume"], item["_keyword"]

    return results
