# -*- coding: utf-8 -*-
"""V3 motor: 429 beklemesi + hata siniflandirmasi (plan_engine_paralellik.md §2).

Sozlesme: DENEME SAYISI ARTMAZ (MAX_ATTEMPTS = 2); yalniz denemeler arasina
bekleme girer. Saglayici cagrisi SAHTE, gercek Gemini YOK.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

import pytest

from app.core.engine import ai_runner as AI


class _Scoped:
    def __init__(self, parent: "FakeAI") -> None:
        self._parent = parent

    def complete_json(self, prompt: str, max_tokens: int = None,
                      response_schema: Dict[str, Any] = None) -> str:
        return self._parent._respond()


class FakeAI:
    def __init__(self, queue: List[Any]) -> None:
        self.queue = list(queue)
        self.calls = 0

    def for_stage(self, stage, *, model=None, thinking_level=None):
        return _Scoped(self)

    def _respond(self) -> str:
        self.calls += 1
        item = self.queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def _ok_payload(ids):
    return json.dumps({"results": [{"id": i, "v": 1} for i in ids]})


@pytest.fixture
def uyku(monkeypatch):
    beklemeler: List[float] = []
    monkeypatch.setattr(AI, "_sleep", lambda s: beklemeler.append(s))
    return beklemeler


def _run(ai, rows=({"keyword_id": 1},)):
    return AI.run_batch(ai, stage="test", model="m", thinking_level="low",
                        rows=list(rows), build_prompt=lambda subset: "p",
                        schema={}, max_tokens=100, result_key="results")


def test_429_da_beklenir_ve_tek_tekrar_yapilir(uyku):
    ai = FakeAI([RuntimeError("429 RESOURCE_EXHAUSTED"), _ok_payload([1])])
    out = _run(ai)
    assert ai.calls == 2                      # ilk cagri + TEK tekrar
    assert len(uyku) == 1 and 2.0 <= uyku[0] <= 3.0


def test_retry_after_basligina_uyulur(uyku):
    exc = RuntimeError("429 rate limit")
    exc.retry_after = 7
    ai = FakeAI([exc, _ok_payload([1])])
    _run(ai)
    assert uyku == [7.0]


def test_retry_after_ust_sinira_kirpilir(uyku):
    exc = RuntimeError("429 quota")
    exc.retry_after = 600
    ai = FakeAI([exc, _ok_payload([1])])
    _run(ai)
    assert uyku == [AI.RATE_LIMIT_WAIT_MAX_SECONDS]


def test_parse_hatasinda_beklenmez(uyku):
    ai = FakeAI(["bu json degil", _ok_payload([1])])
    _run(ai)
    assert ai.calls == 2 and uyku == []       # bugunku davranis korunur


def test_fatal_hatada_tekrar_yok(uyku):
    ai = FakeAI([RuntimeError("API key not valid"), _ok_payload([1])])
    with pytest.raises(RuntimeError, match="API key not valid"):
        _run(ai)
    assert ai.calls == 1                      # ikinci deneme YAPILMAZ
    assert uyku == []


def test_deneme_sayisi_artmaz(uyku):
    ai = FakeAI([RuntimeError("429"), RuntimeError("429"), _ok_payload([1])])
    with pytest.raises(AI.AiStageError):
        _run(ai)
    assert ai.calls == AI.MAX_ATTEMPTS == 2   # ucuncu deneme YOK
    assert len(uyku) == 1                     # son denemeden sonra beklenmez


def test_siniflandirma():
    c = AI.classify_provider_error
    assert c(RuntimeError("429 RESOURCE_EXHAUSTED")) == "rate_limit"
    assert c(RuntimeError("503 Service Unavailable")) == "rate_limit"
    assert c(RuntimeError("deadline exceeded")) == "rate_limit"
    assert c(RuntimeError("API key not valid")) == "fatal"
    assert c(RuntimeError("Invalid argument: schema")) == "fatal"
    assert c(RuntimeError("bozuk cevap")) == "transient"
