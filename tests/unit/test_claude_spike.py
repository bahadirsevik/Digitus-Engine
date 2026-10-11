# -*- coding: utf-8 -*-
"""Claude spike koşucusu: DB'siz, ağsız (httpx2.MockTransport)."""
import hashlib
import json

import httpx2

from app.core.benchmark import claude_spike as cs
from app.core.constants import AI_STAGES
from app.generators.anthropic_service import AnthropicRateLimiter, AnthropicService

PLAN = cs.build_spike_plan()


def _factory(handler):
    return lambda: AnthropicService(api_key="test", transport=httpx2.MockTransport(handler),
                                    limiter=AnthropicRateLimiter())


def _is_optional_schema(body):
    fmt = (body.get("output_config") or {}).get("format")
    return bool(fmt) and fmt["schema"].get("required") == ["title", "summary"]


def ok_handler(reject_optional=False):
    state = {"http": 0}

    def handler(request):
        state["http"] += 1
        if request.url.path.endswith("count_tokens"):
            return httpx2.Response(200, json={"input_tokens": 7})
        body = json.loads(request.content)
        if reject_optional and _is_optional_schema(body):
            return httpx2.Response(400, json={"type": "error", "error": {
                "type": "invalid_request_error", "message": "schema not supported"}})
        return httpx2.Response(200, json={
            "id": "m", "type": "message", "role": "assistant", "model": body["model"],
            "content": [{"type": "text", "text": '{"title":"t","summary":"s"}'}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 100, "output_tokens": 50}},
            headers={"anthropic-ratelimit-requests-limit": "50"})
    handler.state = state
    return handler


def test_plan_bounds_and_determinism_and_ceiling():
    assert len(PLAN) <= 29 and PLAN == cs.build_spike_plan()
    assert {i["model"] for i in PLAN} == {cs.HAIKU, cs.SONNET}
    assert all(i["stage"] in AI_STAGES for i in PLAN)
    total = cs.call_ceiling_usd(cs._probe_item()) + sum(cs.call_ceiling_usd(i) for i in PLAN)
    assert total < 0.50, f"toplam tavan {total:.4f} USD ({len(PLAN)} çağrı)"


def test_run_spike_records_accept_reject_and_seal(tmp_path):
    out = tmp_path / "spike.json"
    res = cs.run_spike(PLAN, out_path=out,
                       service_factory=_factory(ok_handler(reject_optional=True)))
    assert json.loads(out.read_text(encoding="utf-8")) == res
    c1 = [r for r in res["rows"] if r["case"] == "c1_optional"]
    assert c1 and all(r["schema_accepted"] is False for r in c1)
    assert "schema not supported" in c1[0]["schema_error"]
    assert all(r["schema_accepted"] is True for r in res["rows"]
               if r["case"] in ("c2_nullable", "c3_nested"))
    assert res["summary"]["optional_fields_accepted"] is False
    ok = next(r for r in res["rows"] if r["case"] == "c4_json_mode")
    assert ok["input_tokens"] == 100 and ok["visible_tokens"] == 7
    assert ok["thinking_tokens"] == 43 and ok["tokenizer_ratio"] > 0
    assert res["ratelimit_probe"]["headers"]["anthropic-ratelimit-requests-limit"] == "50"
    assert res["gemini"] == {"thoughts_tokens_check": "not run"}
    for k in ("anthropic_version", "price_valid_from", "git_sha", "created_at",
              "adapter_contract_version", "thinking_policy_sha",
              "schema_transformer_version"):
        assert res[k]
    body = {k: v for k, v in res.items() if k != "payload_sha256"}
    assert res["payload_sha256"] == hashlib.sha256(json.dumps(
        body, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()


def test_all_accepted_when_api_accepts(tmp_path):
    res = cs.run_spike(PLAN, out_path=tmp_path / "s.json",
                       service_factory=_factory(ok_handler()))
    assert res["summary"]["optional_fields_accepted"] is True
    assert res["summary"]["schemas_rejected"] == 0


def test_cap_enforced_no_http(tmp_path):
    h = ok_handler()
    res = cs.run_spike(PLAN, out_path=tmp_path / "s.json", cap_usd=1e-6,
                       service_factory=_factory(h))
    assert h.state["http"] == 0 and res["calls_made"] == 0
    assert all(r["status"] == "skipped: cap" for r in res["rows"])


def test_max_calls_enforced(tmp_path):
    res = cs.run_spike(PLAN, out_path=tmp_path / "s.json", max_calls=3,
                       service_factory=_factory(ok_handler()))
    assert res["calls_made"] == 3
