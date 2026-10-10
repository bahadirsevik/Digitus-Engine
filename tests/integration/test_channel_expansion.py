import json
import re
from decimal import Decimal

import app.core.channel.channel_engine as channel_engine_module
from app.core.channel.brand_filter import BRAND_EXCLUDED_REASON
from app.core.channel.channel_engine import ChannelEngine
from app.core.channel.pre_filters.seo_prefilter import SeoPreFilter
from app.database.models import ChannelCandidate, ChannelPool, IntentAnalysis, Keyword, PreFilterResult


class KeepAllAI:
    def complete_json(self, prompt: str, **kwargs):
        ids = [int(value) for value in re.findall(r'"id":\s*(\d+)|-\s*(\d+):', prompt) for value in value if value]
        if "is_brand_relevant" in prompt:
            return json.dumps(
                {
                    "results": [
                        {
                            "keyword_id": keyword_id,
                            "is_brand_relevant": True,
                            "matched_exclude_theme": None,
                            "reason": "Relevant",
                        }
                        for keyword_id in ids
                    ]
                }
            )
        if "intent_type" in prompt:
            return json.dumps(
                [
                    {
                        "keyword_id": keyword_id,
                        "intent_type": "informational",
                        "confidence": 0.90,
                        "gt": 0,
                        "ga": 0,
                        "reasoning": "Relevant",
                    }
                    for keyword_id in ids
                ]
            )
        if "opinion_discussion" in prompt:
            # SOCIAL konuşulabilirlik şeması (v2): dims toplamı > 0 -> kept
            return json.dumps(
                {
                    "results": [
                        {
                            "keyword_id": keyword_id,
                            "dims": {
                                "opinion_discussion": 1,
                                "curiosity_comparison": 1,
                                "agenda_theme": 0,
                            },
                            "reason": "Relevant",
                            "meta": {"hook": "Hook", "scenario_note": "Not"},
                        }
                        for keyword_id in ids
                    ]
                }
            )
        # ADS prefilter yanıtı: v2 label doğrulaması yalnız hot_sale/lead
        # kabul eder (geçersiz label retry'a düşer) — mock "lead" döner.
        # SEO prefilter artık seçimde AI çağırmadığı için bu dal SEO'da ölü.
        return json.dumps(
            {
                "results": [
                    {
                        "keyword_id": keyword_id,
                        "decision": "keep",
                        "label": "lead",
                        "reason_code": "CATEGORY_SEARCH",
                        "reason": "Relevant",
                    }
                    for keyword_id in ids
                ]
            }
        )


class ThemeAwareAI:
    def complete_json(self, prompt: str, **kwargs):
        if "is_brand_relevant" in prompt:
            match = re.search(r"keywords=(\[.*?\])\n\nSADECE", prompt, re.DOTALL)
            keywords = json.loads(match.group(1)) if match else []
            return json.dumps(
                {
                    "results": [
                        {
                            "keyword_id": item["id"],
                            "is_brand_relevant": "hisse" not in item["keyword"],
                            # Faz C v2: iki tema alanı da ZORUNLU — korunan-tema
                            # sorusu yanıtlanmadan eleme terminal olamaz
                            "matched_protected_theme": "",
                            "matched_exclude_theme": (
                                "tekil hisse analizi" if "hisse" in item["keyword"] else None
                            ),
                            "reason": (
                                "Tekil hisse temasi"
                                if "hisse" in item["keyword"]
                                else "Markaya uygun"
                            ),
                        }
                        for item in keywords
                    ]
                }
            )
        ids = [int(value) for value in re.findall(r'"id":\s*(\d+)|-\s*(\d+):', prompt) for value in value if value]
        if "intent_type" in prompt:
            return json.dumps(
                [
                    {
                        "keyword_id": keyword_id,
                        "intent_type": "commercial",
                        "confidence": 0.90,
                        "reasoning": "Relevant",
                    }
                    for keyword_id in ids
                ]
            )
        return KeepAllAI().complete_json(prompt, **kwargs)


def _make_scored_keywords(db_session, make_keyword, make_keyword_score, workspace_id, run_id, count):
    keywords = []
    for idx in range(count):
        kw = make_keyword(f"rehber konu {idx:03d}", brand_profile_id=workspace_id)
        keywords.append(kw)
        score = Decimal(str(1000 - idx))
        make_keyword_score(
            scoring_run_id=run_id,
            keyword_id=kw.id,
            ads_score=score,
            seo_score=score,
            social_score=score,
            ads_rank=idx + 1,
            seo_rank=idx + 1,
            social_rank=idx + 1,
        )
    return keywords


def _seed_initial_prefilter_state(db_session, run_id, keywords, channels, kept_per_channel=1):
    for channel in channels:
        for idx, kw in enumerate(keywords[:60]):
            db_session.add(
                IntentAnalysis(
                    scoring_run_id=run_id,
                    keyword_id=kw.id,
                    channel=channel,
                    intent_type="informational",
                    confidence_score=Decimal("0.90"),
                    ai_reasoning="seed",
                    is_passed=True,
                )
            )
            db_session.add(
                PreFilterResult(
                    scoring_run_id=run_id,
                    keyword_id=kw.id,
                    channel=channel,
                    is_kept=idx < kept_per_channel,
                    label="treasure" if idx < kept_per_channel else "shallow",
                    ai_reasoning="seed",
                    extra_data={
                        "reason_code": (
                            "HIGH_CONTENT_DEPTH" if idx < kept_per_channel else "LOW_CONTENT_DEPTH"
                        )
                    },
                    is_fallback=False,
                )
            )
    db_session.commit()


def test_expansion_uses_seo_expansion_cap_beyond_legacy_pool_size(
    db_session,
    make_workspace,
    make_scoring_run,
    make_keyword,
    make_keyword_score,
):
    workspace = make_workspace(profile_data={"exclude_themes": []})
    run = make_scoring_run(
        brand_profile_id=workspace.id,
        seo_capacity=30,
        enable_ads=False,
        enable_social=False,
    )
    keywords = _make_scored_keywords(
        db_session, make_keyword, make_keyword_score, workspace.id, run.id, 140
    )
    engine = ChannelEngine(db_session, KeepAllAI())
    engine.pool_builder.build_candidate_pools(run.id)

    initial_candidates = (
        db_session.query(ChannelCandidate)
        .filter(ChannelCandidate.scoring_run_id == run.id, ChannelCandidate.channel == "SEO")
        .count()
    )
    assert initial_candidates == 60

    for idx, kw in enumerate(keywords[:60]):
        db_session.add(
            IntentAnalysis(
                scoring_run_id=run.id,
                keyword_id=kw.id,
                channel="SEO",
                intent_type="informational",
                confidence_score=Decimal("0.90"),
                ai_reasoning="seed",
                is_passed=True,
            )
        )
        db_session.add(
            PreFilterResult(
                scoring_run_id=run.id,
                keyword_id=kw.id,
                channel="SEO",
                is_kept=idx < 2,
                label="treasure" if idx < 2 else "shallow",
                ai_reasoning="seed",
                extra_data={"reason_code": "HIGH_CONTENT_DEPTH" if idx < 2 else "LOW_CONTENT_DEPTH"},
                is_fallback=False,
            )
        )
    db_session.commit()

    final_counts = engine._build_final_pools_v2(run.id, run)
    report = engine._run_expansion_rounds(run.id, run, final_counts, relevance_coefficient=1.0)

    expanded_candidates = (
        db_session.query(ChannelCandidate)
        .filter(ChannelCandidate.scoring_run_id == run.id, ChannelCandidate.channel == "SEO")
        .count()
    )
    assert expanded_candidates > 60
    assert expanded_candidates <= 180
    assert report["SEO"]["rounds_run"] >= 1
    assert report["SEO"]["expansion_ai_batch_budget"] == 60
    assert report["SEO"]["final_count_after_expansion"] >= 21


def test_specific_prefilter_skips_intent_failed_expansion_candidates(
    db_session,
    make_workspace,
    make_scoring_run,
    make_keyword,
):
    workspace = make_workspace(profile_data={"exclude_themes": []})
    run = make_scoring_run(brand_profile_id=workspace.id)
    passed_kw = make_keyword("borsa rehberi", brand_profile_id=workspace.id)
    failed_kw = make_keyword("uygunsuz konu", brand_profile_id=workspace.id)
    for rank, kw in enumerate([passed_kw, failed_kw], 1):
        db_session.add(
            ChannelCandidate(
                scoring_run_id=run.id,
                keyword_id=kw.id,
                channel="SEO",
                raw_score=Decimal("1.0"),
                rank_in_channel=rank,
            )
        )
        db_session.add(
            IntentAnalysis(
                scoring_run_id=run.id,
                keyword_id=kw.id,
                channel="SEO",
                intent_type="informational",
                confidence_score=Decimal("0.90"),
                ai_reasoning="test",
                is_passed=kw.id == passed_kw.id,
            )
        )
    db_session.commit()

    result = SeoPreFilter(db_session, KeepAllAI()).filter_candidates(
        run.id,
        keyword_ids=[passed_kw.id, failed_kw.id],
    )

    assert result["total"] == 1
    rows = (
        db_session.query(PreFilterResult)
        .filter(PreFilterResult.scoring_run_id == run.id, PreFilterResult.channel == "SEO")
        .all()
    )
    assert [row.keyword_id for row in rows] == [passed_kw.id]


def test_expansion_budget_is_split_per_expanding_channel(
    db_session,
    make_workspace,
    make_scoring_run,
    make_keyword,
    make_keyword_score,
    monkeypatch,
):
    monkeypatch.setattr(channel_engine_module, "MAX_EXPANSION_AI_BATCHES", 9)
    workspace = make_workspace(profile_data={"exclude_themes": []})
    run = make_scoring_run(
        brand_profile_id=workspace.id,
        ads_capacity=20,
        seo_capacity=20,
        social_capacity=20,
    )
    keywords = _make_scored_keywords(
        db_session, make_keyword, make_keyword_score, workspace.id, run.id, 100
    )
    engine = ChannelEngine(db_session, KeepAllAI())
    engine.pool_builder.build_candidate_pools(run.id)
    _seed_initial_prefilter_state(db_session, run.id, keywords, ["ADS", "SEO", "SOCIAL"])

    final_counts = engine._build_final_pools_v2(run.id, run)
    report = engine._run_expansion_rounds(run.id, run, final_counts, relevance_coefficient=1.0)

    assert report["ADS"]["expansion_ai_batch_budget"] == 3
    assert report["SEO"]["expansion_ai_batch_budget"] == 3
    assert report["SOCIAL"]["expansion_ai_batch_budget"] == 3
    # v2: SEO prefilter deterministik (0 AI çağrısı) — SEO aynı bütçeyle
    # daha fazla tur koşabilir; ADS/SOCIAL AI'lı akışta 1 turda bütçeyi bitirir
    assert report["ADS"]["rounds_run"] == 1
    assert report["SEO"]["rounds_run"] >= 1
    assert report["SOCIAL"]["rounds_run"] == 1
    # HARD bütçe: kanal başına gerçek AI çağrısı bütçeyi aşamaz
    for channel in ("ADS", "SEO", "SOCIAL"):
        assert report[channel]["expansion_ai_batches_used"] <= report[channel]["expansion_ai_batch_budget"]


def test_expansion_budget_reached_stops_channel_without_failing(
    db_session,
    make_workspace,
    make_scoring_run,
    make_keyword,
    make_keyword_score,
    monkeypatch,
):
    monkeypatch.setattr(channel_engine_module, "MAX_EXPANSION_AI_BATCHES", 3)
    workspace = make_workspace(profile_data={"exclude_themes": []})
    run = make_scoring_run(
        brand_profile_id=workspace.id,
        ads_capacity=20,
        enable_seo=False,
        enable_social=False,
    )
    keywords = _make_scored_keywords(
        db_session, make_keyword, make_keyword_score, workspace.id, run.id, 100
    )
    engine = ChannelEngine(db_session, KeepAllAI())
    engine.pool_builder.build_candidate_pools(run.id)
    _seed_initial_prefilter_state(db_session, run.id, keywords, ["ADS"])

    final_counts = engine._build_final_pools_v2(run.id, run)
    report = engine._run_expansion_rounds(run.id, run, final_counts, relevance_coefficient=1.0)

    assert report["ADS"]["rounds_run"] == 1
    assert report["ADS"]["expansion_ai_batches_used"] <= report["ADS"]["expansion_ai_batch_budget"]
    assert report["ADS"]["stop_reason"] == "budget_reached"


def test_expansion_brand_exclusions_do_not_reach_final_pool(
    db_session,
    make_workspace,
    make_scoring_run,
    make_keyword,
    make_keyword_score,
):
    workspace = make_workspace(
        profile_data={
            "products": ["borsa egitimi"],
            "services": ["yatirim danismanligi"],
            "target_audience": "yatirimcilar",
            "use_cases": ["portfoy"],
            "problems_solved": ["bilincli yatirim"],
            "brand_terms": ["marka"],
            "exclude_themes": ["tekil hisse analizi"],
        }
    )
    run = make_scoring_run(
        brand_profile_id=workspace.id,
        ads_capacity=20,
        enable_seo=False,
        enable_social=False,
    )
    keywords = []
    for idx in range(90):
        text = f"akbank hisse {idx:03d}" if idx < 70 else f"borsa egitimi {idx:03d}"
        kw = make_keyword(text, brand_profile_id=workspace.id)
        keywords.append(kw)
        score = Decimal(str(1000 - idx))
        make_keyword_score(
            scoring_run_id=run.id,
            keyword_id=kw.id,
            ads_score=score,
            seo_score=score,
            social_score=score,
            ads_rank=idx + 1,
            seo_rank=idx + 1,
            social_rank=idx + 1,
        )

    engine = ChannelEngine(db_session, ThemeAwareAI())
    engine.pool_builder.build_candidate_pools(run.id)
    for kw in keywords[:60]:
        db_session.add(
            IntentAnalysis(
                scoring_run_id=run.id,
                keyword_id=kw.id,
                channel="ADS",
                intent_type="commercial",
                confidence_score=Decimal("0.90"),
                ai_reasoning="seed",
                is_passed=True,
            )
        )
    db_session.commit()
    engine._run_brand_filter(run.id)
    channel_engine_module.AdsPreFilter(db_session, ThemeAwareAI()).filter_candidates(run.id)

    final_counts = engine._build_final_pools_v2(run.id, run)
    report = engine._run_expansion_rounds(run.id, run, final_counts, relevance_coefficient=1.0)
    final_counts = engine._build_final_pools_v2(run.id, run)

    pool_keywords = [
        keyword.keyword
        for _, keyword in (
            db_session.query(ChannelPool, Keyword)
            .join(Keyword, ChannelPool.keyword_id == Keyword.id)
            .filter(ChannelPool.scoring_run_id == run.id, ChannelPool.channel == "ADS")
            .all()
        )
    ]
    excluded_rows = (
        db_session.query(PreFilterResult)
        .filter(PreFilterResult.scoring_run_id == run.id)
        .filter(PreFilterResult.channel == "ADS")
        .filter(PreFilterResult.extra_data["reason_code"].as_string() == BRAND_EXCLUDED_REASON)
        .count()
    )

    assert report["ADS"]["rounds_run"] >= 1
    assert report["ADS"]["brand_excluded"] == excluded_rows
    assert final_counts["ADS"] > 0
    assert all("hisse" not in keyword for keyword in pool_keywords)
