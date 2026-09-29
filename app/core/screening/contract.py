"""Corpus screening DONDURULMUŞ sözleşmesi (plan_ai.md §4a/§5 — freeze paketi).

TEK SABİT KAYNAĞI (Codex freeze şartı #1): batch boyutu, reason-code uzunluk
kısıtı ve max_output_tokens BURADA tanımlıdır; çıktı şeması, runner ve ölçüm
scripti (scripts/measure_screening_output_budget.py) bu modülden okur.

FREEZE DİSİPLİNİ: Bu dosyadaki prompt şablonu, RC-v1 sözlüğü ve şema,
bake-off sonuçlarına bakılmadan önce commit ile dondurulur
(tests/unit/test_screening_contract.py SHA kilidi). Bilinçli değişiklik =
YENİ PROMPT_VERSION + registry'ye YENİ kayıt (append-only); mevcut kayıt
düzenlenmez. Model karşılaştırması başladıktan sonra sağlayıcıya özel
prompt iyileştirmesi YAPILMAZ (plan_ai §4).

Terminoloji (Codex): karakter sayıları ÖLÇÜMDÜR; token sayıları karakter/
token oranından TAHMİNDİR — gerçek provider tokenizer sonucu bake-off
raporunda `output_tokens` alanından ayrıca gösterilir.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

# ── Sabitler (tek kaynak) ────────────────────────────────────────────────
# v2 (27.07): puan açıklamaları "- 2: ..." biçimindeydi ve ANAHTAR KELİMELER
# bloğunun "- {id}: {keyword}" biçimiyle ÇAKIŞIYORDU (satır-bazlı ayrıştırma
# puan satırlarını keyword sanıyordu). Ücretli koşu YAPILMADAN, sonuçlara
# bakılmadan düzeltildi; v1 kaydı registry'de KALIR (append-only).
PROMPT_VERSION = "SCR-2026-07-27-v2"
REASON_CODE_VERSION = "RC-v1"
BATCH_SIZE = 30
MAX_REASON_CODE_LEN = 24
MAX_OUTPUT_TOKENS = 5888  # ölçüm: scripts/measure_screening_output_budget.py
TEMPERATURE = 0.3
PERMUTATION_SEEDS = (20260727, 42)
# Thinking konfigürasyonu da dondurulan üretim parametresidir (plan_ai §4a)
GEMINI_THINKING_LEVEL = "low"
DEEPSEEK_THINKING = {"type": "disabled"}
# Token TAHMİN oranları (karakter/token) — ölçüm scripti ve freeze testi
# aynı formülü kullanır: güvenli_tahmin = ceil(char / SAFE) ve
# öneri = ceil(güvenli_tahmin * PAY / 256) * 256
CHARS_PER_TOKEN_REALISTIC = 3.5
CHARS_PER_TOKEN_SAFE_UPPER = 2.0
SAFETY_MULTIPLIER = 1.5

_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")

# ── RC-v1 reason-code sözlüğü (kanal başına kapalı liste) ────────────────
# Kurallar: <= MAX_REASON_CODE_LEN karakter, UPPER_SNAKE_CASE, serbest
# metin yok. Enum üyeliği PARSE aşamasında doğrulanır (repo sözleşmesi:
# enum'lar provider şemasına gömülmez). Uzunluk+karakter kısıtını PARSE
# tarafındaki Pydantic şeması taşır; provider'a giden RESPONSE_SCHEMA
# bilinçli olarak minimal tip alt-kümesidir (maxLength/pattern YOK) ve
# freeze testinde kanonik hash ile ayrıca kilitlidir.
RC_V1: Dict[str, tuple] = {
    "ads": (
        "COMMERCIAL_FIT",        # net ürün/hizmet talebi, dönüşüm potansiyeli
        "LEAD_POTENTIAL",        # doğrudan satış değil ama lead üretebilir
        "NO_PURCHASE_INTENT",    # bilgi arama, satın alma niyeti yok
        "PRODUCT_MISMATCH",      # ürün/hizmet alanının dışında
        "COMPETITOR_BRAND",      # rakip marka araması
        "AMBIGUOUS",             # belirsiz/yakın — fit=1 tipik nedeni
    ),
    "seo": (
        "STRATEGY_FIT",          # içerik stratejisi beyanına net uygun
        "STRATEGY_NEAR",         # beyana yakın/sınırda
        "JOURNEY_SUPPORT",       # hedef kullanıcı yolculuğunu destekler
        "OFF_STRATEGY",          # beyanın dışında
        "THIN_VALUE",            # içerik değeri üretmeyecek kadar zayıf
        "AMBIGUOUS",
    ),
    "social": (
        "HIGH_DISCUSSABILITY",   # tartışma/etkileşim potansiyeli yüksek
        "CURIOSITY_HOOK",        # merak/karşılaştırma kancası var
        "AGENDA_POTENTIAL",      # gündem/tema içeriği olabilir
        "LOW_DISCUSSABILITY",    # konuşulabilirlik düşük
        "NICHE_TECHNICAL",       # dar teknik konu, paylaşım değeri düşük
        "AMBIGUOUS",
    ),
}


# ── Çıktı şeması (gerçek Pydantic — parse + freeze boyut testi bunu kullanır)
class ScreeningReasonCodes(BaseModel):
    ads: str = Field(..., max_length=MAX_REASON_CODE_LEN)
    seo: str = Field(..., max_length=MAX_REASON_CODE_LEN)
    social: str = Field(..., max_length=MAX_REASON_CODE_LEN)

    @field_validator("ads", "seo", "social")
    @classmethod
    def _shape(cls, v: str) -> str:
        if not _CODE_RE.match(v):
            raise ValueError(f"reason_code biçimi geçersiz: {v!r}")
        return v


class ScreeningItem(BaseModel):
    id: int = Field(..., ge=0)
    ads_fit: int = Field(..., ge=0, le=2)
    seo_fit: int = Field(..., ge=0, le=2)
    social_fit: int = Field(..., ge=0, le=2)
    reason_codes: ScreeningReasonCodes


class ScreeningBatchOutput(BaseModel):
    results: List[ScreeningItem]


def validate_reason_code_membership(item: ScreeningItem) -> List[str]:
    """Enum üyeliği parse-aşaması doğrulaması (şemaya gömülmez).

    Dönüş: ihlal listesi (boş = temiz). Üyelik dışı kod fit'i DÜŞÜRMEZ —
    ihlal raporlanır ve targeted retry'a konu olur (runner sözleşmesi).
    """
    violations = []
    codes = item.reason_codes
    for ch in ("ads", "seo", "social"):
        if getattr(codes, ch) not in RC_V1[ch]:
            violations.append(f"{ch}:{getattr(codes, ch)}")
    return violations


# Provider'a verilecek sade JSON şeması (repo kalıbı: type/properties/
# required; enum YOK — parse'ta doğrulanır)
RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "ads_fit": {"type": "integer"},
                    "seo_fit": {"type": "integer"},
                    "social_fit": {"type": "integer"},
                    "reason_codes": {
                        "type": "object",
                        "properties": {
                            "ads": {"type": "string"},
                            "seo": {"type": "string"},
                            "social": {"type": "string"},
                        },
                        "required": ["ads", "seo", "social"],
                    },
                },
                "required": [
                    "id", "ads_fit", "seo_fit", "social_fit", "reason_codes",
                ],
            },
        }
    },
    "required": ["results"],
}


# ── Prompt şablonu (SCR-2026-07-27-v1, SHA-kilitli) ──────────────────────
# Genel "markayla ilgili mi?" sorusu YOK (plan_ai §5 — ayırt edici değil);
# üç kanal bağımsız, kanalın gerçek iş hedefiyle sorulur. Bağlam dispatch-anı
# onaylı beyanlardan gelir. reason_code YALNIZ izinli listeden.
PROMPT_TEMPLATE = """Sen bir dijital pazarlama kanal-uygunluk sınıflandırıcısısın.
Aşağıdaki HER anahtar kelimeyi ÜÇ kanal için BAĞIMSIZ değerlendir.

MÜŞTERİ ÜRÜN/HİZMET TANIMI:
{product_definition}

ONAYLI İÇERİK STRATEJİSİ BEYANI:
{content_strategy}

HEDEF KİTLE:
{target_audience}

SOSYAL STRATEJİ MODU: {social_mode}

KANAL SORULARI (her kelime için ayrı ayrı puanla):
- ads_fit: Bu arama, müşterinin ürün/hizmetine TALEP, LEAD veya DÖNÜŞÜM
  üretebilir mi? (Reklam tıklaması satışa/lead'e dönüşür mü?)
- seo_fit: Bu kelime için üretilecek içerik, ONAYLI İÇERİK STRATEJİSİ
  BEYANINA ve hedef kullanıcı yolculuğuna katkı sağlar mı?
- social_fit: Bu kelime sosyal medyada KONUŞULABİLİR mi — tartışma, merak,
  karşılaştırma, gündem veya paylaşılabilirlik değeri taşır mı?

PUANLAR (her kanal için):
2 = güçlü uygunluk
1 = belirsiz/yakın uygunluk (emin değilsen 1 ver — silme kararı SENDE değil)
0 = düşük uygunluk

reason_codes: Her kanal için AŞAĞIDAKİ LİSTEDEN TAM OLARAK BİR kod seç.
Serbest metin, açıklama, yeni kod YASAK.
- ads: {ads_codes}
- seo: {seo_codes}
- social: {social_codes}

ANAHTAR KELİMELER:
{keywords_block}

ÇIKTI: Yalnız geçerli JSON. Şema:
{{"results":[{{"id":<int>,"ads_fit":<0|1|2>,"seo_fit":<0|1|2>,"social_fit":<0|1|2>,"reason_codes":{{"ads":"<KOD>","seo":"<KOD>","social":"<KOD>"}}}}]}}
KURALLAR:
- Girdideki HER id çıktıda TAM BİR KEZ bulunmalı; id ekleme/çıkarma YASAK.
- JSON dışında hiçbir şey yazma (markdown fence dahil).
"""


# v3a (27.07): TEK DEĞİŞKEN — bağımsızlık talimatı. Metin, Dijital
# sonuçlarına BAKILMADAN ÖNCE kilitlendi (holdout disiplini: GR7 aynı
# metinle, yeniden ayar YAPILMADAN koşulur).
INDEPENDENCE_BLOCK = """BAĞIMSIZLIK KURALI (ZORUNLU):
Aşağıdaki liste yalnızca toplu işlem içindir; kelimeler birbirinin bağlamı
DEĞİLDİR. Her kelimeyi, listedeki diğer kelimeler hiç yokmuş gibi TEK BAŞINA
değerlendir. Bir kelimenin puanı; listedeki komşularına, listedeki sırasına
veya listenin genel havasına göre DEĞİŞMEMELİDİR. Aynı kelime bambaşka bir
listede görülse de AYNI puanı almalıdır."""

PROMPT_TEMPLATE_V3A = PROMPT_TEMPLATE.replace(
    "ANAHTAR KELİMELER:", INDEPENDENCE_BLOCK + "\n\nANAHTAR KELİMELER:"
)

# Sürümlü şablon kaydı — APPEND-ONLY (mevcut sürüm metni DEĞİŞTİRİLMEZ)
PROMPT_TEMPLATES: Dict[str, str] = {
    "SCR-2026-07-27-v2": PROMPT_TEMPLATE,
    "SCR-2026-07-27-v3a": PROMPT_TEMPLATE_V3A,
}


def build_prompt(*, product_definition: str, content_strategy: str,
                 target_audience: str, social_mode: str,
                 keywords: List[Dict],
                 prompt_version: Optional[str] = None) -> str:
    """keywords: [{"id": int, "keyword": str}, ...] (<= BATCH_SIZE).

    `prompt_version` verilmezse ÜRETİM varsayılanı (PROMPT_VERSION);
    deney scriptleri sürümü AÇIKÇA geçirir ve manifest'e yazar.
    """
    keywords_block = "\n".join(
        f"- {kw['id']}: {kw['keyword']}" for kw in keywords
    )
    return PROMPT_TEMPLATES[prompt_version or PROMPT_VERSION].format(
        product_definition=product_definition,
        content_strategy=content_strategy,
        target_audience=target_audience,
        social_mode=social_mode,
        ads_codes=", ".join(RC_V1["ads"]),
        seo_codes=", ".join(RC_V1["seo"]),
        social_codes=", ".join(RC_V1["social"]),
        keywords_block=keywords_block,
    )


def worst_case_batch_payload() -> str:
    """Freeze boyut testi + ölçüm scripti ortak en-kötü-durum üreticisi.

    GERÇEK Pydantic şemasıyla kurulur (Codex freeze şartı #3): şemaya alan
    eklenirse payload büyür ve bütçe testi kırılır. Kodlar şema-geçerli
    en kötü uzunlukta (MAX_REASON_CODE_LEN, sentetik); en kötü biçim
    pretty-print JSON'dur.
    """
    code = "X" * MAX_REASON_CODE_LEN
    batch = ScreeningBatchOutput(results=[
        ScreeningItem(
            id=999999, ads_fit=2, seo_fit=2, social_fit=2,
            reason_codes=ScreeningReasonCodes(ads=code, seo=code, social=code),
        )
        for _ in range(BATCH_SIZE)
    ])
    return batch.model_dump_json(indent=2)
