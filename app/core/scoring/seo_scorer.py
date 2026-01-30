"""
SEO SKORLAMA - The Opportunity Engine

Amaç: Rekabeti düşük, trendi yükselen, uzun vadeli otorite kelimelerini bul.

Formül:
    SEO_Skor = log(Hacim) × (Trend3 × 2 + Trend12) × Rekabet_Çarpanı

Açıklama:
    - log(Hacim): Dev hacimlerin niş kelimeleri ezmesini engeller
    - Trend3 daha ağırlıklı: Güncel momentum önemli
    - Rekabet_Çarpanı: Düşük rekabet = yüksek fırsat (1 - rekabet)
"""
import math
from decimal import Decimal
from typing import List, Dict, Any

from app.core.constants import (
    SEO_TREND_3M_WEIGHT,
    SEO_TREND_12M_WEIGHT
)
from app.core.scoring.normalizer import (
    normalize_competition,
    normalize_trend,
    safe_log
)


def calculate_seo_score(
    monthly_volume: int,
    trend_3m: float,
    trend_12m: float,
    competition_score: float
) -> float:
    """
    Tek bir kelime için SEO skoru hesaplar.
    
    Formül: log(Hacim) × (Trend3 × 2 + Trend12) × Rekabet_Çarpanı
    
    Args:
        monthly_volume: Aylık arama hacmi
        trend_3m: Son 3 aylık trend (% değişim)
        trend_12m: Son 12 aylık trend (% değişim)
        competition_score: Rekabet skoru (0-1 arası normalize)
    
    Returns:
        SEO skoru (float)
    """
    # Hacim 0 ise skor 0
    if monthly_volume <= 0:
        return 0.0
    
    # log(Hacim)
    log_volume = safe_log(monthly_volume)
    
    # Trend hesapla
    norm_trend_3m = normalize_trend(trend_3m)
    norm_trend_12m = normalize_trend(trend_12m)
    
    # Trend kombine: (Trend3 × 2 + Trend12)
    trend_factor = (norm_trend_3m * SEO_TREND_3M_WEIGHT) + (norm_trend_12m * SEO_TREND_12M_WEIGHT)
    
    # Rekabet çarpanı: Düşük rekabet = yüksek çarpan
    # Rekabet 0 ise çarpan 1, rekabet 1 ise çarpan 0.1 (minimum)
    competition_normalized = normalize_competition(competition_score)
    competition_multiplier = max(0.1, 1 - competition_normalized)
    
    # Formül
    score = log_volume * trend_factor * competition_multiplier
    
    return round(score, 4)


def calculate_bulk_seo_scores(keywords: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Birden fazla kelime için SEO skorlarını hesaplar.
    
    Args:
        keywords: Kelime listesi
    
    Returns:
        Skor eklenmiş kelime listesi (sıralı)
    """
    results = []
    
    for kw in keywords:
        score = calculate_seo_score(
            monthly_volume=kw['monthly_volume'],
            trend_3m=float(kw['trend_3m']),
            trend_12m=float(kw['trend_12m']),
            competition_score=float(kw['competition_score'])
        )
        results.append({
            'keyword_id': kw['id'],
            'seo_score': score
        })
    
    # Skora göre sırala
    results.sort(key=lambda x: x['seo_score'], reverse=True)
    
    # Sıralama numarası ekle
    for rank, item in enumerate(results, 1):
        item['seo_rank'] = rank
    
    return results
