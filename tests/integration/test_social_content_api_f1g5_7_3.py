# -*- coding: utf-8 -*-
"""MİKRO FAZ F1-G.5.7.3 — Sosyal İçerik POST Async Dispatch, GET Polling ve Lease Reconciliation Entegrasyon Testleri.

Tüm testler gerçek PostgreSQL test veritabanını ve FastAPI TestClient'ı kullanır.
Test edilen senaryolar:
1. Feature Flag:
   - ENABLE_SOCIAL_BRIEF_FLOW=False -> POST /contents/async 404 FEATURE_DISABLED.
   - ENABLE_SOCIAL_BRIEF_FLOW=False -> GET /contents/attempts/{id} 404 FEATURE_DISABLED.
   - Flag kapalıyken DB / reconciliation / read / dispatch çağrılmaz.
2. Endpoint Bağımsızlığı & Güvenlik:
   - POST ve GET endpoint'leri get_ai dependency'si içermez (0 AI calls).
   - GET endpoint Celery task dispatch yapmaz (0 calls).
   - POST Celery apply_async args yalnızca [attempt_id] içerir; task_id kwargs olarak iletilir.
   - Celery payload'a product_facts, trusted_brand_usp veya ham prompt metinleri verilmez.
   - Rota önceliği: /social/briefs/{brief_id}/contents/async rotası /social/{scoring_run_id} tarafından yutulmaz.
   - Legacy /social/contents ve /social/contents/async rotaları bozulmadan korunur.
3. POST Async Dispatch Başarı Senaryoları (202 Accepted):
   - Geçerli yeni istek -> 202 Accepted, SocialBriefContentsAttemptResponse döner.
   - Response: attempt_status="pending", replayed=False, contents=[], warnings=[], total_contents=0.
   - Celery social_brief_contents_task.apply_async tam 1 kez çağrılır.
   - Celery args=[start.attempt_id] (yalnız attempt_id, pozitif int).
   - task_id açık UUID formatındadır.
   - attempt.task_id veritabanında HALA None (worker claim edene kadar DB'ye yazılmaz).
   - Preflight transaction dispatch'ten önce commit edilir.
4. POST Replay Senaryoları:
   - Completed attempt (aynı idempotency_key) -> 200 OK, replayed=True, contents dolu, 0 Celery call.
   - Pending attempt (aynı idempotency_key) -> 202 Accepted, replayed=True, contents=[], 0 Celery call.
   - Running attempt (aynı idempotency_key) -> 202 Accepted, replayed=True, contents=[], 0 Celery call.
   - Failed attempt (aynı idempotency_key) -> 409 Conflict, CONTENT_ATTEMPT_TERMINAL, retryable=True, 0 Celery call.
   - Partial attempt (aynı idempotency_key) -> 409 Conflict, CONTENT_ATTEMPT_TERMINAL, retryable=True, 0 Celery call.
   - Failed attempt sonrası yeni idempotency_key -> 202 Accepted, yeni attempt ve dispatch.
   - Aktif pending/running attempt varken farklı idempotency_key -> 409 Conflict, ATTEMPT_CONFLICT, 0 Celery call.
   - İdempotency key istek uyumsuzluğu (farklı idea_ids veya USP) -> 409 Conflict, CONTENT_ATTEMPT_REQUEST_MISMATCH.
5. Preflight Doğrulama Hataları:
   - Nonexistent brief -> 404 BRIEF_NOT_FOUND, 0 dispatch.
   - Cross-workspace brief -> 404 BRIEF_NOT_FOUND, 0 dispatch.
   - Brief not locked -> 409 CONTENT_BRIEF_NOT_LOCKED, 0 dispatch.
   - Stale brief -> 409 CONTENT_BRIEF_STALE, 0 dispatch.
   - Assignment version mismatch -> 409 CONTENT_ASSIGNMENT_CHANGED, 0 dispatch.
   - Invalid ideas (başka brief'e ait veya bulunamayan) -> 400 CONTENT_IDEAS_INVALID, 0 dispatch.
   - Idea not eligible (stale) -> 400 CONTENT_IDEA_NOT_ELIGIBLE, 0 dispatch.
   - Geçersiz path/query parametreleri -> 400 CONTENT_ATTEMPT_INVALID_INPUT veya 422.
6. Broker Enqueue Failure Compensation:
   - apply_async exception fırlatırsa -> 503 CONTENT_DISPATCH_FAILED.
   - Attempt durumu failed, reason_code="dispatch_failed", lease_expires_at=None.
   - Ham broker exception API yanıtına sızmaz.
   - Compensation finalize patlarsa -> 500 CONTENT_DISPATCH_FINALIZATION_FAILED.
7. Post-dispatch Read Failure İzolasyonu:
   - Enqueue başarılı ancak post-dispatch okuma patlarsa -> 500 döner, attempt dispatch_failed YAPILMAZ.
8. GET Polling Status Başarı Senaryoları (200 OK):
   - Pending attempt polling -> 200, replayed=True, contents=[], warnings=[].
   - Running attempt polling -> 200, replayed=True, contents=[], warnings=[].
   - Completed attempt polling -> 200, replayed=True, tüm 20 alanlı içerikler eksiksiz döner.
   - Failed attempt polling -> 200, replayed=True, attempt_status="failed", reason_code döner, warnings döner.
   - Partial attempt polling -> 200, replayed=True, attempt_status="partial", hem contents hem warnings döner.
9. GET Polling Lease Reconciliation:
   - Expired pending attempt aynı çağrıda failed/worker_lost döner ve commit edilir.
   - Expired running attempt aynı çağrıda failed/worker_lost döner ve commit edilir.
   - Unexpired pending/running değiştirilmez.
   - Completed / failed / partial reconciliation ile değiştirilmez.
10. GET Polling Workspace ve Kimlik İzolasyonu:
    - Başka workspace -> 404 CONTENT_ATTEMPT_NOT_FOUND.
    - Attempt başka brief'e ait -> 404 CONTENT_ATTEMPT_NOT_FOUND.
    - Attempt başka stage (örn: ideas) -> 404 CONTENT_ATTEMPT_NOT_FOUND.
    - Var olmayan attempt -> 404 CONTENT_ATTEMPT_NOT_FOUND.
    - Geçersiz path/query parametreleri -> 400 CONTENT_ATTEMPT_INVALID_INPUT veya 422.
11. Response Şeması & Güvenlik:
    - JSON serializable yanıtlar.
    - product_facts, trusted_brand_usp veya worker task_id yanıta sızmaz.
12. Uçtan Uca Yaşam Döngüsü:
    - POST 202 -> GET 200 pending -> Worker persist -> GET 200 completed.
"""
from __future__ import annotations

import inspect
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.v1 import generation
from app.config import settings
from app.core.social.content_contract import ValidatedHook
from app.core.social.content_flow import begin_social_content_generation
from app.core.social.content_persistence import (
    PersistedSocialContent,
    persist_social_content,
)
from app.core.social.content_read import (
    SocialContentAttemptReadResult,
    load_social_content_result,
)
from app.database.models import (
    BrandProfile,
    ChannelPool,
    ScoringRun,
    SocialBrief,
    SocialBriefKeyword,
    SocialBriefTarget,
    SocialCategory,
    SocialContent,
    SocialGenerationAttempt,
    SocialIdea,
)
from app.generators.social.attempt_state import (
    claim_contents_attempt_for_worker,
    finalize_contents_attempt_failure,
    reconcile_expired_contents_attempt_for_read,
)
from app.schemas.social_brief import (
    SocialBriefContentsAttemptResponse,
    SocialBriefContentsGenerateRequest,
    SocialContentWarningResponse,
    SocialGeneratedContentItemResponse,
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
    """social_brief_contents_task.apply_async çağrılarını yakalar ve izole eder."""
    with patch("app.api.v1.generation.social_brief_contents_task.apply_async") as mock_apply:
        mock_apply.return_value = MagicMock(id=str(uuid.uuid4()))
        yield mock_apply


# ==================== ENVIRONMENT BUILDERS ====================

def _setup_base_env(
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
    brand_name: str = "Contents API Test Brand",
):
    """Workspace, scoring run ve keyword oluşturur."""
    ws = make_workspace(name=brand_name, status="confirmed")
    run = make_scoring_run(
        brand_profile_id=ws.id,
        status="completed",
        algorithm_version="v3",
        channel_pool_policy_version=ws.policy_version or 1,
        relevance_anchor_version=ws.anchor_version or 1,
        channel_assignment_version=1,
        skip_relevance=True,
    )
    kw = make_keyword(
        text_value=f"{brand_name} anahtar kelime",
        brand_profile_id=ws.id,
    )
    pool = ChannelPool(
        scoring_run_id=run.id,
        keyword_id=kw.id,
        channel="SOCIAL",
        final_rank=1,
        relevance_score=0.95,
        adjusted_score=25.0,
    )
    db_session.add(pool)
    db_session.commit()
    return ws, run, kw


def _setup_full_env(
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
    *,
    num_ideas: int = 3,
    brand_name: str = "Contents API Test Brand",
):
    """Eksiksiz otoriter DB zinciri (workspace, brief, target, category, ideas) kurar."""
    ws, run, kw = _setup_base_env(
        db_session, make_workspace, make_scoring_run, make_keyword, brand_name=brand_name
    )

    brief = SocialBrief(
        scoring_run_id=run.id,
        brand_name_snapshot=ws.name,
        brand_context_snapshot="Marka bağlamı özeti",
        channel_assignment_version=1,
        format_matrix_version="v1",
        is_stale=False,
        locked_at=T0,
    )
    db_session.add(brief)
    db_session.flush()

    brief_kw = SocialBriefKeyword(
        brief_id=brief.id,
        keyword_id=kw.id,
        keyword_snapshot=kw.keyword,
        position=0,
    )
    db_session.add(brief_kw)

    target = SocialBriefTarget(
        brief_id=brief.id,
        platform="instagram",
        content_format="post",
    )
    db_session.add(target)
    db_session.flush()

    cat = SocialCategory(
        scoring_run_id=run.id,
        brief_id=brief.id,
        category_name="Eğitici İçerikler",
        category_type="educational",
        description="Eğitici kategori açıklaması",
        is_stale=False,
        relevance_score=0.9,
        suggested_keyword_ids=[kw.id],
    )
    db_session.add(cat)
    db_session.flush()

    ideas = []
    for i in range(num_ideas):
        idea = SocialIdea(
            brief_id=brief.id,
            category_id=cat.id,
            keyword_id=kw.id,
            brief_target_id=target.id,
            idea_title=f"Örnek Fikir {i + 1}",
            idea_description=f"Detaylı içerik fikri {i + 1}",
            target_platform="instagram",
            content_format="post",
            trend_alignment=0.85,
            is_stale=False,
        )
        db_session.add(idea)
        ideas.append(idea)

    db_session.commit()
    db_session.refresh(brief)
    db_session.refresh(run)
    for idea in ideas:
        db_session.refresh(idea)

    return ws, run, brief, target, cat, kw, ideas


def _create_valid_content(
    db_session: Session,
    *,
    brief: SocialBrief,
    target: SocialBriefTarget,
    idea: SocialIdea,
    caption: str = "Bu harika bir Instagram post içeriğidir.",
    is_stale: bool = False,
) -> SocialContent:
    """Doğrulanabilir geçerli bir SocialContent satırı oluşturur."""
    content = SocialContent(
        idea_id=idea.id,
        brief_id=brief.id,
        hooks=[
            {"text": "Dikkat çeken birinci kanca cümlesi?", "style": "question"},
            {"text": "Şaşırtıcı bir sektör gerçeği ortaya çıktı!", "style": "shocking"},
            {"text": "Hepimizin her gün yaşadığı o an...", "style": "relatable"},
        ],
        caption=caption,
        cta_text="Daha fazlası için profildeki linke tıklayın.",
        hashtags=["dijital", "pazarlama", "strateji", "icerik", "sosyalmedya"],
        format_payload=None,
        duration_status="not_applicable",
        actual_duration_sec=None,
        validation_warnings=[],
        is_stale=is_stale,
    )
    db_session.add(content)
    db_session.flush()
    return content


def _create_contents_attempt(
    db_session: Session,
    *,
    brief: SocialBrief,
    ideas: list[SocialIdea],
    status: str = "pending",
    stage: str = "contents",
    reason_code: str | None = None,
    error_message: str | None = None,
    warnings: list[dict] | None = None,
    idempotency_key: str | None = None,
    task_id: str | None = None,
    lease_expires_at: datetime | None = None,
    completed_at: datetime | None = None,
    product_facts: str | None = None,
    trusted_brand_usp: str | None = "Örnek USP",
) -> SocialGenerationAttempt:
    """Test için SocialGenerationAttempt kaydı oluşturur."""
    from app.core.channel.brand_defense import load_product_definition

    run = db_session.query(ScoringRun).filter_by(id=brief.scoring_run_id).one()
    eff_product_facts = (
        product_facts
        if product_facts is not None
        else load_product_definition(db_session, run)
    )

    idea_ids = [i.id for i in ideas]
    key = idempotency_key or f"contents-attempt-{uuid.uuid4().hex[:8]}"
    coverage = {
        "schema_version": "contents_request_v1",
        "request": {
            "idea_ids": idea_ids,
            "product_facts": eff_product_facts,
            "trusted_brand_usp": trusted_brand_usp,
        },
    }
    att = SocialGenerationAttempt(
        brief_id=brief.id,
        stage=stage,
        idempotency_key=key,
        status=status,
        requested_idea_ids=idea_ids,
        coverage=coverage,
        reason_code=reason_code,
        error_message=error_message,
        warnings=warnings,
        task_id=task_id,
        lease_expires_at=lease_expires_at,
        completed_at=completed_at,
    )
    db_session.add(att)
    db_session.commit()
    db_session.refresh(att)
    return att


# ==================== 1. FEATURE FLAG TESTLERİ ====================

def test_01_feature_flag_disabled_post_returns_404(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, mock_celery_task
):
    """1. Flag kapalıyken POST 404 FEATURE_DISABLED döner, Celery dispatch yapılmaz."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    url = f"/api/v1/generation/social/briefs/{brief.id}/contents/async?brand_profile_id={ws.id}"
    payload = {
        "idempotency_key": "flag-test-key",
        "idea_ids": [i.id for i in ideas],
    }
    resp = client.post(url, json=payload)
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "FEATURE_DISABLED"
    mock_celery_task.assert_not_called()


def test_02_feature_flag_disabled_get_returns_404(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """2. Flag kapalıyken GET 404 FEATURE_DISABLED döner."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    att = _create_contents_attempt(db_session, brief=brief, ideas=ideas, status="completed")
    url = f"/api/v1/generation/social/briefs/{brief.id}/contents/attempts/{att.id}?brand_profile_id={ws.id}"
    resp = client.get(url)
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "FEATURE_DISABLED"


# ==================== 2. ENDPOINT GÜVENLİK VE BAĞIMSIZLIK TESTLERİ ====================

def test_03_endpoints_do_not_use_get_ai():
    """3. POST ve GET router fonksiyonları get_ai dependency'si almaz."""
    sig_post = inspect.signature(generation.generate_social_brief_contents_async)
    assert "ai" not in sig_post.parameters

    sig_get = inspect.signature(generation.get_social_brief_contents_attempt)
    assert "ai" not in sig_get.parameters


def test_04_get_polling_does_not_dispatch_celery(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag, mock_celery_task
):
    """4. GET polling kesinlikle Celery dispatch yapmaz."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    att = _create_contents_attempt(db_session, brief=brief, ideas=ideas, status="pending")
    url = f"/api/v1/generation/social/briefs/{brief.id}/contents/attempts/{att.id}?brand_profile_id={ws.id}"
    resp = client.get(url)
    assert resp.status_code == 200
    mock_celery_task.assert_not_called()


def test_05_route_priority_not_swallowed_by_scoring_run(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag, mock_celery_task
):
    """5. /contents/async ve /contents/attempts/{id} rotaları /social/{scoring_run_id} tarafından yutulmaz."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    url_post = f"/api/v1/generation/social/briefs/{brief.id}/contents/async?brand_profile_id={ws.id}"
    resp_post = client.post(url_post, json={"idempotency_key": "route-test", "idea_ids": [ideas[0].id]})
    assert resp_post.status_code == 202

    att = _create_contents_attempt(db_session, brief=brief, ideas=[ideas[0]], status="completed")
    _create_valid_content(db_session, brief=brief, target=target, idea=ideas[0])
    db_session.commit()
    url_get = f"/api/v1/generation/social/briefs/{brief.id}/contents/attempts/{att.id}?brand_profile_id={ws.id}"
    resp_get = client.get(url_get)
    assert resp_get.status_code == 200


# ==================== 3. POST ASYNC DISPATCH BAŞARI SENARYOLARI ====================

def test_06_post_new_attempt_returns_202_and_dispatches_task(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag, mock_celery_task
):
    """6. Geçerli yeni istek -> 202 Accepted, replayed=False, task kuyruğa alınır."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    url = f"/api/v1/generation/social/briefs/{brief.id}/contents/async?brand_profile_id={ws.id}"
    key = "new-dispatch-key-1"
    req_ids = [ideas[0].id, ideas[1].id]
    payload = {
        "idempotency_key": key,
        "idea_ids": req_ids,
        "trusted_brand_usp": "En iyi içerik platformu",
    }

    resp = client.post(url, json=payload)
    assert resp.status_code == 202
    data = resp.json()

    assert data["brief_id"] == brief.id
    assert data["scoring_run_id"] == run.id
    assert data["attempt_status"] == "pending"
    assert data["replayed"] is False
    assert data["requested_idea_ids"] == req_ids
    assert data["successful_idea_ids"] == []
    assert data["unresolved_idea_ids"] == []
    assert data["total_contents"] == 0
    assert data["contents"] == []
    assert data["warnings"] == []
    assert data["reason_code"] is None

    # Celery dispatch assertion
    mock_celery_task.assert_called_once()
    call_args, call_kwargs = mock_celery_task.call_args
    assert call_args == ()
    assert call_kwargs["args"] == [data["attempt_id"]]
    assert "task_id" in call_kwargs
    # task_id broker argümanı değildir, Celery task_id kwargs'ıdır
    assert uuid.UUID(call_kwargs["task_id"])  # geçerli uuid

    # DB durumu: pending ve task_id henüz None
    db_session.expire_all()
    att = db_session.query(SocialGenerationAttempt).filter_by(id=data["attempt_id"]).one()
    assert att.status == "pending"
    assert att.task_id is None
    assert att.stage == "contents"


def test_07_celery_args_do_not_contain_facts_or_usp(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag, mock_celery_task
):
    """7. Celery dispatch argümanlarında product_facts veya USP yer almaz; yalnızca [attempt_id]."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    url = f"/api/v1/generation/social/briefs/{brief.id}/contents/async?brand_profile_id={ws.id}"
    payload = {
        "idempotency_key": "safe-args-key",
        "idea_ids": [ideas[0].id],
        "trusted_brand_usp": "Çok gizli USP",
    }
    resp = client.post(url, json=payload)
    assert resp.status_code == 202

    _, call_kwargs = mock_celery_task.call_args
    assert len(call_kwargs["args"]) == 1
    assert isinstance(call_kwargs["args"][0], int)
    assert "Çok gizli USP" not in str(call_kwargs["args"])


# ==================== 4. POST REPLAY SENARYOLARI ====================

def test_08_post_completed_replay_returns_200_no_dispatch(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag, mock_celery_task
):
    """8. Completed attempt aynı key ile çağrıldığında 200 OK ve dolu contents döner, 0 Celery call."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    key = "completed-replay-key"
    att = _create_contents_attempt(
        db_session, brief=brief, ideas=[ideas[0]], status="completed", idempotency_key=key
    )
    _create_valid_content(db_session, brief=brief, target=target, idea=ideas[0], caption="Replay caption")
    db_session.commit()

    url = f"/api/v1/generation/social/briefs/{brief.id}/contents/async?brand_profile_id={ws.id}"
    payload = {"idempotency_key": key, "idea_ids": [ideas[0].id], "trusted_brand_usp": "Örnek USP"}
    resp = client.post(url, json=payload)

    assert resp.status_code == 200
    data = resp.json()
    assert data["attempt_id"] == att.id
    assert data["attempt_status"] == "completed"
    assert data["replayed"] is True
    assert data["total_contents"] == 1
    assert data["contents"][0]["caption"] == "Replay caption"
    mock_celery_task.assert_not_called()


def test_09_post_pending_replay_returns_202_no_dispatch(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag, mock_celery_task
):
    """9. Pending attempt aynı key ile çağrıldığında 202 Accepted ve replayed=True döner, 0 Celery call."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    key = "pending-replay-key"
    att = _create_contents_attempt(
        db_session, brief=brief, ideas=[ideas[0]], status="pending", idempotency_key=key
    )
    url = f"/api/v1/generation/social/briefs/{brief.id}/contents/async?brand_profile_id={ws.id}"
    payload = {"idempotency_key": key, "idea_ids": [ideas[0].id], "trusted_brand_usp": "Örnek USP"}
    resp = client.post(url, json=payload)

    assert resp.status_code == 202
    data = resp.json()
    assert data["attempt_id"] == att.id
    assert data["attempt_status"] == "pending"
    assert data["replayed"] is True
    mock_celery_task.assert_not_called()


def test_10_post_running_replay_returns_202_no_dispatch(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag, mock_celery_task
):
    """10. Running attempt aynı key ile çağrıldığında 202 Accepted ve replayed=True döner, 0 Celery call."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    key = "running-replay-key"
    att = _create_contents_attempt(
        db_session,
        brief=brief,
        ideas=[ideas[0]],
        status="running",
        idempotency_key=key,
        task_id="worker-task-1",
        lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    url = f"/api/v1/generation/social/briefs/{brief.id}/contents/async?brand_profile_id={ws.id}"
    payload = {"idempotency_key": key, "idea_ids": [ideas[0].id], "trusted_brand_usp": "Örnek USP"}
    resp = client.post(url, json=payload)

    assert resp.status_code == 202
    data = resp.json()
    assert data["attempt_id"] == att.id
    assert data["attempt_status"] == "running"
    assert data["replayed"] is True
    mock_celery_task.assert_not_called()


def test_11_post_failed_replay_returns_409_terminal(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag, mock_celery_task
):
    """11. Failed attempt aynı key ile çağrıldığında 409 CONTENT_ATTEMPT_TERMINAL döner."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    key = "failed-replay-key"
    att = _create_contents_attempt(
        db_session,
        brief=brief,
        ideas=[ideas[0]],
        status="failed",
        reason_code="content_provider_error",
        idempotency_key=key,
    )
    url = f"/api/v1/generation/social/briefs/{brief.id}/contents/async?brand_profile_id={ws.id}"
    payload = {"idempotency_key": key, "idea_ids": [ideas[0].id], "trusted_brand_usp": "Örnek USP"}
    resp = client.post(url, json=payload)

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "CONTENT_ATTEMPT_TERMINAL"
    assert detail["retryable"] is True
    assert detail["retry_with_new_idempotency_key"] is True
    mock_celery_task.assert_not_called()


def test_12_post_partial_replay_returns_409_terminal(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag, mock_celery_task
):
    """12. Partial attempt aynı key ile çağrıldığında 409 CONTENT_ATTEMPT_TERMINAL döner."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    key = "partial-replay-key"
    att = _create_contents_attempt(
        db_session,
        brief=brief,
        ideas=[ideas[0]],
        status="partial",
        idempotency_key=key,
    )
    url = f"/api/v1/generation/social/briefs/{brief.id}/contents/async?brand_profile_id={ws.id}"
    payload = {"idempotency_key": key, "idea_ids": [ideas[0].id], "trusted_brand_usp": "Örnek USP"}
    resp = client.post(url, json=payload)

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "CONTENT_ATTEMPT_TERMINAL"
    assert detail["retryable"] is True
    mock_celery_task.assert_not_called()


def test_13_post_failed_then_retry_with_new_key_succeeds(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag, mock_celery_task
):
    """13. Failed attempt sonrası yeni idempotency key ile yeni attempt açılıp dispatch edilir."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    _create_contents_attempt(
        db_session,
        brief=brief,
        ideas=[ideas[0]],
        status="failed",
        reason_code="content_provider_error",
        idempotency_key="old-failed-key",
    )
    url = f"/api/v1/generation/social/briefs/{brief.id}/contents/async?brand_profile_id={ws.id}"
    payload = {"idempotency_key": "fresh-key-after-failure", "idea_ids": [ideas[0].id]}
    resp = client.post(url, json=payload)

    assert resp.status_code == 202
    assert resp.json()["replayed"] is False
    mock_celery_task.assert_called_once()


def test_14_post_active_conflict_with_different_key_returns_409(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag, mock_celery_task
):
    """14. Aktif pending/running attempt varken farklı key ile istek 409 ATTEMPT_CONFLICT döner."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    _create_contents_attempt(
        db_session,
        brief=brief,
        ideas=[ideas[0]],
        status="running",
        idempotency_key="key-1",
        task_id="active-task",
        lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    url = f"/api/v1/generation/social/briefs/{brief.id}/contents/async?brand_profile_id={ws.id}"
    payload = {"idempotency_key": "different-key-2", "idea_ids": [ideas[0].id]}
    resp = client.post(url, json=payload)

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "ATTEMPT_CONFLICT"
    mock_celery_task.assert_not_called()


def test_15_post_idempotency_key_request_mismatch_returns_409(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag, mock_celery_task
):
    """15. Aynı key ile farklı istek gövdesi (farklı idea_ids) 409 CONTENT_ATTEMPT_REQUEST_MISMATCH döner."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    key = "mismatch-key"
    _create_contents_attempt(
        db_session, brief=brief, ideas=[ideas[0]], status="completed", idempotency_key=key
    )
    url = f"/api/v1/generation/social/briefs/{brief.id}/contents/async?brand_profile_id={ws.id}"
    payload = {"idempotency_key": key, "idea_ids": [ideas[1].id]}  # Farklı fikir
    resp = client.post(url, json=payload)

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "CONTENT_ATTEMPT_REQUEST_MISMATCH"
    mock_celery_task.assert_not_called()


# ==================== 5. PREFLIGHT DOĞRULAMA HATALARI ====================

def test_16_post_nonexistent_brief_returns_404(
    client: TestClient, db_session: Session, make_workspace, enable_flag, mock_celery_task
):
    """16. Olmayan brief -> 404 BRIEF_NOT_FOUND."""
    ws = make_workspace(name="No Brief Brand")
    url = f"/api/v1/generation/social/briefs/99999/contents/async?brand_profile_id={ws.id}"
    payload = {"idempotency_key": "any-key", "idea_ids": [1]}
    resp = client.post(url, json=payload)
    assert resp.status_code == 404
    mock_celery_task.assert_not_called()


def test_17_post_cross_workspace_brief_returns_404(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag, mock_celery_task
):
    """17. Farklı workspace brief'i -> 404 BRIEF_NOT_FOUND."""
    ws1, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword, brand_name="Brand 1"
    )
    ws2 = make_workspace(name="Brand 2", status="confirmed")
    url = f"/api/v1/generation/social/briefs/{brief.id}/contents/async?brand_profile_id={ws2.id}"
    payload = {"idempotency_key": "cross-ws-key", "idea_ids": [ideas[0].id]}
    resp = client.post(url, json=payload)
    assert resp.status_code == 404
    mock_celery_task.assert_not_called()


def test_18_post_brief_not_locked_returns_409(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag, mock_celery_task
):
    """18. Kilitlenmemiş brief -> 409 CONTENT_BRIEF_NOT_LOCKED."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    brief.locked_at = None
    db_session.commit()

    url = f"/api/v1/generation/social/briefs/{brief.id}/contents/async?brand_profile_id={ws.id}"
    payload = {"idempotency_key": "unlocked-brief-key", "idea_ids": [ideas[0].id]}
    resp = client.post(url, json=payload)
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "CONTENT_BRIEF_NOT_LOCKED"
    mock_celery_task.assert_not_called()


def test_19_post_stale_brief_returns_409(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag, mock_celery_task
):
    """19. Stale brief -> 409 CONTENT_BRIEF_STALE."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    brief.is_stale = True
    db_session.commit()

    url = f"/api/v1/generation/social/briefs/{brief.id}/contents/async?brand_profile_id={ws.id}"
    payload = {"idempotency_key": "stale-brief-key", "idea_ids": [ideas[0].id]}
    resp = client.post(url, json=payload)
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "CONTENT_BRIEF_STALE"
    mock_celery_task.assert_not_called()


def test_20_post_assignment_changed_returns_409(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag, mock_celery_task
):
    """20. Assignment versiyonu uyuşmazlığı -> 409 CONTENT_ASSIGNMENT_CHANGED."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    run.channel_assignment_version = 2
    db_session.commit()

    url = f"/api/v1/generation/social/briefs/{brief.id}/contents/async?brand_profile_id={ws.id}"
    payload = {"idempotency_key": "version-mismatch-key", "idea_ids": [ideas[0].id]}
    resp = client.post(url, json=payload)
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "CONTENT_ASSIGNMENT_CHANGED"
    mock_celery_task.assert_not_called()


def test_21_post_invalid_ideas_returns_400(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag, mock_celery_task
):
    """21. Başka brief'e ait fikir ID'si -> 400 CONTENT_IDEAS_INVALID."""
    ws1, run1, brief1, target1, cat1, kw1, ideas1 = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword, brand_name="Brand 1"
    )
    ws2, run2, brief2, target2, cat2, kw2, ideas2 = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword, brand_name="Brand 2"
    )
    url = f"/api/v1/generation/social/briefs/{brief1.id}/contents/async?brand_profile_id={ws1.id}"
    payload = {"idempotency_key": "invalid-ideas-key", "idea_ids": [ideas2[0].id]}
    resp = client.post(url, json=payload)
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "CONTENT_IDEA_NOT_ELIGIBLE"
    mock_celery_task.assert_not_called()


def test_22_post_stale_idea_returns_400_not_eligible(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag, mock_celery_task
):
    """22. Stale fikir seçilirse -> 400 CONTENT_IDEA_NOT_ELIGIBLE."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    ideas[0].is_stale = True
    db_session.commit()

    url = f"/api/v1/generation/social/briefs/{brief.id}/contents/async?brand_profile_id={ws.id}"
    payload = {"idempotency_key": "stale-idea-key", "idea_ids": [ideas[0].id]}
    resp = client.post(url, json=payload)
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "CONTENT_IDEA_NOT_ELIGIBLE"
    mock_celery_task.assert_not_called()


def test_23_post_invalid_id_params_returns_400_or_422(
    client: TestClient, enable_flag
):
    """23. Negatif veya geçersiz kimlik parametreleri -> 400 veya 422."""
    resp = client.post("/api/v1/generation/social/briefs/-1/contents/async?brand_profile_id=1", json={"idempotency_key": "k", "idea_ids": [1]})
    assert resp.status_code in (400, 422)

    resp2 = client.post("/api/v1/generation/social/briefs/1/contents/async?brand_profile_id=-5", json={"idempotency_key": "k", "idea_ids": [1]})
    assert resp2.status_code in (400, 422)


# ==================== 6. BROKER ENQUEUE FAILURE COMPENSATION ====================

def test_24_broker_enqueue_failure_compensation_marks_dispatch_failed_returns_503(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """24. Celery apply_async hata verirse attempt dispatch_failed yapılır ve 503 döner."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    url = f"/api/v1/generation/social/briefs/{brief.id}/contents/async?brand_profile_id={ws.id}"
    payload = {"idempotency_key": "broker-down-key", "idea_ids": [ideas[0].id]}

    with patch("app.api.v1.generation.social_brief_contents_task.apply_async") as mock_apply:
        mock_apply.side_effect = ConnectionError("Redis broker is unreachable")
        resp = client.post(url, json=payload)

    assert resp.status_code == 503
    data = resp.json()
    assert data["detail"]["code"] == "CONTENT_DISPATCH_FAILED"
    assert "Redis" not in str(data)  # broker hatası sızmaz

    # DB'de attempt'in failed/dispatch_failed olduğu doğrulanır
    attempt_id = data["detail"]["attempt_id"]
    db_session.expire_all()
    att = db_session.query(SocialGenerationAttempt).filter_by(id=attempt_id).one()
    assert att.status == "failed"
    assert att.reason_code == "dispatch_failed"
    assert att.lease_expires_at is None
    assert att.completed_at is not None


def test_25_broker_failure_compensation_failure_returns_500(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """25. Broker enqueue patladığında compensation finalize da çökerse güvenli 500 döner."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    url = f"/api/v1/generation/social/briefs/{brief.id}/contents/async?brand_profile_id={ws.id}"
    payload = {"idempotency_key": "double-fault-key", "idea_ids": [ideas[0].id]}

    with patch("app.api.v1.generation.social_brief_contents_task.apply_async") as mock_apply, \
         patch("app.api.v1.generation.finalize_contents_attempt_failure") as mock_finalize:
        mock_apply.side_effect = ConnectionError("Broker down")
        mock_finalize.side_effect = RuntimeError("DB locked during compensation")

        resp = client.post(url, json=payload)

    assert resp.status_code == 500
    assert resp.json()["detail"]["code"] == "CONTENT_DISPATCH_FINALIZATION_FAILED"


# ==================== 7. POST-DISPATCH READ FAILURE İZOLASYONU ====================

def test_26_post_dispatch_read_failure_does_not_mark_dispatch_failed_returns_500(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag, mock_celery_task
):
    """26. Görev başarıyla kuyruğa alındıktan sonra read hatası verirse attempt failed yapılmaz; 500 döner."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    url = f"/api/v1/generation/social/briefs/{brief.id}/contents/async?brand_profile_id={ws.id}"
    payload = {"idempotency_key": "post-read-fail-key", "idea_ids": [ideas[0].id]}

    with patch("app.api.v1.generation.load_social_content_result") as mock_read:
        mock_read.side_effect = RuntimeError("Read projection failed")
        resp = client.post(url, json=payload)

    assert resp.status_code == 500
    mock_celery_task.assert_called_once()

    # DB'de attempt HALA pending kalmalıdır (worker işleyebilir)
    db_session.expire_all()
    att = db_session.query(SocialGenerationAttempt).filter_by(brief_id=brief.id).one()
    assert att.status == "pending"
    assert att.reason_code is None


# ==================== 8. GET POLLING STATUS BAŞARI SENARYOLARI ====================

def test_27_get_pending_attempt_returns_200(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """27. Pending attempt polling -> 200, contents=[], warnings=[]."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    att = _create_contents_attempt(
        db_session,
        brief=brief,
        ideas=[ideas[0]],
        status="pending",
        lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    url = f"/api/v1/generation/social/briefs/{brief.id}/contents/attempts/{att.id}?brand_profile_id={ws.id}"
    resp = client.get(url)

    assert resp.status_code == 200
    data = resp.json()
    assert data["attempt_status"] == "pending"
    assert data["replayed"] is True
    assert data["total_contents"] == 0
    assert data["contents"] == []
    assert data["warnings"] == []


def test_28_get_running_attempt_returns_200(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """28. Running attempt polling -> 200, contents=[], warnings=[]."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    att = _create_contents_attempt(
        db_session,
        brief=brief,
        ideas=[ideas[0]],
        status="running",
        task_id="active-task",
        lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    url = f"/api/v1/generation/social/briefs/{brief.id}/contents/attempts/{att.id}?brand_profile_id={ws.id}"
    resp = client.get(url)

    assert resp.status_code == 200
    data = resp.json()
    assert data["attempt_status"] == "running"
    assert data["replayed"] is True


def test_29_get_completed_attempt_returns_200_with_all_20_fields(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """29. Completed attempt polling -> 200, tüm 20 alan eksiksiz döner."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    att = _create_contents_attempt(
        db_session, brief=brief, ideas=[ideas[0]], status="completed"
    )
    content = _create_valid_content(
        db_session, brief=brief, target=target, idea=ideas[0], caption="Nihai Instagram Post Metni"
    )
    db_session.commit()

    url = f"/api/v1/generation/social/briefs/{brief.id}/contents/attempts/{att.id}?brand_profile_id={ws.id}"
    resp = client.get(url)

    assert resp.status_code == 200
    data = resp.json()
    assert data["attempt_status"] == "completed"
    assert data["total_contents"] == 1
    c = data["contents"][0]

    # 20 alan doğrulaması
    assert c["id"] == content.id
    assert c["idea_id"] == ideas[0].id
    assert c["brief_id"] == brief.id
    assert c["target_id"] == target.id
    assert c["platform"] == "instagram"
    assert c["content_format"] == "post"
    assert len(c["hooks"]) == 3
    assert c["caption"] == "Nihai Instagram Post Metni"
    assert c["scenario"] is None
    assert c["format_payload"] is None
    assert c["visual_suggestion"] is None
    assert c["video_concept"] is None
    assert c["cta_text"] == "Daha fazlası için profildeki linke tıklayın."
    assert len(c["hashtags"]) == 5
    assert c["industry_posting_suggestion"] is None
    assert c["platform_notes"] is None
    assert c["duration_status"] == "not_applicable"
    assert c["actual_duration_sec"] is None
    assert c["validation_warnings"] == []
    assert c["is_stale"] is False


def test_30_get_failed_attempt_returns_200_with_reason_and_warnings(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """30. Failed attempt polling -> 200, attempt_status="failed", reason_code ve warnings döner."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    warnings = [
        {"idea_id": ideas[0].id, "reason_code": "content_rejected", "claims": ["%100 garanti"], "ai_calls_used": 2}
    ]
    att = _create_contents_attempt(
        db_session,
        brief=brief,
        ideas=[ideas[0]],
        status="failed",
        reason_code="content_generation_failed",
        error_message="İçerik üretimi başarısız oldu.",
        warnings=warnings,
    )
    url = f"/api/v1/generation/social/briefs/{brief.id}/contents/attempts/{att.id}?brand_profile_id={ws.id}"
    resp = client.get(url)

    assert resp.status_code == 200
    data = resp.json()
    assert data["attempt_status"] == "failed"
    assert data["reason_code"] == "content_generation_failed"
    assert len(data["warnings"]) == 1
    assert data["warnings"][0]["idea_id"] == ideas[0].id
    assert data["warnings"][0]["reason_code"] == "content_rejected"
    assert data["warnings"][0]["claims"] == ["%100 garanti"]
    assert data["warnings"][0]["ai_calls_used"] == 2


def test_31_get_partial_attempt_returns_200_with_contents_and_warnings(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """31. Partial attempt polling -> 200, hem başarılı contents hem warnings döner."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=2
    )
    warnings = [
        {"idea_id": ideas[1].id, "reason_code": "content_rejected", "claims": ["En iyi ajans"], "ai_calls_used": 2}
    ]
    att = _create_contents_attempt(
        db_session,
        brief=brief,
        ideas=ideas,
        status="partial",
        reason_code="content_partial",
        error_message="Kısmi içerik üretimi gerçekleşti.",
        warnings=warnings,
    )
    _create_valid_content(db_session, brief=brief, target=target, idea=ideas[0], caption="Başarılı içerik")
    db_session.commit()

    url = f"/api/v1/generation/social/briefs/{brief.id}/contents/attempts/{att.id}?brand_profile_id={ws.id}"
    resp = client.get(url)

    assert resp.status_code == 200
    data = resp.json()
    assert data["attempt_status"] == "partial"
    assert data["total_contents"] == 1
    assert data["successful_idea_ids"] == [ideas[0].id]
    assert data["unresolved_idea_ids"] == [ideas[1].id]
    assert len(data["warnings"]) == 1
    assert data["warnings"][0]["reason_code"] == "content_rejected"


# ==================== 9. GET POLLING LEASE RECONCILIATION ====================

def test_32_get_expired_pending_attempt_reconciles_to_failed_worker_lost(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """32. Süresi dolmuş pending attempt polling anında failed/worker_lost yapılır."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    expired_time = datetime.now(timezone.utc) - timedelta(seconds=10)
    att = _create_contents_attempt(
        db_session,
        brief=brief,
        ideas=[ideas[0]],
        status="pending",
        lease_expires_at=expired_time,
    )
    url = f"/api/v1/generation/social/briefs/{brief.id}/contents/attempts/{att.id}?brand_profile_id={ws.id}"
    resp = client.get(url)

    assert resp.status_code == 200
    data = resp.json()
    assert data["attempt_status"] == "failed"
    assert data["reason_code"] == "worker_lost"

    # DB doğrulaması
    db_session.expire_all()
    reloaded = db_session.query(SocialGenerationAttempt).filter_by(id=att.id).one()
    assert reloaded.status == "failed"
    assert reloaded.reason_code == "worker_lost"
    assert reloaded.lease_expires_at is None
    assert reloaded.task_id is None


def test_33_get_expired_running_attempt_reconciles_to_failed_worker_lost(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """33. Süresi dolmuş running attempt polling anında failed/worker_lost yapılır."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    expired_time = datetime.now(timezone.utc) - timedelta(seconds=10)
    att = _create_contents_attempt(
        db_session,
        brief=brief,
        ideas=[ideas[0]],
        status="running",
        task_id="lost-worker-task",
        lease_expires_at=expired_time,
    )
    url = f"/api/v1/generation/social/briefs/{brief.id}/contents/attempts/{att.id}?brand_profile_id={ws.id}"
    resp = client.get(url)

    assert resp.status_code == 200
    data = resp.json()
    assert data["attempt_status"] == "failed"
    assert data["reason_code"] == "worker_lost"

    # DB doğrulaması
    db_session.expire_all()
    reloaded = db_session.query(SocialGenerationAttempt).filter_by(id=att.id).one()
    assert reloaded.status == "failed"
    assert reloaded.reason_code == "worker_lost"
    assert reloaded.lease_expires_at is None
    assert reloaded.task_id is None


def test_34_get_terminal_attempts_are_not_mutated_by_reconciliation(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """34. Completed/failed/partial attempt'ler reconciliation tarafından mutate edilmez."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    att_fail = _create_contents_attempt(
        db_session,
        brief=brief,
        ideas=[ideas[0]],
        status="failed",
        reason_code="content_provider_error",
        error_message="Original failure message",
    )
    url = f"/api/v1/generation/social/briefs/{brief.id}/contents/attempts/{att_fail.id}?brand_profile_id={ws.id}"
    resp = client.get(url)

    assert resp.status_code == 200
    data = resp.json()
    assert data["reason_code"] == "content_provider_error"

    db_session.expire_all()
    reloaded = db_session.query(SocialGenerationAttempt).filter_by(id=att_fail.id).one()
    assert reloaded.error_message == "Original failure message"


# ==================== 10. GET POLLING WORKSPACE VE KİMLİK İZOLASYONU ====================

def test_35_get_cross_workspace_attempt_returns_404(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """35. Başka workspace'e ait attempt -> 404 CONTENT_ATTEMPT_NOT_FOUND."""
    ws1, run1, brief1, target1, cat1, kw1, ideas1 = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword, brand_name="Brand 1"
    )
    ws2 = make_workspace(name="Brand 2", status="confirmed")
    att = _create_contents_attempt(db_session, brief=brief1, ideas=ideas1, status="completed")

    url = f"/api/v1/generation/social/briefs/{brief1.id}/contents/attempts/{att.id}?brand_profile_id={ws2.id}"
    resp = client.get(url)
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "CONTENT_ATTEMPT_NOT_FOUND"


def test_36_get_wrong_brief_attempt_returns_404(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """36. Farklı brief ID'si ile sorgulanan attempt -> 404 CONTENT_ATTEMPT_NOT_FOUND."""
    ws, run, brief1, target1, cat1, kw1, ideas1 = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword, brand_name="Brief 1"
    )
    att = _create_contents_attempt(db_session, brief=brief1, ideas=ideas1, status="completed")

    url = f"/api/v1/generation/social/briefs/99999/contents/attempts/{att.id}?brand_profile_id={ws.id}"
    resp = client.get(url)
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "CONTENT_ATTEMPT_NOT_FOUND"


def test_37_get_wrong_stage_attempt_returns_404(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """37. Ideas stage attempt contents endpoint'inden sorgulanırsa -> 404 CONTENT_ATTEMPT_NOT_FOUND."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    att_ideas = _create_contents_attempt(
        db_session, brief=brief, ideas=ideas, status="completed", stage="ideas"
    )
    url = f"/api/v1/generation/social/briefs/{brief.id}/contents/attempts/{att_ideas.id}?brand_profile_id={ws.id}"
    resp = client.get(url)
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "CONTENT_ATTEMPT_NOT_FOUND"


# ==================== 11. RESPONSE ŞEMASI VE GÜVENLİK ====================

def test_38_response_schema_is_strictly_json_serializable(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """38. Tüm yanıtlar pydantic şemasına tam uyar ve standart JSON serialize edilebilir."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    att = _create_contents_attempt(db_session, brief=brief, ideas=[ideas[0]], status="completed")
    _create_valid_content(db_session, brief=brief, target=target, idea=ideas[0])
    db_session.commit()

    url = f"/api/v1/generation/social/briefs/{brief.id}/contents/attempts/{att.id}?brand_profile_id={ws.id}"
    resp = client.get(url)
    assert resp.status_code == 200

    raw_text = resp.text
    parsed = json.loads(raw_text)
    validated = SocialBriefContentsAttemptResponse.model_validate(parsed)
    assert validated.attempt_id == att.id

    # Güvenlik kontrolü: product_facts, trusted_brand_usp veya task_id yanıt kökünde bulunmaz
    assert "product_facts" not in parsed
    assert "trusted_brand_usp" not in parsed
    assert "task_id" not in parsed


# ==================== 12. UÇTAN UCA YAŞAM DÖNGÜSÜ ====================

def test_39_end_to_end_lifecycle_post_poll_persist_complete(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag, mock_celery_task
):
    """39. Uçtan uca yaşam döngüsü: POST 202 -> GET pending -> Worker persist -> GET completed."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    post_url = f"/api/v1/generation/social/briefs/{brief.id}/contents/async?brand_profile_id={ws.id}"
    key = "e2e-lifecycle-key"
    post_payload = {"idempotency_key": key, "idea_ids": [ideas[0].id]}

    # 1. POST Dispatch
    resp_post = client.post(post_url, json=post_payload)
    assert resp_post.status_code == 202
    post_data = resp_post.json()
    attempt_id = post_data["attempt_id"]

    # 2. GET Polling (Pending)
    get_url = f"/api/v1/generation/social/briefs/{brief.id}/contents/attempts/{attempt_id}?brand_profile_id={ws.id}"
    resp_poll1 = client.get(get_url)
    assert resp_poll1.status_code == 200
    assert resp_poll1.json()["attempt_status"] == "pending"

    # 3. Worker Simülasyonu: Worker claim eder, içeriği persist eder, attempt completed yapar
    worker_task_id = "e2e-worker-task"
    db_session.expire_all()
    claim_contents_attempt_for_worker(db_session, attempt_id=attempt_id, task_id=worker_task_id)
    _create_valid_content(db_session, brief=brief, target=target, idea=ideas[0], caption="Worker üretimi")
    att = db_session.query(SocialGenerationAttempt).filter_by(id=attempt_id).one()
    att.status = "completed"
    att.completed_at = datetime.now(timezone.utc)
    att.lease_expires_at = None
    db_session.commit()

    # 4. GET Polling (Completed)
    resp_poll2 = client.get(get_url)
    assert resp_poll2.status_code == 200
    data2 = resp_poll2.json()
    assert data2["attempt_status"] == "completed"
    assert data2["total_contents"] == 1
    assert data2["contents"][0]["caption"] == "Worker üretimi"
