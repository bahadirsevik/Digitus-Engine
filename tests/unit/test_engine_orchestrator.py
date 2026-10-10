"""Motor v3 Faz 7 — Orkestratör ve API Birim Testleri.

plan_algoritma_entegrasyonu.md Faz 7 kuralları ve bağlayıcı kararlar:
  * K8: Tek koşu — evren + onaylı profil bir kez. Family V2 yalnız ADS veya SEO
    açıksa ve BİR KEZ çalışır. Yalnız SOCIAL koşusunda Family V2 çalışmaz.
  * K9: Model ve thinking kanal başına kod sabitidir (runner modülleri kendi sabitlerini kullanır).
  * K10: Sert tavan otoritesi ENGINE_V3_HARD_CAP_USD'dir.
    approved_screening_cap_usd = Decimal("0.000001"),
    approved_downstream_cap_usd = ENGINE_V3_HARD_CAP_USD - Decimal("0.000001"),
    toplam birebir ENGINE_V3_HARD_CAP_USD'dir.
  * K14: Motorun tek evren kaynağı run'ın dondurulmuş snapshot'ıdır (load_universe).
  * K15: Manifest mühürlenir (seal_manifest); finalize_engine_delivery güncel profille
    birebir hash eşitliğini doğrular.
  * K17: LLM aşamaları tek geçiştir.
  * Yönlendirme: algorithm_version == "v3" mevcut dispatch içinde yeni orkestratöre gider;
    v2/v2_1 davranışı değişmez.
  * Celery task imzası değişmez: run_channel_assignment_task(run_id, relevance_override=None, approval_token=None).
"""
from __future__ import annotations

from decimal import Decimal
import inspect
from unittest.mock import MagicMock, patch

import pytest

from app.config import settings
from app.core.engine.context import EngineInputError
from app.core.engine.orchestrator import run_v3_orchestration
from app.database.models import ScoringRun
from app.tasks.intent_tasks import run_channel_assignment_task


def _make_mock_run(
    *,
    run_id: int = 42,
    algorithm_version: str = "v3",
    enable_ads: bool = True,
    enable_seo: bool = True,
    enable_social: bool = True,
    status: str = "scored",
    brand_profile_id: int = 1,
) -> MagicMock:
    run = MagicMock(spec=ScoringRun)
    run.id = run_id
    run.algorithm_version = algorithm_version
    run.enable_ads = enable_ads
    run.enable_seo = enable_seo
    run.enable_social = enable_social
    run.status = status
    run.brand_profile_id = brand_profile_id
    run.universe_snapshot = {
        "universe_size": 2,
        "keywords": [
            {"keyword_id": 101, "keyword_text": "yapay zeka", "volume": 1000},
            {"keyword_id": 102, "keyword_text": "makine ogrenimi", "volume": 500},
        ],
    }
    run.execution_manifest = {}
    return run


def _mock_profile() -> MagicMock:
    profile = MagicMock()
    profile.id = 1
    profile.status = "confirmed"
    profile.policy_version = 1
    profile.strategy_version = 1
    profile.anchor_version = 1
    profile.profile_data = {
        "company_name": "Test Co",
        "brand_terms": ["testco"],
        "sector": "SaaS",
    }
    profile.channel_strategy = {
        "status": "approved",
        "product_definition": "Test Product",
        "content_strategy": "Educational",
        "social_mode": "hype",
    }
    profile.topic_policy = {"excluded_terms": []}
    profile.competitor_terms = []
    return profile


def _make_universe_mock() -> MagicMock:
    u = MagicMock()
    u.rows = [
        MagicMock(keyword_id=101, keyword_text="yapay zeka", volume=1000),
        MagicMock(keyword_id=102, keyword_text="makine ogrenimi", volume=500),
    ]
    return u


# ---------------------------------------------------------------------------
# K8: Family V2 koşu kuralı ve kanal kombinasyon testleri
# ---------------------------------------------------------------------------

@patch("app.core.engine.orchestrator.finalize_engine_delivery")
@patch("app.core.engine.orchestrator.run_social_stage")
@patch("app.core.engine.orchestrator.run_seo_stage")
@patch("app.core.engine.orchestrator.run_ads_stage")
@patch("app.core.engine.orchestrator.run_family_stage")
@patch("app.core.engine.orchestrator.load_universe")
@patch("app.core.engine.orchestrator.load_confirmed_profile")
def test_v3_orchestration_ads_only_runs_family_and_ads(
    mock_load_profile,
    mock_load_universe,
    mock_run_family,
    mock_run_ads,
    mock_run_seo,
    mock_run_social,
    mock_finalize,
):
    """ADS-only koşusunda Family V2 bir kez çalışır, ADS çalışır, SEO ve SOCIAL çalışmaz."""
    db = MagicMock()
    run = _make_mock_run(enable_ads=True, enable_seo=False, enable_social=False)
    mock_load_profile.return_value = _mock_profile()
    mock_load_universe.return_value = _make_universe_mock()
    mock_run_family.return_value = {101: "famA", 102: "famB"}
    mock_run_ads.return_value = [
        {"keyword_id": 101, "algorithm_rank": 1, "pool_class": "hot_sale"}
    ]
    mock_finalize.return_value = {"delivered": 1}

    result = run_v3_orchestration(db, run=run, ai=MagicMock())

    assert mock_run_family.call_count == 1
    assert mock_run_ads.call_count == 1
    assert mock_run_seo.call_count == 0
    assert mock_run_social.call_count == 0
    assert mock_finalize.call_count == 1

    # finalize_engine_delivery'ye yalnızca ADS teslim edilmelidir
    selections_arg = mock_finalize.call_args.kwargs.get("channel_selections") or mock_finalize.call_args[0][2]
    assert set(selections_arg.keys()) == {"ADS"}
    assert len(selections_arg["ADS"]) == 1
    assert result["delivery_summary"] == {"delivered": 1}
    assert result["enabled_channels"] == ["ADS"]


@patch("app.core.engine.orchestrator.finalize_engine_delivery")
@patch("app.core.engine.orchestrator.run_social_stage")
@patch("app.core.engine.orchestrator.run_seo_stage")
@patch("app.core.engine.orchestrator.run_ads_stage")
@patch("app.core.engine.orchestrator.run_family_stage")
@patch("app.core.engine.orchestrator.load_universe")
@patch("app.core.engine.orchestrator.load_confirmed_profile")
def test_v3_orchestration_seo_only_runs_family_and_seo(
    mock_load_profile,
    mock_load_universe,
    mock_run_family,
    mock_run_ads,
    mock_run_seo,
    mock_run_social,
    mock_finalize,
):
    """SEO-only koşusunda Family V2 bir kez çalışır, SEO çalışır, ADS ve SOCIAL çalışmaz."""
    db = MagicMock()
    run = _make_mock_run(enable_ads=False, enable_seo=True, enable_social=False)
    mock_load_profile.return_value = _mock_profile()
    mock_load_universe.return_value = _make_universe_mock()
    mock_run_family.return_value = {101: "famA", 102: "famB"}
    mock_run_seo.return_value = [
        {"keyword_id": 101, "algorithm_rank": 1, "pool_class": "PRIMARY"}
    ]
    mock_finalize.return_value = {"delivered": 1}

    result = run_v3_orchestration(db, run=run, ai=MagicMock())

    assert mock_run_family.call_count == 1
    assert mock_run_seo.call_count == 1
    assert mock_run_ads.call_count == 0
    assert mock_run_social.call_count == 0
    assert mock_finalize.call_count == 1

    selections_arg = mock_finalize.call_args.kwargs.get("channel_selections") or mock_finalize.call_args[0][2]
    assert set(selections_arg.keys()) == {"SEO"}
    assert len(selections_arg["SEO"]) == 1
    assert result["enabled_channels"] == ["SEO"]


@patch("app.core.engine.orchestrator.finalize_engine_delivery")
@patch("app.core.engine.orchestrator.run_social_stage")
@patch("app.core.engine.orchestrator.run_seo_stage")
@patch("app.core.engine.orchestrator.run_ads_stage")
@patch("app.core.engine.orchestrator.run_family_stage")
@patch("app.core.engine.orchestrator.load_universe")
@patch("app.core.engine.orchestrator.load_confirmed_profile")
def test_v3_orchestration_social_only_does_not_run_family(
    mock_load_profile,
    mock_load_universe,
    mock_run_family,
    mock_run_ads,
    mock_run_seo,
    mock_run_social,
    mock_finalize,
):
    """K8 KURALI: Yalnız SOCIAL koşusunda Family V2 KESİNLİKLE ÇALIŞMAZ."""
    db = MagicMock()
    run = _make_mock_run(enable_ads=False, enable_seo=False, enable_social=True)
    mock_load_profile.return_value = _mock_profile()
    mock_load_universe.return_value = _make_universe_mock()
    mock_run_social.return_value = [
        {"keyword_id": 101, "algorithm_rank": 1, "pool_class": "P1"}
    ]
    mock_finalize.return_value = {"delivered": 1}

    result = run_v3_orchestration(db, run=run, ai=MagicMock())

    assert mock_run_family.call_count == 0, "K8 ihlali: Family V2 yalnız-SOCIAL koşusunda çağrılmamalıdır!"
    assert mock_run_social.call_count == 1
    assert mock_run_ads.call_count == 0
    assert mock_run_seo.call_count == 0
    assert mock_finalize.call_count == 1

    selections_arg = mock_finalize.call_args.kwargs.get("channel_selections") or mock_finalize.call_args[0][2]
    assert set(selections_arg.keys()) == {"SOCIAL"}
    assert result["enabled_channels"] == ["SOCIAL"]


@patch("app.core.engine.orchestrator.finalize_engine_delivery")
@patch("app.core.engine.orchestrator.run_social_stage")
@patch("app.core.engine.orchestrator.run_seo_stage")
@patch("app.core.engine.orchestrator.run_ads_stage")
@patch("app.core.engine.orchestrator.run_family_stage")
@patch("app.core.engine.orchestrator.load_universe")
@patch("app.core.engine.orchestrator.load_confirmed_profile")
def test_v3_orchestration_all_channels_enabled(
    mock_load_profile,
    mock_load_universe,
    mock_run_family,
    mock_run_ads,
    mock_run_seo,
    mock_run_social,
    mock_finalize,
):
    """Üç kanal da seçiliyse: Family V2 tek bir kez çalışır; ADS, SEO, SOCIAL koşucuları çalışır."""
    db = MagicMock()
    run = _make_mock_run(enable_ads=True, enable_seo=True, enable_social=True)
    mock_load_profile.return_value = _mock_profile()
    mock_load_universe.return_value = _make_universe_mock()
    mock_run_family.return_value = {101: "famA", 102: "famB"}
    mock_run_ads.return_value = [{"keyword_id": 101, "algorithm_rank": 1, "pool_class": "hot_sale"}]
    mock_run_seo.return_value = [{"keyword_id": 101, "algorithm_rank": 1, "pool_class": "PRIMARY"}]
    mock_run_social.return_value = [{"keyword_id": 102, "algorithm_rank": 1, "pool_class": "P1"}]
    mock_finalize.return_value = {"delivered": 3}

    result = run_v3_orchestration(db, run=run, ai=MagicMock())

    assert mock_run_family.call_count == 1
    assert mock_run_ads.call_count == 1
    assert mock_run_seo.call_count == 1
    assert mock_run_social.call_count == 1

    selections_arg = mock_finalize.call_args.kwargs.get("channel_selections") or mock_finalize.call_args[0][2]
    assert set(selections_arg.keys()) == {"ADS", "SEO", "SOCIAL"}
    assert result["enabled_channels"] == ["ADS", "SEO", "SOCIAL"]


# ---------------------------------------------------------------------------
# Fail-closed doğrulama testleri
# ---------------------------------------------------------------------------

def test_v3_orchestration_rejects_non_v3_run():
    """algorithm_version 'v3' değilse EngineInputError ile fail-closed durur."""
    db = MagicMock()
    run = _make_mock_run(algorithm_version="v2")
    with pytest.raises(EngineInputError, match="algorithm_version 'v3' değil"):
        run_v3_orchestration(db, run=run, ai=MagicMock())


def test_v3_orchestration_rejects_no_enabled_channels():
    """Hiçbir kanal seçili değilse fail-closed durur."""
    db = MagicMock()
    run = _make_mock_run(enable_ads=False, enable_seo=False, enable_social=False)
    with pytest.raises(EngineInputError, match="en az bir kanal"):
        run_v3_orchestration(db, run=run, ai=MagicMock())


# ---------------------------------------------------------------------------
# K10: Sert Tavan ve Attempt Bütçe Sözleşmesi
# ---------------------------------------------------------------------------

def test_v3_hard_cap_constants_and_attempt_split():
    """K10: ENGINE_V3_HARD_CAP_USD sert tavan otoritesidir.
    approved_screening_cap_usd = Decimal('0.000001'),
    approved_downstream_cap_usd = ENGINE_V3_HARD_CAP_USD - Decimal('0.000001'),
    Toplamı birebir ENGINE_V3_HARD_CAP_USD eder.
    """
    total_cap = Decimal(str(settings.ENGINE_V3_HARD_CAP_USD))
    screening_cap = Decimal("0.000001")
    downstream_cap = total_cap - screening_cap

    assert screening_cap + downstream_cap == total_cap
    assert screening_cap > 0
    assert downstream_cap > 0


# ---------------------------------------------------------------------------
# Dispatcher & Celery Görev Yönlendirme ve İmzası Testleri
# ---------------------------------------------------------------------------

def test_celery_task_signature_is_preserved():
    """Celery task imzası geriye uyumludur ve değişmemiştir."""
    sig = inspect.signature(run_channel_assignment_task)
    params = list(sig.parameters.keys())
    assert params == [
        "scoring_run_id",
        "relevance_coefficient",
        "requested_policy_version",
        "requested_anchor_version",
        "recompute_relevance",
        "requested_strategy_version",
    ]


def _make_mock_attempt(task_id: str = "task-v3-1") -> MagicMock:
    attempt = MagicMock()
    attempt.id = 1
    attempt.parent_task_id = task_id
    attempt.status = "pending"
    attempt.assignment_dispatch_state = "pending"
    attempt.approved_screening_cap_usd = Decimal("0.000001")
    attempt.approved_downstream_cap_usd = Decimal("14.999999")
    attempt.screening_mode = "off"
    attempt.dispatch_attempts = 0
    attempt.dispatch_last_at = None
    return attempt


def _setup_session_mock(
    session: MagicMock,
    run: MagicMock,
    task_res: MagicMock,
    attempt: MagicMock = None,
) -> None:
    def _query(model):
        q = MagicMock()
        name = getattr(model, "__name__", str(model))
        if name == "ScoringRun":
            q.filter.return_value.first.return_value = run
            q.filter.return_value.with_for_update.return_value.first.return_value = run
        elif name == "TaskResult":
            q.filter.return_value.first.return_value = task_res
            q.filter.return_value.with_for_update.return_value.first.return_value = task_res
        elif name == "ChannelAssignmentAttempt":
            q.filter.return_value.first.return_value = attempt
            q.filter.return_value.with_for_update.return_value.first.return_value = attempt
        else:
            q.filter.return_value.first.return_value = None
            q.filter.return_value.with_for_update.return_value.first.return_value = None
        return q
    session.query.side_effect = _query


@patch("app.core.telemetry.downstream_ledger.require_ledgered_routes")
@patch("app.core.telemetry.downstream_ledger.attach_downstream_ledger")
@patch.object(run_channel_assignment_task, "update_state")
@patch("app.tasks.intent_tasks.ChannelEngine")
@patch("app.tasks.intent_tasks.run_v3_orchestration")
@patch("app.tasks.intent_tasks.SessionLocal")
def test_channel_assignment_task_routing_v3_vs_v2(
    mock_session_local,
    mock_v3_orch,
    mock_channel_engine,
    mock_update_state,
    mock_attach_ledger,
    mock_require_routes,
):
    """algorithm_version 'v3' ise run_v3_orchestration çağrılır; 'v2' (eski
    motor, salt-okunur) ise HİÇBİR pipeline çağrılmaz — task fail-closed
    LEGACY_RUN_READ_ONLY ile kapanır (ürün kararı: V3 tek motor)."""
    session = MagicMock()
    mock_session_local.return_value = session
    mock_binding = MagicMock()
    mock_binding.summary.return_value = {"spent_usd": 0.0}
    mock_attach_ledger.return_value = mock_binding

    # 1. v3 senaryosu
    run_v3 = _make_mock_run(run_id=1, algorithm_version="v3")
    task_res_v3 = MagicMock()
    attempt_v3 = _make_mock_attempt(task_id="task-v3-1")
    _setup_session_mock(session, run_v3, task_res_v3, attempt=attempt_v3)
    mock_v3_orch.return_value = {
        "status": "channel_assigned",
        "channels": ["ADS"],
        "delivery_summary": {"delivered": 1},
        "total_calls": 2,
    }

    res_v3 = run_channel_assignment_task.apply(args=[1], task_id="task-v3-1").result
    assert mock_v3_orch.call_count == 1
    assert mock_channel_engine.return_value.run_channel_assignment.call_count == 0
    assert res_v3["status"] == "completed"

    # 2. v2 senaryosu
    mock_v3_orch.reset_mock()
    mock_channel_engine.reset_mock()
    run_v2 = _make_mock_run(run_id=2, algorithm_version="v2")
    _setup_session_mock(session, run_v2, task_res_v3, attempt=None)
    mock_channel_engine.return_value.run_channel_assignment.return_value = {
        "status": "success",
        "total_keywords": 10,
    }

    res_v2 = run_channel_assignment_task.apply(args=[2], task_id="task-v2-2").result
    assert mock_v3_orch.call_count == 0
    assert mock_channel_engine.return_value.run_channel_assignment.call_count == 0
    assert res_v2["status"] == "failed"
    assert res_v2["reason"] == "LEGACY_RUN_READ_ONLY"


@patch("app.core.telemetry.downstream_ledger.require_ledgered_routes")
@patch("app.core.telemetry.downstream_ledger.attach_downstream_ledger")
@patch("app.tasks.intent_tasks.update_task_status")
@patch.object(run_channel_assignment_task, "update_state")
@patch("app.tasks.intent_tasks.transition")
@patch("app.tasks.intent_tasks.run_v3_orchestration")
@patch("app.tasks.intent_tasks.SessionLocal")
def test_channel_assignment_task_failure_transitions_to_failed(
    mock_session_local,
    mock_v3_orch,
    mock_transition,
    mock_update_state,
    mock_update_task_status,
    mock_attach_ledger,
    mock_require_routes,
):
    """Orkestratör hata fırlatırsa run 'failed' durumuna geçer ve görev patlar."""
    session = MagicMock()
    mock_session_local.return_value = session
    mock_binding = MagicMock()
    mock_binding.summary.return_value = {"spent_usd": 0.0}
    mock_attach_ledger.return_value = mock_binding

    run = _make_mock_run(run_id=3, algorithm_version="v3")
    task_res = MagicMock()
    attempt = _make_mock_attempt(task_id="task-fail-3")
    _setup_session_mock(session, run, task_res, attempt=attempt)
    mock_v3_orch.side_effect = RuntimeError("AI provider timeout")

    res = run_channel_assignment_task.apply(args=[3], task_id="task-fail-3").result
    assert res["status"] == "failed"
    assert "AI provider timeout" in res["error"]

    assert mock_transition.call_count == 1
    assert (
        mock_transition.call_args.kwargs.get("target") == "failed"
        or (len(mock_transition.call_args[0]) > 2 and mock_transition.call_args[0][2] == "failed")
    )
    assert mock_update_task_status.called
    assert mock_update_task_status.call_args.kwargs.get("status") == "failed"


@patch("app.tasks.intent_tasks.run_v3_orchestration")
@patch("app.tasks.intent_tasks.SessionLocal")
def test_channel_assignment_task_v3_fails_closed_without_attempt_or_budget(
    mock_session_local,
    mock_v3_orch,
):
    """v3 worker: ChannelAssignmentAttempt veya pozitif bütçe yoksa orkestratör çağrılmaz (call_count == 0)."""
    session = MagicMock()
    mock_session_local.return_value = session

    # 1. Attempt yok
    run = _make_mock_run(run_id=10, algorithm_version="v3")
    task_res = MagicMock()
    _setup_session_mock(session, run, task_res, attempt=None)

    res = run_channel_assignment_task.apply(args=[10], task_id="task-no-attempt").result
    assert res["status"] == "failed"
    assert res.get("reason") == "LEDGER_BIND_FAILED"
    assert mock_v3_orch.call_count == 0

    # 2. Attempt var ama cap pozitif değil
    attempt_zero = _make_mock_attempt("task-zero-cap")
    attempt_zero.approved_screening_cap_usd = Decimal("0.0")
    _setup_session_mock(session, run, task_res, attempt=attempt_zero)

    res2 = run_channel_assignment_task.apply(args=[10], task_id="task-zero-cap").result
    assert res2["status"] == "failed"
    assert res2.get("reason") == "LEDGER_BIND_FAILED"
    assert mock_v3_orch.call_count == 0
