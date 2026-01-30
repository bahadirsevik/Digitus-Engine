"""
SOCIAL SKORLAMA - The Hype Tracker

Amaç: Kitle karşılığı olan, güncel konuşulma ivmesi yüksek konuları bul.

Formül:
    SOCIAL_Skor = log(Hacim) × (Trend3 × 3 + Trend12)

Açıklama:
    - log(Hacim): Sosyal erişim tabanı
    - Trend3 ağırlığı 3x: Sosyal medyada anlık trend çok önemli
    - Rekabet faktörü YOK: Sosyal medyada rekabet farklı işler
"""
import math
from decimal import Decimal
from typing import List, Dict, Any

from app.core.constants import (
    SOCIAL_TREND_3M_WEIGHT,
    SOCIAL_TREND_12M_WEIGHT
)
from app.core.scoring.normalizer import (
    normalize_trend,
    safe_log
)


def calculate_social_score(
    monthly_volume: int,
    trend_3m: float,
    trend_12m: float
) -> float:
    """
    Tek bir kelime için SOCIAL skoru hesaplar.
    
    Formül: log(Hacim) × (Trend3 × 3 + Trend12)
    
    NOT: Rekabet parametresi yok, sosyal medyada farklı dinamikler var.
    
    Args:
        monthly_volume: Aylık arama hacmi
        trend_3m: Son 3 aylık trend (% değişim)
        trend_12m: Son 12 aylık trend (% değişim)
    
    Returns:
        SOCIAL skoru (float)
    """
    # Hacim 0 ise skor 0
    if monthly_volume <= 0:
        return 0.0
    
    # log(Hacim)
    log_volume = safe_log(monthly_volume)
    
    # Trend hesapla
    norm_trend_3m = normalize_trend(trend_3m)
    norm_trend_12m = normalize_trend(trend_12m)
    
    # Trend kombine: (Trend3 × 3 + Trend12)
    trend_factor = (norm_trend_3m * SOCIAL_TREND_3M_WEIGHT) + (norm_trend_12m * SOCIAL_TREND_12M_WEIGHT)
    
    # Formül
    score = log_volume * trend_factor
    
    return round(score, 4)


def calculate_bulk_social_scores(keywords: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Birden fazla kelime için SOCIAL skorlarını hesaplar.
    
    Args:
        keywords: Kelime listesi
    
    Returns:
        Skor eklenmiş kelime listesi (sıralı)
    """
    results = []
    
    for kw in keywords:
        score = calculate_social_score(
            monthly_volume=kw['monthly_volume'],
            trend_3m=float(kw['trend_3m']),
            trend_12m=float(kw['trend_12m'])
        )
        results.append({
            'keyword_id': kw['id'],
            'social_score': score
        })
    
    # Skora göre sırala
    results.sort(key=lambda x: x['social_score'], reverse=True)
    
    # Sıralama numarası ekle
    for rank, item in enumerate(results, 1):
        item['social_rank'] = rank
    
    return results
