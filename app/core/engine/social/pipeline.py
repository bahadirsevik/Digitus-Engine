"""SOCIAL V5 — tek gecisli saf zincir (URETIM KOPYASI).

plan_algoritma_entegrasyonu.md Faz 5 · SOCIAL kod haritasi 2-10. adim.
Kilitli `scripts/noksel_social_run.py`in SAF ALGORITMA CEKIRDEGI'nin birebir
kopyasidir (kilidin golden replay'i tam olarak bunlari kosar); dosya/`Env`
iskelesi, Excel yazimi ve materialize TASINMAZ.

    relevance (x100) -> Rel >= 40 kapisi -> NormBounds (BIR KEZ, dondurulur)
      -> V4 dort boyut -> RF >= 50 ve BC >= 50 kapisi -> V5 niyet
      -> SocialScore -> Priority -> sira (PRIORITY_RANK + order_key) -> havuz

Havuz boyutu URETIMDE kullanicinin kapasitesidir (`social_capacity`); kilitli
tezgahtaki 60 yalniz VARSAYILANDIR.

Olcum mekanizmalari (uc tekrar, medyan/consensus, kontrol capalari, Family V2,
patron etiketleri) BU HATTA YOKTUR — SOCIAL aile kurmaz.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence

from app.core.engine.social import priority as ENG

# Kilitli kosucudaki batch ve havuz sabitleri
BATCH = {"relevance": 10, "v4": 15, "v5": 15}
DEFAULT_SOCIAL_POOL_SIZE = 60

PRIORITY_RANK = {ENG.PRIORITY_PRIMARY: 0, ENG.PRIORITY_TREND_CONTENT: 1,
                 ENG.PRIORITY_SECONDARY: 2}


def relevance_rows(universe: Sequence[Mapping[str, Any]],
                   rel: Mapping[int, Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Relevance 0-1 saklanir, motora x100 girer (kilitli sozlesme)."""
    return [{**r,
             "relevance_100": round(float(rel[r["keyword_id"]]["relevance"]) * 100, 2),
             "relevance_band": rel[r["keyword_id"]].get("band")}
            for r in universe]


def v4_candidates(rows: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """V4'e yalniz Relevance >= 40 gider."""
    return [dict(r) for r in rows
            if r["relevance_100"] >= ENG.RELEVANCE_GATE_MIN]


def v5_candidates(survivors: Sequence[Mapping[str, Any]],
                  dims: Mapping[int, Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """V5'e yalniz UC kapiyi (Rel >= 40, RF >= 50, BC >= 50) gecenler gider."""
    return [dict(r) for r in survivors
            if dims[r["keyword_id"]]["relative_fit"] >= ENG.RF_GATE_MIN
            and dims[r["keyword_id"]]["brand_contentability"] >= ENG.BC_GATE_MIN]


def batch_plan(stage: str, rows: Sequence[Mapping[str, Any]]) -> List[List[int]]:
    """Asama batch plani — kilitli `build_jobs` ile AYNI bolme."""
    size = BATCH[stage]
    ids = [int(r["keyword_id"]) for r in rows]
    return [ids[i:i + size] for i in range(0, len(ids), size)]


def compute_social_list(universe: Sequence[Mapping[str, Any]],
                        rel: Mapping[int, Mapping[str, Any]],
                        dims: Mapping[int, Mapping[str, Any]],
                        intents: Mapping[int, Mapping[str, Any]],
                        *, pool_size: int = DEFAULT_SOCIAL_POOL_SIZE,
                        bounds: Optional[ENG.NormBounds] = None
                        ) -> Dict[str, Any]:
    """Tek gecisli sinyallerden Priority + siralama + havuz (saf; DB/AI yok)."""
    rows = relevance_rows(universe, rel)
    survivors = v4_candidates(rows)
    if not survivors:
        # Hicbir kelime Rel >= 40 kapisini gecemedi. Bu bir VERI hatasi degil,
        # gecerli bir is sonucudur (marka/kelime evreni ortusmuyor): kilitli
        # `compute_norm_bounds` bos listede patlardi; uretim BOS HAVUZ dondurur
        # ve teslim katmani bunu `unfilled_count` olarak raporlar.
        return {"rows": rows, "survivors": [], "bounds": None, "eligible": [],
                "final": [], "kept": [], "pool": [],
                "empty_reason": "no_relevance_survivors"}
    bounds = bounds or ENG.compute_norm_bounds(survivors)
    scored: List[Dict[str, Any]] = []
    for row in rows:
        if row["relevance_100"] < ENG.RELEVANCE_GATE_MIN:
            scored.append({**row, "gate_v5": ENG.GATE_SKIP,
                           "gate_reason_v5": "relevance", "social_score": None})
        else:
            scored.append(ENG.score_row_v5(
                {**row, "family_id": None, **dims[row["keyword_id"]]}, bounds))

    final = ENG.apply_priority(
        scored, {k: v["social_intent_type"] for k, v in intents.items()})
    for row in final:
        info = intents.get(row["keyword_id"], {})
        row["intent_confidence"] = info.get("intent_confidence")
        row["intent_reason"] = info.get("intent_reason")

    kept = [r for r in final if r["social_priority"] != ENG.PRIORITY_EXCLUDE]
    kept.sort(key=lambda r: (PRIORITY_RANK[r["social_priority"]],) + ENG.order_key(r))
    for index, row in enumerate(kept, start=1):
        row["final_rank"] = index

    return {"rows": rows, "survivors": survivors, "bounds": bounds,
            "eligible": v5_candidates(survivors, dims), "final": final,
            "kept": kept, "pool": kept[:pool_size]}
