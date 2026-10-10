# -*- coding: utf-8 -*-
"""Integration tests for ideas_retry Attempt Claim, Failure Finalization, and Guards (F1-F.7.3).

Bu test dosyası gerçek PostgreSQL test veritabanı üzerinde:
1-20: claim_ideas_retry_attempt_for_worker yaşam döngüsü, kilit ve hata kurallarını,
21-34: finalize_ideas_retry_attempt_failure kurallarını,
35-38: Ortak heartbeat_attempt ve lock_attempt_for_write yardımcılarının ideas_retry uyumunu,
39-41: Mevcut ideas davranışı ve çapraz aşama (cross-stage) izolasyon regresyonunu
doğrular.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

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
    ALLOWED_IDEAS_FAILURE_REASONS,
    PENDING_ALLOWED_FAILURE_REASONS,
    AttemptNotClaimableError,
    AttemptNotFoundError,
    AttemptNotWritableError,
    BriefNotFoundError,
    claim_ideas_attempt_for_worker,
    claim_ideas_retry_attempt_for_worker,
    finalize_ideas_attempt_failure,
    finalize_ideas_retry_attempt_failure,
    heartbeat_attempt,
    lock_attempt_for_write,
)

T0 = datetime(2026, 9, 25, 12, 0, 0, tzinfo=timezone.utc)


def _setup_environment(
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
    workspace_name: str = "Retry Attempt Brand",
    is_stale: bool = False,
    channel_assignment_version: int = 1,
    run_channel_assignment_version: int = 1,
):
    """Test için workspace, run, brief ve target kayıtlarını hazırlar."""
    workspace = make_workspace(name=workspace_name, status="confirmed")
    run = make_scoring_run(
        brand_profile_id=workspace.id,
        status="completed",
        algorithm_version="v2",
        channel_pool_policy_version=workspace.policy_version or 1,
        relevance_anchor_version=workspace.anchor_version or 1,
        channel_assignment_version=run_channel_assignment_version,
        skip_relevance=True,
    )

    kw = make_keyword(
        text_value=f"{workspace_name} kw 1",
        brand_profile_id=workspace.id,
    )
    pool = ChannelPool(
        scoring_run_id=run.id,
        keyword_id=kw.id,
        channel="SOCIAL",
        final_rank=1,
        relevance_score=0.9,
        adjusted_score=20.0,
    )
    db_session.add(pool)

    brief = SocialBrief(
        scoring_run_id=run.id,
        brand_name_snapshot="Test Marka",
        brand_context_snapshot="Test Context",
        channel_assignment_version=channel_assignment_version,
        format_matrix_version="v1",
        is_stale=is_stale,
        locked_at=T0,
    )
    db_session.add(brief)
    db_session.flush()

    bk = SocialBriefKeyword(
        brief_id=brief.id,
        keyword_id=kw.id,
        keyword_snapshot=kw.keyword,
        position=0,
    )
    db_session.add(bk)

    targets_data = [("instagram", "post"), ("twitter", "thread")]
    targets = []
    for plat, fmt in targets_data:
        t = SocialBriefTarget(
            brief_id=brief.id,
            platform=plat,
            content_format=fmt,
        )
        db_session.add(t)
        targets.append(t)

    db_session.commit()
    db_session.refresh(workspace)
    db_session.refresh(run)
    db_session.refresh(brief)
    for t in targets:
        db_session.refresh(t)

    return workspace, run, brief, targets


_DEFAULT_LEASE = object()


def _create_attempt(
    db_session: Session,
    brief: SocialBrief,
    targets: list[SocialBriefTarget],
    stage: str = "ideas_retry",
    status: str = "pending",
    task_id: str | None = None,
    idempotency_key: str = "test-attempt-key",
    lease_expires_at: Any = _DEFAULT_LEASE,
    started_at: datetime | None = None,
    heartbeat_at: datetime | None = None,
    reason_code: str | None = None,
    error_message: str | None = None,
) -> SocialGenerationAttempt:
    """Belirtilen özelliklerde SocialGenerationAttempt kaydı oluşturur."""
    if lease_expires_at is _DEFAULT_LEASE:
        lease_expires_at = T0 + timedelta(seconds=1500) if status == "pending" else None
    attempt = SocialGenerationAttempt(
        brief_id=brief.id,
        stage=stage,
        idempotency_key=idempotency_key,
        status=status,
        task_id=task_id,
        started_at=started_at,
        heartbeat_at=heartbeat_at,
        lease_expires_at=lease_expires_at,
        reason_code=reason_code,
        error_message=error_message,
        requested_target_ids=[t.id for t in targets],
        coverage={"schema_version": "ideas_retry_plan_v1"},
        warnings=[],
        created_at=T0,
    )
    db_session.add(attempt)
    db_session.commit()
    db_session.refresh(attempt)
    return attempt


# ==============================================================================
# BÖLÜM 1: CLAIM TESTLERİ (1 - 20)
# ==============================================================================


def test_01_pending_ideas_retry_becomes_running(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """1. Pending ideas_retry başarıyla running olur."""
    ws, run, brief, targets = _setup_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    att = _create_attempt(db_session, brief, targets, status="pending")

    att_out, brief_out, run_out, already_comp = claim_ideas_retry_attempt_for_worker(
        db_session,
        attempt_id=att.id,
        task_id="task-01",
        now=T0,
    )
    assert already_comp is False
    assert att_out.id == att.id
    assert att_out.status == "running"
    assert brief_out.id == brief.id
    assert run_out.id == run.id


def test_02_claim_writes_task_id_started_at_heartbeat_at_and_lease(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """2. Claim task_id, started_at, heartbeat_at ve lease yazar."""
    ws, run, brief, targets = _setup_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    att = _create_attempt(db_session, brief, targets, status="pending")

    att_out, _, _, _ = claim_ideas_retry_attempt_for_worker(
        db_session,
        attempt_id=att.id,
        task_id="task-02",
        now=T0,
    )
    assert att_out.task_id == "task-02"
    assert att_out.started_at == T0
    assert att_out.heartbeat_at == T0
    assert att_out.lease_expires_at == T0 + timedelta(seconds=1500)


def test_03_running_same_task_can_reclaim(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """3. Running aynı task tekrar claim edebilir."""
    ws, run, brief, targets = _setup_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    att = _create_attempt(
        db_session,
        brief,
        targets,
        status="running",
        task_id="task-03",
        started_at=T0,
        heartbeat_at=T0,
        lease_expires_at=T0 + timedelta(seconds=1500),
    )

    att_out, _, _, already_comp = claim_ideas_retry_attempt_for_worker(
        db_session,
        attempt_id=att.id,
        task_id="task-03",
        now=T0 + timedelta(seconds=30),
    )
    assert already_comp is False
    assert att_out.status == "running"
    assert att_out.task_id == "task-03"


def test_04_running_different_task_raises_task_mismatch(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """4. Running başka task TASK_MISMATCH alır."""
    ws, run, brief, targets = _setup_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    att = _create_attempt(
        db_session,
        brief,
        targets,
        status="running",
        task_id="task-orig",
        started_at=T0,
        heartbeat_at=T0,
        lease_expires_at=T0 + timedelta(seconds=1500),
    )

    with pytest.raises(AttemptNotWritableError) as exc_info:
        claim_ideas_retry_attempt_for_worker(
            db_session,
            attempt_id=att.id,
            task_id="task-intruder",
            now=T0,
        )
    assert exc_info.value.error_code == "TASK_MISMATCH"


def test_05_completed_same_task_returns_already_completed_true(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """5. Completed aynı task already_completed=True döner."""
    ws, run, brief, targets = _setup_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    att = _create_attempt(
        db_session,
        brief,
        targets,
        status="completed",
        task_id="task-05",
        started_at=T0,
        heartbeat_at=T0,
        lease_expires_at=None,
    )

    att_out, _, _, already_comp = claim_ideas_retry_attempt_for_worker(
        db_session,
        attempt_id=att.id,
        task_id="task-05",
        now=T0,
    )
    assert already_comp is True
    assert att_out.status == "completed"


def test_06_completed_different_task_raises_task_mismatch(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """6. Completed farklı task TASK_MISMATCH alır."""
    ws, run, brief, targets = _setup_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    att = _create_attempt(
        db_session,
        brief,
        targets,
        status="completed",
        task_id="task-orig",
        started_at=T0,
        heartbeat_at=T0,
        lease_expires_at=None,
    )

    with pytest.raises(AttemptNotWritableError) as exc_info:
        claim_ideas_retry_attempt_for_worker(
            db_session,
            attempt_id=att.id,
            task_id="task-other",
            now=T0,
        )
    assert exc_info.value.error_code == "TASK_MISMATCH"


def test_07_failed_attempt_cannot_be_claimed(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """7. Failed claim edilemez (ATTEMPT_NOT_CLAIMABLE)."""
    ws, run, brief, targets = _setup_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    att = _create_attempt(
        db_session,
        brief,
        targets,
        status="failed",
        reason_code="worker_lost",
        error_message="timeout",
    )

    with pytest.raises(AttemptNotWritableError) as exc_info:
        claim_ideas_retry_attempt_for_worker(
            db_session,
            attempt_id=att.id,
            task_id="task-07",
            now=T0,
        )
    assert exc_info.value.error_code == "ATTEMPT_NOT_CLAIMABLE"


def test_08_partial_attempt_cannot_be_claimed(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """8. Partial claim edilemez (ATTEMPT_NOT_CLAIMABLE)."""
    ws, run, brief, targets = _setup_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    att = _create_attempt(
        db_session,
        brief,
        targets,
        status="partial",
    )

    with pytest.raises(AttemptNotWritableError) as exc_info:
        claim_ideas_retry_attempt_for_worker(
            db_session,
            attempt_id=att.id,
            task_id="task-08",
            now=T0,
        )
    assert exc_info.value.error_code == "ATTEMPT_NOT_CLAIMABLE"


def test_09_expired_pending_becomes_failed_worker_lost(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """9. Expired pending failed/worker_lost olur."""
    ws, run, brief, targets = _setup_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    att = _create_attempt(
        db_session,
        brief,
        targets,
        status="pending",
        lease_expires_at=T0 - timedelta(seconds=10),
    )

    with pytest.raises(AttemptNotWritableError) as exc_info:
        claim_ideas_retry_attempt_for_worker(
            db_session,
            attempt_id=att.id,
            task_id="task-09",
            now=T0,
        )
    assert exc_info.value.error_code == "WORKER_LOST"

    db_session.refresh(att)
    assert att.status == "failed"
    assert att.reason_code == "worker_lost"
    assert att.lease_expires_at is None


def test_10_expired_running_becomes_failed_worker_lost(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """10. Expired running failed/worker_lost olur."""
    ws, run, brief, targets = _setup_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    att = _create_attempt(
        db_session,
        brief,
        targets,
        status="running",
        task_id="task-10",
        lease_expires_at=T0 - timedelta(seconds=5),
    )

    with pytest.raises(AttemptNotWritableError) as exc_info:
        claim_ideas_retry_attempt_for_worker(
            db_session,
            attempt_id=att.id,
            task_id="task-10",
            now=T0,
        )
    assert exc_info.value.error_code == "WORKER_LOST"

    db_session.refresh(att)
    assert att.status == "failed"
    assert att.reason_code == "worker_lost"
    assert att.lease_expires_at is None


def test_11_null_lease_pending_running_fail_closed_rejected(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """11. NULL lease pending/running fail-closed reddedilir."""
    ws, run, brief, targets = _setup_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    att_pending = _create_attempt(
        db_session,
        brief,
        targets,
        status="pending",
        lease_expires_at=None,
        idempotency_key="pending-null-lease",
    )
    with pytest.raises(AttemptNotWritableError) as exc_info1:
        claim_ideas_retry_attempt_for_worker(
            db_session,
            attempt_id=att_pending.id,
            task_id="task-null-1",
            now=T0,
        )
    assert exc_info1.value.error_code == "WORKER_LOST"

    att_running = _create_attempt(
        db_session,
        brief,
        targets,
        status="running",
        task_id="task-null-2",
        lease_expires_at=None,
        idempotency_key="running-null-lease",
    )
    with pytest.raises(AttemptNotWritableError) as exc_info2:
        claim_ideas_retry_attempt_for_worker(
            db_session,
            attempt_id=att_running.id,
            task_id="task-null-2",
            now=T0,
        )
    assert exc_info2.value.error_code == "WORKER_LOST"


def test_12_stale_brief_marks_attempt_failed_brief_stale(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """12. Stale brief claim sırasında attempt’i failed/brief_stale yapar."""
    ws, run, brief, targets = _setup_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, is_stale=True
    )
    att = _create_attempt(db_session, brief, targets, status="pending")

    with pytest.raises(AttemptNotWritableError) as exc_info:
        claim_ideas_retry_attempt_for_worker(
            db_session,
            attempt_id=att.id,
            task_id="task-12",
            now=T0,
        )
    assert exc_info.value.error_code == "BRIEF_STALE"

    db_session.refresh(att)
    assert att.status == "failed"
    assert att.reason_code == "brief_stale"


def test_13_assignment_version_mismatch_marks_attempt_failed_assignment_changed(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """13. Assignment version değişikliği failed/assignment_changed yapar."""
    ws, run, brief, targets = _setup_environment(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        channel_assignment_version=1,
        run_channel_assignment_version=2,
    )
    att = _create_attempt(db_session, brief, targets, status="pending")

    with pytest.raises(AttemptNotWritableError) as exc_info:
        claim_ideas_retry_attempt_for_worker(
            db_session,
            attempt_id=att.id,
            task_id="task-13",
            now=T0,
        )
    assert exc_info.value.error_code == "ASSIGNMENT_CHANGED"

    db_session.refresh(att)
    assert att.status == "failed"
    assert att.reason_code == "assignment_changed"


def test_14_stage_ideas_raises_invalid_stage_in_retry_claim_helper(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """14. stage='ideas' retry claim helper tarafından INVALID_STAGE alır."""
    ws, run, brief, targets = _setup_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    att_ideas = _create_attempt(db_session, brief, targets, stage="ideas", status="pending")

    with pytest.raises(AttemptNotWritableError) as exc_info:
        claim_ideas_retry_attempt_for_worker(
            db_session,
            attempt_id=att_ideas.id,
            task_id="task-14",
            now=T0,
        )
    assert exc_info.value.error_code == "INVALID_STAGE"


def test_15_stage_categories_raises_invalid_stage(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """15. stage='categories' INVALID_STAGE alır."""
    ws, run, brief, targets = _setup_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    att_cat = _create_attempt(db_session, brief, targets, stage="categories", status="pending")

    with pytest.raises(AttemptNotWritableError) as exc_info:
        claim_ideas_retry_attempt_for_worker(
            db_session,
            attempt_id=att_cat.id,
            task_id="task-15",
            now=T0,
        )
    assert exc_info.value.error_code == "INVALID_STAGE"


def test_16_invalid_attempt_id_and_task_id_rejected(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """16. Geçersiz attempt_id ve task_id reddedilir."""
    # Boolean attempt_id
    with pytest.raises(ValueError):
        claim_ideas_retry_attempt_for_worker(db_session, attempt_id=True, task_id="task-ok")

    # Sıfır veya negatif attempt_id
    with pytest.raises(ValueError):
        claim_ideas_retry_attempt_for_worker(db_session, attempt_id=0, task_id="task-ok")
    with pytest.raises(ValueError):
        claim_ideas_retry_attempt_for_worker(db_session, attempt_id=-5, task_id="task-ok")

    # Boş veya whitespace task_id
    with pytest.raises(ValueError):
        claim_ideas_retry_attempt_for_worker(db_session, attempt_id=1, task_id="")
    with pytest.raises(ValueError):
        claim_ideas_retry_attempt_for_worker(db_session, attempt_id=1, task_id="   ")


def test_17_function_does_not_commit(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """17. Fonksiyon commit çağırmaz."""
    ws, run, brief, targets = _setup_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    att = _create_attempt(db_session, brief, targets, status="pending")

    commits: list[str] = []

    def before_commit(session):
        commits.append("commit")

    event.listen(db_session, "before_commit", before_commit)
    try:
        claim_ideas_retry_attempt_for_worker(
            db_session,
            attempt_id=att.id,
            task_id="task-17",
            now=T0,
        )
    finally:
        event.remove(db_session, "before_commit", before_commit)

    assert len(commits) == 0, "claim_ideas_retry_attempt_for_worker commit çağırmamalıdır!"


def test_18_caller_rollback_reverts_claim_mutation(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """18. Caller rollback claim mutasyonunu geri alır."""
    ws, run, brief, targets = _setup_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    att = _create_attempt(db_session, brief, targets, status="pending")

    claim_ideas_retry_attempt_for_worker(
        db_session,
        attempt_id=att.id,
        task_id="task-18",
        now=T0,
    )
    # Caller rollback yapar
    db_session.rollback()

    db_session.refresh(att)
    assert att.status == "pending"
    assert att.task_id is None


def test_19_caller_commit_persists_claim_mutation(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """19. Caller commit claim mutasyonunu kalıcılaştırır."""
    ws, run, brief, targets = _setup_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    att = _create_attempt(db_session, brief, targets, status="pending")

    claim_ideas_retry_attempt_for_worker(
        db_session,
        attempt_id=att.id,
        task_id="task-19",
        now=T0,
    )
    db_session.commit()

    db_session.refresh(att)
    assert att.status == "running"
    assert att.task_id == "task-19"


def test_20_canonical_lock_order_verified_via_sql_listener(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """20. Canonical lock sırası (ScoringRun -> SocialBrief -> SocialGenerationAttempt) doğrulanır."""
    ws, run, brief, targets = _setup_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    att = _create_attempt(db_session, brief, targets, status="pending")

    locked_tables: list[str] = []

    def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        if "FOR UPDATE" in statement:
            if "scoring_runs" in statement:
                locked_tables.append("ScoringRun")
            elif "social_briefs" in statement:
                locked_tables.append("SocialBrief")
            elif "social_generation_attempts" in statement:
                locked_tables.append("SocialGenerationAttempt")

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", before_cursor_execute)
    try:
        claim_ideas_retry_attempt_for_worker(
            db_session,
            attempt_id=att.id,
            task_id="task-lock-order",
            now=T0,
        )
    finally:
        event.remove(engine, "before_cursor_execute", before_cursor_execute)

    assert "ScoringRun" in locked_tables
    assert "SocialBrief" in locked_tables
    assert "SocialGenerationAttempt" in locked_tables

    sr_idx = locked_tables.index("ScoringRun")
    sb_idx = locked_tables.index("SocialBrief")
    att_idx = locked_tables.index("SocialGenerationAttempt")
    assert sr_idx < sb_idx < att_idx, "Global canonical lock sırası ScoringRun -> SocialBrief -> Attempt olmalıdır!"


# ==============================================================================
# BÖLÜM 2: FAILURE FINALIZATION TESTLERİ (21 - 34)
# ==============================================================================


def test_21_running_same_task_finalizes_failure(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """21. Running ve aynı task güvenli failed olur."""
    ws, run, brief, targets = _setup_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    att = _create_attempt(
        db_session,
        brief,
        targets,
        status="running",
        task_id="task-21",
        started_at=T0,
        heartbeat_at=T0,
        lease_expires_at=T0 + timedelta(seconds=1500),
    )

    att_out, modified = finalize_ideas_retry_attempt_failure(
        db_session,
        attempt_id=att.id,
        task_id="task-21",
        reason_code="idea_provider_error",
        error_message="Gemini quota exceeded",
        now=T0 + timedelta(seconds=10),
    )
    assert modified is True
    assert att_out.status == "failed"
    assert att_out.reason_code == "idea_provider_error"
    assert att_out.error_message == "Gemini quota exceeded"
    assert att_out.completed_at == T0 + timedelta(seconds=10)
    assert att_out.lease_expires_at is None


def test_22_running_different_task_is_not_modified(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """22. Running ve farklı task değişmez."""
    ws, run, brief, targets = _setup_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    att = _create_attempt(
        db_session,
        brief,
        targets,
        status="running",
        task_id="task-owner",
        started_at=T0,
        heartbeat_at=T0,
        lease_expires_at=T0 + timedelta(seconds=1500),
    )

    att_out, modified = finalize_ideas_retry_attempt_failure(
        db_session,
        attempt_id=att.id,
        task_id="task-intruder",
        reason_code="idea_provider_error",
        error_message="fail attempt",
        now=T0,
    )
    assert modified is False
    db_session.refresh(att)
    assert att.status == "running"
    assert att.reason_code is None


def test_23_completed_attempt_is_not_modified(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """23. Completed değişmez."""
    ws, run, brief, targets = _setup_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    att = _create_attempt(
        db_session,
        brief,
        targets,
        status="completed",
        task_id="task-23",
        started_at=T0,
        heartbeat_at=T0,
    )

    att_out, modified = finalize_ideas_retry_attempt_failure(
        db_session,
        attempt_id=att.id,
        task_id="task-23",
        reason_code="idea_provider_error",
        error_message="late fail",
        now=T0,
    )
    assert modified is False
    db_session.refresh(att)
    assert att.status == "completed"


def test_24_failed_attempt_preserves_existing_reason_and_error(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """24. Failed mevcut reason/error değerini korur."""
    ws, run, brief, targets = _setup_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    att = _create_attempt(
        db_session,
        brief,
        targets,
        status="failed",
        task_id="task-24",
        reason_code="worker_lost",
        error_message="original failure",
    )

    att_out, modified = finalize_ideas_retry_attempt_failure(
        db_session,
        attempt_id=att.id,
        task_id="task-24",
        reason_code="idea_provider_error",
        error_message="new failure",
        now=T0,
    )
    assert modified is False
    db_session.refresh(att)
    assert att.status == "failed"
    assert att.reason_code == "worker_lost"
    assert att.error_message == "original failure"


def test_25_partial_attempt_is_not_modified(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """25. Partial değişmez."""
    ws, run, brief, targets = _setup_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    att = _create_attempt(
        db_session,
        brief,
        targets,
        status="partial",
        task_id="task-25",
    )

    att_out, modified = finalize_ideas_retry_attempt_failure(
        db_session,
        attempt_id=att.id,
        task_id="task-25",
        reason_code="idea_provider_error",
        error_message="fail partial",
        now=T0,
    )
    assert modified is False
    db_session.refresh(att)
    assert att.status == "partial"


def test_26_pending_can_be_finalized_with_dispatch_failed(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """26. Pending dispatch_failed ile failed yapılabilir."""
    ws, run, brief, targets = _setup_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    att = _create_attempt(db_session, brief, targets, status="pending")

    att_out, modified = finalize_ideas_retry_attempt_failure(
        db_session,
        attempt_id=att.id,
        task_id="task-26",
        reason_code="dispatch_failed",
        error_message="Celery broker connection failed",
        now=T0,
    )
    assert modified is True
    assert att_out.status == "failed"
    assert att_out.reason_code == "dispatch_failed"


def test_27_pending_can_be_finalized_with_worker_bootstrap_failed(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """27. Pending worker_bootstrap_failed ile failed yapılabilir."""
    ws, run, brief, targets = _setup_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    att = _create_attempt(db_session, brief, targets, status="pending")

    att_out, modified = finalize_ideas_retry_attempt_failure(
        db_session,
        attempt_id=att.id,
        task_id="task-27",
        reason_code="worker_bootstrap_failed",
        error_message="Process crash before claim",
        now=T0,
    )
    assert modified is True
    assert att_out.status == "failed"
    assert att_out.reason_code == "worker_bootstrap_failed"


def test_28_pending_cannot_be_finalized_with_idea_provider_error(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """28. Pending idea_provider_error ile sahiplenmeden failed yapılamaz."""
    ws, run, brief, targets = _setup_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    att = _create_attempt(db_session, brief, targets, status="pending")

    att_out, modified = finalize_ideas_retry_attempt_failure(
        db_session,
        attempt_id=att.id,
        task_id="task-28",
        reason_code="idea_provider_error",
        error_message="AI call error",
        now=T0,
    )
    assert modified is False
    db_session.refresh(att)
    assert att.status == "pending"


def test_29_disallowed_reason_code_rejected(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """29. Allowlist dışı reason reddedilir."""
    ws, run, brief, targets = _setup_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    att = _create_attempt(db_session, brief, targets, status="running", task_id="task-29")

    with pytest.raises(ValueError) as exc_info:
        finalize_ideas_retry_attempt_failure(
            db_session,
            attempt_id=att.id,
            task_id="task-29",
            reason_code="unauthorized_custom_reason",
            error_message="some error",
            now=T0,
        )
    # Ortak finalize çekirdeği gerçek aşama adını yazar (ideas / ideas_retry / contents).
    assert "Geçersiz ideas_retry failure reason_code" in str(exc_info.value)


def test_30_empty_error_message_rejected(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """30. Boş error_message reddedilir."""
    ws, run, brief, targets = _setup_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    att = _create_attempt(db_session, brief, targets, status="running", task_id="task-30")

    with pytest.raises(ValueError):
        finalize_ideas_retry_attempt_failure(
            db_session,
            attempt_id=att.id,
            task_id="task-30",
            reason_code="idea_provider_error",
            error_message="",
            now=T0,
        )
    with pytest.raises(ValueError):
        finalize_ideas_retry_attempt_failure(
            db_session,
            attempt_id=att.id,
            task_id="task-30",
            reason_code="idea_provider_error",
            error_message="   ",
            now=T0,
        )


def test_31_stage_ideas_raises_invalid_stage_in_retry_finalizer(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """31. stage='ideas' retry finalizer tarafından INVALID_STAGE alır."""
    ws, run, brief, targets = _setup_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    att_ideas = _create_attempt(
        db_session, brief, targets, stage="ideas", status="running", task_id="task-31"
    )

    with pytest.raises(AttemptNotWritableError) as exc_info:
        finalize_ideas_retry_attempt_failure(
            db_session,
            attempt_id=att_ideas.id,
            task_id="task-31",
            reason_code="idea_provider_error",
            error_message="failed",
            now=T0,
        )
    assert exc_info.value.error_code == "INVALID_STAGE"


def test_32_finalizer_does_not_commit(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """32. Finalizer commit/rollback çağırmaz."""
    ws, run, brief, targets = _setup_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    att = _create_attempt(
        db_session, brief, targets, status="running", task_id="task-32"
    )

    commits: list[str] = []

    def before_commit(session):
        commits.append("commit")

    event.listen(db_session, "before_commit", before_commit)
    try:
        finalize_ideas_retry_attempt_failure(
            db_session,
            attempt_id=att.id,
            task_id="task-32",
            reason_code="idea_provider_error",
            error_message="failed without commit",
            now=T0,
        )
    finally:
        event.remove(db_session, "before_commit", before_commit)

    assert len(commits) == 0, "finalize_ideas_retry_attempt_failure commit çağırmamalıdır!"


def test_33_caller_rollback_reverts_finalizer_mutation(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """33. Caller rollback mutasyonu geri alır."""
    ws, run, brief, targets = _setup_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    att = _create_attempt(
        db_session, brief, targets, status="running", task_id="task-33"
    )

    finalize_ideas_retry_attempt_failure(
        db_session,
        attempt_id=att.id,
        task_id="task-33",
        reason_code="idea_provider_error",
        error_message="error to rollback",
        now=T0,
    )
    db_session.rollback()

    db_session.refresh(att)
    assert att.status == "running"
    assert att.reason_code is None


def test_34_caller_commit_persists_finalizer_mutation(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """34. Caller commit mutasyonu kalıcılaştırır."""
    ws, run, brief, targets = _setup_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    att = _create_attempt(
        db_session, brief, targets, status="running", task_id="task-34"
    )

    finalize_ideas_retry_attempt_failure(
        db_session,
        attempt_id=att.id,
        task_id="task-34",
        reason_code="idea_provider_error",
        error_message="error to commit",
        now=T0,
    )
    db_session.commit()

    db_session.refresh(att)
    assert att.status == "failed"
    assert att.reason_code == "idea_provider_error"


# ==============================================================================
# BÖLÜM 3: ORTAK GUARDLAR (35 - 38)
# ==============================================================================


def test_35_heartbeat_attempt_extends_ideas_retry_lease(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """35. heartbeat_attempt running ideas_retry lease’ini uzatır."""
    ws, run, brief, targets = _setup_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    att = _create_attempt(
        db_session,
        brief,
        targets,
        status="running",
        task_id="task-hb",
        started_at=T0,
        heartbeat_at=T0,
        lease_expires_at=T0 + timedelta(seconds=1500),
    )

    t_hb = T0 + timedelta(seconds=300)
    att_out = heartbeat_attempt(db_session, attempt_id=att.id, task_id="task-hb", now=t_hb)
    assert att_out.heartbeat_at == t_hb
    assert att_out.lease_expires_at == t_hb + timedelta(seconds=1500)


def test_36_heartbeat_attempt_rejects_wrong_task(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """36. heartbeat_attempt yanlış task’ı reddeder."""
    ws, run, brief, targets = _setup_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    att = _create_attempt(
        db_session,
        brief,
        targets,
        status="running",
        task_id="task-owner",
        started_at=T0,
        heartbeat_at=T0,
        lease_expires_at=T0 + timedelta(seconds=1500),
    )

    with pytest.raises(AttemptNotClaimableError):
        heartbeat_attempt(db_session, attempt_id=att.id, task_id="task-impostor", now=T0)


def test_37_lock_attempt_for_write_returns_valid_running_ideas_retry_attempt(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """37. lock_attempt_for_write geçerli running ideas_retry attempt’i döndürür."""
    ws, run, brief, targets = _setup_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    att = _create_attempt(
        db_session,
        brief,
        targets,
        status="running",
        task_id="task-write",
        started_at=T0,
        heartbeat_at=T0,
        lease_expires_at=T0 + timedelta(seconds=1500),
    )

    locked = lock_attempt_for_write(db_session, attempt_id=att.id, task_id="task-write", now=T0)
    assert locked.id == att.id
    assert locked.status == "running"


def test_38_lock_attempt_for_write_preserves_safety_on_stale_version_expired(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """38. lock_attempt_for_write stale/version/expired durumlarında mevcut güvenlik davranışını korur."""
    ws, run, brief, targets = _setup_environment(db_session, make_workspace, make_scoring_run, make_keyword)

    # 1. Lease expired
    att1 = _create_attempt(
        db_session,
        brief,
        targets,
        status="running",
        task_id="task-exp",
        lease_expires_at=T0 - timedelta(seconds=5),
        idempotency_key="idemp-exp",
    )
    with pytest.raises(AttemptNotWritableError) as exc1:
        lock_attempt_for_write(db_session, attempt_id=att1.id, task_id="task-exp", now=T0)
    assert exc1.value.error_code == "WORKER_LOST"

    # 2. Brief stale
    brief.is_stale = True
    db_session.commit()
    att2 = _create_attempt(
        db_session,
        brief,
        targets,
        status="running",
        task_id="task-stale",
        lease_expires_at=T0 + timedelta(seconds=1500),
        idempotency_key="idemp-stale",
    )
    with pytest.raises(AttemptNotWritableError) as exc2:
        lock_attempt_for_write(db_session, attempt_id=att2.id, task_id="task-stale", now=T0)
    assert exc2.value.error_code == "BRIEF_STALE"


# ==============================================================================
# BÖLÜM 4: REGRESYON TESTLERİ (39 - 41)
# ==============================================================================


def test_39_existing_claim_ideas_attempt_for_worker_unchanged(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """39. Mevcut claim_ideas_attempt_for_worker testleri değişmeden geçer."""
    ws, run, brief, targets = _setup_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    att_ideas = _create_attempt(db_session, brief, targets, stage="ideas", status="pending")

    att_out, brief_out, run_out, already_comp = claim_ideas_attempt_for_worker(
        db_session,
        attempt_id=att_ideas.id,
        task_id="task-ideas-orig",
        now=T0,
    )
    assert already_comp is False
    assert att_out.id == att_ideas.id
    assert att_out.status == "running"
    assert att_out.stage == "ideas"


def test_40_existing_finalize_ideas_attempt_failure_unchanged(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """40. Mevcut finalize_ideas_attempt_failure testleri değişmeden geçer."""
    ws, run, brief, targets = _setup_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    att_ideas = _create_attempt(
        db_session,
        brief,
        targets,
        stage="ideas",
        status="running",
        task_id="task-ideas-fail",
        started_at=T0,
        heartbeat_at=T0,
        lease_expires_at=T0 + timedelta(seconds=1500),
    )

    att_out, modified = finalize_ideas_attempt_failure(
        db_session,
        attempt_id=att_ideas.id,
        task_id="task-ideas-fail",
        reason_code="idea_provider_error",
        error_message="Ideas stage provider error",
        now=T0,
    )
    assert modified is True
    assert att_out.status == "failed"
    assert att_out.stage == "ideas"
    assert att_out.reason_code == "idea_provider_error"


def test_41_ideas_and_ideas_retry_cross_rejection(
    db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """41. 'ideas' ile 'ideas_retry' wrapper’larının yanlış stage’i karşılıklı reddettiği doğrulanır."""
    ws, run, brief, targets = _setup_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    att_ideas = _create_attempt(
        db_session,
        brief,
        targets,
        stage="ideas",
        status="running",
        task_id="task-cross-1",
        idempotency_key="cross-1",
    )
    att_retry = _create_attempt(
        db_session,
        brief,
        targets,
        stage="ideas_retry",
        status="running",
        task_id="task-cross-2",
        idempotency_key="cross-2",
    )

    # 1. claim_ideas_attempt_for_worker ideas_retry'yi reddeder
    with pytest.raises(AttemptNotWritableError) as exc1:
        claim_ideas_attempt_for_worker(
            db_session,
            attempt_id=att_retry.id,
            task_id="task-cross-2",
            now=T0,
        )
    assert exc1.value.error_code == "INVALID_STAGE"

    # 2. claim_ideas_retry_attempt_for_worker ideas'ı reddeder
    with pytest.raises(AttemptNotWritableError) as exc2:
        claim_ideas_retry_attempt_for_worker(
            db_session,
            attempt_id=att_ideas.id,
            task_id="task-cross-1",
            now=T0,
        )
    assert exc2.value.error_code == "INVALID_STAGE"

    # 3. finalize_ideas_attempt_failure ideas_retry'yi reddeder
    with pytest.raises(AttemptNotWritableError) as exc3:
        finalize_ideas_attempt_failure(
            db_session,
            attempt_id=att_retry.id,
            task_id="task-cross-2",
            reason_code="idea_provider_error",
            error_message="fail",
            now=T0,
        )
    assert exc3.value.error_code == "INVALID_STAGE"

    # 4. finalize_ideas_retry_attempt_failure ideas'ı reddeder
    with pytest.raises(AttemptNotWritableError) as exc4:
        finalize_ideas_retry_attempt_failure(
            db_session,
            attempt_id=att_ideas.id,
            task_id="task-cross-1",
            reason_code="idea_provider_error",
            error_message="fail",
            now=T0,
        )
    assert exc4.value.error_code == "INVALID_STAGE"
