# -*- coding: utf-8 -*-
"""Entegrasyon Testleri: Attempt Lifecycle Genelinde Worker Kimliği Sanitizasyonu (F1-G.5.2b).

Bu test modülü attempt_state.py içindeki tüm worker ownership kontrollerinde:
- DB ve çağıran sentinel task ID'lerinin exception str ve repr çıktılarına asla sızmadığını,
- Statik "Worker sahipliği doğrulanamadı." mesajının döndüğünü,
- Beklenen exception sınıfı ve error_code değerlerinin tam olarak korunduğunu,
- Hata sonrasında attempt nesnesinin (status, task_id, coverage, lease_expires_at,
  heartbeat_at, reason_code, error_message) kesinlikle mutate edilmediğini
izole test DB üzerinde doğrular.
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from app.database.models import SocialBrief, SocialGenerationAttempt
from app.generators.social.attempt_state import (
    AttemptNotClaimableError,
    AttemptNotWritableError,
    claim_attempt,
    claim_contents_attempt_for_worker,
    claim_ideas_attempt_for_worker,
    claim_ideas_retry_attempt_for_worker,
    create_or_get_attempt,
    finish_attempt,
    heartbeat_attempt,
    lock_attempt_for_write,
    lock_contents_attempt_for_content_write,
    lock_ideas_attempt_for_finalize,
    lock_ideas_retry_attempt_for_finalize,
)

T0 = datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc)
EXPECTED_STATIC_MESSAGE = "Worker sahipliği doğrulanamadı."


@pytest.fixture
def test_brief(db_session: Session, make_workspace, make_scoring_run) -> SocialBrief:
    ws = make_workspace("Ownership Sanitization WS")
    run = make_scoring_run(
        brand_profile_id=ws.id,
        status="channel_assigned",
        channel_assignment_version=1,
    )
    brief = SocialBrief(
        scoring_run_id=run.id,
        brand_name_snapshot="Sanitization Brand",
        brand_context_snapshot="Sanitization Context",
        channel_assignment_version=1,
        format_matrix_version="v1",
        is_stale=False,
    )
    db_session.add(brief)
    db_session.commit()
    db_session.refresh(brief)
    return brief


def _snapshot_attempt(attempt: SocialGenerationAttempt) -> dict:
    return {
        "status": attempt.status,
        "task_id": attempt.task_id,
        "coverage": copy.deepcopy(attempt.coverage),
        "lease_expires_at": attempt.lease_expires_at,
        "heartbeat_at": attempt.heartbeat_at,
        "started_at": attempt.started_at,
        "completed_at": attempt.completed_at,
        "reason_code": attempt.reason_code,
        "error_message": attempt.error_message,
    }


def _assert_sanitized_and_unmutated(
    exc: Exception,
    *,
    db_session: Session,
    attempt: SocialGenerationAttempt,
    initial_snapshot: dict,
    db_sentinel: str,
    caller_sentinel: str,
    expected_error_code: str,
) -> None:
    err_str = str(exc)
    err_repr = repr(exc)

    assert err_str == EXPECTED_STATIC_MESSAGE
    assert EXPECTED_STATIC_MESSAGE in err_repr
    assert db_sentinel not in err_str
    assert caller_sentinel not in err_str
    assert db_sentinel not in err_repr
    assert caller_sentinel not in err_repr
    assert getattr(exc, "error_code", None) == expected_error_code

    db_session.refresh(attempt)
    current_snapshot = _snapshot_attempt(attempt)
    assert current_snapshot == initial_snapshot


def test_01_heartbeat_attempt_ownership_mismatch_sanitization(
    db_session: Session, test_brief: SocialBrief
):
    """1. heartbeat_attempt task_id uyuşmazlığında hassas ID sızdırmaz ve state korur."""
    sentinel_db = "SECRET_DB_HB_TASK_1111"
    sentinel_caller = "INTRUDER_CALLER_HB_TASK_2222"

    att, _ = create_or_get_attempt(
        db_session,
        brief_id=test_brief.id,
        stage="ideas",
        idempotency_key="key-hb-sanitization",
        now=T0,
    )
    claim_attempt(db_session, attempt_id=att.id, task_id=sentinel_db, now=T0)
    db_session.commit()
    db_session.refresh(att)

    initial_snapshot = _snapshot_attempt(att)

    with pytest.raises(AttemptNotClaimableError) as exc_info:
        heartbeat_attempt(
            db_session,
            attempt_id=att.id,
            task_id=sentinel_caller,
            now=T0 + timedelta(seconds=60),
        )

    _assert_sanitized_and_unmutated(
        exc_info.value,
        db_session=db_session,
        attempt=att,
        initial_snapshot=initial_snapshot,
        db_sentinel=sentinel_db,
        caller_sentinel=sentinel_caller,
        expected_error_code="ATTEMPT_NOT_CLAIMABLE",
    )


def test_02_lock_attempt_for_write_ownership_mismatch_sanitization(
    db_session: Session, test_brief: SocialBrief
):
    """2. lock_attempt_for_write guard'ı task_id uyuşmazlığında hassas ID sızdırmaz ve state korur."""
    sentinel_db = "SECRET_DB_WRITE_GUARD_3333"
    sentinel_caller = "INTRUDER_CALLER_WRITE_GUARD_4444"

    att, _ = create_or_get_attempt(
        db_session,
        brief_id=test_brief.id,
        stage="ideas",
        idempotency_key="key-write-guard-sanitization",
        now=T0,
    )
    claim_attempt(db_session, attempt_id=att.id, task_id=sentinel_db, now=T0)
    db_session.commit()
    db_session.refresh(att)

    initial_snapshot = _snapshot_attempt(att)

    with pytest.raises(AttemptNotWritableError) as exc_info:
        lock_attempt_for_write(
            db_session,
            attempt_id=att.id,
            task_id=sentinel_caller,
            now=T0,
        )

    _assert_sanitized_and_unmutated(
        exc_info.value,
        db_session=db_session,
        attempt=att,
        initial_snapshot=initial_snapshot,
        db_sentinel=sentinel_db,
        caller_sentinel=sentinel_caller,
        expected_error_code="TASK_MISMATCH",
    )


def test_03_finish_attempt_running_ownership_mismatch_sanitization(
    db_session: Session, test_brief: SocialBrief
):
    """3. finish_attempt running durumunda yabancı task_id ile çağrıldığında hassas ID sızdırmaz ve state korur."""
    sentinel_db = "SECRET_DB_FINISH_RUN_5555"
    sentinel_caller = "INTRUDER_CALLER_FINISH_RUN_6666"

    att, _ = create_or_get_attempt(
        db_session,
        brief_id=test_brief.id,
        stage="ideas",
        idempotency_key="key-finish-run-sanitization",
        now=T0,
    )
    claim_attempt(db_session, attempt_id=att.id, task_id=sentinel_db, now=T0)
    db_session.commit()
    db_session.refresh(att)

    initial_snapshot = _snapshot_attempt(att)

    with pytest.raises(AttemptNotWritableError) as exc_info:
        finish_attempt(
            db_session,
            attempt_id=att.id,
            task_id=sentinel_caller,
            status="completed",
            now=T0,
        )

    _assert_sanitized_and_unmutated(
        exc_info.value,
        db_session=db_session,
        attempt=att,
        initial_snapshot=initial_snapshot,
        db_sentinel=sentinel_db,
        caller_sentinel=sentinel_caller,
        expected_error_code="TASK_MISMATCH",
    )


def test_04_finish_attempt_completed_replay_ownership_mismatch_sanitization(
    db_session: Session, test_brief: SocialBrief
):
    """4. finish_attempt completed durumundaki replay sırasında yabancı task_id ile çağrıldığında hassas ID sızdırmaz."""
    sentinel_db = "SECRET_DB_FINISH_COMP_7777"
    sentinel_caller = "INTRUDER_CALLER_FINISH_COMP_8888"

    att, _ = create_or_get_attempt(
        db_session,
        brief_id=test_brief.id,
        stage="ideas",
        idempotency_key="key-finish-comp-sanitization",
        now=T0,
    )
    claim_attempt(db_session, attempt_id=att.id, task_id=sentinel_db, now=T0)
    finish_attempt(
        db_session,
        attempt_id=att.id,
        task_id=sentinel_db,
        status="completed",
        now=T0,
    )
    db_session.commit()
    db_session.refresh(att)

    initial_snapshot = _snapshot_attempt(att)

    with pytest.raises(AttemptNotWritableError) as exc_info:
        finish_attempt(
            db_session,
            attempt_id=att.id,
            task_id=sentinel_caller,
            status="completed",
            now=T0,
        )

    _assert_sanitized_and_unmutated(
        exc_info.value,
        db_session=db_session,
        attempt=att,
        initial_snapshot=initial_snapshot,
        db_sentinel=sentinel_db,
        caller_sentinel=sentinel_caller,
        expected_error_code="TASK_MISMATCH",
    )


def test_05_lock_ideas_attempt_for_finalize_running_mismatch_sanitization(
    db_session: Session, test_brief: SocialBrief
):
    """5. lock_ideas_attempt_for_finalize running durumunda yabancı task_id ile çağrıldığında ID sızdırmaz."""
    sentinel_db = "SECRET_DB_IDEAS_FIN_RUN_9999"
    sentinel_caller = "INTRUDER_CALLER_IDEAS_FIN_RUN_AAAA"

    att, _ = create_or_get_attempt(
        db_session,
        brief_id=test_brief.id,
        stage="ideas",
        idempotency_key="key-ideas-fin-run-sanitization",
        now=T0,
    )
    claim_attempt(db_session, attempt_id=att.id, task_id=sentinel_db, now=T0)
    db_session.commit()
    db_session.refresh(att)

    initial_snapshot = _snapshot_attempt(att)

    with pytest.raises(AttemptNotWritableError) as exc_info:
        lock_ideas_attempt_for_finalize(
            db_session,
            attempt_id=att.id,
            task_id=sentinel_caller,
            now=T0,
        )

    _assert_sanitized_and_unmutated(
        exc_info.value,
        db_session=db_session,
        attempt=att,
        initial_snapshot=initial_snapshot,
        db_sentinel=sentinel_db,
        caller_sentinel=sentinel_caller,
        expected_error_code="TASK_MISMATCH",
    )


def test_06_lock_ideas_attempt_for_finalize_completed_mismatch_sanitization(
    db_session: Session, test_brief: SocialBrief
):
    """6. lock_ideas_attempt_for_finalize completed durumunda yabancı task_id ile çağrıldığında ID sızdırmaz."""
    sentinel_db = "SECRET_DB_IDEAS_FIN_COMP_BBBB"
    sentinel_caller = "INTRUDER_CALLER_IDEAS_FIN_COMP_CCCC"

    att, _ = create_or_get_attempt(
        db_session,
        brief_id=test_brief.id,
        stage="ideas",
        idempotency_key="key-ideas-fin-comp-sanitization",
        now=T0,
    )
    claim_attempt(db_session, attempt_id=att.id, task_id=sentinel_db, now=T0)
    finish_attempt(
        db_session,
        attempt_id=att.id,
        task_id=sentinel_db,
        status="completed",
        now=T0,
    )
    db_session.commit()
    db_session.refresh(att)

    initial_snapshot = _snapshot_attempt(att)

    with pytest.raises(AttemptNotWritableError) as exc_info:
        lock_ideas_attempt_for_finalize(
            db_session,
            attempt_id=att.id,
            task_id=sentinel_caller,
            now=T0,
        )

    _assert_sanitized_and_unmutated(
        exc_info.value,
        db_session=db_session,
        attempt=att,
        initial_snapshot=initial_snapshot,
        db_sentinel=sentinel_db,
        caller_sentinel=sentinel_caller,
        expected_error_code="TASK_MISMATCH",
    )


def test_07_lock_ideas_retry_attempt_for_finalize_running_mismatch_sanitization(
    db_session: Session, test_brief: SocialBrief
):
    """7. lock_ideas_retry_attempt_for_finalize running durumunda yabancı task_id ile çağrıldığında ID sızdırmaz."""
    sentinel_db = "SECRET_DB_RETRY_FIN_RUN_DDDD"
    sentinel_caller = "INTRUDER_CALLER_RETRY_FIN_RUN_EEEE"

    att, _ = create_or_get_attempt(
        db_session,
        brief_id=test_brief.id,
        stage="ideas_retry",
        idempotency_key="key-retry-fin-run-sanitization",
        now=T0,
    )
    claim_ideas_retry_attempt_for_worker(
        db_session, attempt_id=att.id, task_id=sentinel_db, now=T0
    )
    db_session.commit()
    db_session.refresh(att)

    initial_snapshot = _snapshot_attempt(att)

    with pytest.raises(AttemptNotWritableError) as exc_info:
        lock_ideas_retry_attempt_for_finalize(
            db_session,
            attempt_id=att.id,
            task_id=sentinel_caller,
            now=T0,
        )

    _assert_sanitized_and_unmutated(
        exc_info.value,
        db_session=db_session,
        attempt=att,
        initial_snapshot=initial_snapshot,
        db_sentinel=sentinel_db,
        caller_sentinel=sentinel_caller,
        expected_error_code="TASK_MISMATCH",
    )


def test_08_lock_ideas_retry_attempt_for_finalize_completed_mismatch_sanitization(
    db_session: Session, test_brief: SocialBrief
):
    """8. lock_ideas_retry_attempt_for_finalize completed durumunda yabancı task_id ile çağrıldığında ID sızdırmaz."""
    sentinel_db = "SECRET_DB_RETRY_FIN_COMP_FFFF"
    sentinel_caller = "INTRUDER_CALLER_RETRY_FIN_COMP_GGGG"

    att, _ = create_or_get_attempt(
        db_session,
        brief_id=test_brief.id,
        stage="ideas_retry",
        idempotency_key="key-retry-fin-comp-sanitization",
        now=T0,
    )
    claim_ideas_retry_attempt_for_worker(
        db_session, attempt_id=att.id, task_id=sentinel_db, now=T0
    )
    finish_attempt(
        db_session,
        attempt_id=att.id,
        task_id=sentinel_db,
        status="completed",
        now=T0,
    )
    db_session.commit()
    db_session.refresh(att)

    initial_snapshot = _snapshot_attempt(att)

    with pytest.raises(AttemptNotWritableError) as exc_info:
        lock_ideas_retry_attempt_for_finalize(
            db_session,
            attempt_id=att.id,
            task_id=sentinel_caller,
            now=T0,
        )

    _assert_sanitized_and_unmutated(
        exc_info.value,
        db_session=db_session,
        attempt=att,
        initial_snapshot=initial_snapshot,
        db_sentinel=sentinel_db,
        caller_sentinel=sentinel_caller,
        expected_error_code="TASK_MISMATCH",
    )


def test_09_lock_contents_attempt_for_content_write_mismatch_sanitization(
    db_session: Session, test_brief: SocialBrief
):
    """9. lock_contents_attempt_for_content_write running task_id uyuşmazlığında ID sızdırmaz."""
    sentinel_db = "SECRET_DB_CONT_WRITE_HHHH"
    sentinel_caller = "INTRUDER_CALLER_CONT_WRITE_IIII"

    att, _ = create_or_get_attempt(
        db_session,
        brief_id=test_brief.id,
        stage="contents",
        idempotency_key="key-cont-write-sanitization",
        now=T0,
    )
    claim_contents_attempt_for_worker(
        db_session, attempt_id=att.id, task_id=sentinel_db, now=T0
    )
    db_session.commit()
    db_session.refresh(att)

    initial_snapshot = _snapshot_attempt(att)

    with pytest.raises(AttemptNotWritableError) as exc_info:
        lock_contents_attempt_for_content_write(
            db_session,
            attempt_id=att.id,
            brief_id=test_brief.id,
            idea_id=1,
            task_id=sentinel_caller,
            now=T0,
        )

    _assert_sanitized_and_unmutated(
        exc_info.value,
        db_session=db_session,
        attempt=att,
        initial_snapshot=initial_snapshot,
        db_sentinel=sentinel_db,
        caller_sentinel=sentinel_caller,
        expected_error_code="TASK_MISMATCH",
    )


def test_10a_claim_attempt_ownership_sanitization(
    db_session: Session, test_brief: SocialBrief
):
    """10a. claim_attempt running attempt'e yabancı task ile çağrıldığında ID sızdırmaz ve state korur."""
    s_db_claim = "SECRET_DB_CLAIM_ATT_JJJJ"
    s_caller_claim = "INTRUDER_CALLER_CLAIM_ATT_KKKK"

    att_claim, _ = create_or_get_attempt(
        db_session,
        brief_id=test_brief.id,
        stage="ideas",
        idempotency_key="key-claim-att-sanitization",
        now=T0,
    )
    claim_attempt(db_session, attempt_id=att_claim.id, task_id=s_db_claim, now=T0)
    db_session.commit()
    db_session.refresh(att_claim)

    init_claim_snap = _snapshot_attempt(att_claim)

    with pytest.raises(AttemptNotClaimableError) as exc_claim:
        claim_attempt(
            db_session,
            attempt_id=att_claim.id,
            task_id=s_caller_claim,
            now=T0,
        )

    _assert_sanitized_and_unmutated(
        exc_claim.value,
        db_session=db_session,
        attempt=att_claim,
        initial_snapshot=init_claim_snap,
        db_sentinel=s_db_claim,
        caller_sentinel=s_caller_claim,
        expected_error_code="ATTEMPT_NOT_CLAIMABLE",
    )


def test_10b_claim_contents_attempt_for_worker_sanitization(
    db_session: Session, test_brief: SocialBrief
):
    """10b. claim_contents_attempt_for_worker running attempt'e yabancı task ile çağrıldığında ID sızdırmaz."""
    s_db_cont = "SECRET_DB_CLAIM_CONT_LLLL"
    s_caller_cont = "INTRUDER_CALLER_CLAIM_CONT_MMMM"

    att_cont, _ = create_or_get_attempt(
        db_session,
        brief_id=test_brief.id,
        stage="contents",
        idempotency_key="key-claim-cont-sanitization",
        now=T0,
    )
    claim_contents_attempt_for_worker(
        db_session, attempt_id=att_cont.id, task_id=s_db_cont, now=T0
    )
    db_session.commit()
    db_session.refresh(att_cont)

    init_cont_snap = _snapshot_attempt(att_cont)

    with pytest.raises(AttemptNotWritableError) as exc_cont_run:
        claim_contents_attempt_for_worker(
            db_session,
            attempt_id=att_cont.id,
            task_id=s_caller_cont,
            now=T0,
        )

    _assert_sanitized_and_unmutated(
        exc_cont_run.value,
        db_session=db_session,
        attempt=att_cont,
        initial_snapshot=init_cont_snap,
        db_sentinel=s_db_cont,
        caller_sentinel=s_caller_cont,
        expected_error_code="TASK_MISMATCH",
    )


def test_10c_claim_ideas_attempt_for_worker_sanitization(
    db_session: Session, test_brief: SocialBrief
):
    """10c. claim_ideas_attempt_for_worker running attempt'e yabancı task ile çağrıldığında ID sızdırmaz."""
    s_db_ideas = "SECRET_DB_CLAIM_IDEAS_NNNN"
    s_caller_ideas = "INTRUDER_CALLER_CLAIM_IDEAS_OOOO"

    att_ideas, _ = create_or_get_attempt(
        db_session,
        brief_id=test_brief.id,
        stage="ideas",
        idempotency_key="key-claim-ideas-sanitization",
        now=T0,
    )
    claim_ideas_attempt_for_worker(
        db_session, attempt_id=att_ideas.id, task_id=s_db_ideas, now=T0
    )
    db_session.commit()
    db_session.refresh(att_ideas)

    init_ideas_snap = _snapshot_attempt(att_ideas)

    with pytest.raises(AttemptNotWritableError) as exc_ideas_run:
        claim_ideas_attempt_for_worker(
            db_session,
            attempt_id=att_ideas.id,
            task_id=s_caller_ideas,
            now=T0,
        )

    _assert_sanitized_and_unmutated(
        exc_ideas_run.value,
        db_session=db_session,
        attempt=att_ideas,
        initial_snapshot=init_ideas_snap,
        db_sentinel=s_db_ideas,
        caller_sentinel=s_caller_ideas,
        expected_error_code="TASK_MISMATCH",
    )


def test_10d_claim_ideas_retry_attempt_for_worker_sanitization(
    db_session: Session, test_brief: SocialBrief
):
    """10d. claim_ideas_retry_attempt_for_worker running attempt'e yabancı task ile çağrıldığında ID sızdırmaz."""
    s_db_retry = "SECRET_DB_CLAIM_RETRY_PPPP"
    s_caller_retry = "INTRUDER_CALLER_CLAIM_RETRY_QQQQ"

    att_retry, _ = create_or_get_attempt(
        db_session,
        brief_id=test_brief.id,
        stage="ideas_retry",
        idempotency_key="key-claim-retry-sanitization",
        now=T0,
    )
    claim_ideas_retry_attempt_for_worker(
        db_session, attempt_id=att_retry.id, task_id=s_db_retry, now=T0
    )
    db_session.commit()
    db_session.refresh(att_retry)

    init_retry_snap = _snapshot_attempt(att_retry)

    with pytest.raises(AttemptNotWritableError) as exc_retry_run:
        claim_ideas_retry_attempt_for_worker(
            db_session,
            attempt_id=att_retry.id,
            task_id=s_caller_retry,
            now=T0,
        )

    _assert_sanitized_and_unmutated(
        exc_retry_run.value,
        db_session=db_session,
        attempt=att_retry,
        initial_snapshot=init_retry_snap,
        db_sentinel=s_db_retry,
        caller_sentinel=s_caller_retry,
        expected_error_code="TASK_MISMATCH",
    )
