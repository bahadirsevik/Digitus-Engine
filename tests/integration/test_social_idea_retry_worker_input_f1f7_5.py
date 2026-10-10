# -*- coding: utf-8 -*-
"""MİKRO FAZ F1-F.7.5 — Ideas Retry Otoriter Worker Input Hazırlığı Entegrasyon Testleri.

Tüm testler gerçek PostgreSQL test veritabanını ve SQLAlchemy oturumunu kullanır.
Test edilen alanlar:
1. Pending retry attempt başarıyla claim edilir ve running olur.
2. task_id/worker ownership doğru yazılır.
3. Happy path yalnız missing target’lar için prompt üretir.
4. Persisted target promptlara girmez.
5. İki missing target aynı kategoriye atanmışsa tek prompt içinde gruplanır.
6. Kategori ve target sırası deterministiktir.
7. Her target spec requested_count=1’dir.
8. Prompt attempt_id retry attempt ID’sidir; source attempt ID değildir.
9. Keyword sırası position ile korunur.
10. Completed replay prompt_inputs=() döndürür ve AI hazırlığı yapmaz.
11. Completed replay snapshot alanlarını doğru döndürür.
12. Source attempt başka brief’e aitse fail-closed.
13. Source stage ideas değilse fail-closed.
14. Source pending/running ise fail-closed.
15. Source snapshot bozuksa fail-closed.
16. Retry snapshot bozuksa fail-closed.
17. Retry/source plan paritesi bozuksa fail-closed.
18. Canonical target DB sırası snapshot ile uyuşmazsa fail-closed.
19. Brief stale ise fail-closed.
20. Assignment version değişmişse fail-closed.
21. Kategori eksik/stale/farklı brief veya run ise fail-closed.
22. Keyword cardinality/position/snapshot bozuksa fail-closed.
23. Target platform-format matriste geçersizse fail-closed.
24. Naive now reddedilir.
25. Geçersiz attempt_id ve task_id reddedilir.
26. Fonksiyon commit/rollback çağırmaz.
27. SocialIdea satırı oluşturmaz, silmez veya değiştirmez.
28. Attempt coverage ve requested_target_ids değiştirilmez.
29. Ham ID, snapshot ve iç exception metni güvenli hata mesajına sızmaz.
30. Planın bütün missing target’ları promptlarda tam bir kez kapsanır.
31. Completed replay yolunda SocialIdea tablosu okunmaz.
32. Yanlış task ile running attempt devralınamaz.
33. Süresi dolmuş/failed attempt claim edilemez veya mevcut helper sözleşmesine göre güvenli hata verir.
34. Mevcut normal ideas worker-input davranışı regresyona uğramaz.
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.config import settings
from app.core.social.idea_flow import begin_social_idea_generation
from app.core.social.idea_planner import build_social_idea_generation_plan
from app.core.social.idea_retry_flow import begin_social_idea_retry
from app.core.social.idea_retry_worker_input import (
    SocialIdeaRetryWorkerInputError,
    SocialIdeaRetryWorkerPreparation,
    prepare_social_idea_retry_worker_inputs,
)
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
from app.generators.social.attempt_state import claim_ideas_retry_attempt_for_worker
from app.generators.social.brief_idea_prompt import SocialIdeaPromptInput
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


def _setup_fresh_environment(
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
    num_kws: int = 3,
    workspace_name: str = "Retry Worker Input Brand",
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
    workspace_name: str = "Retry Worker Input Brand",
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
):
    """Worker testleri için preflight edilmiş bir ideas_retry attempt'i hazırlar."""
    ws, run, kws, brief, cats, targets = _setup_environment_with_categories(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        num_categories=num_categories,
        targets_data=targets_data,
    )
    source_att = _create_source_ideas_attempt(db_session, brief, cats, targets)

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
    db_session.commit()

    retry_att = (
        db_session.query(SocialGenerationAttempt)
        .filter(SocialGenerationAttempt.id == start.attempt_id)
        .one()
    )

    return ws, run, brief, cats, targets, kws, source_att, start, retry_att


# ==================== TESTLER ====================


def test_01_pending_retry_attempt_claimed_and_transitions_to_running(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """1. Pending retry attempt başarıyla claim edilir ve running olur."""
    ws, run, brief, cats, targets, kws, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-01"
    )
    assert retry_att.status == "pending"
    assert retry_att.task_id is None

    prep = prepare_social_idea_retry_worker_inputs(
        db_session,
        attempt_id=retry_att.id,
        task_id="task-01",
        now=T0,
    )

    assert isinstance(prep, SocialIdeaRetryWorkerPreparation)
    assert prep.already_completed is False
    assert retry_att.status == "running"


def test_02_task_id_and_worker_ownership_written_correctly(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """2. task_id/worker ownership doğru yazılır."""
    ws, run, brief, cats, targets, kws, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-02"
    )

    prep = prepare_social_idea_retry_worker_inputs(
        db_session,
        attempt_id=retry_att.id,
        task_id="task-02",
        now=T0,
    )

    assert retry_att.task_id == "task-02"
    assert retry_att.started_at == T0
    assert retry_att.heartbeat_at == T0
    assert retry_att.lease_expires_at == T0 + timedelta(seconds=1500)
    assert prep.task_id == "task-02"


def test_03_happy_path_produces_prompts_only_for_missing_targets(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """3. Happy path yalnız missing target’lar için prompt üretir."""
    # 3 target var: target[0] persist edildi, target[1] ve target[2] missing
    ws, run, brief, cats, targets, kws, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, persist_first_target=True
    )

    prep = prepare_social_idea_retry_worker_inputs(
        db_session,
        attempt_id=retry_att.id,
        task_id="task-03",
        now=T0,
    )

    assert prep.missing_target_ids == (targets[1].id, targets[2].id)
    assert prep.persisted_target_ids_at_start == (targets[0].id,)

    prompt_target_ids = []
    for p in prep.prompt_inputs:
        for spec in p.target_specs:
            prompt_target_ids.append(spec.target_id)

    assert set(prompt_target_ids) == {targets[1].id, targets[2].id}
    assert len(prompt_target_ids) == 2


def test_04_persisted_target_does_not_enter_prompts(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """4. Persisted target promptlara girmez."""
    ws, run, brief, cats, targets, kws, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, persist_first_target=True
    )

    prep = prepare_social_idea_retry_worker_inputs(
        db_session,
        attempt_id=retry_att.id,
        task_id="task-04",
        now=T0,
    )

    for p in prep.prompt_inputs:
        for spec in p.target_specs:
            assert spec.target_id != targets[0].id


def test_05_two_missing_targets_assigned_to_same_category_grouped_in_single_prompt(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """5. İki missing target aynı kategoriye atanmışsa tek prompt içinde gruplanır."""
    # 1 kategori, 2 target: target[0] ve target[1] her ikisi de cat[0]'a atanacak
    targets_data = [("instagram", "post"), ("twitter", "thread")]
    ws, run, brief, cats, targets, kws, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        num_categories=1,
        targets_data=targets_data,
        persist_first_target=False,
    )

    prep = prepare_social_idea_retry_worker_inputs(
        db_session,
        attempt_id=retry_att.id,
        task_id="task-05",
        now=T0,
    )

    # 1 kategori olduğu için tek bir SocialIdeaPromptInput olmalı ve 2 spec taşımalı
    assert len(prep.prompt_inputs) == 1
    assert prep.prompt_inputs[0].category_id == cats[0].id
    assert len(prep.prompt_inputs[0].target_specs) == 2
    assert prep.prompt_inputs[0].target_specs[0].target_id == targets[0].id
    assert prep.prompt_inputs[0].target_specs[1].target_id == targets[1].id


def test_06_category_and_target_order_is_deterministic(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """6. Kategori ve target sırası deterministiktir."""
    ws, run, brief, cats, targets, kws, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, persist_first_target=False
    )

    prep = prepare_social_idea_retry_worker_inputs(
        db_session,
        attempt_id=retry_att.id,
        task_id="task-06",
        now=T0,
    )

    # Kategori sırası source_plan kategori sırasını izlemeli
    cat_order = [p.category_id for p in prep.prompt_inputs]
    assert cat_order == [c.id for c in cats if c.id in cat_order]

    # Her prompt içindeki hedefler canonical_target_ids sırasını izlemeli
    canonical_ids = tuple(t.id for t in targets)
    for p in prep.prompt_inputs:
        spec_ids = [s.target_id for s in p.target_specs]
        expected_order = sorted(spec_ids, key=lambda tid: canonical_ids.index(tid))
        assert spec_ids == expected_order


def test_07_each_target_spec_has_requested_count_equal_one(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """7. Her target spec requested_count=1’dir."""
    ws, run, brief, cats, targets, kws, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, persist_first_target=False
    )

    prep = prepare_social_idea_retry_worker_inputs(
        db_session,
        attempt_id=retry_att.id,
        task_id="task-07",
        now=T0,
    )

    for p in prep.prompt_inputs:
        for spec in p.target_specs:
            assert spec.requested_count == 1


def test_08_prompt_attempt_id_is_retry_attempt_id_not_source_attempt_id(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """8. Prompt attempt_id retry attempt ID’sidir; source attempt ID değildir."""
    ws, run, brief, cats, targets, kws, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, persist_first_target=True
    )

    prep = prepare_social_idea_retry_worker_inputs(
        db_session,
        attempt_id=retry_att.id,
        task_id="task-08",
        now=T0,
    )

    for p in prep.prompt_inputs:
        assert p.attempt_id == retry_att.id
        assert p.attempt_id != source_att.id


def test_09_keyword_order_preserved_by_position(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """9. Keyword sırası position ile korunur."""
    ws, run, brief, cats, targets, kws, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, persist_first_target=True
    )

    prep = prepare_social_idea_retry_worker_inputs(
        db_session,
        attempt_id=retry_att.id,
        task_id="task-09",
        now=T0,
    )

    for p in prep.prompt_inputs:
        assert len(p.keywords) == 3
        assert [k.position for k in p.keywords] == [0, 1, 2]
        assert [k.keyword_snapshot for k in p.keywords] == [kw.keyword for kw in kws]


def test_10_completed_replay_returns_empty_prompt_inputs(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """10. Completed replay prompt_inputs=() döndürür ve AI hazırlığı yapmaz."""
    ws, run, brief, cats, targets, kws, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-10"
    )

    retry_att.status = "completed"
    retry_att.task_id = "task-10"
    retry_att.completed_at = T0
    db_session.commit()

    prep = prepare_social_idea_retry_worker_inputs(
        db_session,
        attempt_id=retry_att.id,
        task_id="task-10",
        now=T0,
    )

    assert prep.already_completed is True
    assert prep.prompt_inputs == ()


def test_11_completed_replay_returns_correct_snapshot_fields(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """11. Completed replay snapshot alanlarını doğru döndürür."""
    ws, run, brief, cats, targets, kws, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-11"
    )

    retry_att.status = "completed"
    retry_att.task_id = "task-11"
    retry_att.completed_at = T0
    db_session.commit()

    prep = prepare_social_idea_retry_worker_inputs(
        db_session,
        attempt_id=retry_att.id,
        task_id="task-11",
        now=T0,
    )

    assert prep.canonical_target_ids == tuple(t.id for t in targets)
    assert prep.persisted_target_ids_at_start == (targets[0].id,)
    assert prep.missing_target_ids == (targets[1].id, targets[2].id)
    assert prep.source_attempt_id == source_att.id


def test_12_source_attempt_different_brief_raises_fail_closed(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """12. Source attempt başka brief’e aitse fail-closed."""
    ws, run, brief, cats, targets, kws, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword
    )

    other_brief = _create_test_brief(
        db_session,
        run.id,
        kws,
        targets_data=[("instagram", "post")],
    )
    source_att.brief_id = other_brief.id
    db_session.commit()

    with pytest.raises(SocialIdeaRetryWorkerInputError) as exc_info:
        prepare_social_idea_retry_worker_inputs(
            db_session,
            attempt_id=retry_att.id,
            task_id="task-12",
            now=T0,
        )
    assert exc_info.value.error_code == "IDEA_RETRY_WORKER_INPUT_INCONSISTENT"


def test_13_source_stage_not_ideas_raises_fail_closed(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """13. Source stage ideas değilse fail-closed."""
    ws, run, brief, cats, targets, kws, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword
    )

    source_att.stage = "categories"
    db_session.commit()

    with pytest.raises(SocialIdeaRetryWorkerInputError) as exc_info:
        prepare_social_idea_retry_worker_inputs(
            db_session,
            attempt_id=retry_att.id,
            task_id="task-13",
            now=T0,
        )
    assert exc_info.value.error_code == "IDEA_RETRY_WORKER_INPUT_INCONSISTENT"


def test_14_source_pending_or_running_raises_fail_closed(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """14. Source pending/running ise fail-closed."""
    ws, run, brief, cats, targets, kws, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword
    )

    source_att.status = "running"
    db_session.commit()

    with pytest.raises(SocialIdeaRetryWorkerInputError) as exc_info:
        prepare_social_idea_retry_worker_inputs(
            db_session,
            attempt_id=retry_att.id,
            task_id="task-14",
            now=T0,
        )
    assert exc_info.value.error_code == "IDEA_RETRY_WORKER_INPUT_INCONSISTENT"


def test_15_source_snapshot_corrupted_raises_fail_closed(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """15. Source snapshot bozuksa fail-closed."""
    ws, run, brief, cats, targets, kws, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword
    )

    source_att.coverage = {"schema_version": "corrupted"}
    db_session.commit()

    with pytest.raises(SocialIdeaRetryWorkerInputError) as exc_info:
        prepare_social_idea_retry_worker_inputs(
            db_session,
            attempt_id=retry_att.id,
            task_id="task-15",
            now=T0,
        )
    assert exc_info.value.error_code == "IDEA_RETRY_WORKER_INPUT_INCONSISTENT"


def test_16_retry_snapshot_corrupted_raises_fail_closed(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """16. Retry snapshot bozuksa fail-closed."""
    ws, run, brief, cats, targets, kws, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword
    )

    cov = copy.deepcopy(retry_att.coverage)
    cov["plan"] = None
    retry_att.coverage = cov
    db_session.commit()

    with pytest.raises(SocialIdeaRetryWorkerInputError) as exc_info:
        prepare_social_idea_retry_worker_inputs(
            db_session,
            attempt_id=retry_att.id,
            task_id="task-16",
            now=T0,
        )
    assert exc_info.value.error_code == "IDEA_RETRY_WORKER_INPUT_INCONSISTENT"


def test_17_retry_source_plan_parity_mismatch_raises_fail_closed(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """17. Retry/source plan paritesi bozuksa fail-closed."""
    ws, run, brief, cats, targets, kws, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword
    )

    cov = copy.deepcopy(retry_att.coverage)
    cov["plan"]["total_requested"] = 99
    retry_att.coverage = cov
    db_session.commit()

    with pytest.raises(SocialIdeaRetryWorkerInputError) as exc_info:
        prepare_social_idea_retry_worker_inputs(
            db_session,
            attempt_id=retry_att.id,
            task_id="task-17",
            now=T0,
        )
    assert exc_info.value.error_code == "IDEA_RETRY_WORKER_INPUT_INCONSISTENT"


def test_18_canonical_target_db_mismatch_raises_fail_closed(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """18. Canonical target DB sırası snapshot ile uyuşmazsa fail-closed."""
    ws, run, brief, cats, targets, kws, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword
    )

    # Ek bir hedef ekleyerek DB hedefleri ile snapshot hedeflerinin uyuşmamasını sağla (non-video format)
    extra_target = SocialBriefTarget(
        brief_id=brief.id,
        platform="linkedin",
        content_format="article",
    )
    db_session.add(extra_target)
    db_session.commit()

    with pytest.raises(SocialIdeaRetryWorkerInputError) as exc_info:
        prepare_social_idea_retry_worker_inputs(
            db_session,
            attempt_id=retry_att.id,
            task_id="task-18",
            now=T0,
        )
    assert exc_info.value.error_code == "IDEA_RETRY_WORKER_INPUT_INCONSISTENT"


def test_19_brief_stale_raises_fail_closed(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """19. Brief stale ise fail-closed."""
    ws, run, brief, cats, targets, kws, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword
    )

    brief.is_stale = True
    db_session.commit()

    with pytest.raises(SocialIdeaRetryWorkerInputError) as exc_info:
        prepare_social_idea_retry_worker_inputs(
            db_session,
            attempt_id=retry_att.id,
            task_id="task-19",
            now=T0,
        )
    assert exc_info.value.error_code == "IDEA_RETRY_WORKER_INPUT_INCONSISTENT"


def test_20_assignment_version_mismatch_raises_fail_closed(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """20. Assignment version değişmişse fail-closed."""
    ws, run, brief, cats, targets, kws, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword
    )

    brief.channel_assignment_version = 2
    db_session.commit()

    with pytest.raises(SocialIdeaRetryWorkerInputError) as exc_info:
        prepare_social_idea_retry_worker_inputs(
            db_session,
            attempt_id=retry_att.id,
            task_id="task-20",
            now=T0,
        )
    assert exc_info.value.error_code == "IDEA_RETRY_WORKER_INPUT_INCONSISTENT"


def test_21_category_stale_or_run_mismatch_raises_fail_closed(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """21. Kategori eksik/stale/farklı brief veya run ise fail-closed."""
    ws, run, brief, cats, targets, kws, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword
    )

    cats[0].is_stale = True
    db_session.commit()

    with pytest.raises(SocialIdeaRetryWorkerInputError) as exc_info:
        prepare_social_idea_retry_worker_inputs(
            db_session,
            attempt_id=retry_att.id,
            task_id="task-21",
            now=T0,
        )
    assert exc_info.value.error_code == "IDEA_RETRY_WORKER_INPUT_INCONSISTENT"
    assert exc_info.value.category_id == cats[0].id


def test_22_keyword_cardinality_or_gap_raises_fail_closed(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """22. Keyword cardinality/position/snapshot bozuksa fail-closed."""
    ws, run, brief, cats, targets, kws, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword
    )

    # Position kesintisi yarat (0, 1 yerine 0, 2)
    kw_rows = (
        db_session.query(SocialBriefKeyword)
        .filter(SocialBriefKeyword.brief_id == brief.id)
        .order_by(SocialBriefKeyword.position.asc())
        .all()
    )
    kw_rows[1].position = 5
    db_session.commit()

    with pytest.raises(SocialIdeaRetryWorkerInputError) as exc_info:
        prepare_social_idea_retry_worker_inputs(
            db_session,
            attempt_id=retry_att.id,
            task_id="task-22",
            now=T0,
        )
    assert exc_info.value.error_code == "IDEA_RETRY_WORKER_INPUT_INCONSISTENT"


def test_23_target_format_matrix_invalid_raises_fail_closed(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """23. Target platform-format matriste geçersizse fail-closed."""
    ws, run, brief, cats, targets, kws, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword
    )

    targets[0].content_format = "invalid_format_xyz"
    db_session.commit()

    with pytest.raises(SocialIdeaRetryWorkerInputError) as exc_info:
        prepare_social_idea_retry_worker_inputs(
            db_session,
            attempt_id=retry_att.id,
            task_id="task-23",
            now=T0,
        )
    assert exc_info.value.error_code == "IDEA_RETRY_WORKER_INPUT_INCONSISTENT"


def test_24_naive_now_is_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """24. Naive now reddedilir."""
    ws, run, brief, cats, targets, kws, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword
    )

    naive_now = datetime(2026, 9, 25, 12, 0, 0)  # tzinfo None
    with pytest.raises(ValueError, match="timezone-aware"):
        prepare_social_idea_retry_worker_inputs(
            db_session,
            attempt_id=retry_att.id,
            task_id="task-24",
            now=naive_now,
        )


def test_25_invalid_attempt_id_and_task_id_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """25. Geçersiz attempt_id ve task_id reddedilir."""
    with pytest.raises(SocialIdeaRetryWorkerInputError) as exc1:
        prepare_social_idea_retry_worker_inputs(
            db_session,
            attempt_id=0,
            task_id="task-25",
            now=T0,
        )
    assert exc1.value.error_code == "IDEA_RETRY_WORKER_INPUT_INVALID"

    with pytest.raises(SocialIdeaRetryWorkerInputError) as exc2:
        prepare_social_idea_retry_worker_inputs(
            db_session,
            attempt_id=True,  # bool reddedilmeli
            task_id="task-25",
            now=T0,
        )
    assert exc2.value.error_code == "IDEA_RETRY_WORKER_INPUT_INVALID"

    with pytest.raises(SocialIdeaRetryWorkerInputError) as exc3:
        prepare_social_idea_retry_worker_inputs(
            db_session,
            attempt_id=1,
            task_id="",  # boş task_id
            now=T0,
        )
    assert exc3.value.error_code == "IDEA_RETRY_WORKER_INPUT_INVALID"


def test_26_function_does_not_call_commit_or_rollback(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """26. Fonksiyon commit/rollback çağırmaz."""
    ws, run, brief, cats, targets, kws, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword
    )

    with patch.object(db_session, "commit") as mock_commit, patch.object(
        db_session, "rollback"
    ) as mock_rollback:
        prep = prepare_social_idea_retry_worker_inputs(
            db_session,
            attempt_id=retry_att.id,
            task_id="task-26",
            now=T0,
        )
        assert mock_commit.call_count == 0
        assert mock_rollback.call_count == 0


def test_27_zero_social_idea_mutation(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """27. SocialIdea satırı oluşturmaz, silmez veya değiştirmez."""
    ws, run, brief, cats, targets, kws, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword
    )

    count_before = db_session.query(SocialIdea).count()

    prep = prepare_social_idea_retry_worker_inputs(
        db_session,
        attempt_id=retry_att.id,
        task_id="task-27",
        now=T0,
    )

    count_after = db_session.query(SocialIdea).count()
    assert count_after == count_before


def test_28_attempt_coverage_and_requested_target_ids_unmutated(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """28. Attempt coverage ve requested_target_ids değiştirilmez."""
    ws, run, brief, cats, targets, kws, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword
    )

    cov_before = copy.deepcopy(retry_att.coverage)
    req_targets_before = list(retry_att.requested_target_ids)

    prep = prepare_social_idea_retry_worker_inputs(
        db_session,
        attempt_id=retry_att.id,
        task_id="task-28",
        now=T0,
    )

    db_session.refresh(retry_att)
    assert retry_att.coverage == cov_before
    assert list(retry_att.requested_target_ids) == req_targets_before


def test_29_error_messages_do_not_leak_raw_ids_or_sql(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """29. Ham ID, snapshot ve iç exception metni güvenli hata mesajına sızmaz."""
    ws, run, brief, cats, targets, kws, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword
    )

    # Var olmayan attempt_id
    with pytest.raises(SocialIdeaRetryWorkerInputError) as exc_info:
        prepare_social_idea_retry_worker_inputs(
            db_session,
            attempt_id=999999,
            task_id="task-29",
            now=T0,
        )
    msg = str(exc_info.value)
    assert "999999" not in msg
    assert "SELECT" not in msg
    assert "SocialGenerationAttempt" not in msg


def test_30_all_missing_targets_covered_exactly_once_in_prompts(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """30. Planın bütün missing target’ları promptlarda tam bir kez kapsanır."""
    ws, run, brief, cats, targets, kws, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, persist_first_target=True
    )

    prep = prepare_social_idea_retry_worker_inputs(
        db_session,
        attempt_id=retry_att.id,
        task_id="task-30",
        now=T0,
    )

    covered_ids = []
    for p in prep.prompt_inputs:
        for spec in p.target_specs:
            covered_ids.append(spec.target_id)

    assert tuple(covered_ids) == prep.missing_target_ids
    assert len(covered_ids) == len(set(covered_ids))


def test_31_completed_replay_does_not_read_social_ideas_table(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """31. Completed replay yolunda SocialIdea tablosu okunmaz."""
    ws, run, brief, cats, targets, kws, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-31"
    )

    retry_att.status = "completed"
    retry_att.task_id = "task-31"
    retry_att.completed_at = T0
    db_session.commit()

    queried_entities = []

    def before_compile(statement):
        # statement text içinde SocialIdea arayalım
        sql_text = str(statement)
        if "social_ideas" in sql_text:
            queried_entities.append("social_ideas")

    # DB seviyesinde query log event dinleyicisi
    event.listen(db_session.bind, "before_cursor_execute", lambda conn, cursor, statement, *args: before_compile(statement))

    prep = prepare_social_idea_retry_worker_inputs(
        db_session,
        attempt_id=retry_att.id,
        task_id="task-31",
        now=T0,
    )

    assert prep.already_completed is True
    assert "social_ideas" not in queried_entities


def test_32_running_attempt_cannot_be_claimed_by_different_task(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """32. Yanlış task ile running attempt devralınamaz."""
    ws, run, brief, cats, targets, kws, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-32-workerA"
    )

    # Worker A claim eder
    prepare_social_idea_retry_worker_inputs(
        db_session,
        attempt_id=retry_att.id,
        task_id="task-32-workerA",
        now=T0,
    )
    db_session.commit()

    # Worker B devralmaya çalışır
    with pytest.raises(SocialIdeaRetryWorkerInputError) as exc_info:
        prepare_social_idea_retry_worker_inputs(
            db_session,
            attempt_id=retry_att.id,
            task_id="task-32-workerB",
            now=T0,
        )
    assert exc_info.value.error_code == "IDEA_RETRY_WORKER_INPUT_INCONSISTENT"


def test_33_expired_or_failed_attempt_cannot_be_claimed(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """33. Süresi dolmuş/failed attempt claim edilemez veya mevcut helper sözleşmesine göre güvenli hata verir."""
    ws, run, brief, cats, targets, kws, source_att, start, retry_att = _setup_ready_retry_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword
    )

    # Lease süresini geçmişe çek
    retry_att.lease_expires_at = T0 - timedelta(seconds=10)
    db_session.commit()

    with pytest.raises(SocialIdeaRetryWorkerInputError) as exc_info:
        prepare_social_idea_retry_worker_inputs(
            db_session,
            attempt_id=retry_att.id,
            task_id="task-33",
            now=T0,
        )
    assert exc_info.value.error_code in ("IDEA_RETRY_WORKER_INPUT_INCONSISTENT", "IDEA_RETRY_WORKER_INPUT_INVALID")


def test_34_normal_ideas_worker_input_does_not_regress(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """34. Mevcut normal ideas worker-input davranışı regresyona uğramaz."""
    from app.core.social.idea_worker_input import (
        SocialIdeaWorkerPreparation as NormalPreparation,
        prepare_social_idea_worker_inputs as normal_prepare,
    )

    ws, run, kws, brief, cats, targets = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    cat_attempt = SocialGenerationAttempt(
        brief_id=brief.id,
        stage="categories",
        idempotency_key="cat-attempt-34",
        status="completed",
        heartbeat_at=T0,
        lease_expires_at=T0 + timedelta(seconds=1500),
        created_at=T0,
    )
    db_session.add(cat_attempt)
    db_session.commit()

    # Normal ideas preflight
    req = SocialBriefIdeasGenerateRequest(
        idempotency_key="normal-task-34",
        category_ids=[c.id for c in cats],
        ideas_per_category=3,
    )
    start = begin_social_idea_generation(
        db_session,
        brief_id=brief.id,
        brand_profile_id=ws.id,
        request=req,
    )
    db_session.commit()

    normal_prep = normal_prepare(
        db_session,
        attempt_id=start.attempt_id,
        task_id="task-normal-34",
        now=T0,
    )

    assert isinstance(normal_prep, NormalPreparation)
    assert normal_prep.already_completed is False
    assert len(normal_prep.prompt_inputs) == len(cats)
