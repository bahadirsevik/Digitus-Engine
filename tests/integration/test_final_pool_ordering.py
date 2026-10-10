"""
Skorlama v2 Aşama 2 — final havuz seçim semantiği testleri.

Doğrulanan kurallar (doküman Bölüm 7):
- Sınıf-öncelikli sıralama: (-sınıf, -adjusted, -hacim, keyword)
- SEO seçim skoru: N_SEO + SEO_W_GT*gt + SEO_W_GA*ga (log-ölçek özelliği)
- "SEO intent elemez ama price filter eler" ayrımı
- Relevance izolasyonu: ham KeywordScore değişmez, ChannelPool.adjusted_score değişir
- Çift kanal etiketleri: is_strategic (ADS ∩ SEO), rising_opportunity (SOCIAL)
- SOCIAL eleme kuralı: sınıf 0 VE MB <= eşik
"""
import json
from decimal import Decimal

import pytest

from app.core.channel.channel_engine import ChannelEngine
from app.core.channel.intent_analyzer import IntentAnalyzer
from app.core.channel.pre_filters.seo_prefilter import SeoPreFilter
from app.core.channel.pre_filters.social_prefilter import SocialPreFilter
from app.core.constants import RISING_OPPORTUNITY_LABEL, SEO_W_GT
from app.database.models import (
    ChannelCandidate,
    ChannelPool,
    IntentAnalysis,
    KeywordRelevance,
    KeywordScore,
    PreFilterResult,
)


class NoCallAI:
    """AI çağrısı beklenmeyen akışlar için — çağrılırsa patlar."""

    def complete_json(self, *args, **kwargs):
        raise AssertionError("Bu testte AI çağrısı beklenmiyordu")


def _seed_pipeline_row(
    db_session,
    *,
    run_id,
    keyword_id,
    channel,
    rank,
    intent_passed=True,
    is_kept=True,
    ai_class=None,
    gt=None,
    ga=None,
):
    db_session.add(ChannelCandidate(
        scoring_run_id=run_id,
        keyword_id=keyword_id,
        channel=channel,
        raw_score=Decimal("1.0"),
        rank_in_channel=rank,
    ))
    db_session.add(IntentAnalysis(
        scoring_run_id=run_id,
        keyword_id=keyword_id,
        channel=channel,
        intent_type="commercial",
        confidence_score=Decimal("0.90"),
        ai_reasoning="seed",
        is_passed=intent_passed,
        gt=gt,
        ga=ga,
    ))
    db_session.add(PreFilterResult(
        scoring_run_id=run_id,
        keyword_id=keyword_id,
        channel=channel,
        is_kept=is_kept,
        label="lead",
        ai_class=ai_class,
        ai_reasoning="seed",
        extra_data={"reason_code": "SEED"},
        is_fallback=False,
    ))


def _seed_score(
    db_session, *, run_id, keyword_id, score, rank=1, volume=1000, h=0.5, mb=0.5
):
    db_session.add(KeywordScore(
        scoring_run_id=run_id,
        keyword_id=keyword_id,
        ads_score=Decimal(str(score)),
        seo_score=Decimal(str(score)),
        social_score=Decimal(str(score)),
        ads_rank=rank,
        seo_rank=rank,
        social_rank=rank,
        metrics_snapshot={
            "monthly_volume": volume,
            "trend_3m": 0.0,
            "trend_12m": 0.0,
            "competition_score": 0.5,
            "derived": {"h": h, "mb": mb, "ln": 0.5, "trk": 0.0, "spec": "v2"},
        },
    ))


def _pool_order(db_session, run_id, channel):
    rows = (
        db_session.query(ChannelPool)
        .filter(ChannelPool.scoring_run_id == run_id, ChannelPool.channel == channel)
        .order_by(ChannelPool.final_rank)
        .all()
    )
    return [row.keyword_id for row in rows]


# ---------------------------------------------------------------------------
# Sınıf-öncelikli sıralama
# ---------------------------------------------------------------------------


def test_class_two_beats_class_one_regardless_of_score(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    ws = make_workspace("Class Order WS")
    run = make_scoring_run(
        brand_profile_id=ws.id, ads_capacity=10, enable_seo=False, enable_social=False
    )
    kw_lead_high = make_keyword("lead yuksek skor", brand_profile_id=ws.id)
    kw_hot_low = make_keyword("hot sale dusuk skor", brand_profile_id=ws.id)

    _seed_score(db_session, run_id=run.id, keyword_id=kw_lead_high.id, score=30, rank=1)
    _seed_score(db_session, run_id=run.id, keyword_id=kw_hot_low.id, score=5, rank=2)
    _seed_pipeline_row(
        db_session, run_id=run.id, keyword_id=kw_lead_high.id,
        channel="ADS", rank=1, ai_class=1,
    )
    _seed_pipeline_row(
        db_session, run_id=run.id, keyword_id=kw_hot_low.id,
        channel="ADS", rank=2, ai_class=2,
    )
    db_session.commit()

    engine = ChannelEngine(db_session, NoCallAI())
    counts = engine._build_final_pools_v2(run.id, run)

    assert counts["ADS"] == 2
    # Sınıf 2 (hot_sale), skoru 6 kat düşük olsa da önce gelir
    assert _pool_order(db_session, run.id, "ADS") == [kw_hot_low.id, kw_lead_high.id]


def test_tiebreak_volume_then_alphabetical(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    ws = make_workspace("Tiebreak WS")
    run = make_scoring_run(
        brand_profile_id=ws.id, ads_capacity=10, enable_seo=False, enable_social=False
    )
    kw_small = make_keyword("zebra konu", brand_profile_id=ws.id)
    kw_big = make_keyword("armut konu", brand_profile_id=ws.id)
    kw_alpha = make_keyword("elma konu", brand_profile_id=ws.id)

    # Aynı skor + aynı sınıf; hacimler: big=5000, small/alpha=100 (eşit)
    _seed_score(db_session, run_id=run.id, keyword_id=kw_small.id, score=10, rank=1, volume=100)
    _seed_score(db_session, run_id=run.id, keyword_id=kw_big.id, score=10, rank=2, volume=5000)
    _seed_score(db_session, run_id=run.id, keyword_id=kw_alpha.id, score=10, rank=3, volume=100)
    for rank, kw in enumerate((kw_small, kw_big, kw_alpha), 1):
        _seed_pipeline_row(
            db_session, run_id=run.id, keyword_id=kw.id,
            channel="ADS", rank=rank, ai_class=1,
        )
    db_session.commit()

    engine = ChannelEngine(db_session, NoCallAI())
    engine._build_final_pools_v2(run.id, run)

    # Hacim önce (armut 5000), sonra eşit hacimde alfabetik (elma < zebra)
    assert _pool_order(db_session, run.id, "ADS") == [kw_big.id, kw_alpha.id, kw_small.id]


def test_missing_ai_class_defaults_to_middle(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """Legacy/fixture satırları (ai_class=None) sınıf 1 sayılır: sınıf 2'nin
    arkasında, aynı sınıftaki skor sıralamasında yerinde."""
    ws = make_workspace("Default Class WS")
    run = make_scoring_run(
        brand_profile_id=ws.id, ads_capacity=10, enable_seo=False, enable_social=False
    )
    kw_legacy = make_keyword("legacy satir", brand_profile_id=ws.id)
    kw_hot = make_keyword("hot satir", brand_profile_id=ws.id)

    _seed_score(db_session, run_id=run.id, keyword_id=kw_legacy.id, score=50, rank=1)
    _seed_score(db_session, run_id=run.id, keyword_id=kw_hot.id, score=5, rank=2)
    _seed_pipeline_row(
        db_session, run_id=run.id, keyword_id=kw_legacy.id,
        channel="ADS", rank=1, ai_class=None,  # legacy
    )
    _seed_pipeline_row(
        db_session, run_id=run.id, keyword_id=kw_hot.id,
        channel="ADS", rank=2, ai_class=2,
    )
    db_session.commit()

    ChannelEngine(db_session, NoCallAI())._build_final_pools_v2(run.id, run)
    assert _pool_order(db_session, run.id, "ADS") == [kw_hot.id, kw_legacy.id]


# ---------------------------------------------------------------------------
# SEO: G_T/G_A seçim boost'u (dokümanın log-ölçek özelliği)
# ---------------------------------------------------------------------------


def test_seo_gt_boost_overcomes_higher_volume_keyword(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """G_T=1 olan düşük hacimli kelime, niyetsiz ~8x hacimli kelimeyi geçer."""
    ws = make_workspace("SEO Boost WS")
    run = make_scoring_run(
        brand_profile_id=ws.id, seo_capacity=10, enable_ads=False, enable_social=False
    )
    kw_intent = make_keyword("niyetli kucuk kelime", brand_profile_id=ws.id)
    kw_volume = make_keyword("niyetsiz buyuk kelime", brand_profile_id=ws.id)

    # N_SEO tabanları: 10 vs 20 (10 puan fark ~ 8x hacim log ölçekte);
    # gt boost +15 farkı kapatıp geçirmeli
    _seed_score(db_session, run_id=run.id, keyword_id=kw_intent.id, score=10, rank=2, volume=1000)
    _seed_score(db_session, run_id=run.id, keyword_id=kw_volume.id, score=20, rank=1, volume=8000)
    _seed_pipeline_row(
        db_session, run_id=run.id, keyword_id=kw_intent.id,
        channel="SEO", rank=2, gt=True, ga=False,
    )
    _seed_pipeline_row(
        db_session, run_id=run.id, keyword_id=kw_volume.id,
        channel="SEO", rank=1, gt=False, ga=False,
    )
    db_session.commit()

    ChannelEngine(db_session, NoCallAI())._build_final_pools_v2(run.id, run)

    assert _pool_order(db_session, run.id, "SEO") == [kw_intent.id, kw_volume.id]

    # adjusted_score = (base + 15*gt) * 0.5 (relevance yok -> konservatif 0.5)
    pool_row = (
        db_session.query(ChannelPool)
        .filter_by(scoring_run_id=run.id, keyword_id=kw_intent.id, channel="SEO")
        .one()
    )
    assert float(pool_row.adjusted_score) == pytest.approx((10 + SEO_W_GT) * 0.5)


# ---------------------------------------------------------------------------
# Kritik ayrım: SEO intent elemez AMA price filter eler
# ---------------------------------------------------------------------------


class SeoIntentAndMetadataAI:
    """Intent: herkese navigational + düşük güven (eski sistemde SEO'dan elerdi).
    SEO prefilter: metadata döner."""

    def complete_json(self, prompt=None, **kwargs):
        if "intent_type" in (prompt or ""):
            ids = []
            for line in (prompt or "").splitlines():
                line = line.strip()
                if line.startswith("- ") and ":" in line:
                    raw = line[2:].split(":", 1)[0].strip()
                    if raw.isdigit():
                        ids.append(int(raw))
            return json.dumps([
                {
                    "keyword_id": kid,
                    "intent_type": "navigational",
                    "confidence": 0.30,
                    "gt": 0,
                    "ga": 0,
                    "reasoning": "dusuk guven navigational",
                }
                for kid in ids
            ])
        # SEO metadata şeması
        import re
        ids = [int(m) for m in re.findall(r'"id":\s*(\d+)', prompt or "")]
        return json.dumps({
            "results": [
                {
                    "keyword_id": kid,
                    "depth_label": "treasure",
                    "reason": "Derin icerik uretilebilir",
                    "meta": {
                        "geo_suitable": False,
                        "h1_suggestion": "Baslik",
                        "h2_suggestions": ["Alt baslik"],
                    },
                }
                for kid in ids
            ]
        })


def test_seo_intent_passes_all_but_price_filter_eliminates(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """Kritik ayrım: SEO intent ELEMEZ ama fiyat filtresi eler; seçim
    aşamasında SIFIR AI çağrısı; metadata seçim SONRASI ve is_kept'e dokunmaz."""
    ws = make_workspace("SEO Price WS")
    run = make_scoring_run(
        brand_profile_id=ws.id, seo_capacity=10, enable_ads=False, enable_social=False
    )
    kw_price = make_keyword("akilli saat fiyat", brand_profile_id=ws.id)
    kw_info = make_keyword("akilli saat nasil secilir", brand_profile_id=ws.id)

    for rank, kw in enumerate((kw_price, kw_info), 1):
        _seed_score(db_session, run_id=run.id, keyword_id=kw.id, score=10, rank=rank)
        db_session.add(ChannelCandidate(
            scoring_run_id=run.id,
            keyword_id=kw.id,
            channel="SEO",
            raw_score=Decimal("1.0"),
            rank_in_channel=rank,
        ))
    db_session.commit()

    ai = SeoIntentAndMetadataAI()

    # 1) Intent: navigational + 0.30 güven bile olsa SEO'da HERKES geçer
    IntentAnalyzer(db_session, ai).analyze_candidates(run.id, "SEO")
    intents = {
        row.keyword_id: row
        for row in db_session.query(IntentAnalysis).filter_by(
            scoring_run_id=run.id, channel="SEO"
        )
    }
    assert intents[kw_price.id].is_passed is True   # intent ELEMEZ
    assert intents[kw_info.id].is_passed is True

    # 2) SEÇİM prefilter'ı: SIFIR AI çağrısı (NoCallAI kanıtlar) —
    #    fiyat deterministik PRICE_TERM ile elenir, kalan deterministik keep
    SeoPreFilter(db_session, NoCallAI()).filter_candidates(run.id)
    pf = {
        row.keyword_id: row
        for row in db_session.query(PreFilterResult).filter_by(
            scoring_run_id=run.id, channel="SEO"
        )
    }
    assert pf[kw_price.id].is_kept is False
    assert (pf[kw_price.id].extra_data or {}).get("reason_code") == "PRICE_TERM"
    assert pf[kw_info.id].is_kept is True
    assert (pf[kw_info.id].extra_data or {}).get("reason_code") == "SEO_KEEP_DEFAULT"

    # 3) Final havuz: fiyat YOK, bilgi VAR
    ChannelEngine(db_session, NoCallAI())._build_final_pools_v2(run.id, run)
    assert _pool_order(db_session, run.id, "SEO") == [kw_info.id]

    # 4) Metadata SEÇİM SONRASI, yalnız final havuz için — h1 persist,
    #    is_kept değişmedi
    result = SeoPreFilter(db_session, ai).generate_metadata(run.id, [kw_info.id])
    assert result["updated"] == 1
    db_session.expire_all()
    row = (
        db_session.query(PreFilterResult)
        .filter_by(scoring_run_id=run.id, keyword_id=kw_info.id, channel="SEO")
        .one()
    )
    assert row.is_kept is True  # metadata seçimi DEĞİŞTİREMEZ
    assert (row.extra_data or {}).get("h1_suggestion") == "Baslik"
    assert (row.extra_data or {}).get("reason_code") == "SEO_METADATA"
    assert row.label == "treasure"


def test_own_brand_price_query_still_price_blocked_single_row(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """Own-brand + fiyat sorgusu brand-defense'ı DEĞİL fiyat filtresini bulur
    (hook brand-defense'tan önce) ve yalnız BİR PreFilterResult satırı oluşur."""
    ws = make_workspace(
        "OwnBrand Price WS",
        profile_data={"brand_terms": ["hissefy"], "company_name": "Hissefy",
                      "exclude_themes": []},
    )
    run = make_scoring_run(
        brand_profile_id=ws.id, seo_capacity=10, enable_ads=False, enable_social=False
    )
    kw = make_keyword("hissefy fiyat", brand_profile_id=ws.id)
    _seed_score(db_session, run_id=run.id, keyword_id=kw.id, score=10, rank=1)
    db_session.add(ChannelCandidate(
        scoring_run_id=run.id, keyword_id=kw.id, channel="SEO",
        raw_score=Decimal("1.0"), rank_in_channel=1,
    ))
    db_session.add(IntentAnalysis(
        scoring_run_id=run.id, keyword_id=kw.id, channel="SEO",
        intent_type="commercial", confidence_score=Decimal("0.90"),
        ai_reasoning="seed", is_passed=True,
    ))
    db_session.commit()

    SeoPreFilter(db_session, NoCallAI()).filter_candidates(run.id)

    rows = (
        db_session.query(PreFilterResult)
        .filter_by(scoring_run_id=run.id, keyword_id=kw.id, channel="SEO")
        .all()
    )
    assert len(rows) == 1  # çift kayıt yok
    assert rows[0].is_kept is False
    assert (rows[0].extra_data or {}).get("reason_code") == "PRICE_TERM"


# ---------------------------------------------------------------------------
# Relevance izolasyonu: ham skor değişmez, adjusted değişir
# ---------------------------------------------------------------------------


def test_relevance_changes_adjusted_but_not_raw_keyword_score(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    ws = make_workspace("Relevance Isolation WS")  # status=confirmed (default)
    run = make_scoring_run(
        brand_profile_id=ws.id, seo_capacity=10, enable_ads=False, enable_social=False
    )
    kw = make_keyword("izolasyon kelimesi", brand_profile_id=ws.id)

    _seed_score(db_session, run_id=run.id, keyword_id=kw.id, score=10, rank=1)
    _seed_pipeline_row(
        db_session, run_id=run.id, keyword_id=kw.id,
        channel="SEO", rank=1, gt=False, ga=False,
    )
    db_session.add(KeywordRelevance(
        scoring_run_id=run.id,
        keyword_id=kw.id,
        relevance_score=Decimal("0.800"),
        method="embedding",
    ))
    db_session.commit()

    raw_before = (
        db_session.query(KeywordScore)
        .filter_by(scoring_run_id=run.id, keyword_id=kw.id)
        .one()
        .seo_score
    )

    ChannelEngine(db_session, NoCallAI())._build_final_pools_v2(run.id, run)

    raw_after = (
        db_session.query(KeywordScore)
        .filter_by(scoring_run_id=run.id, keyword_id=kw.id)
        .one()
        .seo_score
    )
    pool_row = (
        db_session.query(ChannelPool)
        .filter_by(scoring_run_id=run.id, keyword_id=kw.id, channel="SEO")
        .one()
    )

    # Ham KeywordScore saf doküman skoru olarak KALIR
    assert raw_after == raw_before == Decimal("10.0000")
    # Relevance yalnızca havuz sıralamasındaki adjusted_score'a işler
    assert float(pool_row.adjusted_score) == pytest.approx(10 * 0.8)
    assert float(pool_row.relevance_score) == pytest.approx(0.8)


# ---------------------------------------------------------------------------
# Çift kanal etiketleri
# ---------------------------------------------------------------------------


def test_strategic_label_marks_ads_seo_intersection(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    ws = make_workspace("Strategic WS")
    run = make_scoring_run(
        brand_profile_id=ws.id, ads_capacity=10, seo_capacity=10, enable_social=False
    )
    kw_shared = make_keyword("ortak kelime", brand_profile_id=ws.id)
    kw_ads_only = make_keyword("sadece ads kelime", brand_profile_id=ws.id)

    _seed_score(db_session, run_id=run.id, keyword_id=kw_shared.id, score=10, rank=1)
    _seed_score(db_session, run_id=run.id, keyword_id=kw_ads_only.id, score=8, rank=2)
    _seed_pipeline_row(db_session, run_id=run.id, keyword_id=kw_shared.id,
                       channel="ADS", rank=1, ai_class=2)
    _seed_pipeline_row(db_session, run_id=run.id, keyword_id=kw_ads_only.id,
                       channel="ADS", rank=2, ai_class=1)
    _seed_pipeline_row(db_session, run_id=run.id, keyword_id=kw_shared.id,
                       channel="SEO", rank=1, gt=True, ga=True)
    db_session.commit()

    ChannelEngine(db_session, NoCallAI())._build_final_pools_v2(run.id, run)

    pools = {
        (row.channel, row.keyword_id): row
        for row in db_session.query(ChannelPool).filter_by(scoring_run_id=run.id)
    }
    assert pools[("ADS", kw_shared.id)].is_strategic is True
    assert pools[("SEO", kw_shared.id)].is_strategic is True
    assert pools[("ADS", kw_ads_only.id)].is_strategic is False


def test_rising_opportunity_label_requires_top_slice_and_high_h(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    ws = make_workspace("Rising WS")
    run = make_scoring_run(
        brand_profile_id=ws.id, social_capacity=10, enable_ads=False, enable_seo=False
    )
    # cutoff = ceil(10 * 0.2) = 2 -> rank 1-2 aday; H >= 0.7 şartı
    kw_rising = make_keyword("yukselen konu", brand_profile_id=ws.id)     # rank 1, H yüksek
    kw_low_h = make_keyword("dusuk h konu", brand_profile_id=ws.id)       # rank 2, H düşük
    kw_below = make_keyword("alt dilim konu", brand_profile_id=ws.id)     # rank 3, H yüksek

    _seed_score(db_session, run_id=run.id, keyword_id=kw_rising.id,
                score=30, rank=1, h=0.9)
    _seed_score(db_session, run_id=run.id, keyword_id=kw_low_h.id,
                score=20, rank=2, h=0.3)
    _seed_score(db_session, run_id=run.id, keyword_id=kw_below.id,
                score=10, rank=3, h=0.9)
    for rank, kw in enumerate((kw_rising, kw_low_h, kw_below), 1):
        _seed_pipeline_row(db_session, run_id=run.id, keyword_id=kw.id,
                           channel="SOCIAL", rank=rank, ai_class=2)
    db_session.commit()

    ChannelEngine(db_session, NoCallAI())._build_final_pools_v2(run.id, run)

    pools = {
        row.keyword_id: row
        for row in db_session.query(ChannelPool).filter_by(
            scoring_run_id=run.id, channel="SOCIAL"
        )
    }
    assert pools[kw_rising.id].pool_label == RISING_OPPORTUNITY_LABEL
    assert pools[kw_low_h.id].pool_label is None    # üst dilimde ama H düşük
    assert pools[kw_below.id].pool_label is None    # H yüksek ama dilim dışı


# ---------------------------------------------------------------------------
# SOCIAL eleme kuralı: sınıf 0 VE MB <= eşik
# ---------------------------------------------------------------------------


class ZeroDimsSocialAI:
    """Tüm boyutları 0 dönen sosyal AI — eleme kararını kod vermeli."""

    def complete_json(self, prompt=None, **kwargs):
        import re
        ids = [int(m) for m in re.findall(r'"id":\s*(\d+)', prompt or "")]
        return json.dumps({
            "results": [
                {
                    "keyword_id": kid,
                    "dims": {
                        "opinion_discussion": 0,
                        "curiosity_comparison": 0,
                        "agenda_theme": 0,
                    },
                    "reason": "Konusma degeri yok",
                    "meta": {"hook": "Hook", "scenario_note": "Not"},
                }
                for kid in ids
            ]
        })


def test_social_class_zero_eliminated_only_when_momentum_low(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    ws = make_workspace("Social Momentum WS")
    run = make_scoring_run(
        brand_profile_id=ws.id, social_capacity=10, enable_ads=False, enable_seo=False
    )
    kw_dead = make_keyword("olu konu", brand_profile_id=ws.id)        # MB düşük -> elenir
    kw_momentum = make_keyword("ivmeli konu", brand_profile_id=ws.id)  # MB yüksek -> kalır

    _seed_score(db_session, run_id=run.id, keyword_id=kw_dead.id,
                score=10, rank=1, mb=0.1)
    _seed_score(db_session, run_id=run.id, keyword_id=kw_momentum.id,
                score=10, rank=2, mb=0.8)
    for rank, kw in enumerate((kw_dead, kw_momentum), 1):
        db_session.add(ChannelCandidate(
            scoring_run_id=run.id, keyword_id=kw.id, channel="SOCIAL",
            raw_score=Decimal("1.0"), rank_in_channel=rank,
        ))
        db_session.add(IntentAnalysis(
            scoring_run_id=run.id, keyword_id=kw.id, channel="SOCIAL",
            intent_type="trend_worthy", confidence_score=Decimal("0.90"),
            ai_reasoning="seed", is_passed=True,
        ))
    db_session.commit()

    SocialPreFilter(db_session, ZeroDimsSocialAI()).filter_candidates(run.id)

    pf = {
        row.keyword_id: row
        for row in db_session.query(PreFilterResult).filter_by(
            scoring_run_id=run.id, channel="SOCIAL"
        )
    }
    # Sınıf 0 + MB 0.1 <= 0.3 -> elenir
    assert pf[kw_dead.id].is_kept is False
    assert pf[kw_dead.id].ai_class == 0
    # Sınıf 0 ama MB 0.8 > 0.3 -> momentum kurtarması
    assert pf[kw_momentum.id].is_kept is True
    assert (pf[kw_momentum.id].extra_data or {}).get("reason_code") == "MOMENTUM_RESCUE"
    # hook/scenario_note downstream için korunur
    assert (pf[kw_momentum.id].extra_data or {}).get("hook") == "Hook"
