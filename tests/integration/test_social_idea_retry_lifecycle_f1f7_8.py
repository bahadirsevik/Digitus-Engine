# -*- coding: utf-8 -*-
"""MİKRO FAZ F1-F.7.8 — Uçtan Uca Fikir Tekrar Deneme (Retry) Yaşam Döngüsü ve Eşzamanlılık Testleri.

Bu test modülü, F1-F.7.8 kapsamında geliştirilen tüm bileşenlerin (API, Celery Task,
Orkestrasyon, Read, Persistence ve Finalize Lock) gerçek PostgreSQL test veritabanı
üzerinde uçtan uca birlikte çalıştığını ve eşzamanlılık garantilerini kanıtlar:

1. Başarılı Tam Retry Yaşam Döngüsü:
   - POST /api/v1/generation/social/briefs/{brief_id}/ideas/retry (202 Accepted, dispatch yakalama)
   - Celery Task (social_brief_ideas_retry_task eager apply, SmartMockAIService)
   - GET /api/v1/generation/social/briefs/{brief_id}/ideas/retry/attempts/{attempt_id} Polling (200 OK, completed)
   - İkinci GET Polling (idempotent 200 OK, 0 DB mutasyonu)
   - İkinci POST Replay (200 OK completed replay, 0 Celery dispatch, 0 yeni satır)
2. Kısmi Başarı (Partial) ve Takip Eden İkinci Retry:
   - Çoklu eksik hedefli brief
   - İlk kategoride başarı, sonraki kategoride AI hatası -> task partial olarak tamamlanır.
   - GET Polling -> 200 OK partial, warnings içinde unfilled target_id bulunur.
   - Yeni idempotency_key ile ikinci retry -> yalnız kalan eksik hedef planlanır ve tamamlanır.
   - Nihai GET Polling -> 200 OK completed, tüm hedefler dolu.
3. Eşzamanlı POST İstekleri (Concurrency Guard):
   - Aynı brief için eşzamanlı iki farklı POST isteği -> biri 202, diğeri 409 ATTEMPT_CONFLICT.
4. Eşzamanlı / Tekrarlanan Worker Çağrısı (Duplicate Idea Guard):
   - Tamamlanmış attempt üzerinde ikinci worker çağrısı -> already_completed=True, 0 yeni fikir yazımı.
"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import settings
from app.core.social.idea_planner import build_social_idea_generation_plan
from app.database.connection import SessionLocal
from app.database.models import (
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
from app.schemas.social_brief import SocialIdeasGenerateResponse
from app.tasks.generation_tasks import social_brief_ideas_retry_task

T0 = datetime(2026, 9, 25, 12, 0, 0, tzinfo=timezone.utc)


# ==================== SMART MOCK AI SERVICE ====================

class SmartMockAIService:
    """Prompt içindeki INPUT_JSON verisine ve kotalara tam uyan deterministik sahte AI servisi."""

    def __init__(self, fail_on_call: int | None = None) -> None:
        self.call_count: int = 0
        self.calls: list[dict[str, Any]] = []
        self.collector = None
        self.fail_on_call = fail_on_call

    def for_stage(self, stage: str, **overrides) -> SmartMockAIService:
        return self

    def complete_json(self, prompt: str, **kwargs) -> str:
        self.call_count += 1
        self.calls.append({"prompt": prompt, "kwargs": kwargs})

        if self.fail_on_call is not None and self.call_count == self.fail_on_call:
            raise RuntimeError(f"Simulated AI crash on call {self.call_count}")

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
                    "idea_title": f"Retry Fikir Başlığı {idx} - {spec['platform']}",
                    "idea_description": f"Stratejik retry açıklama ve format gerekçesi metni {idx}.",
                    "target_platform": spec["platform"],
                    "content_format": spec["content_format"],
                    "trend_alignment": 0.88,
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

def _setup_retry_lifecycle_environment(
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
    targets_data: list[tuple[str, str]] | None = None,
    num_categories: int = 2,
    filled_targets_count: int = 1,
    ideas_per_category: int | None = None,
    workspace_name: str = "Retry Lifecycle Brand",
):
    """Test için kilitli brief, kategoriler, hedefler ve kısmen doldurulmuş kaynak attempt hazırlar."""
    workspace = make_workspace(name=workspace_name, status="confirmed")
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
            text_value=f"retry kw {i + 1}",
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
        brand_name_snapshot="Retry Brand Snapshot",
        brand_context_snapshot="Retry Context Snapshot",
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

    if targets_data is None:
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
    db_session.flush()

    categories = []
    for i in range(num_categories):
        cat = SocialCategory(
            scoring_run_id=run.id,
            brief_id=brief.id,
            category_name=f"Retry Kategori {i + 1}",
            category_type="educational",
            description=f"Açıklama {i + 1}.",
            is_stale=False,
            relevance_score=0.90,
            suggested_keyword_ids=[kws[0].id],
        )
        db_session.add(cat)
        categories.append(cat)
    db_session.flush()

    eff_ideas_per_category = ideas_per_category if ideas_per_category is not None else len(targets)

    plan = build_social_idea_generation_plan(
        selected_category_ids=tuple(c.id for c in categories),
        target_ids=tuple(t.id for t in targets),
        ideas_per_category=eff_ideas_per_category,
    )

    persisted_target_ids = [t.id for t in targets[:filled_targets_count]]
    cov = {
        "schema_version": "ideas_plan_v1",
        "request": {
            "category_ids": [c.id for c in categories],
            "ideas_per_category": eff_ideas_per_category,
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
        idempotency_key=f"source-attempt-{uuid.uuid4()}",
        status="completed" if filled_targets_count == len(targets) else "partial",
        requested_target_ids=[t.id for t in targets],
        coverage=cov,
        warnings=[],
        heartbeat_at=T0,
        lease_expires_at=None,
        completed_at=T0 + timedelta(seconds=120),
    )
    db_session.add(source_attempt)
    db_session.flush()

    # Doldurulmuş hedefler için SocialIdea satırları ekle
    for t in targets[:filled_targets_count]:
        idea = SocialIdea(
            category_id=categories[0].id,
            keyword_id=kws[0].id,
            brief_id=brief.id,
            brief_target_id=t.id,
            idea_title=f"Önceden Doldurulmuş {t.platform}",
            idea_description="Bu hedef daha önceki denemede kaydedilmiştir.",
            target_platform=t.platform,
            content_format=t.content_format,
            trend_alignment=0.85,
            is_stale=False,
            is_selected=False,
            regeneration_count=0,
            created_at=T0,
        )
        db_session.add(idea)

    db_session.commit()
    for cat in categories:
        db_session.refresh(cat)
    for t in targets:
        db_session.refresh(t)
    db_session.refresh(brief)
    db_session.refresh(source_attempt)

    return workspace, run, brief, categories, targets, kws, source_attempt


# ==================== 1. TAM RETRY YAŞAM DÖNGÜSÜ SMOKE ====================

def test_01_full_retry_lifecycle_smoke(
    client: TestClient,
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
    enable_flag,
):
    """1. Uçtan uca fikir retry yaşam döngüsü smoke testi:
    - POST Retry -> 202 Accepted, pending attempt, Celery dispatch yakalama.
    - Celery Worker (eager apply) -> AI çağrısı, heartbeat, atomik persistence, completed attempt.
    - GET Polling -> 200 OK, completed attempt, tüm hedefler dolu, coverage tam.
    - İkinci GET Polling -> 200 OK, tam idempotency, 0 DB mutasyonu.
    - İkinci POST Replay -> 200 OK completed replay, 0 Celery dispatch, 0 yeni satır.
    """
    workspace, run, brief, categories, targets, kws, source_attempt = _setup_retry_lifecycle_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, filled_targets_count=1
    )

    initial_idea_count = db_session.query(SocialIdea).filter(SocialIdea.brief_id == brief.id).count()
    assert initial_idea_count == 1  # 1 hedef önceden dolu, 1 hedef eksik

    idempotency_key = f"retry-smoke-key-{uuid.uuid4()}"

    # 1. POST Retry Çağrısı (Celery apply_async yakalanır)
    dispatches = []

    def _capture_apply_async(*args, **kwargs):
        dispatches.append({"args": kwargs.get("args"), "task_id": kwargs.get("task_id")})
        return MagicMock(id=kwargs.get("task_id"))

    with patch("app.api.v1.generation.social_brief_ideas_retry_task.apply_async", side_effect=_capture_apply_async):
        post_url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/retry?brand_profile_id={workspace.id}"
        post_resp = client.post(
            post_url,
            json={
                "idempotency_key": idempotency_key,
                "source_attempt_id": source_attempt.id,
            },
        )

    assert post_resp.status_code == 202
    post_body = post_resp.json()
    post_validated = SocialIdeasGenerateResponse.model_validate(post_body)

    assert post_validated.attempt_status == "pending"
    assert post_validated.brief_id == brief.id
    assert post_validated.scoring_run_id == run.id
    assert post_validated.replayed is False
    assert len(post_validated.ideas) == 1  # Mevcut non-stale fikir listelenir
    assert post_validated.reason_code is None

    attempt_id = post_validated.attempt_id
    assert attempt_id > 0

    assert len(dispatches) == 1
    assert dispatches[0]["args"] == [attempt_id]
    task_id = dispatches[0]["task_id"]
    assert isinstance(task_id, str) and len(task_id) > 0
    uuid.UUID(task_id)

    # 2. Celery Worker Task'ını Senkron / Eager Çalıştır
    fake_ai = SmartMockAIService()
    with patch("app.generators.ai_service.get_ai_service", return_value=fake_ai):
        worker_res = social_brief_ideas_retry_task.apply(args=[attempt_id], task_id=task_id).get()

    assert worker_res["status"] == "completed"
    assert worker_res["attempt_id"] == attempt_id
    assert worker_res["newly_persisted_count"] == 1  # Yalnız eksik 1 hedef için yeni fikir yazıldı
    assert worker_res["accepted_target_ids"] == [targets[1].id]
    assert worker_res["unfilled_target_ids"] == []

    # temperature=None sözleşmesinin korunduğunu doğrula
    assert len(fake_ai.calls) >= 1
    for call in fake_ai.calls:
        assert call["kwargs"]["temperature"] is None

    # 3. GET Polling Çağrısı ve Nihai Sonuç Doğrulaması
    get_url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/retry/attempts/{attempt_id}?brand_profile_id={workspace.id}"
    get_resp = client.get(get_url)
    assert get_resp.status_code == 200
    get_body = get_resp.json()
    get_validated = SocialIdeasGenerateResponse.model_validate(get_body)

    assert get_validated.attempt_status == "completed"
    assert get_validated.reason_code is None
    assert get_validated.replayed is True
    assert get_validated.total_ideas == 2  # Toplam 2 hedefin her ikisi de tamamlandı
    assert len(get_validated.ideas) == 2
    assert get_validated.warnings == []

    # 4. İkinci GET Polling (Tam Idempotency)
    get_resp_2 = client.get(get_url)
    assert get_resp_2.status_code == 200
    assert get_resp_2.json() == get_body

    # 5. İkinci POST Replay (Completed Replay, 0 Celery dispatch, 0 yeni satır)
    with patch("app.api.v1.generation.social_brief_ideas_retry_task.apply_async") as mock_retry_apply:
        replay_resp = client.post(
            post_url,
            json={
                "idempotency_key": idempotency_key,
                "source_attempt_id": source_attempt.id,
            },
        )
        assert replay_resp.status_code == 200
        replay_body = replay_resp.json()
        assert replay_body["attempt_status"] == "completed"
        assert replay_body["replayed"] is True
        assert len(replay_body["ideas"]) == 2
        mock_retry_apply.assert_not_called()

    # DB'de toplam tam 2 SocialIdea bulunmalıdır (asla fazlası değil)
    total_ideas_in_db = db_session.query(SocialIdea).filter(SocialIdea.brief_id == brief.id).count()
    assert total_ideas_in_db == 2
    assert db_session.query(TaskResult).count() == 0


# ==================== 2. KISMİ BAŞARI (PARTIAL) VE TAKİP EDEN RETRY ====================

def test_02_partial_retry_lifecycle_and_followup_retry(
    client: TestClient,
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
    enable_flag,
):
    """2. Kısmi başarı (partial) sonrasında yeni retry ile kalan hedefin tamamlanması:
    - 2 hedeften ikisi de başlangıçta eksik (ideas_per_category=1 ile 2 kategoriye dağıtılmış).
    - İlk kategori AI çağrısı başarılı olur, ikinci kategori AI çağrısı hata verir.
    - İlk retry attempt'i partial olarak finalize edilir.
    - İkinci retry yeni idempotency_key ile başlatılır ve yalnız kalan son hedefi planlar.
    - İkinci retry başarılı olur ve tüm hedefler completed haline gelir.
    """
    targets_data = [("instagram", "post"), ("twitter", "thread")]
    workspace, run, brief, categories, targets, kws, source_attempt = _setup_retry_lifecycle_environment(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        targets_data=targets_data,
        num_categories=2,
        filled_targets_count=0,
        ideas_per_category=1,
    )

    post_url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/retry?brand_profile_id={workspace.id}"

    # 1. İlk Retry Başlat
    dispatches = []

    def _capture_apply_async(*args, **kwargs):
        dispatches.append({"args": kwargs.get("args"), "task_id": kwargs.get("task_id")})
        return MagicMock(id=kwargs.get("task_id"))

    with patch("app.api.v1.generation.social_brief_ideas_retry_task.apply_async", side_effect=_capture_apply_async):
        resp1 = client.post(
            post_url,
            json={"idempotency_key": "retry-partial-1", "source_attempt_id": source_attempt.id},
        )
    assert resp1.status_code == 202
    attempt_id_1 = resp1.json()["attempt_id"]
    task_id_1 = dispatches[0]["task_id"]

    # 2. Worker: İlk çağrı başarılı (Target 0), ikinci çağrı fail (Target 1)
    mock_ai_fail = SmartMockAIService(fail_on_call=2)
    with patch("app.generators.ai_service.get_ai_service", return_value=mock_ai_fail):
        worker_res_1 = social_brief_ideas_retry_task.apply(args=[attempt_id_1], task_id=task_id_1).get()

    assert worker_res_1["status"] == "partial"
    assert worker_res_1["newly_persisted_count"] == 1
    assert worker_res_1["accepted_target_ids"] == [targets[0].id]
    assert worker_res_1["unfilled_target_ids"] == [targets[1].id]

    # 3. GET Polling ile Partial Durumunu Doğrula
    get_url_1 = f"/api/v1/generation/social/briefs/{brief.id}/ideas/retry/attempts/{attempt_id_1}?brand_profile_id={workspace.id}"
    resp_get_1 = client.get(get_url_1)
    assert resp_get_1.status_code == 200
    data_get_1 = resp_get_1.json()
    assert data_get_1["attempt_status"] == "partial"
    assert data_get_1["reason_code"] == "target_unfilled"
    # K4: hedef uyarısı + aynı atamanın boş kategori uyarısı (ikisi de targets[1])
    assert sorted(w["reason_code"] for w in data_get_1["warnings"]) == [
        "category_unfilled",
        "target_unfilled",
    ]
    assert {w["target_id"] for w in data_get_1["warnings"]} == {targets[1].id}

    # 4. Takip Eden İkinci Retry (Yeni idempotency_key)
    dispatches_2 = []

    def _capture_apply_async_2(*args, **kwargs):
        dispatches_2.append({"args": kwargs.get("args"), "task_id": kwargs.get("task_id")})
        return MagicMock(id=kwargs.get("task_id"))

    with patch("app.api.v1.generation.social_brief_ideas_retry_task.apply_async", side_effect=_capture_apply_async_2):
        resp2 = client.post(
            post_url,
            json={"idempotency_key": "retry-followup-2", "source_attempt_id": source_attempt.id},
        )
    assert resp2.status_code == 202
    attempt_id_2 = resp2.json()["attempt_id"]
    task_id_2 = dispatches_2[0]["task_id"]
    assert attempt_id_2 != attempt_id_1

    # 5. İkinci Worker Çalıştır (Tamamen Başarılı)
    clean_ai = SmartMockAIService()
    with patch("app.generators.ai_service.get_ai_service", return_value=clean_ai):
        worker_res_2 = social_brief_ideas_retry_task.apply(args=[attempt_id_2], task_id=task_id_2).get()

    assert worker_res_2["status"] == "completed"
    assert worker_res_2["newly_persisted_count"] == 1
    assert worker_res_2["accepted_target_ids"] == [targets[1].id]
    assert worker_res_2["unfilled_target_ids"] == []

    # 6. Nihai GET Polling: Tüm 2 hedef dolu ve completed
    get_url_2 = f"/api/v1/generation/social/briefs/{brief.id}/ideas/retry/attempts/{attempt_id_2}?brand_profile_id={workspace.id}"
    resp_get_2 = client.get(get_url_2)
    assert resp_get_2.status_code == 200
    data_get_2 = resp_get_2.json()
    assert data_get_2["attempt_status"] == "completed"
    assert data_get_2["total_ideas"] == 2
    assert len(data_get_2["ideas"]) == 2
    assert data_get_2["warnings"] == []


# ==================== 3. EŞZAMANLI POST ÇAĞRILARI ====================

def test_03_concurrent_post_requests_produce_single_active_attempt(
    client: TestClient,
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
    enable_flag,
):
    """3. Aynı brief için eşzamanlı iki POST isteği yapıldığında:
    - Biri 202 Accepted alır.
    - Diğeri 409 Conflict (ATTEMPT_CONFLICT) alır.
    - Veritabanında tek bir aktif attempt oluşur.
    """
    workspace, run, brief, categories, targets, kws, source_attempt = _setup_retry_lifecycle_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, filled_targets_count=1
    )

    url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/retry?brand_profile_id={workspace.id}"

    results = []

    def _make_request(key: str):
        with patch("app.api.v1.generation.social_brief_ideas_retry_task.apply_async") as mock_apply:
            mock_apply.return_value = MagicMock(id=str(uuid.uuid4()))
            resp = client.post(url, json={"idempotency_key": key, "source_attempt_id": source_attempt.id})
            results.append(resp.status_code)

    t1 = threading.Thread(target=_make_request, args=("concurrent-key-1",))
    t2 = threading.Thread(target=_make_request, args=("concurrent-key-2",))

    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Biri 202, diğeri 409 dönmelidir
    assert set(results) == {202, 409}

    active_attempts = (
        db_session.query(SocialGenerationAttempt)
        .filter(
            SocialGenerationAttempt.brief_id == brief.id,
            SocialGenerationAttempt.stage == "ideas_retry",
            SocialGenerationAttempt.status.in_(["pending", "running"]),
        )
        .all()
    )
    assert len(active_attempts) == 1


# ==================== 4. EŞZAMANLI WORKER ÇAĞRILARI ====================

def test_04_concurrent_worker_execution_produces_no_duplicate_ideas(
    client: TestClient,
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
    enable_flag,
):
    """4. Aynı attempt üzerinde çalışan ikinci worker çağrısı mükerrer fikir satırı üretmez (already_completed=True)."""
    workspace, run, brief, categories, targets, kws, source_attempt = _setup_retry_lifecycle_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, filled_targets_count=1
    )

    dispatches = []

    def _capture_apply_async(*args, **kwargs):
        dispatches.append({"args": kwargs.get("args"), "task_id": kwargs.get("task_id")})
        return MagicMock(id=kwargs.get("task_id"))

    with patch("app.api.v1.generation.social_brief_ideas_retry_task.apply_async", side_effect=_capture_apply_async):
        post_url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/retry?brand_profile_id={workspace.id}"
        resp = client.post(
            post_url,
            json={"idempotency_key": "duplicate-worker-guard-key", "source_attempt_id": source_attempt.id},
        )

    attempt_id = resp.json()["attempt_id"]
    task_id = dispatches[0]["task_id"]

    fake_ai = SmartMockAIService()
    with patch("app.generators.ai_service.get_ai_service", return_value=fake_ai):
        # 1. İlk worker çalışması
        res1 = social_brief_ideas_retry_task.apply(args=[attempt_id], task_id=task_id).get()
        assert res1["status"] == "completed"
        assert res1["newly_persisted_count"] == 1
        assert res1["replayed"] is False

        # 2. İkinci worker çalışması (aynı attempt ve aynı task_id)
        res2 = social_brief_ideas_retry_task.apply(args=[attempt_id], task_id=task_id).get()
        assert res2["status"] == "completed"
        assert res2["newly_persisted_count"] == 0
        assert res2["replayed"] is True

        # 3. Farklı task_id ile çağrılırsa TASK_MISMATCH reddedilir
        with pytest.raises(Exception, match="Worker sahipliği doğrulanamadı"):
            social_brief_ideas_retry_task.apply(args=[attempt_id], task_id=f"{task_id}-different").get()

    # DB'de toplamda tam olarak 2 fikir olmalı (1 başlangıç + 1 yeni), mükerrer satır oluşmamalıdır
    total_ideas = db_session.query(SocialIdea).filter(SocialIdea.brief_id == brief.id).count()
    assert total_ideas == 2
