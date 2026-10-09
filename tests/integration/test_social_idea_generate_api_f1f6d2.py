# -*- coding: utf-8 -*-
"""MİKRO FAZ F1-F.6d.2 — Fikir Üretim POST Endpoint ve Güvenli Celery Dispatch Entegrasyon Testleri.

Tüm testler gerçek PostgreSQL test veritabanını ve FastAPI TestClient'ı kullanır.
Doğrulanan senaryolar:
1. Feature Flag:
   - ENABLE_SOCIAL_BRIEF_FLOW=False -> 404 FEATURE_DISABLED, 0 DB write, 0 Celery dispatch.
2. Başarı Senaryoları / Yeni Dispatch:
   - Geçerli yeni istek -> 202 Accepted, SocialIdeasGenerateResponse döner.
   - Response alanları: attempt_status="pending", replayed=False, ideas=[], coverage doğru.
   - Celery social_brief_ideas_task.apply_async tam 1 kez çağrılır.
   - Celery args=[start.attempt_id] (yalnız attempt_id, pozitif int).
   - task_id açık ve geçerli UUID formatındadır.
   - attempt.task_id veritabanında HALA None (worker claim edene kadar DB'ye yazılmaz).
   - Preflight transaction dispatch'ten önce commit edilir; dispatch sırasında açık DB transaction'ı yoktur.
3. Replay Senaryoları:
   - Completed attempt (aynı idempotency_key) -> 200 OK, replayed=True, fikirler dolu, 0 Celery call.
   - Pending attempt (aynı idempotency_key) -> 202 Accepted, replayed=True, ideas=[], 0 Celery call.
   - Running attempt (aynı idempotency_key) -> 202 Accepted, replayed=True, ideas=[], 0 Celery call.
   - Failed attempt (aynı idempotency_key) -> 409 Conflict, IDEA_ATTEMPT_TERMINAL, 0 Celery call.
   - Partial attempt (aynı idempotency_key) -> 409 Conflict, IDEA_ATTEMPT_TERMINAL, 0 Celery call.
   - Failed attempt sonrası yeni idempotency_key -> 202 Accepted, yeni attempt ve dispatch.
   - Aktif attempt varken farklı idempotency_key -> 409 Conflict, ATTEMPT_CONFLICT, 0 Celery call.
4. Preflight Doğrulama Hataları:
   - Nonexistent brief -> 404 BRIEF_NOT_FOUND, 0 dispatch.
   - Cross-workspace brief -> 404 BRIEF_NOT_FOUND, 0 dispatch.
   - Brief not locked -> 409 BRIEF_NOT_LOCKED, 0 dispatch.
   - Stale brief -> 409 BRIEF_STALE, 0 dispatch.
   - Assignment version mismatch -> 409 ASSIGNMENT_CHANGED, 0 dispatch.
   - Categories not ready -> 409 CATEGORIES_NOT_READY, 0 dispatch.
   - Ideas already generated (farklı idempotency_key ile) -> 409 IDEAS_ALREADY_GENERATED, 0 dispatch.
   - Category not eligible -> 400 CATEGORY_NOT_ELIGIBLE, 0 dispatch.
   - Mixed / invalid brief -> 400 MIXED_BRIEF / IDEA_PLAN_INVALID, 0 dispatch.
   - Payload validation failure (Pydantic) -> 422 Unprocessable Entity, 0 dispatch.
5. Broker Enqueue Failure Compensation:
   - apply_async exception fırlatırsa -> 503 IDEA_DISPATCH_FAILED.
   - Attempt durumu failed, reason_code="dispatch_failed", completed_at dolu, lease_expires_at=None.
   - Ham broker exception API yanıtına veya DB'ye sızmaz.
   - Compensation finalize patlarsa -> 500 IDEA_DISPATCH_FINALIZATION_FAILED.
6. Post-dispatch Read Failure İzolasyonu:
   - Enqueue başarılı ancak post-dispatch okuma patlarsa -> 500 döner, attempt dispatch_failed YAPILMAZ.
7. Rota Önceliği ve Güvenlik:
   - /social/briefs/{brief_id}/ideas/generate rotası /social/{scoring_run_id} tarafından yutulmaz.
   - Bilgi sızıntısı guard'ları (raw SQL, traceback, provider exception sızmaz).
   - Endpoint get_ai bağımlılığı almaz ve doğrudan AI çağrısı yapmaz.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import settings
from app.core.social.idea_contract import ValidatedSocialIdea
from app.core.social.idea_flow import begin_social_idea_generation
from app.core.social.idea_persistence import persist_social_ideas
from app.core.social.idea_read import (
    SocialIdeaCoverage,
    SocialIdeaReadError,
    SocialIdeaReadItem,
    SocialIdeaReadResult,
    load_social_idea_result,
)
from app.database.models import (
    BrandProfile,
    ChannelPool,
    ScoringRun,
    SocialBrief,
    SocialBriefKeyword,
    SocialBriefTarget,
    SocialCategory,
    SocialGenerationAttempt,
    SocialIdea,
)
from app.generators.social.attempt_state import (
    claim_attempt,
    finalize_ideas_attempt_failure,
)
from app.generators.social.brief_idea_generator import SocialIdeaAIResult
from app.schemas.social_brief import (
    SocialBriefIdeasGenerateRequest,
    SocialIdeasGenerateResponse,
)

T0 = datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc)


# ==================== FIXTURES ====================

@pytest.fixture
def enable_flag(monkeypatch):
    """Test süresince feature flag'i aktif kılar."""
    monkeypatch.setattr(settings, "ENABLE_SOCIAL_BRIEF_FLOW", True)
    yield


@pytest.fixture
def mock_celery_task():
    """social_brief_ideas_task.apply_async çağrılarını yakalar ve izole eder."""
    with patch("app.api.v1.generation.social_brief_ideas_task.apply_async") as mock_apply:
        mock_apply.return_value = MagicMock(id=str(uuid.uuid4()))
        yield mock_apply


# ==================== ENVIRONMENT BUILDERS ====================

def _setup_base_environment(
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
    num_kws: int = 3,
    workspace_name: str = "Ideas Generate Brand",
):
    """Temel workspace, scoring run ve SOCIAL pool kayıtlarını hazırlar."""
    ws = make_workspace(name=workspace_name, status="confirmed")
    run = make_scoring_run(
        brand_profile_id=ws.id,
        status="completed",
        algorithm_version="v3",
        channel_pool_policy_version=1,
        relevance_anchor_version=1,
        channel_assignment_version=1,
        skip_relevance=True,
    )

    kws = []
    for i in range(num_kws):
        kw = make_keyword(
            text_value=f"{workspace_name} kw {i + 1}",
            brand_profile_id=ws.id,
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

    db_session.commit()
    return ws, run, kws


def _setup_ready_brief_and_categories(
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
    targets_data: list[tuple[str, str]] | None = None,
    num_categories: int = 2,
    workspace_name: str = "Ideas Generate Ready Brand",
):
    """Fikir üretimine hazır, kilitlenmiş brief ve tamamlanmış kategorileri kurar."""
    ws, run, kws = _setup_base_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=3, workspace_name=workspace_name
    )

    if targets_data is None:
        targets_data = [("instagram", "post"), ("twitter", "thread")]

    brief = SocialBrief(
        scoring_run_id=run.id,
        brand_name_snapshot=workspace_name,
        brand_context_snapshot="Test marka bağlamı",
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

    targets = []
    for plat, fmt in targets_data:
        t = SocialBriefTarget(
            brief_id=brief.id,
            platform=plat,
            content_format=fmt,
        )
        db_session.add(t)
        targets.append(t)

    # Categories attempt (completed)
    cat_attempt = SocialGenerationAttempt(
        brief_id=brief.id,
        stage="categories",
        idempotency_key=f"cat-attempt-key-{brief.id}",
        status="completed",
        heartbeat_at=T0,
        lease_expires_at=T0 + timedelta(seconds=1500),
        created_at=T0,
    )
    db_session.add(cat_attempt)
    db_session.flush()

    categories = []
    for i in range(num_categories):
        cat = SocialCategory(
            scoring_run_id=run.id,
            brief_id=brief.id,
            category_name=f"Kategori {i + 1}",
            category_type="educational",
            description=f"Açıklama {i + 1} için eğitici içerik.",
            is_stale=False,
            relevance_score=0.9,
            suggested_keyword_ids=[kws[0].id],
        )
        db_session.add(cat)
        categories.append(cat)

    db_session.commit()
    for cat in categories:
        db_session.refresh(cat)
    for t in targets:
        db_session.refresh(t)

    return ws, run, brief, categories, targets, kws


def _build_valid_category_results(
    plan,
    attempt_id: int,
    brief_targets: list[SocialBriefTarget],
    primary_kw_id: int,
) -> tuple[SocialIdeaAIResult, ...]:
    """Plan kotalarına tam uyan doğrulanmış sahte AI sonuçları üretir."""
    target_map = {t.id: t for t in brief_targets}

    results = []
    for cp in plan.category_plans:
        ideas = []
        idea_num = 1
        for quota in cp.target_quotas:
            tid = quota.target_id
            count = quota.requested_count
            t = target_map[tid]
            for _ in range(count):
                ideas.append(
                    ValidatedSocialIdea(
                        target_id=tid,
                        primary_keyword_id=primary_kw_id,
                        idea_title=f"Cat {cp.category_id} Fikir {idea_num}",
                        idea_description=f"Cat {cp.category_id} için açıklama metni {idea_num}.",
                        target_platform=t.platform,
                        content_format=t.content_format,
                        trend_alignment=0.85,
                    )
                )
                idea_num += 1

        results.append(
            SocialIdeaAIResult(
                attempt_id=attempt_id,
                category_id=cp.category_id,
                ideas=tuple(ideas),
                ai_calls_used=1,
            )
        )

    return tuple(results)


def _persist_dummy_ideas_for_attempt(
    db_session: Session,
    plan,
    attempt_id: int,
    brief_targets: list[SocialBriefTarget],
    primary_kw_id: int,
    task_id: str = "task-test-persist",
):
    """Completed replay testleri için geçerli fikir satırlarını persist eder."""
    ai_results = _build_valid_category_results(plan, attempt_id, brief_targets, primary_kw_id)
    persist_social_ideas(
        db_session,
        attempt_id=attempt_id,
        task_id=task_id,
        category_results=ai_results,
    )
    db_session.commit()


# ==================== 1. FEATURE FLAG TESTLERİ ====================

def test_01_feature_flag_disabled_returns_404_no_db_no_dispatch(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, mock_celery_task
):
    """1. ENABLE_SOCIAL_BRIEF_FLOW kapalıyken HTTP 404 döner, DB ve Celery çağrısı yapılmaz."""
    ws, run, brief, categories, targets, kws = _setup_ready_brief_and_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )

    url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/generate?brand_profile_id={ws.id}"
    payload = {
        "idempotency_key": "flag-disabled-key",
        "category_ids": [c.id for c in categories],
        "ideas_per_category": 2,
    }

    resp = client.post(url, json=payload)
    assert resp.status_code == 404
    body = resp.json()
    assert body["detail"]["code"] == "FEATURE_DISABLED"
    assert "not enabled" in body["detail"]["message"].lower()

    # Celery dispatch yapılmamalı
    assert mock_celery_task.call_count == 0

    # DB'de attempt oluşmamış olmalı
    attempts = db_session.query(SocialGenerationAttempt).filter_by(brief_id=brief.id, stage="ideas").all()
    assert len(attempts) == 0


# ==================== 2. BAŞARI / YENİ ATTEMPT DISPATCH TESTLERİ ====================

def test_02_valid_new_request_enqueues_task_and_returns_202(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag, mock_celery_task
):
    """2. Geçerli yeni istek 202 döner, pending attempt oluşturur ve Celery'ye dispatch eder."""
    ws, run, brief, categories, targets, kws = _setup_ready_brief_and_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )

    url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/generate?brand_profile_id={ws.id}"
    payload = {
        "idempotency_key": "valid-new-key-1",
        "category_ids": [c.id for c in categories],
        "ideas_per_category": 2,
    }

    resp = client.post(url, json=payload)
    assert resp.status_code == 202
    body = resp.json()

    # Response Pydantic şemasına tam uygunluk
    validated = SocialIdeasGenerateResponse.model_validate(body)
    assert validated.brief_id == brief.id
    assert validated.scoring_run_id == run.id
    assert validated.attempt_status == "pending"
    assert validated.total_ideas == 0
    assert validated.ideas == []
    assert validated.replayed is False
    assert validated.reason_code is None
    assert len(validated.coverage) == len(targets)
    for cov in validated.coverage:
        assert cov.accepted == 0
        assert cov.missing == 2
        assert cov.requested == 2

    # Response'ta task_id alanı olmamalı
    assert "task_id" not in body

    # Celery çağrısı doğrulaması
    assert mock_celery_task.call_count == 1
    call_kwargs = mock_celery_task.call_args.kwargs
    assert "args" in call_kwargs
    assert call_kwargs["args"] == [validated.attempt_id]
    assert "task_id" in call_kwargs
    task_id_str = call_kwargs["task_id"]
    # Geçerli UUID formatı
    uuid_obj = uuid.UUID(task_id_str)
    assert str(uuid_obj) == task_id_str

    # DB doğrulaması: attempt pending, task_id henüz yazılmamış (None)
    db_attempt = db_session.query(SocialGenerationAttempt).filter_by(id=validated.attempt_id).one()
    assert db_attempt.status == "pending"
    assert db_attempt.stage == "ideas"
    assert db_attempt.idempotency_key == "valid-new-key-1"
    assert db_attempt.task_id is None


def test_03_preflight_committed_before_dispatch_and_no_open_tx(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag, mock_celery_task
):
    """3. Preflight transaction Celery dispatch öncesinde commit edilmiş olmalıdır."""
    ws, run, brief, categories, targets, kws = _setup_ready_brief_and_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )

    seen_committed = False

    def _side_effect(*args, **kwargs):
        nonlocal seen_committed
        attempt_id = kwargs["args"][0]
        # Ayrı bir session ile attempt'in DB'de commit edilmiş olduğunu doğrula
        from app.database.connection import SessionLocal
        check_db = SessionLocal()
        try:
            att = check_db.query(SocialGenerationAttempt).filter_by(id=attempt_id).first()
            if att is not None and att.status == "pending":
                seen_committed = True
        finally:
            check_db.close()
        return MagicMock(id="task-abc")

    mock_celery_task.side_effect = _side_effect

    url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/generate?brand_profile_id={ws.id}"
    payload = {
        "idempotency_key": "tx-boundary-key",
        "category_ids": [c.id for c in categories],
        "ideas_per_category": 2,
    }

    resp = client.post(url, json=payload)
    assert resp.status_code == 202
    assert seen_committed is True


def test_04_celery_task_receives_only_attempt_id(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag, mock_celery_task
):
    """4. Celery apply_async çağrısı args olarak yalnızca [attempt_id] alır; prompt veya kategori taşımaz."""
    ws, run, brief, categories, targets, kws = _setup_ready_brief_and_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )

    url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/generate?brand_profile_id={ws.id}"
    payload = {
        "idempotency_key": "args-contract-key",
        "category_ids": [c.id for c in categories],
        "ideas_per_category": 2,
    }

    resp = client.post(url, json=payload)
    assert resp.status_code == 202

    assert mock_celery_task.call_count == 1
    call_args, call_kwargs = mock_celery_task.call_args
    assert len(call_args) == 0  # positional args kullanılmamalı, kwargs["args"] kullanılmalı
    assert list(call_kwargs.keys()) == ["args", "task_id"]
    assert len(call_kwargs["args"]) == 1
    assert isinstance(call_kwargs["args"][0], int)
    assert not isinstance(call_kwargs["args"][0], bool)
    assert call_kwargs["args"][0] > 0


# ==================== 3. REPLAY SENARYOLARI ====================

def test_05_completed_replay_returns_200_with_ideas_no_dispatch(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag, mock_celery_task
):
    """5. Tamamlanmış attempt aynı idempotency_key ile çağrıldığında 200 döner, fikirleri içerir ve Celery çağırmaz."""
    ws, run, brief, categories, targets, kws = _setup_ready_brief_and_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )

    # Önce preflight ve persist yaparak completed attempt hazırla
    req = SocialBriefIdeasGenerateRequest(
        idempotency_key="completed-replay-key",
        category_ids=[c.id for c in categories],
        ideas_per_category=2,
    )
    start = begin_social_idea_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req)
    db_session.commit()

    claim_attempt(db_session, attempt_id=start.attempt_id, task_id="task-comp-replay")
    db_session.commit()

    _persist_dummy_ideas_for_attempt(
        db_session,
        plan=start.plan,
        attempt_id=start.attempt_id,
        brief_targets=targets,
        primary_kw_id=kws[0].id,
        task_id="task-comp-replay",
    )

    # API'ye aynı key ile POST
    url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/generate?brand_profile_id={ws.id}"
    resp = client.post(url, json=req.model_dump())
    assert resp.status_code == 200
    body = resp.json()

    validated = SocialIdeasGenerateResponse.model_validate(body)
    assert validated.replayed is True
    assert validated.attempt_status == "completed"
    assert validated.attempt_id == start.attempt_id
    assert validated.total_ideas == start.plan.total_requested
    assert len(validated.ideas) == validated.total_ideas
    for cov in validated.coverage:
        assert cov.accepted == cov.requested
        assert cov.missing == 0

    # Kesinlikle Celery dispatch çağrılmamalı
    assert mock_celery_task.call_count == 0


def test_06_pending_replay_returns_202_replayed_no_dispatch(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag, mock_celery_task
):
    """6. Pending attempt aynı idempotency_key ile çağrıldığında 202 döner, ideas boş olur ve Celery çağırmaz."""
    ws, run, brief, categories, targets, kws = _setup_ready_brief_and_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )

    req = SocialBriefIdeasGenerateRequest(
        idempotency_key="pending-replay-key",
        category_ids=[c.id for c in categories],
        ideas_per_category=2,
    )
    start = begin_social_idea_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req)
    db_session.commit()

    url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/generate?brand_profile_id={ws.id}"
    resp = client.post(url, json=req.model_dump())
    assert resp.status_code == 202
    body = resp.json()

    validated = SocialIdeasGenerateResponse.model_validate(body)
    assert validated.replayed is True
    assert validated.attempt_status == "pending"
    assert validated.attempt_id == start.attempt_id
    assert validated.total_ideas == 0
    assert validated.ideas == []
    assert mock_celery_task.call_count == 0


def test_07_running_replay_returns_202_replayed_no_dispatch(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag, mock_celery_task
):
    """7. Running attempt aynı idempotency_key ile çağrıldığında 202 döner, ideas boş olur ve Celery çağırmaz."""
    ws, run, brief, categories, targets, kws = _setup_ready_brief_and_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )

    req = SocialBriefIdeasGenerateRequest(
        idempotency_key="running-replay-key",
        category_ids=[c.id for c in categories],
        ideas_per_category=2,
    )
    start = begin_social_idea_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req)
    db_session.commit()

    claim_attempt(db_session, attempt_id=start.attempt_id, task_id="task-running-replay")
    db_session.commit()

    url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/generate?brand_profile_id={ws.id}"
    resp = client.post(url, json=req.model_dump())
    assert resp.status_code == 202
    body = resp.json()

    validated = SocialIdeasGenerateResponse.model_validate(body)
    assert validated.replayed is True
    assert validated.attempt_status == "running"
    assert validated.attempt_id == start.attempt_id
    assert validated.total_ideas == 0
    assert validated.ideas == []
    assert mock_celery_task.call_count == 0


def test_08_failed_replay_returns_409_terminal_no_dispatch(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag, mock_celery_task
):
    """8. Failed attempt aynı idempotency_key ile çağrıldığında 409 IDEA_ATTEMPT_TERMINAL döner."""
    ws, run, brief, categories, targets, kws = _setup_ready_brief_and_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )

    req = SocialBriefIdeasGenerateRequest(
        idempotency_key="failed-replay-key",
        category_ids=[c.id for c in categories],
        ideas_per_category=2,
    )
    start = begin_social_idea_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req)
    db_session.commit()

    finalize_ideas_attempt_failure(
        db_session,
        attempt_id=start.attempt_id,
        task_id="task-fail-key",
        reason_code="dispatch_failed",
        error_message="Broker hatası nedeniyle görev başlatılamadı.",
    )
    db_session.commit()

    url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/generate?brand_profile_id={ws.id}"
    resp = client.post(url, json=req.model_dump())
    assert resp.status_code == 409
    body = resp.json()

    detail = body["detail"]
    assert detail["code"] == "IDEA_ATTEMPT_TERMINAL"
    assert detail["attempt_id"] == start.attempt_id
    assert detail["attempt_status"] == "failed"
    assert detail["retryable"] is True
    assert detail["retry_with_new_idempotency_key"] is True
    assert mock_celery_task.call_count == 0


def test_09_partial_replay_returns_409_terminal_no_dispatch(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag, mock_celery_task
):
    """9. Partial attempt aynı idempotency_key ile çağrıldığında 409 IDEA_ATTEMPT_TERMINAL döner."""
    ws, run, brief, categories, targets, kws = _setup_ready_brief_and_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )

    req = SocialBriefIdeasGenerateRequest(
        idempotency_key="partial-replay-key",
        category_ids=[c.id for c in categories],
        ideas_per_category=2,
    )
    start = begin_social_idea_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req)
    db_session.commit()

    # Attempt durumunu manuel partial yap
    att = db_session.query(SocialGenerationAttempt).filter_by(id=start.attempt_id).one()
    att.status = "partial"
    att.reason_code = "partial_failure"
    db_session.commit()

    url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/generate?brand_profile_id={ws.id}"
    resp = client.post(url, json=req.model_dump())
    assert resp.status_code == 409
    body = resp.json()

    detail = body["detail"]
    assert detail["code"] == "IDEA_ATTEMPT_TERMINAL"
    assert detail["attempt_id"] == start.attempt_id
    assert detail["attempt_status"] == "partial"
    assert detail["retryable"] is True
    assert detail["retry_with_new_idempotency_key"] is True
    assert mock_celery_task.call_count == 0


def test_10_retry_with_new_idempotency_key_after_failed_succeeds(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag, mock_celery_task
):
    """10. Failed attempt sonrası yeni bir idempotency_key ile gelen istek 202 döner ve dispatch edilir."""
    ws, run, brief, categories, targets, kws = _setup_ready_brief_and_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )

    # 1. Başarısız attempt
    req1 = SocialBriefIdeasGenerateRequest(
        idempotency_key="failed-key-1",
        category_ids=[c.id for c in categories],
        ideas_per_category=2,
    )
    start1 = begin_social_idea_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req1)
    db_session.commit()

    finalize_ideas_attempt_failure(
        db_session,
        attempt_id=start1.attempt_id,
        task_id="task-fail-1",
        reason_code="dispatch_failed",
        error_message="Broker hatası nedeniyle görev başlatılamadı.",
    )
    db_session.commit()

    # 2. Yeni key ile istek
    url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/generate?brand_profile_id={ws.id}"
    req2 = {
        "idempotency_key": "retry-new-key-2",
        "category_ids": [c.id for c in categories],
        "ideas_per_category": 2,
    }

    resp = client.post(url, json=req2)
    assert resp.status_code == 202
    body = resp.json()

    validated = SocialIdeasGenerateResponse.model_validate(body)
    assert validated.replayed is False
    assert validated.attempt_status == "pending"
    assert validated.attempt_id != start1.attempt_id

    # Celery dispatch çağrılmış olmalı
    assert mock_celery_task.call_count == 1
    assert mock_celery_task.call_args.kwargs["args"] == [validated.attempt_id]


def test_11_active_attempt_conflict_different_key_returns_409(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag, mock_celery_task
):
    """11. Aktif (pending/running) attempt varken farklı bir idempotency_key ile gelen istek 409 ATTEMPT_CONFLICT döner."""
    ws, run, brief, categories, targets, kws = _setup_ready_brief_and_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )

    req1 = SocialBriefIdeasGenerateRequest(
        idempotency_key="active-key-1",
        category_ids=[c.id for c in categories],
        ideas_per_category=2,
    )
    start1 = begin_social_idea_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req1)
    db_session.commit()

    # Farklı key ile ikinci istek
    url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/generate?brand_profile_id={ws.id}"
    req2 = {
        "idempotency_key": "conflicting-key-2",
        "category_ids": [c.id for c in categories],
        "ideas_per_category": 2,
    }

    resp = client.post(url, json=req2)
    assert resp.status_code == 409
    body = resp.json()
    assert body["detail"]["code"] == "ATTEMPT_CONFLICT"
    assert mock_celery_task.call_count == 0


# ==================== 4. PREFLIGHT DOĞRULAMA HATALARI ====================

def test_12_nonexistent_brief_returns_404(
    client: TestClient, db_session: Session, make_workspace, enable_flag, mock_celery_task
):
    """12. Var olmayan brief_id için 404 BRIEF_NOT_FOUND döner, Celery dispatch yapılmaz."""
    ws = make_workspace(name="No Brief Brand")
    url = f"/api/v1/generation/social/briefs/999999/ideas/generate?brand_profile_id={ws.id}"
    payload = {
        "idempotency_key": "not-found-key",
        "category_ids": [1, 2],
        "ideas_per_category": 2,
    }

    resp = client.post(url, json=payload)
    assert resp.status_code == 404
    body = resp.json()
    assert body["detail"]["code"] == "BRIEF_NOT_FOUND"
    assert mock_celery_task.call_count == 0


def test_13_cross_workspace_brief_returns_404(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag, mock_celery_task
):
    """13. Başka workspace'e ait brief için 404 BRIEF_NOT_FOUND döner, Celery dispatch yapılmaz."""
    ws1, run1, brief1, categories1, targets1, kws1 = _setup_ready_brief_and_categories(
        db_session, make_workspace, make_scoring_run, make_keyword, workspace_name="WS1 Brand"
    )
    ws2 = make_workspace(name="WS2 Brand")

    url = f"/api/v1/generation/social/briefs/{brief1.id}/ideas/generate?brand_profile_id={ws2.id}"
    payload = {
        "idempotency_key": "cross-ws-key",
        "category_ids": [c.id for c in categories1],
        "ideas_per_category": 2,
    }

    resp = client.post(url, json=payload)
    assert resp.status_code == 404
    body = resp.json()
    assert body["detail"]["code"] == "BRIEF_NOT_FOUND"
    assert mock_celery_task.call_count == 0


def test_14_brief_not_locked_returns_409(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag, mock_celery_task
):
    """14. Kilitlenmemiş (locked_at=None) brief için 409 BRIEF_NOT_LOCKED döner."""
    ws, run, brief, categories, targets, kws = _setup_ready_brief_and_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    brief.locked_at = None
    db_session.commit()

    url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/generate?brand_profile_id={ws.id}"
    payload = {
        "idempotency_key": "not-locked-key",
        "category_ids": [c.id for c in categories],
        "ideas_per_category": 2,
    }

    resp = client.post(url, json=payload)
    assert resp.status_code == 409
    body = resp.json()
    assert body["detail"]["code"] == "BRIEF_NOT_LOCKED"
    assert mock_celery_task.call_count == 0


def test_15_brief_stale_returns_409(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag, mock_celery_task
):
    """15. Stale (is_stale=True) brief için 409 BRIEF_STALE döner."""
    ws, run, brief, categories, targets, kws = _setup_ready_brief_and_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    brief.is_stale = True
    db_session.commit()

    url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/generate?brand_profile_id={ws.id}"
    payload = {
        "idempotency_key": "stale-brief-key",
        "category_ids": [c.id for c in categories],
        "ideas_per_category": 2,
    }

    resp = client.post(url, json=payload)
    assert resp.status_code == 409
    body = resp.json()
    assert body["detail"]["code"] == "BRIEF_STALE"
    assert mock_celery_task.call_count == 0


def test_16_assignment_version_mismatch_returns_409(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag, mock_celery_task
):
    """16. Scoring run ile brief arasındaki channel_assignment_version uyuşmazlığında 409 ASSIGNMENT_CHANGED döner."""
    ws, run, brief, categories, targets, kws = _setup_ready_brief_and_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    run.channel_assignment_version = 2
    db_session.commit()

    url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/generate?brand_profile_id={ws.id}"
    payload = {
        "idempotency_key": "mismatch-version-key",
        "category_ids": [c.id for c in categories],
        "ideas_per_category": 2,
    }

    resp = client.post(url, json=payload)
    assert resp.status_code == 409
    body = resp.json()
    assert body["detail"]["code"] == "ASSIGNMENT_CHANGED"
    assert mock_celery_task.call_count == 0


def test_17_categories_not_ready_returns_409(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag, mock_celery_task
):
    """17. Kategori aşaması completed değilse 409 CATEGORIES_NOT_READY döner."""
    ws, run, brief, categories, targets, kws = _setup_ready_brief_and_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    # Kategori attempt'ini pending yap
    cat_att = db_session.query(SocialGenerationAttempt).filter_by(brief_id=brief.id, stage="categories").one()
    cat_att.status = "pending"
    db_session.commit()

    url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/generate?brand_profile_id={ws.id}"
    payload = {
        "idempotency_key": "cat-not-ready-key",
        "category_ids": [c.id for c in categories],
        "ideas_per_category": 2,
    }

    resp = client.post(url, json=payload)
    assert resp.status_code == 409
    body = resp.json()
    assert body["detail"]["code"] == "CATEGORIES_NOT_READY"
    assert mock_celery_task.call_count == 0


def test_18_ideas_already_generated_returns_409(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag, mock_celery_task
):
    """18. Fikirler zaten üretilmişse (farklı idempotency_key ile) 409 IDEAS_ALREADY_GENERATED döner."""
    ws, run, brief, categories, targets, kws = _setup_ready_brief_and_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )

    # Mevcut completed bir attempt oluştur
    req1 = SocialBriefIdeasGenerateRequest(
        idempotency_key="original-ideas-key",
        category_ids=[c.id for c in categories],
        ideas_per_category=2,
    )
    start1 = begin_social_idea_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req1)
    db_session.commit()

    claim_attempt(db_session, attempt_id=start1.attempt_id, task_id="task-already-gen")
    db_session.commit()

    _persist_dummy_ideas_for_attempt(
        db_session,
        plan=start1.plan,
        attempt_id=start1.attempt_id,
        brief_targets=targets,
        primary_kw_id=kws[0].id,
        task_id="task-already-gen",
    )

    # Farklı bir key ile tekrar deneme
    url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/generate?brand_profile_id={ws.id}"
    payload = {
        "idempotency_key": "different-ideas-key",
        "category_ids": [c.id for c in categories],
        "ideas_per_category": 2,
    }

    resp = client.post(url, json=payload)
    assert resp.status_code == 409
    body = resp.json()
    assert body["detail"]["code"] == "IDEAS_ALREADY_GENERATED"
    assert mock_celery_task.call_count == 0


def test_19_category_not_eligible_returns_400(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag, mock_celery_task
):
    """19. Stale veya brief'e ait olmayan kategori seçildiğinde 400 CATEGORY_NOT_ELIGIBLE döner."""
    ws, run, brief, categories, targets, kws = _setup_ready_brief_and_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    categories[0].is_stale = True
    db_session.commit()

    url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/generate?brand_profile_id={ws.id}"
    payload = {
        "idempotency_key": "ineligible-cat-key",
        "category_ids": [c.id for c in categories],
        "ideas_per_category": 2,
    }

    resp = client.post(url, json=payload)
    assert resp.status_code == 400
    body = resp.json()
    assert body["detail"]["code"] == "CATEGORY_NOT_ELIGIBLE"
    assert mock_celery_task.call_count == 0


def test_20_mixed_brief_category_from_other_brief_returns_400(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag, mock_celery_task
):
    """20. Başka bir brief'e ait kategori seçildiğinde 400 MIXED_BRIEF döner."""
    ws, run, brief1, categories1, targets1, kws1 = _setup_ready_brief_and_categories(
        db_session, make_workspace, make_scoring_run, make_keyword, workspace_name="Brief1 WS"
    )
    ws2, run2, brief2, categories2, targets2, kws2 = _setup_ready_brief_and_categories(
        db_session, make_workspace, make_scoring_run, make_keyword, workspace_name="Brief2 WS"
    )

    url = f"/api/v1/generation/social/briefs/{brief1.id}/ideas/generate?brand_profile_id={ws.id}"
    payload = {
        "idempotency_key": "mixed-brief-key",
        "category_ids": [categories1[0].id, categories2[0].id],
        "ideas_per_category": 2,
    }

    resp = client.post(url, json=payload)
    assert resp.status_code == 400
    body = resp.json()
    assert body["detail"]["code"] == "MIXED_BRIEF"
    assert mock_celery_task.call_count == 0


def test_20b_empty_brief_targets_returns_409(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag, mock_celery_task
):
    """20b. Brief hedefi (target) kalmadığında 409 IDEA_TARGETS_INVALID döner."""
    ws, run, brief, categories, targets, kws = _setup_ready_brief_and_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    for t in targets:
        db_session.delete(t)
    db_session.commit()

    url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/generate?brand_profile_id={ws.id}"
    payload = {
        "idempotency_key": "empty-targets-key",
        "category_ids": [c.id for c in categories],
        "ideas_per_category": 2,
    }

    resp = client.post(url, json=payload)
    assert resp.status_code == 409
    body = resp.json()
    assert body["detail"]["code"] == "IDEA_TARGETS_INVALID"
    assert mock_celery_task.call_count == 0


def test_21_invalid_payload_pydantic_validation_422(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag, mock_celery_task
):
    """21. Şema doğrulaması başarısız payload (boş category_ids, negatif fikir sayısı) 422 döner."""
    ws, run, brief, categories, targets, kws = _setup_ready_brief_and_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )

    url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/generate?brand_profile_id={ws.id}"

    # a. Boş category_ids
    resp_empty = client.post(
        url,
        json={"idempotency_key": "bad-payload-1", "category_ids": [], "ideas_per_category": 2},
    )
    assert resp_empty.status_code == 422

    # b. ideas_per_category <= 0
    resp_zero = client.post(
        url,
        json={
            "idempotency_key": "bad-payload-2",
            "category_ids": [categories[0].id],
            "ideas_per_category": 0,
        },
    )
    assert resp_zero.status_code == 422

    # c. Missing idempotency_key
    resp_missing_key = client.post(
        url,
        json={"category_ids": [categories[0].id], "ideas_per_category": 2},
    )
    assert resp_missing_key.status_code == 422

    assert mock_celery_task.call_count == 0


# ==================== 5. BROKER ENQUEUE FAILURE COMPENSATION ====================

def test_22_broker_enqueue_failure_triggers_compensation_and_returns_503(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag, mock_celery_task
):
    """22. apply_async hata verdiğinde compensation çalışır: attempt failed (dispatch_failed) olur ve 503 döner."""
    ws, run, brief, categories, targets, kws = _setup_ready_brief_and_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )

    # Celery broker hatası simülasyonu
    mock_celery_task.side_effect = RuntimeError("Broker connection timeout to redis:6379")

    url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/generate?brand_profile_id={ws.id}"
    payload = {
        "idempotency_key": "broker-fail-key",
        "category_ids": [c.id for c in categories],
        "ideas_per_category": 2,
    }

    resp = client.post(url, json=payload)
    assert resp.status_code == 503
    body = resp.json()

    detail = body["detail"]
    assert detail["code"] == "IDEA_DISPATCH_FAILED"
    assert "kuyruğa alınamadı" in detail["message"]
    assert "attempt_id" in detail
    failed_attempt_id = detail["attempt_id"]

    # Ham exception yanıt gövdesinde sızmamalı
    assert "redis:6379" not in resp.text
    assert "RuntimeError" not in resp.text

    # DB durumu doğrulaması
    att = db_session.query(SocialGenerationAttempt).filter_by(id=failed_attempt_id).one()
    assert att.status == "failed"
    assert att.reason_code == "dispatch_failed"
    assert att.completed_at is not None
    assert att.lease_expires_at is None


def test_23_broker_enqueue_compensation_failure_returns_500(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag, mock_celery_task
):
    """23. apply_async hata verip compensation finalizasyonu da patlarsa 500 döner."""
    ws, run, brief, categories, targets, kws = _setup_ready_brief_and_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )

    mock_celery_task.side_effect = RuntimeError("Broker connection lost")

    url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/generate?brand_profile_id={ws.id}"
    payload = {
        "idempotency_key": "comp-fail-key",
        "category_ids": [c.id for c in categories],
        "ideas_per_category": 2,
    }

    with patch(
        "app.api.v1.generation.finalize_ideas_attempt_failure",
        side_effect=Exception("Database lock error"),
    ):
        resp = client.post(url, json=payload)
        assert resp.status_code == 500
        body = resp.json()
        assert body["detail"]["code"] == "IDEA_DISPATCH_FINALIZATION_FAILED"
        assert "Database lock error" not in resp.text


# ==================== 6. POST-DISPATCH READ FAILURE İZOLASYONU ====================

def test_24_post_dispatch_read_failure_does_not_mark_attempt_failed(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag, mock_celery_task
):
    """24. Celery dispatch başarılı olduktan sonra okuma hatası oluşursa attempt dispatch_failed yapılmaz."""
    ws, run, brief, categories, targets, kws = _setup_ready_brief_and_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )

    url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/generate?brand_profile_id={ws.id}"
    payload = {
        "idempotency_key": "read-fail-key",
        "category_ids": [c.id for c in categories],
        "ideas_per_category": 2,
    }

    with patch(
        "app.api.v1.generation.load_social_idea_result",
        side_effect=SocialIdeaReadError("Database read failed", error_code="DATABASE_READ_FAILED"),
    ):
        resp = client.post(url, json=payload)
        assert resp.status_code == 500

    # Celery görevi yine de kuyruğa alınmış olmalı
    assert mock_celery_task.call_count == 1
    attempt_id = mock_celery_task.call_args.kwargs["args"][0]

    # Attempt durumu kesinlikle failed veya dispatch_failed YAPILMAMIŞTIR (worker çalışabilir)
    att = db_session.query(SocialGenerationAttempt).filter_by(id=attempt_id).one()
    assert att.status == "pending"
    assert att.reason_code is None


# ==================== 7. ROTA ÖNCELİĞİ VE GÜVENLİK ====================

def test_25_static_route_precedence_not_shadowed_by_dynamic_scoring_run(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag, mock_celery_task
):
    """25. /social/briefs/{brief_id}/ideas/generate rotası /social/{scoring_run_id} tarafından yutulmaz."""
    ws, run, brief, categories, targets, kws = _setup_ready_brief_and_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )

    url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/generate?brand_profile_id={ws.id}"
    payload = {
        "idempotency_key": "route-precedence-key",
        "category_ids": [c.id for c in categories],
        "ideas_per_category": 2,
    }

    resp = client.post(url, json=payload)
    # Doğru rotaya girdiği için 202 dönmelidir (yanlış rotaya girseydi method not allowed ya da 404/422 dönerdi)
    assert resp.status_code == 202
    assert resp.json()["attempt_status"] == "pending"


def test_26_no_information_leakage_on_errors(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag, mock_celery_task
):
    """26. Hata yanıtlarında raw SQL, SQLAlchemy metinleri veya stack trace sızmaz."""
    ws, run, brief, categories, targets, kws = _setup_ready_brief_and_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )

    url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/generate?brand_profile_id={ws.id}"
    # Geçersiz tip gönderimi
    resp = client.post(url, json={"idempotency_key": 12345, "category_ids": "not-a-list"})
    assert resp.status_code == 422
    assert "SELECT " not in resp.text
    assert "FROM " not in resp.text
    assert "Traceback" not in resp.text


def test_27_endpoint_has_no_direct_ai_call_or_dependency():
    """27. Endpoint tanımında get_ai dependency'si bulunmamalıdır."""
    import inspect
    from app.api.v1.generation import generate_social_ideas_for_brief

    sig = inspect.signature(generate_social_ideas_for_brief)
    param_names = list(sig.parameters.keys())
    assert "ai" not in param_names
    assert "ai_service" not in param_names
    assert "get_ai" not in str(sig)


def test_28_attempt_task_id_not_set_by_endpoint(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag, mock_celery_task
):
    """28. Endpoint attempt.task_id alanına yazmaz; bu alan worker claim aşamasında doldurulur."""
    ws, run, brief, categories, targets, kws = _setup_ready_brief_and_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )

    url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/generate?brand_profile_id={ws.id}"
    payload = {
        "idempotency_key": "task-id-unassigned-key",
        "category_ids": [c.id for c in categories],
        "ideas_per_category": 2,
    }

    resp = client.post(url, json=payload)
    assert resp.status_code == 202
    attempt_id = resp.json()["attempt_id"]

    att = db_session.query(SocialGenerationAttempt).filter_by(id=attempt_id).one()
    assert att.task_id is None


def test_29_response_serializes_cleanly_as_json(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag, mock_celery_task
):
    """29. Dönen yanıt json.dumps ile sorunsuz serileştirilir."""
    ws, run, brief, categories, targets, kws = _setup_ready_brief_and_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )

    url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/generate?brand_profile_id={ws.id}"
    payload = {
        "idempotency_key": "json-dumps-key",
        "category_ids": [c.id for c in categories],
        "ideas_per_category": 2,
    }

    resp = client.post(url, json=payload)
    assert resp.status_code == 202
    dumped = json.dumps(resp.json())
    assert isinstance(dumped, str)
    assert len(dumped) > 0
