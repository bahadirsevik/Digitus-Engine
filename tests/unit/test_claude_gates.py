"""Claude bake-off kapıları G1-G7 — DB'siz, sentetik girdiler."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from app.core.benchmark import claude_gates as cg
from app.core.benchmark.claude_gates import (
    FAIL, INSUFFICIENT, NOT_MEASURED, PASS, GateParseError, GateResult)
from app.core.benchmark.patron_labels import labels_from_doc
from app.core.benchmark.reference_characterization import CharacterizationError

PLAN = Path(__file__).resolve().parents[2] / "benchmark" / "claude_bakeoff_plan.json"


@pytest.fixture(scope="module")
def plan():
    return cg.load_plan(PLAN)


@pytest.fixture(scope="module")
def th(plan):
    return cg.thresholds(plan)


def test_thresholds_roundtrip_sealed_plan(th):
    assert (th.confidence, th.g1_recall_lb_min, th.g1_offtopic_delta_max) == (0.90, -0.03, 0.0)
    assert (th.g2_wilson_lb_min, th.g2_n_min, th.g2_kappa_min, th.g2_hardrule_lb_min) == (0.45, 30, 0.50, -0.05)
    assert (th.g3_halluc_ub_max, th.g3_patron_lb_min) == (0.0, -0.3)
    assert (th.g4_fatal_max, th.g4_delivery_lb_min, th.g4_trunc_ub_max) == (0, -0.01, 0.01)
    assert th.g5_flip_ub_max == 0.05
    assert (th.g6a_stage_p95_ratio_max, th.g6a_task_p99_stale_frac, th.g6b_task_p95_ratio_max) == (1.5, 0.5, 1.25)
    assert (th.mde_recall_lb_min, th.mde_content_wilson_lb_gt, th.mde_content_point_min,
            th.mde_profile_ub_max) == (0.05, 0.50, 0.60, -0.5)
    assert th.stale_thresholds_s["ADS_TASK_STALE_SECONDS"] == 1200 and th.alpha == pytest.approx(0.10)


def test_thresholds_fail_on_mutated_text_and_tampered_seal(plan, tmp_path):
    bad = copy.deepcopy(plan)
    bad["gates"]["G1_selection"] = bad["gates"]["G1_selection"].replace("≥ −0,03", "≥ x")
    with pytest.raises(GateParseError):
        cg.thresholds(bad)
    bad2 = copy.deepcopy(plan)
    bad2["gates"]["G4_reliability"] = bad2["gates"]["G4_reliability"].replace("LB90(Δ) ≥ −0,01", "LB95(Δ) ≥ −0,01")
    with pytest.raises(GateParseError):
        cg.thresholds(bad2)
    f = tmp_path / "p.json"
    f.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(CharacterizationError):
        cg.load_plan(f)


def test_bootstrap_brackets_known_delta_and_is_deterministic():
    pairs = []
    for c in range(12):                      # küme başına Δ = 0,10 +- 0,05
        shift = (-1, 0, 1)[c % 3]
        for i in range(20):
            pairs.append((c, 1 if i < 12 + shift else 0, 1 if i < 10 else 0))
    a = cg.clustered_bootstrap_delta(pairs)
    assert a == cg.clustered_bootstrap_delta(pairs)
    point, lb, ub = a
    assert point == pytest.approx(0.10) and lb < 0.10 < ub and 0.05 < lb and ub < 0.15
    assert cg.clustered_bootstrap_delta([(0, 1, 0), (0, 1, 0)])[1:] == (None, None)   # tek küme -> CI yok


def test_wilson_matches_hand_computation():
    p, lb, ub = cg.wilson_interval(18, 4, 30, alpha=0.10)
    assert p == pytest.approx(20 / 30)
    z = 1.6448536269514722
    c = (p + z * z / 60) / (1 + z * z / 30)
    h = z * ((p * (1 - p) / 30 + z * z / 3600) ** 0.5) / (1 + z * z / 30)
    assert (lb, ub) == (pytest.approx(c - h), pytest.approx(c + h))
    assert lb == pytest.approx(0.5166, abs=1e-3) and ub == pytest.approx(0.7892, abs=1e-3)


def test_kappa_and_percentiles():
    assert cg.cohen_kappa(list("aabb"), list("aabb")) == 1.0
    assert cg.cohen_kappa(list("aabb"), list("abab")) == pytest.approx(0.0)
    assert cg.cohen_kappa(list("aaaa"), list("aaaa")) is None
    assert cg.p95(list(range(101))) == 95 and cg.p99([1, 2, 3, 4]) == pytest.approx(3.97)


# -- G1 ---------------------------------------------------------------------

def _g1_inputs(cand_hits, ref_hits, n_ws=4, n_pos=20):
    doc = {"workspace_id": 1, "current_positive": [
        {"canonical_keyword": f"kw {i}", "labels": ["SEO"]} for i in range(n_pos)]}
    lab = labels_from_doc("t", doc, "x")
    uni = {i: f"kw {i}" for i in range(n_pos)}
    mk = lambda k: {(w, "SEO"): set(range(k(w))) for w in range(n_ws)}  # noqa: E731
    return mk(ref_hits), mk(cand_hits), {w: lab for w in range(n_ws)}, {w: uni for w in range(n_ws)}


def test_g1_pass_with_superiority(th):
    ref, cand, lab, uni = _g1_inputs(lambda w: 20, lambda w: 10)
    off = {w: {"cand_only": (0, 5), "ref_only": (1, 5)} for w in range(4)}
    r = cg.evaluate_selection(ref, cand, lab, uni, off, th)
    assert r.status == PASS and r.point == pytest.approx(0.5) and r.detail["superiority_recall"]
    assert r.n == 80 and r.detail["cand_hits"] == 80


def test_g1_hard_fail_far_below_and_insufficient_cases(th):
    ref, cand, lab, uni = _g1_inputs(lambda w: 0, lambda w: 20)
    r = cg.evaluate_selection(ref, cand, lab, uni, None, th)
    assert r.status == FAIL and r.detail["hard"]
    ref, cand, lab, uni = _g1_inputs(lambda w: 20, lambda w: 20)
    assert cg.evaluate_selection(ref, cand, lab, uni, None, th).status == INSUFFICIENT   # konu-dışı bekliyor
    ref, cand, lab, uni = _g1_inputs(lambda w: 10 + (-2, 2, -3, 3)[w], lambda w: 10)
    off = {w: {"cand_only": (0, 5), "ref_only": (0, 5)} for w in range(4)}
    straddle = cg.evaluate_selection(ref, cand, lab, uni, off, th)
    assert straddle.status == INSUFFICIENT and not straddle.detail["hard"]               # CI eşiği içeriyor


def test_g1_rejects_asymmetric_pools(th):
    ref, cand, lab, uni = _g1_inputs(lambda w: 20, lambda w: 20)
    cand.pop((0, "SEO"))
    with pytest.raises(ValueError):
        cg.evaluate_selection(ref, cand, lab, uni, None, th)


# -- G4 ---------------------------------------------------------------------

def _ev(n=30, ok=True, ws=("a", "b", "c"), **kw):
    return [{"stage": "ads_intent", "ws": ws[i % len(ws)], "ok_first_try": ok, "delivered": True,
             "truncated": False, **kw} for i in range(n)]


def test_g4_pass_fail_and_not_measured(th):
    assert cg.evaluate_reliability(_ev(), _ev(), th).status == PASS
    r = cg.evaluate_reliability(_ev(), _ev(retry_reason="refusal_x"), th)
    assert r.status == FAIL and r.detail["fatal_refusal_cand"] == 30 and r.detail["hard"]
    assert cg.evaluate_reliability(_ev(), _ev(finish_reason="refusal")[:1] + _ev(29), th).status == FAIL
    worse = cg.evaluate_reliability(_ev(), _ev(ok=False), th)
    assert worse.status == FAIL and worse.detail["states"]["parse"] == FAIL
    trunc = cg.evaluate_reliability(_ev(), [dict(e, truncated=i % 2 == 0) for i, e in enumerate(_ev())], th)
    assert trunc.status == FAIL and trunc.detail["states"]["truncation"] == FAIL
    assert cg.evaluate_reliability([], _ev(), th).status == NOT_MEASURED


# -- G5 / G6 ----------------------------------------------------------------

def test_g5_not_measured_with_one_repeat(th):
    pairs = [(w, 0.0, 0.0) for w in range(4)]
    assert cg.evaluate_noise(pairs, 1, th).status == NOT_MEASURED
    assert cg.evaluate_noise(pairs, 2, th).status == PASS


def _lat(mult, n=40):
    return [{"stage": s, "latency_ms": (100 + i) * mult} for s in ("a", "b") for i in range(n)]


def test_g6a_pass_fail_and_g6b_report(th):
    stale = th.stale_thresholds_s
    ok = cg.evaluate_latency(_lat(1), _lat(1.2), [100.0] * 20, [100.0] * 20, stale, th)
    assert ok.status == PASS and ok.detail["G6b"]["within"] and ok.point == pytest.approx(1.2)
    slow = cg.evaluate_latency(_lat(1), _lat(2), [100.0] * 20, [100.0] * 20, stale, th)
    assert slow.status == FAIL and slow.detail["hard"]
    late = cg.evaluate_latency(_lat(1), _lat(1), [100.0] * 20, [500.0] * 20, stale, th)   # 500 > 0,5*900
    assert late.status == FAIL and late.detail["task_state"] == FAIL
    assert cg.evaluate_latency(_lat(1), _lat(1), [100.0], [], stale, th).status == INSUFFICIENT
    named = cg.evaluate_latency(_lat(1), _lat(1), {"ADS_TASK_STALE_SECONDS": [100.0]},
                                {"ADS_TASK_STALE_SECONDS": [599.0]}, stale, th)
    assert named.status == PASS            # 599 < 0,5*1200


# -- G2 ---------------------------------------------------------------------

def _pairs(fmt, patron, judge=None, ws=("a", "b")):
    judge = judge or patron
    return [{"format": fmt, "ws": ws[i % 2], "patron_pref": p, "judge_pref": j,
             "hard_rule_cand_ok": True, "hard_rule_ref_ok": True}
            for i, (p, j) in enumerate(zip(patron, judge))]


def test_g2_insufficient_with_15_patron_pairs_and_pass_with_40(th):
    few = ["cand"] * 12 + ["tie"] * 2 + ["ref"]
    r = cg.evaluate_content(_pairs("Blog", few), th)[0]
    assert r.status == INSUFFICIENT and r.n == 15 and r.detail["wins"] == 12
    many = ["cand"] * 22 + ["tie"] * 4 + ["ref"] * 14
    r = cg.evaluate_content(_pairs("Blog", many), th)[0]
    assert r.status == PASS and r.detail["kappa"] == 1.0 and not r.detail["superiority_content"]
    strong = ["cand"] * 38 + ["ref"] * 2
    assert cg.evaluate_content(_pairs("Blog", strong), th)[0].detail["superiority_content"]
    # DeepSeek çiftleri n'e sayılmaz
    ds = _pairs("RSA", ["cand"] * 15 + [None] * 40, ["cand"] * 15 + ["ref"] * 40)
    assert cg.evaluate_content(ds, th)[0].n == 15


def test_g2_fail_on_low_wilson_or_kappa_with_enough_n(th):
    lose = ["ref"] * 30 + ["cand"] * 10
    assert cg.evaluate_content(_pairs("Blog", lose), th)[0].status == FAIL
    patron = ["cand"] * 28 + ["ref"] * 12
    judge = ["ref" if i % 2 else "cand" for i in range(40)]
    r = cg.evaluate_content(_pairs("Blog", patron, judge), th)[0]
    assert r.detail["states"]["kappa"] == FAIL and r.status == FAIL


# -- G3 / G7 / pilot / rapor -------------------------------------------------

def test_g3_and_g7(th, plan):
    rows = [{"ws": i, "halluc_cand": 1, "halluc_ref": 3, "patron_cand": 4, "patron_ref": 4} for i in range(10)]
    g3 = cg.evaluate_profile(rows, th)
    assert g3.status == PASS and g3.detail["superiority_profile"]
    bad = [dict(r, halluc_cand=5) for r in rows]
    assert cg.evaluate_profile(bad, th).status == FAIL
    g7 = cg.evaluate_cost(1.0, 1.4, {"G1": False}, plan["n_max_3_2"], None)
    assert g7.status == FAIL and g7.detail["credit_go"] is None and "patron yazılı hükmü" in g7.detail["credit_go_label"]
    ok = cg.evaluate_cost(1.0, 1.4, {"G1": True}, plan["n_max_3_2"], 50)
    assert ok.status == PASS and ok.detail["n_max_covers_k8"]["Aday"] is False
    assert ok.detail["n_max_covers_k8"]["Yalnız Y Haiku"] is None and ok.detail["credit_go"] is None


def test_pilot_label_rules_and_report(plan):
    g = lambda gate, st: GateResult(gate, st)  # noqa: E731
    assert cg.pilot_label([g("G1", PASS), g("G4", PASS), g("G5", NOT_MEASURED)]) == cg.LABEL_GOOD
    assert cg.pilot_label([g("G1", PASS), g("G4", FAIL)]) == cg.LABEL_BAD
    assert cg.pilot_label([g("G1", INSUFFICIENT), g("G4", PASS)]) == cg.LABEL_EVIDENCE
    assert cg.pilot_label([g("G1", INSUFFICIENT), g("G6a", FAIL)]) == cg.LABEL_BAD
    assert cg.pilot_label([g("G5", NOT_MEASURED)]) == cg.LABEL_EVIDENCE
    assert cg.pilot_label([g("G1", PASS), g("G7", FAIL)]) == cg.LABEL_GOOD
    md = cg.render_report([g("G1", PASS), GateResult("G5", NOT_MEASURED, detail={"summary": "x"}),
                           GateResult("G2/Blog", INSUFFICIENT, 0.6, 0.5, 0.7, 15,
                                      {"wins": 9, "ties": 2, "losses": 4})], plan)
    assert "ölçülemedi" in md and "9/2/4" in md and "Her şey Haiku" in md and "294" in md
