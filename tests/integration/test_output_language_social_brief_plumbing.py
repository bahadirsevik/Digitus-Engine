"""Sosyal brief yolunda cikti dili + hedef konum baglami (plan_pazar_dil_secimi.md Faz 2-c).

AI asamasinda DB oturumu acik olmadigi icin baglam, claim/prep transaction'inda
calismadan okunur ve uretici katmanina tasinir. Kategori (HTTP), fikir ve icerik
(+repair) orkestrasyonlari Berlin/ru calismasi icin gercek DB'ye karsi surulur;
yakalanan HER prompt blogu tasir. AI sahte — UCRETLI CAGRI YOK.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))  # kardes test modulleri (importlib modu)

from app.database.connection import SessionLocal
from app.dependencies import get_ai
from app.generators.output_language import DEFAULT_CONTEXT, MARKET_RULE
from app.main import app

# Mevcut brief testlerinin kurulum yardimcilari + autouse fixture'lari
from test_social_category_generate_api_f1e5 import (  # noqa: F401
    FakeAIService as CategoryAI,
    _setup_environment as category_env,
    _valid_category_json,
    bind_dependencies_get_db,
    cleanup_ai_override,
    enable_flag,
)
from test_social_content_orchestration_f1g5_5 import (
    SmartMockAIService as ContentAI,
    T0 as CONTENT_T0,
    _setup_content_orchestration_env as content_env,
)
from test_social_idea_orchestration_f1f6b import (
    SmartMockAIService as IdeaAI,
    T0 as IDEA_T0,
    _setup_ready_ideas_environment as idea_env,
)

from app.core.social.content_orchestration import run_social_content_generation
from app.core.social.idea_orchestration import run_social_idea_generation

BERLIN, RU = "1003854", "1031"
EXPECT = "ÇIKTI DİLİ: Rusça. HEDEF KONUM: Berlin, Almanya — Şehir"


def _to_berlin_ru(db_session, ws):
    ws.default_geo_target_id = BERLIN
    ws.default_language_id = RU
    db_session.commit()


def test_category_endpoint_prompt_has_workspace_market(
    client, db_session, enable_flag, make_workspace, make_scoring_run, make_keyword
):
    ws, run, brief, kws = category_env(db_session, make_workspace, make_scoring_run, make_keyword)
    _to_berlin_ru(db_session, ws)
    fake = CategoryAI(responses=[_valid_category_json(tuple(k.id for k in kws))])
    app.dependency_overrides[get_ai] = lambda: fake

    resp = client.post(
        f"/api/v1/generation/social/briefs/{brief.id}/categories/generate?brand_profile_id={ws.id}",
        json={"idempotency_key": "market-key-1", "max_categories": 4},
    )
    assert resp.status_code == 201, resp.text
    assert fake.call_args and all(EXPECT in c["prompt"] for c in fake.call_args)
    assert fake.call_args[0]["prompt"].rstrip().endswith(MARKET_RULE)  # blok prompt'un sonunda


def test_category_endpoint_turkish_workspace_gets_turkish_block(
    client, db_session, enable_flag, make_workspace, make_scoring_run, make_keyword
):
    ws, run, brief, kws = category_env(db_session, make_workspace, make_scoring_run, make_keyword)
    fake = CategoryAI(responses=[_valid_category_json(tuple(k.id for k in kws))])
    app.dependency_overrides[get_ai] = lambda: fake
    resp = client.post(
        f"/api/v1/generation/social/briefs/{brief.id}/categories/generate?brand_profile_id={ws.id}",
        json={"idempotency_key": "market-key-2", "max_categories": 4},
    )
    assert resp.status_code == 201, resp.text
    assert "ÇIKTI DİLİ: Türkçe. HEDEF KONUM: Türkiye (TR) — Ülke" in fake.call_args[0]["prompt"]


def test_idea_orchestration_prompts_have_workspace_market(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    ws, run, brief, cats, targets, kws, start, attempt = idea_env(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-mkt-1"
    )
    _to_berlin_ru(db_session, ws)
    ai = IdeaAI()
    result = run_social_idea_generation(
        session_factory=SessionLocal, ai_service=ai, attempt_id=attempt.id,
        task_id="task-mkt-1", now_provider=lambda: IDEA_T0,
    )
    assert result.status == "completed"
    assert ai.calls and all(EXPECT in c["prompt"] for c in ai.calls)


def test_content_orchestration_prompts_have_workspace_market(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    ctx = content_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=2, task_id="task-mkt-2"
    )
    _to_berlin_ru(db_session, ctx["workspace"])
    ai = ContentAI()
    result = run_social_content_generation(
        session_factory=SessionLocal, ai_service=ai, attempt_id=ctx["attempt"].id,
        task_id="task-mkt-2", now_provider=lambda: CONTENT_T0,
    )
    assert result.status == "completed"
    assert len(ai.calls) == 2
    assert all(EXPECT in c["prompt"] for c in ai.calls)


def test_default_context_is_turkish_and_not_selected():
    assert (DEFAULT_CONTEXT.language, DEFAULT_CONTEXT.location) == ("Türkçe", "Seçilmedi")


# ==================== SEO + ADS (calisma -> uretici) ====================

def test_seo_and_ads_generators_derive_context_from_run(
    db_session, make_workspace, make_scoring_run, make_keyword, monkeypatch
):
    import pytest

    from app.generators.ads.ads_generator import AdsGenerator
    from app.generators.seo_geo.seo_geo_generator import SEOGEOGenerator
    from app.schemas.ads import AdsGenerateRequest
    from app.schemas.seo_geo import SEOGEOGenerateRequest

    ws = make_workspace("Plumb", default_geo_target_id=BERLIN, default_language_id=RU)
    run = make_scoring_run(brand_profile_id=ws.id)
    kw = make_keyword("kelime", brand_profile_id=ws.id)

    class Captured(Exception):
        pass

    seen = {}

    def fake_raw(self, **kwargs):
        seen["seo"] = self._output_ctx
        raise Captured()

    monkeypatch.setattr(SEOGEOGenerator, "_generate_raw_content", fake_raw)
    with pytest.raises(Captured):
        SEOGEOGenerator(db_session, None).generate_content(
            SEOGEOGenerateRequest(keyword_id=kw.id), scoring_run_id=run.id
        )
    assert (seen["seo"].language, seen["seo"].location) == ("Rusça", "Berlin, Almanya — Şehir")

    gen = AdsGenerator(db_session, None)
    monkeypatch.setattr(gen, "_get_ads_keywords", lambda run_id: [{"id": kw.id, "keyword": "kelime", "sector": None}])
    monkeypatch.setattr(gen, "_grounding_context", lambda *a, **k: ("", ""))
    monkeypatch.setattr(gen, "_save_to_database", lambda *a, **k: [])

    def fake_group(keywords, max_groups=None, output_ctx=None):
        seen["ads"] = output_ctx
        return []

    monkeypatch.setattr(gen.grouper, "group_keywords", fake_group)
    gen.generate_ads(AdsGenerateRequest(scoring_run_id=run.id))
    assert (seen["ads"].language, seen["ads"].location) == ("Rusça", "Berlin, Almanya — Şehir")
