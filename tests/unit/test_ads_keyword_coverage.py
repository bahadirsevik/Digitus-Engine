"""KW kapsam yardımcısı testleri (plan_ads_15kw_standardi §3, K7/K8).

Saf fonksiyonlar; DB, ağ ve AI çağrısı yok. Eşleşme bizim deterministik
ölçümümüzdür (Google Ad Strength değil): Türkçe katlama + kelime sınırı +
ardışık ifade, ek toleransı YOK.
"""
from app.generators.ads.validators import keyword_coverage, keyword_in_text


class TestKeywordInText:
    def test_capital_dotted_i_matches(self):
        assert keyword_in_text("istanbul", "İstanbul Kurye Hizmeti")

    def test_uppercase_keyword_matches_lowercase_text(self):
        assert keyword_in_text("İSTANBUL", "istanbul kurye")
        assert keyword_in_text("ISPARTA", "ısparta gül yağı")

    def test_suffixed_form_does_not_match(self):
        assert not keyword_in_text("laptop fiyatı", "Laptop Fiyatları")

    def test_word_boundary(self):
        assert not keyword_in_text("kalem", "Kalemlik Modelleri")
        assert keyword_in_text("kalem", "Kalem, Defter")

    def test_multi_word_must_be_contiguous_and_ordered(self):
        assert keyword_in_text("ucuz laptop", "En Ucuz Laptop Modelleri")
        assert not keyword_in_text("ucuz laptop", "Laptop Ucuz")
        assert not keyword_in_text("ucuz laptop", "Ucuz Oyun Laptop")

    def test_apostrophe_and_hyphen_are_boundaries(self):
        assert keyword_in_text("istanbul", "İstanbul'da Kurye")
        assert keyword_in_text("e ticaret", "E-Ticaret Paketleri")

    def test_dki_uses_default_text(self):
        assert keyword_in_text("laptop modelleri", "{KeyWord:Laptop Modelleri}")
        assert keyword_in_text("laptop modelleri", "Ucuz {Keyword:Laptop Modelleri} Burada")
        assert not keyword_in_text("keyword", "{KeyWord:Laptop Modelleri}")

    def test_empty_inputs(self):
        assert not keyword_in_text("", "Laptop Fiyatları")
        assert not keyword_in_text("   ", "Laptop Fiyatları")
        assert not keyword_in_text("laptop", "")
        assert not keyword_in_text("laptop", None)


class TestKeywordCoverage:
    def test_headline_beats_description(self):
        out = keyword_coverage(["laptop"], ["Laptop Fırsatları"], ["Laptop seçenekleri burada"])
        assert out == {"laptop": "headline"}

    def test_description_only_and_none(self):
        out = keyword_coverage(
            ["laptop", "tablet"],
            ["Bilgisayar Fırsatları"],
            ["Uygun fiyatlı laptop seçenekleri"],
        )
        assert out == {"laptop": "description", "tablet": "none"}

    def test_input_order_preserved(self):
        kws = ["zeta", "alfa", "mu"]
        out = keyword_coverage(kws, ["Alfa"], ["Zeta"])
        assert list(out) == kws
        assert list(out.values()) == ["description", "headline", "none"]

    def test_keyword_longer_than_30_chars_found_only_in_description(self):
        kw = "kurumsal e-ticaret sitesi kurulum hizmeti fiyatları"
        assert len(kw) > 30
        out = keyword_coverage(
            [kw],
            ["Kurumsal E-Ticaret Sitesi"],
            [f"{kw.capitalize()} için hemen teklif alın."],
        )
        assert out == {kw: "description"}

    def test_empty_keyword_and_empty_lists(self):
        assert keyword_coverage([], ["a"], ["b"]) == {}
        assert keyword_coverage(["laptop"], [], []) == {"laptop": "none"}
        assert keyword_coverage([""], ["Laptop"], ["Laptop"]) == {"": "none"}
