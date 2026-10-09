"""
GeminiService._extract_text guvenli yanit wrapper'i unit testleri.

Gercek Gemini cagrisi yapmaz; sahte response nesneleriyle SAFETY/blok/bos-candidate
durumlarinin acik AIResponseError'a cevrildigini, gecerli yanitin oldugu gibi
donuldugunu dogrular. `_extract_text` staticmethod oldugu icin GeminiService
instantiate edilmeden cagrilir (genai.configure/network gerekmez).
"""
import sys
import os

import pytest
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from app.generators.ai_service import GeminiService, AIResponseError


class _Feedback:
    def __init__(self, block_reason=None):
        self.block_reason = block_reason


class _Candidate:
    def __init__(self, finish_reason=1):
        self.finish_reason = finish_reason


class _Response:
    """Sahte Gemini response. `_text_exc` verilirse .text erisimi onu firlatir."""
    def __init__(self, text="", candidates=None, block_reason=None, text_exc=None):
        self.prompt_feedback = _Feedback(block_reason)
        self.candidates = candidates if candidates is not None else []
        self._text = text
        self._text_exc = text_exc

    @property
    def text(self):
        if self._text_exc is not None:
            raise self._text_exc
        return self._text


def test_returns_valid_text():
    resp = _Response(text="merhaba", candidates=[_Candidate(finish_reason=1)])
    assert GeminiService._extract_text(resp) == "merhaba"


def test_partial_max_tokens_text_returned():
    # finish_reason=2 (MAX_TOKENS) ama text erisilebilir → kismi metin donulur
    resp = _Response(text='{"partial": true}', candidates=[_Candidate(finish_reason=2)])
    assert GeminiService._extract_text(resp) == '{"partial": true}'


def test_prompt_blocked_raises():
    resp = _Response(block_reason="SAFETY", candidates=[])
    with pytest.raises(AIResponseError) as exc:
        GeminiService._extract_text(resp)
    assert "block_reason" in str(exc.value)


def test_no_candidates_raises():
    resp = _Response(text="", candidates=[])
    with pytest.raises(AIResponseError):
        GeminiService._extract_text(resp)


def test_safety_finish_reason_text_raises_is_wrapped():
    # SAFETY: response.text ValueError firlatir → AIResponseError'a cevrilmeli
    resp = _Response(
        candidates=[_Candidate(finish_reason=3)],
        text_exc=ValueError("response.text quick accessor requires the response to contain a valid Part"),
    )
    with pytest.raises(AIResponseError) as exc:
        GeminiService._extract_text(resp)
    assert "finish_reason=3" in str(exc.value)


def test_empty_text_raises():
    resp = _Response(text="   ", candidates=[_Candidate(finish_reason=1)])
    with pytest.raises(AIResponseError):
        GeminiService._extract_text(resp)


# ---------------------------------------------------------------------------
# finish_reason gozlemlenebilirligi (loguru sink — pytest caplog loguru'yu
# default yakalamaz, sink kalibi kullanilir)
# ---------------------------------------------------------------------------


class _Usage:
    prompt_token_count = 100
    candidates_token_count = 60
    total_token_count = 2900
    thoughts_token_count = 2740


def _capture_warnings(func):
    """loguru WARNING kayitlarini yakalayarak func'i calistirir."""
    from loguru import logger as loguru_logger

    records = []
    sink_id = loguru_logger.add(
        lambda message: records.append(str(message)), level="WARNING"
    )
    try:
        func()
    finally:
        loguru_logger.remove(sink_id)
    return records


def test_non_stop_finish_reason_logs_warning_with_usage():
    resp = _Response(text='{"partial": true}', candidates=[_Candidate(finish_reason=2)])
    resp.usage_metadata = _Usage()

    records = _capture_warnings(lambda: GeminiService._extract_text(resp))

    joined = "\n".join(records)
    assert "finish_reason=2" in joined
    # Dusunme-token hipotezinin kaniti: thoughts/total gorunur olmali
    assert "thoughts_tokens=2740" in joined
    assert "total_tokens=2900" in joined
    # Sozlesme degismedi: kismi metin yine donuyor
    assert GeminiService._extract_text(resp) == '{"partial": true}'


def test_stop_finish_reason_logs_nothing():
    resp = _Response(text="tamam", candidates=[_Candidate(finish_reason=1)])
    records = _capture_warnings(lambda: GeminiService._extract_text(resp))
    assert not any("finish_reason" in r for r in records)


def test_grounded_call_returns_queries_and_evidence(monkeypatch):
    candidate = SimpleNamespace(
        finish_reason=1,
        grounding_metadata=SimpleNamespace(
            web_search_queries=["örnek rakipleri"],
            grounding_chunks=[
                SimpleNamespace(web=SimpleNamespace(uri="https://rakip.com", title="Rakip"))
            ],
            grounding_supports=[
                SimpleNamespace(
                    segment=SimpleNamespace(text="Rakip, pazardaki doğrudan alternatiftir."),
                    grounding_chunk_indices=[0],
                )
            ],
        ),
    )
    response = _Response(text='{"candidates": []}', candidates=[candidate])
    response.usage_metadata = None
    captured = {}

    def generate_content(**kwargs):
        captured.update(kwargs)
        return response

    monkeypatch.setattr(
        GeminiService,
        "client",
        property(lambda self: SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))),
    )
    service = GeminiService(api_key="test")
    result = service.for_stage("competitor_discovery").complete_grounded(
        "rakip bul",
        response_schema={"type": "object", "properties": {}},
    )
    assert result.search_queries == ["örnek rakipleri"]
    assert result.evidence_urls == ["https://rakip.com"]
    assert result.supports == [{
        "text": "Rakip, pazardaki doğrudan alternatiftir.",
        "evidence_urls": ["https://rakip.com"],
    }]
    assert captured["model"] == "gemini-3.8-flash"
    assert captured["config"].tools[0].google_search is not None
