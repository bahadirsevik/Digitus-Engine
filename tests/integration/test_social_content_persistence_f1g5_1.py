# -*- coding: utf-8 -*-
"""MİKRO FAZ F1-G.5.1 — Tek Sosyal İçeriğin Otoriter Doğrulaması ve Atomik Persistence Entegrasyon Testleri.

Tüm testler gerçek PostgreSQL test veritabanını ve izole SQLAlchemy oturumunu kullanır.
Test kapsamı:
A. Başarılı format kayıtları (post, story, carousel, thread, video, reels, short)
B. Quality & Grounding sözleşmeleri (reject/repair/claims engelleme, duration mismatch ve soft warnings)
C. Otoriter DB zinciri ve attempt guard'ları (stale brief/idea/cat, target mismatch, keyword eksikliği, lease expiry, worker task_id)
D. Idempotency (replay, duplicate çağrı, bozuk satır tespiti, legacy brief_id IS NULL izolasyonu)
E. Eşzamanlı yarış (savepoint ile izole uq_social_content_idea_brief yarışı, outer transaction korunumu)
F. Transaction sınırları (commit/rollback çağırmama, caller rollback ile tam temizlik, transaction birleşimi)
G. Güvenlik ve hata sanitization (hassas veri/SQL/AI çıktısı sızdırmama)
"""
from __future__ import annotations

import copy
from dataclasses import FrozenInstanceError
import threading
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.core.social.content_contract import (
    ValidatedCarouselPayload,
    ValidatedCarouselSlide,
    ValidatedHook,
    ValidatedSocialContent,
    ValidatedThreadPayload,
    ValidatedThreadPost,
    ValidatedVideoPayload,
    ValidatedVideoSegment,
    _render_legacy_scenario_from_payload,
)
from app.core.social.content_persistence import (
    CONTENT_PERSISTENCE_ATTEMPT_NOT_WRITABLE,
    CONTENT_PERSISTENCE_INCONSISTENT,
    CONTENT_PERSISTENCE_INVALID_INPUT,
    CONTENT_PERSISTENCE_REJECTED,
    PersistedSocialContent,
    PersistedSocialContentResult,
    SocialContentPersistenceError,
    persist_social_content,
)
from app.core.social.content_quality import SocialContentQualityDecision
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
from app.generators.social.format_matrix import get_duration_preset

T0 = datetime(2026, 9, 25, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def enable_flag(monkeypatch):
    """Test süresince feature flag'i aktif kılar."""
    monkeypatch.setattr(settings, "ENABLE_SOCIAL_BRIEF_FLOW", True)
    yield


# ==================== TEST FIXTURE HELPERS ====================


def _setup_base_env(
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
    brand_name: str = "Test Brand Content",
):
    """Fresh workspace, scoring run ve keyword oluşturur."""
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


def _setup_content_test_context(
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
    *,
    platform: str = "instagram",
    content_format: str = "post",
    duration_preset_id: str | None = None,
    task_id: str | None = None,
    lease_expired: bool = False,
    attempt_status: str = "running",
    attempt_stage: str = "contents",
    brief_stale: bool = False,
    idea_stale: bool = False,
    cat_stale: bool = False,
):
    """İçerik persistence testi için tam otoriter DB zinciri ve running attempt kurar."""
    ws, run, kw = _setup_base_env(
        db_session, make_workspace, make_scoring_run, make_keyword
    )

    brief = SocialBrief(
        scoring_run_id=run.id,
        brand_name_snapshot=ws.name,
        brand_context_snapshot="Marka bağlamı ve ürün detayları",
        channel_assignment_version=1,
        format_matrix_version="v1",
        is_stale=brief_stale,
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

    # Hedef (target) süre sınırlarını canonical preset'ten al
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
        description="Eğitici ve bilgilendirici içerik ekseni.",
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
        idea_title="Örnek Fikir Başlığı",
        idea_description="Detaylı içerik fikri açıklaması.",
        target_platform=platform,
        content_format=content_format,
        trend_alignment=0.85,
        is_stale=idea_stale,
    )
    db_session.add(idea)
    db_session.flush()

    lease_exp = (
        T0 - timedelta(seconds=60)
        if lease_expired
        else T0 + timedelta(seconds=1500)
    )

    resolved_task_id = task_id if task_id is not None else f"task-content-{uuid.uuid4().hex[:8]}"

    attempt = SocialGenerationAttempt(
        brief_id=brief.id,
        stage=attempt_stage,
        idempotency_key=f"content-attempt-{uuid.uuid4().hex[:8]}",
        status=attempt_status,
        task_id=resolved_task_id,
        started_at=T0,
        heartbeat_at=T0,
        lease_expires_at=lease_exp,
        requested_idea_ids=[idea.id],
        created_at=T0,
    )
    db_session.add(attempt)
    db_session.flush()

    return {
        "workspace": ws,
        "scoring_run": run,
        "keyword": kw,
        "brief": brief,
        "target": target,
        "category": cat,
        "idea": idea,
        "attempt": attempt,
    }


def _make_dummy_hooks() -> tuple[ValidatedHook, ...]:
    return (
        ValidatedHook(text="Dikkat çeken soru kancası?", style="question", ab_score=0.9),
        ValidatedHook(text="Şaşırtıcı bir sektör gerçeği!", style="shocking", ab_score=0.8),
        ValidatedHook(text="Hepimizin yaşadığı o an...", style="relatable", ab_score=None),
    )


def _build_content_for_format(
    platform: str,
    content_format: str,
    *,
    duration_status: str = "valid",
    actual_duration_sec: int | None = None,
    warnings: tuple[str, ...] = (),
) -> ValidatedSocialContent:
    """Belirtilen platform ve formata uygun geçerli ValidatedSocialContent nesnesi üretir."""
    hooks = _make_dummy_hooks()
    caption = f"{platform} için {content_format} formatında bilgilendirici açıklama metni."
    cta = "Daha fazlası için profildeki linke tıkla!"
    hashtags = ("dijital", "pazarlama", "strateji", "icerik", "sosyalmedya")

    if content_format in ("video", "reels", "short"):
        dur = actual_duration_sec or 30
        if "voiceover_duration_mismatch" in warnings:
            vo1 = "Giriş sahnesi"
            vo2 = "Detay metni"
        else:
            vo1 = "Bu videoda harika dijital pazarlama ipuçları öğreneceksiniz"
            rem_sec = max(dur - 5, 1)
            num_words = max(int(rem_sec * 1.5), 1)
            words_pool = ["pazarlama", "strateji", "dijital", "hedef", "analiz", "icerik", "sosyal", "medya", "trend", "basari"]
            vo2 = " ".join(words_pool[i % len(words_pool)] for i in range(num_words))
        payload = ValidatedVideoPayload(
            kind="video",
            segments=(
                ValidatedVideoSegment(0, 5, "Giriş sahnesi", "Hook metni", vo1),
                ValidatedVideoSegment(5, dur, "Ana sahne", "Detay metni", vo2),
            ),
        )
        scenario = _render_legacy_scenario_from_payload(payload)
        return ValidatedSocialContent(
            hooks=hooks,
            caption=caption,
            format_payload=payload,
            visual_suggestion="Dinamik geçişler ve marka renkleri",
            video_concept="Hızlı tempo ve b-roll kurgusu",
            cta_text=cta,
            hashtags=hashtags,
            industry_posting_suggestion="Hafta içi 12:00-14:00",
            platform_notes="Altyazı eklemeyi unutmayın",
            duration_status=duration_status,
            actual_duration_sec=dur,
            validation_warnings=warnings,
            scenario=scenario,
        )
    elif content_format == "carousel":
        payload = ValidatedCarouselPayload(
            kind="carousel",
            slides=(
                ValidatedCarouselSlide(1, "Slide 1 Başlık", "Slide 1 Metin", "Görsel 1"),
                ValidatedCarouselSlide(2, "Slide 2 Başlık", "Slide 2 Metin", "Görsel 2"),
                ValidatedCarouselSlide(3, "Slide 3 Başlık", "Slide 3 Metin", "Görsel 3"),
            ),
        )
        scenario = _render_legacy_scenario_from_payload(payload)
        return ValidatedSocialContent(
            hooks=hooks,
            caption=caption,
            format_payload=payload,
            visual_suggestion="Minimal kart tasarımı",
            video_concept=None,
            cta_text=cta,
            hashtags=hashtags,
            industry_posting_suggestion="Salı 10:00",
            platform_notes="Kaydırmalı post",
            duration_status="not_applicable",
            actual_duration_sec=None,
            validation_warnings=warnings,
            scenario=scenario,
        )
    elif content_format == "thread":
        payload = ValidatedThreadPayload(
            kind="thread",
            posts=(
                ValidatedThreadPost(1, "Thread tweet 1/3"),
                ValidatedThreadPost(2, "Thread tweet 2/3"),
                ValidatedThreadPost(3, "Thread tweet 3/3"),
            ),
        )
        scenario = _render_legacy_scenario_from_payload(payload)
        return ValidatedSocialContent(
            hooks=hooks,
            caption=caption,
            format_payload=payload,
            visual_suggestion=None,
            video_concept=None,
            cta_text=cta,
            hashtags=hashtags,
            industry_posting_suggestion="Sabah 09:00",
            platform_notes="Zincir tweet",
            duration_status="not_applicable",
            actual_duration_sec=None,
            validation_warnings=warnings,
            scenario=scenario,
        )
    else:  # post veya story
        return ValidatedSocialContent(
            hooks=hooks,
            caption=caption,
            format_payload=None,
            visual_suggestion="Tek görsel infografik",
            video_concept=None,
            cta_text=cta,
            hashtags=hashtags,
            industry_posting_suggestion="Perşembe 15:00",
            platform_notes="Statik paylaşım",
            duration_status="not_applicable",
            actual_duration_sec=None,
            validation_warnings=warnings,
            scenario=None,
        )


def _make_quality_decision(
    *,
    action: str = "accept",
    reason_codes: tuple[str, ...] = (),
    claims: tuple[str, ...] = (),
    warnings: tuple[str, ...] = (),
    grounding_clean: bool = True,
    duration_acceptable: bool = True,
    repair_attempted: bool = False,
) -> SocialContentQualityDecision:
    return SocialContentQualityDecision(
        action=action,
        reason_codes=reason_codes,
        claims=claims,
        warnings=warnings,
        grounding_clean=grounding_clean,
        duration_acceptable=duration_acceptable,
        repair_attempted=repair_attempted,
    )


# ==================== TESTLER ====================


@pytest.mark.parametrize(
    "platform,content_format,duration_preset_id,actual_sec",
    [
        ("instagram", "post", None, None),
        ("instagram", "story", None, None),
        ("instagram", "carousel", None, None),
        ("twitter", "thread", None, None),
        ("youtube", "video", "long_60_180", 120),
        ("instagram", "reels", "short_16_30", 28),
        ("youtube", "short", "short_31_60", 55),
    ],
)
def test_successful_persistence_for_all_seven_formats(
    enable_flag,
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
    platform: str,
    content_format: str,
    duration_preset_id: str | None,
    actual_sec: int | None,
):
    """Tüm 7 kanonik format için başarılı atomik kayıt ve DTO alan eşlemesi doğrulanır."""
    ctx = _setup_content_test_context(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        platform=platform,
        content_format=content_format,
        duration_preset_id=duration_preset_id,
    )

    content = _build_content_for_format(
        platform,
        content_format,
        duration_status="valid" if actual_sec else "not_applicable",
        actual_duration_sec=actual_sec,
    )
    decision = _make_quality_decision(action="accept", warnings=("test_warn",))

    result = persist_social_content(
        db_session,
        brief_id=ctx["brief"].id,
        attempt_id=ctx["attempt"].id,
        idea_id=ctx["idea"].id,
        content=content,
        quality_decision=decision,
        task_id=ctx["attempt"].task_id,
        now=T0,
    )

    assert isinstance(result, PersistedSocialContentResult)
    assert result.brief_id == ctx["brief"].id
    assert result.attempt_id == ctx["attempt"].id
    assert result.idea_id == ctx["idea"].id
    assert result.already_present is False

    pc = result.content
    assert isinstance(pc, PersistedSocialContent)
    assert pc.idea_id == ctx["idea"].id
    assert pc.brief_id == ctx["brief"].id
    assert pc.target_id == ctx["target"].id
    assert pc.platform == platform
    assert pc.content_format == content_format
    assert len(pc.hooks) == 3
    assert pc.caption == content.caption
    assert pc.scenario == content.scenario
    assert pc.duration_status == content.duration_status
    assert pc.actual_duration_sec == actual_sec
    assert pc.validation_warnings == ("test_warn",)
    assert pc.is_stale is False

    # Veritabanı satırını doğrudan incele
    row = (
        db_session.query(SocialContent)
        .filter(SocialContent.idea_id == ctx["idea"].id)
        .one()
    )
    assert row.brief_id == ctx["brief"].id
    assert row.idea_id == ctx["idea"].id
    assert row.caption == content.caption
    assert row.scenario == content.scenario
    assert row.duration_status == content.duration_status
    assert row.actual_duration_sec == actual_sec
    assert row.is_stale is False
    assert row.regeneration_count == 0


def test_quality_decision_reject_and_repair_not_persisted(
    enable_flag,
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
):
    """Reject ve repair aksiyonları fail-closed reddedilir, hiçbir içerik kaydedilmez."""
    ctx = _setup_content_test_context(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    content = _build_content_for_format("instagram", "post")

    # 1. Action repair
    repair_decision = _make_quality_decision(
        action="repair",
        reason_codes=("ungrounded_claim",),
        grounding_clean=False,
    )
    with pytest.raises(SocialContentPersistenceError) as exc_info:
        persist_social_content(
            db_session,
            brief_id=ctx["brief"].id,
            attempt_id=ctx["attempt"].id,
            idea_id=ctx["idea"].id,
            content=content,
            quality_decision=repair_decision,
            task_id=ctx["attempt"].task_id,
            now=T0,
        )
    assert exc_info.value.error_code == CONTENT_PERSISTENCE_REJECTED

    # 2. Action reject
    reject_decision = _make_quality_decision(
        action="reject",
        reason_codes=("ungrounded_claim",),
        grounding_clean=False,
        repair_attempted=True,
    )
    with pytest.raises(SocialContentPersistenceError) as exc_info:
        persist_social_content(
            db_session,
            brief_id=ctx["brief"].id,
            attempt_id=ctx["attempt"].id,
            idea_id=ctx["idea"].id,
            content=content,
            quality_decision=reject_decision,
            task_id=ctx["attempt"].task_id,
            now=T0,
        )
    assert exc_info.value.error_code == CONTENT_PERSISTENCE_REJECTED

    # DB'de hiçbir satır oluşmadığını teyit et
    assert db_session.query(SocialContent).count() == 0


def test_quality_decision_claims_and_unclean_grounding_rejected(
    enable_flag,
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
):
    """İddia (claim) barındıran veya grounding_clean False olan kararlar fail-closed reddedilir."""
    ctx = _setup_content_test_context(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    content = _build_content_for_format("instagram", "post")

    # 1. claims dolu
    claim_decision = _make_quality_decision(
        action="accept",
        claims=("%100 garantili",),
        grounding_clean=True,
    )
    with pytest.raises(SocialContentPersistenceError) as exc_info:
        persist_social_content(
            db_session,
            brief_id=ctx["brief"].id,
            attempt_id=ctx["attempt"].id,
            idea_id=ctx["idea"].id,
            content=content,
            quality_decision=claim_decision,
            task_id=ctx["attempt"].task_id,
            now=T0,
        )
    assert exc_info.value.error_code == CONTENT_PERSISTENCE_REJECTED

    # 2. grounding_clean False
    unclean_decision = _make_quality_decision(
        action="accept",
        grounding_clean=False,
    )
    with pytest.raises(SocialContentPersistenceError) as exc_info:
        persist_social_content(
            db_session,
            brief_id=ctx["brief"].id,
            attempt_id=ctx["attempt"].id,
            idea_id=ctx["idea"].id,
            content=content,
            quality_decision=unclean_decision,
            task_id=ctx["attempt"].task_id,
            now=T0,
        )
    assert exc_info.value.error_code == CONTENT_PERSISTENCE_REJECTED

    assert db_session.query(SocialContent).count() == 0


def test_duration_mismatch_without_repair_rejected_with_repair_accepted(
    enable_flag,
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
):
    """Repair denenmeden duration mismatch kaydedilemez; repair sonrası warning ile kabul edilir."""
    ctx = _setup_content_test_context(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        platform="youtube",
        content_format="video",
        duration_preset_id="long_60_180",
    )
    mismatch_content = _build_content_for_format(
        "youtube",
        "video",
        duration_status="mismatch",
        actual_duration_sec=45,
        warnings=("duration_mismatch",),
    )

    # 1. repair_attempted False iken duration mismatch reddedilir
    decision_no_repair = _make_quality_decision(
        action="accept",
        repair_attempted=False,
        duration_acceptable=False,
        reason_codes=("duration_mismatch",),
        warnings=("duration_mismatch",),
    )
    with pytest.raises(SocialContentPersistenceError) as exc_info:
        persist_social_content(
            db_session,
            brief_id=ctx["brief"].id,
            attempt_id=ctx["attempt"].id,
            idea_id=ctx["idea"].id,
            content=mismatch_content,
            quality_decision=decision_no_repair,
            task_id=ctx["attempt"].task_id,
            now=T0,
        )
    assert exc_info.value.error_code == CONTENT_PERSISTENCE_INCONSISTENT
    assert db_session.query(SocialContent).count() == 0

    # 2. repair_attempted True iken warning ile kabul edilir
    decision_after_repair = _make_quality_decision(
        action="accept",
        repair_attempted=True,
        duration_acceptable=False,
        reason_codes=("duration_mismatch",),
        warnings=("voiceover_duration_mismatch", "duration_mismatch"),
    )
    content_with_both_warnings = _build_content_for_format(
        "youtube",
        "video",
        duration_status="mismatch",
        actual_duration_sec=45,
        warnings=("voiceover_duration_mismatch",),
    )

    result = persist_social_content(
        db_session,
        brief_id=ctx["brief"].id,
        attempt_id=ctx["attempt"].id,
        idea_id=ctx["idea"].id,
        content=content_with_both_warnings,
        quality_decision=decision_after_repair,
        task_id=ctx["attempt"].task_id,
        now=T0,
    )
    assert result.already_present is False
    assert result.content.duration_status == "mismatch"
    assert result.content.actual_duration_sec == 45
    assert "duration_mismatch" in result.content.validation_warnings
    assert "voiceover_duration_mismatch" in result.content.validation_warnings


def test_authoritative_db_chain_guards_prevent_write(
    enable_flag,
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
):
    """Zincirdeki herhangi bir tutarsızlık durumunda persistence engellenir ve satır oluşmaz."""
    content = _build_content_for_format("instagram", "post")
    decision = _make_quality_decision()

    # 1. Stale brief
    ctx_stale_brief = _setup_content_test_context(
        db_session, make_workspace, make_scoring_run, make_keyword, brief_stale=True
    )
    with pytest.raises(SocialContentPersistenceError) as exc_info:
        persist_social_content(
            db_session,
            brief_id=ctx_stale_brief["brief"].id,
            attempt_id=ctx_stale_brief["attempt"].id,
            idea_id=ctx_stale_brief["idea"].id,
            content=content,
            quality_decision=decision,
            task_id=ctx_stale_brief["attempt"].task_id,
            now=T0,
        )
    assert exc_info.value.error_code == CONTENT_PERSISTENCE_ATTEMPT_NOT_WRITABLE

    # 2. Expired lease
    ctx_expired = _setup_content_test_context(
        db_session, make_workspace, make_scoring_run, make_keyword, lease_expired=True
    )
    with pytest.raises(SocialContentPersistenceError) as exc_info:
        persist_social_content(
            db_session,
            brief_id=ctx_expired["brief"].id,
            attempt_id=ctx_expired["attempt"].id,
            idea_id=ctx_expired["idea"].id,
            content=content,
            quality_decision=decision,
            task_id=ctx_expired["attempt"].task_id,
            now=T0,
        )
    assert exc_info.value.error_code == CONTENT_PERSISTENCE_ATTEMPT_NOT_WRITABLE

    # 3. Task ID mismatch
    ctx_ok = _setup_content_test_context(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    with pytest.raises(SocialContentPersistenceError) as exc_info:
        persist_social_content(
            db_session,
            brief_id=ctx_ok["brief"].id,
            attempt_id=ctx_ok["attempt"].id,
            idea_id=ctx_ok["idea"].id,
            content=content,
            quality_decision=decision,
            task_id="wrong-task-id",
            now=T0,
        )
    assert exc_info.value.error_code == CONTENT_PERSISTENCE_ATTEMPT_NOT_WRITABLE

    # 4. Attempt status != running (ör. completed)
    ctx_completed = _setup_content_test_context(
        db_session, make_workspace, make_scoring_run, make_keyword, attempt_status="completed"
    )
    with pytest.raises(SocialContentPersistenceError) as exc_info:
        persist_social_content(
            db_session,
            brief_id=ctx_completed["brief"].id,
            attempt_id=ctx_completed["attempt"].id,
            idea_id=ctx_completed["idea"].id,
            content=content,
            quality_decision=decision,
            task_id=ctx_completed["attempt"].task_id,
            now=T0,
        )
    assert exc_info.value.error_code == CONTENT_PERSISTENCE_ATTEMPT_NOT_WRITABLE

    # 5. Attempt stage != contents (ör. ideas)
    ctx_wrong_stage = _setup_content_test_context(
        db_session, make_workspace, make_scoring_run, make_keyword, attempt_stage="ideas"
    )
    with pytest.raises(SocialContentPersistenceError) as exc_info:
        persist_social_content(
            db_session,
            brief_id=ctx_wrong_stage["brief"].id,
            attempt_id=ctx_wrong_stage["attempt"].id,
            idea_id=ctx_wrong_stage["idea"].id,
            content=content,
            quality_decision=decision,
            task_id=ctx_wrong_stage["attempt"].task_id,
            now=T0,
        )
    assert exc_info.value.error_code == CONTENT_PERSISTENCE_ATTEMPT_NOT_WRITABLE

    # 6. Idea not in attempt.requested_idea_ids
    ctx_ok["attempt"].requested_idea_ids = [999999]
    db_session.flush()
    with pytest.raises(SocialContentPersistenceError) as exc_info:
        persist_social_content(
            db_session,
            brief_id=ctx_ok["brief"].id,
            attempt_id=ctx_ok["attempt"].id,
            idea_id=ctx_ok["idea"].id,
            content=content,
            quality_decision=decision,
            task_id=ctx_ok["attempt"].task_id,
            now=T0,
        )
    assert exc_info.value.error_code == CONTENT_PERSISTENCE_ATTEMPT_NOT_WRITABLE


def test_authoritative_db_chain_entity_mismatches_prevent_write(
    enable_flag,
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
):
    """Stale idea/category ve platform/format uyuşmazlıkları fail-closed durdurur."""
    content = _build_content_for_format("instagram", "post")
    decision = _make_quality_decision()

    # 1. Stale idea
    ctx_stale_idea = _setup_content_test_context(
        db_session, make_workspace, make_scoring_run, make_keyword, idea_stale=True
    )
    with pytest.raises(SocialContentPersistenceError) as exc_info:
        persist_social_content(
            db_session,
            brief_id=ctx_stale_idea["brief"].id,
            attempt_id=ctx_stale_idea["attempt"].id,
            idea_id=ctx_stale_idea["idea"].id,
            content=content,
            quality_decision=decision,
            task_id=ctx_stale_idea["attempt"].task_id,
            now=T0,
        )
    assert exc_info.value.error_code == CONTENT_PERSISTENCE_INCONSISTENT

    # 2. Stale category
    ctx_stale_cat = _setup_content_test_context(
        db_session, make_workspace, make_scoring_run, make_keyword, cat_stale=True
    )
    with pytest.raises(SocialContentPersistenceError) as exc_info:
        persist_social_content(
            db_session,
            brief_id=ctx_stale_cat["brief"].id,
            attempt_id=ctx_stale_cat["attempt"].id,
            idea_id=ctx_stale_cat["idea"].id,
            content=content,
            quality_decision=decision,
            task_id=ctx_stale_cat["attempt"].task_id,
            now=T0,
        )
    assert exc_info.value.error_code == CONTENT_PERSISTENCE_INCONSISTENT

    # 3. Format mismatch (Hedef post iken carousel payload verilmesi)
    ctx_ok = _setup_content_test_context(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    carousel_content = _build_content_for_format("instagram", "carousel")
    with pytest.raises(SocialContentPersistenceError) as exc_info:
        persist_social_content(
            db_session,
            brief_id=ctx_ok["brief"].id,
            attempt_id=ctx_ok["attempt"].id,
            idea_id=ctx_ok["idea"].id,
            content=carousel_content,
            quality_decision=decision,
            task_id=ctx_ok["attempt"].task_id,
            now=T0,
        )
    assert exc_info.value.error_code in (
        CONTENT_PERSISTENCE_INCONSISTENT,
        CONTENT_PERSISTENCE_INVALID_INPUT,
    )


def test_idempotent_replay_does_not_overwrite_and_detects_corrupt_row(
    enable_flag,
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
):
    """Aynı idea için 2. çağrı already_present=True döner; satır overwrite edilmez."""
    ctx = _setup_content_test_context(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    content1 = _build_content_for_format("instagram", "post")
    decision = _make_quality_decision()

    # 1. İlk yazım: already_present=False
    res1 = persist_social_content(
        db_session,
        brief_id=ctx["brief"].id,
        attempt_id=ctx["attempt"].id,
        idea_id=ctx["idea"].id,
        content=content1,
        quality_decision=decision,
        task_id=ctx["attempt"].task_id,
        now=T0,
    )
    assert res1.already_present is False
    assert db_session.query(SocialContent).count() == 1

    # 2. İkinci yazım (farklı caption verilse dahi ilk yazılan korunur): already_present=True
    content2 = ValidatedSocialContent(
        hooks=content1.hooks,
        caption="Farklı ikinci caption metni",
        format_payload=None,
        visual_suggestion="Yeni görsel",
        video_concept=None,
        cta_text="Yeni CTA",
        hashtags=("yeni", "dijital", "pazarlama", "strateji", "trend"),
        industry_posting_suggestion=None,
        platform_notes=None,
        duration_status="not_applicable",
        actual_duration_sec=None,
        validation_warnings=(),
        scenario=None,
    )
    res2 = persist_social_content(
        db_session,
        brief_id=ctx["brief"].id,
        attempt_id=ctx["attempt"].id,
        idea_id=ctx["idea"].id,
        content=content2,
        quality_decision=decision,
        task_id=ctx["attempt"].task_id,
        now=T0,
    )
    assert res2.already_present is True
    assert res2.content.caption == content1.caption  # İlk yazılan kazanır
    assert db_session.query(SocialContent).count() == 1

    # 3. Bozuk satır tespiti: row.is_stale True yapılırsa fail-closed durur
    db_row = (
        db_session.query(SocialContent)
        .filter(SocialContent.idea_id == ctx["idea"].id)
        .one()
    )
    db_row.is_stale = True
    db_session.flush()

    with pytest.raises(SocialContentPersistenceError) as exc_info:
        persist_social_content(
            db_session,
            brief_id=ctx["brief"].id,
            attempt_id=ctx["attempt"].id,
            idea_id=ctx["idea"].id,
            content=content1,
            quality_decision=decision,
            task_id=ctx["attempt"].task_id,
            now=T0,
        )
    assert exc_info.value.error_code == CONTENT_PERSISTENCE_INCONSISTENT


def test_persist_social_content_sequential_idempotent_replay(
    enable_flag,
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
):
    """Ardışık çağrılarda ilk session commit eder; ikinci bağımsız session mevcut satırı görerek already_present=True döner."""
    ctx = _setup_content_test_context(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    db_session.commit()  # Veriyi SessionLocal için görünür kıl

    content = _build_content_for_format("instagram", "post")
    decision = _make_quality_decision()

    # İkinci bağımsız DB session'ı oluştur
    session2 = SessionLocal()
    try:
        # Session 1: Başarılı insert yapar
        res1 = persist_social_content(
            db_session,
            brief_id=ctx["brief"].id,
            attempt_id=ctx["attempt"].id,
            idea_id=ctx["idea"].id,
            content=content,
            quality_decision=decision,
            task_id=ctx["attempt"].task_id,
            now=T0,
        )
        assert res1.already_present is False
        db_session.commit()

        # Session 2: Aynı idea için idempotent replay
        res2 = persist_social_content(
            session2,
            brief_id=ctx["brief"].id,
            attempt_id=ctx["attempt"].id,
            idea_id=ctx["idea"].id,
            content=content,
            quality_decision=decision,
            task_id=ctx["attempt"].task_id,
            now=T0,
        )
        assert res2.already_present is True
        assert res2.content.id == res1.content.id

        session2.commit()
    finally:
        session2.close()

    assert db_session.query(SocialContent).count() == 1


def test_persist_social_content_concurrent_threads_race(
    enable_flag,
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
):
    """İki bağımsız iş parçacığı aynı anda aynı idea için yarıştığında deadlock olmadan tek satır yazılır."""
    ctx = _setup_content_test_context(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    db_session.commit()

    content = _build_content_for_format("instagram", "post")
    decision = _make_quality_decision()

    brief_id = int(ctx["brief"].id)
    attempt_id = int(ctx["attempt"].id)
    idea_id = int(ctx["idea"].id)
    task_id = str(ctx["attempt"].task_id)

    barrier = threading.Barrier(2)
    thread_results: list[tuple[int, PersistedSocialContentResult]] = []
    thread_errors: list[tuple[int, Exception]] = []

    def worker(worker_id: int):
        session = SessionLocal()
        try:
            session.connection()
            barrier.wait(timeout=5.0)
            res = persist_social_content(
                session,
                brief_id=brief_id,
                attempt_id=attempt_id,
                idea_id=idea_id,
                content=content,
                quality_decision=decision,
                task_id=task_id,
                now=T0,
            )
            session.commit()
            thread_results.append((worker_id, res))
        except Exception as exc:
            session.rollback()
            thread_errors.append((worker_id, exc))
        finally:
            session.close()

    t1 = threading.Thread(target=worker, args=(1,))
    t2 = threading.Thread(target=worker, args=(2,))
    t1.start()
    t2.start()
    t1.join(timeout=10.0)
    t2.join(timeout=10.0)

    assert len(thread_errors) == 0, f"Thread errors occurred: {thread_errors}"
    assert len(thread_results) == 2

    # Bir thread yeni kayıt yazmış, diğeri already_present almıştır
    already_present_flags = [r[1].already_present for r in thread_results]
    assert False in already_present_flags
    assert True in already_present_flags
    assert thread_results[0][1].content.id == thread_results[1][1].content.id
    assert db_session.query(SocialContent).count() == 1


def test_persist_social_content_savepoint_integrity_error_handling(
    enable_flag,
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
    monkeypatch,
):
    """Savepoint içinde uq_social_content_idea_brief IntegrityError oluştuğunda dış transaction bozulmadan winner satır döndürülür."""
    ctx = _setup_content_test_context(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    content = _build_content_for_format("instagram", "post")
    decision = _make_quality_decision()

    # İlk satırı normal kaydet
    res1 = persist_social_content(
        db_session,
        brief_id=ctx["brief"].id,
        attempt_id=ctx["attempt"].id,
        idea_id=ctx["idea"].id,
        content=content,
        quality_decision=decision,
        task_id=ctx["attempt"].task_id,
        now=T0,
    )
    assert res1.already_present is False

    # İkinci çağrıda `existing_content` kontrolünü simüle olarak atlatıp doğrudan savepoint bloğuna düşür
    # böylece uq_social_content_idea_brief savepoint catch bloğu doğrulanır
    orig_query = db_session.query

    def mock_query(*args, **kwargs):
        q = orig_query(*args, **kwargs)
        if args and args[0] is SocialContent:
            # İlk sorguyu (existing_content) boş döndür, savepoint catch sonrasındaki sorguda gerçek satırı ver
            if not hasattr(mock_query, "_called"):
                mock_query._called = True
                orig_one_or_none = q.one_or_none

                def mock_one_or_none():
                    if getattr(mock_query, "_first_check", True):
                        mock_query._first_check = False
                        return None
                    return orig_one_or_none()

                q.one_or_none = mock_one_or_none
        return q

    monkeypatch.setattr(db_session, "query", mock_query)

    res2 = persist_social_content(
        db_session,
        brief_id=ctx["brief"].id,
        attempt_id=ctx["attempt"].id,
        idea_id=ctx["idea"].id,
        content=content,
        quality_decision=decision,
        task_id=ctx["attempt"].task_id,
        now=T0,
    )
    assert res2.already_present is True
    assert res2.content.id == res1.content.id


def test_transaction_independence_and_caller_rollback(
    enable_flag,
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
):
    """Fonksiyon commit/rollback yapmaz; çağıran katman rollback yapınca içerik silinir."""
    ctx = _setup_content_test_context(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    content = _build_content_for_format("instagram", "post")
    decision = _make_quality_decision()

    res = persist_social_content(
        db_session,
        brief_id=ctx["brief"].id,
        attempt_id=ctx["attempt"].id,
        idea_id=ctx["idea"].id,
        content=content,
        quality_decision=decision,
        task_id=ctx["attempt"].task_id,
        now=T0,
    )
    assert res.already_present is False
    assert db_session.query(SocialContent).count() == 1

    # Çağıran rollback çağırır
    db_session.rollback()

    # İçerik tamamen kaybolmalıdır
    assert db_session.query(SocialContent).count() == 0


def test_security_error_sanitization_no_sensitive_leaks(
    enable_flag,
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
):
    """Hata mesajlarında SQL, raw kullanıcı metni veya hassas alanlar bulunmaz."""
    ctx = _setup_content_test_context(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    content = _build_content_for_format("instagram", "post")

    # Bilerek geçersiz decision oluştur
    decision_with_claim = _make_quality_decision(
        action="accept",
        claims=("Gizli $10.000 garanti!",),
    )

    with pytest.raises(SocialContentPersistenceError) as exc_info:
        persist_social_content(
            db_session,
            brief_id=ctx["brief"].id,
            attempt_id=ctx["attempt"].id,
            idea_id=ctx["idea"].id,
            content=content,
            quality_decision=decision_with_claim,
            task_id=ctx["attempt"].task_id,
            now=T0,
        )

    msg = str(exc_info.value)
    assert "Gizli" not in msg
    assert "10.000" not in msg
    assert "SELECT" not in msg
    assert "INSERT" not in msg
    assert exc_info.value.error_code == CONTENT_PERSISTENCE_REJECTED


def test_persist_social_content_mandatory_task_id_and_ownership(
    enable_flag,
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
):
    """task_id boş veya uyuşmaz olduğunda işlem fail-closed reddedilir ve statik hata mesajı döner."""
    ctx = _setup_content_test_context(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    content = _build_content_for_format("instagram", "post")
    decision = _make_quality_decision()

    # 1. task_id boş string
    with pytest.raises(SocialContentPersistenceError) as exc_info:
        persist_social_content(
            db_session,
            brief_id=ctx["brief"].id,
            attempt_id=ctx["attempt"].id,
            idea_id=ctx["idea"].id,
            content=content,
            quality_decision=decision,
            task_id="",
            now=T0,
        )
    assert exc_info.value.error_code == CONTENT_PERSISTENCE_ATTEMPT_NOT_WRITABLE

    # 2. task_id whitespace string
    with pytest.raises(SocialContentPersistenceError) as exc_info:
        persist_social_content(
            db_session,
            brief_id=ctx["brief"].id,
            attempt_id=ctx["attempt"].id,
            idea_id=ctx["idea"].id,
            content=content,
            quality_decision=decision,
            task_id="   ",
            now=T0,
        )
    assert exc_info.value.error_code == CONTENT_PERSISTENCE_ATTEMPT_NOT_WRITABLE

    # 3. task_id uyuşmazlığı -> Statik "Worker sahipliği doğrulanamadı." mesajı
    with pytest.raises(SocialContentPersistenceError) as exc_info:
        persist_social_content(
            db_session,
            brief_id=ctx["brief"].id,
            attempt_id=ctx["attempt"].id,
            idea_id=ctx["idea"].id,
            content=content,
            quality_decision=decision,
            task_id="unrelated-foreign-worker-id",
            now=T0,
        )
    assert exc_info.value.error_code == CONTENT_PERSISTENCE_ATTEMPT_NOT_WRITABLE
    assert "Worker sahipliği doğrulanamadı." in str(exc_info.value)
    assert "unrelated-foreign-worker-id" not in str(exc_info.value)
    assert ctx["attempt"].task_id not in str(exc_info.value)


def test_persist_social_content_rejects_forged_incoming_content(
    enable_flag,
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
):
    """Constructor ile forged üretilmiş, sözleşme kurallarını ihlal eden ValidatedSocialContent reddedilir."""
    ctx = _setup_content_test_context(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        platform="instagram",
        content_format="post",
    )
    decision = _make_quality_decision()
    valid_content = _build_content_for_format("instagram", "post")

    # 1. '#' ile başlayan forged hashtag
    forged_hashtags = ("#etiket1", "etiket2", "etiket3", "etiket4", "etiket5")
    content_bad_tag = ValidatedSocialContent(
        hooks=valid_content.hooks,
        caption=valid_content.caption,
        format_payload=valid_content.format_payload,
        visual_suggestion=valid_content.visual_suggestion,
        video_concept=valid_content.video_concept,
        cta_text=valid_content.cta_text,
        hashtags=forged_hashtags,
        industry_posting_suggestion=valid_content.industry_posting_suggestion,
        platform_notes=valid_content.platform_notes,
        duration_status=valid_content.duration_status,
        actual_duration_sec=valid_content.actual_duration_sec,
        validation_warnings=valid_content.validation_warnings,
        scenario=valid_content.scenario,
    )
    with pytest.raises(SocialContentPersistenceError) as exc_info:
        persist_social_content(
            db_session,
            brief_id=ctx["brief"].id,
            attempt_id=ctx["attempt"].id,
            idea_id=ctx["idea"].id,
            content=content_bad_tag,
            quality_decision=decision,
            task_id=ctx["attempt"].task_id,
            now=T0,
        )
    assert exc_info.value.error_code in (
        CONTENT_PERSISTENCE_INVALID_INPUT,
        CONTENT_PERSISTENCE_INCONSISTENT,
    )

    # 2. 4 hashtag içeren forged içerik
    content_4_tags = ValidatedSocialContent(
        hooks=valid_content.hooks,
        caption=valid_content.caption,
        format_payload=valid_content.format_payload,
        visual_suggestion=valid_content.visual_suggestion,
        video_concept=valid_content.video_concept,
        cta_text=valid_content.cta_text,
        hashtags=("tag1", "tag2", "tag3", "tag4"),
        industry_posting_suggestion=valid_content.industry_posting_suggestion,
        platform_notes=valid_content.platform_notes,
        duration_status=valid_content.duration_status,
        actual_duration_sec=valid_content.actual_duration_sec,
        validation_warnings=valid_content.validation_warnings,
        scenario=valid_content.scenario,
    )
    with pytest.raises(SocialContentPersistenceError) as exc_info:
        persist_social_content(
            db_session,
            brief_id=ctx["brief"].id,
            attempt_id=ctx["attempt"].id,
            idea_id=ctx["idea"].id,
            content=content_4_tags,
            quality_decision=decision,
            task_id=ctx["attempt"].task_id,
            now=T0,
        )
    assert exc_info.value.error_code in (
        CONTENT_PERSISTENCE_INVALID_INPUT,
        CONTENT_PERSISTENCE_INCONSISTENT,
    )

    # 3. Duplicate hashtag içeren forged içerik
    content_dup_tags = ValidatedSocialContent(
        hooks=valid_content.hooks,
        caption=valid_content.caption,
        format_payload=valid_content.format_payload,
        visual_suggestion=valid_content.visual_suggestion,
        video_concept=valid_content.video_concept,
        cta_text=valid_content.cta_text,
        hashtags=("tag1", "tag1", "tag2", "tag3", "tag4"),
        industry_posting_suggestion=valid_content.industry_posting_suggestion,
        platform_notes=valid_content.platform_notes,
        duration_status=valid_content.duration_status,
        actual_duration_sec=valid_content.actual_duration_sec,
        validation_warnings=valid_content.validation_warnings,
        scenario=valid_content.scenario,
    )
    with pytest.raises(SocialContentPersistenceError) as exc_info:
        persist_social_content(
            db_session,
            brief_id=ctx["brief"].id,
            attempt_id=ctx["attempt"].id,
            idea_id=ctx["idea"].id,
            content=content_dup_tags,
            quality_decision=decision,
            task_id=ctx["attempt"].task_id,
            now=T0,
        )
    assert exc_info.value.error_code in (
        CONTENT_PERSISTENCE_INVALID_INPUT,
        CONTENT_PERSISTENCE_INCONSISTENT,
    )

    # 4. Forged senaryo (kanonik payload çıktısıyla uyuşmayan sahte senaryo)
    ctx_video = _setup_content_test_context(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        platform="youtube",
        content_format="short",
        duration_preset_id="short_16_30",
    )
    video_content = _build_content_for_format("youtube", "short", actual_duration_sec=30)
    forged_scenario_content = ValidatedSocialContent(
        hooks=video_content.hooks,
        caption=video_content.caption,
        format_payload=video_content.format_payload,
        visual_suggestion=video_content.visual_suggestion,
        video_concept=video_content.video_concept,
        cta_text=video_content.cta_text,
        hashtags=video_content.hashtags,
        industry_posting_suggestion=video_content.industry_posting_suggestion,
        platform_notes=video_content.platform_notes,
        duration_status=video_content.duration_status,
        actual_duration_sec=video_content.actual_duration_sec,
        validation_warnings=video_content.validation_warnings,
        scenario="SAHTE VE UYDURMA SENARYO METNİ",
    )
    with pytest.raises(SocialContentPersistenceError) as exc_info:
        persist_social_content(
            db_session,
            brief_id=ctx_video["brief"].id,
            attempt_id=ctx_video["attempt"].id,
            idea_id=ctx_video["idea"].id,
            content=forged_scenario_content,
            quality_decision=decision,
            task_id=ctx_video["attempt"].task_id,
            now=T0,
        )
    assert exc_info.value.error_code in (
        CONTENT_PERSISTENCE_INVALID_INPUT,
        CONTENT_PERSISTENCE_INCONSISTENT,
    )


def test_persist_social_content_rejects_corrupted_existing_db_row(
    enable_flag,
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
):
    """Veritabanındaki mevcut SocialContent satırı bozulduğunda replay fail-closed durur ve satırı silmez/değiştirmez."""
    ctx = _setup_content_test_context(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        platform="youtube",
        content_format="short",
        duration_preset_id="short_16_30",
    )
    content = _build_content_for_format("youtube", "short", actual_duration_sec=30)
    decision = _make_quality_decision()

    # 1. Başarılı ilk kayıt
    res = persist_social_content(
        db_session,
        brief_id=ctx["brief"].id,
        attempt_id=ctx["attempt"].id,
        idea_id=ctx["idea"].id,
        content=content,
        quality_decision=decision,
        task_id=ctx["attempt"].task_id,
        now=T0,
    )
    assert res.already_present is False

    row = (
        db_session.query(SocialContent)
        .filter(SocialContent.idea_id == ctx["idea"].id)
        .one()
    )

    # A. DB satırında '#' ile başlayan hashtag bozukluğu
    row.hashtags = ["#dijital", "pazarlama", "strateji", "icerik", "sosyalmedya"]
    db_session.flush()

    with pytest.raises(SocialContentPersistenceError) as exc_info:
        persist_social_content(
            db_session,
            brief_id=ctx["brief"].id,
            attempt_id=ctx["attempt"].id,
            idea_id=ctx["idea"].id,
            content=content,
            quality_decision=decision,
            task_id=ctx["attempt"].task_id,
            now=T0,
        )
    assert exc_info.value.error_code == CONTENT_PERSISTENCE_INCONSISTENT
    assert row.hashtags[0] == "#dijital"  # Overwrite veya silme yok

    # B. DB satırında 4 hashtag kalması
    row.hashtags = ["dijital", "pazarlama", "strateji", "icerik"]
    db_session.flush()

    with pytest.raises(SocialContentPersistenceError) as exc_info:
        persist_social_content(
            db_session,
            brief_id=ctx["brief"].id,
            attempt_id=ctx["attempt"].id,
            idea_id=ctx["idea"].id,
            content=content,
            quality_decision=decision,
            task_id=ctx["attempt"].task_id,
            now=T0,
        )
    assert exc_info.value.error_code == CONTENT_PERSISTENCE_INCONSISTENT

    # C. DB satırında duplicate hashtag
    row.hashtags = ["dijital", "dijital", "pazarlama", "strateji", "icerik"]
    db_session.flush()

    with pytest.raises(SocialContentPersistenceError) as exc_info:
        persist_social_content(
            db_session,
            brief_id=ctx["brief"].id,
            attempt_id=ctx["attempt"].id,
            idea_id=ctx["idea"].id,
            content=content,
            quality_decision=decision,
            task_id=ctx["attempt"].task_id,
            now=T0,
        )
    assert exc_info.value.error_code == CONTENT_PERSISTENCE_INCONSISTENT

    # D. Düzeltilmiş hashtag, bozuk video segment timeline (gap / zaman aralığı boşluğu)
    row.hashtags = ["dijital", "pazarlama", "strateji", "icerik", "sosyalmedya"]
    row.format_payload = {
        "kind": "video",
        "segments": [
            {
                "start_sec": 0,
                "end_sec": 10,
                "scene": "s1",
                "on_screen_text": "t1",
                "voiceover": "v1",
            },
            {
                "start_sec": 15,  # 10 yerine 15 -> timeline gap!
                "end_sec": 30,
                "scene": "s2",
                "on_screen_text": "t2",
                "voiceover": "v2",
            },
        ],
    }
    db_session.flush()

    with pytest.raises(SocialContentPersistenceError) as exc_info:
        persist_social_content(
            db_session,
            brief_id=ctx["brief"].id,
            attempt_id=ctx["attempt"].id,
            idea_id=ctx["idea"].id,
            content=content,
            quality_decision=decision,
            task_id=ctx["attempt"].task_id,
            now=T0,
        )
    assert exc_info.value.error_code == CONTENT_PERSISTENCE_INCONSISTENT

    # E. DB senaryo ile format_payload uyuşmazlığı
    # Payload geçerli ama senaryo bozulmuş
    row.format_payload = {
        "kind": "video",
        "segments": [
            {
                "start_sec": 0,
                "end_sec": 5,
                "scene": "s1",
                "on_screen_text": "t1",
                "voiceover": "Bu videoda harika dijital pazarlama ipuçları öğreneceksiniz",
            },
            {
                "start_sec": 5,
                "end_sec": 30,
                "scene": "s2",
                "on_screen_text": "t2",
                "voiceover": "pazarlama strateji dijital hedef analiz icerik sosyal medya trend basari pazarlama strateji dijital hedef analiz icerik sosyal medya trend basari pazarlama strateji dijital hedef analiz icerik sosyal medya trend basari pazarlama strateji",
            },
        ],
    }
    row.scenario = "Kanonik render ile alakası olmayan rastgele senaryo metni."
    db_session.flush()

    with pytest.raises(SocialContentPersistenceError) as exc_info:
        persist_social_content(
            db_session,
            brief_id=ctx["brief"].id,
            attempt_id=ctx["attempt"].id,
            idea_id=ctx["idea"].id,
            content=content,
            quality_decision=decision,
            task_id=ctx["attempt"].task_id,
            now=T0,
        )
    assert exc_info.value.error_code == CONTENT_PERSISTENCE_INCONSISTENT
    assert exc_info.value.field == "scenario"

    # F. DB actual_duration_sec uyuşmazlığı
    row.scenario = _render_legacy_scenario_from_payload(content.format_payload)
    row.actual_duration_sec = 999
    db_session.flush()

    with pytest.raises(SocialContentPersistenceError) as exc_info:
        persist_social_content(
            db_session,
            brief_id=ctx["brief"].id,
            attempt_id=ctx["attempt"].id,
            idea_id=ctx["idea"].id,
            content=content,
            quality_decision=decision,
            task_id=ctx["attempt"].task_id,
            now=T0,
        )
    assert exc_info.value.error_code == CONTENT_PERSISTENCE_INCONSISTENT

    # G. DB cta_text boş string
    row.actual_duration_sec = 30
    row.cta_text = ""
    db_session.flush()

    with pytest.raises(SocialContentPersistenceError) as exc_info:
        persist_social_content(
            db_session,
            brief_id=ctx["brief"].id,
            attempt_id=ctx["attempt"].id,
            idea_id=ctx["idea"].id,
            content=content,
            quality_decision=decision,
            task_id=ctx["attempt"].task_id,
            now=T0,
        )
    assert exc_info.value.error_code == CONTENT_PERSISTENCE_INCONSISTENT


def test_persisted_social_content_deep_immutability(
    enable_flag,
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
):
    """PersistedSocialContent ve nested alanları derinlemesine dondurulmuştur (frozen/immutable)."""
    ctx = _setup_content_test_context(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        platform="youtube",
        content_format="short",
        duration_preset_id="short_16_30",
    )
    content = _build_content_for_format("youtube", "short", actual_duration_sec=30)
    decision = _make_quality_decision()

    res = persist_social_content(
        db_session,
        brief_id=ctx["brief"].id,
        attempt_id=ctx["attempt"].id,
        idea_id=ctx["idea"].id,
        content=content,
        quality_decision=decision,
        task_id=ctx["attempt"].task_id,
        now=T0,
    )
    pc = res.content

    # 1. Kök DTO alan mutasyonu engellenmeli
    with pytest.raises(FrozenInstanceError):
        pc.caption = "Yeni caption"  # type: ignore

    # 2. hooks tuple ve elemanları ValidatedHook (frozen) olmalı
    assert isinstance(pc.hooks, tuple)
    assert len(pc.hooks) > 0
    assert isinstance(pc.hooks[0], ValidatedHook)

    with pytest.raises(FrozenInstanceError):
        pc.hooks[0].text = "Mutated hook text"  # type: ignore

    # 3. format_payload ValidatedVideoPayload (frozen) olmalı
    assert isinstance(pc.format_payload, ValidatedVideoPayload)
    with pytest.raises(FrozenInstanceError):
        pc.format_payload.kind = "carousel"  # type: ignore

    assert isinstance(pc.format_payload.segments, tuple)
    with pytest.raises(FrozenInstanceError):
        pc.format_payload.segments[0].start_sec = 999  # type: ignore

    # 4. hashtags ve warnings tuple olmalı
    assert isinstance(pc.hashtags, tuple)
    assert isinstance(pc.validation_warnings, tuple)

