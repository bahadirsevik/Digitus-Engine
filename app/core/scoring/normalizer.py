"""
Veri normalizasyonu ve dönüşüm fonksiyonları.
"""
import math
from typing import List, Dict, Any
from decimal import Decimal


def normalize_competition(value: float, min_val: float = 0, max_val: float = 100) -> float:
    """
    Rekabet skorunu 0-1 arasına normalize eder.
    Gelen değer 0-100 arası ise 0-1'e çevirir.
    
    Minimum 0.01 döner — competition=0 olan kelimelerde
    sıfıra bölünme veya çarpanın sıfırlanması engellenir.
    
    Args:
        value: Rekabet skoru
        min_val: Minimum değer (varsayılan 0)
        max_val: Maximum değer (varsayılan 100)
    
    Returns:
        0.01-1 arası normalize edilmiş değer
    """
    if value <= 1:
        return max(0.01, value)  # Minimum 0.01 — sıfıra bölünme/çarpma engeli
    return max(0.01, (value - min_val) / (max_val - min_val))


def normalize_trend(value: float) -> float:
    """
    Trend değerini işlenebilir hale getirir.
    Negatif değerler 0.1'e çekilir (sıfır olmasın).
    Pozitif değerler 1 + (değer/100) olarak hesaplanır.
    
    Örnekler:
        -50% trend -> 0.5
        0% trend -> 1.0
        50% trend -> 1.5
        100% trend -> 2.0
    
    Args:
        value: Trend değeri (% olarak, -100 ile +∞ arası)
    
    Returns:
        Normalize edilmiş trend çarpanı
    """
    if value < 0:
        return max(0.1, 1 + (value / 100))
    return 1 + (value / 100)


def safe_log(value: float, base: float = 10) -> float:
    """
    Güvenli logaritma hesaplama.
    0 ve negatif değerler için minimum 1 kullanılır.
    
    Args:
        value: Logaritması alınacak değer
        base: Logaritma tabanı (varsayılan 10)
    
    Returns:
        Logaritma değeri veya 0
    """
    if value <= 0:
        return 0
    return math.log(max(1, value), base)


def calculate_combined_trend(
    trend_3m: float,
    trend_12m: float,
    weight_3m: float,
    weight_12m: float
) -> float:
    """
    Ağırlıklı trend ortalaması hesaplar.
    
    Args:
        trend_3m: 3 aylık trend (% değişim)
        trend_12m: 12 aylık trend (% değişim)
        weight_3m: 3 aylık trend ağırlığı
        weight_12m: 12 aylık trend ağırlığı
    
    Returns:
        Ağırlıklı trend değeri
    """
    norm_3m = normalize_trend(trend_3m)
    norm_12m = normalize_trend(trend_12m)
    total_weight = weight_3m + weight_12m
    return (norm_3m * weight_3m + norm_12m * weight_12m) / total_weight


def calculate_percentile_rank(scores: List[float], score: float) -> float:
    """
    Bir skorun yüzdelik dilimini hesaplar.
    
    Args:
        scores: Tüm skorların listesi
        score: Hesaplanacak skor
    
    Returns:
        0-100 arası yüzdelik dilim
    """
    if not scores:
        return 0
    
    sorted_scores = sorted(scores)
    count_below = sum(1 for s in sorted_scores if s < score)
    return (count_below / len(scores)) * 100
