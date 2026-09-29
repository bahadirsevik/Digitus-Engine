"""ADS Niche — rekabet ve trend donusumleri (URETIM KOPYASI).

plan_algoritma_entegrasyonu.md Faz 3 · algoritma/ADS_NIHAI_NICHE_KOD_HARITASI.md
6. adim. Kilitli `scripts/ads_v3_formula.py` davranisinin birebir kopyasidir;
uretim kodu `scripts/` icinden import ETMEZ (kilitler SHA ile baglidir),
esitlik golden parity testiyle kanitlanir.

TUZAK: `scripts/nihai2_motor.py` KOPYALANMAZ — o sapmali ikinci kopya trendi
`[-1,+3]` araligina kirpar ve rekabeti korumasiz alir. Kanonik donusum budur:
trend yuzde/100 ve **[0,1]**, rekabet null -> 0,5 ve > 1 -> HATA.
"""
from __future__ import annotations

from typing import Dict, Optional


def competition_to_r(competition: Optional[float],
                     flags: Optional[Dict[str, int]] = None) -> float:
    """R = C. Repo olcegi ZATEN 0-1; 100'e BOLUNMEZ. Null -> 0,5 (notr)."""
    if flags is None:
        flags = {}
    if competition is None:
        flags["competition_null"] = flags.get("competition_null", 0) + 1
        return 0.5
    value = float(competition)
    if value > 1.0:                      # 0-100 olcegi kacak olarak geldiyse
        raise ValueError(
            f"competition {value} > 1: 0-100 olcegi bekleniyor DEGIL; "
            "olcek sozlesmesi bozuk (plan §6)")
    return value


def trend_to_t(trend_3m_percent: Optional[float],
               flags: Optional[Dict[str, int]] = None) -> float:
    """T = min(max(T3,0),1); T3 = yuzde/100. Null -> 0 (bonus yok)."""
    if flags is None:
        flags = {}
    if trend_3m_percent is None:
        flags["trend_null"] = flags.get("trend_null", 0) + 1
        return 0.0
    return min(max(float(trend_3m_percent) / 100.0, 0.0), 1.0)
