"""GR7 holdout + üretim tekrarlanabilirlik testi — test edilebilir çekirdek.

Codex 4. tur dersleri bu modülün varlık sebebidir:
- İlk holdout artifact'ları SALT OKUNURDUR: hiçbir koşu yolu onların
  üzerine yazamaz (`guard_no_overwrite`); tekrar çıktıları AYRI adlara gider.
- Tekrar koşusu ilk artifact'ın sözleşmesini ve dondurulmuş girdilerini
  DOĞRULAR (`verify_contract_match`, `verify_same_frozen_inputs`) — uyuşmayan
  girdiyle sessizce karşılaştırma yapılamaz.
- Esas metrik ENSEMBLE↔ENSEMBLE'dır: `E1 = ensemble(A1,B1)`,
  `E2 = ensemble(A2,B2)`, `Jaccard(E1@3B, E2@3B)` (`repeat_comparison`).
  Tek-görünüm A↔B uyuşması ÜRÜN kararının kararlılığı değildir (G3 dersi).
- Kararlılık eşiği AYNI metrik üzerinde, Dijital'in ücretsiz ensemble
  full↔repeat ölçümünden ÖNCEDEN belirlenir; koşu sonrası değiştirilemez.

Script'ler (`scripts/run_screening_holdout.py`, `scripts/run_holdout_repeat.py`)
ince CLI sarmalayıcılarıdır; davranış burada yaşar ve doğrudan test edilir
(`tests/unit/test_holdout_runner.py`).
"""
from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any, Dict, List, Sequence

from app.core.screening.ensemble import (
    ENSEMBLE_CONTRACT_VERSION,
    PLAN_SALTS,
    VIRTUAL_BUCKETS,
    ensemble_flip_rate,
    ensemble_ordering,
    merge_multi_views,
)
from app.core.screening.manifest import validate_artifact
from app.core.screening.metrics import candidate_recall_at

CHANNELS = ("ADS", "SEO", "SOCIAL")

# ── KİLİTLİ HOLDOUT SÖZLEŞMESİ (a925f78'de dondurulan değerler AYNEN) ────
HOLDOUT_CONTRACT = {
    "name": "HOLDOUT-GR7-2026-07-28",
    "model": "deepseek-v4-flash",
    "prompt_version": "SCR-2026-07-27-v3a",
    "temperature": 0.0,
    "batch_size": 10,
    "views": list(PLAN_SALTS),               # iki sticky görünüm
    "virtual_buckets": VIRTUAL_BUCKETS,
    "ensemble": "mean-fit; eleme yalnız tüm görünümler 0 derse",
    "tiebreak": "raw_rank -> keyword_id",
    "budget_multiplier": 3,                  # ÖNCEDEN seçildi (Dijital B eğrisi dirseği)
    "gates": {
        "G1_positive_kept_min": 0.90,
        "G2": "ensemble recall@3B > raw_rank baseline@3B (her kanal)",
        "G3_view_jaccard_at_3B_min": 0.80,
        "G4": "unresolved == 0 ve ihlal == 0",
    },
}


class HoldoutError(RuntimeError):
    """Holdout güvenlik sözleşmesi ihlali — koşu reddedilir."""


def guard_no_overwrite(paths: Sequence[str]) -> None:
    """Var olan artifact'ların üzerine yazmayı YAPISAL olarak engeller.

    Codex 4. tur #1: `--allow-second-run` ilk koşunun kanıtını siliyordu.
    Artık hiçbir koşu yolu mevcut bir çıktının üzerine yazamaz; tekrar
    çıktıları ayrı adlar kullanmak ZORUNDADIR.
    """
    existing = [p for p in paths if os.path.exists(p)]
    if existing:
        raise HoldoutError(
            "REDDEDİLDİ — şu çıktı dosyaları zaten var ve üzerine yazmak "
            f"ilk koşunun kanıtını siler: {existing}. Tekrar koşusu için "
            "ayrı adlar kullanın (ör. *_repeat.json)."
        )


def view_rows_from_artifact(doc: Dict[str, Any], slug: str,
                            scenario: str = "full") -> Dict[str, List[Any]]:
    """Mühürlü sticky-views artifact'ından A/B görünüm satırlarını kurar.

    Artifact önce doğrulanır (mühür + künye + canlı sözleşme). Kaynak doc
    HİÇ değiştirilmez (salt okunur kullanım).
    """
    validate_artifact(doc)
    scenarios = ((doc.get("report") or {}).get(slug) or {}).get(
        "merged_results") or {}
    if scenario not in scenarios:
        raise HoldoutError(
            f"artifact '{slug}/{scenario}' senaryosunu taşımıyor; "
            f"mevcut: {sorted(scenarios)}"
        )
    out: Dict[str, List[Any]] = {}
    for label in ("A", "B"):
        out[label] = [SimpleNamespace(
            keyword_id=r["keyword_id"], keyword=r["keyword"],
            ads_fit=r["raw_views"][label]["ads"],
            seo_fit=r["raw_views"][label]["seo"],
            social_fit=r["raw_views"][label]["social"],
            unresolved=label in (r.get("unresolved_views") or []))
            for r in scenarios[scenario]]
    return out


def verify_contract_match(doc: Dict[str, Any],
                          contract: Dict[str, Any]) -> None:
    """İlk artifact'ın künyesi kilitli sözleşmeyle birebir örtüşmeli.

    Uyuşmayan sözleşmeyle karşılaştırma anlamsızdır (elma-armut) ve
    fail-closed reddedilir; TÜM uyumsuzluklar birlikte raporlanır.
    """
    manifest = doc.get("manifest") or {}
    extra = manifest.get("extra") or {}
    checks = [
        ("model", manifest.get("model"), contract["model"]),
        ("prompt_version", manifest.get("prompt_version"),
         contract["prompt_version"]),
        ("temperature", manifest.get("temperature"), contract["temperature"]),
        ("batch_size", manifest.get("batch_size"), contract["batch_size"]),
        ("plan_salts", list(extra.get("plan_salts") or []),
         list(contract["views"])),
        ("virtual_buckets", extra.get("virtual_buckets"),
         contract["virtual_buckets"]),
    ]
    # Codex 5. tur #2: yalnız çekirdek parametreler yetmez.
    # (a) Artifact ensemble sözleşme sürümü taşıyorsa canlı sürümle aynı
    #     olmalı (birleştirme/eleme kuralları değişmiş olabilir).
    if extra.get("ensemble_contract_version") is not None:
        checks.append(("ensemble_contract_version",
                       extra["ensemble_contract_version"],
                       ENSEMBLE_CONTRACT_VERSION))
    # (b) Artifact holdout sözleşmesinin TAMAMINI gömdüyse birebir eşitlik
    #     aranır — ensemble/tiebreak/budget_multiplier/gates dahil.
    embedded = extra.get("holdout_contract",
                         extra.get("original_holdout_contract"))
    if isinstance(embedded, dict):
        for key in sorted(set(embedded) | set(contract)):
            if embedded.get(key) != contract.get(key):
                checks.append((f"holdout_contract.{key}",
                               embedded.get(key), contract.get(key)))
    mismatches = [f"{name}: artifact={got!r} != sözleşme={want!r}"
                  for name, got, want in checks if got != want]
    if mismatches:
        raise HoldoutError(
            "SÖZLEŞME UYUŞMAZLIĞI — karşılaştırma reddedildi: "
            + "; ".join(mismatches)
        )


def verify_same_frozen_inputs(doc: Dict[str, Any], *, universe_sha256: str,
                              context_sha256: str) -> None:
    """İlk artifact'ın dondurulmuş girdileri canlı girdilerle aynı olmalı."""
    extra = (doc.get("manifest") or {}).get("extra") or {}
    problems = []
    if extra.get("universe_sha256") != universe_sha256:
        problems.append(
            f"universe_sha256: artifact={extra.get('universe_sha256')} "
            f"!= canlı={universe_sha256}")
    if extra.get("context_sha256") != context_sha256:
        problems.append(
            f"context_sha256: artifact={extra.get('context_sha256')} "
            f"!= canlı={context_sha256}")
    if problems:
        raise HoldoutError(
            "DONDURULMUŞ GİRDİ UYUŞMAZLIĞI — evren/bağlam değişmiş, tekrar "
            "karşılaştırması geçersiz olur: " + "; ".join(problems)
        )


def expected_repeat_metric(multiplier: int) -> str:
    """Tekrar testinin metrik imzası — tek yerde üretilir."""
    return ("Jaccard(E1@%dB, E2@%dB) — ensemble<->ensemble"
            % (multiplier, multiplier))


def verify_calibration_artifact(calib_doc: Dict[str, Any], *,
                                contract: Dict[str, Any],
                                expected_dataset: str = "dijital",
                                source_doc: Dict[str, Any] = None) -> Dict[str, Any]:
    """Kalibrasyon artifact'ının SEMANTİK doğrulaması (Codex 5. tur #1).

    Mühür doğrulaması "bu dosya bozulmamış" der; bu fonksiyon "bu dosya
    GERÇEKTEN bu testin eşik kaynağı" der: dataset, tür, metrik imzası,
    sözleşme adı/çarpanı, eşik alanlarının tipi ve (verilirse) kaynak
    sticky artifact'ının payload SHA'sı. Farklı ama geçerli mühürlenmiş
    bir artifact yanlış eşik olarak KABUL EDİLEMEZ.

    Doğrulanan `declared_gates` sözlüğünü döndürür.
    """
    validate_artifact(calib_doc)
    manifest = calib_doc.get("manifest") or {}
    extra = manifest.get("extra") or {}
    report = calib_doc.get("report") or {}
    problems = []
    if manifest.get("dataset_slug") != expected_dataset:
        problems.append(f"dataset_slug={manifest.get('dataset_slug')!r} "
                        f"!= beklenen {expected_dataset!r}")
    if extra.get("kind") != "ensemble_repeat_calibration":
        problems.append(f"kind={extra.get('kind')!r} != "
                        f"'ensemble_repeat_calibration'")
    if extra.get("holdout_contract") != contract["name"]:
        problems.append(f"holdout_contract={extra.get('holdout_contract')!r} "
                        f"!= {contract['name']!r}")
    metric = ((report.get("comparison") or {}).get("metric"))
    want_metric = expected_repeat_metric(contract["budget_multiplier"])
    if metric != want_metric:
        problems.append(f"metrik={metric!r} != beklenen {want_metric!r} "
                        f"(çarpan {contract['budget_multiplier']})")
    gates = report.get("declared_gates") or {}
    threshold = gates.get("ensemble_jaccard_at_3B_min")
    if not isinstance(threshold, (int, float)) or not 0 < threshold < 1:
        problems.append(f"geçersiz eşik: {threshold!r}")
    flip = gates.get("pass_flip_max")
    if not isinstance(flip, (int, float)) or not 0 < flip <= 1:
        problems.append(f"geçersiz pass-flip limiti: {flip!r}")
    if source_doc is not None:
        want_sha = extra.get("source_artifact_payload_sha256")
        got_sha = source_doc.get("artifact_payload_sha256")
        if not want_sha or want_sha != got_sha:
            problems.append(
                f"kaynak sticky artifact SHA uyuşmuyor: kalibrasyon "
                f"{want_sha!r} != mevcut {got_sha!r}")
    if problems:
        raise HoldoutError(
            "KALİBRASYON REDDİ — bu artifact bu testin eşik kaynağı "
            "olamaz: " + "; ".join(problems)
        )
    return gates


def repeat_comparison(first_views: Dict[str, Sequence[Any]],
                      repeat_views: Dict[str, Sequence[Any]],
                      *,
                      budgets: Dict[str, int],
                      positives: Dict[str, Sequence[int]],
                      raw_ranks: Dict[str, Dict[int, int]],
                      multiplier: int,
                      jaccard_threshold: float,
                      pass_flip_limit: float) -> Dict[str, Any]:
    """ÜRETİM TEKRARLANABİLİRLİĞİ: E1 = ensemble(A1,B1) vs E2 = ensemble(A2,B2).

    G3 dersinin düzeltilmiş hali — ölçülen şey ürün kararının kendisidir
    (iki görünümün birleşimi), tek görünümlerin birbiriyle uyuşması değil.
    Eşik (`jaccard_threshold`) AYNI metrik üzerinde Dijital'in ücretsiz
    full↔repeat ölçümünden ÖNCEDEN belirlenir.
    """
    e1 = merge_multi_views(dict(first_views))
    e2 = merge_multi_views(dict(repeat_views))
    flip = ensemble_flip_rate(e1, e2)

    def jac(a: set, b: set) -> float:
        union = len(a | b)
        return round(len(a & b) / union, 4) if union else 1.0

    channels: Dict[str, Any] = {}
    min_jac = 1.0
    for ch in CHANNELS:
        b = budgets[ch]
        mb = multiplier * b
        pos = list(positives[ch])
        o1 = ensemble_ordering(e1, ch.lower(), raw_rank=raw_ranks[ch])
        o2 = ensemble_ordering(e2, ch.lower(), raw_rank=raw_ranks[ch])
        j_b = jac(set(o1[:b]), set(o2[:b]))
        j_m = jac(set(o1[:mb]), set(o2[:mb]))
        min_jac = min(min_jac, j_m)
        r1 = candidate_recall_at(o1, pos, {"M": mb})["recall_at"]["M"]
        r2 = candidate_recall_at(o2, pos, {"M": mb})["recall_at"]["M"]
        channels[ch] = {
            "B": b, "multiplied_budget": mb,
            "ensemble_jaccard_at_B": j_b,
            "ensemble_jaccard_at_multiplied": j_m,
            "reach_first": r1["reached"], "reach_repeat": r2["reached"],
            "reach_delta": r2["reached"] - r1["reached"],
            "positives": len(pos),
        }
    gates = {
        "ensemble_jaccard_at_multiplied": {
            "min_value": min_jac,
            "threshold": jaccard_threshold,
            "passed": min_jac >= jaccard_threshold,
        },
        "ensemble_pass_flip": {
            "value": flip["any_channel_passing_flip_rate"],
            "limit": pass_flip_limit,
            "passed": (flip["any_channel_passing_flip_rate"] is not None
                       and flip["any_channel_passing_flip_rate"]
                       <= pass_flip_limit),
        },
    }
    return {
        "metric": expected_repeat_metric(multiplier),
        "compared_keywords": flip["compared"],
        "ensemble_pass_flip": flip["any_channel_passing_flip_rate"],
        "per_channel_pass_flip": {
            ch: flip["per_channel"][ch]["passing_flip_rate"]
            for ch in CHANNELS
        },
        "channels": channels,
        "gates": gates,
        "passed": all(g["passed"] for g in gates.values()),
    }
