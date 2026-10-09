"""SEO+GEO kelime bütçesi + guard'lı salt-ekleme genişletme testleri.

Run-16/17 bulgusu: temiz STOP'ta bile model 500-600 talimatına uymayıp
~410 kelime yazıyor. Çözüm iki katman: (1) ilk prompt'ta kelime bütçesi
bölümlere kırılır, (2) hâlâ kısa kalırsa TEK salt-ekleme genişletme turu —
mevcut metin korunur, denetçi regresyon guard'ı sulandırmayı engeller.
"""
import json

from app.generators.seo_geo.prompt_templates import (
    SEO_GEO_EXPANSION_PROMPT,
    SEO_GEO_GENERATION_PROMPT,
)
from app.generators.seo_geo.seo_geo_generator import SEOGEOGenerator


# ==================== ŞABLONLAR ====================

class TestTemplates:
    def test_generation_prompt_has_word_budget_block(self):
        assert "KELİME BÜTÇESİ" in SEO_GEO_GENERATION_PROMPT
        for ph in ("{intro_words}", "{section_count}",
                   "{section_words_min}", "{section_words_max}"):
            assert ph in SEO_GEO_GENERATION_PROMPT

    def test_generation_prompt_formats_with_budget(self):
        rendered = SEO_GEO_GENERATION_PROMPT.format(
            keyword="bist 100", sector="Finans", target_market="TR",
            tone="informative", word_count_min=500, word_count_max=600,
            intro_words=70, section_count=5,
            section_words_min=86, section_words_max=106,
            current_year=2026, brand_context="", link_pool_context="",
        )
        assert "TAM 5 alt başlık" in rendered
        assert "86-106 kelime" in rendered

    def test_expansion_prompt_is_additive_only(self):
        # Salt-ekleme sözleşmesi + dolgu yasağı şablonda AÇIKÇA var
        assert "DEĞİŞTİRME" in SEO_GEO_EXPANSION_PROMPT
        assert "DOLGU YASAK" in SEO_GEO_EXPANSION_PROMPT
        rendered = SEO_GEO_EXPANSION_PROMPT.format(
            keyword="bist 100", content_json="{}", current_words=410,
            words_to_add=140, word_count_min=500, word_count_max=600,
            current_year=2026,
        )
        assert "410 kelime" in rendered
        # Aşım koruması: eklenecek miktar + üst sınır uyarısı açıkça var
        assert "140 kelime ekle" in rendered
        assert "ÜZERİNE ÇIKARMA" in rendered


# ==================== _maybe_expand_once ====================

def _content(words: int, keyword: str = "bist 100"):
    kw_words = keyword.split()
    body = " ".join(kw_words + ["kelime"] * (words - len(kw_words)))
    return {
        "title": "BIST 100 Rehberi",
        "intro_paragraph": "",
        "subheadings": ["BIST 100 Nedir?"],
        "body_sections": [body],
        "word_count": words,
    }


def _expanded_json(words: int):
    return json.dumps(_content(words), ensure_ascii=False)


class CannedAI:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = 0

    def complete_json(self, prompt, max_tokens=None, response_schema=None):
        self.calls += 1
        if self.error:
            raise self.error
        return self.response


class StubChecker:
    """score'ları sırayla döndüren denetçi taklidi."""

    def __init__(self, score=0.9):
        self.score = score

    def check(self, content=None, keyword=None, **kwargs):
        return {"score": self.score, "checks": []}


def _generator(ai, seo_score=0.9, geo_score=0.9):
    gen = SEOGEOGenerator.__new__(SEOGEOGenerator)  # db'siz unit kurulum
    gen.ai_service = ai
    gen.seo_checker = StubChecker(seo_score)
    gen.geo_checker = StubChecker(geo_score)
    return gen


BASE_RESULTS = ({"score": 0.85, "checks": []}, {"score": 0.85, "checks": []})


class TestMaybeExpandOnce:
    def test_in_range_skips_ai_entirely(self):
        ai = CannedAI(error=AssertionError("AI ÇAĞRILMAMALIYDI"))
        gen = _generator(ai)
        seo, geo = BASE_RESULTS
        content = _content(520)
        out, s, g = gen._maybe_expand_once(
            "bist 100", content, seo, geo, 500, 600, link_pool=[]
        )
        assert out is content and s is seo and g is geo
        assert ai.calls == 0

    def test_short_content_expanded_and_accepted(self):
        ai = CannedAI(response=_expanded_json(550))
        gen = _generator(ai, seo_score=0.95, geo_score=0.95)
        seo, geo = BASE_RESULTS
        out, s, g = gen._maybe_expand_once(
            "bist 100", _content(410), seo, geo, 500, 600, link_pool=[]
        )
        assert ai.calls == 1
        # word_count AI beyanından değil gerçek metinden yeniden hesaplanır
        assert out["word_count"] == 550
        assert s["score"] == 0.95

    def test_parse_failure_keeps_original(self):
        ai = CannedAI(response="bozuk json {{{")
        gen = _generator(ai)
        seo, geo = BASE_RESULTS
        content = _content(410)
        out, s, g = gen._maybe_expand_once(
            "bist 100", content, seo, geo, 500, 600, link_pool=[]
        )
        assert out is content and s is seo and g is geo

    def test_not_actually_longer_keeps_original(self):
        ai = CannedAI(response=_expanded_json(405))  # kısaldı/uzamadı
        gen = _generator(ai)
        seo, geo = BASE_RESULTS
        content = _content(410)
        out, s, g = gen._maybe_expand_once(
            "bist 100", content, seo, geo, 500, 600, link_pool=[]
        )
        assert out is content

    def test_score_regression_keeps_original(self):
        # Sulandırma senaryosu: uzadı ama denetçi skoru geriledi (örn. GEO
        # 'dolgu yok' kriteri düştü) → orijinal korunur
        ai = CannedAI(response=_expanded_json(560))
        gen = _generator(ai, seo_score=0.70, geo_score=0.70)
        seo, geo = BASE_RESULTS  # 0.85 + 0.85
        content = _content(410)
        out, s, g = gen._maybe_expand_once(
            "bist 100", content, seo, geo, 500, 600, link_pool=[]
        )
        assert out is content
        assert s["score"] == 0.85  # orijinal sonuçlar korundu
