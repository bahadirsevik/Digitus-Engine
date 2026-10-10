"""İçerik grounding testleri (plan Faz F).

Kapsam:
1. find_ungrounded_claims deterministik validator — sayı normalizasyon
   varyantları, süperlatif/garanti, keyword muafiyeti (yıl false-positive),
   grounding eşleşmeleri.
2. Şablon temizliği — RSA/SOCIAL prompt'larında sayısal/sosyal-kanıt iddiası
   YOK, İDDİA KURALLARI VAR, {product_facts} boş-profil yolunda format'lanıyor.
3. RSA canned-response zinciri — AI yasak iddia döndürünce validator yakalar
   → CLAIM_REWRITE (1 kez) → hâlâ ihlalliyse asset elenir; fallback asset'leri
   de taranır (grup minimum altına düşerse FAILED).
4. SOCIAL _ground_or_reject — ihlalde 1 regenerate; ikinci ihlalde içerik
   reddedilir (warning döner, kayıt yok).
"""
import json

import pytest

from app.generators.ads.prompt_templates import (
    ADS_RSA_GENERATION_PROMPT,
    CLAIM_REWRITE_PROMPT,
)
from app.generators.ads.rsa_generator import RSAGenerator
from app.generators.ads.validators import find_ungrounded_claims
from app.generators.social.prompt_templates import (
    CONTENT_REGENERATE_PROMPT,
    SOCIAL_CONTENT_PROMPT,
)
from app.schemas.ads import KeywordGroupSchema
from app.schemas.social import (
    HookSchema,
    HookStyleEnum,
    SocialContentSchema,
)


# ==================== 1. VALIDATOR ====================

class TestFindUngroundedClaims:
    def test_numeric_variants_caught(self):
        assert find_ungrounded_claims("100.000+ Müşteri", "")
        assert find_ungrounded_claims("50 bin kullanıcı bize güveniyor", "")
        assert find_ungrounded_claims("yüzde 80 indirim", "")
        assert find_ungrounded_claims("%80 memnuniyet", "")
        assert find_ungrounded_claims("4.8 puan aldık", "")
        assert find_ungrounded_claims("4.8/5 puan", "")
        assert find_ungrounded_claims("10 yıldır hizmetinizdeyiz", "")

    def test_superlatives_and_guarantee_caught(self):
        assert find_ungrounded_claims("Türkiye'nin en iyi platformu", "")
        assert find_ungrounded_claims("lider aracı kurum", "")
        assert find_ungrounded_claims("1 numara seçim", "")
        assert find_ungrounded_claims("kazanç garantisi sunuyoruz", "")

    def test_guarantee_banned_even_if_in_facts(self):
        # Kazanç garantisi HER DURUMDA yasak — grounding'e bakılmaz
        assert find_ungrounded_claims("kazanç garantisi", "kazanç garantisi")

    def test_grounded_claims_pass(self):
        facts = "Firma: X\n10 yıl deneyim\nmüşteri memnuniyeti yüzde 80\n4.8 puan"
        assert find_ungrounded_claims("10 yıldır hizmet", facts) == []
        assert find_ungrounded_claims("%80 memnuniyet", facts) == []
        assert find_ungrounded_claims("4.8/5 puan", facts) == []

    def test_number_normalization_cross_format(self):
        # Fact "50 bin" ↔ iddia "50.000" aynı sayı
        assert find_ungrounded_claims("50.000 kullanıcı", "50 bin kullanıcı") == []
        # "100.000+" fact'te "100.000 müşteri" varsa sayı bazında ground'lanır
        assert find_ungrounded_claims("100.000+ müşteri", "100.000 müşteri") == []

    def test_keyword_year_exemption(self):
        # Keyword'deki doğal yıl sayısı claim sayılmaz (dar muafiyet)
        assert (
            find_ungrounded_claims(
                "en iyi hisse 2026 önerileri", "", ["en iyi hisse 2026"]
            )
            == []
        )
        # Aynı sayı keyword DIŞI bağlamda muaf DEĞİL
        assert find_ungrounded_claims(
            "2026 yıl boyunca kazandırdı", "", ["en iyi hisse"]
        )

    def test_neutral_text_clean(self):
        assert find_ungrounded_claims("Verileri Tek Ekranda İncele", "") == []
        assert find_ungrounded_claims("Analiz Araçlarını Keşfet", "") == []
        assert find_ungrounded_claims("", "") == []

    def test_pagination_is_not_a_rating_claim(self):
        # idea-44 dersi: thread/carousel sayfalama numaraları iddia değil —
        # rating deseni bunları yakalayıp içeriği ret döngüsüne sokuyordu
        assert find_ungrounded_claims("1/8: Borsada portföy kurmanın adımları", "") == []
        assert find_ungrounded_claims("2/8 Hisse seçimi nasıl yapılır", "") == []
        assert find_ungrounded_claims("Slayt 3/10: Özet", "") == []
        # Gerçek rating biçimleri hâlâ yakalanıyor
        assert find_ungrounded_claims("4.8/5", "")  # ondalıklı pay
        assert find_ungrounded_claims("9/10 puan aldık", "")  # birimli


# ==================== 2. ŞABLON TEMİZLİĞİ ====================

class TestPromptTemplates:
    def test_rsa_prompt_has_no_fabricated_claims(self):
        assert "100.000+" not in ADS_RSA_GENERATION_PROMPT
        assert "4.8/5" not in ADS_RSA_GENERATION_PROMPT
        assert "Türkiye'nin en geniş" not in ADS_RSA_GENERATION_PROMPT
        assert "10 yıldır" not in ADS_RSA_GENERATION_PROMPT

    def test_rsa_prompt_has_claim_rules_and_product_facts(self):
        assert "İDDİA KURALLARI" in ADS_RSA_GENERATION_PROMPT
        assert "{product_facts}" in ADS_RSA_GENERATION_PROMPT

    def test_rsa_prompt_formats_with_empty_profile(self):
        rendered = ADS_RSA_GENERATION_PROMPT.format(
            group_name="G", group_theme="T", keywords="a, b",
            brand_name="Marka", brand_usp="—", product_facts="—",
        )
        assert "Ürün Bilgisi: —" in rendered

    def test_social_prompt_cleaned_and_grounded(self):
        assert "%80'i bu hatayı yapıyor" not in SOCIAL_CONTENT_PROMPT
        assert "%80'i aynı hatayı yapıyor" not in SOCIAL_CONTENT_PROMPT
        assert "İDDİA KURALI" in SOCIAL_CONTENT_PROMPT
        assert "{product_facts}" in SOCIAL_CONTENT_PROMPT

    def test_social_regenerate_prompt_has_rule(self):
        assert "İDDİA KURALI" in CONTENT_REGENERATE_PROMPT
        assert "{product_facts}" in CONTENT_REGENERATE_PROMPT

    def test_claim_rewrite_prompt_formats(self):
        rendered = CLAIM_REWRITE_PROMPT.format(
            asset_type="headline", original="100.000+ Müşteri",
            claims="- 100.000+", grounding_facts="—", keyword="hisse",
            max_length=30,
        )
        assert "100.000+ Müşteri" in rendered


# ==================== 3. RSA CANNED-RESPONSE ZİNCİRİ ====================

def _rsa_json(headlines, descriptions=None):
    return json.dumps({
        "headlines": [
            {"text": t, "type": "benefit", "position": "any"} for t in headlines
        ],
        "descriptions": [
            {"text": t, "type": "value_prop", "position": "any"}
            for t in (descriptions or [
                "Hisse verilerini tek ekranda inceleyin.",
                "Analiz araçlarını hemen keşfedin.",
            ])
        ],
        "negative_keywords": [
            {"keyword": f"neg{i}", "match_type": "phrase",
             "category": "bilgi_amacli", "reason": "r"}
            for i in range(10)
        ],
    })


class CannedAI:
    """complete_json sabit yanıt; complete (rewrite) yapılandırılabilir."""

    def __init__(self, json_response: str, rewrite_response: str = ""):
        self.json_response = json_response
        self.rewrite_response = rewrite_response
        self.rewrite_prompts = []

    def complete_json(self, prompt, max_tokens=None, response_schema=None):
        return self.json_response

    def complete(self, prompt, max_tokens=None):
        self.rewrite_prompts.append(prompt)
        return self.rewrite_response


GROUP = KeywordGroupSchema(
    name="Hisse Analiz", theme="analiz",
    keyword_ids=[1, 2], keywords=["hisse analiz", "hisse takip"],
)


class TestRsaGroundingChain:
    def test_violating_headline_rewritten_when_rewrite_clean(self):
        ai = CannedAI(
            _rsa_json(["100.000+ Müşteri", "Hisse Analiz Araçları",
                       "Hemen Keşfet", "Verileri İncele"]),
            rewrite_response="Güvenilir Analiz Deneyimi",
        )
        gen = RSAGenerator(ai_service=ai, enable_ai_regeneration=True,
                           enable_fallback=False)
        result = gen.generate_rsa(GROUP, brand_name="Marka", brand_usp="",
                                  grounding_facts="")
        texts = [h.text for h in result.headlines]
        assert "100.000+ Müşteri" not in texts
        assert "Güvenilir Analiz Deneyimi" in texts
        assert len(ai.rewrite_prompts) == 1

    def test_violating_headline_eliminated_when_rewrite_still_violates(self):
        ai = CannedAI(
            _rsa_json(["100.000+ Müşteri", "Hisse Analiz Araçları",
                       "Hemen Keşfet", "Verileri İncele"]),
            rewrite_response="50 bin kullanıcı bize güveniyor",
        )
        gen = RSAGenerator(ai_service=ai, enable_ai_regeneration=True,
                           enable_fallback=False)
        result = gen.generate_rsa(GROUP, brand_name="Marka", brand_usp="",
                                  grounding_facts="")
        texts = [h.text for h in result.headlines]
        assert "100.000+ Müşteri" not in texts
        assert all("50 bin" not in t for t in texts)
        assert len(texts) == 3  # ihlalli asset elendi, kalanlar yeterli

    def test_grounded_claim_survives(self):
        ai = CannedAI(
            _rsa_json(["10 Yıllık Deneyim", "Hisse Analiz Araçları",
                       "Hemen Keşfet"]),
        )
        gen = RSAGenerator(ai_service=ai, enable_ai_regeneration=True,
                           enable_fallback=False)
        result = gen.generate_rsa(
            GROUP, brand_name="Marka", brand_usp="10 yıllık deneyim",
            grounding_facts="10 yıllık deneyim",
        )
        assert "10 Yıllık Deneyim" in [h.text for h in result.headlines]
        assert ai.rewrite_prompts == []

    def test_rewrite_preserves_keyword_exemption(self):
        # codex bulgusu: rewrite sonrası ikinci kontrolde keyword muafiyeti
        # kaybolmamalı — keyword'deki doğal "2026 yıl" sayısı rewrite'ı
        # yanlışlıkla eletmemeli
        group_year = KeywordGroupSchema(
            name="Yıl Sonu", theme="analiz",
            keyword_ids=[1], keywords=["2026 yıl sonu hisse"],
        )
        ai = CannedAI(
            json.dumps({
                "headlines": [
                    {"text": "100.000+ Müşteri", "type": "trust", "position": "any"},
                    {"text": "Hisse Analiz Araçları", "type": "keyword", "position": "any"},
                    {"text": "Hemen Keşfet", "type": "cta", "position": "any"},
                    {"text": "Verileri İncele", "type": "benefit", "position": "any"},
                ],
                "descriptions": [
                    {"text": "Hisse verilerini tek ekranda inceleyin.",
                     "type": "value_prop", "position": "any"},
                    {"text": "Analiz araçlarını hemen keşfedin.",
                     "type": "cta", "position": "any"},
                ],
                "negative_keywords": [
                    {"keyword": f"neg{i}", "match_type": "phrase",
                     "category": "bilgi_amacli", "reason": "r"}
                    for i in range(10)
                ],
            }),
            rewrite_response="2026 yıl sonu hisse rehberi",
        )
        gen = RSAGenerator(ai_service=ai, enable_ai_regeneration=True,
                           enable_fallback=False)
        result = gen.generate_rsa(group_year, brand_name="Marka", brand_usp="",
                                  grounding_facts="")
        texts = [h.text for h in result.headlines]
        assert "100.000+ Müşteri" not in texts
        assert "2026 yıl sonu hisse rehberi" in texts  # muafiyetle hayatta

    def test_fallback_assets_also_grounded_group_fails_below_minimum(self):
        # AI hiç geçerli yanıt vermiyor → deterministik fallback; display USP
        # sayısal iddia taşıyor → fallback description'ları da elenir →
        # minimum sağlanamaz → grup FAILED (ValueError)
        ai = CannedAI("bozuk json {{{", rewrite_response="")
        gen = RSAGenerator(ai_service=ai, enable_ai_regeneration=False,
                           enable_fallback=True)
        with pytest.raises(ValueError):
            gen.generate_rsa(
                GROUP, brand_name="Marka",
                brand_usp="100.000+ müşteri memnuniyeti",
                grounding_facts="",  # generic/display USP whitelist'e girmez
            )

    def test_fallback_clean_usp_survives_grounding(self):
        ai = CannedAI("bozuk json {{{")
        gen = RSAGenerator(ai_service=ai, enable_ai_regeneration=False,
                           enable_fallback=True)
        result = gen.generate_rsa(
            GROUP, brand_name="Marka", brand_usp="Kaliteli ürün ve hizmet",
            grounding_facts="",
        )
        assert len(result.headlines) >= gen.MIN_HEADLINES
        assert len(result.descriptions) >= gen.MIN_DESCRIPTIONS


# ==================== 4. SOCIAL _ground_or_reject ====================

def _content(caption, hook_text="Merak ettiniz mi?"):
    return SocialContentSchema(
        idea_id=1,
        hooks=[HookSchema(text=hook_text, style=HookStyleEnum.QUESTION)],
        caption=caption,
        cta_text="Kaydet",
        hashtags=["a", "b", "c", "d", "e"],
    )


def _idea():
    from app.schemas.social import (
        ContentFormatEnum,
        PlatformEnum,
        SocialIdeaSchema,
    )
    return SocialIdeaSchema(
        id=7, category_id=1, idea_title="Fikir", idea_description="d",
        target_platform=PlatformEnum.INSTAGRAM,
        content_format=ContentFormatEnum.POST, trend_alignment=0.5,
    )


class TestSocialGroundOrReject:
    def _generator(self, regenerated):
        from app.generators.social.social_generator import SocialGenerator

        gen = SocialGenerator.__new__(SocialGenerator)  # db'siz unit kurulum

        class FakeContentGen:
            def __init__(self):
                self.calls = 0

            def regenerate(self, **kwargs):
                self.calls += 1
                return regenerated

        gen.content_gen = FakeContentGen()
        return gen

    def test_clean_content_passes_without_regenerate(self):
        gen = self._generator(regenerated=None)
        content, warning = gen._ground_or_reject(
            _content("Bu hataya dikkat edin."), _idea(), "Marka", "", ""
        )
        assert warning is None
        assert content is not None
        assert gen.content_gen.calls == 0

    def test_violation_regenerated_clean_is_saved(self):
        gen = self._generator(regenerated=_content("Temiz içerik."))
        content, warning = gen._ground_or_reject(
            _content("%80'i bu hatayı yapıyor"), _idea(), "Marka", "", ""
        )
        assert warning is None
        assert content.caption == "Temiz içerik."
        assert gen.content_gen.calls == 1

    def test_second_violation_rejected_with_warning(self):
        gen = self._generator(
            regenerated=_content("50 bin kullanıcı bunu yapıyor")
        )
        content, warning = gen._ground_or_reject(
            _content("%80'i bu hatayı yapıyor"), _idea(), "Marka", "", ""
        )
        assert content is None
        assert warning is not None
        assert warning.idea_id == 7
        assert warning.reason_code == "ungrounded_claim"
        assert warning.claims
        assert gen.content_gen.calls == 1

    def test_regenerate_failure_rejected_with_warning(self):
        gen = self._generator(regenerated=None)
        content, warning = gen._ground_or_reject(
            _content("kazanç garantisi ile"), _idea(), "Marka", "", ""
        )
        assert content is None
        assert warning is not None
        assert warning.reason_code == "ungrounded_claim"
