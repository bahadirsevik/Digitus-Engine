# -*- coding: utf-8 -*-
"""Kalıcı maliyet ledger'ı — GERÇEK Postgres + paralellik (plan §5.7/§10).

Codex şartı: rezervasyon ve settle `UPDATE ... WHERE state='reserved'` /
satır kilidi üzerinden CAS olmalı; DB CHECK'leri yalnız geçerli son satır
şeklini korur.
"""
import threading
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app.core.telemetry.ai_cost_budget import (
    BUDGET_DOWNSTREAM,
    OwnerNotSpendable,
    ReservationAlreadyClosed,
    ReservationInFlight,
    BUDGET_SCREENING,
    STATE_CEILING,
    STATE_RESERVED,
    STATE_SETTLED,
    AiCostLedger,
    BudgetError,
    BudgetExceeded,
    LedgerInvariantError,
    reconcile_stale_reservations,
    to_decimal,
)
from app.database.models import (
    AiCostReservation,
    ChannelAssignmentAttempt,
    ScoringRun,
)


@pytest.fixture
def attempt(db_session, make_workspace):
    ws = make_workspace()
    run = ScoringRun(run_name="ledger", brand_profile_id=ws.id,
                     total_keywords=10, ads_capacity=5, seo_capacity=5,
                     social_capacity=5, status="scored")
    db_session.add(run)
    db_session.commit()
    row = ChannelAssignmentAttempt(
        parent_task_id=f"ledger-{run.id}", scoring_run_id=run.id,
        brand_profile_id=ws.id, status="running", phase="screening",
        screening_mode="assistive", applied_candidate_multiplier=3,
        counterfactual_target_multiplier=3,
        # Codex 7. tur #1: cap OTORITESI DB'dir
        approved_screening_cap_usd=Decimal("0.50"),
        approved_downstream_cap_usd=Decimal("4.00"))
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row


@pytest.fixture
def session_factory(db_session):
    """Testte kısa ömürlü session'lar aynı engine'i paylaşır."""
    engine = db_session.get_bind()
    return sa.orm.sessionmaker(bind=engine, expire_on_commit=False)


def _ledger(session_factory, attempt, db_session=None, **caps):
    """Cap'ler DB'de yasar; test istegi varsa attempt satiri guncellenir."""
    if caps and db_session is not None:
        if "screening_cap_usd" in caps:
            attempt.approved_screening_cap_usd = to_decimal(
                caps["screening_cap_usd"], "s")
        if "downstream_cap_usd" in caps:
            attempt.approved_downstream_cap_usd = to_decimal(
                caps["downstream_cap_usd"], "d")
        db_session.commit()
    return AiCostLedger(
        session_factory, attempt_id=attempt.id,
        expected_screening_cap_usd=attempt.approved_screening_cap_usd,
        expected_downstream_cap_usd=attempt.approved_downstream_cap_usd)


class TestDecimalDiscipline:
    def test_float_never_used_directly(self):
        # 0.1 + 0.2 float toplamı 0.30000000000000004; repr üzerinden
        # Decimal'e çevrilir ve kesin kalır
        assert to_decimal(0.1, "x") + to_decimal(0.2, "x") == Decimal(
            "0.300000")

    def test_invalid_values_fail_closed(self):
        for bad in (None, True, "abc", float("nan"), float("inf")):
            with pytest.raises(BudgetError):
                to_decimal(bad, "x")

    def test_reservation_amounts_round_trip_as_decimal(
            self, db_session, session_factory, attempt):
        led = _ledger(session_factory, attempt)
        rid = led.reserve(kind=BUDGET_SCREENING, request_id="r1",
                          ceiling_usd="0.012345")
        row = db_session.get(AiCostReservation, rid)
        db_session.refresh(row)
        assert isinstance(row.ceiling_usd, Decimal)
        assert row.ceiling_usd == Decimal("0.012345")


class TestReserveCap:
    def test_reserve_creates_row_before_provider_call(
            self, db_session, session_factory, attempt):
        led = _ledger(session_factory, attempt)
        rid = led.reserve(kind=BUDGET_SCREENING, request_id="r1",
                          ceiling_usd="0.01", stage="screening",
                          provider="deepseek", model="deepseek-v4-flash")
        row = db_session.get(AiCostReservation, rid)
        db_session.refresh(row)
        assert row.state == STATE_RESERVED
        assert row.actual_usd is None and row.settled_at is None

    def test_kind_cap_blocks_before_call(self, db_session, session_factory,
                                        attempt):
        led = _ledger(session_factory, attempt, db_session,
                      screening_cap_usd=Decimal("0.02"))
        led.reserve(kind=BUDGET_SCREENING, request_id="a",
                    ceiling_usd="0.015")
        with pytest.raises(BudgetExceeded, match="HARD-CAP \\[screening\\]"):
            led.reserve(kind=BUDGET_SCREENING, request_id="b",
                        ceiling_usd="0.010")

    def test_combined_cap_is_sum_of_db_caps(self, db_session,
                                            session_factory, attempt):
        """Birlesik cap SOZLESME GEREGI iki onayli cap'in toplamidir."""
        led = _ledger(session_factory, attempt, db_session,
                      screening_cap_usd=Decimal("0.10"),
                      downstream_cap_usd=Decimal("0.10"))
        caps = led.caps()
        assert caps["combined"] == Decimal("0.200000")
        led.reserve(kind=BUDGET_SCREENING, request_id="s1",
                    ceiling_usd="0.10")
        led.reserve(kind=BUDGET_DOWNSTREAM, request_id="d1",
                    ceiling_usd="0.10")
        with pytest.raises(BudgetExceeded):
            led.reserve(kind=BUDGET_DOWNSTREAM, request_id="d2",
                        ceiling_usd="0.01")

    def test_settle_frees_headroom(self, db_session, session_factory,
                                   attempt):
        led = _ledger(session_factory, attempt, db_session,
                      screening_cap_usd=Decimal("0.03"))
        rid = led.reserve(kind=BUDGET_SCREENING, request_id="a",
                          ceiling_usd="0.02")
        led.settle(rid, "0.002")
        # 0.002 harcandı → 0.02'lik ikinci rezervasyon artık sığar
        led.reserve(kind=BUDGET_SCREENING, request_id="b",
                    ceiling_usd="0.02")

    def test_unknown_owner_fails_closed(self, session_factory):
        led = AiCostLedger(session_factory, attempt_id=999999,
                           expected_screening_cap_usd="0.5",
                           expected_downstream_cap_usd="4")
        with pytest.raises(BudgetError, match="attempt 999999 yok"):
            led.reserve(kind=BUDGET_SCREENING, request_id="x",
                        ceiling_usd="0.01")

    def test_invalid_kind_and_request_attempt(self, session_factory,
                                              attempt):
        led = _ledger(session_factory, attempt)
        with pytest.raises(BudgetError, match="bilinmeyen bütçe"):
            led.reserve(kind="hayalet", request_id="x", ceiling_usd="0.01")
        for bad in (0, -1, True, "1"):
            with pytest.raises(BudgetError, match="request_attempt"):
                led.reserve(kind=BUDGET_SCREENING, request_id="x",
                            ceiling_usd="0.01", request_attempt=bad)


class TestSettleCAS:
    def test_settle_is_cas_and_double_settle_rejected(
            self, db_session, session_factory, attempt):
        led = _ledger(session_factory, attempt)
        rid = led.reserve(kind=BUDGET_DOWNSTREAM, request_id="d1",
                          ceiling_usd="0.05")
        led.settle(rid, "0.02")
        with pytest.raises(LedgerInvariantError, match="CAS kaybı"):
            led.settle(rid, "0.01")
        row = db_session.get(AiCostReservation, rid)
        db_session.refresh(row)
        assert row.state == STATE_SETTLED
        assert row.actual_usd == Decimal("0.020000")
        assert row.settled_at is not None

    def test_ceiling_charge_burns_full_ceiling(self, db_session,
                                               session_factory, attempt):
        led = _ledger(session_factory, attempt)
        rid = led.reserve(kind=BUDGET_SCREENING, request_id="s1",
                          ceiling_usd="0.030")
        burned = led.charge_ceiling(rid)
        row = db_session.get(AiCostReservation, rid)
        db_session.refresh(row)
        assert burned == Decimal("0.030000")
        assert row.state == STATE_CEILING
        assert row.actual_usd == row.ceiling_usd

    def test_actual_above_ceiling_is_invariant_violation(
            self, db_session, session_factory, attempt):
        """Sağlayıcı sınırı delinirse: tavan yanar VE koşu durur."""
        led = _ledger(session_factory, attempt)
        rid = led.reserve(kind=BUDGET_DOWNSTREAM, request_id="d2",
                          ceiling_usd="0.010")
        with pytest.raises(LedgerInvariantError, match="İNVARYANT"):
            led.settle(rid, "0.020")
        row = db_session.get(AiCostReservation, rid)
        db_session.refresh(row)
        assert row.state == STATE_CEILING          # harcama gizlenmedi
        assert row.actual_usd == Decimal("0.010000")

    def test_settle_unknown_reservation(self, session_factory, attempt):
        led = _ledger(session_factory, attempt)
        with pytest.raises(BudgetError, match="yok"):
            led.settle(999999, "0.01")


class TestConcurrency:
    def test_parallel_reserves_respect_cap(self, db_session,
                                           session_factory, attempt):
        """10 thread aynı anda rezerve etmeye çalışır; cap DELİNMEZ."""
        led = _ledger(session_factory, attempt, db_session,
                      screening_cap_usd=Decimal("0.05"))
        granted, refused = [], []
        barrier = threading.Barrier(10)

        def worker(i):
            barrier.wait()
            try:
                granted.append(led.reserve(kind=BUDGET_SCREENING,
                                           request_id=f"p{i}",
                                           ceiling_usd="0.01"))
            except BudgetExceeded:
                refused.append(i)

        threads = [threading.Thread(target=worker, args=(i,))
                   for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(granted) == 5        # 5 x 0.01 = cap
        assert len(refused) == 5
        total = db_session.execute(sa.select(
            sa.func.sum(AiCostReservation.ceiling_usd)).where(
            AiCostReservation.budget_owner_attempt_id == attempt.id)
        ).scalar_one()
        assert to_decimal(total, "t") == Decimal("0.050000")

    def test_parallel_settle_single_winner(self, session_factory, attempt):
        led = _ledger(session_factory, attempt)
        rid = led.reserve(kind=BUDGET_DOWNSTREAM, request_id="race",
                          ceiling_usd="0.05")
        wins, losses = [], []
        barrier = threading.Barrier(6)

        def worker():
            barrier.wait()
            try:
                led.settle(rid, "0.01")
                wins.append(1)
            except LedgerInvariantError:
                losses.append(1)

        threads = [threading.Thread(target=worker) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(wins) == 1 and len(losses) == 5


class TestStaleReconciliation:
    def test_stale_open_reservation_is_ceiling_charged(
            self, db_session, session_factory, attempt):
        led = _ledger(session_factory, attempt)
        rid = led.reserve(kind=BUDGET_SCREENING, request_id="dead",
                          ceiling_usd="0.02")
        db_session.execute(sa.text(
            "UPDATE ai_cost_reservations SET created_at = now() - "
            "interval '2 hours' WHERE id = :rid"), {"rid": rid})
        attempt.status = "failed"          # terminal sahip (Codex #5)
        db_session.commit()
        changed = reconcile_stale_reservations(db_session,
                                               attempt_id=attempt.id)
        assert changed == 1
        row = db_session.get(AiCostReservation, rid)
        db_session.refresh(row)
        assert row.state == STATE_CEILING
        assert row.actual_usd == row.ceiling_usd

    def test_fresh_reservation_untouched(self, db_session, session_factory,
                                         attempt):
        led = _ledger(session_factory, attempt)
        rid = led.reserve(kind=BUDGET_SCREENING, request_id="fresh",
                          ceiling_usd="0.02")
        assert reconcile_stale_reservations(db_session,
                                            attempt_id=attempt.id) == 0
        row = db_session.get(AiCostReservation, rid)
        assert row.state == STATE_RESERVED

    def test_budget_survives_worker_restart(self, db_session,
                                            session_factory, attempt):
        """Yeni ledger nesnesi (retry/restart) AYNI ledger'dan devam eder."""
        first = _ledger(session_factory, attempt, db_session,
                        screening_cap_usd=Decimal("0.03"))
        rid = first.reserve(kind=BUDGET_SCREENING, request_id="k1",
                            ceiling_usd="0.02")
        first.settle(rid, "0.02")
        second = _ledger(session_factory, attempt)
        with pytest.raises(BudgetExceeded):
            second.reserve(kind=BUDGET_SCREENING, request_id="k2",
                           ceiling_usd="0.02")


class TestSnapshot:
    def test_snapshot_reports_kinds_and_remaining(self, db_session,
                                                  session_factory, attempt):
        led = _ledger(session_factory, attempt)
        r1 = led.reserve(kind=BUDGET_SCREENING, request_id="s",
                         ceiling_usd="0.02")
        led.settle(r1, "0.005")
        r2 = led.reserve(kind=BUDGET_DOWNSTREAM, request_id="d",
                         ceiling_usd="0.10")
        led.charge_ceiling(r2)
        led.reserve(kind=BUDGET_DOWNSTREAM, request_id="d2",
                    ceiling_usd="0.03")
        snap = led.snapshot()
        assert snap["by_kind"][BUDGET_SCREENING]["settled_usd"] == "0.005000"
        assert snap["by_kind"][BUDGET_DOWNSTREAM]["settled_usd"] == "0.100000"
        assert (snap["by_kind"][BUDGET_DOWNSTREAM]["open_reserved_usd"]
                == "0.030000")
        assert snap["ceiling_charges"] == 1
        assert snap["committed_total_usd"] == "0.135000"
        assert snap["remaining_combined_usd"] == "4.365000"


class TestCapAuthorityRound7:
    """Codex 7. tur #1: onaylı cap OTORİTESİ DB'dir."""

    def test_caps_come_from_db_not_constructor(self, db_session,
                                               session_factory, attempt):
        attempt.approved_screening_cap_usd = Decimal("0.02")
        db_session.commit()
        led = AiCostLedger(session_factory, attempt_id=attempt.id,
                           expected_screening_cap_usd=Decimal("0.02"),
                           expected_downstream_cap_usd=Decimal("4.00"))
        assert led.caps()[BUDGET_SCREENING] == Decimal("0.020000")
        with pytest.raises(BudgetExceeded):
            led.reserve(kind=BUDGET_SCREENING, request_id="x",
                        ceiling_usd="0.03")

    def test_missing_db_cap_fails_closed(self, db_session, session_factory,
                                         attempt):
        attempt.approved_screening_cap_usd = None
        db_session.commit()
        led = AiCostLedger(session_factory, attempt_id=attempt.id,
                           expected_screening_cap_usd=Decimal("0.50"),
                           expected_downstream_cap_usd=Decimal("4.00"))
        with pytest.raises(BudgetError, match="onaylı"):
            led.reserve(kind=BUDGET_SCREENING, request_id="x",
                        ceiling_usd="0.001")

    def test_expected_cap_mismatch_fails_closed(self, db_session,
                                                session_factory, attempt):
        """Preflight'ta onaylanan cap DB'de değiştiyse harcama BAŞLAMAZ."""
        led = AiCostLedger(session_factory, attempt_id=attempt.id,
                           expected_screening_cap_usd=Decimal("0.50"),
                           expected_downstream_cap_usd=Decimal("4.00"))
        led.reserve(kind=BUDGET_SCREENING, request_id="ok",
                    ceiling_usd="0.001")  # drift ONCESI gecerli
        attempt.approved_screening_cap_usd = Decimal("9.00")
        db_session.commit()
        with pytest.raises(BudgetError, match="CAP BAĞLAMA REDDİ"):
            led.reserve(kind=BUDGET_SCREENING, request_id="drift",
                        ceiling_usd="0.001")


class TestCrossAttemptIsolationRound7:
    """Codex 7. tur #2: bir ledger BAŞKA attempt'in satırına dokunamaz."""

    @pytest.fixture
    def other_attempt(self, db_session, make_workspace):
        ws = make_workspace()
        run = ScoringRun(run_name="other", brand_profile_id=ws.id,
                         total_keywords=5, ads_capacity=5, seo_capacity=5,
                         social_capacity=5, status="scored")
        db_session.add(run)
        db_session.commit()
        row = ChannelAssignmentAttempt(
            parent_task_id=f"other-{run.id}", scoring_run_id=run.id,
            brand_profile_id=ws.id, status="running", phase="screening",
            screening_mode="off", applied_candidate_multiplier=1,
            counterfactual_target_multiplier=1,
            approved_screening_cap_usd=Decimal("0.50"),
            approved_downstream_cap_usd=Decimal("4.00"))
        db_session.add(row)
        db_session.commit()
        db_session.refresh(row)
        return row

    def test_cross_attempt_settle_rejected(self, db_session,
                                           session_factory, attempt,
                                           other_attempt):
        victim = AiCostLedger(
            session_factory, attempt_id=other_attempt.id,
            expected_screening_cap_usd=Decimal("0.50"),
            expected_downstream_cap_usd=Decimal("4.00"))
        rid = victim.reserve(kind=BUDGET_SCREENING, request_id="victim",
                             ceiling_usd="0.02")
        attacker = AiCostLedger(
            session_factory, attempt_id=attempt.id,
            expected_screening_cap_usd=Decimal("0.50"),
            expected_downstream_cap_usd=Decimal("4.00"))
        with pytest.raises(BudgetError, match="çapraz erişim"):
            attacker.settle(rid, "0.01")
        with pytest.raises(BudgetError, match="çapraz erişim"):
            attacker.charge_ceiling(rid)
        row = db_session.get(AiCostReservation, rid)
        db_session.refresh(row)
        assert row.state == STATE_RESERVED     # dokunulmadı

    def test_committed_totals_are_per_attempt(self, session_factory,
                                              attempt, other_attempt):
        a = AiCostLedger(session_factory, attempt_id=attempt.id,
                         expected_screening_cap_usd=Decimal("0.50"),
                         expected_downstream_cap_usd=Decimal("4.00"))
        b = AiCostLedger(session_factory, attempt_id=other_attempt.id,
                         expected_screening_cap_usd=Decimal("0.50"),
                         expected_downstream_cap_usd=Decimal("4.00"))
        a.reserve(kind=BUDGET_SCREENING, request_id="a1",
                  ceiling_usd="0.10")
        snap_b = b.snapshot()
        assert snap_b["committed_total_usd"] == "0"


class TestObservedActualRound7:
    """Codex 7. tur #3: gerçek harcama denetimde KAYBOLMAZ."""

    def test_invariant_keeps_observed_actual(self, db_session,
                                             session_factory, attempt):
        led = _ledger(session_factory, attempt)
        rid = led.reserve(kind=BUDGET_DOWNSTREAM, request_id="obs",
                          ceiling_usd="0.010")
        with pytest.raises(LedgerInvariantError):
            led.settle(rid, "0.020")
        row = db_session.get(AiCostReservation, rid)
        db_session.refresh(row)
        assert row.actual_usd == Decimal("0.010000")        # muhasebe
        assert row.observed_actual_usd == Decimal("0.020000")  # GERÇEK

    def test_ceiling_charge_can_record_observed(self, db_session,
                                                session_factory, attempt):
        led = _ledger(session_factory, attempt)
        rid = led.reserve(kind=BUDGET_SCREENING, request_id="obs2",
                          ceiling_usd="0.010")
        led.charge_ceiling(rid, observed_actual_usd="0.003")
        row = db_session.get(AiCostReservation, rid)
        db_session.refresh(row)
        assert row.actual_usd == Decimal("0.010000")
        assert row.observed_actual_usd == Decimal("0.003000")

    def test_snapshot_reports_observed_total(self, session_factory,
                                             attempt):
        led = _ledger(session_factory, attempt)
        rid = led.reserve(kind=BUDGET_SCREENING, request_id="obs3",
                          ceiling_usd="0.010")
        led.settle(rid, "0.004")
        assert led.snapshot()["observed_actual_total_usd"] == "0.004000"


class TestRoundingRound7:
    """Codex 7. tur #4: yuvarlama hard-cap YÖNÜNDE konservatif."""

    def test_spend_rounds_up(self):
        assert to_decimal("0.0000001", "x") == Decimal("0.000001")

    def test_cap_rounds_down(self):
        from app.core.telemetry.ai_cost_budget import to_cap_decimal

        assert to_cap_decimal("0.0000019", "cap") == Decimal("0.000001")


class TestStaleOwnerEvidenceRound7:
    """Codex 7. tur #5: canlı çağrıyla yarışma YOK."""

    def test_running_owner_is_not_reconciled(self, db_session,
                                             session_factory, attempt):
        led = _ledger(session_factory, attempt)
        rid = led.reserve(kind=BUDGET_SCREENING, request_id="live",
                          ceiling_usd="0.02")
        db_session.execute(sa.text(
            "UPDATE ai_cost_reservations SET created_at = now() - "
            "interval '3 hours' WHERE id = :rid"), {"rid": rid})
        db_session.commit()
        assert attempt.status == "running"
        assert reconcile_stale_reservations(
            db_session, attempt_id=attempt.id) == 0
        row = db_session.get(AiCostReservation, rid)
        db_session.refresh(row)
        assert row.state == STATE_RESERVED

    def test_operator_override_documented(self, db_session,
                                          session_factory, attempt):
        led = _ledger(session_factory, attempt)
        rid = led.reserve(kind=BUDGET_SCREENING, request_id="force",
                          ceiling_usd="0.02")
        db_session.execute(sa.text(
            "UPDATE ai_cost_reservations SET created_at = now() - "
            "interval '3 hours' WHERE id = :rid"), {"rid": rid})
        db_session.commit()
        assert reconcile_stale_reservations(
            db_session, attempt_id=attempt.id,
            require_terminal_owner=False) == 1


class TestDuplicateReservationRound7:
    """Codex 7. tur #6: aynı kimlik tekrarında TİPLİ davranış."""

    def test_in_flight_duplicate_is_typed(self, session_factory, attempt):
        led = _ledger(session_factory, attempt)
        led.reserve(kind=BUDGET_SCREENING, request_id="dup",
                    ceiling_usd="0.01")
        with pytest.raises(ReservationInFlight, match="AÇIK rezervasyon"):
            led.reserve(kind=BUDGET_SCREENING, request_id="dup",
                        ceiling_usd="0.01")

    def test_closed_duplicate_is_typed(self, session_factory, attempt):
        led = _ledger(session_factory, attempt)
        rid = led.reserve(kind=BUDGET_SCREENING, request_id="dup2",
                          ceiling_usd="0.01")
        led.settle(rid, "0.001")
        with pytest.raises(ReservationAlreadyClosed, match="replay"):
            led.reserve(kind=BUDGET_SCREENING, request_id="dup2",
                        ceiling_usd="0.01")

    def test_retry_attempt_number_is_separate_reservation(self,
                                                          session_factory,
                                                          attempt):
        led = _ledger(session_factory, attempt)
        led.reserve(kind=BUDGET_SCREENING, request_id="retry",
                    ceiling_usd="0.01", request_attempt=1)
        rid2 = led.reserve(kind=BUDGET_SCREENING, request_id="retry",
                           ceiling_usd="0.01", request_attempt=2)
        assert rid2 > 0


class TestSpendableOwnerRound8:
    """Codex 8. tur #1: terminal attempt YENI harcama baslatamaz."""

    def _led(self, session_factory, attempt):
        return AiCostLedger(
            session_factory, attempt_id=attempt.id,
            expected_screening_cap_usd=Decimal("0.50"),
            expected_downstream_cap_usd=Decimal("4.00"))

    @pytest.mark.parametrize("terminal", ["failed", "completed"])
    def test_terminal_owner_cannot_reserve(self, db_session,
                                           session_factory, attempt,
                                           terminal):
        attempt.status = terminal
        db_session.commit()
        led = self._led(session_factory, attempt)
        with pytest.raises(OwnerNotSpendable, match=terminal):
            led.reserve(kind=BUDGET_SCREENING, request_id="late",
                        ceiling_usd="0.01")
        assert db_session.query(AiCostReservation).filter_by(
            budget_owner_attempt_id=attempt.id).count() == 0

    def test_pending_owner_cannot_reserve(self, db_session,
                                          session_factory, attempt):
        """Harcama ancak orkestrasyon `running`'e gecirdikten SONRA."""
        attempt.status = "pending"
        db_session.commit()
        led = self._led(session_factory, attempt)
        with pytest.raises(OwnerNotSpendable):
            led.reserve(kind=BUDGET_SCREENING, request_id="early",
                        ceiling_usd="0.01")

    def test_late_retry_after_reconciliation_spends_nothing(
            self, db_session, session_factory, attempt):
        """Stale temizligi sonrasi gecikmis retry YENI rezervasyon acamaz."""
        led = self._led(session_factory, attempt)
        rid = led.reserve(kind=BUDGET_SCREENING, request_id="r1",
                          ceiling_usd="0.02")
        db_session.execute(sa.text(
            "UPDATE ai_cost_reservations SET created_at = now() - "
            "interval '3 hours' WHERE id = :rid"), {"rid": rid})
        attempt.status = "failed"
        db_session.commit()
        assert reconcile_stale_reservations(
            db_session, attempt_id=attempt.id) == 1
        with pytest.raises(OwnerNotSpendable):
            led.reserve(kind=BUDGET_SCREENING, request_id="r2",
                        ceiling_usd="0.02")
        assert db_session.query(AiCostReservation).filter_by(
            budget_owner_attempt_id=attempt.id).count() == 1

    def test_terminal_transition_race_has_single_winner(
            self, db_session, session_factory, attempt):
        """Terminal gecis ile reserve yarisi: kilit serilestirir."""
        led = self._led(session_factory, attempt)
        results = {"reserved": 0, "refused": 0}
        barrier = threading.Barrier(2)

        committed = []

        def terminator():
            session = session_factory()
            try:
                barrier.wait()
                session.execute(sa.text(
                    "UPDATE channel_assignment_attempts SET status='failed' "
                    "WHERE id = :aid"), {"aid": attempt.id})
                session.commit()
                committed.append(True)
            finally:
                session.close()

        def spender():
            barrier.wait()
            try:
                led.reserve(kind=BUDGET_SCREENING, request_id="race",
                            ceiling_usd="0.01")
                results["reserved"] += 1
            except OwnerNotSpendable:
                results["refused"] += 1

        threads = [threading.Thread(target=terminator),
                   threading.Thread(target=spender)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # Ya rezervasyon terminal gecisten ONCE olur ya da reddedilir;
        # her iki durumda da tutarli: reddedildiyse satir YOK
        assert results["reserved"] + results["refused"] == 1
        db_session.expire_all()
        # Codex cilasi: terminator GERCEKTEN commit etti ve son durum failed
        assert committed == [True]
        assert db_session.get(
            ChannelAssignmentAttempt, attempt.id).status == "failed"
        count = db_session.query(AiCostReservation).filter_by(
            budget_owner_attempt_id=attempt.id).count()
        assert count == results["reserved"]

    def test_settle_still_allowed_after_terminal(self, db_session,
                                                 session_factory, attempt):
        """Acik rezervasyonun kapatilmasi terminal sonrasi da MUMKUN
        olmali (muhasebe tamamlanmali); yalniz YENI harcama yasak."""
        led = self._led(session_factory, attempt)
        rid = led.reserve(kind=BUDGET_SCREENING, request_id="close-me",
                          ceiling_usd="0.02")
        attempt.status = "failed"
        db_session.commit()
        led.settle(rid, "0.003")
        row = db_session.get(AiCostReservation, rid)
        db_session.refresh(row)
        assert row.state == STATE_SETTLED


class TestRequiredCapBindingRound8:
    """Codex 8. tur #2: preflight cap baglamasi ZORUNLU."""

    def test_constructor_requires_expected_caps(self, session_factory,
                                                attempt):
        with pytest.raises(TypeError):
            AiCostLedger(session_factory, attempt_id=attempt.id)
        with pytest.raises(TypeError):
            AiCostLedger(session_factory, attempt_id=attempt.id,
                         expected_screening_cap_usd=Decimal("0.5"))

    def test_expected_cap_must_be_positive(self, session_factory, attempt):
        with pytest.raises(BudgetError, match="pozitif"):
            AiCostLedger(session_factory, attempt_id=attempt.id,
                         expected_screening_cap_usd=Decimal("0"),
                         expected_downstream_cap_usd=Decimal("4"))


class TestMicroNegativeRound8:
    """Codex 8. tur #3: mikro-negatif sifira yuvarlanip GECEMEZ."""

    @pytest.mark.parametrize("value", ["-0.0000001", "-0.000001", -1e-9])
    def test_micro_negative_rejected_before_quantize(self, value):
        with pytest.raises(BudgetError, match="negatif"):
            to_decimal(value, "ceiling_usd")

    def test_micro_negative_rejected_in_reserve_and_settle(
            self, session_factory, attempt):
        led = AiCostLedger(
            session_factory, attempt_id=attempt.id,
            expected_screening_cap_usd=Decimal("0.50"),
            expected_downstream_cap_usd=Decimal("4.00"))
        with pytest.raises(BudgetError, match="negatif"):
            led.reserve(kind=BUDGET_SCREENING, request_id="neg",
                        ceiling_usd="-0.0000001")
        rid = led.reserve(kind=BUDGET_SCREENING, request_id="pos",
                          ceiling_usd="0.01")
        with pytest.raises(BudgetError, match="negatif"):
            led.settle(rid, "-0.0000001")
        with pytest.raises(BudgetError, match="negatif"):
            led.charge_ceiling(rid, observed_actual_usd="-0.0000001")
