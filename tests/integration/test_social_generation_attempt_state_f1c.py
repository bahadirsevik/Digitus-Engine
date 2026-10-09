"""Integration tests for SocialGenerationAttempt lifecycle, lease, heartbeat and reconciliation (Phases F1-C & F1-C.1).

Covers all F1-C and F1-C.1 requirements:
1. İlk create yeni pending attempt oluşturur.
2. Aynı idempotency key aynı attempt’i döndürür.
3. Aynı brief/stage için farklı key aktifken conflict oluşur.
4. Aynı brief’te farklı stage aktif olabilir.
5. Farklı brief’lerde aynı stage aktif olabilir.
6. Bilinmeyen stage reddedilir.
7. ID listelerinde duplicate temizlenir ve sıra deterministiktir.
8. Bool/geçersiz/negatif ID reddedilir.
9. Pending attempt doğru task tarafından running olarak claim edilir.
10. Aynı task tekrar claim ederse idempotenttir.
11. Farklı task running attempt’i claim edemez.
12. Terminal attempt claim edilemez.
13. Heartbeat lease’i ileri taşır.
14. Başka task heartbeat gönderemez.
15. Dolmuş lease heartbeat alamaz ve worker_lost olur.
16. Reconciler dolmuş pending attempt’i failed/worker_lost yapar.
17. Reconciler dolmuş running attempt’i failed/worker_lost yapar.
18. Reconciler henüz dolmamış attempt’e dokunmaz.
19. Reconciler terminal attempt’e dokunmaz.
20. Reconciler tekrar çağrıldığında idempotenttir.
21. lock_attempt_for_write sağlıklı attempt’i geçirir.
22. Stale brief geç worker yazımını engeller ve brief_stale ile kapatır.
23. Assignment version uyuşmazlığı yazımı engeller ve assignment_changed ile kapatır.
24. Lease expiry yazımı engeller ve worker_lost ile kapatır.
25. Yanlış task sahipliği yazımı engeller.
26. completed/partial/failed finish davranışları.
27. Aynı terminal finish idempotenttir.
28. Terminal durum başka terminal duruma çevrilemez.
29. Helper işlemlerinden sonra rollback yapıldığında değişiklikler geri alınır.
30. Mevcut partial unique index davranışı korunur.
31. [F1-C.1] Aynı key'e ait expired attempt create_or_get çağrısında aynı satır olarak fakat failed/worker_lost döner.
32. [F1-C.1] Pending async attempt'ler (ideas, ideas_retry, contents) doğrudan finish edilemez.
33. [F1-C.1] Pending categories attempt task_id ile finish edilemez; task_id=None ile senkron tamamlanabilir.
34. [F1-C.1] Expired running attempt finish edilemez ve worker_lost olur (failed çağrısı bile worker_lost'u ezemez).
35. [F1-C.1] Stale brief ve version mismatch altındaki running attempt completed/partial yapılamaz.
36. [F1-C.1] Terminal attempt'e farklı task_id ile veya task_id'siz idempotent çağrı reddedilir.
37. [F1-C.1] Naive datetime enjekte edilen 'now' parametresi ValueError ile reddedilir.
38. [F1-C.2] İki ayrı session'ın kilit bağlamını seri biçimde temizce devralabildiğini doğrula (reusability).
39. [F1-C.2] Kanal atama sürümü değişmiş brief için yeni attempt oluşturulamaz (ASSIGNMENT_CHANGED).
40. [F1-C.2] FOR UPDATE sorgularının ScoringRun -> SocialBrief -> SocialGenerationAttempt sırasında çıktığını deterministik doğrula.
41. [F1-C.2] Gerçek concurrency: Reassignment (Run->Brief) ile Worker Write Guard (Run->Brief->Attempt) arasında deadlock oluşmadığını doğrula.
42. [F1-C.2] Kilitsiz ön okuma ile kilit alma arasında ilişki/satır değişirse fail-closed sonuç alınır.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import threading
import time

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import IntegrityError

from app.database.connection import SessionLocal
from app.database.models import ScoringRun, SocialBrief, SocialGenerationAttempt
from app.generators.social.attempt_state import (
    SOCIAL_ATTEMPT_LEASE_SECONDS,
    AttemptConflictError,
    AttemptNotClaimableError,
    AttemptNotFoundError,
    AttemptNotWritableError,
    BriefNotFoundError,
    InvalidStageError,
    claim_attempt,
    create_or_get_attempt,
    finish_attempt,
    heartbeat_attempt,
    lock_attempt_for_write,
    normalize_id_list,
    reconcile_expired_attempts,
)

T0 = datetime(2026, 9, 23, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def test_brief(db_session, make_workspace, make_scoring_run):
    ws = make_workspace("Attempt Test WS")
    run = make_scoring_run(
        brand_profile_id=ws.id,
        status="channel_assigned",
        channel_assignment_version=1,
    )
    brief = SocialBrief(
        scoring_run_id=run.id,
        brand_name_snapshot="Test Brand",
        brand_context_snapshot="Test Context",
        channel_assignment_version=1,
        format_matrix_version="v1",
        is_stale=False,
    )
    db_session.add(brief)
    db_session.commit()
    db_session.refresh(brief)
    return brief


class TestSocialGenerationAttemptStateF1C:
    def test_01_create_new_pending_attempt(self, db_session, test_brief):
        """1. İlk create yeni pending attempt oluşturur."""
        att, created = create_or_get_attempt(
            db_session,
            brief_id=test_brief.id,
            stage="categories",
            idempotency_key="key-cat-1",
            now=T0,
        )
        assert created is True
        assert att.id is not None
        assert att.status == "pending"
        assert att.stage == "categories"
        assert att.idempotency_key == "key-cat-1"
        assert att.created_at == T0
        assert att.heartbeat_at == T0
        assert att.lease_expires_at == T0 + timedelta(seconds=SOCIAL_ATTEMPT_LEASE_SECONDS)

    def test_02_idempotency_same_key_returns_existing(self, db_session, test_brief):
        """2. Aynı idempotency key aynı attempt’i döndürür."""
        att1, created1 = create_or_get_attempt(
            db_session,
            brief_id=test_brief.id,
            stage="categories",
            idempotency_key="same-key",
            now=T0,
        )
        assert created1 is True

        att2, created2 = create_or_get_attempt(
            db_session,
            brief_id=test_brief.id,
            stage="categories",
            idempotency_key="same-key",
            now=T0 + timedelta(minutes=5),
        )
        assert created2 is False
        assert att1.id == att2.id

    def test_03_active_attempt_conflict_for_same_brief_stage(
        self, db_session, test_brief
    ):
        """3. Aynı brief/stage için farklı key aktifken conflict oluşur."""
        create_or_get_attempt(
            db_session,
            brief_id=test_brief.id,
            stage="categories",
            idempotency_key="key-alpha",
            now=T0,
        )

        with pytest.raises(AttemptConflictError) as exc_info:
            create_or_get_attempt(
                db_session,
                brief_id=test_brief.id,
                stage="categories",
                idempotency_key="key-beta",
                now=T0,
            )
        assert exc_info.value.error_code == "ATTEMPT_CONFLICT"

    def test_04_different_stages_active_for_same_brief(
        self, db_session, test_brief
    ):
        """4. Aynı brief’te farklı stage aktif olabilir."""
        att_cat, created_cat = create_or_get_attempt(
            db_session,
            brief_id=test_brief.id,
            stage="categories",
            idempotency_key="cat-key",
            now=T0,
        )
        att_ideas, created_ideas = create_or_get_attempt(
            db_session,
            brief_id=test_brief.id,
            stage="ideas",
            idempotency_key="idea-key",
            now=T0,
        )
        assert created_cat is True
        assert created_ideas is True
        assert att_cat.id != att_ideas.id

    def test_05_different_briefs_same_stage_active(
        self, db_session, test_brief, make_scoring_run
    ):
        """5. Farklı brief’lerde aynı stage aktif olabilir."""
        brief2 = SocialBrief(
            scoring_run_id=test_brief.scoring_run_id,
            brand_name_snapshot="Brand 2",
            channel_assignment_version=1,
            format_matrix_version="v1",
            is_stale=False,
        )
        db_session.add(brief2)
        db_session.commit()

        att1, c1 = create_or_get_attempt(
            db_session,
            brief_id=test_brief.id,
            stage="categories",
            idempotency_key="key-1",
            now=T0,
        )
        att2, c2 = create_or_get_attempt(
            db_session,
            brief_id=brief2.id,
            stage="categories",
            idempotency_key="key-2",
            now=T0,
        )
        assert c1 is True
        assert c2 is True
        assert att1.id != att2.id

    def test_06_unknown_stage_rejected(self, db_session, test_brief):
        """6. Bilinmeyen stage reddedilir."""
        with pytest.raises(InvalidStageError) as exc_info:
            create_or_get_attempt(
                db_session,
                brief_id=test_brief.id,
                stage="unknown_stage",
                idempotency_key="key-err",
                now=T0,
            )
        assert exc_info.value.error_code == "INVALID_STAGE"

    def test_07_id_list_normalization_dedup_and_sort(self, db_session, test_brief):
        """7. ID listelerinde duplicate temizlenir ve sıra deterministiktir."""
        att, _ = create_or_get_attempt(
            db_session,
            brief_id=test_brief.id,
            stage="ideas",
            idempotency_key="key-ids",
            requested_target_ids=[4, 2, 4, 1],
            requested_idea_ids=[10, 5, 10],
            now=T0,
        )
        assert att.requested_target_ids == [1, 2, 4]
        assert att.requested_idea_ids == [5, 10]

        # Boş liste ve None davranışı
        assert normalize_id_list(None) is None
        assert normalize_id_list([]) == []

    def test_08_invalid_ids_rejected(self):
        """8. Bool/geçersiz/negatif ID reddedilir."""
        with pytest.raises(ValueError):
            normalize_id_list([1, True])

        with pytest.raises(ValueError):
            normalize_id_list([1, False])

        with pytest.raises(ValueError):
            normalize_id_list([-5])

        with pytest.raises(ValueError):
            normalize_id_list([0])

        with pytest.raises(ValueError):
            normalize_id_list(["string_id"])

        with pytest.raises(ValueError):
            normalize_id_list("not a list")

    def test_09_pending_attempt_claimed_by_task(self, db_session, test_brief):
        """9. Pending attempt doğru task tarafından running olarak claim edilir."""
        att, _ = create_or_get_attempt(
            db_session,
            brief_id=test_brief.id,
            stage="categories",
            idempotency_key="key-claim-1",
            now=T0,
        )
        t_claim = T0 + timedelta(seconds=10)
        claimed = claim_attempt(
            db_session,
            attempt_id=att.id,
            task_id="task-celery-1",
            now=t_claim,
        )
        assert claimed.status == "running"
        assert claimed.task_id == "task-celery-1"
        assert claimed.started_at == t_claim
        assert claimed.heartbeat_at == t_claim
        assert claimed.lease_expires_at == t_claim + timedelta(seconds=SOCIAL_ATTEMPT_LEASE_SECONDS)

    def test_10_claim_running_attempt_same_task_idempotent(
        self, db_session, test_brief
    ):
        """10. Aynı task tekrar claim ederse idempotenttir."""
        att, _ = create_or_get_attempt(
            db_session,
            brief_id=test_brief.id,
            stage="categories",
            idempotency_key="key-claim-2",
            now=T0,
        )
        claim_attempt(db_session, attempt_id=att.id, task_id="task-1", now=T0)
        # İkinci çağrı
        claimed2 = claim_attempt(db_session, attempt_id=att.id, task_id="task-1", now=T0 + timedelta(seconds=5))
        assert claimed2.status == "running"
        assert claimed2.task_id == "task-1"

    def test_11_claim_running_attempt_different_task_rejected(
        self, db_session, test_brief
    ):
        """11. Farklı task running attempt’i claim edemez."""
        att, _ = create_or_get_attempt(
            db_session,
            brief_id=test_brief.id,
            stage="categories",
            idempotency_key="key-claim-3",
            now=T0,
        )
        claim_attempt(db_session, attempt_id=att.id, task_id="task-1", now=T0)

        with pytest.raises(AttemptNotClaimableError):
            claim_attempt(db_session, attempt_id=att.id, task_id="task-2", now=T0 + timedelta(seconds=5))

    def test_12_terminal_attempt_cannot_be_claimed(self, db_session, test_brief):
        """12. Terminal attempt claim edilemez."""
        att, _ = create_or_get_attempt(
            db_session,
            brief_id=test_brief.id,
            stage="categories",
            idempotency_key="key-claim-4",
            now=T0,
        )
        claim_attempt(db_session, attempt_id=att.id, task_id="task-1", now=T0)
        finish_attempt(db_session, attempt_id=att.id, task_id="task-1", status="completed", now=T0 + timedelta(minutes=1))

        with pytest.raises(AttemptNotClaimableError):
            claim_attempt(db_session, attempt_id=att.id, task_id="task-1", now=T0 + timedelta(minutes=2))

    def test_13_heartbeat_extends_lease(self, db_session, test_brief):
        """13. Heartbeat lease’i ileri taşır."""
        att, _ = create_or_get_attempt(
            db_session,
            brief_id=test_brief.id,
            stage="ideas",
            idempotency_key="key-hb-1",
            now=T0,
        )
        claim_attempt(db_session, attempt_id=att.id, task_id="task-hb", now=T0)

        t_hb = T0 + timedelta(seconds=600)
        hb_att = heartbeat_attempt(
            db_session,
            attempt_id=att.id,
            task_id="task-hb",
            now=t_hb,
        )
        assert hb_att.heartbeat_at == t_hb
        assert hb_att.lease_expires_at == t_hb + timedelta(seconds=SOCIAL_ATTEMPT_LEASE_SECONDS)

    def test_14_heartbeat_wrong_task_rejected(self, db_session, test_brief):
        """14. Başka task heartbeat gönderemez."""
        att, _ = create_or_get_attempt(
            db_session,
            brief_id=test_brief.id,
            stage="ideas",
            idempotency_key="key-hb-2",
            now=T0,
        )
        claim_attempt(db_session, attempt_id=att.id, task_id="task-hb-owner", now=T0)

        with pytest.raises(AttemptNotClaimableError):
            heartbeat_attempt(
                db_session,
                attempt_id=att.id,
                task_id="task-other",
                now=T0 + timedelta(seconds=100),
            )

    def test_15_expired_lease_heartbeat_rejected_and_worker_lost(
        self, db_session, test_brief
    ):
        """15. Dolmuş lease heartbeat alamaz ve worker_lost olur."""
        att, _ = create_or_get_attempt(
            db_session,
            brief_id=test_brief.id,
            stage="ideas",
            idempotency_key="key-hb-3",
            now=T0,
        )
        claim_attempt(db_session, attempt_id=att.id, task_id="task-hb-exp", now=T0)

        # Lease süresi T0 + 1500s. 1600s sonra heartbeat denenir:
        t_expired = T0 + timedelta(seconds=1600)
        with pytest.raises(AttemptNotClaimableError) as exc_info:
            heartbeat_attempt(
                db_session,
                attempt_id=att.id,
                task_id="task-hb-exp",
                now=t_expired,
            )
        assert exc_info.value.error_code == "LEASE_EXPIRED"
        assert att.status == "failed"
        assert att.reason_code == "worker_lost"

    def test_16_reconciler_closes_expired_pending(self, db_session, test_brief):
        """16. Reconciler dolmuş pending attempt’i failed/worker_lost yapar."""
        att, _ = create_or_get_attempt(
            db_session,
            brief_id=test_brief.id,
            stage="categories",
            idempotency_key="key-rec-pending",
            now=T0,
        )
        assert att.status == "pending"

        # T0 + 1600'de süresi dolmuş
        t_check = T0 + timedelta(seconds=1600)
        closed_count = reconcile_expired_attempts(db_session, brief_id=test_brief.id, now=t_check)
        assert closed_count == 1
        assert att.status == "failed"
        assert att.reason_code == "worker_lost"
        assert att.completed_at == t_check

    def test_17_reconciler_closes_expired_running(self, db_session, test_brief):
        """17. Reconciler dolmuş running attempt’i failed/worker_lost yapar."""
        att, _ = create_or_get_attempt(
            db_session,
            brief_id=test_brief.id,
            stage="contents",
            idempotency_key="key-rec-running",
            now=T0,
        )
        claim_attempt(db_session, attempt_id=att.id, task_id="task-c", now=T0)
        assert att.status == "running"

        t_check = T0 + timedelta(seconds=1600)
        closed_count = reconcile_expired_attempts(db_session, brief_id=test_brief.id, now=t_check)
        assert closed_count == 1
        assert att.status == "failed"
        assert att.reason_code == "worker_lost"

    def test_18_reconciler_leaves_fresh_attempt_untouched(
        self, db_session, test_brief
    ):
        """18. Reconciler henüz dolmamış attempt’e dokunmaz."""
        att, _ = create_or_get_attempt(
            db_session,
            brief_id=test_brief.id,
            stage="contents",
            idempotency_key="key-fresh",
            now=T0,
        )
        claim_attempt(db_session, attempt_id=att.id, task_id="task-fresh", now=T0)

        # 5 dakika sonra kontrol (lease 25dk)
        t_check = T0 + timedelta(minutes=5)
        closed_count = reconcile_expired_attempts(db_session, brief_id=test_brief.id, now=t_check)
        assert closed_count == 0
        assert att.status == "running"

    def test_19_reconciler_leaves_terminal_attempt_untouched(
        self, db_session, test_brief
    ):
        """19. Reconciler terminal attempt’e dokunmaz."""
        att, _ = create_or_get_attempt(
            db_session,
            brief_id=test_brief.id,
            stage="categories",
            idempotency_key="key-term",
            now=T0,
        )
        claim_attempt(db_session, attempt_id=att.id, task_id="task-term", now=T0)
        finish_attempt(db_session, attempt_id=att.id, task_id="task-term", status="completed", now=T0)

        t_check = T0 + timedelta(days=1)
        closed_count = reconcile_expired_attempts(db_session, brief_id=test_brief.id, now=t_check)
        assert closed_count == 0
        assert att.status == "completed"

    def test_20_reconciler_idempotent(self, db_session, test_brief):
        """20. Reconciler tekrar çağrıldığında idempotenttir."""
        att, _ = create_or_get_attempt(
            db_session,
            brief_id=test_brief.id,
            stage="categories",
            idempotency_key="key-idem-rec",
            now=T0,
        )
        t_check = T0 + timedelta(seconds=2000)
        c1 = reconcile_expired_attempts(db_session, brief_id=test_brief.id, now=t_check)
        assert c1 == 1
        c2 = reconcile_expired_attempts(db_session, brief_id=test_brief.id, now=t_check)
        assert c2 == 0
        assert att.status == "failed"

    def test_21_lock_attempt_for_write_healthy_attempt(
        self, db_session, test_brief
    ):
        """21. lock_attempt_for_write sağlıklı attempt’i geçirir."""
        att, _ = create_or_get_attempt(
            db_session,
            brief_id=test_brief.id,
            stage="ideas",
            idempotency_key="key-write-ok",
            now=T0,
        )
        claim_attempt(db_session, attempt_id=att.id, task_id="task-write", now=T0)

        locked = lock_attempt_for_write(
            db_session,
            attempt_id=att.id,
            task_id="task-write",
            now=T0 + timedelta(minutes=5),
        )
        assert locked.id == att.id
        assert locked.status == "running"

    def test_22_lock_attempt_for_write_stale_brief_fails(
        self, db_session, test_brief
    ):
        """22. Stale brief geç worker yazımını engeller ve brief_stale ile kapatır."""
        att, _ = create_or_get_attempt(
            db_session,
            brief_id=test_brief.id,
            stage="ideas",
            idempotency_key="key-write-stale",
            now=T0,
        )
        claim_attempt(db_session, attempt_id=att.id, task_id="task-write", now=T0)

        # Brief stale yapılıyor
        test_brief.is_stale = True
        db_session.flush()

        with pytest.raises(AttemptNotWritableError) as exc_info:
            lock_attempt_for_write(
                db_session,
                attempt_id=att.id,
                task_id="task-write",
                now=T0 + timedelta(minutes=5),
            )
        assert exc_info.value.error_code == "BRIEF_STALE"
        assert att.status == "failed"
        assert att.reason_code == "brief_stale"

    def test_23_lock_attempt_for_write_version_mismatch_fails(
        self, db_session, test_brief
    ):
        """23. Assignment version uyuşmazlığı yazımı engeller ve assignment_changed ile kapatır."""
        att, _ = create_or_get_attempt(
            db_session,
            brief_id=test_brief.id,
            stage="ideas",
            idempotency_key="key-write-ver",
            now=T0,
        )
        claim_attempt(db_session, attempt_id=att.id, task_id="task-write", now=T0)

        # ScoringRun channel_assignment_version artırılıyor (brief snapshot'ı geride kalıyor)
        run = db_session.get(ScoringRun, test_brief.scoring_run_id)
        run.channel_assignment_version = 2
        db_session.flush()

        with pytest.raises(AttemptNotWritableError) as exc_info:
            lock_attempt_for_write(
                db_session,
                attempt_id=att.id,
                task_id="task-write",
                now=T0 + timedelta(minutes=5),
            )
        assert exc_info.value.error_code == "ASSIGNMENT_CHANGED"
        assert att.status == "failed"
        assert att.reason_code == "assignment_changed"

    def test_24_lock_attempt_for_write_expired_lease_fails(
        self, db_session, test_brief
    ):
        """24. Lease expiry yazımı engeller ve worker_lost ile kapatır."""
        att, _ = create_or_get_attempt(
            db_session,
            brief_id=test_brief.id,
            stage="ideas",
            idempotency_key="key-write-exp",
            now=T0,
        )
        claim_attempt(db_session, attempt_id=att.id, task_id="task-write", now=T0)

        with pytest.raises(AttemptNotWritableError) as exc_info:
            lock_attempt_for_write(
                db_session,
                attempt_id=att.id,
                task_id="task-write",
                now=T0 + timedelta(seconds=1600),
            )
        assert exc_info.value.error_code == "WORKER_LOST"
        assert att.status == "failed"
        assert att.reason_code == "worker_lost"

    def test_25_lock_attempt_for_write_wrong_task_fails(
        self, db_session, test_brief
    ):
        """25. Yanlış task sahipliği yazımı engeller."""
        att, _ = create_or_get_attempt(
            db_session,
            brief_id=test_brief.id,
            stage="ideas",
            idempotency_key="key-write-task",
            now=T0,
        )
        claim_attempt(db_session, attempt_id=att.id, task_id="task-owner", now=T0)

        with pytest.raises(AttemptNotWritableError) as exc_info:
            lock_attempt_for_write(
                db_session,
                attempt_id=att.id,
                task_id="task-intruder",
                now=T0 + timedelta(minutes=2),
            )
        assert exc_info.value.error_code == "TASK_MISMATCH"

    def test_26_finish_attempt_statuses(self, db_session, test_brief):
        """26. completed/partial/failed finish davranışları."""
        # Completed
        att1, _ = create_or_get_attempt(
            db_session, brief_id=test_brief.id, stage="categories", idempotency_key="f-1", now=T0
        )
        claim_attempt(db_session, attempt_id=att1.id, task_id="t-1", now=T0)
        f1 = finish_attempt(
            db_session,
            attempt_id=att1.id,
            task_id="t-1",
            status="completed",
            coverage={"targets": 4},
            warnings=["test warning"],
            now=T0 + timedelta(minutes=1),
        )
        assert f1.status == "completed"
        assert f1.coverage == {"targets": 4}
        assert f1.warnings == ["test warning"]
        assert f1.lease_expires_at is None

        # Partial
        att2, _ = create_or_get_attempt(
            db_session, brief_id=test_brief.id, stage="ideas", idempotency_key="f-2", now=T0
        )
        claim_attempt(db_session, attempt_id=att2.id, task_id="t-2", now=T0)
        f2 = finish_attempt(
            db_session,
            attempt_id=att2.id,
            task_id="t-2",
            status="partial",
            reason_code="partial_coverage",
            now=T0 + timedelta(minutes=1),
        )
        assert f2.status == "partial"
        assert f2.reason_code == "partial_coverage"

        # Failed
        att3, _ = create_or_get_attempt(
            db_session, brief_id=test_brief.id, stage="contents", idempotency_key="f-3", now=T0
        )
        claim_attempt(db_session, attempt_id=att3.id, task_id="t-3", now=T0)
        f3 = finish_attempt(
            db_session,
            attempt_id=att3.id,
            task_id="t-3",
            status="failed",
            error_message="Something failed",
            now=T0 + timedelta(minutes=1),
        )
        assert f3.status == "failed"
        assert f3.error_message == "Something failed"

    def test_27_finish_attempt_same_terminal_status_idempotent(
        self, db_session, test_brief
    ):
        """27. Aynı terminal finish idempotenttir."""
        att, _ = create_or_get_attempt(
            db_session, brief_id=test_brief.id, stage="categories", idempotency_key="f-idem", now=T0
        )
        claim_attempt(db_session, attempt_id=att.id, task_id="t-idem", now=T0)
        f1 = finish_attempt(db_session, attempt_id=att.id, task_id="t-idem", status="completed", now=T0)
        f2 = finish_attempt(db_session, attempt_id=att.id, task_id="t-idem", status="completed", now=T0)
        assert f1.id == f2.id
        assert f2.status == "completed"

    def test_28_finish_attempt_different_terminal_status_rejected(
        self, db_session, test_brief
    ):
        """28. Terminal durum başka terminal duruma çevrilemez."""
        att, _ = create_or_get_attempt(
            db_session, brief_id=test_brief.id, stage="categories", idempotency_key="f-diff", now=T0
        )
        claim_attempt(db_session, attempt_id=att.id, task_id="t-diff", now=T0)
        finish_attempt(db_session, attempt_id=att.id, task_id="t-diff", status="completed", now=T0)

        with pytest.raises(AttemptNotWritableError) as exc_info:
            finish_attempt(db_session, attempt_id=att.id, task_id="t-diff", status="failed", now=T0)
        assert exc_info.value.error_code == "ALREADY_TERMINAL"

    def test_29_no_implicit_commit_rollback_reverts(
        self, db_session, test_brief
    ):
        """29. Helper işlemlerinden sonra rollback yapıldığında değişiklikler geri alınır."""
        att, _ = create_or_get_attempt(
            db_session, brief_id=test_brief.id, stage="categories", idempotency_key="key-rollback", now=T0
        )
        attempt_id = att.id
        # Explicit rollback without commit
        db_session.rollback()

        db_session.expire_all()
        assert db_session.get(SocialGenerationAttempt, attempt_id) is None

    def test_30_db_partial_unique_index_enforced(self, db_session, test_brief):
        """30. Mevcut partial unique index davranışı doğrudan DB seviyesinde doğrulanır."""
        att1 = SocialGenerationAttempt(
            brief_id=test_brief.id,
            stage="categories",
            idempotency_key="direct-1",
            status="pending",
        )
        db_session.add(att1)
        db_session.flush()

        att2 = SocialGenerationAttempt(
            brief_id=test_brief.id,
            stage="categories",
            idempotency_key="direct-2",
            status="running",
        )
        db_session.add(att2)
        with pytest.raises(IntegrityError):
            db_session.flush()
        db_session.rollback()

    # ==================== F1-C.1 TESTS ====================

    def test_31_expired_same_key_reconciled_returns_same_row_as_failed_worker_lost(
        self, db_session, test_brief
    ):
        """31. [F1-C.1] Aynı idempotency key'e ait expired attempt create_or_get çağrısında
        aynı satır olarak fakat failed/worker_lost döner."""
        att1, c1 = create_or_get_attempt(
            db_session,
            brief_id=test_brief.id,
            stage="categories",
            idempotency_key="same-key-expired",
            now=T0,
        )
        assert c1 is True
        assert att1.status == "pending"
        att_id = att1.id

        # T0 + 1600s sonra aynı idempotency key ile çağrılır
        t_after = T0 + timedelta(seconds=1600)
        att2, c2 = create_or_get_attempt(
            db_session,
            brief_id=test_brief.id,
            stage="categories",
            idempotency_key="same-key-expired",
            now=t_after,
        )
        # Yeni satır açılmaz, aynı satır failed/worker_lost döner
        assert c2 is False
        assert att2.id == att_id
        assert att2.status == "failed"
        assert att2.reason_code == "worker_lost"

    @pytest.mark.parametrize("async_stage", ["ideas", "ideas_retry", "contents"])
    def test_32_pending_async_stages_cannot_be_finished_directly(
        self, db_session, test_brief, async_stage
    ):
        """32. [F1-C.1] Pending async attempt'ler (ideas, ideas_retry, contents) doğrudan finish edilemez."""
        att, _ = create_or_get_attempt(
            db_session,
            brief_id=test_brief.id,
            stage=async_stage,
            idempotency_key=f"pending-{async_stage}",
            now=T0,
        )
        assert att.status == "pending"

        with pytest.raises(AttemptNotWritableError) as exc_info:
            finish_attempt(
                db_session,
                attempt_id=att.id,
                status="completed",
                now=T0,
            )
        assert exc_info.value.error_code == "PENDING_ATTEMPT_NOT_CLAIMABLE"

    def test_33_pending_categories_with_task_id_rejected(
        self, db_session, test_brief
    ):
        """33. [F1-C.1] Pending categories attempt task_id verilerek finish edilemez."""
        att, _ = create_or_get_attempt(
            db_session,
            brief_id=test_brief.id,
            stage="categories",
            idempotency_key="cat-with-task",
            now=T0,
        )
        with pytest.raises(AttemptNotWritableError) as exc_info:
            finish_attempt(
                db_session,
                attempt_id=att.id,
                task_id="unexpected-task",
                status="completed",
                now=T0,
            )
        assert exc_info.value.error_code == "UNEXPECTED_TASK_ID_FOR_SYNC"

    def test_33b_pending_categories_without_task_id_can_finish_sync(
        self, db_session, test_brief
    ):
        """33b. [F1-C.1] Pending categories attempt task_id=None ile senkron tamamlanabilir."""
        att, _ = create_or_get_attempt(
            db_session,
            brief_id=test_brief.id,
            stage="categories",
            idempotency_key="cat-sync-ok",
            now=T0,
        )
        finished = finish_attempt(
            db_session,
            attempt_id=att.id,
            task_id=None,
            status="completed",
            now=T0,
        )
        assert finished.status == "completed"

    def test_34_finish_expired_running_attempt_fails_and_marks_worker_lost(
        self, db_session, test_brief
    ):
        """34. [F1-C.1] Expired running attempt finish edilemez ve worker_lost olur."""
        att, _ = create_or_get_attempt(
            db_session,
            brief_id=test_brief.id,
            stage="ideas",
            idempotency_key="finish-exp",
            now=T0,
        )
        claim_attempt(db_session, attempt_id=att.id, task_id="task-owner", now=T0)

        t_exp = T0 + timedelta(seconds=1600)
        # Completed denemesi
        with pytest.raises(AttemptNotWritableError) as exc_info:
            finish_attempt(
                db_session,
                attempt_id=att.id,
                task_id="task-owner",
                status="completed",
                now=t_exp,
            )
        assert exc_info.value.error_code == "WORKER_LOST"
        assert att.status == "failed"
        assert att.reason_code == "worker_lost"

        # Süresi dolmuş worker'ın kendi failed reason_code'u ile worker_lost'u ezemediği kontrolü
        att2, _ = create_or_get_attempt(
            db_session,
            brief_id=test_brief.id,
            stage="contents",
            idempotency_key="finish-exp-2",
            now=T0,
        )
        claim_attempt(db_session, attempt_id=att2.id, task_id="task-owner-2", now=T0)
        with pytest.raises(AttemptNotWritableError) as exc_info2:
            finish_attempt(
                db_session,
                attempt_id=att2.id,
                task_id="task-owner-2",
                status="failed",
                reason_code="custom_user_error",
                now=t_exp,
            )
        assert exc_info2.value.error_code == "WORKER_LOST"
        assert att2.status == "failed"
        assert att2.reason_code == "worker_lost"

    def test_35_finish_running_attempt_stale_brief_fails(
        self, db_session, test_brief
    ):
        """35. [F1-C.1] Stale brief altındaki running attempt completed/partial yapılamaz."""
        att, _ = create_or_get_attempt(
            db_session,
            brief_id=test_brief.id,
            stage="ideas",
            idempotency_key="finish-stale",
            now=T0,
        )
        claim_attempt(db_session, attempt_id=att.id, task_id="task-stale", now=T0)

        test_brief.is_stale = True
        db_session.flush()

        with pytest.raises(AttemptNotWritableError) as exc_info:
            finish_attempt(
                db_session,
                attempt_id=att.id,
                task_id="task-stale",
                status="completed",
                now=T0,
            )
        assert exc_info.value.error_code == "BRIEF_STALE"
        assert att.status == "failed"
        assert att.reason_code == "brief_stale"

    def test_35b_finish_running_attempt_version_mismatch_fails(
        self, db_session, test_brief
    ):
        """35b. [F1-C.1] Assignment version değişmiş running attempt completed/partial yapılamaz."""
        att, _ = create_or_get_attempt(
            db_session,
            brief_id=test_brief.id,
            stage="ideas",
            idempotency_key="finish-ver",
            now=T0,
        )
        claim_attempt(db_session, attempt_id=att.id, task_id="task-ver", now=T0)

        run = db_session.get(ScoringRun, test_brief.scoring_run_id)
        run.channel_assignment_version = 2
        db_session.flush()

        with pytest.raises(AttemptNotWritableError) as exc_info:
            finish_attempt(
                db_session,
                attempt_id=att.id,
                task_id="task-ver",
                status="completed",
                now=T0,
            )
        assert exc_info.value.error_code == "ASSIGNMENT_CHANGED"
        assert att.status == "failed"
        assert att.reason_code == "assignment_changed"

    def test_36_terminal_attempt_different_task_id_rejected(
        self, db_session, test_brief
    ):
        """36. [F1-C.1] Terminal attempt'e farklı task_id ile veya task_id'siz idempotent çağrı reddedilir."""
        att, _ = create_or_get_attempt(
            db_session,
            brief_id=test_brief.id,
            stage="ideas",
            idempotency_key="term-owner",
            now=T0,
        )
        claim_attempt(db_session, attempt_id=att.id, task_id="task-real-owner", now=T0)
        finish_attempt(db_session, attempt_id=att.id, task_id="task-real-owner", status="completed", now=T0)

        # Farklı task_id reddedilir
        with pytest.raises(AttemptNotWritableError) as exc_info:
            finish_attempt(db_session, attempt_id=att.id, task_id="task-intruder", status="completed", now=T0)
        assert exc_info.value.error_code == "TASK_MISMATCH"

        # task_id olmadan çağrı reddedilir
        with pytest.raises(AttemptNotWritableError) as exc_info2:
            finish_attempt(db_session, attempt_id=att.id, task_id=None, status="completed", now=T0)
        assert exc_info2.value.error_code == "TASK_MISMATCH"

        # Doğru task_id idempotent başarı alır
        same = finish_attempt(db_session, attempt_id=att.id, task_id="task-real-owner", status="completed", now=T0)
        assert same.id == att.id

    def test_37_naive_datetime_injected_now_rejected(self, db_session, test_brief):
        """37. [F1-C.1] Timezone bilgisi olmayan injected 'now' ValueError ile reddedilir."""
        naive_now = datetime(2026, 9, 23, 12, 0, 0)  # tzinfo=None

        with pytest.raises(ValueError, match="timezone-aware"):
            create_or_get_attempt(db_session, brief_id=test_brief.id, stage="categories", idempotency_key="k1", now=naive_now)

        with pytest.raises(ValueError, match="timezone-aware"):
            reconcile_expired_attempts(db_session, brief_id=test_brief.id, now=naive_now)

        att, _ = create_or_get_attempt(db_session, brief_id=test_brief.id, stage="categories", idempotency_key="k-naive", now=T0)

        with pytest.raises(ValueError, match="timezone-aware"):
            claim_attempt(db_session, attempt_id=att.id, task_id="t", now=naive_now)

        claim_attempt(db_session, attempt_id=att.id, task_id="t", now=T0)

        with pytest.raises(ValueError, match="timezone-aware"):
            heartbeat_attempt(db_session, attempt_id=att.id, task_id="t", now=naive_now)

        with pytest.raises(ValueError, match="timezone-aware"):
            lock_attempt_for_write(db_session, attempt_id=att.id, task_id="t", now=naive_now)

        with pytest.raises(ValueError, match="timezone-aware"):
            finish_attempt(db_session, attempt_id=att.id, task_id="t", status="completed", now=naive_now)

    def test_38_lock_context_reusable_across_sessions(self, db_session, test_brief):
        """38. [F1-C.2] İki ayrı session'ın kilit bağlamını seri biçimde temizce devralabildiğini doğrula (reusability)."""
        att, _ = create_or_get_attempt(
            db_session,
            brief_id=test_brief.id,
            stage="ideas",
            idempotency_key="concurrency-lock",
            now=T0,
        )
        claim_attempt(db_session, attempt_id=att.id, task_id="task-concurrent", now=T0)
        db_session.commit()

        # Session 1: lock_attempt_for_write çağırarak ScoringRun -> SocialBrief -> SocialGenerationAttempt kilitlerini alır
        locked_att = lock_attempt_for_write(
            db_session,
            attempt_id=att.id,
            task_id="task-concurrent",
            now=T0 + timedelta(minutes=1),
        )
        assert locked_att.id == att.id

        # Session 1 commit edilerek kilitler serbest bırakılır
        db_session.commit()

        # Session 2: İkinci bir DB session açılır ve aynı attempt'i sorunsuz kilitler
        session2 = SessionLocal()
        try:
            locked2 = lock_attempt_for_write(
                session2,
                attempt_id=att.id,
                task_id="task-concurrent",
                now=T0 + timedelta(minutes=2),
            )
            assert locked2.id == att.id
            session2.commit()
        finally:
            session2.close()

    def test_39_create_or_get_attempt_version_mismatch_fails(
        self, db_session, test_brief
    ):
        """39. [F1-C.2] Kanal atama sürümü değişmiş brief için yeni attempt oluşturulamaz (ASSIGNMENT_CHANGED)."""
        run = db_session.get(ScoringRun, test_brief.scoring_run_id)
        run.channel_assignment_version = 2
        db_session.commit()

        with pytest.raises(AttemptNotWritableError) as exc_info:
            create_or_get_attempt(
                db_session,
                brief_id=test_brief.id,
                stage="categories",
                idempotency_key="key-version-mismatch",
                now=T0,
            )
        assert exc_info.value.error_code == "ASSIGNMENT_CHANGED"

    def test_40_lock_order_emitted_sql_query_sequence(
        self, db_session, test_brief
    ):
        """40. [F1-C.2] FOR UPDATE sorgularının ScoringRun -> SocialBrief -> SocialGenerationAttempt sırasında çıktığını deterministik doğrula."""
        for_update_queries: list[str] = []

        def capture_sql(conn, cursor, statement, parameters, context, executemany):
            if "FOR UPDATE" in statement.upper():
                for_update_queries.append(statement.lower())

        bind = db_session.get_bind()
        event.listen(bind, "before_cursor_execute", capture_sql)
        try:
            # 1. create_or_get_attempt testi:
            # ScoringRun FOR UPDATE -> SocialBrief FOR UPDATE
            for_update_queries.clear()
            att, _ = create_or_get_attempt(
                db_session,
                brief_id=test_brief.id,
                stage="ideas",
                idempotency_key="key-sql-seq",
                now=T0,
            )
            claim_attempt(db_session, attempt_id=att.id, task_id="task-sql", now=T0)
            db_session.flush()

            run_idx = next(i for i, q in enumerate(for_update_queries) if "scoring_runs" in q)
            brief_idx = next(i for i, q in enumerate(for_update_queries) if "social_briefs" in q)
            assert run_idx < brief_idx, "create_or_get_attempt ScoringRun'ı SocialBrief'ten önce kilitlemeli"

            # 2. lock_attempt_for_write testi:
            # ScoringRun -> SocialBrief -> SocialGenerationAttempt
            for_update_queries.clear()
            lock_attempt_for_write(db_session, attempt_id=att.id, task_id="task-sql", now=T0)
            db_session.flush()

            run_idx = next(i for i, q in enumerate(for_update_queries) if "scoring_runs" in q)
            brief_idx = next(i for i, q in enumerate(for_update_queries) if "social_briefs" in q)
            att_idx = next(i for i, q in enumerate(for_update_queries) if "social_generation_attempts" in q)
            assert run_idx < brief_idx < att_idx, (
                f"lock_attempt_for_write kilit sırası hatalı: run={run_idx}, brief={brief_idx}, attempt={att_idx}"
            )

            # 3. finish_attempt testi:
            # ScoringRun -> SocialBrief -> SocialGenerationAttempt
            for_update_queries.clear()
            finish_attempt(
                db_session,
                attempt_id=att.id,
                task_id="task-sql",
                status="completed",
                now=T0,
            )
            db_session.flush()

            run_idx = next(i for i, q in enumerate(for_update_queries) if "scoring_runs" in q)
            brief_idx = next(i for i, q in enumerate(for_update_queries) if "social_briefs" in q)
            att_idx = next(i for i, q in enumerate(for_update_queries) if "social_generation_attempts" in q)
            assert run_idx < brief_idx < att_idx, (
                f"finish_attempt kilit sırası hatalı: run={run_idx}, brief={brief_idx}, attempt={att_idx}"
            )
        finally:
            event.remove(bind, "before_cursor_execute", capture_sql)

    def test_41_concurrency_no_deadlock_between_reassignment_and_write_guard(
        self, db_session, test_brief
    ):
        """41. [F1-C.2] Gerçek concurrency: Reassignment (Run->Brief) ile Worker Write Guard (Run->Brief->Attempt) arasında deadlock oluşmadığını doğrula."""
        att, _ = create_or_get_attempt(
            db_session,
            brief_id=test_brief.id,
            stage="ideas",
            idempotency_key="key-concurrency-real",
            now=T0,
        )
        claim_attempt(db_session, attempt_id=att.id, task_id="task-worker", now=T0)
        db_session.commit()

        run_id = test_brief.scoring_run_id
        brief_id = test_brief.id
        attempt_id = att.id

        event_a_locked_run = threading.Event()
        event_b_started_waiting = threading.Event()

        t_a_errors: list[Exception] = []
        t_b_errors: list[Exception] = []

        def transaction_a():
            """Transaction A: Reassignment / Staleness propagation simülasyonu.
            ScoringRun kilitle -> SocialBrief'i is_stale=True güncelle -> commit.
            """
            session_a = SessionLocal()
            try:
                session_a.execute(text("SET lock_timeout = '3000ms'"))
                # 1. ScoringRun kilidi al (begin_channel_assignment gibi)
                session_a.query(ScoringRun).filter(ScoringRun.id == run_id).with_for_update().one()
                event_a_locked_run.set()

                # B'nin devreye girip kilit beklemesini garanti et
                assert event_b_started_waiting.wait(timeout=5.0), "Transaction B zamanında başlamadı"
                time.sleep(0.15)  # B'nin ScoringRun satır kilidi kuyruğuna girdiğinden emin ol

                # 2. SocialBrief kilidi al ve stale yap (_mark_run_outputs_stale gibi)
                br = session_a.query(SocialBrief).filter(SocialBrief.id == brief_id).with_for_update().one()
                br.is_stale = True
                session_a.commit()
            except Exception as e:
                session_a.rollback()
                t_a_errors.append(e)
            finally:
                session_a.close()

        def transaction_b():
            """Transaction B: Worker write guard (lock_attempt_for_write).
            A ScoringRun'ı kilitledikten sonra başlar. Aynı sırada (Run -> Brief -> Attempt) kilit
            almaya çalıştığı için ScoringRun üzerinde bloklanır ama DEADLOCK OLUŞMAZ.
            A commit edince uyanır, güncel stale değerini görür ve BRIEF_STALE hatası fırlatır.
            """
            session_b = SessionLocal()
            try:
                session_b.execute(text("SET lock_timeout = '3000ms'"))
                assert event_a_locked_run.wait(timeout=5.0), "Transaction A ScoringRun'ı kilitleyemedi"
                event_b_started_waiting.set()

                lock_attempt_for_write(
                    session_b,
                    attempt_id=attempt_id,
                    task_id="task-worker",
                    now=T0 + timedelta(minutes=1),
                )
                session_b.commit()
            except Exception as e:
                session_b.rollback()
                t_b_errors.append(e)
            finally:
                session_b.close()

        t_a = threading.Thread(target=transaction_a, name="thread-reassign-A")
        t_b = threading.Thread(target=transaction_b, name="thread-worker-B")

        t_a.start()
        t_b.start()

        t_a.join(timeout=6.0)
        t_b.join(timeout=6.0)

        assert not t_a.is_alive(), "Thread A zaman aşımına uğradı (olası deadlock)"
        assert not t_b.is_alive(), "Thread B zaman aşımına uğradı (olası deadlock)"

        assert len(t_a_errors) == 0, f"Transaction A hata aldı: {t_a_errors}"
        assert len(t_b_errors) == 1, f"Transaction B beklenen hatayı üretmedi: {t_b_errors}"
        err = t_b_errors[0]
        assert isinstance(err, AttemptNotWritableError), f"Beklenmeyen hata tipi: {type(err)}: {err}"
        assert err.error_code == "BRIEF_STALE", f"Beklenmeyen error_code: {err.error_code}"

    def test_42_pre_read_relation_changed_fail_closed(
        self, db_session, test_brief, make_scoring_run, monkeypatch
    ):
        """42. [F1-C.2] Kilitsiz ön okuma ile kilit alma arasında ilişki/satır değişirse fail-closed sonuç alınır."""
        from app.generators.social import attempt_state

        att, _ = create_or_get_attempt(
            db_session,
            brief_id=test_brief.id,
            stage="ideas",
            idempotency_key="key-pre-read-race",
            now=T0,
        )
        claim_attempt(db_session, attempt_id=att.id, task_id="task-race", now=T0)
        db_session.flush()

        # a) run bulunamazsa -> RUN_NOT_FOUND
        monkeypatch.setattr(
            attempt_state,
            "_pre_read_context_for_attempt",
            lambda db, att_id: (test_brief.id, 9999999),
        )
        with pytest.raises(AttemptNotWritableError) as exc_info:
            lock_attempt_for_write(db_session, attempt_id=att.id, task_id="task-race", now=T0)
        assert exc_info.value.error_code == "RUN_NOT_FOUND"

        # b) brief-run ilişkisi kilit sonrasında uyuşmazsa -> RELATION_CHANGED
        current_run = db_session.query(ScoringRun).filter(ScoringRun.id == test_brief.scoring_run_id).one()
        other_run = make_scoring_run(
            brand_profile_id=current_run.brand_profile_id,
            status="channel_assigned",
            channel_assignment_version=1,
        )

        monkeypatch.setattr(
            attempt_state,
            "_pre_read_context_for_attempt",
            lambda db, att_id: (test_brief.id, other_run.id),
        )
        with pytest.raises(AttemptNotWritableError) as exc_info2:
            lock_attempt_for_write(db_session, attempt_id=att.id, task_id="task-race", now=T0)
        assert exc_info2.value.error_code == "RELATION_CHANGED"

        # c) create_or_get_attempt sırasında brief bulunamazsa -> BRIEF_NOT_FOUND
        monkeypatch.undo()
        with pytest.raises(BriefNotFoundError) as exc_info3:
            create_or_get_attempt(
                db_session,
                brief_id=9999999,
                stage="categories",
                idempotency_key="key-missing-brief",
                now=T0,
            )
        assert exc_info3.value.error_code == "BRIEF_NOT_FOUND"
