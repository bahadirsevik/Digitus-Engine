"""BudgetGuardedAI settle'ı paralel çağrıda DOĞRU (DB'siz, ağsız).

Eski `_all_events[before:]` dilimi ENGINE_AI_CONCURRENCY>1'de başka
thread'in event'lerini yutuyordu; settle artık `request_id` ile eşler.
Gerçek UsageCollector kullanılır; flush ÇAĞRILMAZ (flush_every dev).
"""
import threading
import time

import pytest

from app.core.benchmark.reference_characterization import (
    BudgetGuardedAI,
    CharacterizationBudget,
)
from app.core.telemetry.usage import UsageCollector, price_for

HAIKU = "claude-haiku-5-5"
GEMINI = "gemini-3.8-flash"
PROMPT = "kelime: kredi kartı"


def _collector():
    return UsageCollector(flush_every=10**9)


def _cost(model, pt, cand):
    p = price_for(model)
    return pt / 1e6 * p["input"] + cand / 1e6 * p["output"]


class LoggingBudget(CharacterizationBudget):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.settled_log = []  # (thread_ident, actual)

    def settle(self, reservation, actual):
        self.settled_log.append((threading.get_ident(), actual))
        return super().settle(reservation, actual)


class Inner:
    """İki event kaydeder, aralarında uyur (thread'ler iç içe geçsin)."""

    def __init__(self, provider, model, collector, pt, cand):
        self.provider, self.model, self.thinking_level = provider, model, None
        self._c, self._pt, self._cand = collector, pt, cand

    def complete_json(self, prompt, max_tokens=6000, response_schema=None):
        half_p, half_c = self._pt // 2, self._cand // 2
        for pt, cand in ((half_p, half_c),
                         (self._pt - half_p, self._cand - half_c)):
            time.sleep(0.01)
            self._c.record(stage="intent", model=self.model, prompt_tokens=pt,
                           candidates_tokens=cand, provider=self.provider)
        return "{}"


class Pinned:
    """for_stage → thread'e özel token sayılı Inner (stage adı yük taşır)."""

    def __init__(self, collector, plan):
        self._c, self._plan = collector, plan  # stage -> (provider, model, pt, cand)

    def for_stage(self, stage, **_):
        prov, model, pt, cand = self._plan[stage]
        return Inner(prov, model, self._c, pt, cand)


def _run_threads(guard, stages):
    errs, tids = [], {}

    def work(stage):
        try:
            tids[stage] = threading.get_ident()
            guard.for_stage(stage).complete_json(PROMPT)
        except Exception as exc:  # noqa: BLE001
            errs.append(exc)

    ts = [threading.Thread(target=work, args=(s,)) for s in stages]
    for t in ts:
        t.start()
    for t in ts:
        t.join(30)
    assert not errs, errs
    return tids


def test_each_thread_settles_exactly_its_own_cost():
    col = _collector()
    plan = {f"s{i}": ("anthropic", HAIKU, 2000 * (i + 1), 200 * (i + 1))
            for i in range(8)}
    budget = LoggingBudget(100, 50.0, None, provider="anthropic")
    guard = BudgetGuardedAI(Pinned(col, plan), budget, collector=col)
    tids = _run_threads(guard, list(plan))

    by_thread = {tid: a for tid, a in budget.settled_log}
    assert len(by_thread) == 8
    for stage, (_, model, pt, cand) in plan.items():
        assert by_thread[tids[stage]] == pytest.approx(
            _cost(model, pt, cand), abs=1e-9), stage
    total = sum(_cost(m, pt, c) for _, m, pt, c in plan.values())
    assert budget.cost_settled_usd == pytest.approx(total, abs=1e-6)
    assert budget.cost_committed_usd == budget.cost_settled_usd
    assert len(col._all_events) == 16  # çağrı başına 2 event


def test_per_provider_mapping_budget_attributes_correctly():
    col = _collector()
    plan = {}
    for i in range(8):
        if i % 2:
            plan[f"s{i}"] = ("anthropic", HAIKU, 3000 + 100 * i, 300 + 10 * i)
        else:
            plan[f"s{i}"] = ("gemini", GEMINI, 5000 + 100 * i, 500 + 10 * i)
    b_a = LoggingBudget(100, 50.0, None, provider="anthropic")
    b_g = LoggingBudget(100, 20.0, None, provider="gemini")
    guard = BudgetGuardedAI(Pinned(col, plan),
                            {"anthropic": b_a, "gemini": b_g}, collector=col)
    _run_threads(guard, list(plan))

    exp_a = sum(_cost(m, pt, c) for p, m, pt, c in plan.values()
                if p == "anthropic")
    exp_g = sum(_cost(m, pt, c) for p, m, pt, c in plan.values()
                if p == "gemini")
    assert b_a.requests_used == 4 and b_g.requests_used == 4
    assert b_a.cost_settled_usd == pytest.approx(exp_a, abs=1e-6)
    assert b_g.cost_settled_usd == pytest.approx(exp_g, abs=1e-6)


def test_caller_logical_request_scope_settles_only_this_call():
    """Katmanın kendi logical_request'i içinde ardışık iki çağrı: her biri
    yalnız KENDİ attempt'ini settle eder (ctx paylaşılır, sayaç ayrıştırır)."""
    col = _collector()
    plan = {"a": ("anthropic", HAIKU, 2000, 200),
            "b": ("anthropic", HAIKU, 4000, 400)}
    budget = LoggingBudget(100, 50.0, None, provider="anthropic")
    guard = BudgetGuardedAI(Pinned(col, plan), budget, collector=col)
    with col.logical_request():
        guard.for_stage("a").complete_json(PROMPT)
        guard.for_stage("b").complete_json(PROMPT)
    got = [a for _, a in budget.settled_log]
    assert got == [pytest.approx(_cost(HAIKU, 2000, 200), abs=1e-9),
                   pytest.approx(_cost(HAIKU, 4000, 400), abs=1e-9)]
    ids = {e["request_id"] for e in col._all_events}
    assert len(ids) == 1  # telemetri gruplaması bozulmadı


def test_no_collector_burns_reservation_ceiling():
    budget = LoggingBudget(100, 50.0, None, provider="anthropic")
    plan = {"a": ("anthropic", HAIKU, 2000, 200)}
    guard = BudgetGuardedAI(Pinned(_collector(), plan), budget)
    guard.for_stage("a").complete_json(PROMPT)
    assert budget.settled_log[0][1] is None  # usage yok -> tavan yanar
