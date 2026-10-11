"""Faz 1 / 1B hibrit koşucu — DB'siz, ağsız, ücretsiz (Codex kabul ölçütleri)."""
import copy
from types import SimpleNamespace

import pytest

import app.core.benchmark.hybrid_run as hr
import app.generators.ai_service as ai_mod
from app.config import settings
from app.core.benchmark.claude_bakeoff_plan import (
    CONTENT_STAGES, O_STAGES, Y_STAGES, faz1_blockers,
)
from app.core.benchmark.reference_characterization import (
    CharacterizationError, seal,
)
from app.core.constants import AI_STAGES
from app.core.telemetry.usage import UsageCollector
from app.generators.ai_service import AIResponseError


@pytest.fixture(scope="module")
def sealed_plan():
    return hr.load_plan()  # benchmark/claude_bakeoff_plan.json (verify_seal'li)


def _names(plan):
    """Planin 1B firma adlari (test, canli plandaki adlara BAGLI KALMAZ)."""
    n = list(plan["workspaces"]["1B"])
    assert len(n) >= 2
    return n


@pytest.fixture
def open_plan(sealed_plan):
    """Bloklayıcıları kapatılmış YENİDEN MÜHÜRLÜ kopya (gerçek plana dokunmaz)."""
    p = copy.deepcopy(sealed_plan)
    p["decisions"]["K12_document_reference"] = "belge-ref"
    p["spike"]["console_reconciled"] = True
    p["credit"] = {**p.get("credit", {}), "net_available_usd": 80.0}
    n = _names(p)
    p["workspaces"]["ids"] = {n[0]: 21, n[1]: 19}
    p = seal(p)
    assert faz1_blockers(p) == []
    return p


@pytest.fixture
def blocked_plan(sealed_plan):
    """Bloklayicilari ACILMIS kopya: K12/konsol/kredi/ws id bos (canli plan acik olsa da)."""
    p = copy.deepcopy(sealed_plan)
    p["decisions"]["K12_document_reference"] = None
    p["spike"]["console_reconciled"] = False
    p["credit"] = {**p.get("credit", {}), "net_available_usd": None}
    p["workspaces"]["ids"] = None
    p = seal(p)
    assert faz1_blockers(p)
    return p


class FakeRoot:
    def __init__(self):
        self.calls, self.collector, self.cost_binding = [], None, None

    def for_stage(self, stage, **kw):
        self.calls.append((stage, kw))
        return SimpleNamespace(stage=stage, **kw)


# -- StageRoutePinnedAI ------------------------------------------------------

def test_pinned_injects_provider_and_model_for_routed_stage():
    root = FakeRoot()
    pinned = hr.StageRoutePinnedAI(
        root, {"seo_generation": {"provider": "anthropic", "model": "m-x"}})
    s = pinned.for_stage("seo_generation")
    assert (s.provider, s.model) == ("anthropic", "m-x")
    # motor koşucusu mühürlü AYNI değeri geçerse kabul
    pinned.for_stage("seo_generation", provider="anthropic", model="m-x",
                     thinking_level="low")
    assert root.calls[-1][1]["thinking_level"] == "low"


def test_pinned_raises_on_conflicting_caller_route():
    pinned = hr.StageRoutePinnedAI(
        FakeRoot(), {"ads_rsa": {"provider": "anthropic", "model": "m-x"}})
    with pytest.raises(AIResponseError):
        pinned.for_stage("ads_rsa", model="baska-model")
    with pytest.raises(AIResponseError):
        pinned.for_stage("ads_rsa", provider="gemini")
    with pytest.raises(AIResponseError):  # rotasız stage Gemini'de kalır
        pinned.for_stage("intent", provider="anthropic")


def test_pinned_unrouted_stage_resolved_as_gemini(monkeypatch):
    monkeypatch.setattr(settings, "AI_STAGE_MODELS", {"micro_shorten": "gm-1"})
    root = FakeRoot()
    pinned = hr.StageRoutePinnedAI(root, {})
    assert pinned.for_stage("micro_shorten").model == "gm-1"
    s = pinned.for_stage("geo_compliance")
    assert (s.provider, s.model) == ("gemini", settings.GEMINI_MODEL)
    # çağıranın (motor) mühürlü Gemini modeli korunur
    sealed = pinned.for_stage("intent", provider="gemini", model="g-seal")
    assert sealed.model == "g-seal"


def test_pinned_collector_passthrough():
    root = FakeRoot()
    pinned = hr.StageRoutePinnedAI(root, {})
    pinned.collector = "c"
    assert root.collector == "c" and pinned.collector == "c"


# -- CONFIGS -----------------------------------------------------------------

def test_configs_derived_from_sealed_plan(sealed_plan):
    cfg = hr.build_configs(sealed_plan)
    k3 = sealed_plan["decisions"]["K3_models"]
    assert cfg["G/G/G"] == {}
    assert set(cfg["G/G/C"]) == set(CONTENT_STAGES)
    assert set(cfg["Aday"]) == (
        set(Y_STAGES) | set(O_STAGES) | set(CONTENT_STAGES))
    assert {cfg["Aday"][s]["model"] for s in Y_STAGES} == {k3["Y"]}
    assert {cfg["Aday"][s]["model"] for s in O_STAGES} == {k3["O"]}
    assert {cfg["Aday"][s]["model"] for s in CONTENT_STAGES} == {k3["content"]}
    assert cfg["G/G/C"] == {s: cfg["Aday"][s] for s in CONTENT_STAGES}
    assert all(r["provider"] == "anthropic" for c in cfg.values()
               for r in c.values())
    # RoutedAIService bilinmeyen stage anahtarını fail-closed reddeder
    assert set(cfg["Aday"]) <= set(AI_STAGES)
    assert hr.CONFIG_NAMES == tuple(cfg)


def test_config_models_not_hardcoded(sealed_plan):
    p = copy.deepcopy(sealed_plan)
    p["decisions"]["K3_models"]["Y"] = "ozel-y-model"
    assert hr.build_configs(p)["Aday"][Y_STAGES[0]]["model"] == "ozel-y-model"


# -- build_benchmark_ai: retry=1 + per-provider cap --------------------------

class _Recorder:
    kwargs = None

    def __init__(self, **kwargs):
        type(self).kwargs = kwargs


def test_benchmark_ai_uses_gemini_retry_attempts_one(monkeypatch, sealed_plan):
    monkeypatch.setattr(ai_mod.genai, "Client", _Recorder)
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    ai = hr.build_benchmark_ai("Aday", sealed_plan, collector=object())
    ai._pinned._root._default.client  # lazy client burada kurulur
    assert _Recorder.kwargs["http_options"].retry_options.attempts == 1


def test_per_provider_caps_from_plan_and_only_downward(sealed_plan, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    ai = hr.build_benchmark_ai("Aday", sealed_plan, collector=object())
    s = ai.summary()
    assert {k: v["max_cost_usd"] for k, v in s.items()} == {
        "anthropic": 50.0, "gemini": 20.0, "deepseek": 5.0}
    low = hr.build_benchmark_ai("Aday", sealed_plan, collector=object(),
                                caps_usd={"claude": 10.0, "gemini": 1.0})
    assert low.summary()["anthropic"]["max_cost_usd"] == 10.0
    assert low.summary()["gemini"]["max_cost_usd"] == 1.0
    with pytest.raises(ValueError):
        hr.build_benchmark_ai("Aday", sealed_plan, collector=object(),
                              caps_usd={"claude": 60.0})
    with pytest.raises(ValueError):
        hr.build_benchmark_ai("nope", sealed_plan, collector=object())


def test_caps_enforced_before_any_network_call(sealed_plan, monkeypatch):
    """Tavan çok düşükse rezervasyon çağrıdan ÖNCE reddeder (ağ yok)."""
    monkeypatch.setenv("GEMINI_API_KEY", "k")

    def no_client(**kw):
        pytest.fail("Gemini istemcisi kurulmamalıydı")

    monkeypatch.setattr(ai_mod.genai, "Client", no_client)
    ai = hr.build_benchmark_ai("Aday", sealed_plan,
                               collector=UsageCollector(flush_every=10**9),
                               caps_usd={"claude": 1e-6, "gemini": 1e-6})
    with pytest.raises(CharacterizationError):  # Sonnet (anthropic) rotası
        ai.for_stage("seo_generation").complete_json("kelime")
    with pytest.raises(CharacterizationError):  # Gemini rotası
        ai.for_stage("geo_compliance").complete_json("kelime")


# -- run_hybrid: blokaj / dry-run / ön kontroller ----------------------------

def _no_ai(*a, **k):
    pytest.fail("AI kurulmamalıydı")


def test_blocked_plan_stops_before_anything(blocked_plan, monkeypatch):
    monkeypatch.setattr(hr, "build_benchmark_ai", _no_ai)
    before = settings.AI_STAGE_ROUTES
    for dry in (False, True):
        with pytest.raises(hr.Faz1Blocked) as ei:
            hr.run_hybrid(None, workspace_id=21, config_name="Aday",
                          plan=blocked_plan, collector=None, dry_run=dry)
        assert ei.value.blockers == faz1_blockers(blocked_plan)
    assert settings.AI_STAGE_ROUTES == before


def test_tampered_plan_is_rejected(sealed_plan):
    p = copy.deepcopy(sealed_plan)
    p["decisions"]["K7_caps_usd"]["claude"] = 500.0
    with pytest.raises(CharacterizationError):
        hr.run_hybrid(None, workspace_id=1, config_name="Aday", plan=p,
                      collector=None, dry_run=True)


def test_dry_run_returns_work_plan_without_ai(open_plan, monkeypatch):
    monkeypatch.setattr(hr, "build_benchmark_ai", _no_ai)
    monkeypatch.setattr(hr, "GeminiService", _no_ai)
    out = hr.run_hybrid(SimpleNamespace(), workspace_id=21, config_name="Aday",
                        plan=open_plan, collector=None, dry_run=True,
                        content=("ads", "seo"))
    assert out["kind"] == "hybrid_dry_run" and out["ai_calls"] == 0
    assert out["routes"] == hr.build_configs(open_plan)["Aday"]
    assert out["pool_sizes"] == "n/a"
    assert out["caps_usd"] == {"anthropic": 50.0, "gemini": 20.0,
                               "deepseek": 5.0}
    assert [s for s in out["steps"] if s.startswith("content:")] == [
        "content:ads", "content:seo"]
    with pytest.raises(ValueError):
        hr.run_hybrid(SimpleNamespace(), workspace_id=21, config_name="X",
                      plan=open_plan, collector=None, dry_run=True)
    with pytest.raises(ValueError):
        hr.run_hybrid(SimpleNamespace(), workspace_id=21, config_name="Aday",
                      plan=open_plan, collector=None, dry_run=True,
                      content=("blog",))


def test_paid_run_prechecks_fail_before_ai_is_built(open_plan, monkeypatch):
    monkeypatch.setattr(hr, "build_benchmark_ai", _no_ai)
    with pytest.raises(ValueError):  # kapasite zorunlu (adalet)
        hr.run_hybrid(None, workspace_id=21, config_name="Aday",
                      plan=open_plan, collector=None)
    monkeypatch.setattr(settings, "ENABLE_SOCIAL_BRIEF_FLOW", False)
    with pytest.raises(RuntimeError):  # sosyal bayrağı kapalı: ücretten önce dur
        hr.run_hybrid(None, workspace_id=21, config_name="Aday",
                      plan=open_plan, collector=None,
                      capacities={"ADS": 5, "SEO": 5, "SOCIAL": 5})


# -- routes_override ---------------------------------------------------------

def test_routes_override_restores_even_on_exception(monkeypatch):
    monkeypatch.setattr(settings, "AI_STAGE_ROUTES", {"intent": {"x": 1}})
    routes = {"ads_rsa": {"provider": "anthropic", "model": "m"}}
    with hr.routes_override(routes):
        assert settings.AI_STAGE_ROUTES == routes
    assert settings.AI_STAGE_ROUTES == {"intent": {"x": 1}}
    with pytest.raises(RuntimeError):
        with hr.routes_override(routes):
            raise RuntimeError("boom")
    assert settings.AI_STAGE_ROUTES == {"intent": {"x": 1}}


def test_run_hybrid_rejects_unapproved_workspace_before_db(open_plan):
    """Codex Faz 1 #3: onayli id listesi dolu ama hedef listede degil -> DB/AI yok."""
    from app.core.benchmark.claude_bakeoff_plan import WorkspaceNotApproved
    from app.core.benchmark.reference_characterization import seal
    n = _names(open_plan)
    p = dict(open_plan)
    p["workspaces"] = {**open_plan["workspaces"], "ids": {n[0]: 30, n[1]: 21}}
    p = seal(p)
    with pytest.raises(WorkspaceNotApproved):
        hr.run_hybrid(None, workspace_id=99, config_name="Aday", plan=p,
                      collector=object(), dry_run=True)
    # onayli hedef ayni cagriyla gecer (dry-run, AI yok)
    out = hr.run_hybrid(None, workspace_id=30, config_name="Aday", plan=p,
                        collector=object(), dry_run=True)
    assert out["kind"] == "hybrid_dry_run"


def test_build_benchmark_ai_uses_ledger_remaining(sealed_plan, monkeypatch, tmp_path):
    """Codex Faz 1 #1: ikinci komut onceki harcamayi devralir; tukenmis tavan -> hata."""
    import app.generators.ai_service as mod
    from app.core.benchmark.faz1_ledger import Faz1Ledger
    from app.core.benchmark.reference_characterization import CharacterizationError

    class _C:
        def __init__(self, **kw): pass
    monkeypatch.setattr(mod.genai, "Client", _C)
    led = Faz1Ledger.open(tmp_path / "l.json")
    led.record(kind="hybrid_run", ref="ws21:Aday",
               budget_summary={"anthropic": {"cost_committed_usd": 45.0},
                               "gemini": {"cost_committed_usd": 19.5}})
    ai = hr.build_benchmark_ai("Aday", sealed_plan, collector=object(), ledger=led)
    s = ai.summary()
    assert s["anthropic"]["max_cost_usd"] == pytest.approx(5.0)
    assert s["gemini"]["max_cost_usd"] == pytest.approx(0.5)
    led.record(kind="hybrid_run", ref="ws30:Aday",
               budget_summary={"anthropic": {"cost_committed_usd": 5.0}})
    with pytest.raises(CharacterizationError):
        hr.build_benchmark_ai("Aday", sealed_plan, collector=object(), ledger=led)
