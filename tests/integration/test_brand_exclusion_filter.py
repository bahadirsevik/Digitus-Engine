import json
from decimal import Decimal

from app.core.channel.brand_filter import BRAND_EXCLUDED_REASON, BrandExclusionFilter
from app.core.channel.channel_engine import ChannelEngine
from app.core.channel.pre_filters.ads_prefilter import AdsPreFilter
from app.database.models import (
    ChannelCandidate,
    ChannelPool,
    IntentAnalysis,
    KeywordScore,
    PreFilterResult,
)


class FakeBrandAI:
    def __init__(self, results=None, *, fail=False):
        self.results = results or []
        self.fail = fail
        self.calls = 0

    def complete_json(self, *args, **kwargs):
        self.calls += 1
        if self.fail:
            raise RuntimeError("brand filter unavailable")
        return json.dumps({"results": self.results})


class FakePrefilterAI:
    def __init__(self):
        self.calls = 0

    def complete_json(self, *args, **kwargs):
        self.calls += 1
        return json.dumps(
            {
                "results": [
                    {
                        "keyword_id": 1,
                        "decision": "keep",
                        "label": "lead",
                        "reason_code": "HIGH_BUYING_INTENT",
                        "reason": "Relevant",
                        "transfer_channel": None,
                    }
                ]
            }
        )


def _add_candidate(db_session, *, run_id, keyword_id, channel, rank=1):
    db_session.add(
        ChannelCandidate(
            scoring_run_id=run_id,
            keyword_id=keyword_id,
            channel=channel,
            raw_score=Decimal("1.0"),
            rank_in_channel=rank,
        )
    )
    db_session.add(
        IntentAnalysis(
            scoring_run_id=run_id,
            keyword_id=keyword_id,
            channel=channel,
            intent_type="commercial",
            confidence_score=Decimal("0.90"),
            ai_reasoning="test",
            is_passed=True,
        )
    )
    db_session.commit()


def _profile_data(exclude_themes=None):
    if exclude_themes is None:
        exclude_themes = ["tekil hisse analizi"]
    return {
        "products": ["borsa egitimi"],
        "services": ["yatirim danismanligi"],
        "target_audience": "borsa yatirimcilari",
        "use_cases": ["portfoy yonetimi"],
        "problems_solved": ["bilincli yatirim"],
        "brand_terms": ["marka"],
        "exclude_themes": exclude_themes,
    }


def test_brand_filter_noop_without_confirmed_exclude_themes(
    db_session,
    make_workspace,
    make_scoring_run,
):
    draft_ws = make_workspace(status="draft", profile_data=_profile_data())
    draft_run = make_scoring_run(brand_profile_id=draft_ws.id)
    fake = FakeBrandAI()

    result = BrandExclusionFilter(db_session, fake).filter_intent_passed(draft_run.id)

    assert result["status"] == "skipped"
    assert result["reason"] == "profile_not_confirmed"
    assert fake.calls == 0

    confirmed_ws = make_workspace(profile_data=_profile_data(exclude_themes=[]))
    confirmed_run = make_scoring_run(brand_profile_id=confirmed_ws.id)

    result = BrandExclusionFilter(db_session, fake).filter_intent_passed(confirmed_run.id)

    assert result["status"] == "skipped"
    assert result["reason"] == "no_exclude_themes"
    assert fake.calls == 0


def test_brand_filter_writes_exclusion_only_for_candidate_channels(
    db_session,
    make_workspace,
    make_scoring_run,
    make_keyword,
):
    workspace = make_workspace(profile_data=_profile_data())
    run = make_scoring_run(brand_profile_id=workspace.id)
    keyword = make_keyword("thy hisse", brand_profile_id=workspace.id)
    _add_candidate(db_session, run_id=run.id, keyword_id=keyword.id, channel="ADS")
    _add_candidate(db_session, run_id=run.id, keyword_id=keyword.id, channel="SEO")
    fake = FakeBrandAI(
        [
            {
                "keyword_id": keyword.id,
                "is_brand_relevant": False,
                # Faz C v2: iki tema alanı da ZORUNLU (şema required)
                "matched_protected_theme": "",
                "matched_exclude_theme": "tekil hisse analizi",
                "reason": "Tekil hisse yorumu niyeti",
            }
        ]
    )

    result = BrandExclusionFilter(db_session, fake).filter_intent_passed(run.id)

    rows = (
        db_session.query(PreFilterResult)
        .filter(PreFilterResult.scoring_run_id == run.id)
        .order_by(PreFilterResult.channel)
        .all()
    )
    assert fake.calls == 1
    assert result["excluded"] == 2
    assert [row.channel for row in rows] == ["ADS", "SEO"]
    assert all(row.is_kept is False for row in rows)
    assert all(row.extra_data["reason_code"] == BRAND_EXCLUDED_REASON for row in rows)
    assert all(row.extra_data["matched_exclude_theme"] == "tekil hisse analizi" for row in rows)


def test_brand_exclusion_is_not_sent_to_channel_prefilter_or_overwritten(
    db_session,
    make_workspace,
    make_scoring_run,
    make_keyword,
):
    workspace = make_workspace(profile_data=_profile_data())
    run = make_scoring_run(brand_profile_id=workspace.id)
    keyword = make_keyword("enpara hisse", brand_profile_id=workspace.id)
    _add_candidate(db_session, run_id=run.id, keyword_id=keyword.id, channel="ADS")
    db_session.add(
        PreFilterResult(
            scoring_run_id=run.id,
            keyword_id=keyword.id,
            channel="ADS",
            is_kept=False,
            ai_reasoning="Tekil hisse",
            extra_data={
                "reason_code": BRAND_EXCLUDED_REASON,
                "matched_exclude_theme": "tekil hisse analizi",
            },
        )
    )
    db_session.commit()

    fake = FakePrefilterAI()
    result = AdsPreFilter(db_session, fake).filter_candidates(run.id)

    row = (
        db_session.query(PreFilterResult)
        .filter(
            PreFilterResult.scoring_run_id == run.id,
            PreFilterResult.keyword_id == keyword.id,
            PreFilterResult.channel == "ADS",
        )
        .one()
    )
    assert fake.calls == 0
    assert result["total"] == 0
    assert row.is_kept is False
    assert row.extra_data["reason_code"] == BRAND_EXCLUDED_REASON

    AdsPreFilter(db_session, fake)._save_results(
        run.id,
        [
            {
                "keyword_id": keyword.id,
                "is_kept": True,
                "label": "lead",
                "ai_reasoning": "Should not overwrite",
                "extra_data": {"reason_code": "HIGH_BUYING_INTENT"},
            }
        ],
    )
    db_session.refresh(row)
    assert row.is_kept is False
    assert row.extra_data["reason_code"] == BRAND_EXCLUDED_REASON


def test_brand_exclusion_not_backfilled_or_cross_channel_transferred(
    db_session,
    make_workspace,
    make_scoring_run,
    make_keyword,
):
    # v2: backfill tamamen kaldırıldı — elenen satır final havuza hiçbir
    # mekanizmayla giremez; bu test brand-excluded satırın dışarıda
    # kaldığını doğrulamaya devam eder.
    workspace = make_workspace(profile_data=_profile_data())
    run = make_scoring_run(brand_profile_id=workspace.id, ads_capacity=10, seo_capacity=2)
    excluded_kw = make_keyword("sasa hisse", brand_profile_id=workspace.id)
    kept_kw = make_keyword("borsa egitimi", brand_profile_id=workspace.id)
    _add_candidate(db_session, run_id=run.id, keyword_id=excluded_kw.id, channel="ADS", rank=1)
    _add_candidate(db_session, run_id=run.id, keyword_id=kept_kw.id, channel="ADS", rank=2)
    db_session.add_all(
        [
            KeywordScore(
                scoring_run_id=run.id,
                keyword_id=excluded_kw.id,
                ads_score=Decimal("100"),
                seo_score=Decimal("100"),
                social_score=Decimal("100"),
                ads_rank=1,
                seo_rank=1,
                social_rank=1,
            ),
            KeywordScore(
                scoring_run_id=run.id,
                keyword_id=kept_kw.id,
                ads_score=Decimal("90"),
                seo_score=Decimal("90"),
                social_score=Decimal("90"),
                ads_rank=2,
                seo_rank=2,
                social_rank=2,
            ),
            PreFilterResult(
                scoring_run_id=run.id,
                keyword_id=excluded_kw.id,
                channel="ADS",
                is_kept=False,
                ai_reasoning="Tekil hisse",
                transfer_channel="SEO",
                extra_data={"reason_code": BRAND_EXCLUDED_REASON},
            ),
            PreFilterResult(
                scoring_run_id=run.id,
                keyword_id=kept_kw.id,
                channel="ADS",
                is_kept=True,
                ai_reasoning="Relevant",
                extra_data={"reason_code": "HIGH_BUYING_INTENT"},
            ),
        ]
    )
    db_session.commit()

    engine = ChannelEngine(db_session, FakePrefilterAI())
    transfer = engine._process_cross_channel_transfers(run.id, AdsPreFilter(db_session, FakePrefilterAI()))
    final_counts = engine._build_final_pools_v2(run.id, run, relevance_coefficient=1.0)

    ads_pool_ids = {
        row.keyword_id
        for row in db_session.query(ChannelPool).filter(
            ChannelPool.scoring_run_id == run.id,
            ChannelPool.channel == "ADS",
        )
    }
    assert transfer["transferred"] == 0
    assert final_counts["ADS"] == 1
    assert excluded_kw.id not in ads_pool_ids
    assert kept_kw.id in ads_pool_ids


def test_brand_filter_fail_skip_keeps_existing_flow(
    db_session,
    make_workspace,
    make_scoring_run,
    make_keyword,
):
    workspace = make_workspace(profile_data=_profile_data())
    run = make_scoring_run(brand_profile_id=workspace.id)
    keyword = make_keyword("thy hisse", brand_profile_id=workspace.id)
    _add_candidate(db_session, run_id=run.id, keyword_id=keyword.id, channel="ADS")

    result = BrandExclusionFilter(db_session, FakeBrandAI(fail=True)).filter_intent_passed(run.id)

    assert result["status"] == "completed"
    assert result["failed_batches"] == 1
    assert (
        db_session.query(PreFilterResult)
        .filter(PreFilterResult.scoring_run_id == run.id)
        .count()
        == 0
    )


def test_get_channel_pools_includes_capacity(
    db_session,
    make_workspace,
    make_scoring_run,
    make_keyword,
):
    workspace = make_workspace(profile_data=_profile_data())
    run = make_scoring_run(
        brand_profile_id=workspace.id,
        ads_capacity=30,
        seo_capacity=20,
        social_capacity=10,
    )
    keyword = make_keyword("borsa egitimi", brand_profile_id=workspace.id)
    db_session.add(
        ChannelPool(
            scoring_run_id=run.id,
            keyword_id=keyword.id,
            channel="ADS",
            final_rank=1,
            adjusted_score=Decimal("1.0"),
        )
    )
    db_session.commit()

    result = ChannelEngine(db_session, FakePrefilterAI()).get_channel_pools(run.id)

    assert result["capacities"]["ADS"] == 30
    assert result["channels"]["ADS"][0]["capacity"] == 30


# ─────────────────────────────────────────────────────────────────
# Faz A: JSON parse dayanıklılığı — split/retry + fail-safe + metrikler
# ─────────────────────────────────────────────────────────────────

import re as _re


class SplitAwareBrandAI:
    """Büyük batch'te bozuk (kurtarılamaz) JSON, küçük batch'te geçerli döndürür.

    fail_above: bu sayıdan FAZLA keyword içeren batch bozuk yanıt verir.
      → fail_above=1 ise 2+ kelimelik batch çöker, split ile 1'e inince kurtulur.
      → fail_above=0 ise tek kelime bile çöker (fail-safe senaryosu).
    """

    def __init__(self, exclude_ids=(), fail_above=1):
        self.exclude_ids = set(exclude_ids)
        self.fail_above = fail_above
        self.calls = 0
        self.saw_schema = False

    def complete_json(self, prompt=None, response_schema=None, **kwargs):
        self.calls += 1
        if response_schema is not None:
            self.saw_schema = True
        m = _re.search(r"keywords=(\[.*\])\s*\n\nSADECE", prompt, _re.DOTALL)
        kws = json.loads(m.group(1)) if m else []
        if len(kws) > self.fail_above:
            return "BU GECERLI JSON DEGIL"  # parse edilemez → split tetikler
        results = [
            {
                "keyword_id": kw["id"],
                "is_brand_relevant": kw["id"] not in self.exclude_ids,
                "matched_protected_theme": "",
                "matched_exclude_theme": "kripto para" if kw["id"] in self.exclude_ids else "",
            }
            for kw in kws
        ]
        return json.dumps({"results": results})


def test_brand_filter_recovers_via_split_retry(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    workspace = make_workspace(profile_data=_profile_data(exclude_themes=["kripto para"]))
    run = make_scoring_run(brand_profile_id=workspace.id)
    kws = [make_keyword(f"kelime {i}", brand_profile_id=workspace.id) for i in range(3)]
    crypto = make_keyword("coin yorumlari", brand_profile_id=workspace.id)
    for kw in kws + [crypto]:
        _add_candidate(db_session, run_id=run.id, keyword_id=kw.id, channel="SEO")

    # 4 keyword tek batch (BATCH_SIZE=5). fail_above=1 → batch çöker, split ile kurtulur.
    fake = SplitAwareBrandAI(exclude_ids={crypto.id}, fail_above=1)
    result = BrandExclusionFilter(db_session, fake).filter_intent_passed(run.id)

    # Kripto keyword split/retry sonrası yine de dışlanmalı
    excluded = (
        db_session.query(PreFilterResult)
        .filter(PreFilterResult.scoring_run_id == run.id, PreFilterResult.is_kept == False)
        .all()
    )
    assert crypto.id in {row.keyword_id for row in excluded}
    assert result["excluded"] == 1
    assert result["split_retries"] > 0
    assert result["recovered_keywords"] > 0
    assert result["failed_keywords"] == 0
    assert fake.saw_schema is True  # response_schema geçildi (A2)


def test_brand_filter_failsafe_when_unrecoverable(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    workspace = make_workspace(profile_data=_profile_data(exclude_themes=["kripto para"]))
    run = make_scoring_run(brand_profile_id=workspace.id)
    kws = [make_keyword(f"kelime {i}", brand_profile_id=workspace.id) for i in range(3)]
    for kw in kws:
        _add_candidate(db_session, run_id=run.id, keyword_id=kw.id, channel="SEO")

    # fail_above=0 → tek kelime bile çözülemez → hepsi fail-safe (brand-relevant)
    fake = SplitAwareBrandAI(fail_above=0)
    result = BrandExclusionFilter(db_session, fake).filter_intent_passed(run.id)

    # Hiçbir keyword dışlanmamalı (çökme yerine güvenli tarafta kal)
    excluded = (
        db_session.query(PreFilterResult)
        .filter(PreFilterResult.scoring_run_id == run.id)
        .count()
    )
    assert excluded == 0
    assert result["excluded"] == 0
    assert result["failed_keywords"] == 3
    assert result["status"] == "completed"


def test_brand_filter_result_exposes_visibility_metrics(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    workspace = make_workspace(profile_data=_profile_data())
    run = make_scoring_run(brand_profile_id=workspace.id)
    kw = make_keyword("borsa yorum", brand_profile_id=workspace.id)
    _add_candidate(db_session, run_id=run.id, keyword_id=kw.id, channel="SEO")

    fake = SplitAwareBrandAI(fail_above=5)  # 1 kelime → sorunsuz
    result = BrandExclusionFilter(db_session, fake).filter_intent_passed(run.id)

    for key in ("failed_batches", "failed_keywords", "recovered_keywords", "split_retries"):
        assert key in result
    assert result["failed_keywords"] == 0
    assert result["split_retries"] == 0


# ─────────────────────────────────────────────────────────────────
# Faz C: korunan tema önceliği + kanonik tema sözleşmesi
# ─────────────────────────────────────────────────────────────────


class ThemeAwareBrandAI:
    """Keyword metnine göre sabit (relevant, protected, exclude) döndürür."""

    def __init__(self, decisions):
        self.decisions = decisions
        self.calls = 0

    def complete_json(self, prompt=None, **kwargs):
        self.calls += 1
        self.last_prompt = prompt
        m = _re.search(r"keywords=(\[.*\])\s*\n\nSADECE", prompt, _re.DOTALL)
        kws = json.loads(m.group(1)) if m else []
        results = []
        for kw in kws:
            relevant, protected, excluded = self.decisions.get(
                kw["keyword"], (True, "", "")
            )
            results.append({
                "keyword_id": kw["id"],
                "is_brand_relevant": relevant,
                "matched_protected_theme": protected,
                "matched_exclude_theme": excluded,
            })
        return json.dumps({"results": results})


GR7_PROFILE = {
    "products": ["GR-7 Anti-Grey Saç Losyonu"],
    "services": [],
    "target_audience": "Beyazlayan saça sahip yetişkinler",
    "use_cases": ["Boyasız beyaz saç çözümü"],
    "problems_solved": ["Saç beyazlaması"],
    "brand_terms": ["gr-7", "gr7"],
    "protected_themes": [
        "saç boyasının zararları",
        "boyaya alternatif arayışı",
        "beyaz ve gri saç problemi",
    ],
    "exclude_themes": [
        "belirli saç boyası markası",
        "renk kodu/numarası",
        "doğrudan boya satın alma",
        "saç ekimi",
    ],
}

DIGITUS_PROFILE = {
    "products": ["Web tasarım", "E-ticaret sistemleri"],
    "services": ["Teknik SEO", "Meta Ads"],
    "target_audience": "Kurumsal sanayi şirketleri",
    "use_cases": ["B2B talep üretimi"],
    "problems_solved": ["Dijital görünürlük eksikliği"],
    "brand_terms": ["digitus"],
    "protected_themes": ["e-ticaret", "ürün pazarlaması"],
    "exclude_themes": ["donanım ve cihaz satışı", "bilgisayar teknik servisi"],
}


def _run_filter(db_session, make_workspace, make_scoring_run, make_keyword,
                *, profile, decisions, keywords, channel="SEO"):
    workspace = make_workspace(profile_data=profile)
    run = make_scoring_run(brand_profile_id=workspace.id)
    kw_map = {}
    for text in keywords:
        kw = make_keyword(text, brand_profile_id=workspace.id)
        kw_map[text] = kw
        _add_candidate(db_session, run_id=run.id, keyword_id=kw.id, channel=channel)
    fake = ThemeAwareBrandAI(decisions)
    result = BrandExclusionFilter(db_session, fake).filter_intent_passed(run.id)
    excluded_ids = {
        row.keyword_id
        for row in db_session.query(PreFilterResult).filter(
            PreFilterResult.scoring_run_id == run.id,
            PreFilterResult.is_kept == False,  # noqa: E712
        )
    }
    return result, kw_map, excluded_ids, run, fake


def test_protected_theme_alone_keeps_keyword(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    result, kw_map, excluded, _, _ = _run_filter(
        db_session, make_workspace, make_scoring_run, make_keyword,
        profile=GR7_PROFILE,
        keywords=["sac boyasi zararlari"],
        decisions={"sac boyasi zararlari": (True, "saç boyasının zararları", "")},
    )
    assert excluded == set()
    assert result["protected_matches"] == 1
    assert result["protected_overrides"] == 0


def test_exclude_theme_alone_eliminates_keyword(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    result, kw_map, excluded, _, _ = _run_filter(
        db_session, make_workspace, make_scoring_run, make_keyword,
        profile=GR7_PROFILE,
        keywords=["sac boyasi renk kodu"],
        decisions={"sac boyasi renk kodu": (False, "", "renk kodu/numarası")},
    )
    assert excluded == {kw_map["sac boyasi renk kodu"].id}
    assert result["excluded"] == 1
    assert result["protected_overrides"] == 0


def test_protected_theme_beats_exclude_theme(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """21 kayıp senaryosu: model elerdi, kod korumayı uygular."""
    result, kw_map, excluded, run, _ = _run_filter(
        db_session, make_workspace, make_scoring_run, make_keyword,
        profile=GR7_PROFILE,
        keywords=["sac boyasina alternatif"],
        decisions={
            "sac boyasina alternatif": (
                False, "boyaya alternatif arayışı", "belirli saç boyası markası"
            )
        },
    )
    assert excluded == set()
    assert result["excluded"] == 0
    assert result["protected_overrides"] == 1


def test_hallucinated_protected_theme_is_not_accepted(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    result, kw_map, excluded, _, _ = _run_filter(
        db_session, make_workspace, make_scoring_run, make_keyword,
        profile=GR7_PROFILE,
        keywords=["kuaforde sac boyama"],
        decisions={
            # Uydurma koruma + GEÇERLİ dışlama → koruma yok sayılır, eleme kalır
            "kuaforde sac boyama": (
                False, "müşteri memnuniyeti", "belirli saç boyası markası"
            )
        },
    )
    assert excluded == {kw_map["kuaforde sac boyama"].id}
    assert result["theme_contract_violations"] == 1
    assert result["protected_overrides"] == 0


def test_hallucinated_exclude_theme_cannot_terminally_eliminate(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    result, kw_map, excluded, _, _ = _run_filter(
        db_session, make_workspace, make_scoring_run, make_keyword,
        profile=GR7_PROFILE,
        keywords=["beyaz sac bakimi"],
        decisions={"beyaz sac bakimi": (False, "", "kimyasal saç boyaları")},
    )
    assert excluded == set()
    assert result["theme_contract_violations"] == 1
    assert result["unbacked_exclusions"] == 1


def test_false_without_any_theme_is_not_terminal(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    result, kw_map, excluded, _, _ = _run_filter(
        db_session, make_workspace, make_scoring_run, make_keyword,
        profile=GR7_PROFILE,
        keywords=["gri sac losyonu"],
        decisions={"gri sac losyonu": (False, "", "")},
    )
    assert excluded == set()
    assert result["unbacked_exclusions"] == 1
    assert result["theme_contract_violations"] == 0  # boş alan ihlal değildir


def test_digitus_ecommerce_protected_hardware_excluded(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    result, kw_map, excluded, _, _ = _run_filter(
        db_session, make_workspace, make_scoring_run, make_keyword,
        profile=DIGITUS_PROFILE,
        keywords=["e-ticaret urun pazarlamasi", "laptop satin al"],
        decisions={
            "e-ticaret urun pazarlamasi": (
                False, "ürün pazarlaması", "donanım ve cihaz satışı"
            ),
            "laptop satin al": (False, "", "donanım ve cihaz satışı"),
        },
    )
    assert kw_map["e-ticaret urun pazarlamasi"].id not in excluded
    assert kw_map["laptop satin al"].id in excluded
    assert result["protected_overrides"] == 1
    assert result["excluded"] == 1


def test_exclusion_row_carries_full_audit(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    _, kw_map, _, run, _ = _run_filter(
        db_session, make_workspace, make_scoring_run, make_keyword,
        profile=GR7_PROFILE,
        keywords=["sac ekimi fiyat"],
        decisions={"sac ekimi fiyat": (False, "", "saç ekimi")},
    )
    row = (
        db_session.query(PreFilterResult)
        .filter(PreFilterResult.scoring_run_id == run.id)
        .one()
    )
    assert row.extra_data["reason_code"] == BRAND_EXCLUDED_REASON
    assert row.extra_data["matched_exclude_theme"] == "saç ekimi"
    assert row.extra_data["matched_protected_theme"] is None
    assert row.extra_data["model_decision"] is False
    assert row.extra_data["applied_decision"] is False
    assert row.extra_data["theme_winner"] == "exclude"


def test_prompt_carries_protected_themes(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    _, _, _, _, fake = _run_filter(
        db_session, make_workspace, make_scoring_run, make_keyword,
        profile=GR7_PROFILE,
        keywords=["beyaz sac"],
        decisions={},
    )
    assert "protected_themes" in fake.last_prompt
    assert "boyaya alternatif arayışı" in fake.last_prompt
    assert "KORUMA ÜSTÜNDÜR" in fake.last_prompt


# ── Codex #1: koruma garantisi modele BAGLI OLAMAZ ────────────────


class OmitProtectedFieldAI:
    """matched_protected_theme alanini HIC gondermeyen model (eksik cevap)."""

    def __init__(self, decisions):
        self.decisions = decisions

    def complete_json(self, prompt=None, **kwargs):
        m = _re.search(r"keywords=(\[.*\])\s*\n\nSADECE", prompt, _re.DOTALL)
        kws = json.loads(m.group(1)) if m else []
        return json.dumps({"results": [
            {
                "keyword_id": kw["id"],
                "is_brand_relevant": self.decisions.get(kw["keyword"], (True, ""))[0],
                "matched_exclude_theme":
                    self.decisions.get(kw["keyword"], (True, ""))[1],
            }
            for kw in kws
        ]})


def test_deterministic_protection_floor_when_model_leaves_field_empty(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """Codex karsi ornegi: model korunan alani BOS birakip gecerli dislama
    dondururse eskiden terminal eleme oluyordu. Keyword METNI acikca korunan
    temaya oturuyorsa koruma artik deterministik olarak uygulanir."""
    result, kw_map, excluded, _, _ = _run_filter(
        db_session, make_workspace, make_scoring_run, make_keyword,
        profile=GR7_PROFILE,
        keywords=["sac boyasina alternatif arayisi"],
        decisions={
            "sac boyasina alternatif arayisi": (
                False, "", "belirli saç boyası markası"
            )
        },
    )
    assert excluded == set()
    assert result["excluded"] == 0
    assert result["protected_deterministic"] == 1
    assert result["protected_overrides"] == 1
    decision = result["decisions"][0]
    assert decision["model_decision"] is False
    assert decision["applied_decision"] is True
    assert decision["theme_winner"] == "protected_deterministic"
    assert decision["matched_protected_theme"] == "boyaya alternatif arayışı"


def test_deterministic_floor_does_not_rescue_genuinely_excluded_keyword(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """Koruma tabani her seyi kurtarmaz: metin korunan temaya oturmuyorsa
    kanonik dislamaya dayanan eleme AYNEN durur."""
    result, kw_map, excluded, _, _ = _run_filter(
        db_session, make_workspace, make_scoring_run, make_keyword,
        profile=GR7_PROFILE,
        keywords=["sac boyasi renk kodu 7.0"],
        decisions={
            "sac boyasi renk kodu 7.0": (False, "", "renk kodu/numarası")
        },
    )
    assert excluded == {kw_map["sac boyasi renk kodu 7.0"].id}
    assert result["protected_deterministic"] == 0


def test_missing_protected_field_is_contract_violation(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    workspace = make_workspace(profile_data=GR7_PROFILE)
    run = make_scoring_run(brand_profile_id=workspace.id)
    kw = make_keyword("kuaforde sac boyama fiyati", brand_profile_id=workspace.id)
    _add_candidate(db_session, run_id=run.id, keyword_id=kw.id, channel="SEO")

    fake = OmitProtectedFieldAI({
        "kuaforde sac boyama fiyati": (False, "belirli saç boyası markası")
    })
    result = BrandExclusionFilter(db_session, fake).filter_intent_passed(run.id)

    assert result["excluded"] == 0  # eksik cevap terminal eleme URETEMEZ
    assert result["theme_contract_violations"] == 1
    assert result["decisions"][0]["theme_winner"] == "incomplete_answer_kept"


def test_response_schema_requires_both_theme_fields():
    item = BrandExclusionFilter.RESPONSE_SCHEMA["properties"]["results"]["items"]
    assert set(item["required"]) == {
        "keyword_id", "is_brand_relevant",
        "matched_protected_theme", "matched_exclude_theme",
    }


def test_decisions_audit_covers_every_evaluated_keyword(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """Codex #3: model kararı/uygulanan karar/kazanan tema TUM keyword'ler
    icin kalici olarak dondurulur (task result_data'ya akar)."""
    result, kw_map, _, _, _ = _run_filter(
        db_session, make_workspace, make_scoring_run, make_keyword,
        profile=GR7_PROFILE,
        keywords=["sac ekimi merkezi", "sac boyasi zararlari", "beyaz sac"],
        decisions={
            "sac ekimi merkezi": (False, "", "saç ekimi"),
            "sac boyasi zararlari": (False, "saç boyasının zararları", "belirli saç boyası markası"),
            "beyaz sac": (True, "", ""),
        },
    )
    by_kw = {d["keyword"]: d for d in result["decisions"]}
    assert set(by_kw) == {"sac ekimi merkezi", "sac boyasi zararlari", "beyaz sac"}
    assert by_kw["sac ekimi merkezi"]["theme_winner"] == "exclude"
    assert by_kw["sac ekimi merkezi"]["applied_decision"] is False
    assert by_kw["sac boyasi zararlari"]["theme_winner"] == "protected"
    assert by_kw["sac boyasi zararlari"]["model_decision"] is False
    assert by_kw["sac boyasi zararlari"]["applied_decision"] is True
    assert by_kw["beyaz sac"]["theme_winner"] == "none"
    assert all("channels" in d for d in result["decisions"])


# ── Codex #6: ID sözleşmesi (duplicate / ghost / unresolved) ──────


class BrokenIdAI:
    """Duplicate ve/veya ghost keyword_id döndüren model."""

    def __init__(self, *, duplicate=False, ghost=False):
        self.duplicate = duplicate
        self.ghost = ghost
        self.calls = 0

    def complete_json(self, prompt=None, **kwargs):
        self.calls += 1
        m = _re.search(r"keywords=(\[.*\])\s*\n\nSADECE", prompt, _re.DOTALL)
        kws = json.loads(m.group(1)) if m else []
        rows = []
        for kw in kws:
            rows.append({
                "keyword_id": kw["id"],
                "is_brand_relevant": False,
                "matched_protected_theme": "",
                "matched_exclude_theme": "saç ekimi",
            })
            if self.duplicate:
                # Ayni ID, CELISKILI ikinci cevap
                rows.append({
                    "keyword_id": kw["id"],
                    "is_brand_relevant": True,
                    "matched_protected_theme": "",
                    "matched_exclude_theme": "",
                })
        if self.ghost:
            rows.append({
                "keyword_id": 999999,
                "is_brand_relevant": False,
                "matched_protected_theme": "",
                "matched_exclude_theme": "saç ekimi",
            })
        return json.dumps({"results": rows})


def test_duplicate_id_is_not_silently_last_write_wins(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """Celiskili duplicate cevap terminal eleme URETEMEZ; fail-safe'e duser."""
    workspace = make_workspace(profile_data=GR7_PROFILE)
    run = make_scoring_run(brand_profile_id=workspace.id)
    kw = make_keyword("sac ekimi klinigi", brand_profile_id=workspace.id)
    _add_candidate(db_session, run_id=run.id, keyword_id=kw.id, channel="SEO")

    result = BrandExclusionFilter(
        db_session, BrokenIdAI(duplicate=True)
    ).filter_intent_passed(run.id)

    assert result["duplicate_ids"] >= 1
    assert result["excluded"] == 0
    assert result["failed_keywords"] == 1
    assert result["decisions"][0]["theme_winner"] == "unresolved_fail_safe"


def test_ghost_id_violates_strict_contract_and_cannot_eliminate(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """STRICT sözleşme (Codex #5): fazladan/uydurma ID de yanıt ihlalidir.

    Gerçek ID'lerin hepsi gelmiş olsa bile yanıt güvenilmez sayılır; retry
    yolundan geçer, düzelmezse fail-safe (eleme YAPILMAZ)."""
    workspace = make_workspace(profile_data=GR7_PROFILE)
    run = make_scoring_run(brand_profile_id=workspace.id)
    kw = make_keyword("sac ekimi merkezi", brand_profile_id=workspace.id)
    _add_candidate(db_session, run_id=run.id, keyword_id=kw.id, channel="SEO")

    result = BrandExclusionFilter(
        db_session, BrokenIdAI(ghost=True)
    ).filter_intent_passed(run.id)

    assert result["ghost_ids"] >= 1
    assert result["excluded"] == 0
    assert result["failed_keywords"] == 1
    assert result["decisions"][0]["theme_winner"] == "unresolved_fail_safe"


def test_decisions_cover_every_evaluated_keyword_including_unresolved(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """len(decisions) == evaluated: cozulemeyen kelime de audit'te olmali."""
    workspace = make_workspace(profile_data=GR7_PROFILE)
    run = make_scoring_run(brand_profile_id=workspace.id)
    for text in ("kelime a", "kelime b", "kelime c"):
        kw = make_keyword(text, brand_profile_id=workspace.id)
        _add_candidate(db_session, run_id=run.id, keyword_id=kw.id, channel="SEO")

    # fail_above=0 → hicbir kelime cozulemez (hepsi fail-safe)
    result = BrandExclusionFilter(
        db_session, SplitAwareBrandAI(fail_above=0)
    ).filter_intent_passed(run.id)

    assert result["failed_keywords"] == 3
    assert len(result["decisions"]) == result["evaluated"] == 3
    assert all(
        d["theme_winner"] == "unresolved_fail_safe" for d in result["decisions"]
    )
    assert all(d["model_decision"] is None for d in result["decisions"])


class TestBrandFilterPromptContract:
    """Marka filtresi prompt'u + çıktı sözleşmesi sürüm-kilitli (Faz C).

    `PROMPT_CONFIG_VERSION` registry'si (test_v21_phase_d) intent/prefilter
    prompt'larını kapsar; marka filtresi orada YOKTU. Bu kilit onun kendi
    APPEND-ONLY kaydıdır: bilinçli değişiklikte mevcut satır DÜZENLENMEZ,
    yeni BRAND_FILTER_PROMPT_VERSION anahtarıyla yeni kayıt eklenir.
    """

    PROMPT_HASH_REGISTRY = {
        # v1: protected_themes blogu + koruma onceligi (ilk surum)
        "2026-08-03-protected-v1":
            "5e2e142bbc202e01d1fb3f263e5524d7d569a228bf143a6bc8833dc6a1e94965",
        # v2 (Codex #1): iki tema alani da ZORUNLU; prompt bunu acikca soyler
        "2026-08-03-protected-v2":
            "4633db616edaa4d4aa729dc688ddf6448823867d81c2af9e77a0f2c64c3052e1",
    }
    FIXED_PROFILE = {
        "products": ["urun"],
        "services": ["hizmet"],
        "target_audience": "kitle",
        "use_cases": ["kullanim"],
        "problems_solved": ["problem"],
        "brand_terms": ["marka"],
        "exclude_themes": ["dislanacak tema"],
    }

    def test_prompt_is_version_locked(self):
        import hashlib

        from app.core.constants import BRAND_FILTER_PROMPT_VERSION

        assert BRAND_FILTER_PROMPT_VERSION in self.PROMPT_HASH_REGISTRY, (
            f"BRAND_FILTER_PROMPT_VERSION={BRAND_FILTER_PROMPT_VERSION!r} "
            "registry'de yok — prompt değiştiyse YENİ sürüm anahtarıyla yeni "
            "kayıt ekleyin; mevcut kayıtları DÜZENLEMEYİN."
        )
        # Sürümler birebir aynı hash'i taşıyamaz (sahte sürüm şişmesi)
        hashes = list(self.PROMPT_HASH_REGISTRY.values())
        assert len(hashes) == len(set(hashes))

        filt = BrandExclusionFilter(None, None)
        filt._protected_themes = ["korunacak tema"]
        prompt = filt._build_prompt(
            [{"id": 1, "keyword": "sabit kelime"}],
            self.FIXED_PROFILE,
            ["dislanacak tema"],
        )
        assert hashlib.sha256(prompt.encode("utf-8")).hexdigest() == (
            self.PROMPT_HASH_REGISTRY[BRAND_FILTER_PROMPT_VERSION]
        )

    def test_response_schema_declares_both_theme_fields(self):
        props = (
            BrandExclusionFilter.RESPONSE_SCHEMA["properties"]["results"]
            ["items"]["properties"]
        )
        assert "matched_protected_theme" in props
        assert "matched_exclude_theme" in props

    def test_profile_fields_include_protected_themes(self):
        assert "protected_themes" in BrandExclusionFilter.PROFILE_FIELDS
        assert "services" in BrandExclusionFilter.PROFILE_FIELDS


def test_manifest_carries_brand_filter_prompt_version(
    db_session, make_workspace, make_scoring_run, monkeypatch
):
    """Koşunun hangi marka-filtresi sözleşmesiyle yapıldığı denetlenebilir."""
    from app.core.channel.assignment_dispatcher import enqueue_channel_assignment
    from app.core.constants import BRAND_FILTER_PROMPT_VERSION

    monkeypatch.setattr(
        "app.tasks.intent_tasks.run_channel_assignment_task.apply_async",
        lambda *args, **kwargs: None,
    )
    workspace = make_workspace(
        profile_data=dict(_profile_data(), protected_themes=["korunacak tema"])
    )
    run = make_scoring_run(brand_profile_id=workspace.id, status="scored")

    enqueue_channel_assignment(
        db_session, run, relevance_coefficient=1.0, from_status="scored"
    )
    db_session.refresh(run)

    manifest = run.execution_manifest
    assert manifest["brand_filter_prompt_version"] == BRAND_FILTER_PROMPT_VERSION
    assert manifest["policy_snapshot"]["profile_protected_themes"] == [
        "korunacak tema"
    ]


def test_legacy_profile_without_protected_field_behaves_as_before(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """protected_themes taşımayan eski profil: eski eleme davranışı aynen."""
    legacy = dict(_profile_data())  # protected_themes YOK
    result, kw_map, excluded, _, _ = _run_filter(
        db_session, make_workspace, make_scoring_run, make_keyword,
        profile=legacy,
        keywords=["thy hisse"],
        decisions={"thy hisse": (False, "", "tekil hisse analizi")},
    )
    assert excluded == {kw_map["thy hisse"].id}
    assert result["protected_matches"] == 0
