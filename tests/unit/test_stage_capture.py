"""Faz 1A yakalama: ai_runner.CAPTURE_SINK + StageCapture. DB'siz, ucretsiz."""
from __future__ import annotations

import json

import pytest

from app.core.benchmark.reference_characterization import CharacterizationError
from app.core.benchmark.stage_capture import (
    StageCapture, load_capture, load_capture_index, prompt_sha,
)
from app.core.engine import ai_runner as AI

SCHEMA = {"type": "object"}


class FakeAI:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def for_stage(self, stage, *, model=None, thinking_level=None, provider=None):
        outer = self

        class _S:
            def complete_json(self, prompt, **kw):
                outer.calls.append((stage, model, provider, prompt))
                r = outer.responses.pop(0)
                if isinstance(r, BaseException):
                    raise r
                return r
        return _S()


def _batch(ai, ids, **kw):
    return AI.run_batch(
        ai, stage="s_a2", model="m1", thinking_level="low",
        rows=[{"keyword_id": i} for i in ids],
        build_prompt=lambda sub: "P:" + ",".join(str(r["keyword_id"]) for r in sub),
        schema=SCHEMA, max_tokens=99, **kw)


def _ok(*ids):
    return json.dumps({"results": [{"id": i, "v": i} for i in ids]})


def test_sink_none_leaves_engine_unchanged():
    assert AI.CAPTURE_SINK is None
    ai = FakeAI([_ok(1, 2)])
    assert set(_batch(ai, [1, 2])) == {1, 2}
    assert len(ai.calls) == 1


def test_sink_receives_initial_targeted_retry_and_failed(monkeypatch):
    got = []
    monkeypatch.setattr(AI, "CAPTURE_SINK", got.append)
    ai = FakeAI([_ok(1), _ok(2)])               # 2 ilk cevapta eksik
    _batch(ai, [1, 2])
    assert [(c["attempt_kind"], c["expected_ids"], c["prompt"]) for c in got] == [
        ("initial", [1, 2], "P:1,2"), ("targeted_retry", [2], "P:2")]
    c = got[0]
    assert (c["stage"], c["provider"], c["model"], c["thinking_level"]) == (
        "s_a2", "gemini", "m1", "low")
    assert c["response_schema"] == SCHEMA and c["max_tokens"] == 99
    assert c["raw_text"] == _ok(1) and c["error"] is None and c["attempt"] == 1
    assert (c["result_key"], c["id_field"], c["response_id_field"]) == (
        "results", "keyword_id", "id")

    got.clear()
    ai = FakeAI([ConnectionError("boom"), _ok(1)])   # basarisiz + 2. deneme
    monkeypatch.setattr(AI, "_sleep", lambda s: None)
    _batch(ai, [1])
    assert got[0]["raw_text"] is None and "boom" in got[0]["error"]
    assert got[1]["attempt"] == 2 and got[1]["raw_text"] == _ok(1)


def test_run_single_capture_has_empty_expected_ids(monkeypatch):
    got = []
    monkeypatch.setattr(AI, "CAPTURE_SINK", got.append)
    AI.run_single(FakeAI([json.dumps({"families": [1]})]), stage="a1",
                  model="m", thinking_level="low", prompt="X", schema=SCHEMA,
                  max_tokens=5, result_key="families")
    assert got[0]["expected_ids"] == [] and got[0]["prompt"] == "X"


def test_sink_exception_does_not_alter_engine(monkeypatch):
    def bad(_):
        raise RuntimeError("sink down")
    monkeypatch.setattr(AI, "CAPTURE_SINK", bad)
    assert set(_batch(FakeAI([_ok(1)]), [1])) == {1}


def test_capture_writes_sealed_index_and_loads(tmp_path):
    with StageCapture(run_id="r1", out_dir=tmp_path):
        assert AI.CAPTURE_SINK is not None
        _batch(FakeAI([_ok(1, 2)]), [1, 2])
    assert AI.CAPTURE_SINK is None
    idx = load_capture_index("r1", tmp_path)
    assert idx["n_calls"] == 1 and idx["clean_exit"] is True
    assert idx["stages"]["s_a2"]["prompt_sha_set"] == [prompt_sha("P:1,2")]
    assert idx["stages"]["s_a2"]["providers"] == ["gemini"]
    calls = load_capture("r1", tmp_path)
    assert calls[0]["seq"] == 0 and calls[0]["expected_ids"] == [1, 2]
    with pytest.raises(CharacterizationError):    # fail-closed: ustune yazma
        StageCapture(run_id="r1", out_dir=tmp_path).__enter__()
    assert AI.CAPTURE_SINK is None


def test_capture_restores_sink_on_exception_and_flags_partial(tmp_path):
    with pytest.raises(ValueError):
        with StageCapture(run_id="r2", out_dir=tmp_path):
            raise ValueError("x")
    assert AI.CAPTURE_SINK is None
    assert load_capture_index("r2", tmp_path)["clean_exit"] is False


def test_load_detects_tampered_jsonl_and_index(tmp_path):
    with StageCapture(run_id="r3", out_dir=tmp_path):
        _batch(FakeAI([_ok(1)]), [1])
    p = tmp_path / "r3" / "calls.jsonl"
    p.write_text(p.read_text(encoding="utf-8").replace("P:1", "P:9"),
                 encoding="utf-8")
    with pytest.raises(CharacterizationError):
        load_capture("r3", tmp_path)
    idx = tmp_path / "r3" / "index.json"
    d = json.loads(idx.read_text(encoding="utf-8"))
    d["n_calls"] = 7
    idx.write_text(json.dumps(d), encoding="utf-8")
    with pytest.raises(CharacterizationError):
        load_capture("r3", tmp_path)
