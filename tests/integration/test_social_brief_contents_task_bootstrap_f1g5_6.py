# -*- coding: utf-8 -*-
"""MİKRO FAZ F1-G.5.6 — Social Brief Contents Celery Worker Entegrasyon Testleri.

Tüm testler gerçek PostgreSQL test veritabanını ve SessionLocal oturum fabrikasını kullanır.
Gerçek broker, Celery worker veya harici AI kullanılmaz; AI ve collector mock'lar ile izole edilir.

Test edilen sözleşmeler:
1. _load_contents_attempt_telemetry_scope gerçek veritabanından scoring_run_id ve brand_profile_id'yi doğru okur.
2. _finalize_social_contents_bootstrap_failure pending attempt'i atomik olarak failed yapar.
3. _finalize_social_contents_bootstrap_failure aynı task_id'ye sahip running attempt'i failed yapar.
4. _finalize_social_contents_bootstrap_failure farklı task_id'ye ait running attempt'i değiştirmez (korur).
5. _finalize_social_contents_bootstrap_failure tamamlanmış (completed) attempt'i ezmez.
6. social_brief_contents_task uçtan uca başarıyla çalışır, içerik DB'ye kaydedilir ve attempt completed olur.
7. AI init hatasında bootstrap failure tetiklenir ve DB'deki attempt failed durumuna geçer.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import pytest
from sqlalchemy.orm import Session

from app.config import settings
from app.database.connection import SessionLocal
from app.database.models import (
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
from app.tasks.generation_tasks import (
    _finalize_social_contents_bootstrap_failure,
    _load_contents_attempt_telemetry_scope,
    social_brief_contents_task,
)

T0 = datetime(2026, 9, 26, 12, 0, 0, tzinfo=timezone.utc)
SAFE_CONTENTS_ERROR_MSG = "Sosyal içerik worker hazırlığı tamamlanamadı."


@pytest.fixture
def enable_flag(monkeypatch):
    """Test süresince feature flag'i aktif kılar."""
    monkeypatch.setattr(settings, "ENABLE_SOCIAL_BRIEF_FLOW", True)
    yield


def _setup_contents_db_env(
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
    *,
    task_id: str = "task-c-boot-1",
    attempt_status: str = "pending",
) -> dict:
    ws = make_workspace(name=f"Brand {uuid.uuid4().hex[:6]}", status="confirmed")
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
        text_value="organik serum",
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

    brief = SocialBrief(
        scoring_run_id=run.id,
        brand_name_snapshot=ws.name,
        brand_context_snapshot="Doğal serum bakım",
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
        duration_preset_id=None,
    )
    db_session.add(target)
    db_session.flush()

    cat = SocialCategory(
        scoring_run_id=run.id,
        brief_id=brief.id,
        category_name="Cilt Bakımı",
        category_type="educational",
        description="Eğitici içerikler",
        is_stale=False,
        relevance_score=0.9,
        suggested_keyword_ids=[kw.id],
    )
    db_session.add(cat)
    db_session.flush()

    idea = SocialIdea(
        brief_id=brief.id,
        category_id=cat.id,
        keyword_id=kw.id,
        brief_target_id=target.id,
        idea_title="Serum Rehberi",
        idea_description="Doğal serum kullanımı ve bakım rehberi",
        target_platform="instagram",
        content_format="post",
        trend_alignment=0.88,
        is_stale=False,
    )
    db_session.add(idea)
    db_session.flush()

    coverage = {
        "schema_version": "contents_request_v1",
        "request": {
            "idea_ids": [idea.id],
            "product_facts": "Dermatolojik olarak test edilmiştir.",
            "trusted_brand_usp": "Organik serumlar",
        },
    }

    now = datetime.now(timezone.utc)
    attempt = SocialGenerationAttempt(
        brief_id=brief.id,
        stage="contents",
        idempotency_key=f"contents-{task_id}-{uuid.uuid4().hex[:6]}",
        status=attempt_status,
        task_id=task_id if attempt_status == "running" else None,
        heartbeat_at=now,
        lease_expires_at=now + timedelta(seconds=1500),
        requested_idea_ids=[idea.id],
        coverage=coverage,
        created_at=now,
    )
    db_session.add(attempt)
    db_session.commit()

    return {
        "workspace": ws,
        "run": run,
        "brief": brief,
        "target": target,
        "category": cat,
        "idea": idea,
        "attempt": attempt,
    }


def test_01_real_db_load_telemetry_scope(
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
    enable_flag,
):
    """1. _load_contents_attempt_telemetry_scope DB'den scoring_run_id ve brand_profile_id okur."""
    env = _setup_contents_db_env(db_session, make_workspace, make_scoring_run, make_keyword)
    attempt = env["attempt"]
    run = env["run"]
    ws = env["workspace"]

    scoring_run_id, brand_profile_id = _load_contents_attempt_telemetry_scope(db_session, attempt.id)
    assert scoring_run_id == run.id
    assert brand_profile_id == ws.id


def test_02_real_db_finalize_bootstrap_failure_pending_attempt(
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
    enable_flag,
):
    """2. _finalize_social_contents_bootstrap_failure pending attempt'i failed yapar."""
    env = _setup_contents_db_env(
        db_session, make_workspace, make_scoring_run, make_keyword,
        task_id="task-boot-pending-1",
        attempt_status="pending",
    )
    attempt = env["attempt"]

    _finalize_social_contents_bootstrap_failure(attempt_id=attempt.id, task_id="task-boot-pending-1")

    db_session.expire_all()
    refreshed = db_session.query(SocialGenerationAttempt).filter(SocialGenerationAttempt.id == attempt.id).one()
    assert refreshed.status == "failed"
    assert refreshed.reason_code == "worker_bootstrap_failed"
    assert refreshed.error_message == SAFE_CONTENTS_ERROR_MSG
    assert refreshed.lease_expires_at is None
    assert refreshed.completed_at is not None


def test_03_real_db_finalize_bootstrap_failure_running_attempt(
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
    enable_flag,
):
    """3. _finalize_social_contents_bootstrap_failure aynı worker running attempt'ini failed yapar."""
    task_id = "task-boot-running-owner"
    env = _setup_contents_db_env(
        db_session, make_workspace, make_scoring_run, make_keyword,
        task_id=task_id,
        attempt_status="running",
    )
    attempt = env["attempt"]

    _finalize_social_contents_bootstrap_failure(attempt_id=attempt.id, task_id=task_id)

    db_session.expire_all()
    refreshed = db_session.query(SocialGenerationAttempt).filter(SocialGenerationAttempt.id == attempt.id).one()
    assert refreshed.status == "failed"
    assert refreshed.reason_code == "worker_bootstrap_failed"


def test_04_real_db_finalize_bootstrap_failure_mismatched_task_id(
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
    enable_flag,
):
    """4. Farklı task_id sahibi running attempt'e dokunulmaz."""
    env = _setup_contents_db_env(
        db_session, make_workspace, make_scoring_run, make_keyword,
        task_id="owner-worker-123",
        attempt_status="running",
    )
    attempt = env["attempt"]

    _finalize_social_contents_bootstrap_failure(attempt_id=attempt.id, task_id="intruder-worker-456")

    db_session.expire_all()
    refreshed = db_session.query(SocialGenerationAttempt).filter(SocialGenerationAttempt.id == attempt.id).one()
    assert refreshed.status == "running"
    assert refreshed.task_id == "owner-worker-123"


def test_05_real_db_finalize_bootstrap_failure_completed_attempt(
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
    enable_flag,
):
    """5. Completed durumundaki attempt bootstrap failure tarafından değiştirilemez."""
    env = _setup_contents_db_env(
        db_session, make_workspace, make_scoring_run, make_keyword,
        task_id="task-boot-done",
        attempt_status="completed",
    )
    attempt = env["attempt"]

    _finalize_social_contents_bootstrap_failure(attempt_id=attempt.id, task_id="task-boot-done")

    db_session.expire_all()
    refreshed = db_session.query(SocialGenerationAttempt).filter(SocialGenerationAttempt.id == attempt.id).one()
    assert refreshed.status == "completed"


def test_06_real_db_task_execution_success(
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
    enable_flag,
):
    """6. social_brief_contents_task gerçek DB üzerinde başarıyla çalışır ve içerik yazar."""
    task_id = "task-full-success-100"
    env = _setup_contents_db_env(
        db_session, make_workspace, make_scoring_run, make_keyword,
        task_id=task_id,
        attempt_status="pending",
    )
    attempt = env["attempt"]
    idea = env["idea"]

    valid_response = {
        "hooks": [{"text": "Cildiniz için temiz içerikli temel adımlar", "style": "curiosity"}],
        "caption": "Doğal serum bakım rutini önerileri ve günlük uygulama rehberi.",
        "format_payload": None,
        "visual_suggestion": "Aydınlık ortam ve sade ürün fotoğrafı.",
        "video_concept": None,
        "cta_text": "Detaylar için profildeki linke tıklayın.",
        "hashtags": ["ciltbakimi", "organik", "serum", "dogal", "bakim"],
        "industry_posting_suggestion": "Akşam 19:00",
        "platform_notes": None,
    }

    class FakeAIService:
        def __init__(self, response_dict):
            self.response_dict = response_dict
            self.collector = None

        def for_stage(self, stage: str, **kwargs):
            return self

        def complete_json(self, prompt: str, **kwargs):
            return json.dumps(self.response_dict, ensure_ascii=False)

        def close(self):
            pass

    fake_ai = FakeAIService(valid_response)

    with patch("app.generators.ai_service.get_ai_service", return_value=fake_ai):
        res = social_brief_contents_task.apply(args=[attempt.id], task_id=task_id).get()

    assert res["status"] == "completed"
    assert res["persisted_idea_ids"] == [idea.id]
    assert res["unresolved_idea_ids"] == []
    assert res["replayed"] is False

    db_session.expire_all()
    saved_content = db_session.query(SocialContent).filter(SocialContent.idea_id == idea.id).first()
    assert saved_content is not None
    assert saved_content.caption == "Doğal serum bakım rutini önerileri ve günlük uygulama rehberi."

    refreshed_attempt = db_session.query(SocialGenerationAttempt).filter(SocialGenerationAttempt.id == attempt.id).one()
    assert refreshed_attempt.status == "completed"


def test_07_real_db_task_bootstrap_failure_on_ai_init(
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
    enable_flag,
):
    """7. AI init hatasında bootstrap failure tetiklenir ve DB'deki attempt failed olur."""
    task_id = "task-boot-ai-err"
    env = _setup_contents_db_env(
        db_session, make_workspace, make_scoring_run, make_keyword,
        task_id=task_id,
        attempt_status="pending",
    )
    attempt = env["attempt"]

    with patch("app.generators.ai_service.get_ai_service", side_effect=RuntimeError("Gemini client down")):
        with pytest.raises(RuntimeError, match="Gemini client down"):
            social_brief_contents_task.apply(args=[attempt.id], task_id=task_id).get()

    db_session.expire_all()
    refreshed_attempt = db_session.query(SocialGenerationAttempt).filter(SocialGenerationAttempt.id == attempt.id).one()
    assert refreshed_attempt.status == "failed"
    assert refreshed_attempt.reason_code == "worker_bootstrap_failed"
    assert refreshed_attempt.error_message == SAFE_CONTENTS_ERROR_MSG
