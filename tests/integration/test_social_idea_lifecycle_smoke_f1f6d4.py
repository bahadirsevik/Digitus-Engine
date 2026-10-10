# -*- coding: utf-8 -*-
"""MİKRO FAZ F1-F.6d.4 — Uçtan Uca Fikir Üretim Yaşam Döngüsü Smoke Testi.

Bu test, birbirinden bağımsız geliştirilmiş mikro faz bileşenlerinin gerçek uygulama sınırları
üzerinden uçtan uca birlikte çalıştığını kanıtlar:
1. POST /api/v1/generation/social/briefs/{brief_id}/ideas/generate
2. Celery Worker Wrapper (generation.social_brief_ideas / social_brief_ideas_task)
3. Transaction-safe Orkestrasyon ve Fikir Persistence (run_social_idea_generation + persist_social_ideas)
4. GET /api/v1/generation/social/briefs/{brief_id}/ideas/attempts/{attempt_id} Polling
5. Idempotent İkinci GET Polling
6. Idempotent Completed POST Replay

Dış Sınırlar:
- Yalnızca harici Celery broker (social_brief_ideas_task.apply_async) ve AI sağlayıcısı (get_ai_service) mocklanır.
- Orkestrasyon, persistence, preflight, heartbeat, lease reconciliation ve read katmanları gerçek kodlarıyla çalışır.
- Gerçek PostgreSQL test veritabanı kullanılır (docker-compose.test.yml).
- temperature=None sözleşmesi korunur.
- TaskResult oluşturulmaz.
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
    TaskResult,
)
from app.schemas.social_brief import (
    SocialBriefIdeasGenerateRequest,
    SocialIdeasGenerateResponse,
)
from app.tasks.generation_tasks import social_brief_ideas_task

T0 = datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc)


# ==================== FAKE AI SERVICE ====================

class SmartMockAIService:
    """Prompt içindeki INPUT_JSON verisine ve kotalara tam uyan deterministik sahte AI servisi."""

    def __init__(self) -> None:
        self.call_count: int = 0
        self.calls: list[dict[str, Any]] = []
        self.collector = None

    def for_stage(self, stage: str, **overrides) -> SmartMockAIService:
        return self

    def complete_json(self, prompt: str, **kwargs) -> str:
        self.call_count += 1
        self.calls.append({"prompt": prompt, "kwargs": kwargs})

        start_tag = "<INPUT_JSON>\n"
        end_tag = "\n</INPUT_JSON>"
        start = prompt.find(start_tag) + len(start_tag)
        end = prompt.find(end_tag)
        data = json.loads(prompt[start:end])

        kw_id = data["keywords"][0]["id"]
        ideas = []
        idx = 1
        for spec in data["target_specs"]:
            for _ in range(spec["requested_count"]):
                ideas.append({
                    "target_id": spec["target_id"],
                    "primary_keyword_id": kw_id,
                    "idea_title": f"Fikir Başlığı {idx} - {spec['platform']}",
                    "idea_description": f"Stratejik açıklama ve format gerekçesi metni {idx}.",
                    "target_platform": spec["platform"],
                    "content_format": spec["content_format"],
                    "trend_alignment": 0.85,
                })
                idx += 1
        return json.dumps({"ideas": ideas}, ensure_ascii=False)

    def close(self) -> None:
        pass


# ==================== FIXTURES ====================

@pytest.fixture
def enable_flag(monkeypatch):
    """Test süresince feature flag'i aktif kılar."""
    monkeypatch.setattr(settings, "ENABLE_SOCIAL_BRIEF_FLOW", True)
    yield


# ==================== ENVIRONMENT BUILDERS ====================

def _setup_smoke_environment(
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
):
    """Smoke testi için tam kilitli brief, kategoriler ve hedefleri hazırlar."""
    workspace = make_workspace(name="Smoke Lifecycle Brand", status="confirmed")
    run = make_scoring_run(
        brand_profile_id=workspace.id,
        status="completed",
        algorithm_version="v3",
        channel_pool_policy_version=1,
        relevance_anchor_version=1,
        channel_assignment_version=1,
        skip_relevance=True,
    )

    kws = []
    for i in range(2):
        kw = make_keyword(
            text_value=f"smoke keyword {i + 1}",
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
        brand_name_snapshot="Smoke Brand Snapshot",
        brand_context_snapshot="Smoke Context Snapshot",
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
        idempotency_key=f"cat-smoke-key-{uuid.uuid4()}",
        status="completed",
        heartbeat_at=T0,
        lease_expires_at=T0 + timedelta(seconds=1500),
        created_at=T0,
    )
    db_session.add(cat_attempt)
    db_session.flush()

    # En az 2 SocialCategory
    categories = []
    for i in range(2):
        cat = SocialCategory(
            scoring_run_id=run.id,
            brief_id=brief.id,
            category_name=f"Eğitici Seri {i + 1}",
            category_type="educational",
            description=f"Eğitici içerik serisi açıklaması {i + 1}.",
            is_stale=False,
            relevance_score=0.92,
            suggested_keyword_ids=[kws[0].id],
        )
        db_session.add(cat)
        categories.append(cat)

    db_session.commit()
    for cat in categories:
        db_session.refresh(cat)
    for t in targets:
        db_session.refresh(t)

    return workspace, run, brief, categories, targets, kws


# ==================== UÇTAN UCA SMOKE TESTİ ====================

def test_social_idea_full_lifecycle_smoke(
    client: TestClient,
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
    enable_flag,
):
    """Uçtan uca fikir üretim yaşam döngüsü smoke testi:
    1. POST Generate -> 202 Accepted, pending attempt, Celery dispatch yakalama.
    2. Celery Worker (eager apply) -> AI çağrısı, heartbeat, atomik persistence, completed attempt.
    3. GET Polling -> 200 OK, completed attempt, doğrulanmış fikirler, coverage tam.
    4. İkinci GET Polling -> 200 OK, tam idempotency, 0 DB mutasyonu.
    5. İkinci POST Replay -> 200 OK completed replay, 0 Celery dispatch, 0 yeni satır.
    """
    # 1. Hazır Domain Verisini Kur
    workspace, run, brief, categories, targets, kws = _setup_smoke_environment(
        db_session, make_workspace, make_scoring_run, make_keyword
    )

    initial_idea_count = db_session.query(SocialIdea).filter(SocialIdea.brief_id == brief.id).count()
    assert initial_idea_count == 0

    idempotency_key = f"smoke-key-{uuid.uuid4()}"
    category_ids = [c.id for c in categories]
    ideas_per_category = 2

    # 2. POST Generate Çağrısı (Celery apply_async yakalanır)
    dispatches = []

    def _capture_apply_async(*args, **kwargs):
        dispatches.append({"args": kwargs.get("args"), "task_id": kwargs.get("task_id")})
        return MagicMock(id=kwargs.get("task_id"))

    with patch("app.api.v1.generation.social_brief_ideas_task.apply_async", side_effect=_capture_apply_async):
        post_url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/generate?brand_profile_id={workspace.id}"
        post_resp = client.post(
            post_url,
            json={
                "idempotency_key": idempotency_key,
                "category_ids": category_ids,
                "ideas_per_category": ideas_per_category,
            },
        )

    # 3. POST Sonucunu Doğrula
    assert post_resp.status_code == 202
    post_body = post_resp.json()
    post_validated = SocialIdeasGenerateResponse.model_validate(post_body)

    assert post_validated.attempt_status == "pending"
    assert post_validated.brief_id == brief.id
    assert post_validated.scoring_run_id == run.id
    assert post_validated.replayed is False
    assert post_validated.total_ideas == 0
    assert post_validated.ideas == []
    assert post_validated.reason_code is None
    assert "task_id" not in post_body

    attempt_id = post_validated.attempt_id
    assert attempt_id > 0

    # Dispatch doğrulaması
    assert len(dispatches) == 1
    assert dispatches[0]["args"] == [attempt_id]
    task_id = dispatches[0]["task_id"]
    assert isinstance(task_id, str) and len(task_id) > 0
    # Geçerli UUID formatı
    uuid.UUID(task_id)

    # DB durumu: attempt pending, task_id henüz yazılmamış (None), TaskResult oluşturulmamış
    db_session.expire_all()
    attempt = db_session.query(SocialGenerationAttempt).filter_by(id=attempt_id).one()
    assert attempt.status == "pending"
    assert attempt.task_id is None
    assert db_session.query(TaskResult).count() == 0

    # 4. Celery Worker Task'ını Senkron / Eager Çalıştır
    fake_ai = SmartMockAIService()
    with patch("app.generators.ai_service.get_ai_service", return_value=fake_ai):
        worker_res = social_brief_ideas_task.apply(args=[attempt_id], task_id=task_id).get()

    assert worker_res["status"] == "completed"
    assert worker_res["attempt_id"] == attempt_id
    assert worker_res["brief_id"] == brief.id
    assert worker_res["scoring_run_id"] == run.id
    assert worker_res["replayed"] is False
    assert worker_res["total_ideas"] > 0
    assert worker_res["ai_calls_used"] == len(categories)  # 2 kategori = 2 AI çağrısı

    # temperature=None sözleşmesinin korunduğunu doğrula
    assert len(fake_ai.calls) == len(categories)
    for call in fake_ai.calls:
        assert call["kwargs"]["temperature"] is None
        assert call["kwargs"]["max_tokens"] == 4000
        assert call["kwargs"]["response_schema"] is not None

    # Worker sonrası DB durumu: attempt completed, task_id kaydedilmiş, fikir satırları persist edilmiş
    db_session.expire_all()
    attempt_after_worker = db_session.query(SocialGenerationAttempt).filter_by(id=attempt_id).one()
    assert attempt_after_worker.status == "completed"
    assert attempt_after_worker.task_id == task_id
    assert attempt_after_worker.completed_at is not None
    assert attempt_after_worker.lease_expires_at is None

    persisted_ideas = db_session.query(SocialIdea).filter_by(brief_id=brief.id).all()
    expected_total_ideas = len(categories) * len(targets)  # 2 kat * 2 targets (ideas_per_category=2)
    assert len(persisted_ideas) == expected_total_ideas
    assert db_session.query(TaskResult).count() == 0

    # 5. GET Polling Çağrısı ve Nihai Sonuç Doğrulaması
    get_url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/attempts/{attempt_id}?brand_profile_id={workspace.id}"
    get_resp = client.get(get_url)
    assert get_resp.status_code == 200
    get_body = get_resp.json()
    get_validated = SocialIdeasGenerateResponse.model_validate(get_body)

    assert get_validated.attempt_status == "completed"
    assert get_validated.reason_code is None
    assert get_validated.replayed is True
    assert get_validated.total_ideas == expected_total_ideas
    assert len(get_validated.ideas) == expected_total_ideas
    assert get_validated.warnings == []

    # Her seçili kategori ve target en az 1 fikir almış olmalı
    idea_cat_ids = {i.category_id for i in get_validated.ideas}
    assert idea_cat_ids == set(category_ids)

    idea_target_ids = {i.brief_target_id for i in get_validated.ideas}
    target_ids = {t.id for t in targets}
    assert idea_target_ids == target_ids

    # Her fikir yalnızca brief'e ait öğeleri kullanır ve platform/format target ile uyumludur
    brief_kw_ids = {k.id for k in kws}
    target_map = {t.id: t for t in targets}
    for idea in get_validated.ideas:
        assert idea.brief_id == brief.id
        assert idea.keyword_id in brief_kw_ids
        assert idea.brief_target_id in target_map
        t = target_map[idea.brief_target_id]
        assert idea.target_platform == t.platform
        assert idea.content_format == t.content_format
        assert idea.is_stale is False
        assert 0.0 <= idea.trend_alignment <= 1.0

    # Coverage satırlarında missing == 0, accepted == requested
    assert len(get_validated.coverage) == len(targets)
    for cov in get_validated.coverage:
        assert cov.missing == 0
        assert cov.accepted == cov.requested
        assert cov.accepted == len(categories)  # Her kategori için 1 fikir = 2 accepted

    # 6. İkinci GET Polling Çağrısı (Tam İdempotency & Sıfır Mutasyon)
    get_resp2 = client.get(get_url)
    assert get_resp2.status_code == 200
    assert get_resp2.json() == get_body

    db_session.expire_all()
    assert db_session.query(SocialIdea).filter_by(brief_id=brief.id).count() == expected_total_ideas
    assert db_session.query(SocialGenerationAttempt).filter_by(brief_id=brief.id, stage="ideas").count() == 1
    assert db_session.query(TaskResult).count() == 0

    # 7. Aynı İdempotency Key ile İkinci POST (Completed Replay)
    with patch("app.api.v1.generation.social_brief_ideas_task.apply_async") as mock_apply2:
        post_resp2 = client.post(
            post_url,
            json={
                "idempotency_key": idempotency_key,
                "category_ids": category_ids,
                "ideas_per_category": ideas_per_category,
            },
        )
        assert post_resp2.status_code == 200
        assert mock_apply2.call_count == 0  # Kesinlikle yeni Celery dispatch yapılmaz

    post_body2 = post_resp2.json()
    post_validated2 = SocialIdeasGenerateResponse.model_validate(post_body2)
    assert post_validated2.attempt_status == "completed"
    assert post_validated2.replayed is True
    assert post_validated2.total_ideas == expected_total_ideas
    assert len(post_validated2.ideas) == expected_total_ideas

    # DB kayıt sayıları kesinlikle artmamış olmalı
    db_session.expire_all()
    assert db_session.query(SocialIdea).filter_by(brief_id=brief.id).count() == expected_total_ideas
    assert db_session.query(SocialGenerationAttempt).filter_by(brief_id=brief.id, stage="ideas").count() == 1
    assert db_session.query(TaskResult).count() == 0
