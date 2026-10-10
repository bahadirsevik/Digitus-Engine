# -*- coding: utf-8 -*-
"""MİKRO FAZ F1-E.4 — Atomik Kategori Persistence ve Başarı Finalizasyonu Entegrasyon Testleri.

Bu test modülü doğrulanmış kategorilerin SocialCategory tablosuna atomik,
idempotent ve fail-closed biçimde kaydedilmesini, attempt finalizasyonunu
ve iki session'lı gerçek veritabanı eşzamanlılığını doğrular.
"""
from __future__ import annotations

import math
import threading
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from app.core.social.category_contract import (
    CANONICAL_CATEGORY_TYPES,
    ValidatedSocialCategory,
)
from app.core.social.category_flow import (
    CategoryKeywordSnapshot,
    SocialCategoryGenerationStart,
    begin_social_category_generation,
)
from app.core.social.category_persistence import (
    PersistedSocialCategoriesResult,
    PersistedSocialCategory,
    SocialCategoryPersistenceError,
    persist_social_categories,
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
)
from app.generators.social.attempt_state import (
    AttemptNotFoundError,
    AttemptNotWritableError,
    BriefNotFoundError,
    lock_categories_attempt_for_finalize,
)
from app.generators.social.brief_category_generator import SocialCategoryAIResult
from app.schemas.social_brief import SocialBriefCategoriesGenerateRequest

T0 = datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc)


def _setup_fresh_environment(
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
    num_kws: int = 3,
    workspace_name: str = "Persistence Brand",
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
    brand_name: str = "Test Marka",
    brand_context: str = "Test Context",
) -> SocialBrief:
    """Test için SocialBrief ve bağlı keyword/target kayıtlarını oluşturur."""
    brief = SocialBrief(
        scoring_run_id=run_id,
        brand_name_snapshot=brand_name,
        brand_context_snapshot=brand_context,
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

    t1 = SocialBriefTarget(
        brief_id=brief.id,
        platform="instagram",
        content_format="post",
    )
    db_session.add(t1)
    db_session.commit()
    db_session.refresh(brief)
    return brief


def _make_ai_result(
    attempt_id: int,
    categories: list[ValidatedSocialCategory] | None = None,
    ai_calls_used: int = 1,
    kw_ids: tuple[int, ...] = (1, 2),
) -> SocialCategoryAIResult:
    """Geçerli bir SocialCategoryAIResult nesnesi oluşturur."""
    if categories is None:
        categories = [
            ValidatedSocialCategory(
                category_name="Eğitim Rehberi",
                category_type="educational",
                description="Kullanıcılar için rehber içerik serisi.",
                relevance_score=0.95,
                suggested_keyword_ids=kw_ids,
            ),
            ValidatedSocialCategory(
                category_name="Ürün Avantajları",
                category_type="product_benefit",
                description="Öne çıkan ürün faydaları.",
                relevance_score=0.88,
                suggested_keyword_ids=kw_ids[:1],
            ),
        ]
    return SocialCategoryAIResult(
        attempt_id=attempt_id,
        categories=tuple(categories),
        ai_calls_used=ai_calls_used,
    )


# ==================== 1. LOCK ATTEMPT YARDIMCISI TESTLERİ ====================


def test_01_lock_categories_attempt_pending_returns_false(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """1. Pending attempt için already_completed=False döner."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)
    req = SocialBriefCategoriesGenerateRequest(idempotency_key="key-01")
    start = begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0)
    db_session.commit()

    att, b, r, already_comp = lock_categories_attempt_for_finalize(db_session, attempt_id=start.attempt_id, now=T0)
    assert att.id == start.attempt_id
    assert b.id == brief.id
    assert r.id == run.id
    assert already_comp is False


def test_02_lock_categories_attempt_completed_returns_true(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """2. Completed attempt için already_completed=True döner."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)
    req = SocialBriefCategoriesGenerateRequest(idempotency_key="key-02")
    start = begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0)

    # Attempt'i completed yap
    att_db = db_session.get(SocialGenerationAttempt, start.attempt_id)
    att_db.status = "completed"
    att_db.completed_at = T0
    att_db.lease_expires_at = None
    db_session.commit()

    att, b, r, already_comp = lock_categories_attempt_for_finalize(db_session, attempt_id=start.attempt_id, now=T0)
    assert att.id == start.attempt_id
    assert already_comp is True


def test_03_lock_categories_attempt_not_found_raises(db_session: Session):
    """3. Olmayan attempt_id ATTEMPT_NOT_FOUND fırlatır."""
    with pytest.raises(AttemptNotFoundError) as exc_info:
        lock_categories_attempt_for_finalize(db_session, attempt_id=999999, now=T0)
    assert exc_info.value.error_code == "ATTEMPT_NOT_FOUND"


def test_04_lock_categories_attempt_wrong_stage_raises_invalid_stage(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """4. stage != 'categories' AttemptNotWritableError (INVALID_STAGE) fırlatır."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)
    att = SocialGenerationAttempt(
        brief_id=brief.id,
        stage="ideas",
        status="pending",
        idempotency_key="key-04",
        lease_expires_at=T0 + timedelta(seconds=1500),
    )
    db_session.add(att)
    db_session.commit()

    with pytest.raises(AttemptNotWritableError) as exc_info:
        lock_categories_attempt_for_finalize(db_session, attempt_id=att.id, now=T0)
    assert exc_info.value.error_code == "INVALID_STAGE"


def test_05_lock_categories_attempt_with_task_id_raises_unexpected_task_id(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """5. task_id is not None UNEXPECTED_TASK_ID_FOR_SYNC fırlatır."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)
    att = SocialGenerationAttempt(
        brief_id=brief.id,
        stage="categories",
        status="pending",
        task_id="celery-task-123",
        idempotency_key="key-05",
        lease_expires_at=T0 + timedelta(seconds=1500),
    )
    db_session.add(att)
    db_session.commit()

    with pytest.raises(AttemptNotWritableError) as exc_info:
        lock_categories_attempt_for_finalize(db_session, attempt_id=att.id, now=T0)
    assert exc_info.value.error_code == "UNEXPECTED_TASK_ID_FOR_SYNC"


def test_06_lock_categories_attempt_expired_lease_sets_failed_worker_lost(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """6. Lease dolmuşsa failed/worker_lost işaretlenir ve WORKER_LOST fırlatılır."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)
    req = SocialBriefCategoriesGenerateRequest(idempotency_key="key-06")
    start = begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0)

    # Lease süresini geriye al
    att_db = db_session.get(SocialGenerationAttempt, start.attempt_id)
    att_db.lease_expires_at = T0 - timedelta(seconds=10)
    db_session.commit()

    with pytest.raises(AttemptNotWritableError) as exc_info:
        lock_categories_attempt_for_finalize(db_session, attempt_id=start.attempt_id, now=T0)
    assert exc_info.value.error_code == "WORKER_LOST"

    db_session.commit()
    att_reloaded = db_session.get(SocialGenerationAttempt, start.attempt_id)
    assert att_reloaded.status == "failed"
    assert att_reloaded.reason_code == "worker_lost"
    assert att_reloaded.lease_expires_at is None


def test_07_lock_categories_attempt_stale_brief_sets_failed_brief_stale(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """7. Stale brief durumunda failed/brief_stale yapılır ve BRIEF_STALE fırlatılır."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)
    req = SocialBriefCategoriesGenerateRequest(idempotency_key="key-07")
    start = begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0)

    # Brief'i stale yap
    brief_db = db_session.get(SocialBrief, brief.id)
    brief_db.is_stale = True
    db_session.commit()

    with pytest.raises(AttemptNotWritableError) as exc_info:
        lock_categories_attempt_for_finalize(db_session, attempt_id=start.attempt_id, now=T0)
    assert exc_info.value.error_code == "BRIEF_STALE"

    db_session.commit()
    att_reloaded = db_session.get(SocialGenerationAttempt, start.attempt_id)
    assert att_reloaded.status == "failed"
    assert att_reloaded.reason_code == "brief_stale"


def test_08_lock_categories_attempt_assignment_version_mismatch_sets_failed_assignment_changed(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """8. Assignment version uyuşmazlığında failed/assignment_changed yapılır."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)
    req = SocialBriefCategoriesGenerateRequest(idempotency_key="key-08")
    start = begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0)

    # Run sürümünü 2 yap
    run_db = db_session.get(ScoringRun, run.id)
    run_db.channel_assignment_version = 2
    db_session.commit()

    with pytest.raises(AttemptNotWritableError) as exc_info:
        lock_categories_attempt_for_finalize(db_session, attempt_id=start.attempt_id, now=T0)
    assert exc_info.value.error_code == "ASSIGNMENT_CHANGED"

    db_session.commit()
    att_reloaded = db_session.get(SocialGenerationAttempt, start.attempt_id)
    assert att_reloaded.status == "failed"
    assert att_reloaded.reason_code == "assignment_changed"


def test_09_lock_categories_attempt_running_or_failed_raises_not_writable(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """9. Running, partial veya failed attempt yazılabilir kabul edilmez."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)

    for st in ("running", "partial", "failed"):
        att = SocialGenerationAttempt(
            brief_id=brief.id,
            stage="categories",
            status=st,
            idempotency_key=f"key-09-{st}",
            lease_expires_at=T0 + timedelta(seconds=1500),
        )
        db_session.add(att)
        db_session.commit()

        with pytest.raises(AttemptNotWritableError) as exc_info:
            lock_categories_attempt_for_finalize(db_session, attempt_id=att.id, now=T0)
        assert exc_info.value.error_code == "ATTEMPT_NOT_WRITABLE"


def test_10_lock_categories_attempt_naive_datetime_raises_value_error(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """10. Naive datetime ValueError üretir."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)
    req = SocialBriefCategoriesGenerateRequest(idempotency_key="key-10")
    start = begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0)
    db_session.commit()

    naive_now = datetime(2026, 9, 24, 12, 0, 0)
    with pytest.raises(ValueError, match="timezone-aware"):
        lock_categories_attempt_for_finalize(db_session, attempt_id=start.attempt_id, now=naive_now)


# ==================== 2. BAŞARILI PERSISTENCE TESTLERİ ====================


def test_11_persist_social_categories_2_categories_atomic_success(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """11. 2 kategori atomik olarak kaydedilir, attempt completed olur."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)
    req = SocialBriefCategoriesGenerateRequest(idempotency_key="key-11", max_categories=4)
    start = begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0)
    db_session.commit()

    kw_ids = tuple(k.keyword_id for k in start.keywords)
    ai_result = _make_ai_result(attempt_id=start.attempt_id, kw_ids=kw_ids)

    res = persist_social_categories(db_session, start=start, ai_result=ai_result, now=T0)
    db_session.commit()

    assert res.already_completed is False
    assert res.brief_id == brief.id
    assert res.scoring_run_id == run.id
    assert res.attempt_id == start.attempt_id
    assert len(res.categories) == 2
    assert len(res.category_ids) == 2

    # DB kayıtlarını doğrula
    db_cats = (
        db_session.query(SocialCategory)
        .filter(SocialCategory.brief_id == brief.id)
        .order_by(SocialCategory.id.asc())
        .all()
    )
    assert len(db_cats) == 2
    assert [c.id for c in db_cats] == list(res.category_ids)

    # Attempt durumunu doğrula
    att_db = db_session.get(SocialGenerationAttempt, start.attempt_id)
    assert att_db.status == "completed"
    assert att_db.completed_at == T0
    assert att_db.lease_expires_at is None
    assert att_db.warnings == []
    assert att_db.reason_code is None
    assert att_db.coverage == {
        "category_count": 2,
        "keyword_ids_used": sorted(set(kw_ids)),
        "ai_calls_used": 1,
    }


def test_12_persist_social_categories_max_categories_boundary_saved(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """12. max_categories=6 sınırındaki kategori kümesi sırası korunarak kaydedilir."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)
    req = SocialBriefCategoriesGenerateRequest(idempotency_key="key-12", max_categories=6)
    start = begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0)
    db_session.commit()

    kw_ids = tuple(k.keyword_id for k in start.keywords)
    six_cats = [
        ValidatedSocialCategory(
            category_name=f"Kategori {i + 1}",
            category_type=CANONICAL_CATEGORY_TYPES[i % len(CANONICAL_CATEGORY_TYPES)],
            description=f"Açıklama {i + 1}",
            relevance_score=0.9 - (i * 0.05),
            suggested_keyword_ids=kw_ids[:2],
        )
        for i in range(6)
    ]
    ai_result = SocialCategoryAIResult(
        attempt_id=start.attempt_id,
        categories=tuple(six_cats),
        ai_calls_used=2,
    )

    res = persist_social_categories(db_session, start=start, ai_result=ai_result, now=T0)
    db_session.commit()

    assert len(res.categories) == 6
    assert [c.category_name for c in res.categories] == [f"Kategori {i + 1}" for i in range(6)]

    db_cats = (
        db_session.query(SocialCategory)
        .filter(SocialCategory.brief_id == brief.id)
        .order_by(SocialCategory.id.asc())
        .all()
    )
    assert len(db_cats) == 6
    assert [c.category_name for c in db_cats] == [f"Kategori {i + 1}" for i in range(6)]


def test_13_columns_written_correctly(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """13. Tüm kolonlar (isim, tip, açıklama, skor, keywords, stale flag) eksiksiz yazılır."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)
    req = SocialBriefCategoriesGenerateRequest(idempotency_key="key-13")
    start = begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0)
    db_session.commit()

    kw_ids = (kws[0].id, kws[1].id)
    ai_result = _make_ai_result(attempt_id=start.attempt_id, kw_ids=kw_ids)

    res = persist_social_categories(db_session, start=start, ai_result=ai_result, now=T0)
    db_session.commit()

    cat1 = db_session.get(SocialCategory, res.category_ids[0])
    assert cat1.scoring_run_id == run.id
    assert cat1.brief_id == brief.id
    assert cat1.category_name == "Eğitim Rehberi"
    assert cat1.category_type == "educational"
    assert cat1.description == "Kullanıcılar için rehber içerik serisi."
    assert cat1.is_stale is False
    assert math.isclose(cat1.relevance_score, 0.95, rel_tol=1e-5)
    assert cat1.suggested_keyword_ids == list(kw_ids)
    assert isinstance(cat1.suggested_keyword_ids, list)


def test_14_service_does_not_commit_or_rollback(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """14. Servis commit veya rollback çağırmaz; transaction çağıranın kontrolündedir."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)
    req = SocialBriefCategoriesGenerateRequest(idempotency_key="key-14")
    start = begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0)
    db_session.commit()

    ai_result = _make_ai_result(attempt_id=start.attempt_id, kw_ids=(kws[0].id,))

    persist_social_categories(db_session, start=start, ai_result=ai_result, now=T0)

    # İkinci bir session ile henüz commit edilmediğini doğrula
    session2 = SessionLocal()
    try:
        cats_in_other_session = session2.query(SocialCategory).filter_by(brief_id=brief.id).count()
        assert cats_in_other_session == 0
    finally:
        session2.close()

    db_session.commit()


# ==================== 3. ROLLBACK TESTİ ====================


def test_15_caller_rollback_reverts_categories_and_attempt_status(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """15. Caller rollback yaparsa kategoriler silinir ve attempt pending durumunda kalır."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)
    req = SocialBriefCategoriesGenerateRequest(idempotency_key="key-15")
    start = begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0)
    db_session.commit()

    ai_result = _make_ai_result(attempt_id=start.attempt_id, kw_ids=(kws[0].id,))

    persist_social_categories(db_session, start=start, ai_result=ai_result, now=T0)

    # Caller rollback çağırır
    db_session.rollback()

    # Kategoriler veritabanında olmamalı
    cats_count = db_session.query(SocialCategory).filter_by(brief_id=brief.id).count()
    assert cats_count == 0

    # Attempt pending durumuna dönmüş olmalı
    att_reloaded = db_session.get(SocialGenerationAttempt, start.attempt_id)
    assert att_reloaded.status == "pending"
    assert att_reloaded.completed_at is None
    assert att_reloaded.coverage is None


# ==================== 4. FAIL-CLOSED TESTLERİ ====================


def test_16_attempt_result_id_mismatch_raises_invalid_input(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """16. start.attempt_id ile ai_result.attempt_id uyuşmazlığı CATEGORY_PERSISTENCE_INVALID_INPUT fırlatır."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)
    req = SocialBriefCategoriesGenerateRequest(idempotency_key="key-16")
    start = begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0)
    db_session.commit()

    ai_result = _make_ai_result(attempt_id=start.attempt_id + 999, kw_ids=(kws[0].id,))

    with pytest.raises(SocialCategoryPersistenceError) as exc_info:
        persist_social_categories(db_session, start=start, ai_result=ai_result, now=T0)
    assert exc_info.value.error_code == "CATEGORY_PERSISTENCE_INVALID_INPUT"


def test_17_attempt_brief_id_or_run_mismatch_raises_inconsistent(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """17. start içindeki brief/run kimliği attempt ile uyuşmazsa CATEGORY_PERSISTENCE_INCONSISTENT fırlatır."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)
    req = SocialBriefCategoriesGenerateRequest(idempotency_key="key-17")
    start = begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0)
    db_session.commit()

    # Sahte start nesnesi (farklı brief_id)
    tampered_start = SocialCategoryGenerationStart(
        brief_id=brief.id + 999,
        scoring_run_id=start.scoring_run_id,
        brand_profile_id=start.brand_profile_id,
        attempt_id=start.attempt_id,
        attempt_created=True,
        attempt_status="pending",
        idempotency_key=start.idempotency_key,
        max_categories=start.max_categories,
        locked_at=start.locked_at,
        brand_name_snapshot=start.brand_name_snapshot,
        brand_context_snapshot=start.brand_context_snapshot,
        keywords=start.keywords,
    )
    ai_result = _make_ai_result(attempt_id=start.attempt_id, kw_ids=(kws[0].id,))

    with pytest.raises(SocialCategoryPersistenceError) as exc_info:
        persist_social_categories(db_session, start=tampered_start, ai_result=ai_result, now=T0)
    assert exc_info.value.error_code == "CATEGORY_PERSISTENCE_INCONSISTENT"


def test_18_stale_brief_raises_brief_stale_and_creates_zero_categories(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """18. Stale brief persist_social_categories sırasında BRIEF_STALE üretir ve sıfır kategori kaydeder."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)
    req = SocialBriefCategoriesGenerateRequest(idempotency_key="key-18")
    start = begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0)

    # Brief'i stale yap
    brief_db = db_session.get(SocialBrief, brief.id)
    brief_db.is_stale = True
    db_session.commit()

    ai_result = _make_ai_result(attempt_id=start.attempt_id, kw_ids=(kws[0].id,))

    with pytest.raises(AttemptNotWritableError) as exc_info:
        persist_social_categories(db_session, start=start, ai_result=ai_result, now=T0)
    assert exc_info.value.error_code == "BRIEF_STALE"

    db_session.rollback()
    assert db_session.query(SocialCategory).filter_by(brief_id=brief.id).count() == 0


def test_19_assignment_version_changed_raises_assignment_changed_and_creates_zero_categories(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """19. Sürüm değişimi persist_social_categories sırasında ASSIGNMENT_CHANGED üretir ve sıfır kategori kaydeder."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)
    req = SocialBriefCategoriesGenerateRequest(idempotency_key="key-19")
    start = begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0)

    # Run sürümünü değiştir
    run_db = db_session.get(ScoringRun, run.id)
    run_db.channel_assignment_version = 5
    db_session.commit()

    ai_result = _make_ai_result(attempt_id=start.attempt_id, kw_ids=(kws[0].id,))

    with pytest.raises(AttemptNotWritableError) as exc_info:
        persist_social_categories(db_session, start=start, ai_result=ai_result, now=T0)
    assert exc_info.value.error_code == "ASSIGNMENT_CHANGED"

    db_session.rollback()
    assert db_session.query(SocialCategory).filter_by(brief_id=brief.id).count() == 0


def test_20_lease_expired_attempt_raises_worker_lost_and_creates_zero_categories(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """20. Lease süresi dolmuş attempt WORKER_LOST üretir ve sıfır kategori kaydeder."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)
    req = SocialBriefCategoriesGenerateRequest(idempotency_key="key-20")
    start = begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0)

    # Lease süresini geriye al
    att_db = db_session.get(SocialGenerationAttempt, start.attempt_id)
    att_db.lease_expires_at = T0 - timedelta(seconds=1)
    db_session.commit()

    ai_result = _make_ai_result(attempt_id=start.attempt_id, kw_ids=(kws[0].id,))

    with pytest.raises(AttemptNotWritableError) as exc_info:
        persist_social_categories(db_session, start=start, ai_result=ai_result, now=T0)
    assert exc_info.value.error_code == "WORKER_LOST"

    db_session.rollback()
    assert db_session.query(SocialCategory).filter_by(brief_id=brief.id).count() == 0


def test_21_running_or_failed_attempt_raises_not_writable_and_creates_zero_categories(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """21. Failed veya running durumdaki attempt ATTEMPT_NOT_WRITABLE üretir."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)
    req = SocialBriefCategoriesGenerateRequest(idempotency_key="key-21")
    start = begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0)

    att_db = db_session.get(SocialGenerationAttempt, start.attempt_id)
    att_db.status = "failed"
    db_session.commit()

    ai_result = _make_ai_result(attempt_id=start.attempt_id, kw_ids=(kws[0].id,))

    with pytest.raises(AttemptNotWritableError) as exc_info:
        persist_social_categories(db_session, start=start, ai_result=ai_result, now=T0)
    assert exc_info.value.error_code == "ATTEMPT_NOT_WRITABLE"

    db_session.rollback()
    assert db_session.query(SocialCategory).filter_by(brief_id=brief.id).count() == 0


def test_22_start_snapshot_and_db_keyword_snapshot_mismatch_raises_inconsistent(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """22. DB'deki keyword snapshot ile start.keywords uyuşmazlığı CATEGORY_PERSISTENCE_INCONSISTENT üretir."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)
    req = SocialBriefCategoriesGenerateRequest(idempotency_key="key-22")
    start = begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0)
    db_session.commit()

    # start.keywords içine sahte keyword enjekte et
    tampered_kws = (
        CategoryKeywordSnapshot(keyword_id=99999, keyword_snapshot="sahte", position=0),
    )
    tampered_start = SocialCategoryGenerationStart(
        brief_id=start.brief_id,
        scoring_run_id=start.scoring_run_id,
        brand_profile_id=start.brand_profile_id,
        attempt_id=start.attempt_id,
        attempt_created=True,
        attempt_status="pending",
        idempotency_key=start.idempotency_key,
        max_categories=start.max_categories,
        locked_at=start.locked_at,
        brand_name_snapshot=start.brand_name_snapshot,
        brand_context_snapshot=start.brand_context_snapshot,
        keywords=tampered_kws,
    )
    ai_result = _make_ai_result(attempt_id=start.attempt_id, kw_ids=(99999,))

    with pytest.raises(SocialCategoryPersistenceError) as exc_info:
        persist_social_categories(db_session, start=tampered_start, ai_result=ai_result, now=T0)
    assert exc_info.value.error_code == "CATEGORY_PERSISTENCE_INCONSISTENT"


def test_23_suggested_keyword_out_of_brief_raises_inconsistent(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """23. Kategori içindeki suggested_keyword_id brief dışındaysa CATEGORY_PERSISTENCE_INCONSISTENT üretir."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)
    req = SocialBriefCategoriesGenerateRequest(idempotency_key="key-23")
    start = begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0)
    db_session.commit()

    # Brief'e ait olmayan keyword_id: 88888
    ai_result = _make_ai_result(attempt_id=start.attempt_id, kw_ids=(88888,))

    with pytest.raises(SocialCategoryPersistenceError) as exc_info:
        persist_social_categories(db_session, start=start, ai_result=ai_result, now=T0)
    assert exc_info.value.error_code == "CATEGORY_PERSISTENCE_INCONSISTENT"
    assert exc_info.value.field == "suggested_keyword_ids"


def test_24_duplicate_category_name_casefold_raises_invalid_input(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """24. Büyük/küçük harf duyarsız mükerrer kategori adı CATEGORY_PERSISTENCE_INVALID_INPUT üretir."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)
    req = SocialBriefCategoriesGenerateRequest(idempotency_key="key-24")
    start = begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0)
    db_session.commit()

    kw_ids = (kws[0].id,)
    duplicate_cats = [
        ValidatedSocialCategory(
            category_name="Eğitim Rehberi",
            category_type="educational",
            description="Açıklama 1",
            relevance_score=0.9,
            suggested_keyword_ids=kw_ids,
        ),
        ValidatedSocialCategory(
            category_name="eğitim rehberi",
            category_type="product_benefit",
            description="Açıklama 2",
            relevance_score=0.8,
            suggested_keyword_ids=kw_ids,
        ),
    ]
    ai_result = SocialCategoryAIResult(
        attempt_id=start.attempt_id,
        categories=tuple(duplicate_cats),
        ai_calls_used=1,
    )

    with pytest.raises(SocialCategoryPersistenceError) as exc_info:
        persist_social_categories(db_session, start=start, ai_result=ai_result, now=T0)
    assert exc_info.value.error_code == "CATEGORY_PERSISTENCE_INVALID_INPUT"
    assert exc_info.value.field == "category_name"
    assert exc_info.value.category_index == 1


def test_24b_category_name_with_leading_trailing_whitespace_rejected(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """24b. category_name başında veya sonunda whitespace bulunması reddedilir."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)
    req = SocialBriefCategoriesGenerateRequest(idempotency_key="key-24b")
    start = begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0)
    db_session.commit()

    kw_ids = (kws[0].id,)
    ws_cats = [
        ValidatedSocialCategory(
            category_name="  Eğitim Rehberi  ",
            category_type="educational",
            description="Açıklama 1",
            relevance_score=0.9,
            suggested_keyword_ids=kw_ids,
        ),
        ValidatedSocialCategory(
            category_name="Ürün Avantajı",
            category_type="product_benefit",
            description="Açıklama 2",
            relevance_score=0.8,
            suggested_keyword_ids=kw_ids,
        ),
    ]
    ai_result = SocialCategoryAIResult(
        attempt_id=start.attempt_id,
        categories=tuple(ws_cats),
        ai_calls_used=1,
    )

    with pytest.raises(SocialCategoryPersistenceError) as exc_info:
        persist_social_categories(db_session, start=start, ai_result=ai_result, now=T0)
    assert exc_info.value.error_code == "CATEGORY_PERSISTENCE_INVALID_INPUT"
    assert exc_info.value.field == "category_name"
    assert exc_info.value.category_index == 0


def test_24c_description_with_leading_trailing_whitespace_rejected(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """24c. description başında veya sonunda whitespace bulunması reddedilir."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)
    req = SocialBriefCategoriesGenerateRequest(idempotency_key="key-24c")
    start = begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0)
    db_session.commit()

    kw_ids = (kws[0].id,)
    ws_cats = [
        ValidatedSocialCategory(
            category_name="Eğitim Rehberi",
            category_type="educational",
            description=" Açıklama 1 ",
            relevance_score=0.9,
            suggested_keyword_ids=kw_ids,
        ),
        ValidatedSocialCategory(
            category_name="Ürün Avantajı",
            category_type="product_benefit",
            description="Açıklama 2",
            relevance_score=0.8,
            suggested_keyword_ids=kw_ids,
        ),
    ]
    ai_result = SocialCategoryAIResult(
        attempt_id=start.attempt_id,
        categories=tuple(ws_cats),
        ai_calls_used=1,
    )

    with pytest.raises(SocialCategoryPersistenceError) as exc_info:
        persist_social_categories(db_session, start=start, ai_result=ai_result, now=T0)
    assert exc_info.value.error_code == "CATEGORY_PERSISTENCE_INVALID_INPUT"
    assert exc_info.value.field == "description"
    assert exc_info.value.category_index == 0


def test_24d_description_over_2000_chars_rejected(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """24d. 2000 karakter üzeri description reddedilir (2001 karakter)."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)
    req = SocialBriefCategoriesGenerateRequest(idempotency_key="key-24d")
    start = begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0)
    db_session.commit()

    kw_ids = (kws[0].id,)
    long_cats = [
        ValidatedSocialCategory(
            category_name="Eğitim Rehberi",
            category_type="educational",
            description="A" * 2001,
            relevance_score=0.9,
            suggested_keyword_ids=kw_ids,
        ),
        ValidatedSocialCategory(
            category_name="Ürün Avantajı",
            category_type="product_benefit",
            description="Açıklama 2",
            relevance_score=0.8,
            suggested_keyword_ids=kw_ids,
        ),
    ]
    ai_result = SocialCategoryAIResult(
        attempt_id=start.attempt_id,
        categories=tuple(long_cats),
        ai_calls_used=1,
    )

    with pytest.raises(SocialCategoryPersistenceError) as exc_info:
        persist_social_categories(db_session, start=start, ai_result=ai_result, now=T0)
    assert exc_info.value.error_code == "CATEGORY_PERSISTENCE_INVALID_INPUT"
    assert exc_info.value.field == "description"
    assert exc_info.value.category_index == 0


def test_24e_valid_100_char_name_and_2000_char_description_accepted(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """24e. Tam 100 karakter ad ve 2000 karakter açıklama sınır değerleri başarıyla kabul edilir."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)
    req = SocialBriefCategoriesGenerateRequest(idempotency_key="key-24e")
    start = begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0)
    db_session.commit()

    kw_ids = (kws[0].id,)
    boundary_cats = [
        ValidatedSocialCategory(
            category_name="K" * 100,
            category_type="educational",
            description="D" * 2000,
            relevance_score=0.9,
            suggested_keyword_ids=kw_ids,
        ),
        ValidatedSocialCategory(
            category_name="Normal Kat",
            category_type="product_benefit",
            description="Normal açıklama",
            relevance_score=0.8,
            suggested_keyword_ids=kw_ids,
        ),
    ]
    ai_result = SocialCategoryAIResult(
        attempt_id=start.attempt_id,
        categories=tuple(boundary_cats),
        ai_calls_used=1,
    )

    res = persist_social_categories(db_session, start=start, ai_result=ai_result, now=T0)
    db_session.commit()

    assert len(res.categories) == 2
    assert res.categories[0].category_name == "K" * 100
    assert res.categories[0].description == "D" * 2000


def test_24f_forged_suggested_keyword_ids_list_rejected(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """24f. Forged dataclass içinde tuple yerine list verilmesi reddedilir."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)
    req = SocialBriefCategoriesGenerateRequest(idempotency_key="key-24f")
    start = begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0)
    db_session.commit()

    forged_cats = [
        ValidatedSocialCategory(
            category_name="Eğitim Rehberi",
            category_type="educational",
            description="Açıklama 1",
            relevance_score=0.9,
            suggested_keyword_ids=[kws[0].id],  # type: ignore (tuple yerine list)
        ),
        ValidatedSocialCategory(
            category_name="Ürün Avantajı",
            category_type="product_benefit",
            description="Açıklama 2",
            relevance_score=0.8,
            suggested_keyword_ids=(kws[0].id,),
        ),
    ]
    ai_result = SocialCategoryAIResult(
        attempt_id=start.attempt_id,
        categories=tuple(forged_cats),
        ai_calls_used=1,
    )

    with pytest.raises(SocialCategoryPersistenceError) as exc_info:
        persist_social_categories(db_session, start=start, ai_result=ai_result, now=T0)
    assert exc_info.value.error_code == "CATEGORY_PERSISTENCE_INVALID_INPUT"
    assert exc_info.value.field == "suggested_keyword_ids"
    assert exc_info.value.category_index == 0


def test_25_invalid_relevance_score_raises_invalid_input(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """25. 0-1 aralığı dışındaki veya sonsuz relevance_score CATEGORY_PERSISTENCE_INVALID_INPUT üretir."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)
    req = SocialBriefCategoriesGenerateRequest(idempotency_key="key-25")
    start = begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0)
    db_session.commit()

    kw_ids = (kws[0].id,)
    invalid_cats = [
        ValidatedSocialCategory(
            category_name="Kategori 1",
            category_type="educational",
            description="Açıklama 1",
            relevance_score=1.5,  # > 1.0
            suggested_keyword_ids=kw_ids,
        ),
        ValidatedSocialCategory(
            category_name="Kategori 2",
            category_type="product_benefit",
            description="Açıklama 2",
            relevance_score=0.5,
            suggested_keyword_ids=kw_ids,
        ),
    ]
    ai_result = SocialCategoryAIResult(
        attempt_id=start.attempt_id,
        categories=tuple(invalid_cats),
        ai_calls_used=1,
    )

    with pytest.raises(SocialCategoryPersistenceError) as exc_info:
        persist_social_categories(db_session, start=start, ai_result=ai_result, now=T0)
    assert exc_info.value.error_code == "CATEGORY_PERSISTENCE_INVALID_INPUT"
    assert exc_info.value.field == "relevance_score"
    assert exc_info.value.category_index == 0


def test_25b_relevance_score_huge_positive_overflow_safe(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """25b. Aşırı büyük pozitif integer (10**10000) float taşması yapmadan CATEGORY_PERSISTENCE_INVALID_INPUT üretir."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)
    req = SocialBriefCategoriesGenerateRequest(idempotency_key="key-25b")
    start = begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0)
    db_session.commit()

    kw_ids = (kws[0].id,)
    overflow_cats = [
        ValidatedSocialCategory(
            category_name="Kategori 1",
            category_type="educational",
            description="Açıklama 1",
            relevance_score=0.9,
            suggested_keyword_ids=kw_ids,
        ),
        ValidatedSocialCategory(
            category_name="Kategori 2",
            category_type="product_benefit",
            description="Açıklama 2",
            relevance_score=10**10000,  # Huge int
            suggested_keyword_ids=kw_ids,
        ),
    ]
    ai_result = SocialCategoryAIResult(
        attempt_id=start.attempt_id,
        categories=tuple(overflow_cats),
        ai_calls_used=1,
    )

    with pytest.raises(SocialCategoryPersistenceError) as exc_info:
        persist_social_categories(db_session, start=start, ai_result=ai_result, now=T0)

    assert exc_info.value.error_code == "CATEGORY_PERSISTENCE_INVALID_INPUT"
    assert exc_info.value.field == "relevance_score"
    assert exc_info.value.category_index == 1

    # Ham OverflowError sızmamalı, DB'de satır olmamalı, attempt pending kalmalı
    db_session.rollback()
    assert db_session.query(SocialCategory).filter_by(brief_id=brief.id).count() == 0
    att = db_session.get(SocialGenerationAttempt, start.attempt_id)
    assert att.status == "pending"


def test_25c_relevance_score_huge_negative_overflow_safe(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """25c. Aşırı büyük negatif integer (-(10**10000)) float taşması yapmadan CATEGORY_PERSISTENCE_INVALID_INPUT üretir."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)
    req = SocialBriefCategoriesGenerateRequest(idempotency_key="key-25c")
    start = begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0)
    db_session.commit()

    kw_ids = (kws[0].id,)
    overflow_cats = [
        ValidatedSocialCategory(
            category_name="Kategori 1",
            category_type="educational",
            description="Açıklama 1",
            relevance_score=-(10**10000),  # Huge negative int
            suggested_keyword_ids=kw_ids,
        ),
        ValidatedSocialCategory(
            category_name="Kategori 2",
            category_type="product_benefit",
            description="Açıklama 2",
            relevance_score=0.8,
            suggested_keyword_ids=kw_ids,
        ),
    ]
    ai_result = SocialCategoryAIResult(
        attempt_id=start.attempt_id,
        categories=tuple(overflow_cats),
        ai_calls_used=1,
    )

    with pytest.raises(SocialCategoryPersistenceError) as exc_info:
        persist_social_categories(db_session, start=start, ai_result=ai_result, now=T0)

    assert exc_info.value.error_code == "CATEGORY_PERSISTENCE_INVALID_INPUT"
    assert exc_info.value.field == "relevance_score"
    assert exc_info.value.category_index == 0

    db_session.rollback()
    assert db_session.query(SocialCategory).filter_by(brief_id=brief.id).count() == 0
    att = db_session.get(SocialGenerationAttempt, start.attempt_id)
    assert att.status == "pending"
    assert exc_info.value.field == "relevance_score"


def test_26_existing_categories_on_pending_attempt_raises_conflict(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """26. Pending attempt varken brief altında zaten kategori varsa CATEGORY_PERSISTENCE_CONFLICT üretir."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)
    req = SocialBriefCategoriesGenerateRequest(idempotency_key="key-26")
    start = begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0)

    # Brief altına önceden kategori ekle
    existing_cat = SocialCategory(
        scoring_run_id=run.id,
        brief_id=brief.id,
        category_name="Önceden Var Olan",
        category_type="educational",
        description="Açıklama",
        relevance_score=0.9,
        suggested_keyword_ids=[kws[0].id],
    )
    db_session.add(existing_cat)
    db_session.commit()

    ai_result = _make_ai_result(attempt_id=start.attempt_id, kw_ids=(kws[0].id,))

    with pytest.raises(SocialCategoryPersistenceError) as exc_info:
        persist_social_categories(db_session, start=start, ai_result=ai_result, now=T0)
    assert exc_info.value.error_code == "CATEGORY_PERSISTENCE_CONFLICT"


def test_27_none_of_fail_closed_leaves_partial_category_rows(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """27. Fail-closed durumların hiçbirinde kısmi kategori satırı kalmaz."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)
    req = SocialBriefCategoriesGenerateRequest(idempotency_key="key-27")
    start = begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0)
    db_session.commit()

    # İkinci kategoride brief dışı keyword referansı
    kw_ids = (kws[0].id,)
    cats = [
        ValidatedSocialCategory(
            category_name="Geçerli Kategori",
            category_type="educational",
            description="Açıklama",
            relevance_score=0.9,
            suggested_keyword_ids=kw_ids,
        ),
        ValidatedSocialCategory(
            category_name="Bozuk Kategori",
            category_type="product_benefit",
            description="Açıklama",
            relevance_score=0.8,
            suggested_keyword_ids=(9999999,),  # Hatalı
        ),
    ]
    ai_result = SocialCategoryAIResult(attempt_id=start.attempt_id, categories=tuple(cats), ai_calls_used=1)

    with pytest.raises(SocialCategoryPersistenceError):
        persist_social_categories(db_session, start=start, ai_result=ai_result, now=T0)

    db_session.rollback()
    # Hiçbir satır yazılmamış olmalı
    count = db_session.query(SocialCategory).filter_by(brief_id=brief.id).count()
    assert count == 0


def test_28_error_messages_do_not_leak_raw_brand_or_category_or_keyword(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """28. Hata mesajları kategori adı, anahtar kelime metni veya marka adını sızdırmaz."""
    secret_name = "ÇOK_GİZLİ_MARKA_ADI"
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword, workspace_name=secret_name)
    brief = _create_test_brief(db_session, run.id, kws, brand_name=secret_name)
    req = SocialBriefCategoriesGenerateRequest(idempotency_key="key-28")
    start = begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0)
    db_session.commit()

    secret_cat_name = "GİZLİ_KATEGORİ_ADI_XYZ"
    duplicate_cats = [
        ValidatedSocialCategory(
            category_name=secret_cat_name,
            category_type="educational",
            description="Açıklama",
            relevance_score=0.9,
            suggested_keyword_ids=(kws[0].id,),
        ),
        ValidatedSocialCategory(
            category_name=secret_cat_name,
            category_type="product_benefit",
            description="Açıklama",
            relevance_score=0.8,
            suggested_keyword_ids=(kws[0].id,),
        ),
    ]
    ai_result = SocialCategoryAIResult(attempt_id=start.attempt_id, categories=tuple(duplicate_cats), ai_calls_used=1)

    with pytest.raises(SocialCategoryPersistenceError) as exc_info:
        persist_social_categories(db_session, start=start, ai_result=ai_result, now=T0)

    msg = str(exc_info.value)
    assert secret_cat_name not in msg
    assert secret_name not in msg


# ==================== 5. IDEMPOTENCY VE REPLAY TESTLERİ ====================


def test_29_completed_attempt_returns_existing_categories_with_already_completed_true(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """29. Completed attempt tutarlı mevcut kategorileri already_completed=True ile döndürür."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)
    req = SocialBriefCategoriesGenerateRequest(idempotency_key="key-29")
    start = begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0)
    db_session.commit()

    kw_ids = tuple(k.keyword_id for k in start.keywords)
    ai_result = _make_ai_result(attempt_id=start.attempt_id, kw_ids=kw_ids)

    # İlk persist
    res1 = persist_social_categories(db_session, start=start, ai_result=ai_result, now=T0)
    db_session.commit()
    assert res1.already_completed is False

    # İkinci persist (idempotent replay)
    res2 = persist_social_categories(db_session, start=start, ai_result=ai_result, now=T0)
    db_session.commit()

    assert res2.already_completed is True
    assert res2.category_ids == res1.category_ids
    assert len(res2.categories) == len(res1.categories)
    assert res2.categories[0].category_name == res1.categories[0].category_name

    # DB'de hala yalnızca 2 kategori bulunmalı
    total_in_db = db_session.query(SocialCategory).filter_by(brief_id=brief.id).count()
    assert total_in_db == 2


def test_30_replay_does_not_mutate_attempt_coverage_or_completed_at(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """30. Replay yolunda attempt coverage, warnings ve completed_at değiştirilmez."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)
    req = SocialBriefCategoriesGenerateRequest(idempotency_key="key-30")
    start = begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0)
    db_session.commit()

    ai_result = _make_ai_result(attempt_id=start.attempt_id, kw_ids=(kws[0].id,))
    persist_social_categories(db_session, start=start, ai_result=ai_result, now=T0)
    db_session.commit()

    att_after_first = db_session.get(SocialGenerationAttempt, start.attempt_id)
    first_completed_at = att_after_first.completed_at
    first_coverage = dict(att_after_first.coverage)

    # İkinci çağrıda farklı bir zaman damgası gönder
    t_later = T0 + timedelta(minutes=15)
    persist_social_categories(db_session, start=start, ai_result=ai_result, now=t_later)
    db_session.commit()

    att_after_second = db_session.get(SocialGenerationAttempt, start.attempt_id)
    assert att_after_second.completed_at == first_completed_at
    assert att_after_second.coverage == first_coverage


def test_31_completed_attempt_with_zero_categories_raises_inconsistent(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """31. Completed fakat kategorisi bulunmayan attempt CATEGORY_PERSISTENCE_INCONSISTENT üretir."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)
    req = SocialBriefCategoriesGenerateRequest(idempotency_key="key-31")
    start = begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0)

    # Attempt'i kategorisiz completed yap
    att_db = db_session.get(SocialGenerationAttempt, start.attempt_id)
    att_db.status = "completed"
    att_db.completed_at = T0
    db_session.commit()

    ai_result = _make_ai_result(attempt_id=start.attempt_id, kw_ids=(kws[0].id,))

    with pytest.raises(SocialCategoryPersistenceError) as exc_info:
        persist_social_categories(db_session, start=start, ai_result=ai_result, now=T0)
    assert exc_info.value.error_code == "CATEGORY_PERSISTENCE_INCONSISTENT"


def test_32_completed_attempt_with_stale_or_corrupted_categories_raises_inconsistent(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """32. Completed fakat stale kategoriye sahip attempt CATEGORY_PERSISTENCE_INCONSISTENT üretir."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)
    req = SocialBriefCategoriesGenerateRequest(idempotency_key="key-32")
    start = begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0)
    db_session.commit()

    ai_result = _make_ai_result(attempt_id=start.attempt_id, kw_ids=(kws[0].id,))
    persist_social_categories(db_session, start=start, ai_result=ai_result, now=T0)
    db_session.commit()

    # Kategorilerden birini stale yap
    cat1 = db_session.query(SocialCategory).filter_by(brief_id=brief.id).first()
    cat1.is_stale = True
    db_session.commit()

    with pytest.raises(SocialCategoryPersistenceError) as exc_info:
        persist_social_categories(db_session, start=start, ai_result=ai_result, now=T0)
    assert exc_info.value.error_code == "CATEGORY_PERSISTENCE_INCONSISTENT"


def test_32b_completed_attempt_with_2001_char_description_raises_inconsistent(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """32b. Completed attempt altındaki kategorinin açıklaması 2000 karakterden uzunsa CATEGORY_PERSISTENCE_INCONSISTENT üretir."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)
    req = SocialBriefCategoriesGenerateRequest(idempotency_key="key-32b")
    start = begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0)
    db_session.commit()

    ai_result = _make_ai_result(attempt_id=start.attempt_id, kw_ids=(kws[0].id,))
    persist_social_categories(db_session, start=start, ai_result=ai_result, now=T0)
    db_session.commit()

    # DB'deki kategorilerden birinin açıklamasını 2001 karaktere çıkar
    cat1 = db_session.query(SocialCategory).filter_by(brief_id=brief.id).first()
    cat1.description = "A" * 2001
    db_session.commit()

    with pytest.raises(SocialCategoryPersistenceError) as exc_info:
        persist_social_categories(db_session, start=start, ai_result=ai_result, now=T0)
    assert exc_info.value.error_code == "CATEGORY_PERSISTENCE_INCONSISTENT"

    db_session.rollback()
    # Satırlar mutate edilmemiş veya silinmemiş olmalı
    assert db_session.query(SocialCategory).filter_by(brief_id=brief.id).count() == 2


def test_32c_completed_attempt_with_whitespace_name_or_description_raises_inconsistent(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """32c. Completed attempt altındaki kategori adında veya açıklamasında başta/sonda boşluk varsa CATEGORY_PERSISTENCE_INCONSISTENT üretir."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)
    req = SocialBriefCategoriesGenerateRequest(idempotency_key="key-32c")
    start = begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0)
    db_session.commit()

    ai_result = _make_ai_result(attempt_id=start.attempt_id, kw_ids=(kws[0].id,))
    persist_social_categories(db_session, start=start, ai_result=ai_result, now=T0)
    db_session.commit()

    # Kategori adının başına ve sonuna boşluk ekle
    cat1 = db_session.query(SocialCategory).filter_by(brief_id=brief.id).first()
    cat1.category_name = "  Eğitim Rehberi  "
    db_session.commit()

    with pytest.raises(SocialCategoryPersistenceError) as exc_info:
        persist_social_categories(db_session, start=start, ai_result=ai_result, now=T0)
    assert exc_info.value.error_code == "CATEGORY_PERSISTENCE_INCONSISTENT"

    # Şimdi adı düzeltip açıklamaya boşluk ekle
    cat1.category_name = "Eğitim Rehberi"
    cat1.description = "  Açıklama  "
    db_session.commit()

    with pytest.raises(SocialCategoryPersistenceError) as exc_info:
        persist_social_categories(db_session, start=start, ai_result=ai_result, now=T0)
    assert exc_info.value.error_code == "CATEGORY_PERSISTENCE_INCONSISTENT"

    db_session.rollback()
    assert db_session.query(SocialCategory).filter_by(brief_id=brief.id).count() == 2


def test_32d_completed_attempt_with_categories_exceeding_max_categories_raises_inconsistent(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """32d. DB'de start.max_categories'den fazla kategori bulunması CATEGORY_PERSISTENCE_INCONSISTENT üretir."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)
    # 3 kategorili attempt oluşturalım (max_categories=3)
    req3 = SocialBriefCategoriesGenerateRequest(idempotency_key="key-32d", max_categories=3)
    start3 = begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req3, now=T0)
    db_session.commit()

    kw_ids = (kws[0].id,)
    three_cats = [
        ValidatedSocialCategory(
            category_name="Kategori 1",
            category_type="educational",
            description="Açıklama 1",
            relevance_score=0.9,
            suggested_keyword_ids=kw_ids,
        ),
        ValidatedSocialCategory(
            category_name="Kategori 2",
            category_type="product_benefit",
            description="Açıklama 2",
            relevance_score=0.8,
            suggested_keyword_ids=kw_ids,
        ),
        ValidatedSocialCategory(
            category_name="Kategori 3",
            category_type="social_proof",
            description="Açıklama 3",
            relevance_score=0.7,
            suggested_keyword_ids=kw_ids,
        ),
    ]
    ai_result3 = SocialCategoryAIResult(
        attempt_id=start3.attempt_id,
        categories=tuple(three_cats),
        ai_calls_used=1,
    )
    persist_social_categories(db_session, start=start3, ai_result=ai_result3, now=T0)
    db_session.commit()

    assert db_session.query(SocialCategory).filter_by(brief_id=brief.id).count() == 3

    # Şimdi aynı attempt için start.max_categories=2 olan bir start ile replay çağıralım
    # ai_result girdisi start_restricted ile uyumlu (2 kategori) ancak DB'de 3 kategori var
    start_restricted = replace(start3, max_categories=2)
    ai_result_restricted = SocialCategoryAIResult(
        attempt_id=start3.attempt_id,
        categories=tuple(three_cats[:2]),
        ai_calls_used=1,
    )

    with pytest.raises(SocialCategoryPersistenceError) as exc_info:
        persist_social_categories(db_session, start=start_restricted, ai_result=ai_result_restricted, now=T0)
    assert exc_info.value.error_code == "CATEGORY_PERSISTENCE_INCONSISTENT"

    db_session.rollback()
    # DB'deki 3 kategori silinmemeli veya değiştirilmemeli
    assert db_session.query(SocialCategory).filter_by(brief_id=brief.id).count() == 3


# ==================== 6. GERÇEK EŞZAMANLILIK (CONCURRENCY) TESTİ ====================


def test_33_real_concurrency_two_sessions_single_category_set(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """33. [Eşzamanlılık] İki ayrı session aynı attempt'i finalize ederse yalnız biri insert yapar, diğeri already_completed=True döner."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)
    req = SocialBriefCategoriesGenerateRequest(idempotency_key="key-33", max_categories=4)
    start = begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0)
    db_session.commit()

    kw_ids = tuple(k.keyword_id for k in start.keywords)
    ai_result = _make_ai_result(attempt_id=start.attempt_id, kw_ids=kw_ids)

    results: list[PersistedSocialCategoriesResult] = []
    errors: list[Exception] = []
    barrier = threading.Barrier(2)

    def worker(worker_id: int):
        session = SessionLocal()
        try:
            barrier.wait()
            res = persist_social_categories(session, start=start, ai_result=ai_result, now=T0)
            session.commit()
            results.append(res)
        except Exception as e:
            session.rollback()
            errors.append(e)
        finally:
            session.close()

    t1 = threading.Thread(target=worker, args=(1,), name="persist-worker-1")
    t2 = threading.Thread(target=worker, args=(2,), name="persist-worker-2")

    t1.start()
    t2.start()

    t1.join(timeout=10.0)
    t2.join(timeout=10.0)

    assert not t1.is_alive(), "Worker 1 zaman aşımına uğradı (deadlock şüphesi)"
    assert not t2.is_alive(), "Worker 2 zaman aşımına uğradı (deadlock şüphesi)"

    assert len(errors) == 0, f"Worker hata aldı: {errors}"
    assert len(results) == 2, f"İki sonuç bekleniyordu, alınan: {len(results)}"

    # Biri already_completed=False, diğeri already_completed=True olmalı
    completed_flags = sorted([r.already_completed for r in results])
    assert completed_flags == [False, True], f"Beklenen [False, True], alınan: {completed_flags}"

    # İki çağrının da döndüğü category_ids birebir aynı olmalı
    assert results[0].category_ids == results[1].category_ids

    # Veritabanında kesinlikle tek kategori kümesi (2 adet) bulunmalı
    total_in_db = db_session.query(SocialCategory).filter_by(brief_id=brief.id).count()
    assert total_in_db == 2


# ==================== 7. REGRESYON TESTLERİ ====================


def test_34_legacy_categories_with_null_brief_id_unaffected(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """34. brief_id=NULL olan legacy kategoriler persistence işlemlerinden etkilenmez."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)

    # Legacy kategori ekle (brief_id=NULL)
    legacy_cat = SocialCategory(
        scoring_run_id=run.id,
        brief_id=None,
        category_name="Legacy Eğitim",
        category_type="educational",
        description="Legacy açıklama",
        relevance_score=0.75,
        suggested_keyword_ids=[kws[0].id],
        is_stale=False,
    )
    db_session.add(legacy_cat)
    db_session.commit()

    # Yeni brief ve persistence
    brief = _create_test_brief(db_session, run.id, kws)
    req = SocialBriefCategoriesGenerateRequest(idempotency_key="key-34")
    start = begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0)
    db_session.commit()

    ai_result = _make_ai_result(attempt_id=start.attempt_id, kw_ids=(kws[0].id,))
    res = persist_social_categories(db_session, start=start, ai_result=ai_result, now=T0)
    db_session.commit()

    # Legacy kategori hala yerinde durmalı ve id'si yeni kümeye dahil olmamalı
    reloaded_legacy = db_session.get(SocialCategory, legacy_cat.id)
    assert reloaded_legacy is not None
    assert reloaded_legacy.brief_id is None
    assert reloaded_legacy.id not in res.category_ids


def test_35_result_dataclasses_are_frozen():
    """35. Sonuç dataclass'ları immutable'dır (frozen=True)."""
    cat = PersistedSocialCategory(
        id=1,
        category_name="Test",
        category_type="educational",
        description="Açıklama",
        relevance_score=0.9,
        suggested_keyword_ids=(10, 20),
    )
    with pytest.raises(Exception):  # FrozenInstanceError
        cat.category_name = "Değişti"  # type: ignore

    res = PersistedSocialCategoriesResult(
        brief_id=1,
        scoring_run_id=2,
        attempt_id=3,
        category_ids=(1,),
        categories=(cat,),
        already_completed=False,
    )
    with pytest.raises(Exception):  # FrozenInstanceError
        res.already_completed = True  # type: ignore
