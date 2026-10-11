"""T5 — provider-aware benchmark harcama freni (DB'siz, ağsız)."""
import threading
import time

import pytest

from app.core.benchmark.reference_characterization import (
    BudgetGuardedAI,
    CharacterizationBudget,
    CharacterizationError,
    _GuardedScoped,
)
from app.generators.anthropic_service import (
    anthropic_request_envelope,
    anthropic_request_input_ceiling_bytes,
)
from app.generators.ai_service import gemini_request_input_ceiling_bytes

SONNET = "claude-sonnet-5-5"
HAIKU = "claude-haiku-5-5"
PROMPT = "Anahtar kelime: kredi kartı başvurusu — sınıflandır"


class FakeCollector:
    def __init__(self):
        self._all_events = []


class FakeInner:
    """Scoped servis taklidi: provider/model/thinking_level + complete_json."""

    def __init__(self, provider, model, collector=None, event=None):
        self.provider = provider
        self.model = model
        self.thinking_level = None
        self.calls = 0
        self._collector = collector
        self._event = event
        self.gate = None  # set ise çağrı bitmeden önce bekler

    def complete_json(self, prompt, max_tokens=6000, response_schema=None):
        self.calls += 1
        if self.gate is not None:
            self.gate.wait(5)
        if self._collector is not None and self._event is not None:
            self._collector._all_events.append(dict(self._event))
        return "{}"


def _guard(inner, budget, collector=None):
    return _GuardedScoped(inner, budget, "intent", collector=collector)


def _expected_anthropic(prompt, model, caller_cap):
    b = anthropic_request_input_ceiling_bytes(prompt, json_mode=True)
    mt = anthropic_request_envelope(
        prompt, model=model, max_tokens=caller_cap, json_mode=True,
        response_schema=None, thinking_level=None)["max_tokens"]
    return b, mt


def test_anthropic_reserves_envelope_bytes_and_max_tokens():
    b, mt = _expected_anthropic(PROMPT, SONNET, 1000)
    assert mt == 3000 and b > len(PROMPT.encode("utf-8"))  # system dahil
    budget = CharacterizationBudget(5, 10.0, None, provider="anthropic")
    g = _guard(FakeInner("anthropic", SONNET), budget)
    g.complete_json(PROMPT, max_tokens=1000)
    # settle usage'sız -> tavan yanar: committed == tek çağrı tavanı
    assert budget.cost_committed_usd == pytest.approx(
        b / 1e6 * 2.00 + 3000 / 1e6 * 10.00, abs=1e-6)
    assert budget.requests_used == 1


def test_no_double_allowance_even_with_nonzero_budget_allowance():
    b, mt = _expected_anthropic(PROMPT, SONNET, 1000)
    budget = CharacterizationBudget(5, 10.0, None, provider="anthropic",
                                    thinking_allowance_tokens=7777)
    got = budget.reserve(b, mt, model=SONNET, allowance_included=True)
    assert got == pytest.approx(b / 1e6 * 2.00 + 3000 / 1e6 * 10.00)
    # guard yolu da aynı değeri rezerve eder (allowance ikinci kez eklenmez)
    budget2 = CharacterizationBudget(5, 10.0, None,
                                     thinking_allowance_tokens=7777)
    _guard(FakeInner("anthropic", SONNET), budget2).complete_json(
        PROMPT, max_tokens=1000)
    assert budget2.cost_committed_usd == pytest.approx(got, abs=1e-6)


def test_gemini_single_model_path_unchanged():
    budget = CharacterizationBudget(5, 10.0, "gemini-3.8-flash")
    _guard(FakeInner("gemini", "gemini-3.8-flash"), budget).complete_json(
        PROMPT, max_tokens=500)
    b = gemini_request_input_ceiling_bytes(
        PROMPT, json_mode=True, response_schema=None)
    assert budget.cost_committed_usd == pytest.approx(
        budget.per_call_ceiling_usd(b, 500), abs=1e-6)


def test_mapping_budgets_route_by_provider_and_unknown_fails_closed():
    a = CharacterizationBudget(5, 10.0, None, provider="anthropic")
    g = CharacterizationBudget(5, 10.0, "gemini-3.8-flash")
    maps = {"anthropic": a, "gemini": g}
    _guard(FakeInner("anthropic", SONNET), maps).complete_json(PROMPT)
    assert (a.requests_used, g.requests_used) == (1, 0)
    _guard(FakeInner("gemini", "gemini-3.8-flash"), maps).complete_json(
        PROMPT)
    assert (a.requests_used, g.requests_used) == (1, 1)
    inner = FakeInner("deepseek", "deepseek-v4-flash")
    with pytest.raises(CharacterizationError):
        _guard(inner, maps).complete_json(PROMPT)
    assert inner.calls == 0 and (a.requests_used, g.requests_used) == (1, 1)

    class Pinned:
        pass

    summ = BudgetGuardedAI(Pinned(), maps).summary()
    assert set(summ) == {"anthropic", "gemini"}
    assert summ["anthropic"]["provider"] == "anthropic"
    assert summ["gemini"]["requests_used"] == 1


def test_eight_threads_five_slots_exactly_five_succeed():
    b, mt = _expected_anthropic(PROMPT, SONNET, 1000)
    one = b / 1e6 * 2.00 + mt / 1e6 * 10.00
    budget = CharacterizationBudget(100, one * 5 + one / 2, None,
                                    provider="anthropic")
    barrier = threading.Barrier(8)
    release = threading.Event()
    results, lock = [], threading.Lock()

    def worker():
        inner = FakeInner("anthropic", SONNET)
        inner.gate = release  # çağrı SÜRERKEN rezervasyon tutulur
        barrier.wait()
        try:
            _guard(inner, budget).complete_json(PROMPT, max_tokens=1000)
            r = "ok"
        except CharacterizationError:
            r = "denied"
        with lock:
            results.append(r)

    ts = [threading.Thread(target=worker) for _ in range(8)]
    for t in ts:
        t.start()
    deadline = time.time() + 10
    while time.time() < deadline:  # 3 red + 5 tutulan rezervasyon
        with lock:
            if results.count("denied") == 3:
                break
        time.sleep(0.01)
    assert budget.requests_used == 5
    release.set()
    for t in ts:
        t.join(10)
    assert sorted(results) == ["denied"] * 3 + ["ok"] * 5


def test_settle_from_collector_events_uses_per_call_haiku_price():
    col = FakeCollector()
    ev = {"prompt_tokens": 1000, "candidates_tokens": 500,
          "thoughts_tokens": None, "model": HAIKU, "provider": "anthropic",
          "price_snapshot": {"input": 0.10, "output": 0.50}}
    budget = CharacterizationBudget(5, 10.0, None, provider="anthropic")
    inner = FakeInner("anthropic", HAIKU, collector=col, event=ev)
    _guard(inner, budget, collector=col).complete_json(PROMPT)
    assert budget.cost_settled_usd == pytest.approx(
        1000 * 0.10 / 1e6 + 500 * 0.50 / 1e6, abs=1e-6)
    assert budget.cost_committed_usd == budget.cost_settled_usd  # rezerve 0


def test_actual_above_reservation_raises():
    budget = CharacterizationBudget(5, 10.0, None)
    res = budget.reserve(100, 100, model=SONNET)
    with pytest.raises(CharacterizationError):
        budget.settle(res, res * 2)


def test_per_call_budget_unpriced_model_raises_before_reservation():
    budget = CharacterizationBudget(5, 10.0, None, provider="anthropic")
    inner = FakeInner("anthropic", "claude-yok-9")
    with pytest.raises(CharacterizationError):
        _guard(inner, budget).complete_json(PROMPT)
    assert budget.requests_used == 0 and inner.calls == 0
    with pytest.raises(CharacterizationError):  # model hiç çözülemezse de
        _guard(FakeInner("anthropic", None), budget).complete_json(PROMPT)
    assert budget.requests_used == 0
