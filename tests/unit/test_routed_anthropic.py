# -*- coding: utf-8 -*-
"""RoutedAIService: 'anthropic' sağlayıcı yuvası (fail-closed yönlendirme)."""
from __future__ import annotations

import pytest

from app.config import settings
from app.generators.ai_service import (
    VALID_PROVIDERS,
    AIResponseError,
    MockAIService,
    RoutedAIService,
    get_ai_service,
)
from app.generators.anthropic_service import AnthropicService

STAGE = "ads_rsa"


class Recorder(MockAIService):
    def __init__(self):
        self.calls = []
        self.closed = False
        self.collector = None
        self.cost_binding = None

    def _execute(self, prompt, **kw):
        self.calls.append(kw)
        return "anthropic-out"

    def close(self):
        self.closed = True


def _routed(monkeypatch, routes):
    monkeypatch.setattr(settings, "AI_STAGE_ROUTES", routes)
    rec = Recorder()
    return RoutedAIService(MockAIService(), anthropic_backend=rec), rec


def test_valid_providers_include_anthropic():
    assert VALID_PROVIDERS == ("gemini", "deepseek", "anthropic")


def test_route_resolves_to_anthropic_backend(monkeypatch):
    svc, rec = _routed(monkeypatch,
                       {STAGE: {"provider": "anthropic", "model": "claude-haiku-5-5"}})
    out = svc.for_stage(STAGE).complete_json("p")
    assert out == "anthropic-out"
    assert rec.calls[0]["model"] == "claude-haiku-5-5" and rec.calls[0]["stage"] == STAGE


def test_provider_override_via_for_stage(monkeypatch):
    svc, rec = _routed(monkeypatch, {})
    out = svc.for_stage(STAGE, provider="anthropic", model="claude-sonnet-5-5").complete("p")
    assert out == "anthropic-out" and rec.calls[0]["model"] == "claude-sonnet-5-5"


def test_unknown_provider_and_missing_model_still_fail_closed(monkeypatch):
    svc, _ = _routed(monkeypatch, {STAGE: {"provider": "claude", "model": "x"}})
    with pytest.raises(AIResponseError):
        svc.for_stage(STAGE).complete("p")
    svc, _ = _routed(monkeypatch, {STAGE: {"provider": "anthropic"}})
    with pytest.raises(AIResponseError, match="model zorunlu"):
        svc.for_stage(STAGE).complete("p")
    svc, _ = _routed(monkeypatch, {})
    with pytest.raises(AIResponseError, match="açık model"):
        svc.for_stage(STAGE, provider="anthropic").complete("p")


def test_grounded_on_anthropic_route_raises(monkeypatch):
    svc, _ = _routed(monkeypatch,
                     {STAGE: {"provider": "anthropic", "model": "claude-haiku-5-5"}})
    with pytest.raises(AIResponseError, match="GROUNDING_UNSUPPORTED_PROVIDER"):
        svc.for_stage(STAGE).complete_grounded("p")


def test_lazy_backend_inherits_collector_and_binding(monkeypatch):
    svc = RoutedAIService(MockAIService())
    svc.collector, svc.cost_binding = object(), object()
    backend = svc._anthropic_backend()
    assert isinstance(backend, AnthropicService)
    assert backend.collector is svc.collector and backend.cost_binding is svc.cost_binding
    assert svc._anthropic_backend() is backend
    new_c, new_b = object(), object()           # sonradan atama yuvaya yayılır
    svc.collector, svc.cost_binding = new_c, new_b
    assert backend.collector is new_c and backend.cost_binding is new_b


def test_close_closes_anthropic_slot():
    rec = Recorder()
    RoutedAIService(MockAIService(), anthropic_backend=rec).close()
    assert rec.closed


def test_mock_mode_never_builds_real_client():
    svc = get_ai_service(use_mock=True)
    assert isinstance(svc._anthropic, MockAIService)
    assert not isinstance(svc._anthropic_backend(), AnthropicService)
