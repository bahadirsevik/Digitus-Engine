"""Bake-off ölçüm metrikleri (plan_ai §9).

İKİ RECALL AYRI (plan_ai §9/§11):
- `screening_recall`: modelin kendi başarısı — `fit ∈ {1,2}` alan patron
  pozitifleri / tüm patron pozitifleri. Bütçesiz.
- `candidate_recall_at`: sistemin başarısı — aday sıralaması + B bütçesi
  uygulandıktan sonra listede bulunan pozitifler. B/1.5B/2B + tam eğri.

Yüksek screening recall + düşük candidate_recall@B mümkündür (fit=2 kalabalıksa
bütçe yine taşar) — bu yüzden kapılar ayrıdır.
"""
from __future__ import annotations

import math
import statistics
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

CHANNELS = ("ads", "seo", "social")
CHANNEL_KEYS = {"ads": "ADS", "seo": "SEO", "social": "SOCIAL"}
PASSING_FITS = (1, 2)


def fit_of(result, channel: str) -> int:
    return getattr(result, f"{channel}_fit")


def screening_recall(results: Sequence[Any],
                     positives: Dict[str, set]) -> Dict[str, Any]:
    """Kanal başına screening recall — ÜRETİM GÜVENLİĞİ ile MODEL
    DEĞERLENDİRMESİ AYRIDIR (Codex bulgu #1, kritik).

    Runner çözülemeyen keyword'e üretim güvenliği gereği `fit=1` verir
    (eleme yok). Bu fallback MODEL BAŞARISI SAYILMAZ: tamamen çöken bir
    sağlayıcı aksi halde %100 recall alıp bake-off'u kazanırdı.

    - `recall_all_positives` (BİRİNCİL, kabul kapısı buna bakar):
      unresolved pozitif BAŞARISIZ sayılır.
    - `recall_resolved_only` (YARDIMCI): yalnız gerçekten karar verilen
      pozitifler üzerinden — "model karar verebildiğinde ne kadar iyi".
    - `positive_unresolved_*`: kaç pozitifin hiç değerlendirilemediği.
    """
    out: Dict[str, Any] = {}
    for ch in CHANNELS:
        pos = positives.get(CHANNEL_KEYS[ch], set())
        key = CHANNEL_KEYS[ch]
        if not pos:
            out[key] = {
                "P": 0, "recall_all_positives": None,
                "recall_resolved_only": None,
                "passing_all": 0, "passing_resolved": 0,
                "resolved_positives": 0, "positive_unresolved_count": 0,
                "positive_unresolved": [], "false_negative_count": 0,
                "false_negatives": [],
            }
            continue
        passing_all = 0
        passing_resolved = 0
        resolved_positives = 0
        fns: List[Dict[str, Any]] = []
        unresolved: List[Dict[str, Any]] = []
        for r in results:
            if r.keyword_id not in pos:
                continue
            if getattr(r, "unresolved", False):
                unresolved.append({"keyword_id": r.keyword_id,
                                   "keyword": r.keyword,
                                   "reason": getattr(r, "unresolved_reason", None)})
                continue  # BAŞARI SAYILMAZ (fallback fit'i yok sayılır)
            resolved_positives += 1
            if fit_of(r, ch) in PASSING_FITS:
                passing_all += 1
                passing_resolved += 1
            else:
                fns.append({"keyword_id": r.keyword_id, "keyword": r.keyword,
                            "reason_code": r.reason_codes.get(ch)})
        out[key] = {
            "P": len(pos),
            "resolved_positives": resolved_positives,
            "positive_unresolved_count": len(unresolved),
            "positive_unresolved": unresolved[:50],
            "passing_all": passing_all,
            "passing_resolved": passing_resolved,
            "recall_all_positives": round(passing_all / len(pos), 4),
            "recall_resolved_only": (
                round(passing_resolved / resolved_positives, 4)
                if resolved_positives else None
            ),
            "false_negative_count": len(fns),
            "false_negatives": fns[:50],
        }
    return out


def acceptance_gates(recall_block: Dict[str, Any], coverage: Optional[float],
                     *, min_channel_recall: float = 0.90,
                     target_channel_recall: float = 0.95,
                     min_coverage: float = 0.99) -> Dict[str, Any]:
    """plan_ai §11 screening kapıları — BİRİNCİL recall + kapsam.

    Kapsam (coverage) ayrı bir kapıdır: yüksek `recall_resolved_only` düşük
    kapsamla anlamsızdır (Codex #1'in ikinci yarısı).
    """
    channels = {}
    passed = True
    for key, block in recall_block.items():
        rec = block.get("recall_all_positives")
        if rec is None:
            channels[key] = {"recall": None, "status": "no_positives"}
            continue
        status = (
            "pass" if rec >= target_channel_recall
            else "warn" if rec >= min_channel_recall
            else "fail"
        )
        if status == "fail":
            passed = False
        channels[key] = {"recall": rec, "status": status}
    coverage_ok = coverage is not None and coverage >= min_coverage
    if not coverage_ok:
        passed = False
    return {
        "channels": channels,
        "coverage": coverage,
        "coverage_ok": coverage_ok,
        "thresholds": {
            "min_channel_recall": min_channel_recall,
            "target_channel_recall": target_channel_recall,
            "min_coverage": min_coverage,
        },
        "passed": passed,
    }


def llm_fit_ordering(results: Sequence[Any], channel: str, *,
                     tiebreak_rank: Optional[Dict[int, int]] = None) -> List[int]:
    """LLM fit sıralaması: (-fit, tiebreak_rank, keyword_id) — deterministik.

    tiebreak_rank verilmezse yalnız keyword_id ile kırılır (fit içi sıra
    keyfi olmasın diye üretimde ham skor rank'i geçilmelidir).
    """
    rank = tiebreak_rank or {}
    return [
        r.keyword_id
        for r in sorted(
            results,
            key=lambda r: (-fit_of(r, channel),
                           rank.get(r.keyword_id, 10**9),
                           r.keyword_id),
        )
    ]


def rrf_ordering(orderings: Sequence[Sequence[int]], k: int) -> List[int]:
    """Çok kaynaklı RRF (1-based rank'ler; candidate_replay ile aynı kural)."""
    ranks: List[Dict[int, int]] = [
        {kid: i for i, kid in enumerate(o, start=1)} for o in orderings
    ]
    universe = set()
    for o in orderings:
        universe.update(o)
    return sorted(
        universe,
        key=lambda kid: (
            -sum(1.0 / (k + r[kid]) for r in ranks if kid in r), kid
        ),
    )


def quota_union(sources: Dict[str, Sequence[int]], quotas: Dict[str, int],
                budget: int) -> List[int]:
    """Kaynak başına ASGARİ kotalı birleşim (plan_ai §8).

    Her kaynaktan kotası kadar alınır (round-robin doldurma), kalan bütçe
    kaynak sırasıyla tamamlanır. Tek kaynak pencereyi işgal edemez.
    """
    picked: List[int] = []
    seen = set()
    cursors = {name: 0 for name in sources}

    def take(name: str, count: int) -> None:
        seq = sources[name]
        i = cursors[name]
        added = 0
        while i < len(seq) and added < count and len(picked) < budget:
            kid = seq[i]
            i += 1
            if kid in seen:
                continue
            seen.add(kid)
            picked.append(kid)
            added += 1
        cursors[name] = i

    for name in sources:
        take(name, quotas.get(name, 0))
    while len(picked) < budget and any(
        cursors[n] < len(sources[n]) for n in sources
    ):
        progressed = False
        for name in sources:
            before = len(picked)
            take(name, 1)
            progressed = progressed or len(picked) > before
            if len(picked) >= budget:
                break
        if not progressed:
            break
    return picked[:budget]


def candidate_recall_at(ordering: Sequence[int], positives: Iterable[int],
                        budgets: Dict[str, int]) -> Dict[str, Any]:
    """recall@B blokları + tam evren eğrisi (1-based positive_ranks)."""
    pos = set(positives)
    pos_ranks = sorted(i for i, kid in enumerate(ordering, start=1) if kid in pos)
    out: Dict[str, Any] = {"positive_ranks": pos_ranks, "recall_at": {}}
    total = len(pos)
    for name, b in budgets.items():
        reached = sum(1 for r in pos_ranks if r <= b)
        out["recall_at"][name] = {
            "budget": b,
            "reached": reached,
            "recall": round(reached / total, 4) if total else None,
        }
    return out


def decision_flip_rate(run_a: Sequence[Any], run_b: Sequence[Any]) -> Dict[str, Any]:
    """İki koşu/permütasyon arasında karar değişimi (plan_ai §10/§11).

    İKİ metrik AYRI raporlanır (Codex #3):
    - `per_channel` TAM SINIF değişimi (0/1/2) — sıralamayı etkiler.
    - `per_channel_passing` GEÇER/KALIR sınırındaki değişim
      (`fit >= 1` eşiği) — aday havuzuna girip girmemeyi belirler.
    İkisi çok farklı olabilir: 1↔2 oynaklığı sıralamayı bozar ama
    kelimeyi havuzdan atmaz.
    """
    a = {r.keyword_id: r for r in run_a}
    b = {r.keyword_id: r for r in run_b}
    common = set(a) & set(b)
    per_channel = {}
    per_channel_passing = {}
    any_flip = 0
    any_passing_flip = 0
    for ch in CHANNELS:
        flips = sum(1 for kid in common if fit_of(a[kid], ch) != fit_of(b[kid], ch))
        passing_flips = sum(
            1 for kid in common
            if (fit_of(a[kid], ch) in PASSING_FITS)
            != (fit_of(b[kid], ch) in PASSING_FITS)
        )
        per_channel[CHANNEL_KEYS[ch]] = {
            "flips": flips,
            "rate": round(flips / len(common), 4) if common else None,
        }
        per_channel_passing[CHANNEL_KEYS[ch]] = {
            "flips": passing_flips,
            "rate": round(passing_flips / len(common), 4) if common else None,
        }
    for kid in common:
        if any(fit_of(a[kid], ch) != fit_of(b[kid], ch) for ch in CHANNELS):
            any_flip += 1
        if any((fit_of(a[kid], ch) in PASSING_FITS)
               != (fit_of(b[kid], ch) in PASSING_FITS) for ch in CHANNELS):
            any_passing_flip += 1
    return {
        "compared": len(common),
        "per_channel": per_channel,
        "per_channel_passing": per_channel_passing,
        "any_channel_flips": any_flip,
        "any_channel_rate": round(any_flip / len(common), 4) if common else None,
        "any_channel_passing_flips": any_passing_flip,
        "any_channel_passing_rate": (
            round(any_passing_flip / len(common), 4) if common else None
        ),
    }


STABILITY_MAX_RATE = 0.10  # plan_ai §11: karar değişimi <= %5-10


def bakeoff_acceptance(model_block: Dict[str, Any], *,
                       complete: bool) -> Dict[str, Any]:
    """MODEL DÜZEYİ kabul kararı (Codex #1).

    Tekil koşuların screening kapısını geçmesi YETMEZ; plan_ai §11 ayrıca
    istikrar ve candidate-recall artışı ister. Özetteki
    `eligible_for_acceptance` yalnız artifact bütünlüğüydü — bu fonksiyon
    dört ekseni ayrı ayrı raporlar ve genel hükmü verir.
    """
    per_run = []
    stability = []
    cand = []
    for slug, entry in (model_block or {}).items():
        for seed, s in (entry.get("seeds") or {}).items():
            per_run.append(bool(s.get("gates_passed")))
        st = entry.get("seed_stability") or {}
        if st:
            stability.append({
                "dataset": slug,
                "full_class_rate": st.get("any_channel_rate"),
                "passing_rate": st.get("any_channel_passing_rate"),
                "passed": (st.get("any_channel_passing_rate") is not None
                           and st["any_channel_passing_rate"] <= STABILITY_MAX_RATE),
            })
        # candidate_recall: LLM tabanlı en iyi varyant ham skoru geçmeli
        seeds = list((entry.get("seeds") or {}).values())
        for ch in ("ADS", "SEO", "SOCIAL"):
            raw_vals, best_vals = [], []
            for s in seeds:
                block = (s.get("candidate_recall") or {}).get(ch) or {}
                if not block:
                    continue
                raw_vals.append((block.get("raw_rank") or {}).get("B_initial", 0))
                llm = [v.get("B_initial", 0) for k, v in block.items()
                       if k != "raw_rank" and k != "relevance"]
                if llm:
                    best_vals.append(max(llm))
            if raw_vals and best_vals:
                raw_avg = sum(raw_vals) / len(raw_vals)
                best_avg = sum(best_vals) / len(best_vals)
                cand.append({
                    "dataset": slug, "channel": ch,
                    "raw_rank_avg": round(raw_avg, 2),
                    "best_llm_variant_avg": round(best_avg, 2),
                    "delta": round(best_avg - raw_avg, 2),
                })
    stability_passed = bool(stability) and all(s["passed"] for s in stability)
    no_regression = all(c["delta"] >= 0 for c in cand) if cand else False
    improved = sum(1 for c in cand if c["delta"] > 0)
    candidate_passed = bool(cand) and no_regression and improved >= 1
    per_run_passed = bool(per_run) and all(per_run)
    return {
        "per_run_screening_gate_passed": per_run_passed,
        "stability_gate_passed": stability_passed,
        "stability_detail": stability,
        "stability_threshold": STABILITY_MAX_RATE,
        "candidate_recall_gate_passed": candidate_passed,
        "candidate_recall_detail": cand,
        "artifacts_complete": complete,
        "overall_acceptance_passed": bool(
            complete and per_run_passed and stability_passed and candidate_passed
        ),
    }


# ── Churn / bütçe analizi yardımcıları (Codex #4, #5) ────────────────────
# Script içinde gömülü kalırsa test edilemez; ölçüm sözleşmesi burada.

def restricted_reach(order: Sequence[int], positives: Sequence[int],
                     common_ids: Optional[Set[int]], budget: int
                     ) -> Dict[str, Any]:
    """Erişimi YALNIZ iki evrende de bulunan kelimeler üzerinde ölçer.

    Codex #4: churn karşılaştırmasında silinen pozitifler paydada kalırsa
    "erişim düştü" sonucu churn'ün kendisinden doğar, kararsızlıktan
    değil. `common_ids` verildiğinde hem SIRALAMA hem PAYDA kısıtlanır.
    """
    if common_ids is not None:
        order = [k for k in order if k in common_ids]
        positives = [p for p in positives if p in common_ids]
    top = list(order[:budget])
    pos_set = set(positives)
    reached = [k for k in top if k in pos_set]
    return {
        "budget": budget,
        "reached": len(reached),
        "positives": len(pos_set),
        "recall": round(len(reached) / len(pos_set), 4) if pos_set else None,
        "top_set": set(top),
        "reached_ids": reached,
    }


def displacement(base_top: Set[int], variant_order: Sequence[int],
                 budget: int) -> Dict[str, Any]:
    """Ekleme yönünde: yeni kelimeler base top-B'nin ne kadarını itiyor."""
    survivors = base_top & set(variant_order[:budget])
    return {
        "survivors": len(survivors),
        "displaced": len(base_top) - len(survivors),
        "displacement_ratio": round(1 - len(survivors) / max(len(base_top), 1), 4),
    }


def downstream_requests(channel: str, n_candidates: int,
                        stage_batches: Dict[str, int],
                        ai_free_stages: Sequence[str] = ()) -> Dict[str, int]:
    """Aday sayısıyla doğrusal ölçeklenen üretim hattının istek sayısı.

    `ai_free_stages`: o kanalda AI kullanmayan aşamalar (ör. SEO ön-filtresi
    Faz B'den beri deterministiktir → 0 istek).
    """
    out = {}
    for stage, batch in stage_batches.items():
        if stage in ai_free_stages:
            out[stage] = 0
            continue
        out[stage] = math.ceil(n_candidates / batch) if n_candidates else 0
    out["total"] = sum(v for k, v in out.items() if k != "total")
    return out


def reach_range(orders_by_view: Dict[str, Sequence[int]],
                positives: Sequence[int],
                budgets: Dict[str, int]) -> Dict[str, Any]:
    """Erişimi TÜM görünüm(çift)leri üzerinden aralıkla raporlar (Codex #5).

    Tek çiftten okunan sayı şanslı/şanssız olabilir; kabul kararı min–
    medyan–maks üçlüsüyle verilir.
    """
    if not orders_by_view:
        raise ValueError("en az bir görünüm gerekir")
    per_view = {
        name: {b: candidate_recall_at(order, positives, {b: n})
               ["recall_at"][b]["reached"]
               for b, n in budgets.items()}
        for name, order in orders_by_view.items()
    }
    out = {}
    for b in budgets:
        vals = [v[b] for v in per_view.values()]
        out[b] = {
            "budget": budgets[b],
            "reach_min": min(vals), "reach_max": max(vals),
            "reach_median": statistics.median(vals),
            "views": len(vals),
        }
    return {"per_view": per_view, "summary": out}
