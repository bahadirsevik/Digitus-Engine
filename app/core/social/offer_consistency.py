# -*- coding: utf-8 -*-
"""Kampanya içeriği teklif tutarlılık kontrolü (plan_sosyal_kampanya_akisi.md §5).

Saf modül: DB, ORM, HTTP, AI yok. Yalnız TEKLİF BAĞLAMINDAKİ ifadeler değerlendirilir;
çıplak ürün fiyatı ("Ürün 1.000 TL") veya ürün bilgisi ("yüzde yüz pamuk") teklif değildir.
Dayanak: kullanıcının verdiği `value` + `conditions` + onaylı `product_facts`.

shortcut: kalıplar sabit Türkçe listedir ("yarı fiyatına", "iki yüz lira" gibi dolaylı/bileşik
yazımlar kalıp dışı kalır); kaçan ifade çıkarsa listeyi büyüt, anlamsal denetim hedeflenmiyor.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Optional, Sequence

from app.core.social.campaign_spec import _MONTHS_TR as MONTHS_TR, format_ends_tr
from app.generators.ads.validators import _canonical_number
from app.generators.claim_lexicon import fold

@dataclass(frozen=True)
class OfferCheck:
    hard: tuple[str, ...]   # "kod:ifade" — onarıma girer; sürerse içerik kaydedilmez
    scope_review: bool      # teklif anılıyor, kapsam metni geçmiyor -> uyarıyla kabul
    gift_review: bool = False  # hediye anılıyor, onaylı hediye tanımı kısmen geçiyor -> uyarıyla kabul


# ---- sayı (fold edilmiş metin: küçük harf, ı -> i) ----
_UNITS = {"bir": 1, "iki": 2, "üç": 3, "dört": 4, "beş": 5, "alti": 6, "yedi": 7, "sekiz": 8, "dokuz": 9}
_TENS = {"on": 10, "yirmi": 20, "otuz": 30, "kirk": 40, "elli": 50, "altmiş": 60, "yetmiş": 70,
         "seksen": 80, "doksan": 90}
_WORD_VALUES = {**_UNITS, **_TENS, "yüz": 100, "bin": 1000}
_U, _T = "|".join(_UNITS), "|".join(_TENS)
_W = rf"\b(?:(?:{_T})(?:\s+(?:{_U}))?|{_U}|yüz|bin)\b"
_D = r"\d[\d.,]*\d(?:\s*bin\b)?|\d(?:\s*bin\b)?"
_N = rf"(?:{_D}|{_W})"


def _num(raw: str) -> str:
    raw = raw.strip()
    if raw[0].isdigit():
        return _canonical_number(raw)
    return str(sum(_WORD_VALUES[w] for w in raw.split()))


# ---- kalıplar ----
_PCT_RX = re.compile(rf"%\s*(\d+(?:[.,]\d+)?)|(\d+(?:[.,]\d+)?)\s*%|yüzde\s*({_N})")
_MONEY_RX = re.compile(rf"₺\s*({_D})|({_N})\s*(?:₺|tl(?:'\w+)?(?!\w)|lira\w*)")
_BUNDLE_RX = re.compile(
    rf"({_N})\s*(?:adet\s+|tane\s+)?al(?P<ana>ana)?(?=\s|\d)\s*({_N})\s*(?:adet\s+|tane\s+)?"
    rf"(?:öde|bedava|hediye|ücretsiz)"
)
_SHIP_RX = re.compile(r"(?:ücretsiz|bedava)\s+kargo|kargo\s+(?:ücretsiz|bedava|bizden)|kargo\s+ücreti\s+yok")
_GIFT_RX = re.compile(r"\bhediye(?!lik)\w*|\bbedava\b")
_DISCOUNT_WORD_RX = re.compile(r"\bindirim\w*|\biskonto\w*")
_NEAR_OFFER_RX = re.compile(r"indirim|iskonto|tasarruf|ucuz|firsat|kampanya|kazanç|avantaj")
_NEAR_AMOUNT_RX = re.compile(r"indirim|iskonto|hediye|kupon|çek\b")
_THRESHOLD_AFTER_RX = re.compile(
    r"\s*(?:ve\s+)?(?:üzer|üst|fazla)\w*|\s*(?:alişveriş|sepet|sipariş)\w*(?!\s+indirim)"
)
_THRESHOLD_BEFORE_RX = re.compile(r"(?:en az|minimum|min\.?|alt limit:?)\s*$")
# Anahtar kelimede "hediye" geçiyorsa (ürün dili) yalnız promosyon bağlamındaki kullanım tekliftir.
_GIFT_PROMO_RX = re.compile(r"sipariş|alana|alişveriş|yaninda|bizden|ücretsiz|kazan|çeki|kupon|kampanya")
_GIFT_STOP = ("hediye", "bedava", "ücretsiz")
_BARE_AFTER_RX = re.compile(r"(?:'\w+)?\s*(?:$|[!.,;:)?\n])")
_BREAK_RX = re.compile(r"[.!?\n]")
_URGENCY_RX = re.compile(r"bugün son|son gün(?!ler)|son saat")
_GENERAL_SCOPE_RX = re.compile(
    r"\b(?:tüm|bütün)\s+(?:ürün|koleksiyon|mağaza|site|kategori|sepet|alişveriş|sipariş)\w*"
    r"|\bher\s*şeyde\b|\bhepsinde\b"
)
_GENERAL_APPLIES_RX = re.compile(r"\b(?:tüm|bütün|hepsi|her\s*şey)")
# Kupon kodu: BÜYÜK harf + rakam karışık, >=4 karakter (YAZ20); orijinal (fold edilmemiş) metinde aranır.
_CODE_TOKEN = r"(?=[A-ZÇĞİÖŞÜ0-9]*\d)(?=[A-ZÇĞİÖŞÜ0-9]*[A-ZÇĞİÖŞÜ])[A-ZÇĞİÖŞÜ0-9]{4,}"
_CODE_ANY_RX = re.compile(rf"\b{_CODE_TOKEN}\b")
_CODE_NEAR_RX = re.compile(
    rf"(?:[Kk]od|KOD|[Kk]upon|KUPON)\w*\s*[:\-]?\s*({_CODE_TOKEN})\b|\b({_CODE_TOKEN})\s+(?:[Kk]od|KOD|[Kk]upon)"
)


def _after(text: str, pos: int, width: int) -> str:
    window = text[pos:pos + width]
    m = _BREAK_RX.search(window)
    return window[:m.start()] if m else window


def _before(text: str, pos: int, width: int) -> str:
    return _BREAK_RX.split(text[max(0, pos - width):pos])[-1]


def _percents(text: str, *, strict: bool) -> list[tuple[str, str]]:
    """(değer, ifade). strict: yalnız teklif bağlamı (yakında indirim sözcüğü veya çıplak oran)."""
    out = []
    for m in _PCT_RX.finditer(text):
        if strict and not (
            _NEAR_OFFER_RX.search(_after(text, m.end(), 30))
            or _NEAR_OFFER_RX.search(_before(text, m.start(), 20))
            or _BARE_AFTER_RX.match(text, m.end())
        ):
            continue  # "%100 pamuk", "yüzde yüz doğal": ürün bilgisi
        out.append((_num(m.group(1) or m.group(2) or m.group(3)), m.group(0)))
    return out


def _money(text: str) -> list[tuple[str, str, str]]:
    """(rol, değer, ifade); rol: amount (indirim tutarı) | threshold (eşik) | price (çıplak fiyat)."""
    out = []
    for m in _MONEY_RX.finditer(text):
        rest = text[m.end():]
        if _THRESHOLD_AFTER_RX.match(rest) or _THRESHOLD_BEFORE_RX.search(_before(text, m.start(), 12)):
            role = "threshold"
        elif _NEAR_AMOUNT_RX.search(re.split(r"\d", _after(text, m.end(), 25))[0]) or re.search(
            r"indirim\w*\s*:?\s*$", _before(text, m.start(), 20)
        ):
            role = "amount"
        else:
            role = "price"
        out.append((role, _num(m.group(1) or m.group(2)), m.group(0)))
    return out


def _bundles(text: str) -> list[tuple[tuple[str, str, str], str]]:
    return [
        (("alana" if m.group("ana") else "al", _num(m.group(1)), _num(m.group(3))), m.group(0))
        for m in _BUNDLE_RX.finditer(text)
    ]


def _date_state(text: str, ends: str) -> str:
    """ok | wrong_year | missing. Yıl yazılmışsa o da doğrulanır ("30 Kasım 2027" yanlış)."""
    d = date.fromisoformat(ends)
    month = fold(MONTHS_TR[d.month - 1])
    matches = list(re.finditer(
        rf"\b0?{d.day}\s+{month}\w*(?:\s+(\d{{4}}))?|\b0?{d.day}[./]0?{d.month}(?:[./](\d{{4}}))?\b", text))
    if not matches:
        return "missing"
    years = [m.group(1) or m.group(2) for m in matches]
    return "ok" if all(y is None or y == str(d.year) for y in years) else "wrong_year"


def _stem_in(token: str, text: str) -> bool:
    # Ek almış biçimler ("paketinde", "serumu") için kök-önek eşleşmesi
    return bool(re.search(rf"\b{re.escape(token[:max(3, len(token) - 1)])}", text))


def check_offer_consistency(
    texts: Sequence[str],
    offer_terms: dict[str, Any],
    *,
    product_facts: Optional[str] = None,
    exempt: str = "",
) -> OfferCheck:
    """İçeriğin görünür metinlerini kullanıcının teklif koşullarıyla karşılaştırır.

    exempt: anahtar kelime gibi ürün dili sayılacak metin ("hediye fikirleri" kelimesi
    "hediye" sözcüğünü teklif yapmaz).
    """
    offer_type = offer_terms["type"]
    no_offer = offer_type == "none"
    raw = "\n".join(t for t in texts if isinstance(t, str))
    text = fold(raw)
    value_text = fold(offer_terms.get("value") or "")
    conditions_text = fold(offer_terms.get("conditions") or "")
    # Teklifsiz devamda profil bilgisi dayanak DEĞİLDİR: profildeki "%20 indirim" içeriğe teklif açmaz.
    facts_raw = "" if no_offer else (product_facts or "")
    basis_raw = " ".join(filter(None, [offer_terms.get("value"), offer_terms.get("conditions"), facts_raw]))
    basis = fold(basis_raw)
    exempt_text = fold(exempt)
    lexical = fold(f"{basis_raw} {offer_terms.get('applies_to') or ''}")
    violation_code = "offer_in_no_offer" if no_offer else "offer_not_in_terms"

    basis_pcts = {v for v, _ in _percents(basis, strict=False)}
    # Roller ayrı tutulur: indirim tutarı eşiğin, eşik tutarın yerine geçemez. `value`daki çıplak
    # tutar indirim tutarı, `conditions`taki çıplak tutar eşik sayılır; profildeki çıplak fiyat dayanak değil.
    basis_amounts: set[str] = set()
    basis_thresholds: set[str] = set()
    for source, bare_role in ((value_text, "amount"), (conditions_text, "threshold"), (fold(facts_raw), None)):
        for role, value, _ in _money(source):
            role = bare_role if role == "price" else role
            if role == "amount":
                basis_amounts.add(value)
            elif role == "threshold":
                basis_thresholds.add(value)
    required_thresholds = {v: span for role, v, span in _money(conditions_text) if role != "amount"}
    basis_bundles = {k for k, _ in _bundles(basis)}
    basis_codes = {c.upper() for c in _CODE_ANY_RX.findall(basis_raw)}

    hard: list[str] = []
    mentions = False

    for value, span in _percents(text, strict=True):
        mentions = True
        if value not in basis_pcts:
            hard.append(f"{violation_code}:{span}")
    for role, value, span in _money(text):
        if role == "price":
            continue
        mentions = mentions or role == "amount"
        if value not in (basis_amounts if role == "amount" else basis_thresholds):
            hard.append(f"{violation_code}:{span}")
    for key, span in _bundles(text):
        mentions = True
        if key not in basis_bundles:
            hard.append(f"{violation_code}:{span}")
    ship = _SHIP_RX.search(text)
    if ship:
        mentions = True
        if (no_offer or offer_type != "free_shipping") and not _SHIP_RX.search(basis):
            hard.append(f"{violation_code}:{ship.group(0)}")
    gift_review = False
    no_ship_text = _SHIP_RX.sub(" ", text)
    for gift in _GIFT_RX.finditer(no_ship_text):
        word = "bedava" if gift.group(0) == "bedava" else "hediye"
        if word in exempt_text and not (
            _GIFT_PROMO_RX.search(_before(no_ship_text, gift.start(), 25))
            or _GIFT_PROMO_RX.search(_after(no_ship_text, gift.end(), 25))
        ):
            continue  # anahtar kelimenin ürün dili ("hediye fikirleri"); promosyon değil
        mentions = True
        if word not in lexical and (no_offer or not (offer_type == "gift" or word == "bedava")):
            hard.append(f"{violation_code}:{gift.group(0)}")
        elif offer_type == "gift":
            # Onaylanan hediyenin kendisi korunur: hiç geçmiyorsa başka bir hediye vaat edilmiş demektir
            tokens = [t for t in re.findall(r"\w+", value_text) if len(t) >= 3 and not t.startswith(_GIFT_STOP)]
            found = [t for t in tokens if _stem_in(t, text)]
            if tokens and not found:
                hard.append(f"{violation_code}:{gift.group(0)}")
            elif len(found) < len(tokens):
                gift_review = True
    for m in _CODE_NEAR_RX.finditer(raw):
        code = (m.group(1) or m.group(2)).upper()
        if code not in basis_codes:
            hard.append(f"{violation_code}:{code}")
    if no_offer:
        # Muafiyet yalnız anahtar kelime İFADESİNİN kendisidir ("indirimli ayakkabı"); onun dışında
        # geçen her indirim sözcüğü ("Sezon indirimi başladı") yeni promosyondur.
        outside = re.sub(rf"{re.escape(exempt_text)}\w*", " ", text) if exempt_text.strip() else text
        word = _DISCOUNT_WORD_RX.search(outside)
        if word:
            hard.append(f"{violation_code}:{word.group(0)}")

    urgency = _URGENCY_RX.search(text)
    if urgency:
        hard.append(f"offer_relative_urgency:{urgency.group(0)}")

    scope_review = False
    if mentions and not no_offer:
        conditions = offer_terms.get("conditions") or ""
        content_thresholds = {v for role, v, _ in _money(text) if role == "threshold"}
        for value, span in required_thresholds.items():
            if value not in content_thresholds:
                hard.append(f"offer_condition_missing:{span}")
        upper_raw = raw.upper()
        for code in {c.upper() for c in _CODE_ANY_RX.findall(conditions)}:
            if code not in upper_raw:
                hard.append(f"offer_condition_missing:{code}")
        ends = offer_terms.get("ends")
        date_state = _date_state(text, ends) if ends else "ok"
        if date_state != "ok":
            hard.append(f"offer_end_date_{date_state}:{format_ends_tr(ends)}")

        applies_to = fold(offer_terms.get("applies_to") or "")
        general = _GENERAL_SCOPE_RX.search(text)
        if general and not _GENERAL_APPLIES_RX.search(applies_to):
            hard.append(f"offer_scope_conflict:{general.group(0)}")
        elif not general:
            tokens = [t for t in re.findall(r"\w+", applies_to) if len(t) >= 3]
            scope_review = not all(_stem_in(t, text) for t in tokens)

    return OfferCheck(hard=tuple(dict.fromkeys(hard)), scope_review=scope_review, gift_review=gift_review)
