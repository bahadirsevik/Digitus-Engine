"""Paket B (plan_iddia_denetimi §2): çekimli dil kabulü (B2), DKI (B3), negatifler (B4/B6).

Ücretli AI çağrısı yok. Türkçe davranış her testte karşılaştırmalı doğrulanır.
"""
import json

from app.generators.ads.rsa_generator import RSAGenerator, _DEFAULT_NEGATIVES
from app.generators.ads.validators import keyword_in_text
from app.generators.output_language import DEFAULT_CONTEXT, OutputContext
from app.schemas.ads import HeadlineSchema, KeywordGroupSchema

RU_CTX = OutputContext(language="Rusça", location="Berlin, Almanya — Şehir")


def _gen(ctx):
    gen = RSAGenerator(ai_service=None, enable_ai_regeneration=False, enable_fallback=False)
    gen._output_ctx = ctx
    return gen


def _response(entries):
    return json.dumps({"headlines": [], "descriptions": [], "negative_keywords": [],
                       "keyword_headlines": entries}, ensure_ascii=False)


# ---- B2: çekimli başlık elenmez, benefit olur, kapsam birebir kalır ----

def test_ru_inflected_keyword_headline_kept_as_benefit_not_counted_as_coverage():
    group = KeywordGroupSchema(name="G", theme="t", keyword_ids=[1], keywords=["доставка цветов"])
    gen = _gen(RU_CTX)
    stats = {}
    out, claimed = gen._apply_keyword_headlines(
        _response([{"keyword_id": 1, "headline": "Доставку цветов в Берлин", "reason": ""}]),
        group, [], "доставка цветов", stats)

    assert [h.text for h in out] == ["Доставку цветов в Берлин"]
    assert out[0].type == "benefit"
    assert claimed == []  # kapsam sayılmaz
    assert not keyword_in_text("доставка цветов", out[0].text)
    assert gen._kw_decisions["доставка цветов"]["status"] == "invalid"


def test_ru_exact_keyword_headline_still_counts_as_coverage():
    group = KeywordGroupSchema(name="G", theme="t", keyword_ids=[1], keywords=["доставка цветов"])
    out, claimed = _gen(RU_CTX)._apply_keyword_headlines(
        _response([{"keyword_id": 1, "headline": "Доставка цветов в Берлин", "reason": ""}]),
        group, [], "доставка цветов", {})
    assert claimed == ["доставка цветов"] and out[0].type != "benefit"


def test_turkish_inexact_keyword_headline_still_dropped():
    group = KeywordGroupSchema(name="G", theme="t", keyword_ids=[1], keywords=["hisse analiz"])
    gen = _gen(DEFAULT_CONTEXT)
    out, claimed = gen._apply_keyword_headlines(
        _response([{"keyword_id": 1, "headline": "Hisse analizi araçları", "reason": ""}]),
        group, [], "hisse analiz", {})
    assert out == [] and claimed == []
    assert gen._kw_decisions["hisse analiz"]["status"] == "invalid"


# ---- B3: yabancı dilde DKI yalnız çıplak makro ----

def _validate(ctx, headlines):
    return _gen(ctx)._validate_headlines(
        [{"text": t, "type": "dynamic", "position": "any"} for t in headlines], "цветы")[0]


def test_ru_dki_with_surrounding_words_is_expanded_to_keyword_type():
    out = _validate(RU_CTX, ["Лучшие {KeyWord:Цветы} тут", "{KeyWord:Цветы}"])
    by_text = {h.text: h for h in out}
    expanded = by_text["Лучшие Цветы тут"]
    assert expanded.type == "keyword" and expanded.is_dki is False
    assert by_text["{KeyWord:Цветы}"].is_dki is True and by_text["{KeyWord:Цветы}"].type == "dynamic"


def test_ru_expanded_dki_too_long_is_eliminated_and_no_macro_survives():
    out = _validate(RU_CTX, ["Очень длинный заголовок для {KeyWord:Цветы} и подарков"])
    assert all("{KeyWord" not in h.text for h in out if not h.is_dki)
    assert all(len(h.text) <= 30 for h in out)


def test_ru_description_never_keeps_macro():
    descs, _ = _gen(RU_CTX)._validate_descriptions(
        [{"text": "Свежие {KeyWord:Цветы} с доставкой", "type": "value_prop", "position": "any"}],
        None, False)
    assert descs and all("{" not in d.text for d in descs)


def test_turkish_dki_with_surrounding_words_unchanged():
    out = _gen(DEFAULT_CONTEXT)._validate_headlines(
        [{"text": "En İyi {KeyWord:Ürünler}", "type": "dynamic", "position": "any"}], "ürün")[0]
    assert out and out[0].is_dki is True and "{KeyWord:" in out[0].text


# ---- B4: varsayılan negatifler ----

def test_default_negative_pairs_present_and_otzyvy_stays_removed():
    def words(lang):
        return [w for w, _ in _DEFAULT_NEGATIVES[lang]]

    assert {"жалоба", "жалобы"} <= set(words("Rusça"))
    assert {"скарга", "скарги"} <= set(words("Ukraynaca"))
    assert {"beschwerde", "beschwerden"} <= set(words("Almanca"))
    assert {"complaint", "complaints"} <= set(words("İngilizce"))
    assert "отзывы" not in words("Rusça")


def test_turkish_defaults_unchanged():
    assert _DEFAULT_NEGATIVES["Türkçe"] == [
        ("nedir", "bilgi_amacli"), ("nasıl", "bilgi_amacli"), ("ne demek", "bilgi_amacli"),
        ("ücretsiz", "ucretsiz"), ("bedava", "ucretsiz"), ("free", "ucretsiz"),
        ("ucuz", "dusuk_kalite"), ("ikinci el", "ikinci_el"),
        ("şikayet", "sikayet"), ("tamir", "diy"),
    ]


def test_top_up_adds_both_singular_and_plural():
    out = _gen(RU_CTX)._add_default_negatives([], targets=["доставка цветов"])
    assert {"жалоба", "жалобы"} <= {n.keyword for n in out}


# ---- B2/B3/B4/B6 prompt satırları yalnız yabancı dilde ----

def _prompt(ctx, kept=None):
    gen = _gen(ctx)
    group = KeywordGroupSchema(name="G", theme="t", keyword_ids=[1], keywords=["цветы"])
    return gen._build_candidates_prompt(group, "Marka", "—", kept)


def test_foreign_prompt_has_language_rules_turkish_does_not():
    kept = ([HeadlineSchema(text="Цветы", type="keyword", position_preference="any")], [])
    for k in (None, kept):
        ru = _prompt(RU_CTX, k)
        assert "HEDEF DİLE ÖZEL KURALLAR" in ru
        assert "hâl eki alabilir" in ru and "çıplak {KeyWord:Varsayılan}" in ru
        assert "'reason' Türkçe" in ru
        tr = _prompt(DEFAULT_CONTEXT, k)
        assert "HEDEF DİLE ÖZEL" not in tr and "Varsayılan}" not in tr
