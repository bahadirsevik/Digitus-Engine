"""Motor v3 Faz 6 — Post-Policy Kapisi Entegrasyon Testleri.

plan_algoritma_entegrasyonu.md Faz 6 uctan uca sozlesmeleri:
  * K5: Teslim sozlesmesi `ChannelPool`; SEO'da yalniz PRIMARY satirlari yazilir.
  * K6: `algorithm_rank` ile `final_rank` ayri kolonlar.
  * K7: Geri doldurma yok, skor yeniden hesaplanmaz, ikinci AI karari yok.
  * K15: Freshness damgalari ayni transaction'da basariyla yazilir.
  * Re-finalize: Ayni run uzerinde tekrar calistirildiginda temizce gunceller.
  * Rollback Atomikligi: Ikinci finalize sirasinda hata olusursa onceki havuz ve
    selection satirlari ile freshness damgalari birebir korunur.
  * AI cagri sayaci: 0 (post-policy deterministiktir).
"""
from __future__ import annotations

import pytest

from app.core.engine.context import (
    build_firm_profile,
    firm_block,
    firm_block_sha256,
    freeze_universe_snapshot,
)
from app.core.policy.location_policy import policy_snapshot as _loc_snap
from app.core.engine.persistence import (
    load_channel_pools,
    load_engine_selections,
    seal_manifest,
)
from app.core.engine.policy_gate import (
    EXCLUDE_REASON_SEO_NOT_PRIMARY,
    finalize_engine_delivery,
)
from app.core.policy.freshness import compute_pool_freshness
from app.database.models import ChannelPool, EngineSelection


def test_engine_v3_delivery_end_to_end_with_three_channels(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """Uc kanal adaylarinin post-policy kapisindan gecerek DB'ye teslimati."""
    # 1. Onayli workspace ve politika ayarlari
    ws = make_workspace()
    ws.status = "confirmed"
    ws.policy_version = 10
    ws.strategy_version = 5
    ws.anchor_version = 7
    ws.channel_strategy = None
    ws.profile_data = {
        "company_name": "Antigravity Tech",
        "brand_terms": ["antigravity"],
        "sector": "Yapay Zeka",
    }
    ws.topic_policy = {
        "excluded_terms": [
            {"term": "bahis", "status": "approved"},
            {"term": "kripto", "status": "approved"},
        ],
    }
    ws.competitor_terms = [
        {"term": "rakipai", "status": "approved"},
        {"term": "digerbot", "status": "suggested"},  # Onayli degil, elememeli
    ]
    # ADS ve SOCIAL bloklar, SEO izin verir
    ws.competitor_policy = {"ads": "block", "seo": "allow", "social": "block"}

    # 2. Run olustur (Uc kanal da acik)
    run = make_scoring_run(brand_profile_id=ws.id)
    run.algorithm_version = "v3"
    run.ads_capacity = 20
    run.seo_capacity = 10
    run.social_capacity = 10
    run.enable_ads = True
    run.enable_seo = True
    run.enable_social = True

    # 3. Kelimeler (WorkspaceKeywords baglantisiyla olusturulur)
    kw_ads_1 = make_keyword("antigravity yapay zeka araci", brand_profile_id=ws.id)  # Kendi marka -> korundu
    kw_ads_2 = make_keyword("akilli agent platformu", brand_profile_id=ws.id)        # Temiz -> korundu
    kw_ads_3 = make_keyword("rakipai alternatif", brand_profile_id=ws.id)            # Rakip -> ELENDI (ADS block)
    kw_ads_4 = make_keyword("canli bahis tahminleri", brand_profile_id=ws.id)        # Topic -> ELENDI

    kw_seo_1 = kw_ads_2                                                              # ADS ile ayni -> STRATEJIK
    kw_seo_2 = make_keyword("rakipai inceleme", brand_profile_id=ws.id)              # Rakip ama SEO allow -> KORUNDU
    kw_seo_3 = make_keyword("otonom kodlama rehberi", brand_profile_id=ws.id)        # Temiz Primary -> KORUNDU
    kw_seo_4 = make_keyword("otonom kodlama ornekleri", brand_profile_id=ws.id)      # Secondary -> K5: ChannelPool'a girmez!
    kw_seo_5 = make_keyword("kripto botu yazilimi", brand_profile_id=ws.id)          # Topic -> ELENDI

    kw_soc_1 = make_keyword("yapay zeka trendleri", brand_profile_id=ws.id)          # Temiz -> KORUNDU
    kw_soc_2 = make_keyword("rakipai karsilastirmasi", brand_profile_id=ws.id)       # Rakip -> ELENDI (SOCIAL block)
    kw_soc_3 = make_keyword("digerbot ozellikleri", brand_profile_id=ws.id)           # Onaysiz rakip -> KORUNDU

    db_session.commit()

    # Evreni dondur ve manifesti muhurle
    freeze_universe_snapshot(db_session, run)
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

    channel_selections = {
        "ADS": [
            {"keyword_id": kw_ads_1.id, "algorithm_rank": 1,
             "scores": {"Core": 0.9, "Selection": 0.9}, "pool_class": "primary"},
            {"keyword_id": kw_ads_2.id, "algorithm_rank": 2,
             "scores": {"Core": 0.8, "Selection": 0.8}, "pool_class": "primary"},
            {"keyword_id": kw_ads_3.id, "algorithm_rank": 3,
             "scores": {"Core": 0.7, "Selection": 0.7}, "pool_class": "primary"},
            {"keyword_id": kw_ads_4.id, "algorithm_rank": 4,
             "scores": {"Core": 0.6, "Selection": 0.6}, "pool_class": "primary"},
        ],
        "SEO": [
            {"keyword_id": kw_seo_1.id, "algorithm_rank": 1,
             "scores": {"final": 95.0}, "pool_class": "primary"},
            {"keyword_id": kw_seo_2.id, "algorithm_rank": 2,
             "scores": {"final": 90.0}, "pool_class": "primary"},
            {"keyword_id": kw_seo_3.id, "algorithm_rank": 3,
             "scores": {"final": 85.0}, "pool_class": "primary"},
            {"keyword_id": kw_seo_4.id, "algorithm_rank": 4,
             "scores": {"final": 80.0}, "pool_class": "secondary"},
            {"keyword_id": kw_seo_5.id, "algorithm_rank": 5,
             "scores": {"final": 75.0}, "pool_class": "primary"},
        ],
        "SOCIAL": [
            {"keyword_id": kw_soc_1.id, "algorithm_rank": 1,
             "scores": {"social_score": 88.0}, "priority": "primary"},
            {"keyword_id": kw_soc_2.id, "algorithm_rank": 2,
             "scores": {"social_score": 82.0}, "priority": "primary"},
            {"keyword_id": kw_soc_3.id, "algorithm_rank": 3,
             "scores": {"social_score": 78.0}, "priority": "primary"},
        ],
    }

    summary = finalize_engine_delivery(db_session, run=run, channel_selections=channel_selections)
    db_session.commit()

    # Kontroller:
    assert summary["policy_version"] == 10
    assert "strategy_version" not in summary

    # ADS: 4 aday -> 2 kept (kw_ads_1 kendi marka, kw_ads_2 temiz), 2 excluded
    assert summary["channels"]["ADS"]["total_candidates"] == 4
    assert summary["channels"]["ADS"]["kept_count"] == 2
    assert summary["channels"]["ADS"]["excluded_count"] == 2
    assert summary["channels"]["ADS"]["pool_count"] == 2

    # SEO: 5 aday -> 3 primary kept (kw_seo_1, kw_seo_2 rakip allow, kw_seo_3),
    # 1 primary excluded (kw_seo_5 topic), 1 secondary (kw_seo_4 SEO_NOT_PRIMARY)
    assert summary["channels"]["SEO"]["total_candidates"] == 5
    assert summary["channels"]["SEO"]["kept_count"] == 3
    assert summary["channels"]["SEO"]["excluded_count"] == 2
    # K5: SEO ChannelPool'a yalniz PRIMARY girdigi icin pool_count 3 olmali
    assert summary["channels"]["SEO"]["pool_count"] == 3

    # SOCIAL: 3 aday -> 2 kept (kw_soc_1, kw_soc_3 onaysiz rakip elenmez), 1 excluded (kw_soc_2 rakip)
    assert summary["channels"]["SOCIAL"]["total_candidates"] == 3
    assert summary["channels"]["SOCIAL"]["kept_count"] == 2
    assert summary["channels"]["SOCIAL"]["excluded_count"] == 1
    assert summary["channels"]["SOCIAL"]["pool_count"] == 2

    # EngineSelection tablosu
    selections = load_engine_selections(db_session, scoring_run_id=run.id)
    assert len(selections) == 12  # 4 + 5 + 3

    # Tum satirlarda XOR kisiti saglanmis olmali
    for sel in selections:
        assert (sel.final_rank is None) != (sel.exclude_reason is None)

    # SEO secondary kontrolu
    seo_sec = [s for s in selections if s.keyword_id == kw_seo_4.id and s.channel == "SEO"][0]
    assert seo_sec.final_rank is None
    assert seo_sec.exclude_reason == EXCLUDE_REASON_SEO_NOT_PRIMARY

    # ChannelPool tablosu
    pools = load_channel_pools(db_session, scoring_run_id=run.id)
    # 2 ADS + 3 SEO + 2 SOCIAL = 7
    assert len(pools) == 7

    # Dual channel: kw_ads_2 ve kw_seo_1 ayni ID'dir -> is_strategic=True olmali
    shared_id = kw_ads_2.id
    strat_ads = [p for p in pools if p.channel == "ADS" and p.keyword_id == shared_id][0]
    strat_seo = [p for p in pools if p.channel == "SEO" and p.keyword_id == shared_id][0]
    assert strat_ads.is_strategic is True
    assert strat_seo.is_strategic is True

    # Freshness
    freshness = compute_pool_freshness(run, ws)
    assert freshness.channel_pool_stale is False

    # -----------------------------------------------------------------------
    # Re-finalize idempotency: ayni run uzerinde tekrar finalize calistirilmasi
    # -----------------------------------------------------------------------
    summary2 = finalize_engine_delivery(db_session, run=run, channel_selections=channel_selections)
    db_session.commit()

    assert summary2["channels"]["ADS"]["pool_count"] == 2
    assert len(load_engine_selections(db_session, scoring_run_id=run.id)) == 12
    assert len(load_channel_pools(db_session, scoring_run_id=run.id)) == 7


def test_finalize_rollback_preserves_previous_pools_and_selections_on_db_flush_error(
    db_session, make_workspace, make_scoring_run, make_keyword, monkeypatch
):
    """Gerçek PostgreSQL testi: İkinci finalize sırasında silmelerden sonra kontrollü
    bir flush hatası üretildiğinde, rollback sonrasında önceki havuz, selection satırları
    ve freshness damgaları birebir korunur."""
    # 1. Başarılı ilk finalize kurulumu
    ws = make_workspace()
    ws.status = "confirmed"
    ws.policy_version = 10
    ws.strategy_version = 5
    ws.anchor_version = 7
    ws.channel_strategy = None
    ws.profile_data = {"company_name": "TestCorp", "brand_terms": ["testcorp"]}

    run = make_scoring_run(brand_profile_id=ws.id)
    run.algorithm_version = "v3"
    run.ads_capacity = 10
    run.enable_ads = True
    run.enable_seo = False
    run.enable_social = False

    kw1 = make_keyword("ilk basarili kelime", brand_profile_id=ws.id)
    kw2 = make_keyword("ikinci basarili kelime", brand_profile_id=ws.id)
    db_session.commit()

    freeze_universe_snapshot(db_session, run)
    sha = firm_block_sha256(firm_block(build_firm_profile(ws)))
    seal_manifest(
        run,
        firm_block_sha256=sha,
        algorithm_versions={"ads": "nihai_niche_v1"},
        models={"ads": "gemini-2.5"},
        prompt_shas={"ads": "sha_ads"},
        location_policy=_loc_snap({}),
    )
    db_session.commit()

    channel_selections = {
        "ADS": [
            {"keyword_id": kw1.id, "algorithm_rank": 1, "scores": {"Selection": 0.95}},
            {"keyword_id": kw2.id, "algorithm_rank": 2, "scores": {"Selection": 0.85}},
        ]
    }

    summary1 = finalize_engine_delivery(db_session, run=run, channel_selections=channel_selections)
    db_session.commit()

    # İlk durumun kaydedilmesi
    initial_pools = load_channel_pools(db_session, scoring_run_id=run.id, channel="ADS")
    assert len(initial_pools) == 2
    initial_pool_data = [(p.id, p.keyword_id, p.final_rank) for p in initial_pools]

    initial_selections = load_engine_selections(db_session, scoring_run_id=run.id, channel="ADS")
    assert len(initial_selections) == 2
    initial_sel_data = [(s.id, s.keyword_id, s.algorithm_rank, s.final_rank) for s in initial_selections]

    db_session.refresh(run)
    assert run.channel_pool_policy_version == 10
    assert run.channel_pool_strategy_version is None
    assert run.relevance_anchor_version == 7

    # 2. İkinci finalize öncesi workspace politikasını değiştir
    ws.policy_version = 11
    db_session.commit()

    # Kontrollü flush hatası üret: silmeler yapıldıktan sonra flush patlatılır
    real_flush = db_session.flush
    def failing_flush(*args, **kwargs):
        raise RuntimeError("Controlled PostgreSQL flush error simulation")

    monkeypatch.setattr(db_session, "flush", failing_flush)

    # İkinci finalize çağrısı fail-closed hata fırlatmalı
    with pytest.raises(RuntimeError, match="Controlled PostgreSQL flush error simulation"):
        finalize_engine_delivery(db_session, run=run, channel_selections={
            "ADS": [
                {"keyword_id": kw1.id, "algorithm_rank": 1, "scores": {"Selection": 0.95}}
            ]
        })

    # Monkeypatch'i kaldır ve DB oturumunu doğrula
    monkeypatch.undo()

    # 3. Rollback sonrası önceki durumun BİREBİR korunduğunu doğrula
    restored_pools = load_channel_pools(db_session, scoring_run_id=run.id, channel="ADS")
    restored_pool_data = [(p.id, p.keyword_id, p.final_rank) for p in restored_pools]
    assert restored_pool_data == initial_pool_data

    restored_selections = load_engine_selections(db_session, scoring_run_id=run.id, channel="ADS")
    restored_sel_data = [(s.id, s.keyword_id, s.algorithm_rank, s.final_rank) for s in restored_selections]
    assert restored_sel_data == initial_sel_data

    # Freshness damgaları da eski değerlerinde kalmış olmalı (11 değil, 10)
    db_session.refresh(run)
    assert run.channel_pool_policy_version == 10
    assert run.channel_pool_strategy_version is None
    assert run.relevance_anchor_version == 7
