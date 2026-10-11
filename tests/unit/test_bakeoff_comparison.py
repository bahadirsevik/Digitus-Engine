# -*- coding: utf-8 -*-
"""REL-v5 karşılaştırma modülü testleri (Katman-A, Codex kimlik sözleşmesi).

Referans olarak GERÇEK mühürlü artifact kullanılır (CI kilidiyle aynı
dosya); aday, ondan türetilmiş sentetik mühürlü doc'tur — şekil sözleşmesi
gerçek veriyle kilitlenir, eşikler bakeoff_gates'te kalır.
"""
import json
from pathlib import Path

import pytest

from app.core.benchmark.bakeoff_comparison import (
    ComparisonError,
    build_comparison,
    load_sealed_artifact,
)
from app.core.benchmark.reference_characterization import (
    CharacterizationError,
    seal,
)

REF_PATH = (Path(__file__).resolve().parents[2] / "benchmark"
            / "reference_characterization_gemini-3.5-flash.json")
CAND_PLAN_SHA = "a" * 64


def _cand_from_ref(ref, prov_over=None, **over):
    """Referanstan sentetik aday: kimlik pinleri + TAM provenance."""
    prov = {
        "fixture": ref["provenance"]["fixture"],
        "context_sha256": ref["provenance"]["context_sha256"],
        "context_source": ref["provenance"]["context_source"],
        "stage_batches": ref["provenance"]["stage_batches"],
        "stage_temperatures": ref["provenance"]["stage_temperatures"],
        "skipped_stages": ref["provenance"]["skipped_stages"],
        "planned_requests_per_repeat":
            ref["provenance"]["planned_requests_per_repeat"],
        "prompt_config": ref["provenance"]["prompt_config"],
        "plan_artifact_sha256": CAND_PLAN_SHA,
        "budget": {
            "cost_settled_usd": round(
                ref["provenance"]["budget"]["cost_settled_usd"] * 0.4, 6),
            "ceiling_charges": 0,
        },
    }
    prov.update(prov_over or {})
    payload = {
        "kind": "candidate_characterization",
        "model": "deepseek-v4-pro",
        "provider": "deepseek",
        "repeats": ref["repeats"],
        "provenance": prov,
        "repeats_data": ref["repeats_data"],
        "repeat_flips": ref["repeat_flips"],
        "spent_total_estimated_usd": round(
            ref["spent_total_estimated_usd"] * 0.4, 4),
    }
    payload.update(over)
    return seal(payload)


def _build(ref, cand, plan_sha=CAND_PLAN_SHA,
           model="deepseek-v4-pro"):
    return build_comparison(
        ref,
        cand,
        expected_cand_plan_sha=plan_sha,
        expected_cand_model=model,
    )


class TestBuildComparison:
    def test_real_ref_with_self_parity_candidate_passes(self):
        """Gerçek referans + aynı eksenli %60 ucuz (settled) aday →
        GEÇER; rapor mühürlere + onaylı plan SHA'ya bağlanır."""
        ref = load_sealed_artifact(REF_PATH)
        cand = _cand_from_ref(ref)
        doc = _build(ref, cand)
        assert doc["kind"] == "bakeoff_comparison"
        assert doc["result"]["passed"] is True
        assert doc["result"]["violations"] == []
        assert doc["reference"]["payload_sha256"] == ref["payload_sha256"]
        assert doc["candidate"]["plan_artifact_sha256"] == CAND_PLAN_SHA
        # G7 SETTLED maliyetle çalıştı (estimated değil)
        cc = doc["result"]["gates"]["G7_operational"]["cost_reduction"]
        assert cc["ref_usd"] == pytest.approx(
            round(ref["provenance"]["budget"]["cost_settled_usd"], 4))
        from app.core.benchmark.reference_characterization import verify_seal

        verify_seal(doc)

    def test_settled_cost_equal_fails_g7(self):
        ref = load_sealed_artifact(REF_PATH)
        cand = _cand_from_ref(ref, prov_over={"budget": {
            "cost_settled_usd":
                ref["provenance"]["budget"]["cost_settled_usd"],
            "ceiling_charges": 0}})
        doc = _build(ref, cand)
        assert doc["result"]["passed"] is False
        assert any("G7 maliyet" in v for v in doc["result"]["violations"])

    def test_candidate_ceiling_charges_fail_g7(self):
        """Codex #3: usage'sız çağrı 'çok ucuz' yanılsaması yaratamaz."""
        ref = load_sealed_artifact(REF_PATH)
        cand = _cand_from_ref(ref, prov_over={"budget": {
            "cost_settled_usd": round(
                ref["provenance"]["budget"]["cost_settled_usd"] * 0.4, 6),
            "ceiling_charges": 3}})
        doc = _build(ref, cand)
        assert doc["result"]["passed"] is False
        assert any("ceiling_charges 3" in v
                   for v in doc["result"]["violations"])

    def test_missing_budget_fails_closed(self):
        ref = load_sealed_artifact(REF_PATH)
        cand = _cand_from_ref(ref, prov_over={"budget": None})
        with pytest.raises(ComparisonError, match="budget"):
            _build(ref, cand)

    # ── Kimlik pinleri (Codex #2) ──────────────────────────────────

    def test_candidate_wrong_model_rejected(self):
        ref = load_sealed_artifact(REF_PATH)
        cand = _cand_from_ref(ref, model="deepseek-v4-flash")
        with pytest.raises(ComparisonError, match="deepseek-v4-pro"):
            _build(ref, cand)

    def test_flash_candidate_is_pinned_and_supported(self):
        """Flash ayrı, sonuç-öncesi açık model kimliğiyle aynı REL-v5'e girer."""
        ref = load_sealed_artifact(REF_PATH)
        cand = _cand_from_ref(ref, model="deepseek-v4-flash")
        doc = _build(ref, cand, model="deepseek-v4-flash")
        assert doc["candidate"]["model"] == "deepseek-v4-flash"
        assert doc["result"]["passed"] is True

    def test_flash_contract_rejects_pro_artifact(self):
        ref = load_sealed_artifact(REF_PATH)
        cand = _cand_from_ref(ref, model="deepseek-v4-pro")
        with pytest.raises(ComparisonError, match="deepseek-v4-flash"):
            _build(ref, cand, model="deepseek-v4-flash")

    def test_unsupported_candidate_contract_rejected(self):
        ref = load_sealed_artifact(REF_PATH)
        cand = _cand_from_ref(ref, model="deepseek-v3")
        with pytest.raises(ComparisonError, match="desteklenmeyen"):
            _build(ref, cand, model="deepseek-v3")

    def test_candidate_wrong_provider_rejected(self):
        """'provider dolu' YETMEZ — mühürlü Gemini sonucu aday olamaz."""
        ref = load_sealed_artifact(REF_PATH)
        cand = _cand_from_ref(ref, provider="gemini",
                              model="deepseek-v4-pro")
        with pytest.raises(ComparisonError, match="provider"):
            _build(ref, cand)

    def test_reference_wrong_model_rejected(self):
        ref = load_sealed_artifact(REF_PATH)
        cand = _cand_from_ref(ref)
        fake_ref = seal({**{k: v for k, v in ref.items()
                            if k != "payload_sha256"},
                         "model": "gemini-3.1-flash-lite"})
        with pytest.raises(ComparisonError, match="gemini-3.5-flash"):
            _build(fake_ref, cand)

    def test_repeats_must_be_exactly_two(self):
        ref = load_sealed_artifact(REF_PATH)
        cand = _cand_from_ref(ref, repeats=3)
        with pytest.raises(ComparisonError, match="repeats"):
            _build(ref, cand)
        reps = dict(ref["repeats_data"])
        reps["R3"] = reps["R1"]
        cand2 = _cand_from_ref(ref, repeats_data=reps)
        with pytest.raises(ComparisonError, match="repeats_data"):
            _build(ref, cand2)

    def test_source_run_mismatch_rejected(self):
        ref = load_sealed_artifact(REF_PATH)
        src = dict(ref["provenance"]["context_source"])
        src["source_run_id"] = 999
        cand = _cand_from_ref(ref, prov_over={"context_source": src})
        with pytest.raises(ComparisonError, match="source run"):
            _build(ref, cand)

    def test_stage_batch_mismatch_rejected(self):
        """Aynı batch/sıcaklık koşulları — parite bozuksa kıyas geçersiz."""
        ref = load_sealed_artifact(REF_PATH)
        batches = dict(ref["provenance"]["stage_batches"])
        batches[next(iter(batches))] = 99
        cand = _cand_from_ref(ref, prov_over={"stage_batches": batches})
        with pytest.raises(ComparisonError, match="stage_batches"):
            _build(ref, cand)

    def test_temperature_mismatch_rejected(self):
        ref = load_sealed_artifact(REF_PATH)
        temps = dict(ref["provenance"]["stage_temperatures"])
        temps[next(iter(temps))] = 0.99
        cand = _cand_from_ref(ref,
                              prov_over={"stage_temperatures": temps})
        with pytest.raises(ComparisonError, match="stage_temperatures"):
            _build(ref, cand)

    def test_prompt_hash_mismatch_rejected(self):
        ref = load_sealed_artifact(REF_PATH)
        pc = json.loads(json.dumps(ref["provenance"]["prompt_config"]))
        key = next(iter(pc["prompt_source_sha256"]))
        pc["prompt_source_sha256"][key] = "f" * 64
        cand = _cand_from_ref(ref, prov_over={"prompt_config": pc})
        with pytest.raises(ComparisonError, match="prompt"):
            _build(ref, cand)

    def test_hard_cap_contract_divergence_is_allowed(self):
        """hard_cap_contract provider'a göre MEŞRU farklılaşır — parite
        yalnız prompt/şema hash'lerinde aranır."""
        ref = load_sealed_artifact(REF_PATH)
        pc = json.loads(json.dumps(ref["provenance"]["prompt_config"]))
        pc["hard_cap_contract"] = {"deepseek_only": "x"}
        cand = _cand_from_ref(ref, prov_over={"prompt_config": pc})
        assert _build(ref, cand)["result"]["passed"] is True

    def test_plan_sha_mismatch_rejected(self):
        """Aday, ONAYLANAN dry-run planına bağlı olmalı."""
        ref = load_sealed_artifact(REF_PATH)
        cand = _cand_from_ref(ref)
        with pytest.raises(ComparisonError, match="plan"):
            _build(ref, cand, plan_sha="b" * 64)

    def test_short_plan_sha_rejected(self):
        ref = load_sealed_artifact(REF_PATH)
        cand = _cand_from_ref(ref)
        with pytest.raises(CharacterizationError, match="TAM 64"):
            _build(ref, cand, plan_sha="abc123")

    # ── Mevcut kimlik/şekil korumaları ─────────────────────────────

    def test_wrong_candidate_kind_rejected(self):
        ref = load_sealed_artifact(REF_PATH)
        cand = _cand_from_ref(ref, kind="reference_characterization")
        with pytest.raises(ComparisonError, match="kind"):
            _build(ref, cand)

    def test_fixture_mismatch_rejected(self):
        ref = load_sealed_artifact(REF_PATH)
        cand = _cand_from_ref(ref, prov_over={"fixture": {"items": 1}})
        with pytest.raises(ComparisonError, match="fixture"):
            _build(ref, cand)

    def test_context_mismatch_rejected(self):
        ref = load_sealed_artifact(REF_PATH)
        cand = _cand_from_ref(ref,
                              prov_over={"context_sha256": "f" * 64})
        with pytest.raises(ComparisonError, match="context"):
            _build(ref, cand)

    def test_tampered_candidate_seal_rejected(self):
        ref = load_sealed_artifact(REF_PATH)
        cand = _cand_from_ref(ref)
        cand["spent_total_estimated_usd"] = 0.0001
        with pytest.raises(CharacterizationError, match="mühr"):
            _build(ref, cand)

    def test_missing_allowances_rejected(self):
        ref = load_sealed_artifact(REF_PATH)
        cand = _cand_from_ref(ref)
        stripped = seal({k: v for k, v in ref.items()
                         if k not in ("payload_sha256",
                                      "derived_allowances")})
        with pytest.raises(ComparisonError, match="derived_allowances"):
            _build(stripped, cand)

    def test_missing_cand_flips_rejected(self):
        ref = load_sealed_artifact(REF_PATH)
        cand = _cand_from_ref(ref, repeat_flips={})
        with pytest.raises(ComparisonError, match="per_axis_max_flip"):
            _build(ref, cand)

    def test_load_sealed_artifact_verifies(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps({"kind": "x",
                                   "payload_sha256": "0" * 64}),
                       encoding="utf-8")
        with pytest.raises(CharacterizationError, match="mühr"):
            load_sealed_artifact(bad)
