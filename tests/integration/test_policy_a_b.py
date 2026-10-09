"""Plan A+B testleri: rakip hard-block + SOCIAL topic-policy + stale invariant.

Kilit sözleşmeler:
- YALNIZ approved term filtreye girer; rakip aday için AI çağrısı = 0
- Terminal PreFilterResult satırları hiçbir katmanca ezilmez/işlenmez
- SOCIAL stale üç tabloda tek transaction; okuma üçlü non-stale doğrular
- Stale entity üzerinden select/regenerate → ValueError (endpoint 409)
"""
import pytest

from app.core.channel.competitor_filter import apply_competitor_block
from app.core.constants import COMPETITOR_TERM_REASON, TERMINAL_PREFILTER_REASONS
from app.core.policy.competitor_policy import (
    match_term,
    suggest_competitor_terms,
)
from app.database.models import (
    ChannelCandidate,
    Keyword,
    PreFilterResult,
    SocialCategory,
    SocialContent,
    SocialIdea,
)


# ==================== policy servisi ====================

class TestCompetitorPolicyService:
    def test_terminal_reasons_match_layer_constants(self):
        # Değerler brand_filter/seo_prefilter sabitleriyle AYNI olmak zorunda
        from app.core.channel.brand_filter import BRAND_EXCLUDED_REASON
        assert BRAND_EXCLUDED_REASON in TERMINAL_PREFILTER_REASONS
        assert "PRICE_TERM" in TERMINAL_PREFILTER_REASONS
        assert COMPETITOR_TERM_REASON in TERMINAL_PREFILTER_REASONS

    def test_suggestions_skip_rejected(self):
        existing = [{"term": "fintables", "status": "rejected", "source": "domain"}]
        out = suggest_competitor_terms(["https://fintables.com/"], existing)
        assert out == []  # reddedilen öneri bir daha üretilmez

    def test_token_boundary_no_substring(self):
        assert match_term("fintables üyelik ücreti", ["fintables"]) == "fintables"
        assert match_term("finans analizi", ["fin"]) is None
        assert match_term("hissenet borsa", ["hisse net"]) is None  # bitişik ≠ ayrık


# ==================== rakip hard-block ====================

def _mk_candidate(db, run_id, kw_text, channel, rank):
    kw = Keyword(keyword=kw_text, normalized_keyword=kw_text)
    db.add(kw)
    db.flush()
    db.add(ChannelCandidate(
        scoring_run_id=run_id, keyword_id=kw.id,
        channel=channel, rank_in_channel=rank,
        raw_score=10.0,
    ))
    return kw


class TestCompetitorBlock:
    def _setup(self, db_session, make_workspace, make_scoring_run, policy=None):
        ws = make_workspace("Rakip WS")
        ws.competitor_terms = [
            {"term": "fintables", "status": "approved", "source": "domain"},
            {"term": "ekofin", "status": "suggested", "source": "domain"},
        ]
        if policy:
            ws.competitor_policy = policy
        run = make_scoring_run(brand_profile_id=ws.id, status="channel_assigning")
        kw_comp = _mk_candidate(db_session, run.id, "fintables üyelik ücreti", "ADS", 1)
        kw_clean = _mk_candidate(db_session, run.id, "hisse analiz", "ADS", 2)
        kw_sugg = _mk_candidate(db_session, run.id, "ekofin yorum", "ADS", 3)
        db_session.commit()
        return ws, run, kw_comp, kw_clean, kw_sugg

    def test_approved_term_blocked_suggested_not(
        self, db_session, make_workspace, make_scoring_run
    ):
        ws, run, kw_comp, kw_clean, kw_sugg = self._setup(
            db_session, make_workspace, make_scoring_run
        )
        blocked = apply_competitor_block(db_session, run.id)
        assert blocked.get("ADS") == 1  # yalnız approved 'fintables'

        row = db_session.query(PreFilterResult).filter_by(
            scoring_run_id=run.id, keyword_id=kw_comp.id, channel="ADS"
        ).one()
        assert row.is_kept is False
        assert row.extra_data["reason_code"] == COMPETITOR_TERM_REASON
        assert row.extra_data["matched_term"] == "fintables"

        # suggested (onaysız) ve temiz kelimeler için satır YOK
        others = db_session.query(PreFilterResult).filter(
            PreFilterResult.scoring_run_id == run.id,
            PreFilterResult.keyword_id.in_([kw_clean.id, kw_sugg.id]),
        ).count()
        assert others == 0

    def test_allow_policy_skips_channel(
        self, db_session, make_workspace, make_scoring_run
    ):
        ws, run, kw_comp, *_ = self._setup(
            db_session, make_workspace, make_scoring_run,
            policy={"ads": "allow", "seo": "block", "social": "block"},
        )
        blocked = apply_competitor_block(db_session, run.id)
        assert blocked.get("ADS") is None or blocked.get("ADS", 0) == 0

    def test_blocked_candidate_gets_zero_ai_calls(
        self, db_session, make_workspace, make_scoring_run
    ):
        """Zorunlu test (plan A): rakip kelime için AI çağrısı = 0."""
        from app.core.channel.intent_analyzer import IntentAnalyzer

        ws, run, kw_comp, kw_clean, _ = self._setup(
            db_session, make_workspace, make_scoring_run
        )
        apply_competitor_block(db_session, run.id)

        class CountingAI:
            calls = 0

            def complete_json(self, *a, **k):
                CountingAI.calls += 1
                raise RuntimeError("parse fail → fallback")

        analyzer = IntentAnalyzer(db_session, CountingAI())
        result = analyzer.analyze_candidates(run.id, "ADS")
        # Bloklanan aday analize hiç girmedi: analyzed yalnız temiz+suggested
        assert result.get("analyzed", 0) == 2
        # Terminal satır intent tarafından ezilmedi
        row = db_session.query(PreFilterResult).filter_by(
            scoring_run_id=run.id, keyword_id=kw_comp.id, channel="ADS"
        ).one()
        assert row.extra_data["reason_code"] == COMPETITOR_TERM_REASON

    def test_terminal_row_not_overwritten_by_save_results(
        self, db_session, make_workspace, make_scoring_run
    ):
        from app.core.channel.pre_filters.seo_prefilter import SeoPreFilter

        ws = make_workspace("Terminal WS")
        ws.competitor_terms = [
            {"term": "fintables", "status": "approved", "source": "user"}
        ]
        run = make_scoring_run(brand_profile_id=ws.id, status="channel_assigning")
        kw = _mk_candidate(db_session, run.id, "fintables bilanço", "SEO", 1)
        db_session.commit()

        apply_competitor_block(db_session, run.id)

        pf = SeoPreFilter(db_session, ai_service=None)
        pf._save_results(run.id, [{
            "keyword_id": kw.id, "is_kept": True, "label": None,
            "ai_reasoning": "", "extra_data": {"reason_code": "SEO_KEEP_DEFAULT"},
        }])
        db_session.commit()

        row = db_session.query(PreFilterResult).filter_by(
            scoring_run_id=run.id, keyword_id=kw.id, channel="SEO"
        ).one()
        assert row.is_kept is False  # terminal satır korunud — ezilmedi
        assert row.extra_data["reason_code"] == COMPETITOR_TERM_REASON


# ==================== SOCIAL stale invariant ====================

def _mk_social_tree(db, run_id, stale=False):
    cat = SocialCategory(
        scoring_run_id=run_id, category_name="Eğitim",
        category_type="educational", is_stale=stale,
    )
    db.add(cat)
    db.flush()
    idea = SocialIdea(
        category_id=cat.id, idea_title="Fikir X",
        target_platform="instagram", content_format="post",
        trend_alignment=0.5, is_stale=stale,
    )
    db.add(idea)
    db.flush()
    content = SocialContent(
        idea_id=idea.id, caption="c", cta_text="t",
        hashtags=["a", "b", "c", "d", "e"], hooks=[{"text": "h", "style": "question"}],
        is_stale=stale,
    )
    db.add(content)
    db.flush()
    return cat, idea, content


class TestSocialStaleInvariant:
    def test_reassignment_marks_three_tables(
        self, db_session, make_workspace, make_scoring_run
    ):
        from app.core.scoring.state_machine import transition

        ws = make_workspace("Stale Social WS")
        run = make_scoring_run(brand_profile_id=ws.id, status="scored")
        cat, idea, content = _mk_social_tree(db_session, run.id)
        db_session.commit()

        transition(db_session, run, "channel_assigning")
        db_session.expire_all()

        assert db_session.get(SocialCategory, cat.id).is_stale is True
        assert db_session.get(SocialIdea, idea.id).is_stale is True
        assert db_session.get(SocialContent, content.id).is_stale is True

    def test_get_all_filters_and_include_stale(
        self, db_session, make_workspace, make_scoring_run
    ):
        from app.generators.social.social_generator import SocialGenerator

        ws = make_workspace("GetAll WS")
        run = make_scoring_run(brand_profile_id=ws.id)
        _mk_social_tree(db_session, run.id, stale=True)
        fresh_cat, fresh_idea, fresh_content = _mk_social_tree(db_session, run.id)
        db_session.commit()

        gen = SocialGenerator(db_session, ai_service=None)
        default_view = gen.get_all(run.id)
        assert len(default_view.categories) == 1
        assert len(default_view.ideas) == 1
        assert len(default_view.contents) == 1

        audit_view = gen.get_all(run.id, include_stale=True)
        assert len(audit_view.categories) == 2

    def test_stale_select_and_regenerate_raise(
        self, db_session, make_workspace, make_scoring_run
    ):
        from app.generators.social.social_generator import SocialGenerator

        ws = make_workspace("Stale409 WS")
        run = make_scoring_run(brand_profile_id=ws.id)
        cat, idea, content = _mk_social_tree(db_session, run.id, stale=True)
        db_session.commit()

        gen = SocialGenerator(db_session, ai_service=None)
        with pytest.raises(ValueError, match="STALE"):
            gen.select_idea(idea.id, True)
        with pytest.raises(ValueError, match="STALE"):
            gen.regenerate_idea(idea.id, "Marka")
        with pytest.raises(ValueError, match="STALE"):
            gen.regenerate_content(content.id, "Marka")


# ==================== SOCIAL topic policy ====================

class TestSocialTopicPolicy:
    def test_content_rejected_by_approved_topic_term(
        self, db_session, make_workspace, make_scoring_run, monkeypatch
    ):
        """Temettü senaryosu: approved topic term içeren içerik KAYDEDİLMEZ,
        tipli policy warning döner."""
        from app.generators.social.social_generator import SocialGenerator
        from app.schemas.social import SocialContentsRequest

        ws = make_workspace("Topic WS")
        ws.topic_policy = {
            "excluded_terms": [
                {"term": "temettü", "status": "approved", "source": "user"}
            ],
            "excluded_aliases": [],
        }
        run = make_scoring_run(brand_profile_id=ws.id)
        cat, idea, _ = _mk_social_tree(db_session, run.id)
        idea2 = SocialIdea(
            category_id=cat.id, idea_title="Temettü Emekliliği Nedir?",
            target_platform="instagram", content_format="reels",
            trend_alignment=0.6,
        )
        db_session.add(idea2)
        db_session.commit()

        from app.schemas.social import (
            HookSchema, HookStyleEnum, SocialContentSchema,
        )

        def fake_generate(self, idea, brand_name, brand_tone=None, product_facts=None):
            return SocialContentSchema(
                idea_id=idea.id or 0,
                hooks=[HookSchema(text="Merak", style=HookStyleEnum.QUESTION)],
                caption=f"{idea.idea_title} hakkında temettü takibi ipuçları"
                if "Temettü" in idea.idea_title else "Temiz içerik",
                cta_text="Kaydet",
                hashtags=["a", "b", "c", "d", "e"],
            )

        monkeypatch.setattr(
            "app.generators.social.content_generator.ContentGenerator.generate",
            fake_generate,
        )

        gen = SocialGenerator(db_session, ai_service=None)
        resp = gen.generate_contents(
            SocialContentsRequest(idea_ids=[idea.id, idea2.id], brand_name="M"),
            scoring_run_id=run.id,
        )
        # idea (temiz) kaydedildi; idea2 (temettü) reddedildi
        assert resp.total_contents == 1
        assert len(resp.policy_warnings) == 1
        w = resp.policy_warnings[0]
        assert w.entity_type == "content"
        assert w.reason_code == "excluded_topic"
        assert w.matched_term == "temettü"
        assert w.display_name == "Temettü Emekliliği Nedir?"

    def test_stale_parent_blocks_new_content(
        self, db_session, make_workspace, make_scoring_run
    ):
        from app.generators.social.social_generator import SocialGenerator
        from app.schemas.social import SocialContentsRequest

        ws = make_workspace("StaleParent WS")
        run = make_scoring_run(brand_profile_id=ws.id)
        cat, idea, _ = _mk_social_tree(db_session, run.id, stale=True)
        db_session.commit()

        gen = SocialGenerator(db_session, ai_service=None)
        resp = gen.generate_contents(
            SocialContentsRequest(idea_ids=[idea.id], brand_name="M"),
            scoring_run_id=run.id,
        )
        assert resp.total_contents == 0
        assert resp.policy_warnings[0].reason_code == "stale_parent"
