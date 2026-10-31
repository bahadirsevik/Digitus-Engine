"""Dil-duyarlı CTA tespiti + dile özgü varsayılan negatifler (Codex #2/#3, plan_pazar_dil Faz 2).

Ücretli AI çağrısı yok: _FakeAI sabit JSON döner.
"""
import json

import pytest

from app.generators.ads.rsa_generator import RSAGenerator, _DEFAULT_NEGATIVES
from app.generators.ads.validators import build_standard_report, is_cta
from app.generators.output_language import OutputContext
from app.schemas.ads import DescriptionSchema, HeadlineSchema, KeywordGroupSchema

TR, EN, DE, RU, UK = "Türkçe", "İngilizce", "Almanca", "Rusça", "Ukraynaca"

CTA_TABLE = [
    # Türkçe / None: eski davranış değişmez
    ("Hemen Arayın", None, True), ("Hemen Arayın", TR, True), ("Book now", None, False),
    ("Teklif Alma Süreci", TR, False),
    # İngilizce
    ("Book now", EN, True), ("Contact us", EN, True), ("Learn more", EN, True),
    ("Get a Free Quote", EN, True), ("Sign up today", EN, True), ("Shop Now", EN, True),
    ("Now Request a Quote", EN, True),
    ("Fast Laptop Delivery", EN, False), ("Best Prices in Berlin", EN, False), ("", EN, False),
    # Almanca (Mastar sonda, Sie-Imperativ başta)
    ("Jetzt buchen", DE, True), ("Termin vereinbaren", DE, True), ("Kostenlos anrufen", DE, True),
    ("Kontaktieren Sie uns", DE, True), ("Jetzt Angebot anfordern", DE, True),
    ("Schnelle Lieferung", DE, False), ("Beste Laptops in Berlin", DE, False),
    # Rusça
    ("Запишитесь сейчас", RU, True), ("Позвоните нам сегодня", RU, True),
    ("Узнайте цену", RU, True), ("Быстрая доставка", RU, False), ("Лучшие цены", RU, False),
    # Ukraynaca (kesme işareti iki yazımla)
    ("Запишіться зараз", UK, True), ("Зателефонуйте сьогодні", UK, True),
    ("Зв'яжіться з нами", UK, True), ("Звʼяжіться з нами", UK, True),
    ("Швидка доставка", UK, False), ("Найкращі ціни", UK, False),
    # dil çaprazlama: İngilizce CTA Rusça pazarda sayılmaz
    ("Book now", RU, False), ("Запишитесь сейчас", EN, False),
]


@pytest.mark.parametrize("text,language,expected", CTA_TABLE)
def test_is_cta_language(text, language, expected):
    assert is_cta(text, language) is expected


def test_default_language_signature_unchanged():
    assert is_cta("Hemen Arayın") is True and is_cta("Dijital Görünürlük Kazanın") is False


# ---- tam yabancı havuz: tamamlama çağrısı yok, CTA seçimde korunur ----

POOLS = {
    EN: (["Book Now", "Contact Us Today", "Learn More"],
         [f"Laptop Deals Option {c}" for c in "ABCDEFGHIJKL"], "laptop deals"),
    DE: (["Jetzt buchen", "Termin vereinbaren", "Kontaktieren Sie uns"],
         [f"Laptop Angebot {c}" for c in "ABCDEFGHIJKL"], "laptop angebot"),
    RU: (["Запишитесь сейчас", "Позвоните нам", "Узнайте цену"],
         [f"Ноутбук Модель {c}" for c in "ABCDEFGHIJKL"], "ноутбук"),
    UK: (["Запишіться зараз", "Зателефонуйте нам", "Дізнайтеся ціну"],
         [f"Ноутбук Модель {c}" for c in "ABCDEFGHIJKL"], "ноутбук"),
}


def _gen(ctx, ai=None):
    gen = RSAGenerator(ai_service=ai, enable_ai_regeneration=True, enable_fallback=False)
    gen._output_ctx = ctx
    return gen


@pytest.mark.parametrize("lang", [EN, DE, RU, UK])
def test_full_foreign_pool_needs_no_completion_and_keeps_ctas(lang):
    ctas, plain, kw = POOLS[lang]
    h_pool = [HeadlineSchema(text=t, type="benefit", position_preference="any")
              for t in plain + ctas]
    d_pool = [DescriptionSchema(text=f"{kw} {c} {lang} description", type="value_prop",
                                position_preference="any") for c in "abcd"]
    gen = _gen(OutputContext(language=lang))
    assert gen._needs_completion(h_pool, d_pool, [kw]) is False
    # Türkçe bağlamda aynı havuz yeterli sayılmaz (CTA 0): kontrol, test sahte-geçmesin
    assert _gen(OutputContext(language=TR))._needs_completion(h_pool, d_pool, [kw]) is True
    # 16 aday içinden 15 seçilir; 3 CTA'nın hepsi seçimde kalır
    picked = [h_pool[i].text for i in gen._select_headline_indices(h_pool, [kw])]
    assert len(picked) == 15 and all(c in picked for c in ctas)
    rep = build_standard_report([kw], [1], None, picked, [d.text for d in d_pool], lang)
    assert (rep.cta_count, rep.missing_cta) == (3, 0)
    assert build_standard_report([kw], [1], None, picked, [], None).cta_count == 0


# ---- varsayılan negatifler ----

TR_WORDS = {"nedir", "nasıl", "ne demek", "ücretsiz", "bedava", "ucuz", "ikinci el", "şikayet", "tamir"}


@pytest.mark.parametrize("lang", [EN, DE, RU, UK])
def test_default_negatives_are_target_language_only(lang):
    out = _gen(OutputContext(language=lang))._add_default_negatives([], targets=["zzz"])
    kws = [n.keyword for n in out]
    assert kws == [k for k, _ in _DEFAULT_NEGATIVES[lang]]
    assert not (set(kws) & TR_WORDS)
    assert len(kws) >= 8


def test_expected_words_per_language():
    expect = {
        EN: {"what is", "how to", "free", "cheap", "used", "second hand", "complaints", "repair"},
        DE: {"was ist", "wie", "kostenlos", "gratis", "billig", "gebraucht", "beschwerde", "reparatur"},
        RU: {"что такое", "как", "бесплатно", "дешево", "б/у", "жалобы", "ремонт"},
        UK: {"що таке", "як", "безкоштовно", "дешево", "скарги", "ремонт"},
    }
    for lang, words in expect.items():
        assert words <= {k for k, _ in _DEFAULT_NEGATIVES[lang]}, lang


def test_turkish_and_default_context_unchanged():
    for ctx in (OutputContext(), OutputContext(language=TR)):
        out = _gen(ctx)._add_default_negatives([], targets=["laptop"])
        assert [n.keyword for n in out] == [
            "nedir", "nasıl", "ne demek", "ücretsiz", "bedava", "free", "ucuz", "ikinci el",
            "şikayet", "tamir"]
        assert out[0].category == "bilgi_amacli" and out[0].reason == "Bilgi amaçlı arama"


def test_conflict_guard_still_drops_foreign_default():
    out = _gen(OutputContext(language=EN))._add_default_negatives([], targets=["cheap laptop"])
    kws = [n.keyword for n in out]
    assert "cheap" not in kws and "free" in kws
    out = _gen(OutputContext(language=RU))._add_default_negatives([], targets=["ноутбук б/у"])
    assert "б/у" not in [n.keyword for n in out]


# ---- uçtan uca (sahte AI): final negatifler hedef dilde, CTA tamamlaması çağrılmaz ----

class _FakeAI:
    def __init__(self, payload):
        self.payload, self.json_calls = payload, 0

    def complete_json(self, prompt, max_tokens=None, response_schema=None):
        self.json_calls += 1
        return self.payload

    def complete(self, prompt, max_tokens=None):
        return ""


def test_generate_rsa_english_final_negatives_and_no_completion_call():
    ctas, plain, kw = POOLS[EN]
    payload = json.dumps({
        "headlines": [{"text": t, "type": "benefit", "position": "any"} for t in plain[:12] + ctas],
        "descriptions": [{"text": f"Compare {kw} models and find your fit {c}.",
                          "type": "value_prop", "position": "any"} for c in "abcd"],
        "negative_keywords": [],
    })
    ai = _FakeAI(payload)
    group = KeywordGroupSchema(name="Laptops", theme="laptops", keyword_ids=[1], keywords=[kw])
    result = RSAGenerator(ai_service=ai, enable_ai_regeneration=True, enable_fallback=False
                          ).generate_rsa(group, brand_name="Acme",
                                         output_ctx=OutputContext(language=EN, location="Berlin"))
    assert ai.json_calls == 1                      # tamamlama çağrısı yok
    assert result.missing_cta == 0
    kws = {n.keyword for n in result.negative_keywords}
    assert kws and kws <= {k for k, _ in _DEFAULT_NEGATIVES[EN]} and not (kws & TR_WORDS)
