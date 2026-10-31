"""İddia denetimi sözlüğü (plan_iddia_denetimi A1): hizmet/marka, sertifika, mutlak sonuç."""
import pytest

from app.generators.ads.validators import find_ungrounded_claims
from app.generators.claim_lexicon import GroundingSource


def _kinds(violations):
    return {v.split(":", 1)[0] for v in violations if ":" in v}


class TestServiceConcepts:
    def test_translation_of_same_concept_is_grounded(self):
        assert find_ungrounded_claims("седация под контролем", "Hizmetler: sedasyon") == []
        assert find_ungrounded_claims("седація під контролем", "Hizmetler: sedasyon") == []

    def test_service_not_in_source_is_violation(self):
        v = find_ungrounded_claims("лазером без лишних визитов", "Hizmetler: implant")
        assert any(x.startswith("service:") for x in v)

    def test_negated_source_line_does_not_grant(self):
        v = find_ungrounded_claims("седация", "Hizmetler: implant\nsedasyon yapmıyoruz")
        assert any(x.startswith("service:") for x in v)

    def test_brands_are_separate_concepts(self):
        v = find_ungrounded_claims("Straumann implant", "Hizmetler: Osstem implant")
        assert any(x.startswith("service:") for x in v)
        assert find_ungrounded_claims("Osstem implant", "Hizmetler: Osstem implant") == []

    def test_botox_does_not_grant_filler(self):
        v = find_ungrounded_claims("dolgu uygulaması", "Hizmetler: botoks")
        assert any(x.startswith("service:") for x in v)

    def test_target_audience_line_is_not_a_permission_source(self):
        facts = "Hedef kitle: lazer ile ilgilenenler\nHizmetler: implant"
        v = find_ungrounded_claims("лазер", facts)
        assert any(x.startswith("service:") for x in v)

    def test_unlabeled_text_is_usp_backward_compatible(self):
        assert find_ungrounded_claims("Randevu alın", "") == []
        assert find_ungrounded_claims("100.000 müşteri", "100.000 müşteri") == []

    def test_grounding_source_object_accepted(self):
        src = GroundingSource(services=("sedasyon",))
        assert find_ungrounded_claims("sedation available", src) == []


class TestCertificates:
    def test_different_code_is_violation(self):
        v = find_ungrounded_claims("ISO 13485 sertifikalı", "Ürünler: ISO 9001")
        assert any(x.startswith("cert:") for x in v)

    def test_same_code_is_grounded(self):
        assert find_ungrounded_claims("ISO 9001 sertifikalı", "Ürünler: ISO 9001") == []

    def test_bare_org_is_always_violation(self):
        v = find_ungrounded_claims("ISO ve CE belgeli", "Hizmetler: ISO ve CE")
        assert any(x.startswith("cert:") for x in v)

    def test_org_inside_word_not_matched(self):
        assert find_ungrounded_claims("CEO ile tanışın", "") == []


class TestAbsoluteOutcome:
    def test_tamamen_alone_is_clean(self):
        assert find_ungrounded_claims("tamamen dijital randevu", "") == []

    def test_absolute_even_if_source_says_it(self):
        v = find_ungrounded_claims("ağrısız tedavi", "Hizmetler: ağrısız tedavi")
        assert any(x.startswith("absolute:") for x in v)

    def test_keyword_does_not_exempt_absolute(self):
        v = find_ungrounded_claims("%100 ağrısız implant", "", ["%100 ağrısız"])
        assert "absolute" in _kinds(v)

    @pytest.mark.parametrize("text", [
        "абсолютно безболісна процедура",
        "Повна безболісність",
        "лечение без боли",
        "#лечениезубовбезболи",
        "абсолютно безопасно",
        "painless treatment",
        "pain-free dentistry",
        "schmerzfrei behandelt",
        "100% safe",
    ])
    def test_absolute_outcomes_caught_in_all_languages(self, text):
        assert "absolute" in _kinds(find_ungrounded_claims(text, ""))

    def test_only_outcome_phrases_count(self):
        assert find_ungrounded_claims("полностью цифровая запись", "") == []
        assert find_ungrounded_claims("completely online booking", "") == []


class TestKeywordExemptionNarrowed:
    def test_keyword_number_is_exempt_in_numeric_scan(self):
        v = find_ungrounded_claims("4 implant tek seansta", "", ["4 implant"])
        assert not any("4" in x for x in v)

    def test_keyword_year_still_exempt(self):
        assert find_ungrounded_claims("2026 yıl sonu hisse", "", ["2026 yıl sonu hisse"]) == []

    def test_superlative_keyword_is_now_violation(self):
        # Belgelenmiş davranış değişikliği: keyword maskesi artık süperlatifi aklamaz.
        assert find_ungrounded_claims("En iyi diş hekimi", "", ["en iyi diş hekimi"])
