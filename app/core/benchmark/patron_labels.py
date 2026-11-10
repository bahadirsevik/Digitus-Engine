"""Patron etiket setleri yükleyici + eşleme (Claude bake-off G1 için, DB'siz).

Etiket dosyaları `benchmark/labels/` altında arşivden BAYT-AYNI kopyadır;
`benchmark/labels/index.json` (reference_characterization.seal ile mühürlü)
dosya sha256'larını taşır ve yükleme sırasında doğrulanır (fail-closed).

Port edilen arşiv semantiği (okunmuş kaynaklar):
- scripts/seo_v3_measure.py `load_canonical_labels` + `match_units` (:51/:77):
  * Optimice: birim = `group_labels` anahtarı (kanal bayrağı True), eşleme
    metni = anahtarın kendisi (normalize EDİLMEZ — arşivle aynı).
  * gr7 / dijital: birim = `_norm(canonical_keyword)`, eşleme metinleri =
    {canonical} ∪ `source_keywords` (hepsi `_norm`). Kanonik anahtar çakışırsa
    arşivdeki gibi SON yazan kazanır (gr7'de çakışma yok: 72/14).
  * Evren eşlemesi: `_norm(keyword_text)` == birim metinlerinden biri; birim
    evrendeki TÜM eşleşen id'lerle temsil edilir (sıralı, tekil).
- Kanonik payda kuralı: plan_seo_v3_test.md §2.1/§2.2 — birim isabet ⇔ eşleşen
  satırlarından EN AZ BİRİ seçilmiş; payda = kanonik birim sayısı (72/14);
  evrende hiç eşleşmeyen birim paydada KALIR (ITT).
- `_norm` = anchor_signal.normalize_text = " ".join(normalize_turkish(t).lower()
  .split()); arşivdeki `normalize_turkish` ile lean'dekiyle bayt-özdeştir
  (diff ile doğrulandı), anchor_signal lean'de yok -> aynı ifade burada.

Bilinen belirsizlik (yorum): K5 "Optimice ADS 16" patronun 16 seçimine atıf
yapar; artifact'ta `group_labels` ADS bayrağı 123 gruptur (ads_v3_run.load_labels
de tam-evren metin eşlemesiyle bu 123'ü kullanır). Burada artifact sayısı esas.
`n` özelliği kanal -> pozitif kanonik birim sayısı sözlüğüdür.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, FrozenSet, Iterable, List, Mapping, Optional, Set, Tuple

from app.core.benchmark.reference_characterization import (
    CharacterizationError, verify_seal)
from app.core.keyword_dedup import normalize_turkish

CHANNELS = ("ADS", "SEO", "SOCIAL")
LABELS_DIR = Path(__file__).resolve().parents[3] / "benchmark" / "labels"
LABEL_FILES = {
    "optimice": "patron_labels_optimice_v2.json",
    "gr7": "patron_labels_gr7_v2.json",
    "dijital": "patron_labels_dijital_v2.json",
}


class LabelError(CharacterizationError):
    pass


def normalize_text(text: Optional[str]) -> str:
    return " ".join(normalize_turkish(text or "").lower().split())


def _channel(channel: str) -> str:
    ch = str(channel).upper()
    if ch not in CHANNELS:
        raise LabelError(f"bilinmeyen kanal: {channel!r}")
    return ch


@dataclass(frozen=True)
class Labels:
    slug: str
    source_workspace_id: int
    sha256: str
    regime: str
    # kanal -> {kanonik birim -> eşleşme metinleri}
    pos_units: Mapping[str, Mapping[str, FrozenSet[str]]] = field(repr=False)
    neg_units: Mapping[str, Mapping[str, FrozenSet[str]]] = field(repr=False)

    def positives(self, channel: str) -> Set[str]:
        return set(self.pos_units[_channel(channel)])

    def negatives(self, channel: str) -> Set[str]:
        return set(self.neg_units[_channel(channel)])

    def units(self, channel: str, *, negatives: bool = False) -> Mapping[str, FrozenSet[str]]:
        return (self.neg_units if negatives else self.pos_units)[_channel(channel)]

    @property
    def n(self) -> Dict[str, int]:
        return {ch: len(self.pos_units[ch]) for ch in CHANNELS}


def _units(entries: Iterable[Mapping[str, Any]], channel: str) -> Dict[str, FrozenSet[str]]:
    out: Dict[str, FrozenSet[str]] = {}
    for e in entries or []:
        if channel not in (e.get("labels") or []):
            continue
        texts = {normalize_text(e["canonical_keyword"])} | {
            normalize_text(s) for s in (e.get("source_keywords") or [])}
        out[normalize_text(e["canonical_keyword"])] = frozenset(texts)
    return out


def labels_from_doc(slug: str, doc: Mapping[str, Any], sha256: str) -> Labels:
    """Ham JSON -> Labels (arşiv `load_canonical_labels` semantiği)."""
    if "group_labels" in doc:  # optimice
        pos = {ch: {key: frozenset({key}) for key, v in doc["group_labels"].items()
                    if v.get(ch.lower())} for ch in CHANNELS}
        neg = {ch: {} for ch in CHANNELS}   # seçilmeme negatif DEĞİL (label_regime)
        regime = str(doc.get("label_regime"))
    else:
        pos = {ch: _units(doc.get("current_positive"), ch) for ch in CHANNELS}
        neg = {ch: _units(doc.get("policy_negative"), ch) for ch in CHANNELS}
        regime = "positives_only"
    return Labels(slug=slug, source_workspace_id=int(doc["workspace_id"]), sha256=sha256,
                  regime=regime, pos_units=pos, neg_units=neg)


def load_labels(slug: str, labels_dir: Optional[Path] = None) -> Labels:
    d = Path(labels_dir) if labels_dir else LABELS_DIR
    if slug not in LABEL_FILES:
        raise LabelError(f"bilinmeyen etiket seti: {slug!r}")
    index = json.loads((d / "index.json").read_text(encoding="utf-8"))
    verify_seal(index)
    entry = (index.get("files") or {}).get(LABEL_FILES[slug])
    if not entry:
        raise LabelError(f"index.json'da {LABEL_FILES[slug]} yok")
    raw = (d / LABEL_FILES[slug]).read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    if sha != entry["sha256"]:
        raise LabelError(f"{LABEL_FILES[slug]} sha256 uyuşmuyor (dosya değişmiş)")
    return labels_from_doc(slug, json.loads(raw.decode("utf-8")), sha)


def match_units_detailed(labels: Labels, universe: Mapping[int, str], channel: str, *,
                         excluded: Optional[Mapping[int, str]] = None,
                         negatives: bool = False) -> Dict[str, Dict[str, List[int]]]:
    """birim -> {'universe': [ids], 'excluded': [ids]} (arşiv match_units birebir)."""
    by_norm: Dict[str, List[int]] = {}
    for kid, t in universe.items():
        by_norm.setdefault(normalize_text(t), []).append(kid)
    ex_norm: Dict[str, List[int]] = {}
    for kid, t in (excluded or {}).items():
        ex_norm.setdefault(normalize_text(t), []).append(kid)
    out: Dict[str, Dict[str, List[int]]] = {}
    for unit, texts in labels.units(channel, negatives=negatives).items():
        out[unit] = {"universe": sorted({k for t in texts for k in by_norm.get(t, [])}),
                     "excluded": sorted({k for t in texts for k in ex_norm.get(t, [])})}
    return out


def match_units(labels: Labels, universe: Mapping[int, str], channel: str, *,
                negatives: bool = False) -> Dict[str, Set[int]]:
    """birim -> evrendeki eşleşen keyword_id kümesi (boş küme = evrende yok)."""
    return {u: set(m["universe"]) for u, m in
            match_units_detailed(labels, universe, channel, negatives=negatives).items()}


def recall(final_pool_ids: Iterable[int], matched: Mapping[str, Set[int]]) -> Tuple[int, int]:
    """(isabet, n_pos). Kanonik kural: birim isabet ⇔ eşleşen satırlarından en az
    biri havuzda; payda = TÜM birimler (evrende eşleşmeyen dahil, ITT)."""
    pool = set(final_pool_ids)
    return sum(1 for ids in matched.values() if ids & pool), len(matched)


def precision(final_pool_ids: Iterable[int], matched: Mapping[str, Set[int]]) -> Tuple[int, int]:
    """(isabet birimi, havuz boyu) — ALT SINIR: etiket eksik-kapsayıcı olduğundan
    etiketsiz seçimler 'yanlış' sayılır (K5: recall birincil, precision alt sınır)."""
    pool = set(final_pool_ids)
    return sum(1 for ids in matched.values() if ids & pool), len(pool)
