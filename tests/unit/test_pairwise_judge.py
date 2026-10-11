"""pairwise_judge — sahte `ai` ile DB'siz/ağsız."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.benchmark import claude_gates as cg
from app.core.benchmark import pairwise_judge as pj
from app.core.benchmark.reference_characterization import CharacterizationError

PLAN = Path(__file__).resolve().parents[2] / "benchmark" / "claude_bakeoff_plan.json"


def pair(pid="P-blog-001", fmt="blog", ws=1, a="içerik A", b="içerik B"):
    return {"pair_id": pid, "format": fmt, "ws": ws, "key_label": "kahve", "patron": True,
            "A": [["Başlık", a]], "B": [["Başlık", b]]}


class FakeAI:
    """for_stage(...).complete_json(prompt) -> replies sırayla; prompt'a göre karar verebilir."""

    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def for_stage(self, stage, **kw):
        outer = self
        outer.calls.append({"stage": stage, **kw})

        class _S:
            def complete_json(_s, prompt, max_tokens=0, temperature=0.3, response_schema=None):
                outer.calls[-1].update(prompt=prompt, max_tokens=max_tokens, schema=response_schema)
                r = outer.replies.pop(0)
                if isinstance(r, Exception):
                    raise r
                return r if isinstance(r, str) else json.dumps(r)
        return _S()


def rep(pref, conf=0.8):
    return {"preference": pref, "confidence": conf, "dimension_scores": {}}


def by_content(winner_text):
    """Gösterilen sırada winner_text hangi pozisyondaysa onu seçen hakem."""
    class _AI(FakeAI):
        def for_stage(self, stage, **kw):
            outer = self
            base = super().for_stage(stage, **kw)

            class _S:
                def complete_json(_s, prompt, max_tokens=0, response_schema=None, **k):
                    outer.calls[-1].update(max_tokens=max_tokens, schema=response_schema)
                    a_pos = prompt.index("=== İÇERİK A ===")
                    b_pos = prompt.index("=== İÇERİK B ===")
                    w = prompt.index(winner_text)
                    outer.calls[-1]["prompt"] = prompt
                    return json.dumps(rep("A" if a_pos < w < b_pos else "B"))
            return _S()
    return _AI([])


def test_s_formula_consistent_judge_and_swap_prompts():
    ai = by_content("içerik A")
    res = pj.judge_pairs([pair()], ai=ai)["P-blog-001"]
    assert (res["S_AB"], res["S_BA"], res["S"]) == (1.0, 0.0, 1.0)
    assert res["position_bias"] is False and res["excluded"] is False and len(res["raw"]) == 2
    p_ab, p_ba = ai.calls[0]["prompt"], ai.calls[1]["prompt"]
    assert p_ab.index("içerik A") < p_ab.index("içerik B") and p_ba.index("içerik B") < p_ba.index("içerik A")
    assert ai.calls[0]["provider"] == "deepseek" and ai.calls[0]["model"] == "deepseek-v4-pro"
    assert ai.calls[0]["stage"] == "pairwise_judge" and ai.calls[0]["max_tokens"] == 600
    sch = ai.calls[0]["schema"]
    assert sch["additionalProperties"] is False and sch["properties"]["preference"]["enum"] == ["A", "B", "tie"]
    assert "yapi" in sch["properties"]["dimension_scores"]["properties"]
    assert "içerik" in p_ab and pj.RUBRIC_V1.splitlines()[0] in p_ab


def test_other_direction_and_ties_formula():
    res = pj.judge_pairs([pair()], ai=by_content("içerik B"))["P-blog-001"]
    assert res["S"] == 0.0
    # (A,B) berabere, (B,A) berabere -> 0,5 (bayrak yok); (A,B) A, (B,A) berabere -> 0,75
    r = pj.judge_pairs([pair()], ai=FakeAI([rep("tie"), rep("tie")]))["P-blog-001"]
    assert r["S"] == 0.5 and r["position_bias"] is False
    r = pj.judge_pairs([pair()], ai=FakeAI([rep("A"), rep("tie")]))["P-blog-001"]
    assert r["S"] == 0.75


@pytest.mark.parametrize("first,second", [("A", "A"), ("B", "B")])
def test_position_bias_is_flagged_with_S_half(first, second):
    r = pj.judge_pairs([pair()], ai=FakeAI([rep(first), rep(second)]))["P-blog-001"]
    assert r["S"] == 0.5 and r["position_bias"] is True and not r["excluded"]


def test_tie_band_mapping():
    f = pj.judge_pref_from_S
    # varsayilan bant plan K4: |S-0,5| < 0,05 -> berabere
    assert (f(1.0), f(0.6), f(0.0), f(0.4), f(0.55), f(0.45), f(0.54), f(0.46), f(0.5), f(None)) == \
           ("A", "A", "B", "B", "A", "B", "tie", "tie", "tie", None)
    assert f(0.54, tie_band=0.05) == "tie" and f(0.55, tie_band=0.05) == "A"


def test_failed_or_unparseable_direction_is_excluded_not_half():
    r = pj.judge_pairs([pair()], ai=FakeAI([rep("A"), "this is not json"]))["P-blog-001"]
    assert r["excluded"] and r["S"] is None and r["S_AB"] == 1.0 and r["S_BA"] is None
    assert r["reason"] == "insufficient:BA_failed" and r["raw"][1]["error"] == "unparseable"
    r = pj.judge_pairs([pair()], ai=FakeAI([RuntimeError("boom"), rep("A")]))["P-blog-001"]
    assert r["excluded"] and r["raw"][0]["error"] == "call_failed:RuntimeError" and r["S"] is None
    r = pj.judge_pairs([pair()], ai=FakeAI([{"preference": "C"}, rep("A")]))["P-blog-001"]
    assert r["excluded"] and r["raw"][0]["error"] == "invalid_preference"
    assert pj.judge_pref_from_S(r["S"]) is None


def test_budget_error_propagates_and_consecutive_failures_abort():
    with pytest.raises(CharacterizationError):
        pj.judge_pairs([pair()], ai=FakeAI([CharacterizationError("tavan")]))
    pairs = [pair(f"P-blog-{i:03d}") for i in range(1, 5)]
    with pytest.raises(pj.JudgeAborted):
        pj.judge_pairs(pairs, ai=FakeAI([RuntimeError("x")] * 20), max_consecutive_failures=3)


def test_prompt_carries_no_provider_info_and_provenance_stable():
    ai = by_content("içerik A")
    pj.judge_pairs([pair()], ai=ai)
    for c in ai.calls:
        low = c["prompt"].lower()
        assert not any(w in low for w in ("gemini", "claude", "anthropic", "deepseek"))
    assert pj.RUBRIC_SHA == pj.judge_provenance()["rubric_sha256"] and len(pj.RUBRIC_SHA) == 64


# ── kapı girdisi ──
def synthetic(n, cand_wins, ties=0, patron_n=None, fmt="blog", judge_agrees=True):
    """n çift: ilk cand_wins patron cand, sonra ties berabere, kalan ref. A/B = cand/ref sabit."""
    pairs, secret, hard, patron, judge = [], {}, {}, {}, {}
    for i in range(n):
        pid = f"P-{fmt}-{i + 1:03d}"
        pairs.append(pair(pid, fmt=fmt, ws=1 + i % 2))
        secret[pid] = {"A": "cand", "B": "ref", "hard_rule_cand_ok": True, "hard_rule_ref_ok": True}
        hard[pid] = {"ref_ok": True, "cand_ok": True}
        outcome = "cand" if i < cand_wins else "tie" if i < cand_wins + ties else "ref"
        if patron_n is None or i < patron_n:
            patron[pid] = outcome
        S = {"cand": 1.0, "tie": 0.5, "ref": 0.0}[outcome] if judge_agrees else 0.5
        judge[pid] = {"S": S, "excluded": False}
    return pairs, secret, hard, patron, judge


def test_gate_input_conversion_and_kappa():
    pairs, secret, hard, patron, judge = synthetic(10, 6, ties=2)
    secret["P-blog-001"] = {"A": "ref", "B": "cand", "hard_rule_cand_ok": True, "hard_rule_ref_ok": True}
    judge["P-blog-001"] = {"S": 1.0, "excluded": False}     # hakem A'yı seçti, A=ref -> ref
    judge["P-blog-002"] = {"S": None, "excluded": True}     # dışlanan -> judge_pref None
    rows = pj.judge_pairs_to_gate_input(pairs, judge, patron, secret, hard)
    assert rows[0]["judge_pref"] == "ref" and rows[1]["judge_pref"] is None and rows[2]["judge_pref"] == "cand"
    assert rows[0]["patron_pref"] == "cand" and rows[0]["format"] == "blog" and rows[0]["ws"] == 1
    k = pj.kappa_patron_vs_judge(rows)
    assert k["n"] == 9 and k["kappa"] is not None
    hard.pop("P-blog-003")
    with pytest.raises(ValueError, match="hard_rules"):
        pj.judge_pairs_to_gate_input(pairs, judge, patron, secret, hard)
    assert pj.hard_rules_from_secret({"p": {"hard_rule_ref_ok": 1, "hard_rule_cand_ok": 0}}) == \
           {"p": {"ref_ok": True, "cand_ok": False}}


@pytest.fixture(scope="module")
def th():
    return cg.thresholds(cg.load_plan(PLAN))


def test_gate_insufficient_for_15_patron_pairs(th):
    pairs, secret, hard, patron, judge = synthetic(30, 25, patron_n=15)
    rows = pj.judge_pairs_to_gate_input(pairs, judge, patron, secret, hard)
    r = cg.evaluate_content(rows, th)[0]
    assert r.n == 15 and r.status == cg.INSUFFICIENT          # plan §7.5: patron 15 < 30


def test_gate_pass_and_fail_on_synthetic_40_pairs(th):
    pairs, secret, hard, patron, judge = synthetic(40, 32, ties=2)
    rows = pj.judge_pairs_to_gate_input(pairs, judge, patron, secret, hard)
    good = cg.evaluate_content(rows, th)[0]
    assert good.n == 40 and good.status == cg.PASS and good.detail["kappa"] == pytest.approx(1.0)
    pairs, secret, hard, patron, judge = synthetic(40, 4, ties=2)
    bad = cg.evaluate_content(pj.judge_pairs_to_gate_input(pairs, judge, patron, secret, hard), th)[0]
    assert bad.status == cg.FAIL
