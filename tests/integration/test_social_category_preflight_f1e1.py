# -*- coding: utf-8 -*-
"""Integration tests for Social Brief Categories Preflight, Brief Lock and Attempt Initialization (F1-E.1).

Tüm testler gerçek PostgreSQL test DB'sini ve SQLAlchemy session'ını kullanır.
Global kilit sırası, attempt yaşam döngüsü, same-key idempotency, retry kısıtları,
brief lock kalıcılığı ve fail-closed sınır kontrolleri uçtan uca doğrulanır.
"""
from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError
from sqlalchemy import event, text

from app.config import settings
from app.core.social import (
    CategoryKeywordSnapshot,
    SocialCategoryFlowError,
    SocialCategoryGenerationStart,
    begin_social_category_generation,
)
from app.database.models import (
    ChannelPool,
    SocialBrief,
    SocialBriefKeyword,
    SocialBriefTarget,
    SocialCategory,
    SocialGenerationAttempt,
)
from app.generators.social.attempt_state import (
    AttemptConflictError,
    AttemptNotWritableError,
)
from app.schemas.social_brief import SocialBriefCategoriesGenerateRequest


@pytest.fixture
def enable_flag(monkeypatch):
    """Test süresince feature flag'i aktif kılar, test bitiminde otomatik geri alır."""
    monkeypatch.setattr(settings, "ENABLE_SOCIAL_BRIEF_FLOW", True)
    yield


@pytest.fixture(autouse=True)
def bind_dependencies_get_db(db_session):
    """FastAPI TestClient'ın app.dependencies.get_db çağrılarını test db_session'ına bağlar."""
    from app.dependencies import get_db
    from app.main import app

    def _override():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = _override
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_db, None)


def _setup_fresh_environment(
    db_session,
    make_workspace,
    make_scoring_run,
    make_keyword,
    num_kws: int = 3,
    workspace_name: str = "Preflight Brand",
):
    """Merkezi freshness sözleşmesini karşılayan taze bir workspace/run/pool kurgusu oluşturur."""
    workspace = make_workspace(name=workspace_name, status="confirmed")
    run = make_scoring_run(
        brand_profile_id=workspace.id,
        status="completed",
        algorithm_version="v3",
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
    db_session,
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


# ==================== ENTEGRASYON TESTLERİ (1 - 37) ====================

def test_01_valid_brief_creates_pending_categories_attempt(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """1. Geçerli brief için pending categories attempt oluşturulur."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)
    req = SocialBriefCategoriesGenerateRequest(idempotency_key="key-01")

    res = begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req)
    db_session.commit()

    assert res.attempt_status == "pending"
    assert res.attempt_created is True
    assert res.max_categories == 4


def test_02_attempt_stage_is_categories(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """2. attempt.stage == 'categories'."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)
    req = SocialBriefCategoriesGenerateRequest(idempotency_key="key-02")

    res = begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req)
    db_session.commit()

    att = db_session.get(SocialGenerationAttempt, res.attempt_id)
    assert att.stage == "categories"


def test_03_attempt_task_id_is_null(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """3. attempt.task_id null'dır (senkron preflight)."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)
    req = SocialBriefCategoriesGenerateRequest(idempotency_key="key-03")

    res = begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req)
    db_session.commit()

    att = db_session.get(SocialGenerationAttempt, res.attempt_id)
    assert att.task_id is None


def test_04_requested_target_and_idea_ids_are_null(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """4. requested target/idea ID alanları null'dır."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)
    req = SocialBriefCategoriesGenerateRequest(idempotency_key="key-04")

    res = begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req)
    db_session.commit()

    att = db_session.get(SocialGenerationAttempt, res.attempt_id)
    assert att.requested_target_ids is None
    assert att.requested_idea_ids is None


def test_05_brief_locked_at_set_in_same_transaction(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """5. brief.locked_at aynı transaction'da dolar."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)
    assert brief.locked_at is None

    fixed_now = datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc)
    req = SocialBriefCategoriesGenerateRequest(idempotency_key="key-05")

    res = begin_social_category_generation(
        db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=fixed_now
    )
    assert res.locked_at == fixed_now
    assert brief.locked_at == fixed_now


def test_06_attempt_and_locked_at_committed_together(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """6. attempt ve locked_at commit sonrası birlikte kalıcıdır."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)
    req = SocialBriefCategoriesGenerateRequest(idempotency_key="key-06")

    res = begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req)
    db_session.commit()

    db_session.expire_all()
    reloaded_brief = db_session.get(SocialBrief, brief.id)
    reloaded_attempt = db_session.get(SocialGenerationAttempt, res.attempt_id)

    assert reloaded_brief.locked_at is not None
    assert reloaded_attempt is not None
    assert reloaded_attempt.brief_id == brief.id


def test_07_caller_rollback_reverts_attempt_and_locked_at(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """7. Çağıran rollback yaptığında yeni attempt ve locked_at birlikte geri alınır."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)
    req = SocialBriefCategoriesGenerateRequest(idempotency_key="key-07")

    res = begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req)
    db_session.rollback()

    db_session.expire_all()
    reloaded_brief = db_session.get(SocialBrief, brief.id)
    reloaded_attempt = db_session.get(SocialGenerationAttempt, res.attempt_id)

    assert reloaded_brief.locked_at is None
    assert reloaded_attempt is None


def test_08_same_key_returns_same_attempt(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """8. Aynı key aynı attempt'i döndürür ve same-key replay'de max_categories=None olur."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)
    req = SocialBriefCategoriesGenerateRequest(idempotency_key="key-08")

    res1 = begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req)
    db_session.commit()
    assert res1.attempt_created is True
    assert res1.max_categories == 4

    res2 = begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req)
    db_session.commit()

    assert res1.attempt_id == res2.attempt_id
    assert res2.attempt_created is False
    assert res2.max_categories is None


def test_08b_same_key_different_max_categories_does_not_create_new_attempt_and_returns_none(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """Aynı key + farklı max_categories: aynı attempt, yeni attempt yok, attempt_created=False, max_categories=None, locked_at değişmez, kategori oluşturulmaz."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)

    t1 = datetime(2026, 9, 24, 10, 0, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 9, 24, 11, 0, 0, tzinfo=timezone.utc)

    req1 = SocialBriefCategoriesGenerateRequest(idempotency_key="key-replay-diff", max_categories=2)
    res1 = begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req1, now=t1)
    db_session.commit()
    assert res1.attempt_created is True
    assert res1.max_categories == 2
    assert res1.locked_at == t1

    req2 = SocialBriefCategoriesGenerateRequest(idempotency_key="key-replay-diff", max_categories=6)
    res2 = begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req2, now=t2)
    db_session.commit()

    assert res2.attempt_id == res1.attempt_id
    assert res2.attempt_created is False
    assert res2.max_categories is None
    assert res2.locked_at == t1
    assert db_session.query(SocialGenerationAttempt).filter_by(brief_id=brief.id).count() == 1
    assert db_session.query(SocialCategory).filter_by(brief_id=brief.id).count() == 0



def test_09_same_key_attempt_created_is_false(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """9. Aynı key tekrarında attempt_created false olur."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)
    req = SocialBriefCategoriesGenerateRequest(idempotency_key="key-09")

    res1 = begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req)
    db_session.commit()
    assert res1.attempt_created is True

    res2 = begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req)
    db_session.commit()
    assert res2.attempt_created is False


def test_10_same_key_preserves_locked_at(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """10. Aynı key tekrarında locked_at değişmez (asla ileri taşınmaz)."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)

    t1 = datetime(2026, 9, 24, 10, 0, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 9, 24, 11, 0, 0, tzinfo=timezone.utc)
    req = SocialBriefCategoriesGenerateRequest(idempotency_key="key-10")

    res1 = begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=t1)
    db_session.commit()

    res2 = begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=t2)
    db_session.commit()

    assert res1.locked_at == t1
    assert res2.locked_at == t1


def test_11_active_attempt_with_different_key_raises_conflict(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """11. Farklı key ile aktif attempt varken AttemptConflictError fırlatılır."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)

    req1 = SocialBriefCategoriesGenerateRequest(idempotency_key="key-11a")
    begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req1)
    db_session.commit()

    req2 = SocialBriefCategoriesGenerateRequest(idempotency_key="key-11b")
    with pytest.raises(AttemptConflictError) as exc_info:
        begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req2)

    assert exc_info.value.error_code == "ATTEMPT_CONFLICT"


def test_12_failed_terminal_attempt_allows_new_key_new_attempt(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """12. Failed terminal attempt sonrası yeni key yeni attempt açabilir ve yeni request max_categories taşınır."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)

    # 1. İlk attempt oluşturulur ve fail edilir
    req1 = SocialBriefCategoriesGenerateRequest(idempotency_key="key-12a", max_categories=3)
    res1 = begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req1)
    assert res1.max_categories == 3
    att1 = db_session.get(SocialGenerationAttempt, res1.attempt_id)
    att1.status = "failed"
    att1.reason_code = "llm_error"
    db_session.commit()

    # 2. Yeni key ile ikinci attempt oluşturulabilir
    req2 = SocialBriefCategoriesGenerateRequest(idempotency_key="key-12b", max_categories=5)
    res2 = begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req2)
    db_session.commit()

    assert res2.attempt_id != res1.attempt_id
    assert res2.attempt_created is True
    assert res2.attempt_status == "pending"
    assert res2.max_categories == 5


def test_13_expired_same_key_returns_same_row_as_failed_worker_lost(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """13. Expired same-key aynı failed/worker_lost satırı döndürür ve max_categories=None olur."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)

    t0 = datetime(2026, 9, 24, 10, 0, 0, tzinfo=timezone.utc)
    req = SocialBriefCategoriesGenerateRequest(idempotency_key="key-13", max_categories=4)
    res1 = begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=t0)
    assert res1.max_categories == 4
    att1 = db_session.get(SocialGenerationAttempt, res1.attempt_id)
    att1.lease_expires_at = t0 + timedelta(seconds=10)
    db_session.commit()

    # 20 saniye sonra aynı key ile çağrı (lease dolmuş)
    t_later = t0 + timedelta(seconds=20)
    res2 = begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=t_later)
    db_session.commit()

    assert res2.attempt_id == res1.attempt_id
    assert res2.attempt_created is False
    assert res2.attempt_status == "failed"
    assert res2.max_categories is None

    att_reloaded = db_session.get(SocialGenerationAttempt, res1.attempt_id)
    assert att_reloaded.status == "failed"
    assert att_reloaded.reason_code == "worker_lost"



def test_14_expired_same_key_does_not_create_new_row(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """14. Expired same-key otomatik yeni satır oluşturmaz."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)

    t0 = datetime(2026, 9, 24, 10, 0, 0, tzinfo=timezone.utc)
    req = SocialBriefCategoriesGenerateRequest(idempotency_key="key-14")
    res1 = begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=t0)
    att1 = db_session.get(SocialGenerationAttempt, res1.attempt_id)
    att1.lease_expires_at = t0 + timedelta(seconds=10)
    db_session.commit()

    t_later = t0 + timedelta(seconds=20)
    begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=t_later)
    db_session.commit()

    total_attempts = db_session.query(SocialGenerationAttempt).filter_by(brief_id=brief.id).count()
    assert total_attempts == 1


def test_15_stale_brief_rejected_with_brief_stale(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """15. Stale brief BRIEF_STALE ile reddedilir."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws, is_stale=True)
    req = SocialBriefCategoriesGenerateRequest(idempotency_key="key-15")

    with pytest.raises(AttemptNotWritableError) as exc_info:
        begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req)

    assert exc_info.value.error_code == "BRIEF_STALE"


def test_16_assignment_version_mismatch_rejected_with_assignment_changed(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """16. Assignment-version mismatch ASSIGNMENT_CHANGED ile reddedilir."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    # Brief version 1, run version 2
    run.channel_assignment_version = 2
    db_session.commit()

    brief = _create_test_brief(db_session, run.id, kws, channel_assignment_version=1)
    req = SocialBriefCategoriesGenerateRequest(idempotency_key="key-16")

    with pytest.raises(AttemptNotWritableError) as exc_info:
        begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req)

    assert exc_info.value.error_code == "ASSIGNMENT_CHANGED"


def test_17_cross_workspace_brief_returns_brief_not_found(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """17. Cross-workspace brief BRIEF_NOT_FOUND fırlatır."""
    ws1, run1, kws1 = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword, workspace_name="WS1")
    ws2, run2, kws2 = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword, workspace_name="WS2")
    brief1 = _create_test_brief(db_session, run1.id, kws1)

    req = SocialBriefCategoriesGenerateRequest(idempotency_key="key-17")
    with pytest.raises(SocialCategoryFlowError) as exc_info:
        begin_social_category_generation(db_session, brief_id=brief1.id, brand_profile_id=ws2.id, request=req)

    assert exc_info.value.error_code == "BRIEF_NOT_FOUND"


def test_18_non_existent_brief_returns_brief_not_found(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """18. Olmayan brief BRIEF_NOT_FOUND fırlatır."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    req = SocialBriefCategoriesGenerateRequest(idempotency_key="key-18")

    with pytest.raises(SocialCategoryFlowError) as exc_info:
        begin_social_category_generation(db_session, brief_id=999999, brand_profile_id=ws.id, request=req)

    assert exc_info.value.error_code == "BRIEF_NOT_FOUND"


def test_19_archived_workspace_brief_returns_brief_not_found(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """19. Arşivlenmiş workspace altındaki brief BRIEF_NOT_FOUND fırlatır."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)

    ws.deleted_at = datetime.now(timezone.utc)
    db_session.commit()

    req = SocialBriefCategoriesGenerateRequest(idempotency_key="key-19")
    with pytest.raises(SocialCategoryFlowError) as exc_info:
        begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req)

    assert exc_info.value.error_code == "BRIEF_NOT_FOUND"


def test_20_new_attempt_with_existing_categories_raises_categories_already_generated(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """20. Yeni attempt açılırken mevcut kategori varsa CATEGORIES_ALREADY_GENERATED fırlatılır."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)

    # Mevcut kategori ekle
    cat = SocialCategory(
        scoring_run_id=run.id,
        brief_id=brief.id,
        category_name="Eğitici İçerikler",
        category_type="educational",
    )
    db_session.add(cat)
    db_session.commit()

    req = SocialBriefCategoriesGenerateRequest(idempotency_key="key-20")
    with pytest.raises(SocialCategoryFlowError) as exc_info:
        begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req)

    assert exc_info.value.error_code == "CATEGORIES_ALREADY_GENERATED"


def test_21_caller_rollback_after_categories_already_generated_leaves_zero_attempts(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """21. CATEGORIES_ALREADY_GENERATED sonrası caller rollback ile kısmi attempt ve lock kalmaz."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)

    cat = SocialCategory(
        scoring_run_id=run.id,
        brief_id=brief.id,
        category_name="Eğitici İçerikler",
        category_type="educational",
    )
    db_session.add(cat)
    db_session.commit()

    req = SocialBriefCategoriesGenerateRequest(idempotency_key="key-21")
    try:
        begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req)
    except SocialCategoryFlowError:
        db_session.rollback()

    db_session.expire_all()
    assert db_session.query(SocialGenerationAttempt).filter_by(brief_id=brief.id).count() == 0
    reloaded_brief = db_session.get(SocialBrief, brief.id)
    assert reloaded_brief.locked_at is None


def test_22_same_completed_key_readable_idempotently_with_existing_categories(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """22. Aynı completed key mevcut kategoriler varken idempotent okunabilir."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)

    # 1. Attempt oluşturulup tamamlanır
    req = SocialBriefCategoriesGenerateRequest(idempotency_key="key-22", max_categories=5)
    res1 = begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req)
    assert res1.max_categories == 5
    att1 = db_session.get(SocialGenerationAttempt, res1.attempt_id)
    att1.status = "completed"

    # Kategoriler kaydedilir
    cat = SocialCategory(
        scoring_run_id=run.id,
        brief_id=brief.id,
        category_name="Eğitici İçerikler",
        category_type="educational",
    )
    db_session.add(cat)
    db_session.commit()

    # 2. Aynı key ile tekrar çağrılır
    res2 = begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req)
    db_session.commit()

    assert res2.attempt_id == res1.attempt_id
    assert res2.attempt_created is False
    assert res2.attempt_status == "completed"
    assert res2.max_categories is None


def test_23_existing_categories_are_not_mutated_or_deleted(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """23. Mevcut kategoriler silinmez veya değiştirilmez."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)

    req = SocialBriefCategoriesGenerateRequest(idempotency_key="key-23")
    res = begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req)
    att = db_session.get(SocialGenerationAttempt, res.attempt_id)
    att.status = "completed"

    cat = SocialCategory(
        scoring_run_id=run.id,
        brief_id=brief.id,
        category_name="Orijinal Kategori",
        category_type="product_benefit",
        description="Orijinal Açıklama",
    )
    db_session.add(cat)
    db_session.commit()

    # Tekrar same-key çağrısı
    begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req)
    db_session.commit()

    reloaded_cat = db_session.get(SocialCategory, cat.id)
    assert reloaded_cat.category_name == "Orijinal Kategori"
    assert reloaded_cat.description == "Orijinal Açıklama"


def test_24_keyword_snapshot_order_preserves_position(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """24. Keyword snapshot sırası position ile korunur."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword, num_kws=3)
    brief = _create_test_brief(db_session, run.id, kws)
    req = SocialBriefCategoriesGenerateRequest(idempotency_key="key-24")

    res = begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req)
    db_session.commit()

    positions = [kw.position for kw in res.keywords]
    assert positions == [0, 1, 2]


def test_25_snapshot_text_carried_verbatim(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """25. Snapshot metni değiştirilmeden taşınır (normalizasyon yapılmaz)."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword, num_kws=1)
    brief = _create_test_brief(db_session, run.id, kws)

    # Keyword snapshot'ını özel bir string yap
    kw_row = db_session.query(SocialBriefKeyword).filter_by(brief_id=brief.id).first()
    kw_row.keyword_snapshot = "  Büyük/Küçük Türkçe İÇERİK  "
    db_session.commit()

    req = SocialBriefCategoriesGenerateRequest(idempotency_key="key-25")
    res = begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req)
    db_session.commit()

    assert res.keywords[0].keyword_snapshot == "  Büyük/Küçük Türkçe İÇERİK  "


def test_26_null_keyword_id_fails_closed(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """26. Null keyword_id fail-closed (BRIEF_NOT_FOUND fırlatır)."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)

    kw_row = db_session.query(SocialBriefKeyword).filter_by(brief_id=brief.id).first()
    kw_row.keyword_id = None
    db_session.commit()

    req = SocialBriefCategoriesGenerateRequest(idempotency_key="key-26")
    with pytest.raises(SocialCategoryFlowError) as exc_info:
        begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req)

    assert exc_info.value.error_code == "BRIEF_NOT_FOUND"


def test_27_empty_keyword_snapshot_fails_closed(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """27. Boş veya whitespace-only keyword snapshot fail-closed."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)

    kw_row = db_session.query(SocialBriefKeyword).filter_by(brief_id=brief.id).first()
    kw_row.keyword_snapshot = "   "
    db_session.commit()

    req = SocialBriefCategoriesGenerateRequest(idempotency_key="key-27")
    with pytest.raises(SocialCategoryFlowError) as exc_info:
        begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req)

    assert exc_info.value.error_code == "BRIEF_NOT_FOUND"


def test_28_discontinuous_position_fails_closed(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """28. Eksik veya kesintili position fail-closed."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword, num_kws=2)
    brief = _create_test_brief(db_session, run.id, kws)

    # 0 ve 1 yerine 0 ve 2 yap
    kws_in_db = db_session.query(SocialBriefKeyword).filter_by(brief_id=brief.id).order_by(SocialBriefKeyword.id.asc()).all()
    kws_in_db[1].position = 2
    db_session.commit()

    req = SocialBriefCategoriesGenerateRequest(idempotency_key="key-28")
    with pytest.raises(SocialCategoryFlowError) as exc_info:
        begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req)

    assert exc_info.value.error_code == "BRIEF_NOT_FOUND"


def test_29_result_dataclasses_are_immutable(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """29. Sonuç dataclass'ları immutable'dır (frozen=True)."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)
    req = SocialBriefCategoriesGenerateRequest(idempotency_key="key-29")

    res = begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req)
    db_session.commit()

    with pytest.raises(FrozenInstanceError):
        res.attempt_id = 999

    with pytest.raises(FrozenInstanceError):
        res.keywords[0].keyword_id = 888


def test_30_request_is_strict_and_extra_forbid():
    """30. Request strict, extra-forbid, default=4 ve ge=2, le=6 sınırlarını doğrular."""
    # default=4
    req_default = SocialBriefCategoriesGenerateRequest(idempotency_key="key")
    assert req_default.max_categories == 4

    # boundary values 2 and 6 accepted
    req_min = SocialBriefCategoriesGenerateRequest(idempotency_key="key", max_categories=2)
    assert req_min.max_categories == 2
    req_max = SocialBriefCategoriesGenerateRequest(idempotency_key="key", max_categories=6)
    assert req_max.max_categories == 6

    # out of bounds: < 2 (e.g. 1) rejected
    with pytest.raises(ValidationError):
        SocialBriefCategoriesGenerateRequest(idempotency_key="key", max_categories=1)

    # out of bounds: > 6 (e.g. 7) rejected
    with pytest.raises(ValidationError):
        SocialBriefCategoriesGenerateRequest(idempotency_key="key", max_categories=7)

    # string max_categories reddedilir
    with pytest.raises(ValidationError):
        SocialBriefCategoriesGenerateRequest(idempotency_key="key", max_categories="4")

    # float max_categories reddedilir
    with pytest.raises(ValidationError):
        SocialBriefCategoriesGenerateRequest(idempotency_key="key", max_categories=4.0)

    # bool max_categories reddedilir
    with pytest.raises(ValidationError):
        SocialBriefCategoriesGenerateRequest(idempotency_key="key", max_categories=True)

    # extra field reddedilir
    with pytest.raises(ValidationError):
        SocialBriefCategoriesGenerateRequest(idempotency_key="key", brief_id=123)


def test_31_whitespace_only_idempotency_key_rejected():
    """31. Whitespace-only idempotency key reddedilir."""
    with pytest.raises(ValidationError):
        SocialBriefCategoriesGenerateRequest(idempotency_key="    ")


def test_32_naive_datetime_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """32. Naive datetime reddedilir."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)
    req = SocialBriefCategoriesGenerateRequest(idempotency_key="key-32")

    naive_now = datetime(2026, 9, 24, 10, 0, 0)  # tzinfo=None
    with pytest.raises(ValueError) as exc_info:
        begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=naive_now)

    assert "timezone-aware" in str(exc_info.value)


def test_33_service_does_not_call_commit_or_rollback(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """33. Servis commit veya rollback çağırmaz."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)
    req = SocialBriefCategoriesGenerateRequest(idempotency_key="key-33")

    with patch.object(db_session, "commit") as mock_c, patch.object(db_session, "rollback") as mock_r:
        res = begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req)
        assert res.attempt_id > 0
        assert mock_c.call_count == 0
        assert mock_r.call_count == 0


def test_34_service_does_not_use_ai_or_http_exception():
    """34. Servis modülü AI veya HTTPException bağımlılığı taşımaz."""
    import app.core.social.category_flow as mod
    import inspect

    src = inspect.getsource(mod)
    assert "AIService" not in src
    assert "HTTPException" not in src
    assert "get_ai" not in src


def test_35_lock_order_is_brand_profile_scoring_run_social_brief_attempt(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """35. Kilit sırası BrandProfile -> ScoringRun -> SocialBrief -> Attempt'tir."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)
    req = SocialBriefCategoriesGenerateRequest(idempotency_key="key-35")

    captured_locks: list[str] = []

    def _query_catcher(conn, cursor, statement, parameters, context, executemany):
        stmt_lower = statement.lower()
        if "for update" in stmt_lower:
            if "brand_profiles" in stmt_lower:
                captured_locks.append("BrandProfile")
            elif "scoring_runs" in stmt_lower:
                captured_locks.append("ScoringRun")
            elif "social_briefs" in stmt_lower:
                captured_locks.append("SocialBrief")
            elif "social_generation_attempts" in stmt_lower:
                captured_locks.append("SocialGenerationAttempt")

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", _query_catcher)
    try:
        begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req)
        db_session.commit()
    finally:
        event.remove(engine, "before_cursor_execute", _query_catcher)

    # Sıralamada ilk görünüşleri kontrol et
    bp_idx = captured_locks.index("BrandProfile")
    sr_idx = captured_locks.index("ScoringRun")
    sb_idx = captured_locks.index("SocialBrief")

    assert bp_idx < sr_idx < sb_idx


def test_36_no_social_category_rows_created(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """36. Bu fazda SocialCategory satırı oluşturulmaz."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)
    req = SocialBriefCategoriesGenerateRequest(idempotency_key="key-36")

    begin_social_category_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req)
    db_session.commit()

    assert db_session.query(SocialCategory).count() == 0


def test_37_existing_brief_endpoints_unaffected(
    client, db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """37. Mevcut POST/GET/list brief endpointleri etkilenmez (regresyon yok)."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)

    # 1. Single GET
    r_single = client.get(f"/api/v1/generation/social/briefs/{brief.id}", params={"brand_profile_id": ws.id})
    assert r_single.status_code == 200

    # 2. List GET
    r_list = client.get(
        "/api/v1/generation/social/briefs",
        params={"scoring_run_id": run.id, "brand_profile_id": ws.id},
    )
    assert r_list.status_code == 200

    # 3. POST Brief
    payload = {
        "scoring_run_id": run.id,
        "keyword_ids": [kws[0].id],
        "targets": [{"platform": "instagram", "content_format": "post"}],
        "brand_name": "API Brand",
    }
    r_post = client.post("/api/v1/generation/social/briefs", params={"brand_profile_id": ws.id}, json=payload)
    assert r_post.status_code == 201
