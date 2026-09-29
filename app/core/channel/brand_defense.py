"""Own-brand (marka savunma) bypass yardimcilari.

Sorun: Musterinin KENDI markasini iceren aramalar ("vepa tarak") AI tarafindan
"navigational" olarak siniflanip tum kanallardan eleniyordu; oysa kendi marka
aramasi en dusuk maliyetli / en yuksek donusumlu trafiktir.

Cozum: Onaylanmis (confirmed) BrandProfile'daki brand_terms + company_name
uzerinden deterministik bir bypass:

  - Intent kapisinda: eslesme varsa is_passed=True (hard-negative kontrolleri
    YINE de calisir ve gecersiz kilabilir — bkz. intent_analyzer).
  - Prefilter'da: eslesme AI'a hic gonderilmez; dogrudan is_kept=True +
    label="brand_defense" + extra_data.reason_code="OWN_BRAND_DEFENSE" yazilir.

Kurallar:
  - Eslesme token bazlidir (substring degil): cok kelimeli marka teriminin TUM
    token'lari keyword'de bulunmali ("Vepa Firca" -> hem "vepa" hem "firca";
    jenerik "dogal firca" eslesmez).
  - ADS: eslesme yeterli. SEO/SOCIAL: keyword, cekirdek marka token'lari
    DISINDA en az 1 token icermeli (ciplak "vepa" SEO konusu olamaz ama
    "vepa tarak" olabilir — brand_terms yalniz ["Vepa tarak"] olsa bile).
  - Negatif sinyal iceren sorgular (sikayet, is ilani, ikinci el...) bypass
    edilmez; normal AI filtrelerine birakilir. ADS icin liste daha genis
    (bilgi amacli sorgular reklam butcesi harcamamali), SEO/SOCIAL icin dar.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional, Tuple, FrozenSet

from sqlalchemy.orm import Session

from app.core.site_analyzer.turkish_normalizer import normalize_turkish
from app.database.models import BrandProfile, ScoringRun

logger = logging.getLogger(__name__)

BRAND_DEFENSE_REASON = "OWN_BRAND_DEFENSE"
BRAND_DEFENSE_LABEL = "brand_defense"

# Tum degerler normalize_turkish sonrasi (ASCII, kucuk harf) formdadir.
# Itibar / IK / servis / ikinci el sinyalleri: hicbir kanalda marka savunmasi
# kapsamina girmez (tum kanallar icin gecerli dar liste).
REPUTATIONAL_NEGATIVE_SIGNALS: Tuple[str, ...] = (
    "sikayet", "ariza", "bozuk", "tamir", "iade",
    "is ilani", "is ilanlari", "is basvurusu", "eleman", "kariyer", "staj",
    "insan kaynaklari", "maas",
    "ikinci el", "2 el", "sahibinden", "cikma",
)

# ADS'e ozel ek negatifler: bilgi amacli / dusuk deger sorgulari kendi marka
# olsa bile reklam havuzuna zorla sokulmamali (SEO icin bunlar mesru konudur).
ADS_EXTRA_NEGATIVE_SIGNALS: Tuple[str, ...] = (
    "nedir", "nasil", "ne demek", "ne ise yarar",
    "pdf", "indir", "ucretsiz", "bedava", "kiralik",
)


@dataclass(frozen=True)
class BrandDefenseContext:
    """Confirmed profil'den turetilen eslesme baglami."""

    # Her marka terimi = normalize token seti (tamami keyword'de aranir)
    term_token_sets: Tuple[FrozenSet[str], ...]
    # Cekirdek marka kimligi (or. {"vepa"}) — SEO/SOCIAL "marka disi token"
    # hesabi urunlu brand term'lerden degil bu cekirdekten yapilir.
    core_tokens: FrozenSet[str]
    # Prompt enjeksiyonu icin orijinal (ham) terimler
    original_terms: Tuple[str, ...]


def _tokenize(text: str) -> List[str]:
    """Normalize edip token'lara ayirir. 1 karakterlik token kanit sayilmaz."""
    return [t for t in normalize_turkish(text or "").split() if len(t) > 1]


def _derive_core_tokens(
    term_token_lists: List[List[str]],
    company_tokens: List[str],
) -> FrozenSet[str]:
    """Cekirdek marka token'larini turetir.

    1) Birden fazla terim varsa: tum terimlerde ortak gecen token'lar
       (or. Vepa'nin 5 teriminin hepsinde "vepa" var).
    2) Ortak kume bos ya da tek terim varsa: her terimin ILK token'i
       (Turkce'de marka adi basta gelir: "Vepa tarak") + firma adinin ilk
       token'i. Boylece brand_terms=["Vepa tarak"] iken bile cekirdek
       {"vepa"} olur ve "vepa tarak" SEO/SOCIAL'da urun token'i tasidigi
       icin gecebilir.
    """
    non_empty = [toks for toks in term_token_lists if toks]
    if len(non_empty) > 1:
        common = set(non_empty[0]).intersection(*non_empty[1:])
        if common:
            return frozenset(common)
    firsts = {toks[0] for toks in non_empty}
    if company_tokens:
        firsts.add(company_tokens[0])
    return frozenset(firsts)


def build_brand_defense_context(
    brand_terms: Optional[List[str]],
    company_name: Optional[str] = None,
) -> Optional[BrandDefenseContext]:
    """Ham terim listesi + firma adindan baglam kurar (saf fonksiyon)."""
    raw_terms = [
        t.strip() for t in (brand_terms or [])
        if isinstance(t, str) and t.strip()
    ]
    company_tokens = _tokenize(company_name) if isinstance(company_name, str) else []

    # Firma adi da bir eslesme terimi olarak eklenir ("vepa firca" sorgusu icin)
    all_terms = list(raw_terms)
    if isinstance(company_name, str) and company_name.strip():
        all_terms.append(company_name.strip())

    term_token_lists = [_tokenize(t) for t in all_terms]
    term_token_sets = tuple(
        frozenset(toks) for toks in term_token_lists if toks
    )
    if not term_token_sets:
        return None

    core_tokens = _derive_core_tokens(term_token_lists, company_tokens)
    return BrandDefenseContext(
        term_token_sets=term_token_sets,
        core_tokens=core_tokens,
        original_terms=tuple(all_terms),
    )


def load_brand_defense_context(
    db: Session,
    scoring_run: Optional[ScoringRun],
) -> Optional[BrandDefenseContext]:
    """Run'in CONFIRMED marka profilinden baglam yukler; yoksa None.

    Onaylanmamis (draft/pending) profil bypass kaynagi olamaz.
    """
    if scoring_run is None or not scoring_run.brand_profile_id:
        return None

    profile = (
        db.query(BrandProfile)
        .filter(
            BrandProfile.id == scoring_run.brand_profile_id,
            BrandProfile.status == "confirmed",
            BrandProfile.deleted_at.is_(None),
        )
        .first()
    )
    if not profile:
        return None

    data = profile.profile_data if isinstance(profile.profile_data, dict) else {}
    return build_brand_defense_context(
        data.get("brand_terms"),
        data.get("company_name") if isinstance(data.get("company_name"), str) else None,
    )


def load_product_definition(
    db: Session,
    scoring_run: Optional[ScoringRun],
) -> Optional[str]:
    """Confirmed profilden 'müşteri ürün tanımı' prompt bloğu üretir; yoksa None.

    Skorlama v2 Aşama 2: ADS sınıf-1 sorusu ("aranan şey müşterinin ürün
    kategorisinde bir çözüm mü?") ve SEO G_A derecesi müşterinin ürün
    tanımını prompt girdisi olarak ister.
    """
    if scoring_run is None or not scoring_run.brand_profile_id:
        return None

    profile = (
        db.query(BrandProfile)
        .filter(
            BrandProfile.id == scoring_run.brand_profile_id,
            BrandProfile.status == "confirmed",
            BrandProfile.deleted_at.is_(None),
        )
        .first()
    )
    if not profile:
        return None

    data = profile.profile_data if isinstance(profile.profile_data, dict) else {}

    def _join(values, limit):
        if not isinstance(values, list):
            return None
        items = [str(v).strip() for v in values if isinstance(v, str) and v.strip()]
        return ", ".join(items[:limit]) if items else None

    parts = []
    company = data.get("company_name")
    if isinstance(company, str) and company.strip():
        parts.append(f"Firma: {company.strip()}")
    sector = data.get("sector")
    if isinstance(sector, str) and sector.strip():
        parts.append(f"Sektör: {sector.strip()}")
    products = _join(data.get("products"), 10)
    if products:
        parts.append(f"Ürünler: {products}")
    services = _join(data.get("services"), 10)
    if services:
        parts.append(f"Hizmetler: {services}")
    audience = data.get("target_audience")
    if isinstance(audience, str) and audience.strip():
        parts.append(f"Hedef kitle: {audience.strip()}")
    problems = _join(data.get("problems_solved"), 5)
    if problems:
        parts.append(f"Çözdüğü problemler: {problems}")

    return "\n".join(parts) if parts else None


def _has_negative_signal(norm_text: str, token_set: set, signals: Tuple[str, ...]) -> bool:
    """Negatif sinyal kontrolu: tek kelimeler token bazli, cok kelimeliler
    kelime sinirli phrase bazli aranir."""
    padded = f" {norm_text} "
    for sig in signals:
        if " " in sig:
            if f" {sig} " in padded:
                return True
        elif sig in token_set:
            return True
    return False


def is_own_brand_keyword(
    keyword_text: str,
    ctx: Optional[BrandDefenseContext],
    channel: str,
) -> bool:
    """Keyword musterinin kendi marka terimi mi (bypass hak ediyor mu)?"""
    if ctx is None or not keyword_text:
        return False

    norm = normalize_turkish(keyword_text)
    tokens = [t for t in norm.split() if len(t) > 1]
    token_set = set(tokens)
    if not token_set:
        return False

    # Marka terimi eslesmesi: herhangi bir terimin TUM token'lari mevcut olmali
    if not any(term_set <= token_set for term_set in ctx.term_token_sets):
        return False

    # Negatif sinyal: bypass yok, normal filtreler karar versin
    signals = REPUTATIONAL_NEGATIVE_SIGNALS
    if channel == "ADS":
        signals = signals + ADS_EXTRA_NEGATIVE_SIGNALS
    if _has_negative_signal(norm, token_set, signals):
        return False

    # SEO/SOCIAL: ciplak marka adi icerik konusu olamaz; cekirdek marka
    # token'lari disinda en az 1 token (urun/nitelik) gerekli.
    if channel in ("SEO", "SOCIAL") and not (token_set - ctx.core_tokens):
        return False

    return True
