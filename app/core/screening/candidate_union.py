# -*- coding: utf-8 -*-
"""Baseline-union-to-3B aday planı — Faz 0 / shadow / assistive TEK üreticisi.

`plan_corpus_screening_production.md` §4.2'nin çalıştırılabilir sözleşmesi.
Üç tüketici de (ücretsiz Faz 0 replay, shadow counterfactual, assistive canlı
materyalizasyon) `build_union_candidate_plan()` çağırır; ikinci bir union
uygulaması TUTULMAZ (parite kod düzeyinde garanti edilir).

Sözleşme (kanal başına):
  1. `T = min(N, multiplier * B)` hedef benzersiz aday sayısı.
  2. Baseline sırasının ilk `min(B, N)` kimliği AYNEN korunur.
  3. DeepSeek ensemble sırası baştan sona gezilir; daha önce eklenmiş
     kimlikler atlanır.
  4. Benzersiz aday sayısı tam `T` olana veya evren tükenene kadar devam
     edilir — kesişim yüzünden ERKEN DURULMAZ.
  5. Materyalizasyon sırası: baseline satırlar üretim sırasıyla `1..b`,
     screening-only satırlar DeepSeek sırasıyla `b+1..T`.

Bu modül SAF'tır (DB/HTTP yok): girdi iki sıralama + bütçe, çıktı plan +
audit alanları. Böylece sözleşme doğrudan test edilir ve üretim yolu ile
ücretsiz replay aynı kodu paylaşır.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Optional, Sequence

# ── Union sözleşme sürümleri ─────────────────────────────────────────────
# v1 (KOTA): toplam sabit T=min(N,3B); baseline-B kotayı tüketir. Faz 0
#   replay'inde KALDI (benchmark/screening/phase0_union_replay.json) —
#   tarihsel kanıt olarak korunur, üretime AÇILMAZ.
# v2 (ADDITIVE): union = baseline_topB ∪ ensemble_top3B. Toplam SABİT
#   DEĞİL: U = |union| ∈ [3B, 4B]. Ensemble top-3B'nin HİÇBİR satırı
#   baseline kotası yüzünden düşmez ("mevcut adayları koru, DeepSeek yalnız
#   EKLESİN" ürün kararının gerçek karşılığı). Codex kararı, 30.07;
#   SONUÇ GÖRÜLMEDEN donduruldu.
# v3 (KAPSAMLI ADDITIVE): kanal kapsami SONUC GORULMEDEN dondurulmustur —
#   ADS/SEO additive, SOCIAL tam olarak bugunku baseline-B yolu. Faz 0 v2
#   replay'inde SOCIAL H5'i iki veri setinde de veremedi (union 10<13 ve
#   55<61); ADS/SEO tum kapilari gecti. Bu bir ESIK ayari DEGIL, kanal
#   kapsami kararidir (Codex, 30.07).
UNION_CONTRACT_V1 = "UNION-2026-07-30-v1-quota"
UNION_CONTRACT_V2 = "UNION-2026-07-30-v2-additive"
UNION_CONTRACT_V3 = "UNION-2026-07-30-v3-scoped-additive"
UNION_MODE_QUOTA = "quota_v1"
UNION_MODE_ADDITIVE = "additive_v2"
UNION_MODE_BASELINE_ONLY = "baseline_only"
UNION_MODES = (UNION_MODE_QUOTA, UNION_MODE_ADDITIVE,
               UNION_MODE_BASELINE_ONLY)
UNION_CONTRACT_BY_MODE = {
    UNION_MODE_QUOTA: UNION_CONTRACT_V1,
    UNION_MODE_ADDITIVE: UNION_CONTRACT_V2,
    UNION_MODE_BASELINE_ONLY: UNION_CONTRACT_V3,
}
# Codex 3. tur #1: belirsiz `UNION_CONTRACT_VERSION` alias'i KALDIRILDI —
# her cagri modu/surumu ACIKCA belirtir (yanlis kunye riski kapandi).

# v3 dondurulmus kanal kapsami. Kimlik/reuse anahtari bu listeyi TASIR:
# kapsam degisirse screening kararlari yeniden kullanilamaz.
SCREENING_APPLIED_CHANNELS_V3 = ("ADS", "SEO")
SCOPED_SCREENING_CONTRACT_V3 = {
    "contract_version": UNION_CONTRACT_V3,
    "applied_screening_channels": SCREENING_APPLIED_CHANNELS_V3,
    "in_scope_mode": UNION_MODE_ADDITIVE,
    "out_of_scope_mode": UNION_MODE_BASELINE_ONLY,
    "out_of_scope_rationale": (
        "SOCIAL: Faz 0 v2'de H5 iki veri setinde de kaldi (ayni aday "
        "sayisinda yalniz baseline penceresini genisletmek daha iyi); "
        "SOCIAL prompt asiri-pozitiflik duzeltmesi AYRI is"),
    "out_of_scope_guarantee": (
        "kapsam disi kanalin aday kimlikleri VE SIRASI flag-off baseline "
        "ile BIREBIR aynidir; screening karari canli secime uygulanmaz "
        "(shadow'da audit icin saklanabilir)"),
}
DEFAULT_CANDIDATE_MULTIPLIER = 3
# Faz 0 (v1) kapısı: baseline koruması ensemble recall'ından en fazla bu
# kadar geride kalabilirdi (SONUÇ GÖRÜLMEDEN ilan edilmişti — plan §4.3).
# v2 additive sözleşmesinde tolerans YOKTUR: ensemble top-3B eksiksiz
# korunduğu için union recall'ı ensemble'ın ALTINA düşemez (kapı eşitlik).
PHASE0_RECALL_TOLERANCE = 0.05

# Expansion AI çağrıları için üretim hard bütçesi (kanal toplamı).
EXPANSION_AI_CALL_CEILING = 60

# Screening sozlesme kimligi: SOCIAL-only v4 predikati
# (app/core/screening/social_v4_contract.is_social_only_v4) bununla karsilastirir.
SCREENING_CONTRACT_V4 = "v4"

# plan §3 dondurulmuş screening sözleşmesi (artifact künyesi bununla
# doğrulanır; sapma fail-closed). SHA'lar Dijital+GR7 mühürlü
# artifact'larından alınmıştır ve İKİSİNDE DE AYNIDIR.
PRODUCTION_SCREENING_CONTRACT: Dict[str, Any] = {
    "provider": "deepseek",
    "model": "deepseek-v4-flash",
    "prompt_version": "SCR-2026-07-27-v3a",
    "temperature": 0.0,
    "batch_size": 10,
    "virtual_buckets": 128,
    "view_salts": ("scr-view-a", "scr-view-b"),
    "rank_rule": "mean",
    "tiebreak": "raw_rank -> keyword_id",
    "candidate_multiplier": DEFAULT_CANDIDATE_MULTIPLIER,
    # Codex 2. tur: sessiz drift'i kapatan ek pinler
    "reason_code_version": "RC-v1",
    "reason_codes_sha256":
        "05e1add94394e20db0d5522b37344c79490542d47ab667036e23117615c026c5",
    "response_schema_sha256":
        "7610810f540112ab30190e9f727e83f3cce1e6628292b20e244edc106b9b61d4",
    "prompt_template_sha256":
        "1ac634926f4987f69cb66f7ad58f943619e9289bc4a2bd60d0b58b7b3a0cbe03",
    "ensemble_contract_version": "ENS-2026-07-27-v1",
}

ORIGIN_BASELINE = "baseline"
ORIGIN_SCREENING = "screening"
ORIGIN_BOTH = "both"
ORIGIN_NONE = "none"


class CandidateUnionError(RuntimeError):
    """Union sözleşmesi ihlali — plan üretilmez (fail-closed)."""


def _require_id_sequence(values: Sequence[int], name: str) -> List[int]:
    if not isinstance(values, (list, tuple)):
        raise CandidateUnionError(f"{name} liste olmalı: {type(values).__name__}")
    out: List[int] = []
    seen = set()
    for idx, value in enumerate(values):
        if isinstance(value, bool) or not isinstance(value, int):
            raise CandidateUnionError(
                f"{name}[{idx}] tam sayı keyword_id olmalı: {value!r}")
        if value in seen:
            raise CandidateUnionError(
                f"{name} içinde tekrarlanan keyword_id: {value}")
        seen.add(value)
        out.append(value)
    return out


def initial_target(universe_size: int, b_initial: int,
                   multiplier: int = DEFAULT_CANDIDATE_MULTIPLIER) -> int:
    """`T = min(N, multiplier * B)`."""
    if isinstance(universe_size, bool) or not isinstance(universe_size, int) \
            or universe_size < 0:
        raise CandidateUnionError(
            f"universe_size negatif olmayan tam sayı olmalı: {universe_size!r}")
    if isinstance(b_initial, bool) or not isinstance(b_initial, int) \
            or b_initial < 1:
        raise CandidateUnionError(
            f"b_initial >= 1 tam sayı olmalı: {b_initial!r}")
    if isinstance(multiplier, bool) or not isinstance(multiplier, int) \
            or multiplier < 1:
        raise CandidateUnionError(
            f"multiplier >= 1 tam sayı olmalı: {multiplier!r}")
    return min(universe_size, multiplier * b_initial)


def screening_can_change_selection(
        universe_size: int, b_initial: int,
        multiplier: int = DEFAULT_CANDIDATE_MULTIPLIER) -> bool:
    """Küçük evren kısa devresi (plan §4.2).

    Baseline zaten hedefin tamamını kapsıyorsa screening aday kümesini
    DEĞİŞTİREMEZ; bu durumda provider çağrısı yapılmamalıdır.
    """
    target = initial_target(universe_size, b_initial, multiplier)
    return min(b_initial, universe_size) < target


def build_union_candidate_plan(
    *,
    baseline_ordering: Sequence[int],
    ensemble_ordering: Sequence[int],
    b_initial: int,
    universe_size: Optional[int] = None,
    multiplier: int = DEFAULT_CANDIDATE_MULTIPLIER,
    mode: str = UNION_MODE_ADDITIVE,
) -> Dict[str, Any]:
    """İlk aday planı (kanal başına) — iki sözleşme sürümü.

    `mode=additive_v2` (VARSAYILAN, Codex kararı): union = baseline_topB ∪
    ensemble_topT. Toplam sabit DEĞİL (`U ∈ [T, T+B]`); ensemble top-T'nin
    hiçbir satırı baseline yüzünden düşmez.
    `mode=quota_v1` (TARİHSEL): toplam tam `T`; baseline-B kotayı tüketir
    ve ensemble penceresi fiilen kırpılır — Faz 0'da KALDI.

    `baseline_ordering`: bugünkü üretim sırası (relevance etkinse
    `adjusted_score DESC -> raw rank -> keyword_id`, değilse raw rank).
    `ensemble_ordering`: DeepSeek ensemble sırası (`passing -> mean_fit ->
    raw rank -> keyword_id`). İkisi de TAM evreni kapsamalıdır.
    """
    if mode not in UNION_MODES:
        raise CandidateUnionError(
            f"bilinmeyen union modu: {mode!r} (izinli: {UNION_MODES})")
    baseline = _require_id_sequence(baseline_ordering, "baseline_ordering")
    ensemble = _require_id_sequence(ensemble_ordering, "ensemble_ordering")
    if set(baseline) != set(ensemble):
        only_base = sorted(set(baseline) - set(ensemble))[:5]
        only_ens = sorted(set(ensemble) - set(baseline))[:5]
        raise CandidateUnionError(
            "baseline ve ensemble sıralamaları AYNI evreni kapsamalı — "
            f"yalnız baseline'da {len(set(baseline) - set(ensemble))} "
            f"(örn {only_base}), yalnız ensemble'da "
            f"{len(set(ensemble) - set(baseline))} (örn {only_ens})")
    n = len(baseline)
    if universe_size is not None and universe_size != n:
        raise CandidateUnionError(
            f"universe_size {universe_size} sıralama uzunluğu {n} ile "
            f"uyuşmuyor")
    baseline_only = mode == UNION_MODE_BASELINE_ONLY
    # Kapsam disi kanal: hedef TAM OLARAK bugunku baseline-B penceresi
    target = (min(b_initial, n) if baseline_only
              else initial_target(n, b_initial, multiplier))
    baseline_slots = min(b_initial, n)

    baseline_rank = {kid: i for i, kid in enumerate(baseline, start=1)}
    screening_rank = {kid: i for i, kid in enumerate(ensemble, start=1)}
    baseline_selected = baseline[:baseline_slots]
    baseline_selected_set = set(baseline_selected)
    # Screening referansı: SAF screening aynı bütçede ne seçerdi.
    # Kapsam disi kanalda screening HIC KULLANILMAZ (bos referans).
    screening_reference = [] if baseline_only else ensemble[:target]
    screening_reference_set = set(screening_reference)

    selected: List[Dict[str, Any]] = []
    chosen = set()

    def _origin(kid: int) -> str:
        in_base = kid in baseline_selected_set
        in_scr = kid in screening_reference_set
        if in_base and in_scr:
            return ORIGIN_BOTH
        if in_base:
            return ORIGIN_BASELINE
        if in_scr:
            return ORIGIN_SCREENING
        return ORIGIN_NONE

    for kid in baseline_selected:
        chosen.add(kid)
        selected.append({
            "keyword_id": kid,
            "initial_rank": len(selected) + 1,
            "origin_source": _origin(kid),
            "baseline_rank": baseline_rank[kid],
            "screening_rank": screening_rank[kid],
            "from_baseline_slot": True,
        })

    walk_depth = 0
    if baseline_only:
        pass          # ensemble hic gezilmez
    elif mode == UNION_MODE_ADDITIVE:
        # ADDITIVE: ensemble top-T'nin TAMAMI eklenir; toplam T'yi aşabilir
        for position, kid in enumerate(screening_reference, start=1):
            walk_depth = position
            if kid in chosen:
                continue
            chosen.add(kid)
            selected.append({
                "keyword_id": kid,
                "initial_rank": len(selected) + 1,
                "origin_source": _origin(kid),
                "baseline_rank": baseline_rank[kid],
                "screening_rank": screening_rank[kid],
                "from_baseline_slot": False,
            })
    else:
        # QUOTA (v1, tarihsel): toplam tam T — baseline kotayı tüketir
        for position, kid in enumerate(ensemble, start=1):
            if len(selected) >= target:
                break
            walk_depth = position
            if kid in chosen:
                continue
            chosen.add(kid)
            selected.append({
                "keyword_id": kid,
                "initial_rank": len(selected) + 1,
                "origin_source": _origin(kid),
                "baseline_rank": baseline_rank[kid],
                "screening_rank": screening_rank[kid],
                "from_baseline_slot": False,
            })

    additive = mode == UNION_MODE_ADDITIVE
    expected_size = (len(baseline_selected_set | screening_reference_set)
                     if additive else target)

    # ── İnvaryantlar: sözleşme delinirse plan ÜRETİLMEZ ──────────────
    if len(selected) != expected_size:
        raise CandidateUnionError(
            f"İNVARYANT [{mode}]: benzersiz aday {len(selected)} != beklenen "
            f"{expected_size}")
    if len({row["keyword_id"] for row in selected}) != expected_size:
        raise CandidateUnionError("İNVARYANT: union içinde duplicate kimlik")
    if additive:
        # Ensemble top-T EKSİKSİZ korunmalı (kota düşürmesi yasak)
        missing_ens = [kid for kid in screening_reference
                       if kid not in chosen]
        if missing_ens:
            raise CandidateUnionError(
                f"İNVARYANT [additive]: ensemble top-{target}'ten "
                f"{len(missing_ens)} kimlik union dışında "
                f"(örn {missing_ens[:5]})")
        extra = [kid for kid in chosen
                 if kid not in baseline_selected_set
                 and kid not in screening_reference_set]
        if extra:
            raise CandidateUnionError(
                f"İNVARYANT [additive]: sözleşme dışı {len(extra)} ekstra "
                f"kimlik (örn {extra[:5]})")
        if not (target <= expected_size <= target + baseline_slots):
            raise CandidateUnionError(
                f"İNVARYANT [additive]: U={expected_size} beklenen "
                f"[{target}, {target + baseline_slots}] aralığında değil")
    missing_baseline = [kid for kid in baseline_selected if kid not in chosen]
    if missing_baseline:
        raise CandidateUnionError(
            f"İNVARYANT: baseline-B korunmadı — {len(missing_baseline)} kimlik "
            f"union dışında (örn {missing_baseline[:5]})")
    if any(row["origin_source"] == ORIGIN_NONE for row in selected):
        # Ensemble yürüyüşü hedefe `target` konumundan ÖNCE ulaşır
        # (kesişim ≤ baseline_slots), bu yüzden seçilen her satır ya
        # baseline-B'de ya ensemble top-T'dedir.
        raise CandidateUnionError(
            "İNVARYANT: initial sette kaynağı belirsiz ('none') satır var")
    if walk_depth > target:
        raise CandidateUnionError(
            f"İNVARYANT: ensemble yürüyüş derinliği {walk_depth} > T {target}")
    if baseline_only:
        # Kapsam disi garanti: kimlikler VE SIRA flag-off baseline ile birebir
        if [row["keyword_id"] for row in selected] != list(baseline_selected):
            raise CandidateUnionError(
                "İNVARYANT [baseline_only]: aday sırası flag-off baseline "
                "ile birebir değil")
        if screening_reference or walk_depth:
            raise CandidateUnionError(
                "İNVARYANT [baseline_only]: screening referansı/yürüyüşü "
                "olmamalı")
    if additive and walk_depth != min(target, n):
        raise CandidateUnionError(
            f"İNVARYANT [additive]: ensemble top-{target} tam gezilmedi "
            f"(derinlik {walk_depth})")

    both = sum(1 for r in selected if r["origin_source"] == ORIGIN_BOTH)
    baseline_only = sum(1 for r in selected
                        if r["origin_source"] == ORIGIN_BASELINE)
    screening_only = sum(1 for r in selected
                         if r["origin_source"] == ORIGIN_SCREENING)
    # Baseline koruması yüzünden saf-ensemble top-T dışında kalanlar
    dropouts = [kid for kid in screening_reference if kid not in chosen]

    return {
        "contract_version": UNION_CONTRACT_BY_MODE[mode],
        "mode": mode,
        "universe_size": n,
        "b_initial": b_initial,
        "baseline_slots": baseline_slots,
        "multiplier": multiplier,
        "target": target,
        "union_size": expected_size,
        "selected": selected,
        "selected_ids": [r["keyword_id"] for r in selected],
        "baseline_selected_ids": list(baseline_selected),
        "screening_reference_ids": list(screening_reference),
        "screening_fill_ids": [r["keyword_id"] for r in selected
                               if not r["from_baseline_slot"]],
        "baseline_protected_dropout_ids": dropouts,
        "ensemble_walk_depth": walk_depth,
        "counts": {
            "initial_materialized_candidates": expected_size,
            "baseline_preserved": baseline_slots,
            "screening_added": expected_size - baseline_slots,
            "ensemble_reference_size": len(screening_reference),
            "origin_baseline_only": baseline_only,
            "origin_screening_only": screening_only,
            "origin_both": both,
            "baseline_protected_dropouts": len(dropouts),
        },
    }


def _reach(ordering: Sequence[int], positives: Iterable[int],
           budget: int) -> Dict[str, Any]:
    pos = set(positives)
    reached = sum(1 for kid in ordering[:budget] if kid in pos)
    total = len(pos)
    return {
        "budget": budget,
        "reached": reached,
        "recall": round(reached / total, 4) if total else None,
    }


def union_reach_report(
    plan: Dict[str, Any],
    *,
    baseline_ordering: Sequence[int],
    ensemble_ordering: Sequence[int],
    positives: Iterable[int],
) -> Dict[str, Any]:
    """Kanal başına Faz 0 raporu (plan §4.3 kalemleri).

    Kapı metrikleri:
      - `union.reached` vs `baseline_top_T.reached`
      - `union.recall` vs `ensemble_top_T.recall` (tolerans PHASE0_*)
    """
    pos = set(positives)
    target = plan["target"]
    b = plan["baseline_slots"]
    union_ids = plan["selected_ids"]
    union_size = plan.get("union_size", len(union_ids))
    union_reached = sum(1 for kid in union_ids if kid in pos)
    total = len(pos)
    baseline_selected = set(plan["baseline_selected_ids"])
    screening_ref = set(plan["screening_reference_ids"])

    return {
        "positives": total,
        "union_size": union_size,
        "baseline_B": _reach(baseline_ordering, pos, b),
        "baseline_top_T": _reach(baseline_ordering, pos, target),
        # Codex v2 kapısı: AYNI aday sayısında yalnız pencereyi büyütmek
        # daha iyi olur muydu? (dürüst karşı-olgusal)
        "baseline_top_U": _reach(baseline_ordering, pos, union_size),
        "ensemble_top_T": _reach(ensemble_ordering, pos, target),
        "union": {
            "budget": union_size,
            "reached": union_reached,
            "recall": round(union_reached / total, 4) if total else None,
        },
        "positive_source_split": {
            "baseline_only": len([kid for kid in pos
                                  if kid in baseline_selected
                                  and kid not in screening_ref]),
            "screening_only": len([kid for kid in pos
                                   if kid in screening_ref
                                   and kid not in baseline_selected]),
            "both": len([kid for kid in pos if kid in baseline_selected
                         and kid in screening_ref]),
            "neither": len([kid for kid in pos
                            if kid not in baseline_selected
                            and kid not in screening_ref]),
        },
        "delta_vs_ensemble": {
            "reached": union_reached - _reach(ensemble_ordering, pos,
                                              target)["reached"],
            "recall": (round(
                (union_reached / total)
                - (_reach(ensemble_ordering, pos, target)["reached"] / total),
                4) if total else None),
        },
        "delta_vs_baseline_top_T": {
            "reached": union_reached - _reach(baseline_ordering, pos,
                                              target)["reached"],
        },
        "baseline_protected_dropouts": {
            "count": plan["counts"]["baseline_protected_dropouts"],
            "positive_count": len(
                [kid for kid in plan["baseline_protected_dropout_ids"]
                 if kid in pos]),
            "positive_ids": [kid for kid
                             in plan["baseline_protected_dropout_ids"]
                             if kid in pos],
        },
        "counts": dict(plan["counts"]),
    }


def evaluate_union_gates(
    blocks: Sequence[Dict[str, Any]],
    *,
    tolerance: float = PHASE0_RECALL_TOLERANCE,
) -> Dict[str, Any]:
    """Faz 0 kapıları (SONUÇ GÖRÜLMEDEN ilan edildi — plan §4.3).

    `blocks`: her biri `{dataset, channel, plan, report}` taşıyan kayıtlar.
    Kapı:
      G0  initial benzersiz aday sayısı tam `min(N, 3B)`
      G1  `union.reached >= baseline_top_T.reached`
      G2  `union.recall >= ensemble_top_T.recall - tolerance`
      G3  baseline-B'nin TÜM kimlikleri union içinde
    Eksik/None metrik fail-closed ihlaldir (sessiz geçiş yok).
    """
    if not blocks:
        raise CandidateUnionError("kapı değerlendirmesi için blok yok")
    violations: List[str] = []
    detail: Dict[str, Any] = {"tolerance": tolerance, "blocks": {}}
    for block in blocks:
        key = f"{block['dataset']}/{block['channel']}"
        plan = block["plan"]
        rep = block["report"]
        expected_target = initial_target(plan["universe_size"],
                                         plan["b_initial"],
                                         plan["multiplier"])
        g0 = (plan["counts"]["initial_materialized_candidates"]
              == expected_target == len(plan["selected_ids"]))
        if not g0:
            violations.append(
                f"G0 {key}: initial aday "
                f"{plan['counts']['initial_materialized_candidates']} != "
                f"beklenen {expected_target}")
        base_reached = rep["baseline_top_T"]["reached"]
        union_reached = rep["union"]["reached"]
        g1 = union_reached >= base_reached
        if not g1:
            violations.append(
                f"G1 {key}: union erişimi {union_reached} < baseline@T "
                f"{base_reached}")
        ens_recall = rep["ensemble_top_T"]["recall"]
        union_recall = rep["union"]["recall"]
        if ens_recall is None or union_recall is None:
            raise CandidateUnionError(
                f"G2 {key}: recall hesaplanamadı (pozitif yok?) — "
                f"fail-closed")
        floor = round(ens_recall - tolerance, 6)
        g2 = union_recall >= floor
        if not g2:
            violations.append(
                f"G2 {key}: union recall {union_recall} < taban {floor} "
                f"(ensemble {ens_recall} - {tolerance})")
        missing = [kid for kid in plan["baseline_selected_ids"]
                   if kid not in set(plan["selected_ids"])]
        g3 = not missing
        if not g3:
            violations.append(
                f"G3 {key}: baseline-B'den {len(missing)} kimlik union "
                f"dışında")
        detail["blocks"][key] = {
            "G0_initial_target_exact": g0,
            "G1_union_ge_baseline_at_T": g1,
            "G2_union_recall_within_tolerance": g2,
            "G3_baseline_preserved": g3,
            "union_reached": union_reached,
            "baseline_top_T_reached": base_reached,
            "ensemble_top_T_reached": rep["ensemble_top_T"]["reached"],
            "union_recall": union_recall,
            "ensemble_recall": ens_recall,
            "recall_floor": floor,
        }
    detail["violations"] = violations
    detail["passed"] = not violations
    return detail


def evaluate_union_gates_v2(
    blocks: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    """FAZ 0 v2 (ADDITIVE) kapıları — SONUÇ GÖRÜLMEDEN dondurulmuştur.

    Codex kararı (30.07). Kapılar:
      H0  union kümesi TAM OLARAK `baseline_topB ∪ ensemble_topT`
          (duplicate yok, sözleşme dışı ekstra kimlik yok)
      H1  baseline-B EKSİKSİZ korunur
      H2  ensemble top-T EKSİKSİZ korunur (kota düşürmesi yasak)
      H3  `U ∈ [T, T+B]` ve `U == |baseline_topB ∪ ensemble_topT|`
      H4  union erişimi GERÇEK baseline-B erişiminden düşük olamaz
      H5  union erişimi, AYNI aday sayısındaki `baseline_top_U`
          erişiminden düşük olamaz ("yalnız pencereyi büyütmek daha iyi
          miydi?" testi)
      H6  union recall >= ensemble top-T recall (tolerans YOK — additive
          sözleşmede ensemble eksiksiz korunduğu için matematiksel olarak
          sağlanmalıdır; ihlal implementasyon bozulmasıdır)
    Eksik metrik fail-closed.
    """
    if not blocks:
        raise CandidateUnionError("kapı değerlendirmesi için blok yok")
    violations: List[str] = []
    detail: Dict[str, Any] = {"contract_version": UNION_CONTRACT_V2,
                              "tolerance": 0.0, "blocks": {}}
    for block in blocks:
        key = f"{block['dataset']}/{block['channel']}"
        plan = block["plan"]
        rep = block["report"]
        if plan.get("mode") != UNION_MODE_ADDITIVE:
            raise CandidateUnionError(
                f"{key}: v2 kapıları yalnız additive plana uygulanır "
                f"(mod: {plan.get('mode')!r})")
        selected = set(plan["selected_ids"])
        baseline_set = set(plan["baseline_selected_ids"])
        ensemble_set = set(plan["screening_reference_ids"])
        expected_union = baseline_set | ensemble_set
        t, b = plan["target"], plan["baseline_slots"]
        u = plan["union_size"]

        h0 = (selected == expected_union
              and len(plan["selected_ids"]) == len(selected))
        if not h0:
            violations.append(
                f"H0 {key}: union kümesi baseline∪ensemble değil "
                f"(fazla {len(selected - expected_union)}, "
                f"eksik {len(expected_union - selected)})")
        h1 = baseline_set <= selected
        if not h1:
            violations.append(
                f"H1 {key}: baseline-B'den {len(baseline_set - selected)} "
                f"kimlik union dışında")
        h2 = ensemble_set <= selected
        if not h2:
            violations.append(
                f"H2 {key}: ensemble top-{t}'ten "
                f"{len(ensemble_set - selected)} kimlik union dışında")
        h3 = (u == len(expected_union) == len(selected)
              and t <= u <= t + b)
        if not h3:
            violations.append(
                f"H3 {key}: U={u} sözleşme aralığı [{t}, {t + b}] veya "
                f"küme boyutu ({len(expected_union)}) ile tutarsız")
        base_b = rep["baseline_B"]["reached"]
        base_u = rep["baseline_top_U"]["reached"]
        ens_t = rep["ensemble_top_T"]["reached"]
        union_reached = rep["union"]["reached"]
        h4 = union_reached >= base_b
        if not h4:
            violations.append(
                f"H4 {key}: union erişimi {union_reached} < baseline-B "
                f"{base_b}")
        h5 = union_reached >= base_u
        if not h5:
            violations.append(
                f"H5 {key}: union erişimi {union_reached} < aynı aday "
                f"sayısındaki baseline@U {base_u} — bu bütçede yalnız "
                f"pencereyi büyütmek daha iyiydi")
        ens_recall = rep["ensemble_top_T"]["recall"]
        union_recall = rep["union"]["recall"]
        if ens_recall is None or union_recall is None:
            raise CandidateUnionError(
                f"H6 {key}: recall hesaplanamadı — fail-closed")
        h6 = union_recall >= ens_recall
        if not h6:
            violations.append(
                f"H6 {key}: union recall {union_recall} < ensemble "
                f"{ens_recall} (additive sözleşmede imkânsız — bozulma)")
        detail["blocks"][key] = {
            "H0_union_is_exact_set_union": h0,
            "H1_baseline_preserved": h1,
            "H2_ensemble_preserved": h2,
            "H3_union_size_in_contract_range": h3,
            "H4_union_ge_baseline_B": h4,
            "H5_union_ge_baseline_at_U": h5,
            "H6_union_recall_ge_ensemble": h6,
            "T": t, "B": b, "U": u,
            "union_reached": union_reached,
            "baseline_B_reached": base_b,
            "baseline_at_U_reached": base_u,
            "ensemble_top_T_reached": ens_t,
            "union_recall": union_recall,
            "ensemble_recall": ens_recall,
        }
    detail["violations"] = violations
    detail["passed"] = not violations
    return detail


def evaluate_union_gates_v3(
    blocks: Sequence[Dict[str, Any]],
    *,
    applied_channels: Sequence[str] = SCREENING_APPLIED_CHANNELS_V3,
) -> Dict[str, Any]:
    """FAZ 0 v3 (KAPSAMLI) kapıları — SONUÇ GÖRÜLMEDEN dondurulmuştur.

    Kapsam İÇİ kanallar (ADS/SEO): v2 additive kapıları H0-H6 aynen.
    Kapsam DIŞI kanallar (SOCIAL): screening canlı seçime dokunamaz —
      K0  mod `baseline_only`
      K1  aday kimlikleri VE SIRASI flag-off baseline-B ile BİREBİR
      K2  screening referansı/eklemesi/dropout'u YOK
      K3  `U == T == min(B, N)`
    Kapsam listesi dondurulmuş sabitten SAPAMAZ (fail-closed).
    """
    if not blocks:
        raise CandidateUnionError("kapı değerlendirmesi için blok yok")
    scope = tuple(applied_channels)
    if scope != SCREENING_APPLIED_CHANNELS_V3:
        raise CandidateUnionError(
            f"v3 kapsamı dondurulmuş sabitten sapıyor: {scope} != "
            f"{SCREENING_APPLIED_CHANNELS_V3}")
    in_scope = [b for b in blocks if b["channel"] in scope]
    out_scope = [b for b in blocks if b["channel"] not in scope]
    if not in_scope:
        raise CandidateUnionError("kapsam içi blok yok — v3 anlamsız")

    detail = evaluate_union_gates_v2(in_scope)
    detail["contract_version"] = UNION_CONTRACT_V3
    detail["applied_screening_channels"] = list(scope)
    violations = list(detail["violations"])
    for block in out_scope:
        key = f"{block['dataset']}/{block['channel']}"
        plan = block["plan"]
        k0 = plan.get("mode") == UNION_MODE_BASELINE_ONLY
        if not k0:
            violations.append(
                f"K0 {key}: kapsam dışı kanal `baseline_only` modunda "
                f"değil (mod: {plan.get('mode')!r})")
        expected_ids = list(plan["baseline_selected_ids"])
        k1 = list(plan["selected_ids"]) == expected_ids
        if not k1:
            violations.append(
                f"K1 {key}: aday kimlikleri/sırası flag-off baseline ile "
                f"birebir değil")
        k2 = (not plan["screening_reference_ids"]
              and not plan["screening_fill_ids"]
              and not plan["baseline_protected_dropout_ids"])
        if not k2:
            violations.append(
                f"K2 {key}: kapsam dışı kanalda screening izi var")
        k3 = (plan["union_size"] == plan["target"]
              == min(plan["b_initial"], plan["universe_size"]))
        if not k3:
            violations.append(
                f"K3 {key}: U/T baseline-B ile eşit değil "
                f"(U={plan['union_size']}, T={plan['target']})")
        detail["blocks"][key] = {
            "scope": "out_of_scope",
            "K0_baseline_only_mode": k0,
            "K1_bit_identical_with_flag_off": k1,
            "K2_no_screening_trace": k2,
            "K3_size_equals_baseline_B": k3,
            "T": plan["target"], "B": plan["baseline_slots"],
            "U": plan["union_size"],
            "union_reached": (block["report"]["union"]["reached"]
                              if block.get("report") else None),
            "baseline_B_reached": (block["report"]["baseline_B"]["reached"]
                                   if block.get("report") else None),
        }
    for key in list(detail["blocks"]):
        detail["blocks"][key].setdefault("scope", "in_scope")
    detail["violations"] = violations
    detail["passed"] = not violations
    return detail


def verify_screening_artifact_contract(
    manifest: Dict[str, Any],
    *,
    contract: Dict[str, Any] = PRODUCTION_SCREENING_CONTRACT,
) -> Dict[str, Any]:
    """Mühürlü screening artifact künyesi üretim sözleşmesiyle birebir mi.

    Sapma fail-closed: eski/yanlış prompt sürümü, batch, sıcaklık, kova
    veya görünüm tuzlarıyla üretilmiş bir artifact Faz 0 kapısına giremez.
    """
    extra = manifest.get("extra") or {}
    observed = {
        "provider": manifest.get("provider"),
        "model": manifest.get("model"),
        "prompt_version": (extra.get("prompt_version_used")
                           or manifest.get("prompt_version")),
        "temperature": manifest.get("temperature"),
        "batch_size": manifest.get("batch_size"),
        "virtual_buckets": extra.get("virtual_buckets"),
        "view_salts": tuple(extra.get("plan_salts") or ()),
        # Codex 2. tur: sessiz drift kapatıldı — şema/reason-code/prompt
        # gövdesi ve ensemble sürümü de künyeden ZORLANIR
        "reason_code_version": manifest.get("reason_code_version"),
        "reason_codes_sha256": manifest.get("reason_codes_sha256"),
        "response_schema_sha256": manifest.get("response_schema_sha256"),
        "prompt_template_sha256": manifest.get("prompt_template_sha256"),
        "ensemble_contract_version": extra.get("ensemble_contract_version"),
    }
    mismatches = []
    for field in ("provider", "model", "prompt_version", "batch_size",
                  "virtual_buckets", "view_salts", "reason_code_version",
                  "reason_codes_sha256", "response_schema_sha256",
                  "prompt_template_sha256", "ensemble_contract_version"):
        expected = contract[field]
        if observed[field] != expected:
            mismatches.append(f"{field}: artifact={observed[field]!r} "
                              f"sözleşme={expected!r}")
    try:
        temp_ok = float(observed["temperature"]) == float(
            contract["temperature"])
    except (TypeError, ValueError):
        temp_ok = False
    if not temp_ok:
        mismatches.append(f"temperature: artifact={observed['temperature']!r} "
                          f"sözleşme={contract['temperature']!r}")
    if mismatches:
        raise CandidateUnionError(
            "screening artifact künyesi üretim sözleşmesiyle uyuşmuyor: "
            + "; ".join(mismatches))
    return observed


def downstream_request_plan(
    *,
    per_channel_targets: Dict[str, int],
    seo_capacity: int,
    intent_batch: int,
    prefilter_batch: int,
    brand_batch: int,
    metadata_batch: int,
    brand_filter_active: bool,
    expansion_call_ceiling: int = EXPANSION_AI_CALL_CEILING,
) -> Dict[str, Any]:
    """Gemini downstream istek sayısı — KANAL BAŞINA (Codex düzeltmesi).

    Üretim batching gerçeği (`channel_engine`):
      - intent: her kanal için ayrı `ceil(aday/INTENT_BATCH_SIZE)`
      - brand filter: intent'i GEÇEN DISTINCT kelime üzerinden
        `ceil(n/BRAND_FILTER_BATCH_SIZE)` (yalnız exclude_themes varsa)
      - prefilter: ADS ve SOCIAL için ayrı; SEO deterministik (AI yok)
      - seo metadata: final SEO havuzu üzerinden `ceil(n/…)`
    AI sonucuna bağlı adımlarda KONSERVATİF üst sınır kullanılır (tüm
    adayların geçtiği varsayımı) — planlama zarfı bilinçli olarak yüksektir.
    """
    intent = {ch: math.ceil(n / intent_batch) if n else 0
              for ch, n in per_channel_targets.items()}
    distinct_upper = max(per_channel_targets.values()) if per_channel_targets \
        else 0
    union_upper = sum(per_channel_targets.values())
    requests = {
        "intent_per_channel": intent,
        "intent_total": sum(intent.values()),
        "ads_prefilter": math.ceil(per_channel_targets.get("ADS", 0)
                                   / prefilter_batch)
        if per_channel_targets.get("ADS") else 0,
        "social_prefilter": math.ceil(per_channel_targets.get("SOCIAL", 0)
                                      / prefilter_batch)
        if per_channel_targets.get("SOCIAL") else 0,
        "seo_prefilter": 0,   # deterministik — AI çağrısı yok
        "seo_metadata": math.ceil(seo_capacity / metadata_batch)
        if seo_capacity else 0,
    }
    if brand_filter_active:
        requests["brand_filter"] = math.ceil(union_upper / brand_batch) \
            if union_upper else 0
    else:
        requests["brand_filter"] = 0
    requests["initial_total"] = (
        requests["intent_total"] + requests["brand_filter"]
        + requests["ads_prefilter"] + requests["social_prefilter"]
        + requests["seo_metadata"])
    # Codex 2. tur #1: transfer ve expansion preflight ÜST SINIRINA DAHİL
    # (plan §4.2 "sessizce 3B maliyeti dışında bırakılmaz").
    # Transfer: ADS'ten elenip SEO'ya aktarılanlar GERÇEK intent alır
    # (channel_engine._process_cross_channel_transfers -> analyze_candidates);
    # hacim üretimde SINIRSIZDIR, en kötü durum TÜM ADS adaylarıdır. SEO
    # prefilter deterministiktir (AI yok).
    transfer_upper = (math.ceil(per_channel_targets.get("ADS", 0)
                                / intent_batch)
                      if per_channel_targets.get("ADS") else 0)
    # Expansion: kanal toplamı için üretim HARD bütçesi (AiCallBudget)
    expansion_upper = int(expansion_call_ceiling)
    requests["transfer_intent_upper"] = transfer_upper
    requests["expansion_ai_upper"] = expansion_upper
    requests["upper_total"] = (requests["initial_total"] + transfer_upper
                               + expansion_upper)
    # Geriye uyumlu ad: "total" = ilk materyalizasyon (transfer/expansion
    # hariç) — tüketiciler upper_total'ı AYRICA raporlamalıdır
    requests["total"] = requests["initial_total"]
    return {
        "requests": requests,
        "basis": {
            "intent": "kanal başına ceil(T_kanal/%d) — birleşik evrenden "
                      "TEK KEZ sayılmaz" % intent_batch,
            "brand_filter": ("intent'i geçen distinct kelime; konservatif "
                             "üst sınır = kanal hedeflerinin toplamı "
                             "(%d'lik batch)" % brand_batch)
            if brand_filter_active else "exclude_themes boş — AI çağrısı yok",
            "prefilter": "ADS/SOCIAL için ceil(T/%d); SEO deterministik"
                         % prefilter_batch,
            "seo_metadata": "final SEO havuzu (kapasite) / %d" % metadata_batch,
            "conservative": ("AI sonucuna bağlı adımlarda tüm adayların "
                             "geçtiği varsayıldı — üst sınır"),
            "transfer": ("ADS->SEO transferi GERÇEK intent alır; hacim "
                         "üretimde sınırsız, en kötü durum TÜM ADS adayı "
                         "= ceil(T_ADS/%d). SEO prefilter deterministik."
                         % intent_batch),
            "expansion": ("kanal toplamı için üretim hard bütçesi = %d AI "
                          "çağrısı (AiCallBudget); en kötü durum tamamı"
                          % expansion_call_ceiling),
            "totals": ("initial_total = ilk materyalizasyon; upper_total = "
                       "initial + transfer + expansion (preflight ÜST "
                       "SINIRI — plan §4.2 gereği ikisi de raporlanır)"),
            "distinct_upper_note": ("tek kanalın en büyük hedefi %d; "
                                    "kanallar arası kesişim bilinmediği için "
                                    "brand filter üst sınırı toplamdan "
                                    "hesaplandı" % distinct_upper),
        },
    }
