# -*- coding: utf-8 -*-
"""Hard-cap dolduğunda seçim SESSİZCE değişmez (Codex iş sırası #1).

`BudgetExceeded` provider çağrısı YAPILMADAN önce fırlar — para kaybı
yoktur. Ama bu hata AI katmanlarındaki geniş `except Exception` blokları
tarafından normal parse/transport hatası gibi ele alınırsa kelimeler
fallback/karantinaya düşer ve CANLI SEÇİM değişir. Shadow'un "canlı
sonucu değiştirmez" garantisi de bu yüzden zayıflıyordu.

Kilitlenen sözleşme:
1. Seçim yolundaki HER AI katmanı (intent, marka filtresi, ADS/SEO/SOCIAL
   prefilter) `BudgetError`'ı YENİDEN FIRLATIR — fallback üretmez.
2. Motor seviyesinde koşu TEMİZ DÜŞER: run `failed`, final havuz YAZILMAZ,
   karantina/fallback satırı BIRAKILMAZ.
3. Seçimden SONRA çalışan SEO metadata adımı istisnadır: run düşmez ama
   sebep `BUDGET_EXCEEDED` olarak AÇIKÇA etiketlenir.
"""
import pytest

from app.core.channel.brand_filter import BrandExclusionFilter
from app.core.channel.intent_analyzer import IntentAnalyzer
from app.core.channel.pre_filters.ads_prefilter import AdsPreFilter
from app.core.channel.pre_filters.seo_prefilter import SeoPreFilter
from app.core.telemetry.ai_cost_budget import BudgetExceeded
from app.database.models import (
    ChannelCandidate,
    ChannelPool,
    IntentAnalysis,
    Keyword,
    KeywordScore,
    PreFilterResult,
    ScoringRun,
)


class BudgetBlockedAI:
    """Cap dolu: her çağrı provider'a GİTMEDEN reddedilir."""

    def __init__(self):
        self.calls = 0

    def _fail(self, *args, **kwargs):
        self.calls += 1
        raise BudgetExceeded(
            "downstream cap 2.00 USD asilacakti — cagri YAPILMADI")

    complete_json = _fail
    complete = _fail

    def for_stage(self, stage):
        return self


@pytest.fixture
def run_with_candidates(db_session, make_workspace):
    ws = make_workspace("Budget WS", profile_data={"exclude_themes": ["kripto"]},
                        status="confirmed")
    run = ScoringRun(run_name="budget", brand_profile_id=ws.id,
                     total_keywords=8, ads_capacity=3, seo_capacity=3,
                     social_capacity=3, status="channel_assigning",
                     default_relevance_coefficient=1.0)
    db_session.add(run)
    db_session.commit()
    for i in range(8):
        kw = Keyword(keyword=f"butce kelime {i}", monthly_volume=900 - i)
        db_session.add(kw)
        db_session.flush()
        db_session.add(KeywordScore(
            scoring_run_id=run.id, keyword_id=kw.id, ads_score=40 - i,
            seo_score=40 - i, social_score=40 - i, ads_rank=i + 1,
            seo_rank=i + 1, social_rank=i + 1))
        for channel in ("ADS", "SEO", "SOCIAL"):
            db_session.add(ChannelCandidate(
                scoring_run_id=run.id, keyword_id=kw.id, channel=channel,
                raw_score=40 - i, rank_in_channel=i + 1))
    db_session.commit()
    return {"ws": ws, "run": run}


class TestSelectionLayersRethrow:
    """İnvaryant 1: seçim yolundaki katmanlar bütçe hatasını YUTMAZ."""

    def test_intent_analyzer_rethrows(self, db_session, run_with_candidates):
        ai = BudgetBlockedAI()
        with pytest.raises(BudgetExceeded):
            IntentAnalyzer(db_session, ai).analyze_candidates(
                run_with_candidates["run"].id, "ADS")
        # Fallback intent satırı YAZILMADI
        assert db_session.query(IntentAnalysis).count() == 0

    def test_ads_prefilter_rethrows(self, db_session, run_with_candidates):
        run = run_with_candidates["run"]
        for cand in db_session.query(ChannelCandidate).filter_by(
                scoring_run_id=run.id, channel="ADS"):
            db_session.add(IntentAnalysis(
                scoring_run_id=run.id, keyword_id=cand.keyword_id,
                channel="ADS", intent_type="transactional", is_passed=True,
                source="ai"))
        db_session.commit()
        ai = BudgetBlockedAI()
        with pytest.raises(BudgetExceeded):
            AdsPreFilter(db_session, ai).filter_candidates(run.id)
        # Karantina (fallback) satırı YAZILMADI
        assert db_session.query(PreFilterResult).filter_by(
            is_fallback=True).count() == 0

    def test_brand_filter_rethrows(self, db_session, run_with_candidates):
        run = run_with_candidates["run"]
        for cand in db_session.query(ChannelCandidate).filter_by(
                scoring_run_id=run.id, channel="ADS"):
            db_session.add(IntentAnalysis(
                scoring_run_id=run.id, keyword_id=cand.keyword_id,
                channel="ADS", intent_type="transactional", is_passed=True,
                source="ai"))
        db_session.commit()
        ai = BudgetBlockedAI()
        with pytest.raises(BudgetExceeded):
            BrandExclusionFilter(db_session, ai).filter_intent_passed(run.id)

    def test_no_provider_call_is_attempted_twice(self, db_session,
                                                 run_with_candidates):
        """Bütçe hatası retry ZİNCİRİNİ de tetiklemez (boşa deneme yok)."""
        ai = BudgetBlockedAI()
        with pytest.raises(BudgetExceeded):
            IntentAnalyzer(db_session, ai).analyze_candidates(
                run_with_candidates["run"].id, "SEO")
        assert ai.calls == 1


class TestEngineFailsClean:
    """İnvaryant 2: koşu temiz düşer — yarım havuz/karantina kalmaz."""

    def test_assignment_fails_without_writing_final_pool(
            self, db_session, run_with_candidates):
        from app.core.channel.channel_engine import ChannelEngine

        run = run_with_candidates["run"]
        run.status = "channel_assigning"
        db_session.commit()
        engine = ChannelEngine(db_session, BudgetBlockedAI())
        with pytest.raises(BudgetExceeded):
            engine.run_channel_assignment(run.id)
        db_session.rollback()
        db_session.expire_all()
        assert db_session.query(ChannelPool).filter_by(
            scoring_run_id=run.id).count() == 0
        assert db_session.query(PreFilterResult).filter_by(
            scoring_run_id=run.id, is_fallback=True).count() == 0
        assert db_session.get(ScoringRun, run.id).status == "failed"


class TestMetadataIsLabelledNotSilent:
    """İnvaryant 3: seçim SONRASI adım run'ı düşürmez ama sebebi yazar."""

    def test_metadata_budget_error_is_reason_coded(self, db_session,
                                                   run_with_candidates):
        from app.core.channel.channel_engine import ChannelEngine

        run = run_with_candidates["run"]
        keyword_ids = [c.keyword_id for c in db_session.query(ChannelCandidate)
                       .filter_by(scoring_run_id=run.id, channel="SEO")]
        for rank, kid in enumerate(keyword_ids, 1):
            db_session.add(ChannelPool(
                scoring_run_id=run.id, keyword_id=kid, channel="SEO",
                final_rank=rank))
        db_session.commit()
        engine = ChannelEngine(db_session, BudgetBlockedAI())
        out = engine._run_seo_metadata_step(run.id, run, task_id=None)
        assert out["reason_code"] == "BUDGET_EXCEEDED"
        assert out["updated"] == 0
        assert out["unavailable"] == len(keyword_ids)

    def test_generic_transport_error_stays_in_metadata_fallback(
            self, db_session,
                                                    run_with_candidates):
        from app.core.channel.channel_engine import ChannelEngine

        class BoomAI(BudgetBlockedAI):
            def _fail(self, *args, **kwargs):
                raise RuntimeError("transport patladi")

            complete_json = _fail
            complete = _fail

        run = run_with_candidates["run"]
        db_session.add(ChannelPool(
            scoring_run_id=run.id, keyword_id=db_session.query(
                ChannelCandidate).filter_by(
                    scoring_run_id=run.id, channel="SEO").first().keyword_id,
            channel="SEO", final_rank=1))
        db_session.commit()
        out = ChannelEngine(db_session, BoomAI())._run_seo_metadata_step(
            run.id, run, task_id=None)
        # Taşıma hatası metadata'nın KENDİ zincirinde çözülür: motor
        # seviyesine çıkmaz, dolayısıyla bütçe etiketi de OLUŞMAZ
        assert out.get("reason_code") is None
        assert out["unavailable"] >= 1
        assert out["updated"] == 0
