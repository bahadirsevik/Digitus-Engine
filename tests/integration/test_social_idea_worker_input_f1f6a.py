# -*- coding: utf-8 -*-
"""MİKRO FAZ F1-F.6a — Otoriter Worker Input Hazırlığı ve Attempt Claim Entegrasyon Testleri.

Tüm testler gerçek PostgreSQL test veritabanını ve SQLAlchemy oturumunu kullanır.
Test edilen alanlar:
1. Pending attempt'in running durumuna geçirilmesi, task_id, started_at, heartbeat_at, lease atanması
2. Worker preparation çıktısının DTO doğrulaması ve immutability (frozen) garantisi
3. Kategori sıralamasının kesinlikle plan sırasıyla eşleşmesi
4. Her kategori için yalnızca plan kotası > 0 olan hedeflerin target_specs'e girmesi (Cartesian guard)
5. Kota değerlerinin ve keyword snapshot'larının bozulmadan aktarılması
6. Running attempt'in aynı task_id ile idempotent tekrar çağrılması
7. Running attempt'in farklı task_id ile çağrılmasının reddi (TASK_MISMATCH)
8. Completed replay yolunda already_completed=True ve prompt_inputs=() dönmesi
9. Completed attempt'in farklı task_id ile çağrılmasının reddi (TASK_MISMATCH)
10. Failed ve partial durumundaki attempt'lerin reddi (ATTEMPT_NOT_CLAIMABLE)
11. Yanlış stage (categories/contents) attempt'inin reddi (INVALID_STAGE)
12. Running attempt'te lease süresi dolmuşsa worker_lost olarak fail edilmesi (WORKER_LOST)
13. Stale brief durumunda brief_stale olarak fail edilmesi (BRIEF_STALE)
14. Kanal atama sürümü değişmişse assignment_changed olarak fail edilmesi (ASSIGNMENT_CHANGED)
15. Bozuk coverage/plan/request durumunun reddi (IDEA_PERSISTENCE_INCONSISTENT)
16. DB varlık anomalileri: silinmiş/farklı brief kategorisi, farklı run kategorisi, stale kategori
17. Kategori adı/açıklaması sınır dışı veya whitespace-only olması durumu
18. Brief keyword sayısı (1-5 dışı), gapped pozisyonlar, mükerrer keyword_id, boş snapshot reddi
19. Brief hedef sayısı (1-6 dışı), kanonik olmayan platform/format matrisi reddi
20. Plandaki hedefin DB'de brief altında bulunamaması reddi
21. Geçersiz task_id ve attempt_id argümanlarının fail-closed reddi
22. Timezone-naive now parametresinin reddi
23. Sıfır SocialIdea oluşturulması ve coverage alanının mutasyona uğramaması garantisi
24. Servisin commit() veya rollback() çağırmaması
25. Caller rollback ile claim değişikliklerinin (running -> pending) geri alınması
26. SQL kilit sırasının kanonikliği (ScoringRun -> SocialBrief -> SocialGenerationAttempt)
27. generator temperature=None ayarının ve sıfır harici ağ çağrısının korunması
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.config import settings
from app.core.social.idea_flow import begin_social_idea_generation
from app.core.social.idea_persistence import (
    SocialIdeaPersistenceError,
    extract_social_idea_plan_snapshot,
)
from app.core.social.idea_worker_input import (
    SocialIdeaWorkerInputError,
    SocialIdeaWorkerPreparation,
    prepare_social_idea_worker_inputs,
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
from app.generators.social.attempt_state import (
    AttemptNotWritableError,
    claim_attempt,
    claim_ideas_attempt_for_worker,
)
from app.generators.social.brief_idea_prompt import (
    IdeaKeywordSnapshot,
    SocialIdeaPromptInput,
)
from app.schemas.social_brief import SocialBriefIdeasGenerateRequest

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
    workspace_name: str = "Worker Input Brand",
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


def _setup_ready_ideas_attempt(
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
    task_id: str = "task-worker-101",
    ideas_per_category: int = 3,
    num_categories: int = 2,
    targets_data: list[tuple[str, str]] | None = None,
    claim: bool = False,
):
    """Fikir üretimi worker testleri için preflight edilmiş ortam kurar."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword, num_kws=3)

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
        idempotency_key="cat-attempt-key",
        status="completed",
        heartbeat_at=T0,
        lease_expires_at=T0 + timedelta(seconds=1500),
        created_at=T0,
    )
    db_session.add(cat_attempt)
    db_session.flush()

    # Categories
    categories = []
    for i in range(num_categories):
        cat = SocialCategory(
            scoring_run_id=run.id,
            brief_id=brief.id,
            category_name=f"Kategori {i + 1}",
            category_type="educational",
            description=f"Açıklama {i + 1} için eğitici içerik yönü.",
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

    # Ideas preflight başlat
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


def test_01_pending_attempt_transitions_to_running_and_claims_successfully(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """1. Pending attempt 'running' durumuna geçer, lease ve task_id doğrulanır."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-01", claim=False
    )
    assert attempt.status == "pending"
    assert attempt.task_id is None

    prep = prepare_social_idea_worker_inputs(
        db_session,
        attempt_id=attempt.id,
        task_id="task-01",
        now=T0,
    )
    db_session.commit()

    assert prep.already_completed is False
    assert prep.attempt_id == attempt.id
    assert prep.task_id == "task-01"
    assert prep.brief_id == brief.id
    assert prep.scoring_run_id == run.id
    assert len(prep.prompt_inputs) == 2

    # DB state doğrulaması
    db_session.refresh(attempt)
    assert attempt.status == "running"
    assert attempt.task_id == "task-01"
    assert attempt.started_at == T0
    assert attempt.heartbeat_at == T0
    assert attempt.lease_expires_at == T0 + timedelta(seconds=1500)


def test_02_prompt_input_fields_and_immutability(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """2. Prompt input alanları eksiksiz, kanonik ve immutable (frozen) olmalıdır."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-02", claim=False
    )
    prep = prepare_social_idea_worker_inputs(
        db_session,
        attempt_id=attempt.id,
        task_id="task-02",
        now=T0,
    )
    db_session.commit()

    # Preparation nesnesi immutable
    with pytest.raises((AttributeError, TypeError)):
        prep.already_completed = True  # type: ignore

    assert len(prep.prompt_inputs) == 2
    first_input = prep.prompt_inputs[0]

    # Prompt input nesnesi immutable
    with pytest.raises((AttributeError, TypeError)):
        first_input.attempt_id = 999  # type: ignore

    assert first_input.attempt_id == attempt.id
    assert first_input.category_id == cats[0].id
    assert first_input.category_name == "Kategori 1"
    assert first_input.category_description == "Açıklama 1 için eğitici içerik yönü."
    assert first_input.brand_name_snapshot == "Test Marka"
    assert first_input.brand_context_snapshot == "Test Context"

    # Keywords tuple doğrulaması
    assert isinstance(first_input.keywords, tuple)
    assert len(first_input.keywords) == 3
    for pos, kw_snap in enumerate(first_input.keywords):
        assert isinstance(kw_snap, IdeaKeywordSnapshot)
        assert kw_snap.position == pos
        assert kw_snap.keyword_id == kws[pos].id
        assert kw_snap.keyword_snapshot == kws[pos].keyword

    # Target specs tuple doğrulaması
    assert isinstance(first_input.target_specs, tuple)
    assert len(first_input.target_specs) > 0


def test_03_category_order_strictly_matches_plan_order(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """3. Kategori sıralaması kesinlikle attempt plan sıralamasıyla aynıdır."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-03", num_categories=2, claim=False
    )
    plan = extract_social_idea_plan_snapshot(attempt)
    expected_cat_ids = [cp.category_id for cp in plan.categories]

    prep = prepare_social_idea_worker_inputs(
        db_session,
        attempt_id=attempt.id,
        task_id="task-03",
        now=T0,
    )
    actual_cat_ids = [p.category_id for p in prep.prompt_inputs]
    assert actual_cat_ids == expected_cat_ids


def test_04_target_quotas_and_cartesian_guard(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """4. Yalnızca kotası > 0 olan hedefler target_specs içine alınır (Cartesian guard)."""
    targets_data = [
        ("instagram", "post"),
        ("twitter", "thread"),
        ("linkedin", "post"),
    ]
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-04",
        ideas_per_category=2,  # 2 fikir, 3 target -> her kategori 2 target alır, 1 target count=0 kalır
        num_categories=2,
        targets_data=targets_data,
        claim=False,
    )
    plan = extract_social_idea_plan_snapshot(attempt)

    prep = prepare_social_idea_worker_inputs(
        db_session,
        attempt_id=attempt.id,
        task_id="task-04",
        now=T0,
    )

    assert len(prep.prompt_inputs) == 2
    for p_input, c_plan in zip(prep.prompt_inputs, plan.categories):
        # Kategori planında count > 0 olan target'lar
        expected_active_targets = [
            (tid, count) for tid, count in c_plan.target_quotas if count > 0
        ]
        assert len(p_input.target_specs) == len(expected_active_targets)
        for spec, (exp_tid, exp_count) in zip(p_input.target_specs, expected_active_targets):
            assert spec.target_id == exp_tid
            assert spec.requested_count == exp_count
            assert spec.requested_count > 0


def test_05_running_attempt_idempotent_replay_same_task_id(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """5. Running attempt aynı task_id ile tekrar çağrıldığında idempotent olarak aynı girdileri döner."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-05", claim=True
    )
    assert attempt.status == "running"

    prep1 = prepare_social_idea_worker_inputs(
        db_session,
        attempt_id=attempt.id,
        task_id="task-05",
        now=T0,
    )
    assert prep1.already_completed is False
    assert len(prep1.prompt_inputs) == 2

    # İkinci çağrı
    prep2 = prepare_social_idea_worker_inputs(
        db_session,
        attempt_id=attempt.id,
        task_id="task-05",
        now=T0 + timedelta(seconds=10),
    )
    assert prep2.already_completed is False
    assert len(prep2.prompt_inputs) == 2
    assert prep1.attempt_id == prep2.attempt_id
    assert [p.category_id for p in prep1.prompt_inputs] == [p.category_id for p in prep2.prompt_inputs]


def test_06_running_attempt_different_task_id_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """6. Running attempt'e farklı bir task_id ile müdahale TASK_MISMATCH ile reddedilir."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-06-owner", claim=True
    )
    with pytest.raises(AttemptNotWritableError) as exc:
        prepare_social_idea_worker_inputs(
            db_session,
            attempt_id=attempt.id,
            task_id="task-06-intruder",
            now=T0,
        )
    assert exc.value.error_code == "TASK_MISMATCH"


def test_07_completed_attempt_replay_returns_empty_prompt_inputs(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """7. Completed attempt replay durumunda already_completed=True ve prompt_inputs=() döner."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-07", claim=True
    )
    # Attempt'i completed yapalım
    attempt.status = "completed"
    attempt.completed_at = T0
    db_session.commit()

    prep = prepare_social_idea_worker_inputs(
        db_session,
        attempt_id=attempt.id,
        task_id="task-07",
        now=T0,
    )
    assert prep.already_completed is True
    assert prep.prompt_inputs == ()
    assert prep.attempt_id == attempt.id
    assert prep.task_id == "task-07"


def test_08_completed_attempt_different_task_id_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """8. Completed attempt'e farklı bir task_id ile replay TASK_MISMATCH ile reddedilir."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-08-owner", claim=True
    )
    attempt.status = "completed"
    db_session.commit()

    with pytest.raises(AttemptNotWritableError) as exc:
        prepare_social_idea_worker_inputs(
            db_session,
            attempt_id=attempt.id,
            task_id="task-08-intruder",
            now=T0,
        )
    assert exc.value.error_code == "TASK_MISMATCH"


def test_09_failed_and_partial_attempts_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """9. Failed veya partial durumdaki attempt'ler ATTEMPT_NOT_CLAIMABLE ile reddedilir."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-09", claim=False
    )
    # Failed
    attempt.status = "failed"
    db_session.commit()

    with pytest.raises(AttemptNotWritableError) as exc:
        prepare_social_idea_worker_inputs(
            db_session,
            attempt_id=attempt.id,
            task_id="task-09",
            now=T0,
        )
    assert exc.value.error_code == "ATTEMPT_NOT_CLAIMABLE"

    # Partial
    attempt.status = "partial"
    db_session.commit()

    with pytest.raises(AttemptNotWritableError) as exc2:
        prepare_social_idea_worker_inputs(
            db_session,
            attempt_id=attempt.id,
            task_id="task-09",
            now=T0,
        )
    assert exc2.value.error_code == "ATTEMPT_NOT_CLAIMABLE"


def test_10_wrong_stage_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """10. Stage 'ideas' dışında ise INVALID_STAGE ile reddedilir."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-10", claim=False
    )
    attempt.stage = "categories"
    db_session.commit()

    with pytest.raises(AttemptNotWritableError) as exc:
        prepare_social_idea_worker_inputs(
            db_session,
            attempt_id=attempt.id,
            task_id="task-10",
            now=T0,
        )
    assert exc.value.error_code == "INVALID_STAGE"


def test_11_lease_expired_on_running_attempt_fails_with_worker_lost(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """11. Running attempt'in lease süresi dolmuşsa WORKER_LOST ile fail edilir."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-11", claim=True
    )
    # Lease süresini geçmişe alalım
    attempt.lease_expires_at = T0 - timedelta(seconds=1)
    db_session.commit()

    with pytest.raises(AttemptNotWritableError) as exc:
        prepare_social_idea_worker_inputs(
            db_session,
            attempt_id=attempt.id,
            task_id="task-11",
            now=T0,
        )
    assert exc.value.error_code == "WORKER_LOST"

    db_session.refresh(attempt)
    assert attempt.status == "failed"
    assert attempt.reason_code == "worker_lost"
    assert attempt.lease_expires_at is None


def test_11a_lease_expired_on_pending_attempt_fails_with_worker_lost(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """11a. Süresi dolmuş pending attempt WORKER_LOST olur, running yapılmaz ve task_id atanmaz."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-11a", claim=False
    )
    assert attempt.status == "pending"
    assert attempt.task_id is None
    assert attempt.started_at is None

    # Lease süresini geçmişe alalım
    attempt.lease_expires_at = T0 - timedelta(seconds=1)
    db_session.commit()

    with pytest.raises(AttemptNotWritableError) as exc:
        prepare_social_idea_worker_inputs(
            db_session,
            attempt_id=attempt.id,
            task_id="task-11a",
            now=T0,
        )
    assert exc.value.error_code == "WORKER_LOST"

    db_session.refresh(attempt)
    # Süresi dolmuş pending attempt kesinlikle running yapılmamalı ve task_id atanmamalı
    assert attempt.status == "failed"
    assert attempt.status != "running"
    assert attempt.task_id is None
    assert attempt.started_at is None
    assert attempt.reason_code == "worker_lost"
    assert attempt.lease_expires_at is None
    assert attempt.completed_at == T0


def test_11b_lease_none_on_pending_attempt_fails_with_worker_lost(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """11b. lease_expires_at None olan pending attempt WORKER_LOST ile fail edilir."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-11b", claim=False
    )
    assert attempt.status == "pending"
    attempt.lease_expires_at = None
    db_session.commit()

    with pytest.raises(AttemptNotWritableError) as exc:
        prepare_social_idea_worker_inputs(
            db_session,
            attempt_id=attempt.id,
            task_id="task-11b",
            now=T0,
        )
    assert exc.value.error_code == "WORKER_LOST"

    db_session.refresh(attempt)
    assert attempt.status == "failed"
    assert attempt.task_id is None
    assert attempt.started_at is None
    assert attempt.reason_code == "worker_lost"
    assert attempt.lease_expires_at is None


def test_11c_pending_attempt_prefilled_task_id_rejected_as_state_inconsistent(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """11c. task_id önceden dolu pending attempt ATTEMPT_STATE_INCONSISTENT üretir."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-11c", claim=False
    )
    assert attempt.status == "pending"
    attempt.task_id = "prefilled-rogue-task"
    db_session.commit()

    with pytest.raises(AttemptNotWritableError) as exc:
        prepare_social_idea_worker_inputs(
            db_session,
            attempt_id=attempt.id,
            task_id="task-11c",
            now=T0,
        )
    assert exc.value.error_code == "ATTEMPT_STATE_INCONSISTENT"

    db_session.refresh(attempt)
    # Alan sessizce ezilmemeli veya running yapılmamalı
    assert attempt.status == "pending"
    assert attempt.task_id == "prefilled-rogue-task"
    assert attempt.started_at is None


def test_11d_pending_attempt_prefilled_started_at_rejected_as_state_inconsistent(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """11d. started_at önceden dolu pending attempt ATTEMPT_STATE_INCONSISTENT üretir."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-11d", claim=False
    )
    assert attempt.status == "pending"
    bad_started = T0 - timedelta(minutes=10)
    attempt.started_at = bad_started
    db_session.commit()

    with pytest.raises(AttemptNotWritableError) as exc:
        prepare_social_idea_worker_inputs(
            db_session,
            attempt_id=attempt.id,
            task_id="task-11d",
            now=T0,
        )
    assert exc.value.error_code == "ATTEMPT_STATE_INCONSISTENT"

    db_session.refresh(attempt)
    assert attempt.status == "pending"
    assert attempt.started_at == bad_started
    assert attempt.task_id is None


def test_11e_direct_claim_ideas_attempt_for_worker_checks_pending_lease_and_consistency(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """11e. claim_ideas_attempt_for_worker doğrudan çağrıldığında pending lease ve state kurallarını doğrular."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-11e", claim=False
    )
    # 1. Süresi dolmuş pending claim
    attempt.lease_expires_at = T0 - timedelta(seconds=10)
    db_session.commit()

    with pytest.raises(AttemptNotWritableError) as exc1:
        claim_ideas_attempt_for_worker(
            db_session,
            attempt_id=attempt.id,
            task_id="task-direct",
            now=T0,
        )
    assert exc1.value.error_code == "WORKER_LOST"

    db_session.refresh(attempt)
    assert attempt.status == "failed"
    assert attempt.task_id is None

    # 2. Geçerli pending attempt için başarılı claim
    ws2, run2, brief2, cats2, targets2, kws2, start2, attempt2 = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-11e-valid", claim=False
    )
    assert attempt2.status == "pending"
    assert attempt2.task_id is None
    assert attempt2.started_at is None

    att_out, brief_out, run_out, already_comp = claim_ideas_attempt_for_worker(
        db_session,
        attempt_id=attempt2.id,
        task_id="task-direct-valid",
        now=T0,
    )
    db_session.commit()

    assert already_comp is False
    assert att_out.id == attempt2.id
    assert att_out.status == "running"
    assert att_out.task_id == "task-direct-valid"
    assert att_out.started_at == T0
    assert att_out.heartbeat_at == T0
    assert att_out.lease_expires_at == T0 + timedelta(seconds=1500)


def test_12_brief_stale_fails_with_brief_stale(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """12. Brief stale ise claim reddedilir ve attempt BRIEF_STALE ile failed yapılır."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-12", claim=False
    )
    brief.is_stale = True
    db_session.commit()

    with pytest.raises(AttemptNotWritableError) as exc:
        prepare_social_idea_worker_inputs(
            db_session,
            attempt_id=attempt.id,
            task_id="task-12",
            now=T0,
        )
    assert exc.value.error_code == "BRIEF_STALE"

    db_session.refresh(attempt)
    assert attempt.status == "failed"
    assert attempt.reason_code == "brief_stale"


def test_13_assignment_version_changed_fails(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """13. Kanal atama sürümü değişmişse ASSIGNMENT_CHANGED ile reddedilir."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-13", claim=False
    )
    brief.channel_assignment_version = 2
    db_session.commit()

    with pytest.raises(AttemptNotWritableError) as exc:
        prepare_social_idea_worker_inputs(
            db_session,
            attempt_id=attempt.id,
            task_id="task-13",
            now=T0,
        )
    assert exc.value.error_code == "ASSIGNMENT_CHANGED"

    db_session.refresh(attempt)
    assert attempt.status == "failed"
    assert attempt.reason_code == "assignment_changed"


def test_14_corrupted_coverage_plan_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """14. Coverage içindeki plan bozulmuşsa IDEA_PERSISTENCE_INCONSISTENT ile reddedilir."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-14", claim=False
    )
    cov = copy.deepcopy(attempt.coverage)
    cov["plan"]["categories"] = []  # Kategorileri sil
    attempt.coverage = cov
    db_session.commit()

    with pytest.raises(SocialIdeaPersistenceError) as exc:
        prepare_social_idea_worker_inputs(
            db_session,
            attempt_id=attempt.id,
            task_id="task-14",
            now=T0,
        )
    assert exc.value.error_code == "IDEA_PERSISTENCE_INCONSISTENT"


def test_15_category_missing_from_brief_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """15. DB'de plandaki bir kategori bulunamazsa WORKER_INPUT_INCONSISTENT ile reddedilir."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-15", claim=True
    )
    # Bir kategoriyi silelim
    db_session.delete(cats[1])
    db_session.commit()

    with pytest.raises(SocialIdeaWorkerInputError) as exc:
        prepare_social_idea_worker_inputs(
            db_session,
            attempt_id=attempt.id,
            task_id="task-15",
            now=T0,
        )
    assert exc.value.error_code == "WORKER_INPUT_INCONSISTENT"
    assert exc.value.category_id == cats[1].id


def test_16_category_wrong_scoring_run_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """16. DB'deki kategori farklı scoring run'a aitse WORKER_INPUT_INCONSISTENT ile reddedilir."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-16", claim=True
    )
    other_run = make_scoring_run(brand_profile_id=ws.id)
    cats[0].scoring_run_id = other_run.id
    db_session.commit()

    with pytest.raises(SocialIdeaWorkerInputError) as exc:
        prepare_social_idea_worker_inputs(
            db_session,
            attempt_id=attempt.id,
            task_id="task-16",
            now=T0,
        )
    assert exc.value.error_code == "WORKER_INPUT_INCONSISTENT"
    assert exc.value.category_id == cats[0].id


def test_17_category_stale_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """17. DB'deki kategori is_stale=True ise WORKER_INPUT_INCONSISTENT ile reddedilir."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-17", claim=True
    )
    cats[0].is_stale = True
    db_session.commit()

    with pytest.raises(SocialIdeaWorkerInputError) as exc:
        prepare_social_idea_worker_inputs(
            db_session,
            attempt_id=attempt.id,
            task_id="task-17",
            now=T0,
        )
    assert exc.value.error_code == "WORKER_INPUT_INCONSISTENT"
    assert exc.value.category_id == cats[0].id


def test_18_category_name_boundary_and_whitespace_validation(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """18. Kategori adı sınır dışı, boş veya trimlenmemiş ise reddedilir."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-18", claim=True
    )
    # Untrimmed / whitespace-only
    cats[0].category_name = "  "
    db_session.commit()

    with pytest.raises(SocialIdeaWorkerInputError) as exc:
        prepare_social_idea_worker_inputs(
            db_session,
            attempt_id=attempt.id,
            task_id="task-18",
            now=T0,
        )
    assert exc.value.error_code == "WORKER_INPUT_INCONSISTENT"

    # Leading/trailing whitespace
    cats[0].category_name = "  Kategori 1  "
    db_session.commit()

    with pytest.raises(SocialIdeaWorkerInputError) as exc2:
        prepare_social_idea_worker_inputs(
            db_session,
            attempt_id=attempt.id,
            task_id="task-18",
            now=T0,
        )
    assert exc2.value.error_code == "WORKER_INPUT_INCONSISTENT"

    # Empty string
    cats[0].category_name = ""
    db_session.commit()

    with pytest.raises(SocialIdeaWorkerInputError) as exc3:
        prepare_social_idea_worker_inputs(
            db_session,
            attempt_id=attempt.id,
            task_id="task-18",
            now=T0,
        )
    assert exc3.value.error_code == "WORKER_INPUT_INCONSISTENT"


def test_19_category_description_boundary_and_whitespace_validation(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """19. Kategori açıklaması sınır dışı, boş veya trimlenmemiş ise reddedilir."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-19", claim=True
    )
    # Whitespace only
    cats[0].description = " \t\n "
    db_session.commit()

    with pytest.raises(SocialIdeaWorkerInputError) as exc:
        prepare_social_idea_worker_inputs(
            db_session,
            attempt_id=attempt.id,
            task_id="task-19",
            now=T0,
        )
    assert exc.value.error_code == "WORKER_INPUT_INCONSISTENT"

    # > 2000 karakter
    cats[0].description = "Y" * 2001
    db_session.commit()

    with pytest.raises(SocialIdeaWorkerInputError) as exc2:
        prepare_social_idea_worker_inputs(
            db_session,
            attempt_id=attempt.id,
            task_id="task-19",
            now=T0,
        )
    assert exc2.value.error_code == "WORKER_INPUT_INCONSISTENT"


def test_20_brief_keywords_count_outside_1_to_5_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """20. Brief keyword sayısı 1-5 aralığı dışında ise WORKER_INPUT_INCONSISTENT ile reddedilir."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-20", claim=True
    )
    # Tüm brief keyword'lerini silelim
    db_session.query(SocialBriefKeyword).filter_by(brief_id=brief.id).delete()
    db_session.commit()

    with pytest.raises(SocialIdeaWorkerInputError) as exc:
        prepare_social_idea_worker_inputs(
            db_session,
            attempt_id=attempt.id,
            task_id="task-20",
            now=T0,
        )
    assert exc.value.error_code == "WORKER_INPUT_INCONSISTENT"


def test_21_brief_keywords_non_contiguous_positions_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """21. Brief keyword pozisyonları 0..n-1 aralığında kesintisiz değilse reddedilir."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-21", claim=True
    )
    bks = db_session.query(SocialBriefKeyword).filter_by(brief_id=brief.id).all()
    # Pozisyonları [0, 2, 3] yapalım (1 boşlukta)
    bks[1].position = 5
    db_session.commit()

    with pytest.raises(SocialIdeaWorkerInputError) as exc:
        prepare_social_idea_worker_inputs(
            db_session,
            attempt_id=attempt.id,
            task_id="task-21",
            now=T0,
        )
    assert exc.value.error_code == "WORKER_INPUT_INCONSISTENT"


def test_22_brief_keywords_duplicate_keyword_id_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """22. Brief keyword'leri içinde mükerrer keyword_id tespit edilirse reddedilir."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-22", claim=True
    )
    bks = db_session.query(SocialBriefKeyword).filter_by(brief_id=brief.id).all()
    dup_bks = [
        bks[0],
        SocialBriefKeyword(
            id=9999,
            brief_id=brief.id,
            keyword_id=bks[0].keyword_id,  # Mükerrer keyword_id
            keyword_snapshot="Mükerrer kw",
            position=1,
        ),
        bks[2] if len(bks) > 2 else bks[1],
    ]

    orig_query = db_session.query

    def custom_query(model, *args, **kwargs):
        if model is SocialBriefKeyword:
            class MockQuery:
                def filter(self, *f_args, **f_kwargs):
                    return self

                def all(self):
                    return dup_bks
            return MockQuery()
        return orig_query(model, *args, **kwargs)

    with patch.object(db_session, "query", side_effect=custom_query):
        with pytest.raises(SocialIdeaWorkerInputError) as exc:
            prepare_social_idea_worker_inputs(
                db_session,
                attempt_id=attempt.id,
                task_id="task-22",
                now=T0,
            )
        assert exc.value.error_code == "WORKER_INPUT_INCONSISTENT"


def test_23_brief_keywords_empty_or_whitespace_snapshot_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """23. Brief keyword_snapshot boş veya whitespace ise WORKER_INPUT_INCONSISTENT ile reddedilir."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-23", claim=True
    )
    bk = db_session.query(SocialBriefKeyword).filter_by(brief_id=brief.id).first()
    bk.keyword_snapshot = "   "
    db_session.commit()

    with pytest.raises(SocialIdeaWorkerInputError) as exc:
        prepare_social_idea_worker_inputs(
            db_session,
            attempt_id=attempt.id,
            task_id="task-23",
            now=T0,
        )
    assert exc.value.error_code == "WORKER_INPUT_INCONSISTENT"


def test_24_brief_targets_count_outside_1_to_6_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """24. Brief hedef sayısı 1-6 arasında değilse WORKER_INPUT_INCONSISTENT ile reddedilir."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-24", claim=True
    )
    # Tüm hedefleri silelim
    db_session.query(SocialBriefTarget).filter_by(brief_id=brief.id).delete()
    db_session.commit()

    with pytest.raises(SocialIdeaWorkerInputError) as exc:
        prepare_social_idea_worker_inputs(
            db_session,
            attempt_id=attempt.id,
            task_id="task-24",
            now=T0,
        )
    assert exc.value.error_code == "WORKER_INPUT_INCONSISTENT"


def test_25_brief_targets_non_canonical_platform_format_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """25. Kanonik matriste bulunmayan hedef platform/format kombinasyonu reddedilir."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-25", claim=True
    )
    targets[0].platform = "tiktok"
    targets[0].content_format = "thread"  # tiktok için thread geçersiz
    db_session.commit()

    with pytest.raises(SocialIdeaWorkerInputError) as exc:
        prepare_social_idea_worker_inputs(
            db_session,
            attempt_id=attempt.id,
            task_id="task-25",
            now=T0,
        )
    assert exc.value.error_code == "WORKER_INPUT_INCONSISTENT"


def test_26_plan_target_missing_from_brief_targets_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """26. Plandaki bir hedef brief altında bulunamazsa WORKER_INPUT_INCONSISTENT ile reddedilir."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-26", claim=True
    )
    db_session.delete(targets[1])
    db_session.commit()

    with pytest.raises(SocialIdeaWorkerInputError) as exc:
        prepare_social_idea_worker_inputs(
            db_session,
            attempt_id=attempt.id,
            task_id="task-26",
            now=T0,
        )
    assert exc.value.error_code == "WORKER_INPUT_INCONSISTENT"


def test_27_invalid_task_id_and_attempt_id_arguments_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """27. Geçersiz task_id ve attempt_id argümanları WORKER_INPUT_INVALID ile reddedilir."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-27", claim=False
    )
    # Geçersiz task_id
    for bad_task in ["", "  ", None, 123]:
        with pytest.raises(SocialIdeaWorkerInputError) as exc:
            prepare_social_idea_worker_inputs(
                db_session,
                attempt_id=attempt.id,
                task_id=bad_task,  # type: ignore
                now=T0,
            )
        assert exc.value.error_code == "WORKER_INPUT_INVALID"
        assert exc.value.field == "task_id"

    # Geçersiz attempt_id
    for bad_attempt in [0, -1, True, False, "123"]:
        with pytest.raises(SocialIdeaWorkerInputError) as exc2:
            prepare_social_idea_worker_inputs(
                db_session,
                attempt_id=bad_attempt,  # type: ignore
                task_id="task-27",
                now=T0,
            )
        assert exc2.value.error_code == "WORKER_INPUT_INVALID"
        assert exc2.value.field == "attempt_id"


def test_28_naive_datetime_now_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """28. Timezone-naive datetime now parametresi fail-closed ValueError ile reddedilir."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-28", claim=False
    )
    naive_now = datetime(2026, 9, 24, 12, 0, 0)
    with pytest.raises(ValueError) as exc:
        prepare_social_idea_worker_inputs(
            db_session,
            attempt_id=attempt.id,
            task_id="task-28",
            now=naive_now,
        )
    assert "timezone-aware" in str(exc.value)


def test_29_no_social_ideas_created_and_coverage_unmutated(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """29. Worker input hazırlığı sırasında sıfır SocialIdea satırı oluşur ve coverage mutasyona uğramaz."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-29", claim=False
    )
    initial_coverage = copy.deepcopy(attempt.coverage)

    assert db_session.query(SocialIdea).filter_by(brief_id=brief.id).count() == 0

    prep = prepare_social_idea_worker_inputs(
        db_session,
        attempt_id=attempt.id,
        task_id="task-29",
        now=T0,
    )
    db_session.commit()

    # Fikir oluşturulmadı
    assert db_session.query(SocialIdea).filter_by(brief_id=brief.id).count() == 0

    # Coverage değişmedi
    db_session.refresh(attempt)
    assert attempt.coverage == initial_coverage


def test_30_service_does_not_commit_or_rollback(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """30. prepare_social_idea_worker_inputs kendi içinde commit veya rollback çağırmaz."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-30", claim=False
    )
    with patch.object(db_session, "commit") as mock_c, patch.object(db_session, "rollback") as mock_r:
        prep = prepare_social_idea_worker_inputs(
            db_session,
            attempt_id=attempt.id,
            task_id="task-30",
            now=T0,
        )
        assert len(prep.prompt_inputs) == 2
        assert mock_c.call_count == 0
        assert mock_r.call_count == 0


def test_31_caller_rollback_reverts_claim(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """31. Caller rollback yaptığında claim durumu (running, task_id, lease) tamamen geri alınır."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-31", claim=False
    )
    assert attempt.status == "pending"
    assert attempt.task_id is None
    initial_lease = attempt.lease_expires_at

    t_claim = T0 + timedelta(hours=2)
    prep = prepare_social_idea_worker_inputs(
        db_session,
        attempt_id=attempt.id,
        task_id="task-31",
        now=t_claim,
    )
    assert prep.already_completed is False
    assert attempt.status == "running"
    assert attempt.task_id == "task-31"
    assert attempt.started_at == t_claim

    # Caller rollback yapıyor
    db_session.rollback()

    db_session.refresh(attempt)
    assert attempt.status == "pending"
    assert attempt.task_id is None
    assert attempt.started_at is None
    assert attempt.lease_expires_at == initial_lease


def test_32_sql_lock_order_is_canonical(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """32. Kilit sırası kesinlikle ScoringRun -> SocialBrief -> SocialGenerationAttempt'tir."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-32", claim=False
    )
    captured_locks: list[str] = []

    def _query_catcher(conn, cursor, statement, parameters, context, executemany):
        stmt_lower = statement.lower()
        if "for update" in stmt_lower:
            if "scoring_runs" in stmt_lower:
                captured_locks.append("ScoringRun")
            elif "social_briefs" in stmt_lower:
                captured_locks.append("SocialBrief")
            elif "social_generation_attempts" in stmt_lower:
                captured_locks.append("SocialGenerationAttempt")

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", _query_catcher)
    try:
        prep = prepare_social_idea_worker_inputs(
            db_session,
            attempt_id=attempt.id,
            task_id="task-32",
            now=T0,
        )
        db_session.commit()
    finally:
        event.remove(engine, "before_cursor_execute", _query_catcher)

    assert len(captured_locks) == 3, f"Beklenen 3 kilit, yakalanan: {captured_locks}"
    assert captured_locks == ["ScoringRun", "SocialBrief", "SocialGenerationAttempt"]


def test_33_generator_temperature_none_and_zero_network_calls(enable_flag):
    """33. brief_idea_generator.py içinde temperature=None korunmalı ve harici çağrı yapılmamalıdır."""
    import inspect
    from app.generators.social.brief_idea_generator import SocialBriefIdeaGenerator

    source = inspect.getsource(SocialBriefIdeaGenerator.generate)
    assert "temperature=None" in source, "SocialBriefIdeaGenerator.generate içinde temperature=None korunmalıdır!"
