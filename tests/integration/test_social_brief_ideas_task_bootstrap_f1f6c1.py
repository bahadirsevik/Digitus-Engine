# -*- coding: utf-8 -*-
"""MİKRO FAZ F1-F.6c.1 — Worker Bootstrap Failure Finalization Entegrasyon Testleri.

Tüm testler gerçek PostgreSQL test veritabanını ve SessionLocal oturum fabrikasını kullanır.
Gerçek broker, Celery worker veya harici AI kullanılmaz; AI ve collector mock'lar ile izole edilir.

Test edilen 20 sözleşme:
1. get_ai_service hatasında pending attempt hemen failed olur.
2. reason_code tam olarak worker_bootstrap_failed olur.
3. error_message yalnız sabit güvenli mesajdır.
4. lease_expires_at NULL olur ve completed_at atanır.
5. Ham AI init exception metni DB’ye yazılmaz.
6. Telemetry scope yükleme hatasında mevcut attempt bulunabiliyorsa failed finalizasyon denenir.
7. UsageCollector constructor hatasında attempt failed olur.
8. Collector bağlama hatasında attempt failed olur.
9. Bootstrap hatasında run_social_idea_generation çağrılmaz.
10. Orijinal bootstrap exception Celery’ye yeniden yükseltilir.
11. Finalizasyonun kendi hatası orijinal exception’ı maskelemez.
12. Zaten completed attempt bootstrap finalizer ile failed olmaz.
13. Zaten failed attempt’in reason ve mesajı ezilmez.
14. Farklı task_id sahibi running attempt değiştirilmez.
15. Aynı task_id sahibi running attempt bootstrap hatasında failed olabilir.
16. Geçersiz attempt_id ve boş task_id DB finalizasyonu tetiklemez.
17. SQL lock sırası canonical kalır (ScoringRun -> SocialBrief -> SocialGenerationAttempt).
18. TaskResult oluşturulmaz veya güncellenmez.
19. self.retry çağrılmaz.
20. temperature=None korunur.
"""
from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import pytest
from sqlalchemy import event, text
from sqlalchemy.orm import Session

from app.config import settings
from app.core.social.idea_flow import begin_social_idea_generation
from app.database.connection import SessionLocal
from app.database.models import (
    ChannelPool,
    ScoringRun,
    SocialBrief,
    SocialBriefKeyword,
    SocialBriefTarget,
    SocialCategory,
    SocialGenerationAttempt,
    TaskResult,
)
from app.schemas.social_brief import SocialBriefIdeasGenerateRequest
from app.tasks.generation_tasks import (
    _finalize_social_ideas_bootstrap_failure,
    social_brief_ideas_task,
)

T0 = datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc)
SAFE_BOOTSTRAP_ERROR_MSG = "Fikir üretimi worker hazırlığı tamamlanamadı."


@pytest.fixture
def enable_flag(monkeypatch):
    """Test süresince feature flag'i aktif kılar."""
    monkeypatch.setattr(settings, "ENABLE_SOCIAL_BRIEF_FLOW", True)
    yield


def _setup_ready_ideas_environment(
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
    task_id: str = "task-boot-101",
    num_categories: int = 2,
):
    """Fikir üretimi testleri için preflight edilmiş pending attempt ortamı kurar."""
    workspace = make_workspace(name=f"Brand-{task_id}", status="confirmed")
    run = make_scoring_run(
        brand_profile_id=workspace.id,
        status="completed",
        algorithm_version="v3",
        channel_pool_policy_version=workspace.policy_version or 1,
        relevance_anchor_version=workspace.anchor_version or 1,
        channel_assignment_version=1,
        skip_relevance=True,
    )

    kws = []
    for i in range(2):
        kw = make_keyword(
            text_value=f"Keyword {task_id} {i + 1}",
            brand_profile_id=workspace.id,
        )
        pool = ChannelPool(
            scoring_run_id=run.id,
            keyword_id=kw.id,
            channel="SOCIAL",
            final_rank=i + 1,
            relevance_score=0.9,
            adjusted_score=20.0 - i,
        )
        db_session.add(pool)
        kws.append(kw)

    brief = SocialBrief(
        scoring_run_id=run.id,
        brand_name_snapshot="Bootstrap Brand",
        brand_context_snapshot="Bootstrap Context",
        channel_assignment_version=1,
        format_matrix_version="v1",
        is_stale=False,
        locked_at=T0,
    )
    db_session.add(brief)
    db_session.flush()

    for pos, kw in enumerate(kws):
        bk = SocialBriefKeyword(
            brief_id=brief.id,
            keyword_id=kw.id,
            keyword_snapshot=kw.keyword,
            position=pos,
        )
        db_session.add(bk)

    targets_data = [("instagram", "post"), ("twitter", "thread")]
    for plat, fmt in targets_data:
        t = SocialBriefTarget(
            brief_id=brief.id,
            platform=plat,
            content_format=fmt,
        )
        db_session.add(t)

    # Categories attempt
    cat_attempt = SocialGenerationAttempt(
        brief_id=brief.id,
        stage="categories",
        idempotency_key=f"cat-attempt-{task_id}",
        status="completed",
        heartbeat_at=T0,
        lease_expires_at=T0 + timedelta(seconds=1500),
        created_at=T0,
    )
    db_session.add(cat_attempt)
    db_session.flush()

    # Categories
    categories = []
    for i in range(num_categories):
        cat = SocialCategory(
            scoring_run_id=run.id,
            brief_id=brief.id,
            category_name=f"Kategori {i + 1}",
            category_type="educational",
            description=f"Açıklama {i + 1}",
            is_stale=False,
            relevance_score=0.9,
            suggested_keyword_ids=[kws[0].id],
        )
        db_session.add(cat)
        categories.append(cat)

    db_session.commit()

    # Preflight ile pending ideas attempt oluştur
    cat_ids = [c.id for c in categories]
    req = SocialBriefIdeasGenerateRequest(
        idempotency_key=f"idempotency-{task_id}",
        category_ids=cat_ids,
        ideas_per_category=3,
    )

    start = begin_social_idea_generation(
        db_session,
        brief_id=brief.id,
        brand_profile_id=workspace.id,
        request=req,
    )
    db_session.commit()

    attempt = db_session.query(SocialGenerationAttempt).filter_by(id=start.attempt_id).one()
    return workspace, run, brief, categories, kws, attempt


# ==================== TESTLER ====================

def test_01_get_ai_service_error_marks_pending_attempt_failed(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """1. get_ai_service hatasında pending attempt hemen failed olur."""
    ws, run, brief, cats, kws, attempt = _setup_ready_ideas_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="t01"
    )
    assert attempt.status == "pending"

    with patch("app.generators.ai_service.get_ai_service", side_effect=RuntimeError("AI Init Crash")):
        with pytest.raises(RuntimeError, match="AI Init Crash"):
            social_brief_ideas_task.apply(args=[attempt.id], task_id="task-t01").get()

    db_session.expire_all()
    refreshed = db_session.query(SocialGenerationAttempt).filter_by(id=attempt.id).one()
    assert refreshed.status == "failed"


def test_02_reason_code_is_strictly_worker_bootstrap_failed(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """2. reason_code tam olarak worker_bootstrap_failed olur."""
    ws, run, brief, cats, kws, attempt = _setup_ready_ideas_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="t02"
    )

    with patch("app.generators.ai_service.get_ai_service", side_effect=Exception("Provider timeout")):
        with pytest.raises(Exception, match="Provider timeout"):
            social_brief_ideas_task.apply(args=[attempt.id], task_id="task-t02").get()

    db_session.expire_all()
    refreshed = db_session.query(SocialGenerationAttempt).filter_by(id=attempt.id).one()
    assert refreshed.reason_code == "worker_bootstrap_failed"


def test_03_error_message_is_strictly_safe_hardcoded_message(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """3. error_message yalnız sabit güvenli mesajdır."""
    ws, run, brief, cats, kws, attempt = _setup_ready_ideas_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="t03"
    )

    with patch("app.generators.ai_service.get_ai_service", side_effect=Exception("Internal auth error")):
        with pytest.raises(Exception):
            social_brief_ideas_task.apply(args=[attempt.id], task_id="task-t03").get()

    db_session.expire_all()
    refreshed = db_session.query(SocialGenerationAttempt).filter_by(id=attempt.id).one()
    assert refreshed.error_message == SAFE_BOOTSTRAP_ERROR_MSG


def test_04_lease_expires_at_is_none_and_completed_at_set(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """4. lease_expires_at NULL olur ve completed_at atanır."""
    ws, run, brief, cats, kws, attempt = _setup_ready_ideas_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="t04"
    )

    with patch("app.generators.ai_service.get_ai_service", side_effect=Exception("Crash")):
        with pytest.raises(Exception):
            social_brief_ideas_task.apply(args=[attempt.id], task_id="task-t04").get()

    db_session.expire_all()
    refreshed = db_session.query(SocialGenerationAttempt).filter_by(id=attempt.id).one()
    assert refreshed.lease_expires_at is None
    assert refreshed.completed_at is not None


def test_05_raw_ai_init_exception_not_leaked_to_db(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """5. Ham AI init exception metni DB’ye yazılmaz."""
    ws, run, brief, cats, kws, attempt = _setup_ready_ideas_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="t05"
    )
    secret_key = "AI_SECRET_KEY_XYZZY_9999_NEVER_LOG"

    with patch("app.generators.ai_service.get_ai_service", side_effect=ValueError(f"Bad key: {secret_key}")):
        with pytest.raises(ValueError):
            social_brief_ideas_task.apply(args=[attempt.id], task_id="task-t05").get()

    db_session.expire_all()
    refreshed = db_session.query(SocialGenerationAttempt).filter_by(id=attempt.id).one()
    assert secret_key not in (refreshed.error_message or "")
    assert "Bad key" not in (refreshed.error_message or "")
    assert refreshed.error_message == SAFE_BOOTSTRAP_ERROR_MSG


def test_06_telemetry_scope_load_error_attempts_failure_finalization(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """6. Telemetry scope yükleme hatasında mevcut attempt bulunabiliyorsa failed finalizasyon denenir."""
    ws, run, brief, cats, kws, attempt = _setup_ready_ideas_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="t06"
    )

    with patch(
        "app.tasks.generation_tasks._load_ideas_attempt_telemetry_scope",
        side_effect=RuntimeError("Scope load failed"),
    ):
        with pytest.raises(RuntimeError, match="Scope load failed"):
            social_brief_ideas_task.apply(args=[attempt.id], task_id="task-t06").get()

    db_session.expire_all()
    refreshed = db_session.query(SocialGenerationAttempt).filter_by(id=attempt.id).one()
    assert refreshed.status == "failed"
    assert refreshed.reason_code == "worker_bootstrap_failed"
    assert refreshed.error_message == SAFE_BOOTSTRAP_ERROR_MSG


def test_07_usage_collector_constructor_error_marks_attempt_failed(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """7. UsageCollector constructor hatasında attempt failed olur."""
    ws, run, brief, cats, kws, attempt = _setup_ready_ideas_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="t07"
    )

    with patch("app.core.telemetry.UsageCollector", side_effect=TypeError("UsageCollector config invalid")):
        with pytest.raises(TypeError, match="UsageCollector config invalid"):
            social_brief_ideas_task.apply(args=[attempt.id], task_id="task-t07").get()

    db_session.expire_all()
    refreshed = db_session.query(SocialGenerationAttempt).filter_by(id=attempt.id).one()
    assert refreshed.status == "failed"
    assert refreshed.reason_code == "worker_bootstrap_failed"


def test_08_collector_binding_error_marks_attempt_failed(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """8. Collector bağlama hatasında attempt failed olur."""
    ws, run, brief, cats, kws, attempt = _setup_ready_ideas_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="t08"
    )

    class BrokenCollectorAIService:
        @property
        def collector(self):
            return None

        @collector.setter
        def collector(self, value):
            raise AttributeError("Collector assignment rejected")

        def close(self):
            pass

    with patch("app.generators.ai_service.get_ai_service", return_value=BrokenCollectorAIService()):
        with pytest.raises(AttributeError, match="Collector assignment rejected"):
            social_brief_ideas_task.apply(args=[attempt.id], task_id="task-t08").get()

    db_session.expire_all()
    refreshed = db_session.query(SocialGenerationAttempt).filter_by(id=attempt.id).one()
    assert refreshed.status == "failed"
    assert refreshed.reason_code == "worker_bootstrap_failed"


def test_09_orchestration_not_called_on_bootstrap_error(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """9. Bootstrap hatasında run_social_idea_generation çağrılmaz."""
    ws, run, brief, cats, kws, attempt = _setup_ready_ideas_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="t09"
    )

    with patch("app.generators.ai_service.get_ai_service", side_effect=Exception("Bootstrap error")):
        with patch("app.core.social.idea_orchestration.run_social_idea_generation") as mock_orch:
            with pytest.raises(Exception):
                social_brief_ideas_task.apply(args=[attempt.id], task_id="task-t09").get()

            mock_orch.assert_not_called()


def test_10_original_bootstrap_exception_reraised_to_celery(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """10. Orijinal bootstrap exception Celery’ye yeniden yükseltilir."""
    ws, run, brief, cats, kws, attempt = _setup_ready_ideas_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="t10"
    )

    custom_exc = ArithmeticError("Zero division inside bootstrap custom logic 777")
    with patch("app.generators.ai_service.get_ai_service", side_effect=custom_exc):
        with pytest.raises(ArithmeticError, match="Zero division inside bootstrap custom logic 777"):
            social_brief_ideas_task.apply(args=[attempt.id], task_id="task-t10").get()


def test_11_finalizer_own_error_does_not_mask_original_exception(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """11. Finalizasyonun kendi hatası orijinal exception’ı maskelemez."""
    ws, run, brief, cats, kws, attempt = _setup_ready_ideas_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="t11"
    )

    # finalize_ideas_attempt_failure içinde hata fırlatılsa dahi asıl bootstrap hatası döner
    with patch(
        "app.generators.social.attempt_state.finalize_ideas_attempt_failure",
        side_effect=RuntimeError("Database down during failure finalization"),
    ):
        with patch("app.generators.ai_service.get_ai_service", side_effect=ValueError("Original AI Error 555")):
            with pytest.raises(ValueError, match="Original AI Error 555"):
                social_brief_ideas_task.apply(args=[attempt.id], task_id="task-t11").get()


def test_12_completed_attempt_not_marked_failed_by_bootstrap_finalizer(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """12. Zaten completed attempt bootstrap finalizer ile failed olmaz."""
    ws, run, brief, cats, kws, attempt = _setup_ready_ideas_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="t12"
    )
    attempt.status = "completed"
    attempt.completed_at = T0
    db_session.commit()

    _finalize_social_ideas_bootstrap_failure(attempt_id=attempt.id, task_id="task-t12")

    db_session.expire_all()
    refreshed = db_session.query(SocialGenerationAttempt).filter_by(id=attempt.id).one()
    assert refreshed.status == "completed"
    assert refreshed.reason_code is None


def test_13_failed_attempt_reason_and_message_not_overwritten(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """13. Zaten failed attempt’in reason ve mesajı ezilmez."""
    ws, run, brief, cats, kws, attempt = _setup_ready_ideas_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="t13"
    )
    attempt.status = "failed"
    attempt.reason_code = "brief_stale"
    attempt.error_message = "Orijinal bayatlık hatası"
    attempt.completed_at = T0
    db_session.commit()

    _finalize_social_ideas_bootstrap_failure(attempt_id=attempt.id, task_id="task-t13")

    db_session.expire_all()
    refreshed = db_session.query(SocialGenerationAttempt).filter_by(id=attempt.id).one()
    assert refreshed.status == "failed"
    assert refreshed.reason_code == "brief_stale"
    assert refreshed.error_message == "Orijinal bayatlık hatası"


def test_14_running_attempt_owned_by_different_task_id_not_modified(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """14. Farklı task_id sahibi running attempt değiştirilmez."""
    ws, run, brief, cats, kws, attempt = _setup_ready_ideas_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="t14"
    )
    attempt.status = "running"
    attempt.task_id = "other-worker-task-999"
    attempt.started_at = T0
    attempt.lease_expires_at = T0 + timedelta(seconds=1500)
    db_session.commit()

    _finalize_social_ideas_bootstrap_failure(attempt_id=attempt.id, task_id="my-worker-task-111")

    db_session.expire_all()
    refreshed = db_session.query(SocialGenerationAttempt).filter_by(id=attempt.id).one()
    assert refreshed.status == "running"
    assert refreshed.task_id == "other-worker-task-999"


def test_15_running_attempt_owned_by_same_task_id_can_be_marked_failed(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """15. Aynı task_id sahibi running attempt bootstrap hatasında failed olabilir."""
    ws, run, brief, cats, kws, attempt = _setup_ready_ideas_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="t15"
    )
    attempt.status = "running"
    attempt.task_id = "my-worker-task-111"
    attempt.started_at = T0
    attempt.lease_expires_at = T0 + timedelta(seconds=1500)
    db_session.commit()

    _finalize_social_ideas_bootstrap_failure(attempt_id=attempt.id, task_id="my-worker-task-111")

    db_session.expire_all()
    refreshed = db_session.query(SocialGenerationAttempt).filter_by(id=attempt.id).one()
    assert refreshed.status == "failed"
    assert refreshed.reason_code == "worker_bootstrap_failed"
    assert refreshed.error_message == SAFE_BOOTSTRAP_ERROR_MSG


def test_16_invalid_attempt_id_and_empty_task_id_do_not_trigger_db_finalization(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """16. Geçersiz attempt_id ve boş task_id DB finalizasyonu tetiklemez."""
    with patch("app.tasks.generation_tasks._finalize_social_ideas_bootstrap_failure") as mock_fin:
        # Geçersiz attempt_id
        with pytest.raises(ValueError, match="attempt_id pozitif bir tamsayı olmalıdır"):
            social_brief_ideas_task.apply(args=[-5], task_id="task-val").get()

        mock_fin.assert_not_called()

        # Boş task_id
        dummy_task = Mock()
        dummy_task.request = Mock(id="")
        with pytest.raises(ValueError, match="task_id boş olamaz"):
            social_brief_ideas_task.__wrapped__.__func__(dummy_task, 42)

        mock_fin.assert_not_called()


def test_17_sql_lock_order_is_canonical_scoring_run_brief_attempt(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """17. SQL lock sırası canonical kalır: ScoringRun -> SocialBrief -> SocialGenerationAttempt."""
    ws, run, brief, cats, kws, attempt = _setup_ready_ideas_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="t17"
    )

    captured_locks: list[str] = []

    def _query_catcher(conn, cursor, statement, parameters, context, executemany):
        stmt_lower = statement.lower()
        if "for update" in stmt_lower:
            if "scoring_runs" in stmt_lower:
                captured_locks.append("ScoringRun")
            elif "social_briefs" in stmt_lower:
                captured_locks.append("SocialBrief")
            elif "social_generation_attempts" in stmt_lower:
                captured_locks.append("SocialGenerationAttempt")

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", _query_catcher)
    try:
        _finalize_social_ideas_bootstrap_failure(attempt_id=attempt.id, task_id="task-t17")
    finally:
        event.remove(engine, "before_cursor_execute", _query_catcher)

    assert "ScoringRun" in captured_locks
    assert "SocialBrief" in captured_locks
    assert "SocialGenerationAttempt" in captured_locks

    sr_idx = captured_locks.index("ScoringRun")
    sb_idx = captured_locks.index("SocialBrief")
    att_idx = captured_locks.index("SocialGenerationAttempt")

    assert sr_idx < sb_idx < att_idx, (
        f"Kilit sırası canonical olmalıdır! Bulunan: {captured_locks}"
    )


def test_18_no_task_result_created_or_updated(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """18. TaskResult oluşturulmaz veya güncellenmez."""
    ws, run, brief, cats, kws, attempt = _setup_ready_ideas_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="t18"
    )

    task_results_count_before = db_session.query(TaskResult).count()

    with patch("app.generators.ai_service.get_ai_service", side_effect=Exception("Bootstrap failure")):
        with pytest.raises(Exception):
            social_brief_ideas_task.apply(args=[attempt.id], task_id="task-t18").get()

    task_results_count_after = db_session.query(TaskResult).count()
    assert task_results_count_before == task_results_count_after, (
        "TaskResult oluşturulmamalı veya değiştirilmemelidir!"
    )


def test_19_self_retry_not_called_on_bootstrap_failure(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """19. self.retry çağrılmaz ve max_retries=0'dır."""
    ws, run, brief, cats, kws, attempt = _setup_ready_ideas_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="t19"
    )

    assert social_brief_ideas_task.max_retries == 0

    with patch("app.generators.ai_service.get_ai_service", side_effect=Exception("Fatal AI crash")):
        with patch.object(social_brief_ideas_task, "retry") as mock_retry:
            with pytest.raises(Exception):
                social_brief_ideas_task.apply(args=[attempt.id], task_id="task-t19").get()

            mock_retry.assert_not_called()


def test_20_temperature_none_preserved():
    """20. temperature=None generator içinde korunmalıdır."""
    from app.generators.social.brief_idea_generator import SocialBriefIdeaGenerator

    source = inspect.getsource(SocialBriefIdeaGenerator.generate)
    assert "temperature=None" in source, "brief_idea_generator içinde temperature=None korunmalıdır!"


def test_21_finalizer_session_local_constructor_error_does_not_raise():
    """21. [F1-F.6c.1a] SessionLocal constructor hata verdiğinde finalizer exception yükseltmez."""
    with patch("app.tasks.generation_tasks.SessionLocal", side_effect=RuntimeError("DB Pool Exhausted")):
        res = _finalize_social_ideas_bootstrap_failure(attempt_id=999, task_id="task-test-21")
        assert res is None


def test_22_session_local_constructor_error_does_not_mask_original_bootstrap_exception(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """22. [F1-F.6c.1a] SessionLocal constructor hatası asıl bootstrap exception'ını maskelemez."""
    ws, run, brief, cats, kws, attempt = _setup_ready_ideas_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="t22"
    )

    real_session_local = SessionLocal
    call_count = [0]

    def _flaky_session_local():
        call_count[0] += 1
        if call_count[0] == 1:
            # Task içindeki telemetry scope session'ı başarılı açılır
            return real_session_local()
        # Finalizer içindeki SessionLocal çöker
        raise RuntimeError("Finalizer DB Connection Pool Dead")

    with patch("app.tasks.generation_tasks.SessionLocal", side_effect=_flaky_session_local):
        with patch("app.generators.ai_service.get_ai_service", side_effect=ValueError("Original AI Bootstrap Root Error")):
            with pytest.raises(ValueError, match="Original AI Bootstrap Root Error"):
                social_brief_ideas_task.apply(args=[attempt.id], task_id="task-t22").get()


def test_23_rollback_error_in_finalizer_does_not_mask_original_bootstrap_exception(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """23. [F1-F.6c.1a] rollback hata verdiğinde asıl bootstrap exception korunur."""
    ws, run, brief, cats, kws, attempt = _setup_ready_ideas_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="t23"
    )

    mock_db = Mock()
    mock_db.commit.side_effect = RuntimeError("Commit failed")
    mock_db.rollback.side_effect = RuntimeError("Rollback failed due to network partition")

    real_session_local = SessionLocal
    call_count = [0]

    def _session_factory():
        call_count[0] += 1
        if call_count[0] == 1:
            return real_session_local()
        return mock_db

    with patch("app.tasks.generation_tasks.SessionLocal", side_effect=_session_factory):
        with patch("app.generators.ai_service.get_ai_service", side_effect=KeyError("Original AI Key Missing")):
            with pytest.raises(KeyError, match="Original AI Key Missing"):
                social_brief_ideas_task.apply(args=[attempt.id], task_id="task-t23").get()


def test_24_close_error_in_finalizer_does_not_mask_original_bootstrap_exception(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """24. [F1-F.6c.1a] db.close hata verdiğinde asıl bootstrap exception korunur."""
    ws, run, brief, cats, kws, attempt = _setup_ready_ideas_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="t24"
    )

    mock_db = Mock()
    mock_db.close.side_effect = RuntimeError("Close socket failed")

    real_session_local = SessionLocal
    call_count = [0]

    def _session_factory():
        call_count[0] += 1
        if call_count[0] == 1:
            return real_session_local()
        return mock_db

    with patch("app.tasks.generation_tasks.SessionLocal", side_effect=_session_factory):
        with patch("app.generators.ai_service.get_ai_service", side_effect=TypeError("Original AI Type Error")):
            with pytest.raises(TypeError, match="Original AI Type Error"):
                social_brief_ideas_task.apply(args=[attempt.id], task_id="task-t24").get()


def test_25_finalizer_always_returns_none_on_any_internal_error():
    """25. [F1-F.6c.1a] Finalizer kendi hatalarında daima None döner."""
    with patch("app.tasks.generation_tasks.SessionLocal", side_effect=Exception("Crash 1")):
        assert _finalize_social_ideas_bootstrap_failure(attempt_id=1, task_id="t1") is None

    mock_db = Mock()
    mock_db.commit.side_effect = Exception("Crash 2")
    mock_db.rollback.side_effect = Exception("Crash 3")
    mock_db.close.side_effect = Exception("Crash 4")
    with patch("app.tasks.generation_tasks.SessionLocal", return_value=mock_db):
        assert _finalize_social_ideas_bootstrap_failure(attempt_id=1, task_id="t1") is None


def test_26_finalizer_logs_do_not_contain_raw_db_exception_text():
    """26. [F1-F.6c.1a] Finalizer loglarında ham DB exception metni bulunmaz, sabit log üretilir."""
    sensitive_token = "SENSITIVE_PG_CONN_STR_LEAK_TOKEN_999"
    with patch("app.tasks.generation_tasks.SessionLocal", side_effect=RuntimeError(sensitive_token)):
        with patch("app.tasks.generation_tasks.logger.warning") as mock_warn:
            _finalize_social_ideas_bootstrap_failure(attempt_id=42, task_id="task-safe-log")

            mock_warn.assert_called_once_with(
                "Social ideas bootstrap failure finalization could not be persisted."
            )
            for call_arg in mock_warn.call_args_list:
                msg = call_arg[0][0]
                assert sensitive_token not in msg


def test_27_finalizer_close_error_produces_clean_static_log():
    """27. [F1-F.6c.1a] db.close hatasında sabit log üretilir, exception veya secret içermez."""
    mock_db = Mock()
    mock_db.close.side_effect = RuntimeError("RAW_SOCKET_SECRET_CLOSE_LEAK_555")

    with patch("app.tasks.generation_tasks.SessionLocal", return_value=mock_db):
        with patch("app.generators.social.attempt_state.finalize_ideas_attempt_failure", return_value=(Mock(), True)):
            with patch("app.tasks.generation_tasks.logger.warning") as mock_warn:
                _finalize_social_ideas_bootstrap_failure(attempt_id=42, task_id="task-safe-close")

                mock_warn.assert_called_once_with(
                    "Social ideas bootstrap failure session could not be closed cleanly."
                )
                for call_arg in mock_warn.call_args_list:
                    msg = call_arg[0][0]
                    assert "RAW_SOCKET_SECRET_CLOSE_LEAK_555" not in msg


def test_28_collector_created_then_ai_init_fails_calls_collector_flush(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """28. [F1-F.6c.1a] Collector oluşturulduktan sonra AI init hata verirse collector.flush çağrılır."""
    ws, run, brief, cats, kws, attempt = _setup_ready_ideas_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="t28"
    )

    mock_collector = Mock()
    with patch("app.core.telemetry.UsageCollector", return_value=mock_collector):
        with patch("app.generators.ai_service.get_ai_service", side_effect=RuntimeError("AI Init Fail")):
            with pytest.raises(RuntimeError, match="AI Init Fail"):
                social_brief_ideas_task.apply(args=[attempt.id], task_id="task-t28").get()

    mock_collector.flush.assert_called_once()


def test_29_bootstrap_collector_flush_error_does_not_mask_original_ai_init_exception(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """29. [F1-F.6c.1a] Bootstrap collector.flush hata verirse asıl AI init exception korunur."""
    ws, run, brief, cats, kws, attempt = _setup_ready_ideas_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="t29"
    )

    mock_collector = Mock()
    mock_collector.flush.side_effect = RuntimeError("Collector flush redis disconnected")

    with patch("app.core.telemetry.UsageCollector", return_value=mock_collector):
        with patch("app.generators.ai_service.get_ai_service", side_effect=ZeroDivisionError("Root AI init ZeroDivision")):
            with pytest.raises(ZeroDivisionError, match="Root AI init ZeroDivision"):
                social_brief_ideas_task.apply(args=[attempt.id], task_id="task-t29").get()


def test_30_bootstrap_telemetry_flush_error_log_does_not_contain_raw_exception():
    """30. [F1-F.6c.1a] Bootstrap telemetry hata logunda ham exception bulunmaz, sabit log üretilir."""
    mock_collector = Mock()
    secret_telemetry_leak = "TELEMETRY_REDIS_PASSWORD_LEAK_777"
    mock_collector.flush.side_effect = RuntimeError(secret_telemetry_leak)

    with patch("app.tasks.generation_tasks._load_ideas_attempt_telemetry_scope", return_value=(1, 2)):
        with patch("app.core.telemetry.UsageCollector", return_value=mock_collector):
            with patch("app.generators.ai_service.get_ai_service", side_effect=ValueError("AI Error")):
                with patch("app.tasks.generation_tasks.logger.warning") as mock_warn:
                    with pytest.raises(ValueError):
                        social_brief_ideas_task.apply(args=[42], task_id="task-t30").get()

                    warning_messages = [call_arg[0][0] for call_arg in mock_warn.call_args_list]
                    assert "Social ideas bootstrap telemetry could not be flushed." in warning_messages
                    for msg in warning_messages:
                        assert secret_telemetry_leak not in msg


def test_31_ai_client_closed_even_when_collector_flush_fails(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """31. [F1-F.6c.1a] Collector flush hata verse dahi AI client (_close_ai) yine kapatılır."""
    ws, run, brief, cats, kws, attempt = _setup_ready_ideas_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="t31"
    )

    mock_collector = Mock()
    mock_collector.flush.side_effect = RuntimeError("Telemetry flush crash")

    class BrokenBindingAIService:
        def __init__(self):
            self.closed = False

        @property
        def collector(self):
            return None

        @collector.setter
        def collector(self, val):
            raise AttributeError("Collector binding crash")

        def close(self):
            self.closed = True

    broken_ai = BrokenBindingAIService()

    with patch("app.core.telemetry.UsageCollector", return_value=mock_collector):
        with patch("app.generators.ai_service.get_ai_service", return_value=broken_ai):
            with pytest.raises(AttributeError, match="Collector binding crash"):
                social_brief_ideas_task.apply(args=[attempt.id], task_id="task-t31").get()

    assert broken_ai.closed is True, "Collector flush çökse dahi AI client close() çağrılmalıdır!"

