# -*- coding: utf-8 -*-
"""MİKRO FAZ F1-G.5.6 — Social Brief Contents Celery Worker Wrapper Birim Testleri.

Bu test modülü gerçek broker, Celery worker, Redis veya harici AI kullanmaz.
Tüm bağımlılıklar mock ve sahte servislerle izole olarak test edilir.
Doğrulanan 30 sözleşme:
1. Task doğru Celery adıyla ("generation.social_brief_contents") kayıtlıdır.
2. bind=True sözleşmesi korunur.
3. max_retries=0'dır (Celery seviyesinde otomatik retry yapılmaz).
4. soft_time_limit=1140 ve time_limit=1200'dür.
5. Yalnız attempt_id broker argümanı kabul edilir.
6. self.request.id orkestratöre task_id olarak aktarılır.
7. SessionLocal session_factory olarak orkestratöre aktarılır.
8. Scope bilgileri DB'den okunur (_load_contents_attempt_telemetry_scope); broker'dan alınmaz.
9. Scope okuma session'ı orkestrasyon başlamadan önce kapanır.
10. UsageCollector doğru scoring_run_id, brand_profile_id ve task_id ile bağlanır.
11. Orkestrasyon sonucu JSON dict'e eksiksiz çevrilir (tüm list ve warning dönüşümleri dahil).
12. Completed replay sonucu replayed=True ve ai_calls_used=0 kalır.
13. Orkestrasyon exception'ı yutulmaz ve self.retry çağrılmaz.
14. Başarıda AI servisi kapatılır ve collector flush edilir.
15. Hata yolunda da AI servisi kapatılır ve collector flush edilir.
16. AI init / bootstrap hatasında attempt worker_bootstrap_failed ile finalize edilir ve oturumlar kapanır.
17. UsageCollector init hatasında bootstrap finalization çağrılır ve asıl hata fırlatılır.
18. Scope yükleme hatasında bootstrap finalization çağrılır ve asıl hata fırlatılır.
19. Geçersiz attempt_id için AI/orchestrator çağrılmaz ve finalize tetiklenmez.
20. Boş task_id için AI/orchestrator çağrılmaz ve finalize tetiklenmez.
21. Task doğrudan SocialContent yazmaz ve attempt row lock'u almaz.
22. brief_content_generator.py içinde temperature=None ayarı korunur.
23. Celery sonucu JSON serialize edilebilir.
24. Legacy "generation.social_contents" task'ı adını ve davranışını korur, etkilenmez.
25. "generation.social_brief_ideas" ve "generation.social_brief_ideas_retry" task'ları bağımsızlığını korur.
26. _load_contents_attempt_telemetry_scope başarıyla run_id ve brand_profile_id döndürür.
27. _load_contents_attempt_telemetry_scope geçersiz stage durumunda ValueError fırlatır.
28. _load_contents_attempt_telemetry_scope eksik kayıt veya geçersiz ID durumunda ValueError fırlatır.
29. _finalize_social_contents_bootstrap_failure başarıyla finalize eder ve commit yapar.
30. _finalize_social_contents_bootstrap_failure DB hatasında rollback yapar, log üretir ve hatayı yutar.
"""
from __future__ import annotations

import inspect
import json
from unittest.mock import Mock, call, patch

import pytest

from app.core.social.content_batch_execution import SocialContentBatchWarning
from app.core.social.content_orchestration import SocialContentOrchestrationResult
from app.database.connection import SessionLocal
from app.tasks.celery_app import celery_app
from app.tasks.generation_tasks import (
    _finalize_social_contents_bootstrap_failure,
    _load_contents_attempt_telemetry_scope,
    _load_social_content_orchestrator,
    social_brief_contents_task,
    social_brief_ideas_retry_task,
    social_brief_ideas_task,
    social_contents_task,
)


class DummyContextTask:
    """Celery bound task request context simülasyonu."""

    def __init__(self, task_id: str | None = "test-task-uuid-1234"):
        self.request = Mock()
        self.request.id = task_id
        self.retry = Mock()


@pytest.fixture
def dummy_contents_orchestration_result():
    """Örnek başarılı içerik orkestrasyon sonucu (partial, 1 present, 1 persisted, 1 unresolved)."""
    return SocialContentOrchestrationResult(
        attempt_id=42,
        brief_id=101,
        scoring_run_id=202,
        status="partial",
        requested_idea_ids=(1, 2, 3),
        already_present_idea_ids=(1,),
        persisted_idea_ids=(2,),
        unresolved_idea_ids=(3,),
        warnings=(
            SocialContentBatchWarning(
                idea_id=3,
                reason_code="content_rejected",
                claims=("claim1", "claim2"),
                ai_calls_used=2,
            ),
        ),
        ai_calls_used=4,
        replayed=False,
    )


# ==================== TESTLER ====================

def test_01_task_registered_with_correct_name():
    """1. Task 'generation.social_brief_contents' Celery adıyla kayıtlıdır."""
    assert "generation.social_brief_contents" in celery_app.tasks
    task = celery_app.tasks["generation.social_brief_contents"]
    assert task.name == "generation.social_brief_contents"
    assert social_brief_contents_task.name == "generation.social_brief_contents"


def test_02_task_bind_true():
    """2. Task bind=True dekoratörüyle tanımlanmıştır (ilk argümanı self alan bound method)."""
    t = social_brief_contents_task._get_current_object()
    params = list(inspect.signature(t.__wrapped__.__func__).parameters.keys())
    assert params[0] == "self", "bind=True olan task ilk parametre olarak 'self' almalıdır."
    assert callable(getattr(t, "bind", None))


def test_03_task_max_retries_zero():
    """3. Celery seviyesinde otomatik retry kapalıdır (max_retries=0)."""
    assert social_brief_contents_task.max_retries == 0


def test_04_task_time_limits():
    """4. soft_time_limit=1140 ve time_limit=1200 saniyedir."""
    assert social_brief_contents_task.soft_time_limit == 1140
    assert social_brief_contents_task.time_limit == 1200


def test_05_accepts_only_attempt_id_argument():
    """5. Task imzası broker'dan yalnız attempt_id kabul eder (brief_id, run_id vb. almaz)."""
    t = social_brief_contents_task._get_current_object()
    params = list(inspect.signature(t.__wrapped__.__func__).parameters.keys())
    assert params == ["self", "attempt_id"], f"Beklenen parametreler ['self', 'attempt_id'], bulunan: {params}"


def test_06_request_id_passed_as_task_id(dummy_contents_orchestration_result):
    """6. self.request.id orkestratöre task_id olarak aktarılır."""
    mock_ai = Mock()

    with patch("app.tasks.generation_tasks._load_contents_attempt_telemetry_scope", return_value=(202, 303)):
        with patch("app.core.telemetry.UsageCollector"):
            with patch("app.generators.ai_service.get_ai_service", return_value=mock_ai):
                with patch("app.core.social.content_orchestration.run_social_content_generation", return_value=dummy_contents_orchestration_result) as mock_orch:
                    res = social_brief_contents_task.apply(args=[42], task_id="custom-task-id-contents-888").get()

                    assert mock_orch.call_count == 1
                    assert mock_orch.call_args.kwargs["task_id"] == "custom-task-id-contents-888"
                    assert mock_orch.call_args.kwargs["attempt_id"] == 42


def test_07_session_factory_passed_as_SessionLocal(dummy_contents_orchestration_result):
    """7. SessionLocal session_factory olarak orkestratöre aktarılır."""
    mock_ai = Mock()

    with patch("app.tasks.generation_tasks._load_contents_attempt_telemetry_scope", return_value=(202, 303)):
        with patch("app.core.telemetry.UsageCollector"):
            with patch("app.generators.ai_service.get_ai_service", return_value=mock_ai):
                with patch("app.core.social.content_orchestration.run_social_content_generation", return_value=dummy_contents_orchestration_result) as mock_orch:
                    social_brief_contents_task.apply(args=[42], task_id="task-123").get()

                    assert mock_orch.call_args.kwargs["session_factory"] == SessionLocal


def test_08_scope_info_loaded_from_db_not_broker(dummy_contents_orchestration_result):
    """8. scoring_run_id ve brand_profile_id broker'dan değil, DB'den okunur."""
    mock_ai = Mock()

    with patch("app.tasks.generation_tasks._load_contents_attempt_telemetry_scope", return_value=(505, 606)) as mock_scope:
        with patch("app.core.telemetry.UsageCollector") as mock_collector_cls:
            with patch("app.generators.ai_service.get_ai_service", return_value=mock_ai):
                with patch("app.core.social.content_orchestration.run_social_content_generation", return_value=dummy_contents_orchestration_result):
                    social_brief_contents_task.apply(args=[42], task_id="task-123").get()

                    assert mock_scope.call_count == 1
                    assert mock_scope.call_args.args[1] == 42
                    mock_collector_cls.assert_called_once_with(
                        scoring_run_id=505,
                        brand_profile_id=606,
                        task_id="task-123",
                    )


def test_09_scope_session_closed_before_orchestration(dummy_contents_orchestration_result):
    """9. Scope okuma session'ı orkestrasyon başlamadan önce kapatılır."""
    mock_session = Mock()
    manager = Mock()

    manager.attach_mock(mock_session.close, "session_close")

    def mock_orch(**kwargs):
        manager.orchestrate()
        return dummy_contents_orchestration_result

    with patch("app.tasks.generation_tasks.SessionLocal", return_value=mock_session):
        with patch("app.tasks.generation_tasks._load_contents_attempt_telemetry_scope", return_value=(1, 2)):
            with patch("app.core.telemetry.UsageCollector"):
                with patch("app.generators.ai_service.get_ai_service", return_value=Mock()):
                    with patch("app.core.social.content_orchestration.run_social_content_generation", side_effect=mock_orch):
                        social_brief_contents_task.apply(args=[42], task_id="task-123").get()

    calls = manager.mock_calls
    assert calls[0] == call.session_close()
    assert calls[1] == call.orchestrate()


def test_10_usage_collector_initialized_and_bound_to_ai(dummy_contents_orchestration_result):
    """10. UsageCollector doğru alanlarla kurulur ve AI servisine atanır."""
    mock_ai = Mock()
    mock_collector = Mock()

    with patch("app.tasks.generation_tasks._load_contents_attempt_telemetry_scope", return_value=(111, 222)):
        with patch("app.core.telemetry.UsageCollector", return_value=mock_collector) as mock_coll_cls:
            with patch("app.generators.ai_service.get_ai_service", return_value=mock_ai):
                with patch("app.core.social.content_orchestration.run_social_content_generation", return_value=dummy_contents_orchestration_result):
                    social_brief_contents_task.apply(args=[42], task_id="task-telemetry").get()

                    mock_coll_cls.assert_called_once_with(
                        scoring_run_id=111,
                        brand_profile_id=222,
                        task_id="task-telemetry",
                    )
                    assert mock_ai.collector == mock_collector


def test_11_orchestration_result_converted_to_json_dict(dummy_contents_orchestration_result):
    """11. Orkestrasyon sonucu eksiksiz dict'e dönüştürülür."""
    mock_ai = Mock()

    with patch("app.tasks.generation_tasks._load_contents_attempt_telemetry_scope", return_value=(202, 303)):
        with patch("app.core.telemetry.UsageCollector"):
            with patch("app.generators.ai_service.get_ai_service", return_value=mock_ai):
                with patch("app.core.social.content_orchestration.run_social_content_generation", return_value=dummy_contents_orchestration_result):
                    res = social_brief_contents_task.apply(args=[42], task_id="task-123").get()

    assert isinstance(res, dict)
    assert res == {
        "attempt_id": 42,
        "brief_id": 101,
        "scoring_run_id": 202,
        "status": "partial",
        "requested_idea_ids": [1, 2, 3],
        "already_present_idea_ids": [1],
        "persisted_idea_ids": [2],
        "unresolved_idea_ids": [3],
        "warnings": [
            {
                "idea_id": 3,
                "reason_code": "content_rejected",
                "claims": ["claim1", "claim2"],
                "ai_calls_used": 2,
            }
        ],
        "ai_calls_used": 4,
        "replayed": False,
    }


def test_11b_completed_status_result_converted_to_json_dict():
    """11b. Completed durumunda warnings ve unresolved boş olarak serialize edilir."""
    completed_result = SocialContentOrchestrationResult(
        attempt_id=42,
        brief_id=101,
        scoring_run_id=202,
        status="completed",
        requested_idea_ids=(1, 2),
        already_present_idea_ids=(1,),
        persisted_idea_ids=(2,),
        unresolved_idea_ids=(),
        warnings=(),
        ai_calls_used=2,
        replayed=False,
    )
    with patch("app.tasks.generation_tasks._load_contents_attempt_telemetry_scope", return_value=(202, 303)):
        with patch("app.core.telemetry.UsageCollector"):
            with patch("app.generators.ai_service.get_ai_service", return_value=Mock()):
                with patch("app.core.social.content_orchestration.run_social_content_generation", return_value=completed_result):
                    res = social_brief_contents_task.apply(args=[42], task_id="task-completed").get()

    assert res["status"] == "completed"
    assert res["requested_idea_ids"] == [1, 2]
    assert res["already_present_idea_ids"] == [1]
    assert res["persisted_idea_ids"] == [2]
    assert res["unresolved_idea_ids"] == []
    assert res["warnings"] == []
    assert res["ai_calls_used"] == 2
    assert res["replayed"] is False


def test_12_completed_replay_result_replayed_true_and_calls_zero():
    """12. Completed replay durumunda replayed=True ve ai_calls_used=0 korunur."""
    replay_result = SocialContentOrchestrationResult(
        attempt_id=42,
        brief_id=101,
        scoring_run_id=202,
        status="completed",
        requested_idea_ids=(1, 2),
        already_present_idea_ids=(1, 2),
        persisted_idea_ids=(),
        unresolved_idea_ids=(),
        warnings=(),
        ai_calls_used=0,
        replayed=True,
    )

    with patch("app.tasks.generation_tasks._load_contents_attempt_telemetry_scope", return_value=(202, 303)):
        with patch("app.core.telemetry.UsageCollector"):
            with patch("app.generators.ai_service.get_ai_service", return_value=Mock()):
                with patch("app.core.social.content_orchestration.run_social_content_generation", return_value=replay_result):
                    res = social_brief_contents_task.apply(args=[42], task_id="task-replay").get()

    assert res["replayed"] is True
    assert res["ai_calls_used"] == 0
    assert res["status"] == "completed"
    assert res["requested_idea_ids"] == [1, 2]
    assert res["already_present_idea_ids"] == [1, 2]
    assert res["persisted_idea_ids"] == []


def test_13_orchestration_exception_raised_and_no_self_retry():
    """13. Orkestrasyon exception'ı yutulmaz, Celery'ye yükseltilir ve self.retry çağrılmaz."""
    with patch("app.tasks.generation_tasks._load_contents_attempt_telemetry_scope", return_value=(202, 303)):
        with patch("app.core.telemetry.UsageCollector"):
            with patch("app.generators.ai_service.get_ai_service", return_value=Mock()):
                with patch(
                    "app.core.social.content_orchestration.run_social_content_generation",
                    side_effect=ValueError("Domain error in contents orchestration"),
                ):
                    with patch.object(social_brief_contents_task, "retry") as mock_retry:
                        with pytest.raises(ValueError, match="Domain error in contents orchestration"):
                            social_brief_contents_task.apply(args=[42], task_id="task-err").get()

                        mock_retry.assert_not_called()


def test_14_ai_closed_and_collector_flushed_on_success(dummy_contents_orchestration_result):
    """14. Başarılı akışta AI servisi kapatılır ve collector flush edilir."""
    mock_ai = Mock()
    mock_collector = Mock()

    with patch("app.tasks.generation_tasks._load_contents_attempt_telemetry_scope", return_value=(202, 303)):
        with patch("app.core.telemetry.UsageCollector", return_value=mock_collector):
            with patch("app.generators.ai_service.get_ai_service", return_value=mock_ai):
                with patch("app.core.social.content_orchestration.run_social_content_generation", return_value=dummy_contents_orchestration_result):
                    social_brief_contents_task.apply(args=[42], task_id="task-clean").get()

    mock_collector.flush.assert_called_once()
    mock_ai.close.assert_called_once()


def test_15_ai_closed_and_collector_flushed_on_failure():
    """15. Hata durumunda da AI servisi kapatılır ve collector flush edilir."""
    mock_ai = Mock()
    mock_collector = Mock()

    with patch("app.tasks.generation_tasks._load_contents_attempt_telemetry_scope", return_value=(202, 303)):
        with patch("app.core.telemetry.UsageCollector", return_value=mock_collector):
            with patch("app.generators.ai_service.get_ai_service", return_value=mock_ai):
                with patch(
                    "app.core.social.content_orchestration.run_social_content_generation",
                    side_effect=RuntimeError("Contents AI crash"),
                ):
                    with pytest.raises(RuntimeError):
                        social_brief_contents_task.apply(args=[42], task_id="task-clean-fail").get()

    mock_collector.flush.assert_called_once()
    mock_ai.close.assert_called_once()


def test_16_ai_init_failure_leaves_no_open_session_and_finalizes_failure():
    """16. get_ai_service hatasında pending contents attempt failed yapılır ve oturumlar kapatılır."""
    mock_session = Mock()

    with patch("app.tasks.generation_tasks.SessionLocal", return_value=mock_session):
        with patch("app.tasks.generation_tasks._load_contents_attempt_telemetry_scope", return_value=(1, 2)):
            with patch("app.core.telemetry.UsageCollector"):
                with patch("app.generators.ai_service.get_ai_service", side_effect=Exception("API key missing")):
                    with patch(
                        "app.tasks.generation_tasks._finalize_social_contents_bootstrap_failure"
                    ) as mock_finalize:
                        with pytest.raises(Exception, match="API key missing"):
                            social_brief_contents_task.apply(args=[42], task_id="task-ai-err").get()

                        mock_finalize.assert_called_once_with(attempt_id=42, task_id="task-ai-err")

    assert mock_session.close.call_count >= 1


def test_17_collector_init_failure_finalizes_bootstrap_failure():
    """17. UsageCollector başlatma hatasında bootstrap finalization çağrılır ve asıl hata fırlatılır."""
    mock_session = Mock()

    with patch("app.tasks.generation_tasks.SessionLocal", return_value=mock_session):
        with patch("app.tasks.generation_tasks._load_contents_attempt_telemetry_scope", return_value=(1, 2)):
            with patch("app.core.telemetry.UsageCollector", side_effect=ValueError("Collector config error")):
                with patch(
                    "app.tasks.generation_tasks._finalize_social_contents_bootstrap_failure"
                ) as mock_finalize:
                    with pytest.raises(ValueError, match="Collector config error"):
                        social_brief_contents_task.apply(args=[42], task_id="task-coll-err").get()

                    mock_finalize.assert_called_once_with(attempt_id=42, task_id="task-coll-err")


def test_18_scope_loading_failure_finalizes_bootstrap_failure():
    """18. Scope okuma hatasında bootstrap finalization çağrılır ve asıl hata fırlatılır."""
    mock_session = Mock()

    with patch("app.tasks.generation_tasks.SessionLocal", return_value=mock_session):
        with patch(
            "app.tasks.generation_tasks._load_contents_attempt_telemetry_scope",
            side_effect=ValueError("SocialGenerationAttempt bulunamadı: 999"),
        ):
            with patch(
                "app.tasks.generation_tasks._finalize_social_contents_bootstrap_failure"
            ) as mock_finalize:
                with pytest.raises(ValueError, match="SocialGenerationAttempt bulunamadı: 999"):
                    social_brief_contents_task.apply(args=[999], task_id="task-scope-err").get()

                mock_finalize.assert_called_once_with(attempt_id=999, task_id="task-scope-err")


@pytest.mark.parametrize("invalid_id", [True, False, -5, 0, "42", None, 3.14])
def test_19_invalid_attempt_id_rejects_without_ai_or_orchestration(invalid_id):
    """19. Geçersiz attempt_id için AI ve orkestratör çağrılmadan fail-closed ret verilir."""
    with patch("app.generators.ai_service.get_ai_service") as mock_ai:
        with patch("app.core.social.content_orchestration.run_social_content_generation") as mock_orch:
            with patch("app.tasks.generation_tasks._finalize_social_contents_bootstrap_failure") as mock_finalize:
                with pytest.raises(ValueError, match="attempt_id pozitif bir tamsayı olmalıdır"):
                    social_brief_contents_task.apply(args=[invalid_id], task_id="task-val").get()

                mock_ai.assert_not_called()
                mock_orch.assert_not_called()
                mock_finalize.assert_not_called()


@pytest.mark.parametrize("empty_task_id", ["", "   ", None])
def test_20_empty_task_id_rejects_without_ai_or_orchestration(empty_task_id):
    """20. Boş task_id için AI ve orkestratör çağrılmadan fail-closed ret verilir."""
    dummy_self = DummyContextTask(empty_task_id)

    with patch("app.generators.ai_service.get_ai_service") as mock_ai:
        with patch("app.core.social.content_orchestration.run_social_content_generation") as mock_orch:
            with patch("app.tasks.generation_tasks._finalize_social_contents_bootstrap_failure") as mock_finalize:
                with pytest.raises(ValueError, match="task_id boş olamaz"):
                    social_brief_contents_task.__wrapped__.__func__(dummy_self, 42)

                mock_ai.assert_not_called()
                mock_orch.assert_not_called()
                mock_finalize.assert_not_called()


def test_21_task_does_not_directly_write_social_contents_or_lock_attempt():
    """21. Task doğrudan SocialContent yazmaz ve attempt satır kilidi almaz (FOR UPDATE sorgusu çalıştırmaz)."""
    t = social_brief_contents_task._get_current_object()
    source = inspect.getsource(t.__wrapped__.__func__)
    assert "SocialContent" not in source, "Task doğrudan SocialContent modeline erişmemelidir!"
    assert "with_for_update" not in source, "Task doğrudan with_for_update çalıştırmamalıdır!"
    assert "db.commit()" not in source, "Task kendi başına commit yapmamalıdır!"


def test_22_generator_temperature_none_preserved():
    """22. brief_content_generator.py içinde temperature=None ayarı korunmalıdır."""
    from app.generators.social.brief_content_generator import SocialBriefContentGenerator

    source = inspect.getsource(SocialBriefContentGenerator.generate)
    assert "temperature=None" in source, "SocialBriefContentGenerator.generate içinde temperature=None korunmalıdır!"
    source_repair = inspect.getsource(SocialBriefContentGenerator.repair_once)
    assert "temperature=None" in source_repair, "SocialBriefContentGenerator.repair_once içinde temperature=None korunmalıdır!"


def test_23_celery_result_is_json_serializable(dummy_contents_orchestration_result):
    """23. Celery task dönüşü JSON-serializable olmalıdır."""
    with patch("app.tasks.generation_tasks._load_contents_attempt_telemetry_scope", return_value=(202, 303)):
        with patch("app.core.telemetry.UsageCollector"):
            with patch("app.generators.ai_service.get_ai_service", return_value=Mock()):
                with patch("app.core.social.content_orchestration.run_social_content_generation", return_value=dummy_contents_orchestration_result):
                    res = social_brief_contents_task.apply(args=[42], task_id="task-json").get()

    serialized = json.dumps(res)
    assert isinstance(serialized, str)
    deserialized = json.loads(serialized)
    assert deserialized == res


def test_24_legacy_social_contents_task_unaffected():
    """24. Eski generation.social_contents task'ı adını ve varlığını korur."""
    assert "generation.social_contents" in celery_app.tasks
    assert social_contents_task.name == "generation.social_contents"
    assert social_contents_task.name != social_brief_contents_task.name


def test_25_ideas_and_retry_tasks_unaffected():
    """25. Ideas ve ideas_retry task'ları bağımsızlığını korur."""
    assert social_brief_ideas_task.name == "generation.social_brief_ideas"
    assert social_brief_ideas_retry_task.name == "generation.social_brief_ideas_retry"
    assert social_brief_contents_task.name == "generation.social_brief_contents"
    assert len({social_brief_ideas_task.name, social_brief_ideas_retry_task.name, social_brief_contents_task.name}) == 3


def test_26_load_contents_attempt_telemetry_scope_success():
    """26. _load_contents_attempt_telemetry_scope doğru attempt, brief ve run zincirini çözer."""
    mock_db = Mock()

    mock_attempt = Mock(id=42, brief_id=101, stage="contents")
    mock_brief = Mock(id=101, scoring_run_id=202)
    mock_run = Mock(id=202, brand_profile_id=303)

    mock_db.query.return_value.filter.return_value.first.side_effect = [
        mock_attempt,
        mock_brief,
        mock_run,
    ]

    run_id, brand_profile_id = _load_contents_attempt_telemetry_scope(mock_db, 42)
    assert run_id == 202
    assert brand_profile_id == 303


def test_27_load_contents_attempt_telemetry_scope_wrong_stage():
    """27. _load_contents_attempt_telemetry_scope attempt stage != 'contents' ise statik ValueError fırlatır."""
    mock_db = Mock()

    mock_attempt = Mock(id=42, brief_id=101, stage="ideas")
    mock_db.query.return_value.filter.return_value.first.return_value = mock_attempt

    with pytest.raises(ValueError) as exc_info:
        _load_contents_attempt_telemetry_scope(mock_db, 42)

    msg = str(exc_info.value)
    assert msg == "Social contents attempt stage is invalid."
    assert "ideas" not in msg
    assert "42" not in msg


def test_28_load_contents_attempt_telemetry_scope_missing_entities():
    """28. _load_contents_attempt_telemetry_scope eksik kayıt veya geçersiz ID durumlarında statik ValueError fırlatır."""
    # Attempt bulunamadı
    mock_db1 = Mock()
    mock_db1.query.return_value.filter.return_value.first.return_value = None
    with pytest.raises(ValueError) as exc1:
        _load_contents_attempt_telemetry_scope(mock_db1, 42)
    assert str(exc1.value) == "Social contents attempt could not be loaded."
    assert "42" not in str(exc1.value)

    # Brief bulunamadı
    mock_db2 = Mock()
    mock_attempt = Mock(id=42, brief_id=101, stage="contents")
    mock_db2.query.return_value.filter.return_value.first.side_effect = [mock_attempt, None]
    with pytest.raises(ValueError) as exc2:
        _load_contents_attempt_telemetry_scope(mock_db2, 42)
    assert str(exc2.value) == "Social contents brief could not be loaded."
    assert "101" not in str(exc2.value)

    # Run bulunamadı
    mock_db3 = Mock()
    mock_brief = Mock(id=101, scoring_run_id=202)
    mock_db3.query.return_value.filter.return_value.first.side_effect = [mock_attempt, mock_brief, None]
    with pytest.raises(ValueError) as exc3:
        _load_contents_attempt_telemetry_scope(mock_db3, 42)
    assert str(exc3.value) == "Social contents scoring run could not be loaded."
    assert "202" not in str(exc3.value)

    # Geçersiz scoring_run_id veya brand_profile_id
    mock_db4 = Mock()
    mock_run_invalid = Mock(id=202, brand_profile_id=-1)
    mock_db4.query.return_value.filter.return_value.first.side_effect = [mock_attempt, mock_brief, mock_run_invalid]
    with pytest.raises(ValueError) as exc4:
        _load_contents_attempt_telemetry_scope(mock_db4, 42)
    assert str(exc4.value) == "Social contents scoring run could not be loaded."
    assert "-1" not in str(exc4.value)
    assert "202" not in str(exc4.value)


def test_29_finalize_social_contents_bootstrap_failure_success():
    """29. _finalize_social_contents_bootstrap_failure başarıyla finalize_contents_attempt_failure çağırır ve commit eder."""
    mock_db = Mock()

    with patch("app.tasks.generation_tasks.SessionLocal", return_value=mock_db):
        with patch("app.generators.social.attempt_state.finalize_contents_attempt_failure") as mock_finalize:
            _finalize_social_contents_bootstrap_failure(attempt_id=42, task_id="task-bf-1")

            mock_finalize.assert_called_once_with(
                mock_db,
                attempt_id=42,
                task_id="task-bf-1",
                reason_code="worker_bootstrap_failed",
                error_message="Sosyal içerik worker hazırlığı tamamlanamadı.",
            )
            mock_db.commit.assert_called_once()
            mock_db.close.assert_called_once()


def test_30_finalize_social_contents_bootstrap_failure_db_error_swallowed():
    """30. _finalize_social_contents_bootstrap_failure veritabanı hatasında rollback yapar, loglar ve hata fırlatmaz."""
    mock_db = Mock()
    mock_db.commit.side_effect = RuntimeError("DB connection lost")

    with patch("app.tasks.generation_tasks.SessionLocal", return_value=mock_db):
        with patch("app.generators.social.attempt_state.finalize_contents_attempt_failure"):
            # Hata yutulmalı ve raise edilmemeli
            _finalize_social_contents_bootstrap_failure(attempt_id=42, task_id="task-bf-2")

            mock_db.rollback.assert_called_once()
            mock_db.close.assert_called_once()


def test_31_orchestrator_loader_import_error_triggers_bootstrap_finalizer():
    """31. Orchestrator loader ImportError verdiğinde bootstrap finalizer tam bir kez çağrılır."""
    mock_session = Mock()

    with patch("app.tasks.generation_tasks.SessionLocal", return_value=mock_session):
        with patch("app.tasks.generation_tasks._load_contents_attempt_telemetry_scope", return_value=(1, 2)):
            with patch("app.core.telemetry.UsageCollector"):
                with patch("app.generators.ai_service.get_ai_service", return_value=Mock()):
                    with patch(
                        "app.tasks.generation_tasks._load_social_content_orchestrator",
                        side_effect=ImportError("Cannot import run_social_content_generation"),
                    ):
                        with patch(
                            "app.tasks.generation_tasks._finalize_social_contents_bootstrap_failure"
                        ) as mock_finalize:
                            with pytest.raises(ImportError, match="Cannot import run_social_content_generation"):
                                social_brief_contents_task.apply(args=[42], task_id="task-import-err").get()

                            mock_finalize.assert_called_once_with(attempt_id=42, task_id="task-import-err")


def test_32_orchestrator_loader_import_error_flushes_collector_once():
    """32. Orchestrator loader ImportError verdiğinde UsageCollector tam bir kez flush edilir."""
    mock_collector = Mock()

    with patch("app.tasks.generation_tasks._load_contents_attempt_telemetry_scope", return_value=(1, 2)):
        with patch("app.core.telemetry.UsageCollector", return_value=mock_collector):
            with patch("app.generators.ai_service.get_ai_service", return_value=Mock()):
                with patch(
                    "app.tasks.generation_tasks._load_social_content_orchestrator",
                    side_effect=ImportError("Import failed"),
                ):
                    with patch("app.tasks.generation_tasks._finalize_social_contents_bootstrap_failure"):
                        with pytest.raises(ImportError):
                            social_brief_contents_task.apply(args=[42], task_id="task-flush-test").get()

                        mock_collector.flush.assert_called_once()


def test_33_orchestrator_loader_import_error_closes_ai():
    """33. Orchestrator loader ImportError verdiğinde AI servisi kapatılır."""
    mock_ai = Mock()

    with patch("app.tasks.generation_tasks._load_contents_attempt_telemetry_scope", return_value=(1, 2)):
        with patch("app.core.telemetry.UsageCollector"):
            with patch("app.generators.ai_service.get_ai_service", return_value=mock_ai):
                with patch(
                    "app.tasks.generation_tasks._load_social_content_orchestrator",
                    side_effect=ImportError("Import failed"),
                ):
                    with patch("app.tasks.generation_tasks._finalize_social_contents_bootstrap_failure"):
                        with pytest.raises(ImportError):
                            social_brief_contents_task.apply(args=[42], task_id="task-close-test").get()

                        mock_ai.close.assert_called_once()


def test_34_orchestrator_loader_import_error_raised_unmodified():
    """34. Asıl ImportError hiçbir dönüşüme uğramadan Celery'ye aynen yükseltilir."""
    sentinel_error = ImportError("Exact original import error 12345")

    with patch("app.tasks.generation_tasks._load_contents_attempt_telemetry_scope", return_value=(1, 2)):
        with patch("app.core.telemetry.UsageCollector"):
            with patch("app.generators.ai_service.get_ai_service", return_value=Mock()):
                with patch(
                    "app.tasks.generation_tasks._load_social_content_orchestrator",
                    side_effect=sentinel_error,
                ):
                    with patch("app.tasks.generation_tasks._finalize_social_contents_bootstrap_failure"):
                        with pytest.raises(ImportError) as exc_info:
                            social_brief_contents_task.apply(args=[42], task_id="task-unmodified").get()

                        assert exc_info.value is sentinel_error


def test_35_finalizer_error_does_not_mask_original_import_error():
    """35. Bootstrap finalizer hata verse bile asıl ImportError maskelenmez."""
    sentinel_error = ImportError("Original import failure")

    with patch("app.tasks.generation_tasks._load_contents_attempt_telemetry_scope", return_value=(1, 2)):
        with patch("app.core.telemetry.UsageCollector"):
            with patch("app.generators.ai_service.get_ai_service", return_value=Mock()):
                with patch(
                    "app.tasks.generation_tasks._load_social_content_orchestrator",
                    side_effect=sentinel_error,
                ):
                    with patch(
                        "app.tasks.generation_tasks._finalize_social_contents_bootstrap_failure",
                        side_effect=RuntimeError("Finalizer DB crash"),
                    ):
                        with pytest.raises(ImportError) as exc_info:
                            social_brief_contents_task.apply(args=[42], task_id="task-nomask-1").get()

                        assert exc_info.value is sentinel_error


def test_36_collector_flush_error_does_not_mask_original_import_error():
    """36. Collector flush hata verse bile asıl ImportError maskelenmez."""
    mock_collector = Mock()
    mock_collector.flush.side_effect = RuntimeError("Telemetry flush crash")
    sentinel_error = ImportError("Original import failure")

    with patch("app.tasks.generation_tasks._load_contents_attempt_telemetry_scope", return_value=(1, 2)):
        with patch("app.core.telemetry.UsageCollector", return_value=mock_collector):
            with patch("app.generators.ai_service.get_ai_service", return_value=Mock()):
                with patch(
                    "app.tasks.generation_tasks._load_social_content_orchestrator",
                    side_effect=sentinel_error,
                ):
                    with patch("app.tasks.generation_tasks._finalize_social_contents_bootstrap_failure"):
                        with pytest.raises(ImportError) as exc_info:
                            social_brief_contents_task.apply(args=[42], task_id="task-nomask-2").get()

                        assert exc_info.value is sentinel_error


def test_37_scope_error_messages_contain_no_attempt_id():
    """37. Scope yükleme hata mesajlarında attempt ID asla yer almaz."""
    mock_db = Mock()
    mock_db.query.return_value.filter.return_value.first.return_value = None

    with pytest.raises(ValueError) as exc:
        _load_contents_attempt_telemetry_scope(mock_db, 888777)

    assert "888777" not in str(exc.value)
    assert str(exc.value) == "Social contents attempt could not be loaded."


def test_38_scope_error_messages_contain_no_stage_value():
    """38. Yanlış stage hata mesajında gerçek stage değeri asla yer almaz."""
    mock_db = Mock()
    mock_attempt = Mock(id=1, brief_id=1, stage="forbidden_stage_name")
    mock_db.query.return_value.filter.return_value.first.return_value = mock_attempt

    with pytest.raises(ValueError) as exc:
        _load_contents_attempt_telemetry_scope(mock_db, 1)

    assert "forbidden_stage_name" not in str(exc.value)
    assert str(exc.value) == "Social contents attempt stage is invalid."


def test_39_scope_error_messages_contain_no_brief_id():
    """39. Eksik brief hata mesajında brief ID asla yer almaz."""
    mock_db = Mock()
    mock_attempt = Mock(id=1, brief_id=999888, stage="contents")
    mock_db.query.return_value.filter.return_value.first.side_effect = [mock_attempt, None]

    with pytest.raises(ValueError) as exc:
        _load_contents_attempt_telemetry_scope(mock_db, 1)

    assert "999888" not in str(exc.value)
    assert str(exc.value) == "Social contents brief could not be loaded."


def test_40_scope_error_messages_contain_no_scoring_run_id():
    """40. Eksik scoring_run hata mesajında scoring_run ID asla yer almaz."""
    mock_db = Mock()
    mock_attempt = Mock(id=1, brief_id=1, stage="contents")
    mock_brief = Mock(id=1, scoring_run_id=666555)
    mock_db.query.return_value.filter.return_value.first.side_effect = [mock_attempt, mock_brief, None]

    with pytest.raises(ValueError) as exc:
        _load_contents_attempt_telemetry_scope(mock_db, 1)

    assert "666555" not in str(exc.value)
    assert str(exc.value) == "Social contents scoring run could not be loaded."


def test_41_normal_orchestration_flush_failure_log_contains_no_attempt_id(caplog, dummy_contents_orchestration_result):
    """41. Normal orchestration sonrası flush hatasının logunda attempt ID yer almaz."""
    import logging
    mock_collector = Mock()
    mock_collector.flush.side_effect = RuntimeError("Flush failed")

    with caplog.at_level(logging.WARNING):
        with patch("app.tasks.generation_tasks._load_contents_attempt_telemetry_scope", return_value=(202, 303)):
            with patch("app.core.telemetry.UsageCollector", return_value=mock_collector):
                with patch("app.generators.ai_service.get_ai_service", return_value=Mock()):
                    with patch(
                        "app.core.social.content_orchestration.run_social_content_generation",
                        return_value=dummy_contents_orchestration_result,
                    ):
                        social_brief_contents_task.apply(args=[424242], task_id="task-log-test").get()

    for record in caplog.records:
        assert "424242" not in record.message
        if "telemetry" in record.message.lower():
            assert record.message == "Social contents telemetry could not be flushed."


def test_42_normal_orchestration_flush_failure_log_contains_no_raw_exception_or_secrets(caplog, dummy_contents_orchestration_result):
    """42. Flush hatası logunda ham exception ve sızan gizli veriler bulunmaz."""
    import logging
    mock_collector = Mock()
    mock_collector.flush.side_effect = RuntimeError("LEAKED_SECRET_DATABASE_PASSWORD_XYZ")

    with caplog.at_level(logging.WARNING):
        with patch("app.tasks.generation_tasks._load_contents_attempt_telemetry_scope", return_value=(202, 303)):
            with patch("app.core.telemetry.UsageCollector", return_value=mock_collector):
                with patch("app.generators.ai_service.get_ai_service", return_value=Mock()):
                    with patch(
                        "app.core.social.content_orchestration.run_social_content_generation",
                        return_value=dummy_contents_orchestration_result,
                    ):
                        social_brief_contents_task.apply(args=[42], task_id="task-secret-test").get()

    for record in caplog.records:
        assert "LEAKED_SECRET_DATABASE_PASSWORD_XYZ" not in record.message
        assert "password" not in record.message.lower()


def test_43_bootstrap_failure_log_contains_no_task_id(caplog):
    """43. Bootstrap finalization hatası loglarında task_id yer almaz."""
    import logging
    mock_db = Mock()
    mock_db.commit.side_effect = RuntimeError("DB write error")

    with caplog.at_level(logging.WARNING):
        with patch("app.tasks.generation_tasks.SessionLocal", return_value=mock_db):
            with patch("app.generators.social.attempt_state.finalize_contents_attempt_failure"):
                _finalize_social_contents_bootstrap_failure(
                    attempt_id=99,
                    task_id="sensitive-worker-uuid-abcdef",
                )

    for record in caplog.records:
        assert "sensitive-worker-uuid-abcdef" not in record.message


def test_44_load_social_content_orchestrator_returns_callable():
    """44. _load_social_content_orchestrator orkestratör fonksiyonunu başarıyla döndürür."""
    orch = _load_social_content_orchestrator()
    assert callable(orch)
    assert orch.__name__ == "run_social_content_generation"

