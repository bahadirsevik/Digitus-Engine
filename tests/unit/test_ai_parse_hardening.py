"""AI JSON parse dayanikliligi — P7 Adim 4.

Iki sinif net ayrilir (codex sarti):
  (a) bozuk ama KURTARILABILIR JSON (trailing virgul, markdown fence,
      truncation) -> basariyla parse edilir;
  (b) zorunlu alan EKSIK -> "kurtarildi" sayilmaz, hata atilir.

Kapsanan parser'lar: parse_ai_json_object (ortak), category/idea/content
(social), keyword_grouper, rsa_generator, profile_extractor.
"""
import pytest

from app.core.channel.ai_json import AIJsonParseError, parse_ai_json_object
from app.generators.ads.keyword_grouper import KeywordGrouper
from app.generators.ads.rsa_generator import RSAGenerator
from app.generators.social.category_generator import CategoryGenerator
from app.generators.social.content_generator import ContentGenerator
from app.generators.social.idea_generator import IdeaGenerator
from app.core.site_analyzer.profile_extractor import ProfileExtractor


# ---------- ortak yardimci: parse_ai_json_object ----------

class TestParseAiJsonObject:
    def test_plain_valid(self):
        obj = parse_ai_json_object('{"a": 1, "b": 2}', ("a",))
        assert obj["a"] == 1

    def test_trailing_comma_recovered(self):
        obj = parse_ai_json_object('{"a": 1, "b": 2,\n}', ("a", "b"))
        assert obj["b"] == 2

    def test_fence_recovered(self):
        obj = parse_ai_json_object('```json\n{"a": 1}\n```', ("a",))
        assert obj["a"] == 1

    def test_unbalanced_braces_recovered(self):
        obj = parse_ai_json_object('{"a": 1, "b": {"c": 2}', ("a", "b"))
        assert obj["b"] == {"c": 2}

    def test_missing_required_field_raises(self):
        # (b) sinifi: parse basarili ama alan eksik -> kurtarilMAZ
        with pytest.raises(AIJsonParseError):
            parse_ai_json_object('{"a": 1}', ("a", "zorunlu"))

    def test_garbage_raises(self):
        with pytest.raises(AIJsonParseError):
            parse_ai_json_object('tamamen bozuk', ("a",))


# ---------- social: kategori ----------

class TestCategoryParse:
    gen = CategoryGenerator(ai_service=None)  # _parse_response ai kullanmaz

    def test_fenced_trailing_comma_recovered(self):
        raw = (
            '```json\n{"categories": [{"category_name": "Egitim", '
            '"category_type": "educational", "description": "d", '
            '"relevance_score": 0.9, "suggested_keywords": ["k"],}]}\n```'
        )
        cats = self.gen._parse_response(raw)
        assert len(cats) == 1
        assert cats[0].category_name == "Egitim"

    def test_truncated_array_partial_recovery(self):
        # Canli hata sinifi: "Unterminated string" — ikinci obje yarim kalir,
        # ilk tam obje yine de kurtarilir
        raw = (
            '{"categories": [{"category_name": "Tam", "category_type": '
            '"educational", "description": "d"}, {"category_name": "Yar'
        )
        cats = self.gen._parse_response(raw)
        assert len(cats) == 1
        assert cats[0].category_name == "Tam"

    def test_empty_or_garbage_raises(self):
        with pytest.raises((ValueError, AIJsonParseError)):
            self.gen._parse_response("bozuk yanit")

    def test_missing_category_name_raises(self):
        raw = '{"categories": [{"category_type": "educational", "description": "d"}]}'
        with pytest.raises((ValueError, AIJsonParseError)):
            self.gen._parse_response(raw)


# ---------- social: fikir ----------

class TestIdeaParse:
    gen = IdeaGenerator(ai_service=None)

    def test_wrapper_recovered(self):
        raw = (
            '{"ideas": [{"idea_title": "Fikir", "idea_description": "d", '
            '"target_platform": "instagram", "content_format": "reels", '
            '"trend_alignment": 0.8,}]}'
        )
        ideas = self.gen._parse_response(raw, category_id=1)
        assert len(ideas) == 1
        assert ideas[0].idea_title == "Fikir"

    def test_garbage_raises(self):
        with pytest.raises((ValueError, AIJsonParseError)):
            self.gen._parse_response("::", category_id=1)

    def test_missing_idea_title_raises(self):
        raw = (
            '{"ideas": [{"idea_description": "d", "target_platform": "instagram", '
            '"content_format": "post"}]}'
        )
        with pytest.raises((ValueError, AIJsonParseError)):
            self.gen._parse_response(raw, category_id=1)


# ---------- social: icerik ----------

class TestContentParse:
    gen = ContentGenerator(ai_service=None)

    def test_trailing_comma_recovered(self):
        raw = (
            '{"hooks": [{"text": "Merhaba", "style": "question"}], '
            '"caption": "Aciklama metni", "cta_text": "Dene",}'
        )
        content = self.gen._parse_response(raw, idea_id=5)
        assert content.caption == "Aciklama metni"
        assert content.idea_id == 5

    def test_missing_caption_raises(self):
        # caption'siz icerik kurtarilmis sayilmaz
        raw = '{"hooks": [], "cta_text": "Dene"}'
        with pytest.raises((ValueError, AIJsonParseError)):
            self.gen._parse_response(raw, idea_id=5)


# ---------- ads: grouper ----------

class TestGrouperParse:
    gen = KeywordGrouper(ai_service=None)

    def test_fenced_recovered(self):
        raw = (
            '```json\n{"ad_groups": [{"name": "Grup", "theme": "t", '
            '"keyword_ids": [1], "keywords": ["kw"],}]}\n```'
        )
        groups = self.gen._parse_grouping_response(raw, [], stage="primary")
        assert len(groups) == 1
        assert groups[0].name == "Grup"

    def test_missing_ad_groups_raises(self):
        with pytest.raises((ValueError, AIJsonParseError)):
            self.gen._parse_grouping_response('{"foo": []}', [], stage="primary")


# ---------- ads: rsa ----------

class TestRsaParse:
    gen = RSAGenerator(ai_service=None, enable_ai_regeneration=False, enable_fallback=False)

    def test_trailing_comma_recovered(self):
        raw = (
            '{"headlines": [{"text": "Baslik"}], '
            '"descriptions": [{"text": "Aciklama"}], '
            '"negative_keywords": [{"keyword": "bedava"}],}'
        )
        heads, descs, negs = self.gen._parse_rsa_response(raw, "Grup", "primary")
        assert heads and descs and negs

    def test_garbage_returns_empty_lists(self):
        # Mevcut strict-retry sozlesmesi korunur: hata degil bos tuple
        heads, descs, negs = self.gen._parse_rsa_response("bozuk", "Grup", "primary")
        assert heads == [] and descs == [] and negs == []


# ---------- site analyzer: profil ----------

class TestProfileParse:
    extractor = ProfileExtractor.__new__(ProfileExtractor)  # __init__ crawler kurar, gerek yok

    def test_trailing_comma_recovered(self):
        raw = '{"company_name": "Hissefy", "sector": "Fintech",}'
        parsed = self.extractor._parse_json_response(raw)
        assert parsed["company_name"] == "Hissefy"

    def test_truncated_object_recovered(self):
        # Canli ws13 hatasi sinifi: govde ortasinda kesilen buyuk obje
        raw = '{"company_name": "Hissefy", "sector": "Fintech", "products": ["Analiz"'
        parsed = self.extractor._parse_json_response(raw)
        assert parsed["company_name"] == "Hissefy"

    def test_garbage_raises(self):
        with pytest.raises(ValueError):
            self.extractor._parse_json_response("hic json degil")
