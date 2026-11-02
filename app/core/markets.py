"""
Pazar (hedef konum + cikti dili) katalogu.

Bir calisma = bir hedef konum (ulke ya da sehir/il) + bir cikti dili (plan_pazar_dil_secimi.md).

Kaynaklar:
- Konum: Google Ads geotargets tablosu
  https://developers.google.com/google-ads/api/data/geotargets
  dosya: https://developers.google.com/static/google-ads/api/data/geo/geotargets-2026-10-06.csv.zip
  dosya tarihi: 2026-10-06 (273.674 satir; Active + katalog ulkeleri + asagidaki tipler suzulunce
  58.503 satir -> app/core/data/geotargets_2026-10-06.csv.gz, 796 KB).
  Uretim: scripts/build_geotargets_catalog.py (tipler CATALOG_TARGET_TYPES; ham tablodaki
  County/Municipality/District/Neighborhood/Postal Code vb. BILEREK disarida: 2. seviye/cok buyuk).
- Dil: https://developers.google.com/google-ads/api/data/codes-formats (Languages bolumu):
  Turkish 1037, Russian 1031, Ukrainian 1036, English 1000, German 1001.

Katalog dosyasi ilk kullanimda bellege alinir (tek sefer). Dosya yoksa ARANDIGI AN
MarketCatalogError firlatilir; sessizce bos liste donulmez. Ulke ve dil verisi dosyaya
bagli degildir (COUNTRIES / LANGUAGES sabit).
"""
import csv
import gzip
import logging
import threading
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

from fastapi import HTTPException

from app.core.keyword_normalize import normalize_keyword

logger = logging.getLogger(__name__)

GEOTARGETS_FILE_DATE = "2026-10-06"
CATALOG_FILENAME = f"geotargets_{GEOTARGETS_FILE_DATE}.csv.gz"
CATALOG_PATH = Path(__file__).resolve().parent / "data" / CATALOG_FILENAME
CATALOG_COLUMNS = ("Criteria ID", "Name", "Canonical Name", "Parent ID", "Country Code", "Target Type")

# Google "Target Type" -> arayuz turu. Listede olmayan tipler katalogdan atilir.
CATALOG_TARGET_TYPES: Dict[str, str] = {
    "Country": "country",
    "City": "city",
    # birinci seviye idari birimler
    "Province": "region",
    "Region": "region",
    "State": "region",
    "Governorate": "region",
    "Prefecture": "region",
    "Department": "region",
    "Autonomous Community": "region",
    "Canton": "region",
    "Union Territory": "region",
    "Territory": "region",
}
_KIND_SUFFIX = {"country": "Ülke", "city": "Şehir", "region": "İl/Bölge"}
_KIND_RANK = {"country": 0, "city": 1, "region": 2}

# (geo_target_id, ISO alpha-2, Turkce ad, ISO numeric) — IDler resmi tablodan; 2000+iso yalniz test saglamasi
_COUNTRY_ROWS = (
    ("2792", "TR", "Türkiye", 792),
    ("2040", "AT", "Avusturya", 40),
    ("2056", "BE", "Belçika", 56),
    ("2100", "BG", "Bulgaristan", 100),
    ("2191", "HR", "Hırvatistan", 191),
    ("2196", "CY", "Kıbrıs", 196),
    ("2203", "CZ", "Çekya", 203),
    ("2208", "DK", "Danimarka", 208),
    ("2233", "EE", "Estonya", 233),
    ("2246", "FI", "Finlandiya", 246),
    ("2250", "FR", "Fransa", 250),
    ("2276", "DE", "Almanya", 276),
    ("2300", "GR", "Yunanistan", 300),
    ("2348", "HU", "Macaristan", 348),
    ("2372", "IE", "İrlanda", 372),
    ("2380", "IT", "İtalya", 380),
    ("2428", "LV", "Letonya", 428),
    ("2440", "LT", "Litvanya", 440),
    ("2442", "LU", "Lüksemburg", 442),
    ("2470", "MT", "Malta", 470),
    ("2528", "NL", "Hollanda", 528),
    ("2616", "PL", "Polonya", 616),
    ("2620", "PT", "Portekiz", 620),
    ("2642", "RO", "Romanya", 642),
    ("2703", "SK", "Slovakya", 703),
    ("2705", "SI", "Slovenya", 705),
    ("2724", "ES", "İspanya", 724),
    ("2752", "SE", "İsveç", 752),
    ("2826", "GB", "Birleşik Krallık", 826),
    ("2840", "US", "Amerika Birleşik Devletleri", 840),
    ("2124", "CA", "Kanada", 124),
    ("2643", "RU", "Rusya", 643),
    ("2804", "UA", "Ukrayna", 804),
    ("2398", "KZ", "Kazakistan", 398),
    ("2031", "AZ", "Azerbaycan", 31),
    ("2268", "GE", "Gürcistan", 268),
    ("2784", "AE", "Birleşik Arap Emirlikleri", 784),
    ("2682", "SA", "Suudi Arabistan", 682),
    ("2634", "QA", "Katar", 634),
    ("2414", "KW", "Kuveyt", 414),
    ("2048", "BH", "Bahreyn", 48),
    ("2512", "OM", "Umman", 512),
    ("2368", "IQ", "Irak", 368),
    ("2818", "EG", "Mısır", 818),
    ("2504", "MA", "Fas", 504),
    ("2376", "IL", "İsrail", 376),
    ("2356", "IN", "Hindistan", 356),
    ("2392", "JP", "Japonya", 392),
    ("2410", "KR", "Güney Kore", 410),
    ("2076", "BR", "Brezilya", 76),
    ("2036", "AU", "Avustralya", 36),
    ("2756", "CH", "İsviçre", 756),
    ("2578", "NO", "Norveç", 578),
    ("2688", "RS", "Sırbistan", 688),
    ("2070", "BA", "Bosna-Hersek", 70),
    ("2807", "MK", "Kuzey Makedonya", 807),
    ("2008", "AL", "Arnavutluk", 8),
    ("2499", "ME", "Karadağ", 499),
    ("2498", "MD", "Moldova", 498),
    ("2112", "BY", "Belarus", 112),
    ("2860", "UZ", "Özbekistan", 860),
    ("2795", "TM", "Türkmenistan", 795),
    ("2417", "KG", "Kırgızistan", 417),
    ("2762", "TJ", "Tacikistan", 762),
)
COUNTRIES: List[Dict[str, Any]] = [
    {"geo_target_id": g, "code": c, "name_tr": n, "iso_numeric": i}
    for g, c, n, i in _COUNTRY_ROWS
]
_COUNTRY_BY_ID = {c["geo_target_id"]: c for c in COUNTRIES}
_COUNTRY_BY_CODE = {c["code"]: c for c in COUNTRIES}
_COUNTRY_ORDER = {c["code"]: i for i, c in enumerate(COUNTRIES)}

LANGUAGES: List[Dict[str, str]] = [
    {"language_id": "1037", "code": "tr", "name_tr": "Türkçe", "name_native": "Türkçe"},
    {"language_id": "1031", "code": "ru", "name_tr": "Rusça", "name_native": "Русский"},
    {"language_id": "1036", "code": "uk", "name_tr": "Ukraynaca", "name_native": "Українська"},
    {"language_id": "1000", "code": "en", "name_tr": "İngilizce", "name_native": "English"},
    {"language_id": "1001", "code": "de", "name_tr": "Almanca", "name_native": "Deutsch"},
]
_LANGUAGE_BY_ID = {x["language_id"]: x for x in LANGUAGES}
# Eski Windows locale kimligi; google_ads.py'deki kopya kaldirildi (tek kaynak burasi)
LANGUAGE_ALIASES = {
    "1055": "1037",  # Windows/Turkish locale -> Google Ads Turkish criterion ID
}

MARKET_NOT_SET_DETAIL = {"code": "MARKET_NOT_SET", "message": "Önce hedef konum ve dil seçin."}


class MarketCatalogError(RuntimeError):
    """Konum katalogu dosyasi okunamadi."""


class LocationRow(NamedTuple):
    geo_target_id: str
    name: str
    canonical: str
    parent_id: str
    country_code: str
    target_type: str
    kind: str
    norm_name: str
    norm_hay: str


_catalog: Optional[Tuple[List[LocationRow], Dict[str, LocationRow]]] = None
_catalog_lock = threading.Lock()


def _load_catalog() -> Tuple[List[LocationRow], Dict[str, LocationRow]]:
    global _catalog
    if _catalog is not None:
        return _catalog
    with _catalog_lock:
        if _catalog is not None:
            return _catalog
        if not CATALOG_PATH.exists():
            logger.error("Konum katalogu bulunamadi: %s", CATALOG_PATH)
            raise MarketCatalogError(f"Konum katalogu bulunamadı: {CATALOG_PATH}")
        rows: List[LocationRow] = []
        with gzip.open(CATALOG_PATH, "rt", encoding="utf-8", newline="") as fh:
            for r in csv.DictReader(fh):
                kind = CATALOG_TARGET_TYPES.get(r["Target Type"])
                if kind is None:
                    continue
                rows.append(LocationRow(
                    r["Criteria ID"], r["Name"], r["Canonical Name"], r["Parent ID"],
                    r["Country Code"], r["Target Type"], kind,
                    normalize_keyword(r["Name"]),
                    normalize_keyword(r["Canonical Name"].replace(",", " ")),
                ))
        _catalog = (rows, {r.geo_target_id: r for r in rows})
        logger.info("Konum katalogu yuklendi: %d satir", len(rows))
        return _catalog


def _clean_geo(geo_id: Any) -> str:
    """'geoTargetConstants/2792' -> '2792'; None -> ''."""
    if geo_id is None:
        return ""
    return str(geo_id).strip().rsplit("/", 1)[-1]


def _clean_language(language_id: Any) -> str:
    if language_id is None:
        return ""
    value = str(language_id).strip().rsplit("/", 1)[-1]
    return LANGUAGE_ALIASES.get(value, value)


clean_geo_id = _clean_geo
clean_language_id = _clean_language


def get_location(geo_id: Any) -> Optional[LocationRow]:
    """Katalog satiri (ulke dahil); yoksa None. Katalog dosyasi yoksa MarketCatalogError."""
    return _load_catalog()[1].get(_clean_geo(geo_id))


def is_valid_location(geo_id: Any) -> bool:
    gid = _clean_geo(geo_id)
    return gid in _COUNTRY_BY_ID or get_location(gid) is not None


def is_valid_language(language_id: Any) -> bool:
    return _clean_language(language_id) in _LANGUAGE_BY_ID


def country_name(geo_id: Any) -> Optional[str]:
    """Ulke ID'si icin ulkenin, konum ID'si icin bagli oldugu ulkenin Turkce adi; bilinmiyorsa None."""
    gid = _clean_geo(geo_id)
    country = _COUNTRY_BY_ID.get(gid)
    if country is None:
        row = get_location(gid)
        country = _COUNTRY_BY_CODE.get(row.country_code) if row else None
    return country["name_tr"] if country else None


def language_name(language_id: Any) -> Optional[str]:
    lang = _LANGUAGE_BY_ID.get(_clean_language(language_id))
    return lang["name_tr"] if lang else None


def _location_base(row: LocationRow) -> str:
    """'Frankfurt am Main, Hessen, Almanya' — bolge adi sehir adindan farkliysa araya girer."""
    country = _COUNTRY_BY_CODE.get(row.country_code)
    country_tr = country["name_tr"] if country else row.country_code
    middle = row.canonical.split(",")[1:-1]
    if middle == [row.name]:  # "Istanbul,Istanbul,Turkiye": bolge adi sehirle ayni -> eklenmez
        middle = []
    return ", ".join([row.name, *middle, country_tr])


def location_short_label(geo_id: Any) -> str:
    """Tür eki OLMADAN konum ("Berlin, Almanya" / "Almanya (DE)"); çalışma adları için."""
    return _location_short(geo_id)


def _location_short(geo_id: Any) -> str:
    gid = _clean_geo(geo_id)
    country = _COUNTRY_BY_ID.get(gid)
    if country:
        return f"{country['name_tr']} ({country['code']})"
    row = get_location(gid)
    if row is None:
        return f"Tanımlanamayan mevcut seçim ({gid})" if gid else "Seçilmedi"
    return _location_base(row)


def location_label(geo_id: Any) -> str:
    """'Almanya (DE) — Ülke' / 'Berlin, Almanya — Şehir' / '<Ad>, <Ülke> — İl/Bölge'."""
    gid = _clean_geo(geo_id)
    country = _COUNTRY_BY_ID.get(gid)
    if country:
        return f"{country['name_tr']} ({country['code']}) — {_KIND_SUFFIX['country']}"
    row = get_location(gid)
    if row is None:
        return f"Tanımlanamayan mevcut seçim ({gid})" if gid else "Seçilmedi"
    return f"{_location_base(row)} — {_KIND_SUFFIX[row.kind]}"


def market_label(ws: Any) -> str:
    """'Berlin, Almanya · Rusça' / 'Almanya (DE) · Rusça'; bilinmeyen parca 'Tanımlanamayan mevcut seçim (<id>)'."""
    lang_id = _clean_language(getattr(ws, "default_language_id", None))
    lang = language_name(lang_id)
    if lang is None:
        lang = f"Tanımlanamayan mevcut seçim ({lang_id})" if lang_id else "Seçilmedi"
    return f"{_location_short(getattr(ws, 'default_geo_target_id', None))} · {lang}"


def workspace_market(ws: Any) -> Tuple[str, str]:
    """(geo_target_id, language_id), dil alias cozumlu. Biri bossa 409 MARKET_NOT_SET."""
    geo = _clean_geo(getattr(ws, "default_geo_target_id", None))
    lang = _clean_language(getattr(ws, "default_language_id", None))
    if not geo or not lang:
        raise HTTPException(status_code=409, detail=dict(MARKET_NOT_SET_DETAIL))
    return geo, lang


def validate_market_input(
    geo_target_id: Any, language_id: Any
) -> Tuple[Optional[str], Optional[str]]:
    """Yazma yollari icin: verilen (None olmayan) deger katalogda degilse 422.

    KANONIK degerleri dondurur ('geoTargetConstants/1003854' -> '1003854',
    ' languageConstants/1055 ' -> '1037'); cagiran DB'ye BUNLARI yazar — ham
    deger String(20)/String(10) kolonlara sigmayabilir (Codex Faz 0 P2).
    None girdi None doner (alan dokunulmaz). Okuma yollari eski IDleri oldugu gibi dondurur.
    """
    geo = None
    if geo_target_id is not None:
        geo = _clean_geo(geo_target_id)
        if not is_valid_location(geo):
            raise HTTPException(
                status_code=422,
                detail="Geçersiz hedef konum (default_geo_target_id): listeden bir ülke veya şehir seçin.",
            )
    lang = None
    if language_id is not None:
        lang = _clean_language(language_id)
        if not is_valid_language(lang):
            raise HTTPException(
                status_code=422,
                detail="Geçersiz çıktı dili (default_language_id): Türkçe, Rusça, Ukraynaca, İngilizce veya Almanca seçin.",
            )
    return geo, lang


def _location_item(row: LocationRow) -> Dict[str, Any]:
    return {
        "geo_target_id": row.geo_target_id,
        "label": location_label(row.geo_target_id),
        "kind": row.kind,
        "country_code": row.country_code,
        "name": row.name,
    }


def _country_item(country: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "geo_target_id": country["geo_target_id"],
        "label": location_label(country["geo_target_id"]),
        "kind": "country",
        "country_code": country["code"],
        "name": country["name_tr"],
    }


def search_locations_page(
    q: Optional[str], country_code: Optional[str] = None, limit: int = 20
) -> Tuple[List[Dict[str, Any]], int]:
    """(ilk `limit` eslesme, toplam eslesme). Ulkeler once, sonra tam/onek/iceren eslesme sirasi."""
    cc = (country_code or "").strip().upper() or None
    needle = normalize_keyword(q or "")
    countries = [c for c in COUNTRIES if cc is None or c["code"] == cc]
    if len(needle) < 2:  # kisa sorgu: yalniz ulkeler (katalog dosyasi gerekmez)
        items = [_country_item(c) for c in countries]
        return items[:limit], len(items)

    tokens = needle.split()
    hits: List[Tuple[tuple, Dict[str, Any]]] = []
    for c in countries:
        hay = " ".join((normalize_keyword(c["name_tr"]), normalize_keyword(c["code"])))
        hay_en = _country_en_norm(c)
        if all(t in hay or t in hay_en for t in tokens):
            hits.append(((0, 0, 0, _COUNTRY_ORDER[c["code"]], c["name_tr"]), _country_item(c)))

    rows, _ = _load_catalog()
    seen = set()  # Google'da ayni kanonik adli ikinci ID'ler var (Albury x2): listede bir kez gosterilir
    for r in rows:
        if r.kind == "country" or (cc and r.country_code != cc):
            continue
        if not all(t in r.norm_hay for t in tokens):
            continue
        label = (_location_base(r), r.kind)
        if label in seen:
            continue
        seen.add(label)
        # Kendi ADI eslesen satir, yalniz ust konumu (kanonik ad) eslesenin ONUNE gecer:
        # 'petersburg' -> Saint Petersburg sehri, ilceleri (Kolpino, ...) degil (kullanici bulgusu)
        rank = (0 if r.norm_name == needle else 1 if r.norm_name.startswith(needle)
                else 2 if all(t in r.norm_name for t in tokens) else 3)
        key = (1, rank, _KIND_RANK[r.kind], _COUNTRY_ORDER.get(r.country_code, 999), r.name)
        hits.append((key, r))
    hits.sort(key=lambda h: h[0])
    items = [h[1] if isinstance(h[1], dict) else _location_item(h[1]) for h in hits[:limit]]
    return items, len(hits)


def search_locations(
    q: Optional[str], country_code: Optional[str] = None, limit: int = 20
) -> List[Dict[str, Any]]:
    return search_locations_page(q, country_code, limit)[0]


def _country_en_norm(country: Dict[str, Any]) -> str:
    """Ulkenin Google'daki Ingilizce adi (katalogdan); 'germany' yazan Almanya'yi bulsun."""
    row = _load_catalog()[1].get(country["geo_target_id"])
    return row.norm_name if row else ""
