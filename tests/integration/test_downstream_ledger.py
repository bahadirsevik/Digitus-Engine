# -*- coding: utf-8 -*-
"""Gemini downstream çağrıları kalıcı ledger'a BAĞLI (plan §10.2).

Codex 21. tur #1: bugüne kadar yalnız screening rezerve ediliyordu; kanal
atamasının Gemini çağrıları (intent, marka filtresi, prefilter, transfer,
expansion, SEO metadata) hiçbir bütçeye bağlı değildi — bu yüzden
kullanıcıya "birleşik hard-cap" vaat edilemiyordu.

Kilitlenenler:
- Rezervasyon birimi GERÇEK HTTP denemesidir (retry'lar dahil).
- Usage yoksa TAVAN yakılır; usage varsa gerçek maliyetle settle edilir.
- Cap aşılacaksa çağrı YAPILMADAN BudgetExceeded yükselir.
- Bağlı değilse (off koşuları) davranış DEĞİŞMEZ.
"""
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app.core.telemetry.ai_cost_budget import (
    BUDGET_DOWNSTREAM,
    BUDGET_SCREENING,
    STATE_CEILING,
    STATE_SETTLED,
    AiCostLedger,
    BudgetExceeded,
)
from app.core.telemetry.downstream_ledger import attach_downstream_ledger
from app.database.models import AiCostReservation, ChannelAssignmentAttempt


class FakeUsage:
    def __init__(self, prompt=1000, candidates=500, thoughts=0):
        self.prompt_token_count = prompt
        self.candidates_token_count = candidates
        self.thoughts_token_count = thoughts
        self.total_token_count = prompt + candidates + thoughts


class FakeGemini:
    """`_execute`in ledger kancasını taşıyan asgari taklit.

    Gerçek `GeminiService._execute` ile AYNI sırayı uygular:
    reserve -> HTTP -> (hata: ceiling) / (başarı: settle).
    """

    def __init__(self, *, usage=None, fail=False, model="gemini-3.5-flash"):
        self.usage = usage
        self.fail = fail
        self.model = model
        self.cost_binding = None
        self.calls = 0

    def _execute(self, prompt: str, *, max_tokens: int, stage: str) -> str:
        """Gerçek backend imzası: bağlayıcı bunu görüp bağlanabilir."""
        return self.execute(prompt, max_tokens=max_tokens, stage=stage)

    def execute(self, prompt: str, *, max_tokens: int, stage: str) -> str:
        binding = self.cost_binding
        reservation_id = None
        if binding is not None:
            reservation_id = binding.reserve(
                stage=stage, model=self.model,
                prompt_bytes=len(prompt.encode("utf-8")),
                max_output_tokens=max_tokens)
        self.calls += 1
        if self.fail:
            if binding is not None:
                binding.charge_ceiling(reservation_id)
            raise RuntimeError("gemini patladı")
        if binding is not None:
            binding.settle(reservation_id, model=self.model, usage=self.usage)
        return "{}"


@pytest.fixture
def session_factory(db_session):
    return sa.orm.sessionmaker(bind=db_session.get_bind(),
                               expire_on_commit=False)


@pytest.fixture
def attempt(db_session, make_workspace, make_scoring_run):
    ws = make_workspace("Ledger WS")
    run = make_scoring_run(brand_profile_id=ws.id, status="scored")
    row = ChannelAssignmentAttempt(
        parent_task_id="dsn-parent", scoring_run_id=run.id,
        brand_profile_id=ws.id, status="running", phase="assignment",
        screening_mode="shadow", applied_candidate_multiplier=1,
        counterfactual_target_multiplier=3,
        approved_screening_cap_usd=Decimal("0.50"),
        approved_downstream_cap_usd=Decimal("2.00"))
    db_session.add(row)
    db_session.commit()
    return row


def _ledger(session_factory, attempt, downstream="2.00"):
    return AiCostLedger(
        session_factory, attempt_id=attempt.id,
        expected_screening_cap_usd=Decimal("0.50"),
        expected_downstream_cap_usd=Decimal(downstream))


def _rows(db_session, attempt):
    db_session.expire_all()
    return (db_session.query(AiCostReservation)
            .filter_by(budget_owner_attempt_id=attempt.id,
                       budget_kind=BUDGET_DOWNSTREAM).all())


class TestDownstreamBinding:
    def test_every_call_reserves_and_settles(self, db_session,
                                             session_factory, attempt):
        ai = FakeGemini(usage=FakeUsage())
        attach_downstream_ledger(ai, _ledger(session_factory, attempt),
                                 scoring_run_id=attempt.scoring_run_id,
                                 attempt_id=attempt.id)
        ai.execute("intent prompt", max_tokens=3000, stage="intent")
        ai.execute("prefilter prompt", max_tokens=3000, stage="ads_prefilter")
        rows = _rows(db_session, attempt)
        assert len(rows) == 2                      # HER çağrı ayrı satır
        assert all(r.state == STATE_SETTLED for r in rows)
        assert {r.stage for r in rows} == {"intent", "ads_prefilter"}
        assert all(float(r.actual_usd) > 0 for r in rows)
        # Gerçek maliyet TAVANIN altında olmalı
        assert all(float(r.actual_usd) <= float(r.ceiling_usd) for r in rows)

    def test_missing_usage_charges_ceiling(self, db_session, session_factory,
                                           attempt):
        ai = FakeGemini(usage=None)
        attach_downstream_ledger(ai, _ledger(session_factory, attempt),
                                 scoring_run_id=attempt.scoring_run_id,
                                 attempt_id=attempt.id)
        ai.execute("prompt", max_tokens=3000, stage="intent")
        rows = _rows(db_session, attempt)
        assert len(rows) == 1 and rows[0].state == STATE_CEILING

    def test_transport_error_charges_ceiling(self, db_session,
                                             session_factory, attempt):
        ai = FakeGemini(fail=True)
        binding = attach_downstream_ledger(
            ai, _ledger(session_factory, attempt),
            scoring_run_id=attempt.scoring_run_id, attempt_id=attempt.id)
        with pytest.raises(RuntimeError):
            ai.execute("prompt", max_tokens=3000, stage="intent")
        rows = _rows(db_session, attempt)
        assert len(rows) == 1 and rows[0].state == STATE_CEILING
        assert binding.summary()["downstream_ceiling_charges"] == 1

    def test_cap_exceeded_blocks_the_call(self, db_session, session_factory,
                                          attempt):
        attempt.approved_downstream_cap_usd = Decimal("0.000001")
        db_session.commit()
        ai = FakeGemini(usage=FakeUsage())
        attach_downstream_ledger(
            ai, _ledger(session_factory, attempt, downstream="0.000001"),
            scoring_run_id=attempt.scoring_run_id, attempt_id=attempt.id)
        with pytest.raises(BudgetExceeded):
            ai.execute("prompt", max_tokens=3000, stage="intent")
        assert ai.calls == 0                       # ÇAĞRI YAPILMADI

    def test_unbound_service_is_unchanged(self, db_session, attempt):
        ai = FakeGemini(usage=FakeUsage())
        ai.execute("prompt", max_tokens=3000, stage="intent")
        assert ai.calls == 1
        assert _rows(db_session, attempt) == []    # muhasebe YOK

    def test_retries_are_separate_reservations(self, db_session,
                                               session_factory, attempt):
        """Retry AYRI HTTP denemesidir: ayrı rezervasyon alır."""
        ai = FakeGemini(usage=FakeUsage())
        attach_downstream_ledger(ai, _ledger(session_factory, attempt),
                                 scoring_run_id=attempt.scoring_run_id,
                                 attempt_id=attempt.id)
        for _ in range(3):
            ai.execute("aynı prompt", max_tokens=3000, stage="intent")
        rows = _rows(db_session, attempt)
        assert len(rows) == 3
        assert len({r.request_id for r in rows}) == 3


class TestCombinedBudget:
    def test_screening_and_downstream_share_one_owner(self, db_session,
                                                      session_factory,
                                                      attempt):
        ledger = _ledger(session_factory, attempt)
        ledger.reserve(kind=BUDGET_SCREENING, request_id="scr:x:c1",
                       ceiling_usd="0.40")
        ai = FakeGemini(usage=FakeUsage())
        attach_downstream_ledger(ai, ledger,
                                 scoring_run_id=attempt.scoring_run_id,
                                 attempt_id=attempt.id)
        ai.execute("prompt", max_tokens=3000, stage="intent")
        caps = ledger.caps()
        assert caps["combined"] == Decimal("2.50")
        snap = ledger.snapshot()
        by_kind = snap["by_kind"]
        # İKİ bütçe de AYNI attempt'in altında toplanır
        assert Decimal(by_kind[BUDGET_SCREENING]["open_reserved_usd"]) ==             Decimal("0.400000")
        assert Decimal(by_kind[BUDGET_DOWNSTREAM]["settled_usd"]) > 0
        # Birleşik taahhüt iki türün TOPLAMIDIR
        assert Decimal(snap["committed_total_usd"]) > Decimal("0.400000")
        assert Decimal(snap["remaining_combined_usd"]) < Decimal("2.50")

    def test_downstream_cannot_spend_screening_budget(self, db_session,
                                                      session_factory,
                                                      attempt):
        """Tür bazlı cap ayrı: downstream, screening kotasını yiyemez."""
        attempt.approved_downstream_cap_usd = Decimal("0.000001")
        db_session.commit()
        ai = FakeGemini(usage=FakeUsage())
        attach_downstream_ledger(
            ai, _ledger(session_factory, attempt, downstream="0.000001"),
            scoring_run_id=attempt.scoring_run_id, attempt_id=attempt.id)
        with pytest.raises(BudgetExceeded, match="downstream"):
            ai.execute("prompt", max_tokens=3000, stage="intent")
