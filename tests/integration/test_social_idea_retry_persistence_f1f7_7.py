# -*- coding: utf-8 -*-
"""MİKRO FAZ F1-F.7.7 — Ideas Retry Persistence ve Atomik Başarı/Partial Finalization Entegrasyon Testleri.

Tüm testler gerçek PostgreSQL test veritabanını ve SQLAlchemy oturumunu kullanır.
Test edilen alanlar:
1. Tek missing target başarıyla insert edilir ve completed olur.
2. Birden fazla kategori/hedef başarıyla insert edilir.
3. Önceden persisted fikirler korunur ve değişmez.
4. Yalnız missing target'lara yeni fikir yazılır.
5. accepted_target_ids canonical sıradadır.
6. Tam başarıda coverage.generated doğru güncellenir.
7. Tam başarıda warnings boş, lease temiz ve completed_at yazılıdır.
8. Kategorilerden biri eksikse mevcut başarılı kategori kaydedilir ve attempt partial olur.
9. Partial warnings yalnız unfilled target'ları içerir.
10. Partial coverage yalnız gerçekten kabul edilen target'ları içerir.
11. Boş category_results reddedilir ve DB'ye yazılmaz.
12. Aynı category result iki kez reddedilir.
13. Yanlış attempt_id taşıyan AI result reddedilir.
14. Plan dışı category reddedilir.
15. Plan dışı target reddedilir.
16. Persisted target için yeni fikir reddedilir.
17. Aynı target için iki fikir reddedilir.
18. Kategori içi eksik target sonucu reddedilir.
19. Brief dışı keyword reddedilir.
20. Platform/format mismatch reddedilir.
21. Geçersiz title/description/trend_alignment reddedilir.
22. Aşırı büyük numeric değer ham OverflowError sızdırmaz.
23. Brief stale olduğunda hiçbir fikir yazılmaz.
24. Assignment version değiştiğinde hiçbir fikir yazılmaz.
25. Lease dolduğunda geç worker çıktısı yazılmaz.
26. Farklı task_id çıktısı yazılmaz.
27. Missing target canlı DB'de başka fikirle doldurulmuşsa conflict ve sıfır insert.
28. Baseline persisted target artık DB'de yoksa inconsistent ve sıfır insert.
29. Hata sonrasında rollback ile attempt ve fikirler eski hâline döner.
30. Completed replay boş sonuçlarla idempotent döner.
31. Completed replay dolu category_results ile reddedilir.
32. Completed replay bozuk generated coverage ile reddedilir.
33. Completed replay eksik DB fikriyle reddedilir.
34. Completed replay hiçbir satırı mutate etmez.
35. Partial attempt tekrar finalize edilemez.
36. Normal persist_social_ideas regresyona uğramaz.
37. Fonksiyon commit/rollback çağırmaz.
38. Bütün insertler ve attempt finalization aynı transaction'dadır.
39. İki session eşzamanlı finalize denemesinde kopya fikir oluşmaz.
40. Source snapshot bozuksa fail-closed.
41. Retry snapshot bozuksa fail-closed.
42. Canonical target sırası değişmişse fail-closed.
43. Hata mesajlarında ham ID, SQL veya AI metni sızmaz.
44. Existing stale idea missing hedefi dolu saydırmaz.
45. Partial sonucu takip eden yeni retry preflight yalnız kalan hedefleri planlar.
"""
from __future__ import annotations

import copy
import threading
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.config import settings
from app.core.social.idea_contract import ValidatedSocialIdea
from app.core.social.idea_persistence import (
    PersistedSocialIdeasResult,
    persist_social_ideas,
)
from app.core.social.idea_planner import build_social_idea_generation_plan
from app.core.social.idea_retry_flow import begin_social_idea_retry
from app.core.social.idea_retry_persistence import (
    PersistedSocialIdeaRetryResult,
    SocialIdeaRetryPersistenceError,
    persist_social_idea_retry_results,
    persist_social_ideas_retry,
)
from app.core.social.idea_retry_snapshot import extract_social_idea_retry_plan_snapshot
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
)
from app.generators.social.brief_idea_generator import SocialIdeaAIResult
from app.schemas.social_brief import SocialBriefIdeasRetryRequest

T0 = datetime(2026, 9, 25, 12, 0, 0, tzinfo=timezone.utc)


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
    workspace_name: str = "Retry Persistence Brand",
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
    channel_assignment_version: int = 1,
    is_stale: bool = False,
    locked_at: datetime | None = None,
) -> SocialBrief:
    """Test için SocialBrief ve bağlı keyword/target kayıtlarını oluşturur."""
    brief = SocialBrief(
        scoring_run_id=run_id,
        brand_name_snapshot="Test Marka",
        brand_context_snapshot="Test Context",
        channel_assignment_version=channel_assignment_version,
        format_matrix_version="v1",
        is_stale=is_stale,
        locked_at=locked_at or T0,
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
    workspace_name: str = "Retry Persistence Brand",
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
        targets_data=targets_data,
    )

    categories = []
    for i in range(num_categories):
        cat = SocialCategory(
            brief_id=brief.id,
            scoring_run_id=run.id,
            category_name=f"Kategori {i + 1}",
            category_type="educational",
            description=f"Açıklama {i + 1} için içerik yönü.",
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


def _setup_ready_retry_attempt(
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
    task_id: str = "task-retry-001",
    persist_first_target: bool = True,
    targets_data: list[tuple[str, str]] | None = None,
    num_categories: int = 2,
    ideas_per_category: int = 3,
):
    """Worker testleri için preflight edilmiş bir ideas_retry attempt'i hazırlar ve worker tarafından claim eder."""
    ws, run, kws, brief, cats, targets = _setup_environment_with_categories(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        num_categories=num_categories,
        targets_data=targets_data,
    )
    source_att = _create_source_ideas_attempt(
        db_session, brief, cats, targets, ideas_per_category=ideas_per_category
    )

    if persist_first_target:
        _create_test_idea(db_session, brief, cats[0], targets[0], kws[0].id)

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

    retry_att, _, _, _ = claim_ideas_retry_attempt_for_worker(
        db_session,
        attempt_id=start.attempt_id,
        task_id=task_id,
        now=T0,
    )
    db_session.commit()

    return ws, run, kws, brief, cats, targets, source_att, start, retry_att


def _build_valid_retry_results(
    start_plan,
    attempt_id: int,
    targets: list[SocialBriefTarget],
    kws: list,
) -> tuple[SocialIdeaAIResult, ...]:
    """Retry planına göre tam ve geçerli SocialIdeaAIResult listesi üretir."""
    target_map = {t.id: t for t in targets}
    assignments_by_cat: dict[int, list[int]] = {}
    for asgn in start_plan.assignments:
        assignments_by_cat.setdefault(asgn.category_id, []).append(asgn.target_id)

    results: list[SocialIdeaAIResult] = []
    for cat_id, tids in assignments_by_cat.items():
        cat_ideas: list[ValidatedSocialIdea] = []
        for tid in tids:
            t = target_map[tid]
            cat_ideas.append(
                ValidatedSocialIdea(
                    target_id=tid,
                    primary_keyword_id=kws[0].id,
                    target_platform=t.platform,
                    content_format=t.content_format,
                    idea_title=f"Retry Başlık {tid}",
                    idea_description=f"Retry açıklama metni {tid}",
                    trend_alignment=0.85,
                )
            )
        results.append(
            SocialIdeaAIResult(
                attempt_id=attempt_id,
                category_id=cat_id,
                ideas=tuple(cat_ideas),
                ai_calls_used=1,
            )
        )
    return tuple(results)


# ==================== TEST SENARYOLARI (1 - 45) ====================


def test_01_single_missing_target_completed(
    enable_flag, db_session, make_workspace, make_scoring_run, make_keyword
):
    """1. Tek missing target başarıyla insert edilir ve completed olur."""
    # 2 target: T1 persist edildi, T2 missing
    targets_data = [("instagram", "post"), ("twitter", "thread")]
    ws, run, kws, brief, cats, targets, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-01",
        persist_first_target=True,
        targets_data=targets_data,
        num_categories=1,
    )
    assert len(start.plan.missing_target_ids) == 1
    missing_tid = start.plan.missing_target_ids[0]

    results = _build_valid_retry_results(start.plan, retry_att.id, targets, kws)
    now_time = T0 + timedelta(seconds=60)

    res = persist_social_idea_retry_results(
        db_session,
        attempt_id=retry_att.id,
        task_id="task-01",
        category_results=results,
        now=now_time,
    )

    assert isinstance(res, PersistedSocialIdeaRetryResult)
    assert res.status == "completed"
    assert res.newly_persisted_count == 1
    assert res.accepted_target_ids == (missing_tid,)
    assert res.unfilled_target_ids == ()
    assert res.already_completed is False

    # DB kontrolü
    db_session.refresh(retry_att)
    assert retry_att.status == "completed"
    assert retry_att.reason_code is None
    assert retry_att.error_message is None
    assert retry_att.warnings == []
    assert retry_att.completed_at == now_time
    assert retry_att.lease_expires_at is None

    # Toplam 2 fikir olmalı (T1 ve T2)
    db_ideas = db_session.query(SocialIdea).filter_by(brief_id=brief.id).all()
    assert len(db_ideas) == 2


def test_02_multiple_categories_and_targets_completed(
    enable_flag, db_session, make_workspace, make_scoring_run, make_keyword
):
    """2. Birden fazla kategori/hedef başarıyla insert edilir."""
    targets_data = [("instagram", "post"), ("twitter", "thread"), ("linkedin", "post")]
    ws, run, kws, brief, cats, targets, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-02",
        persist_first_target=True,
        targets_data=targets_data,
        num_categories=2,
    )
    # T1 persisted, T2 and T3 missing -> 2 categories assigned
    assert len(start.plan.missing_target_ids) == 2

    results = _build_valid_retry_results(start.plan, retry_att.id, targets, kws)
    res = persist_social_idea_retry_results(
        db_session,
        attempt_id=retry_att.id,
        task_id="task-02",
        category_results=results,
        now=T0,
    )

    assert res.status == "completed"
    assert res.newly_persisted_count == 2
    assert set(res.accepted_target_ids) == set(start.plan.missing_target_ids)
    assert res.unfilled_target_ids == ()


def test_03_previously_persisted_ideas_preserved_unchanged(
    enable_flag, db_session, make_workspace, make_scoring_run, make_keyword
):
    """3. Önceden persisted fikirler korunur ve değişmez."""
    targets_data = [("instagram", "post"), ("twitter", "thread")]
    ws, run, kws, brief, cats, targets, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-03",
        persist_first_target=True,
        targets_data=targets_data,
    )
    initial_idea = db_session.query(SocialIdea).filter_by(brief_target_id=targets[0].id).first()
    init_id = initial_idea.id
    init_title = initial_idea.idea_title
    init_desc = initial_idea.idea_description
    init_created = initial_idea.created_at

    results = _build_valid_retry_results(start.plan, retry_att.id, targets, kws)
    persist_social_idea_retry_results(
        db_session,
        attempt_id=retry_att.id,
        task_id="task-03",
        category_results=results,
        now=T0,
    )

    db_session.refresh(initial_idea)
    assert initial_idea.id == init_id
    assert initial_idea.idea_title == init_title
    assert initial_idea.idea_description == init_desc
    assert initial_idea.created_at == init_created
    assert initial_idea.is_stale is False


def test_04_only_missing_targets_receive_new_ideas(
    enable_flag, db_session, make_workspace, make_scoring_run, make_keyword
):
    """4. Yalnız missing target'lara yeni fikir yazılır."""
    targets_data = [("instagram", "post"), ("twitter", "thread")]
    ws, run, kws, brief, cats, targets, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-04",
        persist_first_target=True,
        targets_data=targets_data,
    )
    results = _build_valid_retry_results(start.plan, retry_att.id, targets, kws)
    res = persist_social_idea_retry_results(
        db_session,
        attempt_id=retry_att.id,
        task_id="task-04",
        category_results=results,
        now=T0,
    )

    for idea in res.newly_persisted_ideas:
        assert idea.brief_target_id in start.plan.missing_target_ids
        assert idea.brief_target_id != targets[0].id


def test_05_accepted_target_ids_in_canonical_order(
    enable_flag, db_session, make_workspace, make_scoring_run, make_keyword
):
    """5. accepted_target_ids canonical sıradadır."""
    targets_data = [("instagram", "post"), ("twitter", "thread"), ("linkedin", "post")]
    ws, run, kws, brief, cats, targets, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-05",
        persist_first_target=True,
        targets_data=targets_data,
        num_categories=2,
    )
    results = _build_valid_retry_results(start.plan, retry_att.id, targets, kws)
    # Ters sırada verelim
    reversed_results = tuple(reversed(results))

    res = persist_social_idea_retry_results(
        db_session,
        attempt_id=retry_att.id,
        task_id="task-05",
        category_results=reversed_results,
        now=T0,
    )

    canonical_order = tuple(t.id for t in targets if t.id in start.plan.missing_target_ids)
    assert res.accepted_target_ids == canonical_order


def test_06_full_success_updates_coverage_generated_correctly(
    enable_flag, db_session, make_workspace, make_scoring_run, make_keyword
):
    """6. Tam başarıda coverage.generated doğru güncellenir."""
    targets_data = [("instagram", "post"), ("twitter", "thread")]
    ws, run, kws, brief, cats, targets, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-06",
        persist_first_target=True,
        targets_data=targets_data,
    )
    results = _build_valid_retry_results(start.plan, retry_att.id, targets, kws)
    persist_social_idea_retry_results(
        db_session,
        attempt_id=retry_att.id,
        task_id="task-06",
        category_results=results,
        now=T0,
    )

    db_session.refresh(retry_att)
    cov = retry_att.coverage
    assert "generated" in cov
    assert cov["generated"]["total_accepted"] == 1
    assert cov["generated"]["target_ids"] == list(start.plan.missing_target_ids)
    assert cov["request"]["source_attempt_id"] == source_att.id


def test_07_full_success_clears_warnings_lease_and_sets_completed_at(
    enable_flag, db_session, make_workspace, make_scoring_run, make_keyword
):
    """7. Tam başarıda warnings boş, lease temiz ve completed_at yazılıdır."""
    targets_data = [("instagram", "post"), ("twitter", "thread")]
    ws, run, kws, brief, cats, targets, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-07",
        persist_first_target=True,
        targets_data=targets_data,
    )
    results = _build_valid_retry_results(start.plan, retry_att.id, targets, kws)
    persist_social_idea_retry_results(
        db_session,
        attempt_id=retry_att.id,
        task_id="task-07",
        category_results=results,
        now=T0,
    )

    db_session.refresh(retry_att)
    assert retry_att.warnings == []
    assert retry_att.lease_expires_at is None
    assert retry_att.completed_at == T0


def test_08_missing_one_category_persists_successful_and_marks_partial(
    enable_flag, db_session, make_workspace, make_scoring_run, make_keyword
):
    """8. Kategorilerden biri eksikse mevcut başarılı kategori kaydedilir ve attempt partial olur."""
    targets_data = [("instagram", "post"), ("twitter", "thread")]
    ws, run, kws, brief, cats, targets, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-08",
        persist_first_target=False,
        targets_data=targets_data,
        num_categories=2,
        ideas_per_category=1,
    )
    results = _build_valid_retry_results(start.plan, retry_att.id, targets, kws)
    assert len(results) == 2

    # Yalnızca ilk kategorinin sonucunu verelim
    partial_results = (results[0],)
    res = persist_social_idea_retry_results(
        db_session,
        attempt_id=retry_att.id,
        task_id="task-08",
        category_results=partial_results,
        now=T0,
    )

    assert res.status == "partial"
    assert res.newly_persisted_count == 1
    assert len(res.accepted_target_ids) == 1
    assert len(res.unfilled_target_ids) == 1

    db_session.refresh(retry_att)
    assert retry_att.status == "partial"
    assert retry_att.reason_code == "target_unfilled"
    assert retry_att.completed_at == T0
    assert retry_att.lease_expires_at is None


def test_09_partial_warnings_contain_only_unfilled_targets(
    enable_flag, db_session, make_workspace, make_scoring_run, make_keyword
):
    """9. Partial warnings yalnız unfilled target'ları içerir."""
    targets_data = [("instagram", "post"), ("twitter", "thread")]
    ws, run, kws, brief, cats, targets, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-09",
        persist_first_target=False,
        targets_data=targets_data,
        num_categories=2,
        ideas_per_category=1,
    )
    results = _build_valid_retry_results(start.plan, retry_att.id, targets, kws)
    partial_results = (results[0],)
    res = persist_social_idea_retry_results(
        db_session,
        attempt_id=retry_att.id,
        task_id="task-09",
        category_results=partial_results,
        now=T0,
    )

    db_session.refresh(retry_att)
    # Hedef uyarısı yalnız unfilled hedef için; ayrıca hiç fikir almayan boş kategori
    # için (K4) aynı atamanın category_unfilled uyarısı taşınır.
    target_warnings = [w for w in retry_att.warnings if w["reason_code"] == "target_unfilled"]
    assert len(target_warnings) == 1
    w = target_warnings[0]
    assert w["target_id"] in res.unfilled_target_ids
    assert "category_id" in w
    category_warnings = [w for w in retry_att.warnings if w["reason_code"] == "category_unfilled"]
    assert [cw["category_id"] for cw in category_warnings] == list(res.unfilled_category_ids)
    assert len(retry_att.warnings) == len(target_warnings) + len(category_warnings)


def test_10_partial_coverage_contains_only_actually_accepted_targets(
    enable_flag, db_session, make_workspace, make_scoring_run, make_keyword
):
    """10. Partial coverage yalnız gerçekten kabul edilen target'ları içerir."""
    targets_data = [("instagram", "post"), ("twitter", "thread")]
    ws, run, kws, brief, cats, targets, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-10",
        persist_first_target=False,
        targets_data=targets_data,
        num_categories=2,
        ideas_per_category=1,
    )
    results = _build_valid_retry_results(start.plan, retry_att.id, targets, kws)
    partial_results = (results[0],)
    res = persist_social_idea_retry_results(
        db_session,
        attempt_id=retry_att.id,
        task_id="task-10",
        category_results=partial_results,
        now=T0,
    )

    db_session.refresh(retry_att)
    cov = retry_att.coverage
    assert cov["generated"]["total_accepted"] == 1
    assert cov["generated"]["target_ids"] == list(res.accepted_target_ids)


def test_11_empty_category_results_rejected_no_db_write(
    enable_flag, db_session, make_workspace, make_scoring_run, make_keyword
):
    """11. Boş category_results reddedilir ve DB'ye yazılmaz."""
    ws, run, kws, brief, cats, targets, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-11",
    )
    with pytest.raises(SocialIdeaRetryPersistenceError) as exc:
        persist_social_idea_retry_results(
            db_session,
            attempt_id=retry_att.id,
            task_id="task-11",
            category_results=(),
            now=T0,
        )
    assert exc.value.error_code == "IDEA_RETRY_PERSISTENCE_INVALID_INPUT"


def test_12_duplicate_category_result_rejected(
    enable_flag, db_session, make_workspace, make_scoring_run, make_keyword
):
    """12. Aynı category result iki kez reddedilir."""
    ws, run, kws, brief, cats, targets, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-12",
    )
    results = _build_valid_retry_results(start.plan, retry_att.id, targets, kws)
    dup_results = (results[0], results[0])

    with pytest.raises(SocialIdeaRetryPersistenceError) as exc:
        persist_social_idea_retry_results(
            db_session,
            attempt_id=retry_att.id,
            task_id="task-12",
            category_results=dup_results,
            now=T0,
        )
    assert exc.value.error_code == "IDEA_RETRY_PERSISTENCE_INVALID_INPUT"


def test_13_mismatched_attempt_id_in_result_rejected(
    enable_flag, db_session, make_workspace, make_scoring_run, make_keyword
):
    """13. Yanlış attempt_id taşıyan AI result reddedilir."""
    ws, run, kws, brief, cats, targets, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-13",
    )
    results = _build_valid_retry_results(start.plan, retry_att.id, targets, kws)
    bad_res = SocialIdeaAIResult(
        attempt_id=retry_att.id + 999,
        category_id=results[0].category_id,
        ideas=results[0].ideas,
        ai_calls_used=1,
    )

    with pytest.raises(SocialIdeaRetryPersistenceError) as exc:
        persist_social_idea_retry_results(
            db_session,
            attempt_id=retry_att.id,
            task_id="task-13",
            category_results=(bad_res,),
            now=T0,
        )
    assert exc.value.error_code == "IDEA_RETRY_PERSISTENCE_INVALID_INPUT"


def test_14_unplanned_category_rejected(
    enable_flag, db_session, make_workspace, make_scoring_run, make_keyword
):
    """14. Plan dışı category reddedilir."""
    ws, run, kws, brief, cats, targets, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-14",
    )
    results = _build_valid_retry_results(start.plan, retry_att.id, targets, kws)
    bad_res = SocialIdeaAIResult(
        attempt_id=retry_att.id,
        category_id=9999,
        ideas=results[0].ideas,
        ai_calls_used=1,
    )

    with pytest.raises(SocialIdeaRetryPersistenceError) as exc:
        persist_social_idea_retry_results(
            db_session,
            attempt_id=retry_att.id,
            task_id="task-14",
            category_results=(bad_res,),
            now=T0,
        )
    assert exc.value.error_code == "IDEA_RETRY_PERSISTENCE_INVALID_INPUT"


def test_15_unplanned_target_rejected(
    enable_flag, db_session, make_workspace, make_scoring_run, make_keyword
):
    """15. Plan dışı target reddedilir."""
    ws, run, kws, brief, cats, targets, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-15",
    )
    results = _build_valid_retry_results(start.plan, retry_att.id, targets, kws)
    first_res = results[0]
    bad_idea = ValidatedSocialIdea(
        target_id=9999,
        primary_keyword_id=kws[0].id,
        target_platform="instagram",
        content_format="post",
        idea_title="Başlık",
        idea_description="Açıklama",
        trend_alignment=0.8,
    )
    bad_res = SocialIdeaAIResult(
        attempt_id=retry_att.id,
        category_id=first_res.category_id,
        ideas=(bad_idea,),
        ai_calls_used=1,
    )

    with pytest.raises(SocialIdeaRetryPersistenceError) as exc:
        persist_social_idea_retry_results(
            db_session,
            attempt_id=retry_att.id,
            task_id="task-15",
            category_results=(bad_res,),
            now=T0,
        )
    assert exc.value.error_code == "IDEA_RETRY_PERSISTENCE_INVALID_INPUT"


def test_16_persisted_target_idea_rejected(
    enable_flag, db_session, make_workspace, make_scoring_run, make_keyword
):
    """16. Persisted target için yeni fikir reddedilir."""
    ws, run, kws, brief, cats, targets, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-16",
        persist_first_target=True,
    )
    persisted_tid = targets[0].id
    results = _build_valid_retry_results(start.plan, retry_att.id, targets, kws)
    bad_idea = ValidatedSocialIdea(
        target_id=persisted_tid,
        primary_keyword_id=kws[0].id,
        target_platform=targets[0].platform,
        content_format=targets[0].content_format,
        idea_title="Başlık",
        idea_description="Açıklama",
        trend_alignment=0.8,
    )
    bad_res = SocialIdeaAIResult(
        attempt_id=retry_att.id,
        category_id=results[0].category_id,
        ideas=(bad_idea,),
        ai_calls_used=1,
    )

    with pytest.raises(SocialIdeaRetryPersistenceError) as exc:
        persist_social_idea_retry_results(
            db_session,
            attempt_id=retry_att.id,
            task_id="task-16",
            category_results=(bad_res,),
            now=T0,
        )
    assert exc.value.error_code == "IDEA_RETRY_PERSISTENCE_INVALID_INPUT"


def test_17_duplicate_idea_for_same_target_rejected(
    enable_flag, db_session, make_workspace, make_scoring_run, make_keyword
):
    """17. Aynı target için iki fikir reddedilir."""
    ws, run, kws, brief, cats, targets, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-17",
    )
    results = _build_valid_retry_results(start.plan, retry_att.id, targets, kws)
    first_res = results[0]
    first_idea = first_res.ideas[0]
    dup_res = SocialIdeaAIResult(
        attempt_id=retry_att.id,
        category_id=first_res.category_id,
        ideas=(first_idea, first_idea),
        ai_calls_used=1,
    )

    with pytest.raises(SocialIdeaRetryPersistenceError) as exc:
        persist_social_idea_retry_results(
            db_session,
            attempt_id=retry_att.id,
            task_id="task-17",
            category_results=(dup_res,),
            now=T0,
        )
    assert exc.value.error_code == "IDEA_RETRY_PERSISTENCE_INVALID_INPUT"


def test_18_partial_targets_inside_category_accepted_as_partial(
    enable_flag, db_session, make_workspace, make_scoring_run, make_keyword
):
    """18. Plan §3.4: kategori içi kısmi sonuç (bir hedefin fikri atıldı) kabul edilir;
    kapsanmayan hedef target_unfilled uyarısıyla kalır, attempt partial olur."""
    # 1 kategori, 2 missing target
    targets_data = [("instagram", "post"), ("twitter", "thread"), ("linkedin", "post")]
    ws, run, kws, brief, cats, targets, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-18",
        persist_first_target=True,
        targets_data=targets_data,
        num_categories=1,
    )
    # start.plan'da cats[0] her iki missing target'ı da aldı (T2 ve T3)
    results = _build_valid_retry_results(start.plan, retry_att.id, targets, kws)
    assert len(results[0].ideas) == 2

    # Yalnız 1 fikir gönderelim
    half_res = SocialIdeaAIResult(
        attempt_id=retry_att.id,
        category_id=results[0].category_id,
        ideas=(results[0].ideas[0],),
        ai_calls_used=1,
    )

    res = persist_social_idea_retry_results(
        db_session,
        attempt_id=retry_att.id,
        task_id="task-18",
        category_results=(half_res,),
        metrics={"off_brief_dropped": 1},
        now=T0,
    )
    db_session.commit()
    kept_tid = results[0].ideas[0].target_id
    dropped_tid = results[0].ideas[1].target_id
    assert res.status == "partial"
    assert res.accepted_target_ids == (kept_tid,)
    assert res.unfilled_target_ids == (dropped_tid,)

    db_session.refresh(retry_att)
    assert retry_att.status == "partial"
    assert retry_att.reason_code == "target_unfilled"
    assert [(w["target_id"], w["reason_code"]) for w in retry_att.warnings] == [
        (dropped_tid, "target_unfilled")
    ]
    assert retry_att.coverage["metrics"]["off_brief_dropped"] == 1
    assert retry_att.coverage["metrics"]["target_unfilled"] == 1


def test_19_off_brief_keyword_rejected(
    enable_flag, db_session, make_workspace, make_scoring_run, make_keyword
):
    """19. Brief dışı keyword reddedilir."""
    targets_data = [("instagram", "post"), ("twitter", "thread")]
    ws, run, kws, brief, cats, targets, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-19",
        persist_first_target=True,
        targets_data=targets_data,
        num_categories=1,
    )
    results = _build_valid_retry_results(start.plan, retry_att.id, targets, kws)
    first_res = results[0]
    bad_idea = ValidatedSocialIdea(
        target_id=first_res.ideas[0].target_id,
        primary_keyword_id=99999,
        target_platform=first_res.ideas[0].target_platform,
        content_format=first_res.ideas[0].content_format,
        idea_title=first_res.ideas[0].idea_title,
        idea_description=first_res.ideas[0].idea_description,
        trend_alignment=first_res.ideas[0].trend_alignment,
    )
    bad_res = SocialIdeaAIResult(
        attempt_id=retry_att.id,
        category_id=first_res.category_id,
        ideas=(bad_idea,),
        ai_calls_used=1,
    )

    with pytest.raises(SocialIdeaRetryPersistenceError) as exc:
        persist_social_idea_retry_results(
            db_session,
            attempt_id=retry_att.id,
            task_id="task-19",
            category_results=(bad_res,),
            now=T0,
        )
    assert exc.value.error_code == "IDEA_RETRY_PERSISTENCE_INCONSISTENT"


def test_20_platform_format_mismatch_rejected(
    enable_flag, db_session, make_workspace, make_scoring_run, make_keyword
):
    """20. Platform/format mismatch reddedilir."""
    targets_data = [("instagram", "post"), ("twitter", "thread")]
    ws, run, kws, brief, cats, targets, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-20",
        persist_first_target=True,
        targets_data=targets_data,
        num_categories=1,
    )
    results = _build_valid_retry_results(start.plan, retry_att.id, targets, kws)
    first_res = results[0]
    # Yanlış platform
    bad_idea = ValidatedSocialIdea(
        target_id=first_res.ideas[0].target_id,
        primary_keyword_id=kws[0].id,
        target_platform="youtube",
        content_format="short",
        idea_title=first_res.ideas[0].idea_title,
        idea_description=first_res.ideas[0].idea_description,
        trend_alignment=first_res.ideas[0].trend_alignment,
    )
    bad_res = SocialIdeaAIResult(
        attempt_id=retry_att.id,
        category_id=first_res.category_id,
        ideas=(bad_idea,),
        ai_calls_used=1,
    )

    with pytest.raises(SocialIdeaRetryPersistenceError) as exc:
        persist_social_idea_retry_results(
            db_session,
            attempt_id=retry_att.id,
            task_id="task-20",
            category_results=(bad_res,),
            now=T0,
        )
    assert exc.value.error_code == "IDEA_RETRY_PERSISTENCE_INCONSISTENT"


def test_21_invalid_title_description_trend_alignment_rejected(
    enable_flag, db_session, make_workspace, make_scoring_run, make_keyword
):
    """21. Geçersiz title/description/trend_alignment reddedilir."""
    ws, run, kws, brief, cats, targets, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-21",
    )
    results = _build_valid_retry_results(start.plan, retry_att.id, targets, kws)
    first_res = results[0]

    # Boş title
    bad_idea = ValidatedSocialIdea(
        target_id=first_res.ideas[0].target_id,
        primary_keyword_id=kws[0].id,
        target_platform=first_res.ideas[0].target_platform,
        content_format=first_res.ideas[0].content_format,
        idea_title="",
        idea_description="Açıklama",
        trend_alignment=0.5,
    )
    with pytest.raises(SocialIdeaRetryPersistenceError) as exc:
        persist_social_idea_retry_results(
            db_session,
            attempt_id=retry_att.id,
            task_id="task-21",
            category_results=(
                SocialIdeaAIResult(
                    attempt_id=retry_att.id,
                    category_id=first_res.category_id,
                    ideas=(bad_idea,),
                    ai_calls_used=1,
                ),
            ),
            now=T0,
        )
    assert exc.value.error_code == "IDEA_RETRY_PERSISTENCE_INVALID_INPUT"


def test_22_huge_numeric_value_no_raw_overflow_error(
    enable_flag, db_session, make_workspace, make_scoring_run, make_keyword
):
    """22. Aşırı büyük numeric değer ham OverflowError sızdırmaz."""
    ws, run, kws, brief, cats, targets, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-22",
    )
    results = _build_valid_retry_results(start.plan, retry_att.id, targets, kws)
    first_res = results[0]

    bad_idea = ValidatedSocialIdea(
        target_id=first_res.ideas[0].target_id,
        primary_keyword_id=kws[0].id,
        target_platform=first_res.ideas[0].target_platform,
        content_format=first_res.ideas[0].content_format,
        idea_title="Başlık",
        idea_description="Açıklama",
        trend_alignment=1e309,
    )
    with pytest.raises(SocialIdeaRetryPersistenceError) as exc:
        persist_social_idea_retry_results(
            db_session,
            attempt_id=retry_att.id,
            task_id="task-22",
            category_results=(
                SocialIdeaAIResult(
                    attempt_id=retry_att.id,
                    category_id=first_res.category_id,
                    ideas=(bad_idea,),
                    ai_calls_used=1,
                ),
            ),
            now=T0,
        )
    assert exc.value.error_code == "IDEA_RETRY_PERSISTENCE_INVALID_INPUT"
    assert "OverflowError" not in exc.value.message


def test_23_stale_brief_writes_no_ideas(
    enable_flag, db_session, make_workspace, make_scoring_run, make_keyword
):
    """23. Brief stale olduğunda hiçbir fikir yazılmaz."""
    ws, run, kws, brief, cats, targets, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-23",
    )
    brief.is_stale = True
    db_session.commit()

    results = _build_valid_retry_results(start.plan, retry_att.id, targets, kws)
    with pytest.raises(AttemptNotWritableError) as exc:
        persist_social_idea_retry_results(
            db_session,
            attempt_id=retry_att.id,
            task_id="task-23",
            category_results=results,
            now=T0,
        )
    assert exc.value.error_code == "BRIEF_STALE"


def test_24_assignment_version_mismatch_writes_no_ideas(
    enable_flag, db_session, make_workspace, make_scoring_run, make_keyword
):
    """24. Assignment version değiştiğinde hiçbir fikir yazılmaz."""
    ws, run, kws, brief, cats, targets, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-24",
    )
    brief.channel_assignment_version = 999
    db_session.commit()

    results = _build_valid_retry_results(start.plan, retry_att.id, targets, kws)
    with pytest.raises(AttemptNotWritableError) as exc:
        persist_social_idea_retry_results(
            db_session,
            attempt_id=retry_att.id,
            task_id="task-24",
            category_results=results,
            now=T0,
        )
    assert exc.value.error_code == "ASSIGNMENT_CHANGED"


def test_25_expired_lease_worker_lost_writes_no_ideas(
    enable_flag, db_session, make_workspace, make_scoring_run, make_keyword
):
    """25. Lease dolduğunda geç worker çıktısı yazılmaz."""
    ws, run, kws, brief, cats, targets, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-25",
    )
    # Geçmişe çekelim
    retry_att.lease_expires_at = T0 - timedelta(seconds=10)
    db_session.commit()

    results = _build_valid_retry_results(start.plan, retry_att.id, targets, kws)
    with pytest.raises(AttemptNotWritableError) as exc:
        persist_social_idea_retry_results(
            db_session,
            attempt_id=retry_att.id,
            task_id="task-25",
            category_results=results,
            now=T0,
        )
    assert exc.value.error_code == "WORKER_LOST"


def test_26_mismatched_task_id_writes_no_ideas(
    enable_flag, db_session, make_workspace, make_scoring_run, make_keyword
):
    """26. Farklı task_id çıktısı yazılmaz."""
    ws, run, kws, brief, cats, targets, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-26-valid",
    )
    results = _build_valid_retry_results(start.plan, retry_att.id, targets, kws)
    with pytest.raises(AttemptNotWritableError) as exc:
        persist_social_idea_retry_results(
            db_session,
            attempt_id=retry_att.id,
            task_id="task-26-wrong",
            category_results=results,
            now=T0,
        )
    assert exc.value.error_code == "TASK_MISMATCH"


def test_27_missing_target_concurrently_filled_raises_conflict_zero_insert(
    enable_flag, db_session, make_workspace, make_scoring_run, make_keyword
):
    """27. Missing target canlı DB'de başka fikirle doldurulmuşsa conflict ve sıfır insert."""
    targets_data = [("instagram", "post"), ("twitter", "thread")]
    ws, run, kws, brief, cats, targets, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-27",
        persist_first_target=True,
        targets_data=targets_data,
    )
    missing_tid = start.plan.missing_target_ids[0]
    missing_target = [t for t in targets if t.id == missing_tid][0]

    # Başka bir session araya girip bu hedefi doldurdu
    _create_test_idea(db_session, brief, cats[0], missing_target, kws[0].id)

    results = _build_valid_retry_results(start.plan, retry_att.id, targets, kws)
    with pytest.raises(SocialIdeaRetryPersistenceError) as exc:
        persist_social_idea_retry_results(
            db_session,
            attempt_id=retry_att.id,
            task_id="task-27",
            category_results=results,
            now=T0,
        )
    assert exc.value.error_code == "IDEA_RETRY_PERSISTENCE_CONFLICT"


def test_28_baseline_persisted_target_missing_from_db_raises_inconsistent_zero_insert(
    enable_flag, db_session, make_workspace, make_scoring_run, make_keyword
):
    """28. Baseline persisted target artık DB'de yoksa inconsistent ve sıfır insert."""
    targets_data = [("instagram", "post"), ("twitter", "thread")]
    ws, run, kws, brief, cats, targets, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-28",
        persist_first_target=True,
        targets_data=targets_data,
    )
    # Baseline fikrini silelim
    db_session.query(SocialIdea).filter_by(brief_target_id=targets[0].id).delete()
    db_session.commit()

    results = _build_valid_retry_results(start.plan, retry_att.id, targets, kws)
    with pytest.raises(SocialIdeaRetryPersistenceError) as exc:
        persist_social_idea_retry_results(
            db_session,
            attempt_id=retry_att.id,
            task_id="task-28",
            category_results=results,
            now=T0,
        )
    assert exc.value.error_code == "IDEA_RETRY_PERSISTENCE_INCONSISTENT"


def test_29_error_rollback_restores_attempt_and_ideas_cleanly(
    enable_flag, db_session, make_workspace, make_scoring_run, make_keyword
):
    """29. Hata sonrasında rollback ile attempt ve fikirler eski hâline döner."""
    targets_data = [("instagram", "post"), ("twitter", "thread")]
    ws, run, kws, brief, cats, targets, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-29",
        persist_first_target=True,
        targets_data=targets_data,
    )
    results = _build_valid_retry_results(start.plan, retry_att.id, targets, kws)
    bad_res = SocialIdeaAIResult(
        attempt_id=retry_att.id + 999,
        category_id=results[0].category_id,
        ideas=results[0].ideas,
        ai_calls_used=1,
    )

    try:
        persist_social_idea_retry_results(
            db_session,
            attempt_id=retry_att.id,
            task_id="task-29",
            category_results=(bad_res,),
            now=T0,
        )
    except SocialIdeaRetryPersistenceError:
        db_session.rollback()

    db_session.refresh(retry_att)
    assert retry_att.status == "running"
    db_ideas = db_session.query(SocialIdea).filter_by(brief_id=brief.id).all()
    assert len(db_ideas) == 1


def test_30_completed_replay_returns_idempotent_with_empty_results(
    enable_flag, db_session, make_workspace, make_scoring_run, make_keyword
):
    """30. Completed replay boş sonuçlarla idempotent döner."""
    targets_data = [("instagram", "post"), ("twitter", "thread")]
    ws, run, kws, brief, cats, targets, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-30",
        persist_first_target=True,
        targets_data=targets_data,
    )
    results = _build_valid_retry_results(start.plan, retry_att.id, targets, kws)
    res1 = persist_social_idea_retry_results(
        db_session,
        attempt_id=retry_att.id,
        task_id="task-30",
        category_results=results,
        now=T0,
    )
    db_session.commit()
    assert res1.already_completed is False

    # Completed replay
    res2 = persist_social_idea_retry_results(
        db_session,
        attempt_id=retry_att.id,
        task_id="task-30",
        category_results=(),
        now=T0,
    )
    assert res2.already_completed is True
    assert res2.status == "completed"
    assert res2.newly_persisted_count == 0
    assert res2.newly_persisted_ideas == ()
    assert res2.accepted_target_ids == res1.accepted_target_ids


def test_31_completed_replay_rejected_if_category_results_non_empty(
    enable_flag, db_session, make_workspace, make_scoring_run, make_keyword
):
    """31. Completed replay dolu category_results ile reddedilir."""
    targets_data = [("instagram", "post"), ("twitter", "thread")]
    ws, run, kws, brief, cats, targets, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-31",
        persist_first_target=True,
        targets_data=targets_data,
    )
    results = _build_valid_retry_results(start.plan, retry_att.id, targets, kws)
    persist_social_idea_retry_results(
        db_session,
        attempt_id=retry_att.id,
        task_id="task-31",
        category_results=results,
        now=T0,
    )
    db_session.commit()

    with pytest.raises(SocialIdeaRetryPersistenceError) as exc:
        persist_social_idea_retry_results(
            db_session,
            attempt_id=retry_att.id,
            task_id="task-31",
            category_results=results,
            now=T0,
        )
    assert exc.value.error_code == "IDEA_RETRY_PERSISTENCE_INVALID_INPUT"


def test_32_completed_replay_rejected_if_generated_coverage_corrupted(
    enable_flag, db_session, make_workspace, make_scoring_run, make_keyword
):
    """32. Completed replay bozuk generated coverage ile reddedilir."""
    targets_data = [("instagram", "post"), ("twitter", "thread")]
    ws, run, kws, brief, cats, targets, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-32",
        persist_first_target=True,
        targets_data=targets_data,
    )
    results = _build_valid_retry_results(start.plan, retry_att.id, targets, kws)
    persist_social_idea_retry_results(
        db_session,
        attempt_id=retry_att.id,
        task_id="task-32",
        category_results=results,
        now=T0,
    )
    db_session.commit()

    # Bozulan generated
    cov = copy.deepcopy(retry_att.coverage)
    cov["generated"]["total_accepted"] = 99
    retry_att.coverage = cov
    flag_modified(retry_att, "coverage")
    db_session.commit()

    with pytest.raises(SocialIdeaRetryPersistenceError) as exc:
        persist_social_idea_retry_results(
            db_session,
            attempt_id=retry_att.id,
            task_id="task-32",
            category_results=(),
            now=T0,
        )
    assert exc.value.error_code == "IDEA_RETRY_PERSISTENCE_INCONSISTENT"


def test_33_completed_replay_rejected_if_db_idea_missing(
    enable_flag, db_session, make_workspace, make_scoring_run, make_keyword
):
    """33. Completed replay eksik DB fikriyle reddedilir."""
    targets_data = [("instagram", "post"), ("twitter", "thread")]
    ws, run, kws, brief, cats, targets, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-33",
        persist_first_target=True,
        targets_data=targets_data,
    )
    results = _build_valid_retry_results(start.plan, retry_att.id, targets, kws)
    res = persist_social_idea_retry_results(
        db_session,
        attempt_id=retry_att.id,
        task_id="task-33",
        category_results=results,
        now=T0,
    )
    db_session.commit()

    # Yeni eklenen fikri silelim
    db_session.query(SocialIdea).filter_by(id=res.newly_persisted_ideas[0].id).delete()
    db_session.commit()

    with pytest.raises(SocialIdeaRetryPersistenceError) as exc:
        persist_social_idea_retry_results(
            db_session,
            attempt_id=retry_att.id,
            task_id="task-33",
            category_results=(),
            now=T0,
        )
    assert exc.value.error_code == "IDEA_RETRY_PERSISTENCE_INCONSISTENT"


def test_34_completed_replay_does_not_mutate_any_rows(
    enable_flag, db_session, make_workspace, make_scoring_run, make_keyword
):
    """34. Completed replay hiçbir satırı mutate etmez."""
    targets_data = [("instagram", "post"), ("twitter", "thread")]
    ws, run, kws, brief, cats, targets, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-34",
        persist_first_target=True,
        targets_data=targets_data,
    )
    results = _build_valid_retry_results(start.plan, retry_att.id, targets, kws)
    persist_social_idea_retry_results(
        db_session,
        attempt_id=retry_att.id,
        task_id="task-34",
        category_results=results,
        now=T0,
    )
    db_session.commit()

    db_session.refresh(retry_att)
    init_cov = copy.deepcopy(retry_att.coverage)
    init_completed_at = retry_att.completed_at

    persist_social_idea_retry_results(
        db_session,
        attempt_id=retry_att.id,
        task_id="task-34",
        category_results=(),
        now=T0 + timedelta(hours=1),
    )
    db_session.commit()

    db_session.refresh(retry_att)
    assert retry_att.coverage == init_cov
    assert retry_att.completed_at == init_completed_at


def test_35_partial_attempt_cannot_be_finalized_again(
    enable_flag, db_session, make_workspace, make_scoring_run, make_keyword
):
    """35. Partial attempt tekrar finalize edilemez."""
    targets_data = [("instagram", "post"), ("twitter", "thread")]
    ws, run, kws, brief, cats, targets, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-35",
        persist_first_target=False,
        targets_data=targets_data,
        num_categories=2,
        ideas_per_category=1,
    )
    results = _build_valid_retry_results(start.plan, retry_att.id, targets, kws)
    persist_social_idea_retry_results(
        db_session,
        attempt_id=retry_att.id,
        task_id="task-35",
        category_results=(results[0],),
        now=T0,
    )
    db_session.commit()

    db_session.refresh(retry_att)
    assert retry_att.status == "partial"

    with pytest.raises(AttemptNotWritableError) as exc:
        persist_social_idea_retry_results(
            db_session,
            attempt_id=retry_att.id,
            task_id="task-35",
            category_results=(),
            now=T0,
        )
    assert exc.value.error_code == "ATTEMPT_NOT_WRITABLE"


def test_36_normal_persist_social_ideas_no_regression(
    enable_flag, db_session, make_workspace, make_scoring_run, make_keyword
):
    """36. Normal persist_social_ideas regresyona uğramaz."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    targets_data = [("instagram", "post"), ("twitter", "thread")]
    brief = _create_test_brief(db_session, run.id, kws, targets_data=targets_data)
    cat = SocialCategory(
        brief_id=brief.id,
        scoring_run_id=run.id,
        category_name="Cat 1",
        category_type="educational",
        description="Desc",
        relevance_score=0.9,
        suggested_keyword_ids=[kws[0].id],
        is_stale=False,
    )
    db_session.add(cat)
    db_session.commit()

    targets = db_session.query(SocialBriefTarget).filter_by(brief_id=brief.id).all()
    source_att = _create_source_ideas_attempt(
        db_session, brief, [cat], targets, status="running", ideas_per_category=2
    )
    source_att.task_id = "task-36-norm"
    db_session.commit()

    ideas = (
        ValidatedSocialIdea(
            target_id=targets[0].id,
            primary_keyword_id=kws[0].id,
            target_platform=targets[0].platform,
            content_format=targets[0].content_format,
            idea_title="Normal Başlık 1",
            idea_description="Normal Açıklama 1",
            trend_alignment=0.8,
        ),
        ValidatedSocialIdea(
            target_id=targets[1].id,
            primary_keyword_id=kws[0].id,
            target_platform=targets[1].platform,
            content_format=targets[1].content_format,
            idea_title="Normal Başlık 2",
            idea_description="Normal Açıklama 2",
            trend_alignment=0.8,
        ),
    )
    res_ai = SocialIdeaAIResult(
        attempt_id=source_att.id,
        category_id=cat.id,
        ideas=ideas,
        ai_calls_used=1,
    )

    norm_res = persist_social_ideas(
        db_session,
        attempt_id=source_att.id,
        task_id="task-36-norm",
        category_results=(res_ai,),
        now=T0,
    )
    assert isinstance(norm_res, PersistedSocialIdeasResult)
    assert norm_res.total_ideas == 2


def test_37_function_does_not_call_commit_or_rollback(
    enable_flag, db_session, make_workspace, make_scoring_run, make_keyword
):
    """37. Fonksiyon commit/rollback çağırmaz."""
    ws, run, kws, brief, cats, targets, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-37",
    )
    results = _build_valid_retry_results(start.plan, retry_att.id, targets, kws)

    commit_called = False
    rollback_called = False

    def on_commit(session):
        nonlocal commit_called
        commit_called = True

    def on_rollback(session):
        nonlocal rollback_called
        rollback_called = True

    event.listen(db_session, "after_commit", on_commit)
    event.listen(db_session, "after_rollback", on_rollback)

    try:
        persist_social_idea_retry_results(
            db_session,
            attempt_id=retry_att.id,
            task_id="task-37",
            category_results=results,
            now=T0,
        )
        assert commit_called is False
        assert rollback_called is False
    finally:
        event.remove(db_session, "after_commit", on_commit)
        event.remove(db_session, "after_rollback", on_rollback)


def test_38_all_inserts_and_finalization_in_same_transaction(
    enable_flag, db_session, make_workspace, make_scoring_run, make_keyword
):
    """38. Bütün insertler ve attempt finalization aynı transaction'dadır."""
    ws, run, kws, brief, cats, targets, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-38",
    )
    results = _build_valid_retry_results(start.plan, retry_att.id, targets, kws)

    persist_social_idea_retry_results(
        db_session,
        attempt_id=retry_att.id,
        task_id="task-38",
        category_results=results,
        now=T0,
    )
    # Rollback yapınca hepsi geri dönmeli
    db_session.rollback()

    db_session.refresh(retry_att)
    assert retry_att.status == "running"
    db_ideas = db_session.query(SocialIdea).filter_by(brief_id=brief.id).all()
    assert len(db_ideas) == 1


def test_39_concurrent_sessions_no_duplicate_ideas(
    enable_flag, db_session, make_workspace, make_scoring_run, make_keyword
):
    """39. İki session eşzamanlı finalize denemesinde kopya fikir oluşmaz."""
    targets_data = [("instagram", "post"), ("twitter", "thread")]
    ws, run, kws, brief, cats, targets, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-39",
        persist_first_target=True,
        targets_data=targets_data,
    )
    results = _build_valid_retry_results(start.plan, retry_att.id, targets, kws)

    barrier = threading.Barrier(2)
    successes = []
    errors = []

    def worker_func():
        session = SessionLocal()
        try:
            barrier.wait(timeout=5)
            res = persist_social_idea_retry_results(
                session,
                attempt_id=retry_att.id,
                task_id="task-39",
                category_results=results,
                now=T0,
            )
            session.commit()
            successes.append(res)
        except Exception as exc:
            session.rollback()
            errors.append(exc)
        finally:
            session.close()

    t1 = threading.Thread(target=worker_func)
    t2 = threading.Thread(target=worker_func)
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    # İki thread'den biri başarıyla completed veya replay yapmalı, DB'de tam olarak 2 fikir (1 baseline + 1 retry) kalmalı
    db_session.expire_all()
    db_ideas = db_session.query(SocialIdea).filter_by(brief_id=brief.id).all()
    assert len(db_ideas) == 2


def test_40_corrupted_source_snapshot_fails_closed(
    enable_flag, db_session, make_workspace, make_scoring_run, make_keyword
):
    """40. Source snapshot bozuksa fail-closed."""
    ws, run, kws, brief, cats, targets, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-40",
    )
    # Source coverage bozalım
    source_att.coverage = {"broken": True}
    db_session.commit()

    results = _build_valid_retry_results(start.plan, retry_att.id, targets, kws)
    with pytest.raises(SocialIdeaRetryPersistenceError) as exc:
        persist_social_idea_retry_results(
            db_session,
            attempt_id=retry_att.id,
            task_id="task-40",
            category_results=results,
            now=T0,
        )
    assert exc.value.error_code == "IDEA_RETRY_PERSISTENCE_INCONSISTENT"


def test_41_corrupted_retry_snapshot_fails_closed(
    enable_flag, db_session, make_workspace, make_scoring_run, make_keyword
):
    """41. Retry snapshot bozuksa fail-closed."""
    ws, run, kws, brief, cats, targets, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-41",
    )
    # Retry coverage request bozalım
    cov = dict(retry_att.coverage)
    cov["request"] = None
    retry_att.coverage = cov
    db_session.commit()

    results = _build_valid_retry_results(start.plan, retry_att.id, targets, kws)
    with pytest.raises(SocialIdeaRetryPersistenceError) as exc:
        persist_social_idea_retry_results(
            db_session,
            attempt_id=retry_att.id,
            task_id="task-41",
            category_results=results,
            now=T0,
        )
    assert exc.value.error_code == "IDEA_RETRY_PERSISTENCE_INCONSISTENT"


def test_42_canonical_target_order_tampered_fails_closed(
    enable_flag, db_session, make_workspace, make_scoring_run, make_keyword
):
    """42. Canonical target sırası/sayısı sonradan değişmişse fail-closed."""
    ws, run, kws, brief, cats, targets, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-42",
    )
    # Yeni bir target ekleyelim
    new_t = SocialBriefTarget(brief_id=brief.id, platform="linkedin", content_format="article")
    db_session.add(new_t)
    db_session.commit()

    results = _build_valid_retry_results(start.plan, retry_att.id, targets, kws)
    with pytest.raises(SocialIdeaRetryPersistenceError) as exc:
        persist_social_idea_retry_results(
            db_session,
            attempt_id=retry_att.id,
            task_id="task-42",
            category_results=results,
            now=T0,
        )
    assert exc.value.error_code == "IDEA_RETRY_PERSISTENCE_INCONSISTENT"


def test_43_error_messages_do_not_leak_raw_id_sql_ai_text(
    enable_flag, db_session, make_workspace, make_scoring_run, make_keyword
):
    """43. Hata mesajlarında ham ID, SQL veya AI metni sızmaz."""
    ws, run, kws, brief, cats, targets, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-43",
    )
    secret_text = "SECRET_USER_INPUT_TEXT_XYZ"
    bad_idea = ValidatedSocialIdea(
        target_id=targets[1].id,
        primary_keyword_id=kws[0].id,
        target_platform=targets[1].platform,
        content_format=targets[1].content_format,
        idea_title=secret_text + " " * 300,  # invalid title
        idea_description="Desc",
        trend_alignment=0.5,
    )
    bad_res = SocialIdeaAIResult(
        attempt_id=retry_att.id,
        category_id=cats[0].id,
        ideas=(bad_idea,),
        ai_calls_used=1,
    )

    with pytest.raises(SocialIdeaRetryPersistenceError) as exc:
        persist_social_idea_retry_results(
            db_session,
            attempt_id=retry_att.id,
            task_id="task-43",
            category_results=(bad_res,),
            now=T0,
        )
    assert secret_text not in exc.value.message
    assert "SELECT" not in exc.value.message


def test_44_existing_stale_idea_does_not_satisfy_missing_target(
    enable_flag, db_session, make_workspace, make_scoring_run, make_keyword
):
    """44. Existing stale idea missing hedefi dolu saydırmaz ve conflict yaratmaz."""
    targets_data = [("instagram", "post"), ("twitter", "thread")]
    ws, run, kws, brief, cats, targets, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-44",
        persist_first_target=True,
        targets_data=targets_data,
    )
    missing_tid = start.plan.missing_target_ids[0]
    missing_target = [t for t in targets if t.id == missing_tid][0]

    # Stale bir fikir ekleyelim
    _create_test_idea(db_session, brief, cats[0], missing_target, kws[0].id, is_stale=True)

    results = _build_valid_retry_results(start.plan, retry_att.id, targets, kws)
    res = persist_social_idea_retry_results(
        db_session,
        attempt_id=retry_att.id,
        task_id="task-44",
        category_results=results,
        now=T0,
    )
    assert res.status == "completed"
    assert res.newly_persisted_count == 1


def test_45_partial_result_subsequent_retry_preflight_plans_only_remaining(
    enable_flag, db_session, make_workspace, make_scoring_run, make_keyword
):
    """45. Partial sonucu takip eden yeni retry preflight yalnız kalan hedefleri planlar."""
    targets_data = [("instagram", "post"), ("twitter", "thread")]
    ws, run, kws, brief, cats, targets, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-45",
        persist_first_target=False,
        targets_data=targets_data,
        num_categories=2,
        ideas_per_category=1,
    )
    # T1 ve T2 missing
    results = _build_valid_retry_results(start.plan, retry_att.id, targets, kws)
    # Yalnız 1 kategoriyi persist edip attempt'i partial yapalım
    res = persist_social_idea_retry_results(
        db_session,
        attempt_id=retry_att.id,
        task_id="task-45",
        category_results=(results[0],),
        now=T0,
    )
    db_session.commit()
    assert res.status == "partial"
    assert len(res.accepted_target_ids) == 1
    accepted_tid = res.accepted_target_ids[0]
    remaining_tid = res.unfilled_target_ids[0]

    # Şimdi bu partial attempt sonrasında yeni bir retry başlatalım
    req2 = SocialBriefIdeasRetryRequest(
        idempotency_key="retry-subsequent-45",
        source_attempt_id=source_att.id,
    )
    start2 = begin_social_idea_retry(
        db_session,
        brief_id=brief.id,
        brand_profile_id=ws.id,
        request=req2,
        now=T0 + timedelta(minutes=5),
    )

    assert start2.plan.missing_target_ids == (remaining_tid,)
    assert set(start2.persisted_target_ids_at_start) == {accepted_tid}


def test_46_completed_replay_zero_newly_persisted_and_preserves_db(
    enable_flag, db_session, make_workspace, make_scoring_run, make_keyword
):
    """46. Completed replay: newly_persisted_count == 0, newly_persisted_ideas == (), accepted_target_ids korunur, DB satır sayısı değişmez."""
    targets_data = [("instagram", "post"), ("twitter", "thread")]
    ws, run, kws, brief, cats, targets, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-46",
        persist_first_target=True,
        targets_data=targets_data,
    )
    results = _build_valid_retry_results(start.plan, retry_att.id, targets, kws)
    res1 = persist_social_idea_retry_results(
        db_session,
        attempt_id=retry_att.id,
        task_id="task-46",
        category_results=results,
        now=T0,
    )
    db_session.commit()
    assert res1.already_completed is False
    assert res1.newly_persisted_count == 1
    assert len(res1.newly_persisted_ideas) == 1

    total_ideas_before = db_session.query(SocialIdea).filter_by(brief_id=brief.id).count()

    # Completed replay çağrısı
    res2 = persist_social_idea_retry_results(
        db_session,
        attempt_id=retry_att.id,
        task_id="task-46",
        category_results=(),
        now=T0,
    )
    assert res2.already_completed is True
    assert res2.status == "completed"
    assert res2.newly_persisted_count == 0
    assert res2.newly_persisted_ideas == ()
    assert res2.accepted_target_ids == res1.accepted_target_ids
    assert res2.unfilled_target_ids == ()

    total_ideas_after = db_session.query(SocialIdea).filter_by(brief_id=brief.id).count()
    assert total_ideas_after == total_ideas_before


def test_47_completed_replay_multiple_ideas_for_generated_target_fails_closed(
    enable_flag, db_session, make_workspace, make_scoring_run, make_keyword
):
    """47. Generated target'ta iki non-stale fikir varsa replay fail-closed reddeder (IDEA_RETRY_PERSISTENCE_INCONSISTENT)."""
    targets_data = [("instagram", "post"), ("twitter", "thread")]
    ws, run, kws, brief, cats, targets, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-47",
        persist_first_target=True,
        targets_data=targets_data,
    )
    results = _build_valid_retry_results(start.plan, retry_att.id, targets, kws)
    persist_social_idea_retry_results(
        db_session,
        attempt_id=retry_att.id,
        task_id="task-47",
        category_results=results,
        now=T0,
    )
    db_session.commit()

    # İkinci bir non-stale fikir ekleyelim (aynı generated (kategori, hedef) çifti için)
    gen_tid = start.plan.missing_target_ids[0]
    gen_target = [t for t in targets if t.id == gen_tid][0]
    gen_cat_id = next(a.category_id for a in start.plan.assignments if a.target_id == gen_tid)
    gen_cat = [c for c in cats if c.id == gen_cat_id][0]
    _create_test_idea(db_session, brief, gen_cat, gen_target, kws[0].id, is_stale=False)

    with pytest.raises(SocialIdeaRetryPersistenceError) as exc:
        persist_social_idea_retry_results(
            db_session,
            attempt_id=retry_att.id,
            task_id="task-47",
            category_results=(),
            now=T0,
        )
    assert exc.value.error_code == "IDEA_RETRY_PERSISTENCE_INCONSISTENT"
    assert exc.value.field == "generated_target_ids"


def test_48_completed_replay_wrong_category_id_fails_closed(
    enable_flag, db_session, make_workspace, make_scoring_run, make_keyword
):
    """48. Generated target fikri yanlış category_id'ye sahipse replay fail-closed reddeder."""
    targets_data = [("instagram", "post"), ("twitter", "thread")]
    ws, run, kws, brief, cats, targets, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-48",
        persist_first_target=True,
        targets_data=targets_data,
        num_categories=2,
    )
    results = _build_valid_retry_results(start.plan, retry_att.id, targets, kws)
    res = persist_social_idea_retry_results(
        db_session,
        attempt_id=retry_att.id,
        task_id="task-48",
        category_results=results,
        now=T0,
    )
    db_session.commit()

    # Fikrin category_id'sini plandaki atamadan farklı bir kategoriye çevirelim
    persisted_idea = db_session.query(SocialIdea).filter_by(id=res.newly_persisted_ideas[0].id).first()
    other_cat = [c for c in cats if c.id != persisted_idea.category_id][0]
    persisted_idea.category_id = other_cat.id
    db_session.commit()

    with pytest.raises(SocialIdeaRetryPersistenceError) as exc:
        persist_social_idea_retry_results(
            db_session,
            attempt_id=retry_att.id,
            task_id="task-48",
            category_results=(),
            now=T0,
        )
    assert exc.value.error_code == "IDEA_RETRY_PERSISTENCE_INCONSISTENT"
    assert exc.value.field == "category_id"


def test_49_completed_replay_wrong_platform_format_fails_closed(
    enable_flag, db_session, make_workspace, make_scoring_run, make_keyword
):
    """49. Generated target fikri bağlı hedefle uyuşmayan target_platform/content_format'a sahipse replay reddedilir."""
    targets_data = [("instagram", "post"), ("twitter", "thread")]
    ws, run, kws, brief, cats, targets, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-49",
        persist_first_target=True,
        targets_data=targets_data,
    )
    results = _build_valid_retry_results(start.plan, retry_att.id, targets, kws)
    res = persist_social_idea_retry_results(
        db_session,
        attempt_id=retry_att.id,
        task_id="task-49",
        category_results=results,
        now=T0,
    )
    db_session.commit()

    persisted_idea = db_session.query(SocialIdea).filter_by(id=res.newly_persisted_ideas[0].id).first()
    persisted_idea.target_platform = "linkedin"
    persisted_idea.content_format = "article"
    db_session.commit()

    with pytest.raises(SocialIdeaRetryPersistenceError) as exc:
        persist_social_idea_retry_results(
            db_session,
            attempt_id=retry_att.id,
            task_id="task-49",
            category_results=(),
            now=T0,
        )
    assert exc.value.error_code == "IDEA_RETRY_PERSISTENCE_INCONSISTENT"
    assert exc.value.field == "target_platform"


def test_50_completed_replay_non_brief_keyword_fails_closed(
    enable_flag, db_session, make_workspace, make_scoring_run, make_keyword
):
    """50. Generated target fikri brief dışı keyword_id taşırsa replay fail-closed reddedilir."""
    targets_data = [("instagram", "post"), ("twitter", "thread")]
    ws, run, kws, brief, cats, targets, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-50",
        persist_first_target=True,
        targets_data=targets_data,
    )
    results = _build_valid_retry_results(start.plan, retry_att.id, targets, kws)
    res = persist_social_idea_retry_results(
        db_session,
        attempt_id=retry_att.id,
        task_id="task-50",
        category_results=results,
        now=T0,
    )
    db_session.commit()

    other_kw = make_keyword(
        text_value="kw-non-brief-test50",
        brand_profile_id=ws.id,
    )
    persisted_idea = db_session.query(SocialIdea).filter_by(id=res.newly_persisted_ideas[0].id).first()
    persisted_idea.keyword_id = other_kw.id
    db_session.commit()

    with pytest.raises(SocialIdeaRetryPersistenceError) as exc:
        persist_social_idea_retry_results(
            db_session,
            attempt_id=retry_att.id,
            task_id="task-50",
            category_results=(),
            now=T0,
        )
    assert exc.value.error_code == "IDEA_RETRY_PERSISTENCE_INCONSISTENT"
    assert exc.value.field == "keyword_id"


def test_51_completed_replay_stale_category_fails_closed(
    enable_flag, db_session, make_workspace, make_scoring_run, make_keyword
):
    """51. Generated target fikri stale kategoriye bağlıysa replay fail-closed reddedilir."""
    targets_data = [("instagram", "post"), ("twitter", "thread")]
    ws, run, kws, brief, cats, targets, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-51",
        persist_first_target=True,
        targets_data=targets_data,
    )
    results = _build_valid_retry_results(start.plan, retry_att.id, targets, kws)
    res = persist_social_idea_retry_results(
        db_session,
        attempt_id=retry_att.id,
        task_id="task-51",
        category_results=results,
        now=T0,
    )
    db_session.commit()

    # Kategori stale yapılsın
    cat = db_session.query(SocialCategory).filter_by(id=res.newly_persisted_ideas[0].category_id).first()
    cat.is_stale = True
    db_session.commit()

    with pytest.raises(SocialIdeaRetryPersistenceError) as exc:
        persist_social_idea_retry_results(
            db_session,
            attempt_id=retry_att.id,
            task_id="task-51",
            category_results=(),
            now=T0,
        )
    assert exc.value.error_code == "IDEA_RETRY_PERSISTENCE_INCONSISTENT"
    assert exc.value.field == "category_id"


def test_52_completed_replay_invalid_idea_fields_fails_closed(
    enable_flag, db_session, make_workspace, make_scoring_run, make_keyword
):
    """52. Generated target fikrinde geçersiz title/description/trend varsa replay fail-closed reddedilir."""
    targets_data = [("instagram", "post"), ("twitter", "thread")]
    ws, run, kws, brief, cats, targets, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-52",
        persist_first_target=True,
        targets_data=targets_data,
    )
    results = _build_valid_retry_results(start.plan, retry_att.id, targets, kws)
    res = persist_social_idea_retry_results(
        db_session,
        attempt_id=retry_att.id,
        task_id="task-52",
        category_results=results,
        now=T0,
    )
    db_session.commit()

    persisted_idea = db_session.query(SocialIdea).filter_by(id=res.newly_persisted_ideas[0].id).first()
    persisted_idea.idea_title = "   "  # Boş/whitespaces title
    db_session.commit()

    with pytest.raises(SocialIdeaRetryPersistenceError) as exc:
        persist_social_idea_retry_results(
            db_session,
            attempt_id=retry_att.id,
            task_id="task-52",
            category_results=(),
            now=T0,
        )
    assert exc.value.error_code == "IDEA_RETRY_PERSISTENCE_INCONSISTENT"
    assert exc.value.field == "idea_title"


def test_53_social_idea_ai_result_subclass_rejected(
    enable_flag, db_session, make_workspace, make_scoring_run, make_keyword
):
    """53. SocialIdeaAIResult alt sınıfı (subclass) yeni yazımda reddedilir."""
    ws, run, kws, brief, cats, targets, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-53",
    )
    results = _build_valid_retry_results(start.plan, retry_att.id, targets, kws)

    class CustomAIResult(SocialIdeaAIResult):
        pass

    orig_r = results[0]
    forged_result = CustomAIResult(
        attempt_id=orig_r.attempt_id,
        category_id=orig_r.category_id,
        ideas=orig_r.ideas,
        ai_calls_used=orig_r.ai_calls_used,
    )

    with pytest.raises(SocialIdeaRetryPersistenceError) as exc:
        persist_social_idea_retry_results(
            db_session,
            attempt_id=retry_att.id,
            task_id="task-53",
            category_results=(forged_result,),
            now=T0,
        )
    assert exc.value.error_code == "IDEA_RETRY_PERSISTENCE_INVALID_INPUT"
    assert exc.value.field == "category_results"


def test_54_validated_social_idea_subclass_rejected(
    enable_flag, db_session, make_workspace, make_scoring_run, make_keyword
):
    """54. ValidatedSocialIdea alt sınıfı (subclass) yeni yazımda reddedilir."""
    ws, run, kws, brief, cats, targets, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-54",
    )
    results = _build_valid_retry_results(start.plan, retry_att.id, targets, kws)

    class CustomValidatedIdea(ValidatedSocialIdea):
        pass

    orig_idea = results[0].ideas[0]
    forged_idea = CustomValidatedIdea(
        target_id=orig_idea.target_id,
        primary_keyword_id=orig_idea.primary_keyword_id,
        target_platform=orig_idea.target_platform,
        content_format=orig_idea.content_format,
        idea_title=orig_idea.idea_title,
        idea_description=orig_idea.idea_description,
        trend_alignment=orig_idea.trend_alignment,
    )
    forged_result = SocialIdeaAIResult(
        attempt_id=results[0].attempt_id,
        category_id=results[0].category_id,
        ideas=(forged_idea,),
        ai_calls_used=results[0].ai_calls_used,
    )

    with pytest.raises(SocialIdeaRetryPersistenceError) as exc:
        persist_social_idea_retry_results(
            db_session,
            attempt_id=retry_att.id,
            task_id="task-54",
            category_results=(forged_result,),
            now=T0,
        )
    assert exc.value.error_code == "IDEA_RETRY_PERSISTENCE_INVALID_INPUT"
    assert exc.value.field == "ideas"


def test_55_result_ai_calls_used_invalid_types_rejected(
    enable_flag, db_session, make_workspace, make_scoring_run, make_keyword
):
    """55. Result.ai_calls_used için True, -1, 1.5, '1' değerleri reddedilir."""
    ws, run, kws, brief, cats, targets, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-55",
    )
    results = _build_valid_retry_results(start.plan, retry_att.id, targets, kws)
    orig_r = results[0]

    for bad_val in [True, -1, 1.5, "1"]:
        bad_r = SocialIdeaAIResult(
            attempt_id=orig_r.attempt_id,
            category_id=orig_r.category_id,
            ideas=orig_r.ideas,
            ai_calls_used=bad_val,  # type: ignore
        )
        with pytest.raises(SocialIdeaRetryPersistenceError) as exc:
            persist_social_idea_retry_results(
                db_session,
                attempt_id=retry_att.id,
                task_id="task-55",
                category_results=(bad_r,),
                now=T0,
            )
        assert exc.value.error_code == "IDEA_RETRY_PERSISTENCE_INVALID_INPUT"
        assert exc.value.field == "ai_calls_used"


def test_56_result_ai_calls_used_zero_and_positive_accepted_no_change_to_persistence(
    enable_flag, db_session, make_workspace, make_scoring_run, make_keyword
):
    """56. Result.ai_calls_used=0 ve pozitif int kabul edilir fakat persistence sonucunu değiştirmez."""
    ws, run, kws, brief, cats, targets, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-56",
    )
    results = _build_valid_retry_results(start.plan, retry_att.id, targets, kws)
    orig_r = results[0]

    # ai_calls_used = 7
    r7 = SocialIdeaAIResult(
        attempt_id=orig_r.attempt_id,
        category_id=orig_r.category_id,
        ideas=orig_r.ideas,
        ai_calls_used=7,
    )
    res = persist_social_idea_retry_results(
        db_session,
        attempt_id=retry_att.id,
        task_id="task-56",
        category_results=(r7,),
        ai_calls_used=7,
        now=T0,
    )
    assert res.status == "completed"
    assert res.newly_persisted_count == len(orig_r.ideas)
    assert len(res.accepted_target_ids) == len(orig_r.ideas)


def test_57_source_snapshot_parser_inner_error_does_not_leak(
    enable_flag, db_session, make_workspace, make_scoring_run, make_keyword
):
    """57. Source snapshot parser'ın dinamik iç hata metni dış hataya sızmaz."""
    ws, run, kws, brief, cats, targets, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-57",
    )
    # Source attempt coverage'ına özel gizli bir hata metni enjekte edelim
    secret_internal_tag = "SECRET_SOURCE_INTERNAL_EXCEPTION_XYZ_123"
    source_att.coverage = {"request": {"total_ideas": secret_internal_tag}}
    db_session.commit()

    results = _build_valid_retry_results(start.plan, retry_att.id, targets, kws)
    with pytest.raises(SocialIdeaRetryPersistenceError) as exc:
        persist_social_idea_retry_results(
            db_session,
            attempt_id=retry_att.id,
            task_id="task-57",
            category_results=results,
            now=T0,
        )
    assert exc.value.error_code == "IDEA_RETRY_PERSISTENCE_INCONSISTENT"
    assert secret_internal_tag not in exc.value.message
    assert exc.value.__cause__ is None


def test_58_retry_snapshot_parser_inner_error_does_not_leak(
    enable_flag, db_session, make_workspace, make_scoring_run, make_keyword
):
    """58. Retry snapshot parser'ın dinamik iç hata metni dış hataya sızmaz."""
    ws, run, kws, brief, cats, targets, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-58",
    )
    secret_internal_tag = "SECRET_RETRY_INTERNAL_EXCEPTION_ABC_789"
    cov = dict(retry_att.coverage)
    cov["baseline"] = secret_internal_tag
    retry_att.coverage = cov
    db_session.commit()

    results = _build_valid_retry_results(start.plan, retry_att.id, targets, kws)
    with pytest.raises(SocialIdeaRetryPersistenceError) as exc:
        persist_social_idea_retry_results(
            db_session,
            attempt_id=retry_att.id,
            task_id="task-58",
            category_results=results,
            now=T0,
        )
    assert exc.value.error_code == "IDEA_RETRY_PERSISTENCE_INCONSISTENT"
    assert secret_internal_tag not in exc.value.message
    assert exc.value.__cause__ is None


def test_59_hardened_concurrency_no_deadlock_single_success_no_duplicate_ideas(
    enable_flag, db_session, make_workspace, make_scoring_run, make_keyword
):
    """59. Eşzamanlılık testi: iki thread de sonlanır, deadlock olmaz, tek yazım başarısı olur, kopya fikir oluşmaz."""
    targets_data = [("instagram", "post"), ("twitter", "thread")]
    ws, run, kws, brief, cats, targets, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-59",
        persist_first_target=True,
        targets_data=targets_data,
    )
    results = _build_valid_retry_results(start.plan, retry_att.id, targets, kws)

    session_a = SessionLocal()
    session_b = SessionLocal()

    barrier = threading.Barrier(2)
    thread_outcomes: list[dict[str, Any]] = []

    def worker_thread(session: Session, t_name: str):
        outcome: dict[str, Any] = {"name": t_name, "success": False, "already_completed": False, "error": None}
        try:
            barrier.wait(timeout=5.0)
            res = persist_social_idea_retry_results(
                session,
                attempt_id=retry_att.id,
                task_id="task-59",
                category_results=results,
                now=T0,
            )
            session.commit()
            outcome["success"] = True
            outcome["already_completed"] = res.already_completed
        except Exception as e:
            session.rollback()
            outcome["error"] = e
        finally:
            session.close()
            thread_outcomes.append(outcome)

    t_a = threading.Thread(target=worker_thread, args=(session_a, "thread_A"))
    t_b = threading.Thread(target=worker_thread, args=(session_b, "thread_B"))

    t_a.start()
    t_b.start()

    t_a.join(timeout=10.0)
    t_b.join(timeout=10.0)

    # 1. İki thread'in de sonlandığı ve deadlock olmadığı
    assert not t_a.is_alive()
    assert not t_b.is_alive()

    # 2. Tam bir yazım başarısı
    successes = [o for o in thread_outcomes if o["success"] is True]
    assert len(successes) == 1
    assert successes[0]["already_completed"] is False

    # 3. Diğer çağrının güvenli hata alması (already_completed durumunda dolu results verildiği için IDEA_RETRY_PERSISTENCE_INVALID_INPUT)
    errors = [o for o in thread_outcomes if o["error"] is not None]
    assert len(errors) == 1
    err = errors[0]["error"]
    assert isinstance(err, SocialIdeaRetryPersistenceError)
    assert err.error_code == "IDEA_RETRY_PERSISTENCE_INVALID_INPUT"

    # 4. DB'de kopya fikir olmadığını doğrula
    db_session.expire_all()
    db_ideas = db_session.query(SocialIdea).filter_by(brief_id=brief.id, is_stale=False).all()
    # Baseline 1 fikir + Retry 1 fikir = toplam tam 2 fikir
    assert len(db_ideas) == 2
    seen_target_ids = [idea.brief_target_id for idea in db_ideas]
    assert len(seen_target_ids) == len(set(seen_target_ids))

