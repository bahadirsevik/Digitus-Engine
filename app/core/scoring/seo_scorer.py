"""
SEO SKORLAMA - The Opportunity Engine (v2)

Formül (Aşama 1 tabanı):
    N_SEO = 40*Ln + w_trend*max(TrK, taban)

Kaynak: Digitus_Engine_v2_Skorlama_Algoritmalari.md Bölüm 5.
    - 40*Ln (organik trafik potansiyeli): Ana sürücü, min-max normalize log hacim
    - +3*max(TrK, -0.5): Evergreen korumalı kombine trend; düşen kelime
      en fazla -1.5 puan yiyebilir
    - G_T (+15) ve G_A (+4) niyet dereceleri Aşama 2'de (AI) gelir ve
      SEÇİM anında eklenir — bu dosya saf tabanı hesaplar
    - Rekabet terimi bilinçli olarak YOK: eldeki metrik reklamveren
      rekabetidir, organik SERP zorluğu değildir (doküman ölçümü).
      Keyword Difficulty verisi eklendiğinde -w*KD_N rezerve parametredir.
"""
from typing import Any, Dict, List

from app.core.constants import (
    SCORE_W_VOLUME,
    SEO_TREND_FLOOR,
    SEO_W_TREND,
)
from app.core.scoring.normalizer import derive_stage1_variables


def calculate_seo_score(ln: float, trk: float) -> float:
    """
    Tek kelime için N_SEO tabanını hesaplar (G_T/G_A hariç).

    Args:
        ln: Normalize log hacim [0, 1] (liste-göreli)
        trk: Kombine trend [-1, +1]

    Returns:
        N_SEO taban (float, [-1.5, 43] aralığında)
    """
    score = SCORE_W_VOLUME * ln + SEO_W_TREND * max(trk, SEO_TREND_FLOOR)
    return round(score, 4)


def calculate_bulk_seo_scores(keywords: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Kelime listesi için SEO taban skorlarını hesaplar ve sıralar.

    Kelimeler ön temizlikten geçmiş olmalı (trendler ondalık oran).
    Türetilmiş değişkenler (ln, trk) yoksa liste üzerinde hesaplanır.
    """
    if keywords and "ln" not in keywords[0]:
        derive_stage1_variables(keywords)

    results = []
    for kw in keywords:
        score = calculate_seo_score(ln=kw["ln"], trk=kw["trk"])
        results.append({
            "keyword_id": kw["id"],
            "seo_score": score,
            "_volume": int(kw["monthly_volume"]),
            "_keyword": str(kw.get("keyword", "")),
        })

    # Deterministik üçlü tie-break: skor -> hacim -> alfabetik
    results.sort(key=lambda x: (-x["seo_score"], -x["_volume"], x["_keyword"]))

    for rank, item in enumerate(results, 1):
        item["seo_rank"] = rank
        del item["_volume"], item["_keyword"]

    return results
