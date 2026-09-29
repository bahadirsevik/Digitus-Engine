"""SOCIAL — V5 kapilar ve Priority (URETIM KOPYASI).

plan_algoritma_entegrasyonu.md Faz 5 · SOCIAL kod haritasi 6. ve 9. adim.
Kilitli `scripts/social_v5_engine.py` davranisinin birebir kopyasidir.

    kapilar : Rel >= 40  ·  RF >= 50  ·  BC >= 50
    priority: gate -> COMMERCIAL -> ABSTAINED -> SS < 40 -> TREND / CN / PE

Skor cekirdegi (`normalize_trend`, `normalize_volume`, `compute_trend_core`,
...) BU DOSYADA YAZILMAZ; `score.py`dan yeniden kullanilir — kilitli kaynak da
ayni ayrimi yapar.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from app.core.engine.social.score import (  # noqa: F401  (bilincli re-export)
    MissingSignal,
    NormBounds,
    assert_unique_keyword_ids,
    compute_creative_score,
    compute_norm_bounds,
    compute_social_score,
    compute_trend_core,
    compute_trend_opportunity,
    compute_volume_support,
    normalize_trend,
    normalize_volume,
    order_key,
    score_row,
)

PLAN_VERSION = "SOCIAL-V5-INTENT-2026-09-14-v0.4"
SOURCE_DOC_SHA256 = "77ad3840f82a4fc9c9392aa120baf5c0983d3962827568807813174e30302824"

# ── kapilar: YENI dokuman §4 (UC kural, BC bandi YOK) ──────────────────────

RELEVANCE_GATE_MIN = 40.0
RF_GATE_MIN = 50.0
BC_GATE_MIN = 50.0

#: Eski dokumanin `BC>=60` Primary kapisi. YENI dokumanda YOK -> UYGULANMAZ.
#: Sabit yalnizca "bilincli olarak uygulanmiyor" kaydi icin durur; hicbir kod
#: yolu bunu okumaz (test bunu dogrular).
LEGACY_BC_PRIMARY_MIN_NOT_APPLIED = 60.0

GATE_ELIGIBLE = "eligible"
GATE_SKIP = "skip"

# ── niyet sinifi: TEK SECIMLI enum (plan §4.2 V2) ──────────────────────────

INTENT_CONTENT_NATIVE = "CONTENT_NATIVE"
INTENT_PRODUCT_EDUCATION = "PRODUCT_EDUCATION"
INTENT_COMMERCIAL_SEARCH = "COMMERCIAL_SEARCH"
INTENT_TREND_OPPORTUNITY = "TREND_OPPORTUNITY"

INTENT_CLASSES: Tuple[str, ...] = (
    INTENT_CONTENT_NATIVE,
    INTENT_PRODUCT_EDUCATION,
    INTENT_COMMERCIAL_SEARCH,
    INTENT_TREND_OPPORTUNITY,
)

#: Kapida elenen satir sinifllandiriciya HIC GONDERILMEZ (plan §4.2 V5).
INTENT_NOT_EVALUATED = "NOT_EVALUATED"
#: Iki retry sonrasi hala gecerli sinif yoksa (plan §4.2 V11) -- sinif UYDURULMAZ.
INTENT_ABSTAINED = "ABSTAINED"

# ── oncelik kovalari: kaynak §6 tablosu ────────────────────────────────────

PRIORITY_PRIMARY = "PRIMARY"
PRIORITY_SECONDARY = "SECONDARY"
PRIORITY_TREND_CONTENT = "TREND_CONTENT"
PRIORITY_EXCLUDE = "EXCLUDE"

# ── PATRON NIHAI PRIORITY KARARI (15.09.2026) ─────────────────────────────
# Kaynak dokumanda sayisal olmayan "yuksek"/"orta" sinirlari patron tarafindan
# VERILDI. Bu sabitler UYDURULMADI; karar kaydidir (`priority_specification`,
# source=patron_decision). Onceki `PENDING_THRESHOLD` bekleme durumu KALKTI --
# hicbir satir beklemede kalmaz.
SOCIAL_SCORE_HIGH_MIN = 70.0   # yuksek Social Score
SOCIAL_SCORE_MID_MIN = 40.0    # orta seviye firsat alt siniri (<40 = dusuk)

PRIORITY_SPECIFICATION = {
    "high_min": SOCIAL_SCORE_HIGH_MIN,
    "mid_min": SOCIAL_SCORE_MID_MIN,
    "low_below": SOCIAL_SCORE_MID_MIN,
    "source": "patron_decision",
    "decided_at": "2026-09-15",
}

EXCLUDE_REASON_GATE = "gate"
EXCLUDE_REASON_COMMERCIAL = "commercial"
EXCLUDE_REASON_ABSTAINED = "abstained"
EXCLUDE_REASON_LOW_SOCIAL_SCORE = "low_social_score"

EXCLUDE_REASONS: Tuple[str, ...] = (
    EXCLUDE_REASON_GATE, EXCLUDE_REASON_COMMERCIAL,
    EXCLUDE_REASON_ABSTAINED, EXCLUDE_REASON_LOW_SOCIAL_SCORE)

#: Kovalar arasi sira (plan §4.2 V9). `EXCLUDE` listeye HIC girmez.
BUCKET_ORDER: Tuple[str, ...] = (
    PRIORITY_PRIMARY, PRIORITY_TREND_CONTENT, PRIORITY_SECONDARY)


# ── 1) kapilar ─────────────────────────────────────────────────────────────

def gate_decision_v5(row: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """YENI dokuman §4 -- UC SKIP kurali, SIRALI degerlendirme.

    Donus: {"decision": "eligible"|"skip", "reason": None|"relevance"|
            "relative_fit"|"brand_contentability"}

    `social_v4_engine.gate_decision`den TEK farki: `BC>=60` Primary kapisi ve
    `[50,60)` `secondary_review` bandi YOKTUR -- BC>=50 olan her satir
    dogrudan `eligible`dir. Bu, yeni dokumanin otorite kabul edilmesinin
    (plan §1.1) TEK kod-duzeyinde sonucudur.
    """
    rel = row.get("relevance_100")
    if rel is None:
        raise MissingSignal(f"relevance_100 yok: keyword_id={row.get('keyword_id')}")
    if float(rel) < RELEVANCE_GATE_MIN:
        return {"decision": GATE_SKIP, "reason": "relevance"}

    rf = row.get("relative_fit")
    if rf is None:
        raise MissingSignal(f"relative_fit yok: keyword_id={row.get('keyword_id')}")
    if float(rf) < RF_GATE_MIN:
        return {"decision": GATE_SKIP, "reason": "relative_fit"}

    bc = row.get("brand_contentability")
    if bc is None:
        raise MissingSignal(f"brand_contentability yok: keyword_id={row.get('keyword_id')}")
    if float(bc) < BC_GATE_MIN:
        return {"decision": GATE_SKIP, "reason": "brand_contentability"}

    return {"decision": GATE_ELIGIBLE, "reason": None}


def score_row_v5(row: Dict[str, Any], bounds: NormBounds) -> Dict[str, Any]:
    """V4 `score_row` (formul BIREBIR AYNI -- yeni dokuman motoru
    degistirmedi) + V5 kapisi. `score_row`un yazdigi `gate`/`gate_reason`
    alanlari (V4 semantigi, BC bandi dahil) `gate_v5`/`gate_reason_v5` ile
    EZILMEZ, YANINA yazilir -- iki semantik ayni satirda karismasin diye
    ayri adlar tasir; V5 tuketicileri YALNIZ `gate_v5`'i okur."""
    scored = score_row(row, bounds)
    gate = gate_decision_v5(scored)
    scored["gate_v5"] = gate["decision"]
    scored["gate_reason_v5"] = gate["reason"]
    return scored


def map_priority(gate_v5: str, intent: Optional[str],
                 social_score: Optional[float]) -> Dict[str, Optional[str]]:
    """Kaynak §6 tablosu + PATRON NIHAI ESIK KARARI (15.09).

    Degerlendirme SIRASI (patron tarafindan verilen sira -- degistirilmez):
      1. kapi basarisizligi                      -> EXCLUDE (gate)
      2. COMMERCIAL_SEARCH                       -> EXCLUDE (commercial)  [KOSULSUZ]
      3. ABSTAINED                               -> EXCLUDE (abstained)
      4. SocialScore < 40 (non-commercial)       -> EXCLUDE (low_social_score)
      5. TREND_OPPORTUNITY  + SS >= 40           -> TREND_CONTENT
      6. CONTENT_NATIVE     + SS >= 70           -> PRIMARY
      7. CONTENT_NATIVE     + 40 <= SS < 70      -> SECONDARY
      8. PRODUCT_EDUCATION  + SS >= 40           -> SECONDARY

    Ticari kural dusuk-skor kuralindan ONCE gelir: ticari bir kelime her
    zaman `commercial` sebebiyle duser, `low_social_score` ile KARISMAZ.

    Donus: {"priority": ..., "exclude_reason": None|gate|commercial|
            abstained|low_social_score}
    """
    if gate_v5 != GATE_ELIGIBLE:
        return {"priority": PRIORITY_EXCLUDE, "exclude_reason": EXCLUDE_REASON_GATE}
    if intent == INTENT_COMMERCIAL_SEARCH:
        return {"priority": PRIORITY_EXCLUDE,
                "exclude_reason": EXCLUDE_REASON_COMMERCIAL}
    if intent in (INTENT_ABSTAINED, None):
        return {"priority": PRIORITY_EXCLUDE,
                "exclude_reason": EXCLUDE_REASON_ABSTAINED}
    if intent not in (INTENT_CONTENT_NATIVE, INTENT_PRODUCT_EDUCATION,
                      INTENT_TREND_OPPORTUNITY):
        raise ValueError(f"bilinmeyen intent sinifi: {intent!r}")
    if social_score is None:
        raise MissingSignal("social_score yok -- Priority atanamaz")
    score = float(social_score)
    if score < SOCIAL_SCORE_MID_MIN:
        return {"priority": PRIORITY_EXCLUDE,
                "exclude_reason": EXCLUDE_REASON_LOW_SOCIAL_SCORE}
    if intent == INTENT_TREND_OPPORTUNITY:
        return {"priority": PRIORITY_TREND_CONTENT, "exclude_reason": None}
    if intent == INTENT_CONTENT_NATIVE:
        return {"priority": (PRIORITY_PRIMARY if score >= SOCIAL_SCORE_HIGH_MIN
                             else PRIORITY_SECONDARY),
                "exclude_reason": None}
    return {"priority": PRIORITY_SECONDARY, "exclude_reason": None}


def apply_priority(scored_rows: Sequence[Dict[str, Any]],
                   intent_by_id: Dict[int, str]) -> List[Dict[str, Any]]:
    """Her satira `social_intent_type` + `social_priority` + `exclude_reason`
    yazar. Kapida elenen satir `NOT_EVALUATED` sinifini alir (AI'a hic
    gitmedi); uygun havuzda sinifi gelmemis satir `ABSTAINED` sayilir."""
    out: List[Dict[str, Any]] = []
    for row in scored_rows:
        new = dict(row)
        gate_v5 = new.get("gate_v5")
        if gate_v5 != GATE_ELIGIBLE:
            intent: Optional[str] = INTENT_NOT_EVALUATED
        else:
            intent = intent_by_id.get(int(new["keyword_id"]), INTENT_ABSTAINED)
        mapped = map_priority(gate_v5,
                              None if intent == INTENT_NOT_EVALUATED else intent,
                              new.get("social_score"))
        new["social_intent_type"] = intent
        new["social_priority"] = mapped["priority"]
        new["exclude_reason"] = mapped["exclude_reason"]
        out.append(new)
    return out


# ── kollar (plan §5.1) ─────────────────────────────────────────────────────


STAGE_ELIGIBLE = "eligible"
STAGE_SKIP_BC = "skip_brand_contentability"
STAGE_SKIP_RF = "skip_relative_fit"
STAGE_SKIP_REL = "skip_relevance"
STAGE_NOT_IN_UNIVERSE = "not_in_universe"

#: Kucuk sayi = daha iyi asama (V4 GATE_STAGE_RANK ile ayni siralama mantigi).
GATE_STAGE_RANK: Dict[str, int] = {
    STAGE_ELIGIBLE: 1,
    STAGE_SKIP_BC: 2,
    STAGE_SKIP_RF: 3,
    STAGE_SKIP_REL: 4,
    STAGE_NOT_IN_UNIVERSE: 5,
}

