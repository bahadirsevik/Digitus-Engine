# -*- coding: utf-8 -*-
"""Teklif tutarlılık kontrolü — sabit Türkçe test kümesi (plan §5, §10c-2; kabul testleri 3-5)."""
from __future__ import annotations

import pytest

from app.core.social.offer_consistency import check_offer_consistency, format_ends_tr


def _terms(**over) -> dict:
    base = {"type": "percent_discount", "value": "%20", "applies_to": "Başlangıç paketi",
            "conditions": None, "ends": None}
    base.update(over)
    return base


NONE = {"type": "none", "value": None, "applies_to": None, "conditions": None, "ends": None}
SCOPE = "Başlangıç paketinde "  # kapsam metni: scope_review gürültüsünü kapatır


def _hard(text: str, terms: dict, **kw) -> tuple[str, ...]:
    return check_offer_consistency([text], terms, **kw).hard


# ---------- doğru metinler: temiz ----------

@pytest.mark.parametrize("text", [
    "Başlangıç paketinde %20 indirim seni bekliyor.",
    "Başlangıç paketinde %20'lik indirim fırsatı.",
    "Başlangıç paketi şimdi %20 indirimli!",
    "Başlangıç paketinde yüzde 20 indirim.",
    "Başlangıç paketinde yüzde yirmi indirim.",
    "Başlangıç paketinde indirim oranı: %20.",
    "Başlangıç paketi %100 pamuk astarlı; pakette %20 indirim var.",   # ürün yüzdesi teklif değil
    "Yüzde yüz pamuk. Başlangıç paketinde %20 indirim.",
    "iPhone 15 kılıfı da Başlangıç paketinde, %20 indirimle.",
    "Başlangıç paketinin fiyatı 1.000 TL; şimdi %20 indirimli.",        # çıplak fiyat
])
def test_matching_offer_is_clean(text):
    res = check_offer_consistency([text], _terms())
    assert res.hard == () and res.scope_review is False


def test_plan_false_positive_example_is_clean():
    # Kabul testi 3: value="100 TL indirim", conditions="500 TL üzeri"
    terms = _terms(type="percent_discount", value="100 TL indirim", conditions="500 TL üzeri")
    text = "Başlangıç paketi: Ürün 1.000 TL. 500 TL üzeri alışverişlerde 100 TL indirim!"
    assert _hard(text, terms) == ()


def test_product_facts_ground_a_standing_offer():
    text = SCOPE + "%20 indirim. Ayrıca ücretsiz kargo her zaman bizden."
    assert _hard(text, _terms()) != ()
    assert _hard(text, _terms(), product_facts="Tüm siparişlerde ücretsiz kargo.") == ()


# ---------- koşulda olmayan teklif ifadesi: sert ----------

@pytest.mark.parametrize("text", [
    SCOPE + "%30 indirim!",
    SCOPE + "%30'luk indirim fırsatı.",
    SCOPE + "yüzde otuz indirim.",
    SCOPE + "yüzde yüz indirim.",                 # Codex: "yüzde yüz indirim" tekliftir
    SCOPE + "sadece bu hafta: %30!",              # çıplak oran
    SCOPE + "%20 indirim ve 100 TL indirim çeki.",
    SCOPE + "%20 indirim, üstüne bin lira indirim.",
    SCOPE + "%20 indirim. 2 al 1 öde!",
    SCOPE + "%20 indirim. İki al bir öde!",
    SCOPE + "%20 indirim, kargo bedava.",
    SCOPE + "%20 indirim ve ücretsiz kargo.",
    SCOPE + "%20 indirim, yanında hediye çanta.",
    SCOPE + "%20 indirim. Kod: BAHAR25",
    SCOPE + "%20 indirim, 250 TL üzeri siparişlerde.",   # uydurma eşik
])
def test_offer_not_in_terms_is_hard(text):
    assert any(h.startswith("offer_not_in_terms:") for h in _hard(text, _terms()))


def test_hashtag_and_other_fields_are_scanned():
    res = check_offer_consistency([SCOPE + "%20 indirim.", "yüzde30indirim"], _terms())
    assert any(h.startswith("offer_not_in_terms:") for h in res.hard)


# ---------- zorunlu koşul düşmüş: sert ----------

def test_dropped_threshold_and_code_are_hard():
    terms = _terms(conditions="500 TL üzeri, kod: YAZ20")
    assert _hard(SCOPE + "%20 indirim, 500 TL üzeri. Kod: YAZ20", terms) == ()
    assert _hard(SCOPE + "%20 indirim! YAZ20 koduyla, 500 TL ve üzeri siparişlerde.", terms) == ()
    missing_code = _hard(SCOPE + "%20 indirim, 500 TL üzeri alışverişlerde.", terms)
    assert missing_code == ("offer_condition_missing:YAZ20",)
    missing_both = _hard(SCOPE + "%20 indirim seni bekliyor.", terms)
    assert set(missing_both) == {"offer_condition_missing:500 tl", "offer_condition_missing:YAZ20"}


def test_condition_not_required_when_offer_is_not_mentioned():
    terms = _terms(conditions="500 TL üzeri, kod: YAZ20")
    assert _hard("Başlangıç paketiyle cilt bakımına nereden başlanır?", terms) == ()


# ---------- teklifsiz devam (type=none): her teklif kalıbı sert ----------

@pytest.mark.parametrize("text", [
    "Sezon indirimi başladı!",
    "İndirimli fiyatları kaçırma.",
    "%10 indirim.",
    "Ücretsiz kargo fırsatı.",
    "Kargo bedava!",
    "2 alana 1 bedava.",
    "Her siparişe hediye.",
    "50 TL indirim kuponu.",
])
def test_no_offer_mode_rejects_any_offer_pattern(text):
    assert any(h.startswith("offer_in_no_offer:") for h in _hard(text, NONE))


@pytest.mark.parametrize("text,exempt", [
    ("Cilt bakımına nereden başlanır? Üç adımda anlatıyoruz.", ""),
    ("Ürün 1.000 TL; %100 pamuk.", ""),
    ("Sevgiliye hediye fikirleri: 5 öneri.", "sevgiliye hediye fikirleri"),   # anahtar kelime ürün dili
])
def test_no_offer_mode_keeps_plain_content(text, exempt):
    assert _hard(text, NONE, exempt=exempt) == ()


# ---------- göreli aciliyet: tarihten bağımsız sert ----------

@pytest.mark.parametrize("text", ["Bugün son!", "Son gün, kaçırma.", "Son saatler!"])
def test_relative_urgency_is_always_hard(text):
    with_date = _terms(ends="2026-11-30")
    assert any(h.startswith("offer_relative_urgency:") for h in _hard(SCOPE + "%20 indirim. " + text, with_date))
    assert any(h.startswith("offer_relative_urgency:") for h in _hard(text, NONE))


def test_son_gunlerde_is_not_urgency():
    assert _hard(SCOPE + "son günlerde çok sorulan %20 indirim devam ediyor.", _terms()) == ()


# ---------- bitiş tarihi: açık tarih zorunlu ----------

def test_end_date_must_be_written_explicitly():
    terms = _terms(ends="2026-11-30")
    assert format_ends_tr("2026-11-30") == "30 Kasım 2026"
    assert _hard(SCOPE + "%20 indirim, 30 Kasım'a kadar.", terms) == ()
    assert _hard(SCOPE + "%20 indirim (30.11'e kadar).", terms) == ()
    assert _hard(SCOPE + "%20 indirim.", terms) == ("offer_end_date_missing:30 Kasım 2026",)


# ---------- kapsam ----------

@pytest.mark.parametrize("text", [
    "Tüm ürünlerde %20 indirim!",
    "Her şeyde %20 indirim.",
    "Bütün koleksiyonda %20 indirim.",
])
def test_scope_conflict_is_hard(text):
    assert any(h.startswith("offer_scope_conflict:") for h in _hard(text, _terms()))


def test_general_scope_allowed_when_applies_to_is_general():
    assert _hard("Tüm ürünlerde %20 indirim!", _terms(applies_to="Tüm ürünler")) == ()


def test_missing_scope_text_is_review_not_hard():
    res = check_offer_consistency(["Şimdi %20 indirim seni bekliyor."], _terms())
    assert res.hard == () and res.scope_review is True
    assert check_offer_consistency(["Tüm cilt tipleri için. Başlangıç paketinde %20 indirim."], _terms()) \
        .scope_review is False


# ---------- diğer türler ----------

def test_bundle_free_shipping_gift_types():
    bundle = _terms(type="bundle", value="2 al 1 öde")
    assert _hard(SCOPE + "2 al 1 öde!", bundle) == ()
    assert _hard(SCOPE + "iki al bir öde!", bundle) == ()
    assert _hard(SCOPE + "3 al 2 öde!", bundle) != ()
    ship = _terms(type="free_shipping", value="ücretsiz kargo", conditions="500 TL üzeri")
    assert _hard(SCOPE + "kargo bedava, 500 TL üzeri siparişlerde.", ship) == ()
    assert _hard(SCOPE + "ücretsiz kargo!", ship) == ("offer_condition_missing:500 tl",)
    gift = _terms(type="gift", value="mini serum hediye")
    assert _hard(SCOPE + "mini serum hediyeli.", gift) == ()
    assert _hard(SCOPE + "mini serum hediye ve %10 indirim.", gift) != ()


# ---------- Codex Faz 3 bulguları: roller, teklifsiz muafiyeti, hediye içeriği, yıl ----------

AMOUNT_TERMS = {"type": "percent_discount", "value": "100 TL indirim", "applies_to": "Başlangıç paketi",
                "conditions": "500 TL üzeri", "ends": None}


@pytest.mark.parametrize("text,expected", [
    # indirim tutarı ile sepet eşiği yer değiştirmiş
    (SCOPE + "500 TL indirim. 100 TL üzeri alışverişlerde.", "offer_not_in_terms:"),
    # eşik yazılmamış: çıplak ürün fiyatı eşik sayılmaz
    (SCOPE + "100 TL indirim. Ürün fiyatı 500 TL.", "offer_condition_missing:500 tl"),
])
def test_amount_and_threshold_roles_are_not_interchangeable(text, expected):
    assert any(h.startswith(expected) for h in _hard(text, AMOUNT_TERMS))


@pytest.mark.parametrize("text", [
    SCOPE + "500 TL üzeri alışverişlerde 100 TL indirim.",
    SCOPE + "100 TL indirim! En az 500 TL sepet tutarında geçerli.",
    SCOPE + "500 TL ve üzeri siparişe 100 TL indirim.",
    SCOPE + "500 TL'lik alışverişe 100 TL indirim.",
])
def test_amount_and_threshold_in_right_roles_are_clean(text):
    assert _hard(text, AMOUNT_TERMS) == ()


def test_no_offer_is_not_opened_by_profile_or_keyword():
    # profil bilgisindeki teklif, "teklifsiz devam" içeriğine teklif açmaz
    assert _hard("Şimdi %20 indirim!", NONE, product_facts="Sitede %20 indirim var.") != ()
    assert _hard("Ücretsiz kargo fırsatı.", NONE, product_facts="Tüm siparişlerde ücretsiz kargo.") != ()
    # anahtar kelimedeki "hediye" ürün dilini korur, yeni promosyonu serbest bırakmaz
    kw = "sevgiliye hediye fikirleri"
    assert _hard("Her siparişe hediye!", NONE, exempt=kw) != ()
    assert _hard("Alışverişinizin yanında hediye bizden.", NONE, exempt=kw) != ()
    assert _hard("Sevgiliye hediye seçerken nelere bakılır?", NONE, exempt=kw) == ()


def test_changed_gift_item_is_hard_and_partial_match_is_review():
    gift = _terms(type="gift", value="mini serum hediye")
    assert _hard(SCOPE + "tam boy krem hediye!", gift) == ("offer_not_in_terms:hediye",)
    exact = check_offer_consistency([SCOPE + "mini serum hediye!"], gift)
    assert exact.hard == () and exact.gift_review is False
    partial = check_offer_consistency([SCOPE + "serum hediye!"], gift)
    assert partial.hard == () and partial.gift_review is True


def test_wrong_year_is_not_the_end_date():
    terms = _terms(ends="2026-11-30")
    assert _hard(SCOPE + "%20 indirim, 30 Kasım 2026 tarihine kadar.", terms) == ()
    assert _hard(SCOPE + "%20 indirim, 30.11.2026'ya kadar.", terms) == ()
    assert _hard(SCOPE + "%20 indirim, 30 Kasım 2027 tarihine kadar.", terms) == \
        ("offer_end_date_wrong_year:30 Kasım 2026",)
    assert _hard(SCOPE + "%20 indirim, 30.11.2027'ye kadar.", terms) == \
        ("offer_end_date_wrong_year:30 Kasım 2026",)


def test_discount_keyword_exempts_only_the_keyword_phrase():
    kw = "indirimli ayakkabi"
    for text in ("Sezon indirimi başladı!", "Tüm ürünlerde indirim var, hemen alın!",
                 "İndirimli ayakkabı modellerinde sezon indirimi!"):
        assert any(h.startswith("offer_in_no_offer:") for h in _hard(text, NONE, exempt=kw)), text
    assert _hard("İndirimli ayakkabı seçerken nelere dikkat edilmeli?", NONE, exempt=kw) == ()
