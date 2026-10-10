# -*- coding: utf-8 -*-
"""SOCIAL V5 uretim motoru — KALICI GOLDEN PARITY testi.

QA gorevi: Motor v3 Faz 5 = SOCIAL V5. Tek otorite tracked
`benchmark/social_v5_uretim_golden_v1.json` (algorithm_id: social_v5_uretim_v1
— bkz. `algoritma/SOCIAL_V5_URETIM_KOD_HARITASI.md`). Girdi 1000 satirlik
DONMUS SOCIAL V5 sinyalleridir (Noksel ws48/run48 golden replay'inden);
METIN YOK — `keyword_text = f"{text_order:010d}"` yalniz siralama
esitlik-bozucusu icin uretilir, hicbir anlam tasimaz.

Bu test YALNIZ uretim fonksiyonunu cagirir:
`app.core.engine.social.pipeline.compute_social_list`. Ara adimlar
(relevance_rows -> v4_candidates -> compute_norm_bounds -> score_row_v5 ->
apply_priority -> sira -> havuz) ayri ayri TEKRAR uygulanmaz; hepsi bu tek
cagrinin icindedir — golden'in kendisi de ayni zinciri kosarak uretildi
(bkz. kod haritasi "Kilidin kapsami").

Ucretli saglayici cagrisi YOK: bu dosya hicbir AI istemcisi kurmaz, saf
fonksiyonlari sabit girdiyle kosturur.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

from app.core.engine.social import pipeline as PL

GOLDEN_PATH = (Path(__file__).resolve().parents[2]
              / "benchmark" / "social_v5_uretim_golden_v1.json")


def _load_golden() -> Dict[str, Any]:
    with GOLDEN_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def _build_inputs(golden: Dict[str, Any]):
    """Golden'in `girdi` bolumunden (uni, rel, dims, intents) kurar.

    Kolon sozlesmesi (`girdi.kolonlar`): keyword_id, volume, trend_3m,
    trend_12m, relevance_0_1, brand_contentability, attention, scenario,
    relative_fit, intent_code, text_order.

    `bc` (brand_contentability) None ise satir V4 dort boyutunu HIC almamis
    demektir -> `dims`e girmez (relevance kapisinda kalmis satirlar).
    `ic` (intent_code) None ise satir V5 niyetini HIC almamis demektir ->
    `intents`e girmez (RF/BC uc kapisinda kalmis satirlar).
    """
    girdi = golden["girdi"]
    cols = girdi["kolonlar"]
    idx = {name: i for i, name in enumerate(cols)}
    codes = girdi["intent_codes"]

    uni: List[Dict[str, Any]] = []
    rel: Dict[int, Dict[str, Any]] = {}
    dims: Dict[int, Dict[str, Any]] = {}
    intents: Dict[int, Dict[str, Any]] = {}

    for row in girdi["satirlar"]:
        kid = int(row[idx["keyword_id"]])
        volume = row[idx["volume"]]
        t3 = row[idx["trend_3m"]]
        t12 = row[idx["trend_12m"]]
        r = row[idx["relevance_0_1"]]
        bc = row[idx["brand_contentability"]]
        att = row[idx["attention"]]
        scen = row[idx["scenario"]]
        rf = row[idx["relative_fit"]]
        ic = row[idx["intent_code"]]
        text_order = row[idx["text_order"]]

        keyword_text = f"{text_order:010d}"
        uni.append({"keyword_id": kid, "keyword_text": keyword_text,
                   "volume": volume, "trend_3m": t3, "trend_12m": t12})
        rel[kid] = {"relevance": r, "band": None}
        if bc is not None:
            dims[kid] = {"brand_contentability": bc, "attention": att,
                        "scenario": scen, "relative_fit": rf}
        if ic is not None:
            intents[kid] = {"social_intent_type": codes[ic],
                            "intent_confidence": None, "intent_reason": None}
    return uni, rel, dims, intents


def _score_sha256(final_rows) -> str:
    """`[[kid, None|round(score,9)]]` listesinin sha256'si (kid artan sirayla,
    determinizm icin) — `json.dumps(..., ensure_ascii=False, sort_keys=True,
    separators=(",", ":"))`."""
    pairs: List[List[Any]] = []
    for row in final_rows:
        score = row.get("social_score")
        pairs.append([int(row["keyword_id"]),
                     None if score is None else round(float(score), 9)])
    pairs.sort(key=lambda p: p[0])
    payload = json.dumps(pairs, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@pytest.fixture(scope="module")
def golden() -> Dict[str, Any]:
    return _load_golden()


@pytest.fixture(scope="module")
def golden_inputs(golden):
    return _build_inputs(golden)


@pytest.fixture(scope="module")
def result(golden_inputs) -> Dict[str, Any]:
    uni, rel, dims, intents = golden_inputs
    return PL.compute_social_list(uni, rel, dims, intents)


# ---------------------------------------------------------------------------
# fixture sanity — golden dosyanin kendisi beklenen sekli
# ---------------------------------------------------------------------------


def test_golden_fixture_shape(golden):
    assert golden["kind"] == "social_v5_uretim_golden"
    assert golden["algorithm_id"] == "social_v5_uretim_v1"
    assert len(golden["girdi"]["satirlar"]) == 1000
    assert golden["girdi"]["kolonlar"] == [
        "keyword_id", "volume", "trend_3m", "trend_12m", "relevance_0_1",
        "brand_contentability", "attention", "scenario", "relative_fit",
        "intent_code", "text_order",
    ]
    assert golden["girdi"]["intent_codes"] == [
        "CONTENT_NATIVE", "PRODUCT_EDUCATION", "COMMERCIAL_SEARCH",
        "TREND_OPPORTUNITY",
    ]


# ---------------------------------------------------------------------------
# AYRI testler — her biri golden'in "beklenen" bolumunden BIR alani dogrular
# ---------------------------------------------------------------------------


def test_relevance_survivor_ids(golden, result):
    got = [int(r["keyword_id"]) for r in result["survivors"]]
    want = golden["beklenen"]["relevance_survivor_ids"]
    assert len(got) == 684
    assert got == want


def test_three_gate_ids(golden, result):
    got = [int(r["keyword_id"]) for r in result["eligible"]]
    want = golden["beklenen"]["three_gate_ids"]
    assert len(got) == 406
    assert got == want


def test_ordered_ids(golden, result):
    got = [int(r["keyword_id"]) for r in result["kept"]]
    want = golden["beklenen"]["ordered_ids"]
    assert len(got) == 241
    assert got == want


def test_pool_ids(golden, result):
    got = [int(r["keyword_id"]) for r in result["pool"]]
    want = golden["beklenen"]["pool_ids"]
    assert len(got) == 60
    assert got == want
    # havuz sirali listenin ONDALIKSIZ ON YUZUDUR (60 varsayilan kapasite)
    assert got == [int(r["keyword_id"]) for r in result["kept"]][:60]


def test_norm_bounds(golden, result):
    got = result["bounds"].describe()
    want = golden["beklenen"]["norm_bounds"]
    assert got.keys() == want.keys()
    for key, expected in want.items():
        assert got[key] == pytest.approx(expected, abs=1e-9), key


def test_priority_by_id(golden, result):
    got = {str(int(row["keyword_id"])): [row["social_priority"], row["exclude_reason"]]
          for row in result["final"]}
    want = golden["beklenen"]["priority_by_id"]
    assert len(got) == 1000
    assert got == want


def test_social_score_sha256(golden, result):
    got = _score_sha256(result["final"])
    assert got == golden["beklenen"]["social_score_sha256"]


def test_batch_plan(golden, result, golden_inputs):
    uni, rel, dims, intents = golden_inputs
    got = {
        "relevance": PL.batch_plan("relevance", uni),
        "v4": PL.batch_plan("v4", result["survivors"]),
        "v5": PL.batch_plan("v5", result["eligible"]),
    }
    want = golden["beklenen"]["batch_plan"]
    assert got == want
    # Batch sabitleri golden'in batch sayisiyla tutarli: relevance=10, v4=15, v5=15.
    assert len(got["relevance"]) == 100      # ceil(1000/10)
    assert len(got["v4"]) == 46              # ceil(684/15)
    assert len(got["v5"]) == 28              # ceil(406/15)
