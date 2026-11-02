"""Cikti dili + hedef konum blogu (plan_pazar_dil_secimi.md Faz 2-a/b/c).

Her uretim prompt yolu, calisma pazarindan (TR/tr ve Berlin/ru) turetilen ortak
blogu tasir; Turkce de ayni tek yoldan gecer. Sablonlar degismez: blok render
edilmis prompt'un SONUNA eklenir, JSON sozlesmesi/karakter limitleri ayni kalir.
Tum AI cagrilari sahte — UCRETLI CAGRI YOK.
"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.generators.output_language import (
    DEFAULT_CONTEXT,
    OUTPUT_BLOCK_TEMPLATE,
    OutputContext,
    output_context,
    output_context_for_run,
    with_output_block,
)

# Sosyal brief yardimcilari (mevcut test dosyalarindan; rootdir import modu)
sys.path.insert(0, str(Path(__file__).parent))

TR_WS = SimpleNamespace(default_geo_target_id="2792", default_language_id="1037")
BERLIN_RU_WS = SimpleNamespace(default_geo_target_id="1003854", default_language_id="1031")
CTXS = [output_context(TR_WS), output_context(BERLIN_RU_WS)]
CTX_IDS = ["tr-TR", "berlin-ru"]


class CapAI:
    """Prompt'lari yakalar; icerigi bilerek bozuk dondurur (fallback yollari calisir)."""

    def __init__(self, response="{}"):
        self.prompts = []
        self.response = response

    def complete_json(self, prompt, **kwargs):
        self.prompts.append(prompt)
        return self.response

    def complete(self, prompt, **kwargs):
        self.prompts.append(prompt)
        return "kisa metin"


def assert_block(prompt: str, ctx: OutputContext):
    assert prompt.rstrip().endswith(ctx.block()), prompt[-400:]
    assert f"ÇIKTI DİLİ: {ctx.language}" in prompt
    assert f"HEDEF KONUM: {ctx.location}" in prompt


# ==================== BAGLAM TURETME ====================

def test_context_from_workspace():
    tr, ru = output_context(TR_WS), output_context(BERLIN_RU_WS)
    assert (tr.language, tr.location) == ("Türkçe", "Türkiye (TR) — Ülke")
    assert (ru.language, ru.location) == ("Rusça", "Berlin, Almanya — Şehir")


def test_context_defaults_without_workspace():
    assert output_context(None) == DEFAULT_CONTEXT
    assert (DEFAULT_CONTEXT.language, DEFAULT_CONTEXT.location) == ("Türkçe", "Seçilmedi")
    empty = SimpleNamespace(default_geo_target_id=None, default_language_id=None)
    assert output_context(empty) == DEFAULT_CONTEXT
    assert output_context_for_run(None, None) == DEFAULT_CONTEXT


def test_block_keeps_template_and_appends_after_prompt():
    base = 'Gorev... {"headlines": []} ... 30 karakter, 90 karakter'
    out = with_output_block(base, CTXS[1])
    assert out.startswith(base + "\n\n")
    assert out.endswith(CTXS[1].block())
    assert OUTPUT_BLOCK_TEMPLATE.format(
        output_language="Rusça", target_location="Berlin, Almanya — Şehir"
    ) in out
    assert "JSON anahtarları, enum değerleri ve ID'ler değişmez" in out
    assert with_output_block(base).endswith(DEFAULT_CONTEXT.block())


def test_context_for_run_reads_workspace(db_session, make_workspace, make_scoring_run):
    ws = make_workspace("Berlin Ru", default_geo_target_id="1003854", default_language_id="1031")
    run = make_scoring_run(brand_profile_id=ws.id)
    ctx = output_context_for_run(db_session, run.id)
    assert (ctx.language, ctx.location) == ("Rusça", "Berlin, Almanya — Şehir")
    assert output_context_for_run(db_session, run) == ctx
    assert output_context_for_run(db_session, 987654321) == DEFAULT_CONTEXT  # yok -> varsayilan


# ==================== ADS ====================

@pytest.mark.parametrize("ctx", CTXS, ids=CTX_IDS)
def test_keyword_grouping_primary_and_strict_retry(ctx):
    from app.generators.ads.keyword_grouper import KeywordGrouper
    from app.schemas.ads import KeywordForGrouping

    ai = CapAI()
    kws = [KeywordForGrouping(id=i, keyword=f"kelime {i}") for i in range(1, 31)]
    KeywordGrouper(ai).group_keywords(kws, output_ctx=ctx)
    assert len(ai.prompts) >= 2  # birincil + sıkı yeniden deneme
    for prompt in ai.prompts:
        assert_block(prompt, ctx)


@pytest.mark.parametrize("ctx", CTXS, ids=CTX_IDS)
def test_rsa_candidates_prompt_keeps_contract_and_has_block(ctx):
    from app.generators.ads.prompt_templates import ADS_RSA_CANDIDATES_PROMPT
    from app.generators.ads.rsa_generator import RSAGenerator
    from app.schemas.ads import KeywordGroupSchema

    group = KeywordGroupSchema(name="G", theme="t", keyword_ids=[1, 2], keywords=["a b", "c d"])
    gen = RSAGenerator(ai_service=CapAI(), enable_ai_regeneration=False)
    gen._output_ctx = ctx
    prompt = gen._build_candidates_prompt(group, "Marka", "—")
    assert_block(prompt, ctx)
    # sablon govdesi (JSON sozlesmesi + limitler) blogun ONCESINDE bayt-ayni
    assert "30 karakter" in prompt and "90 karakter" in prompt
    assert prompt.index('"headlines"') < prompt.index("ÇIKTI DİLİ")
    assert ADS_RSA_CANDIDATES_PROMPT.count("{{") >= 1  # kacis dili bozulmadi


@pytest.mark.parametrize("ctx", CTXS, ids=CTX_IDS)
def test_rsa_generate_passes_context_to_claim_rewrite_and_validators(ctx):
    from app.generators.ads.rsa_generator import RSAGenerator

    ai = CapAI()
    gen = RSAGenerator(ai_service=ai, enable_ai_regeneration=True)
    gen._output_ctx = ctx
    gen._grounding_facts = ""
    gen._rewrite_claim_asset("100.000+ Müşteri", ["100.000+"], "headline", "kw", 30)
    assert_block(ai.prompts[-1], ctx)

    long_headline = "Bu baslik otuz karakterden cok daha uzun bir baslik"
    gen.headline_validator.validate(long_headline, "kw", ai_service=ai, output_ctx=ctx)
    assert_block(ai.prompts[-1], ctx)
    long_desc = "Bu aciklama doksan karakteri kesinlikle asan, hic bitmeyen ve cok uzun bir aciklama metnidir ki sigmaz"
    gen.description_validator.validate(long_desc, ai_service=ai, output_ctx=ctx)
    assert_block(ai.prompts[-1], ctx)


_EMPTY_RSA = {"headlines": [], "descriptions": [], "negative_keywords": [], "keyword_headlines": []}


def _fallback_texts(ctx):
    from app.generators.ads.rsa_generator import RSAGenerator
    from app.schemas.ads import KeywordGroupSchema

    group = KeywordGroupSchema(name="G", theme="t", keyword_ids=[1, 2], keywords=["hair transplant", "hair clinic"])
    gen = RSAGenerator(
        ai_service=CapAI(json.dumps(_EMPTY_RSA)), enable_ai_regeneration=False, enable_fallback=True
    )
    result = gen.generate_rsa(group, brand_name="Acme", brand_usp="", output_ctx=ctx)
    return [h.text for h in result.headlines] + [d.text for d in result.descriptions]


def test_deterministic_fallback_is_turkish_only_for_turkish_workspace():
    tr_texts = " | ".join(_fallback_texts(CTXS[0]))
    ru_texts = " | ".join(_fallback_texts(CTXS[1]))
    assert "Hemen İncele" in tr_texts or "Güvenli alışveriş" in tr_texts
    for turkish in ("Hemen", "Güven", "Kaliteli", "Ürün", "keşfedin", "inceleyin"):
        assert turkish not in ru_texts
    assert "hair transplant" in ru_texts.lower()  # marka + keyword dil-bagimsiz kalir


# ==================== SEO / GEO ====================

@pytest.mark.parametrize("ctx", CTXS, ids=CTX_IDS)
def test_geo_compliance_prompt(ctx):
    from app.compliance.geo_checker import GEOComplianceChecker

    ai = CapAI(json.dumps({"criteria": []}))
    GEOComplianceChecker(ai).check(
        {"title": "T", "intro_paragraph": "i", "subheadings": [], "body_sections": []},
        "kelime", output_ctx=ctx,
    )
    assert_block(ai.prompts[0], ctx)


@pytest.mark.parametrize("ctx", CTXS, ids=CTX_IDS)
def test_seo_generation_revision_expansion_prompts(ctx):
    from app.generators.seo_geo.seo_geo_generator import SEOGEOGenerator

    ai = CapAI(json.dumps({
        "title": "T", "intro_paragraph": "i", "subheadings": ["h"],
        "body_sections": [{"heading": "h", "content": "c"}],
    }))
    gen = SEOGEOGenerator(db=None, ai_service=ai)
    gen._output_ctx = ctx
    gen._generate_raw_content(
        keyword="kelime", sector="s", target_market="m", tone="t",
        word_count_min=500, word_count_max=600,
    )
    assert_block(ai.prompts[-1], ctx)

    content = {"title": "T", "word_count": 100, "intro_paragraph": "i",
               "subheadings": ["h"], "body_sections": []}
    seo = {"score": 1, "improvement_notes": "", "criteria": {}}
    geo = {"score": 1, "improvement_notes": "", "criteria": []}
    n_before = len(ai.prompts)
    gen._maybe_expand_once("kelime", content, seo, geo, 500, 600, [])
    assert len(ai.prompts) > n_before
    assert_block(ai.prompts[n_before], ctx)
    # revizyon yolu: basarisiz kriter olmadan calismaz; sablon yolu expansion ile ayni sarmalayici


# ==================== LEGACY SOSYAL ====================

def _legacy_idea():
    from app.schemas.social import ContentFormatEnum, PlatformEnum, SocialIdeaSchema

    return SocialIdeaSchema(
        id=7, category_id=1, idea_title="Fikir", idea_description="d",
        target_platform=PlatformEnum.INSTAGRAM, content_format=ContentFormatEnum.POST,
        trend_alignment=0.5,
    )


def _legacy_category():
    from app.schemas.social import CategoryTypeEnum, SocialCategorySchema

    return SocialCategorySchema(
        category_name="Kat", category_type=list(CategoryTypeEnum)[0],
        description="d", relevance_score=0.5,
    )


def _legacy_content():
    from app.schemas.social import HookSchema, HookStyleEnum, SocialContentSchema

    return SocialContentSchema(
        idea_id=7, hooks=[HookSchema(text="Merak?", style=HookStyleEnum.QUESTION)],
        caption="c", cta_text="Kaydet", hashtags=["a", "b", "c", "d", "e"],
    )


@pytest.mark.parametrize("ctx", CTXS, ids=CTX_IDS)
def test_legacy_social_all_prompts(ctx):
    from app.generators.social.category_generator import CategoryGenerator
    from app.generators.social.content_generator import ContentGenerator
    from app.generators.social.idea_generator import IdeaGenerator

    ai = CapAI()
    CategoryGenerator(ai).generate([{"keyword": "k", "id": 1}], "Marka", output_ctx=ctx)
    IdeaGenerator(ai).generate(_legacy_category(), "Marka", output_ctx=ctx)
    IdeaGenerator(ai).regenerate(_legacy_idea(), _legacy_category(), "Marka", output_ctx=ctx)
    ContentGenerator(ai).generate(_legacy_idea(), "Marka", output_ctx=ctx)
    ContentGenerator(ai).regenerate(_legacy_content(), _legacy_idea(), "Marka", output_ctx=ctx)
    assert len(ai.prompts) >= 5
    for prompt in ai.prompts:
        assert_block(prompt, ctx)


# ==================== SOSYAL BRIEF ====================

@pytest.mark.parametrize("ctx", CTXS, ids=CTX_IDS)
def test_brief_category_idea_content_repair_prompts(ctx):
    from test_social_brief_category_generator_f1e3 import _make_start
    from test_social_brief_content_generator_f1g2 import _make_prompt_input as content_input
    from test_social_brief_idea_generator_f1f3 import _make_prompt_input as idea_input
    from test_social_content_repair_f1g4 import (
        _make_grounding_context,
        _make_prompt_input as repair_input,
        _make_video_content,
    )

    from app.generators.social.brief_category_generator import (
        SocialBriefCategoryGenerator, SocialCategoryGenerationError,
    )
    from app.generators.social.brief_content_generator import (
        SocialBriefContentGenerator, SocialContentGenerationError,
    )
    from app.generators.social.brief_idea_generator import (
        SocialBriefIdeaGenerator, SocialIdeaGenerationError,
    )

    ai = CapAI("not json")
    with pytest.raises(SocialCategoryGenerationError):
        SocialBriefCategoryGenerator(ai, output_ctx=ctx).generate(_make_start())
    with pytest.raises(SocialIdeaGenerationError):
        SocialBriefIdeaGenerator(ai, output_ctx=ctx).generate(idea_input())
    with pytest.raises(SocialContentGenerationError):
        SocialBriefContentGenerator(ai, output_ctx=ctx).generate(content_input())
    n_before = len(ai.prompts)
    rinp = repair_input()
    with pytest.raises(SocialContentGenerationError):
        SocialBriefContentGenerator(ai, output_ctx=ctx).repair_once(
            rinp,
            _make_video_content(duration_status="mismatch", actual_duration_sec=45),
            _make_grounding_context(rinp),
        )
    assert len(ai.prompts) > n_before  # repair prompt'u da cagrildi
    # kategori + fikir (+retry) + icerik + repair: HER prompt blok tasir; retry duzeltmesi de
    for prompt in ai.prompts:
        assert prompt.count("ÇIKTI DİLİ:") == 1, prompt[-300:]
        assert f"ÇIKTI DİLİ: {ctx.language}" in prompt
        assert f"HEDEF KONUM: {ctx.location}" in prompt


def test_brief_generators_without_context_use_turkish_default():
    from test_social_brief_idea_generator_f1f3 import _make_prompt_input as idea_input

    from app.generators.social.brief_idea_generator import (
        SocialBriefIdeaGenerator, SocialIdeaGenerationError,
    )

    ai = CapAI("not json")
    with pytest.raises(SocialIdeaGenerationError):
        SocialBriefIdeaGenerator(ai).generate(idea_input())
    assert_block(ai.prompts[0], DEFAULT_CONTEXT)


# ==================== PROFIL ONERI KELIMELERI + RAKIP KESFI ====================

@pytest.mark.parametrize("ctx", CTXS, ids=CTX_IDS)
def test_keyword_suggestion_prompts_carry_language_and_location(ctx):
    from app.core.site_analyzer.profile_extractor import ProfileExtractor

    ai = CapAI(json.dumps({"suggested_keywords": ["a"]}))
    ex = ProfileExtractor(ai)
    ex.suggest_keywords("site", output_ctx=ctx)
    ex.suggest_keywords_from_profile("site", {"company_name": "X"}, output_ctx=ctx)
    assert len(ai.prompts) == 2
    for prompt in ai.prompts:
        assert f"Keyword'ler {ctx.language}; hedef konum {ctx.location}." in prompt
        assert "adres/şehir uydurma" in prompt
        assert "Keyword'ler Türkçe, kısa" not in prompt  # eski sabit cümle gitti
        assert '"suggested_keywords"' in prompt  # JSON sozlesmesi ayni


def test_keyword_suggestion_default_without_context():
    from app.core.site_analyzer.profile_extractor import ProfileExtractor

    ai = CapAI(json.dumps({"suggested_keywords": []}))
    ProfileExtractor(ai).suggest_keywords("site")
    assert "Keyword'ler Türkçe; hedef konum Seçilmedi." in ai.prompts[0]


def test_competitor_prompt_uses_target_location_and_keeps_turkish_rationale():
    from app.core.policy.competitor_discovery import _profile_prompt

    prompt = _profile_prompt(BERLIN_RU_WS_FULL())
    assert "Berlin, Almanya — Şehir pazarındaki" in prompt
    assert "Türkiye pazarındaki" not in prompt
    assert "rationale alanını TÜRKÇE yaz" in prompt
    tr = _profile_prompt(SimpleNamespace(
        default_geo_target_id="2792", default_language_id="1037", **_PROFILE_FIELDS))
    assert "Türkiye (TR) — Ülke pazarındaki" in tr
    legacy = _profile_prompt(SimpleNamespace(
        default_geo_target_id=None, default_language_id=None, **_PROFILE_FIELDS))
    assert "Türkiye pazarındaki" in legacy  # pazarsiz eski satir: onceki davranis


_PROFILE_FIELDS = dict(
    id=1, name="Acme", company_url="https://acme.test", profile_data={"company_name": "Acme"},
    preliminary_info=None, excluded_info=None, competitor_urls=None, competitor_terms=None,
    crawl_scope="site",
)


def BERLIN_RU_WS_FULL():
    return SimpleNamespace(default_geo_target_id="1003854", default_language_id="1031",
                           **_PROFILE_FIELDS)


# ==================== PAKET B: unvan + pazar kuralları (B1/B5) ====================

def test_turkish_default_block_is_byte_identical():
    expected = (
        "ÇIKTI DİLİ: Türkçe. HEDEF KONUM: Seçilmedi "
        "(işletmenin adresi değil; adres/şehir uydurma). İstisnalar: marka/ürün adları, "
        "URL'ler, tıbbi/teknik terimler, yerleşik yabancı kelimeler. "
        "JSON anahtarları, enum değerleri ve ID'ler değişmez."
    )
    assert DEFAULT_CONTEXT.block() == expected
    tr = output_context(TR_WS)
    assert tr.block() == expected.replace("Seçilmedi", "Türkiye (TR) — Ülke")


def test_foreign_block_adds_title_and_market_rules():
    from app.generators.output_language import MARKET_RULE, TITLE_RULE

    ru = CTXS[1].block()
    assert TITLE_RULE in ru and MARKET_RULE in ru
    assert "Uzm. Dr., Dt., Op. Dr." in ru and "ödeme sistemi" in ru
    # Türkçe dil + yabancı pazar: yalnız pazar kuralı
    tr_abroad = OutputContext(language="Türkçe", location="Berlin, Almanya — Şehir").block()
    assert MARKET_RULE in tr_abroad and TITLE_RULE not in tr_abroad


def test_turkish_city_with_turkish_language_block_is_byte_identical_to_default_country_block():
    """QA F2: 'Istanbul, Türkiye — Şehir' + Türkçe → ek kural YOK (TR blok bayt-aynı)."""
    from app.generators.output_language import MARKET_RULE, OUTPUT_BLOCK_TEMPLATE

    for label in ("Istanbul, Türkiye — Şehir", "Ankara, Türkiye — İl/Bölge", "Türkiye (TR) — Ülke"):
        block = OutputContext(language="Türkçe", location=label).block()
        assert block == OUTPUT_BLOCK_TEMPLATE.format(output_language="Türkçe", target_location=label)
        assert MARKET_RULE not in block


def test_foreign_city_with_turkish_language_gets_market_rule_only():
    from app.generators.output_language import MARKET_RULE, TITLE_RULE

    block = OutputContext(language="Türkçe", location="Berlin, Almanya — Şehir").block()
    assert MARKET_RULE in block and TITLE_RULE not in block


def test_russian_with_turkish_city_still_gets_both_rules():
    from app.generators.output_language import MARKET_RULE, TITLE_RULE

    block = OutputContext(language="Rusça", location="Istanbul, Türkiye — Şehir").block()
    assert TITLE_RULE in block and MARKET_RULE in block
