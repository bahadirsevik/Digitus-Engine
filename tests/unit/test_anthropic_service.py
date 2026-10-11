# -*- coding: utf-8 -*-
"""AnthropicService adaptörü: zarf, hata eşleme, usage, ledger, limiter.

DB'siz, ağsız: SDK httpx2.MockTransport ile beslenir (anthropic 1.13 httpx2 kullanır).
"""
from __future__ import annotations

import hashlib
import json

import httpx2
import pytest

from app.config import settings
from app.core.engine.ai_runner import classify_provider_error
from app.generators.ai_service import AIResponseError
from app.generators.anthropic_service import (
    ADAPTER_CONTRACT_VERSION,
    HARD_MAX_TOKENS,
    JSON_ONLY_SYSTEM,
    SYSTEM_SHA,
    THINKING_ALLOWANCE,
    THINKING_POLICY_SHA,
    AnthropicContractError,
    AnthropicRateLimiter,
    AnthropicRefusalError,
    AnthropicService,
    anthropic_request_envelope,
    anthropic_request_input_ceiling_bytes,
)

SCHEMA = {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]}
OK_USAGE = {"input_tokens": 100, "output_tokens": 20}


def _msg(text='{"a":"x"}', stop="end_turn", usage=OK_USAGE, content=None):
    body = {"id": "m1", "type": "message", "role": "assistant", "model": "claude-haiku-5-5",
            "content": content if content is not None else [{"type": "text", "text": text}],
            "stop_reason": stop}
    if usage is not None:
        body["usage"] = usage
    return body


class Harness:
    def __init__(self, response=None, status=200, exc=None):
        self.requests = []
        self.response, self.status, self.exc = response or _msg(), status, exc

    def __call__(self, request):
        self.requests.append(json.loads(request.content))
        if self.exc is not None:
            raise self.exc
        return httpx2.Response(self.status, json=self.response)

    @property
    def calls(self):
        return len(self.requests)


class FakeBinding:
    def __init__(self):
        self.events = []

    def reserve(self, **kw):
        self.events.append(("reserve", kw))
        return 7

    def settle(self, rid, **kw):
        self.events.append(("settle", rid, kw))

    def charge_ceiling(self, rid):
        self.events.append(("charge_ceiling", rid))

    def kinds(self):
        return [e[0] for e in self.events]


class FakeCollector:
    def __init__(self):
        self.records = []

    def record(self, **kw):
        self.records.append(kw)


def make(h, **kw):
    svc = AnthropicService(api_key="test-key", transport=httpx2.MockTransport(h),
                           limiter=AnthropicRateLimiter(), **kw)
    svc.collector = FakeCollector()
    svc.cost_binding = FakeBinding()
    return svc


def run(svc, prompt="merhaba", **kw):
    args = dict(max_tokens=1000, temperature=0.3, json_mode=False, response_schema=None,
                stage="ads_rsa", model="claude-haiku-5-5")
    args.update(kw)
    return svc._execute(prompt, **args)


# ---------- zarf ----------
def test_envelope_plain_has_no_sampling_no_system():
    env = anthropic_request_envelope("p", model="m", max_tokens=1000, json_mode=False,
                                     response_schema=None, thinking_level=None)
    assert env == {"model": "m", "max_tokens": 3000,
                   "messages": [{"role": "user", "content": "p"}],
                   "thinking": {"type": "adaptive"}, "output_config": {"effort": "low"}}


def test_envelope_json_without_schema_gets_fixed_system():
    env = anthropic_request_envelope("p", model="m", max_tokens=10, json_mode=True,
                                     response_schema=None, thinking_level="medium")
    assert env["system"] == JSON_ONLY_SYSTEM
    assert "format" not in env["output_config"]
    assert env["output_config"]["effort"] == "medium"
    assert env["max_tokens"] == 10 + THINKING_ALLOWANCE["medium"]


def test_envelope_schema_uses_output_config_format_and_no_system():
    env = anthropic_request_envelope("p", model="m", max_tokens=10, json_mode=True,
                                     response_schema=SCHEMA, thinking_level="high")
    fmt = env["output_config"]["format"]
    assert fmt["type"] == "json_schema" and fmt["schema"]["additionalProperties"] is False
    assert "system" not in env and env["output_config"]["effort"] == "high"


def test_max_tokens_hard_cap_and_bad_inputs():
    env = anthropic_request_envelope("p", model="m", max_tokens=15000, json_mode=False,
                                     response_schema=None, thinking_level="high")
    assert env["max_tokens"] == HARD_MAX_TOKENS
    with pytest.raises(AnthropicContractError):
        anthropic_request_envelope("p", model="m", max_tokens=0, json_mode=False,
                                   response_schema=None, thinking_level=None)
    with pytest.raises(AnthropicContractError, match="invalid argument"):
        anthropic_request_envelope("p", model="m", max_tokens=5, json_mode=False,
                                   response_schema=None, thinking_level="xhigh")


def test_constants_and_shas():
    assert ADAPTER_CONTRACT_VERSION == "1"
    assert SYSTEM_SHA == hashlib.sha256(JSON_ONLY_SYSTEM.encode()).hexdigest()
    assert len(THINKING_POLICY_SHA) == 64


def test_input_ceiling_covers_prompt_system_and_schema():
    p = "çok türkçe ğüşiöç"
    plain = anthropic_request_input_ceiling_bytes(p, json_mode=False)
    js = anthropic_request_input_ceiling_bytes(p, json_mode=True)
    sch = anthropic_request_input_ceiling_bytes(p, json_mode=True, response_schema=SCHEMA)
    assert plain >= len(p.encode()) and js > plain
    assert sch > plain and sch != js


def test_wire_request_is_exact_envelope_without_sampling():
    h = Harness()
    svc = make(h)
    out = run(svc, "prompt-X", json_mode=True, response_schema=SCHEMA, thinking_level="low")
    assert out == '{"a":"x"}'
    sent = h.requests[0]
    assert sent == anthropic_request_envelope(
        "prompt-X", model="claude-haiku-5-5", max_tokens=1000, json_mode=True,
        response_schema=SCHEMA, thinking_level="low")
    assert "temperature" not in sent and "top_p" not in sent
    assert sent["messages"] == [{"role": "user", "content": "prompt-X"}]


# ---------- sözleşme reddi ----------
def test_thinking_budget_is_contract_error_before_http():
    h = Harness()
    svc = make(h)
    with pytest.raises(AnthropicContractError, match="invalid argument"):
        run(svc, thinking_budget=1024)
    assert h.calls == 0 and svc.cost_binding.events == []
    assert classify_provider_error(AnthropicContractError("Anthropic invalid argument: x")) == "fatal"


def test_foreign_provider_and_missing_model_rejected():
    svc = make(Harness())
    with pytest.raises(AIResponseError):
        run(svc, provider="gemini")
    with pytest.raises(AIResponseError, match="model zorunlu"):
        run(svc, model=None)


def test_missing_api_key_fail_closed_before_http_and_reserve(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", None)
    h = Harness()
    svc = AnthropicService(transport=httpx2.MockTransport(h), limiter=AnthropicRateLimiter())
    svc.cost_binding = FakeBinding()
    with pytest.raises(AIResponseError, match="ANTHROPIC_API_KEY"):
        run(svc)
    assert h.calls == 0 and svc.cost_binding.events == []


# ---------- yanıt ----------
def test_text_blocks_concatenated_thinking_ignored():
    content = [{"type": "thinking", "thinking": "gizli", "signature": "s"},
               {"type": "text", "text": "ab"}, {"type": "text", "text": "cd"}]
    svc = make(Harness(_msg(content=content)))
    assert run(svc) == "abcd"


def test_refusal_typed_error_and_billed():
    svc = make(Harness(_msg(text="", stop="refusal")))
    with pytest.raises(AnthropicRefusalError, match="refusal"):
        run(svc)
    assert svc.cost_binding.kinds() == ["reserve", "settle"]
    assert svc.collector.records[-1]["retry_reason"] == "refusal"
    assert svc.collector.records[-1]["finish_reason"] == "refusal"


def test_max_tokens_returns_text_and_records_finish_reason():
    svc = make(Harness(_msg(text='{"a":', stop="max_tokens")))
    assert run(svc) == '{"a":'
    assert svc.collector.records[-1]["finish_reason"] == "max_tokens"
    assert svc.collector.records[-1]["retry_reason"] is None


def test_empty_text_raises_empty_content():
    svc = make(Harness(_msg(content=[{"type": "thinking", "thinking": "x", "signature": "s"}])))
    with pytest.raises(AIResponseError, match="empty_content"):
        run(svc)
    assert svc.collector.records[-1]["retry_reason"] == "empty_content"


# ---------- hata eşleme ----------
def _err(status):
    return {"type": "error", "error": {"type": "e", "message": "msg"}}


@pytest.mark.parametrize("status,needles,kind,reason", [
    (429, ["rate limit (429)"], "rate_limit", "rate_limit: 429"),
    (500, ["500"], "transient", "transport: 500"),
    (503, ["503"], "rate_limit", "transport: 503"),
    (529, ["503"], "rate_limit", "transport: 529"),
    (400, ["400 bad request"], "fatal", "response_error: 400"),
    (404, ["404 bad request", "invalid argument"], "fatal", "response_error: 404"),
    (401, ["401"], "fatal", "auth: 401"),
    (403, ["403"], "fatal", "auth: 403"),
])
def test_http_status_mapping(status, needles, kind, reason):
    svc = make(Harness(_err(status), status=status))
    with pytest.raises(AIResponseError) as ei:
        run(svc)
    msg = str(ei.value).lower()
    assert all(n in msg for n in needles)
    assert classify_provider_error(ei.value) == kind
    assert svc.collector.records[-1]["retry_reason"] == reason
    assert svc.collector.records[-1]["http_attempts"] == 1
    # bilinmeyen harcama tavandan yakılır, settle edilmez
    assert svc.cost_binding.kinds() == ["reserve", "charge_ceiling"]


def test_timeout_mapping():
    svc = make(Harness(exc=httpx2.ReadTimeout("boom")))
    with pytest.raises(AIResponseError, match="read timed out") as ei:
        run(svc)
    assert classify_provider_error(ei.value) == "rate_limit"
    assert svc.collector.records[-1]["retry_reason"].startswith("timeout:")


def test_connection_error_mapping():
    svc = make(Harness(exc=httpx2.ConnectError("down")))
    with pytest.raises(AIResponseError, match="transport") as ei:
        run(svc)
    assert svc.collector.records[-1]["retry_reason"].startswith("transport:")
    assert classify_provider_error(ei.value) == "transient"


# ---------- usage / ledger / telemetri ----------
def test_usage_normalization_with_cache_fields_and_telemetry():
    usage = {"input_tokens": 100, "output_tokens": 20,
             "cache_read_input_tokens": 40, "cache_creation_input_tokens": 10}
    svc = make(Harness(_msg(usage=usage)))
    run(svc, stage="seo_rel")
    settle = [e for e in svc.cost_binding.events if e[0] == "settle"][0]
    u = settle[2]["usage"]
    assert u.prompt_token_count == 150          # input + cache_read + cache_creation
    assert u.candidates_token_count == 20 and u.thoughts_token_count is None
    assert u.total_token_count == 170
    assert (u.cache_read_token_count, u.cache_creation_token_count) == (40, 10)
    rec = svc.collector.records[-1]
    assert rec["provider"] == "anthropic" and rec["cache_status"] == "hit"
    assert (rec["prompt_tokens"], rec["candidates_tokens"], rec["total_tokens"]) == (150, 20, 170)
    assert (rec["cache_read_tokens"], rec["cache_creation_tokens"]) == (40, 10)
    assert rec["thoughts_tokens"] is None and rec["http_attempts"] == 1
    assert rec["queue_wait_ms"] is not None and rec["latency_ms"] >= 0
    assert rec["stage"] == "seo_rel" and rec["model"] == "claude-haiku-5-5"


def test_cache_miss_status():
    svc = make(Harness())
    run(svc)
    assert svc.collector.records[-1]["cache_status"] == "miss"


def test_usage_missing_charges_ceiling_not_settle_but_returns_text():
    svc = make(Harness(_msg(usage={})))            # input/output_tokens yok
    assert run(svc) == '{"a":"x"}'
    assert svc.cost_binding.kinds() == ["reserve", "charge_ceiling"]
    assert svc.collector.records[-1]["retry_reason"] == "usage_missing"


def test_usage_non_int_charges_ceiling():
    svc = make(Harness(_msg(usage={"input_tokens": "100", "output_tokens": 5})))
    run(svc)
    assert svc.cost_binding.kinds() == ["reserve", "charge_ceiling"]


def test_reserve_uses_sent_max_tokens_and_provider():
    svc = make(Harness())
    run(svc, "x" * 500, max_tokens=1000, thinking_level="high", json_mode=True,
        response_schema=SCHEMA)
    kw = svc.cost_binding.events[0][1]
    assert kw["provider"] == "anthropic" and kw["model"] == "claude-haiku-5-5"
    assert kw["max_output_tokens"] == 1000 + THINKING_ALLOWANCE["high"]
    assert kw["prompt_bytes"] == anthropic_request_input_ceiling_bytes(
        "x" * 500, json_mode=True, response_schema=SCHEMA) >= 500
    assert svc.cost_binding.kinds() == ["reserve", "settle"]


def test_unrecognized_exception_charges_ceiling_and_propagates():
    svc = make(Harness(exc=ValueError("kırık")))
    with pytest.raises(Exception):
        run(svc)
    assert svc.cost_binding.kinds() == ["reserve", "charge_ceiling"]


# ---------- limiter ----------
def test_limiter_blocks_on_rpm_and_reports_wait():
    now = [0.0]
    slept = []

    def sleep(s):
        slept.append(s)
        now[0] += s

    lim = AnthropicRateLimiter(rpm=60, concurrency=2, clock=lambda: now[0], sleep=sleep)
    t1, w1 = lim.acquire(10, 10)
    lim.release(t1)
    for _ in range(59):
        lim.release(lim.acquire(1, 1)[0])
    assert w1 == 0 and not slept
    t, wait = lim.acquire(1, 1)             # kova boş: ~1 sn beklemeli
    lim.release(t)
    assert slept and 900 <= wait <= 1100


def test_limiter_refunds_unused_input_tokens():
    now = [0.0]
    lim = AnthropicRateLimiter(itpm=600, clock=lambda: now[0], sleep=lambda s: None)
    t, _ = lim.acquire(600, 0)
    lim.release(t, in_actual=100, out_actual=0)
    t2, wait = lim.acquire(500, 0)          # iade sayesinde bekleme yok
    lim.release(t2)
    assert wait == 0


def test_limiter_unlimited_only_semaphore():
    lim = AnthropicRateLimiter(concurrency=1)
    t, w = lim.acquire(10**9, 10**9)
    lim.release(t)
    assert w == 0
