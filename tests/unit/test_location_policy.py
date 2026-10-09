# -*- coding: utf-8 -*-
"""Lokasyon politikası matcher'ı — plan_v3_lokasyon_filtresi.md §8.1.

Kritik arka plan: paylaşılan `normalize_turkish` eskiden `.lower()`'ı Türkçe
karakter haritasından önce uyguladığı için `İstanbul` -> `i stanbul` olurdu
(28.09.2026'da düzeltildi, bkz. test_turkish_normalizer_capital_i.py). Lokasyon
modülü kendi `İ -> i` / `I -> ı` ön katlamasını savunma olarak korur; aşağıdaki
testler bu katlamayı İKİ YÖNDE (lexicon tarafı ve keyword tarafı) sabitler.
"""
import re
from pathlib import Path

import pytest

from app.core.policy.location_policy import (
    AMBIGUOUS_PROVINCES,
    CITY_LEXICON_VERSION,
    MODE_EXCLUDE_ALL,
    MODE_FOCUS_ONLY,
    MODE_NONE,
    REASON_CITY_FILTER,
    REASON_NON_FOCUS_CITY,
    TURKISH_PROVINCES,
    canonical_city,
    evaluate_keyword,
    lexicon_fingerprint,
    match_city_term,
    match_city_terms,
    match_exempt_term,
    normalize_location_text,
)


# ── Sözlük bütünlüğü ────────────────────────────────────────────────────
def test_lexicon_covers_all_81_provinces():
    assert len(TURKISH_PROVINCES) == 81
    assert len(set(TURKISH_PROVINCES)) == 81


def test_lexicon_fingerprint_is_deterministic():
    assert lexicon_fingerprint() == lexicon_fingerprint()
    assert len(lexicon_fingerprint()) == 64


# ── Drift guard: frontend/src/services/locationPolicy.ts ────────────────
# plan_v3_lokasyon_filtresi.md §3/§6: TS tarafı 81 il listesini VE
# AMBIGUOUS_PROVINCES'i bu Python sözlüğünden bağımsız olarak yeniden
# tanımlar (frontend test koşucusu Python import edemez, bu yüzden ayna
# kopya olarak tutulur). Bugün ikisi eşleşiyor ama hiçbir test bunu
# ZORLAMIYORDU — biri değişip diğeri unutulursa CI sessiz kalırdı. Bu
# testler dosyayı metin olarak okuyup diziyi ayrıştırır ve Python
# sözlüğüyle küme eşitliğini doğrular.
_FRONTEND_LOCATION_POLICY_TS = (
    Path(__file__).resolve().parents[2]
    / "frontend"
    / "src"
    / "services"
    / "locationPolicy.ts"
)


def _parse_ts_string_array(source: str, marker: str) -> list:
    """`marker` TANIM METNİNİN TAM OLARAK dizinin açılış `[` karakteriyle
    BİTMESİNİ bekler (ör. `"TURKISH_PROVINCES: string[] = ["`). Bu yüzden
    `marker` içindeki `string[]` gibi başka köşeli parantezlerle
    karıştırılmaz — arama `source.index("[", ...)` ile YENİDEN yapılmaz,
    doğrudan marker'ın son karakteri (`[`) başlangıç kabul edilir.
    İçindeki düz string literallerini (tek/çift tırnaklı) sırayla döndürür.
    """
    assert marker.rstrip().endswith("["), f"marker '{marker}' '[' ile bitmeli"
    start = source.index(marker)
    bracket_start = start + len(marker) - 1
    assert source[bracket_start] == "["
    depth = 0
    end = None
    for idx in range(bracket_start, len(source)):
        ch = source[idx]
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                end = idx
                break
    assert end is not None, f"{marker!r} için kapanış ']' bulunamadı"
    body = source[bracket_start + 1:end]
    return re.findall(r"""['"]([^'"]+)['"]""", body)


def test_frontend_location_policy_ts_exists():
    assert _FRONTEND_LOCATION_POLICY_TS.is_file(), (
        "frontend/src/services/locationPolicy.ts bulunamadı — QA drift "
        "guard'ı yanlış yola bakıyor olabilir."
    )


def test_frontend_turkish_provinces_matches_backend_lexicon():
    """TS `TURKISH_PROVINCES` == Python `TURKISH_PROVINCES` (küme eşitliği).

    Bu test backend sözlüğü değişip TS kopyası unutulursa (veya tersi)
    KIRILIR. Sözlük sürümü artırılırken her iki dosya da güncellenmelidir
    (plan §3: "Şehir sözlüğü değişirse ... sabit bir CITY_LEXICON_VERSION").
    """
    source = _FRONTEND_LOCATION_POLICY_TS.read_text(encoding="utf-8")
    ts_provinces = _parse_ts_string_array(source, "TURKISH_PROVINCES: string[] = [")

    assert len(ts_provinces) == 81, (
        f"TS TURKISH_PROVINCES 81 il yerine {len(ts_provinces)} taşıyor "
        "— ayrıştırma bozulmuş ya da liste değişmiş olabilir."
    )
    assert set(ts_provinces) == set(TURKISH_PROVINCES), (
        "frontend/src/services/locationPolicy.ts::TURKISH_PROVINCES ile "
        "app/core/policy/location_policy.py::TURKISH_PROVINCES ARASINDA "
        f"FARK VAR. Yalnız TS'de: {sorted(set(ts_provinces) - set(TURKISH_PROVINCES))} "
        f"— Yalnız backend'de: {sorted(set(TURKISH_PROVINCES) - set(ts_provinces))}"
    )
    # Sıra da aynı olmalı (iki taraf da 'kanonik sıra' iddiasında —
    # plan §3: "matcher keyword içindeki tüm şehirleri kanonik sırayla
    # toplar"). Sıra kayarsa bu, kanonik sıraya bağlı davranışın
    # (ör. çok-şehirli keyword raporlama sırası) sessizce ıraksadığının
    # erken sinyalidir.
    assert ts_provinces == list(TURKISH_PROVINCES), (
        "TS ve backend il listeleri aynı KÜMEYİ taşıyor ama SIRALARI "
        "farklı — kanonik sıraya bağlı davranış ıraksayabilir."
    )


def test_frontend_ambiguous_provinces_matches_backend():
    source = _FRONTEND_LOCATION_POLICY_TS.read_text(encoding="utf-8")
    ts_ambiguous = _parse_ts_string_array(
        source, "AMBIGUOUS_PROVINCES = new Set(["
    )
    assert set(ts_ambiguous) == set(AMBIGUOUS_PROVINCES), (
        "frontend/src/services/locationPolicy.ts::AMBIGUOUS_PROVINCES ile "
        "app/core/policy/location_policy.py::AMBIGUOUS_PROVINCES ARASINDA "
        f"FARK VAR. Yalnız TS'de: {sorted(set(ts_ambiguous) - set(AMBIGUOUS_PROVINCES))} "
        f"— Yalnız backend'de: {sorted(set(AMBIGUOUS_PROVINCES) - set(ts_ambiguous))}"
    )


def test_lexicon_version_is_pinned():
    assert CITY_LEXICON_VERSION == "tr-provinces-v1"


def test_ambiguous_provinces_are_real_provinces():
    assert AMBIGUOUS_PROVINCES <= set(TURKISH_PROVINCES)


# ── Büyük İ / I katlaması ───────────────────────────────────────────────
@pytest.mark.parametrize("raw", ["istanbul", "İstanbul", "İSTANBUL", "ISTANBUL"])
def test_istanbul_all_casings_resolve(raw):
    assert canonical_city(raw) == "İstanbul"


def test_izmir_and_isparta_fold_correctly():
    assert canonical_city("İZMİR") == "İzmir"
    assert canonical_city("ISPARTA") == "Isparta"


def test_capital_i_matching_is_bidirectional():
    """Profil seçicisi `İstanbul` verir, keyword ASCII gelir — ve tersi."""
    assert match_city_term("istanbul diş kliniği") == "İstanbul"
    assert match_city_term("İstanbul dis klinigi") == "İstanbul"


def test_circumflex_is_folded():
    assert canonical_city("Hakkâri") == "Hakkâri"
    assert canonical_city("hakkari") == "Hakkâri"


def test_normalize_location_text_keeps_city_as_single_token():
    # Regresyon: paylaşılan normalizer tek başına `i stanbul` üretiyordu.
    assert normalize_location_text("İstanbul").split() == ["istanbul"]
    assert normalize_location_text("İZMİR").split() == ["izmir"]


# ── Ek biçimleri ────────────────────────────────────────────────────────
@pytest.mark.parametrize("keyword,city", [
    ("Ankara'da avukat", "Ankara"),
    ("ankara'ya nakliyat", "Ankara"),
    ("Bursa'dan kargo", "Bursa"),
    ("İzmir'e tasinma", "İzmir"),
])
def test_apostrophe_forms_match_via_token_split(keyword, city):
    assert match_city_term(keyword) == city


@pytest.mark.parametrize("keyword,city", [
    ("istanbulda nakliyat", "İstanbul"),      # bulunma
    ("İzmirden kargo", "İzmir"),              # ayrılma
    ("izmire tasinma", "İzmir"),              # yönelme
    ("sinopta otel", "Sinop"),                # d -> t sertleşmesi
    ("gaziantepte kebapci", "Gaziantep"),     # d -> t sertleşmesi
    ("usaktan kargo", "Uşak"),                # d -> t sertleşmesi
    ("boluya tur", "Bolu"),                   # ünlü sonrası kaynaştırma -y-
    ("orduyu gezmek", "Ordu"),                # ünlü sonrası kaynaştırma -y-
    ("ankaranin trafigi", "Ankara"),          # ünlü sonrası kaynaştırma -n-
])
def test_suffixed_forms_without_apostrophe_match(keyword, city):
    assert match_city_term(keyword) == city


@pytest.mark.parametrize("keyword", ["sinobu gezmek", "gaziantebi gormek",
                                     "usaga gitmek"])
def test_consonant_softening_is_out_of_v1_scope(keyword):
    """V1 SINIRI (bilinçli): özel adda yumuşama üretilmez, fuzzy tahmin YOK."""
    assert match_city_terms(keyword) == []


def test_city_root_is_never_mistaken_for_a_suffix():
    """`strip_turkish_suffixes` `ordu -> ord`, `bolu -> bol` yapıyordu."""
    assert match_city_term("ordu gezilecek yerler") == "Ordu"
    assert match_city_term("bolu daglari") == "Bolu"


# ── Yanlış pozitif sınırları ────────────────────────────────────────────
@pytest.mark.parametrize("keyword", [
    "vantilatör fiyatları",      # `van` alt-string olarak geçmemeli
    "vanilya aroması",
    "karsilastirma tablosu",     # `kars` alt-string
    "rizeli olmayan bir sey",    # desteklenmeyen türetme
    "implant fiyatları",
])
def test_non_city_text_does_not_match(keyword):
    assert match_city_terms(keyword) == []


@pytest.mark.parametrize("keyword", ["antep fıstığı", "maraş dondurması",
                                     "urfa kebap", "afyon kaymağı"])
def test_unofficial_abbreviations_are_not_aliases(keyword):
    """`Antep`/`Maraş`/`Urfa`/`Afyon` V1'de şehir eşleşmesi ÜRETMEZ."""
    assert match_city_terms(keyword) == []


def test_multi_city_keyword_returns_all_in_reading_order():
    assert match_city_terms("istanbul ankara arası nakliyat") == [
        "İstanbul", "Ankara"]


# ── Muafiyetler ─────────────────────────────────────────────────────────
def test_exempt_term_matches_consecutive_tokens_not_substring():
    assert match_exempt_term("gaziantep fıstığı fiyatı",
                             ["gaziantep fıstığı"]) == "gaziantep fıstığı"
    assert match_exempt_term("gaziantep ucuz fıstığı",
                             ["gaziantep fıstığı"]) is None


def test_exempt_term_capital_i_is_bidirectional():
    assert match_exempt_term("istanbul sozlesmesi nedir",
                             ["İstanbul Sözleşmesi"]) is not None
    assert match_exempt_term("İstanbul Sözleşmesi nedir",
                             ["istanbul sozlesmesi"]) is not None


# ── Mod davranışı ───────────────────────────────────────────────────────
def test_mode_none_keeps_everything():
    d = evaluate_keyword("ankara implant", mode=MODE_NONE)
    assert d.is_kept and d.exclude_reason is None


def test_mode_exclude_all_drops_any_city():
    d = evaluate_keyword("ankara implant", mode=MODE_EXCLUDE_ALL)
    assert not d.is_kept
    assert d.exclude_reason == f"{REASON_CITY_FILTER}:Ankara"
    assert d.matched_city == "Ankara"


def test_mode_exclude_all_keeps_cityless_keyword():
    assert evaluate_keyword("implant fiyatları",
                            mode=MODE_EXCLUDE_ALL).is_kept


def test_mode_focus_only_keeps_focus_city_and_drops_others():
    kept = evaluate_keyword("istanbul implant", mode=MODE_FOCUS_ONLY,
                            focus_cities=["İstanbul"])
    dropped = evaluate_keyword("ankara implant", mode=MODE_FOCUS_ONLY,
                               focus_cities=["İstanbul"])
    assert kept.is_kept
    assert not dropped.is_kept
    assert dropped.exclude_reason == f"{REASON_NON_FOCUS_CITY}:Ankara"


def test_mode_focus_only_keeps_cityless_keyword():
    assert evaluate_keyword("implant fiyatları", mode=MODE_FOCUS_ONLY,
                            focus_cities=["İstanbul"]).is_kept


def test_focus_only_multi_city_rule_is_inclusive():
    """Odak şehirlerden biri geçiyorsa, odak dışı şehir de geçse KORUNUR."""
    d = evaluate_keyword("istanbul ankara arası nakliyat",
                         mode=MODE_FOCUS_ONLY, focus_cities=["İstanbul"])
    assert d.is_kept


def test_focus_only_drops_when_no_focus_city_present():
    d = evaluate_keyword("ankara izmir arası nakliyat",
                         mode=MODE_FOCUS_ONLY, focus_cities=["İstanbul"])
    assert not d.is_kept
    # Kanonik sıradaki ilk odak dışı şehir reason suffix'i olur (deterministik).
    assert d.exclude_reason == f"{REASON_NON_FOCUS_CITY}:Ankara"


@pytest.mark.parametrize("mode", [MODE_EXCLUDE_ALL, MODE_FOCUS_ONLY])
def test_exemption_wins_before_city_decision_in_both_active_modes(mode):
    d = evaluate_keyword("gaziantep fıstığı fiyatı", mode=mode,
                         focus_cities=["İstanbul"],
                         exempt_terms=["gaziantep fıstığı"])
    assert d.is_kept
    assert d.matched_exempt_term == "gaziantep fıstığı"
    assert d.exclude_reason is None


@pytest.mark.parametrize("mode", [MODE_EXCLUDE_ALL, MODE_FOCUS_ONLY])
def test_ambiguous_city_is_dropped_without_exemption(mode):
    """Sistem `Batman film mi şehir mi?` diye SEMANTİK karar vermez."""
    assert not evaluate_keyword("batman filmi izle", mode=mode,
                                focus_cities=["İstanbul"]).is_kept
    assert evaluate_keyword("batman filmi izle", mode=mode,
                            focus_cities=["İstanbul"],
                            exempt_terms=["batman filmi"]).is_kept


def test_invalid_mode_is_rejected():
    with pytest.raises(ValueError):
        evaluate_keyword("istanbul", mode="hepsi")


def test_focus_cities_are_canonicalized_before_comparison():
    """Kullanıcı ASCII/küçük harf yazsa da kanonik il ile eşleşir."""
    assert evaluate_keyword("istanbul implant", mode=MODE_FOCUS_ONLY,
                            focus_cities=["istanbul"]).is_kept
    assert evaluate_keyword("izmir implant", mode=MODE_FOCUS_ONLY,
                            focus_cities=["İZMİR"]).is_kept
