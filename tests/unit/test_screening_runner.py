# -*- coding: utf-8 -*-
"""Corpus screening runner + provider + manifest + metrik testleri.

HİÇBİR ÜCRETLİ ÇAĞRI YAPILMAZ: sahte provider'lar (scripted yanıtlar) ve
mock'lanmış HTTP katmanı kullanılır.

Kritik güvenlik özelliği (plan_ai §2): tarama başarısızlığı kelimeyi ELEMEZ —
çözülemeyen keyword fit=1 (belirsiz) alır, ASLA fit=0 değil.
"""
import json
import time
import re
import sys

import pytest

from app.core.screening.contract import BATCH_SIZE, PROMPT_VERSION
from app.core.screening.manifest import (
    ManifestError,
    build_manifest,
    contract_hashes,
    seal_artifact,
    validate_artifact,
)
from app.core.screening.metrics import (
    acceptance_gates,
    candidate_recall_at,
    decision_flip_rate,
    llm_fit_ordering,
    quota_union,
    rrf_ordering,
    screening_recall,
)
from app.core.screening.providers import (
    CorpusScreeningProvider,
    ScreeningAuthError,
    ScreeningEmptyContentError,
    ScreeningRateLimitError,
    ScreeningResponseError,
    ScreeningTimeoutError,
    ScreeningUsage,
    parse_screening_payload,
)
from app.core.screening.runner import (
    MAX_PARSE_RETRIES,
    MAX_TRANSIENT_RETRIES,
    SINGLE_RETRY_LIMIT,
    ScreeningBudget,
    UNRESOLVED_FALLBACK_FIT,
    KeywordScreeningResult,
    ScreeningContext,
    compute_cost_usd,
    permute,
    run_screening,
)

CTX = ScreeningContext(
    product_definition="Ürün tanımı",
    content_strategy="İçerik stratejisi beyanı",
    social_mode="hype",
    target_audience="KOBİ'ler",
)


def _kws(n, start=1):
    return [{"id": i, "keyword": f"kelime {i}"} for i in range(start, start + n)]


def _item(kid, ads=2, seo=1, social=0, codes=None):
    return {
        "id": kid, "ads_fit": ads, "seo_fit": seo, "social_fit": social,
        "reason_codes": codes or {"ads": "COMMERCIAL_FIT",
                                  "seo": "STRATEGY_NEAR",
                                  "social": "LOW_DISCUSSABILITY"},
    }


class ScriptedProvider(CorpusScreeningProvider):
    """Her çağrıda `script(batch, call_no)` sonucunu döndürür.

    Dönüş: (raw_text) veya fırlatılacak istisna.
    """

    provider_name = "scripted"

    def __init__(self, script, model="deepseek-v4-flash", collector=None):
        super().__init__(model=model, collector=collector)
        self.script = script
        self.calls = []
        self.closed = 0

    def _complete(self, prompt):
        call_no = len(self.calls)
        # Regex ŞART: prompt'ta reason-code satırları da "- ads: ..." biçiminde
        # (bilinen tuzak — gevşek split ValueError verir)
        batch_ids = [int(m) for m in re.findall(r"^- (\d+): ", prompt, re.M)]
        self.calls.append({"batch_ids": batch_ids, "prompt": prompt})
        outcome = self.script(batch_ids, call_no)
        if isinstance(outcome, Exception):
            self._record(None, retry_reason=getattr(outcome, "reason_code", "err"))
            raise outcome
        usage = ScreeningUsage(prompt_tokens=100, completion_tokens=50,
                               cache_hit_tokens=40, cache_miss_tokens=60,
                               latency_ms=12)
        self._record(usage)
        return outcome, usage

    def close(self):
        self.closed += 1


def _all_ok(batch_ids, call_no):
    return json.dumps({"results": [_item(k) for k in batch_ids]})


class TestRunnerHappyPath:
    def test_full_coverage_and_batching(self):
        kws = _kws(BATCH_SIZE * 2 + 5)
        p = ScriptedProvider(_all_ok)
        res = run_screening(p, CTX, kws, seed=42)
        assert len(res.results) == len(kws)
        assert res.stats["unresolved"] == 0
        assert res.stats["coverage"] == 1.0
        assert len(p.calls) == 3  # ceil(65/30)
        assert all(len(c["batch_ids"]) <= BATCH_SIZE for c in p.calls)
        # Girdi sırası korunur (permütasyon yalnız batch'lemede)
        assert [r.keyword_id for r in res.results] == [k["id"] for k in kws]

    def test_permutation_is_deterministic_and_seed_sensitive(self):
        kws = _kws(50)
        a1 = [k["id"] for k in permute(kws, 42)]
        a2 = [k["id"] for k in permute(kws, 42)]
        b = [k["id"] for k in permute(kws, 20260727)]
        assert a1 == a2
        assert a1 != b
        assert sorted(a1) == sorted(b) == [k["id"] for k in kws]

    def test_usage_and_cost_from_provider(self):
        p = ScriptedProvider(_all_ok, model="deepseek-v4-flash")
        res = run_screening(p, CTX, _kws(10), seed=42)
        assert res.usage["requests"] == 1
        assert res.usage["cache_hit_tokens"] == 40
        assert res.usage["cost_source"] == "provider_usage"
        assert res.usage["cost_usd"] > 0


class TestRunnerNeverEliminates:
    def test_unresolved_gets_ambiguous_not_zero(self):
        """EN KRİTİK ÖZELLİK: tarama başarısızlığı kelimeyi elemez."""
        kws = _kws(3)

        def script(batch_ids, call_no):
            # id=2 hiçbir zaman dönmüyor (model onu 'unutuyor')
            return json.dumps({"results": [_item(k) for k in batch_ids if k != 2]})

        p = ScriptedProvider(script)
        res = run_screening(p, CTX, kws, seed=42)
        stuck = next(r for r in res.results if r.keyword_id == 2)
        assert stuck.unresolved is True
        assert stuck.ads_fit == stuck.seo_fit == stuck.social_fit == UNRESOLVED_FALLBACK_FIT
        assert UNRESOLVED_FALLBACK_FIT == 1  # 0 OLMAMALI (sessiz eleme yasak)
        assert all(c == "AMBIGUOUS" for c in stuck.reason_codes.values())
        assert res.stats["unresolved"] == 1
        assert 2 in res.stats["unresolved_ids"]

    def test_invalid_reason_code_is_contract_violation_then_retried(self):
        """Codex #3: enum-dışı kod SESSİZ KABUL EDİLMEZ — retry'a gider;
        düzelirse resolved, düzelmezse unresolved (yine ELENMEZ)."""
        bad = {"ads": "UYDURMA_KOD", "seo": "STRATEGY_FIT", "social": "AMBIGUOUS"}
        calls = {"n": 0}

        def script(batch_ids, call_no):
            calls["n"] += 1
            codes = bad if calls["n"] == 1 else None
            return json.dumps({"results": [_item(k, codes=codes) for k in batch_ids]})

        p = ScriptedProvider(script)
        res = run_screening(p, CTX, _kws(1), seed=42)
        assert res.stats["contract_violations"]["invalid_reason_codes"] == 1
        assert res.stats["contract_violations"]["invalid_reason_code_samples"] == [
            "ads:UYDURMA_KOD"
        ]
        assert res.results[0].unresolved is False  # retry düzeltti
        assert res.stats["resolved_by_stage"]["missing_retry"] == 1

    def test_persistent_invalid_reason_code_ends_unresolved_not_eliminated(self):
        bad = {"ads": "UYDURMA_KOD", "seo": "STRATEGY_FIT", "social": "AMBIGUOUS"}
        p = ScriptedProvider(
            lambda b, n: json.dumps({"results": [_item(k, codes=bad) for k in b]})
        )
        res = run_screening(p, CTX, _kws(1), seed=42)
        r = res.results[0]
        assert r.unresolved is True
        assert r.ads_fit == UNRESOLVED_FALLBACK_FIT  # eleme YOK


class TestRunnerRetryChain:
    def test_missing_id_targeted_retry_resolves(self):
        kws = _kws(4)
        state = {"first": True}

        def script(batch_ids, call_no):
            if state["first"]:
                state["first"] = False
                return json.dumps({"results": [_item(k) for k in batch_ids if k != 3]})
            return json.dumps({"results": [_item(k) for k in batch_ids]})

        p = ScriptedProvider(script)
        res = run_screening(p, CTX, kws, seed=42)
        assert res.stats["unresolved"] == 0
        assert res.stats["resolved_by_stage"]["missing_retry"] == 1
        # 2. çağrı YALNIZ eksik id'yi taşır
        assert p.calls[1]["batch_ids"] == [3]

    def test_single_retry_limit_enforced(self):
        kws = _kws(SINGLE_RETRY_LIMIT + 5)
        p = ScriptedProvider(lambda b, n: json.dumps({"results": []}))
        res = run_screening(p, CTX, kws, seed=42)
        assert res.stats["single_retry_calls"] == SINGLE_RETRY_LIMIT
        assert res.stats["unresolved"] == len(kws)
        assert all(r.unresolved for r in res.results)

    def test_transient_error_then_success(self):
        calls = {"n": 0}

        def script(batch_ids, call_no):
            calls["n"] += 1
            if calls["n"] == 1:
                return ScreeningTimeoutError("timeout")
            return _all_ok(batch_ids, call_no)

        p = ScriptedProvider(script)
        res = run_screening(p, CTX, _kws(3), seed=42, sleep_fn=lambda s: None)
        assert res.stats["transient_retries"] == 1
        assert res.stats["unresolved"] == 0
        assert res.usage["failures_by_reason"]["timeout"] == 1

    def test_parse_error_retry_then_success(self):
        calls = {"n": 0}

        def script(batch_ids, call_no):
            calls["n"] += 1
            if calls["n"] == 1:
                return "bu JSON değil {{{"
            return _all_ok(batch_ids, call_no)

        p = ScriptedProvider(script)
        res = run_screening(p, CTX, _kws(3), seed=42, sleep_fn=lambda s: None)
        assert res.stats["parse_retries"] == 1
        assert res.stats["unresolved"] == 0

    def test_empty_content_is_retried(self):
        calls = {"n": 0}

        def script(batch_ids, call_no):
            calls["n"] += 1
            if calls["n"] == 1:
                return "   "
            return _all_ok(batch_ids, call_no)

        p = ScriptedProvider(script)
        res = run_screening(p, CTX, _kws(2), seed=42, sleep_fn=lambda s: None)
        assert res.stats["unresolved"] == 0
        assert res.stats["parse_retries"] == 1

    def test_retry_budgets_are_class_independent(self):
        """Codex #5: parse hatası transient bütçesini TÜKETEMEZ.

        Sürekli parse hatasında toplam çağrı = 1 + MAX_PARSE_RETRIES
        (transient bütçesine düşüp 3'e çıkmaz)."""
        p = ScriptedProvider(lambda b, n: "JSON değil {{{")
        res = run_screening(p, CTX, _kws(1), seed=42, sleep_fn=lambda s: None)
        # 1 ana + 1 parse retry = 2 çağrı; sonra missing_retry turları ve
        # single retry aynı sözleşmeyi tekrar uygular
        first_chain = 1 + MAX_PARSE_RETRIES
        assert res.stats["parse_retries"] >= MAX_PARSE_RETRIES
        assert res.stats["transient_retries"] == 0
        # Zincir başına çağrı sayısı sabit: toplam çağrı zincir sayısının katı
        assert len(p.calls) % first_chain == 0

    def test_transient_budget_independent_of_parse(self):
        p = ScriptedProvider(lambda b, n: ScreeningTimeoutError("timeout"))
        res = run_screening(p, CTX, _kws(1), seed=42, sleep_fn=lambda s: None)
        assert res.stats["parse_retries"] == 0
        assert res.stats["transient_retries"] >= MAX_TRANSIENT_RETRIES

    def test_failed_attempt_tokens_counted_in_cost(self):
        """Codex #4: boş-içerik/parse hatasında token FATURALANIR."""
        usage = ScreeningUsage(prompt_tokens=1000, completion_tokens=200)
        calls = {"n": 0}

        def script(batch_ids, call_no):
            calls["n"] += 1
            if calls["n"] == 1:
                return ScreeningEmptyContentError("bos", usage=usage)
            return _all_ok(batch_ids, call_no)

        p = ScriptedProvider(script)
        res = run_screening(p, CTX, _kws(1), seed=42, sleep_fn=lambda s: None)
        assert res.usage["failed_attempt_tokens"] == 1200
        # Başarısız denemenin token'ları toplam maliyete de girer
        assert res.usage["prompt_tokens"] >= 1000
        assert res.usage["requests"] == 2

    def test_auth_error_aborts_immediately(self):
        p = ScriptedProvider(lambda b, n: ScreeningAuthError("anahtar yok"))
        with pytest.raises(ScreeningAuthError):
            run_screening(p, CTX, _kws(2), seed=42, sleep_fn=lambda s: None)
        assert len(p.calls) == 1  # retry YOK

    def test_ghost_and_duplicate_ids_are_contract_violations(self):
        """Codex #3: 'ilk kazanır' sessiz kabulü YASAK — çelişkili duplicate
        çözülmemiş bırakılır, hayalet id sayılır."""
        kws = _kws(2)
        p = ScriptedProvider(lambda b, n: json.dumps({"results": [
            _item(1, ads=2), _item(1, ads=0), _item(999, ads=2),
        ]}))
        res = run_screening(p, CTX, kws, seed=42)
        v = res.stats["contract_violations"]
        assert v["ghost_ids"] >= 1
        assert v["duplicate_ids"] == 1
        assert {r.keyword_id for r in res.results} == {1, 2}
        assert all(r.unresolved for r in res.results)  # ikisi de çözülemedi
        assert all(r.ads_fit == UNRESOLVED_FALLBACK_FIT for r in res.results)

    def test_duplicate_input_ids_rejected(self):
        with pytest.raises(ValueError, match="benzersiz"):
            run_screening(ScriptedProvider(_all_ok), CTX,
                          [{"id": 1, "keyword": "a"}, {"id": 1, "keyword": "b"}],
                          seed=42)


class TestParsing:
    def test_partial_validity_one_bad_item_does_not_kill_batch(self):
        raw = json.dumps({"results": [
            _item(1),
            {"id": 2, "ads_fit": 9, "seo_fit": 1, "social_fit": 0,
             "reason_codes": {"ads": "A", "seo": "B", "social": "C"}},  # fit>2
            _item(3),
        ]})
        result = parse_screening_payload(raw)
        assert result.returned_ids == {1, 3}
        assert len(result.invalid_items) == 1

    def test_markdown_fence_recovered(self):
        raw = "```json\n" + json.dumps({"results": [_item(1)]}) + "\n```"
        assert parse_screening_payload(raw).returned_ids == {1}

    def test_empty_and_garbage_raise_typed(self):
        with pytest.raises(ScreeningEmptyContentError):
            parse_screening_payload("")
        with pytest.raises(ScreeningResponseError):
            parse_screening_payload("düz metin, JSON yok")


class TestDeepSeekAdapter:
    def _provider(self, response_obj):
        from app.core.screening.providers import DeepSeekCorpusScreeningProvider

        class FakeResponse:
            def __init__(self, status, payload=None, text=""):
                self.status_code = status
                self._payload = payload
                self.text = text

            def json(self):
                if self._payload is None:
                    raise ValueError("gövde JSON değil")
                return self._payload

        class FakeClient:
            def __init__(self, resp):
                self.resp = resp
                self.closed = 0

            def post(self, path, json=None):
                self.last_payload = json
                if isinstance(self.resp, Exception):
                    raise self.resp
                return self.resp

            def close(self):
                self.closed += 1

        p = DeepSeekCorpusScreeningProvider(model="deepseek-v4-flash",
                                            api_key="test")
        p._client = FakeClient(response_obj if isinstance(response_obj, Exception)
                               else FakeResponse(*response_obj))
        return p

    def test_success_parses_cache_tokens_and_sends_non_thinking(self):
        payload = {
            "choices": [{"message": {"content": json.dumps(
                {"results": [_item(1)]})}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 700, "completion_tokens": 300,
                      "total_tokens": 1000, "prompt_cache_hit_tokens": 500,
                      "prompt_cache_miss_tokens": 200},
        }
        p = self._provider((200, payload))
        result = p.screen_batch(CTX.as_dict(), [{"id": 1, "keyword": "a"}])
        assert result.returned_ids == {1}
        assert result.usage.cache_hit_tokens == 500
        sent = p._client.last_payload
        assert sent["thinking"] == {"type": "disabled"}
        assert sent["response_format"] == {"type": "json_object"}
        assert sent["stream"] is False

    @pytest.mark.parametrize("status,exc", [
        (401, ScreeningAuthError), (403, ScreeningAuthError),
        (429, ScreeningRateLimitError), (500, Exception), (400, ScreeningResponseError),
    ])
    def test_status_classification(self, status, exc):
        from app.core.screening.providers import ScreeningTransportError

        expected = ScreeningTransportError if status == 500 else exc
        p = self._provider((status, {}, "hata"))
        with pytest.raises(expected):
            p.screen_batch(CTX.as_dict(), [{"id": 1, "keyword": "a"}])

    def test_empty_content_typed(self):
        payload = {"choices": [{"message": {"content": ""}}], "usage": {}}
        p = self._provider((200, payload))
        with pytest.raises(ScreeningEmptyContentError):
            p.screen_batch(CTX.as_dict(), [{"id": 1, "keyword": "a"}])

    def test_close_is_idempotent(self):
        p = self._provider((200, {"choices": [], "usage": {}}))
        client = p._client
        p.close()
        p.close()
        assert client.closed == 1


class TestTelemetry:
    def test_records_stage_and_logical_request(self):
        from app.core.telemetry.usage import UsageCollector

        collector = UsageCollector(scoring_run_id=None)
        collector.flush = lambda: None  # DB'ye yazma
        calls = {"n": 0}

        def script(batch_ids, call_no):
            calls["n"] += 1
            if calls["n"] == 1:
                return ScreeningTimeoutError("timeout")
            return _all_ok(batch_ids, call_no)

        p = ScriptedProvider(script, collector=collector)
        run_screening(p, CTX, _kws(2), seed=42, sleep_fn=lambda s: None)
        events = collector._all_events
        assert len(events) == 2
        assert all(e["stage"] == "corpus_screening" for e in events)
        assert all(e["model"] == "deepseek-v4-flash" for e in events)
        # Retry aynı logical request altında: attempt 1,2
        assert events[0]["request_id"] == events[1]["request_id"]
        assert [e["attempt"] for e in events] == [1, 2]
        assert events[0]["retry_reason"] == "timeout"

    def test_stage_is_registered_in_ai_stages(self):
        from app.core.constants import AI_STAGES

        assert "corpus_screening" in AI_STAGES


class TestCost:
    def test_deepseek_cache_aware_cost(self):
        totals = {"prompt_tokens": 1_000_000, "cache_hit_tokens": 900_000,
                  "cache_miss_tokens": 100_000, "completion_tokens": 1_000_000}
        cost = compute_cost_usd("deepseek-v4-flash", totals)
        # 0.1M miss × $0.14 + 0.9M hit × $0.0028 + 1M out × $0.28
        assert cost == pytest.approx(0.014 + 0.00252 + 0.28, abs=1e-6)

    def test_gemini_without_cache_uses_prompt_tokens(self):
        totals = {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000,
                  "thoughts_tokens": 500_000}
        cost = compute_cost_usd("gemini-3.5-flash-lite", totals)
        assert cost == pytest.approx(0.30 + 1.5 * 2.50, abs=1e-6)

    def test_unknown_model_returns_none_not_zero(self):
        assert compute_cost_usd("bilinmeyen-model", {"prompt_tokens": 10}) is None


class TestManifest:
    def _doc(self):
        manifest = build_manifest(
            provider="deepseek", model="deepseek-v4-flash", seed=42,
            dataset_slug="dijital", dataset_files=[],
            generated_at="2026-07-27T00:00:00+00:00", universe_size=671,
        )
        return seal_artifact({"manifest": manifest, "results": [1, 2, 3]})

    def test_seal_and_validate(self):
        doc = self._doc()
        validate_artifact(doc)
        assert doc["manifest"]["prompt_version"] == PROMPT_VERSION
        assert doc["manifest"]["max_output_tokens"] == 5888

    def test_missing_manifest_rejected(self):
        with pytest.raises(ManifestError, match="manifest"):
            seal_artifact({"results": []})
        with pytest.raises(ManifestError, match="manifest yok"):
            validate_artifact({"results": [], "artifact_payload_sha256": "x"})

    def test_tampered_payload_rejected(self):
        doc = self._doc()
        doc["results"].append(4)
        with pytest.raises(ManifestError, match="payload SHA"):
            validate_artifact(doc)

    def test_contract_drift_rejected(self):
        doc = self._doc()
        doc["manifest"]["prompt_template_sha256"] = "eski" * 16
        doc = seal_artifact(doc)
        with pytest.raises(ManifestError, match="sözleşme değişmiş"):
            validate_artifact(doc)
        validate_artifact(doc, check_contract=False)  # arşiv okuma yolu

    def test_contract_hashes_stable(self):
        assert contract_hashes() == contract_hashes()


class TestBatchPlan:
    """Açık batch planı: konum/komşuluk ablasyonunun temeli."""

    def test_explicit_plan_is_used_verbatim(self):
        kws = _kws(6)
        plan = [[kws[4], kws[0]], [kws[2], kws[5]], [kws[1], kws[3]]]
        p = ScriptedProvider(_all_ok)
        res = run_screening(p, CTX, kws, seed=42, batch_plan=plan)
        assert res.stats["batching_mode"] == "explicit_plan"
        assert [c["batch_ids"] for c in p.calls] == [[5, 1], [3, 6], [2, 4]]
        assert res.stats["unresolved"] == 0
        # Sonuç sırası GİRDİ sırasını korur (plan yalnız çağrı sırasını belirler)
        assert [r.keyword_id for r in res.results] == [k["id"] for k in kws]

    def test_plan_must_cover_universe_exactly_once(self):
        kws = _kws(4)
        p = ScriptedProvider(_all_ok)
        with pytest.raises(ValueError, match="tam olarak bir kez"):
            run_screening(p, CTX, kws, seed=42, batch_plan=[[kws[0], kws[1]]])
        with pytest.raises(ValueError, match="tam olarak bir kez"):
            run_screening(p, CTX, kws, seed=42,
                          batch_plan=[[kws[0], kws[0], kws[1], kws[2], kws[3]]])

    def test_default_mode_still_permutation(self):
        p = ScriptedProvider(_all_ok)
        res = run_screening(p, CTX, _kws(5), seed=42)
        assert res.stats["batching_mode"] == "permutation"


class TestConcurrency:
    """Paralel ana geçiş: hız için; sonuç İÇERİĞİ değişmemeli."""

    def test_concurrent_results_match_sequential(self):
        kws = _kws(BATCH_SIZE * 4)
        seq = run_screening(ScriptedProvider(_all_ok), CTX, kws, seed=42)
        par = run_screening(ScriptedProvider(_all_ok), CTX, kws, seed=42,
                            concurrency=6)
        assert par.stats["concurrency"] == 6
        assert [(r.keyword_id, r.ads_fit, r.seo_fit, r.social_fit)
                for r in par.results] == [
            (r.keyword_id, r.ads_fit, r.seo_fit, r.social_fit) for r in seq.results
        ]
        assert par.stats["unresolved"] == seq.stats["unresolved"] == 0
        assert par.usage["requests"] == seq.usage["requests"]

    def test_concurrent_calls_actually_overlap(self):
        """Gerçekten paralel mi — eşzamanlı çağrı sayısı 1'i aşmalı."""
        import threading as _t

        state = {"active": 0, "peak": 0}
        lock = _t.Lock()

        class SlowProvider(ScriptedProvider):
            def _complete(self, prompt):
                with lock:
                    state["active"] += 1
                    state["peak"] = max(state["peak"], state["active"])
                time.sleep(0.05)
                try:
                    return super()._complete(prompt)
                finally:
                    with lock:
                        state["active"] -= 1

        run_screening(SlowProvider(_all_ok), CTX, _kws(BATCH_SIZE * 4),
                      seed=42, concurrency=4)
        assert state["peak"] > 1

    def test_budget_limit_is_exact_under_concurrency(self):
        """Fren paralel koşuda da AŞILMAZ (rezervasyon kilitli)."""
        p = ScriptedProvider(_all_ok)
        budget = ScreeningBudget(model="deepseek-v4-flash", max_requests=3)
        run_screening(p, CTX, _kws(BATCH_SIZE * 6), seed=42, budget=budget,
                      concurrency=6)
        assert budget.requests_used == 3
        assert len(p.calls) == 3


class TestBudgetReservation:
    """Codex #1: fren İSTEK BAŞINA rezervasyonlu — kombinasyon-sonu değil."""

    def test_request_limit_is_exact(self):
        p = ScriptedProvider(_all_ok)
        budget = ScreeningBudget(model="deepseek-v4-flash", max_requests=2)
        res = run_screening(p, CTX, _kws(BATCH_SIZE * 5), seed=42, budget=budget)
        assert len(p.calls) == 2  # 3. istek HİÇ başlamadı
        assert budget.requests_used == 2
        assert "request_limit" in res.stats["budget_stopped"]
        # Kalan kelimeler unresolved — sessiz eleme YOK
        assert res.stats["unresolved"] > 0
        assert all(r.ads_fit == UNRESOLVED_FALLBACK_FIT
                   for r in res.results if r.unresolved)

    def test_cost_limit_blocks_before_request(self):
        p = ScriptedProvider(_all_ok, model="gemini-3.5-flash")
        # Tek isteğin konservatif tavanı bile bu limiti aşar → hiç çağrı yok
        budget = ScreeningBudget(model="gemini-3.5-flash", max_cost_usd=0.001)
        res = run_screening(p, CTX, _kws(5), seed=42, budget=budget)
        assert len(p.calls) == 0
        assert "cost_limit" in res.stats["budget_stopped"]
        assert res.stats["unresolved"] == 5

    def test_per_request_ceiling_uses_real_prompt_bytes(self):
        budget = ScreeningBudget(model="gemini-3.5-flash")
        # Prompt boyutu verilmezse fallback
        assert budget.per_request_ceiling() == pytest.approx(
            4096 / 1e6 * 1.50 + 5888 / 1e6 * 9.00, abs=1e-9
        )
        # Gerçek prompt bayt sayısı verilirse tavan ondan türer
        assert budget.per_request_ceiling(3554) == pytest.approx(
            3554 / 1e6 * 1.50 + 5888 / 1e6 * 9.00, abs=1e-9
        )
        assert ScreeningBudget(model="bilinmeyen").per_request_ceiling() == 0.0

    def test_settle_uses_actual_cost_not_ceiling(self):
        p = ScriptedProvider(_all_ok, model="deepseek-v4-flash")
        budget = ScreeningBudget(model="deepseek-v4-flash", max_cost_usd=5.0)
        run_screening(p, CTX, _kws(3), seed=42, budget=budget)
        # Gerçekleşen (100 prompt/50 completion) tavandan çok küçük
        assert 0 < budget.cost_committed < budget.per_request_ceiling()
        assert budget.cost_reserved == 0.0  # rezervasyon serbest bırakıldı
        assert budget.ceiling_charges == 0

    def test_missing_usage_charges_the_ceiling(self):
        """Codex 2. tur #1: usage yoksa maliyet SIFIR sayılmaz — tavan
        harcanmış kabul edilir; aksi halde aynı bütçe defalarca kullanılırdı."""
        class NoUsageProvider(ScriptedProvider):
            def _complete(self, prompt):
                raw, _ = super()._complete(prompt)
                return raw, None  # sağlayıcı usage döndürmedi

        p = NoUsageProvider(_all_ok, model="deepseek-v4-flash")
        budget = ScreeningBudget(model="deepseek-v4-flash", max_cost_usd=5.0)
        run_screening(p, CTX, _kws(3), seed=42, budget=budget)
        assert budget.ceiling_charges == 1
        assert budget.cost_settled == pytest.approx(
            budget.per_request_ceiling(
                p.prompt_size_bytes(CTX.as_dict(), _kws(3))
            ), abs=1e-9
        )

    def test_usageless_provider_cannot_reuse_budget(self):
        """Tavan rezerve edilmezse sonsuz istek mümkündü — artık limit
        birkaç istekte dolar."""
        class NoUsageProvider(ScriptedProvider):
            def _complete(self, prompt):
                raw, _ = super()._complete(prompt)
                return raw, None

        p = NoUsageProvider(lambda b, n: json.dumps({"results": []}),
                            model="gemini-3.5-flash")
        # Tek istek tavanı ~0.055 → 0.2 limitte en fazla 3 istek
        budget = ScreeningBudget(model="gemini-3.5-flash", max_cost_usd=0.2)
        res = run_screening(p, CTX, _kws(BATCH_SIZE * 4), seed=42,
                            budget=budget, sleep_fn=lambda s: None)
        assert len(p.calls) <= 4
        assert "cost_limit" in res.stats["budget_stopped"]

    def test_failed_attempt_cost_is_committed(self):
        usage = ScreeningUsage(prompt_tokens=1_000_000, completion_tokens=0)
        p = ScriptedProvider(
            lambda b, n: ScreeningEmptyContentError("bos", usage=usage),
            model="deepseek-v4-flash",
        )
        budget = ScreeningBudget(model="deepseek-v4-flash", max_cost_usd=5.0)
        run_screening(p, CTX, _kws(1), seed=42, budget=budget,
                      sleep_fn=lambda s: None)
        assert budget.cost_committed > 0  # başarısız deneme de faturalanır


class _FakeDb:
    def close(self):
        pass


class TestMetrics:
    def _res(self, kid, ads, seo, social):
        return KeywordScreeningResult(
            keyword_id=kid, keyword=f"k{kid}", ads_fit=ads, seo_fit=seo,
            social_fit=social,
            reason_codes={"ads": "A", "seo": "B", "social": "C"},
        )

    def test_screening_recall_and_false_negatives(self):
        results = [self._res(1, 2, 0, 1), self._res(2, 0, 2, 0),
                   self._res(3, 1, 1, 1)]
        positives = {"ADS": {1, 2}, "SEO": {2}, "SOCIAL": set()}
        rec = screening_recall(results, positives)
        assert rec["ADS"]["passing_all"] == 1
        assert rec["ADS"]["recall_all_positives"] == 0.5
        assert rec["ADS"]["false_negatives"][0]["keyword_id"] == 2
        assert rec["SEO"]["recall_all_positives"] == 1.0
        assert rec["SOCIAL"]["recall_all_positives"] is None

    def test_unresolved_positive_is_not_success(self):
        """Codex #1 (KRİTİK): tamamen çöken model %100 recall ALAMAZ."""
        crashed = KeywordScreeningResult(
            keyword_id=1, keyword="k1", ads_fit=1, seo_fit=1, social_fit=1,
            reason_codes={"ads": "AMBIGUOUS", "seo": "AMBIGUOUS",
                          "social": "AMBIGUOUS"},
            unresolved=True, unresolved_reason="screening_unresolved",
        )
        positives = {"ADS": {1}, "SEO": {1}, "SOCIAL": {1}}
        rec = screening_recall([crashed], positives)
        for ch in ("ADS", "SEO", "SOCIAL"):
            assert rec[ch]["recall_all_positives"] == 0.0  # 1.0 DEĞİL
            assert rec[ch]["positive_unresolved_count"] == 1
            assert rec[ch]["resolved_positives"] == 0
            assert rec[ch]["recall_resolved_only"] is None
        gates = acceptance_gates(rec, coverage=0.0)
        assert gates["passed"] is False
        assert gates["channels"]["ADS"]["status"] == "fail"

    def test_acceptance_gates_require_coverage_too(self):
        good = [self._res(i, 2, 2, 2) for i in (1, 2)]
        positives = {"ADS": {1, 2}, "SEO": {1, 2}, "SOCIAL": {1, 2}}
        rec = screening_recall(good, positives)
        assert acceptance_gates(rec, coverage=1.0)["passed"] is True
        # Mükemmel recall ama düşük kapsam → kapı GEÇMEZ
        assert acceptance_gates(rec, coverage=0.80)["passed"] is False

    def test_llm_fit_ordering_uses_tiebreak(self):
        results = [self._res(1, 2, 0, 0), self._res(2, 2, 0, 0),
                   self._res(3, 0, 0, 0)]
        order = llm_fit_ordering(results, "ads", tiebreak_rank={2: 1, 1: 5})
        assert order == [2, 1, 3]

    def test_candidate_recall_curve(self):
        out = candidate_recall_at([10, 20, 30, 40], {30, 40},
                                  {"B": 2, "2B": 4})
        assert out["positive_ranks"] == [3, 4]
        assert out["recall_at"]["B"]["reached"] == 0
        assert out["recall_at"]["2B"]["recall"] == 1.0

    def test_rrf_multi_source_and_quota_union(self):
        # 1 ve 3 simetrik (1/61+1/63) > 2 (2/62); eşitlik keyword_id ile kırılır
        assert rrf_ordering([[1, 2, 3], [3, 2, 1]], 60) == [1, 3, 2]
        # Tek kaynakta üstte olan, diğerinde de üstteyse net kazanır
        assert rrf_ordering([[5, 6], [5, 6]], 60)[0] == 5
        union = quota_union({"skor": [1, 2, 3, 4], "llm": [9, 8, 7]},
                            {"skor": 2, "llm": 2}, budget=5)
        assert union[:4] == [1, 2, 9, 8]  # kotalar önce, tek kaynak işgal edemez
        assert len(union) == 5

    def test_decision_flip_rate_splits_class_and_passing(self):
        """Codex #3: tam sınıf (0/1/2) ve geçer/kalır (fit>=1) değişimi
        AYRI raporlanır — 1<->2 sıralamayı bozar ama havuzdan atmaz."""
        a = [self._res(1, 2, 1, 0), self._res(2, 1, 1, 1)]
        b = [self._res(1, 1, 1, 0), self._res(2, 0, 1, 1)]
        flip = decision_flip_rate(a, b)
        assert flip["compared"] == 2
        # ADS'te iki kelime de sınıf değiştirdi (2->1 ve 1->0)
        assert flip["per_channel"]["ADS"]["flips"] == 2
        assert flip["any_channel_rate"] == 1.0
        # Ama geçer/kalır sınırını yalnız 1->0 geçti
        assert flip["per_channel_passing"]["ADS"]["flips"] == 1
        assert flip["any_channel_passing_rate"] == 0.5

    def test_bakeoff_acceptance_requires_stability_and_recall(self):
        """Codex #1: tekil koşu kapıları GEÇSE de istikrar/erişim kapıları
        kalırsa model genel kabulden KALIR."""
        from app.core.screening.metrics import bakeoff_acceptance

        def block(passing_rate, raw, best):
            return {"dijital": {
                "seeds": {"42": {
                    "gates_passed": True,
                    "candidate_recall": {
                        "ADS": {"raw_rank": {"B_initial": raw},
                                "llm_fit": {"B_initial": best}},
                        "SEO": {"raw_rank": {"B_initial": raw},
                                "llm_fit": {"B_initial": best}},
                        "SOCIAL": {"raw_rank": {"B_initial": raw},
                                   "llm_fit": {"B_initial": best}},
                    },
                }},
                "seed_stability": {"any_channel_passing_rate": passing_rate},
            }}

        # DeepSeek gerçeği: koşular geçti ama istikrar kapısı KALDI
        bad = bakeoff_acceptance(block(0.55, 13, 40), complete=True)
        assert bad["per_run_screening_gate_passed"] is True
        assert bad["candidate_recall_gate_passed"] is True
        assert bad["stability_gate_passed"] is False
        assert bad["overall_acceptance_passed"] is False

        good = bakeoff_acceptance(block(0.05, 13, 40), complete=True)
        assert good["overall_acceptance_passed"] is True
        # Erişim artışı yoksa da kalır
        flat = bakeoff_acceptance(block(0.05, 20, 20), complete=True)
        assert flat["candidate_recall_gate_passed"] is False
        assert flat["overall_acceptance_passed"] is False
        # Artifact eksikse hiçbir şey geçmez
        incomplete = bakeoff_acceptance(block(0.05, 13, 40), complete=False)
        assert incomplete["overall_acceptance_passed"] is False
