"""SOCIAL — V4 skor cekirdegi (URETIM KOPYASI).

plan_algoritma_entegrasyonu.md Faz 5 · algoritma/SOCIAL_V5_URETIM_KOD_HARITASI.md
4. ve 8. adim. Kilitli `scripts/social_v4_engine.py` davranisinin birebir
kopyasidir; uretim kodu `scripts/` icinden import ETMEZ, esitlik tracked golden
parity ile kanitlanir.

    NormBounds BIR KEZ hesaplanir (Rel >= 40 hayatta kalanlar uzerinden) ve
    DONDURULUR; sonradan gelen satir sinirlari DEGISTIRMEZ.
    Trend normalizasyonunda KIRPMA YOKTUR (ADS'ten farkli).

Olcum mekanizmalari (uc tekrar, medyan/consensus, kontrol capalari, patron
etiketleri) TASINMAZ.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

RELEVANCE_GATE_MIN = 40.0
RF_GATE_MIN = 50.0
BC_SKIP_MIN = 50.0
BC_PRIMARY_MIN = 60.0

# ── formul agirliklari (kaynak §5/§6/§8, plan §3.1) ─────────────────────────
TREND_CORE_W_T3 = 0.60
TREND_CORE_W_T12 = 0.15
TREND_CORE_W_CHANGE = 0.25
VOLUME_SUPPORT_BASE = 0.70
VOLUME_SUPPORT_SLOPE = 0.30
CREATIVE_W_BC = 0.60
CREATIVE_W_ATTENTION = 0.25
CREATIVE_W_SCENARIO = 0.15
SOCIAL_EXP_TREND_OPPORTUNITY = 0.45
SOCIAL_EXP_CREATIVE = 0.25
SOCIAL_EXP_RELATIVE_FIT = 0.30

# ── §1.10 agirlikli Top-N faydasi — basamakli agirlik (icat DEGIL, plan-donmus) ──
# A9 (v3.7): weighted_C / weighted_P HER ZAMAN int olmalidir — agirliklar bu
# yuzden int (float DEGIL); dort N'deki (10/15/20/30) tam-sayi isabet
# toplamiyla matematiksel ozdes oldugu icin (bkz. social_v4_impl_b.py'nin
# ayni sonuca farkli bir yoldan ulasan int toplamasi) bu int'e daraltma
# hicbir bilgiyi kaybetmez.
RANK_BANDS: Tuple[Tuple[int, int], ...] = ((10, 4), (15, 3), (20, 2), (30, 1))


class MissingSignal(RuntimeError):
    """Zorunlu alan eksik veya bos evrenden norm_bounds istendi."""


class DuplicateKeywordIdError(ValueError):
    """A10 (v3.7 — plan §3.2 tablosu): ham evrende yinelenen `keyword_id`
    YAPISAL HATADIR. Uretimde `keyword_id` PK oldugu icin olusamaz;
    fixture/test katmaninda olusursa kosu DURUR, sessizce tekillestirilmez."""


def assert_unique_keyword_ids(rows: Sequence[Dict[str, Any]]) -> None:
    """A10: `rows` (ham evren, gate/skor UYGULANMAMIS) icinde ayni
    `keyword_id` birden fazla kez geciyorsa `DuplicateKeywordIdError`
    firlatir. Cagiran taraf bunu evreni ISLEMEYE BASLAMADAN ONCE cagirmalidir
    (bu fonksiyon kendisi hicbir sessiz tekillestirme YAPMAZ)."""
    seen: set = set()
    dupes: set = set()
    for r in rows:
        kid = r["keyword_id"]
        if kid in seen:
            dupes.add(kid)
        else:
            seen.add(kid)
    if dupes:
        raise DuplicateKeywordIdError(
            f"Ham evrende yinelenen keyword_id (A10 — yapisal hata, "
            f"sessizce tekillestirilmez): {sorted(dupes)}")


# ── A2/A2b: normalizasyon sinirlari (TEK SEFER, AI'dan ONCE, mühürlenir) ────

@dataclass(frozen=True)
class NormBounds:
    """Relevance kapisini gecen TUM satirlar uzerinden hesaplanan sabit sinirlar.

    Butun kollar (K0-K4) AYNI ornegi kullanmalidir — bu deger bir kez uretilip
    tasinir, hicbir fonksiyon burada TEKRAR turetmez.
    """
    v_max: float
    t3_min: float
    t3_max: float
    t12_min: float
    t12_max: float
    trend_change_min: float
    trend_change_max: float

    def describe(self) -> Dict[str, Any]:
        return asdict(self)


def compute_norm_bounds(rows: Sequence[Dict[str, Any]]) -> NormBounds:
    """A2/A2b: `rows` relevance kapisini GECMIS alt kume olmalidir (cagiran
    taraf `filter_relevance_survivors` ile filtreler). Bu fonksiyon TEK SEFER
    cagirilir; sonradan `insufficient_signal` olan satirlar bu sinirlari
    GERIYE DONUK degistirmez (A2b) — cagiran taraf bunu ayrica isaretler.

    Gerekli alanlar: volume, trend_3m (HAM YUZDE), trend_12m (HAM YUZDE).
    """
    if not rows:
        raise MissingSignal("norm_bounds bos satir kumesinden hesaplanamaz")
    volumes = [float(r["volume"]) for r in rows]
    t3_ratios = [float(r["trend_3m"]) / 100.0 for r in rows]
    t12_ratios = [float(r["trend_12m"]) / 100.0 for r in rows]
    trend_changes = [a - b for a, b in zip(t3_ratios, t12_ratios)]
    return NormBounds(
        v_max=max(volumes),
        t3_min=min(t3_ratios), t3_max=max(t3_ratios),
        t12_min=min(t12_ratios), t12_max=max(t12_ratios),
        trend_change_min=min(trend_changes), trend_change_max=max(trend_changes),
    )


def filter_relevance_survivors(rows: Sequence[Dict[str, Any]]
                               ) -> List[Dict[str, Any]]:
    """Kaynak §11: Relevance < 40 ise SKIP, AI'a gonderilmez (plan §1.7)."""
    return [r for r in rows if float(r["relevance_100"]) >= RELEVANCE_GATE_MIN]


# ── A1/A1b/A1c: normalizasyon ───────────────────────────────────────────────

def _minmax_or_half(value: float, lo: float, hi: float) -> float:
    """Min-max; aralik sifirsa (tum satirlar esit) NOTR 0.5 (A1b)."""
    span = hi - lo
    if span <= 0:
        return 0.5
    return (value - lo) / span


def normalize_trend(row: Dict[str, Any], bounds: NormBounds) -> Dict[str, float]:
    """A1 (oran + kirpmasiz min-max) + A1c (TrendChange_N BAGIMSIZ hesaplanir,
    T3_N - T12_N DEGILDIR)."""
    t3_ratio = float(row["trend_3m"]) / 100.0
    t12_ratio = float(row["trend_12m"]) / 100.0
    trend_change = t3_ratio - t12_ratio          # HAM, kirpmasiz (A1)
    return {
        "t3_ratio": t3_ratio, "t12_ratio": t12_ratio, "trend_change": trend_change,
        "t3_n": _minmax_or_half(t3_ratio, bounds.t3_min, bounds.t3_max),
        "t12_n": _minmax_or_half(t12_ratio, bounds.t12_min, bounds.t12_max),
        "trend_change_n": _minmax_or_half(trend_change, bounds.trend_change_min,
                                          bounds.trend_change_max),
    }


def normalize_volume(volume: float, bounds: NormBounds) -> float:
    """Volume_N = log(1+V)/log(1+V_max). V_max<=0 -> 0.5 (harfte yok, 0/0 icin
    gerekli tamamlayici — bkz. modul docstring'i)."""
    v_max_log = math.log1p(bounds.v_max)
    if v_max_log <= 0:
        return 0.5
    return math.log1p(float(volume)) / v_max_log


# ── formul bilesenleri (kaynak §5/§6/§8) ────────────────────────────────────

def compute_trend_core(t3_n: float, t12_n: float, trend_change_n: float) -> float:
    return (TREND_CORE_W_T3 * t3_n + TREND_CORE_W_T12 * t12_n
            + TREND_CORE_W_CHANGE * trend_change_n)


def compute_volume_support(volume_n: float) -> float:
    return VOLUME_SUPPORT_BASE + VOLUME_SUPPORT_SLOPE * volume_n


def compute_trend_opportunity(trend_core: float, volume_support: float) -> float:
    return trend_core * volume_support


def compute_creative_score(brand_contentability: float, attention: float,
                           scenario: float) -> float:
    """0-100 olcek (kaynak §12 sema kolonu CreativeScore 0-100)."""
    return (CREATIVE_W_BC * float(brand_contentability)
            + CREATIVE_W_ATTENTION * float(attention)
            + CREATIVE_W_SCENARIO * float(scenario))


def compute_social_score(trend_opportunity: float, creative_100: float,
                         relative_fit_100: float) -> float:
    """SOCIAL SCORE = 100 * TO^0.45 * (Creative/100)^0.25 * (RF/100)^0.30.

    A3: bir taban 0 ise sonuc 0 — bu SKIP DEGILDIR, formulun amacidir. `max(...,0)`
    guvenligi yalniz kayan-nokta gurultusune karsidir (girdiler zaten [0,1]
    araligindaki normalize bilesenlerden kurulur); negatif bir taban sartname
    disi bir veri hatasidir, sessizce yutulmaz.
    """
    to = float(trend_opportunity)
    cr = float(creative_100) / 100.0
    rf = float(relative_fit_100) / 100.0
    for name, value in (("TrendOpportunity", to), ("Creative", cr), ("RelativeFit", rf)):
        if value < 0:
            raise ValueError(f"{name} negatif olamaz: {value}")
    return 100.0 * (to ** SOCIAL_EXP_TREND_OPPORTUNITY) * (cr ** SOCIAL_EXP_CREATIVE) \
        * (rf ** SOCIAL_EXP_RELATIVE_FIT)


# ── gate karari (kaynak §7 tablosu — OTORITE, §11/§15 ozeti DEGIL) ──────────

def gate_decision(row: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """Sira kaynaktaki gibi: Relevance -> RelativeFit -> BrandContentability.

    Donus: {"decision": "primary"|"secondary_review"|"skip", "reason": ...}
    `reason` yalnizca skip/secondary_review icin doludur; ilk gecerli engel
    raporlanir (birden fazla kapi ayni anda basarisiz olabilir).
    """
    relevance = float(row["relevance_100"])
    relative_fit = float(row["relative_fit"])
    brand_contentability = float(row["brand_contentability"])
    if relevance < RELEVANCE_GATE_MIN:
        return {"decision": "skip", "reason": "relevance"}
    if relative_fit < RF_GATE_MIN:
        return {"decision": "skip", "reason": "relative_fit"}
    if brand_contentability < BC_SKIP_MIN:
        return {"decision": "skip", "reason": "brand_contentability"}
    if brand_contentability < BC_PRIMARY_MIN:
        return {"decision": "secondary_review", "reason": "brand_contentability_band"}
    return {"decision": "primary", "reason": None}


# ── satir bazinda tam skorlama ───────────────────────────────────────────────

def score_row(row: Dict[str, Any], bounds: NormBounds) -> Dict[str, Any]:
    """Tek satir icin normalize + formul + gate. `row` relevance kapisini
    GECMIS ve AI skorlarina SAHIP olmalidir (yani `filter_relevance_survivors`
    sonrasi, AI cagrisindan sonra).

    Zorunlu alanlar: keyword_id, keyword_text, volume, family_id,
      trend_3m (HAM YUZDE), trend_12m (HAM YUZDE), relevance_100 (0-100),
      brand_contentability, attention, scenario, relative_fit (0-100 AI ciktisi).

    `bounds` HER ZAMAN disaridan gelir; bu fonksiyon kendi sinirini ASLA
    turetmez (A2b).
    """
    trend = normalize_trend(row, bounds)
    volume_n = normalize_volume(row["volume"], bounds)
    trend_core = compute_trend_core(trend["t3_n"], trend["t12_n"], trend["trend_change_n"])
    volume_support = compute_volume_support(volume_n)
    trend_opportunity = compute_trend_opportunity(trend_core, volume_support)
    creative_100 = compute_creative_score(row["brand_contentability"], row["attention"],
                                          row["scenario"])
    social_score = compute_social_score(trend_opportunity, creative_100, row["relative_fit"])
    gate = gate_decision(row)
    return {
        **row,
        "t3_ratio": trend["t3_ratio"], "t12_ratio": trend["t12_ratio"],
        "trend_change": trend["trend_change"], "t3_n": trend["t3_n"],
        "t12_n": trend["t12_n"], "trend_change_n": trend["trend_change_n"],
        "volume_n": volume_n, "trend_core": trend_core,
        "volume_support": volume_support, "trend_opportunity": trend_opportunity,
        "creative_score": creative_100, "social_score": social_score,
        "gate": gate["decision"], "gate_reason": gate["reason"],
    }


# ── §3.1 deterministik siralama ─────────────────────────────────────────────

def order_key(row: Dict[str, Any]) -> Tuple[float, float, str, int]:
    return (-float(row["social_score"]), -float(row["volume"]),
            str(row["keyword_text"]), int(row["keyword_id"]))


def order_rows(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """(-SOCIAL_SCORE, -Volume, keyword_text, keyword_id) — §3.1."""
    return sorted(rows, key=order_key)


# ── A6: aile tekillestirme (skorlama SONRASI) ───────────────────────────────


def top_n_ids(ordered_deduped_rows: Sequence[Dict[str, Any]], n: int) -> List[int]:
    return [int(r["keyword_id"]) for r in ordered_deduped_rows[:n]]


# ── §4.1: K0-K4 kollari (relevance on filtresi 4 kolda da sabit; hicbir AI
#          cagrisi eklenmez — ablasyon yalnizca RF/BC kapisini ve siralamayi
#          acar/kapar) ─────────────────────────────────────────────────────


def _rank_map(ordered_ids: Sequence[int]) -> Dict[int, int]:
    return {kid: i + 1 for i, kid in enumerate(ordered_ids)}

