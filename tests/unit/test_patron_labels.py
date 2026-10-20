"""Patron etiket yükleyici + eşleme — DB'siz."""
from __future__ import annotations

import json
import shutil

import pytest

from app.core.benchmark.patron_labels import (
    LABELS_DIR, LabelError, labels_from_doc, load_labels, match_units,
    match_units_detailed, normalize_text, precision, recall)
from app.core.benchmark.reference_characterization import CharacterizationError


def test_label_counts_match_sealed_artifacts():
    gr7, dij, opt = load_labels("gr7"), load_labels("dijital"), load_labels("optimice")
    assert (gr7.n["SEO"], len(gr7.negatives("SEO"))) == (72, 14)
    assert (gr7.n["ADS"], len(gr7.negatives("ADS"))) == (50, 12)
    assert (gr7.n["SOCIAL"], len(gr7.negatives("SOCIAL"))) == (85, 24)
    assert dij.n == {"ADS": 17, "SEO": 33, "SOCIAL": 20}
    assert opt.n == {"ADS": 123, "SEO": 52, "SOCIAL": 64}
    assert opt.negatives("ADS") == set()          # seçilmeme negatif değil
    assert (opt.source_workspace_id, gr7.source_workspace_id, dij.source_workspace_id) == (21, 39, 38)
    assert gr7.positives("seo") == set(gr7.units("SEO"))


def test_optimice_keys_are_normalization_fixed_points():
    # arşiv anahtarı normalize ETMEDEN eşler; bu varsayım doğru olmalı
    assert all(normalize_text(k) == k for k in load_labels("optimice").positives("ADS"))


def test_index_sha_verifies_and_tamper_is_rejected(tmp_path):
    for f in LABELS_DIR.glob("*.json"):
        shutil.copy(f, tmp_path / f.name)
    assert load_labels("dijital", tmp_path).sha256 == load_labels("dijital").sha256
    p = tmp_path / "patron_labels_dijital_v2.json"
    p.write_bytes(p.read_bytes() + b" ")
    with pytest.raises(LabelError):
        load_labels("dijital", tmp_path)
    idx = tmp_path / "index.json"
    d = json.loads(idx.read_text(encoding="utf-8"))
    d["files"]["patron_labels_gr7_v2.json"]["sha256"] = "0" * 64
    idx.write_text(json.dumps(d), encoding="utf-8")
    with pytest.raises(CharacterizationError):
        load_labels("gr7", tmp_path)       # index mührü bozuldu
    with pytest.raises(LabelError):
        load_labels("nope")


def _tiny():
    doc = {"workspace_id": 7, "current_positive": [
        {"canonical_keyword": "beyaz sac kapatici", "source_keywords": ["beyaz saç kapatıcı"],
         "labels": ["SEO", "ADS"]},
        {"canonical_keyword": "İş Bankası kredi", "source_keywords": [], "labels": ["SEO"]},
        {"canonical_keyword": "evrende yok", "source_keywords": [], "labels": ["SEO"]},
        {"canonical_keyword": "sadece sosyal", "labels": ["SOCIAL"]}],
        "policy_negative": [{"canonical_keyword": "fiyat", "labels": ["SEO"]}]}
    return labels_from_doc("tiny", doc, "x")


def test_match_units_parity_on_tiny_universe():
    lab = _tiny()
    uni = {1: "Beyaz Saç Kapatıcı", 2: "beyaz sac kapatici", 3: "is bankasi KREDI", 4: "fiyat", 9: "x"}
    m = match_units(lab, uni, "SEO")
    assert m == {"beyaz sac kapatici": {1, 2}, "is bankasi kredi": {3}, "evrende yok": set()}
    assert match_units(lab, uni, "SEO", negatives=True) == {"fiyat": {4}}
    det = match_units_detailed(lab, uni, "SEO", excluded={50: "Evrende Yok"})
    assert det["evrende yok"] == {"universe": [], "excluded": [50]}
    assert lab.positives("SOCIAL") == {"sadece sosyal"}


def test_recall_uses_canonical_denominator_and_any_row_rule():
    m = match_units(_tiny(), {1: "beyaz sac kapatici", 2: "Beyaz Saç Kapatıcı", 3: "is bankasi kredi"}, "SEO")
    assert recall({1}, m) == (1, 3)              # iki satırdan biri yeter; payda 3 (evrende yok dahil)
    assert recall({1, 2, 3}, m) == (2, 3)
    assert recall(set(), m) == (0, 3)
    assert precision({1, 2, 99}, m) == (1, 3)    # alt sınır: etiketsizler yanlış sayılır
