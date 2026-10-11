# -*- coding: utf-8 -*-
"""Downstream ledger: provider gecisi, Anthropic-seklinde usage, cache, es-zamanlilik."""
import threading
from decimal import Decimal
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from app.core.telemetry.ai_cost_budget import (
    STATE_CEILING,
    STATE_SETTLED,
    AiCostLedger,
    BudgetExceeded,
)
from app.core.telemetry.downstream_ledger import (
    LedgerBindError,
    attach_downstream_ledger,
)
from app.database.models import AiCostReservation, ChannelAssignmentAttempt

MODEL = "deepseek-v4-flash"      # in 0.14 / hit 0.0028 / out 0.28 (USD per M)


class _Backend:
    def __init__(self):
        self.cost_binding = None

    def _execute(self):  # pragma: no cover
        return ""


@pytest.fixture
def session_factory(db_session):
    return sa.orm.sessionmaker(bind=db_session.get_bind(),
                               expire_on_commit=False)


@pytest.fixture
def make_binding(db_session, session_factory, make_workspace,
                 make_scoring_run):
    def _make(cap="2.00"):
        ws = make_workspace("Anthropic Ledger WS")
        run = make_scoring_run(brand_profile_id=ws.id, status="scored")
        attempt = ChannelAssignmentAttempt(
            parent_task_id="anth-ledger", scoring_run_id=run.id,
            brand_profile_id=ws.id, status="running", phase="assignment",
            screening_mode="shadow", applied_candidate_multiplier=1,
            counterfactual_target_multiplier=3,
            approved_screening_cap_usd=Decimal("0.50"),
            approved_downstream_cap_usd=Decimal(cap))
        db_session.add(attempt)
        db_session.commit()
        ledger = AiCostLedger(
            session_factory, attempt_id=attempt.id,
            expected_screening_cap_usd=Decimal("0.50"),
            expected_downstream_cap_usd=Decimal(cap))
        binding = attach_downstream_ledger(
            _Backend(), ledger, scoring_run_id=run.id, attempt_id=attempt.id)
        return binding, attempt
    return _make


def _usage(prompt=1000, cand=500, read=0, write=0):
    return SimpleNamespace(
        prompt_token_count=prompt, candidates_token_count=cand,
        thoughts_token_count=None, total_token_count=prompt + cand,
        cache_read_token_count=read, cache_creation_token_count=write)


def _row(db_session, rid):
    db_session.expire_all()
    return db_session.get(AiCostReservation, rid)


def _reserve(binding, **kw):
    return binding.reserve(stage="intent", model=MODEL, prompt_bytes=4000,
                           max_output_tokens=3000, **kw)


class TestProviderPassThrough:
    def test_anthropic_stored_and_default_gemini(self, db_session,
                                                 make_binding):
        binding, _ = make_binding()
        rid = _reserve(binding, provider="anthropic")
        assert _row(db_session, rid).provider == "anthropic"
        assert _row(db_session, _reserve(binding)).provider == "gemini"

    def test_invalid_provider_raises_and_no_row(self, db_session,
                                                make_binding):
        binding, _ = make_binding()
        with pytest.raises(LedgerBindError):
            _reserve(binding, provider="openai")
        assert db_session.query(AiCostReservation).count() == 0


class TestAnthropicUsageSettle:
    def test_actual_cost(self, db_session, make_binding):
        binding, _ = make_binding()
        rid = _reserve(binding, provider="anthropic")
        binding.settle(rid, model=MODEL, usage=_usage(1000, 500))
        row = _row(db_session, rid)
        assert row.state == STATE_SETTLED
        # 1000*0.14/1M + 500*0.28/1M
        assert row.actual_usd == Decimal("0.000280")

    def test_missing_candidates_charges_ceiling(self, db_session,
                                                make_binding):
        binding, _ = make_binding()
        rid = _reserve(binding, provider="anthropic")
        u = _usage()
        del u.candidates_token_count
        binding.settle(rid, model=MODEL, usage=u)
        assert _row(db_session, rid).state == STATE_CEILING

    def test_cache_creation_charges_ceiling(self, db_session, make_binding):
        binding, _ = make_binding()
        rid = _reserve(binding, provider="anthropic")
        binding.settle(rid, model=MODEL, usage=_usage(write=5))
        assert _row(db_session, rid).state == STATE_CEILING

    def test_bad_cache_field_charges_ceiling(self, db_session, make_binding):
        binding, _ = make_binding()
        rid = _reserve(binding, provider="anthropic")
        u = _usage()
        u.cache_read_token_count = "7"
        binding.settle(rid, model=MODEL, usage=u)
        assert _row(db_session, rid).state == STATE_CEILING

    def test_cache_read_priced_at_hit_rate(self, db_session, make_binding):
        binding, _ = make_binding()
        rid = _reserve(binding, provider="anthropic")
        binding.settle(rid, model=MODEL, usage=_usage(1000, 500, read=600))
        expected = (Decimal(400) * Decimal("0.14")
                    + Decimal(600) * Decimal("0.0028")
                    + Decimal(500) * Decimal("0.28")) / Decimal(1_000_000)
        row = _row(db_session, rid)
        assert row.state == STATE_SETTLED
        assert row.actual_usd == expected.quantize(Decimal("0.000001"))


class TestConcurrency:
    def test_cap_sized_for_five_of_eight(self, db_session, make_binding):
        # ceiling/reserve = 1M B * .14 + 1M tok * .28 = 0.42; cap 2.20 -> 5 fit
        binding, _ = make_binding(cap="2.20")
        ok, blocked, errors = [], [], []
        barrier = threading.Barrier(8)

        def work():
            # Rezervasyonlar settle edilmeden TUTULUR; aksi hâlde taahhüt
            # düşer ve 8'i de sırayla sığar (tavan testi anlamsızlaşır).
            try:
                barrier.wait()
                rid = binding.reserve(
                    stage="intent", model=MODEL, prompt_bytes=1_000_000,
                    max_output_tokens=1_000_000, provider="anthropic")
                ok.append(rid)
            except BudgetExceeded:
                blocked.append(1)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=work) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        assert len(ok) == 5 and len(blocked) == 3
        for rid in ok:
            binding.settle(rid, model=MODEL, usage=_usage(100, 100))
        db_session.expire_all()
        total = sum((r.actual_usd or 0) for r in
                    db_session.query(AiCostReservation).all())
        assert total <= Decimal("2.20")


class TestAnthropicSlotBinding:
    def test_anthropic_slot_bound_and_refusal_raises(self, make_binding):
        binding, attempt = make_binding()
        ledger = binding.ledger

        class Routed:
            def __init__(self, anth):
                self._default = _Backend()
                self._deepseek = None
                self._anthropic = anth

        anth = _Backend()
        b = attach_downstream_ledger(
            Routed(anth), ledger, scoring_run_id=attempt.scoring_run_id,
            attempt_id=attempt.id)
        assert anth.cost_binding is b

        class Refusing(_Backend):
            def __init__(self):
                pass

            def __setattr__(self, name, value):
                raise AttributeError(name)

        with pytest.raises(LedgerBindError):
            attach_downstream_ledger(
                Routed(Refusing()), ledger,
                scoring_run_id=attempt.scoring_run_id,
                attempt_id=attempt.id)
