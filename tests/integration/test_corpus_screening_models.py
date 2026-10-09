# -*- coding: utf-8 -*-
"""Faz 1 veri modeli kısıtları — GERÇEK Postgres'e karşı (plan §5, §13).

Codex tasarım kısıtları burada kilitlenir:
- para alanları Numeric/Decimal (float sızıntısı yok)
- materyalizasyon unique anahtarı run ID içerir
- screening reuse ve materyalizasyon kimlikleri AYRI indekslidir
- reservation CAS geçişleri DB CHECK'leriyle sınırlıdır
- migration idempotent (baseline-squash düzeni)
"""
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app.database.models import (
    AiCostReservation,
    BrandProfile,
    ChannelAssignmentAttempt,
    CorpusCandidateSelection,
    CorpusScreeningBatchCheckpoint,
    CorpusScreeningJob,
    Keyword,
    ScoringRun,
)


def _run(db, ws_id, name="corpus-test"):
    run = ScoringRun(run_name=name, brand_profile_id=ws_id,
                     total_keywords=10, ads_capacity=5, seo_capacity=5,
                     social_capacity=5, status="scored")
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def _attempt(db, run, ws_id, task_id="task-1", **over):
    payload = dict(parent_task_id=task_id, scoring_run_id=run.id,
                   brand_profile_id=ws_id, status="pending",
                   phase="screening", screening_mode="off",
                   applied_candidate_multiplier=1,
                   counterfactual_target_multiplier=1)
    payload.update(over)
    attempt = ChannelAssignmentAttempt(**payload)
    db.add(attempt)
    db.commit()
    db.refresh(attempt)
    return attempt


def _job(db, run, ws_id, identity, status="pending", **over):
    payload = dict(
        scoring_run_id=run.id, brand_profile_id=ws_id, status=status,
        provider="deepseek", model="deepseek-v4-flash",
        prompt_version="SCR-2026-07-27-v3a", temperature=Decimal("0"),
        batch_size=10, view_salts=["scr-view-a", "scr-view-b"],
        applied_screening_channels=["ADS", "SEO"],
        screening_input_identity_sha256=identity,
        universe_sha256="u" * 64, context_sha256="c" * 64,
        input_snapshot={"rows": []}, screening_context={"fields": {}})
    payload.update(over)
    job = CorpusScreeningJob(**payload)
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


class TestAttemptConstraints:
    def test_multiplier_matrix_enforced(self, db_session, make_workspace):
        ws = make_workspace()
        run = _run(db_session, ws.id)
        # off -> 1/1 geçerli
        _attempt(db_session, run, ws.id, task_id="ok-off")
        # shadow -> 1/3 geçerli
        _attempt(db_session, run, ws.id, task_id="ok-shadow",
                 screening_mode="shadow", status="completed",
                 counterfactual_target_multiplier=3)
        # assistive -> 3/3 geçerli
        _attempt(db_session, run, ws.id, task_id="ok-assistive",
                 screening_mode="assistive", status="completed",
                 applied_candidate_multiplier=3,
                 counterfactual_target_multiplier=3)
        # off + 3 YASAK
        with pytest.raises(IntegrityError):
            _attempt(db_session, run, ws.id, task_id="bad",
                     status="completed", applied_candidate_multiplier=3)
        db_session.rollback()

    def test_single_active_attempt_per_run(self, db_session, make_workspace):
        ws = make_workspace()
        run = _run(db_session, ws.id)
        _attempt(db_session, run, ws.id, task_id="a1", status="running")
        with pytest.raises(IntegrityError):
            _attempt(db_session, run, ws.id, task_id="a2", status="pending")
        db_session.rollback()

    def test_completed_attempts_can_coexist(self, db_session,
                                            make_workspace):
        ws = make_workspace()
        run = _run(db_session, ws.id)
        _attempt(db_session, run, ws.id, task_id="c1", status="completed")
        _attempt(db_session, run, ws.id, task_id="c2", status="completed")
        _attempt(db_session, run, ws.id, task_id="c3", status="failed")
        assert db_session.query(ChannelAssignmentAttempt).filter_by(
            scoring_run_id=run.id).count() == 3

    def test_parent_task_id_unique(self, db_session, make_workspace):
        ws = make_workspace()
        run = _run(db_session, ws.id)
        _attempt(db_session, run, ws.id, task_id="dup", status="completed")
        with pytest.raises(IntegrityError):
            _attempt(db_session, run, ws.id, task_id="dup",
                     status="completed")
        db_session.rollback()

    def test_invalid_enum_values_rejected(self, db_session, make_workspace):
        ws = make_workspace()
        run = _run(db_session, ws.id)
        with pytest.raises(IntegrityError):
            _attempt(db_session, run, ws.id, task_id="x",
                     assignment_dispatch_state="hayalet")
        db_session.rollback()

    def test_caps_must_be_positive(self, db_session, make_workspace):
        ws = make_workspace()
        run = _run(db_session, ws.id)
        with pytest.raises(IntegrityError):
            _attempt(db_session, run, ws.id, task_id="cap0",
                     approved_screening_cap_usd=Decimal("0"))
        db_session.rollback()

    def test_money_is_decimal_not_float(self, db_session, make_workspace):
        ws = make_workspace()
        run = _run(db_session, ws.id)
        attempt = _attempt(
            db_session, run, ws.id, task_id="money",
            approved_screening_cap_usd=Decimal("0.500000"),
            approved_downstream_cap_usd=Decimal("4.250000"))
        db_session.expire_all()
        fresh = db_session.get(ChannelAssignmentAttempt, attempt.id)
        assert isinstance(fresh.approved_screening_cap_usd, Decimal)
        assert fresh.approved_downstream_cap_usd == Decimal("4.250000")


class TestScreeningJobIdentityIndexes:
    def test_single_active_job_per_identity(self, db_session,
                                            make_workspace):
        ws = make_workspace()
        run = _run(db_session, ws.id)
        _job(db_session, run, ws.id, "a" * 64, status="running")
        with pytest.raises(IntegrityError):
            _job(db_session, run, ws.id, "a" * 64, status="pending")
        db_session.rollback()

    def test_single_completed_job_per_identity(self, db_session,
                                               make_workspace):
        """Reuse anahtarı: aynı kimlikte tek doğrulanmış completed iş."""
        ws = make_workspace()
        run = _run(db_session, ws.id)
        _job(db_session, run, ws.id, "b" * 64, status="completed")
        with pytest.raises(IntegrityError):
            _job(db_session, run, ws.id, "b" * 64, status="completed")
        db_session.rollback()

    def test_failed_and_completed_can_coexist(self, db_session,
                                              make_workspace):
        ws = make_workspace()
        run = _run(db_session, ws.id)
        _job(db_session, run, ws.id, "c" * 64, status="failed")
        _job(db_session, run, ws.id, "c" * 64, status="fallback")
        _job(db_session, run, ws.id, "c" * 64, status="completed")
        assert db_session.query(CorpusScreeningJob).filter_by(
            screening_input_identity_sha256="c" * 64).count() == 3

    def test_provider_and_batch_constraints(self, db_session,
                                            make_workspace):
        ws = make_workspace()
        run = _run(db_session, ws.id)
        with pytest.raises(IntegrityError):
            _job(db_session, run, ws.id, "d" * 64, provider="openai")
        db_session.rollback()
        with pytest.raises(IntegrityError):
            _job(db_session, run, ws.id, "e" * 64, batch_size=0)
        db_session.rollback()


class TestCandidateSelectionKeys:
    def _selection(self, db, attempt, run, keyword, **over):
        payload = dict(assignment_attempt_id=attempt.id,
                       scoring_run_id=run.id, keyword_id=keyword.id,
                       channel="ADS", origin_source="baseline",
                       materialization_action="initial", is_applied=True)
        payload.update(over)
        row = CorpusCandidateSelection(**payload)
        db.add(row)
        db.commit()
        return row

    def _keyword(self, db, text="anahtar"):
        kw = Keyword(keyword=text, monthly_volume=100)
        db.add(kw)
        db.commit()
        db.refresh(kw)
        return kw

    def test_unique_key_includes_run_id(self, db_session, make_workspace):
        """Codex: materyalizasyon unique anahtarı run ID İÇERMELİ."""
        insp = sa.inspect(db_session.get_bind())
        uniques = {u["name"]: u["column_names"] for u
                   in insp.get_unique_constraints(
                       "corpus_candidate_selections")}
        assert "scoring_run_id" in uniques["uq_candidate_selection"]
        assert set(uniques["uq_candidate_selection"]) == {
            "scoring_run_id", "assignment_attempt_id", "keyword_id",
            "channel", "materialization_action"}

    def test_duplicate_initial_row_rejected(self, db_session,
                                            make_workspace):
        ws = make_workspace()
        run = _run(db_session, ws.id)
        attempt = _attempt(db_session, run, ws.id, task_id="sel")
        kw = self._keyword(db_session)
        self._selection(db_session, attempt, run, kw)
        with pytest.raises(IntegrityError):
            self._selection(db_session, attempt, run, kw)
        db_session.rollback()

    def test_transfer_and_expansion_rows_coexist(self, db_session,
                                                 make_workspace):
        ws = make_workspace()
        run = _run(db_session, ws.id)
        attempt = _attempt(db_session, run, ws.id, task_id="sel2")
        kw = self._keyword(db_session, "başka anahtar")
        self._selection(db_session, attempt, run, kw)
        self._selection(db_session, attempt, run, kw,
                        materialization_action="transfer",
                        origin_source="none")
        self._selection(db_session, attempt, run, kw,
                        materialization_action="expansion",
                        origin_source="screening")
        assert db_session.query(CorpusCandidateSelection).filter_by(
            assignment_attempt_id=attempt.id).count() == 3

    def test_invalid_origin_rejected(self, db_session, make_workspace):
        ws = make_workspace()
        run = _run(db_session, ws.id)
        attempt = _attempt(db_session, run, ws.id, task_id="sel3")
        kw = self._keyword(db_session, "ucuncu")
        with pytest.raises(IntegrityError):
            self._selection(db_session, attempt, run, kw,
                            origin_source="hayalet")
        db_session.rollback()

    def test_identities_indexed_separately(self, db_session):
        """Codex: iki kimlik AYRI indekslenir."""
        insp = sa.inspect(db_session.get_bind())
        sel_idx = {i["name"] for i
                   in insp.get_indexes("corpus_candidate_selections")}
        job_idx = {i["name"] for i
                   in insp.get_indexes("corpus_screening_jobs")}
        assert "idx_candidate_selection_materialization" in sel_idx
        assert "uq_screening_job_completed_identity" in job_idx


class TestCostReservationCAS:
    def _res(self, db, attempt, **over):
        payload = dict(budget_owner_attempt_id=attempt.id,
                       budget_kind="screening", request_id="req-1",
                       attempt=1, ceiling_usd=Decimal("0.010000"),
                       state="reserved")
        payload.update(over)
        row = AiCostReservation(**payload)
        db.add(row)
        db.commit()
        return row

    def test_reserved_cannot_carry_actual(self, db_session, make_workspace):
        ws = make_workspace()
        run = _run(db_session, ws.id)
        attempt = _attempt(db_session, run, ws.id, task_id="led1")
        with pytest.raises(IntegrityError):
            self._res(db_session, attempt, actual_usd=Decimal("0.005"))
        db_session.rollback()

    def test_settled_requires_actual(self, db_session, make_workspace):
        ws = make_workspace()
        run = _run(db_session, ws.id)
        attempt = _attempt(db_session, run, ws.id, task_id="led2")
        with pytest.raises(IntegrityError):
            self._res(db_session, attempt, state="settled")
        db_session.rollback()

    def test_ceiling_charge_must_equal_ceiling(self, db_session,
                                               make_workspace):
        ws = make_workspace()
        run = _run(db_session, ws.id)
        attempt = _attempt(db_session, run, ws.id, task_id="led3")
        with pytest.raises(IntegrityError):
            self._res(db_session, attempt, state="ceiling_charged",
                      actual_usd=Decimal("0.001000"))
        db_session.rollback()
        ok = self._res(db_session, attempt, state="ceiling_charged",
                       actual_usd=Decimal("0.010000"),
                       settled_at=sa.func.now())
        assert ok.state == "ceiling_charged"

    def test_settle_transition_allowed(self, db_session, make_workspace):
        ws = make_workspace()
        run = _run(db_session, ws.id)
        attempt = _attempt(db_session, run, ws.id, task_id="led4")
        row = self._res(db_session, attempt)
        row.state = "settled"
        row.actual_usd = Decimal("0.004000")
        row.settled_at = sa.func.now()
        db_session.commit()
        db_session.expire_all()
        fresh = db_session.get(AiCostReservation, row.id)
        assert fresh.state == "settled"
        assert isinstance(fresh.actual_usd, Decimal)

    def test_duplicate_request_attempt_rejected(self, db_session,
                                                make_workspace):
        ws = make_workspace()
        run = _run(db_session, ws.id)
        attempt = _attempt(db_session, run, ws.id, task_id="led5")
        self._res(db_session, attempt)
        with pytest.raises(IntegrityError):
            self._res(db_session, attempt)
        db_session.rollback()

    def test_same_request_different_kind_allowed(self, db_session,
                                                 make_workspace):
        ws = make_workspace()
        run = _run(db_session, ws.id)
        attempt = _attempt(db_session, run, ws.id, task_id="led6")
        self._res(db_session, attempt)
        self._res(db_session, attempt, budget_kind="downstream")
        assert db_session.query(AiCostReservation).filter_by(
            budget_owner_attempt_id=attempt.id).count() == 2

    def test_invalid_kind_or_state_rejected(self, db_session,
                                            make_workspace):
        ws = make_workspace()
        run = _run(db_session, ws.id)
        attempt = _attempt(db_session, run, ws.id, task_id="led7")
        with pytest.raises(IntegrityError):
            self._res(db_session, attempt, budget_kind="hayalet")
        db_session.rollback()
        with pytest.raises(IntegrityError):
            self._res(db_session, attempt, state="hayalet")
        db_session.rollback()


class TestCheckpointConstraints:
    def test_completed_requires_payload_sha(self, db_session,
                                            make_workspace):
        ws = make_workspace()
        run = _run(db_session, ws.id)
        job = _job(db_session, run, ws.id, "f" * 64)
        cp = CorpusScreeningBatchCheckpoint(
            screening_job_id=job.id, view="scr-view-a", batch_ordinal=1,
            batch_hash="h" * 64, request_contract_sha256="r" * 64,
            state="completed")
        db_session.add(cp)
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_duplicate_batch_rejected(self, db_session, make_workspace):
        ws = make_workspace()
        run = _run(db_session, ws.id)
        job = _job(db_session, run, ws.id, "g" * 64)
        for _ in range(2):
            cp = CorpusScreeningBatchCheckpoint(
                screening_job_id=job.id, view="scr-view-a", batch_ordinal=1,
                batch_hash="i" * 64, request_contract_sha256="r" * 64,
                state="pending")
            db_session.add(cp)
            if _ == 0:
                db_session.commit()
            else:
                with pytest.raises(IntegrityError):
                    db_session.commit()
                db_session.rollback()

    def test_same_batch_different_view_allowed(self, db_session,
                                               make_workspace):
        ws = make_workspace()
        run = _run(db_session, ws.id)
        job = _job(db_session, run, ws.id, "j" * 64)
        for view in ("scr-view-a", "scr-view-b"):
            db_session.add(CorpusScreeningBatchCheckpoint(
                screening_job_id=job.id, view=view, batch_ordinal=1,
                batch_hash="k" * 64, request_contract_sha256="r" * 64,
                state="pending"))
        db_session.commit()
        assert db_session.query(CorpusScreeningBatchCheckpoint).filter_by(
            screening_job_id=job.id).count() == 2


class TestFlagOffCompatibility:
    def test_existing_runs_default_to_off(self, db_session,
                                          make_workspace):
        """Backfill YOK: mevcut run'lar 'off' ve NULL künye ile çalışır."""
        ws = make_workspace()
        run = _run(db_session, ws.id, name="legacy")
        db_session.expire_all()
        fresh = db_session.get(ScoringRun, run.id)
        assert fresh.screening_preference == "off"
        assert fresh.channel_pool_screening_mode is None
        assert fresh.channel_pool_screening_context_sha256 is None

    def test_channel_candidate_source_fields_nullable(self, db_session,
                                                      make_workspace):
        from app.database.models import ChannelCandidate

        ws = make_workspace()
        run = _run(db_session, ws.id)
        kw = Keyword(keyword="flagoff", monthly_volume=10)
        db_session.add(kw)
        db_session.commit()
        cand = ChannelCandidate(scoring_run_id=run.id, keyword_id=kw.id,
                                channel="ADS", raw_score=1, rank_in_channel=1)
        db_session.add(cand)
        db_session.commit()
        db_session.expire_all()
        fresh = db_session.get(ChannelCandidate, cand.id)
        assert fresh.screening_job_id is None
        assert fresh.candidate_origin_source is None
        assert fresh.screening_fit is None


class TestSchemaGuardsRound6:
    """Codex 6. tur: sema garantileri ledger/runner'dan ONCE (5 bulgu)."""

    # #1 — attempts -> screening job FK'si YUKSELTME YOLUNDA da var
    def test_attempt_screening_job_fk_exists(self, db_session):
        insp = sa.inspect(db_session.get_bind())
        fks = insp.get_foreign_keys("channel_assignment_attempts")
        assert any(fk["constrained_columns"] == ["screening_job_id"]
                   and fk["referred_table"] == "corpus_screening_jobs"
                   for fk in fks), "attempts -> jobs FK yok"

    def test_attempt_rejects_unknown_screening_job(self, db_session,
                                                   make_workspace):
        ws = make_workspace()
        run = _run(db_session, ws.id)
        with pytest.raises(IntegrityError):
            _attempt(db_session, run, ws.id, task_id="fk1",
                     screening_job_id=999999)
        db_session.rollback()

    # #2 — ledger terminal durum sertlestirmesi
    def test_actual_cannot_exceed_ceiling(self, db_session, make_workspace):
        ws = make_workspace()
        run = _run(db_session, ws.id)
        attempt = _attempt(db_session, run, ws.id, task_id="led-r6a")
        row = AiCostReservation(
            budget_owner_attempt_id=attempt.id, budget_kind="downstream",
            request_id="req-over", attempt=1,
            ceiling_usd=Decimal("0.010000"),
            actual_usd=Decimal("0.020000"), state="settled",
            settled_at=sa.func.now())
        db_session.add(row)
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_terminal_state_requires_settled_at(self, db_session,
                                                make_workspace):
        ws = make_workspace()
        run = _run(db_session, ws.id)
        attempt = _attempt(db_session, run, ws.id, task_id="led-r6b")
        row = AiCostReservation(
            budget_owner_attempt_id=attempt.id, budget_kind="screening",
            request_id="req-nots", attempt=1,
            ceiling_usd=Decimal("0.010000"),
            actual_usd=Decimal("0.004000"), state="settled")
        db_session.add(row)
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_actual_equal_ceiling_allowed(self, db_session, make_workspace):
        ws = make_workspace()
        run = _run(db_session, ws.id)
        attempt = _attempt(db_session, run, ws.id, task_id="led-r6c")
        row = AiCostReservation(
            budget_owner_attempt_id=attempt.id, budget_kind="screening",
            request_id="req-eq", attempt=1,
            ceiling_usd=Decimal("0.010000"),
            actual_usd=Decimal("0.010000"), state="ceiling_charged",
            settled_at=sa.func.now())
        db_session.add(row)
        db_session.commit()
        assert row.id is not None

    # #3 — completed checkpoint GERCEK payload ister
    def test_completed_checkpoint_requires_real_payload(self, db_session,
                                                        make_workspace):
        ws = make_workspace()
        run = _run(db_session, ws.id)
        job = _job(db_session, run, ws.id, "r6a" + "0" * 61)
        # SHA var ama payload YOK -> reddedilir
        db_session.add(CorpusScreeningBatchCheckpoint(
            screening_job_id=job.id, view="scr-view-a", batch_ordinal=1,
            batch_hash="h1" + "0" * 62, request_contract_sha256="r" * 64,
            state="completed", payload_sha256="p" * 64,
            completed_at=sa.func.now()))
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_completed_checkpoint_requires_completed_at(self, db_session,
                                                        make_workspace):
        ws = make_workspace()
        run = _run(db_session, ws.id)
        job = _job(db_session, run, ws.id, "r6b" + "0" * 61)
        db_session.add(CorpusScreeningBatchCheckpoint(
            screening_job_id=job.id, view="scr-view-a", batch_ordinal=1,
            batch_hash="h2" + "0" * 62, request_contract_sha256="r" * 64,
            state="completed", payload={"results": []},
            payload_sha256="p" * 64))
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_valid_completed_checkpoint_accepted(self, db_session,
                                                 make_workspace):
        ws = make_workspace()
        run = _run(db_session, ws.id)
        job = _job(db_session, run, ws.id, "r6c" + "0" * 61)
        cp = CorpusScreeningBatchCheckpoint(
            screening_job_id=job.id, view="scr-view-a", batch_ordinal=1,
            batch_hash="h3" + "0" * 62, request_contract_sha256="r" * 64,
            state="completed", payload={"results": [{"id": 1}]},
            payload_sha256="p" * 64, completed_at=sa.func.now())
        db_session.add(cp)
        db_session.commit()
        assert cp.id is not None

    # #4 — selection run'i attempt run'i ile AYNI olmali
    def test_selection_run_must_match_attempt_run(self, db_session,
                                                  make_workspace):
        ws = make_workspace()
        run_a = _run(db_session, ws.id, name="run-a")
        run_b = _run(db_session, ws.id, name="run-b")
        attempt = _attempt(db_session, run_a, ws.id, task_id="mismatch")
        kw = Keyword(keyword="capraz run", monthly_volume=10)
        db_session.add(kw)
        db_session.commit()
        db_session.add(CorpusCandidateSelection(
            assignment_attempt_id=attempt.id, scoring_run_id=run_b.id,
            keyword_id=kw.id, channel="ADS", origin_source="baseline",
            materialization_action="initial"))
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_composite_fk_present(self, db_session):
        insp = sa.inspect(db_session.get_bind())
        fks = insp.get_foreign_keys("corpus_candidate_selections")
        composite = [fk for fk in fks
                     if set(fk["constrained_columns"])
                     == {"assignment_attempt_id", "scoring_run_id"}]
        assert composite, "composite attempt+run FK yok"
        assert composite[0]["referred_table"] == "channel_assignment_attempts"

    # #5 — checkpoint ordinal tekilligi
    def test_duplicate_ordinal_rejected(self, db_session, make_workspace):
        ws = make_workspace()
        run = _run(db_session, ws.id)
        job = _job(db_session, run, ws.id, "r6d" + "0" * 61)
        db_session.add(CorpusScreeningBatchCheckpoint(
            screening_job_id=job.id, view="scr-view-a", batch_ordinal=7,
            batch_hash="x1" + "0" * 62, request_contract_sha256="r" * 64,
            state="pending"))
        db_session.commit()
        db_session.add(CorpusScreeningBatchCheckpoint(
            screening_job_id=job.id, view="scr-view-a", batch_ordinal=7,
            batch_hash="x2" + "0" * 62, request_contract_sha256="r" * 64,
            state="pending"))
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_same_ordinal_different_view_allowed(self, db_session,
                                                 make_workspace):
        ws = make_workspace()
        run = _run(db_session, ws.id)
        job = _job(db_session, run, ws.id, "r6e" + "0" * 61)
        for i, view in enumerate(("scr-view-a", "scr-view-b")):
            db_session.add(CorpusScreeningBatchCheckpoint(
                screening_job_id=job.id, view=view, batch_ordinal=3,
                batch_hash=f"y{i}" + "0" * 62,
                request_contract_sha256="r" * 64, state="pending"))
        db_session.commit()
        assert db_session.query(CorpusScreeningBatchCheckpoint).filter_by(
            screening_job_id=job.id, batch_ordinal=3).count() == 2
