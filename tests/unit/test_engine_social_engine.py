# -*- coding: utf-8 -*-
"""SOCIAL V5 motor cekirdegi — birim testleri + tuzak nobetcileri.

QA gorevi: Motor v3 Faz 5 = SOCIAL V5. `app/core/engine/social/score.py` ve
`app/core/engine/social/priority.py` icindeki saf fonksiyonlari, ozellikle
sozlesmenin ADS/SEO'dan FARKLI oldugu noktalari (trend kirpmasi YOK,
NormBounds bir kez donuyor, uc kapi BC>=50 esigi — eski BC>=60 Primary
bandi YOK) ve sessiz ezme/yapisal kacak sinifi hatalari hedefler.

Ucretli saglayici cagrisi YOK — yalniz saf fonksiyon + AST taramasi.
"""
from __future__ import annotations

import ast
import dataclasses
import pathlib
from typing import Any, Dict

import pytest

from app.core.engine.ads import formula as ADSF
from app.core.engine.social import pipeline as PL
from app.core.engine.social import priority as ENG
from app.core.engine.social import score as SCORE

SOCIAL_DIR = pathlib.Path(__file__).resolve().parents[2] / "app/core/engine/social"


# ---------------------------------------------------------------------------
# Trend KIRPILMIYOR (ADS'ten farkli — ADS trend_to_t [0,1]'e kirpar)
# ---------------------------------------------------------------------------


def test_ads_trend_to_t_clamps_extreme_value_to_one():
    """Karsilastirma icin: ADS ayni asiri deger icin 1.0'a KIRPAR."""
    assert ADSF.trend_to_t(900.0) == 1.0
    assert ADSF.trend_to_t(-500.0) == 0.0


def test_social_normalize_trend_does_not_clamp_extreme_ratio():
    """SOCIAL'da ayni asiri deger (yuzde 900) ORAN olarak 9.0 kalir — ADS'in
    [0,1] kirpmasi UYGULANMAZ. Bu kirpma eklenirse test kirilmali."""
    bounds = SCORE.NormBounds(
        v_max=1000.0, t3_min=-1.0, t3_max=9.0, t12_min=-1.0, t12_max=9.0,
        trend_change_min=-10.0, trend_change_max=10.0,
    )
    row = {"trend_3m": 900.0, "trend_12m": -50.0}
    out = SCORE.normalize_trend(row, bounds)
    assert out["t3_ratio"] == pytest.approx(9.0)      # KIRPILMAMIS oran
    assert out["t12_ratio"] == pytest.approx(-0.5)
    # trend_change de kirpmasiz, ham fark: 9.0 - (-0.5) = 9.5
    assert out["trend_change"] == pytest.approx(9.5)


def test_social_normalize_trend_extreme_ratio_exceeds_ads_clamped_ceiling():
    """Dogrudan karsilastirma: ayni 900% girdi icin ADS<=1.0, SOCIAL>1.0."""
    ads_t = ADSF.trend_to_t(900.0)
    bounds = SCORE.NormBounds(
        v_max=1.0, t3_min=0.0, t3_max=1.0, t12_min=0.0, t12_max=1.0,
        trend_change_min=0.0, trend_change_max=1.0,
    )
    social_t3_ratio = SCORE.normalize_trend(
        {"trend_3m": 900.0, "trend_12m": 0.0}, bounds)["t3_ratio"]
    assert ads_t == 1.0
    assert social_t3_ratio == pytest.approx(9.0)
    assert social_t3_ratio > ads_t


# ---------------------------------------------------------------------------
# NormBounds BIR KEZ hesaplanip DONUYOR
# ---------------------------------------------------------------------------


def test_norm_bounds_is_frozen_dataclass_cannot_be_mutated():
    bounds = SCORE.compute_norm_bounds([
        {"volume": 100, "trend_3m": 10.0, "trend_12m": 5.0},
    ])
    with pytest.raises(dataclasses.FrozenInstanceError):
        bounds.v_max = 999.0  # type: ignore[misc]


def test_bounds_not_re_derived_for_a_row_added_after_freezing():
    """Sinirlar DONDURULDUKTEN SONRA gelen bir satir sinirlari GENISLETMEZ.

    Eger uretim kodu her satir icin sinirlari YENIDEN turetseydi, bu asiri
    hacimli satir kendi max'i olarak normalize edilir ve volume_n<=1 olurdu.
    Dondurulmus (eski) sinirlarla asar (>1) — bu TEK dogru davranistir (A2b).
    """
    rows = [
        {"volume": 100, "trend_3m": 10.0, "trend_12m": 5.0},
        {"volume": 200, "trend_3m": 20.0, "trend_12m": 10.0},
    ]
    bounds = SCORE.compute_norm_bounds(rows)

    late_row_volume = 100_000  # bounds.v_max'tan COK buyuk, SONRADAN "gelen" satir
    volume_n = SCORE.normalize_volume(late_row_volume, bounds)
    assert volume_n > 1.0, (
        "gec gelen satir eski (dondurulmus) sinirlari GENISLETMEDEN "
        "normalize edilmeli — >1 cikmasi bunun kanitidir"
    )


def test_compute_social_list_calls_compute_norm_bounds_exactly_once(monkeypatch):
    """Pipeline seviyesinde: `compute_norm_bounds` bir `compute_social_list`
    cagrisinda TAM OLARAK bir kez cagrilir (survivors uzerinden), sonra
    HERKES icin ayni dondurulmus nesne kullanilir."""
    calls = []
    original = ENG.compute_norm_bounds

    def spy(rows):
        calls.append(len(rows))
        return original(rows)

    monkeypatch.setattr(PL.ENG, "compute_norm_bounds", spy)

    uni = [
        {"keyword_id": 1, "keyword_text": "a", "volume": 100,
         "trend_3m": 5.0, "trend_12m": 1.0},
        {"keyword_id": 2, "keyword_text": "b", "volume": 200,
         "trend_3m": 10.0, "trend_12m": 2.0},
        {"keyword_id": 3, "keyword_text": "c", "volume": 300,
         "trend_3m": -5.0, "trend_12m": -1.0},
    ]
    rel = {1: {"relevance": 0.9, "band": None}, 2: {"relevance": 0.8, "band": None},
          3: {"relevance": 0.1, "band": None}}  # 3 relevance kapisinda ELENIR
    dims = {1: {"brand_contentability": 80, "attention": 70, "scenario": 60,
               "relative_fit": 90},
           2: {"brand_contentability": 60, "attention": 55, "scenario": 50,
               "relative_fit": 55}}
    intents = {1: {"social_intent_type": "CONTENT_NATIVE",
                  "intent_confidence": 90, "intent_reason": "r"},
              2: {"social_intent_type": "CONTENT_NATIVE",
                  "intent_confidence": 90, "intent_reason": "r"}}

    PL.compute_social_list(uni, rel, dims, intents)
    assert calls == [2], "bounds tam olarak BIR kez ve yalniz survivors uzerinden hesaplanmali"


# ---------------------------------------------------------------------------
# Uc kapinin sinir degerleri: Rel 39.9/40, RF 49.9/50, BC 49.9/50
# ---------------------------------------------------------------------------


def _row(relevance_100=80.0, relative_fit=80.0, brand_contentability=80.0):
    return {"keyword_id": 1, "relevance_100": relevance_100,
           "relative_fit": relative_fit,
           "brand_contentability": brand_contentability}


@pytest.mark.parametrize("relevance_100,expected_decision,expected_reason", [
    (39.9, ENG.GATE_SKIP, "relevance"),
    (40.0, ENG.GATE_ELIGIBLE, None),          # RF/BC de varsayilan gecerli
])
def test_gate_decision_v5_relevance_boundary(relevance_100, expected_decision,
                                             expected_reason):
    out = ENG.gate_decision_v5(_row(relevance_100=relevance_100))
    assert out["decision"] == expected_decision
    assert out["reason"] == expected_reason


@pytest.mark.parametrize("relative_fit,expected_decision,expected_reason", [
    (49.9, ENG.GATE_SKIP, "relative_fit"),
    (50.0, ENG.GATE_ELIGIBLE, None),
])
def test_gate_decision_v5_relative_fit_boundary(relative_fit, expected_decision,
                                                expected_reason):
    out = ENG.gate_decision_v5(_row(relative_fit=relative_fit))
    assert out["decision"] == expected_decision
    assert out["reason"] == expected_reason


@pytest.mark.parametrize("brand_contentability,expected_decision,expected_reason", [
    (49.9, ENG.GATE_SKIP, "brand_contentability"),
    (50.0, ENG.GATE_ELIGIBLE, None),
])
def test_gate_decision_v5_brand_contentability_boundary(
    brand_contentability, expected_decision, expected_reason,
):
    out = ENG.gate_decision_v5(_row(brand_contentability=brand_contentability))
    assert out["decision"] == expected_decision
    assert out["reason"] == expected_reason


def test_gate_decision_v5_has_no_legacy_bc_primary_band():
    """Eski V4 `BC>=60` Primary kapisi / `[50,60)` secondary_review bandi
    YENI dokumanda YOK: BC=55 (eskide secondary_review olurdu) burada
    dogrudan `eligible` olmali."""
    out = ENG.gate_decision_v5(_row(brand_contentability=55.0))
    assert out["decision"] == ENG.GATE_ELIGIBLE
    assert out["reason"] is None


def test_gate_decision_v5_raises_missing_signal_when_field_absent():
    with pytest.raises(SCORE.MissingSignal, match="relevance_100"):
        ENG.gate_decision_v5({"keyword_id": 1})
    with pytest.raises(SCORE.MissingSignal, match="relative_fit"):
        ENG.gate_decision_v5({"keyword_id": 1, "relevance_100": 80.0})
    with pytest.raises(SCORE.MissingSignal, match="brand_contentability"):
        ENG.gate_decision_v5({"keyword_id": 1, "relevance_100": 80.0,
                              "relative_fit": 80.0})


# ---------------------------------------------------------------------------
# map_priority / apply_priority — TUM dallar
# ---------------------------------------------------------------------------


def test_map_priority_gate_failure_excludes_regardless_of_intent_or_score():
    out = ENG.map_priority(ENG.GATE_SKIP, ENG.INTENT_CONTENT_NATIVE, 99.0)
    assert out == {"priority": ENG.PRIORITY_EXCLUDE,
                   "exclude_reason": ENG.EXCLUDE_REASON_GATE}


def test_map_priority_commercial_search_excludes_unconditionally_even_with_high_score():
    out = ENG.map_priority(ENG.GATE_ELIGIBLE, ENG.INTENT_COMMERCIAL_SEARCH, 100.0)
    assert out == {"priority": ENG.PRIORITY_EXCLUDE,
                   "exclude_reason": ENG.EXCLUDE_REASON_COMMERCIAL}


@pytest.mark.parametrize("intent", [ENG.INTENT_ABSTAINED, None])
def test_map_priority_abstained_or_missing_intent_excludes(intent):
    out = ENG.map_priority(ENG.GATE_ELIGIBLE, intent, 90.0)
    assert out == {"priority": ENG.PRIORITY_EXCLUDE,
                   "exclude_reason": ENG.EXCLUDE_REASON_ABSTAINED}


def test_map_priority_unknown_intent_class_raises_value_error():
    with pytest.raises(ValueError, match="bilinmeyen intent sinifi"):
        ENG.map_priority(ENG.GATE_ELIGIBLE, "NOT_A_REAL_CLASS", 90.0)


def test_map_priority_missing_social_score_raises_missing_signal():
    with pytest.raises(SCORE.MissingSignal, match="social_score"):
        ENG.map_priority(ENG.GATE_ELIGIBLE, ENG.INTENT_CONTENT_NATIVE, None)


@pytest.mark.parametrize("intent", [
    ENG.INTENT_CONTENT_NATIVE, ENG.INTENT_PRODUCT_EDUCATION,
    ENG.INTENT_TREND_OPPORTUNITY,
])
def test_map_priority_ss_below_40_excludes_low_social_score(intent):
    out = ENG.map_priority(ENG.GATE_ELIGIBLE, intent, 39.999)
    assert out == {"priority": ENG.PRIORITY_EXCLUDE,
                   "exclude_reason": ENG.EXCLUDE_REASON_LOW_SOCIAL_SCORE}


def test_map_priority_trend_opportunity_ss_ge_40_is_trend_content():
    out = ENG.map_priority(ENG.GATE_ELIGIBLE, ENG.INTENT_TREND_OPPORTUNITY, 40.0)
    assert out == {"priority": ENG.PRIORITY_TREND_CONTENT, "exclude_reason": None}
    high = ENG.map_priority(ENG.GATE_ELIGIBLE, ENG.INTENT_TREND_OPPORTUNITY, 95.0)
    assert high["priority"] == ENG.PRIORITY_TREND_CONTENT   # yuksek SS'de de TREND_CONTENT (PRIMARY DEGIL)


def test_map_priority_content_native_high_score_is_primary():
    out = ENG.map_priority(ENG.GATE_ELIGIBLE, ENG.INTENT_CONTENT_NATIVE, 70.0)
    assert out == {"priority": ENG.PRIORITY_PRIMARY, "exclude_reason": None}


def test_map_priority_content_native_mid_score_is_secondary():
    out = ENG.map_priority(ENG.GATE_ELIGIBLE, ENG.INTENT_CONTENT_NATIVE, 69.999)
    assert out == {"priority": ENG.PRIORITY_SECONDARY, "exclude_reason": None}
    low = ENG.map_priority(ENG.GATE_ELIGIBLE, ENG.INTENT_CONTENT_NATIVE, 40.0)
    assert low == {"priority": ENG.PRIORITY_SECONDARY, "exclude_reason": None}


def test_map_priority_product_education_ss_ge_40_is_always_secondary():
    low = ENG.map_priority(ENG.GATE_ELIGIBLE, ENG.INTENT_PRODUCT_EDUCATION, 40.0)
    high = ENG.map_priority(ENG.GATE_ELIGIBLE, ENG.INTENT_PRODUCT_EDUCATION, 100.0)
    assert low == {"priority": ENG.PRIORITY_SECONDARY, "exclude_reason": None}
    assert high == {"priority": ENG.PRIORITY_SECONDARY, "exclude_reason": None}
    # PRODUCT_EDUCATION asla PRIMARY olamaz (yuksek skorda bile).


def test_apply_priority_gate_skip_row_gets_not_evaluated_intent_and_is_never_sent_to_ai():
    """Kapida elenen satir AI'a HIC GONDERILMEZ -> intent_by_id'de olsa BILE
    NOT_EVALUATED'e zorlanir (apply_priority intent_by_id.get'i gate skip
    satirlar icin hic cagirmaz)."""
    scored = [{"keyword_id": 1, "gate_v5": ENG.GATE_SKIP, "social_score": None}]
    intent_by_id = {1: ENG.INTENT_COMMERCIAL_SEARCH}  # yanlislikla verilse BILE
    out = ENG.apply_priority(scored, intent_by_id)
    assert out[0]["social_intent_type"] == ENG.INTENT_NOT_EVALUATED
    assert out[0]["social_priority"] == ENG.PRIORITY_EXCLUDE
    assert out[0]["exclude_reason"] == ENG.EXCLUDE_REASON_GATE


def test_apply_priority_eligible_row_missing_from_intent_map_becomes_abstained():
    scored = [{"keyword_id": 5, "gate_v5": ENG.GATE_ELIGIBLE, "social_score": 90.0}]
    out = ENG.apply_priority(scored, {})   # 5 icin intent HIC gelmedi
    assert out[0]["social_intent_type"] == ENG.INTENT_ABSTAINED
    assert out[0]["social_priority"] == ENG.PRIORITY_EXCLUDE
    assert out[0]["exclude_reason"] == ENG.EXCLUDE_REASON_ABSTAINED


# ---------------------------------------------------------------------------
# order_key + PRIORITY_RANK siralamasi — esitlik bozucular
# ---------------------------------------------------------------------------


def test_order_key_breaks_ties_by_volume_then_text_then_id():
    same_score = [
        {"keyword_id": 3, "keyword_text": "b kelime", "social_score": 50.0, "volume": 100},
        {"keyword_id": 1, "keyword_text": "a kelime", "social_score": 50.0, "volume": 200},
        {"keyword_id": 2, "keyword_text": "a kelime", "social_score": 50.0, "volume": 200},
    ]
    ordered = sorted(same_score, key=ENG.order_key)
    # Ayni skor -> once hacim azalan (200 > 100) -> ayni hacimde metin artan
    # ("a kelime" < "b kelime") -> ayni metinde id artan (1 < 2).
    assert [r["keyword_id"] for r in ordered] == [1, 2, 3]


def test_priority_rank_dominates_social_score_in_final_sort():
    """`compute_social_list`in nihai sirasi ONCE PRIORITY_RANK'e, SONRA
    order_key'e bakar: TREND_CONTENT ile dusuk skorlu bir PRIMARY bile olsa
    PRIMARY her zaman TREND_CONTENT'ten ONCE gelir (PRIORITY_RANK: PRIMARY=0
    < TREND_CONTENT=1 < SECONDARY=2)."""
    low_primary = {"keyword_id": 1, "social_priority": PL.ENG.PRIORITY_PRIMARY,
                  "social_score": 10.0, "volume": 1, "keyword_text": "z"}
    high_trend = {"keyword_id": 2, "social_priority": PL.ENG.PRIORITY_TREND_CONTENT,
                 "social_score": 999.0, "volume": 999, "keyword_text": "a"}
    rows = [high_trend, low_primary]
    ordered = sorted(
        rows, key=lambda r: (PL.PRIORITY_RANK[r["social_priority"]],) + PL.ENG.order_key(r))
    assert [r["keyword_id"] for r in ordered] == [1, 2]


# ---------------------------------------------------------------------------
# relevance_rows: 0-1 degeri x100 ve 2 ondalige yuvarlaniyor
# ---------------------------------------------------------------------------


def test_relevance_rows_scales_0_1_to_100_and_rounds_to_two_decimals():
    universe = [{"keyword_id": 7, "keyword_text": "k", "volume": 10,
                "trend_3m": 0.0, "trend_12m": 0.0}]
    rel = {7: {"relevance": 0.678912345, "band": "adjacent"}}
    out = PL.relevance_rows(universe, rel)
    assert out[0]["relevance_100"] == round(0.678912345 * 100, 2)
    assert out[0]["relevance_100"] == 67.89
    assert out[0]["relevance_band"] == "adjacent"


def test_relevance_rows_zero_and_near_one_edge_values():
    universe = [
        {"keyword_id": 1, "keyword_text": "k1", "volume": 1, "trend_3m": 0.0, "trend_12m": 0.0},
        {"keyword_id": 2, "keyword_text": "k2", "volume": 1, "trend_3m": 0.0, "trend_12m": 0.0},
    ]
    rel = {1: {"relevance": 0.0, "band": None}, 2: {"relevance": 0.999999, "band": None}}
    out = PL.relevance_rows(universe, rel)
    by_id = {r["keyword_id"]: r for r in out}
    assert by_id[1]["relevance_100"] == 0.0
    assert by_id[2]["relevance_100"] == 100.0   # round(99.9999, 2) == 100.0


# ---------------------------------------------------------------------------
# YAPISAL NOBETCI: app/core/engine/social/ altinda scripts importu YOK
# (ast ile — lazy import'lar dahil; naive alt-string taramasi docstring
# yuzunden yanlis-pozitif/yanlis-negatif verebilir, bkz. test_engine_seo_
# structure.py'deki ayni desen)
# ---------------------------------------------------------------------------


def test_social_production_modules_do_not_import_scripts():
    offenders = []
    for path in sorted(SOCIAL_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "scripts" or alias.name.startswith("scripts."):
                        offenders.append(f"{path.name}:{node.lineno} import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module == "scripts" or module.startswith("scripts."):
                    offenders.append(f"{path.name}:{node.lineno} from {module}")
    assert not offenders, (
        "uretim kodu scripts/ icinden import ediyor (kilitler SHA ile bagli): "
        + ", ".join(offenders))


def test_pipeline_module_does_not_import_family_v2():
    """SOCIAL AILE KURMAZ (Family V2 bu kanalda calismaz) — pipeline.py
    `app.core.engine.family`dan HICBIR SEY import etmemeli."""
    tree = ast.parse(pathlib.Path(PL.__file__).read_text(encoding="utf-8"))
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if "family" in module:
                offenders.append(f"{node.lineno} from {module}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if "family" in alias.name:
                    offenders.append(f"{node.lineno} import {alias.name}")
    assert not offenders, (
        "pipeline.py Family V2'den import ediyor — SOCIAL aile KURMAZ: "
        + ", ".join(offenders))


# ---------------------------------------------------------------------------
# Olcum mekanizmalari TASINMAMIS OLMALI: uc tekrar/medyan/consensus/
# patron-etiketi (cevap anahtari) mantigi `score.py`/`priority.py`de
# GERCEK Python sembolu olarak TANIMLI OLMAMALI (AST — saf alt-string
# yanlis-kirmizi/yanlis-yesil verir, bkz. test_engine_seo_structure.py).
#
# NOT (QA bulgusu — app/ DEGISTIRILMEDI, yalniz raporlanir): bu test bu an
# icin KIRMIZI olabilir. `score.py` ve `priority.py`, docstring'lerinin
# vaat ettiginin aksine, ablasyon/olcum donemi kalintisi fonksiyonlar
# tasiyor: `class_agreement`/`majority_intent` (uc tekrar arasi konsensus,
# `intent_by_repeat` parametresiyle), `jaccard`, `gate_loss(_units)`,
# `commercial_loss(_units)`, `family_coverage_at_n`, `exact_hit_at_n(_units)`,
# `weighted_family_utility`, `weighted_exact_utility(_units)`, `build_arms`,
# `build_k_score/vol/intent`, `priority_counts`, `intent_distribution`,
# `exclude_breakdown` — hepsi `positive_ids`/`pos_match`/`intent_by_repeat`
# (patron cevap anahtari / uc-tekrar) parametresi alir ve
# `app/core/engine/social/pipeline.py::compute_social_list` tarafindan HIC
# cagrilmaz; `app/` genelinde bu isimlere baska hicbir uretim modulunde
# referans yoktur (dogrulandi: grep). Bu, modulun kendi ust basligindaki
# "Olcum mekanizmalari (uc tekrar, medyan/consensus, kontrol capalari,
# patron etiketleri) TASINMAZ" vaadiyle CELISIR.
# ---------------------------------------------------------------------------

FORBIDDEN_MEASUREMENT_SYMBOLS = {
    "class_agreement", "majority_intent", "jaccard",
    "gate_loss", "gate_loss_units", "commercial_loss", "commercial_loss_units",
    "family_coverage_at_n", "exact_hit_at_n", "exact_hit_units_at_n",
    "weighted_family_utility", "weighted_exact_utility",
    "weighted_exact_utility_units", "build_arms", "build_k_score",
    "build_k_vol", "build_k_intent", "priority_counts", "intent_distribution",
    "exclude_breakdown", "gate_stage_for_unit",
}


def _top_level_defined_names(path: pathlib.Path) -> set:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
    return names


def test_measurement_only_symbols_not_defined_in_social_score_or_priority_modules():
    score_names = _top_level_defined_names(pathlib.Path(SCORE.__file__))
    priority_names = _top_level_defined_names(pathlib.Path(ENG.__file__))
    leaked = FORBIDDEN_MEASUREMENT_SYMBOLS & (score_names | priority_names)
    assert not leaked, (
        "olcum/ablasyon donemi sembolleri uretim kopyasinda GERCEK Python "
        f"sembolu olarak tanimli (pipeline.compute_social_list bunlari HIC "
        f"cagirmiyor, app/ genelinde baska referanslari yok): {sorted(leaked)} "
        "— modul basligindaki 'olcum mekanizmalari TASINMAZ' vaadiyle celisir"
    )
