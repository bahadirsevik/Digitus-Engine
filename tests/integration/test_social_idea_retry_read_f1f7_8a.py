# -*- coding: utf-8 -*-
"""MİKRO FAZ F1-F.7.8a — Retry Polling Read-Contract ve Snapshot Paritesi Testleri.

Bu test süiti, app/core/social/idea_retry_read.py içerisindeki load_social_idea_retry_result
servisinin fail-closed doğrulama, completed/partial semantiği, DB satır kontrolleri,
canonical target ve historical polling bağımsızlığı kurallarını PostgreSQL test DB üzerinde
kapsamlı ve izole biçimde doğrular:

1. test_retry_read_source_stage_not_ideas_fails
2. test_retry_read_source_status_pending_or_running_fails
3. test_retry_read_canonical_target_count_out_of_bounds_fails (0 ve 7 hedef)
4. test_retry_read_invalid_platform_format_fails (matris dışı format & video duration ihlali)
5. test_retry_read_idea_category_not_in_source_plan_fails
6. test_retry_read_invalid_title_or_description_bounds_fails (boş, trim edilmemiş, aşırı uzun)
7. test_retry_read_invalid_trend_alignment_fails (bool, NaN, inf, overflow, aralık dışı)
8. test_retry_read_completed_generated_target_ids_mismatch_fails (eksik, fazla, ters sıra)
9. test_retry_read_completed_generated_count_mismatch_fails
10. test_retry_read_completed_assignment_category_mismatch_fails
11. test_retry_read_completed_with_warnings_or_reason_fails
12. test_retry_read_partial_empty_generated_fails
13. test_retry_read_partial_generated_count_mismatch_fails
14. test_retry_read_partial_warning_count_or_duplicate_fails
15. test_retry_read_partial_warning_reason_or_category_mismatch_fails
16. test_retry_read_partial_historical_independence_when_target_filled_later
17. test_retry_read_strictly_read_only (0 commit, 0 rollback, 0 flush, 0 mutation)
18. test_retry_read_completed_success_contract
"""
from __future__ import annotations

import math
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy.orm import Session

from app.core.social.idea_planner import build_social_idea_generation_plan
from app.core.social.idea_read import SocialIdeaReadError
from app.core.social.idea_retry_read import load_social_idea_retry_result
from app.database.models import (
    ChannelPool,
    SocialBrief,
    SocialBriefKeyword,
    SocialBriefTarget,
    SocialCategory,
    SocialGenerationAttempt,
    SocialIdea,
)

T0 = datetime(2026, 9, 26, 10, 0, 0, tzinfo=timezone.utc)


def _setup_retry_read_env(
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
    targets_data: list[tuple[str, str]] | None = None,
    filled_targets_count: int = 1,
):
    """Testler için standart workspace, brief, hedefler, kategoriler ve kaynak attempt oluşturur."""
    workspace = make_workspace(name=f"Retry Read Brand {uuid.uuid4().hex[:6]}", status="confirmed")
    run = make_scoring_run(
        brand_profile_id=workspace.id,
        status="completed",
        algorithm_version="v2",
        channel_pool_policy_version=1,
        relevance_anchor_version=1,
        channel_assignment_version=1,
        skip_relevance=True,
    )

    kws = []
    for i in range(2):
        kw = make_keyword(
            text_value=f"retry read kw {i + 1} {uuid.uuid4().hex[:4]}",
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
        brand_name_snapshot="Retry Read Brand Snapshot",
        brand_context_snapshot="Retry Read Context Snapshot",
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
        targets_data = [
            ("instagram", "post"),
            ("twitter", "post"),
            ("linkedin", "post"),
        ]

    targets = []
    for plat, fmt in targets_data:
        t = SocialBriefTarget(
            brief_id=brief.id,
            platform=plat,
            content_format=fmt,
            duration_preset_id="x_1_15" if plat == "twitter" and fmt == "video" else ("long_60_180" if fmt == "video" else None),
            duration_min_sec=1 if plat == "twitter" and fmt == "video" else (60 if fmt == "video" else None),
            duration_max_sec=15 if plat == "twitter" and fmt == "video" else (180 if fmt == "video" else None),
        )
        db_session.add(t)
        targets.append(t)
    db_session.flush()

    categories = []
    for i in range(2):
        cat = SocialCategory(
            scoring_run_id=run.id,
            brief_id=brief.id,
            category_name=f"Read Kat {i + 1}",
            category_type="educational",
            description=f"Açıklama {i + 1}.",
            is_stale=False,
            relevance_score=0.90,
            suggested_keyword_ids=[kws[0].id],
        )
        db_session.add(cat)
        categories.append(cat)
    db_session.flush()

    plan = build_social_idea_generation_plan(
        selected_category_ids=tuple(c.id for c in categories),
        target_ids=tuple(t.id for t in targets),
        ideas_per_category=len(targets),
    )

    persisted_target_ids = [t.id for t in targets[:filled_targets_count]]
    source_cov = {
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
        idempotency_key=f"source-read-{uuid.uuid4()}",
        status="completed" if filled_targets_count == len(targets) else "partial",
        requested_target_ids=[t.id for t in targets],
        coverage=source_cov,
        warnings=[],
        heartbeat_at=T0,
        lease_expires_at=None,
        completed_at=T0 + timedelta(seconds=60),
    )
    db_session.add(source_attempt)
    db_session.flush()

    # Doldurulmuş baseline hedef için SocialIdea ekle
    for t in targets[:filled_targets_count]:
        idea = SocialIdea(
            category_id=categories[0].id,
            keyword_id=kws[0].id,
            brief_id=brief.id,
            brief_target_id=t.id,
            idea_title=f"Baseline Fikir {t.platform}",
            idea_description="Bu hedef baseline'da doldurulmuş fikir açıklamasıdır.",
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


def _create_retry_attempt(
    db_session: Session,
    brief: SocialBrief,
    source_attempt: SocialGenerationAttempt,
    targets: list[SocialBriefTarget],
    categories: list[SocialCategory],
    *,
    status: str = "completed",
    generated_targets: list[SocialBriefTarget] | None = None,
    warnings: list[dict] | None = None,
    reason_code: str | None = None,
    error_message: str | None = None,
    baseline_filled_count: int = 1,
) -> SocialGenerationAttempt:
    """Belirtilen özelliklerde bir retry attempt oluşturur."""
    canonical_target_ids = [t.id for t in targets]
    persisted_at_start = [t.id for t in targets[:baseline_filled_count]]
    missing = [t.id for t in targets[baseline_filled_count:]]

    if generated_targets is None:
        gen_ids = list(missing) if status == "completed" else []
    else:
        gen_ids = [t.id for t in generated_targets]

    retry_cov = {
        "schema_version": "ideas_retry_plan_v1",
        "request": {
            "source_attempt_id": source_attempt.id,
        },
        "baseline": {
            "canonical_target_ids": canonical_target_ids,
            "persisted_target_ids_at_start": persisted_at_start,
        },
        "plan": {
            "total_requested": len(missing),
            "missing_target_ids": missing,
            "assignments": [
                {
                    "category_id": categories[0].id,
                    "target_id": tid,
                    "requested_count": 1,
                }
                for tid in missing
            ],
        },
        "generated": {
            "total_accepted": len(gen_ids),
            "target_ids": gen_ids,
        },
    }

    attempt = SocialGenerationAttempt(
        brief_id=brief.id,
        stage="ideas_retry",
        idempotency_key=f"retry-attempt-{uuid.uuid4()}",
        status=status,
        requested_target_ids=missing,
        coverage=retry_cov,
        warnings=warnings if warnings is not None else [],
        reason_code=reason_code,
        error_message=error_message,
        heartbeat_at=T0,
        lease_expires_at=None,
        completed_at=T0 + timedelta(seconds=120) if status in ("completed", "partial", "failed") else None,
    )
    db_session.add(attempt)
    db_session.commit()
    db_session.refresh(attempt)
    return attempt


# ==================== 1. KAYNAK ATTEMPT DOĞRULAMASI ====================

def test_retry_read_source_stage_not_ideas_fails(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """Kaynak attempt stage 'ideas' değilse IDEA_READ_INCONSISTENT dönmeli."""
    workspace, run, brief, categories, targets, kws, source_attempt = _setup_retry_read_env(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    source_attempt.stage = "categories"
    db_session.commit()

    retry_attempt = _create_retry_attempt(db_session, brief, source_attempt, targets, categories)

    with pytest.raises(SocialIdeaReadError) as exc_info:
        load_social_idea_retry_result(
            db_session,
            brief_id=brief.id,
            attempt_id=retry_attempt.id,
            brand_profile_id=workspace.id,
        )
    assert exc_info.value.error_code == "IDEA_READ_INCONSISTENT"


def test_retry_read_source_status_pending_or_running_fails(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """Kaynak attempt status 'pending' veya 'running' ise IDEA_READ_INCONSISTENT dönmeli."""
    workspace, run, brief, categories, targets, kws, source_attempt = _setup_retry_read_env(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    source_attempt.status = "running"
    db_session.commit()

    retry_attempt = _create_retry_attempt(db_session, brief, source_attempt, targets, categories)

    with pytest.raises(SocialIdeaReadError) as exc_info:
        load_social_idea_retry_result(
            db_session,
            brief_id=brief.id,
            attempt_id=retry_attempt.id,
            brand_profile_id=workspace.id,
        )
    assert exc_info.value.error_code == "IDEA_READ_INCONSISTENT"


# ==================== 2. CANONICAL BRIEF TARGET DOĞRULAMASI ====================

def test_retry_read_canonical_target_count_out_of_bounds_fails(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """Target sayısı 1-6 aralığında değilse fail-closed reddedilmeli."""
    workspace, run, brief, categories, targets, kws, source_attempt = _setup_retry_read_env(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    retry_attempt = _create_retry_attempt(db_session, brief, source_attempt, targets, categories)

    # 4 hedef daha ekleyip toplamı 7 yapalım
    extra_formats = [("instagram", "carousel"), ("twitter", "video"), ("linkedin", "carousel"), ("youtube", "video")]
    for plat, fmt in extra_formats:
        extra_t = SocialBriefTarget(
            brief_id=brief.id,
            platform=plat,
            content_format=fmt,
            duration_preset_id="x_1_15" if plat == "twitter" else ("long_60_180" if fmt == "video" else None),
            duration_min_sec=1 if plat == "twitter" and fmt == "video" else (60 if fmt == "video" else None),
            duration_max_sec=15 if plat == "twitter" and fmt == "video" else (180 if fmt == "video" else None),
        )
        db_session.add(extra_t)
    db_session.commit()

    with pytest.raises(SocialIdeaReadError) as exc_info:
        load_social_idea_retry_result(
            db_session,
            brief_id=brief.id,
            attempt_id=retry_attempt.id,
            brand_profile_id=workspace.id,
        )
    assert exc_info.value.error_code == "IDEA_READ_INCONSISTENT"


def test_retry_read_invalid_platform_format_fails(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """Canonical format matrisinde olmayan platform fail-closed reddedilmeli."""
    workspace, run, brief, categories, targets, kws, source_attempt = _setup_retry_read_env(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    retry_attempt = _create_retry_attempt(db_session, brief, source_attempt, targets, categories)

    # Hedeflerden birine kanonik matris dışı platform verelim
    targets[1].platform = "invalid_platform"
    db_session.commit()

    with pytest.raises(SocialIdeaReadError) as exc_info:
        load_social_idea_retry_result(
            db_session,
            brief_id=brief.id,
            attempt_id=retry_attempt.id,
            brand_profile_id=workspace.id,
        )
    assert exc_info.value.error_code == "IDEA_READ_INCONSISTENT"


def test_retry_read_video_duration_preset_profile_mismatch_fails(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """Video formatının süre ön ayarı format matrisindeki süre profiliyle uyumsuzsa fail-closed reddedilmeli."""
    targets_data = [
        ("instagram", "post"),
        ("twitter", "video"),
    ]
    # twitter/video requires x_video preset, but we set short_1_15 (from short_video)
    workspace, run, brief, categories, targets, kws, source_attempt = _setup_retry_read_env(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        targets_data=targets_data,
        filled_targets_count=1,
    )
    # Target 1 (twitter/video) setup
    targets[1].duration_preset_id = "short_1_15"
    targets[1].duration_min_sec = 1
    targets[1].duration_max_sec = 15
    db_session.commit()

    retry_attempt = _create_retry_attempt(db_session, brief, source_attempt, targets, categories)

    with pytest.raises(SocialIdeaReadError) as exc_info:
        load_social_idea_retry_result(
            db_session,
            brief_id=brief.id,
            attempt_id=retry_attempt.id,
            brand_profile_id=workspace.id,
        )
    assert exc_info.value.error_code == "IDEA_READ_INCONSISTENT"


def test_retry_read_video_duration_bounds_mismatch_fails(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """Video formatının süre sınırları preset tanımıyla uyumsuzsa fail-closed reddedilmeli."""
    targets_data = [
        ("instagram", "post"),
        ("twitter", "video"),
    ]
    workspace, run, brief, categories, targets, kws, source_attempt = _setup_retry_read_env(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        targets_data=targets_data,
        filled_targets_count=1,
    )
    # x_1_15 preset is 1 to 15, but we set 2 to 14
    targets[1].duration_preset_id = "x_1_15"
    targets[1].duration_min_sec = 2
    targets[1].duration_max_sec = 14
    db_session.commit()

    retry_attempt = _create_retry_attempt(db_session, brief, source_attempt, targets, categories)

    with pytest.raises(SocialIdeaReadError) as exc_info:
        load_social_idea_retry_result(
            db_session,
            brief_id=brief.id,
            attempt_id=retry_attempt.id,
            brand_profile_id=workspace.id,
        )
    assert exc_info.value.error_code == "IDEA_READ_INCONSISTENT"


# ==================== 3. DB SOCIALIDEA SATIR KONTROLLERİ ====================

def test_retry_read_idea_category_not_in_source_plan_fails(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """Fikir kategorisi source_plan.covered_category_ids içinde değilse reddedilmeli."""
    workspace, run, brief, categories, targets, kws, source_attempt = _setup_retry_read_env(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    # Yeni bir kategori ekleyelim (kaynak planda yer almayan)
    out_cat = SocialCategory(
        scoring_run_id=run.id,
        brief_id=brief.id,
        category_name="Plan Dışı Kategori",
        category_type="promotional",
        description="Açıklama.",
        is_stale=False,
        relevance_score=0.9,
    )
    db_session.add(out_cat)
    db_session.flush()

    retry_attempt = _create_retry_attempt(db_session, brief, source_attempt, targets, categories)

    # İlgili kategoriye bağlı bir fikir ekleyelim
    bad_idea = SocialIdea(
        category_id=out_cat.id,
        keyword_id=kws[0].id,
        brief_id=brief.id,
        brief_target_id=targets[1].id,
        idea_title="Plan Dışı Fikir",
        idea_description="Bu fikir plan dışı kategoriye aittir.",
        target_platform=targets[1].platform,
        content_format=targets[1].content_format,
        trend_alignment=0.8,
        is_stale=False,
        is_selected=False,
        regeneration_count=0,
        created_at=T0,
    )
    db_session.add(bad_idea)
    db_session.commit()

    with pytest.raises(SocialIdeaReadError) as exc_info:
        load_social_idea_retry_result(
            db_session,
            brief_id=brief.id,
            attempt_id=retry_attempt.id,
            brand_profile_id=workspace.id,
        )
    assert exc_info.value.error_code == "IDEA_READ_INCONSISTENT"


def test_retry_read_invalid_title_or_description_bounds_fails(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """Boş, trim edilmemiş veya limit dışı title/description IDEA_READ_INCONSISTENT üretmeli."""
    workspace, run, brief, categories, targets, kws, source_attempt = _setup_retry_read_env(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    retry_attempt = _create_retry_attempt(db_session, brief, source_attempt, targets, categories)

    # Mevcut baseline fikrin başlığını whitespace'li yapalım
    idea = db_session.query(SocialIdea).filter(SocialIdea.brief_id == brief.id).first()
    idea.idea_title = " Başında boşluk olan başlık"
    db_session.commit()

    with pytest.raises(SocialIdeaReadError) as exc_info:
        load_social_idea_retry_result(
            db_session,
            brief_id=brief.id,
            attempt_id=retry_attempt.id,
            brand_profile_id=workspace.id,
        )
    assert exc_info.value.error_code == "IDEA_READ_INCONSISTENT"


def test_retry_read_invalid_trend_alignment_fails(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """trend_alignment NaN, Inf veya aşırı büyük sayıda fail-closed davranmalı (OverflowError yutulmalı)."""
    workspace, run, brief, categories, targets, kws, source_attempt = _setup_retry_read_env(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    retry_attempt = _create_retry_attempt(db_session, brief, source_attempt, targets, categories)

    idea = db_session.query(SocialIdea).filter(SocialIdea.brief_id == brief.id).first()

    # 1. NaN
    idea.trend_alignment = float("nan")
    db_session.commit()
    with pytest.raises(SocialIdeaReadError) as exc_info:
        load_social_idea_retry_result(
            db_session,
            brief_id=brief.id,
            attempt_id=retry_attempt.id,
            brand_profile_id=workspace.id,
        )
    assert exc_info.value.error_code == "IDEA_READ_INCONSISTENT"

    # 2. Infinity
    idea.trend_alignment = float("inf")
    db_session.commit()
    with pytest.raises(SocialIdeaReadError) as exc_info:
        load_social_idea_retry_result(
            db_session,
            brief_id=brief.id,
            attempt_id=retry_attempt.id,
            brand_profile_id=workspace.id,
        )
    assert exc_info.value.error_code == "IDEA_READ_INCONSISTENT"

    # 3. Aralık dışı (> 1.0)
    idea.trend_alignment = 1.05
    db_session.commit()
    with pytest.raises(SocialIdeaReadError) as exc_info:
        load_social_idea_retry_result(
            db_session,
            brief_id=brief.id,
            attempt_id=retry_attempt.id,
            brand_profile_id=workspace.id,
        )
    assert exc_info.value.error_code == "IDEA_READ_INCONSISTENT"


# ==================== 4. COMPLETED ATTEMPT SEMANTİĞİ ====================

def test_retry_read_completed_generated_target_ids_mismatch_fails(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """Completed attempt generated_target_ids missing_target_ids ile tam eşleşmezse fail etmeli."""
    workspace, run, brief, categories, targets, kws, source_attempt = _setup_retry_read_env(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    # generated hedefleri eksik bırakalım
    retry_attempt = _create_retry_attempt(
        db_session,
        brief,
        source_attempt,
        targets,
        categories,
        status="completed",
        generated_targets=[targets[1]],  # targets[2] eksik!
    )

    with pytest.raises(SocialIdeaReadError) as exc_info:
        load_social_idea_retry_result(
            db_session,
            brief_id=brief.id,
            attempt_id=retry_attempt.id,
            brand_profile_id=workspace.id,
        )
    assert exc_info.value.error_code == "IDEA_READ_INCONSISTENT"


def test_retry_read_completed_generated_count_mismatch_fails(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """Completed attempt generated_total_accepted sayısı ile target_ids sayısı uyumsuzsa fail etmeli."""
    workspace, run, brief, categories, targets, kws, source_attempt = _setup_retry_read_env(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    retry_attempt = _create_retry_attempt(
        db_session,
        brief,
        source_attempt,
        targets,
        categories,
        status="completed",
        generated_targets=[targets[1], targets[2]],
    )
    cov = dict(retry_attempt.coverage)
    cov["generated"]["total_accepted"] = 1  # 2 yerine 1 yapıldı
    retry_attempt.coverage = cov
    db_session.commit()

    with pytest.raises(SocialIdeaReadError) as exc_info:
        load_social_idea_retry_result(
            db_session,
            brief_id=brief.id,
            attempt_id=retry_attempt.id,
            brand_profile_id=workspace.id,
        )
    assert exc_info.value.error_code == "IDEA_READ_INCONSISTENT"


def test_retry_read_completed_assignment_category_mismatch_fails(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """DB'deki fikrin category_id değeri retry snapshot ataması ile uyuşmuyorsa fail etmeli."""
    workspace, run, brief, categories, targets, kws, source_attempt = _setup_retry_read_env(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    retry_attempt = _create_retry_attempt(
        db_session,
        brief,
        source_attempt,
        targets,
        categories,
        status="completed",
        generated_targets=[targets[1], targets[2]],
    )
    # targets[1] ve targets[2] için fikir ekleyelim ancak targets[1]'e categories[1] verelim (atama categories[0] idi)
    for t, cat in [(targets[1], categories[1]), (targets[2], categories[0])]:
        idea = SocialIdea(
            category_id=cat.id,
            keyword_id=kws[0].id,
            brief_id=brief.id,
            brief_target_id=t.id,
            idea_title=f"Retry Fikir {t.platform}",
            idea_description="Açıklama.",
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

    with pytest.raises(SocialIdeaReadError) as exc_info:
        load_social_idea_retry_result(
            db_session,
            brief_id=brief.id,
            attempt_id=retry_attempt.id,
            brand_profile_id=workspace.id,
        )
    assert exc_info.value.error_code == "IDEA_READ_INCONSISTENT"


def test_retry_read_completed_with_warnings_or_reason_fails(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """Completed attempt warnings veya reason_code içeriyorsa fail-closed reddedilmeli."""
    workspace, run, brief, categories, targets, kws, source_attempt = _setup_retry_read_env(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    retry_attempt = _create_retry_attempt(
        db_session,
        brief,
        source_attempt,
        targets,
        categories,
        status="completed",
        generated_targets=[targets[1], targets[2]],
        warnings=[{"target_id": targets[1].id, "reason_code": "target_unfilled"}],
    )
    # Eksik hedefler için DB'ye fikir ekle
    for t in targets[1:]:
        idea = SocialIdea(
            category_id=categories[0].id,
            keyword_id=kws[0].id,
            brief_id=brief.id,
            brief_target_id=t.id,
            idea_title=f"Retry Fikir {t.platform}",
            idea_description="Açıklama.",
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

    with pytest.raises(SocialIdeaReadError) as exc_info:
        load_social_idea_retry_result(
            db_session,
            brief_id=brief.id,
            attempt_id=retry_attempt.id,
            brand_profile_id=workspace.id,
        )
    assert exc_info.value.error_code == "IDEA_READ_INCONSISTENT"


# ==================== 5. PARTIAL ATTEMPT SEMANTİĞİ ====================

def test_retry_read_partial_empty_generated_fails(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """Partial attempt generated_target_ids boş olamaz (hiç sonuç yoksa failed olmalıydı)."""
    workspace, run, brief, categories, targets, kws, source_attempt = _setup_retry_read_env(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    retry_attempt = _create_retry_attempt(
        db_session,
        brief,
        source_attempt,
        targets,
        categories,
        status="partial",
        generated_targets=[],  # Boş!
        warnings=[{"target_id": targets[1].id, "category_id": categories[0].id, "reason_code": "target_unfilled"}],
        reason_code="target_unfilled",
    )

    with pytest.raises(SocialIdeaReadError) as exc_info:
        load_social_idea_retry_result(
            db_session,
            brief_id=brief.id,
            attempt_id=retry_attempt.id,
            brand_profile_id=workspace.id,
        )
    assert exc_info.value.error_code == "IDEA_READ_INCONSISTENT"


def test_retry_read_partial_generated_count_mismatch_fails(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """Partial attempt generated_total_accepted sayısı uyumsuzsa fail etmeli."""
    workspace, run, brief, categories, targets, kws, source_attempt = _setup_retry_read_env(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    retry_attempt = _create_retry_attempt(
        db_session,
        brief,
        source_attempt,
        targets,
        categories,
        status="partial",
        generated_targets=[targets[1]],
        warnings=[{"target_id": targets[2].id, "category_id": categories[0].id, "reason_code": "target_unfilled"}],
        reason_code="target_unfilled",
    )
    cov = dict(retry_attempt.coverage)
    cov["generated"]["total_accepted"] = 5
    retry_attempt.coverage = cov
    db_session.commit()

    with pytest.raises(SocialIdeaReadError) as exc_info:
        load_social_idea_retry_result(
            db_session,
            brief_id=brief.id,
            attempt_id=retry_attempt.id,
            brand_profile_id=workspace.id,
        )
    assert exc_info.value.error_code == "IDEA_READ_INCONSISTENT"


def test_retry_read_partial_warning_count_or_duplicate_fails(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """Partial attempt warnings eksik, fazla veya mükerrer olduğunda fail-closed davranmalı."""
    workspace, run, brief, categories, targets, kws, source_attempt = _setup_retry_read_env(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    # generated: targets[1], unfilled: targets[2]
    # Mükerrer warning ekleyelim
    retry_attempt = _create_retry_attempt(
        db_session,
        brief,
        source_attempt,
        targets,
        categories,
        status="partial",
        generated_targets=[targets[1]],
        warnings=[
            {"target_id": targets[2].id, "category_id": categories[0].id, "reason_code": "target_unfilled"},
            {"target_id": targets[2].id, "category_id": categories[0].id, "reason_code": "target_unfilled"},
        ],
        reason_code="target_unfilled",
    )
    # targets[1] fikrini DB'ye ekle
    idea = SocialIdea(
        category_id=categories[0].id,
        keyword_id=kws[0].id,
        brief_id=brief.id,
        brief_target_id=targets[1].id,
        idea_title=f"Retry Fikir {targets[1].platform}",
        idea_description="Açıklama.",
        target_platform=targets[1].platform,
        content_format=targets[1].content_format,
        trend_alignment=0.85,
        is_stale=False,
        is_selected=False,
        regeneration_count=0,
        created_at=T0,
    )
    db_session.add(idea)
    db_session.commit()

    with pytest.raises(SocialIdeaReadError) as exc_info:
        load_social_idea_retry_result(
            db_session,
            brief_id=brief.id,
            attempt_id=retry_attempt.id,
            brand_profile_id=workspace.id,
        )
    assert exc_info.value.error_code == "IDEA_READ_INCONSISTENT"


def test_retry_read_partial_warning_reason_or_category_mismatch_fails(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """Partial warning reason_code 'target_unfilled' değilse veya category_id uyumsuzsa fail etmeli."""
    workspace, run, brief, categories, targets, kws, source_attempt = _setup_retry_read_env(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    retry_attempt = _create_retry_attempt(
        db_session,
        brief,
        source_attempt,
        targets,
        categories,
        status="partial",
        generated_targets=[targets[1]],
        warnings=[{"target_id": targets[2].id, "category_id": categories[1].id, "reason_code": "target_unfilled"}],
        reason_code="target_unfilled",
    )
    # targets[1] fikrini ekle
    idea = SocialIdea(
        category_id=categories[0].id,
        keyword_id=kws[0].id,
        brief_id=brief.id,
        brief_target_id=targets[1].id,
        idea_title=f"Retry Fikir {targets[1].platform}",
        idea_description="Açıklama.",
        target_platform=targets[1].platform,
        content_format=targets[1].content_format,
        trend_alignment=0.85,
        is_stale=False,
        is_selected=False,
        regeneration_count=0,
        created_at=T0,
    )
    db_session.add(idea)
    db_session.commit()

    with pytest.raises(SocialIdeaReadError) as exc_info:
        load_social_idea_retry_result(
            db_session,
            brief_id=brief.id,
            attempt_id=retry_attempt.id,
            brand_profile_id=workspace.id,
        )
    assert exc_info.value.error_code == "IDEA_READ_INCONSISTENT"


# ==================== 6. TARİHSEL POLİNG BAĞIMSIZLIĞI ====================

def test_retry_read_partial_historical_independence_when_target_filled_later(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """Partial attempt sonrası başka bir retry eksik hedefi doldurmuş olsa bile eski partial GET
    hâlâ başarıyla okunmalı; historical warning korunurken current coverage dolu görünmelidir."""
    workspace, run, brief, categories, targets, kws, source_attempt = _setup_retry_read_env(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    # İlk retry: targets[1] generated, targets[2] unfilled
    partial_attempt = _create_retry_attempt(
        db_session,
        brief,
        source_attempt,
        targets,
        categories,
        status="partial",
        generated_targets=[targets[1]],
        warnings=[{"target_id": targets[2].id, "category_id": categories[0].id, "reason_code": "target_unfilled"}],
        reason_code="target_unfilled",
    )
    idea_t1 = SocialIdea(
        category_id=categories[0].id,
        keyword_id=kws[0].id,
        brief_id=brief.id,
        brief_target_id=targets[1].id,
        idea_title=f"Retry 1 Fikir {targets[1].platform}",
        idea_description="İlk retry tarafından üretildi.",
        target_platform=targets[1].platform,
        content_format=targets[1].content_format,
        trend_alignment=0.85,
        is_stale=False,
        is_selected=False,
        regeneration_count=0,
        created_at=T0 + timedelta(seconds=10),
    )
    db_session.add(idea_t1)
    db_session.commit()

    # Şimdi daha sonra çalışan ikinci bir retry'nin targets[2]'yi de doldurduğunu simüle edelim:
    idea_t2 = SocialIdea(
        category_id=categories[0].id,
        keyword_id=kws[0].id,
        brief_id=brief.id,
        brief_target_id=targets[2].id,
        idea_title=f"Retry 2 Fikir {targets[2].platform}",
        idea_description="İkinci retry tarafından üretildi.",
        target_platform=targets[2].platform,
        content_format=targets[2].content_format,
        trend_alignment=0.90,
        is_stale=False,
        is_selected=False,
        regeneration_count=0,
        created_at=T0 + timedelta(seconds=20),
    )
    db_session.add(idea_t2)
    db_session.commit()

    # Eski partial attempt'i sorgulayalım
    res = load_social_idea_retry_result(
        db_session,
        brief_id=brief.id,
        attempt_id=partial_attempt.id,
        brand_profile_id=workspace.id,
    )

    # Historical verification:
    assert res.attempt_status == "partial"
    assert res.reason_code == "target_unfilled"
    assert len(res.warnings) == 1
    assert res.warnings[0].target_id == targets[2].id
    assert res.warnings[0].reason_code == "target_unfilled"
    assert res.warnings[0].category_id == categories[0].id

    # Current coverage verification (veritabanında artık tüm hedefler dolu):
    assert len(res.coverage) == 3
    for cov_item in res.coverage:
        assert cov_item.requested == 1
        assert cov_item.accepted == 1
        assert cov_item.missing == 0

    assert res.total_ideas == 3


# ==================== 7. SALT OKUNURLUK (READ-ONLY) ====================

def test_retry_read_strictly_read_only(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """Read servisinin commit, rollback, flush yapmadığını doğrular."""
    workspace, run, brief, categories, targets, kws, source_attempt = _setup_retry_read_env(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    retry_attempt = _create_retry_attempt(
        db_session,
        brief,
        source_attempt,
        targets,
        categories,
        status="completed",
        generated_targets=[targets[1], targets[2]],
    )
    for t in targets[1:]:
        idea = SocialIdea(
            category_id=categories[0].id,
            keyword_id=kws[0].id,
            brief_id=brief.id,
            brief_target_id=t.id,
            idea_title=f"Retry Fikir {t.platform}",
            idea_description="Açıklama.",
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

    # Spy
    orig_commit = db_session.commit
    orig_rollback = db_session.rollback
    orig_flush = db_session.flush

    mock_commit = MagicMock(side_effect=orig_commit)
    mock_rollback = MagicMock(side_effect=orig_rollback)
    mock_flush = MagicMock(side_effect=orig_flush)

    db_session.commit = mock_commit
    db_session.rollback = mock_rollback
    db_session.flush = mock_flush

    try:
        res = load_social_idea_retry_result(
            db_session,
            brief_id=brief.id,
            attempt_id=retry_attempt.id,
            brand_profile_id=workspace.id,
        )
        assert res.attempt_status == "completed"
        assert mock_commit.call_count == 0
        assert mock_rollback.call_count == 0
        assert mock_flush.call_count == 0
    finally:
        db_session.commit = orig_commit
        db_session.rollback = orig_rollback
        db_session.flush = orig_flush


# ==================== 8. COMPLETED BAŞARI SÖZLEŞMESİ ====================

def test_retry_read_completed_success_contract(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """Completed attempt için tam parite başarı sözleşmesini doğrular."""
    workspace, run, brief, categories, targets, kws, source_attempt = _setup_retry_read_env(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    retry_attempt = _create_retry_attempt(
        db_session,
        brief,
        source_attempt,
        targets,
        categories,
        status="completed",
        generated_targets=[targets[1], targets[2]],
    )
    for t in targets[1:]:
        idea = SocialIdea(
            category_id=categories[0].id,
            keyword_id=kws[0].id,
            brief_id=brief.id,
            brief_target_id=t.id,
            idea_title=f"Retry Fikir {t.platform}",
            idea_description="Açıklama.",
            target_platform=t.platform,
            content_format=t.content_format,
            trend_alignment=0.88,
            is_stale=False,
            is_selected=False,
            regeneration_count=0,
            created_at=T0,
        )
        db_session.add(idea)
    db_session.commit()

    res = load_social_idea_retry_result(
        db_session,
        brief_id=brief.id,
        attempt_id=retry_attempt.id,
        brand_profile_id=workspace.id,
    )

    assert res.brief_id == brief.id
    assert res.scoring_run_id == run.id
    assert res.attempt_id == retry_attempt.id
    assert res.attempt_status == "completed"
    assert res.reason_code is None
    assert res.warnings == ()
    assert res.total_ideas == 3
    assert len(res.ideas) == 3
    assert len(res.coverage) == 3
    for cov_item in res.coverage:
        assert cov_item.requested == 1
        assert cov_item.accepted == 1
        assert cov_item.missing == 0
