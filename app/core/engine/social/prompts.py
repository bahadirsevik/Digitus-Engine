"""SOCIAL — V4 dort boyut ve V5 niyet promptlari (URETIM KOPYASI).

plan_algoritma_entegrasyonu.md Faz 5 · SOCIAL kod haritasi 5. ve 7. adim.
Kilitli `scripts/social_v4_prompts.py` ve `scripts/social_v5_intent_prompts.py`
sablonlarinin birebir kopyasidir; uretim kodu `scripts/` icinden import ETMEZ.

RELEVANCE promptu BU DOSYADA YOKTUR: SOCIAL, SEO ile AYNI `REL_TEMPLATE`i
kullanir (kod haritasi 2. adim) — `app/core/engine/seo/prompts.py` yeniden
kullanilir, ikinci bir kopya TUTULMAZ.

Iki kaynak ayni dosyada birlestigi icin CAKISAN ust duzey adlar `V5_` onekiyle
tasinir; liste elle degil `ast` ile cikarildi (elle onek listesi uc kez eksik
kalip sessiz ezme uretmisti). Sablon metinleri ve semalar DEGISMEZ.
Cakisan adlar: FORBIDDEN_CONCEPT_WORDS, MAX_TOKENS, PROMPT_TEMPLATE, PROMPT_VERSION, RESPONSE_FIELDS, RESPONSE_SCHEMA, _RAW_TEMPLATE, _listing, build_prompt, known_vectors, parse_batch_response, response_schema_sha256, scan_forbidden_concepts, sha_of, template_sha256
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.core.engine.social.priority import INTENT_CLASSES

PROMPT_VERSION = "SOCIAL-V4-EKA-2026-09-10-v1"

# plan §5: "Model: SEO V3 ile ayni sozlesme (Gemini 3.8 Flash, dusuk dusunme)."
# max_tokens plan §5'te NUMERIK verilmedi (bkz. social_v4_sozlesme.json'daki
# eski PENDING kaydi) -- Gorev 3 karari: 3000 (SEO V3'un relevance/bp
# asamalariyla AYNI tavan; dort skor + kucuk JSON obje icin yeterli, EK-A
# semasi SEO V3'un sub-intent semasindan kucuktur). Tek kaynak BURASI;
# scripts/social_v4_seal.py buradan import eder (Gorev 3: "config VE muhur"
# ikisi de bu sabiti gosterir).
MAX_TOKENS = 3000

# EK-A A-3 -- BIREBIR. Ust duzey semaya DIZI (array) doner, SEO V3'un
# {"results": [...]} sarmali DEGIL -- ai_json.parse_ai_json_list bunu
# `isinstance(data, list)` dalinda dogrudan destekler (result_keys=()).
RESPONSE_SCHEMA: Dict[str, Any] = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "keyword_id": {"type": "integer"},
            "brand_contentability": {"type": "integer", "minimum": 0, "maximum": 100},
            "attention": {"type": "integer", "minimum": 0, "maximum": 100},
            "scenario": {"type": "integer", "minimum": 0, "maximum": 100},
            "relative_fit": {"type": "integer", "minimum": 0, "maximum": 100},
        },
        "required": ["keyword_id", "brand_contentability", "attention",
                     "scenario", "relative_fit"],
    },
}

RESPONSE_FIELDS: Tuple[str, ...] = (
    "brand_contentability", "attention", "scenario", "relative_fit")

# EK-A A-2 -- kaynak §13'ten BIREBIR (ASCII kesme isareti ' -- planda da
# oyle; Unicode U+2019 DEGIL). Tek dogruluk kaynagi: template BU dict'ten
# INSA edilir, ayrica elle kopyalanmaz -- iki yerin (soru metni / template
# govdesi) birbirinden SAPMASI yapisal olarak imkansiz.
QUESTIONS: Dict[str, str] = {
    "brand_contentability": (
        "Bu marka bu keyword'den doğal, anlamlı ve dikkat çekici bir "
        "sosyal medya içeriği üretebilir mi?"),
    "attention": (
        "Bu konu sosyal medyada güçlü bir hook ve merak/dikkat etkisi "
        "yaratabilir mi?"),
    "scenario": (
        "Bu konu Reel/video/carousel gibi formatlara somut bir kurgu ile "
        "dönüştürülebilir mi?"),
    "relative_fit": (
        "Bu keyword, aynı listedeki diğer keyword'lere kıyasla markanın "
        "çekirdek ürün/hizmet alanına ne kadar yakındır?"),
}
# Prompt govdesindeki soru SIRASI -- EK-A A-3 response_schema alan sirasiyla
# hizali (keyword_id disinda): brand_contentability, attention, scenario,
# relative_fit.
QUESTION_FIELD_ORDER: Tuple[str, ...] = RESPONSE_FIELDS


def _questions_block() -> str:
    lines = []
    for i, field in enumerate(QUESTION_FIELD_ORDER, start=1):
        lines.append(f'{i}) {field}: "{QUESTIONS[field]}"')
    return "\n".join(lines)


# ── EK-A A-1: girdi yapisi (FIRMA PROFILI / DEGERLENDIRME OLCEGI /
#    KEYWORD LISTESI) -- __FIRMA__ ve __KELIMELER__ calisma anindaki
#    degerlerle degistirilir; __SORULAR__ import ANINDA (bir kez, sabit)
#    QUESTIONS'tan uretilip gomulur -- boylece PROMPT_TEMPLATE (asagida)
#    TAMAMEN durgun bir sabittir, `template_sha256()` her cagrida ayni
#    sonucu verir. ("KEYWORD LISTESI"nde capa/aday AYIRT EDILMEZ -- EK-A
#    A-1: "Cikti, hangi id'lerin capa oldugunu yalniz kosucu bilir.")
_RAW_TEMPLATE = """Asagidaki firma icin, keyword listesindeki HER kelimeyi DORT ayri boyutta 0-100 arasinda puanla.

[FIRMA PROFILI]
__FIRMA__

[DEGERLENDIRME OLCEGI]
Her boyut icin 0-100 arasinda TAM SAYI ver. 0 = hic, 100 = azami.

[KEYWORD LISTESI]
Asagidaki TUM kelimeler AYNI degerlendirme baglaminda, TEK liste halinde sunulur.
__KELIMELER__

GOREV
Kelimeyi TEK BASINA degil, yukaridaki FIRMA PROFILI'ne ve bu TEK listedeki
diger kelimelere gore degerlendir. Listedeki HER kelime icin asagidaki DORT
soruya ayri ayri 0-100 tam sayi ile cevap ver:

__SORULAR__

KURALLAR
- Listedeki HER kelime icin tam olarak bir sonuc dondur; hicbirini atlama, hicbir id icat etme.
- Her skor 0 ile 100 arasinda (dahil) TAM SAYI olmalidir.
- Yukaridaki DORT sorunun DISINDA hicbir kritere gore puanlama yapma.
- Kelimelerin listedeki sirasi anlam tasimaz; her kelimeyi yalniz id'siyle esle.

CIKTI
Yalniz JSON dondur, baska hicbir metin ekleme. Asagidaki semaya BIREBIR uyan
bir DIZI (array) dondur, tek obje DEGIL:
[{"keyword_id": <tam sayi>, "brand_contentability": <0-100>, "attention": <0-100>, "scenario": <0-100>, "relative_fit": <0-100>}, ...]"""

PROMPT_TEMPLATE: str = _RAW_TEMPLATE.replace("__SORULAR__", _questions_block())

# Placeholderlarin GERCEKTEN sablonda bulundugunu import aninda dogrula --
# yaniboyu bir yeniden yazim bunlari yanlislikla silerse sablon sessizce
# bozuk kalmaz.
assert "__FIRMA__" in PROMPT_TEMPLATE
assert "__KELIMELER__" in PROMPT_TEMPLATE
assert "__SORULAR__" not in PROMPT_TEMPLATE  # bir kez cozulmus olmali


# ── SHA yardimcilari (§3.4 muhur) ────────────────────────────────────────────

def sha_of(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def template_sha256() -> str:
    """`prompt_template_sha` (§3.4) -- sablon TAMAMEN durgun (girdi almaz),
    bu yuzden cagridan cagriya AYNI SHA'yi doner (deterministik, `EK-A A-5`:
    'ekin TAM METNI ... prompt_template_sha olarak §3.4 muhurune girer')."""
    return hashlib.sha256(PROMPT_TEMPLATE.encode("utf-8")).hexdigest()


def response_schema_sha256() -> str:
    return sha_of(RESPONSE_SCHEMA)


# ── EK-A A-1 / batch payload kurucusu ────────────────────────────────────────

def _listing(rows: Sequence[Dict[str, Any]]) -> str:
    """SATIR BASINA YALNIZ `keyword_id` + `keyword_text` okunur -- `row`
    baska alanlar tasisa BILE (orn. gercek pipeline satirlari volume/
    trend_3m/trend_12m/relevance_100/family_id de tasir) bu fonksiyon
    onlara HICBIR ZAMAN erismez. Forbidden-field guvenligi bu YAPISAL
    darlik uzerine kuruludur, bir kelime-taramasi uzerine DEGIL (bkz. modul
    docstring'i ve tests/unit/test_social_v4_prompts.py)."""
    return "\n".join(f'{row["keyword_id"]}: {row["keyword_text"]}' for row in rows)


def build_prompt(firm_block_text: str, ordered_rows: Sequence[Dict[str, Any]]) -> str:
    """`ordered_rows`: ONCEDEN karistirilmis (scripts/social_v4_seal.py::
    batch_payload_order -- capa+aday TEK liste, karisik sira) satirlar.
    Capa/aday burada da AYIRT EDILMEZ -- cagiran taraf hangi id'lerin capa
    oldugunu ayri tutar (EK-A A-1), bu fonksiyon tek bir listing uretir.
    """
    listing = _listing(ordered_rows)
    return (PROMPT_TEMPLATE
            .replace("__FIRMA__", firm_block_text)
            .replace("__KELIMELER__", listing))


# ── statik sablon icin YASAK KAVRAM taramasi (yalniz TEMPLATE metni icin --
#    render EDILMIS gercek keyword metnine UYGULANMAZ; bir aday/capa kelimesi
#    dogal Turkce icinde "trend"/"genel" gibi kelimeler TASIYABILIR, bu bir
#    ihlal DEGILDIR -- yasak olan METRIGIN KENDISININ/ALAN ADININ payload'a
#    SIZMASIDIR, bkz. modul docstring'i ve _listing) ────────────────────────

FORBIDDEN_CONCEPT_WORDS: Tuple[str, ...] = (
    "relevance", "hacim", "volume", "trend", "rekabet", "competition",
    "etiket", "label", "patron", "cevap anahtari",
)


def scan_forbidden_concepts(text: str) -> List[str]:
    """Sadece STATIK sablon/talimat metni icin kullanilir (bkz. yukaridaki
    not). Kucuk harfe cevirip alt-dizgi arar; bulunanlari DONER (bos liste =
    temiz)."""
    lowered = text.lower()
    return [tok for tok in FORBIDDEN_CONCEPT_WORDS if tok in lowered]


# ── EK-A A-4: dayaniklilik (parse + dogrulama, saglayici cagrisi YOK) ───────

class BatchResponseError(RuntimeError):
    """`parse_batch_response` kullanimi icin -- bu modul kendisi hicbir
    zaman firlatmaz (saf fonksiyon, `ok=False` ile raporlar); cagiran
    tarafin (gelecekteki AI kosucusu) kendi akisinda kullanmasi icin
    tasinir."""


def parse_batch_response(raw: str, expected_ids: Sequence[int]) -> Dict[str, Any]:
    """EK-A A-4: cikti CIPLAK `json.loads` ile ASLA parse edilmez --
    `app/core/channel/ai_json.py::parse_ai_json_list` kurtarma zincirinden
    gecirilir (`result_keys=()`: ust duzey DIZI beklenir, EK-A A-3).

    Donus (saglayici cagirmaz -- SADECE metin -> yapi + dogrulama):
      by_id            : {keyword_id: {dort skor}} -- GECERLI, TEKIL, BEKLENEN
                          id'ler icin
      missing_ids      : beklenen ama gecerli/tekil bir sonucu OLMAYAN id'ler
                          (hedefli retry adayi -- A-4: "yalniz eksik id'ler
                          icin hedefli retry")
      duplicate_ids    : yaniti BIRDEN FAZLA kez gecen id'ler -- A-4: "o
                          batch'in ciktisi ATILIR, batch bir kez yeniden
                          kosulur" (KARAR bu fonksiyonun DISINDA, cagiran
                          tarafta)
      unknown_ids      : batch'te GONDERILMEMIS bir id -- yapisal hata,
                          §1.5 duman kapisi
      out_of_range_ids : skor eksik/tip-disi/0-100 disi -- yapisal hata,
                          §1.5 duman kapisi
      malformed_ids    : `keyword_id` alani hic tam sayiya CEVRILEMEYEN
                          satirlar (ham deger raporlanir)
      ok               : yukaridakilerin HICBIRI yoksa True
      parse_error      : `ai_json` bile hicbir sey KURTARAMADIYSA mesaj
                          (bu durumda TUM beklenen id'ler `missing_ids`'e
                          duser)
    """
    from app.core.channel.ai_json import AIJsonParseError, parse_ai_json_list

    expected = sorted({int(x) for x in expected_ids})
    expected_set = set(expected)

    try:
        items = parse_ai_json_list(raw, result_keys=())
    except AIJsonParseError as exc:
        return {
            "ok": False, "parse_error": str(exc), "by_id": {},
            "missing_ids": list(expected), "unknown_ids": [],
            "duplicate_ids": [], "out_of_range_ids": [], "malformed_ids": [],
        }

    seen_ids: List[int] = []
    by_id: Dict[int, Dict[str, int]] = {}
    unknown_ids: List[int] = []
    out_of_range_ids: List[int] = []
    malformed_ids: List[Any] = []

    for item in items:
        if not isinstance(item, dict):
            continue
        raw_id = item.get("keyword_id")
        if isinstance(raw_id, bool) or not isinstance(raw_id, (int, float, str)):
            malformed_ids.append(raw_id)
            continue
        try:
            kid = int(str(raw_id).strip()) if isinstance(raw_id, str) else int(raw_id)
        except (TypeError, ValueError):
            malformed_ids.append(raw_id)
            continue

        seen_ids.append(kid)
        if kid not in expected_set:
            unknown_ids.append(kid)
            continue

        row_ok = True
        scores: Dict[str, int] = {}
        for field in RESPONSE_FIELDS:
            val = item.get(field)
            if isinstance(val, bool) or not isinstance(val, (int, float)):
                row_ok = False
                break
            if float(val) != int(val):
                row_ok = False
                break
            ival = int(val)
            if not (0 <= ival <= 100):
                row_ok = False
                break
            scores[field] = ival
        if not row_ok:
            out_of_range_ids.append(kid)
            continue
        if kid in by_id:
            continue  # yinelenme sayimi asagida seen_ids uzerinden
        by_id[kid] = scores

    duplicate_ids = sorted({kid for kid in seen_ids if seen_ids.count(kid) > 1})
    missing_ids = sorted(expected_set - set(by_id.keys()))
    ok = not (duplicate_ids or unknown_ids or out_of_range_ids
              or malformed_ids or missing_ids)

    return {
        "ok": ok, "parse_error": None, "by_id": by_id,
        "missing_ids": missing_ids,
        "unknown_ids": sorted(set(unknown_ids)),
        "duplicate_ids": duplicate_ids,
        "out_of_range_ids": sorted(set(out_of_range_ids)),
        "malformed_ids": malformed_ids,
    }


def detect_degenerate_dimensions(by_id: Dict[int, Dict[str, int]]) -> List[str]:
    """§1.5 / §1.5b: 'bir batch'te bir skorun TUM degerleri birebir AYNI'
    (matematiksel dejenerelik). En az 2 gozlem gerektirir -- tek satirlik
    batch icin dejenerelik tanimsizdir (icat edilmez, bos liste doner)."""
    if len(by_id) < 2:
        return []
    degenerate: List[str] = []
    for field in RESPONSE_FIELDS:
        values = {row[field] for row in by_id.values() if field in row}
        if len(values) == 1:
            degenerate.append(field)
    return degenerate


def known_vectors() -> Dict[str, Any]:
    """Bagimsiz 2. uygulama icin: sabit girdi -> sabit SHA (§1.12 deseniyle
    ayni -- bkz. scripts/social_v4_seal.py::deterministic_shuffle_known_vector)."""
    return {
        "prompt_version": PROMPT_VERSION,
        "template_sha256": template_sha256(),
        "response_schema_sha256": response_schema_sha256(),
        "max_tokens": MAX_TOKENS,
        "sample_render": build_prompt(
            "Sektor: Ornek\nMarka: Ornek Marka\nOzet: Ornek ozet.",
            [{"keyword_id": 2, "keyword_text": "ornek kelime iki"},
             {"keyword_id": 1, "keyword_text": "ornek kelime bir"}],
        ),
    }


# ── V5 niyet promptu ──

V5_PROMPT_VERSION = "SOCIAL-V5-INTENT-2026-09-14-v1"

#: V4 ile AYNI tavan (scripts/social_v4_prompts.V5_MAX_TOKENS) -- niyet cevabi
#: dort skordan KUCUKTUR (tek enum + tek sayi + kisa metin), ayri bir tavan
#: ICAT EDILMEZ. Tek kaynak BURASI; seal ve transport buradan import eder.
V5_MAX_TOKENS = 3000

#: V7: gerekce metni bu uzunlukta KIRPILIR ve hicbir SHA'ya/metrige girmez.
INTENT_REASON_MAX_CHARS = 200

V5_RESPONSE_SCHEMA: Dict[str, Any] = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "keyword_id": {"type": "integer"},
            "social_intent_type": {"type": "string", "enum": list(INTENT_CLASSES)},
            "intent_confidence": {"type": "integer", "minimum": 0, "maximum": 100},
            "intent_reason": {"type": "string"},
        },
        "required": ["keyword_id", "social_intent_type", "intent_confidence",
                     "intent_reason"],
    },
}

V5_RESPONSE_FIELDS: Tuple[str, ...] = (
    "social_intent_type", "intent_confidence", "intent_reason")

# ── kaynak §5 -- dort sinif tanimi BIREBIR (ornekler dahil) ────────────────
# Tek dogruluk kaynagi: template BU dict'ten INSA edilir, elle kopyalanmaz.
CLASS_DEFINITIONS: Dict[str, Dict[str, str]] = {
    "CONTENT_NATIVE": {
        "tanim": ("Direkt sosyal medya içeriğine dönüşebilen keywordlerdir."),
        "ornek": "saç beyazlamasına çözüm, hisse bilanço, SEO nedir",
    },
    "PRODUCT_EDUCATION": {
        "tanim": ("Ürün veya platform kullanımına bağlanabilir ancak ana "
                  "içerik omurgası değildir."),
        "ornek": "canlı borsa, hisse takip",
    },
    "COMMERCIAL_SEARCH": {
        "tanim": ("Satın alma veya hizmet alma niyeti yüksek sorgulardır."),
        "ornek": "dijital reklam ajansı İstanbul, SEO ajansı, Google Ads ajansı",
    },
    "TREND_OPPORTUNITY": {
        "tanim": ("Güncel dalga oluşturan ve özel içerik fırsatı taşıyan "
                  "konulardır."),
        "ornek": "yapay zeka hisseleri, yeni yatırım trendleri",
    },
}

assert tuple(CLASS_DEFINITIONS.keys()) == tuple(INTENT_CLASSES), (
    "sinif tanimlari motorun enum sirasiyla BIREBIR ayni olmali")


def _classes_block() -> str:
    lines: List[str] = []
    for name in INTENT_CLASSES:
        d = CLASS_DEFINITIONS[name]
        lines.append(f'{name}: {d["tanim"]}')
        lines.append(f'  Örnek: {d["ornek"]}')
    return "\n".join(lines)


V5__RAW_TEMPLATE = """Asagidaki firma icin, keyword listesindeki HER kelimenin SOSYAL MEDYA KULLANIM AMACINI belirle.

[FIRMA PROFILI]
__FIRMA__

[SINIFLAR]
Her kelime asagidaki DORT siniftan TAM OLARAK BIRINE atanir. Birden fazla sinif
verme; kelimeye EN COK uyan BASKIN sinifi sec.
__SINIFLAR__

[KEYWORD LISTESI]
Asagidaki TUM kelimeler AYNI degerlendirme baglaminda, TEK liste halinde sunulur.
__KELIMELER__

GOREV
Kelimeyi TEK BASINA degil, yukaridaki FIRMA PROFILI baglaminda degerlendir.
Listedeki HER kelime icin sunlari dondur:
1) social_intent_type: DORT siniftan TAM OLARAK BIRI (yukaridaki adlardan biri, birebir)
2) intent_confidence: 0-100 arasi TAM SAYI -- bu sinif atamasindan ne kadar emin oldugun
3) intent_reason: en fazla 200 karakter, tek cumlelik kisa gerekce

KURALLAR
- Listedeki HER kelime icin tam olarak bir sonuc dondur; hicbirini atlama, hicbir id icat etme.
- social_intent_type yalniz su dort degerden biri olabilir: __SINIF_ADLARI__
- Yukaridaki dort sinif tanimi DISINDA bir olcut kullanma.
- Kelimelerin listedeki sirasi anlam tasimaz; her kelimeyi yalniz id'siyle esle.

CIKTI
Yalniz JSON dondur, baska hicbir metin ekleme. Asagidaki semaya BIREBIR uyan
bir DIZI (array) dondur, tek obje DEGIL:
[{"keyword_id": <tam sayi>, "social_intent_type": "<sinif>", "intent_confidence": <0-100>, "intent_reason": "<kisa gerekce>"}, ...]"""

V5_PROMPT_TEMPLATE: str = (
    V5__RAW_TEMPLATE
    .replace("__SINIFLAR__", _classes_block())
    .replace("__SINIF_ADLARI__", ", ".join(INTENT_CLASSES))
)

assert "__FIRMA__" in V5_PROMPT_TEMPLATE
assert "__KELIMELER__" in V5_PROMPT_TEMPLATE
assert "__SINIFLAR__" not in V5_PROMPT_TEMPLATE
assert "__SINIF_ADLARI__" not in V5_PROMPT_TEMPLATE


# ── SHA yardimcilari ───────────────────────────────────────────────────────

def V5_sha_of(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def V5_template_sha256() -> str:
    return hashlib.sha256(V5_PROMPT_TEMPLATE.encode("utf-8")).hexdigest()


def V5_response_schema_sha256() -> str:
    return V5_sha_of(V5_RESPONSE_SCHEMA)


# ── payload kurucusu (KORLUK burada yapisal) ───────────────────────────────

def V5__listing(rows: Sequence[Dict[str, Any]]) -> str:
    """SATIR BASINA YALNIZ `keyword_id` + `keyword_text` okunur. Satir
    baska alanlar (social_score, brand_contentability, volume, trend_3m, ...)
    tasisa BILE bu fonksiyon onlara HICBIR ZAMAN erismez -- korluk bu YAPISAL
    darlik uzerine kuruludur (plan §4.2 V12)."""
    return "\n".join(f'{row["keyword_id"]}: {row["keyword_text"]}' for row in rows)


def V5_build_prompt(firm_block_text: str, ordered_rows: Sequence[Dict[str, Any]]) -> str:
    return (V5_PROMPT_TEMPLATE
            .replace("__FIRMA__", firm_block_text)
            .replace("__KELIMELER__", V5__listing(ordered_rows)))


# ── statik sablon icin YASAK KAVRAM taramasi ──────────────────────────────
# Yalniz TEMPLATE metni icin; render EDILMIS keyword metnine UYGULANMAZ
# (bir kelime dogal Turkce icinde "trend" tasiyabilir -- ihlal DEGILDIR;
# yasak olan METRIGIN KENDISININ payload'a sizmasidir).
# NOT: "trend" sozcugu TREND_OPPORTUNITY sinif ADINDA gectigi icin bu
# taramanin disinda tutulur -- sinif adi kaynagin kendi terimidir, bir
# metrik degeri DEGILDIR (V10).

V5_FORBIDDEN_CONCEPT_WORDS: Tuple[str, ...] = (
    "relevance", "hacim", "volume", "rekabet", "competition",
    "social_score", "socialscore", "brand_contentability", "attention",
    "scenario", "relative_fit", "trend_3m", "trend_12m", "trendchange",
    "etiket", "label", "patron", "cevap anahtari",
)


def V5_scan_forbidden_concepts(text: str) -> List[str]:
    lowered = text.lower()
    return [tok for tok in V5_FORBIDDEN_CONCEPT_WORDS if tok in lowered]


# ── dayaniklilik: parse + dogrulama (saglayici cagrisi YOK) ───────────────

def V5_parse_batch_response(raw: str, expected_ids: Sequence[int]) -> Dict[str, Any]:
    """`social_v4_prompts.V5_parse_batch_response` ile AYNI sozlesme ve AYNI
    donus sekli (`run_batches` bunu bekler) -- fark yalniz alan semasi.

    Donus: ok / parse_error / by_id / missing_ids / unknown_ids /
           duplicate_ids / out_of_range_ids / malformed_ids
    `by_id[kid]` = {"social_intent_type": str, "intent_confidence": int,
                    "intent_reason": str}

    `out_of_range_ids`: enum disi sinif, 0-100 disi/tip-disi guven veya
    metin olmayan gerekce. Sinif UYDURULMAZ -- gecersiz satir gecersizdir.
    """
    from app.core.channel.ai_json import AIJsonParseError, parse_ai_json_list

    expected = sorted({int(x) for x in expected_ids})
    expected_set = set(expected)

    try:
        items = parse_ai_json_list(raw, result_keys=())
    except AIJsonParseError as exc:
        return {"ok": False, "parse_error": str(exc), "by_id": {},
                "missing_ids": list(expected), "unknown_ids": [],
                "duplicate_ids": [], "out_of_range_ids": [], "malformed_ids": []}

    seen_ids: List[int] = []
    by_id: Dict[int, Dict[str, Any]] = {}
    unknown_ids: List[int] = []
    out_of_range_ids: List[int] = []
    malformed_ids: List[Any] = []
    valid_classes = set(INTENT_CLASSES)

    for item in items:
        if not isinstance(item, dict):
            continue
        raw_id = item.get("keyword_id")
        if isinstance(raw_id, bool) or not isinstance(raw_id, (int, float, str)):
            malformed_ids.append(raw_id)
            continue
        try:
            kid = int(str(raw_id).strip()) if isinstance(raw_id, str) else int(raw_id)
        except (TypeError, ValueError):
            malformed_ids.append(raw_id)
            continue

        seen_ids.append(kid)
        if kid not in expected_set:
            unknown_ids.append(kid)
            continue

        cls = item.get("social_intent_type")
        if not isinstance(cls, str) or cls.strip() not in valid_classes:
            out_of_range_ids.append(kid)
            continue

        conf = item.get("intent_confidence")
        if isinstance(conf, bool) or not isinstance(conf, (int, float)) \
                or float(conf) != int(conf) or not (0 <= int(conf) <= 100):
            out_of_range_ids.append(kid)
            continue

        reason = item.get("intent_reason")
        if not isinstance(reason, str):
            out_of_range_ids.append(kid)
            continue

        if kid in by_id:
            continue  # yinelenme sayimi asagida seen_ids uzerinden
        by_id[kid] = {
            "social_intent_type": cls.strip(),
            "intent_confidence": int(conf),
            # V7: KIRPILIR; hicbir SHA'ya/metrige girmez
            "intent_reason": reason.strip()[:INTENT_REASON_MAX_CHARS],
        }

    duplicate_ids = sorted({kid for kid in seen_ids if seen_ids.count(kid) > 1})
    missing_ids = sorted(expected_set - set(by_id.keys()))
    ok = not (duplicate_ids or unknown_ids or out_of_range_ids
              or malformed_ids or missing_ids)

    return {"ok": ok, "parse_error": None, "by_id": by_id,
            "missing_ids": missing_ids,
            "unknown_ids": sorted(set(unknown_ids)),
            "duplicate_ids": duplicate_ids,
            "out_of_range_ids": sorted(set(out_of_range_ids)),
            "malformed_ids": malformed_ids}


def detect_degenerate_batch(by_id: Dict[int, Dict[str, Any]]) -> List[str]:
    """Duman kapisi: bir batch'te TUM kelimeler AYNI sinifi aldiysa
    (matematiksel dejenerelik). En az 2 gozlem gerekir -- tek satirlik batch
    icin tanimsizdir (icat edilmez, bos liste doner)."""
    if len(by_id) < 2:
        return []
    classes = {row["social_intent_type"] for row in by_id.values()}
    return ["social_intent_type"] if len(classes) == 1 else []


def V5_known_vectors() -> Dict[str, Any]:
    """Bagimsiz dogrulama icin: sabit girdi -> sabit SHA."""
    return {
        "prompt_version": V5_PROMPT_VERSION,
        "V5_template_sha256": V5_template_sha256(),
        "V5_response_schema_sha256": V5_response_schema_sha256(),
        "max_tokens": V5_MAX_TOKENS,
        "intent_classes": list(INTENT_CLASSES),
        "sample_render": V5_build_prompt(
            "Sektor: Ornek\nMarka: Ornek Marka\nOzet: Ornek ozet.",
            [{"keyword_id": 2, "keyword_text": "ornek kelime iki"},
             {"keyword_id": 1, "keyword_text": "ornek kelime bir"}],
        ),
    }
