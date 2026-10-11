"""Faz 1 kapı/bütçe mührü — `benchmark/claude_bakeoff_plan.json` (plan §5 Faz 0 madde 8-9).

Plan rev.6'nın kararları (K3/K7/K8/K12/K16/K18/K19), §7.5 örneklem, §8 kapılar,
§9 bütçe, §3.2 N_max ve Faz 0 spike kanıtı TEK dosyada, aday görülmeden
mühürlenir. Faz 1 koşusu `faz1_blockers()` boş dönmeden BAŞLAMAZ (fail-closed).
Kullanım: `python -m app.core.benchmark.claude_bakeoff_plan --out benchmark/claude_bakeoff_plan.json`
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from app.core.benchmark.reference_characterization import seal, verify_seal
from app.core.social.anthropic_schema import SCHEMA_TRANSFORMER_VERSION
from app.core.telemetry.usage import PRICE_TABLE
from app.generators.anthropic_service import (
    ADAPTER_CONTRACT_VERSION, EFFORT_TABLE, SYSTEM_SHA, THINKING_ALLOWANCE,
    THINKING_POLICY_SHA,
)

PLAN_VERSION = "rev.6"
HAIKU, SONNET, OPUS = "claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"
Y_STAGES = ["ads_intent", "ads_funnel", "seo_rel", "seo_bp", "social_rel", "family_a2"]
O_STAGES = ["family_a3", "seo_subintent", "seo_urlgroup", "social_v4", "social_intent"]
CONTENT_STAGES = ["seo_generation", "ads_rsa", "social_brief_contents", "social_brief_ideas"]
STAY_GEMINI = ["family_a1", "family_a2b", "family_a2c", "ads_grouping", "micro_shorten",
               "claim_rewrite", "seo_authority", "social_brief_categories", "geo_compliance",
               "embedding", "competitor_discovery"]


def _git_sha() -> str:
    sha = os.environ.get("APP_GIT_SHA")
    if sha and sha != "dev":
        return sha
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"],
                                       text=True).strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def build_plan(spike_path: Path) -> Dict[str, Any]:
    spike = json.loads(spike_path.read_text(encoding="utf-8"))
    verify_seal(spike)
    s = spike["summary"]
    prices = {m: PRICE_TABLE[m] for m in (HAIKU, SONNET, OPUS, "gemini-3.8-flash", "deepseek-v4-pro")}
    doc: Dict[str, Any] = {
        "plan_version": PLAN_VERSION,
        "plan_file": "plan_claude_gemini_karsilastirma.md",
        "sealed_at": datetime.now(timezone.utc).isoformat(),
        "git_sha": _git_sha(),
        "decisions": {
            "K3_models": {"Y": HAIKU, "O": SONNET, "content": SONNET,
                          "opus_in_faz1": False},
            "K7_caps_usd": {"claude": 50.0, "gemini": 20.0, "deepseek": 5.0},
            "K8_monthly_volume_p90": None,          # bilinmiyor -> N_max tablosu, patron hükmü
            "K12_document_reference": None,         # Faz 1 kapısında doldurulur (bloklayıcı)
            "K16_single_shot_idiomatic": True,
            "K18_holdout_ws": "5. ws (Scento veya Proteinim), Faz 2'de bir kez",
            "K19_task_p95_ratio": 1.25,
            "repeats_faz1": 1,
        },
        "routes": {"Y": Y_STAGES, "O": O_STAGES, "content": CONTENT_STAGES,
                   "stay_gemini": STAY_GEMINI},
        "prices": prices,
        "adapter": {
            "adapter_contract_version": ADAPTER_CONTRACT_VERSION,
            "system_sha": SYSTEM_SHA,
            "thinking_policy_sha": THINKING_POLICY_SHA,
            "thinking_allowance": dict(THINKING_ALLOWANCE),
            "effort_table": {str(k): v for k, v in EFFORT_TABLE.items()},
            "schema_transformer_version": SCHEMA_TRANSFORMER_VERSION,
            "anthropic_sdk": spike["anthropic_version"],
            "sdk_retries": {"anthropic_max_retries": 0, "gemini_retry_attempts": 1},
        },
        "spike": {
            "file": "benchmark/claude_spike.json",
            "payload_sha256": spike["payload_sha256"],
            "settled_usd_local_pricing": s["total_settled_usd"],
            "schemas_accepted": s["schemas_accepted"],
            "schemas_rejected": s["schemas_rejected"],
            "optional_fields_accepted": s["optional_fields_accepted"],
            "max_tokens_stops": s["max_tokens_stops"],
            "thinking_p99": {k: v.get("thinking_p99") for k, v in
                             s["thinking_allowance_recommendation"].items()},
            "allowance_decision": "2000/4000/8000 KORUNDU (n küçük; gerçek benchmark verisiyle bakılır)",
            "console_reconciled": False,
        },
        "gates": {
            "G1_selection": "LB90(Δ recall_havuz) ≥ −0,03 VE fark kümesinde konu-dışı Δ ≤ 0",
            "G2_content": "format başına: Wilson90 alt sınırı(kazanma, ITT, berabere=0,5) ≥ 0,45; "
                          "n ≥ 30 PATRON çifti; κ ≥ 0,50; sert kural teslim LB90(Δ) ≥ −0,05",
            "G3_profile": "UB90(Δ halüsinasyon_sayısı) ≤ 0 VE LB90(Δ patron puanı) ≥ −0,3",
            "G4_reliability": "fatal/refusal = 0; ham parse ve teslim LB90(Δ) ≥ −0,01; kırpılma UB90(Δ) ≤ +0,01",
            "G5_noise": "UB90(Δ flip) ≤ +0,05 — yalnız tekrarlı hücreler (Faz 2 ×2); Faz 1'de ölçülemedi",
            "G6a_service": "aşama p95 ≤ 1,5× Gemini VE görev p99 < stale eşiklerinin %50'si",
            "G6b_task": "görev p95 duvar saati ≤ 1,25× Gemini (Faz 3 gerçek tier ile kapı)",
            "G7a_credit_go": "G1–G4 ∧ G6a ∧ (G5 ölçüldüyse) ∧ G6b ∧ net kredi ≥ projeksiyon ∧ dönüş planı; "
                             "K8 yok → N_max tablosu + patron yazılı hükmü",
            "G7b_operational_go": "G1–G4 ∧ G6a ∧ (G5 ölçüldüyse) ∧ G6b ∧ [teslim maliyeti ≤ Gemini ∨ superiority]; "
                                  "MDE: seçim LB90(Δ recall) ≥ +0,05; içerik Wilson LB > 0,50 ve nokta ≥ 0,60; "
                                  "profil UB90(Δ halüsinasyon) ≤ −0,5",
            "outcome_faz1": ["İYİ ADAY", "UYGUN DEĞİL", "KANIT YETERSİZ"],
            "outcome_faz2_3": ["GEÇ-SINIRLI", "GEÇ-KREDİ", "AYARLA-TEKRAR", "KAL",
                               "GEÇ-KALICI (yalnız Faz 3, patron n ≥ 30/format, ≥ 2 ws)"],
            "time_limits_s": {"ads_generate_time_limit": 900,
                              "ADS_TASK_STALE_SECONDS": 1200, "task_time_limit": 3600},
        },
        "sample_table_7_5": [
            {"format": "RSA grubu", "n_expected": "18–26", "deepseek_pairs": "tümü (≤30)",
             "patron_pairs": 15, "g2_n30": False, "faz1": "kanıt yetersiz; yön + W/T/L"},
            {"format": "Blog", "n_expected": "~30", "deepseek_pairs": 30, "patron_pairs": 15,
             "g2_n30": False, "faz1": "kanıt yetersiz; yön + W/T/L"},
            {"format": "Sosyal içerik", "n_expected": "~60", "deepseek_pairs": 30,
             "patron_pairs": 15, "g2_n30": False, "faz1": "kanıt yetersiz; yön + W/T/L"},
            {"format": "Profil", "n_expected": "10 ws", "deepseek_pairs": None,
             "patron_pairs": "10 ws × 1–5", "g2_n30": None, "faz1": "G3"},
        ],
        "budget_usd": {
            "cells": [
                {"cell": "Faz 0 spike", "envelope": 0.5, "settled": s["total_settled_usd"]},
                {"cell": "1A Haiku Y ×1 4 ws", "envelope": 0.7},
                {"cell": "1A Sonnet O ×1 4 ws", "envelope": 8.6},
                {"cell": "1A arşiv replay D (Sonnet)", "envelope": 1.5},
                {"cell": "1B Aday ×1 2 ws", "envelope": 7.2},
                {"cell": "1B G/G/C ×1 2 ws", "envelope": 3.5},
                {"cell": "Profil 10 ws × (Sonnet+Haiku)", "envelope": 1.0},
            ],
            "total_envelope": 23.0, "cap": 50.0, "reserve": 27.0,
            "cut_order": ["1B G/G/C 2→1 ws (−1,75)", "1B Aday 2→1 ws (−3,6)", "arşiv replay düşer"],
            "thinking_margin": "+%30 (spike: low p99 düşünme 489 tok, kırpılma 0 → zarf yeterli)",
            "gemini_usd": {"estimate": 18.0, "cap": 20.0},
            "deepseek_usd": {"estimate": 2.0, "cap": 5.0, "pairs_max": 90},
        },
        "n_max_3_2": {"Aday": {"usd_per_run": 3.6, "n_max": 28},
                      "G/G/C": {"usd_per_run": 1.25, "n_max": 80},
                      "Opus içerik": {"usd_per_run": 2.5, "n_max": 40},
                      "Her şey Haiku": {"usd_per_run": 0.34, "n_max": 290},
                      "Yalnız Y Haiku": {"usd_per_run": 0.17, "n_max": None}},
        "workspaces": {"1A": ["Optimice", "GR-7", "Dijital", "Noksel"],
                       "1B": ["GR-7", "Optimice"], "profile": "10 onaylı ws",
                       "ids": None},                  # Faz 1 başında DB'den mühürlenir
    }
    return seal(doc)


def faz1_blockers(plan: Dict[str, Any]) -> List[str]:
    """Faz 1 koşusunu engelleyen eksikler (boş liste = başlayabilir)."""
    verify_seal(plan)
    out: List[str] = []
    if not plan["decisions"].get("K12_document_reference"):
        out.append("K12_document_reference")
    if not plan["spike"].get("console_reconciled"):
        out.append("spike_console_reconciled")
    if not plan["workspaces"].get("ids"):
        out.append("workspace_ids")
    return out


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spike", default="benchmark/claude_spike.json")
    ap.add_argument("--out", default="benchmark/claude_bakeoff_plan.json")
    a = ap.parse_args(argv)
    plan = build_plan(Path(a.spike))
    Path(a.out).write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print(f"wrote {a.out}: blockers={faz1_blockers(plan)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
