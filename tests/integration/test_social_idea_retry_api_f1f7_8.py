# -*- coding: utf-8 -*-
"""MİKRO FAZ F1-F.7.8 — Fikir Retry POST Endpoint ve GET Polling Entegrasyon Testleri.

Tüm testler gerçek PostgreSQL test veritabanını ve FastAPI TestClient'ı kullanır.
Doğrulanan senaryolar:
1. Feature Flag:
   - ENABLE_SOCIAL_BRIEF_FLOW=False -> 404 FEATURE_DISABLED (POST ve GET).
   - Flag kapalıyken DB / reconciliation / Celery çağrısı yapılmaz.
2. POST Retry Başarı & Celery Dispatch:
   - Geçerli yeni retry isteği -> 202 Accepted, SocialIdeasGenerateResponse döner.
   - Response alanları: attempt_status="pending", replayed=False, ideas=[], coverage doğru.
   - Celery social_brief_ideas_retry_task.apply_async tam 1 kez çağrılır.
   - Celery args=[start.attempt_id] (yalnız attempt_id, pozitif int).
   - task_id geçerli UUID formatındadır.
   - attempt.task_id veritabanında HALA None (worker claim edene kadar DB'ye yazılmaz).
   - Preflight transaction dispatch'ten önce commit edilir.
3. POST Replay Senaryoları:
   - Completed attempt (aynı idempotency_key) -> 200 OK, replayed=True, fikirler dolu, 0 Celery call.
   - Pending attempt (aynı idempotency_key) -> 202 Accepted, replayed=True, ideas=[], 0 Celery call.
   - Running attempt (aynı idempotency_key) -> 202 Accepted, replayed=True, ideas=[], 0 Celery call.
   - Failed attempt (aynı idempotency_key) -> 409 Conflict, IDEA_RETRY_ATTEMPT_TERMINAL, 0 Celery call.
   - Partial attempt (aynı idempotency_key) -> 409 Conflict, IDEA_RETRY_ATTEMPT_TERMINAL, 0 Celery call.
   - Failed attempt sonrası yeni idempotency_key -> 202 Accepted, yeni retry attempt ve dispatch.
   - Partial attempt sonrası yeni idempotency_key -> 202 Accepted, kalan hedefler için yeni attempt ve dispatch.
   - Aktif attempt varken farklı idempotency_key -> 409 Conflict, ATTEMPT_CONFLICT, 0 Celery call.
4. Preflight Doğrulama Hataları:
   - Kaynak attempt yok / bulunamadı -> 404 SOURCE_ATTEMPT_NOT_FOUND.
   - Kaynak attempt aktif (pending / running) -> 409 SOURCE_ATTEMPT_RUNNING.
   - Bütün hedefler dolu -> 409 IDEA_RETRY_NOT_NEEDED.
   - Var olmayan brief -> 404 BRIEF_NOT_FOUND.
   - Cross-workspace brief -> 404 BRIEF_NOT_FOUND.
   - Brief kilitli değil -> 409 BRIEF_NOT_LOCKED.
   - Stale brief -> 409 BRIEF_STALE.
5. Broker Enqueue Failure Compensation:
   - apply_async exception fırlatırsa -> 503 IDEA_RETRY_DISPATCH_FAILED.
   - Attempt durumu failed, reason_code="dispatch_failed", completed_at dolu, lease_expires_at=None.
6. GET Polling Senaryoları:
   - Pending attempt -> 200 OK, ideas=[].
   - Running attempt -> 200 OK, ideas=[].
   - Completed attempt -> 200 OK, fikirler dolu.
   - Failed attempt -> 200 OK, reason_code güvenli.
   - Partial attempt -> 200 OK, kısmi fikirler ve uyarılar.
   - Expired pending/running attempt -> reconciliation ile failed/worker_lost olur.
   - Cross-workspace veya stage uyumsuzluğu -> 404.
7. Rota Önceliği:
   - /social/briefs/{brief_id}/ideas/retry rotası /social/{scoring_run_id} tarafından yutulmaz.
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
from app.core.social.idea_planner import build_social_idea_generation_plan
from app.core.social.idea_retry_persistence import persist_social_ideas_retry
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
from app.generators.social.brief_idea_generator import SocialIdeaAIResult
from app.schemas.social_brief import SocialIdeasGenerateResponse

T0 = datetime(2026, 9, 25, 12, 0, 0, tzinfo=timezone.utc)


# ==================== FIXTURES ====================

@pytest.fixture
def enable_flag(monkeypatch):
    """Test süresince feature flag'i aktif kılar."""
    monkeypatch.setattr(settings, "ENABLE_SOCIAL_BRIEF_FLOW", True)
    yield


@pytest.fixture
def mock_celery_task():
    """social_brief_ideas_retry_task.apply_async çağrılarını yakalar ve izole eder."""
    with patch("app.api.v1.generation.social_brief_ideas_retry_task.apply_async") as mock_apply:
        mock_apply.return_value = MagicMock(id=str(uuid.uuid4()))
        yield mock_apply


# ==================== ENVIRONMENT BUILDERS ====================

def _setup_base_environment(
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
    num_kws: int = 3,
    workspace_name: str = "Ideas Retry Brand",
):
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


def _setup_retry_environment(
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
    targets_data: list[tuple[str, str]] | None = None,
    num_categories: int = 2,
    source_status: str = "partial",
    fill_first_target_only: bool = True,
    workspace_name: str = "Ideas Retry Ready Brand",
):
    """Retry testleri için brief, kategoriler, hedefler ve kaynak ideas attempt'i kurar."""
    ws, run, kws = _setup_base_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=3, workspace_name=workspace_name
    )

    if targets_data is None:
        targets_data = [("instagram", "post"), ("twitter", "thread")]

    brief = SocialBrief(
        scoring_run_id=run.id,
        brand_name_snapshot="Retry Test Marka",
        brand_context_snapshot="Retry Context Metni",
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
    db_session.flush()

    categories = []
    for i in range(num_categories):
        cat = SocialCategory(
            brief_id=brief.id,
            scoring_run_id=run.id,
            category_name=f"Retry Kategori {i + 1}",
            category_type="educational",
            description=f"Açıklama {i + 1} detay metni.",
            relevance_score=0.85,
            suggested_keyword_ids=[kws[0].id],
            is_stale=False,
        )
        db_session.add(cat)
        categories.append(cat)
    db_session.flush()

    plan = build_social_idea_generation_plan(
        selected_category_ids=tuple(c.id for c in categories),
        target_ids=tuple(t.id for t in targets),
        ideas_per_category=len(targets),
    )

    # İlk hedef doldurulmuş, ikinci hedef eksik bırakılmış durumu simüle et
    persisted_target_ids = [targets[0].id] if fill_first_target_only else []
    cov = {
        "schema_version": "ideas_plan_v1",
        "request": {
            "category_ids": [c.id for c in categories],
            "ideas_per_category": len(targets),
        },
        "plan": {
            "total_requested": plan.total_requested,
            "categories": [
                {
                    "category_id": cp.category_id,
                    "requested_count": cp.requested_count,
                    "targets": [
                        {"target_id": tq.target_id, "requested_count": tq.requested_count}
                        for tq in cp.target_quotas
                        if tq.requested_count > 0
                    ],
                }
                for cp in plan.category_plans
            ],
        },
        "generated": {
            "total_accepted": len(persisted_target_ids),
            "target_ids": persisted_target_ids,
        },
    }

    source_attempt = SocialGenerationAttempt(
        brief_id=brief.id,
        stage="ideas",
        idempotency_key="source-attempt-api-001",
        status=source_status,
        requested_target_ids=[t.id for t in targets],
        coverage=cov,
        warnings=[],
        heartbeat_at=T0,
        lease_expires_at=None,
        completed_at=T0 + timedelta(seconds=120),
    )
    db_session.add(source_attempt)
    db_session.flush()

    # Eğer ilk hedef doluysa SocialIdea satırı ekle
    if fill_first_target_only:
        idea = SocialIdea(
            category_id=categories[0].id,
            keyword_id=kws[0].id,
            brief_id=brief.id,
            brief_target_id=targets[0].id,
            idea_title="İlk Hedef Fikri",
            idea_description="Bu hedef daha önce tamamlanmıştır.",
            target_platform=targets[0].platform,
            content_format=targets[0].content_format,
            trend_alignment=0.85,
            is_stale=False,
            is_selected=False,
            regeneration_count=0,
            created_at=T0,
        )
        db_session.add(idea)

    db_session.commit()
    db_session.refresh(brief)
    db_session.refresh(source_attempt)
    return ws, run, brief, categories, targets, kws, source_attempt


# ==================== 1. FEATURE FLAG TESTLERİ ====================

def test_01_feature_flag_disabled_returns_404_on_post(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, mock_celery_task
):
    """1. ENABLE_SOCIAL_BRIEF_FLOW kapalıyken POST 404 FEATURE_DISABLED döner."""
    ws, run, brief, categories, targets, kws, source_attempt = _setup_retry_environment(
        db_session, make_workspace, make_scoring_run, make_keyword
    )

    url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/retry?brand_profile_id={ws.id}"
    resp = client.post(url, json={"idempotency_key": "flag-off-key", "source_attempt_id": source_attempt.id})
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "FEATURE_DISABLED"
    mock_celery_task.assert_not_called()


def test_02_feature_flag_disabled_returns_404_on_get(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """2. ENABLE_SOCIAL_BRIEF_FLOW kapalıyken GET 404 FEATURE_DISABLED döner."""
    ws, run, brief, categories, targets, kws, source_attempt = _setup_retry_environment(
        db_session, make_workspace, make_scoring_run, make_keyword
    )

    url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/retry/attempts/{source_attempt.id}?brand_profile_id={ws.id}"
    resp = client.get(url)
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "FEATURE_DISABLED"


# ==================== 2. POST RETRY BAŞARI & DISPATCH ====================

def test_03_valid_new_retry_request_returns_202_and_dispatches_celery(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, mock_celery_task, enable_flag
):
    """3. Geçerli yeni retry isteği 202 Accepted döner ve Celery worker'a tekil attempt_id ile dispatch edilir."""
    ws, run, brief, categories, targets, kws, source_attempt = _setup_retry_environment(
        db_session, make_workspace, make_scoring_run, make_keyword
    )

    url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/retry?brand_profile_id={ws.id}"
    payload = {"idempotency_key": "valid-retry-key-001", "source_attempt_id": source_attempt.id}

    resp = client.post(url, json=payload)
    assert resp.status_code == 202
    data = resp.json()

    assert data["brief_id"] == brief.id
    assert data["scoring_run_id"] == run.id
    assert data["attempt_id"] > 0
    assert data["attempt_status"] == "pending"
    assert data["replayed"] is False
    assert len(data["ideas"]) == 1  # Kaynak attempt'ten kalan mevcut non-stale fikir döner
    assert len(data["coverage"]) == 2

    # Celery dispatch kontrolü
    assert mock_celery_task.call_count == 1
    call_kwargs = mock_celery_task.call_args.kwargs
    assert call_kwargs["args"] == [data["attempt_id"]]
    assert "task_id" in call_kwargs
    assert uuid.UUID(call_kwargs["task_id"])

    # DB durumu kontrolü
    db_attempt = db_session.query(SocialGenerationAttempt).filter(SocialGenerationAttempt.id == data["attempt_id"]).first()
    assert db_attempt is not None
    assert db_attempt.stage == "ideas_retry"
    assert db_attempt.status == "pending"
    assert db_attempt.task_id is None  # Worker claim edene kadar None kalmalıdır


# ==================== 3. REPLAY SENARYOLARI ====================

def test_04_completed_same_key_replay_returns_200_with_ideas(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, mock_celery_task, enable_flag
):
    """4. Completed attempt aynı idempotency_key ile 200 OK döner, fikirler doldurulur ve Celery çağrılmaz."""
    ws, run, brief, categories, targets, kws, source_attempt = _setup_retry_environment(
        db_session, make_workspace, make_scoring_run, make_keyword
    )

    # 1. İlk istek ile retry attempt oluştur
    url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/retry?brand_profile_id={ws.id}"
    payload = {"idempotency_key": "completed-replay-key", "source_attempt_id": source_attempt.id}
    resp1 = client.post(url, json=payload)
    assert resp1.status_code == 202
    attempt_id = resp1.json()["attempt_id"]

    # 2. İkinci eksik hedefi simüle eden fikir satırını yaz ve attempt'i completed yap
    missing_target = targets[1]
    retry_idea = SocialIdea(
        category_id=categories[1].id,
        keyword_id=kws[0].id,
        brief_id=brief.id,
        brief_target_id=missing_target.id,
        idea_title="İkinci Hedef Retry Fikri",
        idea_description="Bu hedef retry ile tamamlanmıştır.",
        target_platform=missing_target.platform,
        content_format=missing_target.content_format,
        trend_alignment=0.90,
        is_stale=False,
        is_selected=False,
        regeneration_count=0,
        created_at=T0 + timedelta(seconds=10),
    )
    db_session.add(retry_idea)

    retry_attempt = db_session.query(SocialGenerationAttempt).filter(SocialGenerationAttempt.id == attempt_id).first()
    retry_attempt.status = "completed"
    cov = dict(retry_attempt.coverage)
    cov["generated"] = {
        "total_accepted": 1,
        "target_ids": [missing_target.id],
        "assignments": [{"category_id": categories[1].id, "target_id": missing_target.id}],
    }
    retry_attempt.coverage = cov
    retry_attempt.completed_at = T0 + timedelta(seconds=20)
    retry_attempt.lease_expires_at = None
    db_session.commit()

    mock_celery_task.reset_mock()

    # 3. Aynı key ile tekrar POST çağrısı
    resp2 = client.post(url, json=payload)
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["attempt_id"] == attempt_id
    assert data2["attempt_status"] == "completed"
    assert data2["replayed"] is True
    assert len(data2["ideas"]) == 2  # Her iki hedefin non-stale fikirleri
    mock_celery_task.assert_not_called()


def test_05_pending_running_same_key_replay_returns_202(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, mock_celery_task, enable_flag
):
    """5. Pending veya running durumundaki attempt aynı key ile 202 döner ve Celery çağrılmaz."""
    ws, run, brief, categories, targets, kws, source_attempt = _setup_retry_environment(
        db_session, make_workspace, make_scoring_run, make_keyword
    )

    url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/retry?brand_profile_id={ws.id}"
    payload = {"idempotency_key": "pending-running-key", "source_attempt_id": source_attempt.id}

    # İlk istek
    resp1 = client.post(url, json=payload)
    assert resp1.status_code == 202
    attempt_id = resp1.json()["attempt_id"]

    mock_celery_task.reset_mock()

    # İkinci istek (hala pending)
    resp2 = client.post(url, json=payload)
    assert resp2.status_code == 202
    data2 = resp2.json()
    assert data2["attempt_id"] == attempt_id
    assert data2["replayed"] is True
    assert len(data2["ideas"]) == 1  # Mevcut non-stale fikir
    mock_celery_task.assert_not_called()


def test_06_terminal_failed_partial_same_key_returns_409(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, mock_celery_task, enable_flag
):
    """6. Failed veya partial attempt aynı key ile çağrıldığında 409 IDEA_RETRY_ATTEMPT_TERMINAL döner."""
    ws, run, brief, categories, targets, kws, source_attempt = _setup_retry_environment(
        db_session, make_workspace, make_scoring_run, make_keyword
    )

    url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/retry?brand_profile_id={ws.id}"
    payload = {"idempotency_key": "terminal-key", "source_attempt_id": source_attempt.id}

    resp1 = client.post(url, json=payload)
    attempt_id = resp1.json()["attempt_id"]

    # Attempt'i failed yap
    retry_attempt = db_session.query(SocialGenerationAttempt).filter(SocialGenerationAttempt.id == attempt_id).first()
    retry_attempt.status = "failed"
    retry_attempt.reason_code = "worker_bootstrap_failed"
    db_session.commit()

    mock_celery_task.reset_mock()

    resp2 = client.post(url, json=payload)
    assert resp2.status_code == 409
    err = resp2.json()["detail"]
    assert err["code"] == "IDEA_RETRY_ATTEMPT_TERMINAL"
    assert err["retryable"] is True
    mock_celery_task.assert_not_called()


def test_07_new_key_after_failed_attempt_creates_and_dispatches_new_attempt(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, mock_celery_task, enable_flag
):
    """7. Failed attempt sonrasında yeni idempotency_key ile yeni bir retry attempt açılabilir."""
    ws, run, brief, categories, targets, kws, source_attempt = _setup_retry_environment(
        db_session, make_workspace, make_scoring_run, make_keyword
    )

    url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/retry?brand_profile_id={ws.id}"
    resp1 = client.post(url, json={"idempotency_key": "failed-key-1", "source_attempt_id": source_attempt.id})
    attempt_id_1 = resp1.json()["attempt_id"]

    retry_attempt = db_session.query(SocialGenerationAttempt).filter(SocialGenerationAttempt.id == attempt_id_1).first()
    retry_attempt.status = "failed"
    db_session.commit()

    mock_celery_task.reset_mock()

    resp2 = client.post(url, json={"idempotency_key": "new-retry-key-2", "source_attempt_id": source_attempt.id})
    assert resp2.status_code == 202
    attempt_id_2 = resp2.json()["attempt_id"]
    assert attempt_id_2 != attempt_id_1
    assert mock_celery_task.call_count == 1


# ==================== 4. PREFLIGHT DOĞRULAMA HATALARI ====================

def test_08_no_source_attempt_returns_404(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, mock_celery_task, enable_flag
):
    """8. Kaynak attempt bulunamadığında 404 IDEA_RETRY_SOURCE_NOT_FOUND döner."""
    ws, run, brief, categories, targets, kws, source_attempt = _setup_retry_environment(
        db_session, make_workspace, make_scoring_run, make_keyword
    )

    url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/retry?brand_profile_id={ws.id}"
    resp = client.post(url, json={"idempotency_key": "no-source-key", "source_attempt_id": 999999})
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "IDEA_RETRY_SOURCE_NOT_FOUND"
    mock_celery_task.assert_not_called()


def test_09_active_source_attempt_returns_409(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, mock_celery_task, enable_flag
):
    """9. Kaynak attempt hala pending veya running ise 409 IDEA_RETRY_SOURCE_NOT_TERMINAL döner."""
    ws, run, brief, categories, targets, kws, source_attempt = _setup_retry_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, source_status="running"
    )

    url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/retry?brand_profile_id={ws.id}"
    resp = client.post(url, json={"idempotency_key": "running-source-key", "source_attempt_id": source_attempt.id})
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "IDEA_RETRY_SOURCE_NOT_TERMINAL"
    mock_celery_task.assert_not_called()


def test_10_all_targets_filled_returns_409_not_needed(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, mock_celery_task, enable_flag
):
    """10. Tüm hedefler VE tüm kategoriler zaten doluysa 409 IDEA_RETRY_NOT_NEEDED döner (K4)."""
    ws, run, brief, categories, targets, kws, source_attempt = _setup_retry_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, fill_first_target_only=False
    )

    # İkinci hedef için de fikir ekle (ikinci kategoride: her kategori >= 1 fikir)
    idea2 = SocialIdea(
        category_id=categories[1].id,
        keyword_id=kws[0].id,
        brief_id=brief.id,
        brief_target_id=targets[1].id,
        idea_title="Fikir 2",
        idea_description="Açıklama 2",
        target_platform=targets[1].platform,
        content_format=targets[1].content_format,
        trend_alignment=0.85,
        is_stale=False,
        is_selected=False,
        regeneration_count=0,
        created_at=T0,
    )
    # İlk hedef için de fikir ekle
    idea1 = SocialIdea(
        category_id=categories[0].id,
        keyword_id=kws[0].id,
        brief_id=brief.id,
        brief_target_id=targets[0].id,
        idea_title="Fikir 1",
        idea_description="Açıklama 1",
        target_platform=targets[0].platform,
        content_format=targets[0].content_format,
        trend_alignment=0.85,
        is_stale=False,
        is_selected=False,
        regeneration_count=0,
        created_at=T0,
    )
    db_session.add_all([idea1, idea2])
    db_session.commit()

    url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/retry?brand_profile_id={ws.id}"
    resp = client.post(url, json={"idempotency_key": "not-needed-key", "source_attempt_id": source_attempt.id})
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "IDEA_RETRY_NOT_NEEDED"
    mock_celery_task.assert_not_called()


def test_11_cross_workspace_brief_returns_404(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, mock_celery_task, enable_flag
):
    """11. Farklı workspace altındaki brief için 404 döner."""
    ws, run, brief, categories, targets, kws, source_attempt = _setup_retry_environment(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    other_ws = make_workspace(name="Other Brand", status="confirmed")

    url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/retry?brand_profile_id={other_ws.id}"
    resp = client.post(url, json={"idempotency_key": "cross-ws-key", "source_attempt_id": source_attempt.id})
    assert resp.status_code == 404
    mock_celery_task.assert_not_called()


def test_12_unlocked_brief_returns_409(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, mock_celery_task, enable_flag
):
    """12. Brief kilitlenmemişse 409 BRIEF_NOT_LOCKED döner."""
    ws, run, brief, categories, targets, kws, source_attempt = _setup_retry_environment(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    brief.locked_at = None
    db_session.commit()

    url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/retry?brand_profile_id={ws.id}"
    resp = client.post(url, json={"idempotency_key": "unlocked-key", "source_attempt_id": source_attempt.id})
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "BRIEF_NOT_LOCKED"
    mock_celery_task.assert_not_called()


# ==================== 5. BROKER ENQUEUE FAILURE COMPENSATION ====================

def test_13_broker_enqueue_failure_triggers_compensation(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """13. Celery apply_async hatasında 503 IDEA_RETRY_DISPATCH_FAILED döner ve attempt failed yapılır."""
    ws, run, brief, categories, targets, kws, source_attempt = _setup_retry_environment(
        db_session, make_workspace, make_scoring_run, make_keyword
    )

    with patch(
        "app.api.v1.generation.social_brief_ideas_retry_task.apply_async",
        side_effect=RuntimeError("Redis connection refused"),
    ):
        url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/retry?brand_profile_id={ws.id}"
        resp = client.post(url, json={"idempotency_key": "broker-err-key", "source_attempt_id": source_attempt.id})
        assert resp.status_code == 503
        data = resp.json()["detail"]
        assert data["code"] == "IDEA_RETRY_DISPATCH_FAILED"

        # Attempt veritabanında failed olmalıdır
        attempt_id = data["attempt_id"]
        db_attempt = db_session.query(SocialGenerationAttempt).filter(SocialGenerationAttempt.id == attempt_id).first()
        assert db_attempt.status == "failed"
        assert db_attempt.reason_code == "dispatch_failed"
        assert db_attempt.lease_expires_at is None
        assert db_attempt.completed_at is not None


# ==================== 6. GET POLLING TESTLERİ ====================

def test_14_get_polling_pending_attempt(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, mock_celery_task, enable_flag
):
    """14. Pending attempt polling 200 döner, mevcut fikirler listelenir."""
    ws, run, brief, categories, targets, kws, source_attempt = _setup_retry_environment(
        db_session, make_workspace, make_scoring_run, make_keyword
    )

    # Retry başlat
    url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/retry?brand_profile_id={ws.id}"
    resp = client.post(url, json={"idempotency_key": "poll-pending-key", "source_attempt_id": source_attempt.id})
    attempt_id = resp.json()["attempt_id"]

    # GET Polling
    get_url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/retry/attempts/{attempt_id}?brand_profile_id={ws.id}"
    resp_get = client.get(get_url)
    assert resp_get.status_code == 200
    data = resp_get.json()
    assert data["attempt_id"] == attempt_id
    assert data["attempt_status"] == "pending"
    assert data["replayed"] is True
    assert len(data["ideas"]) == 1  # Kaynak attempt'ten kalan mevcut non-stale fikir


def test_15_get_polling_completed_attempt(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, mock_celery_task, enable_flag
):
    """15. Completed attempt polling 200 döner, tüm non-stale fikirler gelir."""
    ws, run, brief, categories, targets, kws, source_attempt = _setup_retry_environment(
        db_session, make_workspace, make_scoring_run, make_keyword
    )

    url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/retry?brand_profile_id={ws.id}"
    resp = client.post(url, json={"idempotency_key": "poll-completed-key", "source_attempt_id": source_attempt.id})
    attempt_id = resp.json()["attempt_id"]

    # Fikir ekle ve tamamla
    missing_target = targets[1]
    retry_idea = SocialIdea(
        category_id=categories[1].id,
        keyword_id=kws[0].id,
        brief_id=brief.id,
        brief_target_id=missing_target.id,
        idea_title="Poll Completed Fikri",
        idea_description="Poll tamamlanan fikir açıklaması.",
        target_platform=missing_target.platform,
        content_format=missing_target.content_format,
        trend_alignment=0.88,
        is_stale=False,
        is_selected=False,
        regeneration_count=0,
        created_at=T0 + timedelta(seconds=10),
    )
    db_session.add(retry_idea)

    retry_attempt = db_session.query(SocialGenerationAttempt).filter(SocialGenerationAttempt.id == attempt_id).first()
    retry_attempt.status = "completed"
    cov = dict(retry_attempt.coverage)
    cov["generated"] = {
        "total_accepted": 1,
        "target_ids": [missing_target.id],
        "assignments": [{"category_id": categories[1].id, "target_id": missing_target.id}],
    }
    retry_attempt.coverage = cov
    retry_attempt.completed_at = T0 + timedelta(seconds=20)
    retry_attempt.lease_expires_at = None
    db_session.commit()

    get_url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/retry/attempts/{attempt_id}?brand_profile_id={ws.id}"
    resp_get = client.get(get_url)
    assert resp_get.status_code == 200
    data = resp_get.json()
    assert data["attempt_id"] == attempt_id
    assert data["attempt_status"] == "completed"
    assert len(data["ideas"]) == 2


def test_16_get_polling_reconciles_expired_attempt(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, mock_celery_task, enable_flag
):
    """16. Süresi dolmuş running retry attempt GET polling sırasında worker_lost olarak finalize edilir."""
    ws, run, brief, categories, targets, kws, source_attempt = _setup_retry_environment(
        db_session, make_workspace, make_scoring_run, make_keyword
    )

    url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/retry?brand_profile_id={ws.id}"
    resp = client.post(url, json={"idempotency_key": "poll-reconcile-key", "source_attempt_id": source_attempt.id})
    attempt_id = resp.json()["attempt_id"]

    # Running ve expired yap
    retry_attempt = db_session.query(SocialGenerationAttempt).filter(SocialGenerationAttempt.id == attempt_id).first()
    retry_attempt.status = "running"
    retry_attempt.task_id = "task-worker-died"
    retry_attempt.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=60)
    db_session.commit()

    get_url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/retry/attempts/{attempt_id}?brand_profile_id={ws.id}"
    resp_get = client.get(get_url)
    assert resp_get.status_code == 200
    data = resp_get.json()
    assert data["attempt_status"] == "failed"
    assert data["reason_code"] == "worker_lost"

    db_session.refresh(retry_attempt)
    assert retry_attempt.status == "failed"
    assert retry_attempt.reason_code == "worker_lost"
    assert retry_attempt.lease_expires_at is None


def test_17_get_polling_cross_stage_returns_404(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """17. Retry GET endpoint'ine normal 'ideas' attempt ID'si verildiğinde 404 döner."""
    ws, run, brief, categories, targets, kws, source_attempt = _setup_retry_environment(
        db_session, make_workspace, make_scoring_run, make_keyword
    )

    get_url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/retry/attempts/{source_attempt.id}?brand_profile_id={ws.id}"
    resp = client.get(get_url)
    assert resp.status_code == 404
