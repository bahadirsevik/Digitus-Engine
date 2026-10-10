"""Plan E: embedding sözleşmesi testleri.

- Stabil model + her metin AYRI types.Content (düz string listesi bu
  modelde TEK embedding döndürüyordu — run-17) + instruction öneki;
  task_type gönderilmez.
- Cache key: model + dim + instruction_version + normalizer_version + md5.
- Fallback storm sınırı: kurtarma çağrıları (batch retry + ikiye bölme +
  tekiller) TOPLAM ≤ EMBEDDING_FAILURE_CALL_BUDGET; bitince neutral
  fallback + telemetride 'embedding_fallback_exhausted' (bir kez).
"""
from types import SimpleNamespace

import numpy as np
import pytest

import app.core.site_analyzer.relevance_scorer as rs_mod
from app.core.site_analyzer.relevance_scorer import (
    EMBEDDING_DIMENSIONS,
    EMBEDDING_FAILURE_CALL_BUDGET,
    EMBEDDING_FALLBACK_SCORE,
    EMBEDDING_INSTRUCTION,
    EMBEDDING_INSTRUCTION_VERSION,
    EMBEDDING_MODEL,
    RelevanceScorer,
)
from app.core.site_analyzer.turkish_normalizer import NORMALIZER_VERSION


class RecordingCollector:
    def __init__(self):
        self.events = []

    def record(self, **kwargs):
        self.events.append(kwargs)


class ScriptedClient:
    """Çağrı başına davranışı senaryodan okuyan fake embed istemcisi.

    scenario: her çağrı için 'ok' | 'fail' | 'mismatch' — biterse 'ok'.
    """

    def __init__(self, scenario=None):
        self.scenario = list(scenario or [])
        self.calls = []  # her çağrının metin sayısı
        self.last_kwargs = None
        outer = self

        class _Models:
            def embed_content(inner, model=None, contents=None, config=None):
                outer.calls.append(len(contents))
                outer.last_kwargs = {
                    "model": model, "contents": contents, "config": config,
                }
                step = outer.scenario.pop(0) if outer.scenario else "ok"
                if step == "fail":
                    raise RuntimeError("503 transient boom")
                n = 1 if step == "mismatch" and len(contents) > 1 else len(contents)
                return SimpleNamespace(embeddings=[
                    SimpleNamespace(values=[1.0] + [0.0] * (EMBEDDING_DIMENSIONS - 1))
                    for _ in range(n)
                ])

        self.models = _Models()


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(rs_mod.time, "sleep", lambda s: None)


def _scorer(scenario=None, collector=None):
    scorer = RelevanceScorer(collector=collector)
    scorer.client = ScriptedClient(scenario)
    return scorer


class TestApiCallContract:
    def test_per_text_content_wrapping_and_instruction_prefix(self):
        scorer = _scorer()
        out = scorer._embed_batch(["borsa analiz", "hisse takip"])
        assert out is not None and len(out) == 2

        kwargs = scorer.client.last_kwargs
        assert kwargs["model"] == EMBEDDING_MODEL
        contents = kwargs["contents"]
        assert len(contents) == 2  # her metin AYRI Content
        text0 = contents[0].parts[0].text
        assert text0.startswith(EMBEDDING_INSTRUCTION)
        assert text0.endswith("borsa analiz")

    def test_task_type_not_sent(self):
        scorer = _scorer()
        scorer._embed_batch(["kw"])
        config = scorer.client.last_kwargs["config"]
        assert getattr(config, "task_type", None) is None
        assert config.output_dimensionality == EMBEDDING_DIMENSIONS


class TestCacheKey:
    def test_key_carries_all_versions(self):
        key = RelevanceScorer._cache_key("borsa analiz")
        assert EMBEDDING_MODEL in key
        assert f":{EMBEDDING_DIMENSIONS}:" in key
        assert f":i{EMBEDDING_INSTRUCTION_VERSION}:" in key
        assert f":n{NORMALIZER_VERSION}:" in key

    def test_old_format_key_is_not_hit(self):
        """Eski format (embed:{model}:{md5}) yeni anahtarla ASLA çakışmaz —
        model/önek geçişinde bayat vektör sessizce karışamaz."""
        import hashlib

        old = f"embed:{EMBEDDING_MODEL}:{hashlib.md5(b'kw').hexdigest()}"
        assert RelevanceScorer._cache_key("kw") != old


class TestBoundedRecovery:
    def test_happy_path_single_call_no_budget_use(self):
        scorer = _scorer()
        out = scorer._embed_batch(["a", "b", "c"])
        assert len(out) == 3
        assert scorer.client.calls == [3]
        assert scorer._failure_calls_left == EMBEDDING_FAILURE_CALL_BUDGET

    def test_transient_failure_one_batch_retry(self):
        scorer = _scorer(scenario=["fail"])  # ilk çağrı düşer, retry ok
        out = scorer._embed_batch(["a", "b", "c"])
        assert len(out) == 3
        assert scorer.client.calls == [3, 3]
        assert scorer._failure_calls_left == EMBEDDING_FAILURE_CALL_BUDGET - 1

    def test_split_path_after_failed_retry(self):
        # initial fail + retry fail → ikiye bölme: [1] ok, [2] ok
        scorer = _scorer(scenario=["fail", "fail"])
        out = scorer._embed_batch(["a", "b", "c"])
        assert len(out) == 3
        assert scorer.client.calls == [3, 3, 1, 2]
        assert scorer._failure_calls_left == EMBEDDING_FAILURE_CALL_BUDGET - 3

    def test_budget_exhaustion_reports_once_and_falls_back(self):
        """Art arda başarısız batch'ler: kurtarma çağrıları TOPLAM ≤ bütçe;
        bütçe bitince tek 'embedding_fallback_exhausted' işareti (spam yok).

        Tek batch bütçeyi bitirmez (zincir ilk kurtarılamayan metinde pes
        eder) — tükenme çok-batch koşusunda gerçekleşir; 723 kelimelik
        gerçek koşunun 15 batch'ini temsilen 5 batch yeter."""
        collector = RecordingCollector()
        scorer = RelevanceScorer(collector=collector)
        scorer.client = ScriptedClient(scenario=["fail"] * 1000)

        for i in range(5):
            out = scorer._embed_batch([f"kw{i}-{j}" for j in range(8)])
            assert out is None  # her batch neutral fallback'e düşer

        assert scorer._failure_calls_left == 0
        # kurtarma çağrıları TOPLAM ≤ bütçe (5 mutlu-yol denemesi hariç)
        assert len(scorer.client.calls) <= 5 + EMBEDDING_FAILURE_CALL_BUDGET

        exhausted = [
            e for e in collector.events
            if e.get("retry_reason") == "embedding_fallback_exhausted"
        ]
        assert len(exhausted) == 1  # tek işaret (spam yok)

        # Bütçe bitmişken SONRAKİ batch: mutlu yol hâlâ denenir (bütçe dışı)
        before = len(scorer.client.calls)
        scorer.client.scenario = []  # artık başarılı
        out2 = scorer._embed_batch(["x", "y"])
        assert out2 is not None and len(out2) == 2
        assert len(scorer.client.calls) == before + 1

    def test_exhausted_batch_gets_neutral_fallback_in_compute(self):
        scorer = RelevanceScorer()
        scorer.client = ScriptedClient(scenario=["fail"] * 100)
        results = scorer.compute_relevance(["kw1", "kw2"], ["anchor"])
        assert len(results) == 2
        for r in results:
            assert r["relevance_score"] == EMBEDDING_FALLBACK_SCORE

    def test_mismatch_goes_straight_to_singles_without_batch_retry(self):
        """Yapısal uyuşmazlık deterministiktir — batch retry İSRAFTIR;
        doğrudan tekli mod (run-17 davranış korunur: [3,1,1,1])."""
        scorer = _scorer(scenario=["mismatch"])
        out = scorer._embed_batch(["a", "b", "c"])
        assert out is not None and len(out) == 3
        assert scorer.client.calls == [3, 1, 1, 1]

    def test_mismatch_recorded_as_failure_not_success(self):
        collector = RecordingCollector()
        scorer = _scorer(scenario=["mismatch"], collector=collector)
        scorer._embed_batch(["a", "b"])
        mismatch_events = [
            e for e in collector.events
            if (e.get("retry_reason") or "").startswith("embedding_count_mismatch")
        ]
        assert len(mismatch_events) == 1


class TestCostAccounting:
    """Codex v9-2: embedding maliyeti 'tam run' muhasebesine dahil —
    token tahmini + PRICE_TABLE satırı olmadan D'nin ≤25 TL kapısı
    relevance'ı sıfır sayar."""

    def test_events_carry_estimated_prompt_tokens(self):
        collector = RecordingCollector()
        scorer = _scorer(collector=collector)
        scorer._embed_batch(["borsa analiz uygulaması", "hisse takip"])
        assert len(collector.events) == 1
        tokens = collector.events[0]["prompt_tokens"]
        assert tokens and tokens > 0
        # Instruction öneki dahil (yalnız metinlerden büyük olmalı)
        bare = (len("borsa analiz uygulaması") + len("hisse takip")) // 4
        assert tokens > bare

    def test_estimate_cost_nonzero_for_embedding_event(self):
        from app.core.telemetry.usage import UsageCollector, estimate_cost

        c = UsageCollector(flush_every=1000)
        c.record(stage="embedding", model=EMBEDDING_MODEL,
                 prompt_tokens=1_000_000, total_tokens=1_000_000)
        cost = estimate_cost(c._all_events)
        assert cost["estimated_usd"] == pytest.approx(0.20)

    def test_estimate_tokens_helper(self):
        from app.core.site_analyzer.relevance_scorer import (
            estimate_embedding_tokens,
        )

        one = estimate_embedding_tokens(["kısa"])
        two = estimate_embedding_tokens(["kısa", "daha uzun bir metin"])
        assert 0 < one < two


class TestScorerClose:
    """Codex v9-3: RelevanceScorer client'ı da kapatılabilir olmalı —
    uzun ömürlü web process'inde httpx havuzları birikmesin."""

    def test_close_is_idempotent_and_closes_client(self):
        class FakeClient:
            def __init__(self):
                self.closed = False

            def close(self):
                self.closed = True

        scorer = RelevanceScorer()
        fake = FakeClient()
        scorer.client = fake
        scorer.close()
        assert fake.closed is True
        assert scorer.client is None
        scorer.close()  # idempotent — patlamaz
