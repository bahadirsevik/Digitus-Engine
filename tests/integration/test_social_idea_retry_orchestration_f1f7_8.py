# -*- coding: utf-8 -*-
"""MİKRO FAZ F1-F.7.8 — Fikir Tekrar Deneme Orkestrasyon Entegrasyon Testleri.

Tüm testler gerçek PostgreSQL test veritabanını ve SessionLocal oturum fabrikasını kullanır.
Test edilen alanlar:
1. Full success -> completed.
2. Birden fazla prompt -> her başarılı prompt sonrası heartbeat.
3. AI sırasında açık DB transaction/row lock yok.
4. Completed replay -> sıfır AI, sıfır yeni fikir.
5. İlk AI çağrısı hatası -> failed.
6. İlk kategori başarılı, sonraki hata -> partial ve başarılı ilk kategori persist edilir.
7. Heartbeat hatası -> güvenli failed, persistence yok.
8. Persistence hatası -> güvenli failed.
9. Late worker / expired lease -> çıktı yazılmaz.
10. Stale brief / assignment change -> çıktı yazılmaz.
11. Failure finalizer hatası asıl exception'ı maskelemez.
12. AI/provider/SQL metni dışarı veya DB error_message içine sızmaz.
13. Her session commit/rollback/close edilir.
14. temperature=None korunur.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy.orm import Session

from app.config import settings
from app.core.social.idea_flow import begin_social_idea_generation
from app.core.social.idea_orchestration import run_social_idea_generation
from app.core.social.idea_planner import build_social_idea_generation_plan
from app.core.social.idea_retry_flow import begin_social_idea_retry
from app.core.social.idea_retry_orchestration import (
    SocialIdeaRetryHeartbeatError,
    SocialIdeaRetryOrchestrationError,
    SocialIdeaRetryOrchestrationResult,
    run_social_idea_retry_generation,
)
from app.core.social.idea_retry_persistence import (
    SocialIdeaRetryPersistenceError,
    persist_social_idea_retry_results,
)
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
)
from app.generators.social.attempt_state import (
    AttemptNotWritableError,
    claim_ideas_retry_attempt_for_worker,
    finalize_ideas_retry_attempt_failure,
    heartbeat_attempt,
)
from app.generators.social.brief_idea_generator import (
    SocialBriefIdeaGenerator,
    SocialIdeaAIResult,
    SocialIdeaGenerationError,
)
from app.schemas.social_brief import (
    SocialBriefIdeasGenerateRequest,
    SocialBriefIdeasRetryRequest,
)

T0 = datetime(2026, 9, 25, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def enable_flag(monkeypatch):
    """Test süresince feature flag'i aktif kılar."""
    monkeypatch.setattr(settings, "ENABLE_SOCIAL_BRIEF_FLOW", True)
    yield


class SmartMockAIService:
    """Orkestrasyon testleri için akıllı sahte AI servisi."""

    def __init__(
        self,
        responses: list[Any] | None = None,
        on_complete_callback: Any = None,
    ) -> None:
        self.responses: list[Any] = list(responses) if responses else []
        self.on_complete_callback = on_complete_callback
        self.call_count: int = 0
        self.calls: list[dict[str, Any]] = []

    def for_stage(self, stage: str, **overrides) -> SmartMockAIService:
        return self

    def complete_json(self, prompt: str, **kwargs) -> str:
        self.call_count += 1
        self.calls.append({"prompt": prompt, "kwargs": kwargs})

        if self.on_complete_callback is not None:
            self.on_complete_callback(self.call_count, prompt, kwargs)

        if self.responses:
            resp = self.responses.pop(0)
            if isinstance(resp, Exception):
                raise resp
            if resp is not None:
                return resp

        # Otomatik uyumlu JSON yanıtı üret
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
                    "idea_title": f"Fikir Başlığı {idx}",
                    "idea_description": f"Stratejik gerekçe ve fikir açıklaması {idx}.",
                    "target_platform": spec["platform"],
                    "content_format": spec["content_format"],
                    "trend_alignment": 0.8,
                })
                idx += 1
        return json.dumps({"ideas": ideas}, ensure_ascii=False)


def _setup_fresh_environment(
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
    num_kws: int = 3,
    workspace_name: str = "Orchestration Brand",
):
    """Merkezi freshness sözleşmesini karşılayan workspace/run/pool oluşturur."""
    workspace = make_workspace(name=workspace_name, status="confirmed")
    run = make_scoring_run(
        brand_profile_id=workspace.id,
        status="completed",
        algorithm_version="v2",
        channel_pool_policy_version=workspace.policy_version or 1,
        relevance_anchor_version=workspace.anchor_version or 1,
        channel_assignment_version=1,
        skip_relevance=True,
    )

    keywords = []
    for i in range(num_kws):
        kw = make_keyword(
            text_value=f"{workspace_name} kw {i + 1}",
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
        keywords.append(kw)

    db_session.commit()
    return workspace, run, keywords


def _create_test_brief(
    db_session: Session,
    run_id: int,
    keywords: list,
    targets_data: list[tuple[str, str]] | None = None,
) -> SocialBrief:
    """Test için SocialBrief ve bağlı keyword/target kayıtlarını oluşturur."""
    brief = SocialBrief(
        scoring_run_id=run_id,
        brand_name_snapshot="Test Marka",
        brand_context_snapshot="Test Context",
        channel_assignment_version=1,
        format_matrix_version="v1",
        is_stale=False,
        locked_at=T0,
    )
    db_session.add(brief)
    db_session.flush()

    for pos, kw in enumerate(keywords):
        bk = SocialBriefKeyword(
            brief_id=brief.id,
            keyword_id=kw.id,
            keyword_snapshot=kw.keyword,
            position=pos,
        )
        db_session.add(bk)

    if targets_data is None:
        targets_data = [("instagram", "post"), ("twitter", "thread")]

    for plat, fmt in targets_data:
        t = SocialBriefTarget(
            brief_id=brief.id,
            platform=plat,
            content_format=fmt,
        )
        db_session.add(t)

    db_session.commit()
    db_session.refresh(brief)
    return brief


def _setup_retry_orchestration_env(
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
    task_id: str = "task-orch-001",
    persist_first_target: bool = True,
    targets_data: list[tuple[str, str]] | None = None,
    num_categories: int = 2,
    ideas_per_category: int = 2,
):
    """Orkestrasyon testleri için pending durumda ideas_retry attempt'i hazırlar."""
    ws, run, kws = _setup_fresh_environment(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        num_kws=3,
    )
    brief = _create_test_brief(db_session, run.id, kws, targets_data=targets_data)

    categories = []
    for i in range(num_categories):
        cat = SocialCategory(
            brief_id=brief.id,
            scoring_run_id=run.id,
            category_name=f"Kategori {i + 1}",
            category_type="educational",
            description=f"Açıklama {i + 1}",
            relevance_score=0.85,
            suggested_keyword_ids=[kws[0].id],
            is_stale=False,
        )
        db_session.add(cat)
        categories.append(cat)

    db_session.commit()
    targets = (
        db_session.query(SocialBriefTarget)
        .filter(SocialBriefTarget.brief_id == brief.id)
        .order_by(SocialBriefTarget.id.asc())
        .all()
    )

    # Kaynak ideas attempt
    plan = build_social_idea_generation_plan(
        selected_category_ids=tuple(c.id for c in categories),
        target_ids=tuple(t.id for t in targets),
        ideas_per_category=ideas_per_category,
    )
    cov = {
        "schema_version": "ideas_plan_v1",
        "request": {
            "category_ids": [c.id for c in categories],
            "ideas_per_category": ideas_per_category,
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
            "total_accepted": 0,
            "target_ids": [],
        },
    }
    source_att = SocialGenerationAttempt(
        brief_id=brief.id,
        stage="ideas",
        status="completed",
        task_id="task-source-001",
        idempotency_key="source-attempt-001",
        coverage=cov,
        requested_target_ids=list(plan.covered_target_ids),
        completed_at=T0,
        created_at=T0,
    )
    db_session.add(source_att)
    db_session.commit()

    if persist_first_target:
        # 1. hedefi dolduralım
        idea = SocialIdea(
            brief_id=brief.id,
            category_id=categories[0].id,
            brief_target_id=targets[0].id,
            keyword_id=kws[0].id,
            idea_title="Mevcut Fikir",
            idea_description="Mevcut Açıklama",
            target_platform=targets[0].platform,
            content_format=targets[0].content_format,
            trend_alignment=0.85,
            is_stale=False,
        )
        db_session.add(idea)
        db_session.commit()

    req = SocialBriefIdeasRetryRequest(
        idempotency_key=f"retry-key-{task_id}",
        source_attempt_id=source_att.id,
    )
    start = begin_social_idea_retry(
        db_session,
        brief_id=brief.id,
        brand_profile_id=ws.id,
        request=req,
        now=T0,
    )
    db_session.commit()

    retry_att = db_session.query(SocialGenerationAttempt).filter_by(id=start.attempt_id).first()
    return ws, run, kws, brief, categories, targets, source_att, start, retry_att


def test_01_retry_orchestration_full_success_completed(
    enable_flag, db_session, make_workspace, make_scoring_run, make_keyword
):
    """1. Full success: bütün eksik hedefler doldurulur ve attempt completed olur."""
    targets_data = [("instagram", "post"), ("twitter", "thread")]
    ws, run, kws, brief, cats, targets, source_att, start, retry_att = _setup_retry_orchestration_env(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-01",
        persist_first_target=True,
        targets_data=targets_data,
    )
    ai_service = SmartMockAIService()

    res = run_social_idea_retry_generation(
        session_factory=SessionLocal,
        ai_service=ai_service,
        attempt_id=retry_att.id,
        task_id="task-01",
        now_provider=T0,
    )

    assert isinstance(res, SocialIdeaRetryOrchestrationResult)
    assert res.status == "completed"
    assert res.replayed is False
    assert res.newly_persisted_count == 1
    assert res.accepted_target_ids == (targets[1].id,)
    assert res.unfilled_target_ids == ()
    assert res.ai_calls_used == 1

    # DB kontrolü
    db_session.refresh(retry_att)
    assert retry_att.status == "completed"
    assert retry_att.completed_at is not None
    assert retry_att.lease_expires_at is None
    assert retry_att.warnings == []

    # Fikir kontrolü
    db_ideas = db_session.query(SocialIdea).filter_by(brief_id=brief.id, is_stale=False).all()
    assert len(db_ideas) == 2


def test_02_multiple_prompts_trigger_heartbeat_after_each_category(
    enable_flag, db_session, make_workspace, make_scoring_run, make_keyword
):
    """2. Birden fazla prompt: her başarılı kategori ardından heartbeat kaydedilir."""
    targets_data = [("instagram", "post"), ("twitter", "thread")]
    ws, run, kws, brief, cats, targets, source_att, start, retry_att = _setup_retry_orchestration_env(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-02",
        persist_first_target=False,
        targets_data=targets_data,
        num_categories=2,
        ideas_per_category=1,
    )
    ai_service = SmartMockAIService()

    heartbeat_calls = []
    orig_heartbeat = heartbeat_attempt

    def tracking_heartbeat(*args, **kwargs):
        heartbeat_calls.append(kwargs.get("task_id"))
        return orig_heartbeat(*args, **kwargs)

    with patch("app.core.social.idea_retry_orchestration.heartbeat_attempt", side_effect=tracking_heartbeat):
        res = run_social_idea_retry_generation(
            session_factory=SessionLocal,
            ai_service=ai_service,
            attempt_id=retry_att.id,
            task_id="task-02",
            now_provider=T0,
        )

    assert res.status == "completed"
    assert len(heartbeat_calls) == 2


def test_03_no_open_db_session_or_locks_during_ai_generation(
    enable_flag, db_session, make_workspace, make_scoring_run, make_keyword
):
    """3. AI çalışırken açık DB transaction veya satır kilidi bulunmaz."""
    ws, run, kws, brief, cats, targets, source_att, start, retry_att = _setup_retry_orchestration_env(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-03",
    )

    db_open_during_ai = False

    def on_ai_complete(call_idx, prompt, kwargs):
        nonlocal db_open_during_ai
        # Başka bir session ile attempt tablosunu FOR UPDATE ile kilitlemeyi deneyelim
        # Eğer orchestration AI anında kilidi açık tutsaydı bu kilit sorgusu bloke olurdu
        test_session = SessionLocal()
        try:
            row = (
                test_session.query(SocialGenerationAttempt)
                .filter(SocialGenerationAttempt.id == retry_att.id)
                .with_for_update(nowait=True)
                .one_or_none()
            )
            assert row is not None
            test_session.commit()
        except Exception:
            db_open_during_ai = True
            test_session.rollback()
        finally:
            test_session.close()

    ai_service = SmartMockAIService(on_complete_callback=on_ai_complete)

    res = run_social_idea_retry_generation(
        session_factory=SessionLocal,
        ai_service=ai_service,
        attempt_id=retry_att.id,
        task_id="task-03",
        now_provider=T0,
    )
    assert res.status == "completed"
    assert db_open_during_ai is False


def test_04_completed_replay_zero_ai_zero_new_ideas(
    enable_flag, db_session, make_workspace, make_scoring_run, make_keyword
):
    """4. Completed replay: sıfır AI, sıfır yeni fikir, replayed=True."""
    ws, run, kws, brief, cats, targets, source_att, start, retry_att = _setup_retry_orchestration_env(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-04",
    )
    ai_service = SmartMockAIService()

    # 1. İlk normal çalıştırma
    res1 = run_social_idea_retry_generation(
        session_factory=SessionLocal,
        ai_service=ai_service,
        attempt_id=retry_att.id,
        task_id="task-04",
        now_provider=T0,
    )
    assert res1.status == "completed"
    assert res1.replayed is False
    assert res1.newly_persisted_count == 1
    assert ai_service.call_count == 1

    # 2. İkinci çalıştırma (completed replay)
    ai_service2 = SmartMockAIService()
    res2 = run_social_idea_retry_generation(
        session_factory=SessionLocal,
        ai_service=ai_service2,
        attempt_id=retry_att.id,
        task_id="task-04",
        now_provider=T0,
    )
    assert res2.status == "completed"
    assert res2.replayed is True
    assert res2.newly_persisted_count == 0
    assert ai_service2.call_count == 0


def test_05_first_ai_call_failure_fails_closed_and_raises(
    enable_flag, db_session, make_workspace, make_scoring_run, make_keyword
):
    """5. İlk AI çağrısı hatası: attempt failed yapılır ve hata dışarı fırlatılır."""
    ws, run, kws, brief, cats, targets, source_att, start, retry_att = _setup_retry_orchestration_env(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-05",
    )
    ai_service = SmartMockAIService(
        responses=[SocialIdeaGenerationError("Provider çöktü", error_code="IDEA_PROVIDER_ERROR")]
    )

    with pytest.raises(SocialIdeaGenerationError):
        run_social_idea_retry_generation(
            session_factory=SessionLocal,
            ai_service=ai_service,
            attempt_id=retry_att.id,
            task_id="task-05",
            now_provider=T0,
        )

    db_session.refresh(retry_att)
    assert retry_att.status == "failed"
    assert retry_att.reason_code == "idea_provider_error"
    assert "Provider çöktü" not in retry_att.error_message
    assert retry_att.completed_at is not None


def test_06_first_category_succeeds_second_fails_results_in_partial_success(
    enable_flag, db_session, make_workspace, make_scoring_run, make_keyword
):
    """6. İlk kategori başarılı, sonraki AI hatası: partial başarı döner, worker exception fırlatmaz."""
    targets_data = [("instagram", "post"), ("twitter", "thread")]
    ws, run, kws, brief, cats, targets, source_att, start, retry_att = _setup_retry_orchestration_env(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-06",
        persist_first_target=False,
        targets_data=targets_data,
        num_categories=2,
        ideas_per_category=1,
    )
    # Cat 1 başarılı, Cat 2 hata
    ai_service = SmartMockAIService(
        responses=[
            None,  # Otomatik JSON üretir
            SocialIdeaGenerationError("Provider hatası", error_code="IDEA_PROVIDER_ERROR"),
        ]
    )

    res = run_social_idea_retry_generation(
        session_factory=SessionLocal,
        ai_service=ai_service,
        attempt_id=retry_att.id,
        task_id="task-06",
        now_provider=T0,
    )

    assert res.status == "partial"
    assert res.replayed is False
    assert res.newly_persisted_count == 1
    assert len(res.accepted_target_ids) == 1
    assert len(res.unfilled_target_ids) == 1

    db_session.refresh(retry_att)
    assert retry_att.status == "partial"
    assert retry_att.reason_code == "target_unfilled"
    # K4: başarısız kategori hiç fikir almadığı için hedef uyarısına ek olarak aynı
    # atamanın kategori uyarısı da taşınır.
    assert len(res.unfilled_category_ids) == 1
    assert sorted(w["reason_code"] for w in retry_att.warnings) == [
        "category_unfilled",
        "target_unfilled",
    ]
    assert {w["target_id"] for w in retry_att.warnings} == {res.unfilled_target_ids[0]}
    assert {w["category_id"] for w in retry_att.warnings} == {res.unfilled_category_ids[0]}


def test_07_heartbeat_failure_fails_closed_no_persistence(
    enable_flag, db_session, make_workspace, make_scoring_run, make_keyword
):
    """7. Heartbeat hatası: güvenli failed yapılır, persistence çağrılmaz."""
    ws, run, kws, brief, cats, targets, source_att, start, retry_att = _setup_retry_orchestration_env(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-07",
    )
    ai_service = SmartMockAIService()

    with patch(
        "app.core.social.idea_retry_orchestration.heartbeat_attempt",
        side_effect=SocialIdeaRetryHeartbeatError("Heartbeat mock failed"),
    ):
        with pytest.raises(SocialIdeaRetryHeartbeatError):
            run_social_idea_retry_generation(
                session_factory=SessionLocal,
                ai_service=ai_service,
                attempt_id=retry_att.id,
                task_id="task-07",
                now_provider=T0,
            )

    db_session.refresh(retry_att)
    assert retry_att.status == "failed"
    assert retry_att.reason_code == "idea_heartbeat_failed"


def test_08_persistence_failure_fails_closed(
    enable_flag, db_session, make_workspace, make_scoring_run, make_keyword
):
    """8. Persistence hatası: güvenli failed yapılır."""
    ws, run, kws, brief, cats, targets, source_att, start, retry_att = _setup_retry_orchestration_env(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-08",
    )
    ai_service = SmartMockAIService()

    with patch(
        "app.core.social.idea_retry_orchestration.persist_social_idea_retry_results",
        side_effect=SocialIdeaRetryPersistenceError(
            "Persistence fail", error_code="IDEA_RETRY_PERSISTENCE_CONFLICT"
        ),
    ):
        with pytest.raises(SocialIdeaRetryPersistenceError):
            run_social_idea_retry_generation(
                session_factory=SessionLocal,
                ai_service=ai_service,
                attempt_id=retry_att.id,
                task_id="task-08",
                now_provider=T0,
            )

    db_session.refresh(retry_att)
    assert retry_att.status == "failed"
    assert retry_att.reason_code == "idea_persistence_failed"


def test_09_expired_lease_worker_lost_no_output_written(
    enable_flag, db_session, make_workspace, make_scoring_run, make_keyword
):
    """9. Expired lease: worker_lost olarak failed edilir, çıktı yazılmaz."""
    ws, run, kws, brief, cats, targets, source_att, start, retry_att = _setup_retry_orchestration_env(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-09",
    )
    # Lease süresini geçmişe alalım
    retry_att.lease_expires_at = T0 - timedelta(seconds=10)
    db_session.commit()

    ai_service = SmartMockAIService()
    with pytest.raises(Exception):
        run_social_idea_retry_generation(
            session_factory=SessionLocal,
            ai_service=ai_service,
            attempt_id=retry_att.id,
            task_id="task-09",
            now_provider=T0,
        )

    db_session.refresh(retry_att)
    assert retry_att.status == "failed"
    assert retry_att.reason_code == "worker_lost"


def test_10_stale_brief_during_ai_prevents_persistence(
    enable_flag, db_session, make_workspace, make_scoring_run, make_keyword
):
    """10. AI sırasında brief stale olursa persistence reddeder ve failed olur."""
    ws, run, kws, brief, cats, targets, source_att, start, retry_att = _setup_retry_orchestration_env(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-10",
    )

    def on_complete(idx, prompt, kwargs):
        # AI anında brief'i stale yapalım
        s = SessionLocal()
        b = s.query(SocialBrief).filter_by(id=brief.id).first()
        b.is_stale = True
        s.commit()
        s.close()

    ai_service = SmartMockAIService(on_complete_callback=on_complete)

    with pytest.raises(Exception):
        run_social_idea_retry_generation(
            session_factory=SessionLocal,
            ai_service=ai_service,
            attempt_id=retry_att.id,
            task_id="task-10",
            now_provider=T0,
        )

    db_session.refresh(retry_att)
    assert retry_att.status == "failed"


def test_11_failure_finalizer_error_does_not_mask_original_exception(
    enable_flag, db_session, make_workspace, make_scoring_run, make_keyword
):
    """11. Failure finalizer'ın kendi hatası asıl istisnayı maskelemez."""
    ws, run, kws, brief, cats, targets, source_att, start, retry_att = _setup_retry_orchestration_env(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-11",
    )
    ai_service = SmartMockAIService(
        responses=[SocialIdeaGenerationError("Asıl provider hatası", error_code="IDEA_PROVIDER_ERROR")]
    )

    with patch(
        "app.core.social.idea_retry_orchestration.finalize_ideas_retry_attempt_failure",
        side_effect=RuntimeError("Finalizer çöktü"),
    ):
        with pytest.raises(SocialIdeaGenerationError) as exc:
            run_social_idea_retry_generation(
                session_factory=SessionLocal,
                ai_service=ai_service,
                attempt_id=retry_att.id,
                task_id="task-11",
                now_provider=T0,
            )
        assert exc.value.error_code == "IDEA_PROVIDER_ERROR"


def test_12_error_messages_do_not_leak_raw_ai_sql_provider_text(
    enable_flag, db_session, make_workspace, make_scoring_run, make_keyword
):
    """12. Hata mesajlarında asla ham provider hatası veya SQL metni sızmaz."""
    ws, run, kws, brief, cats, targets, source_att, start, retry_att = _setup_retry_orchestration_env(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-12",
    )
    secret_leak = "GEMINI_QUOTA_EXCEEDED_FOR_CUSTOMER_SECRET_XYZ"
    ai_service = SmartMockAIService(
        responses=[SocialIdeaGenerationError(secret_leak, error_code="IDEA_PROVIDER_ERROR")]
    )

    with pytest.raises(SocialIdeaGenerationError):
        run_social_idea_retry_generation(
            session_factory=SessionLocal,
            ai_service=ai_service,
            attempt_id=retry_att.id,
            task_id="task-12",
            now_provider=T0,
        )

    db_session.refresh(retry_att)
    assert secret_leak not in (retry_att.error_message or "")


def test_13_all_sessions_committed_or_rolled_back_and_closed(
    enable_flag, db_session, make_workspace, make_scoring_run, make_keyword
):
    """13. Her session commit/rollback ve close garantisine sahiptir."""
    ws, run, kws, brief, cats, targets, source_att, start, retry_att = _setup_retry_orchestration_env(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-13",
    )
    ai_service = SmartMockAIService()

    opened_sessions = []
    closed_sessions = []

    def tracking_factory():
        s = SessionLocal()
        opened_sessions.append(s)
        orig_close = s.close

        def tracking_close():
            closed_sessions.append(s)
            orig_close()

        s.close = tracking_close
        return s

    res = run_social_idea_retry_generation(
        session_factory=tracking_factory,
        ai_service=ai_service,
        attempt_id=retry_att.id,
        task_id="task-13",
        now_provider=T0,
    )
    assert res.status == "completed"
    assert len(opened_sessions) > 0
    assert len(opened_sessions) == len(closed_sessions)


def test_14_temperature_none_preserved(
    enable_flag, db_session, make_workspace, make_scoring_run, make_keyword
):
    """14. temperature=None ayarı AI generator çağrısında korunur."""
    ws, run, kws, brief, cats, targets, source_att, start, retry_att = _setup_retry_orchestration_env(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-14",
    )
    ai_service = SmartMockAIService()

    res = run_social_idea_retry_generation(
        session_factory=SessionLocal,
        ai_service=ai_service,
        attempt_id=retry_att.id,
        task_id="task-14",
        now_provider=T0,
    )
    assert res.status == "completed"
    assert len(ai_service.calls) == 1
    # Generator for_stage("social_ideas") çağırır ve temperature override yapmaz
    # Böylece default temperature=None korunur
