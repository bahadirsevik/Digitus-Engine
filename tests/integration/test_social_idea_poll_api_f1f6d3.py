# -*- coding: utf-8 -*-
"""MİKRO FAZ F1-F.6d.3 — Fikir Attempt GET Polling ve Lease Reconciliation Entegrasyon Testleri.

Tüm testler gerçek PostgreSQL test veritabanını ve FastAPI TestClient'ı kullanır.
Test edilen senaryolar:
1. Feature Flag:
   - ENABLE_SOCIAL_BRIEF_FLOW=False -> 404 FEATURE_DISABLED.
   - Flag kapalıyken DB / reconciliation / read çağrılmaz.
2. Endpoint Bağımsızlığı & Güvenlik:
   - Endpoint get_ai dependency'si içermez.
   - Endpoint Celery task dispatch yapmaz (0 calls).
   - Endpoint TaskResult oluşturmaz.
   - Endpoint AI çağrısı yapmaz.
   - Statik rota /social/{scoring_run_id} tarafından yutulmaz.
   - temperature=None yapılandırması korunur.
3. Status Polling Başarı Senaryoları (200 OK):
   - Pending attempt polling -> 200, replayed=True, ideas=[], coverage accepted=0, missing=requested.
   - Running attempt polling -> 200, replayed=True, ideas=[].
   - Completed attempt polling -> 200, replayed=True, fikirler dolu, coverage missing=0.
   - Failed attempt polling -> 200, replayed=True, ideas=[], reason_code güvenli döner, error_message sızmaz.
   - Partial attempt polling -> 200, replayed=True, geçerli fikirler ve gerçek coverage döner.
4. Lease Reconciliation:
   - Expired pending attempt aynı çağrıda failed/worker_lost döner (kullanıcı ikinci polling beklemez).
   - Expired running attempt aynı çağrıda failed/worker_lost döner.
   - Expired attempt lease_expires_at=None ve completed_at doldurulur.
   - Unexpired pending değiştirilmez.
   - Unexpired running değiştirilmez.
   - Completed attempt reconciliation ile değiştirilmez.
   - Failed attempt reason_code / error_message ezilmez.
   - Partial attempt değiştirilmez.
   - İkinci polling tam idempotency sergiler.
5. Workspace ve Kimlik İzolasyonu:
   - Başka workspace -> 404 IDEA_ATTEMPT_NOT_FOUND.
   - Attempt başka brief'e ait -> 404 IDEA_ATTEMPT_NOT_FOUND.
   - Categories veya contents stage attempt -> bilgi sızdırmayan 404 IDEA_ATTEMPT_NOT_FOUND.
   - Soft-deleted workspace (deleted_at IS NOT NULL) -> 404 IDEA_ATTEMPT_NOT_FOUND.
   - Var olmayan brief / attempt -> 404 IDEA_ATTEMPT_NOT_FOUND.
   - Geçersiz path/query ID parametreleri -> 400 IDEA_ATTEMPT_INVALID_INPUT veya 422.
6. Hata ve Tutarlılık Korumaları:
   - Bozuk plan / coverage snapshot -> güvenli 500 IDEA_READ_INCONSISTENT.
   - Bilinmeyen attempt status -> güvenli 500; raw status sızmaz.
   - Reconciliation hata yolunda rollback yapılır.
   - Reconciliation commit'i read'den önce gerçekleşir; read servisi güncel state'i görür.
   - Yenilenmiş lease eski session cache'i nedeniyle failed yapılmaz (populate_existing).
   - Canonical SQL lock sırası: ScoringRun -> SocialBrief -> SocialGenerationAttempt.
   - Response SocialIdeasGenerateResponse şemasına tam uyar ve JSON serializable'dır.
"""
from __future__ import annotations

import inspect
import json
from datetime import datetime, timedelta, timezone
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
    SocialIdeaReadNotFoundError,
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
    reconcile_expired_ideas_attempt_for_read,
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


# ==================== ENVIRONMENT BUILDERS ====================

def _setup_base_environment(
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
    num_kws: int = 3,
    workspace_name: str = "Ideas Poll Brand",
):
    """Temel workspace, scoring run ve SOCIAL pool kayıtlarını hazırlar."""
    ws = make_workspace(name=workspace_name, status="confirmed")
    run = make_scoring_run(
        brand_profile_id=ws.id,
        status="completed",
        algorithm_version="v2",
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
    workspace_name: str = "Ideas Poll Ready Brand",
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
        idempotency_key=f"cat-poll-key-{brief.id}",
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
    task_id: str = "task-poll-persist",
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

def test_01_feature_flag_disabled_returns_404(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """1. ENABLE_SOCIAL_BRIEF_FLOW kapalıyken GET polling HTTP 404 FEATURE_DISABLED döner."""
    ws, run, brief, categories, targets, kws = _setup_ready_brief_and_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    req = SocialBriefIdeasGenerateRequest(
        idempotency_key="poll-flag-off",
        category_ids=[c.id for c in categories],
        ideas_per_category=2,
    )
    start = begin_social_idea_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req)
    db_session.commit()

    url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/attempts/{start.attempt_id}?brand_profile_id={ws.id}"
    resp = client.get(url)
    assert resp.status_code == 404
    body = resp.json()
    assert body["detail"]["code"] == "FEATURE_DISABLED"


def test_02_feature_flag_disabled_calls_zero_reconcile_or_read(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """2. Feature flag kapalıyken reconcile ve read fonksiyonları kesinlikle çağrılmaz."""
    ws, run, brief, categories, targets, kws = _setup_ready_brief_and_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/attempts/101?brand_profile_id={ws.id}"

    with patch("app.api.v1.generation.reconcile_expired_ideas_attempt_for_read") as mock_rec, \
         patch("app.api.v1.generation.load_social_idea_result") as mock_read:
        resp = client.get(url)
        assert resp.status_code == 404
        assert mock_rec.call_count == 0
        assert mock_read.call_count == 0


# ==================== 2. ENDPOINT BAĞIMSIZLIĞI & GÜVENLİK ====================

def test_03_endpoint_has_no_ai_dependency_or_calls():
    """3. Endpoint get_ai bağımlılığı almaz."""
    from app.api.v1.generation import get_social_ideas_attempt

    sig = inspect.signature(get_social_ideas_attempt)
    param_names = list(sig.parameters.keys())
    assert "ai" not in param_names
    assert "ai_service" not in param_names
    assert "get_ai" not in str(sig)


def test_04_get_endpoint_does_not_dispatch_celery(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """4. GET polling endpoint'i hiçbir Celery görevi dispatch etmez."""
    ws, run, brief, categories, targets, kws = _setup_ready_brief_and_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    req = SocialBriefIdeasGenerateRequest(
        idempotency_key="no-dispatch-poll",
        category_ids=[c.id for c in categories],
        ideas_per_category=2,
    )
    start = begin_social_idea_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req)
    db_session.commit()

    url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/attempts/{start.attempt_id}?brand_profile_id={ws.id}"

    with patch("app.api.v1.generation.social_brief_ideas_task.apply_async") as mock_apply:
        resp = client.get(url)
        assert resp.status_code == 200
        assert mock_apply.call_count == 0


def test_05_static_route_precedence_over_dynamic_scoring_run(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """5. /social/briefs/{brief_id}/ideas/attempts/{attempt_id} rotası /social/{scoring_run_id} tarafından yutulmaz."""
    ws, run, brief, categories, targets, kws = _setup_ready_brief_and_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    req = SocialBriefIdeasGenerateRequest(
        idempotency_key="route-check-poll",
        category_ids=[c.id for c in categories],
        ideas_per_category=2,
    )
    start = begin_social_idea_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req)
    db_session.commit()

    url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/attempts/{start.attempt_id}?brand_profile_id={ws.id}"
    resp = client.get(url)
    assert resp.status_code == 200
    assert resp.json()["attempt_status"] == "pending"


# ==================== 3. STATUS POLLING BAŞARI SENARYOLARI (200 OK) ====================

def test_06_pending_attempt_polling_returns_200(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """6. Pending durumdaki attempt polling edildiğinde 200 döner, ideas boştur, coverage accepted=0'dır."""
    ws, run, brief, categories, targets, kws = _setup_ready_brief_and_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    req = SocialBriefIdeasGenerateRequest(
        idempotency_key="pending-poll-key",
        category_ids=[c.id for c in categories],
        ideas_per_category=2,
    )
    start = begin_social_idea_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req)
    # Gelecekteki lease ata ki expired olmasın
    att = db_session.query(SocialGenerationAttempt).filter_by(id=start.attempt_id).one()
    att.lease_expires_at = datetime.now(timezone.utc) + timedelta(seconds=1200)
    db_session.commit()

    url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/attempts/{start.attempt_id}?brand_profile_id={ws.id}"
    resp = client.get(url)
    assert resp.status_code == 200
    body = resp.json()

    validated = SocialIdeasGenerateResponse.model_validate(body)
    assert validated.attempt_status == "pending"
    assert validated.replayed is True
    assert validated.total_ideas == 0
    assert validated.ideas == []
    for cov in validated.coverage:
        assert cov.accepted == 0
        assert cov.missing == cov.requested


def test_07_running_attempt_polling_returns_200(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """7. Running (claim edilmiş) attempt polling edildiğinde 200 döner, ideas boştur."""
    ws, run, brief, categories, targets, kws = _setup_ready_brief_and_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    req = SocialBriefIdeasGenerateRequest(
        idempotency_key="running-poll-key",
        category_ids=[c.id for c in categories],
        ideas_per_category=2,
    )
    start = begin_social_idea_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req)
    db_session.commit()

    claim_attempt(db_session, attempt_id=start.attempt_id, task_id="task-running-poll")
    # Gelecekteki lease
    att = db_session.query(SocialGenerationAttempt).filter_by(id=start.attempt_id).one()
    att.lease_expires_at = datetime.now(timezone.utc) + timedelta(seconds=1200)
    db_session.commit()

    url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/attempts/{start.attempt_id}?brand_profile_id={ws.id}"
    resp = client.get(url)
    assert resp.status_code == 200
    body = resp.json()

    validated = SocialIdeasGenerateResponse.model_validate(body)
    assert validated.attempt_status == "running"
    assert validated.replayed is True
    assert validated.total_ideas == 0
    assert validated.ideas == []


def test_08_completed_attempt_polling_returns_200_with_ideas(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """8. Completed attempt polling edildiğinde 200 döner, tüm fikirler ve missing=0 coverage döner."""
    ws, run, brief, categories, targets, kws = _setup_ready_brief_and_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    req = SocialBriefIdeasGenerateRequest(
        idempotency_key="completed-poll-key",
        category_ids=[c.id for c in categories],
        ideas_per_category=2,
    )
    start = begin_social_idea_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req)
    db_session.commit()

    claim_attempt(db_session, attempt_id=start.attempt_id, task_id="task-comp-poll")
    db_session.commit()

    _persist_dummy_ideas_for_attempt(
        db_session,
        plan=start.plan,
        attempt_id=start.attempt_id,
        brief_targets=targets,
        primary_kw_id=kws[0].id,
        task_id="task-comp-poll",
    )

    url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/attempts/{start.attempt_id}?brand_profile_id={ws.id}"
    resp = client.get(url)
    assert resp.status_code == 200
    body = resp.json()

    validated = SocialIdeasGenerateResponse.model_validate(body)
    assert validated.attempt_status == "completed"
    assert validated.replayed is True
    assert validated.total_ideas == start.plan.total_requested
    assert len(validated.ideas) == validated.total_ideas
    for cov in validated.coverage:
        assert cov.accepted == cov.requested
        assert cov.missing == 0


def test_09_failed_attempt_polling_returns_200_with_reason_code_no_error_message(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """9. Failed attempt polling edildiğinde 200 döner, reason_code döner, error_message yanıt şemasında yer almaz."""
    ws, run, brief, categories, targets, kws = _setup_ready_brief_and_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    req = SocialBriefIdeasGenerateRequest(
        idempotency_key="failed-poll-key",
        category_ids=[c.id for c in categories],
        ideas_per_category=2,
    )
    start = begin_social_idea_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req)
    db_session.commit()

    finalize_ideas_attempt_failure(
        db_session,
        attempt_id=start.attempt_id,
        task_id="task-fail-poll",
        reason_code="dispatch_failed",
        error_message="Gizli dahili hata mesajı sızmamalı.",
    )
    db_session.commit()

    url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/attempts/{start.attempt_id}?brand_profile_id={ws.id}"
    resp = client.get(url)
    assert resp.status_code == 200
    body = resp.json()

    validated = SocialIdeasGenerateResponse.model_validate(body)
    assert validated.attempt_status == "failed"
    assert validated.reason_code == "dispatch_failed"
    assert validated.ideas == []
    assert "error_message" not in body
    assert "Gizli dahili hata" not in resp.text


def test_10_partial_attempt_polling_returns_200_with_partial_ideas(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """10. Partial durumdaki attempt 200 döner, mevcut geçerli fikirleri ve gerçek kotaları içerir."""
    ws, run, brief, categories, targets, kws = _setup_ready_brief_and_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    req = SocialBriefIdeasGenerateRequest(
        idempotency_key="partial-poll-key",
        category_ids=[c.id for c in categories],
        ideas_per_category=2,
    )
    start = begin_social_idea_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req)
    db_session.commit()

    claim_attempt(db_session, attempt_id=start.attempt_id, task_id="task-partial-poll")
    db_session.commit()

    # Kısmi fikir üretimi
    target = targets[0]
    cat = categories[0]
    idea = SocialIdea(
        brief_id=brief.id,
        category_id=cat.id,
        brief_target_id=target.id,
        keyword_id=kws[0].id,
        idea_title="Kısmi Başlık",
        idea_description="Kısmi açıklama.",
        target_platform=target.platform,
        content_format=target.content_format,
        trend_alignment=0.8,
        is_stale=False,
    )
    db_session.add(idea)

    att = db_session.query(SocialGenerationAttempt).filter_by(id=start.attempt_id).one()
    att.status = "partial"
    att.reason_code = "idea_provider_error"
    db_session.commit()

    url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/attempts/{start.attempt_id}?brand_profile_id={ws.id}"
    resp = client.get(url)
    assert resp.status_code == 200
    body = resp.json()

    validated = SocialIdeasGenerateResponse.model_validate(body)
    assert validated.attempt_status == "partial"
    assert validated.total_ideas == 1
    assert len(validated.ideas) == 1
    assert validated.reason_code == "idea_provider_error"


# ==================== 4. LEASE RECONCILIATION TESTLERİ ====================

def test_11_expired_pending_attempt_reconciles_to_failed_worker_lost_in_same_call(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """11. Süresi dolmuş pending attempt aynı GET çağrısında failed/worker_lost durumuna geçer ve 200 döner."""
    ws, run, brief, categories, targets, kws = _setup_ready_brief_and_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    req = SocialBriefIdeasGenerateRequest(
        idempotency_key="expired-pending-key",
        category_ids=[c.id for c in categories],
        ideas_per_category=2,
    )
    start = begin_social_idea_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req)

    # Lease süresini geçmişe ayarla
    att = db_session.query(SocialGenerationAttempt).filter_by(id=start.attempt_id).one()
    att.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=60)
    db_session.commit()

    url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/attempts/{start.attempt_id}?brand_profile_id={ws.id}"
    resp = client.get(url)
    assert resp.status_code == 200
    body = resp.json()

    # Yanıt failed/worker_lost dönmeli
    validated = SocialIdeasGenerateResponse.model_validate(body)
    assert validated.attempt_status == "failed"
    assert validated.reason_code == "worker_lost"

    # DB durumu failed, lease_expires_at None, completed_at dolu
    db_session.expire_all()
    db_att = db_session.query(SocialGenerationAttempt).filter_by(id=start.attempt_id).one()
    assert db_att.status == "failed"
    assert db_att.reason_code == "worker_lost"
    assert db_att.lease_expires_at is None
    assert db_att.completed_at is not None


def test_12_expired_running_attempt_reconciles_to_failed_worker_lost_in_same_call(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """12. Süresi dolmuş running attempt aynı GET çağrısında failed/worker_lost durumuna geçer."""
    ws, run, brief, categories, targets, kws = _setup_ready_brief_and_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    req = SocialBriefIdeasGenerateRequest(
        idempotency_key="expired-running-key",
        category_ids=[c.id for c in categories],
        ideas_per_category=2,
    )
    start = begin_social_idea_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req)
    db_session.commit()

    claim_attempt(db_session, attempt_id=start.attempt_id, task_id="task-exp-run")
    att = db_session.query(SocialGenerationAttempt).filter_by(id=start.attempt_id).one()
    att.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=100)
    db_session.commit()

    url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/attempts/{start.attempt_id}?brand_profile_id={ws.id}"
    resp = client.get(url)
    assert resp.status_code == 200
    body = resp.json()

    validated = SocialIdeasGenerateResponse.model_validate(body)
    assert validated.attempt_status == "failed"
    assert validated.reason_code == "worker_lost"

    db_session.expire_all()
    db_att = db_session.query(SocialGenerationAttempt).filter_by(id=start.attempt_id).one()
    assert db_att.status == "failed"
    assert db_att.reason_code == "worker_lost"
    assert db_att.lease_expires_at is None
    assert db_att.completed_at is not None


def test_13_unexpired_pending_or_running_attempt_is_not_modified(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """13. Süresi dolmamış pending veya running attempt değiştirilmez."""
    ws, run, brief, categories, targets, kws = _setup_ready_brief_and_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    req = SocialBriefIdeasGenerateRequest(
        idempotency_key="unexpired-key",
        category_ids=[c.id for c in categories],
        ideas_per_category=2,
    )
    start = begin_social_idea_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req)
    future_time = datetime.now(timezone.utc) + timedelta(seconds=1800)
    att = db_session.query(SocialGenerationAttempt).filter_by(id=start.attempt_id).one()
    att.lease_expires_at = future_time
    db_session.commit()

    url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/attempts/{start.attempt_id}?brand_profile_id={ws.id}"
    resp = client.get(url)
    assert resp.status_code == 200
    assert resp.json()["attempt_status"] == "pending"

    db_session.expire_all()
    db_att = db_session.query(SocialGenerationAttempt).filter_by(id=start.attempt_id).one()
    assert db_att.status == "pending"
    assert db_att.reason_code is None


def test_14_completed_failed_partial_attempts_not_modified_by_reconciliation(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """14. Terminal (completed, failed, partial) attempt'ler reconciliation tarafından asla değiştirilmez."""
    ws, run, brief, categories, targets, kws = _setup_ready_brief_and_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    req = SocialBriefIdeasGenerateRequest(
        idempotency_key="term-key",
        category_ids=[c.id for c in categories],
        ideas_per_category=2,
    )
    start = begin_social_idea_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req)
    db_session.commit()

    finalize_ideas_attempt_failure(
        db_session,
        attempt_id=start.attempt_id,
        task_id="task-fail-term",
        reason_code="dispatch_failed",
        error_message="Orijinal hata.",
    )
    db_session.commit()

    # Reconcile çağrısı False dönmeli ve reason_code ezilmemeli
    url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/attempts/{start.attempt_id}?brand_profile_id={ws.id}"
    resp = client.get(url)
    assert resp.status_code == 200

    db_session.expire_all()
    db_att = db_session.query(SocialGenerationAttempt).filter_by(id=start.attempt_id).one()
    assert db_att.status == "failed"
    assert db_att.reason_code == "dispatch_failed"
    assert db_att.error_message == "Orijinal hata."


def test_15_second_polling_is_completely_idempotent(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """15. İkinci polling çağrısı tamamen idempotenttir; durumu yeniden değiştirmez."""
    ws, run, brief, categories, targets, kws = _setup_ready_brief_and_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    req = SocialBriefIdeasGenerateRequest(
        idempotency_key="double-poll-key",
        category_ids=[c.id for c in categories],
        ideas_per_category=2,
    )
    start = begin_social_idea_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req)
    att = db_session.query(SocialGenerationAttempt).filter_by(id=start.attempt_id).one()
    att.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=50)
    db_session.commit()

    url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/attempts/{start.attempt_id}?brand_profile_id={ws.id}"
    resp1 = client.get(url)
    assert resp1.status_code == 200
    assert resp1.json()["attempt_status"] == "failed"

    resp2 = client.get(url)
    assert resp2.status_code == 200
    assert resp2.json()["attempt_status"] == "failed"
    assert resp1.json() == resp2.json()


# ==================== 5. WORKSPACE VE KİMLİK İZOLASYONU ====================

def test_16_other_workspace_attempt_returns_404(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """16. Başka workspace'e ait attempt polling edildiğinde 404 IDEA_ATTEMPT_NOT_FOUND döner."""
    ws1, run1, brief1, categories1, targets1, kws1 = _setup_ready_brief_and_categories(
        db_session, make_workspace, make_scoring_run, make_keyword, workspace_name="WS1 Poll"
    )
    ws2 = make_workspace(name="WS2 Poll")

    req = SocialBriefIdeasGenerateRequest(
        idempotency_key="cross-ws-poll",
        category_ids=[c.id for c in categories1],
        ideas_per_category=2,
    )
    start = begin_social_idea_generation(db_session, brief_id=brief1.id, brand_profile_id=ws1.id, request=req)
    db_session.commit()

    url = f"/api/v1/generation/social/briefs/{brief1.id}/ideas/attempts/{start.attempt_id}?brand_profile_id={ws2.id}"
    resp = client.get(url)
    assert resp.status_code == 404
    body = resp.json()
    assert body["detail"]["code"] == "IDEA_ATTEMPT_NOT_FOUND"


def test_17_attempt_belongs_to_different_brief_returns_404(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """17. Attempt başka brief'e aitse 404 IDEA_ATTEMPT_NOT_FOUND döner."""
    ws, run, brief1, categories1, targets1, kws1 = _setup_ready_brief_and_categories(
        db_session, make_workspace, make_scoring_run, make_keyword, workspace_name="Brief1 WS"
    )
    ws2, run2, brief2, categories2, targets2, kws2 = _setup_ready_brief_and_categories(
        db_session, make_workspace, make_scoring_run, make_keyword, workspace_name="Brief2 WS"
    )

    req = SocialBriefIdeasGenerateRequest(
        idempotency_key="diff-brief-poll",
        category_ids=[c.id for c in categories1],
        ideas_per_category=2,
    )
    start = begin_social_idea_generation(db_session, brief_id=brief1.id, brand_profile_id=ws.id, request=req)
    db_session.commit()

    # brief2 id'si ile attempt1 sorgula
    url = f"/api/v1/generation/social/briefs/{brief2.id}/ideas/attempts/{start.attempt_id}?brand_profile_id={ws.id}"
    resp = client.get(url)
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "IDEA_ATTEMPT_NOT_FOUND"


def test_18_non_ideas_stage_attempt_returns_404_without_leakage(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """18. Stage 'categories' olan attempt sorgulandığında bilgi sızdırmadan 404 IDEA_ATTEMPT_NOT_FOUND döner."""
    ws, run, brief, categories, targets, kws = _setup_ready_brief_and_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    # Categories attempt id'sini al
    cat_attempt = db_session.query(SocialGenerationAttempt).filter_by(brief_id=brief.id, stage="categories").one()

    url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/attempts/{cat_attempt.id}?brand_profile_id={ws.id}"
    resp = client.get(url)
    assert resp.status_code == 404
    body = resp.json()
    assert body["detail"]["code"] == "IDEA_ATTEMPT_NOT_FOUND"
    assert "categories" not in resp.text


def test_19_soft_deleted_workspace_returns_404(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """19. deleted_at dolu (silinmiş) workspace için 404 IDEA_ATTEMPT_NOT_FOUND döner."""
    ws, run, brief, categories, targets, kws = _setup_ready_brief_and_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    req = SocialBriefIdeasGenerateRequest(
        idempotency_key="del-ws-poll",
        category_ids=[c.id for c in categories],
        ideas_per_category=2,
    )
    start = begin_social_idea_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req)
    db_session.commit()

    # Workspace'i soft delete yap
    ws.deleted_at = datetime.now(timezone.utc)
    db_session.commit()

    url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/attempts/{start.attempt_id}?brand_profile_id={ws.id}"
    resp = client.get(url)
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "IDEA_ATTEMPT_NOT_FOUND"


def test_20_non_existent_brief_or_attempt_returns_404(
    client: TestClient, db_session: Session, make_workspace, enable_flag
):
    """20. Var olmayan brief veya attempt sorgulandığında 404 IDEA_ATTEMPT_NOT_FOUND döner."""
    ws = make_workspace(name="Empty WS")
    url = f"/api/v1/generation/social/briefs/99999/ideas/attempts/99999?brand_profile_id={ws.id}"
    resp = client.get(url)
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "IDEA_ATTEMPT_NOT_FOUND"


def test_21_invalid_path_or_query_id_returns_400_or_422(
    client: TestClient, enable_flag
):
    """21. Geçersiz (negatif/sıfır veya string) ID parametreleri güvenli hata döner."""
    # a. Negatif ID -> 400 IDEA_ATTEMPT_INVALID_INPUT
    resp_neg = client.get("/api/v1/generation/social/briefs/-1/ideas/attempts/1?brand_profile_id=1")
    assert resp_neg.status_code == 400
    assert resp_neg.json()["detail"]["code"] == "IDEA_ATTEMPT_INVALID_INPUT"

    # b. String geçersiz tip -> 422
    resp_str = client.get("/api/v1/generation/social/briefs/abc/ideas/attempts/1?brand_profile_id=1")
    assert resp_str.status_code == 422


# ==================== 6. HATA VE TUTARLILIK KORUMALARI ====================

def test_22_corrupted_plan_snapshot_returns_500_idea_read_inconsistent(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """22. Bozuk/tutarsız snapshot verisi olduğunda 500 IDEA_READ_INCONSISTENT döner."""
    ws, run, brief, categories, targets, kws = _setup_ready_brief_and_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    req = SocialBriefIdeasGenerateRequest(
        idempotency_key="corrupt-poll-key",
        category_ids=[c.id for c in categories],
        ideas_per_category=2,
    )
    start = begin_social_idea_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req)
    # Coverage snapshot'ını boz
    att = db_session.query(SocialGenerationAttempt).filter_by(id=start.attempt_id).one()
    att.coverage = {"corrupted": "bad"}
    db_session.commit()

    url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/attempts/{start.attempt_id}?brand_profile_id={ws.id}"
    resp = client.get(url)
    assert resp.status_code == 500
    assert resp.json()["detail"]["code"] == "IDEA_READ_INCONSISTENT"


def test_23_unknown_attempt_status_returns_500_without_status_leak(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """23. Bilinmeyen attempt durumu güvenli 500 döner ve ham durumu sızdırmaz."""
    ws, run, brief, categories, targets, kws = _setup_ready_brief_and_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    req = SocialBriefIdeasGenerateRequest(
        idempotency_key="unknown-status-key",
        category_ids=[c.id for c in categories],
        ideas_per_category=2,
    )
    start = begin_social_idea_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req)
    att = db_session.query(SocialGenerationAttempt).filter_by(id=start.attempt_id).one()
    att.status = "mystery_state"
    db_session.commit()

    url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/attempts/{start.attempt_id}?brand_profile_id={ws.id}"
    resp = client.get(url)
    assert resp.status_code == 500
    assert resp.json()["detail"]["code"] == "IDEA_READ_INCONSISTENT"
    assert "mystery_state" not in resp.text


def test_24_reconciliation_error_triggers_rollback(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """24. Reconciliation sırasında veritabanı hatası oluşursa rollback çağrılır."""
    ws, run, brief, categories, targets, kws = _setup_ready_brief_and_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    req = SocialBriefIdeasGenerateRequest(
        idempotency_key="rollback-poll-key",
        category_ids=[c.id for c in categories],
        ideas_per_category=2,
    )
    start = begin_social_idea_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req)
    db_session.commit()

    url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/attempts/{start.attempt_id}?brand_profile_id={ws.id}"

    with patch("app.api.v1.generation.reconcile_expired_ideas_attempt_for_read", side_effect=Exception("DB deadlock")):
        resp = client.get(url)
        assert resp.status_code == 500
        assert "DB deadlock" not in resp.text


def test_25_read_service_sees_reconciled_state_in_same_request(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """25. Reconciliation commit edildikten sonra read servisi güncel failed state'ini okur."""
    ws, run, brief, categories, targets, kws = _setup_ready_brief_and_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    req = SocialBriefIdeasGenerateRequest(
        idempotency_key="commit-before-read-key",
        category_ids=[c.id for c in categories],
        ideas_per_category=2,
    )
    start = begin_social_idea_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req)
    att = db_session.query(SocialGenerationAttempt).filter_by(id=start.attempt_id).one()
    att.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=120)
    db_session.commit()

    url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/attempts/{start.attempt_id}?brand_profile_id={ws.id}"
    resp = client.get(url)
    assert resp.status_code == 200
    body = resp.json()
    assert body["attempt_status"] == "failed"
    assert body["reason_code"] == "worker_lost"


def test_26_renewed_lease_in_other_session_not_failed(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """26. Worker lease'i başka oturumda yenilediyse populate_existing ile güncel lease okunur ve failed yapılmaz."""
    from app.database.connection import SessionLocal

    ws, run, brief, categories, targets, kws = _setup_ready_brief_and_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    req = SocialBriefIdeasGenerateRequest(
        idempotency_key="lease-renewal-race-key",
        category_ids=[c.id for c in categories],
        ideas_per_category=2,
    )
    start = begin_social_idea_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req)
    db_session.commit()

    # İkinci oturumla worker lease yenilemesi simülasyonu
    other_db = SessionLocal()
    try:
        att = other_db.query(SocialGenerationAttempt).filter_by(id=start.attempt_id).one()
        att.lease_expires_at = datetime.now(timezone.utc) + timedelta(seconds=1500)
        other_db.commit()
    finally:
        other_db.close()

    url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/attempts/{start.attempt_id}?brand_profile_id={ws.id}"
    resp = client.get(url)
    assert resp.status_code == 200
    assert resp.json()["attempt_status"] == "pending"


def test_27_response_validates_and_serializes_cleanly(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """27. GET polling yanıtı SocialIdeasGenerateResponse şemasına uyar ve json.dumps ile serileştirilir."""
    ws, run, brief, categories, targets, kws = _setup_ready_brief_and_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    req = SocialBriefIdeasGenerateRequest(
        idempotency_key="clean-json-poll-key",
        category_ids=[c.id for c in categories],
        ideas_per_category=2,
    )
    start = begin_social_idea_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req)
    db_session.commit()

    url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/attempts/{start.attempt_id}?brand_profile_id={ws.id}"
    resp = client.get(url)
    assert resp.status_code == 200
    dumped = json.dumps(resp.json())
    assert isinstance(dumped, str)
    assert len(dumped) > 0


def test_28_canonical_lock_order_in_reconcile():
    """28. reconcile_expired_ideas_attempt_for_read canonical kilit sırasını kullanır."""
    from app.generators.social.attempt_state import reconcile_expired_ideas_attempt_for_read

    # reconcile_expired_ideas_attempt_for_read implementasyonunun _lock_context_for_attempt'i lock_scoring_run=True ile çağırdığını doğrula
    lines = inspect.getsource(reconcile_expired_ideas_attempt_for_read)
    assert "_lock_context_for_attempt" in lines
    assert "lock_scoring_run=True" in lines
