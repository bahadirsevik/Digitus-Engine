"""SEO kati-2 — prompt katmani (URETIM KOPYASI).

plan_algoritma_entegrasyonu.md Faz 4 · SEO kod haritasi 3a-3e.
Kilitli `scripts/seo_v3_prompts.py` (REL / BP / SUBINTENT) ve
`scripts/seo_v31_prompts.py` (AUTHORITY / URLGROUP) sablonlarinin birebir
kopyasidir; uretim kodu `scripts/` icinden import ETMEZ.

Sinyal PENCERELERI (kilitli sozlesme):
  * BP       : yalniz `Rel >= 0,50` evreni
  * AUTHORITY: yalniz `Rel >= 0,85 ve 0,70 <= BP < 0,80` penceresi
  * SUBINTENT: aile basina TEK cagri
  * URLGROUP : >= 2 uygun kumesi olan aile basina TEK cagri

Ad cakismasini onlemek icin v3.1 sembolleri `V31_` onekiyle tasinir; sablon
metinleri ve semalar DEGISMEZ.
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional, Sequence, Tuple

PROMPT_VERSIONS: Dict[str, str] = {
    "bp": "SEO-V3-BP-2026-09-06-v1",
    "relevance": "SEO-V3-REL-2026-09-06-v2",
    "subintent": "SEO-V3-SUBINTENT-2026-09-06-v2",
}

BROAD_INTENTS: Tuple[str, ...] = (
    "informational", "commercial", "transactional", "navigational")

# Bantlar: (alt sinir, ust sinir) — ikisi de DAHIL. Bant disi deger en yakin
# sinira kirpilir ve SAYILIR (Intent V4 `apply_band` deseni).
BP_BANDS: Dict[str, Tuple[float, float]] = {
    "direct": (1.00, 1.00),
    "very_close": (0.80, 0.95),
    "support": (0.70, 0.79),
    "related_far": (0.50, 0.69),
    "broad": (0.00, 0.49),
}

REL_BANDS: Dict[str, Tuple[float, float]] = {
    "core": (1.00, 1.00),
    "strong": (0.80, 0.99),
    "adjacent": (0.60, 0.79),
    "sector_only": (0.40, 0.59),
    "broad": (0.00, 0.39),
}

# ── EK-A ─────────────────────────────────────────────────────────────
BP_TEMPLATE = """Asagidaki firma icin anahtar kelimelerin IS YAKINLIGINI (Business Proximity) puanla.
FIRMA
__FIRMA__
TANIM
Business Proximity su soruya cevap verir: "Bu konu, markanin sattigi urune/hizmete,
urunun gercek kullanimina veya markanin sahip olmasi gereken uzmanlik alanina ne
kadar yakin?"
OLCEK (baglayici; once bandi sec, sonra bandin icinde deger ver)
  direct        1.00        dogrudan urun/hizmet kullanim alani
  very_close    0.80-0.95   cok yakin problem / ticari niyet / uzmanlik alani
  support       0.70-0.79   otorite yaratabilecek destek konu
  related_far   0.50-0.69   sektorle iliskili ama uzak
  broad         0.00-0.49   broad / konu disina tasan
KURALLAR
- Kelimenin arama hacmi, trendi, rekabeti veya firmaya genel uygunluk derecesi
  SORULMUYOR; bunlari tahmin etmeye calisma. Yalniz KONUNUN ise yakinligini puanla.
- Marka adi gecmesi tek basina yakinlik YARATMAZ; konu neyse ona gore puanla.
- Ayni konunun yazim, tekil/cogul ve kelime sirasi varyasyonlari AYNI banda duser.
- Bilgi amacli sorgu (nedir / nasil yapilir) otomatik olarak uzak DEGILDIR; konu
  markanin uzmanlik alanindaysa support veya very_close olabilir.
- Listedeki HER kelime icin tam olarak bir sonuc dondur; hicbirini atlama.
KELIMELER
__KELIMELER__
Yalniz JSON: {"results": [{"id": <kelime id>, "band": "...", "business_proximity": 0.00}]}"""

# ── EK-B ─────────────────────────────────────────────────────────────
REL_TEMPLATE = """Asagidaki firma icin anahtar kelimelerin firmaya RELEVANCE (uygunluk) degerini puanla.
FIRMA
__FIRMA__
TANIM
Relevance, sorguyu yapan kisinin firmanin urun/hizmeti VEYA otorite alaniyla GERCEK
bagini olcer. Soru sudur: "Bu sorguyu yapan kisi, firmanin urun/hizmetinin veya
uzmanlik alaninin gercek bir muhatabi mi?"
OLCEK (baglayici; once bandi sec, sonra bandin icinde deger ver)
  core          1.00        dogrudan cekirdek konu; firmanin urun/hizmetinin tam icinde
  strong        0.80-0.99   cok guclu gercek bag; firmanin dogal olarak cevap verdigi sorgu
  adjacent      0.60-0.79   gercek fakat destekleyici / komsu nis; firma bu konuda otorite kurabilir
  sector_only   0.40-0.59   sektorle iliskili fakat firmanin nisinin DISINDA
  broad         0.00-0.39   broad, genel ya da baska bir alanin sorgusu
KURALLAR
- Arama hacmi, trend ve rekabet Relevance hesabina DAHIL DEGILDIR; tahmin etme.
- Bu bir reklam siniflandirmasi DEGILDIR; satin alma niyeti puani verme. Bilgi amacli
  sorgu, firmanin nisindeyse yuksek relevance alabilir.
- Marka adi gecmesi tek basina relevance YARATMAZ; konu neyse ona gore puanla.
- Rakip marka adi geciyorsa konuya gore puanla; marka adini ceza sebebi yapma.
- Ayni konunun yazim, tekil/cogul ve kelime sirasi varyasyonlari ayni banda duser.
- Listedeki HER kelime icin tam olarak bir sonuc dondur; hicbirini atlama.
KELIMELER
__KELIMELER__
Yalniz JSON: {"results": [{"id": <kelime id>, "band": "...", "relevance": 0.00}]}"""

# ── EK-C ─────────────────────────────────────────────────────────────
SUBINTENT_TEMPLATE = """Asagidaki firma ve AILE icin, ailenin uyelerini ICERIK ALT NIYETI kumelerine ayir.
FIRMA
__FIRMA__
AILE
Ad: __AILE_ADI__ · Temel ihtiyac: __CORE_NEED__ · Cozum turu: __SOLUTION_TYPE__
TANIM
Bir alt niyet kumesi, TEK BIR icerik / TEK BIR URL ile karsilanabilecek sorgular
grubudur. Ayni kumedeki sorgular ayni soruya cevap arar.
KURALLAR
- Once her kelime icin genis niyeti sec: informational / commercial / transactional /
  navigational.
- Sonra alt niyet kumesini ver: subintent_id kisa, sabit, kucuk harf ve alt cizgi
  (genel ornekler: konu_nasil_yapilir, konu_fiyat, konu_nedir, konu_en_iyi).
- Her kume TEK BIR genis niyet tasir. Ayni konu farkli genis niyetlerle geciyorsa
  AYRI kumedir.
- Farkli icerik ihtiyaci doguran sorgular AYRI kumedir ("fiyat" ile "nasil yapilir"
  ayni ailede olsa da ayri kume).
- Tekil/cogul, yazim ve kelime sirasi varyasyonlari AYNI kumedir; ayri kume ACMA.
- Es anlamli ve uzun-kuyruk varyasyonlar ayni soruya cevap ariyorsa AYNI kumedir.
- Gorevin YALNIZ kumelemektir. Hicbir kelimeyi eleme, "uygun degil" diye isaretleme,
  kalite hukmu verme.
- Listedeki HER kelime icin tam olarak bir sonuc dondur; hicbirini atlama.
KELIMELER
__KELIMELER__
Yalniz JSON: {"results": [{"id": <kelime id>, "broad_intent": "...", "subintent_id": "...", "subintent_label": "..."}]}"""

TEMPLATES: Dict[str, str] = {
    "bp": BP_TEMPLATE,
    "relevance": REL_TEMPLATE,
    "subintent": SUBINTENT_TEMPLATE,
}

# ── semalar (Gemini response_schema) ────────────────────────────────
BP_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {"results": {"type": "array", "items": {
        "type": "object",
        "properties": {"id": {"type": "integer"},
                       "band": {"type": "string", "enum": list(BP_BANDS)},
                       "business_proximity": {"type": "number"}},
        "required": ["id", "band", "business_proximity"]}}},
    "required": ["results"],
}

REL_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {"results": {"type": "array", "items": {
        "type": "object",
        "properties": {"id": {"type": "integer"},
                       "band": {"type": "string", "enum": list(REL_BANDS)},
                       "relevance": {"type": "number"}},
        "required": ["id", "band", "relevance"]}}},
    "required": ["results"],
}

SUBINTENT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {"results": {"type": "array", "items": {
        "type": "object",
        "properties": {"id": {"type": "integer"},
                       "broad_intent": {"type": "string",
                                        "enum": list(BROAD_INTENTS)},
                       "subintent_id": {"type": "string"},
                       "subintent_label": {"type": "string"}},
        "required": ["id", "broad_intent", "subintent_id", "subintent_label"]}}},
    "required": ["results"],
}

SCHEMAS: Dict[str, Dict[str, Any]] = {
    "bp": BP_SCHEMA, "relevance": REL_SCHEMA, "subintent": SUBINTENT_SCHEMA}


# ── yardimcilar ──────────────────────────────────────────────────────

def template_sha256(name: str) -> str:
    return hashlib.sha256(TEMPLATES[name].encode("utf-8")).hexdigest()


def all_template_sha256() -> Dict[str, Dict[str, str]]:
    return {name: {"version": PROMPT_VERSIONS[name],
                   "template_sha256": template_sha256(name)}
            for name in TEMPLATES}


def _listing(batch: Sequence[Dict[str, Any]]) -> str:
    return "\n".join(f'{row["keyword_id"]}: {row["keyword_text"]}' for row in batch)


def _firm(profile: Dict[str, Any]) -> str:
    """SEO'nun firma blogu — ADS funnel blogunun AYNISI.

    Kilitli kaynak bu blogu `scripts/ads_v3_ai.firm_block`tan import eder;
    uretim kodu `scripts/` icinden import ETMEZ, ayni davranisi tasiyan
    `app/core/engine/context.firm_block` kullanilir (Faz 1'de birebir
    esitligi parity testiyle kanitlandi).
    """
    from app.core.engine.context import firm_block

    return firm_block(profile)


def build_bp_prompt(profile: Dict[str, Any],
                    batch: Sequence[Dict[str, Any]]) -> str:
    return (BP_TEMPLATE.replace("__FIRMA__", _firm(profile))
            .replace("__KELIMELER__", _listing(batch)))


def build_relevance_prompt(profile: Dict[str, Any],
                           batch: Sequence[Dict[str, Any]]) -> str:
    return (REL_TEMPLATE.replace("__FIRMA__", _firm(profile))
            .replace("__KELIMELER__", _listing(batch)))


def build_subintent_prompt(profile: Dict[str, Any], family: Dict[str, Any],
                           members: Sequence[Dict[str, Any]]) -> str:
    return (SUBINTENT_TEMPLATE.replace("__FIRMA__", _firm(profile))
            .replace("__AILE_ADI__", str(family.get("family_name") or family.get("family_id") or "-"))
            .replace("__CORE_NEED__", str(family.get("core_need") or "-"))
            .replace("__SOLUTION_TYPE__", str(family.get("solution_type") or "-"))
            .replace("__KELIMELER__", _listing(members)))


def apply_band(bands: Dict[str, Tuple[float, float]], band: Optional[str],
               value: Any) -> Tuple[Optional[float], bool]:
    """Deger bandin icine kirpilir. Donus: (deger | None, kirpildi_mi).

    Bant gecersiz veya deger sayi degilse (None, False) — cagiran EKSIK sayar.
    """
    if band not in bands or isinstance(value, bool) \
            or not isinstance(value, (int, float)):
        return None, False
    lo, hi = bands[band]
    v = float(value)
    clamped = v < lo or v > hi
    return round(min(max(v, lo), hi), 4), clamped


def parse_signal_results(kind: str, items: List[Any],
                         batch: Sequence[Dict[str, Any]]) -> Dict[int, Dict[str, Any]]:
    """Saglayici ciktisini kelime basina tek kayda indirger; eksikleri isaretler."""
    field = {"bp": "business_proximity", "relevance": "relevance"}.get(kind)
    bands = {"bp": BP_BANDS, "relevance": REL_BANDS}.get(kind)
    out: Dict[int, Dict[str, Any]] = {}
    for row in batch:
        kid = row["keyword_id"]
        item = next((x for x in items if isinstance(x, dict)
                     and str(x.get("id", "")).strip().lstrip("-").isdigit()
                     and int(x["id"]) == kid), None)
        if kind == "subintent":
            bi = (item or {}).get("broad_intent")
            sid = (item or {}).get("subintent_id")
            ok = (item is not None and bi in BROAD_INTENTS
                  and isinstance(sid, str) and sid.strip() != "")
            out[kid] = {"id": kid,
                        "broad_intent": bi if ok else None,
                        "subintent_id": sid.strip().lower() if ok else None,
                        "subintent_label": (item or {}).get("subintent_label"),
                        "eksik": not ok}
            continue
        value, clamped = apply_band(bands, (item or {}).get("band"),
                                    (item or {}).get(field))
        out[kid] = {"id": kid, field: value, "band": (item or {}).get("band"),
                    "ham": (item or {}).get(field), "band_kirpildi": clamped,
                    "eksik": value is None}
    return out


# ── V3.1: authority + URL grubu ──

V31_PROMPT_VERSIONS: Dict[str, str] = {
    "authority": "SEO-V31-AUTH-2026-09-09-v1",
    "urlgroup": "SEO-V31-URLGROUP-2026-09-09-v1",
}

AUTHORITY_LEVELS: Tuple[str, ...] = ("high", "medium", "low")

# ── EK-E ─────────────────────────────────────────────────────────────
AUTHORITY_TEMPLATE = """Asagidaki firma icin anahtar kelimelerin AUTHORITY VALUE degerini puanla.
FIRMA
__FIRMA__
TANIM
Authority Value su soruya cevap verir: "Bu firma, bu konuda gercekten soz sahibi
bir kaynak olabilir mi? Yayinlayacagi icerik, kendi isinden gelen gercek bir
uzmanliga mi dayanir, yoksa herkesin yazabilecegi genel bir metin mi olur?"
OLCEK (baglayici)
  high     firmanin kendi isinden gelen dogrudan uzmanlik; ilk agizdan veri,
           deneyim veya operasyonel bilgi ile yazilabilir
  medium   firma yazabilir fakat ustunlugu yok; sektorde herkes yazabilir
  low      firmanin uzmanlik alani disinda; yazarsa taklit icerik olur
KURALLAR
- Arama hacmi, trend, rekabet ve ticari niyet SORULMUYOR; tahmin etme.
- Bu bir uygunluk (relevance) veya is yakinligi (business proximity) puani DEGILDIR;
  yalniz "bu konuda otorite olabilir mi" sorusudur.
- Marka adi gecmesi tek basina otorite YARATMAZ.
- Listedeki HER kelime icin tam olarak bir sonuc dondur; hicbirini atlama.
KELIMELER
__KELIMELER__
Yalniz JSON: {"results": [{"id": <kelime id>, "authority": "high|medium|low"}]}"""

# ── EK-F ─────────────────────────────────────────────────────────────
URLGROUP_TEMPLATE = """Asagidaki firma ve AILE icin, Primary adaylarinin AYRI SAYFA gerektirip
gerektirmedigine karar ver.
FIRMA
__FIRMA__
AILE
Ad: __AILE_ADI__ · Temel ihtiyac: __CORE_NEED__
SORU
Bu adaylardan hangileri TEK BIR sayfayla birlikte karsilanabilir?
Ayni sayfayla karsilanabilecek adaylari ayni gruba koy.
KURALLAR
- Iki aday ayni soruyu farkli kelimelerle soruyorsa AYNI gruptadir.
- Iki aday farkli bir kullanici sorusuna cevap veriyorsa ve her biri kendi basina
  bir sayfayi dolduruyorsa AYRI gruptadir.
- Tekil/cogul, yazim ve kelime sirasi varyasyonlari her zaman AYNI gruptadir.
- Bir adayin daha genis olmasi tek basina ayri sayfa sebebi DEGILDIR; genis sorgu,
  dar sorgunun sayfasinda karsilanabiliyorsa ayni gruptadir.
- Hacim, rekabet, trend veya "hangisi daha degerli" SORULMUYOR; siralama yapma,
  hicbir adayi eleme.
- HER adayi tam olarak bir gruba koy; hicbirini atlama.
ADAYLAR
__ADAYLAR__
Yalniz JSON: {"groups": [{"group_id": "g1", "ids": [<kelime id>, ...]}]}"""

V31_TEMPLATES: Dict[str, str] = {
    "authority": AUTHORITY_TEMPLATE,
    "urlgroup": URLGROUP_TEMPLATE,
}

# ── semalar (Gemini response_schema) ────────────────────────────────
AUTHORITY_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "authority": {"type": "string", "enum": list(AUTHORITY_LEVELS)},
                },
                "required": ["id", "authority"],
            },
        }
    },
    "required": ["results"],
}

URLGROUP_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "groups": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "group_id": {"type": "string"},
                    "ids": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["group_id", "ids"],
            },
        }
    },
    "required": ["groups"],
}

V31_SCHEMAS: Dict[str, Dict[str, Any]] = {
    "authority": AUTHORITY_SCHEMA, "urlgroup": URLGROUP_SCHEMA}


def V31_template_sha256(name: str) -> str:
    return hashlib.sha256(V31_TEMPLATES[name].encode("utf-8")).hexdigest()


def V31_all_template_sha256() -> Dict[str, Dict[str, str]]:
    return {name: {"version": V31_PROMPT_VERSIONS[name],
                   "template_sha256": V31_template_sha256(name)}
            for name in sorted(V31_TEMPLATES)}


def V31__listing(batch: Sequence[Dict[str, Any]]) -> str:
    return "\n".join(f'{it["id"]}: {it["keyword"]}' for it in batch)


def build_authority_prompt(profile: Dict[str, Any],
                           batch: Sequence[Dict[str, Any]]) -> str:
    return (AUTHORITY_TEMPLATE
            .replace("__FIRMA__", _firm(profile))
            .replace("__KELIMELER__", V31__listing(batch)))


def build_urlgroup_prompt(profile: Dict[str, Any], family: Dict[str, Any],
                          candidates: Sequence[Dict[str, Any]]) -> str:
    """`candidates`: {id, keyword, subintent_label} — her biri bir KUMENIN temsilcisi."""
    satirlar = []
    for it in candidates:
        etiket = it.get("subintent_label") or it.get("subintent_id") or ""
        satirlar.append(f'{it["id"]}: {it["keyword"]}  [alt niyet: {etiket}]')
    return (URLGROUP_TEMPLATE
            .replace("__FIRMA__", _firm(profile))
            .replace("__AILE_ADI__", str(family.get("name") or family.get("family_id")))
            .replace("__CORE_NEED__", str(family.get("core_need") or "-"))
            .replace("__ADAYLAR__", "\n".join(satirlar)))


def parse_authority(items: Sequence[Any], beklenen: Sequence[int]
                    ) -> Tuple[Dict[int, Dict[str, Any]], List[int]]:
    """(id -> {authority}, eksik id listesi). Bilinmeyen derece -> 'medium' DEGIL, eksik."""
    out: Dict[int, Dict[str, Any]] = {}
    for it in items or []:
        if not isinstance(it, dict):
            continue
        try:
            kid = int(it.get("id"))
        except (TypeError, ValueError):
            continue
        lvl = str(it.get("authority") or "").strip().lower()
        if lvl not in AUTHORITY_LEVELS:
            continue
        out[kid] = {"id": kid, "authority": lvl}
    eksik = [k for k in beklenen if k not in out]
    return out, eksik


def parse_urlgroups(groups: Sequence[Any], beklenen: Sequence[int]
                    ) -> Tuple[Dict[int, str], List[int]]:
    """(aday id -> grup kimligi, eksik id listesi). Bir aday birden fazla grupta
    gorunurse ILK grubu gecerlidir (deterministik)."""
    out: Dict[int, str] = {}
    for g in groups or []:
        if not isinstance(g, dict):
            continue
        gid = str(g.get("group_id") or "").strip()
        if not gid:
            continue
        for raw in g.get("ids") or []:
            try:
                kid = int(raw)
            except (TypeError, ValueError):
                continue
            out.setdefault(kid, gid)
    eksik = [k for k in beklenen if k not in out]
    return out, eksik


# ── model/thinking (seo_v3_seal) ──
MODEL = "gemini-3.8-flash"
THINKING_LEVEL = "low"
