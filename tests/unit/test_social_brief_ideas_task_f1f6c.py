# -*- coding: utf-8 -*-
"""MİKRO FAZ F1-F.6c — Social Brief Ideas Celery Worker Wrapper Birim Testleri.

Bu test modülü gerçek broker, Celery worker, Redis veya harici AI kullanmaz.
Tüm bağımlılıklar mock ve sahte servislerle izole olarak test edilir.
Test edilen sözleşmeler:
1. Task doğru Celery adıyla kayıtlıdır.
2. bind=True sözleşmesi korunur.
3. max_retries=0'dır (Celery seviyesinde otomatik retry yapılmaz).
4. soft_time_limit=1140 ve time_limit=1200'dür.
5. Yalnız attempt_id broker argümanı kabul edilir.
6. self.request.id orkestratöre task_id olarak aktarılır.
7. SessionLocal session_factory olarak aktarılır.
8. Scope bilgileri DB'den okunur; broker'dan alınmaz.
9. Scope okuma session'ı orkestrasyon başlamadan önce kapanır.
10. UsageCollector doğru scoring_run_id, brand_profile_id ve task_id ile bağlanır.
11. Orkestrasyon sonucu JSON dict'e eksiksiz çevrilir.
12. Completed replay sonucu replayed=True ve ai_calls_used=0 kalır.
13. Orkestrasyon exception'ı yutulmaz ve self.retry çağrılmaz.
14. Başarıda AI servisi kapatılır ve collector flush edilir.
15. Hata yolunda da AI servisi kapatılır ve collector flush edilir.
16. AI oluşturma hatasında açık session kalmaz.
17. Geçersiz attempt_id için AI/orchestrator çağrılmaz.
18. Boş task_id için AI/orchestrator çağrılmaz.
19. Bulunamayan/tutarsız attempt scope'unda AI çağrılmaz.
20. Task doğrudan SocialIdea yazmaz ve attempt row lock'u almaz.
21. temperature=None davranışının değişmediğini doğrular.
22. Celery sonucu JSON serialize edilebilir.
"""
from __future__ import annotations

import inspect
import json
from unittest.mock import Mock, call, patch

import pytest

from app.core.social.idea_orchestration import SocialIdeaOrchestrationResult
from app.database.connection import SessionLocal
from app.tasks.celery_app import celery_app
from app.tasks.generation_tasks import (
    _load_ideas_attempt_telemetry_scope,
    social_brief_ideas_task,
)


class DummyContextTask:
    """Celery bound task request context simülasyonu."""

    def __init__(self, task_id: str | None = "test-task-uuid-1234"):
        self.request = Mock()
        self.request.id = task_id
        self.retry = Mock()



@pytest.fixture
def dummy_orchestration_result():
    """Örnek başarılı orkestrasyon sonucu."""
    return SocialIdeaOrchestrationResult(
        attempt_id=42,
        brief_id=101,
        scoring_run_id=202,
        status="completed",
        total_ideas=6,
        ai_calls_used=2,
        replayed=False,
    )


# ==================== TESTLER ====================

def test_01_task_registered_with_correct_name():
    """1. Task 'generation.social_brief_ideas' Celery adıyla kayıtlıdır."""
    assert "generation.social_brief_ideas" in celery_app.tasks
    task = celery_app.tasks["generation.social_brief_ideas"]
    assert task.name == "generation.social_brief_ideas"
    assert social_brief_ideas_task.name == "generation.social_brief_ideas"


def test_02_task_bind_true():
    """2. Task bind=True dekoratörüyle tanımlanmıştır (ilk argümanı self alan bound method)."""
    t = social_brief_ideas_task._get_current_object()
    params = list(inspect.signature(t.__wrapped__.__func__).parameters.keys())
    assert params[0] == "self", "bind=True olan task ilk parametre olarak 'self' almalıdır."
    assert callable(getattr(t, "bind", None))


def test_03_task_max_retries_zero():
    """3. Celery seviyesinde otomatik retry kapalıdır (max_retries=0)."""
    assert social_brief_ideas_task.max_retries == 0


def test_04_task_time_limits():
    """4. soft_time_limit=1140 ve time_limit=1200 saniyedir."""
    assert social_brief_ideas_task.soft_time_limit == 1140
    assert social_brief_ideas_task.time_limit == 1200


def test_05_accepts_only_attempt_id_argument():
    """5. Task imzası broker'dan yalnız attempt_id kabul eder (brief_id, run_id vb. almaz)."""
    t = social_brief_ideas_task._get_current_object()
    params = list(inspect.signature(t.__wrapped__.__func__).parameters.keys())
    # İlk parametre self (bind=True), ikinci parametre attempt_id
    assert params == ["self", "attempt_id"], f"Beklenen parametreler ['self', 'attempt_id'], bulunan: {params}"


def test_06_request_id_passed_as_task_id(dummy_orchestration_result):
    """6. self.request.id orkestratöre task_id olarak aktarılır."""
    mock_ai = Mock()

    with patch("app.tasks.generation_tasks._load_ideas_attempt_telemetry_scope", return_value=(202, 303)):
        with patch("app.core.telemetry.UsageCollector"):
            with patch("app.generators.ai_service.get_ai_service", return_value=mock_ai):
                with patch("app.core.social.idea_orchestration.run_social_idea_generation", return_value=dummy_orchestration_result) as mock_orch:
                    res = social_brief_ideas_task.apply(args=[42], task_id="custom-task-id-777").get()

                    assert mock_orch.call_count == 1
                    assert mock_orch.call_args.kwargs["task_id"] == "custom-task-id-777"
                    assert mock_orch.call_args.kwargs["attempt_id"] == 42


def test_07_session_factory_passed_as_SessionLocal(dummy_orchestration_result):
    """7. SessionLocal session_factory olarak orkestratöre aktarılır."""
    mock_ai = Mock()

    with patch("app.tasks.generation_tasks._load_ideas_attempt_telemetry_scope", return_value=(202, 303)):
        with patch("app.core.telemetry.UsageCollector"):
            with patch("app.generators.ai_service.get_ai_service", return_value=mock_ai):
                with patch("app.core.social.idea_orchestration.run_social_idea_generation", return_value=dummy_orchestration_result) as mock_orch:
                    social_brief_ideas_task.apply(args=[42], task_id="task-123").get()

                    assert mock_orch.call_args.kwargs["session_factory"] == SessionLocal


def test_08_scope_info_loaded_from_db_not_broker(dummy_orchestration_result):
    """8. scoring_run_id ve brand_profile_id broker'dan değil, DB'den okunur."""
    mock_ai = Mock()

    with patch("app.tasks.generation_tasks._load_ideas_attempt_telemetry_scope", return_value=(505, 606)) as mock_scope:
        with patch("app.core.telemetry.UsageCollector") as mock_collector_cls:
            with patch("app.generators.ai_service.get_ai_service", return_value=mock_ai):
                with patch("app.core.social.idea_orchestration.run_social_idea_generation", return_value=dummy_orchestration_result):
                    social_brief_ideas_task.apply(args=[42], task_id="task-123").get()

                    assert mock_scope.call_count == 1
                    assert mock_scope.call_args.args[1] == 42
                    mock_collector_cls.assert_called_once_with(
                        scoring_run_id=505,
                        brand_profile_id=606,
                        task_id="task-123",
                    )


def test_09_scope_session_closed_before_orchestration(dummy_orchestration_result):
    """9. Scope okuma session'ı orkestrasyon başlamadan önce kapatılır."""
    mock_session = Mock()
    manager = Mock()

    manager.attach_mock(mock_session.close, "session_close")

    def mock_orch(**kwargs):
        manager.orchestrate()
        return dummy_orchestration_result

    with patch("app.tasks.generation_tasks.SessionLocal", return_value=mock_session):
        with patch("app.tasks.generation_tasks._load_ideas_attempt_telemetry_scope", return_value=(1, 2)):
            with patch("app.core.telemetry.UsageCollector"):
                with patch("app.generators.ai_service.get_ai_service", return_value=Mock()):
                    with patch("app.core.social.idea_orchestration.run_social_idea_generation", side_effect=mock_orch):
                        social_brief_ideas_task.apply(args=[42], task_id="task-123").get()

    # session_close, orchestrate'ten önce çağrılmalıdır
    calls = manager.mock_calls
    assert calls[0] == call.session_close()
    assert calls[1] == call.orchestrate()


def test_10_usage_collector_initialized_and_bound_to_ai(dummy_orchestration_result):
    """10. UsageCollector doğru alanlarla kurulur ve AI servisine atanır."""
    mock_ai = Mock()
    mock_collector = Mock()

    with patch("app.tasks.generation_tasks._load_ideas_attempt_telemetry_scope", return_value=(111, 222)):
        with patch("app.core.telemetry.UsageCollector", return_value=mock_collector) as mock_coll_cls:
            with patch("app.generators.ai_service.get_ai_service", return_value=mock_ai):
                with patch("app.core.social.idea_orchestration.run_social_idea_generation", return_value=dummy_orchestration_result):
                    social_brief_ideas_task.apply(args=[42], task_id="task-telemetry").get()

                    mock_coll_cls.assert_called_once_with(
                        scoring_run_id=111,
                        brand_profile_id=222,
                        task_id="task-telemetry",
                    )
                    assert mock_ai.collector == mock_collector


def test_11_orchestration_result_converted_to_json_dict(dummy_orchestration_result):
    """11. Orkestrasyon sonucu eksiksiz dict'e dönüştürülür."""
    mock_ai = Mock()

    with patch("app.tasks.generation_tasks._load_ideas_attempt_telemetry_scope", return_value=(202, 303)):
        with patch("app.core.telemetry.UsageCollector"):
            with patch("app.generators.ai_service.get_ai_service", return_value=mock_ai):
                with patch("app.core.social.idea_orchestration.run_social_idea_generation", return_value=dummy_orchestration_result):
                    res = social_brief_ideas_task.apply(args=[42], task_id="task-123").get()

    assert isinstance(res, dict)
    assert res == {
        "attempt_id": 42,
        "brief_id": 101,
        "scoring_run_id": 202,
        "status": "completed",
        "total_ideas": 6,
        "ai_calls_used": 2,
        "replayed": False,
    }


def test_12_completed_replay_result_replayed_true_and_calls_zero():
    """12. Completed replay durumunda replayed=True ve ai_calls_used=0 korunur."""
    replay_result = SocialIdeaOrchestrationResult(
        attempt_id=42,
        brief_id=101,
        scoring_run_id=202,
        status="completed",
        total_ideas=6,
        ai_calls_used=0,
        replayed=True,
    )

    with patch("app.tasks.generation_tasks._load_ideas_attempt_telemetry_scope", return_value=(202, 303)):
        with patch("app.core.telemetry.UsageCollector"):
            with patch("app.generators.ai_service.get_ai_service", return_value=Mock()):
                with patch("app.core.social.idea_orchestration.run_social_idea_generation", return_value=replay_result):
                    res = social_brief_ideas_task.apply(args=[42], task_id="task-replay").get()

    assert res["replayed"] is True
    assert res["ai_calls_used"] == 0
    assert res["status"] == "completed"


def test_13_orchestration_exception_raised_and_no_self_retry():
    """13. Orkestrasyon exception'ı yutulmaz, Celery'ye yükseltilir ve self.retry çağrılmaz."""
    with patch("app.tasks.generation_tasks._load_ideas_attempt_telemetry_scope", return_value=(202, 303)):
        with patch("app.core.telemetry.UsageCollector"):
            with patch("app.generators.ai_service.get_ai_service", return_value=Mock()):
                with patch(
                    "app.core.social.idea_orchestration.run_social_idea_generation",
                    side_effect=ValueError("Domain error in orchestration"),
                ):
                    with patch.object(social_brief_ideas_task, "retry") as mock_retry:
                        with pytest.raises(ValueError, match="Domain error in orchestration"):
                            social_brief_ideas_task.apply(args=[42], task_id="task-err").get()

                        mock_retry.assert_not_called()


def test_14_ai_closed_and_collector_flushed_on_success(dummy_orchestration_result):
    """14. Başarılı akışta AI servisi kapatılır ve collector flush edilir."""
    mock_ai = Mock()
    mock_collector = Mock()

    with patch("app.tasks.generation_tasks._load_ideas_attempt_telemetry_scope", return_value=(202, 303)):
        with patch("app.core.telemetry.UsageCollector", return_value=mock_collector):
            with patch("app.generators.ai_service.get_ai_service", return_value=mock_ai):
                with patch("app.core.social.idea_orchestration.run_social_idea_generation", return_value=dummy_orchestration_result):
                    social_brief_ideas_task.apply(args=[42], task_id="task-clean").get()

    mock_collector.flush.assert_called_once()
    mock_ai.close.assert_called_once()


def test_15_ai_closed_and_collector_flushed_on_failure():
    """15. Hata durumunda da AI servisi kapatılır ve collector flush edilir."""
    mock_ai = Mock()
    mock_collector = Mock()

    with patch("app.tasks.generation_tasks._load_ideas_attempt_telemetry_scope", return_value=(202, 303)):
        with patch("app.core.telemetry.UsageCollector", return_value=mock_collector):
            with patch("app.generators.ai_service.get_ai_service", return_value=mock_ai):
                with patch(
                    "app.core.social.idea_orchestration.run_social_idea_generation",
                    side_effect=RuntimeError("AI crash"),
                ):
                    with pytest.raises(RuntimeError):
                        social_brief_ideas_task.apply(args=[42], task_id="task-clean-fail").get()

    mock_collector.flush.assert_called_once()
    mock_ai.close.assert_called_once()


def test_16_ai_init_failure_leaves_no_open_session():
    """16. get_ai_service hatasında DB session'ları açık kalmaz (hem scope hem finalization oturumları kapanır)."""
    mock_session = Mock()

    with patch("app.tasks.generation_tasks.SessionLocal", return_value=mock_session):
        with patch("app.tasks.generation_tasks._load_ideas_attempt_telemetry_scope", return_value=(1, 2)):
            with patch("app.core.telemetry.UsageCollector"):
                with patch("app.generators.ai_service.get_ai_service", side_effect=Exception("API key missing")):
                    with pytest.raises(Exception, match="API key missing"):
                        social_brief_ideas_task.apply(args=[42], task_id="task-ai-err").get()

    # Scope oturumu + bootstrap finalization oturumu olmak üzere her iki oturum da kapanmalıdır
    assert mock_session.close.call_count == 2


@pytest.mark.parametrize("invalid_id", [True, False, -5, 0, "42", None, 3.14])
def test_17_invalid_attempt_id_rejects_without_ai_or_orchestration(invalid_id):
    """17. Geçersiz attempt_id için AI ve orkestratör çağrılmadan fail-closed ret verilir."""
    with patch("app.generators.ai_service.get_ai_service") as mock_ai:
        with patch("app.core.social.idea_orchestration.run_social_idea_generation") as mock_orch:
            with pytest.raises(ValueError, match="attempt_id pozitif bir tamsayı olmalıdır"):
                social_brief_ideas_task.apply(args=[invalid_id], task_id="task-val").get()

            mock_ai.assert_not_called()
            mock_orch.assert_not_called()


@pytest.mark.parametrize("empty_task_id", ["", "   ", None])
def test_18_empty_task_id_rejects_without_ai_or_orchestration(empty_task_id):
    """18. Boş task_id için AI ve orkestratör çağrılmadan fail-closed ret verilir."""
    dummy_self = DummyContextTask(empty_task_id)

    with patch("app.generators.ai_service.get_ai_service") as mock_ai:
        with patch("app.core.social.idea_orchestration.run_social_idea_generation") as mock_orch:
            with pytest.raises(ValueError, match="task_id boş olamaz"):
                social_brief_ideas_task.__wrapped__.__func__(dummy_self, 42)

            mock_ai.assert_not_called()
            mock_orch.assert_not_called()


def test_19_missing_or_inconsistent_attempt_scope_rejects_without_ai():
    """19. Bulunamayan veya tutarsız attempt scope durumunda AI çağrısı yapılmaz."""
    with patch(
        "app.tasks.generation_tasks._load_ideas_attempt_telemetry_scope",
        side_effect=ValueError("SocialGenerationAttempt bulunamadı: 999"),
    ):
        with patch("app.generators.ai_service.get_ai_service") as mock_ai:
            with patch("app.core.social.idea_orchestration.run_social_idea_generation") as mock_orch:
                with pytest.raises(ValueError, match="SocialGenerationAttempt bulunamadı"):
                    social_brief_ideas_task.apply(args=[999], task_id="task-inconsistent").get()

                mock_ai.assert_not_called()
                mock_orch.assert_not_called()


def test_20_task_does_not_directly_write_social_ideas_or_lock_attempt():
    """20. Task doğrudan SocialIdea yazmaz ve attempt satır kilidi almaz (FOR UPDATE sorgusu çalıştırmaz)."""
    t = social_brief_ideas_task._get_current_object()
    source = inspect.getsource(t.__wrapped__.__func__)
    assert "SocialIdea" not in source, "Task doğrudan SocialIdea modeline erişmemelidir!"
    assert "with_for_update" not in source, "Task doğrudan with_for_update çalıştırmamalıdır!"
    assert "db.commit()" not in source, "Task kendi başına commit yapmamalıdır!"


def test_21_generator_temperature_none_preserved():
    """21. brief_idea_generator.py içinde temperature=None ayarı korunmalıdır."""
    from app.generators.social.brief_idea_generator import SocialBriefIdeaGenerator

    source = inspect.getsource(SocialBriefIdeaGenerator.generate)
    assert "temperature=None" in source, "SocialBriefIdeaGenerator.generate içinde temperature=None korunmalıdır!"


def test_22_celery_result_is_json_serializable(dummy_orchestration_result):
    """22. Celery task dönüşü JSON-serializable olmalıdır."""
    with patch("app.tasks.generation_tasks._load_ideas_attempt_telemetry_scope", return_value=(202, 303)):
        with patch("app.core.telemetry.UsageCollector"):
            with patch("app.generators.ai_service.get_ai_service", return_value=Mock()):
                with patch("app.core.social.idea_orchestration.run_social_idea_generation", return_value=dummy_orchestration_result):
                    res = social_brief_ideas_task.apply(args=[42], task_id="task-json").get()

    serialized = json.dumps(res)
    assert isinstance(serialized, str)
    deserialized = json.loads(serialized)
    assert deserialized == res
