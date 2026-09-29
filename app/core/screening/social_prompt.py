"""SOCIAL retrieval DeepSeek prompt'u — SOCIAL-DEEPSEEK-PROMPT-2026-08-17-v1.

Bu modul, mühürlü prompt sozlesmesinin (benchmark/social_deepseek_prompt_contract.json,
sha256 29E32A21...) kod karsiligidir. Prompt METNI deneyin girdisidir; degistirilirse
ayni deneyden bahsedilemez.

YASAKLAR (sozlesme forbidden_context):
  * patron pozitif/negatifleri, final liste, final rank
  * v2.1 SOCIAL skoru (N_SOC), arama hacmi, hacim sirasi
  * test sonucu ornekleri, benchmark dosyalari, "bu kelimeyi sec" ipuclari
Baseline skoru YALNIZ RRF tarafinda kullanilir; buraya GIRMEZ.
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Sequence

# Sozlesme: reason_code_enum (14 deger)
REASON_CODES = (
    "SOCIAL_RELEVANT", "AUTHORITY_CONTENT", "EDUCATIONAL_CONTENT",
    "ENGAGEMENT_OPPORTUNITY", "PRODUCT_DISCOVERY", "AUDIENCE_PROBLEM",
    "TOO_GENERIC", "BRAND_NAVIGATION", "OFF_TOPIC", "JOB_OR_CAREER",
    "SUPPORT_ONLY", "CHANNEL_MISMATCH", "MISSING_CONTEXT", "AMBIGUOUS",
)

# Prompt'a gecirilmesine IZIN VERILEN baglam anahtarlari (allowlist)
ALLOWED_CONTEXT_KEYS = (
    "product_definition",
    "target_audience",
    "content_strategy",
    "social_mode",
    "b2b_b2c",
    "geography",
    "language",
    "brand_competitor_policy",
    "verified_off_topic",
)

RESPONSE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "keyword_id": {"type": "integer"},
                    "business_fit": {"type": "integer"},
                    "social_fit": {"type": "integer"},
                    "content_actionability": {"type": "integer"},
                    "strategy_fit": {"type": "integer"},
                    "uncertain": {"type": "boolean"},
                    "reason_code": {"type": "string", "enum": list(REASON_CODES)},
                    "short_reason": {"type": "string"},
                },
                "required": ["keyword_id", "business_fit", "social_fit",
                             "content_actionability", "strategy_fit",
                             "uncertain", "reason_code"],
            },
        }
    },
    "required": ["results"],
}

SYSTEM_TEMPLATE = """Sen bir sosyal medya icerik stratejisti olarak calisiyorsun.

GOREVIN TEK SORU: Verilen her anahtar kelime, bu firmanin hedef kitlesi ve ONAYLI
sosyal medya stratejisi icin anlamli bir SOSYAL ICERIK FIRSATI midir?

SU SORULARI CEVAPLAMIYORSUN (bunlar baska katmanlarin karari):
- Kelime final listeye alinmali mi?
- Aylik arama hacmi yeterli mi?
- Listede kapasite var mi?
- Daha iyi bir es anlamlisi var mi?
- Kelimenin mevcut siralamasi nedir?
Bu konularda yorum YAPMA; sana bu bilgiler VERILMEMISTIR.

FIRMA BAGLAMI
-------------
Urun/hizmet tanimi:
{product_definition}

Onayli icerik stratejisi:
{content_strategy}

Onayli sosyal mod: {social_mode}
Is modeli: {b2b_b2c}
Cografya / dil: {geography} / {language}
Marka-rakip politikasi: {brand_competitor_policy}
Dogrulanmis konu-disi alanlar: {verified_off_topic}

SOSYAL MOD SEMANTIGI
--------------------
- social_mode = authority ise: uzmanlik, egitim, kanit ve GUVEN OLUSTURAN icerik one
  cikar. Guncellik/viral olma tek basina deger DEGILDIR.
- social_mode = hype ise: guncellik, paylasilabilirlik ve konusulabilirlik daha
  onemli olabilir.
- Mod belirtilmemisse KENDI BASINA mod UYDURMA: uncertain=true ve
  reason_code=MISSING_CONTEXT dondur.

PUANLAMA (her alan 0-3)
-----------------------
0 = uygun degil | 1 = zayif veya dolayli | 2 = kullanilabilir | 3 = guclu firsat

- business_fit          : Kelimenin firmanin ISIYLE iliskisi
- social_fit            : SOSYAL MEDYA kanalina uygunlugu
- content_actionability : Bu kelimeden gercekten ANLAMLI ICERIK cikarilabilmesi
- strategy_fit          : Firmanin ONAYLI stratejisiyle (yukaridaki mod) uyumu

uncertain = true YALNIZCA baglam karar vermeye YETMIYORSA.

GEREKCE KODU (yalniz bu listeden biri)
--------------------------------------
{reason_codes}

Bu listede "hacim dusuk", "kapasite yok" veya "duplicate" YOKTUR — cunku bunlar
icerik firsati sorusunun cevabi DEGILDIR.

CIKTI
-----
Yalniz gecerli JSON dondur. Her girdi kelimesi icin TAM BIR sonuc nesnesi uret;
keyword_id degerlerini AYNEN koru. short_reason en fazla 15 kelime olsun ve yalniz
denetim icindir.
"""

USER_TEMPLATE = """Degerlendirilecek anahtar kelimeler ({count} adet):

{keywords}

Her biri icin JSON sonucu uret."""


def _fmt(value: Any) -> str:
    if value is None or value == "":
        return "(belirtilmemis)"
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value) or "(belirtilmemis)"
    return str(value)


def build_context(raw: Dict[str, Any]) -> Dict[str, str]:
    """Baglam sozlugunu ALLOWLIST'e indirger. Baska anahtar TASINMAZ."""
    return {k: _fmt(raw.get(k)) for k in ALLOWED_CONTEXT_KEYS}


def build_system_prompt(context: Dict[str, str]) -> str:
    safe = build_context(context)
    return SYSTEM_TEMPLATE.format(
        reason_codes="\n".join(f"- {c}" for c in REASON_CODES), **safe)


def build_user_prompt(batch: Sequence[Dict[str, Any]]) -> str:
    """Batch: [{"keyword_id": int, "keyword": str}, ...]

    Kelimeler CAGIRAN tarafinda keyword_id ARTAN siraya konur (sozlesme:
    baseline sirasini sizdirmamak icin).
    """
    lines = [f"{i['keyword_id']}\t{i['keyword']}" for i in batch]
    return USER_TEMPLATE.format(count=len(batch), keywords="\n".join(lines))


def prompt_text_sha256(context: Dict[str, str]) -> str:
    """Sistem prompt'unun (baglam dahil) mühürlenebilir SHA'si."""
    return hashlib.sha256(
        build_system_prompt(context).encode("utf-8")).hexdigest()


def template_sha256() -> str:
    """Baglamdan BAGIMSIZ sablon SHA'si — iki firmada da aynidir."""
    payload = SYSTEM_TEMPLATE + USER_TEMPLATE + "|".join(REASON_CODES)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def deepseek_fit(result: Dict[str, Any]) -> int:
    """Sozlesme: esit agirlikli toplam; baseline rank ICERIDE DEGIL."""
    return (int(result["business_fit"]) + int(result["social_fit"])
            + int(result["content_actionability"]) + int(result["strategy_fit"]))


def rank_key(result: Dict[str, Any], baseline_rank: int) -> tuple:
    """Sozlesme siralamasi: fit DESC, uncertain=False once, business_fit DESC,
    baseline rank ASC, keyword_id ASC."""
    return (-deepseek_fit(result),
            1 if result.get("uncertain") else 0,
            -int(result["business_fit"]),
            baseline_rank,
            int(result["keyword_id"]))


FORBIDDEN_TOKENS = (
    "monthly_volume", "social_score", "n_soc", "final_rank", "final_pool",
    "patron", "hacim", "volume", "rank", "secildi", "positive", "negatif",
)


def scan_prompt_for_leakage(text: str) -> List[str]:
    """Statik leakage taramasi — prompt metninde yasak token var mi?

    'rank' ve 'volume' gibi tokenlar prompt'ta YALNIZ 'sana verilmemistir'
    baglaminda gecebilir; bu yuzden bulgular cagirana dondurulur ve
    beklenen istisnalar orada beyaz listeye alinir.
    """
    low = text.lower()
    return [t for t in FORBIDDEN_TOKENS if t in low]
