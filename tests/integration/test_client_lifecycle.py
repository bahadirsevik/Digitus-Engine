"""Plan G: client lifecycle + transport-error sınıflandırması.

Run-18 bulgusu: 4× "Cannot send a request, as the client has been closed".
Kök neden hipotezi: kilitsiz lazy-init'te paralel katmanlar iki client
yaratır; yarışı kaybeden client referanssız kalıp finalize edilirken
üzerinde in-flight istek olabilir. Sözleşmeler:

1. Double-checked lock: eşzamanlı ilk erişimde TEK client (3 thread).
2. PID kontrolü: fork sonrası client yeniden yaratılır, eski KAPATILMAZ.
3. close(): idempotent, yalnız root servis sahibi; sonrası yeni client.
4. Transport hataları "transport-error" etiketiyle kaydedilir ve intent
   retry zincirinde rate-limit gibi TAM retriable sayılır.
"""
import os
import threading
import time
from types import SimpleNamespace

import pytest

from app.generators.ai_service import (
    GeminiService,
    is_transport_error,
)


class SlowFakeClient:
    """__init__'te bekleyerek yarış penceresini genişleten fake client."""

    instances = 0

    def __init__(self, api_key=None):
        time.sleep(0.05)
        SlowFakeClient.instances += 1
        self.closed = False

    def close(self):
        self.closed = True


class TestLazyClientLifecycle:
    def test_concurrent_first_access_creates_single_client(self, monkeypatch):
        """Plan G zorunlu test: 3 thread eşzamanlı ilk erişim → TEK client."""
        import app.generators.ai_service as mod

        SlowFakeClient.instances = 0
        monkeypatch.setattr(mod.genai, "Client", SlowFakeClient)
        svc = GeminiService(api_key="k")

        results = []
        errors = []
        barrier = threading.Barrier(3)

        def grab():
            try:
                barrier.wait(timeout=5)
                results.append(svc.client)
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=grab) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors
        assert SlowFakeClient.instances == 1  # kilitsiz halde 2-3 olurdu
        assert len({id(c) for c in results}) == 1  # hepsi aynı nesne

    def test_pid_change_recreates_client_without_closing_old(self, monkeypatch):
        """Fork simülasyonu: PID değişti → yeni client; eski KAPATILMAZ
        (parent'ın fd'lerini kapatmak parent'ı bozar)."""
        import app.generators.ai_service as mod

        SlowFakeClient.instances = 0
        monkeypatch.setattr(mod.genai, "Client", SlowFakeClient)
        svc = GeminiService(api_key="k")
        first = svc.client
        assert SlowFakeClient.instances == 1

        real_pid = os.getpid()
        monkeypatch.setattr(mod.os, "getpid", lambda: real_pid + 12345)
        second = svc.client
        assert second is not first
        assert SlowFakeClient.instances == 2
        assert first.closed is False

    def test_close_is_idempotent_and_allows_recreation(self, monkeypatch):
        import app.generators.ai_service as mod

        monkeypatch.setattr(mod.genai, "Client", SlowFakeClient)
        svc = GeminiService(api_key="k")
        first = svc.client
        svc.close()
        assert first.closed is True
        svc.close()  # idempotent — ikinci çağrı patlamaz

        second = svc.client  # close sonrası erişim YENİ client yaratır
        assert second is not first

    def test_wrapper_cannot_close(self):
        """StageScopedAIService client'ın sahibi DEĞİLDİR — close'u yoktur."""
        svc = GeminiService(api_key="k")
        wrapper = svc.for_stage("intent")
        assert not hasattr(wrapper, "close")


class TestTransportErrorClassification:
    def test_client_closed_message_is_transport(self):
        exc = RuntimeError("Cannot send a request, as the client has been closed.")
        assert is_transport_error(exc) is True

    def test_httpx_exceptions_are_transport(self):
        import httpx

        assert is_transport_error(httpx.ConnectError("boom")) is True
        assert is_transport_error(httpx.ReadTimeout("slow")) is True

    def test_parse_and_quota_errors_are_not_transport(self):
        assert is_transport_error(ValueError("Unterminated string at char 220")) is False
        assert is_transport_error(RuntimeError("429 Resource exhausted")) is False

    def test_execute_labels_transport_error_in_telemetry(
        self, db_session, make_workspace, make_scoring_run, monkeypatch
    ):
        """Provider transport hatası telemetriye 'transport-error:' önekiyle
        yazılır — SQL'de client-closed sınıfı ayrı sayılabilir (plan G)."""
        from app.core.telemetry.usage import UsageCollector
        from app.database.models import AiUsageEvent

        ws = make_workspace("Trans WS")
        run = make_scoring_run(brand_profile_id=ws.id)
        db_session.commit()

        svc = GeminiService(api_key="k")
        svc.collector = UsageCollector(
            scoring_run_id=run.id, task_id="t-transport", flush_every=1
        )

        def boom(**kwargs):
            raise RuntimeError(
                "Cannot send a request, as the client has been closed."
            )

        fake_models = SimpleNamespace(generate_content=boom)
        monkeypatch.setattr(
            GeminiService, "client",
            property(lambda self: SimpleNamespace(models=fake_models)),
        )

        with pytest.raises(RuntimeError):
            svc._execute(
                "p", max_tokens=10, temperature=0.1,
                json_mode=False, response_schema=None, stage="intent",
            )

        row = db_session.query(AiUsageEvent).filter_by(task_id="t-transport").one()
        assert row.retry_reason.startswith("transport-error:")

    def test_intent_transport_error_gets_full_retry_chain(
        self, db_session, monkeypatch
    ):
        """Transport hatası attempt 0 VE 1'de gelse bile fallback'e düşmez —
        rate-limit gibi tam retry alır (eski davranış: attempt 1'deki
        sınıflandırılmamış hata anında fallback'ti)."""
        import json

        import app.core.channel.intent_analyzer as intent_mod
        from app.core.channel.intent_analyzer import IntentAnalyzer

        monkeypatch.setattr(intent_mod.time, "sleep", lambda s: None)

        calls = {"n": 0}

        class FlakyTransportAI:
            def complete_json(self, prompt, max_tokens=6000, temperature=0.3,
                              response_schema=None):
                calls["n"] += 1
                if calls["n"] <= 2:
                    raise RuntimeError(
                        "Cannot send a request, as the client has been closed."
                    )
                return json.dumps({"results": [{
                    "keyword_id": 1,
                    "intent_type": "informational",
                    "confidence": 0.9,
                    "reasoning": "ok",
                }]})

        analyzer = IntentAnalyzer(db_session, FlakyTransportAI())
        results = analyzer._batch_analyze_intent(
            [{"id": 1, "keyword": "borsa analiz"}], channel="SEO",
        )

        assert calls["n"] == 3  # 2 transport hatası + 1 başarı
        assert len(results) == 1
        # Fallback DEĞİL — gerçek AI sonucu döndü
        assert not results[0].get("is_fallback", False)
        assert results[0]["intent_type"] == "informational"
