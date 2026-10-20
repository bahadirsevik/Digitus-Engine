"""Claude bake-off G1–G7 kapıları — yürütülebilir kontroller (DB'siz, saf python).

Eşikler MÜHÜRLÜ plandan (`benchmark/claude_bakeoff_plan.json` -> "gates" metni)
regex ile okunur; sayı bulunamazsa `GateParseError` (plan düzenlemesi anlamı
sessizce değiştiremez). Mantıkta sabit eşik YOK; yalnız ayrıştırıcının
beklentileri (n_boot=1000: plan §6.4, seed, MIN_CLUSTERS) sabittir.

Karar üçlüsü (tüm kapılar aynı): sınır açıkça sağlandı -> PASS; güven aralığı
eşiğin açıkça yanlış tarafında -> FAIL; aralık eşiği içeriyor ("gösterilemedi")
-> INSUFFICIENT; ölçüm girdisi yok / Faz dışı -> NOT_MEASURED.
Bootstrap: küme = workspace, 1000 yeniden örnek, %90 (LB=%5, UB=%95), sabit tohum.
"""
from __future__ import annotations

import math
import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from statistics import NormalDist
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
import json

from app.core.benchmark.patron_labels import Labels, match_units, precision, recall
from app.core.benchmark.reference_characterization import (
    CharacterizationError, verify_seal)

PASS, FAIL, INSUFFICIENT, NOT_MEASURED = "PASS", "FAIL", "INSUFFICIENT", "NOT_MEASURED"
N_BOOT = 1000                 # plan §6.4
BOOTSTRAP_SEED = 20261011
MIN_CLUSTERS = 2              # tek kümeli bootstrap dejenere -> CI üretilmez (fail-closed)
LABEL_GOOD, LABEL_BAD, LABEL_EVIDENCE = "İYİ ADAY", "UYGUN DEĞİL", "KANIT YETERSİZ"


class GateParseError(CharacterizationError):
    pass


# ── eşik ayrıştırıcı ────────────────────────────────────────────────────

_NUM = r"([−+\-]?\d+(?:,\d+)?)"


def _num(s: str) -> float:
    return float(s.replace("−", "-").replace(",", "."))


@dataclass(frozen=True)
class GateThresholds:
    confidence: float
    g1_recall_lb_min: float
    g1_offtopic_delta_max: float
    g2_wilson_lb_min: float
    g2_n_min: int
    g2_kappa_min: float
    g2_hardrule_lb_min: float
    g3_halluc_ub_max: float
    g3_patron_lb_min: float
    g4_fatal_max: int
    g4_delivery_lb_min: float
    g4_trunc_ub_max: float
    g5_flip_ub_max: float
    g6a_stage_p95_ratio_max: float
    g6a_task_p99_stale_frac: float
    g6b_task_p95_ratio_max: float
    mde_recall_lb_min: float
    mde_content_wilson_lb_gt: float
    mde_content_point_min: float
    mde_profile_ub_max: float
    stale_thresholds_s: Mapping[str, float]

    @property
    def alpha(self) -> float:
        return 1.0 - self.confidence


def load_plan(path: Path | str) -> Dict[str, Any]:
    plan = json.loads(Path(path).read_text(encoding="utf-8"))
    verify_seal(plan)
    return plan


def thresholds(plan: Mapping[str, Any]) -> GateThresholds:
    g = plan["gates"]

    def find(key: str, pattern: str) -> str:
        text = g.get(key)
        m = re.search(pattern, text) if isinstance(text, str) else None
        if not m:
            raise GateParseError(f"gates.{key}: {pattern!r} bulunamadı")
        return m.group(1)

    levels = {int(x) for key in ("G1_selection", "G2_content", "G3_profile", "G4_reliability",
                                 "G5_noise") for x in re.findall(r"(?:LB|UB|Wilson)(\d{2})", g[key])}
    if len(levels) != 1:
        raise GateParseError(f"güven düzeyi tutarsız/eksik: {sorted(levels)}")
    stale = g.get("time_limits_s")
    if not isinstance(stale, dict) or not stale or not all(
            isinstance(v, (int, float)) and v > 0 for v in stale.values()):
        raise GateParseError("gates.time_limits_s eksik/geçersiz")
    f = lambda key, pat: _num(find(key, pat))  # noqa: E731
    return GateThresholds(
        confidence=levels.pop() / 100.0,
        g1_recall_lb_min=f("G1_selection", r"LB90\(Δ recall_havuz\) ≥ " + _NUM),
        g1_offtopic_delta_max=f("G1_selection", r"konu-dışı Δ ≤ " + _NUM),
        g2_wilson_lb_min=f("G2_content", r"Wilson90 alt sınırı\([^)]*\) ≥ " + _NUM),
        g2_n_min=int(find("G2_content", r"n ≥ (\d+) PATRON")),
        g2_kappa_min=f("G2_content", r"κ ≥ " + _NUM),
        g2_hardrule_lb_min=f("G2_content", r"sert kural teslim LB90\(Δ\) ≥ " + _NUM),
        g3_halluc_ub_max=f("G3_profile", r"UB90\(Δ halüsinasyon_sayısı\) ≤ " + _NUM),
        g3_patron_lb_min=f("G3_profile", r"LB90\(Δ patron puanı\) ≥ " + _NUM),
        g4_fatal_max=int(find("G4_reliability", r"fatal/refusal = (\d+)")),
        g4_delivery_lb_min=f("G4_reliability", r"ham parse ve teslim LB90\(Δ\) ≥ " + _NUM),
        g4_trunc_ub_max=f("G4_reliability", r"kırpılma UB90\(Δ\) ≤ " + _NUM),
        g5_flip_ub_max=f("G5_noise", r"UB90\(Δ flip\) ≤ " + _NUM),
        g6a_stage_p95_ratio_max=f("G6a_service", r"aşama p95 ≤ " + _NUM + "×"),
        g6a_task_p99_stale_frac=int(find("G6a_service", r"stale eşiklerinin %(\d+)")) / 100.0,
        g6b_task_p95_ratio_max=f("G6b_task", r"görev p95 duvar saati ≤ " + _NUM + "×"),
        mde_recall_lb_min=f("G7b_operational_go", r"seçim LB90\(Δ recall\) ≥ " + _NUM),
        mde_content_wilson_lb_gt=f("G7b_operational_go", r"Wilson LB > " + _NUM),
        mde_content_point_min=f("G7b_operational_go", r"nokta ≥ " + _NUM),
        mde_profile_ub_max=f("G7b_operational_go", r"profil UB90\(Δ halüsinasyon\) ≤ " + _NUM),
        stale_thresholds_s={k: float(v) for k, v in stale.items()},
    )


# ── sonuç tipi ──────────────────────────────────────────────────────────

@dataclass
class GateResult:
    gate: str
    status: str
    point: Optional[float] = None
    lb: Optional[float] = None
    ub: Optional[float] = None
    n: Optional[int] = None
    detail: Dict[str, Any] = field(default_factory=dict)


def _worst(states: Sequence[str]) -> str:
    for s in (FAIL, INSUFFICIENT, NOT_MEASURED):
        if s in states:
            return s
    return PASS


def _tri_ge(lb: Optional[float], ub: Optional[float], thr: float) -> str:
    """Δ ≥ thr kapısı: LB ≥ thr geç; UB < thr kal; aksi gösterilemedi."""
    if lb is None or ub is None:
        return INSUFFICIENT
    return PASS if lb >= thr else FAIL if ub < thr else INSUFFICIENT


def _tri_le(lb: Optional[float], ub: Optional[float], thr: float) -> str:
    """Δ ≤ thr kapısı: UB ≤ thr geç; LB > thr kal; aksi gösterilemedi."""
    if lb is None or ub is None:
        return INSUFFICIENT
    return PASS if ub <= thr else FAIL if lb > thr else INSUFFICIENT


# ── istatistik ──────────────────────────────────────────────────────────

def percentile(values: Sequence[float], q: float) -> float:
    """Doğrusal enterpolasyonlu yüzdelik (q ∈ [0,100])."""
    if not values:
        raise ValueError("boş dizi")
    s = sorted(values)
    pos = (len(s) - 1) * q / 100.0
    lo, hi = math.floor(pos), math.ceil(pos)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def p95(values: Sequence[float]) -> float:
    return percentile(values, 95)


def p99(values: Sequence[float]) -> float:
    return percentile(values, 99)


def clustered_bootstrap(cluster_vecs: Sequence[Sequence[float]],
                        stat: Callable[[Tuple[float, ...]], Optional[float]], *,
                        n_boot: int = N_BOOT, alpha: float = 0.10,
                        seed: int = BOOTSTRAP_SEED
                        ) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """Küme-örneklemeli bootstrap: her küme bir yeterli-istatistik vektörü;
    `stat(toplam_vektör)` -> sayı|None. Dönüş (nokta, LB, UB); <MIN_CLUSTERS küme
    veya yarıdan çoğu örnek tanımsızsa (nokta, None, None)."""
    vecs = [tuple(v) for v in cluster_vecs]
    k = len(vecs)
    if k == 0:
        raise ValueError("küme yok")
    width = len(vecs[0])

    def total(idx: Iterable[int]) -> Tuple[float, ...]:
        idx = list(idx)
        return tuple(sum(vecs[i][j] for i in idx) for j in range(width))

    point = stat(total(range(k)))
    if point is None or k < MIN_CLUSTERS:
        return point, None, None
    rng = random.Random(seed)
    samples = []
    for _ in range(n_boot):
        s = stat(total(rng.randrange(k) for _ in range(k)))
        if s is not None:
            samples.append(s)
    if len(samples) < n_boot // 2:
        return point, None, None
    return point, percentile(samples, 100 * alpha / 2), percentile(samples, 100 * (1 - alpha / 2))


def clustered_bootstrap_delta(pairs: Sequence[Tuple[Any, float, float]], *,
                              n_boot: int = N_BOOT, alpha: float = 0.10,
                              seed: int = BOOTSTRAP_SEED
                              ) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """pairs = [(küme_id, x_aday, x_ref)] -> Δ = ort(x_aday) − ort(x_ref); küme (workspace)
    yeniden örneklenir, birimler küme içinde havuzlanır. (nokta, LB, UB)."""
    by: Dict[Any, List[float]] = {}
    for c, xc, xr in pairs:
        v = by.setdefault(c, [0.0, 0.0, 0.0])
        v[0] += xc
        v[1] += xr
        v[2] += 1
    if not by:
        raise ValueError("çift yok")
    stat = lambda t: (t[0] - t[1]) / t[2] if t[2] else None  # noqa: E731
    return clustered_bootstrap(list(by.values()), stat, n_boot=n_boot, alpha=alpha, seed=seed)


def _rate_delta_boot(groups: Mapping[Any, Tuple[float, float, float, float]], *, alpha: float
                     ) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """Eşleşmemiş oran farkı: küme -> (aday_toplam, aday_n, ref_toplam, ref_n)."""
    def stat(t):
        return None if t[1] == 0 or t[3] == 0 else t[0] / t[1] - t[2] / t[3]
    return clustered_bootstrap(list(groups.values()), stat, alpha=alpha)


def wilson_interval(wins: float, ties: float, n: int, alpha: float = 0.10
                    ) -> Tuple[float, float, float]:
    """Wilson skor aralığı, berabere=0,5, ITT (her çift n'de). (p̂, LB, UB); LB %90 iki yanlı."""
    if n <= 0:
        raise ValueError("n <= 0")
    z = NormalDist().inv_cdf(1 - alpha / 2)
    p = (wins + 0.5 * ties) / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return p, center - half, center + half


def cohen_kappa(a: Sequence[Any], b: Sequence[Any]) -> Optional[float]:
    """Cohen κ; tek-kategori (pe=1) veya boş girdi -> None (tanımsız)."""
    if len(a) != len(b):
        raise ValueError("uzunluklar eşit değil")
    n = len(a)
    if n == 0:
        return None
    cats = set(a) | set(b)
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    pe = sum((sum(1 for x in a if x == c) / n) * (sum(1 for y in b if y == c) / n) for c in cats)
    return None if pe >= 1.0 else (po - pe) / (1 - pe)


# ── G1 seçim ────────────────────────────────────────────────────────────

def evaluate_selection(ref_pool: Mapping[Tuple[Any, str], Iterable[int]],
                       cand_pool: Mapping[Tuple[Any, str], Iterable[int]],
                       labels_by_ws: Mapping[Any, Labels],
                       universe_by_ws: Mapping[Any, Mapping[int, str]],
                       offtopic_counts: Optional[Mapping[Any, Mapping[str, Tuple[int, int]]]],
                       th: GateThresholds) -> GateResult:
    """Havuzlanmış patron recall Δ (ws+kanal havuzlu, küme = workspace, birim =
    kanonik pozitif; payda tüm kanonik birimler) + fark kümesinde konu-dışı Δ.

    ref_pool/cand_pool: {(ws, kanal): final-havuz keyword_id'leri} (aynı anahtarlar şart).
    offtopic_counts: {ws: {"cand_only": (konu_dışı, n), "ref_only": (konu_dışı, n)}} — kör etiket;
    None -> konu-dışı yarısı ölçülmedi -> INSUFFICIENT. Karar: recall LB ≥ eşik geç; UB < eşik
    kal (ÇOK ALTINDA = hard); aksi gösterilemedi. Konu-dışı: nokta Δ ≤ eşik geç (plan: LB/UB
    raporlu); nokta > eşik ve LB > eşik kal; nokta > eşik ve LB ≤ eşik gösterilemedi."""
    if set(ref_pool) != set(cand_pool):
        raise ValueError("ref/aday havuz anahtarları simetrik değil")
    pairs: List[Tuple[Any, int, int]] = []
    per: Dict[str, Any] = {}
    tot = {"ref_hits": 0, "cand_hits": 0, "n_pos": 0, "ref_prec": [0, 0], "cand_prec": [0, 0],
           "unmatched_units": 0}
    for key in sorted(ref_pool, key=repr):
        ws, ch = key
        matched = match_units(labels_by_ws[ws], universe_by_ws[ws], ch)
        r, c = set(ref_pool[key]), set(cand_pool[key])
        for ids in matched.values():
            pairs.append((ws, int(bool(ids & c)), int(bool(ids & r))))
        rh, n_pos = recall(r, matched)
        ch_, _ = recall(c, matched)
        rp, cp = precision(r, matched), precision(c, matched)
        per[f"{ws}/{ch}"] = {"n_pos": n_pos, "ref_hits": rh, "cand_hits": ch_,
                             "ref_pool": len(r), "cand_pool": len(c)}
        tot["ref_hits"] += rh
        tot["cand_hits"] += ch_
        tot["n_pos"] += n_pos
        tot["unmatched_units"] += sum(1 for ids in matched.values() if not ids)
        for name, p in (("ref_prec", rp), ("cand_prec", cp)):
            tot[name][0] += p[0]
            tot[name][1] += p[1]
    if not pairs:
        return GateResult("G1", NOT_MEASURED, detail={"summary": "pozitif birim yok"})
    point, lb, ub = clustered_bootstrap_delta(pairs, alpha=th.alpha)
    recall_state = _tri_ge(lb, ub, th.g1_recall_lb_min)
    # offtopic
    off_state, off_detail = INSUFFICIENT, {"measured": False}
    if offtopic_counts is not None:
        groups = {ws: (v["cand_only"][0], v["cand_only"][1], v["ref_only"][0], v["ref_only"][1])
                  for ws, v in offtopic_counts.items()}
        sums = [sum(g[i] for g in groups.values()) for i in range(4)]
        if sums[1] == 0 and sums[3] == 0:
            off_state, off_detail = PASS, {"measured": True, "point": 0.0, "note": "fark kümesi boş"}
        elif sums[1] == 0 or sums[3] == 0:
            off_detail = {"measured": False, "note": "tek taraflı fark kümesi — oran tanımsız"}
        else:
            op, olb, oub = _rate_delta_boot(groups, alpha=th.alpha)
            off_state = (PASS if op <= th.g1_offtopic_delta_max else
                         FAIL if olb is not None and olb > th.g1_offtopic_delta_max else INSUFFICIENT)
            off_detail = {"measured": True, "point": op, "lb": olb, "ub": oub,
                          "cand_only": (sums[0], sums[1]), "ref_only": (sums[2], sums[3])}
    status = _worst([recall_state, off_state])
    hard = recall_state == FAIL
    superior = lb is not None and lb >= th.mde_recall_lb_min
    return GateResult("G1", status, point, lb, ub, tot["n_pos"], {
        "hard": hard, "recall_state": recall_state, "offtopic_state": off_state,
        "offtopic": off_detail, "superiority_recall": superior,
        "ref_recall": tot["ref_hits"] / tot["n_pos"], "cand_recall": tot["cand_hits"] / tot["n_pos"],
        "ref_hits": tot["ref_hits"], "cand_hits": tot["cand_hits"],
        "precision_lower_bound": {"ref": tot["ref_prec"][0] / max(tot["ref_prec"][1], 1),
                                  "cand": tot["cand_prec"][0] / max(tot["cand_prec"][1], 1)},
        "unmatched_units": tot["unmatched_units"], "n_clusters": len({p[0] for p in pairs}),
        "per_ws_channel": per,
        "summary": f"Δrecall={point:+.3f} [{_fmt(lb)}, {_fmt(ub)}]; konu-dışı {off_state}"})


def _fmt(x: Optional[float]) -> str:
    return "—" if x is None else f"{x:+.3f}"


# ── G4 güvenilirlik ─────────────────────────────────────────────────────

_TRUNC = {"max_tokens", "MAX_TOKENS"}


def _is_refusal(e: Mapping[str, Any]) -> bool:
    return (str(e.get("retry_reason") or "").startswith("refusal")
            or str(e.get("finish_reason") or "").lower() == "refusal" or bool(e.get("fatal")))


def _is_trunc(e: Mapping[str, Any]) -> bool:
    return bool(e["truncated"]) if "truncated" in e else e.get("finish_reason") in _TRUNC


def evaluate_reliability(ref_events: Sequence[Mapping[str, Any]],
                         cand_events: Sequence[Mapping[str, Any]],
                         th: GateThresholds) -> GateResult:
    """Olay: {stage, ok_first_try, delivered, truncated|finish_reason, [ws], [retry_reason],
    [fatal], [infra_error]}. ITT: hiçbir olay düşmez. Fatal/refusal aday için = eşik (0);
    ham parse ve teslim Δ LB ≥ eşik; kırpılma Δ UB ≤ eşik (ws-kümeli bootstrap)."""
    if not ref_events or not cand_events:
        return GateResult("G4", NOT_MEASURED, detail={"summary": "olay yok"})
    fatal_c = sum(1 for e in cand_events if _is_refusal(e))
    fatal_r = sum(1 for e in ref_events if _is_refusal(e))
    groups: Dict[Any, List[float]] = {}
    for side, evs in ((0, cand_events), (1, ref_events)):
        for e in evs:
            g = groups.setdefault(e.get("ws", "_all"), [0.0] * 8)
            o = side * 4
            g[o] += bool(e["ok_first_try"])
            g[o + 1] += bool(e["delivered"])
            g[o + 2] += _is_trunc(e)
            g[o + 3] += 1

    def boot(i: int):
        def stat(t):
            return None if t[3] == 0 or t[7] == 0 else t[i] / t[3] - t[4 + i] / t[7]
        return clustered_bootstrap(list(groups.values()), stat, alpha=th.alpha)

    parse, deliv, trunc = boot(0), boot(1), boot(2)
    states = {"parse": _tri_ge(parse[1], parse[2], th.g4_delivery_lb_min),
              "delivery": _tri_ge(deliv[1], deliv[2], th.g4_delivery_lb_min),
              "truncation": _tri_le(trunc[1], trunc[2], th.g4_trunc_ub_max)}
    fatal_state = PASS if fatal_c <= th.g4_fatal_max else FAIL
    status = _worst([fatal_state, *states.values()])
    stages: Dict[str, Any] = {}
    for st in sorted({e["stage"] for e in (*cand_events, *ref_events)}):
        c = [e for e in cand_events if e["stage"] == st]
        r = [e for e in ref_events if e["stage"] == st]
        m = lambda es, k: sum(bool(e[k]) for e in es) / len(es) if es else None  # noqa: E731
        stages[st] = {"n_cand": len(c), "n_ref": len(r),
                      "parse_delta": None if not c or not r else m(c, "ok_first_try") - m(r, "ok_first_try"),
                      "delivery_delta": None if not c or not r else m(c, "delivered") - m(r, "delivered")}
    infra = [e for e in cand_events if e.get("infra_error")]
    infra_ub = wilson_interval(len(infra), 0, len(cand_events), th.alpha)[2]
    return GateResult("G4", status, deliv[0], deliv[1], deliv[2], len(cand_events), {
        "hard": fatal_state == FAIL, "fatal_refusal_cand": fatal_c, "fatal_refusal_ref": fatal_r,
        "fatal_state": fatal_state, "states": states,
        "parse": dict(zip(("point", "lb", "ub"), parse)),
        "delivery": dict(zip(("point", "lb", "ub"), deliv)),
        "truncation": dict(zip(("point", "lb", "ub"), trunc)),
        "per_stage": stages, "infra_errors_cand": len(infra), "infra_rate_ub": infra_ub,
        "n_clusters": len(groups),
        "summary": f"fatal/refusal={fatal_c}; teslimΔ={_fmt(deliv[0])}, parseΔ={_fmt(parse[0])}, "
                   f"kırpılmaΔ={_fmt(trunc[0])}"})


# ── G5 gürültü ──────────────────────────────────────────────────────────

def evaluate_noise(flip_pairs: Sequence[Tuple[Any, float, float]], repeats: int,
                   th: GateThresholds) -> GateResult:
    """flip_pairs=[(ws, flip_aday, flip_ref)] (yalnız tekrarlı hücreler). repeats < 2 -> NOT_MEASURED."""
    if repeats < 2 or not flip_pairs:
        return GateResult("G5", NOT_MEASURED, detail={"repeats": repeats, "summary": "ölçülemedi (tekrar yok)"})
    point, lb, ub = clustered_bootstrap_delta(flip_pairs, alpha=th.alpha)
    return GateResult("G5", _tri_le(lb, ub, th.g5_flip_ub_max), point, lb, ub, len(flip_pairs),
                      {"repeats": repeats, "summary": f"Δflip={_fmt(point)}"})


# ── G6 gecikme ──────────────────────────────────────────────────────────

def evaluate_latency(ref_events: Sequence[Mapping[str, Any]],
                     cand_events: Sequence[Mapping[str, Any]],
                     task_walls_ref: Any, task_walls_cand: Any,
                     stale_thresholds_s: Mapping[str, float], th: GateThresholds) -> GateResult:
    """G6a: aşama başına p95(aday)/p95(ref) ≤ oran VE görev p99 < stale eşiğinin %X'i.
    Olay: {stage, latency_ms, [queue_wait_ms]}. task_walls_*: saniye listesi VEYA
    {eşik_adı: liste}. Liste -> EN KÜÇÜK stale eşiğiyle karşılaştırılır (plan 'stale
    eşiklerinin %50'si' hangi eşik olduğunu belirtmez; en sıkı okuma uygulandı); eşik-adlı
    sözlükte her ad kendi eşiğiyle. G6b yalnız rapor (Faz 3 kapısı): detail['G6b']."""
    if not cand_events or not ref_events:
        return GateResult("G6a", NOT_MEASURED, detail={"summary": "olay yok"})
    stages: Dict[str, Any] = {}
    ratios_ok: List[Optional[bool]] = []
    for st in sorted({e["stage"] for e in (*cand_events, *ref_events)}):
        c = [float(e["latency_ms"]) for e in cand_events if e["stage"] == st]
        r = [float(e["latency_ms"]) for e in ref_events if e["stage"] == st]
        if not c or not r or p95(r) <= 0:
            stages[st] = {"n_cand": len(c), "n_ref": len(r), "ratio": None}
            ratios_ok.append(None)
            continue
        ratio = p95(c) / p95(r)
        stages[st] = {"n_cand": len(c), "n_ref": len(r), "p95_cand_ms": p95(c),
                      "p95_ref_ms": p95(r), "ratio": ratio}
        ratios_ok.append(ratio <= th.g6a_stage_p95_ratio_max)
    stage_state = FAIL if False in ratios_ok else INSUFFICIENT if None in ratios_ok else PASS

    def walls(w: Any) -> Dict[str, List[float]]:
        return {k: list(map(float, v)) for k, v in w.items()} if isinstance(w, Mapping) else {
            "*": list(map(float, w or []))}
    wc, wr = walls(task_walls_cand), walls(task_walls_ref)
    task_checks: Dict[str, Any] = {}
    for name, vals in wc.items():
        if not vals:
            continue
        lim = (min(stale_thresholds_s.values()) if name == "*" else stale_thresholds_s[name])
        v99 = p99(vals)
        task_checks[name] = {"n": len(vals), "p99_s": v99, "limit_s": th.g6a_task_p99_stale_frac * lim,
                             "ok": v99 < th.g6a_task_p99_stale_frac * lim}
    task_state = (INSUFFICIENT if not task_checks else
                  FAIL if any(not c["ok"] for c in task_checks.values()) else PASS)
    flat_c = [x for v in wc.values() for x in v]
    flat_r = [x for v in wr.values() for x in v]
    g6b: Dict[str, Any] = {"phase": "Faz 3 gerçek tier ile kapı; Faz 1'de yalnız rapor",
                           "ratio": None, "within": None}
    if flat_c and flat_r and p95(flat_r) > 0:
        g6b["ratio"] = p95(flat_c) / p95(flat_r)
        g6b["within"] = g6b["ratio"] <= th.g6b_task_p95_ratio_max
    status = _worst([stage_state, task_state])
    worst_ratio = max((s["ratio"] for s in stages.values() if s["ratio"] is not None), default=None)
    return GateResult("G6a", status, worst_ratio, None, None, len(cand_events), {
        "hard": status == FAIL, "stages": stages, "stage_state": stage_state,
        "task": task_checks, "task_state": task_state, "G6b": g6b,
        "queue_wait_p95_ms": {"cand": _opt_p95(cand_events, "queue_wait_ms"),
                              "ref": _opt_p95(ref_events, "queue_wait_ms")},
        "summary": f"en kötü aşama p95 oranı={'—' if worst_ratio is None else f'{worst_ratio:.2f}×'}; "
                   f"görev p99 {task_state}"})


def _opt_p95(events: Sequence[Mapping[str, Any]], key: str) -> Optional[float]:
    vals = [float(e[key]) for e in events if e.get(key) is not None]
    return p95(vals) if vals else None


# ── G2 içerik ───────────────────────────────────────────────────────────

def _wtl(prefs: Sequence[str]) -> Dict[str, int]:
    return {"wins": prefs.count("cand"), "ties": prefs.count("tie"), "losses": prefs.count("ref")}


def evaluate_content(pairs: Sequence[Mapping[str, Any]], th: GateThresholds) -> List[GateResult]:
    """Format başına G2. Çift: {format, ws, patron_pref, judge_pref, hard_rule_cand_ok,
    hard_rule_ref_ok}; *_pref ∈ {"cand","ref","tie",None} (kör çözüldükten SONRA; None = o
    hakem bakmadı). n = yalnız PATRON çiftleri (DeepSeek n'e sayılmaz; κ ve yön için).
    n < eşik -> INSUFFICIENT (Wilson tanımlayıcı). Sert kural Δ tüm çiftlerde (ITT)."""
    out: List[GateResult] = []
    for fmt in sorted({p["format"] for p in pairs}):
        ps = [p for p in pairs if p["format"] == fmt]
        patron = [p["patron_pref"] for p in ps if p.get("patron_pref") is not None]
        judge = [p["judge_pref"] for p in ps if p.get("judge_pref") is not None]
        both = [(p["patron_pref"], p["judge_pref"]) for p in ps
                if p.get("patron_pref") is not None and p.get("judge_pref") is not None]
        kappa = cohen_kappa([a for a, _ in both], [b for _, b in both]) if both else None
        hr = clustered_bootstrap_delta(
            [(p["ws"], int(bool(p["hard_rule_cand_ok"])), int(bool(p["hard_rule_ref_ok"]))) for p in ps],
            alpha=th.alpha)
        w = _wtl(patron)
        n = len(patron)
        if n:
            p_hat, wlb, wub = wilson_interval(w["wins"], w["ties"], n, th.alpha)
        else:
            p_hat = wlb = wub = None
        hr_state = _tri_ge(hr[1], hr[2], th.g2_hardrule_lb_min)
        wilson_state = (INSUFFICIENT if wlb is None else PASS if wlb >= th.g2_wilson_lb_min else
                        FAIL if wub < th.g2_wilson_lb_min else INSUFFICIENT)
        kappa_state = INSUFFICIENT if kappa is None else PASS if kappa >= th.g2_kappa_min else FAIL
        full = _worst([wilson_state, kappa_state, hr_state])
        status = INSUFFICIENT if n < th.g2_n_min else full  # n yetersizken karar verilmez (tanımlayıcı)
        superior = (n >= th.g2_n_min and wlb is not None and wlb > th.mde_content_wilson_lb_gt
                    and p_hat >= th.mde_content_point_min)
        out.append(GateResult(f"G2/{fmt}", status, p_hat, wlb, wub, n, {
            **w, "n_pairs_total": len(ps), "judge": _wtl(judge), "n_judge": len(judge),
            "kappa": kappa, "n_kappa": len(both), "hard_rule_delta": dict(zip(("point", "lb", "ub"), hr)),
            "states": {"wilson": wilson_state, "kappa": kappa_state, "hard_rule": hr_state},
            "n_required": th.g2_n_min, "descriptive_status_if_n_met": full,
            "superiority_content": superior,
            "summary": f"W/T/L={w['wins']}/{w['ties']}/{w['losses']} (patron n={n}); "
                       f"Wilson LB={'—' if wlb is None else f'{wlb:.3f}'}; κ={'—' if kappa is None else f'{kappa:.2f}'}"}))
    return out


# ── G3 profil ───────────────────────────────────────────────────────────

def evaluate_profile(rows: Sequence[Mapping[str, Any]], th: GateThresholds) -> GateResult:
    """Satır: {ws, halluc_cand, halluc_ref, [patron_cand, patron_ref]} (ws başına tek profil).
    UB(Δ halüsinasyon sayısı) ≤ eşik VE LB(Δ patron puanı) ≥ eşik; küme = workspace."""
    if not rows:
        return GateResult("G3", NOT_MEASURED, detail={"summary": "profil yok"})
    h = clustered_bootstrap_delta([(r["ws"], r["halluc_cand"], r["halluc_ref"]) for r in rows], alpha=th.alpha)
    pr = [(r["ws"], r["patron_cand"], r["patron_ref"]) for r in rows
          if r.get("patron_cand") is not None and r.get("patron_ref") is not None]
    p = clustered_bootstrap_delta(pr, alpha=th.alpha) if pr else (None, None, None)
    hs, ps = _tri_le(h[1], h[2], th.g3_halluc_ub_max), _tri_ge(p[1], p[2], th.g3_patron_lb_min)
    return GateResult("G3", _worst([hs, ps]), h[0], h[1], h[2], len(rows), {
        "halluc": dict(zip(("point", "lb", "ub"), h)), "patron": dict(zip(("point", "lb", "ub"), p)),
        "states": {"halluc": hs, "patron": ps}, "n_patron": len(pr),
        "superiority_profile": h[2] is not None and h[2] <= th.mde_profile_ub_max,
        "summary": f"Δhalüsinasyon={_fmt(h[0])}; Δpatron={_fmt(p[0])}"})


# ── G7 maliyet ──────────────────────────────────────────────────────────

def evaluate_cost(ref_cost: Optional[float], cand_cost_delivered: Optional[float],
                  superiority_flags: Mapping[str, bool],
                  n_max_table: Mapping[str, Mapping[str, Any]],
                  k8_p90: Optional[float]) -> GateResult:
    """G7b maliyet şartı: optimize TESLİM maliyeti ≤ Gemini ∨ herhangi bir superiority.
    G7a credit_go ASLA otomatik açılmaz: K8 yoksa 'patron yazılı hükmü' etiketi + N_max tablosu;
    K8 varsa yalnız her konfigürasyonun N_max ≥ K8 olup olmadığı raporlanır (net kredi,
    dönüş planı, G6b elle)."""
    sup = [k for k, v in superiority_flags.items() if v]
    if ref_cost is None or cand_cost_delivered is None:
        return GateResult("G7", NOT_MEASURED, detail={"superiority": sup, "summary": "maliyet ölçülemedi"})
    cost_ok = cand_cost_delivered <= ref_cost
    coverage = {name: (None if row.get("n_max") is None or k8_p90 is None else row["n_max"] >= k8_p90)
                for name, row in n_max_table.items()}
    return GateResult("G7", PASS if (cost_ok or sup) else FAIL,
                      cand_cost_delivered / ref_cost if ref_cost else None, n=None, detail={
        "G7b_cost_clause": cost_ok, "superiority": sup, "n_max_table": dict(n_max_table),
        "credit_go": None,
        "credit_go_label": ("patron yazılı hükmü bekleniyor (K8 yok; N_max tablosu rapora girer)"
                            if k8_p90 is None else "K8_p90 verildi: N_max kapsaması aşağıda; "
                            "net kredi/dönüş planı/G6b elle"),
        "n_max_covers_k8": coverage,
        "summary": f"teslim maliyeti aday/ref={cand_cost_delivered:.2f}/{ref_cost:.2f}; "
                   f"superiority={sup or 'yok'}"})


# ── pilot etiketi + rapor ───────────────────────────────────────────────

def pilot_label(results: Sequence[GateResult]) -> str:
    """Plan §8 Faz 1: herhangi bir FAIL (G4 fatal, G1 çok altında, G6a aşıldı, ... — kapılar
    aralık açıkça yanlış tarafta olduğunda FAIL verir) -> UYGUN DEĞİL; yoksa herhangi bir
    INSUFFICIENT -> KANIT YETERSİZ; ölçülen tüm kapılar PASS ise İYİ ADAY (ölçülemeyenler
    NOT_MEASURED olarak raporda listelenir; G5/G6b/G7 pilot kararını kilitlemez)."""
    rel = [r for r in results if not r.gate.startswith("G7")]
    if any(r.status == FAIL for r in rel):
        return LABEL_BAD
    if any(r.status == INSUFFICIENT for r in rel):
        return LABEL_EVIDENCE
    if any(r.status == PASS for r in rel):
        return LABEL_GOOD
    return LABEL_EVIDENCE


def _tr(x: Optional[float], nd: int = 3) -> str:
    return "—" if x is None else f"{x:.{nd}f}".replace(".", ",")


def render_report(results: Sequence[GateResult], plan: Mapping[str, Any]) -> str:
    """CLAUDE_GEMINI_KARSILASTIRMA_RAPORU.md için Markdown blok."""
    lines = [f"### Kapı sonuçları (plan {plan.get('plan_version')}, mühür "
             f"{str(plan.get('payload_sha256'))[:12]})", "",
             "| Kapı | Durum | Nokta | LB90 | UB90 | n | W/T/L | Özet |",
             "|---|---|---:|---:|---:|---:|---|---|"]
    for r in results:
        d = r.detail
        wtl = f"{d['wins']}/{d['ties']}/{d['losses']}" if "wins" in d else "—"
        status = "ölçülemedi" if r.status == NOT_MEASURED else r.status
        lines.append(f"| {r.gate} | {status} | {_tr(r.point)} | {_tr(r.lb)} | {_tr(r.ub)} | "
                     f"{'—' if r.n is None else r.n} | {wtl} | {str(d.get('summary', '')).replace('|', '/')} |")
    lines += ["", f"**Pilot etiketi:** {pilot_label(results)}", ""]
    nm = [r.gate for r in results if r.status == NOT_MEASURED]
    lines.append("Ölçülemeyen kapılar: " + (", ".join(nm) if nm else "yok") + ".")
    g6 = next((r for r in results if r.gate == "G6a"), None)
    if g6:
        g6b = g6.detail.get("G6b", {})
        lines.append(f"G6b (görev p95 oranı, Faz 3 kapısı — burada yalnız rapor): "
                     f"{_tr(g6b.get('ratio'), 2)}× (içinde={g6b.get('within')}).")
    nmax = plan.get("n_max_3_2") or {}
    k8 = (plan.get("decisions") or {}).get("K8_monthly_volume_p90")
    lines += ["", "### N_max tablosu (G7a; K8 hacim " +
              ("bilinmiyor — `credit_go` yalnız patronun yazılı hükmüyle" if k8 is None
               else f"p90={k8}") + ")", "",
              "| Konfigürasyon | $/koşu | N_max (koşu/ay) |", "|---|---:|---:|"]
    for name, row in nmax.items():
        lines.append(f"| {name} | {_tr(row.get('usd_per_run'), 2)} | "
                     f"{'—' if row.get('n_max') is None else row['n_max']} |")
    return "\n".join(lines) + "\n"
