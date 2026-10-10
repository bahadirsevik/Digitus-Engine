# -*- coding: utf-8 -*-
"""Baseline-union-to-3B sözleşmesi + Faz 0 kapıları (plan §4.2/§4.3/§13).

Union üreticisi Faz 0 replay, shadow counterfactual ve assistive canlı
materyalizasyonun TEK kaynağıdır; sözleşme burada kilitlenir. Ek olarak
commit'li Faz 0 artifact'ı iç tutarlılık (CI) kilidinden geçer.
"""
import json
import math
from pathlib import Path

import pytest

from app.core.screening.candidate_union import (
    EXPANSION_AI_CALL_CEILING,
    ORIGIN_BASELINE,
    ORIGIN_BOTH,
    ORIGIN_NONE,
    ORIGIN_SCREENING,
    PHASE0_RECALL_TOLERANCE,
    PRODUCTION_SCREENING_CONTRACT,
    SCOPED_SCREENING_CONTRACT_V3,
    SCREENING_APPLIED_CHANNELS_V3,
    UNION_CONTRACT_V1,
    UNION_CONTRACT_V2,
    UNION_CONTRACT_V3,
    UNION_MODE_BASELINE_ONLY,
    UNION_MODE_ADDITIVE,
    UNION_MODE_QUOTA,
    CandidateUnionError,
    build_union_candidate_plan,
    downstream_request_plan,
    evaluate_union_gates,
    evaluate_union_gates_v2,
    evaluate_union_gates_v3,
    initial_target,
    screening_can_change_selection,
    union_reach_report,
    verify_screening_artifact_contract,
)

BENCH = Path(__file__).resolve().parents[2] / "benchmark" / "screening"


def _plan(baseline, ensemble, b, **kw):
    """v1 KOTA modu (tarihsel sözleşme testleri)."""
    kw.setdefault("mode", UNION_MODE_QUOTA)
    return build_union_candidate_plan(
        baseline_ordering=baseline, ensemble_ordering=ensemble,
        b_initial=b, **kw)


def _additive(baseline, ensemble, b, **kw):
    """v2 ADDITIVE modu (üretim kararı)."""
    kw.setdefault("mode", UNION_MODE_ADDITIVE)
    return build_union_candidate_plan(
        baseline_ordering=baseline, ensemble_ordering=ensemble,
        b_initial=b, **kw)


class TestTargetMath:
    def test_target_is_min_universe_three_b(self):
        assert initial_target(100, 10) == 30
        assert initial_target(20, 10) == 20      # evren küçük
        assert initial_target(0, 5) == 0

    def test_invalid_inputs_fail_closed(self):
        for bad in (-1, True, 1.5, "10", None):
            with pytest.raises(CandidateUnionError):
                initial_target(bad, 10)
        for bad in (0, -3, True, 2.5):
            with pytest.raises(CandidateUnionError):
                initial_target(100, bad)
        with pytest.raises(CandidateUnionError):
            initial_target(100, 10, multiplier=0)

    def test_small_universe_short_circuit(self):
        # baseline zaten hedefin tamamını kapsıyorsa screening değiştiremez
        assert screening_can_change_selection(100, 10) is True
        assert screening_can_change_selection(10, 10) is False
        assert screening_can_change_selection(8, 10) is False


class TestUnionContract:
    def test_exact_target_and_no_duplicates(self):
        baseline = list(range(1, 101))
        ensemble = list(range(100, 0, -1))
        plan = _plan(baseline, ensemble, 10)
        assert plan["target"] == 30
        assert len(plan["selected_ids"]) == 30
        assert len(set(plan["selected_ids"])) == 30
        assert plan["counts"]["initial_materialized_candidates"] == 30
        assert plan["contract_version"] == UNION_CONTRACT_V1
        assert plan["mode"] == UNION_MODE_QUOTA

    def test_baseline_b_fully_preserved(self):
        baseline = list(range(1, 101))
        ensemble = list(range(100, 0, -1))
        plan = _plan(baseline, ensemble, 10)
        assert plan["baseline_selected_ids"] == list(range(1, 11))
        assert set(range(1, 11)) <= set(plan["selected_ids"])

    def test_no_early_stop_on_full_intersection(self):
        """Kesişim toplamı küçültmez: ensemble ilk 10'u baseline ile aynı
        olsa da benzersiz aday tam T olur."""
        baseline = list(range(1, 101))
        ensemble = list(range(1, 101))          # birebir aynı sıra
        plan = _plan(baseline, ensemble, 10)
        assert len(plan["selected_ids"]) == 30
        assert plan["selected_ids"] == list(range(1, 31))
        assert plan["ensemble_walk_depth"] == 30

    def test_materialization_order_baseline_then_screening(self):
        baseline = [5, 4, 3, 2, 1, 10, 9, 8, 7, 6]
        ensemble = [10, 9, 8, 7, 6, 5, 4, 3, 2, 1]
        plan = _plan(baseline, ensemble, 2)     # b=2, T=6
        assert plan["selected_ids"][:2] == [5, 4]          # üretim sırası
        assert plan["selected_ids"][2:] == [10, 9, 8, 7]   # ensemble sırası
        assert [r["initial_rank"] for r in plan["selected"]] == [1, 2, 3, 4, 5, 6]

    def test_origin_sources_and_never_none(self):
        baseline = [1, 2, 3, 4, 5, 6, 7, 8]
        ensemble = [8, 7, 1, 6, 5, 4, 3, 2]
        plan = _plan(baseline, ensemble, 2)     # b=2 -> [1,2]; T=6
        origins = {r["keyword_id"]: r["origin_source"] for r in plan["selected"]}
        # 1: baseline-B ve ensemble top-6 -> both
        assert origins[1] == ORIGIN_BOTH
        # 2: baseline-B'de, ensemble top-6 dışında (8. sırada) -> baseline
        assert origins[2] == ORIGIN_BASELINE
        # 8,7,6,5: yalnız screening
        assert origins[8] == origins[7] == ORIGIN_SCREENING
        assert ORIGIN_NONE not in origins.values()
        assert plan["counts"]["origin_both"] == 1
        assert plan["counts"]["origin_baseline_only"] == 1
        assert plan["counts"]["origin_screening_only"] == 4

    def test_walk_depth_never_exceeds_target(self):
        baseline = list(range(1, 61))
        ensemble = list(range(60, 0, -1))
        plan = _plan(baseline, ensemble, 10)
        assert plan["ensemble_walk_depth"] <= plan["target"]

    def test_dropouts_are_ensemble_top_t_minus_union(self):
        baseline = list(range(1, 21))
        ensemble = list(range(20, 0, -1))
        plan = _plan(baseline, ensemble, 4)     # b=4, T=12
        ens_top = set(ensemble[:12])
        assert set(plan["baseline_protected_dropout_ids"]) == (
            ens_top - set(plan["selected_ids"]))
        assert plan["counts"]["baseline_protected_dropouts"] == len(
            plan["baseline_protected_dropout_ids"])

    def test_universe_exhausted_target_equals_n(self):
        baseline = [1, 2, 3, 4, 5]
        ensemble = [5, 4, 3, 2, 1]
        plan = _plan(baseline, ensemble, 4)     # 3*4=12 > N=5 -> T=5
        assert plan["target"] == 5
        assert sorted(plan["selected_ids"]) == [1, 2, 3, 4, 5]

    def test_b_greater_than_universe(self):
        plan = _plan([1, 2, 3], [3, 2, 1], 10)
        assert plan["baseline_slots"] == 3
        assert plan["target"] == 3
        assert plan["selected_ids"] == [1, 2, 3]

    def test_screening_fill_ids_match_counts(self):
        baseline = list(range(1, 51))
        ensemble = list(range(50, 0, -1))
        plan = _plan(baseline, ensemble, 5)
        assert len(plan["screening_fill_ids"]) == (
            plan["counts"]["screening_added"])
        assert not set(plan["screening_fill_ids"]) & set(
            plan["baseline_selected_ids"])


class TestUnionFailClosed:
    def test_different_universes_rejected(self):
        with pytest.raises(CandidateUnionError, match="AYNI evreni"):
            _plan([1, 2, 3], [1, 2, 4], 2)

    def test_duplicate_ids_rejected(self):
        with pytest.raises(CandidateUnionError, match="tekrarlanan"):
            _plan([1, 2, 2], [1, 2, 3], 2)

    def test_non_int_ids_rejected(self):
        with pytest.raises(CandidateUnionError, match="tam sayı"):
            _plan([1, "2", 3], [1, 2, 3], 2)
        with pytest.raises(CandidateUnionError, match="tam sayı"):
            _plan([1, True, 3], [1, 2, 3], 2)

    def test_universe_size_mismatch_rejected(self):
        with pytest.raises(CandidateUnionError, match="uyuşmuyor"):
            _plan([1, 2, 3], [3, 2, 1], 2, universe_size=5)

    def test_non_list_rejected(self):
        with pytest.raises(CandidateUnionError, match="liste olmalı"):
            _plan({1, 2, 3}, [1, 2, 3], 2)


class TestReachReport:
    def _setup(self):
        baseline = list(range(1, 31))            # 30 kelime
        ensemble = list(range(30, 0, -1))
        plan = _plan(baseline, ensemble, 3)      # b=3 -> [1,2,3]; T=9
        return baseline, ensemble, plan

    def test_reach_blocks_and_deltas(self):
        baseline, ensemble, plan = self._setup()
        # pozitifler: 1 (baseline başı), 30/29 (ensemble başı), 20 (hiçbiri)
        positives = {1, 30, 29, 20}
        rep = union_reach_report(plan, baseline_ordering=baseline,
                                 ensemble_ordering=ensemble,
                                 positives=positives)
        assert rep["positives"] == 4
        assert rep["baseline_B"]["reached"] == 1          # yalnız 1
        assert rep["baseline_top_T"]["budget"] == 9
        assert rep["ensemble_top_T"]["reached"] == 2      # 30, 29
        # union = [1,2,3] + ensemble[30..25] -> 1, 30, 29
        assert rep["union"]["reached"] == 3
        assert rep["delta_vs_ensemble"]["reached"] == 1
        assert rep["counts"]["initial_materialized_candidates"] == 9

    def test_positive_source_split_partitions_positives(self):
        baseline, ensemble, plan = self._setup()
        positives = {1, 30, 29, 20}
        rep = union_reach_report(plan, baseline_ordering=baseline,
                                 ensemble_ordering=ensemble,
                                 positives=positives)
        split = rep["positive_source_split"]
        assert (split["baseline_only"] + split["screening_only"]
                + split["both"] + split["neither"]) == 4

    def test_dropout_positive_ids_reported(self):
        baseline = list(range(1, 13))
        ensemble = [12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1]
        plan = _plan(baseline, ensemble, 4)      # b=4, T=12 == N
        rep = union_reach_report(plan, baseline_ordering=baseline,
                                 ensemble_ordering=ensemble,
                                 positives={12})
        # T == N: hiçbir şey dışarıda kalamaz
        assert rep["baseline_protected_dropouts"]["count"] == 0
        assert rep["union"]["reached"] == 1


class TestPhase0Gates:
    def _block(self, dataset="d", channel="ADS", *, union=10, base=8, ens=10,
               p=20, b=5, n=100, missing=False):
        plan = {
            "universe_size": n, "b_initial": b, "multiplier": 3,
            "target": min(n, 3 * b),
            "selected_ids": list(range(1, min(n, 3 * b) + 1)),
            "baseline_selected_ids": ([9991] if missing
                                      else list(range(1, b + 1))),
            "counts": {"initial_materialized_candidates": min(n, 3 * b)},
        }
        report = {
            "positives": p,
            "baseline_top_T": {"reached": base},
            "ensemble_top_T": {"reached": ens,
                               "recall": round(ens / p, 4)},
            "union": {"reached": union, "recall": round(union / p, 4)},
        }
        return {"dataset": dataset, "channel": channel, "plan": plan,
                "report": report}

    def test_all_gates_pass(self):
        out = evaluate_union_gates([self._block()])
        assert out["passed"] is True
        assert out["violations"] == []
        assert out["tolerance"] == PHASE0_RECALL_TOLERANCE

    def test_g1_union_below_baseline_fails(self):
        out = evaluate_union_gates([self._block(union=7, base=8, ens=7)])
        assert out["passed"] is False
        assert any(v.startswith("G1") for v in out["violations"])

    def test_g2_recall_tolerance_boundary(self):
        # p=20 -> her pozitif 0.05 recall; tam sınır GEÇER
        ok = evaluate_union_gates([self._block(union=9, base=5, ens=10)])
        assert not any(v.startswith("G2") for v in ok["violations"])
        bad = evaluate_union_gates([self._block(union=8, base=5, ens=10)])
        assert any(v.startswith("G2") for v in bad["violations"])

    def test_g0_target_mismatch_fails(self):
        block = self._block()
        block["plan"]["counts"]["initial_materialized_candidates"] = 14
        out = evaluate_union_gates([block])
        assert any(v.startswith("G0") for v in out["violations"])

    def test_g3_baseline_missing_fails(self):
        out = evaluate_union_gates([self._block(missing=True)])
        assert any(v.startswith("G3") for v in out["violations"])

    def test_missing_recall_fails_closed(self):
        block = self._block()
        block["report"]["ensemble_top_T"]["recall"] = None
        with pytest.raises(CandidateUnionError, match="recall"):
            evaluate_union_gates([block])

    def test_empty_blocks_fail_closed(self):
        with pytest.raises(CandidateUnionError, match="blok"):
            evaluate_union_gates([])


class TestArtifactContractGuard:
    def _manifest(self, **over):
        c = PRODUCTION_SCREENING_CONTRACT
        m = {
            "provider": "deepseek", "model": "deepseek-v4-flash",
            "prompt_version": "SCR-2026-07-27-v3a", "temperature": 0,
            "batch_size": 10,
            "reason_code_version": c["reason_code_version"],
            "reason_codes_sha256": c["reason_codes_sha256"],
            "response_schema_sha256": c["response_schema_sha256"],
            "prompt_template_sha256": c["prompt_template_sha256"],
            "extra": {"virtual_buckets": 128,
                      "plan_salts": ["scr-view-a", "scr-view-b"],
                      "prompt_version_used": "SCR-2026-07-27-v3a",
                      "ensemble_contract_version":
                          c["ensemble_contract_version"]},
        }
        m.update(over)
        return m

    def test_matching_manifest_passes(self):
        observed = verify_screening_artifact_contract(self._manifest())
        assert observed["model"] == "deepseek-v4-flash"
        assert observed["view_salts"] == ("scr-view-a", "scr-view-b")

    def test_wrong_prompt_version_rejected(self):
        m = self._manifest()
        m["extra"]["prompt_version_used"] = "SCR-2026-07-27-v2"
        with pytest.raises(CandidateUnionError, match="prompt_version"):
            verify_screening_artifact_contract(m)

    def test_wrong_batch_or_buckets_or_salts_rejected(self):
        with pytest.raises(CandidateUnionError, match="batch_size"):
            verify_screening_artifact_contract(self._manifest(batch_size=30))
        m = self._manifest()
        m["extra"]["virtual_buckets"] = 64
        with pytest.raises(CandidateUnionError, match="virtual_buckets"):
            verify_screening_artifact_contract(m)
        m2 = self._manifest()
        m2["extra"]["plan_salts"] = ["scr-view-a"]
        with pytest.raises(CandidateUnionError, match="view_salts"):
            verify_screening_artifact_contract(m2)

    def test_nonzero_temperature_rejected(self):
        with pytest.raises(CandidateUnionError, match="temperature"):
            verify_screening_artifact_contract(self._manifest(temperature=0.3))
        with pytest.raises(CandidateUnionError, match="temperature"):
            verify_screening_artifact_contract(self._manifest(temperature=None))

    def test_contract_constant_is_frozen(self):
        assert PRODUCTION_SCREENING_CONTRACT["model"] == "deepseek-v4-flash"
        assert PRODUCTION_SCREENING_CONTRACT["prompt_version"] == \
            "SCR-2026-07-27-v3a"
        assert PRODUCTION_SCREENING_CONTRACT["batch_size"] == 10
        assert PRODUCTION_SCREENING_CONTRACT["virtual_buckets"] == 128
        assert PRODUCTION_SCREENING_CONTRACT["candidate_multiplier"] == 3


class TestDownstreamRequestPlan:
    def _plan(self, **over):
        kwargs = dict(per_channel_targets={"ADS": 360, "SEO": 180,
                                           "SOCIAL": 180},
                      seo_capacity=86, intent_batch=8, prefilter_batch=6,
                      brand_batch=5, metadata_batch=4,
                      brand_filter_active=True)
        kwargs.update(over)
        return downstream_request_plan(**kwargs)

    def test_intent_counted_per_channel_not_once(self):
        """Codex düzeltmesi: intent birleşik evrenden TEK KEZ sayılamaz."""
        out = self._plan()
        req = out["requests"]
        assert req["intent_per_channel"] == {"ADS": 45, "SEO": 23,
                                             "SOCIAL": 23}
        assert req["intent_total"] == 91
        # birleşik-tek-sayım hatası 720/8=90 DEĞİL 91 olmalı (kanal kırpma)
        assert req["intent_total"] != math.ceil(720 / 8)

    def test_seo_prefilter_is_deterministic_zero(self):
        assert self._plan()["requests"]["seo_prefilter"] == 0

    def test_brand_filter_toggle(self):
        active = self._plan()["requests"]["brand_filter"]
        assert active == math.ceil(720 / 5)
        assert self._plan(brand_filter_active=False)[
            "requests"]["brand_filter"] == 0

    def test_total_is_sum_of_parts(self):
        req = self._plan()["requests"]
        assert req["total"] == (req["intent_total"] + req["brand_filter"]
                                + req["ads_prefilter"]
                                + req["social_prefilter"]
                                + req["seo_metadata"])

    def test_basis_documents_conservative_and_totals(self):
        basis = self._plan()["basis"]
        assert "kanal başına" in basis["intent"]
        assert "üst sınır" in basis["conservative"]
        # Codex 2. tur #1: transfer/expansion DIŞLANMAZ, üst sınıra girer
        assert "upper_total" in basis["totals"]
        assert "transfer" in basis and "expansion" in basis


class TestPhase0ArtifactLock:
    """Commit'li Faz 0 artifact'ı iç tutarlılık kilidi (CI)."""

    @pytest.fixture(scope="class")
    def doc(self):
        path = BENCH / "phase0_union_replay.json"
        if not path.exists():
            pytest.skip("Faz 0 artifact'ı yok")
        d = json.loads(path.read_text(encoding="utf-8"))
        from app.core.screening.manifest import compute_payload_sha

        assert compute_payload_sha(d) == d["artifact_payload_sha256"]
        return d

    def test_zero_provider_calls_and_declared_gates(self, doc):
        m = doc["manifest"]
        assert m["provider_calls"] == 0
        assert m["gates_declared_before_results"] is True
        assert m["recall_tolerance"] == PHASE0_RECALL_TOLERANCE
        # v1 artifact tarihsel sürüm adını taşır (mühürlü, değiştirilemez);
        # v2 artifact yeni additive sürümünü taşır
        assert m["union_contract_version"] in (
            "UNION-2026-07-30-v1", UNION_CONTRACT_V1, UNION_CONTRACT_V2,
            UNION_CONTRACT_V3)

    def test_union_counts_internally_consistent(self, doc):
        for slug, ds in doc["report"]["datasets"].items():
            n = ds["universe_size"]
            for ch, blk in ds["channels"].items():
                b, t = blk["B_initial"], blk["T_target"]
                counts = blk["union_plan_counts"]
                assert t == min(n, 3 * b), f"{slug}/{ch}"
                u = blk.get("U_union_size", t)
                assert counts["initial_materialized_candidates"] == u
                assert counts["baseline_preserved"] == min(b, n)
                assert counts["screening_added"] == u - min(b, n)
                assert t <= u <= t + min(b, n)
                assert len(blk["screening_only_ids"]) == counts[
                    "screening_added"]
                assert blk["ensemble_walk_depth"] <= t
                assert counts["baseline_protected_dropouts"] == len(
                    blk["baseline_protected_dropout_ids"])

    def test_gate_verdict_matches_reported_metrics(self, doc):
        gates = doc["report"]["gates"]
        assert gates["passed"] == (not gates["violations"])
        for key, blk in gates["blocks"].items():
            assert blk["G1_union_ge_baseline_at_T"] == (
                blk["union_reached"] >= blk["baseline_top_T_reached"])
            assert blk["G2_union_recall_within_tolerance"] == (
                blk["union_recall"] >= blk["recall_floor"])
            assert blk["recall_floor"] == round(
                blk["ensemble_recall"] - gates["tolerance"], 6)

    def test_costs_are_labelled_estimates(self, doc):
        for ds in doc["report"]["datasets"].values():
            cost = ds["cost_basis"]
            assert "TAHMİN" in cost["labels"]
            assert cost["downstream_expected_usd"] > 0
            assert (cost["downstream_p90_retry_envelope_usd"]
                    >= cost["downstream_p90_usd"])
            # intent kanal başına sayıldı
            assert cost["downstream_requests"]["intent_total"] == sum(
                cost["downstream_requests"]["intent_per_channel"].values())


class TestAdditiveContractV2:
    """v2 ADDITIVE sözleşmesi (Codex kararı) — SONUÇ GÖRÜLMEDEN dondu."""

    def test_union_is_exact_set_union_and_size_range(self):
        baseline = list(range(1, 101))
        ensemble = list(range(100, 0, -1))
        plan = _additive(baseline, ensemble, 10)      # B=10, T=30
        expected = set(baseline[:10]) | set(ensemble[:30])
        assert set(plan["selected_ids"]) == expected
        assert plan["union_size"] == len(expected) == 40      # kesişim yok
        assert plan["target"] <= plan["union_size"] <= (
            plan["target"] + plan["baseline_slots"])
        assert plan["contract_version"] == UNION_CONTRACT_V2
        assert plan["mode"] == UNION_MODE_ADDITIVE

    def test_ensemble_top_t_never_dropped(self):
        baseline = list(range(1, 101))
        ensemble = list(range(100, 0, -1))
        plan = _additive(baseline, ensemble, 10)
        assert set(ensemble[:30]) <= set(plan["selected_ids"])
        assert plan["counts"]["baseline_protected_dropouts"] == 0
        assert plan["baseline_protected_dropout_ids"] == []

    def test_full_overlap_gives_exactly_t(self):
        """Ensemble ve baseline aynı sırada -> U = T (alt sınır)."""
        ids = list(range(1, 101))
        plan = _additive(ids, ids, 10)
        assert plan["union_size"] == plan["target"] == 30

    def test_order_baseline_then_ensemble(self):
        baseline = [5, 4, 3, 2, 1, 10, 9, 8, 7, 6]
        ensemble = [10, 9, 8, 7, 6, 5, 4, 3, 2, 1]
        plan = _additive(baseline, ensemble, 2)   # b=2 [5,4]; T=6
        assert plan["selected_ids"][:2] == [5, 4]
        # ensemble top-6: 10,9,8,7,6,5 -> 5 zaten var, kalanlar sırayla
        assert plan["selected_ids"][2:] == [10, 9, 8, 7, 6]
        assert plan["union_size"] == 7

    def test_walk_covers_full_ensemble_window(self):
        baseline = list(range(1, 61))
        ensemble = list(range(60, 0, -1))
        plan = _additive(baseline, ensemble, 10)
        assert plan["ensemble_walk_depth"] == plan["target"]

    def test_small_universe_additive(self):
        plan = _additive([1, 2, 3], [3, 2, 1], 10)
        assert plan["target"] == plan["union_size"] == 3

    def test_unknown_mode_rejected(self):
        with pytest.raises(CandidateUnionError, match="bilinmeyen union modu"):
            build_union_candidate_plan(
                baseline_ordering=[1, 2], ensemble_ordering=[2, 1],
                b_initial=1, mode="hayalet")


class TestAdditiveGatesV2:
    def _blocks(self, *, baseline_reach=5, base_u_reach=8, ens_reach=10,
                union_reach=12, p=20, b=10, n=100):
        baseline = list(range(1, n + 1))
        ensemble = list(range(n, 0, -1))
        plan = _additive(baseline, ensemble, b)
        rep = {
            "positives": p,
            "union_size": plan["union_size"],
            "baseline_B": {"reached": baseline_reach},
            "baseline_top_T": {"reached": base_u_reach},
            "baseline_top_U": {"reached": base_u_reach},
            "ensemble_top_T": {"reached": ens_reach,
                               "recall": round(ens_reach / p, 4)},
            "union": {"budget": plan["union_size"], "reached": union_reach,
                      "recall": round(union_reach / p, 4)},
        }
        return [{"dataset": "d", "channel": "ADS", "plan": plan,
                 "report": rep}]

    def test_all_gates_pass(self):
        out = evaluate_union_gates_v2(self._blocks())
        assert out["passed"] is True
        assert out["violations"] == []
        assert out["contract_version"] == UNION_CONTRACT_V2
        assert out["tolerance"] == 0.0          # additive'de tolerans YOK

    def test_h4_union_below_baseline_b_fails(self):
        out = evaluate_union_gates_v2(
            self._blocks(baseline_reach=15, union_reach=12))
        assert any(v.startswith("H4") for v in out["violations"])

    def test_h5_union_below_baseline_at_u_fails(self):
        """'Yalnız pencereyi büyütmek daha iyiydi' testi."""
        out = evaluate_union_gates_v2(
            self._blocks(base_u_reach=14, union_reach=12))
        assert any(v.startswith("H5") for v in out["violations"])
        assert any("pencereyi büyütmek" in v for v in out["violations"])

    def test_h6_union_recall_below_ensemble_fails(self):
        out = evaluate_union_gates_v2(
            self._blocks(ens_reach=14, union_reach=12))
        assert any(v.startswith("H6") for v in out["violations"])

    def test_h0_h2_detect_tampered_plan(self):
        blocks = self._blocks()
        dropped = blocks[0]["plan"]["screening_reference_ids"][0]
        blocks[0]["plan"]["selected_ids"] = [
            k for k in blocks[0]["plan"]["selected_ids"] if k != dropped]
        out = evaluate_union_gates_v2(blocks)
        assert any(v.startswith("H0") for v in out["violations"])
        assert any(v.startswith("H2") for v in out["violations"])

    def test_h1_detects_missing_baseline(self):
        blocks = self._blocks()
        base_id = blocks[0]["plan"]["baseline_selected_ids"][0]
        blocks[0]["plan"]["selected_ids"] = [
            k for k in blocks[0]["plan"]["selected_ids"] if k != base_id]
        out = evaluate_union_gates_v2(blocks)
        assert any(v.startswith("H1") for v in out["violations"])

    def test_h3_detects_size_mismatch(self):
        blocks = self._blocks()
        blocks[0]["plan"]["union_size"] = 999
        out = evaluate_union_gates_v2(blocks)
        assert any(v.startswith("H3") for v in out["violations"])

    def test_quota_plan_rejected_by_v2_gates(self):
        plan = _plan(list(range(1, 101)), list(range(100, 0, -1)), 10)
        block = {"dataset": "d", "channel": "ADS", "plan": plan,
                 "report": {}}
        with pytest.raises(CandidateUnionError, match="additive"):
            evaluate_union_gates_v2([block])

    def test_empty_blocks_fail_closed(self):
        with pytest.raises(CandidateUnionError, match="blok"):
            evaluate_union_gates_v2([])


class TestTransferExpansionCostInclusion:
    """Codex 2. tur #1: preflight üst sınırı transfer+expansion'ı KAPSAR."""

    def _out(self, **over):
        kwargs = dict(per_channel_targets={"ADS": 360, "SEO": 180,
                                           "SOCIAL": 180},
                      seo_capacity=86, intent_batch=8, prefilter_batch=6,
                      brand_batch=5, metadata_batch=4,
                      brand_filter_active=True)
        kwargs.update(over)
        return downstream_request_plan(**kwargs)

    def test_transfer_upper_is_all_ads_candidates(self):
        req = self._out()["requests"]
        assert req["transfer_intent_upper"] == math.ceil(360 / 8)

    def test_expansion_upper_is_production_hard_budget(self):
        req = self._out()["requests"]
        assert req["expansion_ai_upper"] == EXPANSION_AI_CALL_CEILING == 60

    def test_upper_total_includes_both(self):
        req = self._out()["requests"]
        assert req["upper_total"] == (req["initial_total"]
                                     + req["transfer_intent_upper"]
                                     + req["expansion_ai_upper"])
        assert req["upper_total"] > req["initial_total"]
        assert req["total"] == req["initial_total"]   # geriye uyumlu ad

    def test_custom_expansion_ceiling_respected(self):
        req = self._out(expansion_call_ceiling=12)["requests"]
        assert req["expansion_ai_upper"] == 12


class TestContractPinningV2:
    """Codex 2. tur #2: şema/RC/prompt/ensemble sürümü de pinli."""

    def _manifest(self, **over):
        c = PRODUCTION_SCREENING_CONTRACT
        m = {
            "provider": "deepseek", "model": "deepseek-v4-flash",
            "prompt_version": "SCR-2026-07-27-v3a", "temperature": 0,
            "batch_size": 10,
            "reason_code_version": c["reason_code_version"],
            "reason_codes_sha256": c["reason_codes_sha256"],
            "response_schema_sha256": c["response_schema_sha256"],
            "prompt_template_sha256": c["prompt_template_sha256"],
            "extra": {"virtual_buckets": 128,
                      "plan_salts": ["scr-view-a", "scr-view-b"],
                      "prompt_version_used": "SCR-2026-07-27-v3a",
                      "ensemble_contract_version":
                          c["ensemble_contract_version"]},
        }
        m.update(over)
        return m

    def test_fully_pinned_manifest_passes(self):
        observed = verify_screening_artifact_contract(self._manifest())
        assert observed["response_schema_sha256"] == \
            PRODUCTION_SCREENING_CONTRACT["response_schema_sha256"]

    def test_schema_drift_rejected(self):
        with pytest.raises(CandidateUnionError,
                           match="response_schema_sha256"):
            verify_screening_artifact_contract(
                self._manifest(response_schema_sha256="f" * 64))

    def test_reason_code_drift_rejected(self):
        with pytest.raises(CandidateUnionError, match="reason_code_version"):
            verify_screening_artifact_contract(
                self._manifest(reason_code_version="RC-v2"))
        with pytest.raises(CandidateUnionError, match="reason_codes_sha256"):
            verify_screening_artifact_contract(
                self._manifest(reason_codes_sha256="a" * 64))

    def test_prompt_body_drift_rejected(self):
        with pytest.raises(CandidateUnionError,
                           match="prompt_template_sha256"):
            verify_screening_artifact_contract(
                self._manifest(prompt_template_sha256="b" * 64))

    def test_ensemble_version_drift_rejected(self):
        m = self._manifest()
        m["extra"]["ensemble_contract_version"] = "ENS-2026-08-01-v2"
        with pytest.raises(CandidateUnionError,
                           match="ensemble_contract_version"):
            verify_screening_artifact_contract(m)

    def test_live_ensemble_version_matches_pin(self):
        from app.core.screening.ensemble import ENSEMBLE_CONTRACT_VERSION

        assert (ENSEMBLE_CONTRACT_VERSION
                == PRODUCTION_SCREENING_CONTRACT["ensemble_contract_version"])


def _baseline_only(baseline, ensemble, b, **kw):
    """v3 kapsam DIŞI kanal (SOCIAL) — screening canlı seçime dokunmaz."""
    kw.setdefault("mode", UNION_MODE_BASELINE_ONLY)
    return build_union_candidate_plan(
        baseline_ordering=baseline, ensemble_ordering=ensemble,
        b_initial=b, **kw)


class TestScopedContractV3:
    """v3 KAPSAM sözleşmesi (Codex kararı) — SONUÇ GÖRÜLMEDEN dondu."""

    def test_frozen_scope_is_ads_seo(self):
        assert SCREENING_APPLIED_CHANNELS_V3 == ("ADS", "SEO")
        c = SCOPED_SCREENING_CONTRACT_V3
        assert c["contract_version"] == UNION_CONTRACT_V3
        assert c["in_scope_mode"] == UNION_MODE_ADDITIVE
        assert c["out_of_scope_mode"] == UNION_MODE_BASELINE_ONLY
        assert "SOCIAL" in c["out_of_scope_rationale"]
        assert "BIREBIR" in c["out_of_scope_guarantee"]

    def test_no_ambiguous_version_alias_exported(self):
        """Codex 3. tur #1: belirsiz alias kaldırıldı."""
        import app.core.screening.candidate_union as mod

        assert not hasattr(mod, "UNION_CONTRACT_VERSION")

    def test_baseline_only_is_bit_identical_with_flag_off(self):
        baseline = [7, 3, 9, 1, 5, 2, 8, 4, 6, 10]
        ensemble = [10, 9, 8, 7, 6, 5, 4, 3, 2, 1]
        plan = _baseline_only(baseline, ensemble, 4)
        assert plan["selected_ids"] == baseline[:4]     # SIRA dahil birebir
        assert plan["target"] == plan["union_size"] == 4
        assert plan["contract_version"] == UNION_CONTRACT_V3
        assert plan["mode"] == UNION_MODE_BASELINE_ONLY

    def test_baseline_only_has_no_screening_trace(self):
        plan = _baseline_only(list(range(1, 21)), list(range(20, 0, -1)), 5)
        assert plan["screening_reference_ids"] == []
        assert plan["screening_fill_ids"] == []
        assert plan["baseline_protected_dropout_ids"] == []
        assert plan["ensemble_walk_depth"] == 0
        assert plan["counts"]["screening_added"] == 0
        assert all(r["origin_source"] == ORIGIN_BASELINE
                   for r in plan["selected"])

    def test_baseline_only_b_greater_than_universe(self):
        plan = _baseline_only([1, 2, 3], [3, 2, 1], 10)
        assert plan["selected_ids"] == [1, 2, 3]
        assert plan["union_size"] == 3


class TestScopedGatesV3:
    def _blocks(self, *, social_mode=UNION_MODE_BASELINE_ONLY,
                social_tamper=False):
        n, b = 100, 10
        baseline = list(range(1, n + 1))
        ensemble = list(range(n, 0, -1))
        blocks = []
        for ch in ("ADS", "SEO"):
            plan = _additive(baseline, ensemble, b)
            rep = {
                "positives": 20, "union_size": plan["union_size"],
                "baseline_B": {"reached": 5},
                "baseline_top_T": {"reached": 8},
                "baseline_top_U": {"reached": 8},
                "ensemble_top_T": {"reached": 10, "recall": 0.5},
                "union": {"budget": plan["union_size"], "reached": 12,
                          "recall": 0.6},
            }
            blocks.append({"dataset": "d", "channel": ch, "plan": plan,
                           "report": rep})
        social = build_union_candidate_plan(
            baseline_ordering=baseline, ensemble_ordering=ensemble,
            b_initial=b, universe_size=n, mode=social_mode)
        if social_tamper:
            social["selected_ids"] = list(reversed(social["selected_ids"]))
        blocks.append({
            "dataset": "d", "channel": "SOCIAL", "plan": social,
            "report": {"positives": 20, "union_size": social["union_size"],
                       "baseline_B": {"reached": 4},
                       "baseline_top_T": {"reached": 4},
                       "baseline_top_U": {"reached": 4},
                       "ensemble_top_T": {"reached": 3, "recall": 0.15},
                       "union": {"budget": social["union_size"],
                                 "reached": 4, "recall": 0.2}}})
        return blocks

    def test_scoped_gates_pass_with_frozen_scope(self):
        out = evaluate_union_gates_v3(self._blocks())
        assert out["passed"] is True
        assert out["violations"] == []
        assert out["contract_version"] == UNION_CONTRACT_V3
        assert out["applied_screening_channels"] == ["ADS", "SEO"]
        assert out["blocks"]["d/SOCIAL"]["scope"] == "out_of_scope"
        assert out["blocks"]["d/ADS"]["scope"] == "in_scope"

    def test_social_in_additive_mode_fails_k0(self):
        out = evaluate_union_gates_v3(
            self._blocks(social_mode=UNION_MODE_ADDITIVE))
        assert any(v.startswith("K0") for v in out["violations"])
        assert any(v.startswith("K2") for v in out["violations"])

    def test_social_order_tamper_fails_k1(self):
        out = evaluate_union_gates_v3(self._blocks(social_tamper=True))
        assert any(v.startswith("K1") for v in out["violations"])

    def test_scope_drift_fails_closed(self):
        with pytest.raises(CandidateUnionError, match="kapsamı dondurulmuş"):
            evaluate_union_gates_v3(self._blocks(),
                                    applied_channels=("ADS",))
        with pytest.raises(CandidateUnionError, match="kapsamı dondurulmuş"):
            evaluate_union_gates_v3(
                self._blocks(), applied_channels=("ADS", "SEO", "SOCIAL"))

    def test_in_scope_violations_still_reported(self):
        blocks = self._blocks()
        # ADS union'ından bir ensemble kimliği düşür -> H0/H2
        dropped = blocks[0]["plan"]["screening_reference_ids"][0]
        blocks[0]["plan"]["selected_ids"] = [
            k for k in blocks[0]["plan"]["selected_ids"] if k != dropped]
        out = evaluate_union_gates_v3(blocks)
        assert out["passed"] is False
        assert any(v.startswith("H0") or v.startswith("H2")
                   for v in out["violations"])

    def test_no_in_scope_block_fails_closed(self):
        social_only = [b for b in self._blocks() if b["channel"] == "SOCIAL"]
        with pytest.raises(CandidateUnionError, match="kapsam içi blok yok"):
            evaluate_union_gates_v3(social_only)


class TestPhase0V3ArtifactLock:
    """v3 artifact: kapsam + maliyet semantiği CI kilidi."""

    @pytest.fixture(scope="class")
    def doc(self):
        path = BENCH / "phase0_union_replay_v3_scoped.json"
        if not path.exists():
            pytest.skip("v3 artifact yok")
        d = json.loads(path.read_text(encoding="utf-8"))
        from app.core.screening.manifest import compute_payload_sha

        assert compute_payload_sha(d) == d["artifact_payload_sha256"]
        return d

    def test_scope_recorded_and_frozen(self, doc):
        m = doc["manifest"]
        assert m["union_contract_version"] == UNION_CONTRACT_V3
        assert m["scoped_contract"]["applied_screening_channels"] == [
            "ADS", "SEO"]
        assert doc["report"]["gates"]["applied_screening_channels"] == [
            "ADS", "SEO"]

    def test_social_channels_are_baseline_only(self, doc):
        for slug, ds in doc["report"]["datasets"].items():
            social = ds["channels"]["SOCIAL"]
            assert social["union_mode"] == UNION_MODE_BASELINE_ONLY
            assert social["in_screening_scope"] is False
            assert social["U_union_size"] == social["B_initial"]
            assert social["screening_only_ids"] == []
            for ch in ("ADS", "SEO"):
                assert ds["channels"][ch]["in_screening_scope"] is True
                assert ds["channels"][ch]["U_union_size"] >= (
                    ds["channels"][ch]["T_target"])

    def test_retry_envelope_covers_transfer_expansion(self, doc):
        """Codex 3. tur #2: zarf artık üst sınırın ALTINDA kalamaz."""
        for ds in doc["report"]["datasets"].values():
            c = ds["cost_basis"]
            assert (c["downstream_p90_retry_envelope_usd"]
                    >= c["downstream_p90_with_transfer_expansion_usd"])
            assert (c["downstream_p90_retry_envelope_usd"]
                    > c["downstream_p90_initial_only_retry_envelope_usd"])
            # Codex 4. tur: uc tavan AYRI; birlesik = screening + downstream
            assert (c["downstream_hard_cap_usd"]
                    == c["downstream_p90_retry_envelope_usd"])
            assert c["screening_hard_cap_usd"] > 0
            assert c["combined_hard_cap_usd"] == pytest.approx(
                c["downstream_hard_cap_usd"] + c["screening_hard_cap_usd"],
                abs=1e-4)
            assert c["combined_hard_cap_usd"] > c["downstream_hard_cap_usd"]
            assert "parasal" in c["cost_semantics"]
            assert "AiCostReservation" in c["cost_semantics"]
            assert "TOPLAM" in c["cost_semantics"]
