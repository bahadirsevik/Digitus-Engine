# -*- coding: utf-8 -*-
"""MİKRO FAZ F1-G.5.5 — Sosyal İçerik Orkestrasyon, Atomik Persistence ve Finalization Entegrasyon Testleri.

Tüm testler gerçek PostgreSQL test veritabanını ve SessionLocal oturum fabrikasını kullanır.
Test edilen 32 senaryo:
1. Tek accepted item -> persist + completed
2. Çoklu accepted item -> kanonik sıra persist + completed
3. Accepted + rejected karışık -> accepted persist, attempt partial, warning korunur
4. Tümü rejected -> 0 content yazılır, attempt failed
5. Already-present + accepted -> completed
6. Already-present + rejected -> partial
7. Tümü already-present running attempt -> 0 AI, completed finalization
8. Completed replay -> 0 AI, 0 heartbeat, 0 persistence
9. Completed replay eksik content -> fail-closed
10. Completed replay bozuk/stale content -> fail-closed
11. Her tamamlanan work item sonrasında heartbeat çağrılması
12. Already-present ve replay için heartbeat çağrılmaması
13. Heartbeat hatasında batch'in durması (warning'e dönüştürülmez)
14. Lease süresi dolan worker persistence yapamaz
15. Task mismatch worker persistence/finalization yapamaz
16. AI sırasında brief stale olursa persistence reddedilir
17. AI sırasında assignment version değişirse persistence reddedilir
18. İkinci accepted item persistence hatasında ilk item rollback edilir
19. Persistence hatasından sonra güvenli failure finalization çalışır
20. Failure finalizer hatası asıl exception'ı maskelemez
21. Warning JSON nesnesi tam alanları içerir (idea_id, reason_code, claims, ai_calls_used)
22. Provider/SQL/raw exception mesajı warning ve attempt'e sızmaz
23. content_rejected durumunda ungrounded claims korunur
24. Diğer warning türlerinde claims=() olur
25. contents_request_v1 request snapshot coverage içinde korunur
26. Sahte/yanlış attempt_id ile persistence fail-closed reddedilir
27. Sahte/yanlış idea_id ile persistence fail-closed reddedilir
28. AI çağrısı sırasında açık DB session/transaction bulunmaz
29. Persistence ve finish_attempt aynı transaction içinde atomiktir
30. OrchestrationResult DTO invariant ve immutability doğrulamaları
31. Tüm yapay zeka çağrılarında temperature=None korunur
32. Replay durumunda mükerrer SocialContent satırı oluşmaz
"""
from __future__ import annotations

import copy
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
import json
from typing import Any
from unittest.mock import patch
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.core.social.content_batch_execution import (
    SocialContentBatchExecutionError,
    SocialContentBatchWarning,
)
from app.core.social.content_contract import (
    ValidatedCarouselPayload,
    ValidatedCarouselSlide,
    ValidatedHook,
    ValidatedSocialContent,
    ValidatedVideoPayload,
    ValidatedVideoSegment,
    _render_legacy_scenario_from_payload,
)
from app.core.social.content_orchestration import (
    SocialContentHeartbeatError,
    SocialContentOrchestrationError,
    SocialContentOrchestrationResult,
    run_social_content_generation,
)
from app.core.social.content_persistence import (
    CONTENT_PERSISTENCE_ATTEMPT_NOT_WRITABLE,
    SocialContentPersistenceError,
    persist_social_content,
    validate_social_content_attempt_coverage,
)
from app.core.social.content_quality import (
    SocialContentQualityDecision,
)
from app.core.social.content_worker_input import (
    SocialContentWorkerInputError,
)
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
from app.generators.social.attempt_state import (
    AttemptNotWritableError,
    finalize_contents_attempt_failure,
    finish_attempt,
    heartbeat_attempt,
)
from app.generators.social.format_matrix import get_duration_preset

T0 = datetime(2026, 9, 25, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def enable_flag(monkeypatch):
    """Test süresince feature flag'i aktif kılar."""
    monkeypatch.setattr(settings, "ENABLE_SOCIAL_BRIEF_FLOW", True)
    yield


# ==================== TEST YARDIMCILARI ====================


class FakeCollector:
    def __init__(self) -> None:
        self.failed_reasons: list[str] = []

    def mark_current_attempt_failed(self, reason: str) -> bool:
        self.failed_reasons.append(reason)
        return True

    def logical_request(self):
        from contextlib import nullcontext
        return nullcontext()


class SmartMockAIService:
    """Orkestrasyon testleri için akıllı sahte AI servisi."""

    def __init__(
        self,
        responses: list[Any] | None = None,
        raise_exc: Exception | None = None,
        on_call: Any = None,
    ) -> None:
        self.responses: list[Any] = list(responses) if responses else []
        self.raise_exc = raise_exc
        self.on_call = on_call
        self.call_count: int = 0
        self.calls: list[dict[str, Any]] = []
        self.collector = FakeCollector()

    def for_stage(self, stage: str, **overrides) -> SmartMockAIService:
        return self

    def complete_json(self, prompt: str, **kwargs) -> str:
        self.call_count += 1
        self.calls.append({"prompt": prompt, "kwargs": kwargs})

        if self.on_call is not None:
            self.on_call(self.call_count, prompt, kwargs)

        if self.raise_exc is not None:
            raise self.raise_exc

        if self.responses:
            resp = self.responses.pop(0)
            if isinstance(resp, Exception):
                raise resp
            if isinstance(resp, str):
                return resp
            return json.dumps(resp, ensure_ascii=False)

        return json.dumps(_make_valid_post_dict(), ensure_ascii=False)


def _make_valid_post_dict(
    *,
    caption: str = "Doğal cilt bakım rutini önerileri ve günlük uygulama rehberi.",
    hook_text: str = "Cildiniz için temiz içerikli temel adımlar",
) -> dict[str, Any]:
    return {
        "hooks": [
            {"text": hook_text, "style": "curiosity"},
        ],
        "caption": caption,
        "cta_text": "Detaylar için profildeki linke tıklayın.",
        "hashtags": ["ciltbakimi", "dogalkozmetik", "nemlendirici", "organik", "guzellik"],
        "format_payload": None,
        "visual_suggestion": "Pastel ve ferah ışık",
        "video_concept": None,
        "industry_posting_suggestion": "Akşam 19:00",
        "platform_notes": None,
    }


def _make_valid_content_object(
    platform: str = "instagram",
    content_format: str = "post",
    *,
    duration_preset_id: str | None = None,
) -> ValidatedSocialContent:
    hooks = (
        ValidatedHook(text="Temiz içerikli cilt bakımı rehberi", style="question", ab_score=0.9),
    )
    caption = "Doğal cilt bakım rutini önerileri ve günlük uygulama rehberi."
    cta = "Detaylar için profildeki linke tıklayın."
    hashtags = ("ciltbakimi", "dogalkozmetik", "nemlendirici", "organik", "guzellik")

    if content_format in ("video", "reels", "short"):
        payload = ValidatedVideoPayload(
            kind="video",
            segments=(
                ValidatedVideoSegment(0, 5, "Giriş", "Hook", "Doğal içerikli adımlarla cildinizi koruyun"),
                ValidatedVideoSegment(5, 25, "Detay", "Metin", "Günlük bakımınızda organik yağları tercih edin"),
            ),
        )
        scenario = _render_legacy_scenario_from_payload(payload)
        return ValidatedSocialContent(
            hooks=hooks,
            caption=caption,
            format_payload=payload,
            visual_suggestion="Aydınlık ortam",
            video_concept="B-roll",
            cta_text=cta,
            hashtags=hashtags,
            industry_posting_suggestion="Akşam 19:00",
            platform_notes=None,
            duration_status="valid",
            actual_duration_sec=25,
            validation_warnings=(),
            scenario=scenario,
        )
    return ValidatedSocialContent(
        hooks=hooks,
        caption=caption,
        format_payload=None,
        visual_suggestion="Aydınlık ortam",
        video_concept=None,
        cta_text=cta,
        hashtags=hashtags,
        industry_posting_suggestion="Akşam 19:00",
        platform_notes=None,
        duration_status="not_applicable",
        actual_duration_sec=None,
        validation_warnings=(),
        scenario=None,
    )


def _make_quality_decision(
    *,
    action: str = "accept",
    grounding_clean: bool = True,
    duration_acceptable: bool = True,
    repair_attempted: bool = False,
    warnings: tuple[str, ...] = (),
    claims: tuple[str, ...] = (),
    reason_codes: tuple[str, ...] = (),
) -> SocialContentQualityDecision:
    return SocialContentQualityDecision(
        action=action,
        grounding_clean=grounding_clean,
        duration_acceptable=duration_acceptable,
        repair_attempted=repair_attempted,
        warnings=warnings,
        claims=claims,
        reason_codes=reason_codes,
    )


def _persist_content_for_test(
    db_session: Session,
    *,
    brief_id: int,
    attempt_id: int,
    idea_id: int,
    task_id: str,
    content: ValidatedSocialContent | None = None,
    quality_decision: SocialContentQualityDecision | None = None,
    now: datetime = T0,
):
    if content is None:
        content = _make_valid_content_object("instagram", "post")
    if quality_decision is None:
        quality_decision = _make_quality_decision()
    return persist_social_content(
        db_session,
        brief_id=brief_id,
        attempt_id=attempt_id,
        idea_id=idea_id,
        content=content,
        quality_decision=quality_decision,
        task_id=task_id,
        now=now,
    )


def _setup_content_orchestration_env(
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
    *,
    num_ideas: int = 1,
    platform: str = "instagram",
    content_format: str = "post",
    duration_preset_id: str | None = None,
    task_id: str = "task-orch-c1",
    attempt_status: str = "running",
    attempt_stage: str = "contents",
    brief_stale: bool = False,
    brief_locked: bool = True,
    channel_assignment_version: int = 1,
    product_facts: str | None = "Dermatolojik olarak test edilmiştir.",
    trusted_brand_usp: str | None = "Organik soğuk sıkım bitkisel yağlar",
) -> dict[str, Any]:
    ws = make_workspace(name=f"Brand {uuid.uuid4().hex[:6]}", status="confirmed")
    run = make_scoring_run(
        brand_profile_id=ws.id,
        status="completed",
        algorithm_version="v2",
        channel_pool_policy_version=ws.policy_version or 1,
        relevance_anchor_version=ws.anchor_version or 1,
        channel_assignment_version=channel_assignment_version,
        skip_relevance=True,
    )
    kw = make_keyword(
        text_value="organik cilt bakımı",
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
        brand_context_snapshot="Doğal ve organik kozmetik ürünleri",
        channel_assignment_version=channel_assignment_version,
        format_matrix_version="v1",
        is_stale=brief_stale,
        locked_at=T0 if brief_locked else None,
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

    min_sec, max_sec = None, None
    if duration_preset_id is not None:
        p_def = get_duration_preset(duration_preset_id)
        if p_def:
            min_sec = p_def.min_sec
            max_sec = p_def.max_sec

    target = SocialBriefTarget(
        brief_id=brief.id,
        platform=platform,
        content_format=content_format,
        duration_preset_id=duration_preset_id,
        duration_min_sec=min_sec,
        duration_max_sec=max_sec,
    )
    db_session.add(target)
    db_session.flush()

    cat = SocialCategory(
        scoring_run_id=run.id,
        brief_id=brief.id,
        category_name="Eğitici İçerikler",
        category_type="educational",
        description="Eğitici bakım tavsiyeleri.",
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
            idea_title=f"Doğal Cilt Bakımı Fikri {i + 1}",
            idea_description=f"Detaylı günlük bakım tavsiyesi ve kullanım planı {i + 1}.",
            target_platform=platform,
            content_format=content_format,
            trend_alignment=0.88,
            is_stale=False,
        )
        db_session.add(idea)
        ideas.append(idea)
    db_session.flush()

    idea_ids_list = [i.id for i in ideas]

    coverage = {
        "schema_version": "contents_request_v1",
        "request": {
            "idea_ids": idea_ids_list,
            "product_facts": product_facts,
            "trusted_brand_usp": trusted_brand_usp,
        },
    }

    started_at = T0 if attempt_status != "pending" else None
    attempt = SocialGenerationAttempt(
        brief_id=brief.id,
        stage=attempt_stage,
        idempotency_key=f"content-orch-{uuid.uuid4().hex[:8]}",
        status=attempt_status,
        task_id=task_id if attempt_status != "pending" else None,
        started_at=started_at,
        heartbeat_at=started_at,
        lease_expires_at=T0 + timedelta(seconds=1500),
        requested_idea_ids=idea_ids_list,
        coverage=coverage,
        created_at=T0,
    )
    db_session.add(attempt)
    db_session.commit()

    return {
        "workspace": ws,
        "scoring_run": run,
        "keyword": kw,
        "brief": brief,
        "target": target,
        "category": cat,
        "ideas": ideas,
        "attempt": attempt,
    }


# ==================== TESTLER (32 SENARYO) ====================


def test_01_single_accepted_item_persisted_and_completed(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """1. Tek bir accepted item persist edilir ve attempt completed olur."""
    ctx = _setup_content_orchestration_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=1, task_id="task-01"
    )
    attempt = ctx["attempt"]
    idea = ctx["ideas"][0]
    ai = SmartMockAIService()

    result = run_social_content_generation(
        session_factory=SessionLocal,
        ai_service=ai,
        attempt_id=attempt.id,
        task_id="task-01",
        now_provider=lambda: T0,
    )

    assert result.status == "completed"
    assert result.requested_idea_ids == (idea.id,)
    assert result.already_present_idea_ids == ()
    assert result.persisted_idea_ids == (idea.id,)
    assert result.unresolved_idea_ids == ()
    assert result.warnings == ()
    assert result.ai_calls_used == 1
    assert result.replayed is False

    db_session.expire_all()
    db_attempt = db_session.query(SocialGenerationAttempt).filter_by(id=attempt.id).one()
    assert db_attempt.status == "completed"
    assert db_attempt.completed_at is not None

    db_content = db_session.query(SocialContent).filter_by(idea_id=idea.id).one()
    assert db_content.brief_id == ctx["brief"].id
    assert db_content.caption is not None


def test_02_multiple_accepted_items_canonical_order_persisted_and_completed(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """2. Çoklu accepted item'lar kanonik sıra ile persist edilir ve completed olur."""
    ctx = _setup_content_orchestration_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=3, task_id="task-02"
    )
    attempt = ctx["attempt"]
    ideas = ctx["ideas"]
    sorted_ids = tuple(sorted(i.id for i in ideas))
    ai = SmartMockAIService()

    result = run_social_content_generation(
        session_factory=SessionLocal,
        ai_service=ai,
        attempt_id=attempt.id,
        task_id="task-02",
        now_provider=lambda: T0,
    )

    assert result.status == "completed"
    assert result.persisted_idea_ids == sorted_ids
    assert result.ai_calls_used == 3
    assert result.warnings == ()

    db_session.expire_all()
    count = db_session.query(SocialContent).filter_by(brief_id=ctx["brief"].id).count()
    assert count == 3


def test_03_accepted_and_rejected_mix_partial_status_and_warnings(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """3. Accepted ve rejected karışık olduğunda accepted kaydedilir, attempt partial olur."""
    ctx = _setup_content_orchestration_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=2, task_id="task-03"
    )
    attempt = ctx["attempt"]
    ideas = ctx["ideas"]
    id1, id2 = ideas[0].id, ideas[1].id

    # Item 1: kabul edilir (1 call)
    # Item 2: iddia içerir, tamirde de iddia devam eder -> ret (2 calls)
    resp_clean = _make_valid_post_dict(caption="Tamamen doğal içerikli bakım rehberi.")
    resp_claim1 = _make_valid_post_dict(caption="Ürünümüz %100 garantili doğal çözümdür.")
    resp_claim2 = _make_valid_post_dict(caption="Hala %100 kesin garanti veriyoruz.")
    ai = SmartMockAIService(responses=[resp_clean, resp_claim1, resp_claim2])

    result = run_social_content_generation(
        session_factory=SessionLocal,
        ai_service=ai,
        attempt_id=attempt.id,
        task_id="task-03",
        now_provider=lambda: T0,
    )

    assert result.status == "partial"
    assert result.persisted_idea_ids == (id1,)
    assert result.unresolved_idea_ids == (id2,)
    assert len(result.warnings) == 1
    assert result.warnings[0].idea_id == id2
    assert result.warnings[0].reason_code == "content_rejected"
    assert len(result.warnings[0].claims) > 0
    assert result.ai_calls_used == 3

    db_session.expire_all()
    db_attempt = db_session.query(SocialGenerationAttempt).filter_by(id=attempt.id).one()
    assert db_attempt.status == "partial"
    assert db_attempt.reason_code == "content_partial"
    assert len(db_attempt.warnings) == 1
    assert db_attempt.warnings[0]["idea_id"] == id2

    # Yalnızca 1 adet kaydedilmiş olmalı
    count = db_session.query(SocialContent).filter_by(brief_id=ctx["brief"].id).count()
    assert count == 1


def test_04_all_rejected_failed_status_zero_content(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """4. Tüm item'lar rejected olduğunda 0 içerik yazılır, attempt failed olur."""
    ctx = _setup_content_orchestration_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=2, task_id="task-04"
    )
    attempt = ctx["attempt"]
    # İki item için de ikişer claimli yanıt
    claim = _make_valid_post_dict(caption="%100 garantili ve en iyi sonuç.")
    ai = SmartMockAIService(responses=[claim, claim, claim, claim])

    result = run_social_content_generation(
        session_factory=SessionLocal,
        ai_service=ai,
        attempt_id=attempt.id,
        task_id="task-04",
        now_provider=lambda: T0,
    )

    assert result.status == "failed"
    assert result.persisted_idea_ids == ()
    assert len(result.unresolved_idea_ids) == 2
    assert len(result.warnings) == 2

    db_session.expire_all()
    db_attempt = db_session.query(SocialGenerationAttempt).filter_by(id=attempt.id).one()
    assert db_attempt.status == "failed"
    assert db_attempt.reason_code == "content_generation_failed"

    count = db_session.query(SocialContent).filter_by(brief_id=ctx["brief"].id).count()
    assert count == 0


def test_05_already_present_plus_accepted_completed(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """5. Bir fikir önceden kayıtlı, ikincisi accepted olduğunda completed olur."""
    ctx = _setup_content_orchestration_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=2, task_id="task-05"
    )
    attempt = ctx["attempt"]
    ideas = ctx["ideas"]
    id1, id2 = ideas[0].id, ideas[1].id

    # id1'i önceden persist et
    _persist_content_for_test(
        db_session,
        brief_id=ctx["brief"].id,
        attempt_id=attempt.id,
        task_id="task-05",
        idea_id=id1,
        now=T0,
    )
    db_session.commit()

    ai = SmartMockAIService()  # Sadece id2 için çağrılacak

    result = run_social_content_generation(
        session_factory=SessionLocal,
        ai_service=ai,
        attempt_id=attempt.id,
        task_id="task-05",
        now_provider=lambda: T0,
    )

    assert result.status == "completed"
    assert result.already_present_idea_ids == (id1,)
    assert result.persisted_idea_ids == (id2,)
    assert result.unresolved_idea_ids == ()
    assert result.ai_calls_used == 1

    db_session.expire_all()
    db_attempt = db_session.query(SocialGenerationAttempt).filter_by(id=attempt.id).one()
    assert db_attempt.status == "completed"


def test_06_already_present_plus_rejected_partial(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """6. Bir fikir önceden kayıtlı, diğeri rejected olduğunda partial olur."""
    ctx = _setup_content_orchestration_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=2, task_id="task-06"
    )
    attempt = ctx["attempt"]
    ideas = ctx["ideas"]
    id1, id2 = ideas[0].id, ideas[1].id

    _persist_content_for_test(
        db_session,
        brief_id=ctx["brief"].id,
        attempt_id=attempt.id,
        task_id="task-06",
        idea_id=id1,
        now=T0,
    )
    db_session.commit()

    claim = _make_valid_post_dict(caption="%100 garantili doğal kür.")
    ai = SmartMockAIService(responses=[claim, claim])

    result = run_social_content_generation(
        session_factory=SessionLocal,
        ai_service=ai,
        attempt_id=attempt.id,
        task_id="task-06",
        now_provider=lambda: T0,
    )

    assert result.status == "partial"
    assert result.already_present_idea_ids == (id1,)
    assert result.persisted_idea_ids == ()
    assert result.unresolved_idea_ids == (id2,)

    db_session.expire_all()
    db_attempt = db_session.query(SocialGenerationAttempt).filter_by(id=attempt.id).one()
    assert db_attempt.status == "partial"


def test_07_all_requested_already_present_running_attempt_zero_ai_completed(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """7. Running attempt içindeki tüm fikirler zaten kayıtlıysa 0 AI çağrısıyla completed finalize edilir."""
    ctx = _setup_content_orchestration_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=1, task_id="task-07"
    )
    attempt = ctx["attempt"]
    idea = ctx["ideas"][0]

    _persist_content_for_test(
        db_session,
        brief_id=ctx["brief"].id,
        attempt_id=attempt.id,
        task_id="task-07",
        idea_id=idea.id,
        now=T0,
    )
    db_session.commit()

    ai = SmartMockAIService()

    result = run_social_content_generation(
        session_factory=SessionLocal,
        ai_service=ai,
        attempt_id=attempt.id,
        task_id="task-07",
        now_provider=lambda: T0,
    )

    assert result.status == "completed"
    assert result.already_present_idea_ids == (idea.id,)
    assert result.persisted_idea_ids == ()
    assert result.unresolved_idea_ids == ()
    assert result.ai_calls_used == 0
    assert result.replayed is False
    assert ai.call_count == 0

    db_session.expire_all()
    db_attempt = db_session.query(SocialGenerationAttempt).filter_by(id=attempt.id).one()
    assert db_attempt.status == "completed"


def test_08_completed_replay_zero_ai_zero_heartbeat_zero_writes(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """8. Completed replay durumunda 0 AI, 0 heartbeat ve 0 persistence ile replay döner."""
    ctx = _setup_content_orchestration_env(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        num_ideas=1,
        task_id="task-08",
        attempt_status="running",
    )
    attempt = ctx["attempt"]
    idea = ctx["ideas"][0]

    _persist_content_for_test(
        db_session,
        brief_id=ctx["brief"].id,
        attempt_id=attempt.id,
        task_id="task-08",
        idea_id=idea.id,
        now=T0,
    )
    finish_attempt(
        db_session,
        attempt_id=attempt.id,
        task_id="task-08",
        status="completed",
        now=T0,
    )
    db_session.commit()

    ai = SmartMockAIService()
    with patch("app.core.social.content_orchestration.heartbeat_attempt") as spy_hb:
        result = run_social_content_generation(
            session_factory=SessionLocal,
            ai_service=ai,
            attempt_id=attempt.id,
            task_id="task-08",
            now_provider=lambda: T0,
        )

        assert result.replayed is True
        assert result.status == "completed"
        assert result.ai_calls_used == 0
        assert result.persisted_idea_ids == ()
        assert result.already_present_idea_ids == (idea.id,)
        assert ai.call_count == 0
        assert spy_hb.call_count == 0


def test_09_completed_replay_missing_content_fail_closed(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """9. Completed replay'de içerik satırı eksikse fail-closed hata verir."""
    ctx = _setup_content_orchestration_env(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        num_ideas=1,
        task_id="task-09",
        attempt_status="running",
    )
    attempt = ctx["attempt"]
    finish_attempt(
        db_session,
        attempt_id=attempt.id,
        task_id="task-09",
        status="completed",
        now=T0,
    )
    db_session.commit()

    ai = SmartMockAIService()

    with pytest.raises(SocialContentWorkerInputError):
        run_social_content_generation(
            session_factory=SessionLocal,
            ai_service=ai,
            attempt_id=attempt.id,
            task_id="task-09",
            now_provider=lambda: T0,
        )


def test_10_completed_replay_corrupt_or_stale_content_fail_closed(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """10. Completed replay'de içerik satırı bozuksa fail-closed hata verir."""
    ctx = _setup_content_orchestration_env(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        num_ideas=1,
        task_id="task-10",
        attempt_status="running",
    )
    attempt = ctx["attempt"]
    idea = ctx["ideas"][0]

    _persist_content_for_test(
        db_session,
        brief_id=ctx["brief"].id,
        attempt_id=attempt.id,
        task_id="task-10",
        idea_id=idea.id,
        now=T0,
    )
    # İçeriği kasıtlı olarak boz (actual_duration_sec pozitif olmalı iken 0 yap veya format_payload boz)
    row = db_session.query(SocialContent).filter_by(idea_id=idea.id).one()
    row.brief_id = None
    finish_attempt(
        db_session,
        attempt_id=attempt.id,
        task_id="task-10",
        status="completed",
        now=T0,
    )
    db_session.commit()

    ai = SmartMockAIService()

    with pytest.raises((SocialContentWorkerInputError, SocialContentPersistenceError)):
        run_social_content_generation(
            session_factory=SessionLocal,
            ai_service=ai,
            attempt_id=attempt.id,
            task_id="task-10",
            now_provider=lambda: T0,
        )


def test_11_heartbeat_called_after_each_finished_work_item(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """11. Her tamamlanan work item sonrasında heartbeat çağrılır."""
    ctx = _setup_content_orchestration_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=2, task_id="task-11"
    )
    attempt = ctx["attempt"]
    ai = SmartMockAIService()

    with patch("app.core.social.content_orchestration.heartbeat_attempt", wraps=heartbeat_attempt) as spy_hb:
        result = run_social_content_generation(
            session_factory=SessionLocal,
            ai_service=ai,
            attempt_id=attempt.id,
            task_id="task-11",
            now_provider=lambda: T0,
        )

        assert spy_hb.call_count == 2
        assert result.status == "completed"


def test_12_heartbeat_not_called_for_already_present_and_replay(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """12. Already-present olan öğeler için heartbeat çağrılmaz."""
    ctx = _setup_content_orchestration_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=2, task_id="task-12"
    )
    attempt = ctx["attempt"]
    id1 = ctx["ideas"][0].id

    # id1 önceden mevcut
    _persist_content_for_test(
        db_session,
        brief_id=ctx["brief"].id,
        attempt_id=attempt.id,
        task_id="task-12",
        idea_id=id1,
        now=T0,
    )
    db_session.commit()

    ai = SmartMockAIService()
    with patch("app.core.social.content_orchestration.heartbeat_attempt", wraps=heartbeat_attempt) as spy_hb:
        result = run_social_content_generation(
            session_factory=SessionLocal,
            ai_service=ai,
            attempt_id=attempt.id,
            task_id="task-12",
            now_provider=lambda: T0,
        )

        # Sadece 2. öğe için heartbeat çağrılmalı
        assert spy_hb.call_count == 1
        assert result.status == "completed"


def test_13_heartbeat_failure_terminates_batch_not_converted_to_warning(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """13. Heartbeat hatası batch'i derhal sonlandırır ve warning'e dönüştürülmez."""
    ctx = _setup_content_orchestration_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=2, task_id="task-13"
    )
    attempt = ctx["attempt"]
    ai = SmartMockAIService()

    def failing_hb(*args, **kwargs):
        raise AttemptNotWritableError("Lock hatası", error_code="TASK_MISMATCH")

    with patch("app.core.social.content_orchestration.heartbeat_attempt", side_effect=failing_hb):
        with pytest.raises((SocialContentHeartbeatError, AttemptNotWritableError)):
            run_social_content_generation(
                session_factory=SessionLocal,
                ai_service=ai,
                attempt_id=attempt.id,
                task_id="task-13",
                now_provider=lambda: T0,
            )

    db_session.expire_all()
    count = db_session.query(SocialContent).filter_by(brief_id=ctx["brief"].id).count()
    assert count == 0


def test_14_lease_expired_worker_cannot_persist_content(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """14. Lease süresi dolan worker persistence yapamaz, attempt failed edilir."""
    ctx = _setup_content_orchestration_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=1, task_id="task-14"
    )
    attempt = ctx["attempt"]

    def expire_lease(call_count, prompt, kwargs):
        # AI anında lease süresini geriye al
        s = SessionLocal()
        try:
            att = s.query(SocialGenerationAttempt).filter_by(id=attempt.id).one()
            att.lease_expires_at = T0 - timedelta(seconds=60)
            s.commit()
        finally:
            s.close()

    ai = SmartMockAIService(on_call=expire_lease)

    with pytest.raises((AttemptNotWritableError, SocialContentPersistenceError)):
        run_social_content_generation(
            session_factory=SessionLocal,
            ai_service=ai,
            attempt_id=attempt.id,
            task_id="task-14",
            now_provider=lambda: T0,
        )

    db_session.expire_all()
    db_attempt = db_session.query(SocialGenerationAttempt).filter_by(id=attempt.id).one()
    assert db_attempt.status == "failed"
    assert db_attempt.reason_code == "worker_lost"


def test_15_task_mismatch_worker_cannot_persist_or_finalize(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """15. Farklı task_id attempt'i devraldıysa eski worker persistence/finalize yapamaz."""
    ctx = _setup_content_orchestration_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=1, task_id="task-15"
    )
    attempt = ctx["attempt"]

    def change_task(call_count, prompt, kwargs):
        s = SessionLocal()
        try:
            att = s.query(SocialGenerationAttempt).filter_by(id=attempt.id).one()
            att.task_id = "alien-task-999"
            s.commit()
        finally:
            s.close()

    ai = SmartMockAIService(on_call=change_task)

    with pytest.raises((AttemptNotWritableError, SocialContentPersistenceError)):
        run_social_content_generation(
            session_factory=SessionLocal,
            ai_service=ai,
            attempt_id=attempt.id,
            task_id="task-15",
            now_provider=lambda: T0,
        )


def test_16_brief_stale_during_ai_persistence_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """16. AI çağrısı sırasında brief stale olursa persistence BRIEF_STALE ile reddedilir."""
    ctx = _setup_content_orchestration_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=1, task_id="task-16"
    )
    attempt = ctx["attempt"]
    brief = ctx["brief"]

    def make_brief_stale(call_count, prompt, kwargs):
        s = SessionLocal()
        try:
            b = s.query(SocialBrief).filter_by(id=brief.id).one()
            b.is_stale = True
            s.commit()
        finally:
            s.close()

    ai = SmartMockAIService(on_call=make_brief_stale)

    with pytest.raises((AttemptNotWritableError, SocialContentPersistenceError)):
        run_social_content_generation(
            session_factory=SessionLocal,
            ai_service=ai,
            attempt_id=attempt.id,
            task_id="task-16",
            now_provider=lambda: T0,
        )

    db_session.expire_all()
    db_attempt = db_session.query(SocialGenerationAttempt).filter_by(id=attempt.id).one()
    assert db_attempt.status == "failed"
    assert db_attempt.reason_code == "brief_stale"


def test_17_assignment_version_changed_during_ai_persistence_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """17. AI sırasında channel assignment version değişirse persistence ASSIGNMENT_CHANGED ile reddedilir."""
    ctx = _setup_content_orchestration_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=1, task_id="task-17"
    )
    attempt = ctx["attempt"]
    brief = ctx["brief"]

    def bump_assignment(call_count, prompt, kwargs):
        s = SessionLocal()
        try:
            b = s.query(SocialBrief).filter_by(id=brief.id).one()
            b.channel_assignment_version += 1
            s.commit()
        finally:
            s.close()

    ai = SmartMockAIService(on_call=bump_assignment)

    with pytest.raises((AttemptNotWritableError, SocialContentPersistenceError)):
        run_social_content_generation(
            session_factory=SessionLocal,
            ai_service=ai,
            attempt_id=attempt.id,
            task_id="task-17",
            now_provider=lambda: T0,
        )

    db_session.expire_all()
    db_attempt = db_session.query(SocialGenerationAttempt).filter_by(id=attempt.id).one()
    assert db_attempt.status == "failed"
    assert db_attempt.reason_code == "assignment_changed"


def test_18_second_accepted_item_error_rolls_back_first_accepted_item(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """18. İkinci accepted item persistence sırasında hata verirse ilk item de rollback edilir."""
    ctx = _setup_content_orchestration_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=2, task_id="task-18"
    )
    attempt = ctx["attempt"]
    ai = SmartMockAIService()

    orig_persist = persist_social_content
    persist_call_count = [0]

    def mock_persist(*args, **kwargs):
        persist_call_count[0] += 1
        if persist_call_count[0] == 2:
            raise SocialContentPersistenceError(
                "Simüle persistence patlaması", error_code="CONTENT_PERSISTENCE_FAILED"
            )
        return orig_persist(*args, **kwargs)

    with patch("app.core.social.content_orchestration.persist_social_content", side_effect=mock_persist):
        with pytest.raises(SocialContentPersistenceError):
            run_social_content_generation(
                session_factory=SessionLocal,
                ai_service=ai,
                attempt_id=attempt.id,
                task_id="task-18",
                now_provider=lambda: T0,
            )

    db_session.expire_all()
    count = db_session.query(SocialContent).filter_by(brief_id=ctx["brief"].id).count()
    # İlk item kesinlikle DB'de kalmamalıdır (atomik rollback)
    assert count == 0


def test_19_safe_failure_finalization_runs_after_persistence_failure(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """19. Persistence hatasından sonra bağımsız oturumla attempt failed durumuna çekilir."""
    ctx = _setup_content_orchestration_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=1, task_id="task-19"
    )
    attempt = ctx["attempt"]
    ai = SmartMockAIService()

    with patch(
        "app.core.social.content_orchestration.persist_social_content",
        side_effect=SocialContentPersistenceError(
            "DB diski dolu", error_code="CONTENT_PERSISTENCE_FAILED"
        ),
    ):
        with pytest.raises(SocialContentPersistenceError):
            run_social_content_generation(
                session_factory=SessionLocal,
                ai_service=ai,
                attempt_id=attempt.id,
                task_id="task-19",
                now_provider=lambda: T0,
            )

    db_session.expire_all()
    db_attempt = db_session.query(SocialGenerationAttempt).filter_by(id=attempt.id).one()
    assert db_attempt.status == "failed"
    assert db_attempt.reason_code == "content_persistence_failed"


def test_20_failure_finalizer_error_does_not_mask_original_exception(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """20. Failure finalizer hata verirse asıl exception maskelenmez."""
    ctx = _setup_content_orchestration_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=1, task_id="task-20"
    )
    attempt = ctx["attempt"]
    ai = SmartMockAIService()

    def exploding_finalizer(*args, **kwargs):
        raise RuntimeError("Finalizer DB bağlantısı koptu")

    with patch("app.core.social.content_orchestration.finalize_contents_attempt_failure", side_effect=exploding_finalizer):
        with patch(
            "app.core.social.content_orchestration.persist_social_content",
            side_effect=SocialContentPersistenceError(
                "Asıl persistence hatası", error_code="CONTENT_PERSISTENCE_FAILED"
            ),
        ):
            with pytest.raises(SocialContentPersistenceError) as exc_info:
                run_social_content_generation(
                    session_factory=SessionLocal,
                    ai_service=ai,
                    attempt_id=attempt.id,
                    task_id="task-20",
                    now_provider=lambda: T0,
                )
            assert "Asıl persistence hatası" in str(exc_info.value)


def test_21_warning_json_contains_exact_fields(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """21. Warning JSON nesnesi tam ve eksiksiz alanları içerir."""
    ctx = _setup_content_orchestration_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=2, task_id="task-21"
    )
    attempt = ctx["attempt"]

    clean = _make_valid_post_dict(caption="Doğal cilt bakımı.")
    claim = _make_valid_post_dict(caption="%100 garantili kesin sonuç.")
    ai = SmartMockAIService(responses=[clean, claim, claim])

    run_social_content_generation(
        session_factory=SessionLocal,
        ai_service=ai,
        attempt_id=attempt.id,
        task_id="task-21",
        now_provider=lambda: T0,
    )

    db_session.expire_all()
    db_attempt = db_session.query(SocialGenerationAttempt).filter_by(id=attempt.id).one()
    assert isinstance(db_attempt.warnings, list)
    assert len(db_attempt.warnings) == 1
    w = db_attempt.warnings[0]
    assert set(w.keys()) == {"idea_id", "reason_code", "claims", "ai_calls_used"}


def test_22_provider_sql_raw_exception_does_not_leak(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """22. Hassas veritabanı veya provider hata detayları warning ve error_message alanlarına sızmaz."""
    ctx = _setup_content_orchestration_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=2, task_id="task-22"
    )
    attempt = ctx["attempt"]

    clean = _make_valid_post_dict(caption="Doğal cilt bakımı.")
    secret_err = Exception("SELECT password_hash FROM secret_table; API KEY AIzaSySecretKey leaked")
    ai = SmartMockAIService(responses=[clean, secret_err])

    run_social_content_generation(
        session_factory=SessionLocal,
        ai_service=ai,
        attempt_id=attempt.id,
        task_id="task-22",
        now_provider=lambda: T0,
    )

    db_session.expire_all()
    db_attempt = db_session.query(SocialGenerationAttempt).filter_by(id=attempt.id).one()
    assert "password_hash" not in str(db_attempt.warnings)
    assert "AIzaSySecretKey" not in str(db_attempt.warnings)
    assert "secret_table" not in str(db_attempt.warnings)


def test_23_content_rejected_claims_preserved(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """23. content_rejected durumunda tespit edilen iddia metinleri claims içinde saklanır."""
    ctx = _setup_content_orchestration_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=2, task_id="task-23"
    )
    attempt = ctx["attempt"]

    clean = _make_valid_post_dict(caption="Doğal cilt bakımı.")
    claim = _make_valid_post_dict(caption="%100 garantili mucizevi kür.")
    ai = SmartMockAIService(responses=[clean, claim, claim])

    result = run_social_content_generation(
        session_factory=SessionLocal,
        ai_service=ai,
        attempt_id=attempt.id,
        task_id="task-23",
        now_provider=lambda: T0,
    )

    assert len(result.warnings) == 1
    assert result.warnings[0].reason_code == "content_rejected"
    assert len(result.warnings[0].claims) > 0

    db_session.expire_all()
    db_attempt = db_session.query(SocialGenerationAttempt).filter_by(id=attempt.id).one()
    assert len(db_attempt.warnings[0]["claims"]) > 0


def test_24_other_warning_types_have_empty_claims(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """24. İddia dışı hata warning türlerinde claims boş tuple ve boş liste olur."""
    ctx = _setup_content_orchestration_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=2, task_id="task-24"
    )
    attempt = ctx["attempt"]

    clean = _make_valid_post_dict(caption="Doğal cilt bakımı.")
    provider_err = RuntimeError("AI servisi zaman aşımına uğradı")
    ai = SmartMockAIService(responses=[clean, provider_err])

    result = run_social_content_generation(
        session_factory=SessionLocal,
        ai_service=ai,
        attempt_id=attempt.id,
        task_id="task-24",
        now_provider=lambda: T0,
    )

    assert len(result.warnings) == 1
    assert result.warnings[0].reason_code == "content_provider_error"
    assert result.warnings[0].claims == ()

    db_session.expire_all()
    db_attempt = db_session.query(SocialGenerationAttempt).filter_by(id=attempt.id).one()
    assert db_attempt.warnings[0]["claims"] == []


def test_25_contents_request_v1_request_snapshot_in_attempt_coverage_preserved(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """25. contents_request_v1 istek snapshot'ı attempt finalization sonrasında coverage içinde korunur."""
    ctx = _setup_content_orchestration_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=1, task_id="task-25"
    )
    attempt = ctx["attempt"]
    ai = SmartMockAIService()

    run_social_content_generation(
        session_factory=SessionLocal,
        ai_service=ai,
        attempt_id=attempt.id,
        task_id="task-25",
        now_provider=lambda: T0,
    )

    db_session.expire_all()
    db_attempt = db_session.query(SocialGenerationAttempt).filter_by(id=attempt.id).one()
    assert isinstance(db_attempt.coverage, dict)
    assert db_attempt.coverage.get("schema_version") == "contents_request_v1"
    assert "request" in db_attempt.coverage


def test_26_persistence_forged_wrong_attempt_id_fail_closed(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """26. Yanlış attempt_id ile persist_social_content çağrısı fail-closed reddedilir."""
    ctx = _setup_content_orchestration_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=1, task_id="task-26"
    )
    attempt = ctx["attempt"]
    idea = ctx["ideas"][0]
    content_obj = _make_valid_content_object("instagram", "post")
    decision = _make_quality_decision()

    with pytest.raises(SocialContentPersistenceError) as exc_info:
        persist_social_content(
            db_session,
            brief_id=ctx["brief"].id,
            attempt_id=attempt.id + 9999,
            task_id="task-26",
            idea_id=idea.id,
            content=content_obj,
            quality_decision=decision,
            now=T0,
        )
    assert exc_info.value.error_code in (CONTENT_PERSISTENCE_ATTEMPT_NOT_WRITABLE, "CONTENT_PERSISTENCE_INCONSISTENT")


def test_27_persistence_forged_wrong_idea_id_fail_closed(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """27. Brief'e ait olmayan idea_id ile persist_social_content çağrısı fail-closed reddedilir."""
    ctx = _setup_content_orchestration_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=1, task_id="task-27"
    )
    attempt = ctx["attempt"]
    content_obj = _make_valid_content_object("instagram", "post")
    decision = _make_quality_decision()

    with pytest.raises(SocialContentPersistenceError):
        persist_social_content(
            db_session,
            brief_id=ctx["brief"].id,
            attempt_id=attempt.id,
            task_id="task-27",
            idea_id=999999,
            content=content_obj,
            quality_decision=decision,
            now=T0,
        )


def test_28_no_open_db_session_during_ai_execution(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """28. AI çağrısı anında açık DB transaction bulunmaz ve satır kilidi tutulmaz."""
    ctx = _setup_content_orchestration_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=1, task_id="task-28"
    )
    attempt = ctx["attempt"]
    can_lock_nowait = []

    def on_ai_call(call_count, prompt, kwargs):
        probe = SessionLocal()
        try:
            # Eğer ana oturum satır kilidi tutsaydı NOWAIT anında patlardı
            probe.execute(
                text("SELECT id FROM social_generation_attempts WHERE id = :id FOR UPDATE NOWAIT"),
                {"id": attempt.id},
            )
            can_lock_nowait.append(True)
            probe.commit()
        except Exception:
            can_lock_nowait.append(False)
            probe.rollback()
        finally:
            probe.close()

    ai = SmartMockAIService(on_call=on_ai_call)

    run_social_content_generation(
        session_factory=SessionLocal,
        ai_service=ai,
        attempt_id=attempt.id,
        task_id="task-28",
        now_provider=lambda: T0,
    )

    assert can_lock_nowait == [True]


def test_29_persistence_and_attempt_finalization_atomic_in_same_transaction(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """29. Persistence ve finish_attempt aynı transaction içindedir; finish hatasında içerik rollback olur."""
    ctx = _setup_content_orchestration_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=1, task_id="task-29"
    )
    attempt = ctx["attempt"]
    ai = SmartMockAIService()

    def exploding_finish(*args, **kwargs):
        raise RuntimeError("Finish attempt sırasında simüle hata")

    with patch("app.core.social.content_orchestration.finish_attempt", side_effect=exploding_finish):
        with pytest.raises(RuntimeError):
            run_social_content_generation(
                session_factory=SessionLocal,
                ai_service=ai,
                attempt_id=attempt.id,
                task_id="task-29",
                now_provider=lambda: T0,
            )

    db_session.expire_all()
    count = db_session.query(SocialContent).filter_by(brief_id=ctx["brief"].id).count()
    assert count == 0


def test_30_orchestration_result_dto_invariants_and_immutability():
    """30. SocialContentOrchestrationResult DTO invariant ve immutability kontrollerini doğrular."""
    valid_res = SocialContentOrchestrationResult(
        attempt_id=1,
        brief_id=2,
        scoring_run_id=3,
        status="completed",
        requested_idea_ids=(10, 20),
        already_present_idea_ids=(),
        persisted_idea_ids=(10, 20),
        unresolved_idea_ids=(),
        warnings=(),
        ai_calls_used=2,
        replayed=False,
    )

    with pytest.raises(FrozenInstanceError):
        valid_res.status = "failed"

    # Ayrık olmayan kümeler hatası
    with pytest.raises(SocialContentOrchestrationError):
        SocialContentOrchestrationResult(
            attempt_id=1,
            brief_id=2,
            scoring_run_id=3,
            status="completed",
            requested_idea_ids=(10, 20),
            already_present_idea_ids=(10,),
            persisted_idea_ids=(10, 20),
            unresolved_idea_ids=(),
            warnings=(),
            ai_calls_used=2,
            replayed=False,
        )

    # Geçersiz status
    with pytest.raises(SocialContentOrchestrationError):
        SocialContentOrchestrationResult(
            attempt_id=1,
            brief_id=2,
            scoring_run_id=3,
            status="unknown_status",
            requested_idea_ids=(10,),
            already_present_idea_ids=(),
            persisted_idea_ids=(10,),
            unresolved_idea_ids=(),
            warnings=(),
            ai_calls_used=1,
            replayed=False,
        )


def test_31_temperature_none_preserved_across_all_calls(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """31. Tüm yapay zeka çağrılarında temperature=None korunur (asla 0 kullanılmaz)."""
    ctx = _setup_content_orchestration_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=2, task_id="task-31"
    )
    attempt = ctx["attempt"]
    ai = SmartMockAIService()

    run_social_content_generation(
        session_factory=SessionLocal,
        ai_service=ai,
        attempt_id=attempt.id,
        task_id="task-31",
        now_provider=lambda: T0,
    )

    assert len(ai.calls) == 2
    for call in ai.calls:
        assert call["kwargs"].get("temperature") is None
        assert call["kwargs"].get("temperature") != 0


def test_32_replay_does_not_create_duplicate_social_content_rows(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """32. Replay durumunda SocialContent tablosunda mükerrer satır oluşmaz."""
    ctx = _setup_content_orchestration_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=1, task_id="task-32"
    )
    attempt = ctx["attempt"]
    ai = SmartMockAIService()

    # İlk çalıştırma -> completed
    res1 = run_social_content_generation(
        session_factory=SessionLocal,
        ai_service=ai,
        attempt_id=attempt.id,
        task_id="task-32",
        now_provider=lambda: T0,
    )
    assert res1.status == "completed"

    db_session.expire_all()
    count_after_first = db_session.query(SocialContent).filter_by(brief_id=ctx["brief"].id).count()
    assert count_after_first == 1

    # İkinci çalıştırma -> replay
    res2 = run_social_content_generation(
        session_factory=SessionLocal,
        ai_service=ai,
        attempt_id=attempt.id,
        task_id="task-32",
        now_provider=lambda: T0,
    )
    assert res2.replayed is True
    assert res2.status == "completed"

    db_session.expire_all()
    count_after_second = db_session.query(SocialContent).filter_by(brief_id=ctx["brief"].id).count()
    assert count_after_second == 1


def test_33_non_ascending_snapshot_order_orchestrated_successfully(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """33. requested_idea_ids=(id2, id1) gibi sayısal artan olmayan snapshot sırası uçtan uca kabul edilir."""
    ctx = _setup_content_orchestration_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=2, task_id="task-33"
    )
    attempt = ctx["attempt"]
    ideas = ctx["ideas"]
    id1, id2 = ideas[0].id, ideas[1].id
    assert id1 < id2

    # Snapshot sırasını ters çevir: (id2, id1)
    cov = copy.deepcopy(attempt.coverage)
    cov["request"]["idea_ids"] = [id2, id1]
    attempt.coverage = cov
    attempt.requested_idea_ids = [id2, id1]
    db_session.commit()

    ai = SmartMockAIService()
    result = run_social_content_generation(
        session_factory=SessionLocal,
        ai_service=ai,
        attempt_id=attempt.id,
        task_id="task-33",
        now_provider=lambda: T0,
    )

    assert result.status == "completed"
    assert result.requested_idea_ids == (id2, id1)
    assert result.persisted_idea_ids == (id2, id1)
    assert result.already_present_idea_ids == ()
    assert result.unresolved_idea_ids == ()


def test_34_result_dto_preserves_snapshot_order_for_all_partition_fields(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """34. already_present, persisted ve unresolved alanları tam olarak snapshot sırasını korur."""
    ctx = _setup_content_orchestration_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=3, task_id="task-34"
    )
    attempt = ctx["attempt"]
    ideas = ctx["ideas"]
    id1, id2, id3 = ideas[0].id, ideas[1].id, ideas[2].id

    # Snapshot sırası: (id3, id1, id2)
    cov = copy.deepcopy(attempt.coverage)
    cov["request"]["idea_ids"] = [id3, id1, id2]
    attempt.coverage = cov
    attempt.requested_idea_ids = [id3, id1, id2]
    db_session.commit()

    # id3'ü önceden kaydet (already_present olacak)
    _persist_content_for_test(
        db_session,
        brief_id=ctx["brief"].id,
        attempt_id=attempt.id,
        idea_id=id3,
        task_id="task-34",
    )
    db_session.commit()

    # id1 accepted, id2 rejected olacak
    resp_clean = _make_valid_post_dict(caption="Doğal ve temiz içerik.")
    resp_dirty1 = _make_valid_post_dict(caption="%80 başarı formülü!")
    resp_dirty2 = _make_valid_post_dict(caption="Hala %80 başarı!")
    ai = SmartMockAIService(responses=[resp_clean, resp_dirty1, resp_dirty2])

    result = run_social_content_generation(
        session_factory=SessionLocal,
        ai_service=ai,
        attempt_id=attempt.id,
        task_id="task-34",
        now_provider=lambda: T0,
    )

    assert result.status == "partial"
    assert result.requested_idea_ids == (id3, id1, id2)
    assert result.already_present_idea_ids == (id3,)
    assert result.persisted_idea_ids == (id1,)
    assert result.unresolved_idea_ids == (id2,)
    assert len(result.warnings) == 1
    assert result.warnings[0].idea_id == id2


def test_35_dto_invariants_allow_non_ascending_requested_idea_ids():
    """35. SocialContentOrchestrationResult sayısal artan sıra zorlamaz, snapshot sırasını korur."""
    res = SocialContentOrchestrationResult(
        attempt_id=1,
        brief_id=2,
        scoring_run_id=3,
        status="completed",
        requested_idea_ids=(103, 101, 105),
        already_present_idea_ids=(),
        persisted_idea_ids=(103, 101, 105),
        unresolved_idea_ids=(),
        warnings=(),
        ai_calls_used=3,
        replayed=False,
    )
    assert res.requested_idea_ids == (103, 101, 105)
    assert res.persisted_idea_ids == (103, 101, 105)

    # Sırayı bozan persisted tuple'ı reddedilir
    with pytest.raises(SocialContentOrchestrationError) as exc_info:
        SocialContentOrchestrationResult(
            attempt_id=1,
            brief_id=2,
            scoring_run_id=3,
            status="completed",
            requested_idea_ids=(103, 101, 105),
            already_present_idea_ids=(),
            persisted_idea_ids=(101, 103, 105),  # Yanlış sıra!
            unresolved_idea_ids=(),
            warnings=(),
            ai_calls_used=3,
            replayed=False,
        )
    assert "requested sırasını korumalıdır" in str(exc_info.value)


def test_36_dto_construction_failure_rolls_back_persistence_and_fails_attempt(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """36. DTO oluşturma hatasında hiçbir yeni içerik commit edilmez, attempt completed olmaz, failed finalize edilir."""
    ctx = _setup_content_orchestration_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=1, task_id="task-36"
    )
    attempt = ctx["attempt"]
    ai = SmartMockAIService()

    with patch(
        "app.core.social.content_orchestration.SocialContentOrchestrationResult",
        side_effect=SocialContentOrchestrationError("Simüle DTO invariant hatası", error_code="CONTENT_ORCHESTRATION_INVALID_RESULT"),
    ):
        with pytest.raises(SocialContentOrchestrationError):
            run_social_content_generation(
                session_factory=SessionLocal,
                ai_service=ai,
                attempt_id=attempt.id,
                task_id="task-36",
                now_provider=lambda: T0,
            )

    db_session.expire_all()
    # İçerik kesinlikle commit edilmemiş olmalı
    count = db_session.query(SocialContent).filter_by(brief_id=ctx["brief"].id).count()
    assert count == 0

    # Attempt completed değil, failed finalize edilmiş olmalı
    db_attempt = db_session.query(SocialGenerationAttempt).filter_by(id=attempt.id).one()
    assert db_attempt.status == "failed"
    assert db_attempt.reason_code == "content_persistence_failed"


def test_37_commit_exception_rolls_back_persistence_and_fails_attempt(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """37. Commit aşamasında exception oluşursa içerikler ve terminal durum rollback edilir, failure finalization çalışır."""
    ctx = _setup_content_orchestration_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=1, task_id="task-37"
    )
    attempt = ctx["attempt"]
    ai = SmartMockAIService()

    orig_commit = Session.commit

    commit_called = [0]

    def crashing_commit(session_instance):
        commit_called[0] += 1
        # Commit 1: Transaction A prep
        # Commit 2: Heartbeat item 1
        # Commit 3: Transaction C persistence
        if commit_called[0] == 3:
            raise RuntimeError("Veritabanı commit bağlantısı koptu")
        return orig_commit(session_instance)

    with patch.object(Session, "commit", autospec=True, side_effect=crashing_commit):
        with pytest.raises(RuntimeError) as exc_info:
            run_social_content_generation(
                session_factory=SessionLocal,
                ai_service=ai,
                attempt_id=attempt.id,
                task_id="task-37",
                now_provider=lambda: T0,
            )
        assert "bağlantısı koptu" in str(exc_info.value)

    db_session.expire_all()
    # İçerik satırı kalmamalı
    count = db_session.query(SocialContent).filter_by(brief_id=ctx["brief"].id).count()
    assert count == 0

    db_attempt = db_session.query(SocialGenerationAttempt).filter_by(id=attempt.id).one()
    assert db_attempt.status == "failed"


def test_38_final_db_coverage_validation_checks(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """38. validate_social_content_attempt_coverage; eksik, stale, unresolved aktif ve bozuk format durumlarını tespit eder."""
    ctx = _setup_content_orchestration_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=2, task_id="task-38"
    )
    brief = ctx["brief"]
    ideas = ctx["ideas"]
    id1, id2 = ideas[0].id, ideas[1].id

    # 1. Başarılı beklenen içerik eksik -> HATA
    with pytest.raises(SocialContentPersistenceError) as exc_1:
        validate_social_content_attempt_coverage(
            db_session,
            brief_id=brief.id,
            requested_idea_ids=(id1, id2),
            expected_successful_idea_ids=(id1,),
            expected_unresolved_idea_ids=(id2,),
        )
    assert "aktif içerik bulunamadı" in str(exc_1.value)

    # 2. id1 içeriğini kaydet -> doğrulanır
    _persist_content_for_test(
        db_session,
        brief_id=brief.id,
        attempt_id=ctx["attempt"].id,
        idea_id=id1,
        task_id="task-38",
    )
    db_session.commit()

    succ = validate_social_content_attempt_coverage(
        db_session,
        brief_id=brief.id,
        requested_idea_ids=(id1, id2),
        expected_successful_idea_ids=(id1,),
        expected_unresolved_idea_ids=(id2,),
    )
    assert succ == (id1,)

    # 3. id2 unresolved beklenirken DB'de aktif içerik bulunursa -> HATA
    _persist_content_for_test(
        db_session,
        brief_id=brief.id,
        attempt_id=ctx["attempt"].id,
        idea_id=id2,
        task_id="task-38",
    )
    db_session.commit()

    with pytest.raises(SocialContentPersistenceError) as exc_2:
        validate_social_content_attempt_coverage(
            db_session,
            brief_id=brief.id,
            requested_idea_ids=(id1, id2),
            expected_successful_idea_ids=(id1,),
            expected_unresolved_idea_ids=(id2,),
        )
    assert "Unresolved fikir için veritabanında aktif içerik bulundu" in str(exc_2.value)

    # 4. id1 içeriği stale işaretlenirse -> HATA
    c1 = db_session.query(SocialContent).filter_by(idea_id=id1).one()
    c1.is_stale = True
    db_session.commit()

    with pytest.raises(SocialContentPersistenceError) as exc_3:
        validate_social_content_attempt_coverage(
            db_session,
            brief_id=brief.id,
            requested_idea_ids=(id1, id2),
            expected_successful_idea_ids=(id1, id2),
            expected_unresolved_idea_ids=(),
        )
    assert "aktif içerik bulunamadı" in str(exc_3.value)


def test_39_final_db_coverage_validator_does_not_commit_or_rollback(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """39. validate_social_content_attempt_coverage asla db.commit() veya db.rollback() çağırmaz."""
    ctx = _setup_content_orchestration_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=1, task_id="task-39"
    )
    brief = ctx["brief"]
    idea = ctx["ideas"][0]

    _persist_content_for_test(
        db_session,
        brief_id=brief.id,
        attempt_id=ctx["attempt"].id,
        idea_id=idea.id,
        task_id="task-39",
    )
    db_session.commit()

    commit_mock = patch.object(Session, "commit")
    rollback_mock = patch.object(Session, "rollback")

    with commit_mock as m_commit, rollback_mock as m_rollback:
        validate_social_content_attempt_coverage(
            db_session,
            brief_id=brief.id,
            requested_idea_ids=(idea.id,),
            expected_successful_idea_ids=(idea.id,),
            expected_unresolved_idea_ids=(),
        )
        assert m_commit.call_count == 0
        assert m_rollback.call_count == 0


def test_40_status_coverage_parity_completed_partial_failed_invariants():
    """40. SocialContentOrchestrationResult; completed, partial ve failed durumlarında coverage paritesini zorlar."""
    # completed: unresolved boş olmalı
    with pytest.raises(SocialContentOrchestrationError):
        SocialContentOrchestrationResult(
            attempt_id=1,
            brief_id=2,
            scoring_run_id=3,
            status="completed",
            requested_idea_ids=(10, 20),
            already_present_idea_ids=(),
            persisted_idea_ids=(10,),
            unresolved_idea_ids=(20,),
            warnings=(SocialContentBatchWarning(20, "content_rejected", (), 1),),
            ai_calls_used=2,
            replayed=False,
        )

    # partial: hem başarılı hem unresolved olmalı
    with pytest.raises(SocialContentOrchestrationError):
        SocialContentOrchestrationResult(
            attempt_id=1,
            brief_id=2,
            scoring_run_id=3,
            status="partial",
            requested_idea_ids=(10,),
            already_present_idea_ids=(),
            persisted_idea_ids=(10,),
            unresolved_idea_ids=(),
            warnings=(),
            ai_calls_used=1,
            replayed=False,
        )

    # failed: başarılı fikir bulunmamalı
    with pytest.raises(SocialContentOrchestrationError):
        SocialContentOrchestrationResult(
            attempt_id=1,
            brief_id=2,
            scoring_run_id=3,
            status="failed",
            requested_idea_ids=(10, 20),
            already_present_idea_ids=(),
            persisted_idea_ids=(10,),
            unresolved_idea_ids=(20,),
            warnings=(SocialContentBatchWarning(20, "content_rejected", (), 1),),
            ai_calls_used=2,
            replayed=False,
        )


def test_41_contents_request_v1_request_snapshot_untouched_after_orchestration(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """41. finish_attempt çağrısında coverage=None verilir ve contents_request_v1 request snapshot'ı korunur."""
    ctx = _setup_content_orchestration_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=1, task_id="task-41"
    )
    attempt = ctx["attempt"]
    orig_coverage = dict(attempt.coverage)
    ai = SmartMockAIService()

    result = run_social_content_generation(
        session_factory=SessionLocal,
        ai_service=ai,
        attempt_id=attempt.id,
        task_id="task-41",
        now_provider=lambda: T0,
    )
    assert result.status == "completed"

    db_session.expire_all()
    db_attempt = db_session.query(SocialGenerationAttempt).filter_by(id=attempt.id).one()
    assert db_attempt.coverage == orig_coverage
    assert db_attempt.coverage["schema_version"] == "contents_request_v1"
    assert db_attempt.coverage["request"]["idea_ids"] == [ctx["ideas"][0].id]

