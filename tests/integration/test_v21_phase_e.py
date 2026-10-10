# -*- coding: utf-8 -*-
"""v2.1 Faz E testleri (plan §8): deney bayrağı kapıları, run oluşturma
sözleşmesi, v2_1 SEO seçim formülü (strategy_fit) motor+simülatör paritesi,
selection_quality yeni alanları."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.config import settings
from app.database.models import (
    ChannelCandidate,
    ChannelPool,
    IntentAnalysis,
    PreFilterResult,
    ScoringRun,
)

APPROVED_STRATEGY = {
    "product_definition": "Beyaz saçları eski rengine döndüren kozmetik ürün.",
    "content_strategy": "Ürün-problem alanının tamamı.",
    "social_mode": "hype",
    "schema_version": 1,
    "status": "approved",
}

RUN_BODY = {
    "run_name": "faz e",
    "ads_capacity": 10,
    "seo_capacity": 10,
    "social_capacity": 10,
}


def _create(client, ws_id, **overrides):
    body = {**RUN_BODY, "brand_profile_id": ws_id, **overrides}
    return client.post("/api/v1/scoring/runs", json=body)


class TestCapabilitiesEndpoint:
    """Codex Faz E #2: UI deney bayrağını 409 duvarına çarpmadan öğrenir."""

    def test_flag_off(self, client, monkeypatch):
        monkeypatch.setattr(settings, "ENABLE_V21_EXPERIMENT", False)
        res = client.get("/api/v1/scoring/capabilities")
        assert res.status_code == 200
        # NOT: motor v3 kabul kapıları aynı endpoint'e `engine_v3_enabled`
        # ekledi (bkz. test_engine_v3_acceptance.py) — burada yalnız v2.1
        # alanı doğrulanır, tam sözlük eşitliği ARANMAZ.
        assert res.json()["v21_experiment_enabled"] is False

    def test_flag_on(self, client, monkeypatch):
        monkeypatch.setattr(settings, "ENABLE_V21_EXPERIMENT", True)
        res = client.get("/api/v1/scoring/capabilities")
        assert res.status_code == 200
        assert res.json()["v21_experiment_enabled"] is True


class TestV21CreateGates:
    def test_default_is_v3(self, client, make_workspace):
        ws = make_workspace("faz e default is v3")
        res = _create(client, ws.id)
        assert res.status_code == 201
        assert res.json()["algorithm_version"] == "v3"
        assert res.json()["auto_assign_channels"] is True

    def test_explicit_v2_returns_retired(self, client, make_workspace):
        ws = make_workspace("faz e explicit v2")
        res = _create(client, ws.id, algorithm_version="v2")
        assert res.status_code == 400
        assert res.json()["detail"]["code"] == "LEGACY_ENGINE_RETIRED"

    def test_explicit_v21_returns_retired_regardless_of_flag(
        self, client, db_session, make_workspace, monkeypatch
    ):
        """V3-only kararı gereği v2_1 create isteği bayrak açık/kapalı fark etmeksizin emeklidir."""
        for flag in (False, True):
            monkeypatch.setattr(settings, "ENABLE_V21_EXPERIMENT", flag)
            ws = make_workspace(f"faz e v21 flag {flag}")
            res = _create(client, ws.id, algorithm_version="v2_1")
            assert res.status_code == 400
            assert res.json()["detail"]["code"] == "LEGACY_ENGINE_RETIRED"
            assert (
                db_session.query(ScoringRun)
                .filter(ScoringRun.brand_profile_id == ws.id)
                .count() == 0
            )

    def test_tarihsel_v21_run_fixture_ile_okunabilir(
        self, client, db_session, make_workspace, make_scoring_run
    ):
        """Tarihsel v2_1 run'ları DB'den doğrudan oluşturulur ve read endpoint'inde okunabilir."""
        ws = make_workspace("faz e historical v21")
        run = make_scoring_run(
            brand_profile_id=ws.id, status="scored", algorithm_version="v2_1"
        )
        db_session.expire_all()
        assert db_session.get(ScoringRun, run.id).algorithm_version == "v2_1"

        res = client.get(f"/api/v1/scoring/runs/{run.id}", params={"brand_profile_id": ws.id})
        assert res.status_code == 200
        assert res.json()["algorithm_version"] == "v2_1"

    def test_invalid_version_rejected_by_schema(self, client, make_workspace):
        # Bilinmeyen sürüm (v4) emekli kodunu değil, INVALID_ALGORITHM_VERSION 422 döner.
        ws = make_workspace("faz e invalid")
        res = _create(client, ws.id, algorithm_version="v4")
        assert res.status_code == 422
        assert res.json()["detail"]["code"] == "INVALID_ALGORITHM_VERSION"


class TestV21SeoSelectionFormula:
    """v2_1 SEO seçimi strategy_fit okur, gt'yi OKUMAZ; v2 dalı değişmez.
    Motor ve drift simülatörü aynı dalı paylaşır (parite kilidi)."""

    def _seo_row(self, db, run, kw, *, seo_score, gt, ga, strategy_fit):
        db.add(IntentAnalysis(
            scoring_run_id=run.id, keyword_id=kw.id, channel="SEO",
            intent_type="informational", confidence_score=0.9,
            is_passed=True, gt=gt, ga=ga, strategy_fit=strategy_fit,
        ))
        db.add(ChannelCandidate(
            scoring_run_id=run.id, keyword_id=kw.id, channel="SEO",
            raw_score=float(seo_score), rank_in_channel=1,
        ))
        db.add(PreFilterResult(
            scoring_run_id=run.id, keyword_id=kw.id, channel="SEO",
            is_kept=True,
        ))
        db.commit()

    def _setup(self, db_session, make_workspace, make_scoring_run,
               make_keyword, make_keyword_score, algorithm_version):
        ws = make_workspace(f"faz e formül {algorithm_version}")
        run = make_scoring_run(
            brand_profile_id=ws.id, status="channel_assigning",
            seo_capacity=1, enable_ads=False, enable_seo=True,
            enable_social=False,
        )
        run.algorithm_version = algorithm_version
        db_session.commit()

        # A: taban 10, strategy_fit=1, gt NULL  -> v2_1: 25, v2: 10
        # B: taban 20, gt=1, strategy_fit NULL -> v2_1: 20, v2: 35
        kw_a = make_keyword("beyan uyumlu kelime", brand_profile_id=ws.id)
        kw_b = make_keyword("eski gt kelimesi", brand_profile_id=ws.id)
        make_keyword_score(scoring_run_id=run.id, keyword_id=kw_a.id,
                           seo_score=10, seo_rank=2,
                           metrics_snapshot={"monthly_volume": 100})
        make_keyword_score(scoring_run_id=run.id, keyword_id=kw_b.id,
                           seo_score=20, seo_rank=1,
                           metrics_snapshot={"monthly_volume": 100})
        self._seo_row(db_session, run, kw_a, seo_score=10,
                      gt=None, ga=None, strategy_fit=True)
        self._seo_row(db_session, run, kw_b, seo_score=20,
                      gt=True, ga=None, strategy_fit=None)
        return ws, run, kw_a, kw_b

    def _build_pool(self, db_session, run):
        from app.core.channel.channel_engine import ChannelEngine

        engine = ChannelEngine(db_session, ai_service=None)
        engine._build_final_pools_v2(run.id, run, relevance_coefficient=1.0)
        return [
            row.keyword_id
            for row in db_session.query(ChannelPool)
            .filter(ChannelPool.scoring_run_id == run.id,
                    ChannelPool.channel == "SEO")
            .all()
        ]

    def test_v21_selects_by_strategy_fit_not_gt(
        self, db_session, make_workspace, make_scoring_run,
        make_keyword, make_keyword_score,
    ):
        ws, run, kw_a, kw_b = self._setup(
            db_session, make_workspace, make_scoring_run,
            make_keyword, make_keyword_score, "v2_1",
        )
        assert self._build_pool(db_session, run) == [kw_a.id]

        # Simülatör paritesi: aynı run'da drift kopyası da A'yı seçer
        from app.core.site_analyzer.drift_metrics import simulate_final_pools

        sim = simulate_final_pools(db_session, run, {})
        assert [r["kid"] for r in sim["SEO"]] == [kw_a.id]

    def test_v2_branch_unchanged_selects_by_gt(
        self, db_session, make_workspace, make_scoring_run,
        make_keyword, make_keyword_score,
    ):
        ws, run, kw_a, kw_b = self._setup(
            db_session, make_workspace, make_scoring_run,
            make_keyword, make_keyword_score, "v2",
        )
        assert self._build_pool(db_session, run) == [kw_b.id]

        from app.core.site_analyzer.drift_metrics import simulate_final_pools

        sim = simulate_final_pools(db_session, run, {})
        assert [r["kid"] for r in sim["SEO"]] == [kw_b.id]


class TestV21SelectionQuality:
    def test_v21_reports_strategy_fit_and_ga_null_separately(
        self, db_session, make_workspace, make_scoring_run,
        make_keyword, make_keyword_score,
    ):
        formula = TestV21SeoSelectionFormula()
        ws, run, kw_a, kw_b = formula._setup(
            db_session, make_workspace, make_scoring_run,
            make_keyword, make_keyword_score, "v2_1",
        )
        formula._build_pool(db_session, run)

        from app.core.channel.channel_engine import ChannelEngine

        engine = ChannelEngine(db_session, ai_service=None)
        quality = engine._collect_selection_quality(run.id, {
            "steps": {"final_pools": {"SEO": 1}, "expansion_rounds": {}},
        })
        assert quality["algorithm_version"] == "v2_1"
        seo = quality["channels"]["SEO"]
        # Havuzdaki tek satır A: strategy_fit=1 (null değil), ga NULL
        assert seo["strategy_fit_null_in_pool"] == 0
        assert seo["ga_null_in_pool"] == 1
        assert "gt_ga_null_in_pool" not in seo
        # Relevance etkisi bloğu var; relevance kaydı olmadığında nötrle
        # birebir aynı kesme -> 0/0
        assert quality["relevance_effect"]["SEO"] == {
            "moved_in": 0, "moved_out": 0,
        }

    def test_social_dimension_counts_with_explicit_denominators(
        self, db_session, make_workspace, make_scoring_run,
        make_keyword, make_keyword_score,
    ):
        """Codex Faz E #1: üç boyutun ayrı dağılımı, payda AÇIK — 'pool'
        final havuz, 'evaluated' değerlendirilen TÜM SOCIAL satırları."""
        ws = make_workspace("faz e social dims")
        run = make_scoring_run(
            brand_profile_id=ws.id, status="channel_assigning",
            social_capacity=2, enable_ads=False, enable_seo=False,
            enable_social=True,
        )

        def _social_row(text, *, ai_class, is_kept, dims, score):
            kw = make_keyword(text, brand_profile_id=ws.id)
            make_keyword_score(
                scoring_run_id=run.id, keyword_id=kw.id,
                social_score=score, social_rank=1,
                metrics_snapshot={"monthly_volume": 100,
                                  "derived": {"h": 0.1, "mb": 0.5}},
            )
            db_session.add(IntentAnalysis(
                scoring_run_id=run.id, keyword_id=kw.id, channel="SOCIAL",
                intent_type="informational", confidence_score=0.9,
                is_passed=True,
            ))
            db_session.add(ChannelCandidate(
                scoring_run_id=run.id, keyword_id=kw.id, channel="SOCIAL",
                raw_score=float(score), rank_in_channel=1,
            ))
            db_session.add(PreFilterResult(
                scoring_run_id=run.id, keyword_id=kw.id, channel="SOCIAL",
                is_kept=is_kept, ai_class=ai_class,
                extra_data={"dims": dims},
            ))
            db_session.commit()
            return kw

        _social_row("tartisma konusu", ai_class=2, is_kept=True, score=30,
                    dims={"opinion_discussion": 1, "curiosity_comparison": 1,
                          "agenda_theme": 0})
        _social_row("gundem konusu", ai_class=1, is_kept=True, score=20,
                    dims={"opinion_discussion": 0, "curiosity_comparison": 0,
                          "agenda_theme": 1})
        # Elenen satır: evaluated paydasına girer, pool paydasına GİRMEZ
        _social_row("elenen konu", ai_class=0, is_kept=False, score=10,
                    dims={"opinion_discussion": 0, "curiosity_comparison": 0,
                          "agenda_theme": 0})

        from app.core.channel.channel_engine import ChannelEngine

        engine = ChannelEngine(db_session, ai_service=None)
        engine._build_final_pools_v2(run.id, run, relevance_coefficient=1.0)
        quality = engine._collect_selection_quality(run.id, {
            "steps": {"final_pools": {"SOCIAL": 2}, "expansion_rounds": {}},
        })
        social = quality["channels"]["SOCIAL"]
        assert social["dimension_counts"] == {
            "pool": {"total": 2, "opinion_discussion": 1,
                     "curiosity_comparison": 1, "agenda_theme": 1},
            "evaluated": {"total": 3, "opinion_discussion": 1,
                          "curiosity_comparison": 1, "agenda_theme": 1},
        }
        assert social["class_counts"]["2"] == 1
        assert social["class_counts"]["1"] == 1

    def test_v2_keeps_legacy_gt_ga_field(
        self, db_session, make_workspace, make_scoring_run,
        make_keyword, make_keyword_score,
    ):
        formula = TestV21SeoSelectionFormula()
        ws, run, kw_a, kw_b = formula._setup(
            db_session, make_workspace, make_scoring_run,
            make_keyword, make_keyword_score, "v2",
        )
        formula._build_pool(db_session, run)

        from app.core.channel.channel_engine import ChannelEngine

        engine = ChannelEngine(db_session, ai_service=None)
        quality = engine._collect_selection_quality(run.id, {
            "steps": {"final_pools": {"SEO": 1}, "expansion_rounds": {}},
        })
        assert quality["algorithm_version"] == "v2"
        seo = quality["channels"]["SEO"]
        # Havuzdaki tek satır B: gt=1, ga NULL -> null sayacı 1
        assert seo["gt_ga_null_in_pool"] == 1
        assert "strategy_fit_null_in_pool" not in seo
