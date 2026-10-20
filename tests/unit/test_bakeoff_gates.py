# -*- coding: utf-8 -*-
"""Göreli bake-off kapı sözleşmesinin kilidi (Codex kararı).

V4 Pro sonucu görülmeden dondurulan sözleşme: her kapının geçiş/ihlal
matematiği + sınır değerleri + fail-closed eksik-veri davranışı burada
kilitlenir. Sabitler değişirse sürüm değişmeden test kırılır.
"""
import pytest

from app.core.benchmark.bakeoff_gates import (
    G1_F1_MAX_LOSS,
    G3_MAX_LOSS,
    G4_FP_HEADROOM_RATIO,
    G5_MAE_MAX_INCREASE,
    RELATIVE_GATES_VERSION,
    RelativeGateError,
    evaluate_relative_gates,
)


# routing_harness yalın dalda yok: iki saf yardımcı arşivden birebir (test-yerel)
from typing import Optional


def macro_f1(y_true: list, y_pred: list) -> Optional[float]:
    if not y_true:
        return None
    classes = sorted({*y_true, *y_pred}, key=str)
    f1s = []
    for cls in classes:
        tp = sum(1 for t, p in zip(y_true, y_pred) if t == cls and p == cls)
        fp = sum(1 for t, p in zip(y_true, y_pred) if t != cls and p == cls)
        fn = sum(1 for t, p in zip(y_true, y_pred) if t == cls and p != cls)
        denom = 2 * tp + fp + fn
        f1s.append((2 * tp / denom) if denom else 1.0)
    return sum(f1s) / len(f1s)


def class_metrics(y_true: list, y_pred: list) -> dict:
    """Per-class confusion counts used by sparse-class acceptance gates."""
    classes = sorted({*y_true, *y_pred}, key=str)
    out = {}
    for cls in classes:
        support = sum(1 for value in y_true if value == cls)
        tp = sum(
            1 for truth, pred in zip(y_true, y_pred)
            if truth == cls and pred == cls
        )
        fp = sum(
            1 for truth, pred in zip(y_true, y_pred)
            if truth != cls and pred == cls
        )
        fn = support - tp
        denom = 2 * tp + fp + fn
        out[str(cls)] = {
            "label": cls,
            "support": support,
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "recall": (tp / support) if support else None,
            "f1": (2 * tp / denom) if denom else None,
        }
    return out


ALL_AXES = ("intent_type", "ads_class", "opinion_discussion",
            "curiosity_comparison", "agenda_theme", "gt", "ga")


def _axes(mae=0.2, tp1=10, fp1=5, sparse_tp=3,
          ads_m1_tp=42, ads_m1_fp=17, gold_n=149,
          n=None, missing=0):
    """Sentetik axis_report — GERÇEK invariantlarla tutarlı:
    sum(class_support) == gold_n; metrics support == class_support[label];
    n + missing == gold_n; confusion matrix ve macro-F1 yeniden üretilebilir."""

    def report(full_truth, full_pred):
        evaluated_truth = full_truth[:n]
        evaluated_pred = full_pred[:n]
        support = {}
        for label in full_truth:
            key = str(label)
            support[key] = support.get(key, 0) + 1
        return {
            "macro_f1": macro_f1(evaluated_truth, evaluated_pred),
            "n": n,
            "gold_n": gold_n,
            "missing_predictions": missing,
            "class_support": support,
            "class_metrics": class_metrics(evaluated_truth, evaluated_pred),
        }

    n = (gold_n - missing) if n is None else n
    axes = {}
    generic_truth = (
        ["1"] * 25 + ["0"] * (gold_n - 29) + ["sparse"] * 4
    )
    generic_pred = (
        ["1"] * tp1 + ["0"] * (25 - tp1)
        + ["1"] * fp1 + ["0"] * (gold_n - 29 - fp1)
        + ["sparse"] * sparse_tp + ["0"] * (4 - sparse_tp)
    )
    for ax in ("intent_type", "opinion_discussion",
               "curiosity_comparison", "agenda_theme"):
        axes[ax] = report(generic_truth, generic_pred)

    ads_truth = [-1] * 86 + [1] * (gold_n - 90) + ["sparse"] * 4
    ads_pred = (
        [-1] * ads_m1_tp + [1] * (86 - ads_m1_tp)
        + [-1] * ads_m1_fp + [1] * (gold_n - 90 - ads_m1_fp)
        + ["sparse"] * sparse_tp + [1] * (4 - sparse_tp)
    )
    axes["ads_class"] = report(ads_truth, ads_pred)
    for ax in ("gt", "ga"):
        axes[ax] = {"n": n, "mae": mae}
    return axes


def _full_allowances(value=0.11):
    return {ax: {"candidate_flip_allowance": value} for ax in ALL_AXES}


def _full_flips(value=0.05):
    return {ax: value for ax in ALL_AXES}


def _kwargs(ref=None, cand=None, **over):
    ref = ref or _axes()
    cand = cand or _axes()
    base = dict(
        ref_axes_by_repeat={"R1": ref, "R2": ref},
        cand_axes_by_repeat={"C1": cand, "C2": cand},
        flip_allowances=_full_allowances(),
        cand_max_flips=_full_flips(),
        cand_coverages=[{"missing_predictions": 0, "fallback_rate": 0.0},
                        {"missing_predictions": 0, "fallback_rate": 0.0}],
        cand_parses=[{"post_retry_ok": 1.0}, {"post_retry_ok": 1.0}],
        ref_cost_usd=1.0,
        cand_cost_usd=0.2,
        ref_ceiling_charges=0,
        cand_ceiling_charges=0,
    )
    base.update(over)
    return base


class TestContractIdentity:
    def test_version_and_constants_locked(self):
        assert RELATIVE_GATES_VERSION == "REL-2026-07-29-v5"
        assert G1_F1_MAX_LOSS == 0.03
        assert G3_MAX_LOSS == 0.05
        assert G4_FP_HEADROOM_RATIO == 0.05
        assert G5_MAE_MAX_INCREASE == 0.03

    def test_all_green_passes(self):
        out = evaluate_relative_gates(**_kwargs())
        assert out["passed"] is True
        assert out["violations"] == []
        assert out["version"] == RELATIVE_GATES_VERSION


class TestG1RelativeF1:
    def test_identical_recomputed_metrics_pass(self):
        cand = _axes()
        out = evaluate_relative_gates(**_kwargs(cand=cand))
        assert all(b["passed"] for b in
                   out["gates"]["G1_macro_f1_relative"].values())

    def test_degraded_confusion_matrix_fails_with_reason(self):
        cand = _axes(tp1=0, sparse_tp=0, ads_m1_tp=20)
        out = evaluate_relative_gates(**_kwargs(cand=cand))
        assert out["passed"] is False
        assert any("G1" in v for v in out["violations"])


class TestG2SparseClasses:
    def test_one_tp_loss_allowed_two_fails(self):
        ok = evaluate_relative_gates(**_kwargs(cand=_axes(sparse_tp=2)))
        assert not any("G2" in v for v in ok["violations"])
        bad = evaluate_relative_gates(**_kwargs(cand=_axes(sparse_tp=1)))
        assert any("G2" in v and "sparse" in v for v in bad["violations"])

    def test_no_absolute_recall_floor(self):
        """Mutlak %70 recall KALKTI: ref TP=1 iken aday TP=0 bile geçer."""
        ref = _axes(sparse_tp=1)
        cand = _axes(sparse_tp=0)
        out = evaluate_relative_gates(**_kwargs(ref=ref, cand=cand))
        assert not any("G2" in v for v in out["violations"])


class TestG3AdsSafety:
    def test_recall_and_precision_both_guarded(self):
        # ref -1: tp42 fp17 → recall .488, precision .712
        cand = _axes(ads_m1_tp=37)  # recall .430 = ref-.058 < -.05 → ihlal
        out = evaluate_relative_gates(**_kwargs(cand=cand))
        assert any("G3 ads/-1 recall" in v for v in out["violations"])
        cand2 = _axes(ads_m1_fp=45)  # precision .483 → ihlal
        out2 = evaluate_relative_gates(**_kwargs(cand=cand2))
        assert any("G3 ads/-1 precision" in v for v in out2["violations"])

    def test_within_margin_passes(self):
        cand = _axes(ads_m1_tp=39)  # recall .453 >= .488-.05=.438
        out = evaluate_relative_gates(**_kwargs(cand=cand))
        assert not any("G3" in v for v in out["violations"])


class TestG4SocialPositive:
    def test_fp_cap_is_ref_plus_five_percent(self):
        # ref fp1=5, n=149 → tavan 5 + floor(7.45)=12
        ok = evaluate_relative_gates(**_kwargs(cand=_axes(fp1=12)))
        assert not any("G4" in v for v in ok["violations"])
        bad = evaluate_relative_gates(**_kwargs(cand=_axes(fp1=13)))
        assert any("aşırı-üretkenlik" in v for v in bad["violations"])

    def test_tp_loss_more_than_one_fails(self):
        bad = evaluate_relative_gates(**_kwargs(cand=_axes(tp1=8)))
        assert any("G4" in v and "TP" in v for v in bad["violations"])


class TestG5GradeMae:
    def test_boundary_and_violation(self):
        ok = evaluate_relative_gates(
            **_kwargs(cand=_axes(mae=0.2 + G5_MAE_MAX_INCREASE)))
        assert not any("G5" in v for v in ok["violations"])
        bad = evaluate_relative_gates(
            **_kwargs(cand=_axes(mae=0.2 + G5_MAE_MAX_INCREASE + 0.001)))
        assert any("G5" in v for v in bad["violations"])


class TestG6SealedFlips:
    def test_uses_sealed_allowance(self):
        flips = _full_flips()
        flips["intent_type"] = 0.12  # mühürlü izin 0.11'in üstünde
        bad = evaluate_relative_gates(**_kwargs(cand_max_flips=flips))
        assert any("G6" in v and "mühürlü" in v for v in bad["violations"])

    def test_missing_candidate_flip_fails_closed(self):
        with pytest.raises(RelativeGateError, match="G6"):
            evaluate_relative_gates(**_kwargs(cand_max_flips={}))

    def test_removed_allowance_axis_rejected(self):
        """Codex v2 #4: eksen haritadan çıkarılarak kapı atlatılamaz."""
        allow = _full_allowances()
        del allow["agenda_theme"]
        with pytest.raises(RelativeGateError, match="agenda_theme"):
            evaluate_relative_gates(**_kwargs(flip_allowances=allow))

    def test_extra_allowance_axis_rejected(self):
        allow = _full_allowances()
        allow["hayalet"] = {"candidate_flip_allowance": 0.5}
        with pytest.raises(RelativeGateError, match="fazla"):
            evaluate_relative_gates(**_kwargs(flip_allowances=allow))

    def test_nan_flip_fails_closed(self):
        flips = _full_flips()
        flips["gt"] = float("nan")
        with pytest.raises(RelativeGateError, match="sonlu"):
            evaluate_relative_gates(**_kwargs(cand_max_flips=flips))


class TestG7Operational:
    def test_coverage_fallback_parse_cost(self):
        bad = evaluate_relative_gates(**_kwargs(
            cand_coverages=[{"missing_predictions": 2,
                             "fallback_rate": 0.02},
                            {"missing_predictions": 0,
                             "fallback_rate": 0.0}],
            cand_parses=[{"post_retry_ok": 0.97},
                         {"post_retry_ok": 1.0}],
            cand_cost_usd=0.6))
        joined = " | ".join(bad["violations"])
        assert "coverage" in joined
        assert "fallback" in joined
        assert "parse" in joined
        assert "maliyet" in joined

    def test_cost_reduction_boundary(self):
        ok = evaluate_relative_gates(**_kwargs(cand_cost_usd=0.5))
        assert not any("maliyet" in v for v in ok["violations"])

    def test_missing_ref_cost_fails_closed(self):
        with pytest.raises(RelativeGateError, match="maliyet"):
            evaluate_relative_gates(**_kwargs(ref_cost_usd=0.0))


class TestG0AxisIntegrity:
    """Codex v2 #1: eksen bazlı eksik tahmin F1 içinde saklanamaz."""

    def test_axis_missing_predictions_is_violation(self):
        cand = _axes(missing=3, n=146)
        out = evaluate_relative_gates(**_kwargs(cand=cand))
        assert any("G0" in v and "tahmin yok" in v
                   for v in out["violations"])
        assert any("aday n=146 != referans n=[149]" in v
                   for v in out["violations"])
        assert out["passed"] is False

    def test_class_support_mismatch_rejected(self):
        cand = _axes()
        cand["intent_type"]["class_support"] = {"1": 24, "0": 125}
        with pytest.raises(RelativeGateError, match="class_support"):
            evaluate_relative_gates(**_kwargs(cand=cand))

    def test_gold_n_mismatch_rejected(self):
        cand = _axes()
        cand["ads_class"]["gold_n"] = 140
        with pytest.raises(RelativeGateError, match="gold_n"):
            evaluate_relative_gates(**_kwargs(cand=cand))

    def test_missing_predictions_field_required(self):
        cand = _axes()
        del cand["intent_type"]["missing_predictions"]
        with pytest.raises(RelativeGateError,
                           match="missing_predictions"):
            evaluate_relative_gates(**_kwargs(cand=cand))


class TestG7StrictInputs:
    """Codex v2 #2/#3: tek kayıt, NaN, negatif maliyet fail-closed."""

    def test_single_coverage_for_two_repeats_rejected(self):
        with pytest.raises(RelativeGateError, match="tekrar sayısına"):
            evaluate_relative_gates(**_kwargs(
                cand_coverages=[{"missing_predictions": 0,
                                 "fallback_rate": 0.0}]))

    def test_single_parse_for_two_repeats_rejected(self):
        with pytest.raises(RelativeGateError, match="tekrar sayısına"):
            evaluate_relative_gates(**_kwargs(
                cand_parses=[{"post_retry_ok": 1.0}]))

    def test_missing_fallback_rate_rejected(self):
        with pytest.raises(RelativeGateError, match="fallback_rate"):
            evaluate_relative_gates(**_kwargs(
                cand_coverages=[{"missing_predictions": 0},
                                {"missing_predictions": 0,
                                 "fallback_rate": 0.0}]))

    def test_nan_fallback_rejected(self):
        with pytest.raises(RelativeGateError, match="sonlu"):
            evaluate_relative_gates(**_kwargs(
                cand_coverages=[{"missing_predictions": 0,
                                 "fallback_rate": float("nan")},
                                {"missing_predictions": 0,
                                 "fallback_rate": 0.0}]))

    def test_negative_candidate_cost_rejected(self):
        with pytest.raises(RelativeGateError, match="alt sınır"):
            evaluate_relative_gates(**_kwargs(cand_cost_usd=-0.5))

    def test_nan_parse_rejected(self):
        with pytest.raises(RelativeGateError, match="sonlu"):
            evaluate_relative_gates(**_kwargs(
                cand_parses=[{"post_retry_ok": float("nan")},
                             {"post_retry_ok": 1.0}]))


class TestG0DataIntegrityV3:
    """Codex v3: hepsi-None eşitliği, muhasebe, tamsayı, referans tabanı."""

    def test_all_none_axis_rejected(self):
        cand = _axes()
        cand["intent_type"] = {"macro_f1": 0.5}  # gold_n/n/missing yok
        with pytest.raises(RelativeGateError, match="gold_n"):
            evaluate_relative_gates(**_kwargs(cand=cand))

    def test_accounting_n_plus_missing_must_equal_gold(self):
        cand = _axes()
        cand["ads_class"]["n"] = 140  # missing=0 iken 140+0 != 149
        with pytest.raises(RelativeGateError, match="muhasebe"):
            evaluate_relative_gates(**_kwargs(cand=cand))

    def test_support_sum_must_equal_gold(self):
        cand = _axes()
        cand["intent_type"]["class_support"] = {"1": 25, "0": 100,
                                                "sparse": 4}  # 129 != 149
        with pytest.raises(RelativeGateError, match="sum"):
            evaluate_relative_gates(**_kwargs(cand=cand))

    def test_fractional_missing_rejected_not_truncated(self):
        cand = _axes()
        cand["agenda_theme"]["missing_predictions"] = 0.9
        with pytest.raises(RelativeGateError, match="TAM SAYI"):
            evaluate_relative_gates(**_kwargs(cand=cand))

    def test_reference_missing_predictions_rejected(self):
        """Codex v3 #5: kurcalanmış referans taban düşüremez."""
        ref = _axes(missing=2)
        with pytest.raises(RelativeGateError, match="REFERANS"):
            evaluate_relative_gates(**_kwargs(ref=ref))


class TestMetricValidationV3:
    """Codex v3 #2: G1-G5 girdileri aralık/tip doğrulamalı."""

    def test_macro_f1_above_one_rejected(self):
        cand = _axes()
        cand["intent_type"]["macro_f1"] = 2.0
        with pytest.raises(RelativeGateError, match="üst sınır"):
            evaluate_relative_gates(**_kwargs(cand=cand))

    def test_negative_mae_rejected(self):
        cand = _axes(mae=-0.1)
        with pytest.raises(RelativeGateError, match="alt sınır"):
            evaluate_relative_gates(**_kwargs(cand=cand))

    def test_tp_above_support_rejected(self):
        cand = _axes()
        cand["ads_class"]["class_metrics"]["-1"]["tp"] = 90  # support 86
        with pytest.raises(RelativeGateError, match="imkânsız"):
            evaluate_relative_gates(**_kwargs(cand=cand))

    def test_fractional_tp_rejected(self):
        cand = _axes()
        cand["intent_type"]["class_metrics"]["sparse"]["tp"] = 2.5
        with pytest.raises(RelativeGateError, match="TAM SAYI"):
            evaluate_relative_gates(**_kwargs(cand=cand))

    def test_metrics_support_must_match_class_support(self):
        cand = _axes()
        cand["ads_class"]["class_metrics"]["-1"]["support"] = 87
        with pytest.raises(RelativeGateError, match="class_support"):
            evaluate_relative_gates(**_kwargs(cand=cand))

    def test_extra_candidate_flip_axis_rejected(self):
        """Codex v3 #4: aday tarafında da fazla eksen reddedilir."""
        flips = _full_flips()
        flips["hayalet"] = 0.01
        with pytest.raises(RelativeGateError, match="fazla"):
            evaluate_relative_gates(**_kwargs(cand_max_flips=flips))


class TestMetricRecomputationV4:
    """REL-v4: hazır skor değil, confusion matrix karar otoritesidir."""

    def test_missing_majority_class_metrics_rejected(self):
        cand = _axes()
        del cand["intent_type"]["class_metrics"]["1"]
        with pytest.raises(RelativeGateError, match="eksik sınıflar"):
            evaluate_relative_gates(**_kwargs(cand=cand))

    def test_forged_macro_f1_inside_range_rejected(self):
        cand = _axes()
        cand["intent_type"]["macro_f1"] = 0.99
        with pytest.raises(RelativeGateError, match="yeniden hesaplanan"):
            evaluate_relative_gates(**_kwargs(cand=cand))

    def test_impossible_false_positive_count_rejected(self):
        cand = _axes()
        block = cand["intent_type"]["class_metrics"]["1"]
        block["fp"] = cand["intent_type"]["n"] - block["support"] + 1
        with pytest.raises(RelativeGateError, match="negatif örnek"):
            evaluate_relative_gates(**_kwargs(cand=cand))

    def test_forged_fn_rejected(self):
        cand = _axes()
        cand["intent_type"]["class_metrics"]["1"]["fn"] += 1
        with pytest.raises(RelativeGateError, match="yeniden hesaplanan"):
            evaluate_relative_gates(**_kwargs(cand=cand))

    def test_forged_recall_rejected(self):
        cand = _axes()
        cand["intent_type"]["class_metrics"]["1"]["recall"] = 0.99
        with pytest.raises(RelativeGateError, match="yeniden hesaplanan"):
            evaluate_relative_gates(**_kwargs(cand=cand))

    def test_forged_class_f1_rejected(self):
        cand = _axes()
        cand["intent_type"]["class_metrics"]["1"]["f1"] = 0.99
        with pytest.raises(RelativeGateError, match="yeniden hesaplanan"):
            evaluate_relative_gates(**_kwargs(cand=cand))


class TestFailClosed:
    def test_single_repeat_rejected(self):
        kw = _kwargs()
        kw["cand_axes_by_repeat"] = {"C1": _axes()}
        with pytest.raises(RelativeGateError, match="iki tekrar"):
            evaluate_relative_gates(**kw)

    def test_missing_class_metrics_rejected(self):
        cand = _axes()
        del cand["ads_class"]["class_metrics"]["-1"]
        with pytest.raises(RelativeGateError, match="class_metrics"):
            evaluate_relative_gates(**_kwargs(cand=cand))


class TestV4Additions:
    """Doğrulama turunda bulunan iki açığın kilidi."""

    def test_mae_above_one_rejected(self):
        """gt/ga 0/1 ekseninde MAE>1 imkânsız — şişirilmiş referans MAE'si
        G5 tavanını boşaltamaz."""
        ref = _axes(mae=1.5)
        with pytest.raises(RelativeGateError, match="üst sınır"):
            evaluate_relative_gates(**_kwargs(ref=ref))


class TestRealSealedArtifactLock:
    """'Gerçek artifact REL-v4'ten geçer' iddiası CI kilidi olur: sözleşme
    ileride mühürlü referansı reddedecek şekilde değişirse burada kırılır."""

    def test_committed_reference_artifact_validates(self):
        import json
        from pathlib import Path

        from app.core.benchmark.bakeoff_gates import (
            LLM_AXES,
            _validate_classification_axis,
        )

        path = (Path(__file__).resolve().parents[2] / "benchmark"
                / "reference_characterization_gemini-3.5-flash.json")
        doc = json.loads(path.read_text(encoding="utf-8"))
        for rid in ("R1", "R2"):
            for axis in LLM_AXES:
                out = _validate_classification_axis(
                    doc["repeats_data"][rid]["axes"][axis], axis,
                    f"real {rid}")
                assert out["missing_predictions"] == 0

    def test_committed_artifact_self_parity_passes_gates(self):
        import json
        from pathlib import Path

        path = (Path(__file__).resolve().parents[2] / "benchmark"
                / "reference_characterization_gemini-3.5-flash.json")
        doc = json.loads(path.read_text(encoding="utf-8"))
        reps = doc["repeats_data"]
        flips = {ax: blk["reference_max_flip"]
                 for ax, blk in doc["derived_allowances"]["per_axis"].items()}
        out = evaluate_relative_gates(
            {r: reps[r]["axes"] for r in ("R1", "R2")},
            {r: reps[r]["axes"] for r in ("R1", "R2")},
            flip_allowances=doc["derived_allowances"]["per_axis"],
            cand_max_flips=flips,
            cand_coverages=[reps[r]["coverage"] for r in ("R1", "R2")],
            cand_parses=[reps[r]["parse"] for r in ("R1", "R2")],
            ref_cost_usd=doc["provenance"]["budget"]["cost_settled_usd"],
            cand_cost_usd=doc["provenance"]["budget"]["cost_settled_usd"]
            * 0.4,
            ref_ceiling_charges=doc["provenance"]["budget"][
                "ceiling_charges"],
            cand_ceiling_charges=0)
        assert out["passed"] is True


class TestG7CeilingChargesV5:
    """Codex Katman-A #3: ceiling-charge maliyet kapisini kandiramaz."""

    def test_cand_ceiling_charge_is_violation(self):
        out = evaluate_relative_gates(**_kwargs(cand_ceiling_charges=2))
        assert out["passed"] is False
        assert any("aday ceiling_charges 2" in v for v in out["violations"])

    def test_ref_ceiling_charge_is_violation(self):
        """Referans tarafi da mutlak: usage'siz cagri tabani sisirip
        maliyet kapisini adaya AVANTAJLI carpitirdi."""
        out = evaluate_relative_gates(**_kwargs(ref_ceiling_charges=1))
        assert out["passed"] is False
        assert any("referans ceiling_charges 1" in v
                   for v in out["violations"])

    def test_zero_charges_pass_and_reported(self):
        out = evaluate_relative_gates(**_kwargs())
        blk = out["gates"]["G7_operational"]["ceiling_charges"]
        assert blk == {"ref": 0, "cand": 0, "passed": True}

    def test_invalid_ceiling_charge_types_rejected(self):
        with pytest.raises(RelativeGateError, match="TAM SAYI"):
            evaluate_relative_gates(**_kwargs(cand_ceiling_charges=0.5))
        with pytest.raises(RelativeGateError, match="negatif"):
            evaluate_relative_gates(**_kwargs(ref_ceiling_charges=-1))
        with pytest.raises(RelativeGateError, match="TAM SAYI"):
            evaluate_relative_gates(**_kwargs(cand_ceiling_charges=None))
