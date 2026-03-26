"""
ADS SKORLAMA - The ROI Hunter

Amaç: Talebi yüksek, trendi yükselen, maliyeti makul kelimeleri bul.

Formül:
    ADS_Skor = (√(Hacim+1) × Trend_Kombine) / √(Rekabet + 1)

Açıklama:
    - Hacim yükseldikçe skor artar
    - Trend pozitifse skor artar
    - Rekabet yükseldikçe skor AZALIR (ama √ ile yumuşatılır)
    - +1 sıfıra bölmeyi önler ve taban değer sağlar
"""
import math
from decimal import Decimal
from typing import List, Dict, Any

from app.core.constants import (
    ADS_TREND_3M_WEIGHT,
    ADS_TREND_12M_WEIGHT
)
from app.core.scoring.normalizer import (
    normalize_competition,
    calculate_combined_trend
)


def calculate_ads_score(
    monthly_volume: int,
    trend_3m: float,
    trend_12m: float,
    competition_score: float
) -> float:
    """
    Tek bir kelime için ADS skoru hesaplar.
    
    Formül: (√(Hacim+1) × Trend_Kombine) / √(Rekabet + 1)
    #rekabet +1 yap
    Args:
        monthly_volume: Aylık arama hacmi
        trend_3m: Son 3 aylık trend (% değişim)
        trend_12m: Son 12 aylık trend (% değişim)
        competition_score: Rekabet skoru (0-1 arası normalize)
    
    Returns:
        ADS skoru (float)
    """
    # Hacim 0 ise skor 0
    if monthly_volume <= 0:
        return 0.0
    
    # Trend kombine hesapla
    trend_combined = calculate_combined_trend(
        trend_3m=trend_3m,
        trend_12m=trend_12m,
        weight_3m=ADS_TREND_3M_WEIGHT,
        weight_12m=ADS_TREND_12M_WEIGHT
    )
    
    # Rekabet normalize et (0-1 arası olmalı)
    competition_normalized = normalize_competition(competition_score)
    
    # Formül: (√(Hacim+1) × Trend) / √(Rekabet + 1)
    numerator = math.sqrt(monthly_volume + 1) * trend_combined
    denominator = math.sqrt(competition_normalized + 1)
    
    score = numerator / denominator
    
    return round(score, 4)


def calculate_bulk_ads_scores(keywords: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Birden fazla kelime için ADS skorlarını hesaplar.
    
    Args:
        keywords: Kelime listesi, her biri şu alanları içermeli:
            - id
            - monthly_volume
            - trend_3m
            - trend_12m
            - competition_score
    
    Returns:
        Skor eklenmiş kelime listesi (sıralı)
    """
    results = []
    
    for kw in keywords:
        score = calculate_ads_score(
            monthly_volume=kw['monthly_volume'],
            trend_3m=float(kw['trend_3m']),
            trend_12m=float(kw['trend_12m']),
            competition_score=float(kw['competition_score'])
        )
        results.append({
            'keyword_id': kw['id'],
            'ads_score': score
        })
    
    # Skora göre sırala (büyükten küçüğe)
    results.sort(key=lambda x: x['ads_score'], reverse=True)
    
    # Sıralama numarası ekle
    for rank, item in enumerate(results, 1):
        item['ads_rank'] = rank
    
    return results
