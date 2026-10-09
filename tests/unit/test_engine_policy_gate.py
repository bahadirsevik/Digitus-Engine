"""Motor v3 Faz 6 — Post-Policy Kapisi ve Teslimat Testleri.

plan_algoritma_entegrasyonu.md Faz 6 ve sozlesmeler:
  * K5: Teslim sozlesmesi `ChannelPool`; SEO'da yalniz PRIMARY satirlari yazilir.
  * K6: `algorithm_rank` ile `final_rank` ayri kolonlar.
  * K7: Geri doldurma yok, teslim dilimi policy oncesi dondurulur, unfilled_count raporlanir.
  * K15: Freshness damgalari yalniz basarili finalize ile ayni transaction'da yazilir;
         muhur degistirilemez, nested engine_v3.firm_block_sha256 esastir.
  * Atomiklik: Hata halinde kismi ChannelPool/selection birakilmaz.
  * AI cagri sayaci 0 (yalniz deterministik ve onayli policy kaynaklari).
"""
from __future__ import annotations

from decimal import Decimal
import pytest

from app.core.channel.brand_defense import build_brand_defense_context
from app.core.constants import COMPETITOR_TERM_REASON
from app.core.engine.context import (
    EngineInputError,
    build_firm_profile,
    firm_block,
    firm_block_sha256,
    freeze_universe_snapshot,
)
from app.core.policy.location_policy import policy_snapshot as _loc_snap
from app.core.engine.persistence import (
    StageValidationError,
    load_channel_pools,
    load_engine_selections,
    seal_manifest,
    write_engine_selections,
)
from app.core.engine.policy_gate import (
    EXCLUDE_REASON_CAPACITY_LIMIT,
    EXCLUDE_REASON_COMPETITOR,
    EXCLUDE_REASON_SEO_NOT_PRIMARY,
    EXCLUDE_REASON_TOPIC,
    evaluate_policy_candidate,
    finalize_engine_delivery,
)
from app.core.policy.freshness import compute_pool_freshness
from app.database.models import ChannelPool, EngineSelection


# ---------------------------------------------------------------------------
# Test Yardimcilari (Fixture Helpers)
# ---------------------------------------------------------------------------

def _setup_v3_delivery_fixture(
    db_session,
    make_workspace,
    make_scoring_run,
    make_keyword,
    keyword_specs,
    *,
    workspace_kwargs=None,
    run_kwargs=None,
    enabled_channels=("ADS",),
):
    ws_defaults = {
        "status": "confirmed",
        "policy_version": 1,
        "strategy_version": 1,
        "anchor_version": 1,
        "channel_strategy": {
            "status": "approved",
            "product_definition": "Test Urunler",
            "content_strategy": "Test Strateji",
            "social_mode": "hype",
        },
        "profile_data": {
            "company_name": "TestMarka",
            "sector": "Teknoloji",
            "brand_terms": ["testmarka"],
        },
    }
    if workspace_kwargs:
        ws_defaults.update(workspace_kwargs)
    ws = make_workspace(**ws_defaults)

    r_defaults = {
        "brand_profile_id": ws.id,
        "algorithm_version": "v3",
        "ads_capacity": 10,
        "seo_capacity": 10,
        "social_capacity": 10,
        "enable_ads": "ADS" in enabled_channels,
        "enable_seo": "SEO" in enabled_channels,
        "enable_social": "SOCIAL" in enabled_channels,
    }
    if run_kwargs:
        r_defaults.update(run_kwargs)
    run = make_scoring_run(**r_defaults)

    keywords = []
    for spec in keyword_specs:
        if isinstance(spec, tuple):
            text = spec[0]
            vol = spec[1] if len(spec) > 1 else 100
            comp = spec[2] if len(spec) > 2 else 0.5
            t3 = spec[3] if len(spec) > 3 else 0.0
            kw = make_keyword(
                text,
                brand_profile_id=ws.id,
                monthly_volume=vol,
                competition_score=comp,
                trend_3m=t3,
            )
        elif isinstance(spec, str):
            kw = make_keyword(
                spec,
                brand_profile_id=ws.id,
                monthly_volume=100,
                competition_score=0.5,
                trend_3m=0.0,
            )
        else:
            kw = spec
        keywords.append(kw)

    db_session.commit()

    # Evreni dondur
    universe = freeze_universe_snapshot(db_session, run)

    # Manifesti muhurle
    sha = firm_block_sha256(firm_block(build_firm_profile(ws)))
    seal_manifest(
        run,
        firm_block_sha256=sha,
        algorithm_versions={"ads": "nihai_niche_v1", "seo": "v31_kati2", "social": "v5"},
        models={"ads": "gemini-2.5", "seo": "gemini-2.5", "social": "gemini-2.5"},
        prompt_shas={"ads": "sha_ads", "seo": "sha_seo", "social": "sha_soc"},
        location_policy=_loc_snap({}),
    )
    db_session.commit()

    return ws, run, keywords, universe


# ---------------------------------------------------------------------------
# 1. evaluate_policy_candidate: Deterministik kural testleri
# ---------------------------------------------------------------------------

def test_evaluate_policy_candidate_clean_keyword():
    res = evaluate_policy_candidate(
        "vepa sac fircasi",
        "ADS",
        topic_terms=["kripto", "forex"],
        competitor_terms=["rakipmarka"],
        competitor_blocked=True,
        brand_ctx=None,
    )
    assert res.is_kept is True
    assert res.exclude_reason is None


def test_evaluate_policy_candidate_topic_excluded():
    res = evaluate_policy_candidate(
        "bitcoin kripto para al",
        "SEO",
        topic_terms=["kripto", "forex"],
        competitor_terms=["rakipmarka"],
        competitor_blocked=True,
        brand_ctx=None,
    )
    assert res.is_kept is False
    assert res.exclude_reason.startswith(EXCLUDE_REASON_TOPIC)
    assert "kripto" in res.exclude_reason


def test_evaluate_policy_candidate_competitor_blocked():
    res = evaluate_policy_candidate(
        "rakipmarka fiyati",
        "ADS",
        topic_terms=[],
        competitor_terms=["rakipmarka"],
        competitor_blocked=True,
        brand_ctx=None,
    )
    assert res.is_kept is False
    assert res.exclude_reason.startswith(EXCLUDE_REASON_COMPETITOR)
    assert "rakipmarka" in res.exclude_reason


def test_evaluate_policy_candidate_competitor_allowed_in_channel():
    res = evaluate_policy_candidate(
        "rakipmarka urunleri",
        "SEO",
        topic_terms=[],
        competitor_terms=["rakipmarka"],
        competitor_blocked=False,  # Channel policy allow
        brand_ctx=None,
    )
    assert res.is_kept is True
    assert res.exclude_reason is None


def test_evaluate_policy_candidate_own_brand_overrides_competitor():
    brand_ctx = build_brand_defense_context(["vepa"], company_name="Vepa A.S.")
    res = evaluate_policy_candidate(
        "vepa dis fircasi",
        "ADS",
        topic_terms=[],
        competitor_terms=["vepa", "rakipmarka"],
        competitor_blocked=True,
        brand_ctx=brand_ctx,
    )
    assert res.is_kept is True
    assert res.exclude_reason is None


def test_evaluate_policy_candidate_own_brand_with_negative_signal_not_protected():
    brand_ctx = build_brand_defense_context(["vepa"], company_name="Vepa A.S.")
    res = evaluate_policy_candidate(
        "vepa sikayet",
        "ADS",
        topic_terms=[],
        competitor_terms=["vepa"],
        competitor_blocked=True,
        brand_ctx=brand_ctx,
    )
    assert res.is_kept is False
    assert res.exclude_reason.startswith(EXCLUDE_REASON_COMPETITOR)


# ---------------------------------------------------------------------------
# 2. Persistence: EngineSelection ve XOR kisiti
# ---------------------------------------------------------------------------

def test_write_engine_selections_xor_violation_raises(db_session, make_workspace, make_scoring_run):
    ws = make_workspace()
    run = make_scoring_run(brand_profile_id=ws.id)
    db_session.commit()

    # Hem final_rank dolu hem exclude_reason dolu -> HATA
    invalid_item = {
        "keyword_id": 1,
        "channel": "ADS",
        "algorithm_rank": 1,
        "final_rank": 1,
        "exclude_reason": "SOMETHING",
        "policy_version": 1,
    }
    with pytest.raises(StageValidationError):
        write_engine_selections(db_session, scoring_run_id=run.id, selections=[invalid_item])

    # Hem final_rank None hem exclude_reason None -> HATA
    invalid_item2 = {
        "keyword_id": 2,
        "channel": "ADS",
        "algorithm_rank": 2,
        "final_rank": None,
        "exclude_reason": None,
        "policy_version": 1,
    }
    with pytest.raises(StageValidationError):
        write_engine_selections(db_session, scoring_run_id=run.id, selections=[invalid_item2])


def test_write_and_load_engine_selections(db_session, make_workspace, make_scoring_run, make_keyword):
    ws = make_workspace()
    run = make_scoring_run(brand_profile_id=ws.id)
    kw1 = make_keyword("kw1")
    kw2 = make_keyword("kw2")
    db_session.commit()

    valid_selections = [
        {
            "keyword_id": kw1.id,
            "channel": "ADS",
            "algorithm_rank": 1,
            "scores": {"Selection": 0.95},
            "pool_class": "primary",
            "final_rank": 1,
            "exclude_reason": None,
            "policy_version": 2,
        },
        {
            "keyword_id": kw2.id,
            "channel": "ADS",
            "algorithm_rank": 2,
            "scores": {"Selection": 0.85},
            "pool_class": "primary",
            "final_rank": None,
            "exclude_reason": "COMPETITOR_TERM:xyz",
            "policy_version": 2,
        },
    ]

    written = write_engine_selections(db_session, scoring_run_id=run.id, selections=valid_selections)
    db_session.commit()
    assert written == 2

    loaded = load_engine_selections(db_session, scoring_run_id=run.id, channel="ADS")
    assert len(loaded) == 2
    assert loaded[0].keyword_id == kw1.id
    assert loaded[0].final_rank == 1
    assert loaded[0].exclude_reason is None
    assert loaded[1].keyword_id == kw2.id
    assert loaded[1].final_rank is None
    assert loaded[1].exclude_reason == "COMPETITOR_TERM:xyz"


# ---------------------------------------------------------------------------
# 3. Belirtilen 6 Senaryo Regresyon Testleri
# ---------------------------------------------------------------------------

def test_scenario_1_profile_changed_after_seal_rejects_and_writes_zero(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """1. Profil mühürden sonra değişmişse: 0 satır yazılmalı, teslimat reddedilmeli."""
    ws, run, keywords, _ = _setup_v3_delivery_fixture(
        db_session, make_workspace, make_scoring_run, make_keyword,
        [("temiz kelime", 100, 0.5, 0.0)],
        enabled_channels=("ADS",)
    )

    # Muhurden SONRA profili degistir
    ws.profile_data = {
        "company_name": "Tamamen Farkli Marka",
        "sector": "Otomotiv",
        "brand_terms": ["farklimarka"],
    }
    db_session.commit()

    channel_selections = {
        "ADS": [
            {"keyword_id": keywords[0].id, "algorithm_rank": 1, "scores": {"Selection": 0.9}}
        ]
    }

    with pytest.raises(EngineInputError, match="profil muhurden sonra degismis"):
        finalize_engine_delivery(db_session, run=run, channel_selections=channel_selections)

    # 0 satir yazilmali (fail-closed)
    assert db_session.query(EngineSelection).filter(EngineSelection.scoring_run_id == run.id).count() == 0
    assert db_session.query(ChannelPool).filter(ChannelPool.scoring_run_id == run.id).count() == 0


def test_scenario_2_missing_or_draft_strategy_allowed_in_v3(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """2. V3 kanal stratejisi olmadan da çalışır (channel_strategy=None veya draft)."""
    ws, run, keywords, _ = _setup_v3_delivery_fixture(
        db_session, make_workspace, make_scoring_run, make_keyword,
        [("temiz kelime", 100, 0.5, 0.0)],
        enabled_channels=("ADS",)
    )

    # Strateji kaydını tamamen kaldır (None)
    ws.channel_strategy = None
    db_session.commit()

    channel_selections = {
        "ADS": [
            {"keyword_id": keywords[0].id, "algorithm_rank": 1, "scores": {"Selection": 0.9}}
        ]
    }

    summary = finalize_engine_delivery(db_session, run=run, channel_selections=channel_selections)
    db_session.commit()

    # Başarıyla satır yazılmalı, strateji zorunluluğu olmamalı
    assert db_session.query(EngineSelection).filter(EngineSelection.scoring_run_id == run.id).count() == 1
    assert db_session.query(ChannelPool).filter(ChannelPool.scoring_run_id == run.id).count() == 1
    assert "strategy_version" not in summary
    assert run.channel_pool_strategy_version is None



def test_scenario_3_foreign_keyword_and_canonical_text_usage(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """3. Evrende olmayan ID reject, tekrarlı ID/rank reject; policy eşlemesinde snapshot metni kullanılır."""
    ws, run, keywords, _ = _setup_v3_delivery_fixture(
        db_session, make_workspace, make_scoring_run, make_keyword,
        [
            ("yasakli_konu terimi", 100, 0.5, 0.0),
            ("temiz kelime", 100, 0.5, 0.0),
        ],
        workspace_kwargs={
            "topic_policy": {"excluded_terms": [{"term": "yasakli_konu", "status": "approved"}]},
        },
        enabled_channels=("ADS",)
    )

    # a) Evrende olmayan keyword_id -> fail-closed
    with pytest.raises(EngineInputError, match="run evreninde yok"):
        finalize_engine_delivery(db_session, run=run, channel_selections={
            "ADS": [{"keyword_id": 999999, "algorithm_rank": 1}]
        })

    # b) Kanal icinde tekrarli keyword_id -> fail-closed
    with pytest.raises(EngineInputError, match="tekrarlı keyword_id"):
        finalize_engine_delivery(db_session, run=run, channel_selections={
            "ADS": [
                {"keyword_id": keywords[0].id, "algorithm_rank": 1},
                {"keyword_id": keywords[0].id, "algorithm_rank": 2},
            ]
        })

    # c) Pozitif olmayan algorithm_rank -> fail-closed
    with pytest.raises(EngineInputError, match="algorithm_rank pozitif tamsayi olmalidir"):
        finalize_engine_delivery(db_session, run=run, channel_selections={
            "ADS": [{"keyword_id": keywords[0].id, "algorithm_rank": 0}]
        })

    # d) Kanal icinde tekrarli algorithm_rank -> fail-closed
    with pytest.raises(EngineInputError, match="tekrarlı algorithm_rank"):
        finalize_engine_delivery(db_session, run=run, channel_selections={
            "ADS": [
                {"keyword_id": keywords[0].id, "algorithm_rank": 1},
                {"keyword_id": keywords[1].id, "algorithm_rank": 1},
            ]
        })

    # e) Snapshot kanonik metin kullanimi:
    # Aday nesnesinde keyword_text sahte olarak "masum kelime" gonderilse bile,
    # snapshot metni "yasakli_konu terimi" oldugu icin policy tarafindan ELENMELIDIR!
    finalize_engine_delivery(db_session, run=run, channel_selections={
        "ADS": [
            {"keyword_id": keywords[0].id, "keyword_text": "masum kelime aldatmacasi", "algorithm_rank": 1},
            {"keyword_id": keywords[1].id, "keyword_text": "temiz kelime", "algorithm_rank": 2},
        ]
    })
    db_session.commit()

    selections = load_engine_selections(db_session, scoring_run_id=run.id, channel="ADS")
    assert len(selections) == 2
    # keywords[0] snapshot metni nedeniyle elendi!
    assert selections[0].keyword_id == keywords[0].id
    assert selections[0].final_rank is None
    assert selections[0].exclude_reason.startswith(EXCLUDE_REASON_TOPIC)
    # keywords[1] secildi
    assert selections[1].keyword_id == keywords[1].id
    assert selections[1].final_rank == 1


def test_scenario_4_capacity_2_rank_1_excluded_unfilled_1_no_backfill(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """4. Capacity 2 iken rank 1 policy ile elenirse: rank 3 asla havuza girmemeli, unfilled_count=1."""
    ws, run, keywords, _ = _setup_v3_delivery_fixture(
        db_session, make_workspace, make_scoring_run, make_keyword,
        [
            ("yasakli_konu urunu", 100, 0.5, 0.0),
            ("temiz kelime 1", 100, 0.5, 0.0),
            ("temiz kelime 2", 100, 0.5, 0.0),
        ],
        workspace_kwargs={
            "topic_policy": {"excluded_terms": [{"term": "yasakli_konu", "status": "approved"}]},
        },
        run_kwargs={"ads_capacity": 2},
        enabled_channels=("ADS",)
    )

    channel_selections = {
        "ADS": [
            {"keyword_id": keywords[0].id, "algorithm_rank": 1, "scores": {"Selection": 0.9}},
            {"keyword_id": keywords[1].id, "algorithm_rank": 2, "scores": {"Selection": 0.8}},
            {"keyword_id": keywords[2].id, "algorithm_rank": 3, "scores": {"Selection": 0.7}},
        ]
    }

    summary = finalize_engine_delivery(db_session, run=run, channel_selections=channel_selections, capacities={"ADS": 2})
    db_session.commit()

    # Summary dogrulamalari
    ads_sum = summary["channels"]["ADS"]
    assert ads_sum["total_candidates"] == 3
    assert ads_sum["kept_count"] == 1
    assert ads_sum["excluded_count"] == 2
    assert ads_sum["pool_count"] == 1
    assert ads_sum["capacity"] == 2
    assert ads_sum["unfilled_count"] == 1

    # ChannelPool dogrulamasi: Rank 3 ASLA havuza girmemistir (no-backfill)
    pools = load_channel_pools(db_session, scoring_run_id=run.id, channel="ADS")
    assert len(pools) == 1
    assert pools[0].keyword_id == keywords[1].id
    assert pools[0].final_rank == 1

    # EngineSelection dogrulamasi
    selections = load_engine_selections(db_session, scoring_run_id=run.id, channel="ADS")
    assert len(selections) == 3
    # Rank 1: Policy ile elendi
    assert selections[0].keyword_id == keywords[0].id
    assert selections[0].algorithm_rank == 1
    assert selections[0].final_rank is None
    assert selections[0].exclude_reason.startswith(EXCLUDE_REASON_TOPIC)
    # Rank 2: Secildi -> final_rank=1
    assert selections[1].keyword_id == keywords[1].id
    assert selections[1].algorithm_rank == 2
    assert selections[1].final_rank == 1
    assert selections[1].exclude_reason is None
    # Rank 3: Dilim disi (kapasite asimi) -> final_rank=None, exclude_reason=CAPACITY_LIMIT
    assert selections[2].keyword_id == keywords[2].id
    assert selections[2].algorithm_rank == 3
    assert selections[2].final_rank is None
    assert selections[2].exclude_reason == EXCLUDE_REASON_CAPACITY_LIMIT


def test_scenario_5_seo_secondary_rank_1_primary_rank_2(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """5. SEO secondary rank 1, primary rank 2: primary ChannelPool'a final_rank=1 girmeli, secondary girmemeli."""
    ws, run, keywords, _ = _setup_v3_delivery_fixture(
        db_session, make_workspace, make_scoring_run, make_keyword,
        [
            ("seo kelime secondary", 100, 0.5, 0.0),
            ("seo kelime primary", 100, 0.5, 0.0),
        ],
        run_kwargs={"seo_capacity": 10},
        enabled_channels=("SEO",)
    )

    channel_selections = {
        "SEO": [
            {"keyword_id": keywords[0].id, "algorithm_rank": 1, "pool_class": "secondary", "scores": {"final": 90.0}},
            {"keyword_id": keywords[1].id, "algorithm_rank": 2, "pool_class": "primary", "scores": {"final": 80.0}},
        ]
    }

    summary = finalize_engine_delivery(db_session, run=run, channel_selections=channel_selections)
    db_session.commit()

    # Summary
    assert summary["channels"]["SEO"]["pool_count"] == 1

    # ChannelPool
    pools = load_channel_pools(db_session, scoring_run_id=run.id, channel="SEO")
    assert len(pools) == 1
    assert pools[0].keyword_id == keywords[1].id
    assert pools[0].final_rank == 1

    # EngineSelection
    selections = load_engine_selections(db_session, scoring_run_id=run.id, channel="SEO")
    assert len(selections) == 2
    assert selections[0].keyword_id == keywords[0].id
    assert selections[0].algorithm_rank == 1
    assert selections[0].final_rank is None
    assert selections[0].exclude_reason == EXCLUDE_REASON_SEO_NOT_PRIMARY

    assert selections[1].keyword_id == keywords[1].id
    assert selections[1].algorithm_rank == 2
    assert selections[1].final_rank == 1
    assert selections[1].exclude_reason is None


def test_scenario_6_zero_scores_preserved(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """6. relevance_score = 0.0 ve adjusted_score = 0.0 ChannelPool'da 0.0 olarak kalmalı (None olmamalı)."""
    ws, run, keywords, _ = _setup_v3_delivery_fixture(
        db_session, make_workspace, make_scoring_run, make_keyword,
        [("sifir skorlu kelime", 100, 0.5, 0.0)],
        enabled_channels=("ADS",)
    )

    channel_selections = {
        "ADS": [
            {
                "keyword_id": keywords[0].id,
                "algorithm_rank": 1,
                "relevance_score": 0.0,
                "adjusted_score": 0.0,
            }
        ]
    }

    finalize_engine_delivery(db_session, run=run, channel_selections=channel_selections)
    db_session.commit()

    pools = load_channel_pools(db_session, scoring_run_id=run.id, channel="ADS")
    assert len(pools) == 1
    row = pools[0]
    assert row.relevance_score is not None
    assert float(row.relevance_score) == 0.0
    assert row.adjusted_score is not None
    assert float(row.adjusted_score) == 0.0


# ---------------------------------------------------------------------------
# 4. Kanal Seti Eslesme ve Dilimleme Dogrulamalari (Madde 1 ve 2)
# ---------------------------------------------------------------------------

def test_finalize_channel_set_mismatch_and_collision_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """Kanal seti birebir uyusmali: eksik, fazla ve normalize cakismasi EngineInputError verir."""
    ws, run, keywords, _ = _setup_v3_delivery_fixture(
        db_session, make_workspace, make_scoring_run, make_keyword,
        [("kelime a", 100, 0.5, 0.0)],
        enabled_channels=("ADS", "SEO")  # ADS ve SEO acik, SOCIAL kapali
    )

    # 1. Eksik acik kanal (SEO gonderilmedi)
    with pytest.raises(EngineInputError, match="Beklenen acik kanallar eksik"):
        finalize_engine_delivery(db_session, run=run, channel_selections={
            "ADS": [{"keyword_id": keywords[0].id, "algorithm_rank": 1}]
        })

    # 2. Kapali kanal gonderildi (SOCIAL kapaliydi)
    with pytest.raises(EngineInputError, match="Kapali veya tanimsiz kanallar gonderildi"):
        finalize_engine_delivery(db_session, run=run, channel_selections={
            "ADS": [{"keyword_id": keywords[0].id, "algorithm_rank": 1}],
            "SEO": [],
            "SOCIAL": [],
        })

    # 3. Normalize cakismasi (ads ve ADS birlikte gonderildi)
    with pytest.raises(EngineInputError, match="cakisan anahtar"):
        finalize_engine_delivery(db_session, run=run, channel_selections={
            "ads": [{"keyword_id": keywords[0].id, "algorithm_rank": 1}],
            "ADS": [{"keyword_id": keywords[0].id, "algorithm_rank": 1}],
            "SEO": [],
        })

    # 4. Acik kanal bos sonuc [] kabul edilir
    summary = finalize_engine_delivery(db_session, run=run, channel_selections={
        "ADS": [{"keyword_id": keywords[0].id, "algorithm_rank": 1}],
        "SEO": [],
    })
    assert summary["channels"]["ADS"]["pool_count"] == 1
    assert summary["channels"]["SEO"]["pool_count"] == 0
    assert summary["channels"]["SEO"]["unfilled_count"] == 10  # capacity 10


def test_delivery_slice_uses_first_n_candidates_with_gapped_ranks(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """algorithm_rank bosluklu olsa bile teslim dilimi siralanmis ilk capacity adayini alir."""
    ws, run, keywords, _ = _setup_v3_delivery_fixture(
        db_session, make_workspace, make_scoring_run, make_keyword,
        [
            ("kelime 1", 100, 0.5, 0.0),
            ("kelime 2", 100, 0.5, 0.0),
            ("kelime 3", 100, 0.5, 0.0),
        ],
        run_kwargs={"ads_capacity": 2},
        enabled_channels=("ADS",)
    )

    # algorithm_rank degerleri bosluklu: 5, 12, 40
    # Kapasite = 2 -> Ilk 2 aday (5 ve 12) teslim dilimine girmeli; 40 CAPACITY_LIMIT ile elenmeli
    channel_selections = {
        "ADS": [
            {"keyword_id": keywords[0].id, "algorithm_rank": 5, "scores": {"Selection": 0.9}},
            {"keyword_id": keywords[1].id, "algorithm_rank": 12, "scores": {"Selection": 0.8}},
            {"keyword_id": keywords[2].id, "algorithm_rank": 40, "scores": {"Selection": 0.7}},
        ]
    }

    summary = finalize_engine_delivery(db_session, run=run, channel_selections=channel_selections, capacities={"ADS": 2})
    db_session.commit()

    pools = load_channel_pools(db_session, scoring_run_id=run.id, channel="ADS")
    assert len(pools) == 2
    assert pools[0].keyword_id == keywords[0].id
    assert pools[0].final_rank == 1
    assert pools[1].keyword_id == keywords[1].id
    assert pools[1].final_rank == 2

    selections = load_engine_selections(db_session, scoring_run_id=run.id, channel="ADS")
    assert len(selections) == 3
    assert selections[0].algorithm_rank == 5
    assert selections[0].final_rank == 1
    assert selections[1].algorithm_rank == 12
    assert selections[1].final_rank == 2
    assert selections[2].algorithm_rank == 40
    assert selections[2].final_rank is None
    assert selections[2].exclude_reason == EXCLUDE_REASON_CAPACITY_LIMIT


# ---------------------------------------------------------------------------
# 5. Genel Finalize ve Freshness Testleri
# ---------------------------------------------------------------------------

def test_finalize_rejects_non_v3_algorithm_version(db_session, make_workspace, make_scoring_run):
    ws = make_workspace()
    run = make_scoring_run(brand_profile_id=ws.id)
    run.algorithm_version = "v2"
    db_session.commit()

    with pytest.raises(EngineInputError, match="algorithm_version 'v3' degil"):
        finalize_engine_delivery(db_session, run=run, channel_selections={"ADS": []})


def test_v3_freshness_stale_detection(db_session, make_workspace, make_scoring_run):
    ws = make_workspace()
    ws.status = "confirmed"
    ws.policy_version = 1
    ws.strategy_version = 1
    ws.profile_data = {"company_name": "TestMarka", "sector": "Tekstil", "products": ["Gömlek"]}

    prof_dict = build_firm_profile(ws)
    current_sha = firm_block_sha256(firm_block(prof_dict))

    run = make_scoring_run(brand_profile_id=ws.id)
    run.algorithm_version = "v3"
    run.channel_pool_policy_version = 1
    run.channel_pool_strategy_version = None
    # V3 freshness nested engine_v3 altindaki SHA'yi otorite kabul eder
    run.execution_manifest = {
        "engine_v3": {
            "firm_block_sha256": current_sha,
            "algorithm_versions": {},
            "models": {},
            "prompt_shas": {},
        }
    }
    db_session.commit()

    # 1. Fresh
    f0 = compute_pool_freshness(run, ws)
    assert f0.channel_pool_stale is False

    # 2. Politika degisti -> policy_stale
    ws.policy_version = 2
    f1 = compute_pool_freshness(run, ws)
    assert f1.policy_stale is True
    assert f1.channel_pool_stale is True
    ws.policy_version = 1  # reset

    # 3. Strateji degisti -> V3 havuzunu bayatlatmaz (strategy_stale=False)
    ws.strategy_version = 2
    ws.channel_strategy = {
        "content_strategy": "Yeni editoryal yön",
        "social_mode": "authority",
    }
    f2 = compute_pool_freshness(run, ws)
    assert f2.strategy_stale is False
    assert f2.channel_pool_stale is False
    ws.strategy_version = 1  # reset

    # 4. Profil metni degisti -> relevance_stale (K15 firm_block_sha256 otoritesi)
    ws.profile_data = {"company_name": "FARKLI MARKA ADI", "sector": "Tekstil", "products": ["Gömlek"]}
    f3 = compute_pool_freshness(run, ws)
    assert f3.relevance_stale is True
    assert f3.channel_pool_stale is True


def test_firm_block_includes_products_and_services(make_workspace):
    """build_firm_profile ve firm_block ürün/hizmetleri taşır; değişiklik hash'i değiştirir."""
    ws = make_workspace(profile_data={
        "company_name": "Test Marka",
        "sector": "E-Ticaret",
        "products": ["Ayakkabı", "Çanta"],
        "services": ["Hızlı Teslimat"],
    })
    prof1 = build_firm_profile(ws)
    block1 = firm_block(prof1)
    sha1 = firm_block_sha256(block1)

    assert "Ayakkabı" in block1
    assert "Hızlı Teslimat" in block1

    # Ürün değişince hash değişmeli
    ws.profile_data["products"] = ["Ayakkabı", "Çanta", "Cüzdan"]
    prof2 = build_firm_profile(ws)
    block2 = firm_block(prof2)
    sha2 = firm_block_sha256(block2)
    assert sha1 != sha2

    # content_strategy veya social_mode değişince firm_block hash DEĞİŞMEMELİ
    ws.channel_strategy = {
        "content_strategy": "Farklı içerik",
        "social_mode": "authority",
    }
    prof3 = build_firm_profile(ws)
    sha3 = firm_block_sha256(firm_block(prof3))
    assert sha2 == sha3
