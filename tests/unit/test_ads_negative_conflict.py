"""Negatif <-> hedef kelime çakışması testleri (plan_yapilacaklar 2.1).

Kapsam: negative_blocks_target kuralları (broad / phrase / exact), Türkçe
normalizasyon, öbek eşitleme YOKLUĞU (masa != masaj), varsayılan tamamlamanın
çakışan kelimeyi geri eklememesi ve generate_rsa kancasının her yolu
kapsaması. Ücretli AI çağrısı yok (CannedAI).
"""
import json

from app.generators.ads.prompt_templates import ADS_RSA_GENERATION_PROMPT
from app.generators.ads.rsa_generator import RSAGenerator
from app.generators.ads.validators import (
    _negative_tokens,
    negative_blocks_target,
    partition_negatives,
)
from app.schemas.ads import AdGroupFullSchema, KeywordGroupSchema, NegativeKeywordSchema


# ==================== 1. SAF FONKSİYON ====================

class TestNegativeBlocksTarget:
    def test_broad_drops_when_all_words_in_target(self):
        assert negative_blocks_target("ücretsiz", "broad", "ücretsiz diş macunu")

    def test_broad_ignores_order(self):
        assert negative_blocks_target("macunu diş", "broad", "ucuz diş macunu")

    def test_broad_needs_all_words(self):
        assert not negative_blocks_target("diş fırçası", "broad", "diş macunu")

    def test_phrase_requires_same_order_and_contiguous(self):
        # sıra farklı -> kalır
        assert not negative_blocks_target("diş macunu ucuz", "phrase", "ucuz diş macunu")
        # bitişik alt dizi -> atılır
        assert negative_blocks_target("diş macunu", "phrase", "ucuz diş macunu")
        # bitişik değil -> kalır
        assert not negative_blocks_target("ucuz macunu", "phrase", "ucuz diş macunu")

    def test_exact_requires_equal_sequences(self):
        assert negative_blocks_target("[diş macunu]", "exact", "diş macunu")
        assert not negative_blocks_target("[diş macunu]", "exact", "ucuz diş macunu")

    def test_match_type_syntax_residue_is_stripped(self):
        assert negative_blocks_target('"diş macunu"', "phrase", "diş macunu")
        assert negative_blocks_target("-ücretsiz", "broad", "ücretsiz kurs")

    def test_no_prefix_or_stem_equality(self):
        assert not negative_blocks_target("masa", "broad", "masaj yağı")
        assert not negative_blocks_target("masa", "phrase", "masaj yağı")
        assert not negative_blocks_target("masa", "exact", "masaj")
        # çoğul/ek eşitleme yok
        assert not negative_blocks_target("laptop", "broad", "laptoplar")

    def test_turkish_dotted_capital_i(self):
        assert negative_blocks_target("İzmir", "broad", "izmir diş")
        assert negative_blocks_target("ISPARTA", "broad", "ısparta gül")

    def test_unknown_or_missing_match_type_is_broad(self):
        assert negative_blocks_target("macunu diş", "weird", "diş macunu")
        assert negative_blocks_target("macunu diş", "", "diş macunu")
        assert negative_blocks_target("macunu diş", None, "diş macunu")

    def test_empty_inputs_never_block(self):
        assert not negative_blocks_target("", "broad", "diş macunu")
        assert not negative_blocks_target("[]", "broad", "diş macunu")
        assert not negative_blocks_target("diş", "broad", "")

    def test_partition_reports_dropped_details(self):
        negs = [
            NegativeKeywordSchema(keyword="ücretsiz", match_type="broad"),
            NegativeKeywordSchema(keyword="bedava", match_type="phrase"),
        ]
        kept, dropped = partition_negatives(negs, ["ücretsiz diş macunu"], "Grup A")
        assert [n.keyword for n in kept] == ["bedava"]
        assert dropped == [{
            "type": "negative_dropped",
            "group": "Grup A",
            "negative": "ücretsiz",
            "match_type": "broad",
            "target": "ücretsiz diş macunu",
            "rule": "broad",
        }]


# ==================== 2. VARSAYILAN TAMAMLAMA ====================

def _gen():
    return RSAGenerator(ai_service=_Canned("{}"), enable_ai_regeneration=False,
                        enable_fallback=False)


class _Canned:
    def __init__(self, json_response: str):
        self.json_response = json_response

    def complete_json(self, prompt, max_tokens=None, response_schema=None):
        return self.json_response

    def complete(self, prompt, max_tokens=None):
        return ""


class TestDefaultNegatives:
    def test_conflicting_default_not_added(self):
        out = _gen()._add_default_negatives([], targets=["ucuz laptop"])
        kws = [n.keyword for n in out]
        assert "ucuz" not in kws
        assert "bedava" in kws

    def test_dedupes_on_normalized_form(self):
        existing = [NegativeKeywordSchema(keyword="ÜCRETSİZ", match_type="phrase")]
        out = _gen()._add_default_negatives(existing, targets=["laptop"])
        # mevcut 'ÜCRETSİZ' ile varsayılan 'ücretsiz' aynı normalize biçim
        assert sum(1 for n in out if _negative_tokens(n.keyword) == ["ücretsiz"]) == 1

    def test_excluded_terms_not_readded(self):
        out = _gen()._add_default_negatives(
            [], targets=["laptop"], excluded=["Bedava"],
        )
        assert "bedava" not in [n.keyword for n in out]

    def test_no_safe_default_returns_fewer_not_more(self):
        # Hedef tüm varsayılanları kapsıyor -> hiçbiri eklenmez
        target = ("nedir nasıl ne demek ücretsiz bedava free ucuz ikinci el "
                  "şikayet tamir")
        out = _gen()._add_default_negatives([], targets=[target])
        assert out == []


# ==================== 3. generate_rsa KANCASI ====================

def _rsa_json(negatives):
    return json.dumps({
        "headlines": [
            {"text": t, "type": "benefit", "position": "any"}
            for t in ["Laptop Fiyatları", "Hemen Keşfet", "Modelleri İncele"]
        ],
        "descriptions": [
            {"text": t, "type": "value_prop", "position": "any"}
            for t in ["Laptop modellerini tek ekranda inceleyin.",
                      "Hemen keşfedin ve karşılaştırın."]
        ],
        "negative_keywords": [
            {"keyword": k, "match_type": m, "category": "c", "reason": "r"}
            for k, m in negatives
        ],
    })


GROUP = KeywordGroupSchema(
    name="Laptop", theme="laptop", keyword_ids=[1], keywords=["ucuz laptop"],
)


class TestGenerateRsaHook:
    def test_primary_attempt_drops_conflicting_llm_negative(self):
        negs = [("ucuz", "broad"), ("laptop", "phrase")] + [
            (f"neg{i}", "phrase") for i in range(10)
        ]
        gen = RSAGenerator(ai_service=_Canned(_rsa_json(negs)),
                           enable_ai_regeneration=False, enable_fallback=False)
        result = gen.generate_rsa(GROUP, brand_name="Marka")
        kws = [n.keyword for n in result.negative_keywords]
        assert "ucuz" not in kws and "laptop" not in kws
        assert len(kws) == 10
        assert {d["negative"] for d in result.dropped_negatives} == {"ucuz", "laptop"}
        assert all(d["group"] == "Laptop" and d["target"] == "ucuz laptop"
                   for d in result.dropped_negatives)

    def test_topup_never_readds_dropped_or_unsafe(self):
        # 2 LLM negatifi de çakışıyor -> atılır; top-up 'ucuz' EKLEMEZ
        gen = RSAGenerator(
            ai_service=_Canned(_rsa_json([("ucuz", "phrase"), ("laptop", "broad")])),
            enable_ai_regeneration=False, enable_fallback=False,
        )
        result = gen.generate_rsa(GROUP, brand_name="Marka")
        kws = [n.keyword for n in result.negative_keywords]
        assert "ucuz" not in kws and "laptop" not in kws
        # 10 varsayılandan yalnız çakışmayan 9'u eklendi (sayı doldurulmadı)
        assert len(kws) == 9

    def test_fallback_path_defaults_are_safe(self):
        # AI çöp döner -> deterministik fallback -> varsayılan negatifler
        gen = RSAGenerator(ai_service=_Canned("not json"),
                           enable_ai_regeneration=False, enable_fallback=True)
        result = gen.generate_rsa(GROUP, brand_name="Marka")
        kws = [n.keyword for n in result.negative_keywords]
        assert "ucuz" not in kws
        assert "bedava" in kws

    def test_dropped_negatives_not_serialized(self):
        gen = RSAGenerator(
            ai_service=_Canned(_rsa_json([("ucuz", "phrase")])),
            enable_ai_regeneration=False, enable_fallback=False,
        )
        result = gen.generate_rsa(GROUP, brand_name="Marka")
        assert result.dropped_negatives
        assert "dropped_negatives" not in result.model_dump()
        assert "dropped_negatives" not in json.loads(result.model_dump_json())


# ==================== 4. PROMPT ====================

class TestPrompts:
    def test_main_prompt_has_preventive_line_and_formats(self):
        assert "Hedef anahtar kelimeleri veya bunların parçalarını negatif" in ADS_RSA_GENERATION_PROMPT
        ADS_RSA_GENERATION_PROMPT.format(
            group_name="G", group_theme="T", keywords="a, b",
            brand_name="M", brand_usp="—", product_facts="—",
        )

    def test_strict_retry_prompt_has_preventive_line(self):
        gen = _gen()
        prompt = gen._build_strict_retry_prompt(GROUP, "Marka", "usp")
        assert "Hedef anahtar kelimeleri veya bunların parçalarını negatif" in prompt
        assert '"negative_keywords"' in prompt  # JSON şeması korunuyor


def test_schema_default_is_empty_and_independent():
    a = AdGroupFullSchema(name="a", theme="t", keyword_ids=[], keywords=[],
                          headlines=[], descriptions=[], negative_keywords=[])
    b = AdGroupFullSchema(name="b", theme="t", keyword_ids=[], keywords=[],
                          headlines=[], descriptions=[], negative_keywords=[])
    a.dropped_negatives.append({"x": 1})
    assert b.dropped_negatives == []
