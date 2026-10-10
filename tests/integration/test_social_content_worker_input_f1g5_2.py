# -*- coding: utf-8 -*-
"""Entegrasyon Testleri: Sosyal İçerik Worker Input ve Snapshot Hazırlığı (F1-G.5.2).

Bu modül Micro-Phase F1-G.5.2 gereksinimlerini ve 22 kritik senaryoyu
izole test DB üzerinde kapsamlı biçimde doğrular.
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
import uuid

import pytest
from sqlalchemy.orm import Session

from app.config import settings
from app.core.social.content_contract import (
    ContentTargetSpec,
    ValidatedCarouselPayload,
    ValidatedCarouselSlide,
    ValidatedHook,
    ValidatedSocialContent,
    ValidatedThreadPayload,
    ValidatedThreadPost,
    ValidatedVideoPayload,
    ValidatedVideoSegment,
    render_legacy_scenario,
    serialize_content_format_payload,
)
from app.core.social.content_persistence import (
    persist_social_content,
)
from app.core.social.content_quality import (
    SocialContentQualityDecision,
)
from app.core.social.content_worker_input import (
    CONTENT_WORKER_INPUT_INCONSISTENT,
    CONTENT_WORKER_INPUT_INVALID,
    SocialContentRequestSnapshot,
    SocialContentWorkItem,
    SocialContentWorkerInputError,
    SocialContentWorkerPreparation,
    extract_social_content_request_snapshot,
    prepare_social_content_worker_inputs,
)
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
    claim_contents_attempt_for_worker,
    claim_ideas_attempt_for_worker,
    claim_ideas_retry_attempt_for_worker,
)
from app.generators.social.format_matrix import get_duration_preset

T0 = datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def enable_flag(monkeypatch):
    """Test süresince feature flag'i aktif kılar."""
    monkeypatch.setattr(settings, "ENABLE_SOCIAL_BRIEF_FLOW", True)
    yield


# ==================== YARDIMCI KURULUM FONKSİYONLARI ====================


def _setup_base_env(
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
    brand_name: str = "Test Brand Worker",
):
    """Workspace, scoring run ve keyword oluşturur."""
    ws = make_workspace(name=brand_name, status="confirmed")
    run = make_scoring_run(
        brand_profile_id=ws.id,
        status="completed",
        algorithm_version="v2",
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


def _setup_content_attempt_context(
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
    *,
    platform: str = "instagram",
    content_format: str = "post",
    duration_preset_id: str | None = None,
    task_id: str | None = None,
    attempt_status: str = "running",
    attempt_stage: str = "contents",
    brief_stale: bool = False,
    brief_locked: bool = True,
    channel_version_mismatch: bool = False,
    idea_stale: bool = False,
    cat_stale: bool = False,
    product_facts: str | None = "Doğrulanmış ürün içeriği",
    trusted_brand_usp: str | None = "En güvenilir servis",
):
    """Contents worker input testi için tam otoriter DB zinciri ve attempt kurar."""
    ws, run, kw = _setup_base_env(
        db_session, make_workspace, make_scoring_run, make_keyword
    )

    locked_at = T0 if brief_locked else None
    chan_ver = (
        run.channel_assignment_version + 1
        if channel_version_mismatch
        else run.channel_assignment_version
    )

    brief = SocialBrief(
        scoring_run_id=run.id,
        brand_name_snapshot=ws.name,
        brand_context_snapshot="Marka bağlamı ve genel hedefler",
        channel_assignment_version=chan_ver,
        format_matrix_version="v1",
        is_stale=brief_stale,
        locked_at=locked_at,
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
        category_name="Bilgilendirici İçerikler",
        category_type="educational",
        description="Eğitici ve bilgilendirici eksen",
        is_stale=cat_stale,
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
        idea_title="Örnek İçerik Fikri",
        idea_description="Detaylı içerik açıklaması ve kurgu planı",
        target_platform=platform,
        content_format=content_format,
        trend_alignment=0.88,
        is_stale=idea_stale,
    )
    db_session.add(idea)
    db_session.flush()

    resolved_task_id = (
        task_id
        if task_id is not None
        else (None if attempt_status == "pending" else f"task-content-{uuid.uuid4().hex[:8]}")
    )
    started_at = T0 if attempt_status != "pending" else None

    coverage = {
        "schema_version": "contents_request_v1",
        "request": {
            "idea_ids": [idea.id],
            "product_facts": product_facts,
            "trusted_brand_usp": trusted_brand_usp,
        },
    }

    attempt = SocialGenerationAttempt(
        brief_id=brief.id,
        stage=attempt_stage,
        idempotency_key=f"content-attempt-{uuid.uuid4().hex[:8]}",
        status=attempt_status,
        task_id=resolved_task_id,
        started_at=started_at,
        heartbeat_at=started_at,
        lease_expires_at=T0 + timedelta(seconds=1500),
        requested_idea_ids=[idea.id],
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
        "brief_kw": brief_kw,
        "target": target,
        "category": cat,
        "idea": idea,
        "attempt": attempt,
    }


def _build_valid_content_for_idea(
    idea: SocialIdea,
    brief: SocialBrief,
    target: SocialBriefTarget,
) -> ValidatedSocialContent:
    """Belirli bir format için kanonik ValidatedSocialContent nesnesi oluşturur."""
    hooks = (
        ValidatedHook(text="Soru kancası örneği?", style="question", ab_score=0.9),
        ValidatedHook(text="Şok edici istatistik!", style="shocking", ab_score=0.8),
        ValidatedHook(text="Günlük hayat tecrübesi", style="relatable", ab_score=None),
    )
    caption = f"{target.platform} {target.content_format} için açıklama metni."
    cta = "Daha fazlası için profili ziyaret edin!"
    hashtags = ("dijital", "pazarlama", "sosyalmedya", "trend", "strateji")

    fmt = target.content_format
    if fmt in ("video", "reels", "short"):
        dur = target.duration_max_sec or 30
        vo1 = "Giriş metni ve hook"
        vo2 = "Bu içerikte harika bilgiler bulacaksınız"
        payload = ValidatedVideoPayload(
            kind="video",
            segments=(
                ValidatedVideoSegment(0, 5, "Giriş sahnesi", "Hook metni", vo1),
                ValidatedVideoSegment(5, dur, "Ana sahne", "Detay metni", vo2),
            ),
        )
        scenario = render_legacy_scenario(payload)
        return ValidatedSocialContent(
            hooks=hooks,
            caption=caption,
            format_payload=payload,
            visual_suggestion="Video dinamik geçişler",
            video_concept="Hızlı tempo",
            cta_text=cta,
            hashtags=hashtags,
            industry_posting_suggestion="Hafta içi 14:00",
            platform_notes="Altyazı ekleyin",
            duration_status="valid",
            actual_duration_sec=dur,
            validation_warnings=(),
            scenario=scenario,
        )
    elif fmt == "carousel":
        payload = ValidatedCarouselPayload(
            kind="carousel",
            slides=(
                ValidatedCarouselSlide(1, "Slayt 1", "Giriş metni", "Görsel yön"),
                ValidatedCarouselSlide(2, "Slayt 2", "Ana metin", "Grafik yön"),
            ),
        )
        scenario = render_legacy_scenario(payload)
        return ValidatedSocialContent(
            hooks=hooks,
            caption=caption,
            format_payload=payload,
            visual_suggestion=None,
            video_concept=None,
            cta_text=cta,
            hashtags=hashtags,
            industry_posting_suggestion="12:00-13:00",
            platform_notes="Kayan gönderi",
            duration_status="not_applicable",
            actual_duration_sec=None,
            validation_warnings=(),
            scenario=scenario,
        )
    elif fmt == "thread":
        payload = ValidatedThreadPayload(
            kind="thread",
            posts=(
                ValidatedThreadPost(1, "Tweet 1/2"),
                ValidatedThreadPost(2, "Tweet 2/2"),
            ),
        )
        scenario = render_legacy_scenario(payload)
        return ValidatedSocialContent(
            hooks=hooks,
            caption=caption,
            format_payload=payload,
            visual_suggestion=None,
            video_concept=None,
            cta_text=cta,
            hashtags=hashtags,
            industry_posting_suggestion="18:00",
            platform_notes="Zincir gönderi",
            duration_status="not_applicable",
            actual_duration_sec=None,
            validation_warnings=(),
            scenario=scenario,
        )
    else:  # post veya story
        return ValidatedSocialContent(
            hooks=hooks,
            caption=caption,
            format_payload=None,
            visual_suggestion="Statik infografik",
            video_concept=None,
            cta_text=cta,
            hashtags=hashtags,
            industry_posting_suggestion="10:00",
            platform_notes="Tek görsel",
            duration_status="not_applicable",
            actual_duration_sec=None,
            validation_warnings=(),
            scenario=None,
        )


# ==================== TESTLER (22 SENARYO) ====================


@pytest.mark.parametrize(
    "platform,content_format,duration_preset_id,min_sec,max_sec",
    [
        ("instagram", "post", None, None, None),
        ("instagram", "story", None, None, None),
        ("instagram", "carousel", None, None, None),
        ("twitter", "thread", None, None, None),
        ("youtube", "video", "long_60_180", 60, 180),
        ("instagram", "reels", "short_16_30", 16, 30),
        ("youtube", "short", "short_31_60", 31, 60),
    ],
)
def test_01_successful_worker_input_preparation_for_all_seven_formats(
    enable_flag,
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
    platform: str,
    content_format: str,
    duration_preset_id: str | None,
    min_sec: int | None,
    max_sec: int | None,
):
    """1. Yedi kanonik format için doğru work item hazırlanması."""
    ctx = _setup_content_attempt_context(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        platform=platform,
        content_format=content_format,
        duration_preset_id=duration_preset_id,
        task_id="worker-task-01",
    )
    attempt = ctx["attempt"]
    idea = ctx["idea"]

    prep = prepare_social_content_worker_inputs(
        db_session,
        attempt_id=attempt.id,
        task_id="worker-task-01",
        now=T0,
    )

    assert isinstance(prep, SocialContentWorkerPreparation)
    assert prep.attempt_id == attempt.id
    assert prep.task_id == "worker-task-01"
    assert prep.already_completed is False
    assert prep.already_present_idea_ids == ()
    assert prep.requested_idea_ids == (idea.id,)
    assert len(prep.work_items) == 1

    item = prep.work_items[0]
    assert item.idea_id == idea.id
    assert item.prompt_input.attempt_id == attempt.id
    assert item.prompt_input.idea_id == idea.id
    assert item.prompt_input.target_spec.platform == platform
    assert item.prompt_input.target_spec.content_format == content_format
    assert item.prompt_input.target_spec.duration_preset_id == duration_preset_id
    assert item.prompt_input.target_spec.duration_min_sec == min_sec
    assert item.prompt_input.target_spec.duration_max_sec == max_sec
    assert item.prompt_input.primary_keyword.keyword == ctx["keyword"].keyword
    assert item.prompt_input.product_facts == "Doğrulanmış ürün içeriği"
    assert item.prompt_input.trusted_brand_usp == "En güvenilir servis"

    # Parite doğrulaması
    assert item.grounding_context.primary_keyword == item.prompt_input.primary_keyword.keyword
    assert item.grounding_context.product_facts == item.prompt_input.product_facts
    assert item.grounding_context.trusted_brand_usp == item.prompt_input.trusted_brand_usp


def test_02_pending_attempt_transitions_to_running_and_claims_successfully(
    enable_flag,
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
):
    """2. Pending attempt’in doğru task_id ile running claim edilmesi."""
    ctx = _setup_content_attempt_context(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        attempt_status="pending",
        task_id=None,
    )
    attempt = ctx["attempt"]
    assert attempt.status == "pending"
    assert attempt.task_id is None

    prep = prepare_social_content_worker_inputs(
        db_session,
        attempt_id=attempt.id,
        task_id="worker-claimed-02",
        now=T0,
    )

    db_session.refresh(attempt)
    assert attempt.status == "running"
    assert attempt.task_id == "worker-claimed-02"
    assert attempt.started_at == T0
    assert attempt.lease_expires_at > T0
    assert prep.task_id == "worker-claimed-02"


def test_03_running_attempt_idempotent_replay_same_task_id(
    enable_flag,
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
):
    """3. Aynı task_id running replay."""
    ctx = _setup_content_attempt_context(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        attempt_status="running",
        task_id="worker-task-03",
    )
    attempt = ctx["attempt"]

    prep1 = prepare_social_content_worker_inputs(
        db_session,
        attempt_id=attempt.id,
        task_id="worker-task-03",
        now=T0,
    )
    prep2 = prepare_social_content_worker_inputs(
        db_session,
        attempt_id=attempt.id,
        task_id="worker-task-03",
        now=T0,
    )

    assert prep1.requested_idea_ids == prep2.requested_idea_ids
    assert len(prep1.work_items) == len(prep2.work_items)
    assert prep1.work_items[0].idea_id == prep2.work_items[0].idea_id


def test_04_running_attempt_different_task_id_rejected(
    enable_flag,
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
):
    """4. Farklı task_id reddi (TASK_MISMATCH)."""
    ctx = _setup_content_attempt_context(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        attempt_status="running",
        task_id="worker-owner-04",
    )
    attempt = ctx["attempt"]

    with pytest.raises(AttemptNotWritableError) as exc_info:
        prepare_social_content_worker_inputs(
            db_session,
            attempt_id=attempt.id,
            task_id="worker-intruder-04",
            now=T0,
        )
    assert exc_info.value.error_code == "TASK_MISMATCH"


def test_05_wrong_stage_rejected(
    enable_flag,
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
):
    """5. Yanlış stage reddi (INVALID_STAGE)."""
    ctx = _setup_content_attempt_context(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        attempt_stage="ideas",
        task_id="worker-task-05",
    )
    attempt = ctx["attempt"]

    with pytest.raises(AttemptNotWritableError) as exc_info:
        prepare_social_content_worker_inputs(
            db_session,
            attempt_id=attempt.id,
            task_id="worker-task-05",
            now=T0,
        )
    assert exc_info.value.error_code == "INVALID_STAGE"


def test_06_completed_attempt_replay_returns_empty_work_items(
    enable_flag,
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
):
    """6. Completed replay’in work_items=() ve already_present_idea_ids döndürmesi."""
    ctx = _setup_content_attempt_context(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        attempt_status="running",
        task_id="worker-task-06",
    )
    attempt = ctx["attempt"]
    idea = ctx["idea"]
    brief = ctx["brief"]
    target = ctx["target"]

    content = _build_valid_content_for_idea(idea, brief, target)
    decision = SocialContentQualityDecision(
        action="accept",
        reason_codes=(),
        claims=(),
        warnings=(),
        grounding_clean=True,
        duration_acceptable=True,
        repair_attempted=False,
    )
    persist_social_content(
        db_session,
        brief_id=brief.id,
        attempt_id=attempt.id,
        idea_id=idea.id,
        content=content,
        quality_decision=decision,
        task_id="worker-task-06",
        now=T0,
    )
    attempt.status = "completed"
    db_session.commit()

    prep = prepare_social_content_worker_inputs(
        db_session,
        attempt_id=attempt.id,
        task_id="worker-task-06",
        now=T0,
    )

    assert prep.already_completed is True
    assert prep.work_items == ()
    assert prep.already_present_idea_ids == (ctx["idea"].id,)
    assert prep.requested_idea_ids == (ctx["idea"].id,)


def test_07_snapshot_idea_ids_mismatch_with_requested_idea_ids_rejected(
    enable_flag,
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
):
    """7. Snapshot ile requested_idea_ids uyuşmazlığı."""
    ctx = _setup_content_attempt_context(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="worker-task-07",
    )
    attempt = ctx["attempt"]
    # Snapshot'taki ID ile requested_idea_ids'i farklı yap
    attempt.requested_idea_ids = [ctx["idea"].id, 9999]
    db_session.commit()

    with pytest.raises(SocialContentWorkerInputError) as exc_info:
        prepare_social_content_worker_inputs(
            db_session,
            attempt_id=attempt.id,
            task_id="worker-task-07",
            now=T0,
        )
    assert exc_info.value.error_code == CONTENT_WORKER_INPUT_INCONSISTENT


def test_08_snapshot_validation_duplicates_bool_string_31_ideas_rejected(
    enable_flag,
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
):
    """8. Duplicate, bool, string ve 31 idea ID reddi."""
    ctx = _setup_content_attempt_context(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="worker-task-08",
    )
    attempt = ctx["attempt"]

    # 1. Duplicates
    attempt.coverage = {
        "schema_version": "contents_request_v1",
        "request": {
            "idea_ids": [ctx["idea"].id, ctx["idea"].id],
            "product_facts": None,
            "trusted_brand_usp": None,
        },
    }
    attempt.requested_idea_ids = [ctx["idea"].id, ctx["idea"].id]
    db_session.commit()

    with pytest.raises(SocialContentWorkerInputError) as exc_info:
        prepare_social_content_worker_inputs(
            db_session,
            attempt_id=attempt.id,
            task_id="worker-task-08",
            now=T0,
        )
    assert exc_info.value.error_code == CONTENT_WORKER_INPUT_INCONSISTENT

    # 2. Bool ID
    attempt.coverage["request"]["idea_ids"] = [True]
    attempt.requested_idea_ids = [True]
    db_session.commit()
    with pytest.raises(SocialContentWorkerInputError):
        prepare_social_content_worker_inputs(
            db_session,
            attempt_id=attempt.id,
            task_id="worker-task-08",
            now=T0,
        )

    # 3. String ID
    attempt.coverage["request"]["idea_ids"] = ["123"]
    attempt.requested_idea_ids = ["123"]
    db_session.commit()
    with pytest.raises(SocialContentWorkerInputError):
        prepare_social_content_worker_inputs(
            db_session,
            attempt_id=attempt.id,
            task_id="worker-task-08",
            now=T0,
        )

    # 4. 31 idea IDs
    ids_31 = list(range(1, 32))
    attempt.coverage["request"]["idea_ids"] = ids_31
    attempt.requested_idea_ids = ids_31
    db_session.commit()
    with pytest.raises(SocialContentWorkerInputError):
        prepare_social_content_worker_inputs(
            db_session,
            attempt_id=attempt.id,
            task_id="worker-task-08",
            now=T0,
        )

    # 5. Whitespace-only product_facts
    attempt.coverage["request"]["idea_ids"] = [ctx["idea"].id]
    attempt.requested_idea_ids = [ctx["idea"].id]
    attempt.coverage["request"]["product_facts"] = "   "
    db_session.commit()
    with pytest.raises(SocialContentWorkerInputError):
        prepare_social_content_worker_inputs(
            db_session,
            attempt_id=attempt.id,
            task_id="worker-task-08",
            now=T0,
        )


def test_09_brief_stale_version_locked_at_guards(
    enable_flag,
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
):
    """9. Brief stale/version/locked_at guard’ları."""
    # 1. Brief stale
    ctx1 = _setup_content_attempt_context(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        brief_stale=True,
        task_id="task-stale-09",
    )
    with pytest.raises((AttemptNotWritableError, SocialContentWorkerInputError)):
        prepare_social_content_worker_inputs(
            db_session,
            attempt_id=ctx1["attempt"].id,
            task_id="task-stale-09",
            now=T0,
        )

    # 2. Brief not locked
    ctx2 = _setup_content_attempt_context(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        brief_locked=False,
        task_id="task-unlocked-09",
    )
    with pytest.raises(SocialContentWorkerInputError) as exc_info:
        prepare_social_content_worker_inputs(
            db_session,
            attempt_id=ctx2["attempt"].id,
            task_id="task-unlocked-09",
            now=T0,
        )
    assert exc_info.value.error_code == CONTENT_WORKER_INPUT_INCONSISTENT

    # 3. Channel assignment version mismatch
    ctx3 = _setup_content_attempt_context(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        channel_version_mismatch=True,
        task_id="task-ver-09",
    )
    with pytest.raises((AttemptNotWritableError, SocialContentWorkerInputError)):
        prepare_social_content_worker_inputs(
            db_session,
            attempt_id=ctx3["attempt"].id,
            task_id="task-ver-09",
            now=T0,
        )


def test_10_cross_brief_entities_rejected(
    enable_flag,
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
):
    """10. Cross-brief idea/category/target/keyword reddi."""
    ctx_a = _setup_content_attempt_context(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-cross-a",
    )
    ctx_b = _setup_content_attempt_context(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-cross-b",
    )

    # Idea'nın kategorisini ctx_b'den yap
    ctx_a["idea"].category_id = ctx_b["category"].id
    db_session.commit()

    with pytest.raises(SocialContentWorkerInputError) as exc_info:
        prepare_social_content_worker_inputs(
            db_session,
            attempt_id=ctx_a["attempt"].id,
            task_id="task-cross-a",
            now=T0,
        )
    assert exc_info.value.error_code == CONTENT_WORKER_INPUT_INCONSISTENT


def test_11_stale_idea_or_category_rejected(
    enable_flag,
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
):
    """11. Stale idea veya category reddi."""
    # Stale idea
    ctx1 = _setup_content_attempt_context(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        idea_stale=True,
        task_id="task-idea-stale",
    )
    with pytest.raises(SocialContentWorkerInputError) as exc_info:
        prepare_social_content_worker_inputs(
            db_session,
            attempt_id=ctx1["attempt"].id,
            task_id="task-idea-stale",
            now=T0,
        )
    assert exc_info.value.error_code == CONTENT_WORKER_INPUT_INCONSISTENT

    # Stale category
    ctx2 = _setup_content_attempt_context(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        cat_stale=True,
        task_id="task-cat-stale",
    )
    with pytest.raises(SocialContentWorkerInputError) as exc_info:
        prepare_social_content_worker_inputs(
            db_session,
            attempt_id=ctx2["attempt"].id,
            task_id="task-cat-stale",
            now=T0,
        )
    assert exc_info.value.error_code == CONTENT_WORKER_INPUT_INCONSISTENT


def test_12_platform_format_target_mismatch_rejected(
    enable_flag,
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
):
    """12. Platform-format-target uyuşmazlığı."""
    ctx = _setup_content_attempt_context(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        platform="instagram",
        content_format="post",
        task_id="task-mismatch-12",
    )
    # Idea'nın formatını reels yap ama target post kalsın
    ctx["idea"].content_format = "reels"
    db_session.commit()

    with pytest.raises(SocialContentWorkerInputError) as exc_info:
        prepare_social_content_worker_inputs(
            db_session,
            attempt_id=ctx["attempt"].id,
            task_id="task-mismatch-12",
            now=T0,
        )
    assert exc_info.value.error_code == CONTENT_WORKER_INPUT_INCONSISTENT


def test_13_video_duration_preset_min_max_corruption_rejected(
    enable_flag,
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
):
    """13. Video duration preset/min/max bozulması."""
    ctx = _setup_content_attempt_context(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        platform="youtube",
        content_format="video",
        duration_preset_id="long_60_180",
        task_id="task-dur-13",
    )
    # Target min_sec değerini preset'ten farklı yap
    ctx["target"].duration_min_sec = 10
    db_session.commit()

    with pytest.raises(SocialContentWorkerInputError) as exc_info:
        prepare_social_content_worker_inputs(
            db_session,
            attempt_id=ctx["attempt"].id,
            task_id="task-dur-13",
            now=T0,
        )
    assert exc_info.value.error_code == CONTENT_WORKER_INPUT_INCONSISTENT


def test_14_non_video_target_with_duration_fields_rejected(
    enable_flag,
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
    monkeypatch,
):
    """14. Non-video target’ta süre alanı bulunması (Python fail-closed guard testi)."""
    ctx = _setup_content_attempt_context(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        platform="instagram",
        content_format="post",
        task_id="task-post-dur-14",
    )
    # Non-video hedefe duration preset koyulmasını Python nesnesi seviyesinde simüle et
    # (Postgres check constraint DB seviyesinde de korur; servis katmanı bunu fail-closed yakalar)
    orig_query = db_session.query

    def mock_query(*entities, **kwargs):
        q = orig_query(*entities, **kwargs)
        if entities and entities[0] is SocialBriefTarget:
            orig_all = q.all

            def mock_all():
                results = orig_all()
                for r in results:
                    r.duration_preset_id = "short_16_30"
                return results

            q.all = mock_all
        return q

    monkeypatch.setattr(db_session, "query", mock_query)

    with pytest.raises(SocialContentWorkerInputError) as exc_info:
        prepare_social_content_worker_inputs(
            db_session,
            attempt_id=ctx["attempt"].id,
            task_id="task-post-dur-14",
            now=T0,
        )
    assert exc_info.value.error_code == CONTENT_WORKER_INPUT_INCONSISTENT


def test_15_keyword_snapshot_chain_and_order_preserved(
    enable_flag,
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
):
    """15. Keyword snapshot zincirinin ve sırasının korunması."""
    ws, run, kw1 = _setup_base_env(db_session, make_workspace, make_scoring_run, make_keyword, brand_name="Brand Multi")
    kw2 = make_keyword(text_value="İkinci kelime", brand_profile_id=ws.id)
    kw3 = make_keyword(text_value="Üçüncü kelime", brand_profile_id=ws.id)

    brief = SocialBrief(
        scoring_run_id=run.id,
        brand_name_snapshot=ws.name,
        brand_context_snapshot="Bağlam",
        channel_assignment_version=1,
        locked_at=T0,
    )
    db_session.add(brief)
    db_session.flush()

    b_kw1 = SocialBriefKeyword(brief_id=brief.id, keyword_id=kw1.id, keyword_snapshot="Snapshot 1", position=0)
    b_kw2 = SocialBriefKeyword(brief_id=brief.id, keyword_id=kw2.id, keyword_snapshot="Snapshot 2", position=1)
    b_kw3 = SocialBriefKeyword(brief_id=brief.id, keyword_id=kw3.id, keyword_snapshot="Snapshot 3", position=2)
    db_session.add_all([b_kw1, b_kw2, b_kw3])

    target = SocialBriefTarget(brief_id=brief.id, platform="instagram", content_format="post")
    db_session.add(target)
    db_session.flush()

    cat = SocialCategory(scoring_run_id=run.id, brief_id=brief.id, category_name="Kategori 1", category_type="educational", description="Açıklama")
    db_session.add(cat)
    db_session.flush()

    idea1 = SocialIdea(brief_id=brief.id, category_id=cat.id, keyword_id=kw1.id, brief_target_id=target.id, idea_title="Fikir 1", idea_description="Açıklama 1", target_platform="instagram", content_format="post")
    idea2 = SocialIdea(brief_id=brief.id, category_id=cat.id, keyword_id=kw2.id, brief_target_id=target.id, idea_title="Fikir 2", idea_description="Açıklama 2", target_platform="instagram", content_format="post")
    idea3 = SocialIdea(brief_id=brief.id, category_id=cat.id, keyword_id=kw3.id, brief_target_id=target.id, idea_title="Fikir 3", idea_description="Açıklama 3", target_platform="instagram", content_format="post")
    db_session.add_all([idea1, idea2, idea3])
    db_session.flush()

    # Sıralama: [idea3, idea1, idea2]
    requested_order = [idea3.id, idea1.id, idea2.id]
    attempt = SocialGenerationAttempt(
        brief_id=brief.id,
        stage="contents",
        idempotency_key="attempt-multi-order",
        status="running",
        task_id="task-multi-15",
        started_at=T0,
        heartbeat_at=T0,
        lease_expires_at=T0 + timedelta(seconds=1000),
        requested_idea_ids=requested_order,
        coverage={
            "schema_version": "contents_request_v1",
            "request": {
                "idea_ids": requested_order,
                "product_facts": None,
                "trusted_brand_usp": None,
            },
        },
    )
    db_session.add(attempt)
    db_session.commit()

    prep = prepare_social_content_worker_inputs(
        db_session,
        attempt_id=attempt.id,
        task_id="task-multi-15",
        now=T0,
    )

    assert [item.idea_id for item in prep.work_items] == requested_order
    assert prep.work_items[0].prompt_input.primary_keyword.keyword == "Snapshot 3"
    assert prep.work_items[1].prompt_input.primary_keyword.keyword == "Snapshot 1"
    assert prep.work_items[2].prompt_input.primary_keyword.keyword == "Snapshot 2"


def test_16_prompt_input_grounding_context_exact_parity(
    enable_flag,
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
):
    """16. Prompt input ile grounding context’in birebir eşleşmesi."""
    ctx = _setup_content_attempt_context(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-parity-16",
        product_facts="F1 Facts",
        trusted_brand_usp="F1 USP",
    )
    attempt = ctx["attempt"]

    prep = prepare_social_content_worker_inputs(
        db_session,
        attempt_id=attempt.id,
        task_id="task-parity-16",
        now=T0,
    )

    item = prep.work_items[0]
    assert item.grounding_context.primary_keyword == item.prompt_input.primary_keyword.keyword
    assert item.grounding_context.product_facts == item.prompt_input.product_facts == "F1 Facts"
    assert item.grounding_context.trusted_brand_usp == item.prompt_input.trusted_brand_usp == "F1 USP"


def test_17_existing_valid_content_skipped_and_in_already_present_ids(
    enable_flag,
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
):
    """17. Existing geçerli content’in work item dışına alınması (already_present_idea_ids)."""
    ctx = _setup_content_attempt_context(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-existing-17",
    )
    attempt = ctx["attempt"]
    idea = ctx["idea"]
    brief = ctx["brief"]
    target = ctx["target"]

    # Fikir için geçerli içerik persist et
    content = _build_valid_content_for_idea(idea, brief, target)
    decision = SocialContentQualityDecision(
        action="accept",
        reason_codes=(),
        claims=(),
        warnings=(),
        grounding_clean=True,
        duration_acceptable=True,
        repair_attempted=False,
    )
    persist_social_content(
        db_session,
        brief_id=brief.id,
        attempt_id=attempt.id,
        idea_id=idea.id,
        content=content,
        quality_decision=decision,
        task_id="task-existing-17",
        now=T0,
    )
    db_session.commit()

    prep = prepare_social_content_worker_inputs(
        db_session,
        attempt_id=attempt.id,
        task_id="task-existing-17",
        now=T0,
    )

    assert prep.already_present_idea_ids == (idea.id,)
    assert prep.work_items == ()


def test_18_existing_corrupt_content_fails_closed(
    enable_flag,
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
):
    """18. Existing bozuk content’in fail-closed reddedilmesi."""
    ctx = _setup_content_attempt_context(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-corrupt-18",
    )
    attempt = ctx["attempt"]
    idea = ctx["idea"]
    brief = ctx["brief"]
    target = ctx["target"]

    content = _build_valid_content_for_idea(idea, brief, target)
    decision = SocialContentQualityDecision(
        action="accept",
        reason_codes=(),
        claims=(),
        warnings=(),
        grounding_clean=True,
        duration_acceptable=True,
        repair_attempted=False,
    )
    persist_social_content(
        db_session,
        brief_id=brief.id,
        attempt_id=attempt.id,
        idea_id=idea.id,
        content=content,
        quality_decision=decision,
        task_id="task-corrupt-18",
        now=T0,
    )
    db_session.commit()

    # Satırı tahrif et (geçersiz hashtag'ler koy)
    row = db_session.query(SocialContent).filter(SocialContent.idea_id == idea.id).first()
    row.hashtags = ["#gecersiz_tag_with_hash"]
    db_session.commit()

    with pytest.raises(SocialContentWorkerInputError) as exc_info:
        prepare_social_content_worker_inputs(
            db_session,
            attempt_id=attempt.id,
            task_id="task-corrupt-18",
            now=T0,
        )
    assert exc_info.value.error_code == CONTENT_WORKER_INPUT_INCONSISTENT


def test_19_service_does_not_commit_or_rollback(
    enable_flag,
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
    monkeypatch,
):
    """19. Fonksiyonun commit/rollback çağırmaması."""
    ctx = _setup_content_attempt_context(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-commit-19",
    )
    attempt = ctx["attempt"]

    commit_mock = MagicMock()
    rollback_mock = MagicMock()
    monkeypatch.setattr(db_session, "commit", commit_mock)
    monkeypatch.setattr(db_session, "rollback", rollback_mock)

    prep = prepare_social_content_worker_inputs(
        db_session,
        attempt_id=attempt.id,
        task_id="task-commit-19",
        now=T0,
    )

    assert commit_mock.call_count == 0
    assert rollback_mock.call_count == 0
    assert len(prep.work_items) == 1


def test_20_dto_deep_immutability(
    enable_flag,
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
):
    """20. Dönen DTO’ların nested olarak immutable olması."""
    ctx = _setup_content_attempt_context(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-immut-20",
    )
    attempt = ctx["attempt"]

    prep = prepare_social_content_worker_inputs(
        db_session,
        attempt_id=attempt.id,
        task_id="task-immut-20",
        now=T0,
    )

    from dataclasses import FrozenInstanceError

    with pytest.raises(FrozenInstanceError):
        prep.task_id = "mutated"  # type: ignore

    assert isinstance(prep.requested_idea_ids, tuple)
    assert isinstance(prep.already_present_idea_ids, tuple)
    assert isinstance(prep.work_items, tuple)

    item = prep.work_items[0]
    with pytest.raises(FrozenInstanceError):
        item.idea_id = 999  # type: ignore

    with pytest.raises(FrozenInstanceError):
        item.prompt_input.idea_title = "mutated"  # type: ignore


def test_21_security_no_sensitive_leaks_in_exceptions(
    enable_flag,
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
):
    """21. Hata mesajlarında ham caption, prompt, USP, product facts, SQL veya ID listesi sızmaması."""
    ctx = _setup_content_attempt_context(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-sec-21",
        product_facts="SECRET_PRODUCT_FACTS_NEVER_LEAK",
        trusted_brand_usp="SECRET_USP_NEVER_LEAK",
    )
    attempt = ctx["attempt"]
    # Idea'yı bozalım
    ctx["idea"].idea_title = "   "
    db_session.commit()

    with pytest.raises(SocialContentWorkerInputError) as exc_info:
        prepare_social_content_worker_inputs(
            db_session,
            attempt_id=attempt.id,
            task_id="task-sec-21",
            now=T0,
        )

    err_str = str(exc_info.value)
    assert "SECRET_PRODUCT_FACTS_NEVER_LEAK" not in err_str
    assert "SECRET_USP_NEVER_LEAK" not in err_str
    assert "SELECT " not in err_str
    assert "INSERT " not in err_str


def test_22_naive_datetime_and_invalid_task_id_rejected(
    enable_flag,
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
):
    """22. Naive datetime ve geçersiz task_id girdilerinin reddedilmesi."""
    ctx = _setup_content_attempt_context(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-naive-22",
    )
    attempt = ctx["attempt"]

    # 1. Naive datetime
    naive_dt = datetime(2026, 9, 24, 12, 0, 0)
    with pytest.raises(ValueError):
        prepare_social_content_worker_inputs(
            db_session,
            attempt_id=attempt.id,
            task_id="task-naive-22",
            now=naive_dt,
        )

    # 2. Empty task_id
    with pytest.raises(SocialContentWorkerInputError) as exc_info:
        prepare_social_content_worker_inputs(
            db_session,
            attempt_id=attempt.id,
            task_id="   ",
            now=T0,
        )
    assert exc_info.value.error_code == CONTENT_WORKER_INPUT_INVALID


def test_23_worker_ownership_task_mismatch_sanitization_no_leak(
    enable_flag,
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
):
    """23. Worker ownership task mismatch hata mesajlarının hassas ID sızdırmadan statik olması ve state koruması."""
    db_sentinel_running = "SECRET_DB_TASK_RUNNING_9999_ALPHA"
    caller_sentinel_running = "INTRUDER_CALLER_TASK_RUNNING_8888_BETA"

    # A. Running contents attempt
    ctx_running = _setup_content_attempt_context(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id=db_sentinel_running,
        attempt_status="running",
    )
    att_running = ctx_running["attempt"]
    orig_status = att_running.status
    orig_task_id = att_running.task_id
    orig_coverage = copy.deepcopy(att_running.coverage)
    orig_lease = att_running.lease_expires_at
    orig_error = att_running.error_message
    orig_reason = att_running.reason_code
    orig_started = att_running.started_at
    orig_completed = att_running.completed_at

    # 1. claim_contents_attempt_for_worker ile çağırma
    with pytest.raises(AttemptNotWritableError) as exc_info:
        claim_contents_attempt_for_worker(
            db_session,
            attempt_id=att_running.id,
            task_id=caller_sentinel_running,
            now=T0,
        )
    assert exc_info.value.error_code == "TASK_MISMATCH"
    assert str(exc_info.value) == "Worker sahipliği doğrulanamadı."
    assert repr(exc_info.value) == "AttemptNotWritableError('Worker sahipliği doğrulanamadı.')"
    assert db_sentinel_running not in str(exc_info.value)
    assert caller_sentinel_running not in str(exc_info.value)
    assert db_sentinel_running not in repr(exc_info.value)
    assert caller_sentinel_running not in repr(exc_info.value)

    # State'in değişmediğinin doğrulanması
    db_session.refresh(att_running)
    assert att_running.status == orig_status == "running"
    assert att_running.task_id == orig_task_id == db_sentinel_running
    assert att_running.coverage == orig_coverage
    assert att_running.lease_expires_at == orig_lease
    assert att_running.error_message == orig_error
    assert att_running.reason_code == orig_reason
    assert att_running.started_at == orig_started
    assert att_running.completed_at == orig_completed

    # 2. prepare_social_content_worker_inputs ile çağırma (aynı running attempt)
    with pytest.raises(AttemptNotWritableError) as exc_info_prep:
        prepare_social_content_worker_inputs(
            db_session,
            attempt_id=att_running.id,
            task_id=caller_sentinel_running,
            now=T0,
        )
    assert exc_info_prep.value.error_code == "TASK_MISMATCH"
    assert str(exc_info_prep.value) == "Worker sahipliği doğrulanamadı."
    assert db_sentinel_running not in str(exc_info_prep.value)
    assert caller_sentinel_running not in str(exc_info_prep.value)
    assert db_sentinel_running not in repr(exc_info_prep.value)
    assert caller_sentinel_running not in repr(exc_info_prep.value)

    # 3. Doğru task_id ile running attempt claim başarılı
    claimed_att, claimed_brief, claimed_run, already_completed = claim_contents_attempt_for_worker(
        db_session,
        attempt_id=att_running.id,
        task_id=db_sentinel_running,
        now=T0,
    )
    assert claimed_att.id == att_running.id
    assert already_completed is False

    # B. Completed contents attempt
    db_sentinel_completed = "SECRET_DB_TASK_COMPLETED_7777_GAMMA"
    caller_sentinel_completed = "INTRUDER_CALLER_TASK_COMPLETED_6666_DELTA"

    ctx_completed = _setup_content_attempt_context(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id=db_sentinel_completed,
        attempt_status="running",
    )
    att_completed = ctx_completed["attempt"]
    idea_comp = ctx_completed["idea"]
    brief_comp = ctx_completed["brief"]
    target_comp = ctx_completed["target"]

    content_comp = _build_valid_content_for_idea(idea_comp, brief_comp, target_comp)
    decision_comp = SocialContentQualityDecision(
        action="accept",
        reason_codes=(),
        claims=(),
        warnings=(),
        grounding_clean=True,
        duration_acceptable=True,
        repair_attempted=False,
    )
    persist_social_content(
        db_session,
        brief_id=brief_comp.id,
        attempt_id=att_completed.id,
        idea_id=idea_comp.id,
        content=content_comp,
        quality_decision=decision_comp,
        task_id=db_sentinel_completed,
        now=T0,
    )
    att_completed.status = "completed"
    att_completed.completed_at = T0
    db_session.commit()

    orig_comp_status = att_completed.status
    orig_comp_task_id = att_completed.task_id
    orig_comp_coverage = copy.deepcopy(att_completed.coverage)
    orig_comp_lease = att_completed.lease_expires_at
    orig_comp_error = att_completed.error_message
    orig_comp_reason = att_completed.reason_code
    orig_comp_completed = att_completed.completed_at

    # 4. claim_contents_attempt_for_worker ile completed attempt'e yetkisiz task_id
    with pytest.raises(AttemptNotWritableError) as exc_info_comp:
        claim_contents_attempt_for_worker(
            db_session,
            attempt_id=att_completed.id,
            task_id=caller_sentinel_completed,
            now=T0,
        )
    assert exc_info_comp.value.error_code == "TASK_MISMATCH"
    assert str(exc_info_comp.value) == "Worker sahipliği doğrulanamadı."
    assert repr(exc_info_comp.value) == "AttemptNotWritableError('Worker sahipliği doğrulanamadı.')"
    assert db_sentinel_completed not in str(exc_info_comp.value)
    assert caller_sentinel_completed not in str(exc_info_comp.value)
    assert db_sentinel_completed not in repr(exc_info_comp.value)
    assert caller_sentinel_completed not in repr(exc_info_comp.value)

    # State'in değişmediğinin doğrulanması
    db_session.refresh(att_completed)
    assert att_completed.status == orig_comp_status == "completed"
    assert att_completed.task_id == orig_comp_task_id == db_sentinel_completed
    assert att_completed.coverage == orig_comp_coverage
    assert att_completed.lease_expires_at == orig_comp_lease
    assert att_completed.error_message == orig_comp_error
    assert att_completed.reason_code == orig_comp_reason
    assert att_completed.completed_at == orig_comp_completed

    # 5. prepare_social_content_worker_inputs ile completed attempt'e yetkisiz task_id
    with pytest.raises(AttemptNotWritableError) as exc_info_comp_prep:
        prepare_social_content_worker_inputs(
            db_session,
            attempt_id=att_completed.id,
            task_id=caller_sentinel_completed,
            now=T0,
        )
    assert exc_info_comp_prep.value.error_code == "TASK_MISMATCH"
    assert str(exc_info_comp_prep.value) == "Worker sahipliği doğrulanamadı."
    assert db_sentinel_completed not in str(exc_info_comp_prep.value)
    assert caller_sentinel_completed not in str(exc_info_comp_prep.value)

    # 6. Doğru task_id ile completed replay başarılı ve idempotent
    claimed_c_att, _, _, c_already_completed = claim_contents_attempt_for_worker(
        db_session,
        attempt_id=att_completed.id,
        task_id=db_sentinel_completed,
        now=T0,
    )
    assert claimed_c_att.id == att_completed.id
    assert c_already_completed is True

    # prepare_social_content_worker_inputs ile completed replay
    prep_res = prepare_social_content_worker_inputs(
        db_session,
        attempt_id=att_completed.id,
        task_id=db_sentinel_completed,
        now=T0,
    )
    assert prep_res.already_completed is True
    assert prep_res.work_items == ()


def test_24_claim_across_stages_ideas_and_retry_sanitization(
    enable_flag,
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
):
    """24. Ideas ve ideas_retry claim yollarında task mismatch mesaj sanitizasyonu ve state dokunulmazlığı."""
    # A. Ideas stage - running
    sentinel_ideas_db = "SECRET_IDEAS_DB_RUNNING_5555"
    sentinel_ideas_caller = "INTRUDER_IDEAS_CALLER_RUNNING_4444"

    ctx_ideas_running = _setup_content_attempt_context(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        attempt_stage="ideas",
        task_id=sentinel_ideas_db,
        attempt_status="running",
    )
    att_ir = ctx_ideas_running["attempt"]
    orig_ir_status = att_ir.status
    orig_ir_task_id = att_ir.task_id
    orig_ir_coverage = copy.deepcopy(att_ir.coverage)
    orig_ir_lease = att_ir.lease_expires_at

    with pytest.raises(AttemptNotWritableError) as exc_ir:
        claim_ideas_attempt_for_worker(
            db_session,
            attempt_id=att_ir.id,
            task_id=sentinel_ideas_caller,
            now=T0,
        )
    assert exc_ir.value.error_code == "TASK_MISMATCH"
    assert str(exc_ir.value) == "Worker sahipliği doğrulanamadı."
    assert repr(exc_ir.value) == "AttemptNotWritableError('Worker sahipliği doğrulanamadı.')"
    assert sentinel_ideas_db not in str(exc_ir.value)
    assert sentinel_ideas_caller not in str(exc_ir.value)
    assert sentinel_ideas_db not in repr(exc_ir.value)
    assert sentinel_ideas_caller not in repr(exc_ir.value)

    db_session.refresh(att_ir)
    assert att_ir.status == orig_ir_status == "running"
    assert att_ir.task_id == orig_ir_task_id == sentinel_ideas_db
    assert att_ir.coverage == orig_ir_coverage
    assert att_ir.lease_expires_at == orig_ir_lease

    # Doğru task_id ile ideas running claim başarılı
    _, _, _, ir_completed = claim_ideas_attempt_for_worker(
        db_session,
        attempt_id=att_ir.id,
        task_id=sentinel_ideas_db,
        now=T0,
    )
    assert ir_completed is False

    # B. Ideas stage - completed
    sentinel_ideas_comp_db = "SECRET_IDEAS_DB_COMPLETED_3333"
    sentinel_ideas_comp_caller = "INTRUDER_IDEAS_CALLER_COMPLETED_2222"

    ctx_ideas_comp = _setup_content_attempt_context(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        attempt_stage="ideas",
        task_id=sentinel_ideas_comp_db,
        attempt_status="completed",
    )
    att_ic = ctx_ideas_comp["attempt"]
    att_ic.completed_at = T0
    db_session.commit()

    with pytest.raises(AttemptNotWritableError) as exc_ic:
        claim_ideas_attempt_for_worker(
            db_session,
            attempt_id=att_ic.id,
            task_id=sentinel_ideas_comp_caller,
            now=T0,
        )
    assert exc_ic.value.error_code == "TASK_MISMATCH"
    assert str(exc_ic.value) == "Worker sahipliği doğrulanamadı."
    assert sentinel_ideas_comp_db not in str(exc_ic.value)
    assert sentinel_ideas_comp_caller not in str(exc_ic.value)

    db_session.refresh(att_ic)
    assert att_ic.status == "completed"
    assert att_ic.task_id == sentinel_ideas_comp_db

    # Doğru task_id ile ideas completed claim başarılı
    _, _, _, ic_completed = claim_ideas_attempt_for_worker(
        db_session,
        attempt_id=att_ic.id,
        task_id=sentinel_ideas_comp_db,
        now=T0,
    )
    assert ic_completed is True

    # C. Ideas Retry stage - running
    sentinel_retry_db = "SECRET_RETRY_DB_RUNNING_1111"
    sentinel_retry_caller = "INTRUDER_RETRY_CALLER_RUNNING_0000"

    ctx_retry_running = _setup_content_attempt_context(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        attempt_stage="ideas_retry",
        task_id=sentinel_retry_db,
        attempt_status="running",
    )
    att_rr = ctx_retry_running["attempt"]
    orig_rr_status = att_rr.status
    orig_rr_task_id = att_rr.task_id
    orig_rr_coverage = copy.deepcopy(att_rr.coverage)
    orig_rr_lease = att_rr.lease_expires_at

    with pytest.raises(AttemptNotWritableError) as exc_rr:
        claim_ideas_retry_attempt_for_worker(
            db_session,
            attempt_id=att_rr.id,
            task_id=sentinel_retry_caller,
            now=T0,
        )
    assert exc_rr.value.error_code == "TASK_MISMATCH"
    assert str(exc_rr.value) == "Worker sahipliği doğrulanamadı."
    assert repr(exc_rr.value) == "AttemptNotWritableError('Worker sahipliği doğrulanamadı.')"
    assert sentinel_retry_db not in str(exc_rr.value)
    assert sentinel_retry_caller not in str(exc_rr.value)
    assert sentinel_retry_db not in repr(exc_rr.value)
    assert sentinel_retry_caller not in repr(exc_rr.value)

    db_session.refresh(att_rr)
    assert att_rr.status == orig_rr_status == "running"
    assert att_rr.task_id == orig_rr_task_id == sentinel_retry_db
    assert att_rr.coverage == orig_rr_coverage
    assert att_rr.lease_expires_at == orig_rr_lease

    # Doğru task_id ile retry running claim başarılı
    _, _, _, rr_completed = claim_ideas_retry_attempt_for_worker(
        db_session,
        attempt_id=att_rr.id,
        task_id=sentinel_retry_db,
        now=T0,
    )
    assert rr_completed is False

    # D. Ideas Retry stage - completed
    sentinel_retry_comp_db = "SECRET_RETRY_DB_COMPLETED_9876"
    sentinel_retry_comp_caller = "INTRUDER_RETRY_CALLER_COMPLETED_5432"

    ctx_retry_comp = _setup_content_attempt_context(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        attempt_stage="ideas_retry",
        task_id=sentinel_retry_comp_db,
        attempt_status="completed",
    )
    att_rc = ctx_retry_comp["attempt"]
    att_rc.completed_at = T0
    db_session.commit()

    with pytest.raises(AttemptNotWritableError) as exc_rc:
        claim_ideas_retry_attempt_for_worker(
            db_session,
            attempt_id=att_rc.id,
            task_id=sentinel_retry_comp_caller,
            now=T0,
        )
    assert exc_rc.value.error_code == "TASK_MISMATCH"
    assert str(exc_rc.value) == "Worker sahipliği doğrulanamadı."
    assert sentinel_retry_comp_db not in str(exc_rc.value)
    assert sentinel_retry_comp_caller not in str(exc_rc.value)

    db_session.refresh(att_rc)
    assert att_rc.status == "completed"
    assert att_rc.task_id == sentinel_retry_comp_db

    # Doğru task_id ile retry completed claim başarılı
    _, _, _, rc_completed = claim_ideas_retry_attempt_for_worker(
        db_session,
        attempt_id=att_rc.id,
        task_id=sentinel_retry_comp_db,
        now=T0,
    )
    assert rc_completed is True

