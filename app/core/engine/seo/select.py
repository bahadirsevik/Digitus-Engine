"""SEO kati-2 — secim katmani (URETIM KOPYASI).

plan_algoritma_entegrasyonu.md Faz 4 · SEO kod haritasi 6. adim. Kilitli
`scripts/seo_v31_engine.py` davranisinin birebir kopyasidir.

    kapi      : (Rel >= 0,75 ve BP >= 0,80) veya
                (Rel >= 0,85 ve BP >= 0,70 ve Authority = high)
    kume basina 1 aday (-Final, -hacim, id)
    2.+ Primary: ayri URL grubu (K-URL) + aday sirasinin ilk ceil(n/2)'si (K-ESIK)
    aile siniri: en fazla 2 Primary; N doldurma bu siniri ASAMAZ

URETIM SOZLESMESI: `ContractV31()` VARSAYILANI `family_cap=None`dur ve bu
REDDEDILEN dinamik koldur. Uretim DAIMA `production_contract()` kullanir
(`family_cap=2`). Olcum kollari (ARM_VOLUME, ARM_RANDOM, duyarlilik) bu module
TASINMAZ.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, replace
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.core.engine.seo.engine import (
    Contract as V3Contract,
    cluster_id,
    compute_components,
    depth_counts,
)

# Uretimde TEK kol vardir. Olcum kollari (ARM_VOLUME / ARM_RANDOM) ve
# permutasyon makinesi bu module TASINMAZ; yapisal test bunu korur.
ARM_V31 = "v31"

class MissingSignal(RuntimeError):
    """Degerlendirilen kol icin gerekli sinyal eksik — kosu DURUR."""


@dataclass(frozen=True)
class ContractV31:
    """Plan §3'te donan sozlesme. Varsayilanlar ANA KOL'dur."""

    # D1 — kapi
    rel_min: float = 0.75
    bp_min: float = 0.80
    auth_rel_min: float = 0.85
    auth_bp_min: float = 0.70
    bp_universe_min: float = 0.50

    # D2 — sayim tabanlari (B1, B7). "eligible" = kapiyi gecenler.
    norm_base: str = "eligible"           # duyarlilik: "universe"
    depth_base: str = "eligible"          # duyarlilik: "family_all"

    # D3 — kota
    threshold_rule: str = "rank_half"     # B4; duyarlilik: "rank_quarter" | "none"
    family_cap: Optional[int] = None      # B8; duyarlilik: 2 (kati)

    # B10 — N doldurma
    fill_to_n: bool = True                # duyarlilik: False (kati kol)

    n_set: Tuple[int, ...] = (10, 15, 20, 30)
    n_extra: int = 60
    seed: int = 20260906
    permutations: int = 200
    secondary_marked: int = 10            # §3.4 "en uygun 5-10" ust siniri

    def describe(self) -> Dict[str, Any]:
        return asdict(self)

    def all_n(self) -> Tuple[int, ...]:
        return tuple(self.n_set) + (self.n_extra,)

    def v3_core(self) -> V3Contract:
        """Skor katsayilari V3 ile BIREBIR ayni (plan §3.2)."""
        return V3Contract()


# ── kapi (D1) ────────────────────────────────────────────────────────

def gates(row: Dict[str, Any], c: ContractV31) -> Dict[str, Any]:
    """Iki kollu kapi. `authority` yalnizca istisna dalinda okunur (B2)."""
    rel = float(row["relevance"])
    bp = row.get("bp")
    bp_f = None if bp is None else float(bp)
    auth = row.get("authority")

    in_bp_universe = rel >= c.bp_universe_min
    normal = rel >= c.rel_min and bp_f is not None and bp_f >= c.bp_min
    in_window = (rel >= c.auth_rel_min and bp_f is not None
                 and c.auth_bp_min <= bp_f < c.bp_min)
    exception = in_window and auth == "high"
    return {
        "in_bp_universe": in_bp_universe,
        "not_evaluated_by_design": (not in_bp_universe) and bp is None,
        "gate_normal": normal,
        "authority_window": in_window,      # Authority'nin sonucu degistirebildigi tek yer
        "gate_authority": exception,
        "eligible": normal or exception,
    }


def authority_window_ids(rows: Sequence[Dict[str, Any]],
                         c: ContractV31 = ContractV31()) -> List[int]:
    """B2: Authority yalniz bu satirlar icin hesaplanir; disarida sonucu degistiremez."""
    out = []
    for r in rows:
        bp = r.get("bp")
        if bp is None:
            continue
        if float(r["relevance"]) >= c.auth_rel_min and c.auth_bp_min <= float(bp) < c.bp_min:
            out.append(int(r["keyword_id"]))
    return sorted(out)


# ── dogrulama ────────────────────────────────────────────────────────

REQUIRED = ("keyword_id", "keyword_text", "volume", "competition",
            "family_id", "broad_intent", "subintent_id", "relevance")






def _validate(rows: Sequence[Dict[str, Any]]) -> None:
    problems: List[str] = []
    seen = set()
    for r in rows:
        kid = r.get("keyword_id")
        if kid in seen:
            problems.append(f"{kid}: tekrar eden keyword_id")
        seen.add(kid)
        for key in REQUIRED:
            if r.get(key) is None:
                problems.append(f"{kid}: {key} eksik")
    if problems:
        raise MissingSignal("; ".join(problems[:20]))


# ── kume ve URL grubu ────────────────────────────────────────────────

def cluster_groups(members: Dict[str, List[int]],
                   by_id: Dict[int, Dict[str, Any]],
                   url_groups: Optional[Dict[str, str]]) -> Dict[str, str]:
    """B11: EK-F karari KUME duzeyinde tutulur.

    EK-F adaylari gruplar; ayni gruptaki adaylarin KUMELERI ayni sayfaya duser.
    Grup kimligini kumeye baglamak sarttir: kontrol kollari kume temsilcisini
    farkli sectigi icin keyword'e bagli bir grup haritasi kollarda cozulemezdi
    (plan §4.1 "ayni URL gruplari" sarti).

    Haritada yer almayan kume KENDI grubudur (birlesme yok).
    """
    url_groups = url_groups or {}
    out: Dict[str, str] = {}
    for cl in members:
        fam = by_id[members[cl][0]]["family_id"]
        out[cl] = url_groups.get(cl, f"{fam}|__self__|{cl}")
    return out


# ── kol tanimlari (plan §4.1 matched-rate) ───────────────────────────

def _arm_candidates(arm: str, members: Dict[str, List[int]],
                    final: Dict[int, float], by_id: Dict[int, Dict[str, Any]],
                    c: ContractV31, perm_key: Optional[str] = None,
                    perm_index: int = 0) -> Tuple[Dict[str, int], List[int]]:
    """(kume -> temsilci, global sira). Kollar arasinda YALNIZ burasi degisir."""
    if arm == ARM_V31:
        key = lambda k: (-final[k], -float(by_id[k]["volume"]), k)  # noqa: E731
        primary = {cl: min(ids, key=key) for cl, ids in members.items()}
        return primary, sorted(primary.values(), key=key)
    # ARM_VOLUME ve ARM_RANDOM OLCUM kollaridir ve uretime TASINMAMISTIR.
    raise ValueError(f"kol bilinmiyor / uretimde desteklenmiyor: {arm}")


def threshold_passers(order: Sequence[int], c: ContractV31) -> set:
    """B4 — SIRA yuzdeligi. Sayisal `>= P50` DEGIL: baglar yaridan fazlasini gecirir."""
    n = len(order)
    if c.threshold_rule == "none":
        return set(order)
    if c.threshold_rule == "rank_half":
        k = math.ceil(n / 2)
    elif c.threshold_rule == "rank_quarter":
        k = math.ceil(n / 4)
    else:
        raise ValueError(f"threshold_rule bilinmiyor: {c.threshold_rule}")
    return set(order[:k])


# ── secim (§3.3) ─────────────────────────────────────────────────────

def select(order: Sequence[int], primary_by_cluster: Dict[str, int],
           by_id: Dict[int, Dict[str, Any]], groups: Dict[str, str],
           c: ContractV31) -> Dict[str, Any]:
    """Dinamik aile kotasi. N'den BAGIMSIZ; N kesimi `capacity_view` ile yapilir."""
    cluster_of = {kid: cl for cl, kid in primary_by_cluster.items()}
    passers = threshold_passers(order, c)

    selected: List[int] = []
    merged: Dict[int, int] = {}          # aday -> ayni sayfaya dustugu Primary
    deferred: List[Tuple[int, str]] = []  # (aday, sebep)
    fam_selected: Dict[Any, List[int]] = defaultdict(list)

    for kid in order:
        fam = by_id[kid]["family_id"]
        if not fam_selected[fam]:
            selected.append(kid)
            fam_selected[fam].append(kid)
            continue
        # K-URL: ayni aileden zaten secilmis biriyle ayni sayfa mi?
        grp = groups[cluster_of[kid]]
        same_page = next((p for p in fam_selected[fam]
                          if groups[cluster_of[p]] == grp), None)
        if same_page is not None:
            merged[kid] = same_page
            continue
        if c.family_cap is not None and len(fam_selected[fam]) >= c.family_cap:
            deferred.append((kid, "aile_siniri"))
            continue
        if kid not in passers:
            deferred.append((kid, "esik"))
            continue
        selected.append(kid)
        fam_selected[fam].append(kid)

    return {"selected": selected, "merged": merged, "deferred": deferred,
            "cluster_of": cluster_of,
            "family_of": {kid: by_id[kid]["family_id"] for kid in cluster_of},
            "threshold_passers": sorted(passers)}


def capacity_view(sel: Dict[str, Any], n: int, c: ContractV31) -> Dict[str, Any]:
    """N kesimi + B10 doldurma. Doldurma YALNIZ K-ESIK'te dusenlerden yapilir."""
    top = list(sel["selected"][:n])
    relaxed: List[int] = []
    family_of = sel.get("family_of", {})
    family_counts = Counter(family_of.get(kid) for kid in top)
    if c.fill_to_n and len(top) < n:
        # K-URL'de dusen ASLA alinmaz (ayni sayfa olurdu); sira global koldur.
        pool = [kid for kid, reason in sel["deferred"] if reason == "esik"]
        for kid in pool:
            if len(top) >= n:
                break
            family = family_of.get(kid)
            # Katı aile sınırı B10 doldurmasından da üstündür. Aksi halde
            # `family_cap=2` kolu eşik gevşetilirken aynı aileden 3+ Primary
            # üretebilir ve katı-2 diye raporlanan kol gerçekte katı olmaz.
            if (c.family_cap is not None
                    and family_counts[family] >= c.family_cap):
                continue
            top.append(kid)
            relaxed.append(kid)
            family_counts[family] += 1
    used = set(top)
    ertelenen: List[Dict[str, Any]] = []
    for kid, reason in sel["deferred"]:
        if kid not in used:
            ertelenen.append({"keyword_id": kid, "sebep": reason})
    for kid in sel["selected"][n:]:
        ertelenen.append({"keyword_id": kid, "sebep": "N_doldu"})
    return {"top_ids": top, "esik_gevsetilerek": relaxed,
            "filled": len(top) >= n, "ertelenen": ertelenen}


# ── Secondary havuzu (§3.4) ──────────────────────────────────────────

def secondary_pools(top_ids: Sequence[int], sel: Dict[str, Any],
                    members: Dict[str, List[int]], by_id: Dict[int, Dict[str, Any]],
                    gate_by_id: Dict[int, Dict[str, Any]],
                    c: ContractV31) -> Dict[int, List[Dict[str, Any]]]:
    """Primary -> Secondary listesi.

    Kaynaklar: (1) kendi kumesinin diger uyeleri, (2) K-URL'de birlesen kumelerin
    TUM uyeleri, (3) B9: ayni kumelerdeki KAPI-DISI kelimeler (isaretli).
    Sira: ayni sub-intent once, sonra -hacim, keyword_id.
    """
    top = set(top_ids)
    owned: Dict[int, List[str]] = {p: [sel["cluster_of"][p]] for p in top_ids}
    for cand, target in sel["merged"].items():
        if target in top:
            owned[target].append(sel["cluster_of"][cand])

    # kapi-disi kelimeleri kumelerine dagit (B9)
    out_of_gate: Dict[str, List[int]] = defaultdict(list)
    eligible_ids = {k for k, g in gate_by_id.items() if g["eligible"]}
    for kid, row in by_id.items():
        if kid in eligible_ids:
            continue
        out_of_gate[cluster_id(row)].append(kid)

    pools: Dict[int, List[Dict[str, Any]]] = {}
    for p in top_ids:
        own_cluster = sel["cluster_of"][p]
        items: List[Dict[str, Any]] = []
        for cl in owned[p]:
            for kid in members.get(cl, []):
                if kid == p:
                    continue
                items.append({"keyword_id": kid, "cluster": cl,
                              "gate_disi_secondary": False})
            for kid in out_of_gate.get(cl, []):
                items.append({"keyword_id": kid, "cluster": cl,
                              "gate_disi_secondary": True})
        items.sort(key=lambda it: (0 if it["cluster"] == own_cluster else 1,
                                   -float(by_id[it["keyword_id"]]["volume"]),
                                   it["keyword_id"]))
        for i, it in enumerate(items):
            it["secildi_5_10"] = i < c.secondary_marked
        pools[p] = items
    return pools


# ── tam degerlendirme ────────────────────────────────────────────────

def evaluate(rows: Sequence[Dict[str, Any]], c: ContractV31 = ContractV31(),
             url_groups: Optional[Dict[str, str]] = None,
             arm: str = ARM_V31, perm_key: Optional[str] = None,
             perm_index: int = 0, with_secondary: bool = True) -> Dict[str, Any]:
    """Tek tekrar icin tam degerlendirme. Etiket YOK.

    URETIM KAPISI: `family_cap` ZORUNLUDUR. `ContractV31()` varsayilani
    `family_cap=None`dur ve bu REDDEDILEN dinamik koldur; sozlesme unutularak
    bu fonksiyon cagrilirsa kosu BURADA durur. Uretim `production_contract()`
    kullanir (kati-2 = `family_cap=2`).
    """
    if c.family_cap is None:
        raise ValueError(
            "SEO secimi `family_cap` olmadan kosulamaz: ContractV31() "
            "varsayilani REDDEDILEN dinamik koldur. Uretim icin "
            "`production_contract()` (kati-2, family_cap=2) kullanin.")
    _validate(rows)
    by_id = {int(r["keyword_id"]): r for r in rows}
    gate_by_id = {kid: gates(r, c) for kid, r in by_id.items()}
    eligible = sorted(k for k, g in gate_by_id.items() if g["eligible"])
    if not eligible:
        raise MissingSignal("kapiyi gecen satir yok")

    elig_rows = [by_id[k] for k in eligible]
    if c.depth_base == "eligible":
        depth = depth_counts(elig_rows)
    elif c.depth_base == "family_all":
        depth = depth_counts(list(rows))
    else:
        raise ValueError(f"depth_base bilinmiyor: {c.depth_base}")
    depth = {fam: depth.get(fam, 0) for fam in {r["family_id"] for r in rows}}

    if c.norm_base == "eligible":
        base_rows = elig_rows
    elif c.norm_base == "universe":
        base_rows = [by_id[k] for k in sorted(by_id)]
    else:
        raise ValueError(f"norm_base bilinmiyor: {c.norm_base}")

    comps = compute_components(base_rows, depth, c.v3_core())
    core = c.v3_core()
    final: Dict[int, float] = {}
    for kid in eligible:
        bp = float(by_id[kid]["bp"])
        final[kid] = comps[kid]["base_opportunity"] * (core.mult_base
                                                       + core.mult_slope * bp)

    members: Dict[str, List[int]] = defaultdict(list)
    for kid in eligible:
        members[cluster_id(by_id[kid])].append(kid)
    members = {cl: sorted(ids) for cl, ids in members.items()}
    groups = cluster_groups(members, by_id, url_groups)

    primary_by_cluster, order = _arm_candidates(arm, members, final, by_id, c,
                                                perm_key, perm_index)
    sel = select(order, primary_by_cluster, by_id, groups, c)

    capacity: Dict[str, Dict[str, Any]] = {}
    for n in c.all_n():
        view = capacity_view(sel, n, c)
        if with_secondary:
            pools = secondary_pools(view["top_ids"], sel, members, by_id,
                                    gate_by_id, c)
            view["secondary"] = {str(k): v for k, v in pools.items()}
        view["diagnostics"] = family_diagnostics(view["top_ids"], by_id)
        capacity[str(n)] = view

    counts = {
        "rows": len(rows),
        "eligible": len(eligible),
        "gate_normal": sum(1 for g in gate_by_id.values() if g["gate_normal"]),
        "gate_authority": sum(1 for g in gate_by_id.values() if g["gate_authority"]),
        "authority_window": sum(1 for g in gate_by_id.values() if g["authority_window"]),
        "clusters": len(members),
        "families_in_eligible": len({by_id[k]["family_id"] for k in eligible}),
        "candidates": len(order),
        "selected": len(sel["selected"]),
        "merged_secondary": len(sel["merged"]),
        "deferred": len(sel["deferred"]),
    }
    return {"contract": c.describe(), "arm": arm, "gates": gate_by_id,
            "components": comps, "final": final, "members": members,
            "cluster_groups": groups, "primary_by_cluster": primary_by_cluster,
            "order": order, "selection": sel, "capacity": capacity,
            "counts": counts}



def family_diagnostics(top_ids: Sequence[int],
                       by_id: Dict[int, Dict[str, Any]]) -> Dict[str, Any]:
    """§4.2 — benzersiz aile@N ve en buyuk aile payi@N (O3 kabul kriteri)."""
    if not top_ids:
        return {"benzersiz_aile": 0, "en_buyuk_aile": 0, "en_buyuk_aile_payi": 0.0}
    counts: Dict[Any, int] = defaultdict(int)
    for kid in top_ids:
        counts[by_id[kid]["family_id"]] += 1
    biggest = max(counts.values())
    return {"benzersiz_aile": len(counts), "en_buyuk_aile": biggest,
            "en_buyuk_aile_payi": biggest / len(top_ids)}


# ── sentetik duman (0 cagri) ─────────────────────────────────────────



def production_contract(**overrides: Any) -> ContractV31:
    """URETIM sozlesmesi: kati-2 kolu (`family_cap=2`).

    `ContractV31()` varsayilani dinamik koldur ve URETIMDE KULLANILMAZ.
    """
    return replace(ContractV31(), family_cap=2, **overrides)
