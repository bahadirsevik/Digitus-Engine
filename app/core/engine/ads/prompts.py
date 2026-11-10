"""ADS Niche — funnel ve Intent V4 promptlari (URETIM KOPYASI).

plan_algoritma_entegrasyonu.md Faz 3 · ADS kod haritasi 3. ve 4. adim.
Kilitli kaynaklardan BIREBIR kopyalanmistir; uretim kodu `scripts/` icinden
import ETMEZ, esitlik parity testleriyle kanitlanir:

  * funnel  : `scripts/ads_v3_ai.py` (Rel + huni + brand_type, tek cagri)
  * intent  : `scripts/nihai_akis_intent.py` (v2 tabani) ->
              `..._v3.py` (huni + bant kuyrugu turetmesi) ->
              `..._v4.py` (transactional tanimi geri gelir)
              Zincirin UCU DE gereklidir; uretim V4'u kullanir.

DIKKAT — UC AYRI FIRMA BLOGU vardir ve KARISTIRILAMAZ:
  * `app/core/engine/context.py::firm_block` — ADS funnel'inin blogu
    (temalar + marka terimleri DAHIL),
  * `app/core/engine/family/prompts.py::firm_block` — aile blogu (DAR),
  * bu dosyadaki `intent_firm_block` — Intent blogu (relevance/hacim/rekabet
    ICERMEZ, kural geregi).

Bu modul saglayici cagrisi YAPMAZ; yalniz metin ve sema uretir.
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict, Sequence, Tuple

from app.core.engine.context import firm_block as ads_firm_block

# ── funnel (Rel + huni + brand_type) ────────────────────────────────
FUNNEL_VALUES = ("transactional", "commercial", "informational")
BRAND_VALUES = ("yok", "kendi", "rakip")

FUNNEL_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "funnel": {"type": "string", "enum": list(FUNNEL_VALUES)},
                    "brand_type": {"type": "string", "enum": list(BRAND_VALUES)},
                    "relevance": {"type": "number"},
                },
                "required": ["id", "funnel", "brand_type", "relevance"],
            },
        }
    },
    "required": ["results"],
}

def build_funnel_prompt(profile: Dict[str, Any],
                        batch: Sequence[Dict[str, Any]]) -> str:
    brand_name = profile.get("brand_name") or "marka"
    listing = "\n".join(f'{row["keyword_id"]}: {row["keyword_text"]}'
                        for row in batch)
    return f"""Asagidaki firma icin Google Ads anahtar kelimelerini siniflandir.

FIRMA
{ads_firm_block(profile)}

GOREV
Her kelime icin UC alan uret:

1) funnel — satin alma niyeti sinifi. YALNIZ su ucunden biri:
   - transactional: dogrudan satin alma niyeti (ornek: "... satin al", "... fiyat")
   - commercial: karar oncesi arastirma, karsilastirma, fiyat sorgusu
     (ornek: "en iyi ...", "... karsilastirma")
   - informational: bilgi arayisi, satin alma sinyali yok
     (ornek: "... nasil yapilir", "... nedir")

2) brand_type — YALNIZ su ucunden biri:
   - yok: kelimede marka adi gecmiyor
   - kendi: kelimede BU firmanin markasi geciyor ({brand_name})
   - rakip: kelimede baska bir firmanin markasi geciyor

3) relevance — bu kelimenin firmanin isine uygunlugu, 0 ile 1 arasi ondalik.
   1'e yakin = firmanin sattigi/sundugu seyle dogrudan ilgili.
   0'a yakin = firmanin isiyle ilgisiz.

KURALLAR
- Marka iceren kelimeler de HUNI NIYETINE gore etiketlenir.
  Ornek: "{brand_name} fiyat" -> transactional (marka gecmesi sinifi degistirmez).
- Ciplak marka aramasi ("{brand_name}") transactional sayilir.
- funnel icin 0-100 arasi PUAN URETME. Yalniz yukaridaki uc siniftan birini yaz.
- Her kelime icin TAM olarak bir satir dondur; hicbirini atlama.

KELIMELER
{listing}

CIKTI
Yalniz JSON: {{"results": [{{"id": <kelime id>, "funnel": "...", "brand_type": "...", "relevance": 0.0}}]}}"""

# ── funnel prompt KIMLIGI (kilitli sozlesmeden) ─────────────────────
# Funnel prompt'unun sablon icinde surum etiketi YOKTUR; kilit bu yuzden
# SABIT bir kanonik fixture ile render edip icerik SHA'sini dondurmustur
# (benchmark/ads_nihai_niche_algorithm_LOCKED.json -> prompts.funnel).
# Uretim bu dondurulmus degeri KULLANIR; kendi ureteceigi bir hash'e
# (ornegin bos profil/bos batch render'i) BAGLANMAZ — oyle bir hash kilitle
# ilgisiz olur ve prompt degisse bile ayni kalabilirdi.
FUNNEL_VERSION_LABEL = "ADS-V3-FUNNEL-2026-08-22"
FUNNEL_CANONICAL_PROFILE: Dict[str, Any] = {
    "brand_name": "KILIT_FIXTURE", "sector": "test",
    "brand_summary": "ozet", "target_audience": "hedef",
    "products": ["urun"], "services": ["hizmet"],
    "brand_terms": ["marka"], "protected_themes": ["koru"],
    "exclude_themes": ["disla"],
}
FUNNEL_CANONICAL_ROWS: Sequence[Dict[str, Any]] = [
    {"keyword_id": 1, "keyword_text": "birinci kelime"},
    {"keyword_id": 2, "keyword_text": "ikinci kelime"},
]
FUNNEL_CANONICAL_RENDER_SHA256 = (
    "e426e0ae77b320693ec3df804ce6e7aef95c5f5c98c76112a36353494cd9d390")


def canonical_funnel_render() -> str:
    """Kilidin kanonik fixture'i ile uretim render'i (parity testi kullanir)."""
    return build_funnel_prompt(FUNNEL_CANONICAL_PROFILE, FUNNEL_CANONICAL_ROWS)


# ── Intent V4 zinciri (v2 tabani -> v3 -> v4) ───────────────────────
INTENT_BANDS: Dict[str, Dict[str, Any]] = {
    "transactional": {"low": 0.8000, "high": 1.0000,
                      "high_inclusive": True, "clamp_high": 1.0000},
    "commercial": {"low": 0.4000, "high": 0.8000,
                   "high_inclusive": False, "clamp_high": 0.7999},
    "informational": {"low": 0.1000, "high": 0.4000,
                      "high_inclusive": False, "clamp_high": 0.3999},
}

INTENT_DECIMALS = 4

INTENT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "funnel": {"type": "string", "enum": list(FUNNEL_VALUES)},
                    "intent": {"type": "number"},
                },
                "required": ["id", "funnel", "intent"],
            },
        }
    },
    "required": ["results"],
}


V2_TEMPLATE = """Asagidaki firma icin Google Ads anahtar kelimelerini degerlendir.

FIRMA
__FIRMA__

GOREV
Her kelime icin IKI alan uret:

1) funnel — satin alma niyeti sinifi. YALNIZ su ucunden biri:
   - transactional: dogrudan satin alma veya islem niyeti
     (ornek: "... satin al", "... siparis", "... fiyati")
   - commercial: karar oncesi arastirma, karsilastirma, secim
     (ornek: "en iyi ...", "... karsilastirma", "... onerileri")
   - informational: bilgi arayisi, satin alma sinyali yok
     (ornek: "... nedir", "... nasil yapilir")

2) intent — satin alma niyetinin SUREKLI gucu. 0 ile 1 arasi, DORT ONDALIK.
   Verdigin deger, yukarida sectigin funnel sinifinin bandi ICINDE olmalidir:
   - transactional  -> 0.8000 ile 1.0000 arasi (1.0000 dahil)
   - commercial     -> 0.4000 ile 0.8000 arasi (0.8000 HARIC)
   - informational  -> 0.1000 ile 0.4000 arasi (0.4000 HARIC)
   Band icinde nereye koyacagina kelimenin niyet gucu karar verir; bandin
   ortasi varsayilan deger DEGILDIR. Ayni sinifta daha guclu satin alma
   sinyali tasiyan kelime bandin ustune, zayif olan altina yakin olmalidir.

KURALLAR
- Marka iceren kelimeler de huni niyetine gore etiketlenir.
- 0-100 arasi PUAN URETME; intent yalniz 0 ile 1 arasi ondalik sayidir.
- Kelimenin arama hacmi, rekabeti veya firmaya uygunluk derecesi
  SORULMUYOR; bunlari tahmin etmeye calisma.
- Listedeki HER kelime icin tam olarak bir sonuc dondur; hicbirini atlama.

KELIMELER
__KELIMELER__

Yalniz JSON: {"results": [{"id": <kelime id>, "funnel": "...", "intent": 0.0000}]}"""

V2_TEMPLATE_SHA256 = hashlib.sha256(
    V2_TEMPLATE.encode("utf-8")).hexdigest()

def intent_firm_block(profile: Dict[str, Any]) -> str:
    """Marka baglami. Relevance/hacim/rekabet ICERMEZ (kural geregi)."""
    parts = [f"Sektor: {profile.get('sector') or '-'}",
             f"Marka: {profile.get('brand_name') or '-'}",
             f"Ozet: {profile.get('brand_summary') or '-'}"]
    if profile.get("target_audience"):
        parts.append(f"Hedef kitle: {profile['target_audience']}")
    for field, label in (("products", "Urunler"), ("services", "Hizmetler")):
        values = profile.get(field) or []
        if values:
            parts.append(f"{label}: " + ", ".join(map(str, values)))
    terms = profile.get("brand_terms") or []
    if terms:
        parts.append("Marka terimleri: " + ", ".join(map(str, terms)))
    return "\n".join(parts)

OLD_FUNNEL = """1) funnel — satin alma niyeti sinifi. YALNIZ su ucunden biri:
   - transactional: dogrudan satin alma veya islem niyeti
     (ornek: "... satin al", "... siparis", "... fiyati")
   - commercial: karar oncesi arastirma, karsilastirma, secim
     (ornek: "en iyi ...", "... karsilastirma", "... onerileri")
   - informational: bilgi arayisi, satin alma sinyali yok
     (ornek: "... nedir", "... nasil yapilir")"""

NEW_FUNNEL = """1) funnel — satin alma niyeti sinifi. YALNIZ su ucunden biri:

   - transactional: satin alma, siparis, basvuru, iletisim, fiyat alma veya
     dogrudan aksiyon dili.

   - commercial: kullanici bir PROBLEMI COZMEK, BELIRLI BIR SONUCU ELDE ETMEK,
     oneri/tavsiye bulmak, alternatif degerlendirmek veya uygun cozum aramak
     istiyor. Sunlardan HERHANGI BIRI commercial sayilir:
       * cozum arayisi
       * belirli bir sonucu elde etme istegi
       * oneri veya tavsiye arayisi
       * karsilastirma, "en iyi" / "hangisi" arayisi
       * urun veya hizmet KATEGORISI arayisi
       * acik urun adi bulunmasa bile, firma profilindeki cozumle DOGRUDAN
         ortusen bir sonuc arayisi

   - informational: YALNIZCA aciklama veya bilgi edinme. Cozum, urun ya da
     hizmet degerlendirme amaci YOKTUR.
     (ornek: "... nedir", "... neden olur", "... nasil calisir")

   URUN KELIMESI SART DEGILDIR. Sorguda urun adi, marka veya fiyat gecmemesi
   onu tek basina informational YAPMAZ. Belirleyici olan sudur: kullanici bir
   SONUC mu ariyor, yoksa yalnizca ACIKLAMA mi istiyor?

   Genel ornekler (farkli sektorlerden; degerlendirdigin listeyle ilgisi yok):
     "ayakkabi tabani neden asinir"    -> informational (aciklama)
     "kaygan zeminde kaymayan taban"   -> commercial (sonuc arayisi)
     "klima neden su damlatir"         -> informational (aciklama)
     "klima damlatmasini onlemek"      -> commercial (sonuc arayisi)
     "vergi beyannamesi nedir"         -> informational (aciklama)
     "vergi beyannamesi icin destek"   -> commercial (cozum arayisi)
     "en dayanikli valiz hangisi"      -> commercial (karsilastirma)
     "muhasebe programi satin al"      -> transactional (aksiyon)"""

# ── DEGISIKLIK 2: commercial band ici surekli deger rehberi ─────────

OLD_BAND_TAIL = """   Band icinde nereye koyacagina kelimenin niyet gucu karar verir; bandin
   ortasi varsayilan deger DEGILDIR. Ayni sinifta daha guclu satin alma
   sinyali tasiyan kelime bandin ustune, zayif olan altina yakin olmalidir."""

NEW_BAND_TAIL = """   Band icinde nereye koyacagina kelimenin niyet gucu karar verir; bandin
   ortasi varsayilan deger DEGILDIR. Ayni sinifta daha guclu satin alma
   sinyali tasiyan kelime bandin ustune, zayif olan altina yakin olmalidir.

   Commercial bandin ICINDE de surekli deger uret:
     0.4000 - 0.5500  zayif veya ortuk cozum arayisi
     0.5500 - 0.7000  acik oneri, karsilastirma veya sonuc arayisi
     0.7000 - 0.7999  transactional sinira yakin guclu aksiyon dili"""

V3_TEMPLATE = (V2_TEMPLATE
               .replace(OLD_FUNNEL, NEW_FUNNEL)
               .replace(OLD_BAND_TAIL, NEW_BAND_TAIL))

assert V3_TEMPLATE != V2_TEMPLATE, "v3 degisiklikleri uygulanmadi"
assert NEW_FUNNEL in V3_TEMPLATE and NEW_BAND_TAIL in V3_TEMPLATE



OLD_TRANSACTIONAL = """   - transactional: satin alma, siparis, basvuru, iletisim, fiyat alma veya
     dogrudan aksiyon dili."""

NEW_TRANSACTIONAL = """   - transactional: kullanici AKSIYONA hazir. Guclu transactional sinyaller:
       * satin alma, siparis, basvuru, iletisim dili
       * fiyat sorgusu ("... fiyati", "... ne kadar", "... ucreti")
       * BELIRLI bir urun/hizmet ozgullugu — kategori degil; somut bir urun,
         model, paket, surum veya adiyla tanimlanmis bir hizmet
       * Belirli bir hizmeti, saglayiciyi, uzmani veya LOKASYONA BAGLI hizmet
         sunucusunu dogrudan arayan; aciklama, karsilastirma veya arastirma
         dili TASIMAYAN sorgular, acik bir fiil bulunmasa da aksiyona yakin
         kabul edilir ve transactional olabilir.
     Bu sinyaller BIRLIKTE tartilir. Belirli bir urun/hizmet adi gecmesi TEK
     BASINA otomatik transactional YAPMAZ: kullanicinin COZUME YAKINLIGI ile
     sorgunun SOMUTLUGU birlikte degerlendirilir.

     KARAR TABLOSU
       somut urun/hizmet + aksiyon/fiyat dili        -> transactional
       belirli hizmet/saglayici + arastirma dili YOK -> transactional
       somut urun/hizmet + arastirma/karsilastirma   -> commercial
       genel urun/hizmet KATEGORISI arayisi          -> commercial
       yalnizca aciklama / neden / nasil calisir     -> informational

     Genel ornekler (farkli sektorlerden; degerlendirdigin listeyle ilgisi yok):
       "kadikoy kombi servisi"          -> transactional (belirli hizmet+lokasyon)
       "kombi servisleri nasil secilir" -> commercial (arastirma dili)
       "kombi neden su akitir"          -> informational (aciklama)
       "X model cadir fiyati"           -> transactional (somut urun + fiyat)
       "10 kisilik cadir nasil secilir" -> commercial (arastirma dili)"""

V4_TEMPLATE = V3_TEMPLATE.replace(OLD_TRANSACTIONAL, NEW_TRANSACTIONAL)

assert V4_TEMPLATE != V3_TEMPLATE, "v4 degisikligi uygulanmadi"

PROMPT_VERSION_V4 = "NIHAI-INTENT-2026-08-26-v4"
INTENT_TEMPLATE_SHA256 = hashlib.sha256(
    V4_TEMPLATE.encode("utf-8")).hexdigest()


def build_intent_prompt(profile: Dict[str, Any],
                        batch: Sequence[Dict[str, Any]]) -> str:
    """Intent V4 promptu — kilitli zincirin son halkasi."""
    listing = "\n".join(f'{row["keyword_id"]}: {row["keyword_text"]}'
                         for row in batch)
    return (V4_TEMPLATE
            .replace("__FIRMA__", intent_firm_block(profile))
            .replace("__KELIMELER__", listing))


def apply_band(funnel: str, intent: Any) -> Tuple[float, bool]:
    """Bandi funnel belirler; band disi intent GECERLI SINIRA kirpilir.

    Sartname huniyi IntentScore'un baglami sayar, yani huni yukaridadir.
    Ust siniri HARIC olan bantlarda kirpma hedefi `clamp_high`tir
    (commercial 0.7999, informational 0.3999).

    Donen ikinci deger kirpma yapildigini bildirir (ihlal sayaci icin).
    """
    band = INTENT_BANDS[funnel]
    if not isinstance(intent, (int, float)):
        return band["low"], True
    value = round(float(intent), INTENT_DECIMALS)
    if value < band["low"]:
        return band["low"], True
    if band["high_inclusive"]:
        if value > band["high"]:
            return band["clamp_high"], True
    elif value >= band["high"]:
        return band["clamp_high"], True
    return value, False