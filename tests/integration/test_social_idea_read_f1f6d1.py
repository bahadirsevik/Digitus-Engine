# -*- coding: utf-8 -*-
"""MİKRO FAZ F1-F.6d.1 — Salt-Okunur Fikir Sonuç ve Coverage Entegrasyon Testleri.

Tüm testler gerçek PostgreSQL test veritabanını ve SQLAlchemy oturumunu kullanır.
Test edilen alanlar:
1. Workspace-scoped completed result okunur.
2. Başka workspace erişemez.
3. Attempt başka brief'e aitse bulunamaz.
4. Stage ideas değilse reddedilir.
5. pending sonucu ideas=[] ve accepted=0.
6. pending durumda fikir satırı varsa reddedilir.
7. running sonucu doğru boş coverage döner.
8. completed toplam fikir sayısı doğrulanır.
9. completed tüm target'larda missing=0.
10. Eksik kategori-target kotası reddedilir.
11. Kota aşımı reddedilir.
12. Plan dışı kategori reddedilir.
13. Plan dışı target reddedilir.
14. Brief dışı keyword reddedilir.
15. Platform/format uyuşmazlığı reddedilir.
16. Başlık/açıklama sınırları doğrulanır.
17. NaN, Infinity ve bool trend_alignment reddedilir.
18. failed durumda fikir satırı reddedilir.
19. failed reason_code döner; error_message dönmez.
20. partial accepted/missing hesabı doğru.
21. Stale brief/category/idea tarihsel olarak okunabilir.
22. Güncel format matrisi değişse bile target uyumlu tarihsel fikir okunur.
23. Warning şeması doğrulanır.
24. Bozuk veya ekstra alanlı warning reddedilir.
25. Deterministik sıralama.
26. commit/rollback/flush çağrılmaz.
27. FOR UPDATE kullanılmaz.
28. DB satırları değişmez.
29. Response şemaları extra alanları reddeder.
30. Sonuç JSON serializable.
31. Gerçek AI/ağ çağrısı yok.
"""
from __future__ import annotations

import copy
import dataclasses
import json
import math
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.config import settings
from app.core.social.idea_contract import ValidatedSocialIdea
from app.core.social.idea_flow import begin_social_idea_generation
from app.core.social.idea_persistence import persist_social_ideas
from app.core.social.idea_read import (
    SocialIdeaCoverage,
    SocialIdeaReadError,
    SocialIdeaReadItem,
    SocialIdeaReadNotFoundError,
    SocialIdeaReadResult,
    SocialIdeaWarning,
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
from app.generators.social.attempt_state import claim_attempt
from app.generators.social.brief_idea_generator import SocialIdeaAIResult
from app.schemas.social_brief import (
    SocialBriefIdeasGenerateRequest,
    SocialGeneratedIdeaResponse,
    SocialIdeaCoverageResponse,
    SocialIdeaWarningResponse,
    SocialIdeasGenerateResponse,
)

T0 = datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def enable_flag(monkeypatch):
    """Test süresince feature flag'i aktif kılar."""
    monkeypatch.setattr(settings, "ENABLE_SOCIAL_BRIEF_FLOW", True)
    yield


def _setup_fresh_environment(
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
    num_kws: int = 3,
    workspace_name: str = "Ideas Read Brand",
):
    """Workspace, run ve SOCIAL havuz kayıtlarını hazırlar."""
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


def _setup_ready_ideas_attempt(
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
    task_id: str = "task-read-101",
    ideas_per_category: int = 3,
    num_categories: int = 2,
    targets_data: list[tuple[str, str]] | None = None,
    claim: bool = True,
):
    """Fikir preflight edilmiş ve claim edilmiş temel ortamı hazırlar."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=3
    )

    if targets_data is None:
        targets_data = [("instagram", "post"), ("twitter", "thread")]

    brief = SocialBrief(
        scoring_run_id=run.id,
        brand_name_snapshot="Test Marka",
        brand_context_snapshot="Test Context",
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

    # Categories attempt
    cat_attempt = SocialGenerationAttempt(
        brief_id=brief.id,
        stage="categories",
        idempotency_key=f"cat-attempt-key-{task_id}",
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

    cat_ids = [c.id for c in categories]
    req = SocialBriefIdeasGenerateRequest(
        idempotency_key=f"idempotency-{task_id}",
        category_ids=cat_ids,
        ideas_per_category=ideas_per_category,
    )

    start = begin_social_idea_generation(
        db_session,
        brief_id=brief.id,
        brand_profile_id=ws.id,
        request=req,
    )
    db_session.commit()

    if claim:
        claimed = claim_attempt(
            db_session,
            attempt_id=start.attempt_id,
            task_id=task_id,
        )
        db_session.commit()
    else:
        claimed = db_session.query(SocialGenerationAttempt).filter_by(id=start.attempt_id).one()

    return ws, run, brief, categories, targets, kws, start, claimed


def _build_valid_category_results(
    plan,
    attempt_id: int,
    brief_targets: list[SocialBriefTarget],
    brief_keywords: list,
) -> tuple[SocialIdeaAIResult, ...]:
    """Plan kotalarına tam uyan doğrulanmış sahte AI sonuçları üretir."""
    target_map = {t.id: t for t in brief_targets}
    kw_id = brief_keywords[0].id

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
                        primary_keyword_id=kw_id,
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


# ==================== TESTLER ====================

def test_01_workspace_scoped_completed_result(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """1. Workspace-scoped completed result doğru okunur."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-01"
    )
    ai_results = _build_valid_category_results(start.plan, attempt.id, targets, kws)
    persist_social_ideas(
        db_session,
        attempt_id=attempt.id,
        task_id="task-01",
        category_results=ai_results,
    )
    db_session.commit()

    res = load_social_idea_result(
        db_session,
        brief_id=brief.id,
        attempt_id=attempt.id,
        brand_profile_id=ws.id,
    )

    assert res.brief_id == brief.id
    assert res.scoring_run_id == run.id
    assert res.attempt_id == attempt.id
    assert res.attempt_status == "completed"
    assert res.total_ideas == 6
    assert len(res.ideas) == 6
    assert len(res.coverage) == 2
    for cov in res.coverage:
        assert cov.requested == 3
        assert cov.accepted == 3
        assert cov.missing == 0


def test_02_other_workspace_cannot_access(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """2. Başka workspace veya silinmiş workspace erişemez."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-02"
    )
    ws2 = make_workspace(name="Other Workspace 2", status="confirmed")
    db_session.commit()

    # Başka workspace
    with pytest.raises(SocialIdeaReadNotFoundError):
        load_social_idea_result(
            db_session,
            brief_id=brief.id,
            attempt_id=attempt.id,
            brand_profile_id=ws2.id,
        )

    # Var olmayan workspace
    with pytest.raises(SocialIdeaReadNotFoundError):
        load_social_idea_result(
            db_session,
            brief_id=brief.id,
            attempt_id=attempt.id,
            brand_profile_id=999999,
        )

    # Silinmiş workspace (deleted_at dolu)
    ws.deleted_at = datetime.now(timezone.utc)
    db_session.commit()
    with pytest.raises(SocialIdeaReadNotFoundError):
        load_social_idea_result(
            db_session,
            brief_id=brief.id,
            attempt_id=attempt.id,
            brand_profile_id=ws.id,
        )


def test_03_attempt_from_another_brief_not_found(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """3. Attempt başka brief'e aitse not found fırlatır."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-03a"
    )
    _, _, brief2, _, _, _, _, attempt2 = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-03b"
    )

    # brief1 ile attempt2 sorgulanırsa bulunamamalı
    with pytest.raises(SocialIdeaReadNotFoundError):
        load_social_idea_result(
            db_session,
            brief_id=brief.id,
            attempt_id=attempt2.id,
            brand_profile_id=ws.id,
        )


def test_04_stage_not_ideas_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """4. Stage ideas değilse reddedilir."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-04"
    )
    attempt.stage = "categories"
    db_session.commit()

    with pytest.raises(SocialIdeaReadError) as exc:
        load_social_idea_result(
            db_session,
            brief_id=brief.id,
            attempt_id=attempt.id,
            brand_profile_id=ws.id,
        )
    assert exc.value.error_code == "IDEA_READ_INCONSISTENT"


def test_05_pending_returns_empty_ideas_and_zero_accepted(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """5. pending sonucu ideas=[] ve accepted=0 döner."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-05", claim=False
    )
    # attempt status pending durumda
    assert attempt.status == "pending"

    res = load_social_idea_result(
        db_session,
        brief_id=brief.id,
        attempt_id=attempt.id,
        brand_profile_id=ws.id,
    )

    assert res.attempt_status == "pending"
    assert res.total_ideas == 0
    assert res.ideas == ()
    for cov in res.coverage:
        assert cov.requested == 3
        assert cov.accepted == 0
        assert cov.missing == 3


def test_06_pending_with_ideas_in_db_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """6. pending durumda fikir satırı varsa reddedilir."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-06", claim=False
    )
    # Beklenmedik fikir satırı ekle
    idea = SocialIdea(
        brief_id=brief.id,
        category_id=cats[0].id,
        brief_target_id=targets[0].id,
        keyword_id=kws[0].id,
        idea_title="Erken Fikir",
        idea_description="Açıklama",
        target_platform=targets[0].platform,
        content_format=targets[0].content_format,
        trend_alignment=0.8,
    )
    db_session.add(idea)
    db_session.commit()

    with pytest.raises(SocialIdeaReadError) as exc:
        load_social_idea_result(
            db_session,
            brief_id=brief.id,
            attempt_id=attempt.id,
            brand_profile_id=ws.id,
        )
    assert exc.value.error_code == "IDEA_READ_INCONSISTENT"


def test_07_running_returns_correct_empty_coverage(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """7. running sonucu doğru boş coverage döner."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-07", claim=True
    )
    # claim_attempt sonrası status == running
    assert attempt.status == "running"

    res = load_social_idea_result(
        db_session,
        brief_id=brief.id,
        attempt_id=attempt.id,
        brand_profile_id=ws.id,
    )

    assert res.attempt_status == "running"
    assert res.total_ideas == 0
    assert res.ideas == ()
    for cov in res.coverage:
        assert cov.requested == 3
        assert cov.accepted == 0
        assert cov.missing == 3


def test_08_completed_requires_k4_coverage_not_exact_quota(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """8. completed = K4 (her hedef >= 1, her kategori >= 1); kota altı hücre tutarsızlık
    değildir (missing > 0 raporlanır). Bir hedefin hiç fikri kalmazsa okuma tutarsızdır."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-08"
    )
    ai_results = _build_valid_category_results(start.plan, attempt.id, targets, kws)
    persist_social_ideas(
        db_session,
        attempt_id=attempt.id,
        task_id="task-08",
        category_results=ai_results,
    )
    db_session.commit()

    # Kota altı: bir fikir eksik -> okunur, missing=1
    rows = (
        db_session.query(SocialIdea)
        .filter_by(brief_id=brief.id, brief_target_id=targets[0].id)
        .order_by(SocialIdea.id.asc())
        .all()
    )
    db_session.delete(rows[0])
    db_session.commit()

    result = load_social_idea_result(
        db_session,
        brief_id=brief.id,
        attempt_id=attempt.id,
        brand_profile_id=ws.id,
    )
    cov = {c.target_id: c for c in result.coverage}
    assert cov[targets[0].id].missing == 1
    assert cov[targets[0].id].accepted == cov[targets[0].id].requested - 1

    # Hedefin hiç fikri kalmazsa completed tutarsız olur
    for row in rows[1:]:
        db_session.delete(row)
    db_session.commit()

    with pytest.raises(SocialIdeaReadError) as exc:
        load_social_idea_result(
            db_session,
            brief_id=brief.id,
            attempt_id=attempt.id,
            brand_profile_id=ws.id,
        )
    assert exc.value.error_code == "IDEA_READ_INCONSISTENT"


def test_09_completed_all_targets_missing_zero(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """9. completed tüm target'larda missing=0 ve accepted=requested olmalıdır."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-09"
    )
    ai_results = _build_valid_category_results(start.plan, attempt.id, targets, kws)
    persist_social_ideas(
        db_session,
        attempt_id=attempt.id,
        task_id="task-09",
        category_results=ai_results,
    )
    db_session.commit()

    res = load_social_idea_result(
        db_session,
        brief_id=brief.id,
        attempt_id=attempt.id,
        brand_profile_id=ws.id,
    )
    for cov in res.coverage:
        assert cov.missing == 0
        assert cov.accepted == cov.requested


def test_10_missing_category_target_quota_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """10. Toplam sayı tutsa bile kategori-target kotası eksik/fazla dağılımı reddedilir."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-10"
    )
    ai_results = _build_valid_category_results(start.plan, attempt.id, targets, kws)
    persist_social_ideas(
        db_session,
        attempt_id=attempt.id,
        task_id="task-10",
        category_results=ai_results,
    )
    db_session.commit()

    # Bir fikrin brief_target_id'sini diğerine kaydır (toplam 6 kalır ama kotalar bozulur)
    ideas = db_session.query(SocialIdea).filter_by(brief_id=brief.id).all()
    # cats[0] için targets[0]'dan alıp targets[1]'e ver
    for i in ideas:
        if i.category_id == cats[0].id and i.brief_target_id == targets[0].id:
            i.brief_target_id = targets[1].id
            i.target_platform = targets[1].platform
            i.content_format = targets[1].content_format
            break
    db_session.commit()

    with pytest.raises(SocialIdeaReadError) as exc:
        load_social_idea_result(
            db_session,
            brief_id=brief.id,
            attempt_id=attempt.id,
            brand_profile_id=ws.id,
        )
    assert exc.value.error_code == "IDEA_READ_INCONSISTENT"


def test_11_quota_overflow_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """11. Kota aşımı durumunda reddedilir."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-11"
    )
    ai_results = _build_valid_category_results(start.plan, attempt.id, targets, kws)
    persist_social_ideas(
        db_session,
        attempt_id=attempt.id,
        task_id="task-11",
        category_results=ai_results,
    )
    db_session.commit()

    # Fazladan 7. fikir ekle
    extra_idea = SocialIdea(
        brief_id=brief.id,
        category_id=cats[0].id,
        brief_target_id=targets[0].id,
        keyword_id=kws[0].id,
        idea_title="Fazla Fikir",
        idea_description="Fazla Açıklama",
        target_platform=targets[0].platform,
        content_format=targets[0].content_format,
        trend_alignment=0.8,
    )
    db_session.add(extra_idea)
    db_session.commit()

    with pytest.raises(SocialIdeaReadError) as exc:
        load_social_idea_result(
            db_session,
            brief_id=brief.id,
            attempt_id=attempt.id,
            brand_profile_id=ws.id,
        )
    assert exc.value.error_code == "IDEA_READ_INCONSISTENT"


def test_12_category_outside_plan_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """12. Plan dışı kategori reddedilir."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-12"
    )
    ai_results = _build_valid_category_results(start.plan, attempt.id, targets, kws)
    persist_social_ideas(
        db_session,
        attempt_id=attempt.id,
        task_id="task-12",
        category_results=ai_results,
    )
    db_session.commit()

    # Plan dışı yeni kategori oluştur
    cat_outside = SocialCategory(
        scoring_run_id=run.id,
        brief_id=brief.id,
        category_name="Plan Dışı",
        category_type="entertainment",
        description="Plan dışı açıklama",
        is_stale=False,
        relevance_score=0.9,
    )
    db_session.add(cat_outside)
    db_session.flush()

    first_idea = db_session.query(SocialIdea).filter_by(brief_id=brief.id).first()
    first_idea.category_id = cat_outside.id
    db_session.commit()

    with pytest.raises(SocialIdeaReadError) as exc:
        load_social_idea_result(
            db_session,
            brief_id=brief.id,
            attempt_id=attempt.id,
            brand_profile_id=ws.id,
        )
    assert exc.value.error_code == "IDEA_READ_INCONSISTENT"


def test_13_target_outside_plan_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """13. Plan dışı target reddedilir."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-13"
    )
    ai_results = _build_valid_category_results(start.plan, attempt.id, targets, kws)
    persist_social_ideas(
        db_session,
        attempt_id=attempt.id,
        task_id="task-13",
        category_results=ai_results,
    )
    db_session.commit()

    # Plan dışı yeni hedef oluştur (post formatı video kısıtına takılmaz)
    target_outside = SocialBriefTarget(
        brief_id=brief.id,
        platform="facebook",
        content_format="post",
    )
    db_session.add(target_outside)
    db_session.flush()

    first_idea = db_session.query(SocialIdea).filter_by(brief_id=brief.id).first()
    first_idea.brief_target_id = target_outside.id
    first_idea.target_platform = "facebook"
    first_idea.content_format = "post"
    db_session.commit()

    with pytest.raises(SocialIdeaReadError) as exc:
        load_social_idea_result(
            db_session,
            brief_id=brief.id,
            attempt_id=attempt.id,
            brand_profile_id=ws.id,
        )
    assert exc.value.error_code == "IDEA_READ_INCONSISTENT"


def test_14_keyword_outside_brief_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """14. Brief dışı keyword reddedilir."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-14"
    )
    ai_results = _build_valid_category_results(start.plan, attempt.id, targets, kws)
    persist_social_ideas(
        db_session,
        attempt_id=attempt.id,
        task_id="task-14",
        category_results=ai_results,
    )
    db_session.commit()

    # Brief dışı keyword oluştur
    kw_outside = make_keyword(text_value="outside kw", brand_profile_id=ws.id)
    first_idea = db_session.query(SocialIdea).filter_by(brief_id=brief.id).first()
    first_idea.keyword_id = kw_outside.id
    db_session.commit()

    with pytest.raises(SocialIdeaReadError) as exc:
        load_social_idea_result(
            db_session,
            brief_id=brief.id,
            attempt_id=attempt.id,
            brand_profile_id=ws.id,
        )
    assert exc.value.error_code == "IDEA_READ_INCONSISTENT"


def test_15_platform_format_mismatch_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """15. Platform/format uyuşmazlığı reddedilir."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-15"
    )
    ai_results = _build_valid_category_results(start.plan, attempt.id, targets, kws)
    persist_social_ideas(
        db_session,
        attempt_id=attempt.id,
        task_id="task-15",
        category_results=ai_results,
    )
    db_session.commit()

    first_idea = db_session.query(SocialIdea).filter_by(brief_id=brief.id).first()
    # Hedef platformla uyuşmayan platform yaz
    first_idea.target_platform = "linkedin"
    db_session.commit()

    with pytest.raises(SocialIdeaReadError) as exc:
        load_social_idea_result(
            db_session,
            brief_id=brief.id,
            attempt_id=attempt.id,
            brand_profile_id=ws.id,
        )
    assert exc.value.error_code == "IDEA_READ_INCONSISTENT"


def test_16_title_description_boundaries_verified(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """16. Başlık/açıklama sınırları ve whitespace kuralları doğrulanır."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-16"
    )
    ai_results = _build_valid_category_results(start.plan, attempt.id, targets, kws)
    persist_social_ideas(
        db_session,
        attempt_id=attempt.id,
        task_id="task-16",
        category_results=ai_results,
    )
    db_session.commit()

    first_idea = db_session.query(SocialIdea).filter_by(brief_id=brief.id).first()

    # Trimlenmemiş başlık
    first_idea.idea_title = " Başta Boşluk Var"
    db_session.commit()
    with pytest.raises(SocialIdeaReadError) as exc:
        load_social_idea_result(db_session, brief_id=brief.id, attempt_id=attempt.id, brand_profile_id=ws.id)
    assert exc.value.error_code == "IDEA_READ_INCONSISTENT"

    # Boş başlık
    first_idea.idea_title = ""
    db_session.commit()
    with pytest.raises(SocialIdeaReadError) as exc:
        load_social_idea_result(db_session, brief_id=brief.id, attempt_id=attempt.id, brand_profile_id=ws.id)
    assert exc.value.error_code == "IDEA_READ_INCONSISTENT"

    # Yalnızca boşluktan oluşan başlık
    first_idea.idea_title = "   "
    db_session.commit()
    with pytest.raises(SocialIdeaReadError) as exc:
        load_social_idea_result(db_session, brief_id=brief.id, attempt_id=attempt.id, brand_profile_id=ws.id)
    assert exc.value.error_code == "IDEA_READ_INCONSISTENT"

    # >200 karakter başlık (in-memory nesne doğrulaması)
    with db_session.no_autoflush:
        first_idea.idea_title = "A" * 201
        with pytest.raises(SocialIdeaReadError) as exc:
            load_social_idea_result(db_session, brief_id=brief.id, attempt_id=attempt.id, brand_profile_id=ws.id)
        assert exc.value.error_code == "IDEA_READ_INCONSISTENT"

    # Düzelt
    first_idea.idea_title = "Geçerli Başlık"
    db_session.commit()

    # Trimlenmemiş açıklama
    first_idea.idea_description = "Açıklama Sonda Boşluk "
    db_session.commit()
    with pytest.raises(SocialIdeaReadError) as exc:
        load_social_idea_result(db_session, brief_id=brief.id, attempt_id=attempt.id, brand_profile_id=ws.id)
    assert exc.value.error_code == "IDEA_READ_INCONSISTENT"

    # >2000 karakter açıklama
    first_idea.idea_description = "B" * 2001
    db_session.commit()
    with pytest.raises(SocialIdeaReadError) as exc:
        load_social_idea_result(db_session, brief_id=brief.id, attempt_id=attempt.id, brand_profile_id=ws.id)
    assert exc.value.error_code == "IDEA_READ_INCONSISTENT"


def test_17_nan_infinity_and_bool_trend_alignment_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """17. NaN, Infinity ve bool trend_alignment reddedilir."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-17"
    )
    ai_results = _build_valid_category_results(start.plan, attempt.id, targets, kws)
    persist_social_ideas(
        db_session,
        attempt_id=attempt.id,
        task_id="task-17",
        category_results=ai_results,
    )
    db_session.commit()

    first_idea = db_session.query(SocialIdea).filter_by(brief_id=brief.id).first()

    # NaN
    first_idea.trend_alignment = float("nan")
    db_session.commit()
    with pytest.raises(SocialIdeaReadError) as exc:
        load_social_idea_result(db_session, brief_id=brief.id, attempt_id=attempt.id, brand_profile_id=ws.id)
    assert exc.value.error_code == "IDEA_READ_INCONSISTENT"

    # Infinity
    first_idea.trend_alignment = float("inf")
    db_session.commit()
    with pytest.raises(SocialIdeaReadError) as exc:
        load_social_idea_result(db_session, brief_id=brief.id, attempt_id=attempt.id, brand_profile_id=ws.id)
    assert exc.value.error_code == "IDEA_READ_INCONSISTENT"

    # 0.0 - 1.0 dışı
    first_idea.trend_alignment = 1.05
    db_session.commit()
    with pytest.raises(SocialIdeaReadError) as exc:
        load_social_idea_result(db_session, brief_id=brief.id, attempt_id=attempt.id, brand_profile_id=ws.id)
    assert exc.value.error_code == "IDEA_READ_INCONSISTENT"


def test_18_failed_attempt_with_ideas_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """18. failed durumda fikir satırı bulunursa fail-closed reddedilir."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-18"
    )
    attempt.status = "failed"
    attempt.reason_code = "TASK_TIMEOUT"
    # Yanlışlıkla fikir eklenmiş olsun
    idea = SocialIdea(
        brief_id=brief.id,
        category_id=cats[0].id,
        brief_target_id=targets[0].id,
        keyword_id=kws[0].id,
        idea_title="Failed Fikir",
        idea_description="Failed Açıklama",
        target_platform=targets[0].platform,
        content_format=targets[0].content_format,
        trend_alignment=0.8,
    )
    db_session.add(idea)
    db_session.commit()

    with pytest.raises(SocialIdeaReadError) as exc:
        load_social_idea_result(
            db_session,
            brief_id=brief.id,
            attempt_id=attempt.id,
            brand_profile_id=ws.id,
        )
    assert exc.value.error_code == "IDEA_READ_INCONSISTENT"


def test_19_failed_returns_reason_code_and_no_error_message(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """19. failed reason_code döner; error_message kesinlikle dönmez."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-19", claim=False
    )
    attempt.status = "failed"
    attempt.reason_code = "TASK_FAILED"
    attempt.error_message = "CRITICAL: internal error stack trace and tokens"
    db_session.commit()

    res = load_social_idea_result(
        db_session,
        brief_id=brief.id,
        attempt_id=attempt.id,
        brand_profile_id=ws.id,
    )

    assert res.attempt_status == "failed"
    assert res.reason_code == "TASK_FAILED"
    assert not hasattr(res, "error_message")
    assert res.total_ideas == 0
    assert res.ideas == ()


def test_20_partial_accepted_and_missing_calculation(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """20. partial accepted/missing hesabı DB'deki gerçek satırlardan doğru yapılır."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-20"
    )
    attempt.status = "partial"
    attempt.reason_code = "PARTIAL_SUCCESS"

    ai_results = _build_valid_category_results(start.plan, attempt.id, targets, kws)
    # Her kategoriden 1 adet geçerli fikir ekleyelim (toplam 2 fikir < 6 fikir, partial durum)
    added_count = 0
    target_counts: dict[int, int] = {}
    for r in ai_results:
        for vi in r.ideas[:1]:
            idea = SocialIdea(
                brief_id=brief.id,
                category_id=r.category_id,
                brief_target_id=vi.target_id,
                keyword_id=vi.primary_keyword_id,
                idea_title=vi.idea_title,
                idea_description=vi.idea_description,
                target_platform=vi.target_platform,
                content_format=vi.content_format,
                trend_alignment=vi.trend_alignment,
            )
            db_session.add(idea)
            target_counts[vi.target_id] = target_counts.get(vi.target_id, 0) + 1
            added_count += 1
    db_session.commit()

    res = load_social_idea_result(
        db_session,
        brief_id=brief.id,
        attempt_id=attempt.id,
        brand_profile_id=ws.id,
    )

    assert res.attempt_status == "partial"
    assert res.total_ideas == added_count
    assert len(res.ideas) == added_count
    for cov in res.coverage:
        expected_accepted = target_counts.get(cov.target_id, 0)
        assert cov.accepted == expected_accepted
        assert cov.missing == cov.requested - expected_accepted
        assert cov.missing >= 0


def test_21_stale_records_historically_readable(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """21. Brief, category veya idea sonradan stale olsa bile tarihsel olarak okunabilir."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-21"
    )
    ai_results = _build_valid_category_results(start.plan, attempt.id, targets, kws)
    persist_social_ideas(
        db_session,
        attempt_id=attempt.id,
        task_id="task-21",
        category_results=ai_results,
    )
    db_session.commit()

    # Stale bayraklarını işaretle
    brief.is_stale = True
    for c in cats:
        c.is_stale = True
    for i in db_session.query(SocialIdea).filter_by(brief_id=brief.id).all():
        i.is_stale = True
    db_session.commit()

    res = load_social_idea_result(
        db_session,
        brief_id=brief.id,
        attempt_id=attempt.id,
        brand_profile_id=ws.id,
    )

    assert res.total_ideas == 6
    for item in res.ideas:
        assert item.is_stale is True


def test_22_historical_ideas_read_even_if_format_matrix_changes(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """22. Format matrisi güncellense bile target ile uyumlu tarihsel fikirler okunabilir."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-22"
    )
    ai_results = _build_valid_category_results(start.plan, attempt.id, targets, kws)
    persist_social_ideas(
        db_session,
        attempt_id=attempt.id,
        task_id="task-22",
        category_results=ai_results,
    )
    db_session.commit()

    # format_matrix_version değiştir
    brief.format_matrix_version = "v99"
    db_session.commit()

    res = load_social_idea_result(
        db_session,
        brief_id=brief.id,
        attempt_id=attempt.id,
        brand_profile_id=ws.id,
    )
    assert res.total_ideas == 6


def test_23_warning_schema_verified(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """23. Warning şeması doğrulanır ve DTO'ya taşınır."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-23"
    )
    ai_results = _build_valid_category_results(start.plan, attempt.id, targets, kws)
    persist_social_ideas(
        db_session,
        attempt_id=attempt.id,
        task_id="task-23",
        category_results=ai_results,
    )
    attempt.warnings = [
        {"target_id": targets[0].id, "category_id": cats[0].id, "reason_code": "LOW_TREND"},
        {"target_id": targets[1].id, "reason_code": "FORMAT_UNCERTAIN"},
    ]
    db_session.commit()

    res = load_social_idea_result(
        db_session,
        brief_id=brief.id,
        attempt_id=attempt.id,
        brand_profile_id=ws.id,
    )

    assert len(res.warnings) == 2
    assert res.warnings[0].target_id == targets[0].id
    assert res.warnings[0].category_id == cats[0].id
    assert res.warnings[0].reason_code == "LOW_TREND"
    assert res.warnings[1].target_id == targets[1].id
    assert res.warnings[1].category_id is None
    assert res.warnings[1].reason_code == "FORMAT_UNCERTAIN"


def test_24_corrupt_or_extra_field_warning_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """24. Bozuk veya ekstra alanlı warning fail-closed reddedilir."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-24"
    )
    ai_results = _build_valid_category_results(start.plan, attempt.id, targets, kws)
    persist_social_ideas(
        db_session,
        attempt_id=attempt.id,
        task_id="task-24",
        category_results=ai_results,
    )

    # Ekstra alan
    attempt.warnings = [
        {"target_id": targets[0].id, "reason_code": "WARN", "extra_bad_field": "dangerous"}
    ]
    db_session.commit()
    with pytest.raises(SocialIdeaReadError) as exc:
        load_social_idea_result(db_session, brief_id=brief.id, attempt_id=attempt.id, brand_profile_id=ws.id)
    assert exc.value.error_code == "IDEA_READ_INCONSISTENT"

    # Bozuk target_id
    attempt.warnings = [{"target_id": 99999, "reason_code": "WARN"}]
    db_session.commit()
    with pytest.raises(SocialIdeaReadError) as exc:
        load_social_idea_result(db_session, brief_id=brief.id, attempt_id=attempt.id, brand_profile_id=ws.id)
    assert exc.value.error_code == "IDEA_READ_INCONSISTENT"


def test_25_deterministic_sorting(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """25. Deterministik sıralama (ideas, coverage, warnings)."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-25"
    )
    ai_results = _build_valid_category_results(start.plan, attempt.id, targets, kws)
    persist_social_ideas(
        db_session,
        attempt_id=attempt.id,
        task_id="task-25",
        category_results=ai_results,
    )
    attempt.warnings = [
        {"target_id": targets[1].id, "reason_code": "WARN_B"},
        {"target_id": targets[0].id, "category_id": cats[1].id, "reason_code": "WARN_A"},
        {"target_id": targets[0].id, "category_id": cats[0].id, "reason_code": "WARN_A"},
    ]
    db_session.commit()

    res1 = load_social_idea_result(db_session, brief_id=brief.id, attempt_id=attempt.id, brand_profile_id=ws.id)
    res2 = load_social_idea_result(db_session, brief_id=brief.id, attempt_id=attempt.id, brand_profile_id=ws.id)

    assert [i.id for i in res1.ideas] == [i.id for i in res2.ideas]
    assert [c.target_id for c in res1.coverage] == [c.target_id for c in res2.coverage]
    assert [(w.target_id, w.category_id, w.reason_code) for w in res1.warnings] == [
        (w.target_id, w.category_id, w.reason_code) for w in res2.warnings
    ]
    # Coverage sırası: attempt.requested_target_ids sırası
    assert [c.target_id for c in res1.coverage] == list(attempt.requested_target_ids)


def test_26_read_service_does_not_commit_or_rollback_or_flush(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """26. commit/rollback/flush asla çağrılmaz."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-26"
    )
    ai_results = _build_valid_category_results(start.plan, attempt.id, targets, kws)
    persist_social_ideas(
        db_session,
        attempt_id=attempt.id,
        task_id="task-26",
        category_results=ai_results,
    )
    db_session.commit()

    with patch.object(db_session, "commit", wraps=db_session.commit) as mock_commit, \
         patch.object(db_session, "rollback", wraps=db_session.rollback) as mock_rollback, \
         patch.object(db_session, "flush", wraps=db_session.flush) as mock_flush:
        load_social_idea_result(
            db_session,
            brief_id=brief.id,
            attempt_id=attempt.id,
            brand_profile_id=ws.id,
        )
        assert mock_commit.call_count == 0
        assert mock_rollback.call_count == 0
        assert mock_flush.call_count == 0


def test_27_for_update_is_never_used(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """27. FOR UPDATE asla kullanılmaz."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-27"
    )
    ai_results = _build_valid_category_results(start.plan, attempt.id, targets, kws)
    persist_social_ideas(
        db_session,
        attempt_id=attempt.id,
        task_id="task-27",
        category_results=ai_results,
    )
    db_session.commit()

    executed_sqls = []

    def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        executed_sqls.append(statement.upper())

    conn = db_session.connection()
    event.listen(conn, "before_cursor_execute", before_cursor_execute)
    try:
        load_social_idea_result(
            db_session,
            brief_id=brief.id,
            attempt_id=attempt.id,
            brand_profile_id=ws.id,
        )
    finally:
        event.remove(conn, "before_cursor_execute", before_cursor_execute)

    for sql in executed_sqls:
        assert "FOR UPDATE" not in sql


def test_28_database_rows_remain_unmodified(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """28. DB satırları ve alanları çağrı sonrasında tamamen aynı kalır."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-28"
    )
    ai_results = _build_valid_category_results(start.plan, attempt.id, targets, kws)
    persist_social_ideas(
        db_session,
        attempt_id=attempt.id,
        task_id="task-28",
        category_results=ai_results,
    )
    db_session.commit()

    ideas_before = [
        (i.id, i.idea_title, i.trend_alignment, i.is_stale)
        for i in db_session.query(SocialIdea).filter_by(brief_id=brief.id).order_by(SocialIdea.id).all()
    ]
    attempt_before = (attempt.status, attempt.reason_code, attempt.completed_at)

    load_social_idea_result(
        db_session,
        brief_id=brief.id,
        attempt_id=attempt.id,
        brand_profile_id=ws.id,
    )

    ideas_after = [
        (i.id, i.idea_title, i.trend_alignment, i.is_stale)
        for i in db_session.query(SocialIdea).filter_by(brief_id=brief.id).order_by(SocialIdea.id).all()
    ]
    attempt_after = (attempt.status, attempt.reason_code, attempt.completed_at)

    assert ideas_before == ideas_after
    assert attempt_before == attempt_after


def test_29_response_schemas_reject_extra_fields():
    """29. Response şemaları ekstra alanları ConfigDict(extra='forbid') ile reddeder."""
    # 1. SocialIdeaWarningResponse
    with pytest.raises(ValidationError):
        SocialIdeaWarningResponse(
            target_id=1,
            reason_code="WARN",
            forbidden_extra="bad",
        )

    # 2. SocialIdeaCoverageResponse
    with pytest.raises(ValidationError):
        SocialIdeaCoverageResponse(
            target_id=1,
            requested=3,
            accepted=3,
            missing=0,
            forbidden_extra="bad",
        )

    # 3. SocialGeneratedIdeaResponse
    with pytest.raises(ValidationError):
        SocialGeneratedIdeaResponse(
            id=1,
            category_id=1,
            keyword_id=1,
            brief_id=1,
            brief_target_id=1,
            idea_title="Başlık",
            idea_description="Açıklama",
            target_platform="instagram",
            content_format="post",
            trend_alignment=0.8,
            is_stale=False,
            forbidden_extra="bad",
        )

    # 4. SocialIdeasGenerateResponse
    with pytest.raises(ValidationError):
        SocialIdeasGenerateResponse(
            brief_id=1,
            scoring_run_id=1,
            attempt_id=1,
            attempt_status="completed",
            total_ideas=0,
            replayed=False,
            forbidden_extra="bad",
        )


def test_30_result_is_json_serializable(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """30. Sonuç ve Pydantic response modelleri JSON serializable olmalıdır."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-30"
    )
    ai_results = _build_valid_category_results(start.plan, attempt.id, targets, kws)
    persist_social_ideas(
        db_session,
        attempt_id=attempt.id,
        task_id="task-30",
        category_results=ai_results,
    )
    db_session.commit()

    res = load_social_idea_result(
        db_session,
        brief_id=brief.id,
        attempt_id=attempt.id,
        brand_profile_id=ws.id,
    )

    # Domain DTO dataclass serialization
    res_dict = dataclasses.asdict(res)
    json_str = json.dumps(res_dict)
    assert json_str is not None

    # Pydantic Response Schema serialization
    ideas_resp = [
        SocialGeneratedIdeaResponse(
            id=i.id,
            category_id=i.category_id,
            keyword_id=i.keyword_id,
            brief_id=i.brief_id,
            brief_target_id=i.brief_target_id,
            idea_title=i.idea_title,
            idea_description=i.idea_description,
            target_platform=i.target_platform,
            content_format=i.content_format,
            trend_alignment=i.trend_alignment,
            is_stale=i.is_stale,
        )
        for i in res.ideas
    ]
    cov_resp = [
        SocialIdeaCoverageResponse(
            target_id=c.target_id,
            requested=c.requested,
            accepted=c.accepted,
            missing=c.missing,
        )
        for c in res.coverage
    ]
    warn_resp = [
        SocialIdeaWarningResponse(
            target_id=w.target_id,
            category_id=w.category_id,
            reason_code=w.reason_code,
        )
        for w in res.warnings
    ]
    pydantic_resp = SocialIdeasGenerateResponse(
        brief_id=res.brief_id,
        scoring_run_id=res.scoring_run_id,
        attempt_id=res.attempt_id,
        attempt_status=res.attempt_status,
        total_ideas=res.total_ideas,
        ideas=ideas_resp,
        coverage=cov_resp,
        warnings=warn_resp,
        reason_code=res.reason_code,
        replayed=res.replayed,
    )
    pydantic_json = json.dumps(pydantic_resp.model_dump())
    assert pydantic_json is not None
    loaded = json.loads(pydantic_json)
    assert loaded["total_ideas"] == 6


def test_31_zero_real_ai_or_network_calls(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """31. load_social_idea_result çağrısı sırasında sıfır AI ve ağ çağrısı yapılır."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-31"
    )
    ai_results = _build_valid_category_results(start.plan, attempt.id, targets, kws)
    persist_social_ideas(
        db_session,
        attempt_id=attempt.id,
        task_id="task-31",
        category_results=ai_results,
    )
    db_session.commit()

    with patch("http.client.HTTPConnection.connect", side_effect=RuntimeError("Dış HTTP çağrısı yasaktır!")):
        res = load_social_idea_result(
            db_session,
            brief_id=brief.id,
            attempt_id=attempt.id,
            brand_profile_id=ws.id,
        )
        assert res.total_ideas == 6
