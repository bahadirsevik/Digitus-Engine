# -*- coding: utf-8 -*-
"""Motor rota cozumu + route-baglama garantisi (plan_claude_gemini §5 Faz 0 / 6a).

DB'siz, agsiz: Anthropic SDK httpx2.MockTransport ile beslenir.
"""
from __future__ import annotations

import json

import httpx2
import pytest

from app.config import settings
from app.core.engine import ai_runner as AI
from app.core.engine.ads.runner import ads_models
from app.core.engine.family.runner import family_models
from app.core.engine.routing import resolve_stage_route
from app.core.engine.seo.runner import seo_models
from app.core.engine.social.runner import social_models
from app.core.telemetry.downstream_ledger import (
    DownstreamLedgerBinding, UnpricedModelError,
)
from app.generators.ai_service import (
    AIResponseError, MockAIService, RoutedAIService,
)
from app.generators.anthropic_service import (
    AnthropicRateLimiter, AnthropicRefusalError, AnthropicService,
)

HAIKU = "claude-haiku-5-5"
ALL_MODELS = {}
for _m in (family_models(), ads_models(), seo_models(), social_models()):
    ALL_MODELS.update(_m)
AI_STAGE_PARAMS = sorted(s for s, m in ALL_MODELS.items() if m != "deterministic")

SCHEMA = {"type": "object", "required": ["results"], "properties": {
    "results": {"type": "array", "items": {
        "type": "object", "required": ["id"],
        "properties": {"id": {"type": "integer"}}}}}}
ROWS = [{"keyword_id": 1, "keyword_text": "a"}]


def _route(monkeypatch, stage, provider, model):
    monkeypatch.setattr(settings, "AI_STAGE_ROUTES",
                        {stage: {"provider": provider, "model": model}},
                        raising=False)


# ---------- resolve_stage_route ----------
def test_no_route_gives_gemini_default(monkeypatch):
    monkeypatch.setattr(settings, "AI_STAGE_ROUTES", {}, raising=False)
    assert resolve_stage_route("ads_funnel", "gemini-3.8-flash") == (
        "gemini", "gemini-3.8-flash")
    _route(monkeypatch, "seo_rel", "anthropic", HAIKU)   # baska asama icin route
    assert resolve_stage_route("ads_funnel", "gemini-3.8-flash") == (
        "gemini", "gemini-3.8-flash")


@pytest.mark.parametrize("route", [
    {}, "x", {"provider": "openai", "model": "m"}, {"provider": "anthropic"},
    {"provider": "anthropic", "model": ""}, {"provider": "anthropic", "model": 5},
    {"model": HAIKU},
])
def test_invalid_route_fails_closed(monkeypatch, route):
    monkeypatch.setattr(settings, "AI_STAGE_ROUTES", {"ads_funnel": route},
                        raising=False)
    with pytest.raises(AIResponseError):
        resolve_stage_route("ads_funnel", "gemini-3.8-flash")


def test_unknown_stage_key_in_table_fails_closed(monkeypatch):
    monkeypatch.setattr(settings, "AI_STAGE_ROUTES", {
        "ads_funnl": {"provider": "anthropic", "model": HAIKU}}, raising=False)
    with pytest.raises(AIResponseError):
        resolve_stage_route("ads_funnel", "gemini-3.8-flash")


def test_provider_is_never_inferred_from_model_name(monkeypatch):
    _route(monkeypatch, "ads_funnel", "gemini", HAIKU)
    assert resolve_stage_route("ads_funnel", "x") == ("gemini", HAIKU)


# ---------- test donanimi ----------
class Harness:
    def __init__(self, events, body=None, stop="end_turn"):
        self.events, self.requests = events, []
        self.body = body if body is not None else {"results": [{"id": 1}]}
        self.stop = stop

    def __call__(self, request):
        self.events.append("http")
        self.requests.append(json.loads(request.content))
        return httpx2.Response(200, json={
            "id": "m", "type": "message", "role": "assistant", "model": HAIKU,
            "content": [{"type": "text", "text": json.dumps(self.body)}],
            "stop_reason": self.stop,
            "usage": {"input_tokens": 10, "output_tokens": 5}})


class RecordingBinding:
    def __init__(self, events):
        self.events = events

    def reserve(self, **kw):
        self.events.append("reserve")
        return 1

    def settle(self, rid, **kw):
        self.events.append("settle")

    def charge_ceiling(self, rid):
        self.events.append("charge_ceiling")


class RecordingRouter(RoutedAIService):
    """Gercek RoutedAIService; for_stage cagrilarini (stage, model, provider) kaydeder."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.stage_calls = []

    def for_stage(self, stage, **kw):
        self.stage_calls.append((stage, kw.get("model"), kw.get("provider")))
        return super().for_stage(stage, **kw)


def _router(harness, events):
    anth = AnthropicService(api_key="k", transport=httpx2.MockTransport(harness),
                            limiter=AnthropicRateLimiter())
    router = RecordingRouter(MockAIService(), anthropic_backend=anth)
    router.cost_binding = RecordingBinding(events)
    return router


def _run_single(router, stage, provider, model):
    return AI.run_single(router, stage=stage, model=model, provider=provider,
                         thinking_level="low", prompt="p", schema=SCHEMA,
                         max_tokens=500, result_key="results")


def _run_batch(router, stage, provider, model):
    return AI.run_batch(router, stage=stage, model=model, provider=provider,
                        thinking_level="low", rows=ROWS,
                        build_prompt=lambda rows: "p", schema=SCHEMA,
                        max_tokens=500)


# ---------- route-baglama garantisi (plan 6a son cumle) ----------
@pytest.mark.parametrize("runner", [_run_single, _run_batch])
@pytest.mark.parametrize("stage", AI_STAGE_PARAMS)
def test_every_ai_stage_route_reaches_anthropic_backend_reserve_before_http(
        monkeypatch, stage, runner):
    _route(monkeypatch, stage, "anthropic", HAIKU)
    provider, model = resolve_stage_route(stage, ALL_MODELS[stage])
    assert (provider, model) == ("anthropic", HAIKU)          # (a)

    events = []
    harness = Harness(events)
    router = _router(harness, events)
    runner(router, stage, provider, model)

    assert router.stage_calls == [(stage, HAIKU, "anthropic")]  # (b)
    assert len(harness.requests) == 1
    assert harness.requests[0]["model"] == HAIKU
    assert events == ["reserve", "http", "settle"]            # (c) sira


@pytest.mark.parametrize("stage", AI_STAGE_PARAMS)
def test_unpriced_route_model_fails_before_any_http(monkeypatch, stage):
    _route(monkeypatch, stage, "anthropic", "claude-haiku-9")
    provider, model = resolve_stage_route(stage, ALL_MODELS[stage])
    events = []
    harness = Harness(events)
    router = _router(harness, events)
    router.cost_binding = DownstreamLedgerBinding(
        None, scoring_run_id=1, attempt_id=1)                 # DB'siz: ledger'a inmeden patlar
    with pytest.raises(AI.AiStageError) as ei:
        _run_single(router, stage, provider, model)
    assert isinstance(ei.value.__cause__, UnpricedModelError)  # (d)
    assert harness.requests == [] and events == []


def test_deepseek_route_for_engine_stage_passes_through_unchanged(monkeypatch):
    _route(monkeypatch, "ads_funnel", "deepseek", "deepseek-v4-pro")
    provider, model = resolve_stage_route("ads_funnel", ALL_MODELS["ads_funnel"])
    assert (provider, model) == ("deepseek", "deepseek-v4-pro")

    seen = []

    class FakeDeepSeek(MockAIService):
        def _execute(self, prompt, **kw):
            seen.append((kw["stage"], kw["model"]))
            return json.dumps({"results": [{"id": 1}]})

    router = RecordingRouter(MockAIService(), deepseek_backend=FakeDeepSeek())
    _run_single(router, "ads_funnel", provider, model)
    assert router.stage_calls == [("ads_funnel", "deepseek-v4-pro", "deepseek")]
    assert seen == [("ads_funnel", "deepseek-v4-pro")]


# ---------- gemini yolu bayt-aynı ----------
def test_gemini_provider_does_not_pass_provider_kwarg():
    calls = []

    class StrictAI:                       # bugunku imza: provider kabul ETMEZ
        def for_stage(self, stage, *, model, thinking_level):
            calls.append((stage, model, thinking_level))
            return self

        def complete_json(self, prompt, **kw):
            return json.dumps({"results": [{"id": 1}]})

    _run_single(StrictAI(), "ads_funnel", "gemini", "gemini-3.8-flash")
    assert calls == [("ads_funnel", "gemini-3.8-flash", "low")]


# ---------- refusal asla tekrar edilmez ----------
def test_refusal_is_fatal_and_never_retried():
    assert AI.classify_provider_error(AnthropicRefusalError("x")) == "fatal"
    events = []
    harness = Harness(events, stop="refusal")
    router = _router(harness, events)
    with pytest.raises(AnthropicRefusalError):
        _run_single(router, "ads_funnel", "anthropic", HAIKU)
    assert len(harness.requests) == 1                          # tek HTTP, tekrar yok
