"""Faz 1A replay + asama karari uyumu. DB'siz, ucretsiz."""
from __future__ import annotations

import json

import pytest

from app.core.benchmark.reference_characterization import (
    CharacterizationError, verify_seal,
)
from app.core.benchmark.stage_replay import compare_stage_outputs, replay_capture
from app.core.telemetry.ai_cost_budget import BudgetExceeded
from app.core.telemetry.usage import UsageCollector

SCHEMA = {"type": "object", "properties": {"x": {}}}


def _call(seq, stage="s_a2", ids=(1, 2), raw=None, kind="initial", attempt=1,
          result_key="results"):
    return {"seq": seq, "stage": stage, "provider": "gemini", "model": "g",
            "thinking_level": "low", "prompt": f"PROMPT-{seq}",
            "response_schema": SCHEMA, "max_tokens": 77, "raw_text": raw,
            "attempt_kind": kind, "attempt": attempt,
            "expected_ids": list(ids), "result_key": result_key,
            "id_field": "keyword_id", "response_id_field": "id", "error": None}


def _res(*items):
    return json.dumps({"results": list(items)})


class Rec:
    """for_stage'i kaydeden, collector'a usage yazan RoutedAIService benzeri."""

    def __init__(self, collector, responses):
        self.collector, self.responses, self.seen = collector, list(responses), []

    def for_stage(self, stage, *, model=None, thinking_level=None, provider=None):
        outer = self

        class _S:
            def complete_json(self, prompt, **kw):
                outer.seen.append({"stage": stage, "model": model,
                                   "provider": provider, "prompt": prompt,
                                   "schema": kw["response_schema"],
                                   "max_tokens": kw["max_tokens"]})
                outer.collector.record(stage=stage, model=model or "?",
                                       prompt_tokens=10, candidates_tokens=5,
                                       thoughts_tokens=1, latency_ms=100,
                                       provider=provider)
                r = outer.responses.pop(0)
                if isinstance(r, BaseException):
                    raise r
                return r
        return _S()


def _collector():
    return UsageCollector(flush_every=10**9)


def test_replay_resends_exact_prompt_and_schema_with_candidate_model(tmp_path):
    col = _collector()
    ai = Rec(col, [_res({"id": 1, "v": "a"}, {"id": 2, "v": "b"})])
    calls = [_call(0, raw="x"),
             _call(1, kind="targeted_retry", ids=(2,)),     # atlanir
             _call(2, attempt=2)]                           # atlanir
    art = replay_capture(calls, ai=ai, provider="anthropic",
                         model_for_stage={"s_a2": "haiku-5.5"}, collector=col,
                         out_path=tmp_path / "o" / "r.json", source_run_id="r1")
    assert ai.seen == [{"stage": "s_a2", "model": "haiku-5.5",
                        "provider": "anthropic", "prompt": "PROMPT-0",
                        "schema": SCHEMA, "max_tokens": 77}]
    c = art["calls"][0]
    assert c["ok"] and c["parsed"] == {"1": {"id": 1, "v": "a"},
                                       "2": {"id": 2, "v": "b"}}
    assert c["usage"] == {"prompt": 10, "candidates": 5, "thoughts": 1,
                          "n_events": 1} and c["latency_ms"] == 100
    assert art["n_calls"] == 1 and art["n_ok"] == 1 and art["n_error"] == 0
    assert art["usage_totals"]["anthropic"]["prompt"] == 10
    verify_seal(json.loads((tmp_path / "o" / "r.json").read_text("utf-8")))


def test_replay_records_error_without_aborting(tmp_path, monkeypatch):
    monkeypatch.setattr("app.core.engine.ai_runner._sleep", lambda s: None)
    col = _collector()
    ai = Rec(col, [ValueError("bad1"), ValueError("bad2"),     # 1. cagri: 2 deneme
                   _res({"id": 3, "v": "z"})])
    calls = [_call(0, ids=(1, 2)), _call(1, ids=(3,))]
    art = replay_capture(calls, ai=ai, provider="anthropic",
                         model_for_stage={"s_a2": "m"}, collector=col,
                         out_path=tmp_path / "r.json")
    assert [c["ok"] for c in art["calls"]] == [False, True]
    assert "AiStageError" in art["calls"][0]["error"]
    assert art["calls"][0]["usage"]["n_events"] == 2 and art["n_error"] == 1


def test_replay_missing_model_fails_closed_and_budget_aborts(tmp_path):
    col = _collector()
    ai = Rec(col, [])
    with pytest.raises(CharacterizationError):
        replay_capture([_call(0)], ai=ai, provider="p", model_for_stage={},
                       collector=col, out_path=tmp_path / "r.json")
    assert ai.seen == []
    ai = Rec(col, [BudgetExceeded("cap")])
    with pytest.raises(BudgetExceeded):
        replay_capture([_call(0), _call(1)], ai=ai, provider="p",
                       model_for_stage={"s_a2": "m"}, collector=col,
                       out_path=tmp_path / "b.json")
    art = json.loads((tmp_path / "b.json").read_text("utf-8"))
    assert art["n_calls"] == 1 and art["aborted"]      # kismi artifact yazildi


def test_replay_single_stage_uses_run_single(tmp_path):
    col = _collector()
    ai = Rec(col, [json.dumps({"families": [{"n": 1}]})])
    art = replay_capture([_call(0, stage="a1", ids=(), result_key="families")],
                         ai=ai, provider="p", model_for_stage={"a1": "m"},
                         stages={"a1"}, collector=col, out_path=tmp_path / "r.json")
    assert art["calls"][0]["parsed"] == [{"n": 1}]


def test_compare_stage_outputs_agreement():
    long_a, long_b = "a" * 90, "b" * 90
    ref = [_call(0, ids=(1, 2, 3), raw=_res(
        {"id": 1, "cls": "A", "t": "x", "why": long_a},
        {"id": 2, "cls": "B", "t": "x", "why": long_a},
        {"id": 3, "cls": "A", "t": "x"})),
        _call(1, stage="a1", ids=(), result_key="families",
              raw=json.dumps({"families": [{"n": 1}]}))]
    cand = {"provider": "anthropic", "source_calls_sha256": "s", "calls": [
        {"source_seq": 0, "stage": "s_a2", "ok": True, "parsed": {
            "1": {"id": 1, "cls": "A", "t": "x", "why": long_b},   # uyumlu
            "2": {"id": 2, "cls": "A", "t": "x", "why": long_b},   # cls farkli
            "4": {"id": 4, "cls": "A", "t": "x"}}},                # yalniz aday
        {"source_seq": 1, "stage": "a1", "ok": True, "parsed": [{"n": 1}]}]}
    out = compare_stage_outputs(ref, cand)["stages"]
    s = out["s_a2"]
    assert (s["n_both"], s["n_agree"]) == (2, 1)
    assert s["decision_agreement"] == 0.5
    assert s["only_ref_ids"] == ["3"] and s["only_cand_ids"] == ["4"]
    assert s["fields"]["cls"] == {"n": 2, "agree": 1, "rate": 0.5}
    assert s["fields"]["t"]["rate"] == 1.0 and "why" not in s["fields"]
    assert out["a1"]["n_single"] == 1 and out["a1"]["n_single_agree"] == 1
