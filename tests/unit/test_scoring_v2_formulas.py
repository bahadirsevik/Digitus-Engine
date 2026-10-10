"""
Skorlama v2 formül testleri (Digitus_Engine_v2_Skorlama_Algoritmalari.md).

Golden vektörler elle hesaplandı; H/Ln/MB liste-göreli olduğundan
vektörler 5 kelimelik sabit liste üzerinde doğrulanır.
"""
import pytest

from app.core.constants import (
    ADS_W_COMPETITION,
    LN_EQUAL_VOLUME_VALUE,
    SCORE_W_VOLUME,
    SEO_TREND_FLOOR,
)
from app.core.scoring.ads_scorer import calculate_ads_score, calculate_bulk_ads_scores
from app.core.scoring.normalizer import (
    avg_rank_percentile,
    combined_trend_trk,
    derive_stage1_variables,
    normalized_log_volume,
)
from app.core.scoring.score_engine import ScoreEngine, _normalize_scoring_metrics
from app.core.scoring.seo_scorer import calculate_seo_score, calculate_bulk_seo_scores
from app.core.scoring.social_scorer import (
    calculate_social_score,
    calculate_bulk_social_scores,
)

APPROX_ABS = 2e-3  # golden tablo 4 haneli; ara yuvarlama farkı toleransı


# ---------------------------------------------------------------------------
# Türetilmiş değişkenler
# ---------------------------------------------------------------------------


def test_avg_rank_percentile_ties_get_equal_score():
    values = [10, 20, 20, 30]
    result = avg_rank_percentile(values)
    # H(x) = (count(<x) + count(<=x)) / 2n
    assert result[0] == pytest.approx(1 / 8)   # (0+1)/8
    assert result[1] == pytest.approx(4 / 8)   # (1+3)/8
    assert result[2] == result[1]              # eşit değer = eşit puan
    assert result[3] == pytest.approx(7 / 8)   # (3+4)/8


def test_avg_rank_percentile_single_item_is_half():
    assert avg_rank_percentile([42]) == [pytest.approx(0.5)]


def test_avg_rank_percentile_bounds_open_interval():
    result = avg_rank_percentile([1, 2, 3, 4, 5])
    assert all(0 < h < 1 for h in result)


def test_normalized_log_volume_minmax_and_guards():
    result = normalized_log_volume([10, 100, 1000])
    assert result[0] == pytest.approx(0.0)
    assert result[1] == pytest.approx(0.5)
    assert result[2] == pytest.approx(1.0)
    # Guard: hepsi eşit / n=1 -> LN_EQUAL_VOLUME_VALUE
    assert normalized_log_volume([500, 500]) == [LN_EQUAL_VOLUME_VALUE] * 2
    assert normalized_log_volume([500]) == [LN_EQUAL_VOLUME_VALUE]


def test_combined_trend_trk_weighting_and_clip():
    assert combined_trend_trk(0.5, 0.2) == pytest.approx(0.4)  # (1.0+0.2)/3
    assert combined_trend_trk(3.0, 3.0) == pytest.approx(1.0)  # üst kırpma
    assert combined_trend_trk(-1.0, -1.0) == pytest.approx(-1.0)  # alt kırpma


# ---------------------------------------------------------------------------
# Ön temizlik (_normalize_scoring_metrics)
# ---------------------------------------------------------------------------


def test_preclean_converts_percent_to_ratio_and_clips():
    metrics = _normalize_scoring_metrics(1000, 56.0, 400.0, 0.5)
    assert metrics["trend_3m"] == pytest.approx(0.56)   # +%56 -> 0.56
    assert metrics["trend_12m"] == pytest.approx(3.0)   # +%400 -> +3 kırpma
    assert metrics["_raw_trend_3m"] == pytest.approx(56.0)   # snapshot ham yüzde
    assert metrics["_raw_trend_12m"] == pytest.approx(400.0)

    metrics = _normalize_scoring_metrics(1000, -150.0, None, None)
    assert metrics["trend_3m"] == pytest.approx(-1.0)   # -%150 -> -1 kırpma
    assert metrics["trend_12m"] == pytest.approx(0.0)   # null -> 0
    assert metrics["competition_score"] == pytest.approx(0.0)  # null -> 0


def test_preclean_drops_invalid_volume_rows():
    assert _normalize_scoring_metrics(0, 10.0, 10.0, 0.5) is None
    assert _normalize_scoring_metrics(None, 10.0, 10.0, 0.5) is None
    assert _normalize_scoring_metrics(-5, 10.0, 10.0, 0.5) is None
    assert _normalize_scoring_metrics("bozuk", 10.0, 10.0, 0.5) is None


def test_preclean_clamps_competition_into_unit_interval():
    assert _normalize_scoring_metrics(100, 0, 0, 1.7)["competition_score"] == 1.0
    assert _normalize_scoring_metrics(100, 0, 0, -0.2)["competition_score"] == 0.0


# ---------------------------------------------------------------------------
# Golden vektörler (n=5, elle hesaplandı)
# ---------------------------------------------------------------------------

GOLDEN = [
    # (kw, V, T3, T12, C, H, Ln, TrK, MB, N_ADS, N_SEO_taban, N_SOC)
    ("alpha",   50,     1.2,  -1.0, 0.90, 0.1, 0.0,    0.4667,  0.5, -4.5,  1.4,     27.0),
    ("beta",    1000,   0.5,   0.2, 0.50, 0.4, 0.3365, 0.4,     0.7, 11.0,  14.6583, 43.0),
    ("gamma",   1000,   0.0,   0.0, 0.00, 0.4, 0.3365, 0.0,     0.2, 16.0,  13.4583, 18.0),
    ("delta",   10000,  0.2,   0.4, 0.35, 0.7, 0.5951, 0.2667,  0.9, 23.75, 24.6028, 59.0),
    ("epsilon", 368000, -0.4,  0.1, 0.70, 0.9, 1.0,   -0.2333,  0.2, 25.5,  39.3,    28.0),
]


def _golden_keywords():
    return [
        {
            "id": idx + 1,
            "keyword": row[0],
            "monthly_volume": row[1],
            "trend_3m": row[2],
            "trend_12m": row[3],
            "competition_score": row[4],
        }
        for idx, row in enumerate(GOLDEN)
    ]


def test_golden_derived_variables():
    keywords = _golden_keywords()
    derive_stage1_variables(keywords)
    for kw, row in zip(keywords, GOLDEN):
        _, _, _, _, _, h, ln, trk, mb, _, _, _ = row
        assert kw["h"] == pytest.approx(h, abs=APPROX_ABS), kw["keyword"]
        assert kw["ln"] == pytest.approx(ln, abs=APPROX_ABS), kw["keyword"]
        assert kw["trk"] == pytest.approx(trk, abs=APPROX_ABS), kw["keyword"]
        assert kw["mb"] == pytest.approx(mb, abs=APPROX_ABS), kw["keyword"]


def test_golden_channel_scores():
    keywords = _golden_keywords()
    ads = {r["keyword_id"]: r["ads_score"] for r in calculate_bulk_ads_scores(keywords)}
    seo = {r["keyword_id"]: r["seo_score"] for r in calculate_bulk_seo_scores(keywords)}
    soc = {r["keyword_id"]: r["social_score"] for r in calculate_bulk_social_scores(keywords)}

    for idx, row in enumerate(GOLDEN, start=1):
        kw, *_rest = row
        n_ads, n_seo, n_soc = row[9], row[10], row[11]
        assert ads[idx] == pytest.approx(n_ads, abs=APPROX_ABS), kw
        assert seo[idx] == pytest.approx(n_seo, abs=APPROX_ABS), kw
        assert soc[idx] == pytest.approx(n_soc, abs=APPROX_ABS), kw


def test_golden_structural_properties():
    """Negatif N_ADS mümkün; SOCIAL'da T12 etkisiz; SEO/SOCIAL'da rekabet etkisiz."""
    keywords = _golden_keywords()
    derive_stage1_variables(keywords)

    # alpha: yüksek rekabet + düşük hacim -> negatif ADS skoru
    assert calculate_ads_score(keywords[0]["h"], 0.9, 1.2) < 0

    # SOCIAL T12'den bağımsız: T12 değişse de N_SOC aynı (H ve MB sadece V/T3'ten)
    a = _golden_keywords()
    b = _golden_keywords()
    for kw in b:
        kw["trend_12m"] = kw["trend_12m"] + 0.5
    soc_a = calculate_bulk_social_scores(a)
    soc_b = calculate_bulk_social_scores(b)
    assert [r["social_score"] for r in soc_a] == [r["social_score"] for r in soc_b]

    # SEO/SOCIAL rekabetten bağımsız
    c = _golden_keywords()
    for kw in c:
        kw["competition_score"] = 0.0
    seo_ref = [r["seo_score"] for r in calculate_bulk_seo_scores(_golden_keywords())]
    seo_nocomp = [r["seo_score"] for r in calculate_bulk_seo_scores(c)]
    assert seo_ref == seo_nocomp


def test_score_bounds():
    # N_ADS: [-15, 45] — min: H~0, C=1, T3<=0; max: H~1, C=0, T3>=1
    assert calculate_ads_score(0.0, 1.0, -1.0) == pytest.approx(-ADS_W_COMPETITION)
    assert calculate_ads_score(1.0, 0.0, 1.0) == pytest.approx(45.0)
    # N_SEO taban: [-1.5, 43]
    assert calculate_seo_score(0.0, -1.0) == pytest.approx(3 * SEO_TREND_FLOOR)
    assert calculate_seo_score(1.0, 1.0) == pytest.approx(SCORE_W_VOLUME + 3.0)
    # N_SOC: (0, 70)
    assert calculate_social_score(1.0, 1.0) == pytest.approx(70.0)
    assert calculate_social_score(0.0, 0.0) == pytest.approx(0.0)


def test_seo_evergreen_floor_limits_decline_penalty():
    # Düşen kelime en fazla -1.5 puan yiyebilir
    assert calculate_seo_score(0.5, -1.0) == calculate_seo_score(0.5, -0.5)
    assert calculate_seo_score(0.5, -0.5) == pytest.approx(0.5 * SCORE_W_VOLUME - 1.5)


def test_ads_freshness_only_rewards_positive_trend_capped_at_one():
    h, c = 0.5, 0.2
    base = calculate_ads_score(h, c, 0.0)
    assert calculate_ads_score(h, c, -0.8) == base          # negatif ceza YOK
    assert calculate_ads_score(h, c, 2.5) == calculate_ads_score(h, c, 1.0)  # +%100 tavan


# ---------------------------------------------------------------------------
# Deterministik tie-break: skor -> hacim -> alfabetik
# ---------------------------------------------------------------------------


def test_bulk_tiebreak_score_volume_alphabetical():
    # İki kelime aynı H/MB'ye düşecek şekilde: eşit hacim, eşit trendler
    keywords = [
        {"id": 1, "keyword": "zebra", "monthly_volume": 500,
         "trend_3m": 0.0, "trend_12m": 0.0, "competition_score": 0.0},
        {"id": 2, "keyword": "elma", "monthly_volume": 500,
         "trend_3m": 0.0, "trend_12m": 0.0, "competition_score": 0.0},
        {"id": 3, "keyword": "armut", "monthly_volume": 2000,
         "trend_3m": 0.0, "trend_12m": 0.0, "competition_score": 0.0},
    ]
    results = calculate_bulk_social_scores(keywords)
    # armut (yüksek hacim) önce; elma/zebra eşit skorda alfabetik
    assert [r["keyword_id"] for r in results] == [3, 2, 1]
    assert [r["social_rank"] for r in results] == [1, 2, 3]


# ---------------------------------------------------------------------------
# DB-backed: run_scoring ön temizlik + snapshot + relevance clamp
# ---------------------------------------------------------------------------


def test_run_scoring_drops_zero_volume_and_reports_skipped(
    db_session, make_workspace, make_keyword
):
    ws = make_workspace("V2 Preclean WS")
    make_keyword("gecerli kelime", brand_profile_id=ws.id, monthly_volume=1000)
    make_keyword("hacimsiz kelime", brand_profile_id=ws.id, monthly_volume=0)

    engine = ScoreEngine(db_session)
    run = engine.create_scoring_run(
        ads_capacity=10, seo_capacity=10, social_capacity=10,
        brand_profile_id=ws.id, skip_relevance=True,
    )
    result = engine.run_scoring(run.id)

    assert result["scored_count"] == 1
    assert result["skipped_invalid_metrics"] == 1
    db_session.refresh(run)
    assert run.status == "scored"


def test_run_scoring_all_invalid_completes_with_zero(
    db_session, make_workspace, make_keyword
):
    ws = make_workspace("V2 Empty WS")
    make_keyword("hacimsiz bir", brand_profile_id=ws.id, monthly_volume=0)
    make_keyword("hacimsiz iki", brand_profile_id=ws.id, monthly_volume=0)

    engine = ScoreEngine(db_session)
    run = engine.create_scoring_run(
        ads_capacity=10, seo_capacity=10, social_capacity=10,
        brand_profile_id=ws.id, skip_relevance=True,
    )
    result = engine.run_scoring(run.id)

    assert result["scored_count"] == 0
    assert result["skipped_invalid_metrics"] == 2
    db_session.refresh(run)
    assert run.status == "scored"


def test_run_scoring_snapshot_contract(db_session, make_workspace, make_keyword):
    """Üst düzey trendler ham yüzde; derived.t3/t12 oran+kırpılmış."""
    from app.database.models import KeywordScore

    ws = make_workspace("V2 Snapshot WS")
    kw = make_keyword(
        "snapshot kelime", brand_profile_id=ws.id,
        monthly_volume=1000, trend_3m=56.0, trend_12m=400.0, competition_score=0.5,
    )

    engine = ScoreEngine(db_session)
    run = engine.create_scoring_run(
        ads_capacity=10, seo_capacity=10, social_capacity=10,
        brand_profile_id=ws.id, skip_relevance=True,
    )
    engine.run_scoring(run.id)

    score = (
        db_session.query(KeywordScore)
        .filter(KeywordScore.scoring_run_id == run.id,
                KeywordScore.keyword_id == kw.id)
        .one()
    )
    snap = score.metrics_snapshot
    assert snap["trend_3m"] == pytest.approx(56.0)     # ham yüzde
    assert snap["trend_12m"] == pytest.approx(400.0)
    derived = snap["derived"]
    assert derived["spec"] == "v2"
    assert derived["t3"] == pytest.approx(0.56)        # oran
    assert derived["t12"] == pytest.approx(3.0)        # +3 kırpılmış
    for field in ("h", "ln", "trk", "mb"):
        assert derived[field] is not None


def test_relevance_clamp_negative_scores_never_outrank(
    db_session, make_workspace, make_keyword, make_scoring_run, make_keyword_score
):
    """Negatif ham skor x relevance, hiçbir pozitif skorun önüne geçemez;
    negatifler arasında 'daha az alakalı olan öne geçer' ters çevirmesi olmaz."""
    from decimal import Decimal

    from app.core.channel.pool_builder import PoolBuilder
    from app.database.models import KeywordRelevance

    ws = make_workspace("V2 Clamp WS")
    run = make_scoring_run(brand_profile_id=ws.id)

    kw_pos = make_keyword("pozitif skor", brand_profile_id=ws.id)
    kw_neg_hirel = make_keyword("negatif yuksek alaka", brand_profile_id=ws.id)
    kw_neg_lorel = make_keyword("negatif dusuk alaka", brand_profile_id=ws.id)

    make_keyword_score(scoring_run_id=run.id, keyword_id=kw_pos.id,
                       ads_score=Decimal("2.0"), ads_rank=1)
    make_keyword_score(scoring_run_id=run.id, keyword_id=kw_neg_hirel.id,
                       ads_score=Decimal("-10.0"), ads_rank=2)
    make_keyword_score(scoring_run_id=run.id, keyword_id=kw_neg_lorel.id,
                       ads_score=Decimal("-10.0"), ads_rank=3)

    for kid, rel in ((kw_pos.id, "0.500"), (kw_neg_hirel.id, "0.900"),
                     (kw_neg_lorel.id, "0.100")):
        db_session.add(KeywordRelevance(
            scoring_run_id=run.id, keyword_id=kid,
            relevance_score=Decimal(rel), method="embedding",
        ))
    db_session.commit()

    ranked = PoolBuilder(db_session)._ranked_scores_for_channel(run.id, "ADS")
    scores_by_kid = {ks.keyword_id: adj for ks, adj in ranked}

    # Pozitif skor her zaman önde
    assert ranked[0][0].keyword_id == kw_pos.id
    # Clamp: negatifler 0'a çöker — eski formülde -10*0.1=-1 > -10*0.9=-9
    # olurdu (daha az alakalı öne geçerdi); artık ikisi de 0
    assert scores_by_kid[kw_neg_hirel.id] == pytest.approx(0.0)
    assert scores_by_kid[kw_neg_lorel.id] == pytest.approx(0.0)
    # Deterministik tie-break: 0'a çöken grup kanal rank'ine göre sıralanır
    # (hirel ads_rank=2 < lorel ads_rank=3) — aynı veriyle hep aynı sıra
    assert [ks.keyword_id for ks, _ in ranked] == [
        kw_pos.id, kw_neg_hirel.id, kw_neg_lorel.id
    ]
