# -*- coding: utf-8 -*-
"""Faz 5 — AI öncesi global lokasyon eleme + ikinci savunma hattı + run özeti.

plan_v3_lokasyon_filtresi.md §5.5, §5.6, §8.3. Kapsam:
  * Orchestrator: mühürlü lokasyon politikası (canlı profil DEĞİL) evren
    dondurulduktan hemen sonra, Family/ADS/SEO/SOCIAL çağrılmadan ÖNCE
    uygulanır. Kanal motorları yalnız `eligible_rows` görür.
  * Tüm evren elenirse motor/AI hiç çağrılmadan açık bir hata verir.
  * policy_gate: aynı matcher ikinci savunma hattı olarak — geri doldurma
    yapmadan, ikinci AI kararı üretmeden — yalnız dışlar.
  * finalize_engine_delivery özetine `location_filter` kırılımı eklenir.

KAPSAM DIŞI: mühür/fail-closed davranışı (tests/integration/test_location_manifest_seal.py),
önizleme endpoint'i (tests/integration/test_location_filter_preview.py), matcher birim
testleri (tests/unit/test_location_policy.py) — burada tekrar edilmez.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.core.engine.context import (
    EngineInputError,
    build_firm_profile,
    firm_block,
    firm_block_sha256,
    freeze_universe_snapshot,
    load_universe,
)
from app.core.engine.orchestrator import run_v3_orchestration
from app.core.engine.persistence import (
    load_channel_pools,
    load_engine_selections,
    seal_manifest,
)
from app.core.engine.policy_gate import finalize_engine_delivery
from app.core.policy.location_policy import (
    MODE_EXCLUDE_ALL,
    MODE_FOCUS_ONLY,
    MODE_NONE,
    REASON_CITY_FILTER,
    canonical_city,
    policy_snapshot,
)
from app.database.models import ChannelPool, EngineSelection


def _setup_confirmed_workspace(make_workspace, *, location=None):
    ws = make_workspace()
    ws.status = "confirmed"
    ws.policy_version = 1
    ws.profile_data = {
        "company_name": "Antigravity Tech",
        "brand_terms": ["antigravity"],
        "sector": "Yapay Zeka",
        **(location or {}),
    }
    ws.channel_strategy = None  # V3 kanal stratejisi okumaz (ADR-004)
    return ws


def _mock_stage_capture():
    """`rows`/`universe` kwarg'ını yakalayan basit bir stage mock'u."""
    calls = {}

    def _capture(name):
        def _fn(db, *, run, profile, rows=None, universe=None, **kwargs):
            captured = rows if rows is not None else universe
            calls[name] = {int(r.keyword_id) for r in captured}
            return []
        return _fn

    return calls, _capture


def test_mode_none_leaves_universe_untouched(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """`none` modunda şehirli/şehirsiz TÜM kelimeler motorlara ulaşır."""
    ws = _setup_confirmed_workspace(make_workspace)  # location_filter_mode yok -> none
    run = make_scoring_run(brand_profile_id=ws.id)
    run.algorithm_version = "v3"
    run.status = "scored"
    run.enable_ads, run.enable_seo, run.enable_social = True, False, False
    run.ads_capacity = 10

    kw_city = make_keyword("ankara ofis kiralama", brand_profile_id=ws.id)
    kw_plain = make_keyword("ofis kiralama hizmeti", brand_profile_id=ws.id)
    db_session.commit()
    freeze_universe_snapshot(db_session, run)
    db_session.commit()

    calls, capture = _mock_stage_capture()
    with patch("app.core.engine.orchestrator.run_family_stage", side_effect=capture("family")), \
         patch("app.core.engine.orchestrator.run_ads_stage", side_effect=capture("ads")):
        run_v3_orchestration(db_session, run=run, ai=MagicMock())

    assert calls["family"] == {kw_city.id, kw_plain.id}
    assert calls["ads"] == {kw_city.id, kw_plain.id}


def test_exclude_all_mode_city_keyword_never_reaches_any_stage(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """`exclude_all`: şehirli kelime Family/ADS/SEO/SOCIAL'a hiç ulaşmaz."""
    ws = _setup_confirmed_workspace(
        make_workspace, location={"location_filter_mode": MODE_EXCLUDE_ALL})
    run = make_scoring_run(brand_profile_id=ws.id)
    run.algorithm_version = "v3"
    run.status = "scored"
    run.enable_ads, run.enable_seo, run.enable_social = True, True, True
    run.ads_capacity = 10
    run.seo_capacity = 10
    run.social_capacity = 10

    kw_city = make_keyword("ankara ofis kiralama", brand_profile_id=ws.id)
    kw_plain = make_keyword("ofis kiralama hizmeti", brand_profile_id=ws.id)
    db_session.commit()
    freeze_universe_snapshot(db_session, run)
    db_session.commit()

    calls, capture = _mock_stage_capture()
    with patch("app.core.engine.orchestrator.run_family_stage", side_effect=capture("family")), \
         patch("app.core.engine.orchestrator.run_ads_stage", side_effect=capture("ads")), \
         patch("app.core.engine.orchestrator.run_seo_stage", side_effect=capture("seo")), \
         patch("app.core.engine.orchestrator.run_social_stage", side_effect=capture("social")):
        run_v3_orchestration(db_session, run=run, ai=MagicMock())

    for stage_name in ("family", "ads", "seo", "social"):
        assert kw_city.id not in calls[stage_name], f"{stage_name} sehirli kelimeyi gormemeliydi"
        assert kw_plain.id in calls[stage_name], f"{stage_name} sehirsiz kelimeyi gormeliydi"


def test_focus_only_mode_inclusive_multi_city_rule(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """`focus_only`: odak şehir + şehirsiz kalır; yalnız odak-dışı şehir elenir.

    Kapsayıcı kural: odak şehirlerden en az biri geçiyorsa, başka odak dışı
    şehirler de geçse keyword KORUNUR.
    """
    ws = _setup_confirmed_workspace(
        make_workspace,
        location={"location_filter_mode": MODE_FOCUS_ONLY, "focus_cities": ["İstanbul"]},
    )
    run = make_scoring_run(brand_profile_id=ws.id)
    run.algorithm_version = "v3"
    run.status = "scored"
    run.enable_ads, run.enable_seo, run.enable_social = True, False, False
    run.ads_capacity = 10

    kw_inclusive = make_keyword("istanbul ankara arasi nakliyat", brand_profile_id=ws.id)
    kw_non_focus = make_keyword("ankara izmir nakliyat", brand_profile_id=ws.id)
    kw_plain = make_keyword("nakliyat hizmeti", brand_profile_id=ws.id)
    db_session.commit()
    freeze_universe_snapshot(db_session, run)
    db_session.commit()

    calls, capture = _mock_stage_capture()
    with patch("app.core.engine.orchestrator.run_family_stage", side_effect=capture("family")), \
         patch("app.core.engine.orchestrator.run_ads_stage", side_effect=capture("ads")):
        run_v3_orchestration(db_session, run=run, ai=MagicMock())

    assert calls["ads"] == {kw_inclusive.id, kw_plain.id}
    assert kw_non_focus.id not in calls["ads"]


@pytest.mark.parametrize("location", [
    {"location_filter_mode": MODE_EXCLUDE_ALL,
     "location_exempt_terms": ["gaziantep fıstığı"]},
    {"location_filter_mode": MODE_FOCUS_ONLY, "focus_cities": ["İstanbul"],
     "location_exempt_terms": ["gaziantep fıstığı"]},
])
def test_exemption_protected_keyword_survives_in_both_active_modes(
    db_session, make_workspace, make_scoring_run, make_keyword, location
):
    ws = _setup_confirmed_workspace(make_workspace, location=location)
    run = make_scoring_run(brand_profile_id=ws.id)
    run.algorithm_version = "v3"
    run.status = "scored"
    run.enable_ads, run.enable_seo, run.enable_social = True, False, False
    run.ads_capacity = 10

    kw_exempt = make_keyword("gaziantep fıstığı fiyat", brand_profile_id=ws.id)
    db_session.commit()
    freeze_universe_snapshot(db_session, run)
    db_session.commit()

    calls, capture = _mock_stage_capture()
    with patch("app.core.engine.orchestrator.run_family_stage", side_effect=capture("family")), \
         patch("app.core.engine.orchestrator.run_ads_stage", side_effect=capture("ads")):
        run_v3_orchestration(db_session, run=run, ai=MagicMock())

    assert kw_exempt.id in calls["ads"]


def test_capacity_is_computed_after_location_exclusion(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """Kapasite, lokasyon elemesinden SONRAKİ havuz üzerinden hesaplanır.

    ads_capacity=1 iken iki şehirli (elenecek) + bir temiz kelime varsa,
    ADS motoru yalnız temiz kelimeyi görmeli ve kapasite onunla dolmalı —
    şehirli adaylar kapasiteyi asla işgal etmemelidir.
    """
    ws = _setup_confirmed_workspace(
        make_workspace, location={"location_filter_mode": MODE_EXCLUDE_ALL})
    run = make_scoring_run(brand_profile_id=ws.id)
    run.algorithm_version = "v3"
    run.status = "scored"
    run.enable_ads, run.enable_seo, run.enable_social = True, False, False
    run.ads_capacity = 1

    kw_ankara = make_keyword("ankara ofis kiralama", brand_profile_id=ws.id)
    kw_izmir = make_keyword("izmir ofis kiralama", brand_profile_id=ws.id)
    kw_real = make_keyword("ofis kiralama hizmeti", brand_profile_id=ws.id)
    db_session.commit()
    freeze_universe_snapshot(db_session, run)
    db_session.commit()

    def _ads_stage(db, *, run, profile, rows, family_by_id, ai, firm_block_sha256, log=None):
        # Gercek ADS motorunu simule eder: yalniz KENDISINE VERILEN satirlardan
        # aday uretir (elenen satirlar buraya hic ulasmaz).
        return [
            {"keyword_id": int(r.keyword_id), "algorithm_rank": idx, "pool_class": "primary"}
            for idx, r in enumerate(sorted(rows, key=lambda x: x.keyword_id), start=1)
        ]

    with patch("app.core.engine.orchestrator.run_family_stage", return_value={}), \
         patch("app.core.engine.orchestrator.run_ads_stage", side_effect=_ads_stage):
        res = run_v3_orchestration(db_session, run=run, ai=MagicMock())

    pools = load_channel_pools(db_session, scoring_run_id=run.id)
    assert len(pools) == 1
    assert pools[0].keyword_id == kw_real.id

    ads_summary = res["delivery_summary"]["channels"]["ADS"]
    assert ads_summary["pool_count"] == 1
    assert ads_summary["unfilled_count"] == 0
    assert ads_summary["total_candidates"] == 1  # sehirli adaylar motora hic girmedi

    for kid in (kw_ankara.id, kw_izmir.id):
        assert kid not in {p.keyword_id for p in pools}


def test_all_keywords_excluded_raises_and_makes_zero_ai_calls(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """Evrenin tamamı lokasyon nedeniyle elenirse motor/AI hiç çağrılmaz."""
    ws = _setup_confirmed_workspace(
        make_workspace, location={"location_filter_mode": MODE_EXCLUDE_ALL})
    run = make_scoring_run(brand_profile_id=ws.id)
    run.algorithm_version = "v3"
    run.status = "scored"
    run.enable_ads, run.enable_seo, run.enable_social = True, False, False
    run.ads_capacity = 10

    make_keyword("ankara ofis kiralama", brand_profile_id=ws.id)
    make_keyword("izmir ofis kiralama", brand_profile_id=ws.id)
    db_session.commit()
    freeze_universe_snapshot(db_session, run)
    db_session.commit()

    ai_mock = MagicMock()
    with patch("app.core.engine.orchestrator.run_family_stage") as p_fam, \
         patch("app.core.engine.orchestrator.run_ads_stage") as p_ads:
        with pytest.raises(EngineInputError, match="TAMAMINI eledi"):
            run_v3_orchestration(db_session, run=run, ai=ai_mock)

        assert p_fam.call_count == 0
        assert p_ads.call_count == 0

    # AI servisine dogrudan/dolayli HICBIR cagri yapilmadi.
    assert ai_mock.mock_calls == []

    db_session.rollback()
    assert run.status == "scored"
    assert len(load_engine_selections(db_session, scoring_run_id=run.id)) == 0
    assert len(load_channel_pools(db_session, scoring_run_id=run.id)) == 0


# ── policy_gate ikinci savunma hattı ────────────────────────────────────
def _seal(run, ws, location_policy):
    return seal_manifest(
        run,
        firm_block_sha256=firm_block_sha256(firm_block(build_firm_profile(ws))),
        algorithm_versions={"ads": "nihai_niche_v1"},
        models={"ads": "gemini-2.5"},
        prompt_shas={"ads": "sha_ads"},
        location_policy=location_policy,
    )


def test_second_defense_line_excludes_injected_location_candidate(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """Pre-AI filtreyi atlayıp doğrudan delivery'ye enjekte edilen şehirli
    aday, ChannelPool'a asla yazılmaz; doğru exclude_reason ile elenir."""
    ws = _setup_confirmed_workspace(
        make_workspace, location={"location_filter_mode": MODE_EXCLUDE_ALL})
    run = make_scoring_run(brand_profile_id=ws.id)
    run.algorithm_version = "v3"
    run.enable_ads, run.enable_seo, run.enable_social = True, False, False
    run.ads_capacity = 10

    kw_city = make_keyword("ankara ofis kiralama", brand_profile_id=ws.id)
    db_session.commit()
    freeze_universe_snapshot(db_session, run)
    _seal(run, ws, policy_snapshot(ws.profile_data))
    db_session.commit()

    channel_selections = {
        "ADS": [
            {"keyword_id": kw_city.id, "algorithm_rank": 1,
             "scores": {"Core": 0.9, "Selection": 0.9}, "pool_class": "primary"},
        ],
    }

    summary = finalize_engine_delivery(db_session, run=run, channel_selections=channel_selections)
    db_session.commit()

    pools = db_session.query(ChannelPool).filter(ChannelPool.scoring_run_id == run.id).all()
    assert pools == []

    selections = db_session.query(EngineSelection).filter(
        EngineSelection.scoring_run_id == run.id).all()
    assert len(selections) == 1
    assert selections[0].final_rank is None
    assert selections[0].exclude_reason == f"{REASON_CITY_FILTER}:{canonical_city('ankara')}"

    assert summary["channels"]["ADS"]["pool_count"] == 0
    assert summary["channels"]["ADS"]["excluded_count"] == 1


# ── Run özeti: location_filter kırılımı ──────────────────────────────────
def test_run_summary_carries_location_filter_breakdown(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    ws = _setup_confirmed_workspace(
        make_workspace, location={"location_filter_mode": MODE_EXCLUDE_ALL})
    run = make_scoring_run(brand_profile_id=ws.id)
    run.algorithm_version = "v3"
    run.enable_ads, run.enable_seo, run.enable_social = True, False, False
    run.ads_capacity = 10

    kw_real = make_keyword("ofis kiralama hizmeti", brand_profile_id=ws.id)
    kw_ankara = make_keyword("ankara ofis kiralama", brand_profile_id=ws.id)
    kw_izmir = make_keyword("izmir ofis kiralama", brand_profile_id=ws.id)
    db_session.commit()
    freeze_universe_snapshot(db_session, run)
    _seal(run, ws, policy_snapshot(ws.profile_data))
    db_session.commit()

    # Yalniz temiz kelime aday olarak gonderilir (pre-AI filtre simulasyonu);
    # ozet yine de TAM evren uzerinden sayar.
    channel_selections = {
        "ADS": [
            {"keyword_id": kw_real.id, "algorithm_rank": 1,
             "scores": {"Core": 0.9, "Selection": 0.9}, "pool_class": "primary"},
        ],
    }

    summary = finalize_engine_delivery(db_session, run=run, channel_selections=channel_selections)
    db_session.commit()

    loc = summary["location_filter"]
    assert loc["enabled"] is True
    assert loc["mode"] == MODE_EXCLUDE_ALL
    assert loc["lexicon_version"] == "tr-provinces-v1"
    assert loc["excluded_count"] == 2
    assert loc["excluded_by_reason"] == {REASON_CITY_FILTER: 2}
    assert loc["excluded_by_city"] == {
        canonical_city("ankara"): 1, canonical_city("izmir"): 1,
    }

    for kid in (kw_ankara.id, kw_izmir.id):
        assert kid not in {p.keyword_id for p in
                           db_session.query(ChannelPool)
                           .filter(ChannelPool.scoring_run_id == run.id).all()}


def test_run_summary_location_filter_disabled_when_mode_none(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    ws = _setup_confirmed_workspace(make_workspace)  # mode yok -> none
    run = make_scoring_run(brand_profile_id=ws.id)
    run.algorithm_version = "v3"
    run.enable_ads, run.enable_seo, run.enable_social = True, False, False
    run.ads_capacity = 10

    kw = make_keyword("ankara ofis kiralama", brand_profile_id=ws.id)
    db_session.commit()
    freeze_universe_snapshot(db_session, run)
    _seal(run, ws, policy_snapshot(ws.profile_data))
    db_session.commit()

    channel_selections = {
        "ADS": [
            {"keyword_id": kw.id, "algorithm_rank": 1,
             "scores": {"Core": 0.9, "Selection": 0.9}, "pool_class": "primary"},
        ],
    }
    summary = finalize_engine_delivery(db_session, run=run, channel_selections=channel_selections)
    db_session.commit()

    loc = summary["location_filter"]
    assert loc["enabled"] is False
    assert loc["mode"] == MODE_NONE
    assert loc["excluded_count"] == 0
    assert loc["excluded_by_reason"] == {}
    assert loc["excluded_by_city"] == {}
    assert summary["channels"]["ADS"]["pool_count"] == 1


# ── Mühürlü politika kullanılır, canlı profil DEĞİL ──────────────────────
def test_orchestrator_uses_sealed_location_policy_not_live_profile(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """Mühürlenmiş `exclude_all` politikası, koşu sırasında canlı profil
    `none`'a değiştirilse bile uygulanmaya devam eder — mühür bu koşunun
    sözleşmesidir."""
    ws = _setup_confirmed_workspace(make_workspace)  # canli profil: mode yok -> none
    run = make_scoring_run(brand_profile_id=ws.id)
    run.algorithm_version = "v3"
    run.status = "scored"
    run.enable_ads, run.enable_seo, run.enable_social = True, False, False
    run.ads_capacity = 10

    kw_city = make_keyword("ankara ofis kiralama", brand_profile_id=ws.id)
    kw_plain = make_keyword("ofis kiralama hizmeti", brand_profile_id=ws.id)
    db_session.commit()
    freeze_universe_snapshot(db_session, run)
    db_session.commit()

    # Onceden farkli bir politikayla (exclude_all) muhurle — orkestratorun
    # KENDI seal_manifest cagrisi no-op'a alinacagi icin bu deger KALICIDIR.
    _seal(run, ws, policy_snapshot({"location_filter_mode": MODE_EXCLUDE_ALL}))
    db_session.commit()

    calls, capture = _mock_stage_capture()
    with patch("app.core.engine.orchestrator.seal_manifest") as p_seal, \
         patch("app.core.engine.orchestrator.run_family_stage", side_effect=capture("family")), \
         patch("app.core.engine.orchestrator.run_ads_stage", side_effect=capture("ads")):
        p_seal.return_value = None  # orkestratorun kendi muhuru NO-OP

        # finalize_engine_delivery'nin KENDI ayrı fail-closed kontrolü (mühür
        # vs. CANLI profil karşılaştırması, plan §5.4 — ayrı, önceden test
        # edilmiş bir çalışma) burada beklendiği gibi devreye girip durur,
        # çünkü mühür ile canlı profil kasıtlı olarak FARKLI. Bizim asıl
        # ilgilendiğimiz gözlem bundan ÖNCE gerçekleşir: pre-AI filtrenin
        # ADS motoruna hangi satırları gönderdiği.
        with pytest.raises(EngineInputError, match="lokasyon politikasi muhurden sonra degismis"):
            run_v3_orchestration(db_session, run=run, ai=MagicMock())

    # Canli profil `none` deseydi kw_city KORUNURDU; muhurlu `exclude_all`
    # kullanildigi icin ELENDI — bu, mühürün canli profile ustun oldugunu kanitlar.
    assert kw_city.id not in calls["ads"]
    assert kw_plain.id in calls["ads"]
