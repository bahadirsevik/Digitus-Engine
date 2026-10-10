# -*- coding: utf-8 -*-
"""GERÇEK servis sarmalayıcısıyla ledger bağlama (Codex 22. tur #1).

Canlı denetimde yakalandı: bağlayıcı yalnız KÖK nesneye `cost_binding`
yazıyordu; üretimdeki `RoutedAIService` çağrıyı `_default` (GeminiService)
üzerinden yapıyor ve o backend BAĞLANMAMIŞ kalıyordu. Yani kod "bağlandı"
diyor, Gemini çağrıları rezervasyon AÇMIYORDU. Eski testler `FakeGemini`
kullandığı için bunu kaçırdı — bu dosya GERÇEK sınıfları kullanır.
"""
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app.core.telemetry.ai_cost_budget import AiCostLedger
from app.core.telemetry.downstream_ledger import (
    LedgerBindError,
    UnpricedModelError,
    _executing_backends,
    _usage_totals,
    attach_downstream_ledger,
)
from app.database.models import ChannelAssignmentAttempt


@pytest.fixture
def session_factory(db_session):
    return sa.orm.sessionmaker(bind=db_session.get_bind(),
                               expire_on_commit=False)


@pytest.fixture
def ledger(db_session, session_factory, make_workspace, make_scoring_run):
    ws = make_workspace("Real Bind WS")
    run = make_scoring_run(brand_profile_id=ws.id, status="scored")
    attempt = ChannelAssignmentAttempt(
        parent_task_id="real-bind", scoring_run_id=run.id,
        brand_profile_id=ws.id, status="running", phase="assignment",
        screening_mode="shadow", applied_candidate_multiplier=1,
        counterfactual_target_multiplier=3,
        approved_screening_cap_usd=Decimal("0.50"),
        approved_downstream_cap_usd=Decimal("2.00"))
    db_session.add(attempt)
    db_session.commit()
    return AiCostLedger(
        session_factory, attempt_id=attempt.id,
        expected_screening_cap_usd=Decimal("0.50"),
        expected_downstream_cap_usd=Decimal("2.00")), attempt


class TestRealRoutedServiceBinding:
    def test_gemini_backend_is_actually_bound(self, ledger):
        """ÜRETİM yolu: `get_ai_service()` -> RoutedAIService -> _default."""
        from app.generators.ai_service import RoutedAIService, get_ai_service

        cost_ledger, attempt = ledger
        ai = get_ai_service()
        assert isinstance(ai, RoutedAIService)
        binding = attach_downstream_ledger(
            ai, cost_ledger, scoring_run_id=attempt.scoring_run_id,
            attempt_id=attempt.id)
        # ASIL kanıt: gerçek çağrıyı yapan backend bağlı mı?
        assert ai._default.cost_binding is binding
        assert ai.cost_binding is binding
        for backend in _executing_backends(ai):
            assert backend.cost_binding is binding

    def test_lazily_created_deepseek_backend_is_bound(self, ledger):
        """Sonradan doğan backend de muhasebeye BAĞLI doğar."""
        from app.generators.ai_service import get_ai_service

        cost_ledger, attempt = ledger
        ai = get_ai_service()
        binding = attach_downstream_ledger(
            ai, cost_ledger, scoring_run_id=attempt.scoring_run_id,
            attempt_id=attempt.id)
        backend = ai._deepseek_backend()
        assert backend.cost_binding is binding

    def test_unbindable_service_fails_closed(self, ledger):
        """Provider çağrısı yapan backend YOKSA muhasebe kurulamaz."""
        cost_ledger, attempt = ledger

        class NotAService:
            pass

        with pytest.raises(LedgerBindError, match="backend bulunamadı"):
            attach_downstream_ledger(
                NotAService(), cost_ledger,
                scoring_run_id=attempt.scoring_run_id, attempt_id=attempt.id)

    def test_binding_refused_when_write_fails(self, ledger):
        """Yazma engellenirse SESSİZ geçilmez."""
        cost_ledger, attempt = ledger

        class Frozen:
            """Yazmayı ENGELLEYEN backend (donmuş nesne taklidi)."""

            def _execute(self):  # pragma: no cover - imza yeterli
                return ""

            def __setattr__(self, name, value):
                raise AttributeError(f"{name} yazilamaz")

        with pytest.raises(LedgerBindError):
            attach_downstream_ledger(
                Frozen(), cost_ledger,
                scoring_run_id=attempt.scoring_run_id, attempt_id=attempt.id)


class TestUnpricedModelFailsClosed:
    def test_unknown_model_raises_before_call(self, ledger):
        cost_ledger, attempt = ledger
        binding = attach_downstream_ledger(
            _Dummy(), cost_ledger, scoring_run_id=attempt.scoring_run_id,
            attempt_id=attempt.id)
        with pytest.raises(UnpricedModelError, match="fiyat tablosunda YOK"):
            binding.reserve(stage="intent", model="uydurma-model-x",
                            prompt_bytes=1000, max_output_tokens=3000)

    def test_known_model_reserves(self, ledger):
        from app.config import settings

        cost_ledger, attempt = ledger
        binding = attach_downstream_ledger(
            _Dummy(), cost_ledger, scoring_run_id=attempt.scoring_run_id,
            attempt_id=attempt.id)
        rid = binding.reserve(stage="intent", model=settings.GEMINI_MODEL,
                              prompt_bytes=1000, max_output_tokens=3000)
        assert isinstance(rid, int)


class TestStrictUsageValidation:
    """Kısmi/bozuk usage DÜŞÜK MALİYET yazamaz (Codex 22. tur #3)."""

    def test_missing_completion_is_invalid(self):
        usage = _U(prompt=1000, candidates=None)
        assert _usage_totals(usage) is None

    def test_missing_prompt_is_invalid(self):
        assert _usage_totals(_U(prompt=None, candidates=500)) is None

    def test_negative_is_invalid(self):
        assert _usage_totals(_U(prompt=-5, candidates=500)) is None

    def test_string_is_invalid(self):
        assert _usage_totals(_U(prompt="1000", candidates=500)) is None

    def test_bool_is_invalid(self):
        assert _usage_totals(_U(prompt=True, candidates=500)) is None

    def test_float_is_invalid(self):
        assert _usage_totals(_U(prompt=1000.5, candidates=500)) is None

    def test_zero_tokens_is_no_evidence(self):
        assert _usage_totals(_U(prompt=0, candidates=0)) is None

    def test_valid_usage_passes(self):
        totals = _usage_totals(_U(prompt=1000, candidates=500, thoughts=50))
        assert totals == {"prompt_tokens": 1000, "completion_tokens": 500,
                          "thoughts_tokens": 50}

    def test_missing_thoughts_defaults_to_zero(self):
        totals = _usage_totals(_U(prompt=1000, candidates=500, thoughts=None))
        assert totals["thoughts_tokens"] == 0

    def test_partial_usage_charges_ceiling(self, db_session, ledger):
        from app.core.telemetry.ai_cost_budget import STATE_CEILING
        from app.config import settings
        from app.database.models import AiCostReservation

        cost_ledger, attempt = ledger
        binding = attach_downstream_ledger(
            _Dummy(), cost_ledger, scoring_run_id=attempt.scoring_run_id,
            attempt_id=attempt.id)
        rid = binding.reserve(stage="intent", model=settings.GEMINI_MODEL,
                              prompt_bytes=1000, max_output_tokens=3000)
        # completion EKSİK: maliyet bilinmiyor -> TAVAN yakılır
        binding.settle(rid, model=settings.GEMINI_MODEL,
                       usage=_U(prompt=1000, candidates=None))
        db_session.expire_all()
        row = db_session.get(AiCostReservation, rid)
        assert row.state == STATE_CEILING


class _U:
    def __init__(self, prompt=None, candidates=None, thoughts=0):
        self.prompt_token_count = prompt
        self.candidates_token_count = candidates
        self.thoughts_token_count = thoughts


class _Dummy:
    """Provider çağrısı yapan minimal backend (bağlanabilir)."""

    def __init__(self):
        self.cost_binding = None

    def _execute(self):  # pragma: no cover - imza yeterli
        return ""
