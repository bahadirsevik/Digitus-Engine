# -*- coding: utf-8 -*-
"""MİKRO FAZ F1-F.7.6 — Ideas Retry Success-Finalize Lock Yardımcısı Entegrasyon Testleri.

Tüm testler gerçek PostgreSQL test veritabanını ve SQLAlchemy oturumunu kullanır.
Test edilen alanlar:
1. running ideas_retry + aynı task + geçerli lease: kilit alınır, already_completed=False, attempt/brief/scoring_run doğru döner.
2. completed ideas_retry + aynı task: already_completed=True döner.
3. completed ideas_retry + farklı task: TASK_MISMATCH fırlatılır.
4. pending ideas_retry: PENDING_ATTEMPT_NOT_CLAIMABLE fırlatılır.
5. running ideas_retry + farklı task: TASK_MISMATCH fırlatılır.
6. running ideas_retry + expired lease: WORKER_LOST, attempt failed/worker_lost olur, completed_at yazılır, lease temizlenir.
7. stale brief: BRIEF_STALE, attempt failed/brief_stale olur.
8. assignment version mismatch: ASSIGNMENT_CHANGED, attempt failed/assignment_changed olur.
9. failed ideas_retry: ATTEMPT_NOT_WRITABLE fırlatılır.
10. partial ideas_retry: ATTEMPT_NOT_WRITABLE fırlatılır.
11. stage="ideas" attempt yeni retry wrapper'a verilirse: INVALID_STAGE.
12. stage="ideas_retry" attempt normal ideas wrapper'a verilirse: INVALID_STAGE.
13. Olmayan attempt: ATTEMPT_NOT_FOUND (AttemptNotFoundError).
14. Geçersiz attempt_id: fail-closed ValueError.
15. Boş/whitespace task_id: ValueError.
16. Naive now: ValueError.
17. Helper commit veya rollback çağırmaz.
18. Running happy path attempt'in coverage, warnings ve requested_target_ids alanlarını değiştirmez.
19. Completed replay hiçbir attempt alanını değiştirmez.
20. İki session eşzamanlı lock testi: canonical sıra korunur, deadlock oluşmaz, ikinci işlem tutarlı sonuç görür.
21. Mevcut lock_ideas_attempt_for_finalize regresyon testi: normal ideas davranışı aynen korunur.
22. Private çekirdeğe geçersiz expected_stage verilirse ValueError.
"""
from __future__ import annotations

import copy
import threading
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy.orm import Session

from app.database.connection import SessionLocal
from app.database.models import (
    ChannelPool,
    ScoringRun,
    SocialBrief,
    SocialBriefKeyword,
    SocialBriefTarget,
    SocialGenerationAttempt,
)
from app.generators.social.attempt_state import (
    AttemptNotFoundError,
    AttemptNotWritableError,
    _lock_social_idea_attempt_for_finalize,
    claim_attempt,
    create_or_get_attempt,
    finish_attempt,
    lock_ideas_attempt_for_finalize,
    lock_ideas_retry_attempt_for_finalize,
)

T0 = datetime(2026, 9, 25, 12, 0, 0, tzinfo=timezone.utc)


def _setup_test_environment(
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
    workspace_name: str = "Retry Finalize Lock Brand",
    is_stale: bool = False,
    brief_version: int = 1,
    run_version: int = 1,
):
    """Test için workspace, run, brief ve target kayıtlarını hazırlar."""
    workspace = make_workspace(name=workspace_name, status="confirmed")
    run = make_scoring_run(
        brand_profile_id=workspace.id,
        status="completed",
        algorithm_version="v2",
        channel_pool_policy_version=workspace.policy_version or 1,
        relevance_anchor_version=workspace.anchor_version or 1,
        channel_assignment_version=run_version,
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
        channel_assignment_version=brief_version,
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


def _create_retry_attempt(
    db_session: Session,
    brief: SocialBrief,
    targets: list[SocialBriefTarget],
    status: str = "pending",
    task_id: str | None = None,
    idempotency_key: str = "retry-lock-test-01",
    lease_expires_at: datetime | None = None,
) -> SocialGenerationAttempt:
    """Doğrulanabilir bir ideas_retry attempt kaydı oluşturur."""
    attempt = SocialGenerationAttempt(
        brief_id=brief.id,
        stage="ideas_retry",
        idempotency_key=idempotency_key,
        status=status,
        task_id=task_id,
        requested_target_ids=[t.id for t in targets],
        coverage={"schema_version": "ideas_retry_plan_v1", "baseline": {}, "plan": {}},
        warnings=[],
        heartbeat_at=T0 if status == "running" else None,
        started_at=T0 if status == "running" else None,
        lease_expires_at=lease_expires_at or (T0 + timedelta(seconds=1500) if status in ("pending", "running") else None),
        created_at=T0,
    )
    db_session.add(attempt)
    db_session.commit()
    db_session.refresh(attempt)
    return attempt


# ==================== TESTLER ====================


def test_01_running_retry_same_task_valid_lease_success(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """1. running ideas_retry + aynı task + geçerli lease: kilit alınır, already_completed=False, attempt/brief/run döner."""
    ws, run, brief, targets = _setup_test_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    att = _create_retry_attempt(db_session, brief, targets, status="running", task_id="task-01")

    locked_att, locked_brief, locked_run, already_completed = lock_ideas_retry_attempt_for_finalize(
        db_session,
        attempt_id=att.id,
        task_id="task-01",
        now=T0,
    )

    assert already_completed is False
    assert locked_att.id == att.id
    assert locked_att.status == "running"
    assert locked_brief.id == brief.id
    assert locked_run.id == run.id


def test_02_completed_retry_same_task_returns_already_completed(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """2. completed ideas_retry + aynı task: already_completed=True."""
    ws, run, brief, targets = _setup_test_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    att = _create_retry_attempt(db_session, brief, targets, status="completed", task_id="task-02")

    locked_att, locked_brief, locked_run, already_completed = lock_ideas_retry_attempt_for_finalize(
        db_session,
        attempt_id=att.id,
        task_id="task-02",
        now=T0,
    )

    assert already_completed is True
    assert locked_att.id == att.id
    assert locked_att.status == "completed"


def test_03_completed_retry_different_task_raises_task_mismatch(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """3. completed ideas_retry + farklı task: TASK_MISMATCH."""
    ws, run, brief, targets = _setup_test_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    att = _create_retry_attempt(db_session, brief, targets, status="completed", task_id="task-owner")

    with pytest.raises(AttemptNotWritableError) as exc_info:
        lock_ideas_retry_attempt_for_finalize(
            db_session,
            attempt_id=att.id,
            task_id="task-intruder",
            now=T0,
        )
    assert exc_info.value.error_code == "TASK_MISMATCH"


def test_04_pending_retry_raises_pending_not_claimable(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """4. pending ideas_retry: PENDING_ATTEMPT_NOT_CLAIMABLE."""
    ws, run, brief, targets = _setup_test_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    att = _create_retry_attempt(db_session, brief, targets, status="pending")

    with pytest.raises(AttemptNotWritableError) as exc_info:
        lock_ideas_retry_attempt_for_finalize(
            db_session,
            attempt_id=att.id,
            task_id="task-04",
            now=T0,
        )
    assert exc_info.value.error_code == "PENDING_ATTEMPT_NOT_CLAIMABLE"


def test_05_running_retry_different_task_raises_task_mismatch(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """5. running ideas_retry + farklı task: TASK_MISMATCH."""
    ws, run, brief, targets = _setup_test_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    att = _create_retry_attempt(db_session, brief, targets, status="running", task_id="task-owner")

    with pytest.raises(AttemptNotWritableError) as exc_info:
        lock_ideas_retry_attempt_for_finalize(
            db_session,
            attempt_id=att.id,
            task_id="task-intruder",
            now=T0,
        )
    assert exc_info.value.error_code == "TASK_MISMATCH"


def test_06_running_retry_expired_lease_fails_as_worker_lost(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """6. running ideas_retry + expired lease: WORKER_LOST, attempt failed olur, completed_at yazılır, lease temizlenir."""
    ws, run, brief, targets = _setup_test_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    expired_time = T0 - timedelta(seconds=10)
    att = _create_retry_attempt(
        db_session,
        brief,
        targets,
        status="running",
        task_id="task-06",
        lease_expires_at=expired_time,
    )

    with pytest.raises(AttemptNotWritableError) as exc_info:
        lock_ideas_retry_attempt_for_finalize(
            db_session,
            attempt_id=att.id,
            task_id="task-06",
            now=T0,
        )

    assert exc_info.value.error_code == "WORKER_LOST"
    assert att.status == "failed"
    assert att.reason_code == "worker_lost"
    assert att.completed_at == T0
    assert att.lease_expires_at is None


def test_07_stale_brief_fails_as_brief_stale(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """7. stale brief: BRIEF_STALE, attempt failed/brief_stale olur."""
    ws, run, brief, targets = _setup_test_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, is_stale=True
    )
    att = _create_retry_attempt(db_session, brief, targets, status="running", task_id="task-07")

    with pytest.raises(AttemptNotWritableError) as exc_info:
        lock_ideas_retry_attempt_for_finalize(
            db_session,
            attempt_id=att.id,
            task_id="task-07",
            now=T0,
        )

    assert exc_info.value.error_code == "BRIEF_STALE"
    assert att.status == "failed"
    assert att.reason_code == "brief_stale"
    assert att.completed_at == T0
    assert att.lease_expires_at is None


def test_08_assignment_version_mismatch_fails_as_assignment_changed(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """8. assignment version mismatch: ASSIGNMENT_CHANGED, attempt failed/assignment_changed olur."""
    ws, run, brief, targets = _setup_test_environment(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        brief_version=2,
        run_version=1,
    )
    att = _create_retry_attempt(db_session, brief, targets, status="running", task_id="task-08")

    with pytest.raises(AttemptNotWritableError) as exc_info:
        lock_ideas_retry_attempt_for_finalize(
            db_session,
            attempt_id=att.id,
            task_id="task-08",
            now=T0,
        )

    assert exc_info.value.error_code == "ASSIGNMENT_CHANGED"
    assert att.status == "failed"
    assert att.reason_code == "assignment_changed"
    assert att.completed_at == T0
    assert att.lease_expires_at is None


def test_09_failed_retry_raises_attempt_not_writable(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """9. failed ideas_retry: ATTEMPT_NOT_WRITABLE."""
    ws, run, brief, targets = _setup_test_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    att = _create_retry_attempt(db_session, brief, targets, status="failed", task_id="task-09")

    with pytest.raises(AttemptNotWritableError) as exc_info:
        lock_ideas_retry_attempt_for_finalize(
            db_session,
            attempt_id=att.id,
            task_id="task-09",
            now=T0,
        )
    assert exc_info.value.error_code == "ATTEMPT_NOT_WRITABLE"


def test_10_partial_retry_raises_attempt_not_writable(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """10. partial ideas_retry: ATTEMPT_NOT_WRITABLE."""
    ws, run, brief, targets = _setup_test_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    att = _create_retry_attempt(db_session, brief, targets, status="partial", task_id="task-10")

    with pytest.raises(AttemptNotWritableError) as exc_info:
        lock_ideas_retry_attempt_for_finalize(
            db_session,
            attempt_id=att.id,
            task_id="task-10",
            now=T0,
        )
    assert exc_info.value.error_code == "ATTEMPT_NOT_WRITABLE"


def test_11_ideas_stage_attempt_rejected_by_retry_wrapper(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """11. stage='ideas' attempt yeni retry wrapper'a verilirse: INVALID_STAGE."""
    ws, run, brief, targets = _setup_test_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    att = SocialGenerationAttempt(
        brief_id=brief.id,
        stage="ideas",
        idempotency_key="ideas-stage-test",
        status="running",
        task_id="task-11",
        requested_target_ids=[t.id for t in targets],
        lease_expires_at=T0 + timedelta(seconds=1500),
        created_at=T0,
    )
    db_session.add(att)
    db_session.commit()

    with pytest.raises(AttemptNotWritableError) as exc_info:
        lock_ideas_retry_attempt_for_finalize(
            db_session,
            attempt_id=att.id,
            task_id="task-11",
            now=T0,
        )
    assert exc_info.value.error_code == "INVALID_STAGE"


def test_12_ideas_retry_stage_attempt_rejected_by_normal_ideas_wrapper(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """12. stage='ideas_retry' attempt normal ideas wrapper'a verilirse: INVALID_STAGE."""
    ws, run, brief, targets = _setup_test_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    att = _create_retry_attempt(db_session, brief, targets, status="running", task_id="task-12")

    with pytest.raises(AttemptNotWritableError) as exc_info:
        lock_ideas_attempt_for_finalize(
            db_session,
            attempt_id=att.id,
            task_id="task-12",
            now=T0,
        )
    assert exc_info.value.error_code == "INVALID_STAGE"


def test_13_non_existent_attempt_raises_attempt_not_found(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """13. Olmayan attempt: ATTEMPT_NOT_FOUND (AttemptNotFoundError)."""
    with pytest.raises(AttemptNotFoundError) as exc_info:
        lock_ideas_retry_attempt_for_finalize(
            db_session,
            attempt_id=999999,
            task_id="task-13",
            now=T0,
        )
    assert exc_info.value.error_code == "ATTEMPT_NOT_FOUND"


def test_14_invalid_attempt_id_raises_value_error(
    db_session
):
    """14. Geçersiz attempt_id: fail-closed ValueError."""
    with pytest.raises(ValueError):
        lock_ideas_retry_attempt_for_finalize(
            db_session,
            attempt_id=0,
            task_id="task-14",
            now=T0,
        )

    with pytest.raises(ValueError):
        lock_ideas_retry_attempt_for_finalize(
            db_session,
            attempt_id=-5,
            task_id="task-14",
            now=T0,
        )

    with pytest.raises(ValueError):
        lock_ideas_retry_attempt_for_finalize(
            db_session,
            attempt_id=True,  # bool reddedilmeli
            task_id="task-14",
            now=T0,
        )


def test_15_empty_or_whitespace_task_id_raises_value_error(
    db_session
):
    """15. Boş/whitespace task_id: ValueError."""
    with pytest.raises(ValueError, match="task_id"):
        lock_ideas_retry_attempt_for_finalize(
            db_session,
            attempt_id=1,
            task_id="",
            now=T0,
        )

    with pytest.raises(ValueError, match="task_id"):
        lock_ideas_retry_attempt_for_finalize(
            db_session,
            attempt_id=1,
            task_id="   ",
            now=T0,
        )


def test_16_naive_now_is_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """16. Naive now: ValueError."""
    ws, run, brief, targets = _setup_test_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    att = _create_retry_attempt(db_session, brief, targets, status="running", task_id="task-16")

    naive_now = datetime(2026, 9, 25, 12, 0, 0)  # tzinfo None
    with pytest.raises(ValueError, match="timezone-aware"):
        lock_ideas_retry_attempt_for_finalize(
            db_session,
            attempt_id=att.id,
            task_id="task-16",
            now=naive_now,
        )


def test_17_helper_does_not_commit_or_rollback(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """17. Helper commit veya rollback çağırmaz."""
    ws, run, brief, targets = _setup_test_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    att = _create_retry_attempt(db_session, brief, targets, status="running", task_id="task-17")

    with patch.object(db_session, "commit") as mock_commit, patch.object(
        db_session, "rollback"
    ) as mock_rollback:
        lock_ideas_retry_attempt_for_finalize(
            db_session,
            attempt_id=att.id,
            task_id="task-17",
            now=T0,
        )
        assert mock_commit.call_count == 0
        assert mock_rollback.call_count == 0


def test_18_running_happy_path_does_not_mutate_coverage_warnings_or_requested_targets(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """18. Running happy path attempt'in coverage, warnings ve requested_target_ids alanlarını değiştirmez."""
    ws, run, brief, targets = _setup_test_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    att = _create_retry_attempt(db_session, brief, targets, status="running", task_id="task-18")

    cov_before = copy.deepcopy(att.coverage)
    req_targets_before = list(att.requested_target_ids)
    warn_before = list(att.warnings)

    lock_ideas_retry_attempt_for_finalize(
        db_session,
        attempt_id=att.id,
        task_id="task-18",
        now=T0,
    )

    db_session.refresh(att)
    assert att.coverage == cov_before
    assert list(att.requested_target_ids) == req_targets_before
    assert list(att.warnings) == warn_before


def test_19_completed_replay_does_not_mutate_attempt(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """19. Completed replay hiçbir attempt alanını değiştirmez."""
    ws, run, brief, targets = _setup_test_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    att = _create_retry_attempt(db_session, brief, targets, status="completed", task_id="task-19")
    att.completed_at = T0
    db_session.commit()

    state_before = {
        "status": att.status,
        "task_id": att.task_id,
        "completed_at": att.completed_at,
        "heartbeat_at": att.heartbeat_at,
        "lease_expires_at": att.lease_expires_at,
    }

    locked_att, _, _, already_completed = lock_ideas_retry_attempt_for_finalize(
        db_session,
        attempt_id=att.id,
        task_id="task-19",
        now=T0,
    )

    assert already_completed is True
    assert locked_att.status == state_before["status"]
    assert locked_att.task_id == state_before["task_id"]
    assert locked_att.completed_at == state_before["completed_at"]
    assert locked_att.heartbeat_at == state_before["heartbeat_at"]
    assert locked_att.lease_expires_at == state_before["lease_expires_at"]


def test_20_concurrent_locking_preserves_canonical_order_no_deadlock(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """20. İki session eşzamanlı lock testi: canonical sıra korunur, deadlock oluşmaz, ikinci işlem tutarlı sonuç görür."""
    ws, run, brief, targets = _setup_test_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    att = _create_retry_attempt(db_session, brief, targets, status="running", task_id="task-20")

    session_a = SessionLocal()
    session_b = SessionLocal()

    barrier_a_locked = threading.Event()
    barrier_b_done = threading.Event()
    t_b_errors = []
    t_b_results = []

    def transaction_a():
        try:
            locked_att_a, _, _, comp_a = lock_ideas_retry_attempt_for_finalize(
                session_a,
                attempt_id=att.id,
                task_id="task-20",
                now=T0,
            )
            assert comp_a is False
            barrier_a_locked.set()

            # Transaction A kısa bir süre kilidi tutar ve tamamlayıp commit eder
            time.sleep(0.3)
            locked_att_a.status = "completed"
            locked_att_a.completed_at = T0
            session_a.commit()
        except Exception as e:
            session_a.rollback()
            raise e
        finally:
            session_a.close()

    def transaction_b():
        try:
            # Transaction A'nın kilit almasını bekle
            assert barrier_a_locked.wait(timeout=3.0)
            # Transaction B kilit talep eder; Session A bitene kadar satır kilitlerinde bekler
            locked_att_b, _, _, comp_b = lock_ideas_retry_attempt_for_finalize(
                session_b,
                attempt_id=att.id,
                task_id="task-20",
                now=T0,
            )
            t_b_results.append(comp_b)
            session_b.commit()
        except Exception as e:
            session_b.rollback()
            t_b_errors.append(e)
        finally:
            session_b.close()
            barrier_b_done.set()

    t_a = threading.Thread(target=transaction_a, name="thread-lock-A")
    t_b = threading.Thread(target=transaction_b, name="thread-lock-B")

    t_a.start()
    t_b.start()

    t_a.join(timeout=5.0)
    t_b.join(timeout=5.0)

    assert not t_a.is_alive(), "Thread A zaman aşımına uğradı (olası deadlock)"
    assert not t_b.is_alive(), "Thread B zaman aşımına uğradı (olası deadlock)"
    assert len(t_b_errors) == 0, f"Thread B hata aldı: {t_b_errors}"
    assert len(t_b_results) == 1
    # Transaction B, Transaction A'nın completed yaptığını görerek already_completed=True dönmeli
    assert t_b_results[0] is True


def test_21_normal_ideas_lock_regression(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """21. Mevcut lock_ideas_attempt_for_finalize normal ideas davranışını aynen korur."""
    ws, run, brief, targets = _setup_test_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    att = SocialGenerationAttempt(
        brief_id=brief.id,
        stage="ideas",
        idempotency_key="normal-ideas-finalize-test",
        status="running",
        task_id="task-normal-21",
        requested_target_ids=[t.id for t in targets],
        lease_expires_at=T0 + timedelta(seconds=1500),
        created_at=T0,
    )
    db_session.add(att)
    db_session.commit()

    locked_att, locked_brief, locked_run, already_completed = lock_ideas_attempt_for_finalize(
        db_session,
        attempt_id=att.id,
        task_id="task-normal-21",
        now=T0,
    )

    assert already_completed is False
    assert locked_att.id == att.id
    assert locked_att.stage == "ideas"
    assert locked_brief.id == brief.id
    assert locked_run.id == run.id


def test_22_invalid_expected_stage_in_core_raises_value_error(
    db_session
):
    """22. Private çekirdeğe geçersiz expected_stage verilirse ValueError."""
    with pytest.raises(ValueError, match="Geçersiz expected_stage"):
        _lock_social_idea_attempt_for_finalize(
            db_session,
            attempt_id=1,
            task_id="task-22",
            expected_stage="contents",  # İzin verilmeyen stage
            now=T0,
        )

    with pytest.raises(ValueError, match="Geçersiz expected_stage"):
        _lock_social_idea_attempt_for_finalize(
            db_session,
            attempt_id=1,
            task_id="task-22",
            expected_stage="invalid_stage_xyz",
            now=T0,
        )
