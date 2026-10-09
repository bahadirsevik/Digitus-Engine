# -*- coding: utf-8 -*-
"""Integration tests for Social Brief Idea Retry DB Preflight and Attempt Snapshot (F1-F.7.2).

Tüm testler gerçek PostgreSQL test veritabanını ve izole oturumu kullanır.
Test edilen senaryolar:
1. Hiç fikir yokken bütün canonical target’lar missing olur.
2. Bir target doluysa yalnız diğerleri retry planına girer.
3. Aynı target’ta birden fazla fikir olması tek persisted target üretir.
4. Bütün target’lar doluysa IDEA_RETRY_NOT_NEEDED ve attempt oluşmaz.
5. Kota eksiği olan fakat en az bir fikri bulunan target yeniden planlanmaz.
6. Kategori source plan’daki ilk pozitif kotadan seçilir.
7. Workspace izolasyonu.
8. Soft-deleted workspace reddi.
9. Başka brief source attempt reddi.
10. stage="categories" source attempt reddi.
11. pending source attempt reddi.
12. running source attempt reddi.
13. failed source attempt geçerli retry kaynağı olabilir.
14. partial source attempt geçerli retry kaynağı olabilir.
15. completed source attempt, eksik DB coverage varsa geçerli olabilir.
16. Bozuk source coverage snapshot reddedilir.
17. Brief stale reddedilir.
18. Assignment version değişmiş brief reddedilir.
19. Kilitlenmemiş brief reddedilir.
20. NULL brief_target_id taşıyan non-stale fikir fail-closed reddedilir.
21. Başka brief target’ına bağlı fikir reddedilir.
22. Başka brief kategori veya keyword bağlantılı fikir reddedilir.
23. Platform/format target snapshot uyumsuzluğu reddedilir.
24. Yeni attempt stage ideas_retry ve pending oluşturulur.
25. requested_target_ids yalnız missing target’lardır.
26. coverage snapshot tam beklenen yapıda saklanır.
27. warnings boş başlar.
28. Fonksiyon commit çağırmaz; caller rollback yapınca attempt kaybolur.
29. Caller commit yapınca attempt kalıcı olur.
30. Aynı key replay yeni attempt oluşturmaz.
31. Same-key replay canlı coverage değişse bile özgün snapshot planını döndürür.
32. Same-key farklı source_attempt_id mismatch üretir.
33. Başka key ile aktif ideas_retry attempt conflict üretir.
34. Aktif ideas attempt varken retry conflict üretir.
35. Süresi dolmuş aktif attempt reconcile edilir ve yeni retry açılabilir.
36. Replay snapshot bozuksa fail-closed hata verir.
37. İki session ile eşzamanlı aynı key sonucunda tek attempt oluşur.
38. Canonical kilit sırası için mevcut lock-order yaklaşımı korunur.
39. Hiç SocialIdea satırı oluşturulmadığı/değiştirilmediği doğrulanır.
40. AI, Celery, TaskResult veya ağ çağrısı yapılmadığı doğrulanır.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import copy
import threading
from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.core.social.idea_flow import _build_plan_coverage_snapshot
from app.core.social.idea_planner import (
    SocialIdeaGenerationPlan,
    build_social_idea_generation_plan,
)
from app.core.social.idea_retry_planner import build_social_idea_retry_plan
from app.core.social.idea_retry_flow import (
    SocialIdeaRetryFlowError,
    SocialIdeaRetryStart,
    begin_social_idea_retry,
)
from app.database.connection import SessionLocal
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
from app.generators.social.attempt_state import AttemptConflictError
from app.schemas.social_brief import (
    SocialBriefIdeasGenerateRequest,
    SocialBriefIdeasRetryRequest,
)

T0 = datetime(2026, 9, 25, 12, 0, 0, tzinfo=timezone.utc)


def _setup_fresh_environment(
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
    num_kws: int = 3,
    workspace_name: str = "Retry Brand",
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
    is_stale: bool = False,
    channel_assignment_version: int = 1,
    locked_at: datetime | None = None,
    targets_data: list[tuple[str, str]] | None = None,
) -> SocialBrief:
    """Test için SocialBrief ve bağlı keyword/target kayıtlarını oluşturur."""
    brief = SocialBrief(
        scoring_run_id=run_id,
        brand_name_snapshot="Test Marka",
        brand_context_snapshot="Test Context",
        channel_assignment_version=channel_assignment_version,
        format_matrix_version="v1",
        is_stale=is_stale,
        locked_at=locked_at,
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
        targets_data = [("instagram", "post"), ("twitter", "thread"), ("linkedin", "post")]

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


def _setup_environment_with_categories(
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
    num_categories: int = 2,
    targets_data: list[tuple[str, str]] | None = None,
    locked: bool = True,
    workspace_name: str = "Retry Brand",
):
    """Brief ve kategorileri hazır ortam oluşturur."""
    ws, run, kws = _setup_fresh_environment(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        num_kws=3,
        workspace_name=workspace_name,
    )
    brief = _create_test_brief(
        db_session,
        run.id,
        kws,
        locked_at=T0 if locked else None,
        targets_data=targets_data,
    )

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
    for cat in categories:
        db_session.refresh(cat)
    db_session.refresh(brief)

    targets = (
        db_session.query(SocialBriefTarget)
        .filter(SocialBriefTarget.brief_id == brief.id)
        .order_by(SocialBriefTarget.id.asc())
        .all()
    )

    return ws, run, kws, brief, categories, targets


def _create_source_ideas_attempt(
    db_session: Session,
    brief: SocialBrief,
    categories: list[SocialCategory],
    targets: list[SocialBriefTarget],
    status: str = "completed",
    idempotency_key: str = "source-attempt-001",
    ideas_per_category: int = 3,
) -> SocialGenerationAttempt:
    """Kanonik formatta geçerli bir kaynak 'ideas' attempt'i oluşturur."""
    plan = build_social_idea_generation_plan(
        selected_category_ids=tuple(c.id for c in categories),
        target_ids=tuple(t.id for t in targets),
        ideas_per_category=ideas_per_category,
    )
    req = SocialBriefIdeasGenerateRequest(
        idempotency_key=idempotency_key,
        category_ids=[c.id for c in categories],
        ideas_per_category=ideas_per_category,
    )
    cov = _build_plan_coverage_snapshot(req, plan)
    attempt = SocialGenerationAttempt(
        brief_id=brief.id,
        stage="ideas",
        idempotency_key=idempotency_key,
        status=status,
        requested_target_ids=list(t.id for t in targets),
        coverage=cov,
        warnings=[],
        heartbeat_at=T0,
        lease_expires_at=T0 + timedelta(seconds=1500),
        created_at=T0,
    )
    db_session.add(attempt)
    db_session.commit()
    db_session.refresh(attempt)
    return attempt


def _create_test_idea(
    db_session: Session,
    brief: SocialBrief,
    category: SocialCategory,
    target: SocialBriefTarget,
    keyword_id: int,
    idea_title: str = "Test Fikir",
    is_stale: bool = False,
) -> SocialIdea:
    """Doğrulanabilir SocialIdea satırı ekler."""
    idea = SocialIdea(
        brief_id=brief.id,
        category_id=category.id,
        brief_target_id=target.id,
        keyword_id=keyword_id,
        idea_title=idea_title,
        idea_description="Test Açıklaması",
        target_platform=target.platform,
        content_format=target.content_format,
        trend_alignment=0.85,
        is_stale=is_stale,
    )
    db_session.add(idea)
    db_session.commit()
    db_session.refresh(idea)
    return idea


# ==================== TESTLER ====================

def test_01_no_ideas_all_canonical_targets_missing(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """1. Hiç fikir yokken bütün canonical target’lar missing olur."""
    ws, run, kws, brief, cats, targets = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    source_att = _create_source_ideas_attempt(db_session, brief, cats, targets)

    req = SocialBriefIdeasRetryRequest(
        idempotency_key="retry-k-01",
        source_attempt_id=source_att.id,
    )

    res = begin_social_idea_retry(
        db_session,
        brief_id=brief.id,
        brand_profile_id=ws.id,
        request=req,
        now=T0,
    )

    assert isinstance(res, SocialIdeaRetryStart)
    assert res.attempt_created is True
    assert res.attempt_status == "pending"
    assert res.canonical_target_ids == tuple(t.id for t in targets)
    assert res.persisted_target_ids_at_start == ()
    assert res.missing_target_ids == tuple(t.id for t in targets)
    assert res.plan.total_requested == len(targets)


def test_02_one_target_persisted_only_others_in_retry_plan(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """2. Bir target doluysa yalnız diğerleri retry planına girer."""
    ws, run, kws, brief, cats, targets = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    source_att = _create_source_ideas_attempt(db_session, brief, cats, targets)

    # 1. target dolu
    _create_test_idea(db_session, brief, cats[0], targets[0], kws[0].id)

    req = SocialBriefIdeasRetryRequest(
        idempotency_key="retry-k-02",
        source_attempt_id=source_att.id,
    )

    res = begin_social_idea_retry(
        db_session,
        brief_id=brief.id,
        brand_profile_id=ws.id,
        request=req,
        now=T0,
    )

    assert res.persisted_target_ids_at_start == (targets[0].id,)
    assert res.missing_target_ids == (targets[1].id, targets[2].id)
    assert res.plan.total_requested == 2


def test_03_multiple_ideas_for_same_target_single_persisted_target(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """3. Aynı target’ta birden fazla fikir olması tek persisted target üretir."""
    ws, run, kws, brief, cats, targets = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    source_att = _create_source_ideas_attempt(db_session, brief, cats, targets)

    # Aynı target 0 için iki ayrı fikir
    _create_test_idea(db_session, brief, cats[0], targets[0], kws[0].id, idea_title="Fikir 1")
    _create_test_idea(db_session, brief, cats[1], targets[0], kws[1].id, idea_title="Fikir 2")

    req = SocialBriefIdeasRetryRequest(
        idempotency_key="retry-k-03",
        source_attempt_id=source_att.id,
    )

    res = begin_social_idea_retry(
        db_session,
        brief_id=brief.id,
        brand_profile_id=ws.id,
        request=req,
        now=T0,
    )

    assert res.persisted_target_ids_at_start == (targets[0].id,)
    assert res.missing_target_ids == (targets[1].id, targets[2].id)


def test_04_all_targets_persisted_raises_not_needed_no_attempt(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """4. Bütün target'lar VE bütün kategoriler doluysa IDEA_RETRY_NOT_NEEDED ve attempt oluşmaz."""
    ws, run, kws, brief, cats, targets = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    source_att = _create_source_ideas_attempt(db_session, brief, cats, targets)

    # Tüm hedefler dolu ve her kategori en az bir fikir almış (K4)
    for i, t in enumerate(targets):
        _create_test_idea(
            db_session, brief, cats[i % len(cats)], t, kws[0].id, idea_title=f"Fikir {i}"
        )

    req = SocialBriefIdeasRetryRequest(
        idempotency_key="retry-k-04",
        source_attempt_id=source_att.id,
    )

    with pytest.raises(SocialIdeaRetryFlowError) as exc_info:
        begin_social_idea_retry(
            db_session,
            brief_id=brief.id,
            brand_profile_id=ws.id,
            request=req,
            now=T0,
        )

    assert exc_info.value.error_code == "IDEA_RETRY_NOT_NEEDED"

    # Attempt oluşturulmadığını doğrula
    attempts_count = (
        db_session.query(SocialGenerationAttempt)
        .filter(
            SocialGenerationAttempt.brief_id == brief.id,
            SocialGenerationAttempt.stage == "ideas_retry",
        )
        .count()
    )
    assert attempts_count == 0


def test_05_quota_deficit_not_topped_up_if_target_has_one_idea(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """5. Kota eksiği olan fakat en az bir fikri bulunan target yeniden planlanmaz."""
    ws, run, kws, brief, cats, targets = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    # ideas_per_category=3 iken normalde her target 2-3 fikir alabilir
    source_att = _create_source_ideas_attempt(db_session, brief, cats, targets, ideas_per_category=3)

    # targets[0] için yalnız 1 fikir var
    _create_test_idea(db_session, brief, cats[0], targets[0], kws[0].id)

    req = SocialBriefIdeasRetryRequest(
        idempotency_key="retry-k-05",
        source_attempt_id=source_att.id,
    )

    res = begin_social_idea_retry(
        db_session,
        brief_id=brief.id,
        brand_profile_id=ws.id,
        request=req,
        now=T0,
    )

    # targets[0] dolu sayılır, retry planında YER ALMAZ
    assert targets[0].id not in res.missing_target_ids
    assert targets[0].id in res.persisted_target_ids_at_start


def test_06_category_selected_from_first_positive_quota_in_source_plan(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """6. Kategori source plan’daki ilk pozitif kotadan seçilir."""
    ws, run, kws, brief, cats, targets = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    source_att = _create_source_ideas_attempt(db_session, brief, cats, targets)

    req = SocialBriefIdeasRetryRequest(
        idempotency_key="retry-k-06",
        source_attempt_id=source_att.id,
    )

    res = begin_social_idea_retry(
        db_session,
        brief_id=brief.id,
        brand_profile_id=ws.id,
        request=req,
        now=T0,
    )

    # Her missing target'ın atandığı category_id doğrulanır
    for assign in res.plan.assignments:
        assert assign.category_id in (cats[0].id, cats[1].id)
        assert assign.requested_count == 1


def test_07_workspace_isolation(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """7. Workspace izolasyonu: Başka workspace ID'si ile çağrılınca BRIEF_NOT_FOUND."""
    ws, run, kws, brief, cats, targets = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    source_att = _create_source_ideas_attempt(db_session, brief, cats, targets)

    other_ws = make_workspace(name="Other Brand", status="confirmed")
    db_session.commit()

    req = SocialBriefIdeasRetryRequest(
        idempotency_key="retry-k-07",
        source_attempt_id=source_att.id,
    )

    with pytest.raises(SocialIdeaRetryFlowError) as exc_info:
        begin_social_idea_retry(
            db_session,
            brief_id=brief.id,
            brand_profile_id=other_ws.id,
            request=req,
            now=T0,
        )

    assert exc_info.value.error_code == "BRIEF_NOT_FOUND"


def test_08_soft_deleted_workspace_rejected(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """8. Soft-deleted workspace reddi: BRIEF_NOT_FOUND."""
    ws, run, kws, brief, cats, targets = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    source_att = _create_source_ideas_attempt(db_session, brief, cats, targets)

    ws.deleted_at = T0
    db_session.commit()

    req = SocialBriefIdeasRetryRequest(
        idempotency_key="retry-k-08",
        source_attempt_id=source_att.id,
    )

    with pytest.raises(SocialIdeaRetryFlowError) as exc_info:
        begin_social_idea_retry(
            db_session,
            brief_id=brief.id,
            brand_profile_id=ws.id,
            request=req,
            now=T0,
        )

    assert exc_info.value.error_code == "BRIEF_NOT_FOUND"


def test_09_other_brief_source_attempt_rejected(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """9. Başka brief source attempt reddi: IDEA_RETRY_SOURCE_NOT_FOUND."""
    ws1, run1, kws1, brief1, cats1, targets1 = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword, workspace_name="Brand 1"
    )
    ws2, run2, kws2, brief2, cats2, targets2 = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword, workspace_name="Brand 2"
    )
    source_att_other = _create_source_ideas_attempt(db_session, brief2, cats2, targets2)

    req = SocialBriefIdeasRetryRequest(
        idempotency_key="retry-k-09",
        source_attempt_id=source_att_other.id,
    )

    with pytest.raises(SocialIdeaRetryFlowError) as exc_info:
        begin_social_idea_retry(
            db_session,
            brief_id=brief1.id,
            brand_profile_id=ws1.id,
            request=req,
            now=T0,
        )

    assert exc_info.value.error_code == "IDEA_RETRY_SOURCE_NOT_FOUND"


def test_10_categories_stage_source_attempt_rejected(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """10. stage='categories' source attempt reddi: IDEA_RETRY_SOURCE_NOT_FOUND."""
    ws, run, kws, brief, cats, targets = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    cat_attempt = SocialGenerationAttempt(
        brief_id=brief.id,
        stage="categories",
        idempotency_key="cat-attempt-k",
        status="completed",
        heartbeat_at=T0,
        lease_expires_at=T0 + timedelta(seconds=1500),
        created_at=T0,
    )
    db_session.add(cat_attempt)
    db_session.commit()

    req = SocialBriefIdeasRetryRequest(
        idempotency_key="retry-k-10",
        source_attempt_id=cat_attempt.id,
    )

    with pytest.raises(SocialIdeaRetryFlowError) as exc_info:
        begin_social_idea_retry(
            db_session,
            brief_id=brief.id,
            brand_profile_id=ws.id,
            request=req,
            now=T0,
        )

    assert exc_info.value.error_code == "IDEA_RETRY_SOURCE_NOT_FOUND"


def test_11_pending_source_attempt_rejected(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """11. pending source attempt reddi: IDEA_RETRY_SOURCE_NOT_TERMINAL."""
    ws, run, kws, brief, cats, targets = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    source_att = _create_source_ideas_attempt(db_session, brief, cats, targets, status="pending")

    req = SocialBriefIdeasRetryRequest(
        idempotency_key="retry-k-11",
        source_attempt_id=source_att.id,
    )

    with pytest.raises(SocialIdeaRetryFlowError) as exc_info:
        begin_social_idea_retry(
            db_session,
            brief_id=brief.id,
            brand_profile_id=ws.id,
            request=req,
            now=T0,
        )

    assert exc_info.value.error_code == "IDEA_RETRY_SOURCE_NOT_TERMINAL"


def test_12_running_source_attempt_rejected(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """12. running source attempt reddi: IDEA_RETRY_SOURCE_NOT_TERMINAL."""
    ws, run, kws, brief, cats, targets = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    source_att = _create_source_ideas_attempt(db_session, brief, cats, targets, status="running")

    req = SocialBriefIdeasRetryRequest(
        idempotency_key="retry-k-12",
        source_attempt_id=source_att.id,
    )

    with pytest.raises(SocialIdeaRetryFlowError) as exc_info:
        begin_social_idea_retry(
            db_session,
            brief_id=brief.id,
            brand_profile_id=ws.id,
            request=req,
            now=T0,
        )

    assert exc_info.value.error_code == "IDEA_RETRY_SOURCE_NOT_TERMINAL"


def test_13_failed_source_attempt_is_valid(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """13. failed source attempt geçerli retry kaynağı olabilir."""
    ws, run, kws, brief, cats, targets = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    source_att = _create_source_ideas_attempt(db_session, brief, cats, targets, status="failed")

    req = SocialBriefIdeasRetryRequest(
        idempotency_key="retry-k-13",
        source_attempt_id=source_att.id,
    )

    res = begin_social_idea_retry(
        db_session,
        brief_id=brief.id,
        brand_profile_id=ws.id,
        request=req,
        now=T0,
    )
    assert res.attempt_created is True
    assert res.source_attempt_id == source_att.id


def test_14_partial_source_attempt_is_valid(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """14. partial source attempt geçerli retry kaynağı olabilir."""
    ws, run, kws, brief, cats, targets = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    source_att = _create_source_ideas_attempt(db_session, brief, cats, targets, status="partial")

    req = SocialBriefIdeasRetryRequest(
        idempotency_key="retry-k-14",
        source_attempt_id=source_att.id,
    )

    res = begin_social_idea_retry(
        db_session,
        brief_id=brief.id,
        brand_profile_id=ws.id,
        request=req,
        now=T0,
    )
    assert res.attempt_created is True


def test_15_completed_source_attempt_valid_if_missing_db_coverage(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """15. completed source attempt, eksik DB coverage varsa geçerli olabilir."""
    ws, run, kws, brief, cats, targets = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    source_att = _create_source_ideas_attempt(db_session, brief, cats, targets, status="completed")

    # DB'de sadece 1 target var
    _create_test_idea(db_session, brief, cats[0], targets[0], kws[0].id)

    req = SocialBriefIdeasRetryRequest(
        idempotency_key="retry-k-15",
        source_attempt_id=source_att.id,
    )

    res = begin_social_idea_retry(
        db_session,
        brief_id=brief.id,
        brand_profile_id=ws.id,
        request=req,
        now=T0,
    )
    assert res.attempt_created is True
    assert len(res.missing_target_ids) == 2


def test_16_corrupt_source_coverage_snapshot_rejected(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """16. Bozuk source coverage snapshot reddedilir: IDEA_RETRY_SOURCE_SNAPSHOT_INVALID."""
    ws, run, kws, brief, cats, targets = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    source_att = _create_source_ideas_attempt(db_session, brief, cats, targets)
    source_att.coverage = {"bad": "data"}
    db_session.commit()

    req = SocialBriefIdeasRetryRequest(
        idempotency_key="retry-k-16",
        source_attempt_id=source_att.id,
    )

    with pytest.raises(SocialIdeaRetryFlowError) as exc_info:
        begin_social_idea_retry(
            db_session,
            brief_id=brief.id,
            brand_profile_id=ws.id,
            request=req,
            now=T0,
        )

    assert exc_info.value.error_code == "IDEA_RETRY_SOURCE_SNAPSHOT_INVALID"


def test_17_stale_brief_rejected(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """17. Brief stale reddedilir: BRIEF_STALE."""
    ws, run, kws, brief, cats, targets = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    source_att = _create_source_ideas_attempt(db_session, brief, cats, targets)
    brief.is_stale = True
    db_session.commit()

    req = SocialBriefIdeasRetryRequest(
        idempotency_key="retry-k-17",
        source_attempt_id=source_att.id,
    )

    with pytest.raises(SocialIdeaRetryFlowError) as exc_info:
        begin_social_idea_retry(
            db_session,
            brief_id=brief.id,
            brand_profile_id=ws.id,
            request=req,
            now=T0,
        )

    assert exc_info.value.error_code == "BRIEF_STALE"


def test_18_assignment_version_changed_brief_rejected(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """18. Assignment version değişmiş brief reddedilir: ASSIGNMENT_CHANGED."""
    ws, run, kws, brief, cats, targets = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    source_att = _create_source_ideas_attempt(db_session, brief, cats, targets)
    brief.channel_assignment_version = 999
    db_session.commit()

    req = SocialBriefIdeasRetryRequest(
        idempotency_key="retry-k-18",
        source_attempt_id=source_att.id,
    )

    with pytest.raises(SocialIdeaRetryFlowError) as exc_info:
        begin_social_idea_retry(
            db_session,
            brief_id=brief.id,
            brand_profile_id=ws.id,
            request=req,
            now=T0,
        )

    assert exc_info.value.error_code == "ASSIGNMENT_CHANGED"


def test_19_unlocked_brief_rejected(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """19. Kilitlenmemiş brief reddedilir: BRIEF_NOT_LOCKED."""
    ws, run, kws, brief, cats, targets = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword, locked=False
    )
    source_att = _create_source_ideas_attempt(db_session, brief, cats, targets)

    req = SocialBriefIdeasRetryRequest(
        idempotency_key="retry-k-19",
        source_attempt_id=source_att.id,
    )

    with pytest.raises(SocialIdeaRetryFlowError) as exc_info:
        begin_social_idea_retry(
            db_session,
            brief_id=brief.id,
            brand_profile_id=ws.id,
            request=req,
            now=T0,
        )

    assert exc_info.value.error_code == "BRIEF_NOT_LOCKED"


def test_20_null_brief_target_id_in_idea_rejected(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """20. NULL brief_target_id taşıyan non-stale fikir fail-closed reddedilir."""
    ws, run, kws, brief, cats, targets = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    source_att = _create_source_ideas_attempt(db_session, brief, cats, targets)

    # NULL brief_target_id ile fikir
    bad_idea = SocialIdea(
        brief_id=brief.id,
        category_id=cats[0].id,
        brief_target_id=None,
        keyword_id=kws[0].id,
        idea_title="Bad Idea",
        idea_description="Desc",
        target_platform="instagram",
        content_format="post",
        trend_alignment=0.5,
        is_stale=False,
    )
    db_session.add(bad_idea)
    db_session.commit()

    req = SocialBriefIdeasRetryRequest(
        idempotency_key="retry-k-20",
        source_attempt_id=source_att.id,
    )

    with pytest.raises(SocialIdeaRetryFlowError) as exc_info:
        begin_social_idea_retry(
            db_session,
            brief_id=brief.id,
            brand_profile_id=ws.id,
            request=req,
            now=T0,
        )

    assert exc_info.value.error_code == "IDEA_RETRY_EXISTING_IDEA_INCONSISTENT"


def test_21_idea_linked_to_other_brief_target_rejected(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """21. Başka brief target’ına bağlı fikir reddedilir: IDEA_RETRY_EXISTING_IDEA_INCONSISTENT."""
    ws1, run1, kws1, brief1, cats1, targets1 = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword, workspace_name="B1"
    )
    ws2, run2, kws2, brief2, cats2, targets2 = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword, workspace_name="B2"
    )
    source_att = _create_source_ideas_attempt(db_session, brief1, cats1, targets1)

    # brief1 fikri ama brief2 target'ına bağlı
    bad_idea = SocialIdea(
        brief_id=brief1.id,
        category_id=cats1[0].id,
        brief_target_id=targets2[0].id,
        keyword_id=kws1[0].id,
        idea_title="Cross Target Idea",
        idea_description="Desc",
        target_platform=targets2[0].platform,
        content_format=targets2[0].content_format,
        trend_alignment=0.5,
        is_stale=False,
    )
    db_session.add(bad_idea)
    db_session.commit()

    req = SocialBriefIdeasRetryRequest(
        idempotency_key="retry-k-21",
        source_attempt_id=source_att.id,
    )

    with pytest.raises(SocialIdeaRetryFlowError) as exc_info:
        begin_social_idea_retry(
            db_session,
            brief_id=brief1.id,
            brand_profile_id=ws1.id,
            request=req,
            now=T0,
        )

    assert exc_info.value.error_code == "IDEA_RETRY_EXISTING_IDEA_INCONSISTENT"


def test_22_idea_linked_to_other_brief_category_rejected(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """22. Başka brief kategori bağlantılı fikir reddedilir: IDEA_RETRY_EXISTING_IDEA_INCONSISTENT."""
    ws1, run1, kws1, brief1, cats1, targets1 = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword, workspace_name="B1"
    )
    ws2, run2, kws2, brief2, cats2, targets2 = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword, workspace_name="B2"
    )
    source_att = _create_source_ideas_attempt(db_session, brief1, cats1, targets1)

    bad_idea = SocialIdea(
        brief_id=brief1.id,
        category_id=cats2[0].id,  # Other brief's category
        brief_target_id=targets1[0].id,
        keyword_id=kws1[0].id,
        idea_title="Cross Category Idea",
        idea_description="Desc",
        target_platform=targets1[0].platform,
        content_format=targets1[0].content_format,
        trend_alignment=0.5,
        is_stale=False,
    )
    db_session.add(bad_idea)
    db_session.commit()

    req = SocialBriefIdeasRetryRequest(
        idempotency_key="retry-k-22",
        source_attempt_id=source_att.id,
    )

    with pytest.raises(SocialIdeaRetryFlowError) as exc_info:
        begin_social_idea_retry(
            db_session,
            brief_id=brief1.id,
            brand_profile_id=ws1.id,
            request=req,
            now=T0,
        )

    assert exc_info.value.error_code == "IDEA_RETRY_EXISTING_IDEA_INCONSISTENT"


def test_23_platform_format_target_mismatch_in_idea_rejected(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """23. Platform/format target snapshot uyumsuzluğu reddedilir: IDEA_RETRY_EXISTING_IDEA_INCONSISTENT."""
    ws, run, kws, brief, cats, targets = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    source_att = _create_source_ideas_attempt(db_session, brief, cats, targets)

    # target[0] instagram/post, ama fikir twitter/thread diyor
    bad_idea = SocialIdea(
        brief_id=brief.id,
        category_id=cats[0].id,
        brief_target_id=targets[0].id,
        keyword_id=kws[0].id,
        idea_title="Mismatch Idea",
        idea_description="Desc",
        target_platform="twitter",  # Uyumsuz
        content_format="thread",
        trend_alignment=0.5,
        is_stale=False,
    )
    db_session.add(bad_idea)
    db_session.commit()

    req = SocialBriefIdeasRetryRequest(
        idempotency_key="retry-k-23",
        source_attempt_id=source_att.id,
    )

    with pytest.raises(SocialIdeaRetryFlowError) as exc_info:
        begin_social_idea_retry(
            db_session,
            brief_id=brief.id,
            brand_profile_id=ws.id,
            request=req,
            now=T0,
        )

    assert exc_info.value.error_code == "IDEA_RETRY_EXISTING_IDEA_INCONSISTENT"


def test_24_to_27_new_attempt_state_and_snapshot_structure(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """24-27. Yeni attempt stage ideas_retry, status pending, requested_target_ids missing, coverage yapısı, warnings boş."""
    ws, run, kws, brief, cats, targets = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    source_att = _create_source_ideas_attempt(db_session, brief, cats, targets)

    # targets[0] dolu olsun
    _create_test_idea(db_session, brief, cats[0], targets[0], kws[0].id)

    req = SocialBriefIdeasRetryRequest(
        idempotency_key="retry-k-24",
        source_attempt_id=source_att.id,
    )

    res = begin_social_idea_retry(
        db_session,
        brief_id=brief.id,
        brand_profile_id=ws.id,
        request=req,
        now=T0,
    )

    # 24. Stage & status
    attempt = db_session.query(SocialGenerationAttempt).filter(SocialGenerationAttempt.id == res.attempt_id).one()
    assert attempt.stage == "ideas_retry"
    assert attempt.status == "pending"

    # 25. requested_target_ids yalnız missing target'lardır
    assert tuple(attempt.requested_target_ids) == (targets[1].id, targets[2].id)

    # 26. coverage snapshot tam beklenen yapıda saklanır
    cov = attempt.coverage
    assert cov["schema_version"] == "ideas_retry_plan_v1"
    assert cov["request"]["source_attempt_id"] == source_att.id
    assert cov["baseline"]["canonical_target_ids"] == [t.id for t in targets]
    assert cov["baseline"]["persisted_target_ids_at_start"] == [targets[0].id]
    assert cov["plan"]["total_requested"] == 2
    assert cov["plan"]["missing_target_ids"] == [targets[1].id, targets[2].id]
    assert len(cov["plan"]["assignments"]) == 2
    assert cov["generated"] == {"total_accepted": 0, "target_ids": [], "assignments": []}
    # K4 kategori kapsaması baseline'ı: yalnız dolu kategori kayıtlı, boşlar planda
    assert cov["baseline"]["persisted_category_ids_at_start"] == [cats[0].id]
    assert cov["plan"]["empty_category_ids"] == [c.id for c in cats[1:]]

    # 27. warnings boş başlar
    assert attempt.warnings == []


def test_28_function_does_not_commit_caller_rollback_removes_attempt(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """28. Fonksiyon commit çağırmaz; caller rollback yapınca attempt kaybolur."""
    ws, run, kws, brief, cats, targets = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    source_att = _create_source_ideas_attempt(db_session, brief, cats, targets)

    req = SocialBriefIdeasRetryRequest(
        idempotency_key="retry-k-28",
        source_attempt_id=source_att.id,
    )

    res = begin_social_idea_retry(
        db_session,
        brief_id=brief.id,
        brand_profile_id=ws.id,
        request=req,
        now=T0,
    )
    att_id = res.attempt_id

    # Caller rollback
    db_session.rollback()

    assert db_session.query(SocialGenerationAttempt).filter(SocialGenerationAttempt.id == att_id).first() is None


def test_29_caller_commit_persists_attempt(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """29. Caller commit yapınca attempt kalıcı olur."""
    ws, run, kws, brief, cats, targets = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    source_att = _create_source_ideas_attempt(db_session, brief, cats, targets)

    req = SocialBriefIdeasRetryRequest(
        idempotency_key="retry-k-29",
        source_attempt_id=source_att.id,
    )

    res = begin_social_idea_retry(
        db_session,
        brief_id=brief.id,
        brand_profile_id=ws.id,
        request=req,
        now=T0,
    )
    att_id = res.attempt_id

    db_session.commit()

    persisted = db_session.query(SocialGenerationAttempt).filter(SocialGenerationAttempt.id == att_id).one()
    assert persisted.status == "pending"


def test_30_same_key_replay_returns_existing_attempt_no_duplicate(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """30. Aynı key replay yeni attempt oluşturmaz."""
    ws, run, kws, brief, cats, targets = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    source_att = _create_source_ideas_attempt(db_session, brief, cats, targets)

    req = SocialBriefIdeasRetryRequest(
        idempotency_key="retry-k-30",
        source_attempt_id=source_att.id,
    )

    res1 = begin_social_idea_retry(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0)
    db_session.commit()

    res2 = begin_social_idea_retry(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0)

    assert res1.attempt_id == res2.attempt_id
    assert res1.attempt_created is True
    assert res2.attempt_created is False
    assert res1.plan == res2.plan


def test_31_same_key_replay_returns_original_plan_even_if_db_coverage_changed(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """31. Same-key replay canlı coverage değişse bile özgün snapshot planını döndürür."""
    ws, run, kws, brief, cats, targets = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    source_att = _create_source_ideas_attempt(db_session, brief, cats, targets)

    req = SocialBriefIdeasRetryRequest(
        idempotency_key="retry-k-31",
        source_attempt_id=source_att.id,
    )

    # 1. Çağrı: Hiçbir hedef dolu değil, 3 hedef missing
    res1 = begin_social_idea_retry(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0)
    db_session.commit()
    assert len(res1.missing_target_ids) == 3

    # Canlı DB'de sonradan fikirler eklensin (tüm hedefler dolsun!)
    for i, t in enumerate(targets):
        _create_test_idea(db_session, brief, cats[0], t, kws[0].id, idea_title=f"Later Idea {i}")

    # 2. Çağrı: Canlı coverage değişti ama aynı idempotency key replay yapılıyor!
    # IDEA_RETRY_NOT_NEEDED üretmemeli; özgün snapshot planını döndürmeli
    res2 = begin_social_idea_retry(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0)

    assert res2.attempt_id == res1.attempt_id
    assert res2.attempt_created is False
    assert res2.missing_target_ids == res1.missing_target_ids
    assert res2.plan == res1.plan


def test_32_same_key_different_source_attempt_id_mismatch(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """32. Same-key farklı source_attempt_id mismatch üretir: IDEA_RETRY_ATTEMPT_REQUEST_MISMATCH."""
    ws, run, kws, brief, cats, targets = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    source_att1 = _create_source_ideas_attempt(db_session, brief, cats, targets, idempotency_key="src-1")
    source_att2 = _create_source_ideas_attempt(db_session, brief, cats, targets, idempotency_key="src-2")

    req1 = SocialBriefIdeasRetryRequest(idempotency_key="same-key", source_attempt_id=source_att1.id)
    begin_social_idea_retry(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req1, now=T0)
    db_session.commit()

    req2 = SocialBriefIdeasRetryRequest(idempotency_key="same-key", source_attempt_id=source_att2.id)
    with pytest.raises(SocialIdeaRetryFlowError) as exc_info:
        begin_social_idea_retry(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req2, now=T0)

    assert exc_info.value.error_code == "IDEA_RETRY_ATTEMPT_REQUEST_MISMATCH"


def test_33_active_ideas_retry_attempt_with_different_key_conflict(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """33. Başka key ile aktif ideas_retry attempt conflict üretir: ATTEMPT_CONFLICT."""
    ws, run, kws, brief, cats, targets = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    source_att = _create_source_ideas_attempt(db_session, brief, cats, targets)

    req1 = SocialBriefIdeasRetryRequest(idempotency_key="retry-k-1", source_attempt_id=source_att.id)
    begin_social_idea_retry(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req1, now=T0)
    db_session.commit()

    req2 = SocialBriefIdeasRetryRequest(idempotency_key="retry-k-2", source_attempt_id=source_att.id)
    with pytest.raises(SocialIdeaRetryFlowError) as exc_info:
        begin_social_idea_retry(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req2, now=T0)

    assert exc_info.value.error_code == "ATTEMPT_CONFLICT"


def test_34_active_ideas_attempt_blocks_retry(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """34. Aktif ideas attempt varken retry conflict üretir: ATTEMPT_CONFLICT."""
    ws, run, kws, brief, cats, targets = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    source_att = _create_source_ideas_attempt(db_session, brief, cats, targets, status="completed")

    # Yeni bir aktif ideas attempt
    active_ideas = SocialGenerationAttempt(
        brief_id=brief.id,
        stage="ideas",
        idempotency_key="active-ideas-key",
        status="running",
        heartbeat_at=T0,
        lease_expires_at=T0 + timedelta(seconds=1500),
        created_at=T0,
    )
    db_session.add(active_ideas)
    db_session.commit()

    req = SocialBriefIdeasRetryRequest(
        idempotency_key="retry-k-34",
        source_attempt_id=source_att.id,
    )

    with pytest.raises(SocialIdeaRetryFlowError) as exc_info:
        begin_social_idea_retry(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0)

    assert exc_info.value.error_code == "ATTEMPT_CONFLICT"


def test_35_expired_active_attempt_is_reconciled_allowing_new_retry(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """35. Süresi dolmuş aktif attempt reconcile edilir ve yeni retry açılabilir."""
    ws, run, kws, brief, cats, targets = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    source_att = _create_source_ideas_attempt(db_session, brief, cats, targets)

    # Süresi geçmiş running bir retry attempt
    stuck_retry = SocialGenerationAttempt(
        brief_id=brief.id,
        stage="ideas_retry",
        idempotency_key="old-stuck-retry",
        status="running",
        heartbeat_at=T0 - timedelta(hours=2),
        lease_expires_at=T0 - timedelta(hours=1),
        created_at=T0 - timedelta(hours=2),
    )
    db_session.add(stuck_retry)
    db_session.commit()

    req = SocialBriefIdeasRetryRequest(
        idempotency_key="new-retry-key",
        source_attempt_id=source_att.id,
    )

    # Reconciliation devreye girmeli ve yeni retry açılabilmeli
    res = begin_social_idea_retry(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0)
    assert res.attempt_created is True
    assert res.idempotency_key == "new-retry-key"

    db_session.refresh(stuck_retry)
    assert stuck_retry.status == "failed"
    assert stuck_retry.reason_code == "worker_lost"


def test_36_corrupt_replay_snapshot_raises_fail_closed(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """36. Replay snapshot bozuksa fail-closed hata verir: IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID."""
    ws, run, kws, brief, cats, targets = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    source_att = _create_source_ideas_attempt(db_session, brief, cats, targets)

    req = SocialBriefIdeasRetryRequest(
        idempotency_key="retry-k-36",
        source_attempt_id=source_att.id,
    )

    res = begin_social_idea_retry(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0)
    db_session.commit()

    # Snapshot'ı boz
    att = db_session.query(SocialGenerationAttempt).filter(SocialGenerationAttempt.id == res.attempt_id).one()
    att.coverage = {"bad": "schema"}
    db_session.commit()

    with pytest.raises(SocialIdeaRetryFlowError) as exc_info:
        begin_social_idea_retry(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0)

    assert exc_info.value.error_code == "IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID"


def test_37_two_concurrent_sessions_single_attempt_created(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """37. İki session ile eşzamanlı aynı key yarışında tek attempt oluşur (gerçek multi-session concurrency)."""
    ws, run, kws, brief, cats, targets = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    source_att = _create_source_ideas_attempt(db_session, brief, cats, targets)
    db_session.commit()

    brief_id = brief.id
    ws_id = ws.id
    source_att_id = source_att.id

    req = SocialBriefIdeasRetryRequest(
        idempotency_key="concurrent-key-37",
        source_attempt_id=source_att_id,
    )

    results = {}
    barrier = threading.Barrier(2)

    def _worker(thread_name: str):
        db = SessionLocal()
        try:
            barrier.wait(timeout=10)
            res = begin_social_idea_retry(
                db,
                brief_id=brief_id,
                brand_profile_id=ws_id,
                request=req,
                now=T0,
            )
            db.commit()
            results[thread_name] = ("ok", res)
        except Exception as exc:
            db.rollback()
            results[thread_name] = ("error", exc)
        finally:
            db.close()

    threads = [
        threading.Thread(target=_worker, args=(f"worker_{i}",))
        for i in range(2)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    for name, outcome in results.items():
        assert outcome[0] == "ok", f"Worker {name} failed: {outcome[1]}"

    res_0: SocialIdeaRetryStart = results["worker_0"][1]
    res_1: SocialIdeaRetryStart = results["worker_1"][1]

    assert res_0.attempt_id == res_1.attempt_id
    assert {res_0.attempt_created, res_1.attempt_created} == {True, False}

    db_session.commit()
    total_retry_attempts = (
        db_session.query(SocialGenerationAttempt)
        .filter(
            SocialGenerationAttempt.brief_id == brief_id,
            SocialGenerationAttempt.stage == "ideas_retry",
        )
        .count()
    )
    assert total_retry_attempts == 1


def test_38_canonical_lock_order_verified_via_sql_listener(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """38. Canonical kilit sırası (BrandProfile -> ScoringRun -> SocialBrief) korunur."""
    ws, run, kws, brief, cats, targets = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    source_att = _create_source_ideas_attempt(db_session, brief, cats, targets)

    locked_tables: list[str] = []

    def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        if "FOR UPDATE" in statement:
            if "brand_profiles" in statement:
                locked_tables.append("BrandProfile")
            elif "scoring_runs" in statement:
                locked_tables.append("ScoringRun")
            elif "social_briefs" in statement:
                locked_tables.append("SocialBrief")
            elif "social_generation_attempts" in statement:
                locked_tables.append("SocialGenerationAttempt")

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", before_cursor_execute)
    try:
        req = SocialBriefIdeasRetryRequest(
            idempotency_key="lock-order-key",
            source_attempt_id=source_att.id,
        )
        begin_social_idea_retry(
            db_session,
            brief_id=brief.id,
            brand_profile_id=ws.id,
            request=req,
            now=T0,
        )
    finally:
        event.remove(engine, "before_cursor_execute", before_cursor_execute)

    # Sıralama: BrandProfile -> ScoringRun -> SocialBrief
    assert "BrandProfile" in locked_tables
    assert "ScoringRun" in locked_tables
    assert "SocialBrief" in locked_tables

    bp_idx = locked_tables.index("BrandProfile")
    sr_idx = locked_tables.index("ScoringRun")
    sb_idx = locked_tables.index("SocialBrief")
    assert bp_idx < sr_idx < sb_idx


def test_39_no_social_idea_created_or_modified(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """39. Hiç SocialIdea satırı oluşturulmadığı/değiştirilmediği doğrulanır."""
    ws, run, kws, brief, cats, targets = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    source_att = _create_source_ideas_attempt(db_session, brief, cats, targets)

    _create_test_idea(db_session, brief, cats[0], targets[0], kws[0].id)
    ideas_before = db_session.query(SocialIdea).filter(SocialIdea.brief_id == brief.id).all()
    count_before = len(ideas_before)

    req = SocialBriefIdeasRetryRequest(
        idempotency_key="retry-k-39",
        source_attempt_id=source_att.id,
    )

    begin_social_idea_retry(
        db_session,
        brief_id=brief.id,
        brand_profile_id=ws.id,
        request=req,
        now=T0,
    )

    count_after = db_session.query(SocialIdea).filter(SocialIdea.brief_id == brief.id).count()
    assert count_before == count_after


def test_40_no_ai_celery_task_result_or_network_calls(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """40. AI, Celery, TaskResult veya ağ çağrısı yapılmadığı doğrulanır."""
    ws, run, kws, brief, cats, targets = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    source_att = _create_source_ideas_attempt(db_session, brief, cats, targets)

    initial_task_results = db_session.query(TaskResult).count()

    req = SocialBriefIdeasRetryRequest(
        idempotency_key="retry-k-40",
        source_attempt_id=source_att.id,
    )

    with patch("app.generators.ai_service.get_ai_service", side_effect=RuntimeError("AI must not be called")), \
         patch("celery.Celery.send_task", side_effect=RuntimeError("Celery must not be called")):
        res = begin_social_idea_retry(
            db_session,
            brief_id=brief.id,
            brand_profile_id=ws.id,
            request=req,
            now=T0,
        )

    assert res.attempt_created is True
    assert db_session.query(TaskResult).count() == initial_task_results


def test_41_expired_running_source_attempt_reconciled_and_retry_allowed(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """41. Expired running source attempt reconcile edilip failed/worker_lost olur ve retry açılır."""
    ws, run, kws, brief, cats, targets = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    source_att = _create_source_ideas_attempt(db_session, brief, cats, targets, status="running")
    source_att.lease_expires_at = T0 - timedelta(minutes=10)
    db_session.commit()

    req = SocialBriefIdeasRetryRequest(
        idempotency_key="retry-k-41",
        source_attempt_id=source_att.id,
    )

    res = begin_social_idea_retry(
        db_session,
        brief_id=brief.id,
        brand_profile_id=ws.id,
        request=req,
        now=T0,
    )

    assert res.attempt_created is True
    assert res.source_attempt_id == source_att.id

    db_session.refresh(source_att)
    assert source_att.status == "failed"
    assert source_att.reason_code == "worker_lost"


def test_42_non_expired_running_source_rejected(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """42. Non-expired running source hâlâ IDEA_RETRY_SOURCE_NOT_TERMINAL verir."""
    ws, run, kws, brief, cats, targets = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    source_att = _create_source_ideas_attempt(db_session, brief, cats, targets, status="running")
    source_att.lease_expires_at = T0 + timedelta(minutes=20)
    db_session.commit()

    req = SocialBriefIdeasRetryRequest(
        idempotency_key="retry-k-42",
        source_attempt_id=source_att.id,
    )

    with pytest.raises(SocialIdeaRetryFlowError) as exc_info:
        begin_social_idea_retry(
            db_session,
            brief_id=brief.id,
            brand_profile_id=ws.id,
            request=req,
            now=T0,
        )

    assert exc_info.value.error_code == "IDEA_RETRY_SOURCE_NOT_TERMINAL"
    db_session.refresh(source_att)
    assert source_att.status == "running"


def test_43_corrupt_replay_snapshot_canonical_order_rejected(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """43. Snapshot canonical target sırası bozuksa reddedilir: IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID."""
    ws, run, kws, brief, cats, targets = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    source_att = _create_source_ideas_attempt(db_session, brief, cats, targets)

    req = SocialBriefIdeasRetryRequest(
        idempotency_key="retry-k-43",
        source_attempt_id=source_att.id,
    )
    res = begin_social_idea_retry(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0)
    db_session.commit()

    att = db_session.query(SocialGenerationAttempt).filter(SocialGenerationAttempt.id == res.attempt_id).one()
    cov = copy.deepcopy(att.coverage)
    cov["baseline"]["canonical_target_ids"] = list(reversed(cov["baseline"]["canonical_target_ids"]))
    att.coverage = cov
    flag_modified(att, "coverage")
    db_session.commit()

    with pytest.raises(SocialIdeaRetryFlowError) as exc_info:
        begin_social_idea_retry(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0)

    assert exc_info.value.error_code == "IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID"


def test_44_corrupt_replay_snapshot_persisted_order_rejected(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """44. Snapshot persisted target sırası bozuksa reddedilir: IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID."""
    ws, run, kws, brief, cats, targets = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    source_att = _create_source_ideas_attempt(db_session, brief, cats, targets)
    _create_test_idea(db_session, brief, cats[0], targets[0], kws[0].id)
    _create_test_idea(db_session, brief, cats[1], targets[1], kws[1].id)

    req = SocialBriefIdeasRetryRequest(
        idempotency_key="retry-k-44",
        source_attempt_id=source_att.id,
    )
    res = begin_social_idea_retry(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0)
    db_session.commit()

    att = db_session.query(SocialGenerationAttempt).filter(SocialGenerationAttempt.id == res.attempt_id).one()
    cov = copy.deepcopy(att.coverage)
    cov["baseline"]["persisted_target_ids_at_start"] = [targets[1].id, targets[0].id]
    att.coverage = cov
    flag_modified(att, "coverage")
    db_session.commit()

    with pytest.raises(SocialIdeaRetryFlowError) as exc_info:
        begin_social_idea_retry(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0)

    assert exc_info.value.error_code == "IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID"


def test_45_corrupt_replay_snapshot_persisted_and_missing_intersect(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """45. Persisted ve missing kesişiyorsa reddedilir: IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID."""
    ws, run, kws, brief, cats, targets = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    source_att = _create_source_ideas_attempt(db_session, brief, cats, targets)
    _create_test_idea(db_session, brief, cats[0], targets[0], kws[0].id)

    req = SocialBriefIdeasRetryRequest(
        idempotency_key="retry-k-45",
        source_attempt_id=source_att.id,
    )
    res = begin_social_idea_retry(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0)
    db_session.commit()

    att = db_session.query(SocialGenerationAttempt).filter(SocialGenerationAttempt.id == res.attempt_id).one()
    cov = copy.deepcopy(att.coverage)
    cov["baseline"]["persisted_target_ids_at_start"] = [targets[0].id]
    cov["plan"]["missing_target_ids"] = [targets[0].id, targets[1].id, targets[2].id]
    att.coverage = cov
    flag_modified(att, "coverage")
    db_session.commit()

    with pytest.raises(SocialIdeaRetryFlowError) as exc_info:
        begin_social_idea_retry(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0)

    assert exc_info.value.error_code == "IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID"


def test_46_corrupt_replay_snapshot_union_not_canonical(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """46. Persisted ∪ missing canonical kümeyi tamamlamıyorsa reddedilir: IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID."""
    ws, run, kws, brief, cats, targets = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    source_att = _create_source_ideas_attempt(db_session, brief, cats, targets)

    req = SocialBriefIdeasRetryRequest(
        idempotency_key="retry-k-46",
        source_attempt_id=source_att.id,
    )
    res = begin_social_idea_retry(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0)
    db_session.commit()

    att = db_session.query(SocialGenerationAttempt).filter(SocialGenerationAttempt.id == res.attempt_id).one()
    cov = copy.deepcopy(att.coverage)
    cov["plan"]["missing_target_ids"] = [targets[0].id, targets[1].id]
    cov["plan"]["total_requested"] = 2
    cov["plan"]["assignments"] = cov["plan"]["assignments"][:2]
    att.requested_target_ids = [targets[0].id, targets[1].id]
    att.coverage = cov
    flag_modified(att, "coverage")
    db_session.commit()

    with pytest.raises(SocialIdeaRetryFlowError) as exc_info:
        begin_social_idea_retry(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0)

    assert exc_info.value.error_code == "IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID"


def test_47_corrupt_replay_snapshot_missing_differs_from_planner(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """47. Snapshot missing listesi planner sonucundan farklıysa reddedilir: IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID."""
    ws, run, kws, brief, cats, targets = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    source_att = _create_source_ideas_attempt(db_session, brief, cats, targets)
    _create_test_idea(db_session, brief, cats[0], targets[0], kws[0].id)

    req = SocialBriefIdeasRetryRequest(
        idempotency_key="retry-k-47",
        source_attempt_id=source_att.id,
    )
    res = begin_social_idea_retry(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0)
    db_session.commit()

    att = db_session.query(SocialGenerationAttempt).filter(SocialGenerationAttempt.id == res.attempt_id).one()
    cov = copy.deepcopy(att.coverage)
    cov["plan"]["missing_target_ids"] = [targets[2].id, targets[1].id]
    att.coverage = cov
    flag_modified(att, "coverage")
    db_session.commit()

    with pytest.raises(SocialIdeaRetryFlowError) as exc_info:
        begin_social_idea_retry(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0)

    assert exc_info.value.error_code == "IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID"


def test_48_corrupt_replay_snapshot_assignment_category_differs_from_planner(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """48. Snapshot assignment category_id planner sonucundan farklıysa reddedilir: IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID."""
    ws, run, kws, brief, cats, targets = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    source_att = _create_source_ideas_attempt(db_session, brief, cats, targets)

    req = SocialBriefIdeasRetryRequest(
        idempotency_key="retry-k-48",
        source_attempt_id=source_att.id,
    )
    res = begin_social_idea_retry(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0)
    db_session.commit()

    att = db_session.query(SocialGenerationAttempt).filter(SocialGenerationAttempt.id == res.attempt_id).one()
    cov = copy.deepcopy(att.coverage)
    cov["plan"]["assignments"][0]["category_id"] = 999999
    att.coverage = cov
    flag_modified(att, "coverage")
    db_session.commit()

    with pytest.raises(SocialIdeaRetryFlowError) as exc_info:
        begin_social_idea_retry(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0)

    assert exc_info.value.error_code == "IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID"


def test_49_corrupt_replay_snapshot_assignment_order_broken(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """49. Snapshot assignment sırası bozuksa reddedilir: IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID."""
    ws, run, kws, brief, cats, targets = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    source_att = _create_source_ideas_attempt(db_session, brief, cats, targets)

    req = SocialBriefIdeasRetryRequest(
        idempotency_key="retry-k-49",
        source_attempt_id=source_att.id,
    )
    res = begin_social_idea_retry(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0)
    db_session.commit()

    att = db_session.query(SocialGenerationAttempt).filter(SocialGenerationAttempt.id == res.attempt_id).one()
    cov = copy.deepcopy(att.coverage)
    cov["plan"]["assignments"] = list(reversed(cov["plan"]["assignments"]))
    att.coverage = cov
    flag_modified(att, "coverage")
    db_session.commit()

    with pytest.raises(SocialIdeaRetryFlowError) as exc_info:
        begin_social_idea_retry(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0)

    assert exc_info.value.error_code == "IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID"


def test_50_corrupt_replay_snapshot_requested_target_ids_mismatch(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """50. Snapshot requested_target_ids ile missing uyuşmuyorsa reddedilir: IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID."""
    ws, run, kws, brief, cats, targets = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    source_att = _create_source_ideas_attempt(db_session, brief, cats, targets)

    req = SocialBriefIdeasRetryRequest(
        idempotency_key="retry-k-50",
        source_attempt_id=source_att.id,
    )
    res = begin_social_idea_retry(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0)
    db_session.commit()

    att = db_session.query(SocialGenerationAttempt).filter(SocialGenerationAttempt.id == res.attempt_id).one()
    att.requested_target_ids = [targets[0].id]
    flag_modified(att, "requested_target_ids")
    db_session.commit()

    with pytest.raises(SocialIdeaRetryFlowError) as exc_info:
        begin_social_idea_retry(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0)

    assert exc_info.value.error_code == "IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID"


def test_51_corrupt_replay_snapshot_generated_target_out_of_scope(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """51. generated target kapsam dışıysa reddedilir: IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID."""
    ws, run, kws, brief, cats, targets = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    source_att = _create_source_ideas_attempt(db_session, brief, cats, targets)
    _create_test_idea(db_session, brief, cats[0], targets[0], kws[0].id)

    req = SocialBriefIdeasRetryRequest(
        idempotency_key="retry-k-51",
        source_attempt_id=source_att.id,
    )
    res = begin_social_idea_retry(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0)
    db_session.commit()

    att = db_session.query(SocialGenerationAttempt).filter(SocialGenerationAttempt.id == res.attempt_id).one()
    cov = copy.deepcopy(att.coverage)
    cov["generated"]["total_accepted"] = 1
    cov["generated"]["target_ids"] = [targets[0].id]
    att.coverage = cov
    flag_modified(att, "coverage")
    db_session.commit()

    with pytest.raises(SocialIdeaRetryFlowError) as exc_info:
        begin_social_idea_retry(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0)

    assert exc_info.value.error_code == "IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID"


def test_52_corrupt_replay_snapshot_generated_target_duplicate(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """52. generated target duplicate ise reddedilir: IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID."""
    ws, run, kws, brief, cats, targets = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    source_att = _create_source_ideas_attempt(db_session, brief, cats, targets)

    req = SocialBriefIdeasRetryRequest(
        idempotency_key="retry-k-52",
        source_attempt_id=source_att.id,
    )
    res = begin_social_idea_retry(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0)
    db_session.commit()

    att = db_session.query(SocialGenerationAttempt).filter(SocialGenerationAttempt.id == res.attempt_id).one()
    cov = copy.deepcopy(att.coverage)
    cov["generated"]["total_accepted"] = 2
    cov["generated"]["target_ids"] = [targets[0].id, targets[0].id]
    att.coverage = cov
    flag_modified(att, "coverage")
    db_session.commit()

    with pytest.raises(SocialIdeaRetryFlowError) as exc_info:
        begin_social_idea_retry(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0)

    assert exc_info.value.error_code == "IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID"


def test_53_corrupt_replay_snapshot_generated_total_accepted_mismatch(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """53. generated total_accepted ile target_ids uzunluğu uyuşmuyorsa reddedilir: IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID."""
    ws, run, kws, brief, cats, targets = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    source_att = _create_source_ideas_attempt(db_session, brief, cats, targets)

    req = SocialBriefIdeasRetryRequest(
        idempotency_key="retry-k-53",
        source_attempt_id=source_att.id,
    )
    res = begin_social_idea_retry(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0)
    db_session.commit()

    att = db_session.query(SocialGenerationAttempt).filter(SocialGenerationAttempt.id == res.attempt_id).one()
    cov = copy.deepcopy(att.coverage)
    cov["generated"]["total_accepted"] = 2
    cov["generated"]["target_ids"] = [targets[0].id]
    att.coverage = cov
    flag_modified(att, "coverage")
    db_session.commit()

    with pytest.raises(SocialIdeaRetryFlowError) as exc_info:
        begin_social_idea_retry(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0)

    assert exc_info.value.error_code == "IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID"


def test_54_corrupt_replay_snapshot_generated_total_accepted_exceeds_requested(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """54. generated total_accepted > total_requested ise reddedilir: IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID."""
    ws, run, kws, brief, cats, targets = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    source_att = _create_source_ideas_attempt(db_session, brief, cats, targets)

    req = SocialBriefIdeasRetryRequest(
        idempotency_key="retry-k-54",
        source_attempt_id=source_att.id,
    )
    res = begin_social_idea_retry(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0)
    db_session.commit()

    att = db_session.query(SocialGenerationAttempt).filter(SocialGenerationAttempt.id == res.attempt_id).one()
    cov = copy.deepcopy(att.coverage)
    cov["generated"]["total_accepted"] = 4
    cov["generated"]["target_ids"] = [targets[0].id, targets[1].id, targets[2].id]
    att.coverage = cov
    flag_modified(att, "coverage")
    db_session.commit()

    with pytest.raises(SocialIdeaRetryFlowError) as exc_info:
        begin_social_idea_retry(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0)

    assert exc_info.value.error_code == "IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID"


def test_55_valid_partially_completed_generated_block_in_replay_accepted(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """55. Geçerli kısmi veya tamamlanmış generated bloğu replay sırasında başarıyla kabul edilir."""
    ws, run, kws, brief, cats, targets = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    source_att = _create_source_ideas_attempt(db_session, brief, cats, targets)

    req = SocialBriefIdeasRetryRequest(
        idempotency_key="retry-k-55",
        source_attempt_id=source_att.id,
    )
    res = begin_social_idea_retry(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0)
    db_session.commit()

    att = db_session.query(SocialGenerationAttempt).filter(SocialGenerationAttempt.id == res.attempt_id).one()
    cov = copy.deepcopy(att.coverage)
    cov["generated"]["total_accepted"] = 1
    cov["generated"]["target_ids"] = [targets[0].id]
    first_pair = next(a for a in cov["plan"]["assignments"] if a["target_id"] == targets[0].id)
    cov["generated"]["assignments"] = [
        {"category_id": first_pair["category_id"], "target_id": targets[0].id}
    ]
    att.coverage = cov
    att.status = "partial"
    flag_modified(att, "coverage")
    db_session.commit()

    res2 = begin_social_idea_retry(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0)
    assert res2.attempt_id == att.id
    assert res2.attempt_created is False
    assert res2.attempt_status == "partial"


def test_56_same_key_replay_does_not_query_social_ideas_table(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """56. Same-key replay yolunda canlı SocialIdea coverage planlama sorgusunun kullanılmadığı doğrulanır."""
    ws, run, kws, brief, cats, targets = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    source_att = _create_source_ideas_attempt(db_session, brief, cats, targets)

    req = SocialBriefIdeasRetryRequest(
        idempotency_key="retry-k-56",
        source_attempt_id=source_att.id,
    )
    res1 = begin_social_idea_retry(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0)
    db_session.commit()

    social_idea_queries: list[str] = []

    def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        if "social_ideas" in statement.lower():
            social_idea_queries.append(statement)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", before_cursor_execute)
    try:
        res2 = begin_social_idea_retry(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0)
    finally:
        event.remove(engine, "before_cursor_execute", before_cursor_execute)

    assert res2.attempt_id == res1.attempt_id
    assert len(social_idea_queries) == 0, f"Replay sırasında social_ideas sorgusu tespit edildi: {social_idea_queries}"


def test_57_attempt_conflict_error_message_is_safe_and_does_not_leak_internals(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """57. AttemptConflictError mesajında iç attempt ID veya durum sızmaz."""
    ws, run, kws, brief, cats, targets = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    source_att = _create_source_ideas_attempt(db_session, brief, cats, targets)

    req = SocialBriefIdeasRetryRequest(
        idempotency_key="retry-k-57",
        source_attempt_id=source_att.id,
    )

    fake_conflict = AttemptConflictError(
        "Attempt 87654 conflict details that must never leak to user in running status"
    )
    with patch("app.core.social.idea_retry_flow.create_or_get_attempt", side_effect=fake_conflict):
        with pytest.raises(SocialIdeaRetryFlowError) as exc_info:
            begin_social_idea_retry(
                db_session,
                brief_id=brief.id,
                brand_profile_id=ws.id,
                request=req,
                now=T0,
            )

    assert exc_info.value.error_code == "ATTEMPT_CONFLICT"
    assert exc_info.value.message == "Aktif bir fikir tekrar deneme işlemi mevcut."
    assert "87654" not in str(exc_info.value)
    assert "running" not in str(exc_info.value)


def _make_race_existing_attempt(
    brief_id: int,
    source_attempt_id: int,
    source_plan: SocialIdeaGenerationPlan,
    canonical_target_ids: tuple[int, ...],
    persisted_target_ids: tuple[int, ...] = (),
    attempt_id: int = 777,
    idempotency_key: str = "retry-k-race",
    corrupt_schema: bool = False,
) -> SocialGenerationAttempt:
    retry_plan = build_social_idea_retry_plan(
        source_plan=source_plan,
        canonical_target_ids=canonical_target_ids,
        persisted_target_ids=persisted_target_ids,
    )
    coverage = {
        "schema_version": "invalid_version" if corrupt_schema else "ideas_retry_plan_v1",
        "request": {
            "source_attempt_id": source_attempt_id,
        },
        "baseline": {
            "canonical_target_ids": list(canonical_target_ids),
            "persisted_target_ids_at_start": list(persisted_target_ids),
        },
        "plan": {
            "total_requested": retry_plan.total_requested,
            "missing_target_ids": list(retry_plan.missing_target_ids),
            "assignments": [
                {
                    "category_id": a.category_id,
                    "target_id": a.target_id,
                    "requested_count": 1,
                }
                for a in retry_plan.assignments
            ],
        },
        "generated": {
            "total_accepted": 0,
            "target_ids": [],
        },
    }
    return SocialGenerationAttempt(
        id=attempt_id,
        brief_id=brief_id,
        stage="ideas_retry",
        status="pending",
        idempotency_key=idempotency_key,
        coverage=coverage,
        requested_target_ids=list(retry_plan.missing_target_ids),
    )


def test_58_race_fallback_happy_path(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """58. Race fallback happy path: create_or_get_attempt returns attempt_created=False."""
    ws, run, kws, brief, cats, targets = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    source_att = _create_source_ideas_attempt(db_session, brief, cats, targets)
    canonical_target_ids = tuple(t.id for t in targets)
    source_plan = build_social_idea_generation_plan(
        selected_category_ids=tuple(c.id for c in cats),
        target_ids=canonical_target_ids,
        ideas_per_category=3,
    )

    existing_att = _make_race_existing_attempt(
        brief.id,
        source_att.id,
        source_plan,
        canonical_target_ids,
        persisted_target_ids=(targets[0].id,),
        attempt_id=777,
    )

    initial_attempts_count = db_session.query(SocialGenerationAttempt).count()
    initial_ideas_count = db_session.query(SocialIdea).count()

    req_race = SocialBriefIdeasRetryRequest(
        idempotency_key="retry-k-race-58",
        source_attempt_id=source_att.id,
    )

    with patch(
        "app.core.social.idea_retry_flow.create_or_get_attempt",
        return_value=(existing_att, False),
    ):
        res_race = begin_social_idea_retry(
            db_session,
            brief_id=brief.id,
            brand_profile_id=ws.id,
            request=req_race,
            now=T0,
        )

    assert res_race.attempt_created is False
    assert res_race.attempt_id == 777
    assert res_race.canonical_target_ids == canonical_target_ids
    assert res_race.persisted_target_ids_at_start == (targets[0].id,)
    assert res_race.missing_target_ids == (targets[1].id, targets[2].id)
    assert res_race.plan.total_requested == 2
    assert db_session.query(SocialGenerationAttempt).count() == initial_attempts_count
    assert db_session.query(SocialIdea).count() == initial_ideas_count


def test_59_race_fallback_request_mismatch(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """59. Race fallback request mismatch: source_attempt_id uyuşmazlığında IDEA_RETRY_ATTEMPT_REQUEST_MISMATCH."""
    ws, run, kws, brief, cats, targets = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    source_att = _create_source_ideas_attempt(db_session, brief, cats, targets)
    canonical_target_ids = tuple(t.id for t in targets)
    source_plan = build_social_idea_generation_plan(
        selected_category_ids=tuple(c.id for c in cats),
        target_ids=canonical_target_ids,
        ideas_per_category=3,
    )

    # Snapshot'taki source_attempt_id istekten farklı olsun
    existing_att = _make_race_existing_attempt(
        brief.id,
        source_att.id + 999,
        source_plan,
        canonical_target_ids,
        attempt_id=778,
    )

    req_race = SocialBriefIdeasRetryRequest(
        idempotency_key="retry-k-race-59",
        source_attempt_id=source_att.id,
    )

    with patch(
        "app.core.social.idea_retry_flow.create_or_get_attempt",
        return_value=(existing_att, False),
    ):
        with pytest.raises(SocialIdeaRetryFlowError) as exc_info:
            begin_social_idea_retry(
                db_session,
                brief_id=brief.id,
                brand_profile_id=ws.id,
                request=req_race,
                now=T0,
            )

    assert exc_info.value.error_code == "IDEA_RETRY_ATTEMPT_REQUEST_MISMATCH"
    assert exc_info.value.message == "Replay request source_attempt_id parametresi mevcut attempt ile eşleşmiyor."
    assert exc_info.value.field == "request.source_attempt_id"
    assert str(source_att.id + 999) not in str(exc_info.value.message)


def test_60_race_fallback_corrupt_snapshot(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """60. Race fallback bozuk snapshot: IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID."""
    ws, run, kws, brief, cats, targets = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    source_att = _create_source_ideas_attempt(db_session, brief, cats, targets)
    canonical_target_ids = tuple(t.id for t in targets)
    source_plan = build_social_idea_generation_plan(
        selected_category_ids=tuple(c.id for c in cats),
        target_ids=canonical_target_ids,
        ideas_per_category=3,
    )

    existing_att = _make_race_existing_attempt(
        brief.id,
        source_att.id,
        source_plan,
        canonical_target_ids,
        corrupt_schema=True,
    )

    req_race = SocialBriefIdeasRetryRequest(
        idempotency_key="retry-k-race-60",
        source_attempt_id=source_att.id,
    )

    with patch(
        "app.core.social.idea_retry_flow.create_or_get_attempt",
        return_value=(existing_att, False),
    ):
        with pytest.raises(SocialIdeaRetryFlowError) as exc_info:
            begin_social_idea_retry(
                db_session,
                brief_id=brief.id,
                brand_profile_id=ws.id,
                request=req_race,
                now=T0,
            )

    assert exc_info.value.error_code == "IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID"
    assert exc_info.value.message == "Replay edilen attempt snapshot verisi geçersiz."
    assert "invalid_version" not in str(exc_info.value.message)


def test_61_race_fallback_authoritative_replay_safety(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """61. Race fallback sırasında sonucun yalnız attempt snapshot'ından geldiği doğrulanır."""
    ws, run, kws, brief, cats, targets = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    source_att = _create_source_ideas_attempt(db_session, brief, cats, targets)
    canonical_target_ids = tuple(t.id for t in targets)
    source_plan = build_social_idea_generation_plan(
        selected_category_ids=tuple(c.id for c in cats),
        target_ids=canonical_target_ids,
        ideas_per_category=3,
    )

    # 1. DB'de target 0 için fikir bulunsun (adım 10 bunu okuyacak)
    _create_test_idea(db_session, brief, cats[0], targets[0], kws[0].id)
    db_session.commit()

    # 2. Ancak yarışta gelen existing_att snapshot'ında persisted_target_ids_at_start boştur (tüm hedefler missing'dir)
    existing_att = _make_race_existing_attempt(
        brief.id,
        source_att.id,
        source_plan,
        canonical_target_ids,
        persisted_target_ids=(),  # Hepsi missing
        attempt_id=779,
    )

    req_race = SocialBriefIdeasRetryRequest(
        idempotency_key="retry-k-race-61",
        source_attempt_id=source_att.id,
    )

    with patch(
        "app.core.social.idea_retry_flow.create_or_get_attempt",
        return_value=(existing_att, False),
    ):
        res_race = begin_social_idea_retry(
            db_session,
            brief_id=brief.id,
            brand_profile_id=ws.id,
            request=req_race,
            now=T0,
        )

    # Canlı DB'de target 0 için fikir olsa bile, race fallback sonucu snapshot'tan gelir:
    assert res_race.persisted_target_ids_at_start == ()
    assert res_race.missing_target_ids == canonical_target_ids
    assert res_race.plan.total_requested == 3


def test_62_zero_references_to_removed_function():
    """62. Kod tabanında _validate_and_reconstruct_replay_snapshot bulunmadığı doğrulanır."""
    import inspect
    import app.core.social.idea_retry_flow as flow_mod
    source_code = inspect.getsource(flow_mod)
    assert "_validate_and_reconstruct_replay_snapshot" not in source_code

