"""SEO kati-2 — skor katmani (URETIM KOPYASI).

plan_algoritma_entegrasyonu.md Faz 4 · algoritma/SEO_V31_KATI2_KOD_HARITASI.md
5. adim. Kilitli `scripts/seo_v3_engine.py` davranisinin birebir kopyasidir;
uretim kodu `scripts/` icinden import ETMEZ, esitlik tracked golden parity ile
kanitlanir.

    Base  = 100·[0,30·VolumeScore + 0,25·CompAdv + 0,25·TrendConf + 0,20·Depth]
    Final = Base · (0,72 + 0,28·BP)
    Norm/Depth tabani = KAPIYI GECEN satirlar
    kume  = family x broad_intent x subintent_id

Olcum kollari (rastgele/hacim kontrolu, duyarlilik) BURAYA TASINMAZ.
"""
from __future__ import annotations

import math
import statistics
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, replace
from typing import Any, Dict, List, Optional, Sequence, Tuple

class MissingSignal(RuntimeError):
    """Degerlendirilen kol icin gerekli sinyal eksik — kosu DURUR (§1.7)."""


@dataclass(frozen=True)
class Contract:
    rel_min: float = 0.60
    bp_min: float = 0.70
    bp_universe_min: float = 0.50          # §1.7 BP evreni alt siniri
    w_volume: float = 0.30
    w_comp: float = 0.25
    w_trend: float = 0.25
    w_depth: float = 0.20
    mult_base: float = 0.72
    mult_slope: float = 0.28
    trend_w3: float = 0.70
    trend_w12: float = 0.30
    tq_w_rel: float = 0.50
    tq_w_abs: float = 0.50
    t_floor: float = -0.95                  # A6
    winsor_lo: float = 5.0                  # A17 (ii)
    winsor_hi: float = 95.0
    n_set: Tuple[int, ...] = (10, 15, 20, 30)
    n_extra: int = 60
    seed: int = 20260906                    # A16
    permutations: int = 200
    volume_tolerance: float = 0.5           # §1.11
    norm_base: str = "universe"             # A5: "universe" | "eligible" (H2)
    bp_multiplier_on: bool = True           # H1: False
    # A8 varyanti (aile varyant kosusu, 08.09): "family_all" = tam aile (ana kol);
    # "gate1" = yalniz Relevance kapisini gecen uyelerin alt-niyetleri sayilir
    # (dokuman §14 sirasi: kapi -> intent -> aile). Kapi gecen uyesi olmayan aile depth 0.
    depth_base: str = "family_all"

    def describe(self) -> Dict[str, Any]:
        return asdict(self)

    def all_n(self) -> Tuple[int, ...]:
        return tuple(self.n_set) + (self.n_extra,)


# §2.2 — kanonik birimde "en ileri giden satir" onceligi (kucuk = daha ileri)
STAGE_RANK: Dict[str, int] = {
    "primary_hit": 1,
    "secondary_coverage": 2,
    "primary_out": 3,            # 3a
    "secondary_out": 4,          # 3b
    "bp_fail": 5,                # 4
    "rel_fail": 6,               # 5
    "volume_zero": 7,            # 6a
    "competition_unresolved": 8,  # 6b
    "not_in_universe": 9,        # 7
}


# ── Norm yardimcilari (A17) ──────────────────────────────────────────


def percentile_linear(values: Sequence[float], p: float) -> float:
    """NumPy `percentile(..., method="linear")` ile birebir."""
    xs = sorted(float(v) for v in values)
    if not xs:
        raise ValueError("percentile: bos dizi")
    if len(xs) == 1:
        return xs[0]
    pos = (p / 100.0) * (len(xs) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def minmax(values: Sequence[float]) -> List[float]:
    """Min-max; max == min ise NOTR 0.5 (A17-i)."""
    if not values:
        return []
    lo, hi = min(values), max(values)
    span = hi - lo
    if span <= 0:
        return [0.5] * len(values)
    return [(float(v) - lo) / span for v in values]


def winsorized_minmax(values: Sequence[float], lo_p: float, hi_p: float
                      ) -> List[float]:
    """[P_lo, P_hi]'ye kirp, sonra o aralikta min-max (A17-iii)."""
    if not values:
        return []
    p_lo = percentile_linear(values, lo_p)
    p_hi = percentile_linear(values, hi_p)
    clipped = [min(max(float(v), p_lo), p_hi) for v in values]
    return minmax(clipped)


def trend_terms(volume: float, trend_percent: Optional[float], floor: float
                ) -> Tuple[float, float, float]:
    """(T, Prev, AbsChange). T = yuzde/100, T < floor ise floor (A6)."""
    t = 0.0 if trend_percent is None else float(trend_percent) / 100.0
    if t < floor:
        t = floor
    prev = float(volume) / (1.0 + t)
    return t, prev, float(volume) - prev


# ── bilesenler ───────────────────────────────────────────────────────

def depth_counts(rows: Sequence[Dict[str, Any]]) -> Dict[Any, int]:
    """A8: aile -> farkli (broad_intent, subintent_id) cifti sayisi (tam aile)."""
    pairs: Dict[Any, set] = defaultdict(set)
    for r in rows:
        pairs[r["family_id"]].add((r["broad_intent"], r["subintent_id"]))
    return {fam: len(s) for fam, s in pairs.items()}


def compute_components(base_rows: Sequence[Dict[str, Any]], depth: Dict[Any, int],
                       c: Contract) -> Dict[int, Dict[str, float]]:
    """Norm tabani = base_rows (A5). Her bilesen bu satirlar uzerinde normalize."""
    if not base_rows:
        return {}
    vol = minmax([math.log1p(float(r["volume"])) for r in base_rows])
    comp = minmax([float(r["competition"]) for r in base_rows])
    dep = minmax([math.log1p(depth[r["family_id"]]) for r in base_rows])
    tq: Dict[str, List[float]] = {}
    for key in ("trend_3m", "trend_12m"):
        rel_t, abs_t = [], []
        for r in base_rows:
            t, _prev, change = trend_terms(r["volume"], r.get(key), c.t_floor)
            rel_t.append(t)
            abs_t.append(change)
        n_rel = winsorized_minmax(rel_t, c.winsor_lo, c.winsor_hi)
        n_abs = winsorized_minmax(abs_t, c.winsor_lo, c.winsor_hi)
        tq[key] = [c.tq_w_rel * a + c.tq_w_abs * b for a, b in zip(n_rel, n_abs)]
    out: Dict[int, Dict[str, float]] = {}
    for i, r in enumerate(base_rows):
        trend_conf = c.trend_w3 * tq["trend_3m"][i] + c.trend_w12 * tq["trend_12m"][i]
        base = 100.0 * (c.w_volume * vol[i] + c.w_comp * (1.0 - comp[i])
                        + c.w_trend * trend_conf + c.w_depth * dep[i])
        out[r["keyword_id"]] = {
            "volume_score": vol[i], "competition_advantage": 1.0 - comp[i],
            "trend_quality_3m": tq["trend_3m"][i],
            "trend_quality_12m": tq["trend_12m"][i],
            "trend_confidence": trend_conf, "depth_count": depth[r["family_id"]],
            "depth_factor": dep[i], "base_opportunity": base,
        }
    return out


# ── kapilar ve dogrulama (§1.7) ──────────────────────────────────────


def gates(row: Dict[str, Any], c: Contract) -> Dict[str, Any]:
    rel = float(row["relevance"])
    bp = row.get("bp")
    in_bp = rel >= c.bp_universe_min
    g1 = rel >= c.rel_min
    g2 = bp is not None and float(bp) >= c.bp_min
    return {"in_bp_universe": in_bp,
            "not_evaluated_by_design": (not in_bp) and bp is None,
            "gate1_relevance": g1, "gate2_bp": g2 if bp is not None else None,
            "eligible": g1 and g2}


# ── kumeleme, secim, kontroller ──────────────────────────────────────

def cluster_id(row: Dict[str, Any]) -> str:
    return f'{row["family_id"]}|{row["broad_intent"]}|{row["subintent_id"]}'


