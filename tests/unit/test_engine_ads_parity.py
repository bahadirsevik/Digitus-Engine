"""ADS Niche motoru — GOLDEN PARITY testi (`benchmark/ads_nihai_niche_golden_v1.json`).

QA gorevi: Motor v3 Faz 3 = ADS Niche motoru. Bu dosya UZERETIM
fonksiyonlarini (`app/core/engine/ads/formula.py` + `app/core/engine/ads/
engine.py`) kilitli golden fixture'a karsi kosturur. Kilit script'i
(`scripts/nihai_akis_e2_e6.py`, `scripts/ads_v3_formula.py`) BURADA
CAGRILMAZ — golden dosyasi zaten o script'lerle dondurulmus veriden
uretildi (bkz. `algoritma/ADS_NIHAI_NICHE_KOD_HARITASI.md`). Amac, uretim
kopyasinin kilitli davranisla BIREBIR ayni sonucu urettigini kanitlamaktir.

Sozlesme (haritanin "Motor formulu" bolumu):
    onisleme : hacimsiz satir duser
               Rel alt %20 kesilir; SINIR DEGERINI paylasanlarin HEPSI kalir
               Ln = min-max log10(hacim) (tek hacimde 0,5)
               Ln' = 0,20 + 0,80*Ln
    Core      = Rel^2 * Intent^2 * Ln' * (1 - 0,5*R) * (1 + 0,2*T)
    MFV       = Core / ailedeki en yuksek Core
    FamilyRelQ= KESIMDEN SAG CIKAN uyelerin ortalama Rel'i
    Selection = Core * MFV * FamilyRelQ
    sira      = (-Selection, -hacim, keyword_id)

Golden yapi (`d["firmalar"][slug]`):
    evren    : [keyword_id, volume, competition_raw, trend_3m_percent_raw, family_id]
    tekrarlar[rep] : {"rel": {str(id): float}, "intent": {str(id): float}}
Beklenen (`d["beklenen"][slug][rep]`): top60_ids, top60_selection (ilk 60
Selection degeri, top60_ids ile ayni sirada), full_order_sha256 (TAM sira
uzerinden, yalniz top60 degil), pool_size.

Bu dosya ucretli hicbir saglayici cagrisi YAPMAZ — yalniz dondurulmus JSON
fixture okur ve saf motor fonksiyonlarini kosturur.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from app.core.engine.ads.engine import run_niche_engine
from app.core.engine.ads.formula import competition_to_r, trend_to_t

GOLDEN_PATH = (Path(__file__).resolve().parents[2]
               / "benchmark" / "ads_nihai_niche_golden_v1.json")

SELECTION_TOLERANCE = 1e-9


def _load_golden() -> Dict[str, Any]:
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


GOLDEN = _load_golden()

CELLS = [
    (slug, rep)
    for slug in GOLDEN["firmalar"]
    for rep in GOLDEN["firmalar"][slug]["tekrarlar"]
]


def _build_rows(slug: str, rep: str) -> List[Dict[str, Any]]:
    """Golden evren + tekrar verisinden motor giris satirlari kurar.

    Sozlesmedeki alan haritasi (QA gorev tanimi): keyword_text bos birakilir
    (golden'da metin YOK — bilincli, bkz. `d["icerik"]`).
    """
    firm = GOLDEN["firmalar"][slug]
    tekrar = firm["tekrarlar"][rep]
    rel = tekrar["rel"]
    intent = tekrar["intent"]
    flags: Dict[str, int] = {}
    rows: List[Dict[str, Any]] = []
    for keyword_id, volume, competition_raw, trend_raw, family_id in firm["evren"]:
        rows.append({
            "keyword_id": keyword_id,
            "keyword_text": "",
            "volume": volume,
            "R": competition_to_r(competition_raw, flags),
            "T": trend_to_t(trend_raw, flags),
            "Rel": rel[str(keyword_id)],
            "Intent": intent[str(keyword_id)],
            "family": family_id,
        })
    return rows


def _full_order_sha256(pool: List[Dict[str, Any]]) -> str:
    ids = [row["keyword_id"] for row in pool]
    canonical = json.dumps(ids, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@pytest.mark.parametrize("slug,rep", CELLS,
                         ids=[f"{slug}-{rep}" for slug, rep in CELLS])
def test_golden_parity_top60_ids_exact(slug: str, rep: str) -> None:
    rows = _build_rows(slug, rep)
    pool, _checks = run_niche_engine(rows)
    expected = GOLDEN["beklenen"][slug][rep]

    top60_ids = [row["keyword_id"] for row in pool[:60]]
    assert top60_ids == expected["top60_ids"], (
        f"{slug}/{rep}: ilk-60 keyword_id sirasi golden ile BIREBIR uyusmuyor"
    )


@pytest.mark.parametrize("slug,rep", CELLS,
                         ids=[f"{slug}-{rep}" for slug, rep in CELLS])
def test_golden_parity_full_order_sha256_exact(slug: str, rep: str) -> None:
    rows = _build_rows(slug, rep)
    pool, _checks = run_niche_engine(rows)
    expected = GOLDEN["beklenen"][slug][rep]

    assert _full_order_sha256(pool) == expected["full_order_sha256"], (
        f"{slug}/{rep}: TAM sira SHA'si golden ile uyusmuyor "
        "(yalniz top60 degil, butun havuz sirasi)"
    )


@pytest.mark.parametrize("slug,rep", CELLS,
                         ids=[f"{slug}-{rep}" for slug, rep in CELLS])
def test_golden_parity_pool_size_exact(slug: str, rep: str) -> None:
    rows = _build_rows(slug, rep)
    pool, _checks = run_niche_engine(rows)
    expected = GOLDEN["beklenen"][slug][rep]

    assert len(pool) == expected["pool_size"], (
        f"{slug}/{rep}: kesim sonrasi havuz boyutu golden ile uyusmuyor"
    )


@pytest.mark.parametrize("slug,rep", CELLS,
                         ids=[f"{slug}-{rep}" for slug, rep in CELLS])
def test_golden_parity_top60_selection_within_tolerance(slug: str, rep: str) -> None:
    rows = _build_rows(slug, rep)
    pool, _checks = run_niche_engine(rows)
    expected = GOLDEN["beklenen"][slug][rep]

    top60_selection = [row["Selection"] for row in pool[:60]]
    assert len(top60_selection) == len(expected["top60_selection"])
    for index, (got, want) in enumerate(
            zip(top60_selection, expected["top60_selection"])):
        assert got == pytest.approx(want, abs=SELECTION_TOLERANCE), (
            f"{slug}/{rep}: top60[{index}] Selection sapmasi 1e-9 toleransini "
            f"asiyor (got={got!r}, want={want!r})"
        )


# ---------------------------------------------------------------------------
# Fixture bütünlüğü — golden dosyasinin kendisi bozulmus/eksikse sessiz
# gecmemesi icin (yanlis-yesil koruma).
# ---------------------------------------------------------------------------


def test_golden_fixture_has_three_firms_and_three_repeats_each() -> None:
    assert set(GOLDEN["firmalar"]) == {"optimice", "dijital", "gr7"}
    for slug, firm in GOLDEN["firmalar"].items():
        assert set(firm["tekrarlar"]) == {"r1", "r2", "r3"}, slug
    assert len(CELLS) == 9


def test_golden_fixture_every_universe_row_has_relevance_and_intent() -> None:
    """9 hucrenin hepsinde evrendeki HER keyword_id icin rel/intent VAR —
    aksi halde `_build_rows` sessizce KeyError yerine None ureterek testi
    yanlis-yesil yapabilirdi (dict.get kullanmadik, bilerek [] ile erisiyoruz)."""
    for slug, firm in GOLDEN["firmalar"].items():
        universe_ids = {str(row[0]) for row in firm["evren"]}
        for rep, tekrar in firm["tekrarlar"].items():
            assert universe_ids <= set(tekrar["rel"]), f"{slug}/{rep}: rel eksik"
            assert universe_ids <= set(tekrar["intent"]), f"{slug}/{rep}: intent eksik"
