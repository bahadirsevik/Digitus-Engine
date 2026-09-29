"""ADS Niche motoru — on temizlik, skor, aile metrikleri, siralama.

plan_algoritma_entegrasyonu.md Faz 3 · algoritma/ADS_NIHAI_NICHE_KOD_HARITASI.md
7. adim. Kilitli `scripts/nihai_akis_e2_e6.py` davranisinin BIREBIR uretim
kopyasidir; `scripts/` icinden import EDILMEZ, esitlik golden parity testiyle
(3 firma x 3 tekrar: ilk-60 id, tam sira SHA'si, Selection 1e-9) kanitlanir.

    onisleme : hacimsiz satir duser
               Rel alt %20 kesilir; SINIR DEGERINI paylasanlarin HEPSI kalir
               Ln  = min-max log10(hacim)   (tek hacimde 0,5)
               Ln' = 0,20 + 0,80·Ln
    Core      = Rel² · Intent² · Ln' · (1 − 0,5·R) · (1 + 0,2·T)
    MFV       = Core / ailedeki en yuksek Core
    FamilyRelQ= KESIMDEN SAG CIKAN uyelerin ortalama Rel'i
    Selection = Core · MFV · FamilyRelQ
    sira      = (−Selection, −hacim, keyword_id)

Aile atamasi TAM EVRENDE, kesimden ONCE yapilir (Faz 2 ciktisi).

URETIME ALINMAYANLAR (plan karari): `breadth` ve `balanced` motorlari,
otomatik motor yonlendirmesi ve refinement turlari. Bu modul YALNIZ Niche
kolunu tasir; bilincli olarak baska motor secenegi sunmaz.
"""
from __future__ import annotations

import math
import statistics
from collections import defaultdict
from typing import Any, Dict, List, Optional, Sequence, Tuple

NOMINAL_CUT = 0.20
LN_FLOOR = 0.20
TREND_COEF = 0.2
REL_EXP = 2
INTENT_EXP = 2
NICHE_COMPETITION_COEF = 0.5


def preprocess(rows: Sequence[Dict[str, Any]], cut: float = NOMINAL_CUT,
               ln_floor: float = LN_FLOOR
               ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """hacim dus -> relevance kesimi (sinir beraberligi KALIR) -> Ln -> Ln'."""
    usable = [r for r in rows if r.get("volume")]
    volume_dropped = len(rows) - len(usable)

    ordered = sorted(usable, key=lambda r: ((r["Rel"] if r["Rel"] is not None
                                             else -1.0), r["keyword_id"]))
    target = int(len(usable) * cut)
    if target <= 0:
        kept, boundary = list(usable), None
    else:
        boundary = ordered[target - 1]["Rel"]
        # Sinir degerini paylasanlarin TAMAMI kalir.
        kept = [r for r in usable
                if r["Rel"] is not None and r["Rel"] >= boundary]

    volumes = [int(r["volume"]) for r in kept]
    logs = [math.log10(v) for v in volumes]
    if not logs:
        # Kilitli kaynak bu durumda min()/max() ile patlardi. Uretimde SESSIZ
        # bos havuz DONDURULMEZ: bos evren bir yapilandirma/sinyal hatasidir
        # ve acik bir hata olarak yuzeye cikar (fail-closed).
        raise ValueError(
            "ADS motoru: kesim sonrasi hic satir kalmadi — evren bos veya "
            "butun Rel degerleri eksik (sinyal asamasi basarisiz)")
    lo, hi = min(logs), max(logs)
    span = hi - lo
    for row, log_v in zip(kept, logs):
        ln = 0.5 if span == 0 else (log_v - lo) / span
        row["Ln"] = ln
        row["Ln_prime"] = ln_floor + (1.0 - ln_floor) * ln

    checks = {
        "hacim_dusen": volume_dropped,
        "nominal_kesim": cut,
        "kesim_sinir_degeri": boundary,
        "hedeflenen_atilan": target,
        "gerceklesen_atilan": len(usable) - len(kept),
        "gerceklesen_kesim_orani": (
            round((len(usable) - len(kept)) / len(usable), 4) if usable else None),
        "kalan_satir": len(kept),
        "Ln_araligi": [round(min(r["Ln"] for r in kept), 6),
                       round(max(r["Ln"] for r in kept), 6)] if kept else None,
        "Ln_prime_araligi": [round(min(r["Ln_prime"] for r in kept), 6),
                             round(max(r["Ln_prime"] for r in kept), 6)] if kept else None,
        "nan_yok": all(not math.isnan(r["Ln"]) for r in kept),
        "Rel_eksik": sum(1 for r in kept if r["Rel"] is None),
        "Intent_eksik": sum(1 for r in kept if r["Intent"] is None),
        "aile_eksik": sum(1 for r in kept if r["family"] is None),
    }
    return kept, checks


def score(kept: Sequence[Dict[str, Any]],
          competition_coef: float = NICHE_COMPETITION_COEF) -> None:
    """Core = Rel² · Intent² · Ln' · (1 − coef·R) · (1 + 0,2·T)."""
    for row in kept:
        rel = row["Rel"] or 0.0
        intent = row["Intent"] or 0.0
        row["Core"] = ((rel ** REL_EXP) * (intent ** INTENT_EXP)
                       * row["Ln_prime"] * (1.0 - competition_coef * row["R"])
                       * (1.0 + TREND_COEF * row["T"]))


def family_terms(kept: Sequence[Dict[str, Any]]) -> None:
    """MFV ve FamilyRelQ — YALNIZ kesimden sag cikan uyeler uzerinde."""
    groups: Dict[Any, List[Dict[str, Any]]] = defaultdict(list)
    for row in kept:
        groups[row["family"]].append(row)
    for members in groups.values():
        ordered = sorted(members, key=lambda r: -r["Core"])
        top = ordered[0]["Core"]
        rel_q = statistics.mean(r["Rel"] or 0.0 for r in members)
        for row in members:
            row["MFV"] = (row["Core"] / top) if top > 0 else 1.0
            row["FamilyRelQ"] = rel_q


def selection_score(kept: Sequence[Dict[str, Any]]) -> None:
    for row in kept:
        row["Selection"] = row["Core"] * row["MFV"] * row["FamilyRelQ"]


def order_pool(kept: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Deterministik sira: (−Selection, −hacim, keyword_id)."""
    return sorted(kept, key=lambda r: (-r["Selection"], -int(r["volume"]),
                                       int(r["keyword_id"])))


def run_niche_engine(rows: Sequence[Dict[str, Any]], *,
                     ln_floor: float = LN_FLOOR,
                     competition_coef: Optional[float] = None,
                     cut: float = NOMINAL_CUT
                     ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Kilitli `run_engine(rows, "niche")` ile ayni: MFV ve FamilyRelQ ACIK."""
    kept, checks = preprocess([dict(r) for r in rows], cut=cut,
                              ln_floor=ln_floor)
    score(kept, NICHE_COMPETITION_COEF if competition_coef is None
          else competition_coef)
    family_terms(kept)
    selection_score(kept)
    pool = order_pool(kept)
    checks["mfv_acik"], checks["familyrelq_acik"] = True, True
    return pool, checks
