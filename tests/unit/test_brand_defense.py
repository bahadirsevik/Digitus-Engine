"""Marka savunma (own-brand bypass) birim testleri.

Senaryo kaynagi: Run #8'de "vepa tarak" ADS skorunda ust siralardayken
navigational olarak siniflanip tum kanallardan elenmisti.
"""
import pytest

from app.core.channel.brand_defense import (
    build_brand_defense_context,
    is_own_brand_keyword,
)

VEPA_TERMS = ["Vepa", "Vepa Fırça", "Vepa diş fırçası", "Vepa saç fırçası", "Vepa tarak"]
VEPA_COMPANY = "Vepa Fırça"


@pytest.fixture
def vepa_ctx():
    return build_brand_defense_context(VEPA_TERMS, VEPA_COMPANY)


# ── Baglam kurulumu ──────────────────────────────────────────────

def test_context_none_when_no_terms():
    assert build_brand_defense_context([], None) is None
    assert build_brand_defense_context(None, None) is None
    assert build_brand_defense_context(["   ", ""], None) is None


def test_core_tokens_derived_from_common_token(vepa_ctx):
    # 5 terimin hepsinde gecen tek token: "vepa"
    assert vepa_ctx.core_tokens == frozenset({"vepa"})


def test_original_terms_include_company_name(vepa_ctx):
    assert "Vepa Fırça" in vepa_ctx.original_terms


# ── ADS eslesmeleri ──────────────────────────────────────────────

def test_own_brand_product_passes_ads(vepa_ctx):
    assert is_own_brand_keyword("vepa tarak", vepa_ctx, "ADS")
    assert is_own_brand_keyword("vepa saç fırçası", vepa_ctx, "ADS")


def test_bare_brand_passes_ads(vepa_ctx):
    assert is_own_brand_keyword("vepa", vepa_ctx, "ADS")


def test_turkish_ascii_variants_match(vepa_ctx):
    # Normalizasyon: "fırçası" ile "fircasi" ayni token'a iner
    assert is_own_brand_keyword("vepa dis fircasi", vepa_ctx, "ADS")
    assert is_own_brand_keyword("VEPA TARAK", vepa_ctx, "ADS")


def test_generic_token_trap_does_not_match(vepa_ctx):
    # "Vepa Fırça" teriminin jenerik "fırça" token'i tek basina kanit degil:
    # terimin TUM token'lari gerekli, "vepa" yoksa eslesme yok.
    assert not is_own_brand_keyword("doğal fırça", vepa_ctx, "ADS")
    assert not is_own_brand_keyword("diş fırçası modelleri", vepa_ctx, "ADS")


def test_other_brands_do_not_match(vepa_ctx):
    assert not is_own_brand_keyword("sensodyne diş fırçası", vepa_ctx, "ADS")
    assert not is_own_brand_keyword("dyson saç açma tarağı", vepa_ctx, "ADS")


# ── Negatif sinyal guard'lari ────────────────────────────────────

@pytest.mark.parametrize("kw", [
    "vepa şikayet",
    "vepa iş ilanı",
    "vepa iş başvurusu",
    "vepa ikinci el",
    "vepa 2. el tarak",
    "vepa tarak tamir",
])
def test_negative_own_brand_not_bypassed_any_channel(vepa_ctx, kw):
    for channel in ("ADS", "SEO", "SOCIAL"):
        assert not is_own_brand_keyword(kw, vepa_ctx, channel), f"{kw} / {channel}"


@pytest.mark.parametrize("kw", ["vepa tarak nedir", "vepa fırça nasıl kullanılır", "vepa katalog pdf"])
def test_informational_blocked_for_ads_only(vepa_ctx, kw):
    # ADS: bilgi amacli kendi-marka sorgusu reklam butcesi harcamamali
    assert not is_own_brand_keyword(kw, vepa_ctx, "ADS")
    # SEO: ayni sorgu mesru icerik konusudur (dar negatif liste)
    assert is_own_brand_keyword(kw, vepa_ctx, "SEO")


# ── SEO/SOCIAL kanal ayrimi ─────────────────────────────────────

def test_bare_brand_fails_seo_social(vepa_ctx):
    for kw in ("vepa",):
        assert not is_own_brand_keyword(kw, vepa_ctx, "SEO")
        assert not is_own_brand_keyword(kw, vepa_ctx, "SOCIAL")


def test_brand_plus_product_passes_seo_social(vepa_ctx):
    # "vepa tarak" brand_terms icinde birebir olsa da cekirdek ("vepa")
    # disinda urun token'i tasidigi icin SEO/SOCIAL'a girebilmeli
    assert is_own_brand_keyword("vepa tarak", vepa_ctx, "SEO")
    assert is_own_brand_keyword("vepa tarak", vepa_ctx, "SOCIAL")
    assert is_own_brand_keyword("vepa saç fırçası", vepa_ctx, "SEO")


def test_single_product_term_profile_still_behaves():
    # Codex senaryosu: brand_terms yalnizca ["Vepa tarak"] olsa bile
    # cekirdek ilk token'dan ("vepa") turetilir ve davranis korunur.
    ctx = build_brand_defense_context(["Vepa tarak"], None)
    assert ctx is not None
    assert ctx.core_tokens == frozenset({"vepa"})
    assert is_own_brand_keyword("vepa tarak", ctx, "ADS")
    assert is_own_brand_keyword("vepa tarak", ctx, "SEO")       # urun token'i var
    # Muhafazakar davranis: bypass yalnizca profilin ACIKCA bildirdigi
    # terimlerle calisir; cekirdek token tek basina eslesme kaniti degildir.
    # Ciplak "vepa" icin profile "Vepa" terimi eklenmelidir.
    assert not is_own_brand_keyword("vepa", ctx, "ADS")
    assert not is_own_brand_keyword("vepa", ctx, "SEO")
    assert not is_own_brand_keyword("tarak", ctx, "ADS")        # tek basina urun != marka


def test_short_brand_token_works():
    # 2 karakterli gercek markalar (HP, LG) olmemeli
    ctx = build_brand_defense_context(["HP yazıcı", "HP"], "HP")
    assert is_own_brand_keyword("hp yazıcı", ctx, "ADS")
    assert is_own_brand_keyword("hp yazıcı kartuş", ctx, "SEO")
    assert not is_own_brand_keyword("yazıcı", ctx, "ADS")


def test_none_ctx_is_noop():
    assert not is_own_brand_keyword("vepa tarak", None, "ADS")


# ── Intent kapisi override'i (_process_intent_result) ───────────

def _process(own_brand: bool, keyword: str, intent="navigational", channel="ADS"):
    from app.core.channel.intent_analyzer import IntentAnalyzer

    analyzer = IntentAnalyzer(db=None, ai_service=None)
    return analyzer._process_intent_result(
        intent_result={"intent_type": intent, "confidence": 0.95, "reasoning": "test"},
        accepted_intents=["transactional", "commercial"],
        channel=channel,
        keyword_text=keyword,
        own_brand=own_brand,
    )


def test_intent_override_passes_own_brand_navigational():
    res = _process(own_brand=True, keyword="vepa tarak")
    assert res["is_passed"] is True
    assert "[brand_defense]" in res["reasoning"]
    assert res["intent_type"] == "navigational"  # etiket korunur


def test_intent_override_does_not_beat_hard_negatives():
    # Guard bir sekilde asilsa bile mevcut hard-negative katmani son soz sahibi
    res = _process(own_brand=True, keyword="vepa ikinci el tarak")
    assert res["is_passed"] is False


def test_intent_no_override_without_own_brand():
    res = _process(own_brand=False, keyword="vepa tarak")
    assert res["is_passed"] is False
