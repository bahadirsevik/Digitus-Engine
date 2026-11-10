# -*- coding: utf-8 -*-
"""REL-v5 bake-off karşılaştırması (plan_v4pro Katman-A).

İki MÜHÜRLÜ karakterizasyon artifact'ını (referans + aday) yükler,
kimliklerini doğrular ve `evaluate_relative_gates`'e (bakeoff_gates.py —
eşiklerin TEK kaynağı) verir. Bu modül yalnız çıkarım + kimlik
doğrulama + mühürlü rapor üretir.

Kimlik kuralları (Codex Katman-A #2 — hepsi fail-closed):
- Referans TAM OLARAK gemini/gemini-3.5-flash; aday TAM OLARAK
  deepseek/deepseek-v4-pro (bake-off sözleşmesi bu çifte pinli;
  "provider dolu" yetmez — mühürlü başka bir model aday sayılamaz).
- İki tarafta da TAM İKİ tekrar.
- Aynı source run + aynı fixture parmak izi + aynı context SHA.
- Aynı aşama listesi, batch boyutları ve sıcaklıklar.
- Aynı prompt/şema hash'leri (prompt_source_sha256 +
  response_schema_sha256 — hard_cap_contract bilinçli dışarıda:
  provider'a göre meşru farklılaşır ve plan bağlamada ayrıca doğrulanır).
- Adayın artifact'ı ONAYLANAN plana bağlı olmalı
  (provenance.plan_artifact_sha256 == expected_cand_plan_sha, tam 64).
- Maliyet SETTLED bütçeden (provenance.budget.cost_settled_usd) gelir;
  ceiling_charges her iki taraftan G7'ye taşınır (Codex #3).
- Flip izinleri YALNIZ referansın mühürlü `derived_allowances`
  bloğundan — aday tarafından türetilen hiçbir eşik kullanılmaz.
"""
import json
from pathlib import Path
from typing import Any, Dict

from app.core.benchmark.bakeoff_gates import (
    RELATIVE_GATES_VERSION,
    evaluate_relative_gates,
)
from app.core.benchmark.reference_characterization import (
    require_full_sha,
    seal,
    verify_seal,
)

REFERENCE_KIND = "reference_characterization"
CANDIDATE_KIND = "candidate_characterization"
REQUIRED_REFERENCE = {"provider": "gemini", "model": "gemini-3.5-flash"}
REQUIRED_CANDIDATE_PROVIDER = "deepseek"
SUPPORTED_CANDIDATE_MODELS = frozenset({
    "deepseek-v4-pro",
    "deepseek-v4-flash",
})
REQUIRED_REPEATS = 2
# Codex #2: yalnız prompt/şema paritesi — hard_cap_contract provider'a
# göre meşru farklılaşır (plan bağlama kendi tarafında doğrular)
PROMPT_PARITY_KEYS = ("prompt_source_sha256", "response_schema_sha256")
STAGE_PARITY_KEYS = ("stage_batches", "stage_temperatures",
                     "skipped_stages", "planned_requests_per_repeat")


class ComparisonError(Exception):
    pass


def load_sealed_artifact(path) -> Dict[str, Any]:
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    verify_seal(doc)
    return doc


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise ComparisonError(f"KARŞILAŞTIRMA REDDİ — {msg}")


def _require_identity(doc: Dict[str, Any], required: Dict[str, str],
                      side: str) -> None:
    # Eski (provider alansız) referans artifact'ı gemini sayılır —
    # plan bağlamadaki legacy kuralıyla aynı
    provider = doc.get("provider", "gemini")
    _require(provider == required["provider"],
             f"{side} provider '{provider}' != '{required['provider']}'")
    _require(doc.get("model") == required["model"],
             f"{side} model '{doc.get('model')}' != '{required['model']}'")


def _require_exact_repeats(doc: Dict[str, Any], side: str) -> None:
    reps = doc.get("repeats_data") or {}
    _require(doc.get("repeats") == REQUIRED_REPEATS,
             f"{side} repeats {doc.get('repeats')} != {REQUIRED_REPEATS}")
    _require(len(reps) == REQUIRED_REPEATS,
             f"{side} repeats_data {len(reps)} kayıt != {REQUIRED_REPEATS}")


def _budget_of(doc: Dict[str, Any], side: str) -> Dict[str, Any]:
    budget = (doc.get("provenance") or {}).get("budget")
    _require(isinstance(budget, dict) and budget,
             f"{side} artifact'ta provenance.budget yok — settled maliyet "
             f"ve ceiling_charges olmadan G7 çalıştırılamaz")
    _require(budget.get("cost_settled_usd") is not None,
             f"{side} budget.cost_settled_usd eksik")
    _require(budget.get("ceiling_charges") is not None,
             f"{side} budget.ceiling_charges eksik")
    return budget


def build_comparison(ref_doc: Dict[str, Any], cand_doc: Dict[str, Any],
                     *, expected_cand_plan_sha: str,
                     expected_cand_model: str = "deepseek-v4-pro"
                     ) -> Dict[str, Any]:
    """Mühürlü ref+cand → REL-v5 kapı sonucu (mühürlü rapor dict'i)."""
    _require(expected_cand_model in SUPPORTED_CANDIDATE_MODELS,
             f"desteklenmeyen aday model '{expected_cand_model}'")
    verify_seal(ref_doc)
    verify_seal(cand_doc)
    _require(ref_doc.get("kind") == REFERENCE_KIND,
             f"referans kind '{ref_doc.get('kind')}' != '{REFERENCE_KIND}'")
    _require(cand_doc.get("kind") == CANDIDATE_KIND,
             f"aday kind '{cand_doc.get('kind')}' != '{CANDIDATE_KIND}'")
    _require_identity(ref_doc, REQUIRED_REFERENCE, "referans")
    _require_identity(
        cand_doc,
        {"provider": REQUIRED_CANDIDATE_PROVIDER,
         "model": expected_cand_model},
        "aday",
    )
    _require_exact_repeats(ref_doc, "referans")
    _require_exact_repeats(cand_doc, "aday")

    ref_prov = ref_doc.get("provenance") or {}
    cand_prov = cand_doc.get("provenance") or {}
    _require(ref_prov.get("fixture") == cand_prov.get("fixture"),
             "fixture parmak izleri farklı — aynı K-150 evreni değil")
    _require(ref_prov.get("context_sha256") == cand_prov.get(
        "context_sha256"),
        "context SHA'ları farklı — aynı dondurulmuş bağlam değil")
    ref_run = (ref_prov.get("context_source") or {}).get("source_run_id")
    cand_run = (cand_prov.get("context_source") or {}).get("source_run_id")
    _require(ref_run is not None and ref_run == cand_run,
             f"source run farklı: referans {ref_run}, aday {cand_run}")
    for key in STAGE_PARITY_KEYS:
        _require(ref_prov.get(key) == cand_prov.get(key),
                 f"aşama paritesi bozuk: '{key}' alanları farklı")
    ref_pc = ref_prov.get("prompt_config") or {}
    cand_pc = cand_prov.get("prompt_config") or {}
    for key in PROMPT_PARITY_KEYS:
        _require(bool(ref_pc.get(key)) and ref_pc.get(key)
                 == cand_pc.get(key),
                 f"prompt/şema paritesi bozuk: '{key}' hash'leri farklı")

    expected_plan = require_full_sha(expected_cand_plan_sha,
                                     "aday plan SHA")
    cand_plan_sha = cand_prov.get("plan_artifact_sha256")
    _require(cand_plan_sha == expected_plan,
             f"aday plan bağlaması: artifact'taki "
             f"{str(cand_plan_sha)[:16]}... != onaylanan "
             f"{expected_plan[:16]}...")

    allowances = (ref_doc.get("derived_allowances") or {}).get("per_axis")
    _require(bool(allowances),
             "referans artifact'ta derived_allowances.per_axis yok")
    cand_max_flips = (cand_doc.get("repeat_flips") or {}).get(
        "per_axis_max_flip")
    _require(bool(cand_max_flips),
             "aday artifact'ta repeat_flips.per_axis_max_flip yok")

    ref_budget = _budget_of(ref_doc, "referans")
    cand_budget = _budget_of(cand_doc, "aday")

    ref_reps = ref_doc["repeats_data"]
    cand_reps = cand_doc["repeats_data"]
    ref_rids = sorted(ref_reps)
    cand_rids = sorted(cand_reps)

    result = evaluate_relative_gates(
        {r: ref_reps[r]["axes"] for r in ref_rids},
        {r: cand_reps[r]["axes"] for r in cand_rids},
        flip_allowances=allowances,
        cand_max_flips=cand_max_flips,
        cand_coverages=[cand_reps[r]["coverage"] for r in cand_rids],
        cand_parses=[cand_reps[r]["parse"] for r in cand_rids],
        ref_cost_usd=ref_budget["cost_settled_usd"],
        cand_cost_usd=cand_budget["cost_settled_usd"],
        ref_ceiling_charges=ref_budget["ceiling_charges"],
        cand_ceiling_charges=cand_budget["ceiling_charges"],
    )
    return seal({
        "kind": "bakeoff_comparison",
        "gates_version": RELATIVE_GATES_VERSION,
        "reference": {
            "model": ref_doc.get("model"),
            "provider": ref_doc.get("provider", "gemini"),
            "payload_sha256": ref_doc.get("payload_sha256"),
            "cost_settled_usd": ref_budget["cost_settled_usd"],
            "ceiling_charges": ref_budget["ceiling_charges"],
            "spent_total_estimated_usd": ref_doc.get(
                "spent_total_estimated_usd"),
        },
        "candidate": {
            "model": cand_doc.get("model"),
            "provider": cand_doc.get("provider"),
            "payload_sha256": cand_doc.get("payload_sha256"),
            "plan_artifact_sha256": cand_plan_sha,
            "cost_settled_usd": cand_budget["cost_settled_usd"],
            "ceiling_charges": cand_budget["ceiling_charges"],
            "spent_total_estimated_usd": cand_doc.get(
                "spent_total_estimated_usd"),
        },
        "source_run_id": ref_run,
        "fixture": ref_prov.get("fixture"),
        "context_sha256": ref_prov.get("context_sha256"),
        "result": result,
    })
