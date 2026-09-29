# -*- coding: utf-8 -*-
"""Lokasyon politikası — şehir terimi filtresi (deterministik, AI YOK).

Üç mod (plan_v3_lokasyon_filtresi.md §2):
  - `none`         : şehir filtresi yok
  - `exclude_all`  : şehir adı içeren keyword elenir
  - `focus_only`   : yalnız odak şehirleri içeren + şehirsiz keyword'ler kalır

Muafiyet (`location_exempt_terms`) HER İKİ etkin modda da şehir kararından
ÖNCE çalışır: `gaziantep fıstığı` gibi ürün terimleri şehir adı içerse de
korunur.

## Türkçe normalizasyon — büyük `İ` tuzağı

Python'da `'İ'.lower()` → `i + U+0307` üretir; birleşen nokta noktalama
temizliğinde boşluğa dönüşür ve `İstanbul` → `i stanbul` (iki token) olurdu.
Paylaşılan `normalize_turkish` bunu artık kendisi katlar (plan §12 düzeltmesi,
28.09.2026; etki ölçümü: 50 workspace'te yalnız 2 rakip terimi, mevcut
anahtar kelime kararlarında fark 0). Lokasyon modülünün dar ön katlaması
(`İ -> i`, `I -> ı`, düzeltme işaretleri) savunma katmanı olarak kalır.

## Ek üretimi — ileri yönlü, kökü korur

Keyword'den geriye doğru ek KIRPILMAZ (`strip_turkish_suffixes` `ordu -> ord`,
`bolu -> bol` yapar). Bunun yerine kanonik şehir adından sınırlı bir varyant
tablosu ÜRETİLİR. Kesme işaretli biçimler (`Ankara'da`) zaten noktalama
normalizasyonunda ayrı token'a bölündüğü için yalın şehir token'ıyla eşleşir;
tablo yalnız kesmesiz biçimler (`ankarada`) içindir.

V1 KAPSAM DIŞI: özel adlarda ünsüz yumuşaması (`p->b`, `k->ğ`, `t->d`).
`sinobu`, `gaziantebi`, `uşağa` gibi yazımlar eşleşmez. Bu bilinçlidir ve
fuzzy tahminle genişletilmez (plan §3).
"""
from __future__ import annotations

import hashlib
from functools import lru_cache
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from app.core.site_analyzer.turkish_normalizer import normalize_turkish

# Sözlük kimliği — içerik veya üretim kuralı değişirse ARTIRILMALIDIR.
CITY_LEXICON_VERSION = "tr-provinces-v1"

# Filtre modları
MODE_NONE = "none"
MODE_EXCLUDE_ALL = "exclude_all"
MODE_FOCUS_ONLY = "focus_only"
VALID_MODES = (MODE_NONE, MODE_EXCLUDE_ALL, MODE_FOCUS_ONLY)

# Eleme nedenleri
REASON_CITY_FILTER = "LOCATION_CITY_FILTER"
REASON_NON_FOCUS_CITY = "LOCATION_NON_FOCUS_CITY"

MAX_FOCUS_CITIES = 20
# Muafiyet sınırları — mevcut profil listesi konvansiyonuyla uyumlu
# (bkz. competitor `term: max_length=200`).
MAX_EXEMPT_TERMS = 50
MAX_EXEMPT_TERM_LENGTH = 200

# 81 resmi il. Halk arasındaki kısaltmalar (Antep/Maraş/Urfa) BİLİNÇLİ olarak
# alias yapılmaz: `antep fıstığı`, `maraş dondurması` gibi ürün terimlerinde
# gereksiz eleme alanı büyür (plan §3).
TURKISH_PROVINCES: Tuple[str, ...] = (
    "Adana", "Adıyaman", "Afyonkarahisar", "Ağrı", "Aksaray", "Amasya",
    "Ankara", "Antalya", "Ardahan", "Artvin", "Aydın", "Balıkesir", "Bartın",
    "Batman", "Bayburt", "Bilecik", "Bingöl", "Bitlis", "Bolu", "Burdur",
    "Bursa", "Çanakkale", "Çankırı", "Çorum", "Denizli", "Diyarbakır",
    "Düzce", "Edirne", "Elazığ", "Erzincan", "Erzurum", "Eskişehir",
    "Gaziantep", "Giresun", "Gümüşhane", "Hakkâri", "Hatay", "Iğdır",
    "Isparta", "İstanbul", "İzmir", "Kahramanmaraş", "Karabük", "Karaman",
    "Kars", "Kastamonu", "Kayseri", "Kırıkkale", "Kırklareli", "Kırşehir",
    "Kilis", "Kocaeli", "Konya", "Kütahya", "Malatya", "Manisa", "Mardin",
    "Mersin", "Muğla", "Muş", "Nevşehir", "Niğde", "Ordu", "Osmaniye",
    "Rize", "Sakarya", "Samsun", "Siirt", "Sinop", "Sivas", "Şanlıurfa",
    "Şırnak", "Tekirdağ", "Tokat", "Trabzon", "Tunceli", "Uşak", "Van",
    "Yalova", "Yozgat", "Zonguldak",
)

# Başka anlamı/ürün kullanımı da olan il adları. Sistem semantik tahmin
# YAPMAZ; bunlar yalnız arayüzün kullanıcıyı uyarması ve muafiyet önerisi
# için işaretlidir (plan §3).
AMBIGUOUS_PROVINCES = frozenset({
    "Ordu", "Van", "Batman", "Rize", "Uşak", "Mersin", "Aksaray", "Osmaniye",
})

# ── Türkçe normalizasyon ────────────────────────────────────────────────
# `İ`/`I` katlaması paylaşılan normalizer'dan ÖNCE; düzeltme işaretli
# ünlüler de burada sadeleşir (Hakkâri -> hakkari).
_PRE_FOLD = str.maketrans({
    "İ": "i", "I": "ı",
    "Â": "A", "â": "a", "Î": "İ", "î": "i", "Û": "U", "û": "u",
})


def normalize_location_text(text: str) -> str:
    """Lokasyon eşleşmesinin TEK normalizasyon hattı.

    Şehir adları, keyword metni ve muafiyet terimleri bu fonksiyondan geçer.
    `match_term`'e ham metin gönderilerek bu adım ATLANAMAZ.
    """
    if not text:
        return ""
    return normalize_turkish(str(text).translate(_PRE_FOLD))


def _tokens(text: str) -> List[str]:
    return [t for t in normalize_location_text(text).split() if t]


# ── Ek varyantı üretimi (ileri yönlü) ───────────────────────────────────
_BACK_VOWELS = "aıou"
_FRONT_VOWELS = "eiöü"
_VOWELS = _BACK_VOWELS + _FRONT_VOWELS
# "fıstıkçı şahap" — sert ünsüzler; bulunma/ayrılma ekinde d -> t
_HARD_CONSONANTS = "fstkçşhp"


def _last_vowel(word: str) -> Optional[str]:
    for ch in reversed(word.lower()):
        if ch in _VOWELS:
            return ch
    return None


def _two_way(word: str) -> str:
    """Büyük ünlü uyumu: a / e."""
    v = _last_vowel(word)
    return "a" if (v is None or v in _BACK_VOWELS) else "e"


def _four_way(word: str) -> str:
    """Küçük ünlü uyumu: ı / i / u / ü."""
    v = _last_vowel(word)
    if v in ("a", "ı"):
        return "ı"
    if v in ("e", "i"):
        return "i"
    if v in ("o", "u"):
        return "u"
    if v in ("ö", "ü"):
        return "ü"
    return "ı"


def suffix_variants(name: str) -> List[str]:
    """Kanonik il adından desteklenen kesmesiz hâl eki biçimleri.

    Kapsam: bulunma, ayrılma, yönelme, belirtme, ilgi. Ünsüz YUMUŞAMASI
    uygulanmaz (V1 sınırı — modül docstring'ine bakın).
    """
    # DİKKAT: katlama `.lower()`'dan ÖNCE. Aksi halde `"İzmir".lower()`
    # `i + U+0307` üretir ve türetilen varyantlar normalize edilince
    # parçalanır (`izmirden` hiç eşleşmezdi).
    low = name.translate(_PRE_FOLD).lower()
    a_e = _two_way(low)
    i4 = _four_way(low)
    ends_vowel = bool(low) and low[-1] in _VOWELS
    hard = bool(low) and low[-1] in _HARD_CONSONANTS
    d = "t" if hard else "d"

    out = [
        f"{low}{d}{a_e}",                 # bulunma:  ankarada / sinopta
        f"{low}{d}{a_e}n",                # ayrılma:  ankaradan / sinoptan
        f"{low}{'y' if ends_vowel else ''}{a_e}",          # yönelme
        f"{low}{'y' if ends_vowel else ''}{i4}",           # belirtme
        f"{low}{'n' if ends_vowel else ''}{i4}n",          # ilgi
    ]
    return out


def _build_lexicon() -> Dict[str, str]:
    """normalize edilmiş token -> kanonik il adı."""
    table: Dict[str, str] = {}
    for province in TURKISH_PROVINCES:
        forms = [province] + suffix_variants(province)
        for form in forms:
            key = normalize_location_text(form)
            # Yalın ad çakışmada KAZANIR (ör. bir ilin eki başka ilin adına
            # eşitlenirse belirsizlik sessizce ilk gelene gitmesin).
            if key and (key not in table or form == province):
                table[key] = province
    return table


CITY_LEXICON: Dict[str, str] = _build_lexicon()


@lru_cache(maxsize=1)
def lexicon_fingerprint() -> str:
    """Sözlüğün deterministik kimliği (manifest mührü + enforcement kimliği).

    Her politika karşılaştırmasında çağrıldığı için sonucu önbelleklenir;
    sözlük modül yüklenirken BİR KEZ kurulur, çalışma anında değişmez.
    """
    payload = "\n".join(
        f"{key}\t{CITY_LEXICON[key]}" for key in sorted(CITY_LEXICON)
    )
    stamped = f"{CITY_LEXICON_VERSION}\n{payload}"
    return hashlib.sha256(stamped.encode("utf-8")).hexdigest()


# ── Eşleşme ─────────────────────────────────────────────────────────────
def canonical_city(value: str) -> Optional[str]:
    """Kullanıcı girdisini kanonik il adına çevirir; desteklenmiyorsa None."""
    key = normalize_location_text(value)
    return CITY_LEXICON.get(key) if key else None


def match_city_terms(keyword: str) -> List[str]:
    """Keyword içinde geçen TÜM kanonik il adları (görülme sırasıyla, tekil)."""
    found: List[str] = []
    for token in _tokens(keyword):
        city = CITY_LEXICON.get(token)
        if city and city not in found:
            found.append(city)
    return found


def match_city_term(keyword: str) -> Optional[str]:
    """İlk kanonik il adı (None = şehir içermiyor)."""
    cities = match_city_terms(keyword)
    return cities[0] if cities else None


def match_exempt_term(keyword: str, exempt_terms: Sequence[str]) -> Optional[str]:
    """Ardışık token dizisi eşleşmesi — substring DEĞİL.

    `match_term` ile aynı semantik; fark, normalizasyonun lokasyon hattından
    geçmesidir (büyük `İ` içeren muafiyet terimleri de çalışsın diye).
    """
    if not keyword or not exempt_terms:
        return None
    text_tokens = _tokens(keyword)
    if not text_tokens:
        return None
    for term in exempt_terms:
        term_tokens = _tokens(term)
        if not term_tokens or len(term_tokens) > len(text_tokens):
            continue
        span = len(term_tokens)
        for i in range(len(text_tokens) - span + 1):
            if text_tokens[i:i + span] == term_tokens:
                return term
    return None


# ── Karar ───────────────────────────────────────────────────────────────
class LocationDecision(tuple):
    """(is_kept, exclude_reason, matched_city, matched_exempt_term)."""

    __slots__ = ()

    def __new__(cls, is_kept, exclude_reason=None, matched_city=None,
                matched_exempt_term=None):
        return super().__new__(
            cls, (is_kept, exclude_reason, matched_city, matched_exempt_term))

    is_kept = property(lambda self: self[0])
    exclude_reason = property(lambda self: self[1])
    matched_city = property(lambda self: self[2])
    matched_exempt_term = property(lambda self: self[3])


def evaluate_keyword(
    keyword: str,
    *,
    mode: str,
    focus_cities: Iterable[str] = (),
    exempt_terms: Sequence[str] = (),
) -> LocationDecision:
    """Tek keyword için deterministik lokasyon kararı.

    `focus_only` modunda KAPSAYICI kural: odak şehirlerden en az biri
    geçiyorsa, başka odak dışı şehirler de geçse keyword KORUNUR
    (ör. odak İstanbul iken `istanbul ankara arası nakliyat` kalır).
    """
    if mode not in VALID_MODES:
        raise ValueError(f"gecersiz location_filter_mode: {mode!r}")
    if mode == MODE_NONE:
        return LocationDecision(True)

    exempt = match_exempt_term(keyword, exempt_terms)
    if exempt is not None:
        return LocationDecision(True, matched_exempt_term=exempt)

    cities = match_city_terms(keyword)
    if not cities:
        return LocationDecision(True)

    if mode == MODE_EXCLUDE_ALL:
        city = cities[0]
        return LocationDecision(
            False, f"{REASON_CITY_FILTER}:{city}"[:60], city)

    focus = {c for c in (canonical_city(x) for x in focus_cities) if c}
    if focus & set(cities):
        return LocationDecision(True, matched_city=cities[0])
    # Kanonik sıradaki ilk odak dışı şehir reason suffix'i olur.
    city = sorted(cities)[0]
    return LocationDecision(
        False, f"{REASON_NON_FOCUS_CITY}:{city}"[:60], city)


# ── Profil okuma / kanonik kimlik ───────────────────────────────────────
def read_settings(profile_data) -> Tuple[str, List[str], List[str]]:
    """profile_data'dan (mode, focus_cities, exempt_terms) — geriye uyumlu.

    Alanı olmayan eski profiller `none` + boş listeler olarak okunur; mevcut
    workspace davranışı kendiliğinden DEĞİŞMEZ.
    """
    data = profile_data if isinstance(profile_data, dict) else {}
    mode = str(data.get("location_filter_mode") or MODE_NONE).strip().lower()
    if mode not in VALID_MODES:
        mode = MODE_NONE
    focus = [str(x) for x in (data.get("focus_cities") or []) if str(x).strip()]
    exempt = [str(x) for x in (data.get("location_exempt_terms") or [])
              if str(x).strip()]
    return mode, focus, exempt


def enforcement_identity(profile_data) -> Tuple:
    """SONUCU ETKİLEYEN lokasyon ayarlarının kanonik kimliği.

    `focus_cities` yalnız `focus_only` modunda kimliğe girer: diğer modlarda
    saklanır ama seçimi etkilemez, dolayısıyla değişmesi havuzu bayatlatmaz
    (plan §5.2). Muafiyetler her iki etkin modda da enforcement'ın parçasıdır.
    """
    mode, focus, exempt = read_settings(profile_data)
    if mode == MODE_NONE:
        return (MODE_NONE,)
    # Sözlük KİMLİĞİ (yalnız sürüm etiketi değil) kimliğe girer: sözlük
    # içeriği değişip CITY_LEXICON_VERSION yanlışlıkla artırılmazsa bile
    # mühür değişmeli, eski havuz sessizce "güncel" görünmemeli (plan §5.4).
    lexicon_key = (CITY_LEXICON_VERSION, lexicon_fingerprint())
    exempt_key = tuple(sorted(
        {normalize_location_text(t) for t in exempt} - {""}))
    if mode == MODE_EXCLUDE_ALL:
        return (MODE_EXCLUDE_ALL, exempt_key, lexicon_key)
    focus_key = tuple(sorted(
        {c for c in (canonical_city(x) for x in focus) if c}))
    return (MODE_FOCUS_ONLY, focus_key, exempt_key, lexicon_key)


def policy_snapshot(profile_data) -> Dict[str, object]:
    """Run manifestine mühürlenecek lokasyon politikası (Faz 3).

    `focus_cities` denetim için snapshot'ta DAİMA bulunur; enforcement
    fingerprint'ine ise yalnız sonucu etkilediği modda girer.
    """
    mode, focus, exempt = read_settings(profile_data)
    identity = enforcement_identity(profile_data)
    payload = "|".join(repr(part) for part in identity)
    return {
        "mode": mode,
        "focus_cities": [c for c in (canonical_city(x) for x in focus) if c],
        "exempt_terms": sorted({normalize_location_text(t) for t in exempt}
                               - {""}),
        "city_lexicon_version": CITY_LEXICON_VERSION,
        "city_lexicon_sha256": lexicon_fingerprint(),
        "enforcement_fingerprint": hashlib.sha256(
            payload.encode("utf-8")).hexdigest(),
    }
