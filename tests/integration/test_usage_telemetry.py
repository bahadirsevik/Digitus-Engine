"""Plan C testleri: stage-scoped AI API + kalıcı usage telemetrisi.

Sözleşmeler:
- for_stage immutable wrapper (mutable stage yarışı imkânsız)
- Bilinmeyen stage çağrıyı düşürmez, telemetride unknown_stage işareti
- UsageCollector bounded-flush + (request_id, attempt) idempotent yazım
- Başarısız istekler NULL token + retry_reason ile kaydedilir
- Katman kurucuları bilinen stage'lere bağlanır (registry testi)
"""
import pytest

from app.config import settings
from app.core.constants import AI_STAGES
from app.core.telemetry.usage import UsageCollector, estimate_cost
from app.database.models import AiUsageEvent
from app.generators.ai_service import MockAIService, StageScopedAIService


# ==================== for_stage API ====================

class TestStageScopedService:
    def test_wrapper_is_immutable(self):
        svc = MockAIService().for_stage("intent")
        assert isinstance(svc, StageScopedAIService)
        assert svc.stage == "intent"
        with pytest.raises(AttributeError):
            svc.stage = "brand_filter"

    def test_wrapper_delegates_json(self):
        svc = MockAIService().for_stage("social_content")
        out = svc.complete_json('{"x": 1}')
        assert isinstance(out, str)

    def test_unknown_stage_does_not_crash(self):
        svc = MockAIService().for_stage("boyle-bir-stage-yok")
        assert svc.stage == "boyle-bir-stage-yok"
        assert isinstance(svc.complete("test"), str)

    def test_layer_constructors_use_registered_stages(self, db_session):
        """Her katman kurucusu registry'deki bir stage'e bağlanır (plan C)."""
        from app.compliance.geo_checker import GEOComplianceChecker
        from app.core.channel.brand_filter import BrandExclusionFilter
        from app.core.channel.intent_analyzer import IntentAnalyzer
        from app.core.channel.pre_filters.ads_prefilter import AdsPreFilter
        from app.core.channel.pre_filters.seo_prefilter import SeoPreFilter
        from app.core.channel.pre_filters.social_prefilter import SocialPreFilter
        from app.generators.ads.keyword_grouper import KeywordGrouper
        from app.generators.ads.rsa_generator import RSAGenerator
        from app.generators.social.category_generator import CategoryGenerator
        from app.generators.social.content_generator import ContentGenerator
        from app.generators.social.idea_generator import IdeaGenerator

        mock = MockAIService()
        layers = [
            IntentAnalyzer(db_session, mock).ai_service,
            BrandExclusionFilter(db_session, mock).ai_service,
            AdsPreFilter(db_session, mock).ai_service,
            SeoPreFilter(db_session, mock).ai_service,
            SocialPreFilter(db_session, mock).ai_service,
            KeywordGrouper(mock).ai_service,
            RSAGenerator(mock).ai_service,
            CategoryGenerator(mock).ai_service,
            IdeaGenerator(mock).ai_service,
            ContentGenerator(mock).ai_service,
            GEOComplianceChecker(mock).ai_service,
        ]
        for layer_service in layers:
            assert isinstance(layer_service, StageScopedAIService)
            assert layer_service.stage in AI_STAGES, layer_service.stage


# ==================== UsageCollector ====================

class TestUsageCollector:
    def _collector(self, run_id=None, flush_every=100):
        return UsageCollector(scoring_run_id=run_id, task_id="t-1",
                              flush_every=flush_every)

    def test_bounded_flush_persists_incrementally(self, db_session, make_workspace, make_scoring_run):
        ws = make_workspace("Tel WS")
        run = make_scoring_run(brand_profile_id=ws.id)
        run_id = run.id
        db_session.commit()

        c = UsageCollector(scoring_run_id=run_id, task_id="t-flush", flush_every=2)
        for i in range(3):
            c.record(stage="intent", model="gemini-3.5-flash",
                     prompt_tokens=100, candidates_tokens=50,
                     thoughts_tokens=300, total_tokens=450,
                     finish_reason="STOP", latency_ms=100)
        # flush_every=2 → ilk 2 event zaten yazıldı (task ölse de kayıp ≤ pencere)
        persisted = db_session.query(AiUsageEvent).filter_by(task_id="t-flush").count()
        assert persisted >= 2

        summary = c.finalize()
        persisted = db_session.query(AiUsageEvent).filter_by(task_id="t-flush").count()
        assert persisted == 3
        assert summary["requests"] == 3
        assert summary["thoughts_tokens"] == 900
        assert summary["estimated_usd"] > 0

    def test_flush_is_idempotent(self, db_session, make_workspace, make_scoring_run):
        ws = make_workspace("Tel2 WS")
        run = make_scoring_run(brand_profile_id=ws.id)
        db_session.commit()

        c = UsageCollector(scoring_run_id=run.id, task_id="t-idem", flush_every=100)
        c.record(stage="intent", model="gemini-3.5-flash", total_tokens=10)
        # Aynı batch'i iki kez flush etmeye zorla (çift yazım denemesi)
        with c._lock:
            batch_copy = list(c._pending)
        c.flush()
        with c._lock:
            c._pending = batch_copy  # aynı request_id'ler
        c.flush()
        count = db_session.query(AiUsageEvent).filter_by(task_id="t-idem").count()
        assert count == 1  # (request_id, attempt) unique + on_conflict_do_nothing

    def test_failed_request_recorded_with_null_tokens(self, db_session, make_workspace, make_scoring_run):
        ws = make_workspace("Tel3 WS")
        run = make_scoring_run(brand_profile_id=ws.id)
        db_session.commit()

        c = UsageCollector(scoring_run_id=run.id, task_id="t-fail", flush_every=1)
        c.record(stage="ads_rsa", model="gemini-3.5-flash",
                 retry_reason="503 quota", latency_ms=1200)
        row = db_session.query(AiUsageEvent).filter_by(task_id="t-fail").one()
        assert row.prompt_tokens is None
        assert row.retry_reason == "503 quota"
        assert row.stage == "ads_rsa"

    def test_unknown_stage_marked_in_event(self, db_session, make_workspace, make_scoring_run):
        """GeminiService._record_usage bilinmeyen stage'i 'unknown:' önekiyle yazar."""
        from app.generators.ai_service import GeminiService

        ws = make_workspace("Tel4 WS")
        run = make_scoring_run(brand_profile_id=ws.id)
        db_session.commit()

        svc = GeminiService(api_key="test")
        svc.collector = UsageCollector(scoring_run_id=run.id, task_id="t-unk",
                                       flush_every=1)
        svc._record_usage(stage="garip_stage", model="m", usage=None,
                          finish_reason=None, latency_ms=5,
                          retry_reason=None, unknown_stage=True)
        row = db_session.query(AiUsageEvent).filter_by(task_id="t-unk").one()
        assert row.stage == "unknown:garip_stage"

    def test_estimate_cost_includes_thinking_as_output(self):
        events = [{
            "prompt_tokens": 1_000_000,
            "candidates_tokens": 500_000,
            "thoughts_tokens": 500_000,
            "price_snapshot": {"input_per_m": 1.50, "output_per_m": 9.00},
        }]
        cost = estimate_cost(events)
        # 1M giriş (1.50) + 1M çıktı[görünen+düşünme] (9.00) = 10.50
        assert cost["estimated_usd"] == 10.5


# ==================== Retry/attempt sözleşmesi (Codex v8-4) ====================

class TestLogicalRequestContract:
    def test_logical_request_fixes_id_and_increments_attempt(
        self, db_session, make_workspace, make_scoring_run
    ):
        """Aynı-prompt retry zinciri: sabit request_id + attempt 1,2,3."""
        ws = make_workspace("Retry WS")
        run = make_scoring_run(brand_profile_id=ws.id)
        db_session.commit()

        c = UsageCollector(scoring_run_id=run.id, task_id="t-retry", flush_every=1)
        with c.logical_request():
            for _ in range(3):
                c.record(stage="intent", model="gemini-3.5-flash", total_tokens=10)

        rows = (
            db_session.query(AiUsageEvent)
            .filter_by(task_id="t-retry")
            .order_by(AiUsageEvent.attempt)
            .all()
        )
        assert len(rows) == 3
        assert len({r.request_id for r in rows}) == 1  # sabit request_id
        assert [r.attempt for r in rows] == [1, 2, 3]

    def test_records_outside_context_keep_old_behavior(
        self, db_session, make_workspace, make_scoring_run
    ):
        ws = make_workspace("Retry2 WS")
        run = make_scoring_run(brand_profile_id=ws.id)
        db_session.commit()

        c = UsageCollector(scoring_run_id=run.id, task_id="t-noctx", flush_every=1)
        c.record(stage="intent", model="m", total_tokens=1)
        c.record(stage="intent", model="m", total_tokens=1)
        rows = db_session.query(AiUsageEvent).filter_by(task_id="t-noctx").all()
        assert len(rows) == 2
        assert len({r.request_id for r in rows}) == 2  # ayrı mantıksal çağrılar
        assert all(r.attempt == 1 for r in rows)

    def test_nested_context_restores_outer(self):
        c = UsageCollector(flush_every=100)
        with c.logical_request():
            c.record(stage="intent", model="m")
            with c.logical_request():
                c.record(stage="intent", model="m")  # iç: yeni id, attempt=1
            c.record(stage="intent", model="m")  # dış devam: attempt=2
        events = c._all_events
        assert events[0]["request_id"] == events[2]["request_id"]
        assert events[0]["attempt"] == 1 and events[2]["attempt"] == 2
        assert events[1]["request_id"] != events[0]["request_id"]
        assert events[1]["attempt"] == 1

    def test_parse_retry_marks_already_flushed_attempt(
        self, db_session, make_workspace, make_scoring_run, monkeypatch
    ):
        """A caller-level JSON parse failure annotates attempt 1 even when
        flush_every=1 persisted the provider event before parsing."""
        import json

        from app.core.channel.intent_analyzer import IntentAnalyzer

        ws = make_workspace("Parse telemetry WS")
        run = make_scoring_run(brand_profile_id=ws.id)
        db_session.commit()

        class ParseOnceAI:
            def __init__(self):
                self.calls = 0
                self.collector = UsageCollector(
                    scoring_run_id=run.id,
                    task_id="t-parse-retry",
                    flush_every=1,
                )

            def complete_json(self, *args, **kwargs):
                self.calls += 1
                self.collector.record(
                    stage="intent",
                    model="gemini-3.5-flash",
                    total_tokens=10,
                )
                if self.calls == 1:
                    return "not-json"
                return json.dumps({
                    "results": [{
                        "keyword_id": 1,
                        "intent_type": "informational",
                        "confidence": 0.9,
                        "reasoning": "ok",
                        "gt": 1,
                        "ga": 1,
                    }]
                })

        monkeypatch.setattr("app.core.channel.intent_analyzer.time.sleep", lambda _: None)
        ai = ParseOnceAI()
        analyzer = IntentAnalyzer(db_session, ai)
        results = analyzer._batch_analyze_intent(
            [{"id": 1, "keyword": "test", "rank": 1}],
            channel="SEO",
        )
        assert len(results) == 1

        rows = (
            db_session.query(AiUsageEvent)
            .filter_by(task_id="t-parse-retry")
            .order_by(AiUsageEvent.attempt)
            .all()
        )
        assert [row.attempt for row in rows] == [1, 2]
        assert len({row.request_id for row in rows}) == 1
        assert rows[0].retry_reason.startswith("parse_error:")
        assert rows[1].retry_reason is None
        assert ai.collector.summary()["failed_requests"] == 1

    def test_attempt_annotation_preserves_provider_failure(self):
        c = UsageCollector(flush_every=100)
        with c.logical_request():
            c.record(
                stage="intent",
                model="m",
                retry_reason="503 overloaded",
            )
            c.mark_current_attempt_failed("parse_error: invalid json")
        assert c._all_events[0]["retry_reason"] == "503 overloaded"

    def test_blocked_response_recorded_as_response_error(
        self, db_session, make_workspace, make_scoring_run, monkeypatch
    ):
        """Provider yanıt verdi ama _extract_text patladı (blocked/boş):
        event BAŞARI değil, token bilgileri korunarak
        retry_reason='response_error: ...' ile yazılır (Codex v8-4)."""
        from types import SimpleNamespace

        from app.generators.ai_service import AIResponseError, GeminiService

        ws = make_workspace("Blk WS")
        run = make_scoring_run(brand_profile_id=ws.id)
        db_session.commit()

        svc = GeminiService(api_key="test")
        svc.collector = UsageCollector(
            scoring_run_id=run.id, task_id="t-blocked", flush_every=1
        )

        blocked_response = SimpleNamespace(
            prompt_feedback=SimpleNamespace(block_reason="SAFETY"),
            candidates=[SimpleNamespace(finish_reason="SAFETY")],
            usage_metadata=SimpleNamespace(
                prompt_token_count=120, candidates_token_count=0,
                thoughts_token_count=None, total_token_count=120,
            ),
            text=None,
        )
        fake_models = SimpleNamespace(
            generate_content=lambda **kwargs: blocked_response
        )
        monkeypatch.setattr(
            GeminiService, "client",
            property(lambda self: SimpleNamespace(models=fake_models)),
        )

        with pytest.raises(AIResponseError):
            svc._execute(
                "test", max_tokens=100, temperature=0.3,
                json_mode=False, response_schema=None, stage="intent",
            )

        row = db_session.query(AiUsageEvent).filter_by(task_id="t-blocked").one()
        assert row.retry_reason is not None
        assert row.retry_reason.startswith("response_error:")
        assert row.prompt_tokens == 120  # token bilgisi KORUNUR

    def test_success_recorded_after_extract(
        self, db_session, make_workspace, make_scoring_run, monkeypatch
    ):
        from types import SimpleNamespace

        from app.generators.ai_service import GeminiService

        ws = make_workspace("Ok WS")
        run = make_scoring_run(brand_profile_id=ws.id)
        db_session.commit()

        svc = GeminiService(api_key="test")
        svc.collector = UsageCollector(
            scoring_run_id=run.id, task_id="t-ok", flush_every=1
        )
        ok_response = SimpleNamespace(
            prompt_feedback=None,
            candidates=[SimpleNamespace(finish_reason="STOP")],
            usage_metadata=SimpleNamespace(
                prompt_token_count=10, candidates_token_count=5,
                thoughts_token_count=2, total_token_count=17,
            ),
            text='{"ok": true}',
        )
        fake_models = SimpleNamespace(generate_content=lambda **kwargs: ok_response)
        monkeypatch.setattr(
            GeminiService, "client",
            property(lambda self: SimpleNamespace(models=fake_models)),
        )

        out = svc._execute(
            "test", max_tokens=100, temperature=0.3,
            json_mode=True, response_schema=None, stage="intent",
        )
        assert out == '{"ok": true}'
        row = db_session.query(AiUsageEvent).filter_by(task_id="t-ok").one()
        assert row.retry_reason is None
        assert row.candidates_tokens == 5


# ==================== Manifest SHA çözümü (Codex v8-5) ====================

class TestGitShaResolution:
    """Dev bind-mount'ta build-time APP_GIT_SHA bayatlıyordu — manifest
    artık önce .git/HEAD dosya okumasını dener (git binary'siz)."""

    def test_resolves_from_branch_ref_file(self, tmp_path):
        from app.core.gitinfo import resolve_git_sha

        (tmp_path / ".git" / "refs" / "heads").mkdir(parents=True)
        (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
        (tmp_path / ".git" / "refs" / "heads" / "main").write_text(
            "abcdef1234567890abcdef1234567890abcdef12\n"
        )
        assert resolve_git_sha(tmp_path) == "abcdef123456"

    def test_resolves_detached_head(self, tmp_path):
        from app.core.gitinfo import resolve_git_sha

        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "HEAD").write_text(
            "1234567890abcdef1234567890abcdef12345678\n"
        )
        assert resolve_git_sha(tmp_path) == "1234567890ab"

    def test_resolves_from_packed_refs(self, tmp_path):
        from app.core.gitinfo import resolve_git_sha

        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
        (tmp_path / ".git" / "packed-refs").write_text(
            "# pack-refs with: peeled fully-peeled sorted\n"
            "fedcba0987654321fedcba0987654321fedcba09 refs/heads/main\n"
        )
        assert resolve_git_sha(tmp_path) == "fedcba098765"

    def test_missing_git_returns_none(self, tmp_path):
        from app.core.gitinfo import resolve_git_sha

        assert resolve_git_sha(tmp_path) is None

    def test_production_compose_requires_explicit_sha(self):
        from pathlib import Path

        repo_root = Path(__file__).resolve().parents[2]
        compose = (repo_root / "docker-compose.prod.yml").read_text()
        required = "${APP_GIT_SHA:?APP_GIT_SHA must be set for production builds}"
        # Sabit sayı DEĞİL: imajı derleyen HER servis açık SHA istemeli
        # (yeni servis eklenince testin sessizce zayıflamaması için)
        builds = len([line for line in compose.splitlines()
                      if line.strip() == "context: ."])   # frontend hariç
        assert builds >= 4                      # app + 3 celery worker
        assert compose.count(required) == builds
        assert "APP_GIT_SHA: ${APP_GIT_SHA:-dev}" not in compose

    def test_dispatcher_manifest_prefers_runtime_sha(
        self, db_session, make_workspace, make_scoring_run, monkeypatch
    ):
        """Gerçek dispatch: manifest git_sha runtime çözümden gelir
        (test container'ı .:/app bind-mount'ludur → .git okunabilir)."""
        from app.core.channel.assignment_dispatcher import enqueue_channel_assignment
        from app.core.gitinfo import resolve_git_sha
        from app.database.models import ScoringRun

        ws = make_workspace("Sha WS")
        run = make_scoring_run(brand_profile_id=ws.id, status="scored")
        monkeypatch.setattr(
            "app.tasks.intent_tasks.run_channel_assignment_task.apply_async",
            lambda *, args, task_id, **kwargs: None,
        )
        enqueue_channel_assignment(
            db_session, run, relevance_coefficient=1.0, from_status="scored"
        )
        db_session.expire_all()
        manifest = db_session.get(ScoringRun, run.id).execution_manifest
        assert manifest and manifest.get("git_sha")
        runtime_sha = resolve_git_sha()
        if runtime_sha is not None:
            assert manifest["git_sha"] == runtime_sha
        # Codex v18-1: dispatch-anı policy snapshot'ı manifest'te
        snapshot = manifest.get("policy_snapshot")
        assert isinstance(snapshot, dict)
        for field in ("profile_exclude_themes", "competitor_terms_approved",
                      "competitor_channel_policy", "topic_terms_approved"):
            assert field in snapshot, field
        # Pencere deney anahtarı manifest'te görünür (default=1)
        assert manifest.get("candidate_pool_multiplier") == 1


# ==================== Finalize kapsaması (Codex v8-2) ====================

class TestCollectorFinalizeCoverage:
    def test_social_bulk_task_persists_usage_summary(
        self, db_session, make_workspace, make_scoring_run, monkeypatch
    ):
        """SOCIAL bulk görevi <8 event'te bile usage'ı persist eder ve
        TaskResult.result_data.usage taşır (önceden hiç finalize yoktu).

        K12: legacy bulk gorevi varsayilan kapali — bu test generate_
        full_pipeline'in telemetri davranisini olcer, gate'i degil."""
        from types import SimpleNamespace

        from app.database.models import TaskResult
        from app.tasks import generation_tasks
        from app.tasks.task_status import create_task_record

        monkeypatch.setattr(settings, "ENABLE_SOCIAL_LEGACY_BULK", True)

        ws = make_workspace("SocUsage WS")
        run = make_scoring_run(brand_profile_id=ws.id, status="channel_assigned")
        task_id = "social-usage-test"
        create_task_record(task_id, "social", run.id)

        class FakeAI:
            collector = None

        fake_ai = FakeAI()  # task instance attr'a atar — aynı nesne kullanılmalı
        monkeypatch.setattr(
            "app.generators.ai_service.get_ai_service", lambda: fake_ai
        )

        def fake_pipeline(self, request):
            # Üretim sırasında 2 event (flush penceresi 8'in ALTINDA)
            fake_ai.collector.record(stage="social_category", model="m", total_tokens=5)
            fake_ai.collector.record(stage="social_content", model="m", total_tokens=7)
            return SimpleNamespace(
                total_categories=1, total_ideas=1, total_contents=1,
                warnings=[], policy_warnings=[],
            )

        monkeypatch.setattr(
            "app.generators.social.social_generator.SocialGenerator."
            "generate_full_pipeline",
            fake_pipeline,
        )

        generation_tasks.generate_social_task.apply(
            kwargs={"scoring_run_id": run.id, "brand_name": "M"},
            task_id=task_id,
        )

        db_session.expire_all()
        row = db_session.query(TaskResult).filter_by(task_id=task_id).one()
        assert row.status == "completed"
        usage = (row.result_data or {}).get("usage")
        assert usage and usage["requests"] == 2
        # Event'ler DB'de (finalize flush etti)
        assert (
            db_session.query(AiUsageEvent).filter_by(task_id=task_id).count() == 2
        )

    def test_social_bulk_exception_keeps_usage_summary(
        self, db_session, make_workspace, make_scoring_run, monkeypatch
    ):
        from app.database.models import TaskResult
        from app.tasks import generation_tasks
        from app.tasks.task_status import create_task_record

        monkeypatch.setattr(settings, "ENABLE_SOCIAL_LEGACY_BULK", True)

        ws = make_workspace("Social exception usage WS")
        run = make_scoring_run(brand_profile_id=ws.id, status="channel_assigned")
        task_id = "social-usage-exception"
        create_task_record(task_id, "social", run.id)

        class FakeAI:
            collector = None

        fake_ai = FakeAI()
        monkeypatch.setattr(
            "app.generators.ai_service.get_ai_service", lambda: fake_ai
        )

        def fail_pipeline(self, request):
            fake_ai.collector.record(
                stage="social_category", model="m", total_tokens=9
            )
            raise RuntimeError("pipeline failed")

        monkeypatch.setattr(
            "app.generators.social.social_generator.SocialGenerator."
            "generate_full_pipeline",
            fail_pipeline,
        )
        generation_tasks.generate_social_task.apply(
            kwargs={"scoring_run_id": run.id, "brand_name": "M"},
            task_id=task_id,
        )

        db_session.expire_all()
        row = db_session.query(TaskResult).filter_by(task_id=task_id).one()
        assert row.status == "failed"
        assert row.result_data["usage"]["requests"] == 1
        assert row.result_data["usage"]["prompt_tokens"] == 0
        assert row.result_data["scoring_run_id"] == run.id

    def test_social_contents_exception_keeps_usage_summary(
        self, db_session, make_workspace, make_scoring_run, monkeypatch
    ):
        from app.database.models import TaskResult
        from app.tasks import generation_tasks
        from app.tasks.task_status import create_task_record

        ws = make_workspace("Social content exception usage WS")
        run = make_scoring_run(brand_profile_id=ws.id, status="channel_assigned")
        task_id = "social-content-usage-exception"
        create_task_record(task_id, "social_content", run.id)

        class FakeAI:
            collector = None

        fake_ai = FakeAI()
        monkeypatch.setattr(
            "app.generators.ai_service.get_ai_service", lambda: fake_ai
        )

        def fail_contents(self, request, scoring_run_id=None):
            fake_ai.collector.record(
                stage="social_content", model="m", total_tokens=11
            )
            raise RuntimeError("contents failed")

        monkeypatch.setattr(
            "app.generators.social.social_generator.SocialGenerator."
            "generate_contents",
            fail_contents,
        )
        generation_tasks.social_contents_task.apply(
            args=(run.id, [101], "M", None),
            task_id=task_id,
        )

        db_session.expire_all()
        row = db_session.query(TaskResult).filter_by(task_id=task_id).one()
        assert row.status == "failed"
        assert row.result_data["usage"]["requests"] == 1
        assert row.result_data["idea_ids"] == [101]
        assert row.result_data["scoring_run_id"] == run.id
