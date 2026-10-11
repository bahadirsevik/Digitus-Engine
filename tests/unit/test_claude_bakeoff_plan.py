"""Faz 1 kapı/bütçe mührü (claude_bakeoff_plan) — DB'siz."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.benchmark.claude_bakeoff_plan import build_plan, faz1_blockers
from app.core.benchmark.reference_characterization import (
    CharacterizationError, verify_seal)

SPIKE = Path("benchmark/claude_spike.json")


@pytest.fixture(scope="module")
def plan():
    return build_plan(SPIKE)


def test_plan_is_sealed_and_bound_to_spike(plan):
    verify_seal(plan)
    spike = json.loads(SPIKE.read_text(encoding="utf-8"))
    assert plan["spike"]["payload_sha256"] == spike["payload_sha256"]
    assert plan["spike"]["schemas_rejected"] == 0


def test_faz1_blocked_until_k12_console_and_ws_ids(plan):
    assert faz1_blockers(plan) == ["K12_document_reference", "spike_console_reconciled",
                                   "credit_net_available_usd", "workspace_ids"]
    unlocked = dict(plan)
    unlocked["decisions"] = {**plan["decisions"], "K12_document_reference": "sözleşme md. X"}
    unlocked["spike"] = {**plan["spike"], "console_reconciled": True}
    unlocked["credit"] = {**plan["credit"], "net_available_usd": 97.5}
    unlocked["workspaces"] = {**plan["workspaces"], "ids": {"GR-7": 1, "Optimice": 2}}
    with pytest.raises(CharacterizationError):     # mühür bozuldu -> yeniden mühür şart
        faz1_blockers(unlocked)
    from app.core.benchmark.reference_characterization import seal
    assert faz1_blockers(seal(unlocked)) == []


def test_budget_and_caps_match_plan_rev6(plan):
    b = plan["budget_usd"]
    assert sum(c["envelope"] for c in b["cells"]) == pytest.approx(23.0)
    assert b["cap"] == 50.0 and plan["decisions"]["K7_caps_usd"]["gemini"] == 20.0
    assert plan["decisions"]["K19_task_p95_ratio"] == 1.25
    assert plan["adapter"]["thinking_allowance"] == {"low": 2000, "medium": 4000, "high": 8000}
    assert plan["adapter"]["sdk_retries"] == {"anthropic_max_retries": 0, "gemini_retry_attempts": 1}
    for m in ("claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"):
        assert plan["prices"][m]["valid_from"] == "2026-10-11"


def test_n_max_rounds_down(plan):
    from app.core.benchmark.claude_bakeoff_plan import _n_max
    assert _n_max(100.0, 3.6) == 27 and _n_max(100.0, 1.25) == 80
    assert plan["n_max_3_2"]["Aday"]["n_max"] == 27
    assert plan["n_max_3_2"]["Her şey Haiku"]["n_max"] == 294
