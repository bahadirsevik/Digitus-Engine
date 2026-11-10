# -*- coding: utf-8 -*-
"""V4 Pro Katman-A GÖRELİ kapı sözleşmesi (Codex kararı, aday görülmeden).

Gerekçe: referans karakterizasyonu (reference_characterization_gemini-*.json)
referansın KENDİSİNİN .85 mutlak macro-F1 tabanının çok altında olduğunu
ölçtü (.29-.53). Mutlak taban provider-değiştirme kapısı olmaktan çıktı
(ürün kalitesi hedefi olarak AYRI işte yaşıyor); bake-off kapıları artık
REFERANSA GÖRELİDİR ve sınıf-bazlı korumalar içerir:

  G1  Genel F1 (LLM ekseni başına):
      min(aday tekrarları) >= min(referans tekrarları) - 0.03
  G2  Seyrek sınıflar (destek < 15): mutlak %70 recall KALKTI;
      min(aday TP) >= min(referans TP) - 1
  G3  ADS güvenlik sınıfı '-1': recall VE precision >= referans min - 0.05
      (macro-F1 reklam israfı / yanlış eleme riskini saklayabilir)
  G4  SOCIAL pozitif sınıfları ('1'): TP kaybı <= 1 (referans min'e göre)
      VE FP <= referans maks FP + %5·n (aşırı-üretkenlik daha da kötüleşemez)
  G5  GT/GA: max(aday MAE) <= max(referans MAE) + 0.03
      (eski +0.25 provider-paritesi için fazla gevşekti)
  G6  Kararlılık: karakterizasyonda MÜHÜRLENEN flip izinleri aynen
  G7  Operasyonel mutlaklar: coverage %100, fallback <= %1,
      retry-sonrası parse >= %98, maliyet düşüşü >= %50 (SETTLED
      maliyet üstünden; her iki tarafta ceiling_charges == 0 —
      usage'sız/kısmi çağrı 'çok ucuz' yanılsaması yaratamaz)
  G8  Tolerant skor YALNIZ yan rapor (karar otoritesi STRICT skordur)

Bu modül sözleşmenin TEK kaynağıdır; V4 Pro sonucu görülmeden commit'lenir
ve testle kilitlenir. Sözleşme değişirse sürüm değişmek zorundadır.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence

RELATIVE_GATES_VERSION = "REL-2026-07-29-v5"
# v4 (Codex, aday gorulmeden): class_metrics eksiksiz ve matematiksel
# olarak tutarli olmak zorunda. macro_f1, recall, class f1 ve fn hazir
# sayi olarak guvenilmez; tp/fp/support'tan yeniden hesaplanir ve raporlanan
# degerlerle capraz dogrulanir. Eksik cogunluk sinifi, sahte macro_f1 ve
# imkansiz confusion matrix fail-closed reddedilir.
# v3 (Codex, aday gorulmeden): tam veri-butunlugu — hepsi-None esitligi
# GECEMEZ (gold_n pozitif tamsayi; n/missing negatif olmayan TAMSAYI,
# kesirli red; n+missing==gold_n; class_support degerleri tamsayi ve
# toplami gold_n); REFERANS tekrarlari da ayni butunluge tabi (missing=0);
# G1-G5 metrikleri aralik/tip dogrulamali (F1/recall/precision [0,1],
# MAE>=0, tp<=support, metrics support == class_support[label] ya da
# tahmin-ddisi sinifta 0); G6'da aday flip kumesi de TAM esit.
# v2 (Codex, aday gorulmeden): G0 butunluk katmani eklendi — eksen bazli
# eksik tahmin saklanamaz (cand n == ref n, missing_predictions == 0,
# gold_n/class_support tum tekrarlarda birebir); coverage/parse kayit
# sayisi tekrar sayisina esit olmak zorunda; tum oran/maliyetler mevcut,
# sonlu ve gecerli aralikta (NaN/negatif fail-closed); G6 eksen kumesi
# TAM olarak LLM+GRADE olmali (eksik/fazla red).

LLM_AXES = ("intent_type", "ads_class", "opinion_discussion",
            "curiosity_comparison", "agenda_theme")
GRADE_AXES = ("gt", "ga")
SOCIAL_AXES = ("opinion_discussion", "curiosity_comparison", "agenda_theme")

G1_F1_MAX_LOSS = 0.03
G2_SPARSE_MIN_SUPPORT = 15
G2_SPARSE_MAX_TP_LOSS = 1
G3_ADS_SAFETY_CLASS = "-1"
G3_MAX_LOSS = 0.05
G4_MAX_TP_LOSS = 1
G4_FP_HEADROOM_RATIO = 0.05
G5_MAE_MAX_INCREASE = 0.03
G7_FALLBACK_MAX = 0.01
G7_PARSE_POST_RETRY_MIN = 0.98
G7_COST_REDUCTION_MIN = 0.50


class RelativeGateError(RuntimeError):
    """Kapı değerlendirmesi için gereken veri eksik/bozuk — fail-closed."""


def _require_finite(value, name: str, *, lo=None, hi=None):
    """NaN/None/aralık-dışı değer İHLAL ÜRETMEDEN GEÇEMEZ (fail-closed)."""
    if value is None or isinstance(value, bool)             or not isinstance(value, (int, float))             or not math.isfinite(float(value)):
        raise RelativeGateError(f"{name}: eksik/sonlu-olmayan değer: {value!r}")
    v = float(value)
    if lo is not None and v < lo:
        raise RelativeGateError(f"{name}: {v} < izinli alt sınır {lo}")
    if hi is not None and v > hi:
        raise RelativeGateError(f"{name}: {v} > izinli üst sınır {hi}")
    return v


def _require_nonneg_int(value, name: str) -> int:
    """Negatif olmayan GERÇEK tamsayı; bool ve kesirli değer REDDEDİLİR
    (int(0.9)=0 kırpması kapı atlatamaz)."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise RelativeGateError(
            f"{name}: negatif olmayan TAM SAYI olmalı; verilen: {value!r}")
    if value < 0:
        raise RelativeGateError(f"{name}: negatif olamaz: {value}")
    return value


def json_key(obj) -> str:
    import json as _json

    return _json.dumps(obj, sort_keys=True, ensure_ascii=False)


def _axis_f1(axes: dict, axis: str) -> Optional[float]:
    return (axes.get(axis) or {}).get("macro_f1")


def _axis_mae(axes: dict, axis: str) -> Optional[float]:
    return (axes.get(axis) or {}).get("mae")


def _require_close(value, expected: float, name: str) -> float:
    value = _require_finite(value, name, lo=0.0, hi=1.0)
    if not math.isclose(value, expected, rel_tol=1e-12, abs_tol=1e-12):
        raise RelativeGateError(
            f"{name}: raporlanan {value} != yeniden hesaplanan {expected}")
    return expected


def _validate_classification_axis(
    axis_blk: dict,
    axis: str,
    context: str,
) -> dict:
    """Validate and normalize one complete classification-axis report."""
    gold_n = _require_nonneg_int(
        axis_blk.get("gold_n"), f"{context} {axis} gold_n")
    n = _require_nonneg_int(axis_blk.get("n"), f"{context} {axis} n")
    missing = _require_nonneg_int(
        axis_blk.get("missing_predictions"),
        f"{context} {axis} missing_predictions")
    gold_support = axis_blk.get("class_support")
    metrics = axis_blk.get("class_metrics")
    if not isinstance(gold_support, dict) or not gold_support:
        raise RelativeGateError(f"{context} {axis}: class_support eksik/boş")
    if not isinstance(metrics, dict) or not metrics:
        raise RelativeGateError(f"{context} {axis}: class_metrics eksik/boş")

    normalized_support = {
        str(label): _require_nonneg_int(
            count, f"{context} {axis} class_support[{label}]")
        for label, count in gold_support.items()
    }
    if sum(normalized_support.values()) != gold_n:
        raise RelativeGateError(
            f"{context} {axis}: sum(class_support) != gold_n")

    metric_labels = {str(label) for label in metrics}
    if missing == 0:
        missing_labels = set(normalized_support) - metric_labels
        if missing_labels:
            raise RelativeGateError(
                f"{context} {axis}: class_metrics eksik sınıflar: "
                f"{sorted(missing_labels)}")

    normalized_metrics = {}
    total_predicted = 0
    total_fp = 0
    total_fn = 0
    f1_values = []
    for raw_label, raw_blk in metrics.items():
        label = str(raw_label)
        if not isinstance(raw_blk, dict):
            raise RelativeGateError(
                f"{context} {axis}/{label}: class_metrics bloğu geçersiz")
        tp = _require_nonneg_int(
            raw_blk.get("tp"), f"{context} {axis}/{label} tp")
        fp = _require_nonneg_int(
            raw_blk.get("fp"), f"{context} {axis}/{label} fp")
        support = _require_nonneg_int(
            raw_blk.get("support"), f"{context} {axis}/{label} support")
        expected_gold_support = normalized_support.get(label, 0)
        if support > expected_gold_support:
            raise RelativeGateError(
                f"{context} {axis}/{label}: metrics support {support} > "
                f"class_support {expected_gold_support}")
        if missing == 0 and support != expected_gold_support:
            raise RelativeGateError(
                f"{context} {axis}/{label}: metrics support {support} != "
                f"class_support {expected_gold_support}")
        if tp > support:
            raise RelativeGateError(
                f"{context} {axis}/{label}: tp {tp} > support {support} "
                "— imkânsız confusion matrix")
        if fp > n - support:
            raise RelativeGateError(
                f"{context} {axis}/{label}: fp {fp} > negatif örnek "
                f"sayısı {n - support}")

        fn = support - tp
        stored_fn = _require_nonneg_int(
            raw_blk.get("fn"), f"{context} {axis}/{label} fn")
        if stored_fn != fn:
            raise RelativeGateError(
                f"{context} {axis}/{label}: fn {stored_fn} != "
                f"yeniden hesaplanan {fn}")
        stored_label = raw_blk.get("label")
        if stored_label is None or str(stored_label) != label:
            raise RelativeGateError(
                f"{context} {axis}/{label}: label alanı anahtarla uyuşmuyor")

        recall = (tp / support) if support else None
        if recall is None:
            if raw_blk.get("recall") is not None:
                raise RelativeGateError(
                    f"{context} {axis}/{label}: support=0 iken recall "
                    "null olmalı")
        else:
            _require_close(
                raw_blk.get("recall"), recall,
                f"{context} {axis}/{label} recall")

        denom = 2 * tp + fp + fn
        if denom <= 0:
            raise RelativeGateError(
                f"{context} {axis}/{label}: boş class_metrics sınıfı")
        class_f1 = 2 * tp / denom
        _require_close(
            raw_blk.get("f1"), class_f1,
            f"{context} {axis}/{label} f1")

        normalized_metrics[label] = {
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "support": support,
            "recall": recall,
            "f1": class_f1,
        }
        total_predicted += tp + fp
        total_fp += fp
        total_fn += fn
        f1_values.append(class_f1)

    if sum(m["support"] for m in normalized_metrics.values()) != n:
        raise RelativeGateError(
            f"{context} {axis}: class_metrics support toplamı n ile "
            "uyuşmuyor")
    if total_predicted != n:
        raise RelativeGateError(
            f"{context} {axis}: tahmin toplamı {total_predicted} != n {n}")
    if total_fp != total_fn:
        raise RelativeGateError(
            f"{context} {axis}: toplam fp {total_fp} != toplam fn {total_fn}")

    macro_f1 = sum(f1_values) / len(f1_values)
    _require_close(
        axis_blk.get("macro_f1"), macro_f1,
        f"{context} {axis} macro_f1")
    return {
        "n": n,
        "gold_n": gold_n,
        "missing_predictions": missing,
        "class_support": normalized_support,
        "class_metrics": normalized_metrics,
        "macro_f1": macro_f1,
    }


def _class_blk(axes: dict, axis: str, label: str) -> dict:
    blk = ((axes.get(axis) or {}).get("class_metrics") or {}).get(label)
    if blk is None:
        raise RelativeGateError(
            f"{axis}/{label}: class_metrics eksik — kapı değerlendirilemez")
    return blk


def _precision(blk: dict) -> Optional[float]:
    tp, fp = blk["tp"], blk["fp"]
    denom = tp + fp
    return (tp / denom) if denom else None


def evaluate_relative_gates(
    ref_axes_by_repeat: Dict[str, dict],
    cand_axes_by_repeat: Dict[str, dict],
    *,
    flip_allowances: Dict[str, dict],
    cand_max_flips: Dict[str, Optional[float]],
    cand_coverages: Sequence[dict],
    cand_parses: Sequence[dict],
    ref_cost_usd: float,
    cand_cost_usd: float,
    ref_ceiling_charges: int,
    cand_ceiling_charges: int,
) -> Dict[str, Any]:
    """Göreli kapılar — her ihlal açık gerekçeyle listelenir.

    `ref_axes_by_repeat` / `cand_axes_by_repeat`: {repeat_id: axis_report}.
    `flip_allowances`: karakterizasyonda mühürlenen `derived_allowances
    .per_axis` (candidate_flip_allowance alanları).
    `cand_max_flips`: adayın kendi tekrarları arasındaki eksen başına
    maksimum pass-flip oranı.
    """
    if len(ref_axes_by_repeat) < 2 or len(cand_axes_by_repeat) < 2:
        raise RelativeGateError("iki tarafta da en az iki tekrar gerekir")
    n_cand_repeats = len(cand_axes_by_repeat)
    if (len(cand_coverages) != n_cand_repeats
            or len(cand_parses) != n_cand_repeats):
        raise RelativeGateError(
            f"coverage({len(cand_coverages)})/parse({len(cand_parses)}) "
            f"kayıt sayısı aday tekrar sayısına ({n_cand_repeats}) eşit "
            f"olmalı — tek kayıtla iki tekrar raporlanamaz")
    violations: List[str] = []
    detail: Dict[str, Any] = {"version": RELATIVE_GATES_VERSION, "gates": {}}

    # G0 — eksen bütünlüğü (v3): hepsi-None eşitliği GEÇEMEZ; invariantlar
    # her REFERANS ve ADAY tekrarında ayrı ayrı doğrulanır.
    g0 = {}
    all_reps = {**{f"ref:{k}": v for k, v in ref_axes_by_repeat.items()},
                **{f"cand:{k}": v for k, v in cand_axes_by_repeat.items()}}
    validated_reps = {name: {} for name in all_reps}
    for axis in LLM_AXES:
        per_rep = {}
        for name, rep in all_reps.items():
            blk = rep.get(axis) or {}
            gold_n = _require_nonneg_int(blk.get("gold_n"),
                                         f"G0 {axis} {name} gold_n")
            if gold_n <= 0:
                raise RelativeGateError(
                    f"G0 {axis} {name}: gold_n pozitif olmalı ({gold_n})")
            n = _require_nonneg_int(blk.get("n"), f"G0 {axis} {name} n")
            missing = _require_nonneg_int(
                blk.get("missing_predictions"),
                f"G0 {axis} {name} missing_predictions")
            if n + missing != gold_n:
                raise RelativeGateError(
                    f"G0 {axis} {name}: n({n}) + missing({missing}) != "
                    f"gold_n({gold_n}) — muhasebe tutmuyor")
            support = blk.get("class_support")
            if not isinstance(support, dict) or not support:
                raise RelativeGateError(
                    f"G0 {axis} {name}: class_support eksik/boş")
            total = 0
            for label, count in support.items():
                total += _require_nonneg_int(
                    count, f"G0 {axis} {name} class_support[{label}]")
            if total != gold_n:
                raise RelativeGateError(
                    f"G0 {axis} {name}: sum(class_support)={total} != "
                    f"gold_n={gold_n}")
            # REFERANS da eksiksiz olmalı (v3 #5): taban düşürülemez
            if name.startswith("ref:") and missing != 0:
                raise RelativeGateError(
                    f"G0 {axis} {name}: REFERANS tekrarında {missing} "
                    f"eksik tahmin — referans tabanı geçersiz")
            validated_reps[name][axis] = _validate_classification_axis(
                blk, axis, f"G0 {name}")
            per_rep[name] = {"gold_n": gold_n, "n": n, "missing": missing,
                            "support": support}
        if len({v["gold_n"] for v in per_rep.values()}) != 1:
            raise RelativeGateError(
                f"G0 {axis}: gold_n tekrarlar arasında uyuşmuyor")
        if len({json_key(v["support"]) for v in per_rep.values()}) != 1:
            raise RelativeGateError(
                f"G0 {axis}: class_support tekrarlar arasında uyuşmuyor "
                f"(fixture/etiket kayması)")
        ref_ns = {v["n"] for k, v in per_rep.items()
                  if k.startswith("ref:")}
        for name, v in per_rep.items():
            if not name.startswith("cand:"):
                continue
            if v["missing"] != 0:
                violations.append(
                    f"G0 {axis} {name}: {v['missing']} maddede tahmin yok "
                    f"— eksen bazlı eksik F1'de saklanamaz")
            if v["n"] not in ref_ns:
                violations.append(
                    f"G0 {axis} {name}: aday n={v['n']} != referans "
                    f"n={sorted(ref_ns)}")
        g0[axis] = {"gold_n": next(iter(
            v["gold_n"] for v in per_rep.values())),
            "cand_missing": [v["missing"] for k, v in per_rep.items()
                             if k.startswith("cand:")]}
    for axis in GRADE_AXES:
        ns = {}
        for name, rep in all_reps.items():
            ns[name] = _require_nonneg_int(
                (rep.get(axis) or {}).get("n"), f"G0 {axis} {name} n")
        if len(set(ns.values())) != 1:
            violations.append(f"G0 {axis}: n tekrarlar arasında uyuşmuyor "
                              f"{ns}")
        g0[axis] = {"n": ns}
    detail["gates"]["G0_axis_integrity"] = g0

    validated_ref_axes = {
        key: validated_reps[f"ref:{key}"] for key in ref_axes_by_repeat
    }
    validated_cand_axes = {
        key: validated_reps[f"cand:{key}"] for key in cand_axes_by_repeat
    }

    # G1 — genel F1
    g1 = {}
    for axis in LLM_AXES:
        ref_vals = [_require_finite(_axis_f1(a, axis),
                                    f"G1 {axis} ref macro_f1",
                                    lo=0.0, hi=1.0)
                    for a in validated_ref_axes.values()]
        cand_vals = [_require_finite(_axis_f1(a, axis),
                                     f"G1 {axis} aday macro_f1",
                                     lo=0.0, hi=1.0)
                     for a in validated_cand_axes.values()]
        floor = min(ref_vals) - G1_F1_MAX_LOSS
        ok = min(cand_vals) >= floor
        g1[axis] = {"ref_min": round(min(ref_vals), 4),
                    "cand_min": round(min(cand_vals), 4),
                    "floor": round(floor, 4), "passed": ok}
        if not ok:
            violations.append(
                f"G1 {axis}: aday F1 {min(cand_vals):.3f} < taban "
                f"{floor:.3f} (ref min {min(ref_vals):.3f} - "
                f"{G1_F1_MAX_LOSS})")
    detail["gates"]["G1_macro_f1_relative"] = g1

    # G2 — seyrek sınıflar (destek referans gold desteğinden)
    g2 = {}
    first_ref = next(iter(validated_ref_axes.values()))
    for axis in LLM_AXES:
        support = (first_ref.get(axis) or {}).get("class_support") or {}
        for label, count in support.items():
            if count >= G2_SPARSE_MIN_SUPPORT:
                continue
            ref_tp = min(_class_blk(a, axis, label)["tp"]
                         for a in validated_ref_axes.values())
            cand_tp = min(_class_blk(a, axis, label)["tp"]
                          for a in validated_cand_axes.values())
            floor = ref_tp - G2_SPARSE_MAX_TP_LOSS
            ok = cand_tp >= floor
            g2[f"{axis}/{label}"] = {"support": count, "ref_min_tp": ref_tp,
                                     "cand_min_tp": cand_tp,
                                     "floor": floor, "passed": ok}
            if not ok:
                violations.append(
                    f"G2 {axis}/{label} (destek {count}): aday TP "
                    f"{cand_tp} < {floor} (ref min {ref_tp} - 1)")
    detail["gates"]["G2_sparse_class_tp"] = g2

    # G3 — ADS '-1' güvenlik sınıfı: recall + precision
    g3 = {}
    for metric, fn in (("recall", lambda b: b["recall"]),
                       ("precision", _precision)):
        ref_vals = [fn(_class_blk(a, "ads_class", G3_ADS_SAFETY_CLASS))
                    for a in validated_ref_axes.values()]
        cand_vals = [fn(_class_blk(a, "ads_class", G3_ADS_SAFETY_CLASS))
                     for a in validated_cand_axes.values()]
        ref_vals = [_require_finite(v, f"G3 ads/-1 ref {metric}",
                                    lo=0.0, hi=1.0) for v in ref_vals]
        cand_vals = [_require_finite(v, f"G3 ads/-1 aday {metric}",
                                     lo=0.0, hi=1.0) for v in cand_vals]
        floor = min(ref_vals) - G3_MAX_LOSS
        ok = min(cand_vals) >= floor
        g3[metric] = {"ref_min": round(min(ref_vals), 4),
                      "cand_min": round(min(cand_vals), 4),
                      "floor": round(floor, 4), "passed": ok}
        if not ok:
            violations.append(
                f"G3 ads/-1 {metric}: aday {min(cand_vals):.3f} < taban "
                f"{floor:.3f}")
    detail["gates"]["G3_ads_safety_class"] = g3

    # G4 — SOCIAL pozitif sınıfları: TP kaybı + FP tavanı
    g4 = {}
    for axis in SOCIAL_AXES:
        n = (first_ref.get(axis) or {}).get("gold_n") or 0
        fp_headroom = int(math.floor(G4_FP_HEADROOM_RATIO * n))
        ref_tp = min(_class_blk(a, axis, "1")["tp"]
                     for a in validated_ref_axes.values())
        cand_tp = min(_class_blk(a, axis, "1")["tp"]
                      for a in validated_cand_axes.values())
        ref_fp = max(_class_blk(a, axis, "1")["fp"]
                     for a in validated_ref_axes.values())
        cand_fp = max(_class_blk(a, axis, "1")["fp"]
                      for a in validated_cand_axes.values())
        tp_floor = ref_tp - G4_MAX_TP_LOSS
        fp_cap = ref_fp + fp_headroom
        tp_ok, fp_ok = cand_tp >= tp_floor, cand_fp <= fp_cap
        g4[axis] = {"ref_min_tp": ref_tp, "cand_min_tp": cand_tp,
                    "tp_floor": tp_floor, "ref_max_fp": ref_fp,
                    "cand_max_fp": cand_fp, "fp_cap": fp_cap,
                    "passed": tp_ok and fp_ok}
        if not tp_ok:
            violations.append(f"G4 {axis}: aday TP {cand_tp} < {tp_floor}")
        if not fp_ok:
            violations.append(
                f"G4 {axis}: aday FP {cand_fp} > tavan {fp_cap} "
                f"(ref maks {ref_fp} + %5·{n}={fp_headroom}) — "
                f"aşırı-üretkenlik kötüleşemez")
    detail["gates"]["G4_social_positive"] = g4

    # G5 — GT/GA MAE
    g5 = {}
    for axis in GRADE_AXES:
        # gt/ga 0/1 eksenleridir: MAE [0,1] dışına ÇIKAMAZ — üst sınır
        # olmadan şişirilmiş referans MAE'si tavanı boşaltabilirdi
        ref_vals = [_require_finite(_axis_mae(a, axis),
                                    f"G5 {axis} ref mae", lo=0.0, hi=1.0)
                    for a in ref_axes_by_repeat.values()]
        cand_vals = [_require_finite(_axis_mae(a, axis),
                                     f"G5 {axis} aday mae", lo=0.0, hi=1.0)
                     for a in cand_axes_by_repeat.values()]
        cap = max(ref_vals) + G5_MAE_MAX_INCREASE
        ok = max(cand_vals) <= cap
        g5[axis] = {"ref_max": round(max(ref_vals), 4),
                    "cand_max": round(max(cand_vals), 4),
                    "cap": round(cap, 4), "passed": ok}
        if not ok:
            violations.append(
                f"G5 {axis}: aday MAE {max(cand_vals):.3f} > tavan "
                f"{cap:.3f}")
    detail["gates"]["G5_grade_mae"] = g5

    # G6 — mühürlenen flip izinleri; eksen kümesi TAM olmak zorunda
    expected_axes = set(LLM_AXES) | set(GRADE_AXES)
    if set(flip_allowances) != expected_axes:
        raise RelativeGateError(
            "G6: izin ekseni kümesi TAM değil — eksik: "
            f"{sorted(expected_axes - set(flip_allowances))}, fazla: "
            f"{sorted(set(flip_allowances) - expected_axes)} (eksen "
            f"haritadan çıkarılarak kapı atlatılamaz)")
    if set(cand_max_flips) != expected_axes:
        raise RelativeGateError(
            "G6: aday flip kümesi TAM değil — eksik: "
            f"{sorted(expected_axes - set(cand_max_flips))}, fazla: "
            f"{sorted(set(cand_max_flips) - expected_axes)}")
    g6 = {}
    for axis in sorted(expected_axes):
        allowance = (flip_allowances[axis] or {}).get(
            "candidate_flip_allowance")
        allowance = _require_finite(
            allowance, f"G6 {axis} izin", lo=0.0, hi=1.0)
        observed = _require_finite(
            cand_max_flips[axis], f"G6 {axis} gözlenen", lo=0.0, hi=1.0)
        ok = observed <= allowance
        g6[axis] = {"allowance": allowance, "observed": observed,
                    "passed": ok}
        if not ok:
            violations.append(
                f"G6 {axis}: aday flip {observed} > mühürlü izin "
                f"{allowance}")
    detail["gates"]["G6_stability_sealed_allowances"] = g6

    # G7 — operasyonel mutlaklar (eksik/NaN/negatif FAIL-CLOSED)
    g7 = {}
    for i, cov in enumerate(cand_coverages, start=1):
        if "missing_predictions" not in cov and "missing" not in cov:
            raise RelativeGateError(
                f"G7 R{i}: missing_predictions alanı yok")
        missing = _require_nonneg_int(
            cov.get("missing_predictions", cov.get("missing")),
            f"G7 R{i} missing")
        if "fallback_rate" not in cov:
            raise RelativeGateError(f"G7 R{i}: fallback_rate alanı yok")
        fb = _require_finite(cov["fallback_rate"], f"G7 R{i} fallback",
                             lo=0.0, hi=1.0)
        if missing:
            violations.append(f"G7 R{i}: coverage eksik ({missing})")
        if fb > G7_FALLBACK_MAX:
            violations.append(f"G7 R{i}: fallback {fb:.3f} > "
                              f"{G7_FALLBACK_MAX}")
        g7[f"R{i}_coverage"] = {"missing": missing, "fallback_rate": fb}
    for i, par in enumerate(cand_parses, start=1):
        pr = _require_finite(par.get("post_retry_ok"),
                             f"G7 R{i} parse post-retry", lo=0.0, hi=1.0)
        if pr < G7_PARSE_POST_RETRY_MIN:
            violations.append(f"G7 R{i}: parse post-retry {pr} < "
                              f"{G7_PARSE_POST_RETRY_MIN}")
        g7[f"R{i}_parse_post_retry"] = pr
    # Codex Katman-A #3: maliyet SETTLED üstünden gelir ve usage'sız
    # çağrı (ceiling charge) 'çok ucuz' yanılsaması yaratamaz — iki
    # tarafta da ceiling_charges 0 olmalı (ref>0 tabanı şişirip maliyet
    # kapısını adaya AVANTAJLI çarpıtırdı; o yüzden ref de mutlak)
    cc_ref = _require_nonneg_int(ref_ceiling_charges,
                                 "G7 referans ceiling_charges")
    cc_cand = _require_nonneg_int(cand_ceiling_charges,
                                  "G7 aday ceiling_charges")
    g7["ceiling_charges"] = {"ref": cc_ref, "cand": cc_cand,
                             "passed": cc_ref == 0 and cc_cand == 0}
    if cc_ref > 0:
        violations.append(
            f"G7 referans ceiling_charges {cc_ref} > 0 — settled "
            f"maliyet tabanı usage'sız çağrılarla şişmiş")
    if cc_cand > 0:
        violations.append(
            f"G7 aday ceiling_charges {cc_cand} > 0 — usage'sız/kısmi "
            f"çağrı gerçek maliyeti gizliyor olabilir")
    ref_cost = _require_finite(ref_cost_usd, "G7 referans maliyeti")
    cand_cost = _require_finite(cand_cost_usd, "G7 aday maliyeti", lo=0.0)
    if ref_cost <= 0:
        raise RelativeGateError("G7: referans maliyeti pozitif olmalı")
    reduction = (ref_cost - cand_cost) / ref_cost
    ok = reduction >= G7_COST_REDUCTION_MIN
    g7["cost_reduction"] = {"ref_usd": round(ref_cost, 4),
                            "cand_usd": round(cand_cost, 4),
                            "reduction": round(reduction, 4),
                            "passed": ok}
    if not ok:
        violations.append(
            f"G7 maliyet: düşüş {reduction:.2%} < "
            f"{G7_COST_REDUCTION_MIN:.0%}")
    detail["gates"]["G7_operational"] = g7

    detail["violations"] = violations
    detail["passed"] = not violations
    detail["note"] = (
        "G8: tolerant skor YALNIZ yan rapordur; karar otoritesi STRICT. "
        ".85 mutlak macro-F1 artık provider kapısı DEĞİL, ürün kalitesi "
        "hedefi (ayrı iş: prompt-tanım uyumu).")
    return detail
