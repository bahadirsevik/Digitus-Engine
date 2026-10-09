# -*- coding: utf-8 -*-
"""SEO katı-2 (app/core/engine/seo) — KALICI GOLDEN PARITY testi.

QA görevi: Motor v3 Faz 4 = SEO katı-2. Üretim kodu `app/core/engine/seo/`
altında zaten yazıldı (BEN yazmadım); bu dosya SADECE test'tir, `app/` ve
`scripts/` altına dokunulmadı.

Tracked `benchmark/seo_v31_kati2_golden_v2.json` TEK OTORİTE. `benchmark/private`e
HİÇBİR bağımlılık yok (dosya yoksa skip de YOK — golden zaten repoya tracked).

Akış YALNIZ ÜRETİM fonksiyonlarıyla kurulur (kilit script'i
`scripts/seo_v31_kati2_lock_v2.py` hiç import edilmez / çağrılmaz):

    rows.build_rows(universe, family_by_id=..., subintent=..., relevance=...,
                     bp=..., authority=...)
    -> urlgroup.run_two_pass(rows, urlgroup_map,
                              contract=select.production_contract(permutations=0))

Kanonik çıktı + SHA hesabı burada `scripts/seo_v31_kati2_lock_v2.py`'nin
`canonical_output` / `summarize_cell` / `sha_payload` fonksiyonlarıyla BİREBİR
aynı mantıkla, o script'i import ETMEDEN, bağımsız olarak yeniden uygulanır —
aksi halde "aynı kodu iki kez çağırıp kendisiyle karşılaştırma" döngüsüne
düşülür ve golden'ın üretim koduna GERÇEKTEN eşit olduğu kanıtlanmamış olur.

Ben elle koşturdum (bu dosyanın yazarı): 15/15 hücre birebir geçiyor.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict

import pytest

from app.core.engine.seo import rows as ROWS
from app.core.engine.seo import select as SEL
from app.core.engine.seo import urlgroup as URLG

REPO = Path(__file__).resolve().parents[2]
GOLDEN_PATH = REPO / "benchmark" / "seo_v31_kati2_golden_v2.json"
N_ALL = ("10", "15", "20", "30", "60")

assert GOLDEN_PATH.is_file(), (
    f"Golden dosyası tracked olmalı ve mevcut olmalı: {GOLDEN_PATH} — "
    "bu test benchmark/private'a BAĞIMLI DEĞİLDİR, skip de yoktur."
)
GOLDEN: Dict[str, Any] = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))

CASES = [
    (slug, rep)
    for slug in GOLDEN["girdi"]["firmalar"]
    for rep in GOLDEN["girdi"]["firmalar"][slug]["tekrarlar"]
]


def test_golden_dosyasi_5_firma_3_tekrar_15_hucre() -> None:
    assert len(GOLDEN["girdi"]["firmalar"]) == 5
    for firm in GOLDEN["girdi"]["firmalar"].values():
        assert set(firm["tekrarlar"]) == {"r1", "r2", "r3"}
    assert len(CASES) == 15


# ── kanonikleştirme + SHA — scripts/seo_v31_kati2_lock_v2.py ile BİREBİR ──

def _canon(value: Any) -> Any:
    """JSON gidiş-dönüşü: int anahtarlar str olur, tuple liste olur."""
    return json.loads(json.dumps(value, ensure_ascii=False))


def _sha_payload(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _canonical_output(detail: Dict[str, Any]) -> Dict[str, Any]:
    """`scripts/seo_v31_kati2_lock_v2.py::canonical_output` ile BİREBİR aynı.

    DİKKAT: lock script'in `canonical_output`'u `detail["final"]`'i doğrudan
    kullanır, ama lock script'teki `detail`, `evaluate()`'in HAM çıktısı
    DEĞİL — `scripts/seo_v31_final.py::run_one()`'ın DÖNDÜRDÜĞÜ sözlüktür ve
    o fonksiyon "final" alanını ORADA `{str(k): round(v, 6) for k, v in
    main["final"].items()}` ile 6 ondalığa YUVARLAR (satır ~111). Burada
    `run_two_pass()`'in HAM (yuvarlanmamış) `final` çıktısını kullandığımız
    için aynı yuvarlamayı BURADA açıkça uyguluyoruz; aksi halde iki tarafın
    kayan nokta gösterimleri (örn. 12.345600000000001 vs 12.3456) SHA
    düzeyinde uyuşmaz.
    """
    cap = detail["capacity"]
    return _canon({
        "final": {str(k): round(v, 6) for k, v in detail["final"].items()},
        "cluster_groups": detail["cluster_groups"],
        "selection": {k: detail["selection"][k]
                     for k in ("selected", "merged", "deferred")},
        "capacity": {n: {"top_ids": cap[n]["top_ids"],
                         "esik_gevsetilerek": cap[n]["esik_gevsetilerek"],
                         "filled": cap[n]["filled"], "ertelenen": cap[n]["ertelenen"],
                         "secondary_sha256": _sha_payload(_canon(cap[n].get("secondary")))}
                    for n in N_ALL},
    })


def _summarize_cell(canon: Dict[str, Any]) -> Dict[str, Any]:
    return {"content_sha256": _sha_payload(canon),
            "top_ids": {n: canon["capacity"][n]["top_ids"] for n in N_ALL},
            "final_sha256": _sha_payload(canon["final"]),
            "secondary_sha256": {n: canon["capacity"][n]["secondary_sha256"] for n in N_ALL},
            "selection_sha256": _sha_payload(canon["selection"]),
            "cluster_groups_sha256": _sha_payload(canon["cluster_groups"])}


def _run_cell(slug: str, rep: str) -> Dict[str, Any]:
    firm = GOLDEN["girdi"]["firmalar"][slug]
    rep_data = firm["tekrarlar"][rep]

    universe = [
        SimpleNamespace(keyword_id=kid, keyword_text="", volume=vol,
                        trend_3m=t3, trend_12m=t12, competition=comp)
        for kid, vol, t3, t12, comp in firm["satirlar"]
    ]
    rows = ROWS.build_rows(
        universe,
        family_by_id=firm["aile"],
        subintent=firm["subintent"],
        relevance=rep_data["relevance"],
        bp=rep_data["bp"],
        authority=rep_data["authority"],
    )
    main = URLG.run_two_pass(
        rows, rep_data["urlgroup"],
        contract=SEL.production_contract(permutations=0))
    return _summarize_cell(_canonical_output(main))


@pytest.mark.parametrize("slug,rep", CASES, ids=[f"{s}-{r}" for s, r in CASES])
def test_kati2_golden_parity(slug: str, rep: str) -> None:
    got = _run_cell(slug, rep)
    want = GOLDEN["beklenen"][slug][rep]

    # Parça parça: sapmada HANGİ katmanın kaydığı hemen görünsün.
    assert got["final_sha256"] == want["final_sha256"], (
        f"{slug}/{rep}: final_sha256 farklı — skor katmanı (rows.build_rows / "
        "select.compute_components) sapmış")
    assert got["selection_sha256"] == want["selection_sha256"], (
        f"{slug}/{rep}: selection_sha256 farklı — seçim (select.select) sapmış")
    assert got["cluster_groups_sha256"] == want["cluster_groups_sha256"], (
        f"{slug}/{rep}: cluster_groups_sha256 farklı — URL grubu eşlemesi "
        "(urlgroup.cluster_group_map / select.cluster_groups) sapmış")
    for n in N_ALL:
        assert got["top_ids"][n] == want["top_ids"][n], (
            f"{slug}/{rep}: top_ids@N{n} farklı\n"
            f"  got : {got['top_ids'][n]}\n"
            f"  want: {want['top_ids'][n]}")
        assert got["secondary_sha256"][n] == want["secondary_sha256"][n], (
            f"{slug}/{rep}: secondary_sha256@N{n} farklı — select.secondary_pools sapmış")

    # Bütün parçalar eşleştiyse content_sha256 da eşleşmeli (kanonikleştirme
    # kapsamı/sırası dışında content'i etkileyen başka bir alan yok).
    assert got["content_sha256"] == want["content_sha256"], (
        f"{slug}/{rep}: parça SHA'ları eşleşti ama content_sha256 farklı — "
        "kanonikleştirmenin kapsadığı bir alan gözden kaçmış olabilir")
