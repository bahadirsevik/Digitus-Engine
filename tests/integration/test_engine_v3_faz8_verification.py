"""Motor v3 Faz 8 — Uçtan Uca Doğrulama ve Downstream Smoke Testleri.

plan_algoritma_entegrasyonu.md Faz 8 gereksinimleri:
  1. Üç kanal kombinasyonu (MockAI):
     - ADS + SEO + SOCIAL (Family V2 yalnız bir kez, üç kanalın sonuçları üretilir)
     - Yalnız SOCIAL (Family V2 çağrısı kesinlikle 0)
     - Yalnız SEO (Family V2 çalışır, ADS motoru çalışmaz)
     - Her üçünde ChannelCandidate, IntentAnalysis ve PreFilterResult tabloları BOŞ kalır.
     - Run durum zinciri: pending -> scoring -> scored -> channel_assigning -> channel_assigned.
     - Dondurulmuş run snapshot'ı kullanılır; canlı veriye dönüş yok.
     - algorithm_rank ve final_rank ayrı kalır; elenenlerde final_rank=None, exclude_reason dolu.
     - Geri doldurma yok; unfilled_count doğru.

  2. Policy, freshness, bütçe ve rollback doğrulaması:
     - Post-policy aşamasında AI çağrısı 0.
     - Policy elemesi algorithm_rank ve motor skorlarını değiştirmez.
     - Elenen kelime ChannelPool'a yazılmaz.
     - Freshness damgaları yalnız başarılı finalize ile atomik yazılır.
     - Profil / policy / strategy değişince stale tespiti çalışır.
     - Attempt / cap / ledger eksikse fail-closed.
     - DB hatasında önceki başarılı havuz ve seçimler korunur.
     - Resume manifest uyuşmazlığında sessiz yeniden kullanım engellenir.

  3. Downstream smoke doğrulamaları (MockAI / stub):
     - ADS RSA generation dispatch yolu v3 ChannelPool'u okur.
     - SEO bulk generation dispatch yolu v3 ChannelPool'u okur.
     - SOCIAL kategori generation yolu v3 ChannelPool'u okur.
     - Export (DataCollector + ExcelExporter) v3 ChannelPool'u hatasız okur ve raporlar.
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch
import pytest

from app.config import settings
from app.core.channel.assignment_dispatcher import enqueue_channel_assignment
from app.core.channel.channel_engine import ChannelEngine
from app.core.engine.context import (
    EngineInputError,
    build_firm_profile,
    firm_block,
    firm_block_sha256,
    freeze_universe_snapshot,
)
from app.core.engine.orchestrator import run_v3_orchestration
from app.core.policy.location_policy import policy_snapshot as _loc_snap
from app.core.engine.persistence import (
    load_channel_pools,
    load_engine_selections,
    manifest_firm_block_sha,
)
from app.core.engine.policy_gate import finalize_engine_delivery
from app.core.policy.freshness import compute_pool_freshness
from app.database.models import (
    ChannelCandidate,
    ChannelPool,
    EngineSelection,
    EngineStageResult,
    IntentAnalysis,
    Keyword,
    PreFilterResult,
    ScoringRun,
    TaskResult,
)
from app.exporters.data_collector import ExportDataCollector
from app.exporters.excel_exporter import ExcelExporter
from app.generators.ads.ads_generator import AdsGenerator
from app.generators.ai_service import get_ai_service
from app.generators.social.social_generator import SocialGenerator
from app.schemas.export import ExportSectionEnum
from app.tasks.intent_tasks import run_channel_assignment_task


def _setup_confirmed_profile(make_workspace):
    ws = make_workspace("Faz 8 Test Workspace")
    ws.status = "confirmed"
    ws.policy_version = 1
    ws.strategy_version = 1
    ws.anchor_version = 1
    ws.channel_strategy = {
        "status": "approved",
        "product_definition": "AI Destekli Pazarlama Motoru",
        "content_strategy": "Teknik B2B Analiz ve Rehberler",
        "social_mode": "hype",
    }
    ws.profile_data = {
        "company_name": "Digitus AI",
        "brand_terms": ["digitus"],
        "sector": "SaaS",
    }
    ws.topic_policy = {
        "excluded_terms": [
            {"term": "kumar", "status": "approved"},
            {"term": "yasakli_terim", "status": "approved"},
        ],
    }
    ws.competitor_terms = [
        {"term": "rakipco", "status": "approved"},
    ]
    ws.competitor_policy = {"ads": "block", "seo": "allow", "social": "block"}
    return ws


# ===========================================================================
# 1 & 2: Üç Uçtan Uca Kanal Kombinasyonu (MockAI) ve Bütünlük Doğrulamaları
# ===========================================================================

class TestFaz8ThreeChannelCombinations:

    def test_combination_ads_seo_social_full_pipeline(
        self, db_session, make_workspace, make_scoring_run, make_keyword, monkeypatch
    ):
        """Kombinasyon 1: ADS + SEO + SOCIAL.
        - Family V2 yalnız bir kez çalışır.
        - Üç kanalın sonuçları üretilir.
        - Durum zinciri: pending -> scoring -> scored -> channel_assigning -> channel_assigned.
        - ChannelCandidate, IntentAnalysis, PreFilterResult boş kalır (0 satır).
        - algorithm_rank ve final_rank ayrı kalır; elenenlerde final_rank=None, exclude_reason dolu.
        - Geri doldurma yapılmaz; unfilled_count doğrudur.
        """
        monkeypatch.setattr(settings, "ENABLE_ENGINE_V3", True)
        ws = _setup_confirmed_profile(make_workspace)

        run = make_scoring_run(brand_profile_id=ws.id)
        run.algorithm_version = "v3"
        run.status = "pending"
        run.enable_ads = True
        run.enable_seo = True
        run.enable_social = True
        run.ads_capacity = 5
        run.seo_capacity = 5
        run.social_capacity = 5
        run.auto_assign_channels = True
        db_session.commit()

        # Evren kelimeleri (biri temiz, biri topic elenecek, biri rakip)
        kw_clean = make_keyword("digitus pazarlama yazilimi", brand_profile_id=ws.id, monthly_volume=2000)
        kw_topic_bad = make_keyword("kumar ve bahis yazilimi", brand_profile_id=ws.id, monthly_volume=1500)
        kw_comp = make_keyword("rakipco alternatifi araclar", brand_profile_id=ws.id, monthly_volume=1000)
        kw_social = make_keyword("yapay zeka trendleri 2026", brand_profile_id=ws.id, monthly_volume=3000)
        db_session.commit()

        # 1. State machine: pending -> scoring -> snapshot -> scored
        from app.core.scoring.state_machine import transition_atomic
        assert transition_atomic(db_session, run, target="scoring", from_status="pending")
        freeze_universe_snapshot(db_session, run)
        assert transition_atomic(db_session, run, target="scored", from_status="scoring")
        db_session.commit()

        # 2. Enqueue: scored -> channel_assigning
        with patch("app.tasks.intent_tasks.run_channel_assignment_task.apply_async"):
            dispatch = enqueue_channel_assignment(db_session, run, from_status="scored")
        task_id = dispatch["task_id"]
        assert run.status == "channel_assigning"

        # Runner mock'ları (Family bir kez, kanallar kendilerine göre döner)
        family_calls = []
        def _mock_run_family(db, **kwargs):
            family_calls.append(1)
            return {
                kw_clean.id: "fam_pazarlama",
                kw_topic_bad.id: "fam_bad",
                kw_comp.id: "fam_comp",
                kw_social.id: "fam_social",
            }

        ads_candidates = [
            {"keyword_id": kw_clean.id, "algorithm_rank": 1, "pool_class": "hot_sale", "algorithm_score": 35.0},
            {"keyword_id": kw_topic_bad.id, "algorithm_rank": 2, "pool_class": "lead", "algorithm_score": 25.0},
        ]
        seo_candidates = [
            {"keyword_id": kw_clean.id, "algorithm_rank": 1, "pool_class": "primary", "algorithm_score": 40.0},
            {"keyword_id": kw_comp.id, "algorithm_rank": 2, "pool_class": "primary", "algorithm_score": 30.0},
        ]
        social_candidates = [
            {"keyword_id": kw_social.id, "algorithm_rank": 1, "pool_class": "P1", "algorithm_score": 50.0},
            {"keyword_id": kw_comp.id, "algorithm_rank": 2, "pool_class": "P2", "algorithm_score": 20.0},
        ]

        with patch("app.core.engine.orchestrator.run_family_stage", side_effect=_mock_run_family), \
             patch("app.core.engine.orchestrator.run_ads_stage", return_value=ads_candidates), \
             patch("app.core.engine.orchestrator.run_seo_stage", return_value=seo_candidates), \
             patch("app.core.engine.orchestrator.run_social_stage", return_value=social_candidates), \
             patch.object(run_channel_assignment_task, "update_state"):

            out = run_channel_assignment_task.apply(args=[run.id], task_id=task_id)
            result = out.result

        assert result["status"] == "completed"
        db_session.refresh(run)
        assert run.status == "channel_assigned"

        # K8: Family V2 yalnız BİR KEZ çalışmalı
        assert len(family_calls) == 1, "Family V2 tek koşuda yalnız 1 kez çalışmalıdır"

        # Tablo kirliliği denetimi: v3 ChannelCandidate, IntentAnalysis, PreFilterResult yazmaz!
        assert db_session.query(ChannelCandidate).filter(ChannelCandidate.scoring_run_id == run.id).count() == 0
        assert db_session.query(IntentAnalysis).filter(IntentAnalysis.scoring_run_id == run.id).count() == 0
        assert db_session.query(PreFilterResult).filter(PreFilterResult.scoring_run_id == run.id).count() == 0

        # EngineSelection denetimi: algorithm_rank korunur, elenenlerde final_rank=None
        selections = load_engine_selections(db_session, scoring_run_id=run.id)
        assert len(selections) > 0
        sel_map = {(s.channel, s.keyword_id): s for s in selections}

        # ADS: kw_topic_bad topic_policy nedeniyle elenmeli (kumar terimi)
        bad_ads_sel = sel_map[("ADS", kw_topic_bad.id)]
        assert bad_ads_sel.final_rank is None
        assert bad_ads_sel.exclude_reason is not None
        assert "topic" in bad_ads_sel.exclude_reason.lower() or "excluded" in bad_ads_sel.exclude_reason.lower()
        assert bad_ads_sel.algorithm_rank == 2  # algorithm_rank policy elemesinden etkilenmez

        # ADS: kw_clean onaylanmalı ve final_rank=1 olmalı
        clean_ads_sel = sel_map[("ADS", kw_clean.id)]
        assert clean_ads_sel.final_rank == 1
        assert clean_ads_sel.algorithm_rank == 1

        # ChannelPool denetimi: elenen kelime havuza ASLA yazılmaz
        pools = load_channel_pools(db_session, scoring_run_id=run.id)
        pool_pairs = {(p.channel, p.keyword_id) for p in pools}
        assert ("ADS", kw_topic_bad.id) not in pool_pairs
        assert ("ADS", kw_clean.id) in pool_pairs

        # Unfilled counts denetimi (kapasite 5, seçilen 1 -> unfilled 4)
        pool_dict = ChannelEngine(db_session, MagicMock()).get_channel_pools(run.id)
        assert pool_dict["unfilled_counts"]["ADS"] == 4
        assert len(pool_dict["channels"]["ADS"]) == 1

    def test_combination_social_only_zero_family(
        self, db_session, make_workspace, make_scoring_run, make_keyword, monkeypatch
    ):
        """Kombinasyon 2: Yalnız SOCIAL.
        - K8: Family V2 çağrı sayısı kesinlikle 0 olmalıdır.
        - ADS ve SEO motorları çağrılmaz.
        - Yalnız SOCIAL havuzu oluşturulur.
        - ChannelCandidate, IntentAnalysis, PreFilterResult boş kalır.
        """
        monkeypatch.setattr(settings, "ENABLE_ENGINE_V3", True)
        ws = _setup_confirmed_profile(make_workspace)

        run = make_scoring_run(brand_profile_id=ws.id)
        run.algorithm_version = "v3"
        run.status = "scored"
        run.enable_ads = False
        run.enable_seo = False
        run.enable_social = True
        run.social_capacity = 5
        db_session.commit()

        kw = make_keyword("viral ai reels formati", brand_profile_id=ws.id)
        freeze_universe_snapshot(db_session, run)
        db_session.commit()

        with patch("app.tasks.intent_tasks.run_channel_assignment_task.apply_async"):
            dispatch = enqueue_channel_assignment(db_session, run, from_status="scored")
        task_id = dispatch["task_id"]

        social_candidates = [
            {"keyword_id": kw.id, "algorithm_rank": 1, "pool_class": "P1", "algorithm_score": 45.0}
        ]

        with patch("app.core.engine.orchestrator.run_family_stage") as p_fam, \
             patch("app.core.engine.orchestrator.run_ads_stage") as p_ads, \
             patch("app.core.engine.orchestrator.run_seo_stage") as p_seo, \
             patch("app.core.engine.orchestrator.run_social_stage", return_value=social_candidates) as p_soc, \
             patch.object(run_channel_assignment_task, "update_state"):

            out = run_channel_assignment_task.apply(args=[run.id], task_id=task_id)
            result = out.result

        assert result["status"] == "completed"
        assert p_fam.call_count == 0, "K8 ihlali: Yalnız SOCIAL koşusunda Family V2 çağrı sayısı 0 olmalıdır"
        assert p_ads.call_count == 0
        assert p_seo.call_count == 0
        assert p_soc.call_count == 1
        db_session.refresh(run)
        assert run.status == "channel_assigned"
        pools = load_channel_pools(db_session, scoring_run_id=run.id)
        assert len(pools) == 1
        assert pools[0].channel == "SOCIAL"

        # Tablo kirliliği yok
        assert db_session.query(ChannelCandidate).filter(ChannelCandidate.scoring_run_id == run.id).count() == 0
        assert db_session.query(IntentAnalysis).filter(IntentAnalysis.scoring_run_id == run.id).count() == 0
        assert db_session.query(PreFilterResult).filter(PreFilterResult.scoring_run_id == run.id).count() == 0

    def test_combination_seo_only_runs_family_and_skips_ads(
        self, db_session, make_workspace, make_scoring_run, make_keyword, monkeypatch
    ):
        """Kombinasyon 3: Yalnız SEO.
        - Family V2 çalışır (call_count == 1).
        - ADS motoru kesinlikle çalışmaz (call_count == 0).
        - SOCIAL motoru çalışmaz (call_count == 0).
        - Yalnız SEO havuzu teslim edilir.
        """
        monkeypatch.setattr(settings, "ENABLE_ENGINE_V3", True)
        ws = _setup_confirmed_profile(make_workspace)

        run = make_scoring_run(brand_profile_id=ws.id)
        run.algorithm_version = "v3"
        run.status = "scored"
        run.enable_ads = False
        run.enable_seo = True
        run.enable_social = False
        run.seo_capacity = 5
        db_session.commit()

        kw = make_keyword("seo teknik blog rehberi", brand_profile_id=ws.id)
        freeze_universe_snapshot(db_session, run)
        db_session.commit()

        with patch("app.tasks.intent_tasks.run_channel_assignment_task.apply_async"):
            dispatch = enqueue_channel_assignment(db_session, run, from_status="scored")
        task_id = dispatch["task_id"]

        seo_candidates = [
            {"keyword_id": kw.id, "algorithm_rank": 1, "pool_class": "primary", "algorithm_score": 42.0}
        ]

        with patch("app.core.engine.orchestrator.run_family_stage", return_value={kw.id: "fam_seo"}) as p_fam, \
             patch("app.core.engine.orchestrator.run_ads_stage") as p_ads, \
             patch("app.core.engine.orchestrator.run_seo_stage", return_value=seo_candidates) as p_seo, \
             patch("app.core.engine.orchestrator.run_social_stage") as p_soc, \
             patch.object(run_channel_assignment_task, "update_state"):

            out = run_channel_assignment_task.apply(args=[run.id], task_id=task_id)
            result = out.result

        assert result["status"] == "completed"
        assert p_fam.call_count == 1, "SEO-only koşusunda Family V2 çalışmalıdır"
        assert p_ads.call_count == 0, "SEO-only koşusunda ADS motoru çalışmamalıdır"
        assert p_seo.call_count == 1
        assert p_soc.call_count == 0

        db_session.refresh(run)
        assert run.status == "channel_assigned"
        pools = load_channel_pools(db_session, scoring_run_id=run.id)
        assert len(pools) == 1
        assert pools[0].channel == "SEO"


# ===========================================================================
# 3: Policy, Freshness, Bütçe ve Rollback Doğrulamaları
# ===========================================================================

class TestFaz8PolicyFreshnessBudgetRollback:

    def test_post_policy_zero_ai_calls_and_score_integrity(
        self, db_session, make_workspace, make_scoring_run, make_keyword, monkeypatch
    ):
        """Post-policy kapısında AI çağrı sayısı kesinlikle 0 olmalıdır.
        Policy elemesi algorithm_rank değerlerini ve motor skorlarını bozmaz.
        """
        monkeypatch.setattr(settings, "ENABLE_ENGINE_V3", True)
        ws = _setup_confirmed_profile(make_workspace)
        run = make_scoring_run(brand_profile_id=ws.id)
        run.algorithm_version = "v3"
        run.status = "scored"
        run.enable_ads = True
        run.enable_seo = False
        run.enable_social = False
        run.ads_capacity = 5

        kw1 = make_keyword("temiz urun", brand_profile_id=ws.id)
        kw2 = make_keyword("yasakli_terim urun", brand_profile_id=ws.id)
        db_session.commit()
        freeze_universe_snapshot(db_session, run)
        db_session.commit()

        candidates = [
            {"keyword_id": kw1.id, "algorithm_rank": 1, "pool_class": "hot_sale", "scores": {"algorithm_score": 50.0}},
            {"keyword_id": kw2.id, "algorithm_rank": 2, "pool_class": "hot_sale", "scores": {"algorithm_score": 40.0}},
        ]

        # 1. AST yapısal kontrolü: policy_gate modülü hiçbir AI kütüphanesi veya servisi import etmez
        import ast
        from pathlib import Path
        policy_gate_path = Path(__file__).resolve().parent.parent.parent / "app" / "core" / "engine" / "policy_gate.py"
        tree = ast.parse(policy_gate_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "ai_service" not in alias.name, f"policy_gate içinde yasaklı import: {alias.name}"
                    assert "generativeai" not in alias.name, f"policy_gate içinde yasaklı import: {alias.name}"
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                assert "ai_service" not in mod, f"policy_gate içinde yasaklı import: {mod}"
                assert "brand_filter" not in mod, f"policy_gate içinde BrandExclusionFilter AI importu yasak: {mod}"
                assert "generators" not in mod, f"policy_gate içinde generators importu yasak: {mod}"

        # Manifest mühürle
        from app.core.engine.persistence import seal_manifest
        seal_manifest(
            run,
            firm_block_sha256=firm_block_sha256(firm_block(build_firm_profile(ws))),
            algorithm_versions={"ads": "nihai_niche_v1"},
            models={"ads": "test_model"},
            prompt_shas={"ads": "test_sha"},
            location_policy=_loc_snap({}),
        )
        db_session.commit()

        # 2. Gerçek çağrı noktası guard'ı: AIService ve BrandExclusionFilter'a erişilirse test patlar
        with patch("app.generators.ai_service.AIService.complete", side_effect=AssertionError("policy_gate ASLA AI çağırmaz")), \
             patch("app.generators.ai_service.AIService.complete_json", side_effect=AssertionError("policy_gate ASLA AI çağırmaz")), \
             patch("app.generators.ai_service.get_ai_service", side_effect=AssertionError("policy_gate ASLA AI servisi almaz")), \
             patch("app.core.channel.brand_filter.BrandExclusionFilter.filter_intent_passed", side_effect=AssertionError("BrandExclusionFilter AI yolu çağrılmamalı")):

            delivery = finalize_engine_delivery(
                db_session,
                run=run,
                channel_selections={"ADS": candidates},
            )

        assert delivery["channels"]["ADS"]["kept_count"] == 1
        assert delivery["channels"]["ADS"]["excluded_count"] == 1
        assert delivery["channels"]["ADS"]["pool_count"] == 1

        # Skor bütünlüğü: EngineSelection kayıtlarındaki algorithm_score ve algorithm_rank orijinal kalmalı
        selections = load_engine_selections(db_session, scoring_run_id=run.id)
        sel_map = {s.keyword_id: s for s in selections}
        assert sel_map[kw1.id].algorithm_rank == 1
        assert sel_map[kw1.id].scores["algorithm_score"] == 50.0
        assert sel_map[kw1.id].final_rank == 1

        assert sel_map[kw2.id].algorithm_rank == 2
        assert sel_map[kw2.id].scores["algorithm_score"] == 40.0
        assert sel_map[kw2.id].final_rank is None  # Elenenin final rank'i NULL

    def test_freshness_stamps_and_stale_detection(
        self, db_session, make_workspace, make_scoring_run, make_keyword, monkeypatch
    ):
        """Freshness damgaları atomik yazılır; workspace policy/strategy/brand değişince stale tespiti çalışır."""
        monkeypatch.setattr(settings, "ENABLE_ENGINE_V3", True)
        ws = _setup_confirmed_profile(make_workspace)
        run = make_scoring_run(brand_profile_id=ws.id)
        run.algorithm_version = "v3"
        run.status = "scored"
        run.enable_ads = True
        run.enable_seo = False
        run.enable_social = False
        run.ads_capacity = 5

        kw = make_keyword("taze kelime", brand_profile_id=ws.id)
        db_session.commit()
        freeze_universe_snapshot(db_session, run)
        db_session.commit()

        with patch("app.core.engine.orchestrator.run_family_stage", return_value={kw.id: "fam"}), \
             patch("app.core.engine.orchestrator.run_ads_stage", return_value=[{"keyword_id": kw.id, "algorithm_rank": 1, "pool_class": "hot_sale"}]):
            run_v3_orchestration(db_session, run=run, ai=MagicMock())

        # Freshness damgaları yazıldı mı?
        assert run.channel_pool_policy_version == ws.policy_version
        assert run.channel_pool_strategy_version is None
        assert run.relevance_anchor_version == ws.anchor_version
        assert manifest_firm_block_sha(run) == firm_block_sha256(firm_block(build_firm_profile(ws)))

        # Başlangıçta havuz tazedir
        freshness = compute_pool_freshness(run, ws)
        assert freshness.channel_pool_stale is False
        assert freshness.policy_stale is False
        assert freshness.strategy_stale is False
        assert freshness.relevance_stale is False

        # 1. Policy değişince stale olmalı
        ws.policy_version += 1
        db_session.commit()
        freshness_policy = compute_pool_freshness(run, ws)
        assert freshness_policy.policy_stale is True
        assert freshness_policy.channel_pool_stale is True

        # 2. Strategy değişince V3 havuzu bayatlamaz (V3-only kararı)
        ws.policy_version = run.channel_pool_policy_version  # reset
        ws.strategy_version += 1
        db_session.commit()
        freshness_strategy = compute_pool_freshness(run, ws)
        assert freshness_strategy.strategy_stale is False
        assert freshness_strategy.channel_pool_stale is False

        # 3. Firma profil verisi değişince relevance_stale olmalı (hash değişti)
        ws.profile_data = {**ws.profile_data, "company_name": "Farkli Bir Firma Adresi"}
        db_session.commit()
        freshness_firm = compute_pool_freshness(run, ws)
        assert freshness_firm.relevance_stale is True
        assert freshness_firm.channel_pool_stale is True

    def test_fail_closed_budget_gate_zero_provider_calls(
        self, db_session, make_workspace, make_scoring_run
    ):
        """Bütçe/attempt/ledger eksikliğinde orkestratör/sağlayıcı çağrılmadan fail-closed durulur."""
        ws = _setup_confirmed_profile(make_workspace)
        run = make_scoring_run(brand_profile_id=ws.id)
        run.algorithm_version = "v3"
        run.status = "scored"
        db_session.commit()

        mock_orch = MagicMock()
        with patch("app.tasks.intent_tasks.run_v3_orchestration", mock_orch), \
             patch.object(run_channel_assignment_task, "update_state"):
            res = run_channel_assignment_task.apply(args=[run.id], task_id="task-budget-gate").result

        assert res["status"] == "failed"
        assert res.get("reason") == "LEDGER_BIND_FAILED"
        assert mock_orch.call_count == 0, "Bütçe şartı sağlanmadığında sağlayıcı/orkestratör ASLA çağrılmamalıdır"

    def test_resume_mismatch_fails_closed(
        self, db_session, make_workspace, make_scoring_run, make_keyword
    ):
        """Mühürlü manifestteki firma hash'i ile güncel profil uyuşmazsa finalize teslim etmeden durur."""
        ws = _setup_confirmed_profile(make_workspace)
        run = make_scoring_run(brand_profile_id=ws.id)
        run.algorithm_version = "v3"
        run.status = "scored"
        run.enable_ads = True
        run.enable_seo = False
        run.enable_social = False
        run.ads_capacity = 5

        kw = make_keyword("kw1", brand_profile_id=ws.id)
        db_session.commit()
        freeze_universe_snapshot(db_session, run)
        db_session.commit()

        # Sahte veya eski bir hash ile mühürle
        from app.core.engine.persistence import seal_manifest
        seal_manifest(
            run,
            firm_block_sha256="tamamen_farkli_eski_hash_123456",
            algorithm_versions={"ads": "nihai_niche_v1"},
            models={"ads": "test_model"},
            prompt_shas={"ads": "test_sha"},
            location_policy=_loc_snap({}),
        )
        db_session.commit()

        with pytest.raises(EngineInputError, match="muhurden sonra degismis"):
            finalize_engine_delivery(
                db_session,
                run=run,
                channel_selections={"ADS": [{"keyword_id": kw.id, "algorithm_rank": 1, "pool_class": "hot_sale"}]},
            )


# ===========================================================================
# 4: Downstream Smoke Doğrulamaları
# ===========================================================================

class TestFaz8DownstreamSmoke:

    @pytest.fixture
    def populated_v3_channel_pools(self, db_session, make_workspace, make_scoring_run, make_keyword):
        """ADS, SEO ve SOCIAL havuzları dolu taze v3 koşusu oluşturur."""
        ws = _setup_confirmed_profile(make_workspace)
        run = make_scoring_run(brand_profile_id=ws.id)
        run.algorithm_version = "v3"
        run.status = "scored"
        run.enable_ads = True
        run.enable_seo = True
        run.enable_social = True
        run.ads_capacity = 5
        run.seo_capacity = 5
        run.social_capacity = 5

        kw_ads = make_keyword("ads anahtar kelimesi", brand_profile_id=ws.id)
        kw_seo = make_keyword("seo rehberi ve blogu", brand_profile_id=ws.id)
        kw_social = make_keyword("sosyal medya post konusu", brand_profile_id=ws.id)
        db_session.commit()

        freeze_universe_snapshot(db_session, run)
        db_session.commit()

        candidates = {
            "ADS": [{"keyword_id": kw_ads.id, "algorithm_rank": 1, "pool_class": "hot_sale", "algorithm_score": 40.0}],
            "SEO": [{"keyword_id": kw_seo.id, "algorithm_rank": 1, "pool_class": "primary", "algorithm_score": 45.0}],
            "SOCIAL": [{"keyword_id": kw_social.id, "algorithm_rank": 1, "pool_class": "P1", "algorithm_score": 50.0}],
        }

        with patch("app.core.engine.orchestrator.run_family_stage", return_value={kw_ads.id: "fam", kw_seo.id: "fam", kw_social.id: "fam"}), \
             patch("app.core.engine.orchestrator.run_ads_stage", return_value=candidates["ADS"]), \
             patch("app.core.engine.orchestrator.run_seo_stage", return_value=candidates["SEO"]), \
             patch("app.core.engine.orchestrator.run_social_stage", return_value=candidates["SOCIAL"]):
            run_v3_orchestration(db_session, run=run, ai=MagicMock())

        return ws, run, kw_ads, kw_seo, kw_social

    def test_ads_rsa_smoke_reads_v3_channel_pool(self, db_session, populated_v3_channel_pools):
        """ADS: generate_ads_rsa / _dispatch_ads üretim yolu v3 ChannelPool'u okur ve görev sözleşmesini korur."""
        from app.api.v1.generation import generate_ads_rsa
        from app.database.models import AdGenerationSet, TaskResult
        from app.schemas.ads import AdsGenerateRequest

        ws, run, kw_ads, _, _ = populated_v3_channel_pools

        mock_apply_async = MagicMock()
        mock_provider = MagicMock()
        mock_provider.complete.side_effect = AssertionError("Gerçek provider çağrısı 0 olmalıdır")
        mock_provider.complete_json.side_effect = AssertionError("Gerçek provider çağrısı 0 olmalıdır")

        with patch("app.tasks.generation_tasks.generate_ads_task.apply_async", mock_apply_async), \
             patch("app.generators.ai_service.get_ai_service", return_value=mock_provider):
            req = AdsGenerateRequest(
                scoring_run_id=run.id,
                brand_name="Digitus AI",
                brand_usp="Yapay zeka pazarlama optimizasyonu",
            )
            res = generate_ads_rsa(req, brand_profile_id=ws.id, db=db_session)

        # 1. AdGenerationSet / TaskResult sözleşmesi doğrulanır
        assert res.status == "generating"
        assert res.version_number == 1
        gen_set = db_session.query(AdGenerationSet).filter_by(id=res.generation_set_id).first()
        assert gen_set is not None
        assert gen_set.status == "generating"
        assert gen_set.scoring_run_id == run.id

        task_res = db_session.query(TaskResult).filter_by(task_id=res.task_id).first()
        assert task_res is not None
        assert task_res.status == "pending"
        assert task_res.task_type == "ads"

        # 2. Celery task payload'unda scoring_run_id ve generation_set_id doğrulanır
        assert mock_apply_async.call_count == 1
        call_kwargs = mock_apply_async.call_args[1]["kwargs"]
        assert call_kwargs["scoring_run_id"] == run.id
        assert call_kwargs["generation_set_id"] == gen_set.id

        # 3. v3 ChannelPool'daki kelimeler AdsGenerator tarafından okunur
        ads_keywords = AdsGenerator(db_session, mock_provider)._get_ads_keywords(call_kwargs["scoring_run_id"])
        assert len(ads_keywords) >= 1
        assert any(k["id"] == kw_ads.id for k in ads_keywords)
        assert any(k["keyword"] == kw_ads.keyword for k in ads_keywords)

        # 4. Gerçek provider çağrısı 0
        assert mock_provider.complete.call_count == 0
        assert mock_provider.complete_json.call_count == 0

    def test_seo_bulk_generation_smoke_reads_v3_channel_pool(
        self, db_session, populated_v3_channel_pools, monkeypatch
    ):
        from app.api.v1.generation import generate_seo_geo_bulk

        ws, run, _, kw_seo, _ = populated_v3_channel_pools

        dispatched_payloads = []
        def _mock_start_bulk(scoring_run_id, keyword_ids, tone=None):
            dispatched_payloads.append({
                "scoring_run_id": scoring_run_id,
                "keyword_ids": keyword_ids,
                "tone": tone,
            })
            return "fake-seo-task-id-123"

        monkeypatch.setattr(
            "app.tasks.generation_tasks.start_bulk_seo_generation",
            _mock_start_bulk,
        )

        res = generate_seo_geo_bulk(
            scoring_run_id=run.id,
            limit=None,
            tone="formal",
            brand_profile_id=ws.id,
            db=db_session,
        )

        assert res["task_id"] == "fake-seo-task-id-123"
        assert res["status"] == "pending"
        assert res["total_keywords"] >= 1
        assert kw_seo.id in dispatched_payloads[0]["keyword_ids"]

    def test_social_categories_smoke_reads_v3_channel_pool(self, db_session, populated_v3_channel_pools):
        """SOCIAL: generate_social_categories endpoint yolu v3 ChannelPool kelimelerini prompta geçirir."""
        import json
        from app.api.v1.generation import generate_social_categories
        from app.schemas.social import SocialCategoriesRequest

        ws, run, _, _, kw_social = populated_v3_channel_pools

        recorded_prompts = []
        mock_ai = MagicMock()
        mock_ai.for_stage.return_value = mock_ai
        mock_ai.complete_json.side_effect = lambda prompt, *a, **kw: (
            recorded_prompts.append(prompt) or json.dumps({
                "categories": [{
                    "category_name": "AI Trendleri",
                    "category_type": "educational",
                    "description": "Yapay zeka içerik stratejisi",
                    "relevance_score": 0.9,
                }]
            })
        )

        req = SocialCategoriesRequest(
            scoring_run_id=run.id,
            brand_name="Digitus AI",
            brand_context="SaaS pazarlama",
            max_categories=4,
        )
        res = generate_social_categories(
            request=req,
            brand_profile_id=ws.id,
            db=db_session,
            ai=mock_ai,
        )

        assert res.scoring_run_id == run.id
        assert res.total_categories >= 1
        assert len(recorded_prompts) == 1
        # v3 SOCIAL havuzundaki kelimenin gerçek kategori üretim promptuna girdiği kanıtlanır
        assert kw_social.keyword in recorded_prompts[0]
        assert str(kw_social.id) in recorded_prompts[0]

    def test_export_smoke_reads_v3_channel_pool(self, db_session, populated_v3_channel_pools):
        """Export: DataCollector ve ExcelExporter v3 ChannelPool'u eksiksiz toplar ve rapor üretir."""
        import tempfile
        import os

        ws, run, kw_ads, kw_seo, kw_social = populated_v3_channel_pools

        collector = ExportDataCollector(db_session)
        channels_data = collector._collect_channels(run.id)

        assert channels_data is not None
        assert channels_data.ads is not None
        assert channels_data.seo is not None
        assert channels_data.social is not None

        assert any(k.keyword_id == kw_ads.id for k in channels_data.ads.keywords)
        assert any(k.keyword_id == kw_seo.id for k in channels_data.seo.keywords)
        assert any(k.keyword_id == kw_social.id for k in channels_data.social.keywords)

        exporter = ExcelExporter(db_session)
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            out_path = exporter.export(
                run.id,
                sections=[ExportSectionEnum.CHANNELS],
                filepath=tmp_path,
            )
            assert os.path.exists(out_path)
            assert os.path.getsize(out_path) > 0
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
