"""Family V2 — aile promptlari ve dondurulmus sozlesme (URETIM KOPYASI).

plan_algoritma_entegrasyonu.md Faz 2. Bu dosya `scripts/nihai_akis_family_v2_prompt.py`
kilitli kaynagindan BIREBIR kopyalanmis sablonlari tasir; uretim kodu `scripts/`
icinden import ETMEZ (kilitler SHA ile baglidir). Esitlik
`tests/unit/test_engine_family_prompts.py` parity testleriyle kanitlanir:
sablon metinleri, semalar ve `frozen_contract()` kilitli kaynakla ayni olmak
ZORUNDADIR.

DIKKAT — iki ayri firma blogu vardir ve KARISTIRILAMAZ:
  * `app/core/engine/context.py::firm_block` (ADS/SEO/SOCIAL sinyalleri) temalari
    ve marka terimlerini TASIR;
  * buradaki `firm_block` DAHA DARDIR (yalniz sektor/marka/ozet/hedef kitle/
    urunler/hizmetler) — aile karari marka terimlerinden etkilenmemelidir.

`REFINE_TEMPLATE` ve `build_refine` sozlesme butunlugu icin korunur ancak
URETIMDE CAGRILMAZ: refinement turlari plan geregi uretime alinmaz
(bkz. algoritma/ADS_NIHAI_NICHE_KOD_HARITASI.md).

Bu modul saglayici cagrisi YAPMAZ; yalniz metin ve sema uretir.
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Sequence

FAMILY_V2_VERSION = "NIHAI-FAMILY-V2-2026-08-26-v2"

CONFIDENCE_VALUES = ("high", "medium", "low")

# Sartname sahibinin verdigi SEKIZ ornegin TAMAMI. Ucuncu satir en ayirt
# edici olan: icinde "kapatici" gectigi halde Sampuan ailesine gider.
BOSS_EXAMPLES: List[Sequence[str]] = [
    ("beyaz saçlar için şampuan", "Beyaz Saç Şampuanı"),
    ("beyazlık giderici şampuan", "Beyaz Saç Şampuanı"),
    ("beyaz saç kapatıcı şampuan", "Beyaz Saç Şampuanı"),
    ("beyaz saç kapatıcı", "Beyaz Saç Kapatıcı"),
    ("beyaz saç kapatma ürünü", "Beyaz Saç Kapatıcı"),
    ("beyaz saçı eski rengine döndürme", "Orijinal Renge Döndürme"),
    ("beyaz saçları doğal rengine çevirme", "Orijinal Renge Döndürme"),
    ("beyaz saç neden olur", "Beyaz Saç Nedenleri"),
]

# Uc asamada da AYNEN tekrarlanan ortak kural blogu.
RULES = """AILE TANIMI
Keyword ailesi, yazilisi birbirine benzeyen kelimelerin degil, AYNI TEMEL
KULLANICI IHTIYACINI ve AYNI URUN/HIZMET COZUMUNU ifade eden sorgularin
olusturdugu gruptur.

KARAR SIRASI (yukaridan asagiya; ustteki her zaman baskindir)
1. Temel kullanici ihtiyaci ayni mi? Kullanici temelde ayni seyi mi ariyor?
2. Aranan cozum / urun / hizmet turu ayni mi? Farkli urun beklentisi yaratan
   sorgular, kelimeleri cok benzese bile AYRI ailedir.
3. Anlamsal cekirdek ve entity ayni mi? Es anlamli, farkli kelime dizilimli
   veya uzun-kuyruk varyasyonlar ayni ihtiyaci anlatiyorsa AYNI ailedir.
4. Yazilis benzerligi EN SON ve yalniz yardimci sinyaldir.

BELIRLEYICI OLMAYANLAR
- Relevance aile belirleyicisi DEGILDIR. Iki kelimenin firmaya uygunlugunun
  yuksek olmasi onlari ayni aile YAPMAZ.
- Intent seviyesi aile belirleyicisi DEGILDIR. Ayni ihtiyacin arastirma,
  karsilastirma ve satin alma varyasyonlari AYNI ailede bulunabilir
  (ornek: "beyaz sac kapatici", "beyaz sac kapatici fiyatlari",
  "en iyi beyaz sac kapatici" ayni temel ihtiyacin varyasyonlaridir).

TEK IHTIYAC / TEK COZUM KURALI (BAGLAYICI)
Her aile TEK BIR temel kullanici ihtiyacini ve TEK BIR cozum/urun/hizmet
turunu temsil etmelidir. "boya ve sampuan" gibi KARMA solution_type
YASAKTIR. Bir ailenin core_need alani "X veya Y" bicimimde iki ayri ihtiyac
tasiyorsa o aile BOLUNMELIDIR.

ACIK URUN TURU KURALI (BAGLAYICI YORUM)
Sorgu acik bir urun turu iceriyorsa COZUM TURU korunur ve aile ona gore
secilir:
  beyaz sac kapatici sampuan                 ->  Sampuan ailesi
  beyaz saci dogal rengine donduren sampuan  ->  Sampuan ailesi
Bu kelimeler ELENMEZ; yalniz dogru aileye atanir.

GRANULARITE
Gereksiz mikro aileler olusturma; ayni ihtiyacin es anlamli ve uzun-kuyruk
varyasyonlarini bolme. Buna karsilik FARKLI kullanici ihtiyaclarini tek genis
aile altinda BIRLESTIRME. Renk, marka veya yazilis farki TEK BASINA yeni aile
sebebi DEGILDIR. Aile isimleri kisa, spesifik ve aciklayici olsun.

ORTAK KELIME TUZAGI
Ortak bir kelime tasimak tek basina ayni aile yapmaz. Asagidaki ornekler
sartname sahibi tarafindan verilmistir ve BAGLAYICIDIR:
__ORNEKLER__
Ucuncu ornege dikkat: "beyaz sac kapatici sampuan" icinde "kapatici" gectigi
halde Beyaz Sac Sampuani ailesine girer, cunku aranan COZUM bir sampuandir.

KAPSAM SINIRI — COK ONEMLI
Gorevin YALNIZ GRUPLAMAKTIR. Hicbir kelimeyi eleme, "urun disi", "alakasiz"
veya "uygun degil" diye isaretleme, kalite/uygunluk hukmu verme. Firmanin
urunuyle ortusmeyen bir ihtiyac gorursen onu da kendi ailesine koy. Eleme
karari bu adimin isi DEGILDIR."""


def _examples_block() -> str:
    width = max(len(k) for k, _ in BOSS_EXAMPLES)
    return "\n".join(f"  {k.ljust(width)}  ->  {fam}"
                     for k, fam in BOSS_EXAMPLES)


RULES_RENDERED = RULES.replace("__ORNEKLER__", _examples_block())

# ── Asama 1: Family Dictionary ──────────────────────────────────────

STAGE1_TEMPLATE = """Asagidaki firma icin, verilen anahtar kelime evreninin TAMAMINI kapsayan bir
AILE TAKSONOMISI (Family Dictionary) cikar.

FIRMA
__FIRMA__

__KURALLAR__

GOREV
Once butun listeyi oku ve hangi temel ihtiyac/cozum kumelerinin var oldugunu
belirle. Sonra her aile icin su alanlari uret:

- family_id      : kisa, sabit, kucuk harf ve alt cizgi (ornek: beyaz_sac_sampuani)
- family_name    : kisa, spesifik, aciklayici Turkce isim
- core_need      : kullanicinin temel ihtiyaci, tek cumle
- solution_type  : aranan cozum/urun/hizmet turu (ornek: sampuan, losyon,
                   boya, bilgi, hizmet, karsilastirma)
- entity         : aileyi ayiran konu/varlik
- examples       : bu listeden 3-6 ornek kelime
- do_not_confuse : bu aileyle karistirilmamasi gereken YAKIN ailelerin
                   family_id'leri ve ayrimin tek cumlelik gerekcesi

Taksonomi butun evreni kapsamali; hicbir belirgin ihtiyac disarida kalmamali.
Ayni ihtiyaci iki ayri aileye bolme.

ANAHTAR KELIMELER (__SAYI__ adet)
__KELIMELER__

Yalniz JSON: {"families": [{"family_id": "...", "family_name": "...", "core_need": "...", "solution_type": "...", "entity": "...", "examples": ["..."], "do_not_confuse": [{"family_id": "...", "reason": "..."}]}]}"""

STAGE1_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "families": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "family_id": {"type": "string"},
                    "family_name": {"type": "string"},
                    "core_need": {"type": "string"},
                    "solution_type": {"type": "string"},
                    "entity": {"type": "string"},
                    "examples": {"type": "array", "items": {"type": "string"}},
                    "do_not_confuse": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "family_id": {"type": "string"},
                                "reason": {"type": "string"},
                            },
                            "required": ["family_id", "reason"],
                        },
                    },
                },
                "required": ["family_id", "family_name", "core_need",
                             "solution_type", "entity", "examples"],
            },
        }
    },
    "required": ["families"],
}

# ── Asama 2: sabit sozluge atama ────────────────────────────────────

STAGE2_TEMPLATE = """Asagidaki firma icin anahtar kelimeleri SABIT aile sozlugune ata.

FIRMA
__FIRMA__

__KURALLAR__

AILE SOZLUGU (SABIT — sirali listedeki family_id'lerden birini sec)
__SOZLUK__

GOREV
Her kelime icin TAM OLARAK BIR family_id ver.
- Yalniz yukaridaki SABIT sozlukte bulunan family_id'lerden birini secebilirsin.
- YENI AILE ACAMAZSIN. Sozlukte gercekten uygun aile yoksa family_id yerine
  tam olarak "UNMATCHED" yaz. Zorlama bir aileye atama YAPMA.
- confidence: high (acik), medium (muhtemel), low (kararsiz).
- Kelimenin arama hacmi, rekabeti, firmaya uygunluk derecesi veya satin alma
  niyeti SORULMUYOR; bunlari tahmin etme ve karara katma.
- Hicbir kelimeyi eleme veya "uygun degil" diye isaretleme.

ANAHTAR KELIMELER
__KELIMELER__

Yalniz JSON: {"results": [{"id": <kelime id>, "family_id": "...", "confidence": "high"}]}"""

STAGE2_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "family_id": {"type": "string"},
                    "confidence": {"type": "string",
                                   "enum": list(CONFIDENCE_VALUES)},
                },
                "required": ["id", "family_id", "confidence"],
            },
        }
    },
    "required": ["results"],
}


# ── Asama 2B: butun UNMATCHED'ler TEK SEFERDE ──────────────────────

STAGE2B_TEMPLATE = """Asagidaki kelimeler mevcut aile sozlugunde uygun aile BULUNAMADIGI icin
UNMATCHED isaretlendi. HEPSI BIR ARADA degerlendirilecek; boylece eklenecek
yeni aileler batch sirasindan BAGIMSIZ olur.

FIRMA
__FIRMA__

__KURALLAR__

MEVCUT AILE SOZLUGU
__SOZLUK__

UNMATCHED KELIMELER (__SAYI__ adet)
__KELIMELER__

GOREV
1. Bu kelimelerin tamamina bak. Aralarinda MEVCUT sozlukteki bir aileye
   gercekten uyanlar varsa onlari isaretleme; yalniz gercekten yeni bir
   ihtiyac/cozum kumesi olusturanlar icin yeni aile oner.
2. Yeni aile onerirken ayni ihtiyaci ikiye bolme ve tek kelimelik mikro
   aileler uretme; birkac kelimenin ayni ihtiyaci paylastigi kumeler ara.
3. Her yeni aile icin ayni alanlari uret: family_id, family_name, core_need,
   solution_type, entity, examples, do_not_confuse.
4. family_id mevcut sozluktekilerden FARKLI olmali.

Hicbir kelimeyi eleme veya "uygun degil" diye isaretleme.

Yalniz JSON: {"new_families": [{"family_id": "...", "family_name": "...", "core_need": "...", "solution_type": "...", "entity": "...", "examples": ["..."], "do_not_confuse": [{"family_id": "...", "reason": "..."}]}]}"""

STAGE2B_SCHEMA: Dict[str, Any] = STAGE1_SCHEMA.copy()
STAGE2B_SCHEMA = {
    "type": "object",
    "properties": {"new_families": STAGE1_SCHEMA["properties"]["families"]},
    "required": ["new_families"],
}


def build_stage2b(profile: Dict[str, Any],
                  families: Sequence[Dict[str, Any]],
                  rows: Sequence[Dict[str, Any]]) -> str:
    listing = "\n".join(f'{row["keyword_id"]}: {row["keyword_text"]}'
                        for row in rows)
    return (STAGE2B_TEMPLATE
            .replace("__FIRMA__", firm_block(profile))
            .replace("__KURALLAR__", RULES_RENDERED)
            .replace("__SOZLUK__", dictionary_block(families))
            .replace("__SAYI__", str(len(rows)))
            .replace("__KELIMELER__", listing))



# ── Refinement: tek bir sorunlu ailenin hedefli bolunmesi ───────────

REFINE_TEMPLATE = """Asagidaki aile TEK IHTIYAC / TEK COZUM kuralini ihlal ediyor ve BOLUNMESI
gerekiyor. Yalniz bu ailenin uyelerini yeniden degerlendir.

FIRMA
__FIRMA__

__KURALLAR__

BOLUNECEK AILE
family_id     : __AILE_ID__
family_name   : __AILE_ADI__
core_need     : __CORE_NEED__
solution_type : __SOLUTION_TYPE__
ihlal         : __IHLAL__

DEGISMEYECEK MEVCUT AILELER (SABIT — bunlari yeniden tanimlama)
__SABIT_SOZLUK__

BU AILENIN UYELERI (__SAYI__ adet)
__KELIMELER__

GOREV
1. ONCE her uye icin, yukaridaki SABIT ailelerden birine gercekten ait olup
   olmadigina bak. Aitse yeni aile ACMA — o kelime sabit aileye tasinacak.
2. Geri kalanlar icin, her biri TEK ihtiyac ve TEK solution_type tasiyan yeni
   aileler uret. Karma tur yasaktir.
3. Renk, marka veya yazilis farki tek basina yeni aile sebebi DEGILDIR.
   Gereksiz mikro aile uretme; her yeni ailede birkac uye olmali.
4. Yeni family_id'ler hem birbirinden hem SABIT ailelerden FARKLI olmali.
5. Hicbir kelimeyi eleme veya "uygun degil" diye isaretleme.

Yalniz JSON: {"new_families": [{"family_id": "...", "family_name": "...", "core_need": "...", "solution_type": "...", "entity": "...", "examples": ["..."], "do_not_confuse": [{"family_id": "...", "reason": "..."}]}]}"""


def build_refine(profile: Dict[str, Any], target: Dict[str, Any],
                 violation: str, fixed: Sequence[Dict[str, Any]],
                 rows: Sequence[Dict[str, Any]]) -> str:
    listing = "\n".join(f'{row["keyword_id"]}: {row["keyword_text"]}'
                        for row in rows)
    return (REFINE_TEMPLATE
            .replace("__FIRMA__", firm_block(profile))
            .replace("__KURALLAR__", RULES_RENDERED)
            .replace("__AILE_ID__", target["family_id"])
            .replace("__AILE_ADI__", target["family_name"])
            .replace("__CORE_NEED__", target["core_need"])
            .replace("__SOLUTION_TYPE__", target["solution_type"])
            .replace("__IHLAL__", violation)
            .replace("__SABIT_SOZLUK__", dictionary_block(fixed))
            .replace("__SAYI__", str(len(rows)))
            .replace("__KELIMELER__", listing))


# ── Asama 3: dusuk guvenli atamalarin ikinci turu ───────────────────

STAGE3_TEMPLATE = """Asagidaki atamalar birinci turda KARARSIZ isaretlendi. Her biri icin yakin
aday aileler birlikte veriliyor. Kesin karari ver.

FIRMA
__FIRMA__

__KURALLAR__

GOREV
Her kelime icin, verilen aday aileler arasindan EN UYGUN olani sec ve tek
cumlelik gerekce yaz. Adaylardan hicbiri uymuyorsa NIHAI sozlukteki baska bir
family_id'yi de secebilirsin.

SOZLUK NIHAIDIR: yeni aile ACAMAZSIN, sozluk disinda family_id
URETEMEZSIN. Bu adim yalniz mevcut atamayi DEGISTIREBILIR.

KARARSIZ ATAMALAR
__KAYITLAR__

Yalniz JSON: {"results": [{"id": <kelime id>, "family_id": "...", "reason": "..."}]}"""

STAGE3_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "family_id": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["id", "family_id"],
            },
        }
    },
    "required": ["results"],
}


def firm_block(profile: Dict[str, Any]) -> str:
    parts = [f"Sektor: {profile.get('sector') or '-'}",
             f"Marka: {profile.get('brand_name') or '-'}",
             f"Ozet: {profile.get('brand_summary') or '-'}"]
    if profile.get("target_audience"):
        parts.append(f"Hedef kitle: {profile['target_audience']}")
    for field, label in (("products", "Urunler"), ("services", "Hizmetler")):
        values = profile.get(field) or []
        if values:
            parts.append(f"{label}: " + ", ".join(map(str, values)))
    return "\n".join(parts)


def build_stage1(profile: Dict[str, Any],
                 rows: Sequence[Dict[str, Any]]) -> str:
    listing = "\n".join(row["keyword_text"] for row in rows)
    return (STAGE1_TEMPLATE
            .replace("__FIRMA__", firm_block(profile))
            .replace("__KURALLAR__", RULES_RENDERED)
            .replace("__SAYI__", str(len(rows)))
            .replace("__KELIMELER__", listing))


def dictionary_block(families: Sequence[Dict[str, Any]]) -> str:
    lines = []
    for fam in families:
        avoid = ", ".join(x.get("family_id", "") for x
                          in (fam.get("do_not_confuse") or []))
        lines.append(
            f"- {fam['family_id']} | {fam['family_name']}\n"
            f"    ihtiyac: {fam['core_need']}\n"
            f"    cozum turu: {fam['solution_type']} | entity: {fam['entity']}"
            + (f"\n    karistirma: {avoid}" if avoid else ""))
    return "\n".join(lines)


def build_stage2(profile: Dict[str, Any], families: Sequence[Dict[str, Any]],
                 batch: Sequence[Dict[str, Any]]) -> str:
    listing = "\n".join(f'{row["keyword_id"]}: {row["keyword_text"]}'
                        for row in batch)
    return (STAGE2_TEMPLATE
            .replace("__FIRMA__", firm_block(profile))
            .replace("__KURALLAR__", RULES_RENDERED)
            .replace("__SOZLUK__", dictionary_block(families))
            .replace("__KELIMELER__", listing))


def build_stage3(profile: Dict[str, Any],
                 records: Sequence[Dict[str, Any]]) -> str:
    lines = []
    for rec in records:
        cands = " | ".join(rec["candidates"])
        lines.append(f'{rec["keyword_id"]}: {rec["keyword_text"]}\n'
                     f'    birinci tur: {rec["first_pass"]} '
                     f'({rec["confidence"]})\n'
                     f'    aday aileler: {cands}')
    return (STAGE3_TEMPLATE
            .replace("__FIRMA__", firm_block(profile))
            .replace("__KURALLAR__", RULES_RENDERED)
            .replace("__KAYITLAR__", "\n".join(lines)))


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def frozen_contract() -> Dict[str, Any]:
    return {
        "version": FAMILY_V2_VERSION,
        "kurallar_sha256": _sha(RULES_RENDERED),
        "stage1_sha256": _sha(STAGE1_TEMPLATE),
        "stage2_sha256": _sha(STAGE2_TEMPLATE),
        "stage2b_sha256": _sha(STAGE2B_TEMPLATE),
        "stage3_sha256": _sha(STAGE3_TEMPLATE),
        "refine_sha256": _sha(REFINE_TEMPLATE),
        "ornek_sayisi": len(BOSS_EXAMPLES),
        "prompta_verilmeyenler": ["relevance", "intent", "hacim", "rekabet",
                                  "trend", "referans etiket",
                                  "dogru keyword listesi"],
        "agirliklar": ("w1..w4 SAYISAL OLARAK UYGULANMADI — sayi verilmedi; "
                       "oncelik sirasi karar kurali olarak uygulanir"),
        "kapsam_siniri": ("Family Engine yalniz GRUPLAR; eleme veya 'urun disi' "
                          "karari VERMEZ. Filtreleme relevance/intent/formulun "
                          "isidir."),
        "sozluk_sabitligi": (
            "Asama 2 batch'leri YENI AILE ACAMAZ; uyum yoksa UNMATCHED. Butun "
            "UNMATCHED'ler TEK SEFERDE (Asama 2B) birlikte incelenir ve yeni "
            "aileler yalniz orada eklenir. Sonra sozluk YENIDEN DONDURULUR ve "
            "yeni SHA kaydedilir. Boylece aile sonucu batch sirasina BAGLI "
            "DEGILDIR."),
        "asama3_siniri": ("yalniz family_id degistirebilir; yeni aile "
                          "OLUSTURAMAZ"),
        "tek_ihtiyac_tek_cozum": ("Her aile TEK ihtiyac + TEK solution_type; "
                                  "karma tur ('boya ve sampuan') YASAK."),
        "acik_urun_turu": ("Sorgu acik urun turu iceriyorsa cozum turu "
                           "korunur; ornek 'beyaz saci dogal rengine donduren "
                           "sampuan' -> Sampuan ailesi. Bu kelimeler ELENMEZ."),
        "donmus": ("Aile bir kez uretilir; uc intent tekrarinda AYNI aileler "
                   "kullanilir."),
    }
