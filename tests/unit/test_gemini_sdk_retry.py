"""GeminiService SDK ic retry kontrolu (plan §6.3: iki SDK'da ayni deneme butcesi)."""
from __future__ import annotations

import app.generators.ai_service as mod
from app.generators.ai_service import GeminiService


class _Recorder:
    kwargs = None

    def __init__(self, **kwargs):
        type(self).kwargs = kwargs


def test_default_client_construction_is_unchanged(monkeypatch):
    monkeypatch.setattr(mod.genai, "Client", _Recorder)
    GeminiService(api_key="k").client
    assert _Recorder.kwargs == {"api_key": "k"}


def test_retry_attempts_one_disables_sdk_retries(monkeypatch):
    monkeypatch.setattr(mod.genai, "Client", _Recorder)
    GeminiService(api_key="k", retry_attempts=1).client
    opts = _Recorder.kwargs["http_options"]
    assert opts.retry_options.attempts == 1
    assert _Recorder.kwargs["api_key"] == "k"


def test_sdk_default_is_five_attempts_documented():
    # Adalet kuralinin gerekcesi: SDK varsayilani 5 deneme (kaynak sabit).
    from google.genai import _api_client
    assert _api_client._RETRY_ATTEMPTS == 5
