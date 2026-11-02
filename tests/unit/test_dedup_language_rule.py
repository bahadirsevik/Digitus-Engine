"""Dedup dil kurali (plan_pazar_dil_secimi 2-e): Turkce fuzzy, diger diller exact."""
from app.core.keyword_dedup import dedup_mode, deduplicate_keywords


def _kw(text, vol=100, comp=0.4):
    return {"keyword": text, "monthly_volume": vol, "competition_score": comp}


def test_dedup_mode_table():
    assert dedup_mode(None) == "fuzzy_tr"
    assert dedup_mode("") == "fuzzy_tr"
    assert dedup_mode("1037") == "fuzzy_tr"
    assert dedup_mode(1037) == "fuzzy_tr"
    assert dedup_mode("languageConstants/1037") == "fuzzy_tr"
    assert dedup_mode("1055") == "fuzzy_tr"  # eski Windows locale alias'i
    for lang in ("1031", "1036", "1000", "1001"):
        assert dedup_mode(lang) == "exact"


def test_agenda_safari_not_merged_in_exact_mode_but_in_turkish():
    # 'agenda'/'safari': Turkce ek kirpma (-da/-i) ikisini de kisaltip yakin gosterebilir
    pair = [_kw("online agenda"), _kw("online agend")]
    assert len(deduplicate_keywords(pair, language_id="1000")) == 2
    assert len(deduplicate_keywords(pair)) == 1  # Turkce kural ('-da' kirpma) bunu birlestirirdi
    pair = [_kw("safari tours"), _kw("safar tours")]
    assert len(deduplicate_keywords(pair, language_id="1000")) == 2
    assert len(deduplicate_keywords(pair)) == 1


def test_cyrillic_inflections_distinct_in_exact_mode():
    pair = [_kw("лечение волос"), _kw("лечения волос")]
    assert len(deduplicate_keywords(pair, language_id="1031")) == 2


def test_exact_mode_merges_same_normalized_same_metrics():
    pair = [_kw("Лечение  Волос"), _kw("лечение волос")]
    out, details = deduplicate_keywords(pair, return_merge_details=True, language_id="1031")
    assert len(out) == 1
    assert details == [{"dropped": "лечение волос", "kept": "Лечение  Волос"}]


def test_exact_mode_keeps_same_text_with_different_metrics():
    pair = [_kw("лечение волос", vol=100), _kw("лечение волос", vol=200)]
    assert len(deduplicate_keywords(pair, language_id="1031")) == 2


def test_turkish_mode_unchanged_on_golden_pairs():
    # mevcut golden: ek varyanti birlesir, zit niyet birlesmez
    assert len(deduplicate_keywords([_kw("hisse tavsiyeleri"), _kw("hisse tavsiyesi")])) == 1
    assert len(deduplicate_keywords(
        [_kw("hisse tavsiyeleri"), _kw("hisse tavsiyesi")], language_id="1037")) == 1
    assert len(deduplicate_keywords(
        [_kw("en yüksek fiyatlı hisseler"), _kw("en dusuk fiyatli hisseler")],
        language_id="1037")) == 2
