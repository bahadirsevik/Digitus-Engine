# -*- coding: utf-8 -*-
"""UÇTAN UCA zincir: routed çağrı GERÇEKTEN rezervasyon satırı yazıyor mu?

Codex 23. tur #2: önceki hata tam olarak "attribute var ama rezervasyon
yok" sınıfındaydı; `cost_binding is binding` doğrulaması bunu yakalayamaz.
Bu dosya zinciri BAŞTAN SONA koşturur:

    RoutedAIService.complete_json(stage=...)
      -> GeminiService._execute
        -> ledger.reserve            (AiCostReservation satırı)
        -> (sahte) provider yanıtı   (usage_metadata ile)
        -> ledger.settle             (satır `settled`, actual_usd > 0)

Provider AĞA ÇIKMAZ: `client` sahte bir nesneyle değiştirilir.
"""
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app.core.telemetry.ai_cost_budget import (
    BUDGET_DOWNSTREAM,
    STATE_CEILING,
    STATE_SETTLED,
    AiCostLedger,
    BudgetExceeded,
)
from app.core.telemetry.downstream_ledger import (
    DownstreamRouteError,
    attach_downstream_ledger,
    require_ledgered_routes,
)
from app.database.models import AiCostReservation, ChannelAssignmentAttempt


class _Usage:
    prompt_token_count = 1200
    candidates_token_count = 400
    thoughts_token_count = 50
    total_token_count = 1650


class _Candidate:
    finish_reason = "STOP"

    def __init__(self, text):
        self.content = type("C", (), {"parts": [type("P", (), {"text": text})()]})()


class _Response:
    def __init__(self, text='{"ok": true}', usage=None):
        self.text = text
        self.candidates = [_Candidate(text)]
        self.usage_metadata = usage


class _FakeModels:
    """`client.models.generate_content` — ağa çıkmaz, çağrıları sayar."""

    def __init__(self, response):
        self.response = response
        self.calls = []

    def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model})
        return self.response


class _FakeClient:
    def __init__(self, response):
        self.models = _FakeModels(response)


@pytest.fixture
def session_factory(db_session):
    return sa.orm.sessionmaker(bind=db_session.get_bind(),
                               expire_on_commit=False)


@pytest.fixture
def bound(db_session, session_factory, make_workspace, make_scoring_run,
          monkeypatch):
    """Gerçek `RoutedAIService` + sahte client + bağlı ledger."""
    from app.generators.ai_service import GeminiService, get_ai_service

    ws = make_workspace("Routed WS")
    run = make_scoring_run(brand_profile_id=ws.id, status="scored")
    attempt = ChannelAssignmentAttempt(
        parent_task_id="routed-call", scoring_run_id=run.id,
        brand_profile_id=ws.id, status="running", phase="assignment",
        screening_mode="shadow", applied_candidate_multiplier=1,
        counterfactual_target_multiplier=3,
        approved_screening_cap_usd=Decimal("0.50"),
        approved_downstream_cap_usd=Decimal("2.00"))
    db_session.add(attempt)
    db_session.commit()

    fake = _FakeClient(_Response(usage=_Usage()))
    monkeypatch.setattr(GeminiService, "client",
                        property(lambda self: fake), raising=False)
    ai = get_ai_service()
    binding = attach_downstream_ledger(
        ai,
        AiCostLedger(session_factory, attempt_id=attempt.id,
                     expected_screening_cap_usd=Decimal("0.50"),
                     expected_downstream_cap_usd=Decimal("2.00")),
        scoring_run_id=run.id, attempt_id=attempt.id)
    return {"ai": ai, "attempt": attempt, "fake": fake, "binding": binding}


def _rows(db_session, attempt):
    db_session.expire_all()
    return (db_session.query(AiCostReservation)
            .filter_by(budget_owner_attempt_id=attempt.id,
                       budget_kind=BUDGET_DOWNSTREAM)
            .order_by(AiCostReservation.id).all())


class TestRoutedCallWritesLedgerRow:
    def test_real_chain_creates_settled_reservation(self, db_session, bound):
        """ASIL KANIT: routed çağrı sonunda `settled` satır VAR."""
        text = bound["ai"].for_stage("intent").complete_json(
            "intent prompt", max_tokens=3000)
        assert text                                   # yanıt döndü
        assert len(bound["fake"].models.calls) == 1   # provider çağrıldı
        rows = _rows(db_session, bound["attempt"])
        assert len(rows) == 1, "routed çağrı rezervasyon YAZMADI"
        row = rows[0]
        assert row.state == STATE_SETTLED
        assert row.stage == "intent"
        assert row.provider == "gemini"
        assert float(row.actual_usd) > 0
        assert float(row.actual_usd) <= float(row.ceiling_usd)

    def test_each_stage_gets_its_own_row(self, db_session, bound):
        for stage in ("intent", "brand_filter", "ads_prefilter"):
            bound["ai"].for_stage(stage).complete_json(
                f"{stage} prompt", max_tokens=3000)
        rows = _rows(db_session, bound["attempt"])
        assert len(rows) == 3
        assert {r.stage for r in rows} == {"intent", "brand_filter",
                                           "ads_prefilter"}

    def test_missing_usage_in_chain_charges_ceiling(self, db_session,
                                                    bound):
        bound["fake"].models.response = _Response(usage=None)
        bound["ai"].for_stage("intent").complete_json(
            "intent prompt", max_tokens=3000)
        rows = _rows(db_session, bound["attempt"])
        assert len(rows) == 1 and rows[0].state == STATE_CEILING

    def test_provider_error_in_chain_charges_ceiling(self, db_session, bound):
        def _boom(**kwargs):
            raise RuntimeError("transport patladı")

        bound["fake"].models.generate_content = _boom
        with pytest.raises(Exception):
            bound["ai"].for_stage("intent").complete_json(
                "intent prompt", max_tokens=3000)
        rows = _rows(db_session, bound["attempt"])
        assert rows and all(r.state == STATE_CEILING for r in rows)

    def test_cap_exhaustion_stops_the_chain(self, db_session,
                                            session_factory, bound):
        """Cap dolunca provider ÇAĞRILMAZ (para harcanmaz)."""
        attempt = bound["attempt"]
        attempt.approved_downstream_cap_usd = Decimal("0.000001")
        db_session.commit()
        ai = bound["ai"]
        attach_downstream_ledger(
            ai,
            AiCostLedger(session_factory, attempt_id=attempt.id,
                         expected_screening_cap_usd=Decimal("0.50"),
                         expected_downstream_cap_usd=Decimal("0.000001")),
            scoring_run_id=attempt.scoring_run_id, attempt_id=attempt.id)
        before = len(bound["fake"].models.calls)
        with pytest.raises(BudgetExceeded):
            ai.for_stage("intent").complete_json("intent prompt",
                                                 max_tokens=3000)
        assert len(bound["fake"].models.calls) == before   # ÇAĞRI YOK


class TestRouteGuard:
    """Codex 23. tur #1: atama aşamaları Gemini DIŞINA route edilemez."""

    def test_clean_routes_pass(self):
        require_ledgered_routes()          # varsayılan: route yok

    def test_deepseek_route_is_refused(self, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(
            settings, "AI_STAGE_ROUTES",
            {"intent": {"provider": "deepseek", "model": "deepseek-v4-flash"}})
        with pytest.raises(DownstreamRouteError, match="intent->deepseek"):
            require_ledgered_routes()

    def test_preflight_refuses_and_spends_nothing(self, db_session,
                                                  make_workspace,
                                                  make_scoring_run,
                                                  monkeypatch):
        """Route bozuksa preflight tipli reddeder — sağlayıcıya GİDİLMEZ."""
        from app.config import settings
        from app.core.screening.preflight import (
            PreflightError,
            build_assignment_preflight,
        )

        monkeypatch.setattr(settings, "ENABLE_CORPUS_SCREENING", True)
        monkeypatch.setattr(
            settings, "AI_STAGE_ROUTES",
            {"intent": {"provider": "deepseek", "model": "deepseek-v4-flash"}})
        ws = make_workspace("Route WS")
        run = make_scoring_run(brand_profile_id=ws.id, status="scored")
        with pytest.raises(PreflightError) as exc:
            build_assignment_preflight(db_session, run, ws, mode="shadow",
                                       settings=settings)
        assert exc.value.code == "DOWNSTREAM_ROUTE_NOT_LEDGERED"

    def test_assignment_task_refuses_before_spending(self, db_session,
                                                     make_workspace,
                                                     make_scoring_run,
                                                     monkeypatch):
        """Ledger bağlıyken bozuk route: koşu HARCAMADAN durur."""
        from app.config import settings
        from app.tasks import intent_tasks

        ws = make_workspace("Route Task WS")
        run = make_scoring_run(brand_profile_id=ws.id, status="scored")
        db_session.add(ChannelAssignmentAttempt(
            parent_task_id="route-task", scoring_run_id=run.id,
            brand_profile_id=ws.id, status="running", phase="assignment",
            screening_mode="shadow", applied_candidate_multiplier=1,
            counterfactual_target_multiplier=3,
            approved_screening_cap_usd=Decimal("0.50"),
            approved_downstream_cap_usd=Decimal("2.00")))
        db_session.commit()
        monkeypatch.setattr(
            settings, "AI_STAGE_ROUTES",
            {"intent": {"provider": "deepseek", "model": "deepseek-v4-flash"}})
        monkeypatch.setattr(
            "app.database.connection.SessionLocal",
            lambda: db_session.__class__(bind=db_session.get_bind()))
        called = []
        monkeypatch.setattr(
            "app.core.channel.channel_engine.ChannelEngine."
            "run_channel_assignment",
            lambda *a, **kw: called.append(1))
        out = intent_tasks.run_channel_assignment_task.apply(
            args=[run.id], task_id="route-task").get()
        assert out["status"] == "failed"
        assert out["reason"] == "LEDGER_BIND_FAILED"
        assert called == []          # pipeline HİÇ koşmadı, harcama YOK
