"""Motor v3 Faz 7 — Orkestratör ve Teslimat Entegrasyon Testleri.

plan_algoritma_entegrasyonu.md Faz 7 kuralları ve bağlayıcı kararlar:
  * K8: Tek koşu — evren + onaylı profil bir kez. Family V2 yalnız ADS veya SEO
    açıksa ve BİR KEZ çalışır. Yalnız SOCIAL koşusunda Family V2 çalışmaz.
  * K10: Sert tavan otoritesi ENGINE_V3_HARD_CAP_USD'dir.
  * K14: Motorun tek evren kaynağı run'ın dondurulmuş snapshot'ıdır (load_universe).
  * K15: Manifest mühürlenir (seal_manifest); finalize_engine_delivery güncel profille
    birebir hash eşitliğini doğrular.
  * K17: LLM aşamaları tek geçiştir.
  * Atomiklik: Hata durumunda hiçbir kısmi EngineSelection veya ChannelPool yazılmaz.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch
import pytest

from app.core.channel.channel_engine import ChannelEngine
from app.core.engine.context import (
    EngineInputError,
    build_firm_profile,
    firm_block,
    firm_block_sha256,
    freeze_universe_snapshot,
)
from app.core.engine.orchestrator import run_v3_orchestration
from app.core.engine.persistence import (
    load_channel_pools,
    load_engine_selections,
)
from app.database.models import ChannelPool, EngineSelection, TaskResult
from app.tasks.intent_tasks import run_channel_assignment_task


def _setup_confirmed_workspace(make_workspace):
    ws = make_workspace()
    ws.status = "confirmed"
    ws.policy_version = 10
    ws.strategy_version = 5
    ws.anchor_version = 7
    ws.channel_strategy = {
        "status": "approved",
        "product_definition": "Otonom AI Platformu",
        "content_strategy": "Teknik rehber ve icerikler",
        "social_mode": "hype",
    }
    ws.profile_data = {
        "company_name": "Antigravity Tech",
        "brand_terms": ["antigravity"],
        "sector": "Yapay Zeka",
    }
    ws.topic_policy = {
        "excluded_terms": [
            {"term": "bahis", "status": "approved"},
        ],
    }
    ws.competitor_terms = [
        {"term": "rakipai", "status": "approved"},
    ]
    ws.competitor_policy = {"ads": "block", "seo": "allow", "social": "block"}
    return ws


def test_v3_orchestration_end_to_end_delivery(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """v3 tek koşusu: snapshot -> runners -> post-policy gate -> ChannelPool teslimatı."""
    ws = _setup_confirmed_workspace(make_workspace)
    run = make_scoring_run(brand_profile_id=ws.id)
    run.algorithm_version = "v3"
    run.status = "scored"
    run.enable_ads = True
    run.enable_seo = True
    run.enable_social = True
    run.ads_capacity = 10
    run.seo_capacity = 10
    run.social_capacity = 10

    kw1 = make_keyword("antigravity yapay zeka", brand_profile_id=ws.id)
    kw2 = make_keyword("akilli agent rehberi", brand_profile_id=ws.id)
    kw3 = make_keyword("otonom kodlama trendleri", brand_profile_id=ws.id)

    db_session.commit()
    freeze_universe_snapshot(db_session, run)
    db_session.commit()

    # Runners mock: her runner geçerli aday listesi döner
    mock_family = {kw1.id: "fam1", kw2.id: "fam2", kw3.id: "fam3"}
    mock_ads_candidates = [
        {"keyword_id": kw1.id, "algorithm_rank": 1, "pool_class": "hot_sale"}
    ]
    mock_seo_candidates = [
        {"keyword_id": kw2.id, "algorithm_rank": 1, "pool_class": "primary"}
    ]
    mock_social_candidates = [
        {"keyword_id": kw3.id, "algorithm_rank": 1, "pool_class": "P1"}
    ]

    with patch("app.core.engine.orchestrator.run_family_stage", return_value=mock_family) as p_fam, \
         patch("app.core.engine.orchestrator.run_ads_stage", return_value=mock_ads_candidates) as p_ads, \
         patch("app.core.engine.orchestrator.run_seo_stage", return_value=mock_seo_candidates) as p_seo, \
         patch("app.core.engine.orchestrator.run_social_stage", return_value=mock_social_candidates) as p_soc:

        res = run_v3_orchestration(db_session, run=run, ai=MagicMock())

        assert p_fam.call_count == 1
        assert p_ads.call_count == 1
        assert p_seo.call_count == 1
        assert p_soc.call_count == 1

    assert res["status"] == "channel_assigned"
    assert res["channels"] == ["ADS", "SEO", "SOCIAL"]
    assert run.status == "channel_assigned"

    # PostgreSQL'deki EngineSelection kayıtlarını doğrula
    selections = load_engine_selections(db_session, scoring_run_id=run.id)
    assert len(selections) == 3
    sel_map = {(s.channel, s.keyword_id): s for s in selections}
    assert sel_map[("ADS", kw1.id)].algorithm_rank == 1
    assert sel_map[("ADS", kw1.id)].pool_class == "hot_sale"
    assert sel_map[("SEO", kw2.id)].algorithm_rank == 1
    assert sel_map[("SEO", kw2.id)].pool_class == "primary"
    assert sel_map[("SOCIAL", kw3.id)].algorithm_rank == 1
    assert sel_map[("SOCIAL", kw3.id)].pool_class == "P1"

    # PostgreSQL'deki ChannelPool kayıtlarını doğrula
    pools = load_channel_pools(db_session, scoring_run_id=run.id)
    assert len(pools) == 3

    # get_channel_pools API sözleşmesini doğrula (unfilled_counts ve v3 metadata)
    pool_data = ChannelEngine(db_session, MagicMock()).get_channel_pools(run.id)
    assert "channels" in pool_data
    assert "unfilled_counts" in pool_data
    assert pool_data["unfilled_counts"]["ADS"] == 9
    assert pool_data["unfilled_counts"]["SEO"] == 9
    assert pool_data["unfilled_counts"]["SOCIAL"] == 9

    ads_kw = pool_data["channels"]["ADS"][0]
    assert ads_kw["algorithm_rank"] == 1
    assert ads_kw["pool_class"] == "hot_sale"


def test_v3_orchestration_social_only_skips_family_v2(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """K8 KURALI: Yalnız SOCIAL koşusunda Family V2 ASLA çağrılmaz."""
    ws = _setup_confirmed_workspace(make_workspace)
    run = make_scoring_run(brand_profile_id=ws.id)
    run.algorithm_version = "v3"
    run.status = "scored"
    run.enable_ads = False
    run.enable_seo = False
    run.enable_social = True
    run.social_capacity = 5

    kw1 = make_keyword("viral ai trendi", brand_profile_id=ws.id)
    db_session.commit()
    freeze_universe_snapshot(db_session, run)
    db_session.commit()

    mock_social_candidates = [
        {"keyword_id": kw1.id, "algorithm_rank": 1, "pool_class": "P1"}
    ]

    with patch("app.core.engine.orchestrator.run_family_stage") as p_fam, \
         patch("app.core.engine.orchestrator.run_ads_stage") as p_ads, \
         patch("app.core.engine.orchestrator.run_seo_stage") as p_seo, \
         patch("app.core.engine.orchestrator.run_social_stage", return_value=mock_social_candidates) as p_soc:

        res = run_v3_orchestration(db_session, run=run, ai=MagicMock())

        assert p_fam.call_count == 0, "K8 ihlali: Yalnız SOCIAL koşusunda Family V2 çalışmamalıdır"
        assert p_ads.call_count == 0
        assert p_seo.call_count == 0
        assert p_soc.call_count == 1

    assert res["status"] == "channel_assigned"
    assert res["channels"] == ["SOCIAL"]

    pools = load_channel_pools(db_session, scoring_run_id=run.id)
    assert len(pools) == 1
    assert pools[0].channel == "SOCIAL"


def test_v3_orchestration_atomic_rollback_on_policy_gate_failure(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """Post-policy kapısında hata oluştuğunda hiçbir kısmi EngineSelection veya ChannelPool bırakılmaz."""
    ws = _setup_confirmed_workspace(make_workspace)
    # Strateji onayını kaldır -> policy gate fail-closed duracak
    ws.channel_strategy = None
    db_session.commit()

    run = make_scoring_run(brand_profile_id=ws.id)
    run.algorithm_version = "v3"
    run.status = "scored"
    run.enable_ads = True
    run.enable_seo = False
    run.enable_social = False

    kw1 = make_keyword("deneme kelimesi", brand_profile_id=ws.id)
    db_session.commit()
    freeze_universe_snapshot(db_session, run)
    db_session.commit()

    mock_family = {kw1.id: "fam1"}
    # Evrende olmayan keyword_id ile policy gate reddi tetiklenir
    mock_ads_candidates = [
        {"keyword_id": 999999, "algorithm_rank": 1, "pool_class": "hot_sale"}
    ]

    with patch("app.core.engine.orchestrator.run_family_stage", return_value=mock_family), \
         patch("app.core.engine.orchestrator.run_ads_stage", return_value=mock_ads_candidates):

        with pytest.raises(EngineInputError, match="run evreninde yok"):
            run_v3_orchestration(db_session, run=run, ai=MagicMock())

    # Atomik geri alma: run channel_assigned olmamalı ve hiçbir seçim/havuz yazılmamalı
    db_session.rollback()
    assert run.status == "scored"
    assert len(load_engine_selections(db_session, scoring_run_id=run.id)) == 0
    assert len(load_channel_pools(db_session, scoring_run_id=run.id)) == 0


def test_v3_celery_task_execution_end_to_end(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """Celery task seviyesinde v3 çalıştırma: TaskResult ve run durumu güncellenir."""
    ws = _setup_confirmed_workspace(make_workspace)
    run = make_scoring_run(brand_profile_id=ws.id)
    run.algorithm_version = "v3"
    run.status = "scored"
    run.enable_ads = True
    run.enable_seo = False
    run.enable_social = False
    run.ads_capacity = 5

    kw1 = make_keyword("reklam kelimesi", brand_profile_id=ws.id)
    db_session.commit()
    freeze_universe_snapshot(db_session, run)
    db_session.commit()

    from app.core.channel.assignment_dispatcher import enqueue_channel_assignment

    with patch("app.tasks.intent_tasks.run_channel_assignment_task.apply_async"):
        dispatch = enqueue_channel_assignment(db_session, run, from_status="scored")
    task_id = dispatch["task_id"]

    mock_family = {kw1.id: "fam1"}
    mock_ads_candidates = [
        {"keyword_id": kw1.id, "algorithm_rank": 1, "pool_class": "hot_sale"}
    ]

    with patch("app.core.engine.orchestrator.run_family_stage", return_value=mock_family), \
         patch("app.core.engine.orchestrator.run_ads_stage", return_value=mock_ads_candidates), \
         patch.object(run_channel_assignment_task, "update_state"):

        out = run_channel_assignment_task.apply(args=[run.id], task_id=task_id)
        result = out.result

    assert result.get("status") == "completed", f"Task failed with: {result}"
    assert result["result"]["status"] == "channel_assigned"
    assert result["result"]["channels"] == ["ADS"]

    # TaskResult kontrolü
    task_res = db_session.query(TaskResult).filter(
        TaskResult.task_id == task_id,
    ).first()

    assert task_res is not None
    assert task_res.status == "completed"
    assert task_res.result_data["channels"] == ["ADS"]
    assert "delivery_summary" in task_res.result_data
