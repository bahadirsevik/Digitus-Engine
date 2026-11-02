"""Faz 4 canli dogrulama bulgulari: Rusya icin Google hata metni + tek-grup etiketi."""
from app.api.v1.google_ads import _translate_invalid_argument
from app.generators.ads.keyword_grouper import KeywordGrouper
from app.generators.output_language import OutputContext
from app.schemas.ads import KeywordForGrouping

RU_GRPC = (
    "errors { error_code { keyword_plan_idea_error: INVALID_VALUE } "
    'message: "The input has an invalid value." '
    'location { field_path_elements { field_name: "geo_target_constants" index: 0 } } }'
)


def test_untargetable_geo_gets_friendly_message():
    msg = _translate_invalid_argument(RU_GRPC)
    assert "isteği reddetti" in msg and "CSV" in msg
    assert "INVALID_VALUE" not in msg


def test_other_invalid_argument_text_unchanged():
    assert _translate_invalid_argument("something else").startswith("Google Ads geçersiz parametre: ")


def _grouper():
    return object.__new__(KeywordGrouper)  # _create_single_group AI servisine dokunmaz


def _kws():
    return [KeywordForGrouping(id=1, keyword="имплантация зубов"), KeywordForGrouping(id=2, keyword="имплант цена")]


def test_single_group_turkish_label_unchanged():
    g = _grouper()._create_single_group(_kws(), OutputContext())[0]
    assert g.name.endswith(" - Genel") and g.theme == "Tüm ilgili anahtar kelimeler"


def test_single_group_foreign_language_has_no_turkish_label():
    g = _grouper()._create_single_group(_kws(), OutputContext(language="Rusça"))[0]
    assert g.name == "Имплантация Зубов" and g.theme == "имплантация зубов"
    assert "Genel" not in g.name and g.keyword_ids == [1, 2]
