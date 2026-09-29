"""SEO kati-2 — satir birlestirme (URETIM KOPYASI).

plan_algoritma_entegrasyonu.md Faz 4 · SEO kod haritasi 4. adim. Kilitli
`scripts/seo_v3_final.build_rows` + `scripts/seo_v31_ai_run.rows_with_authority`
davranisinin uretim kopyasidir; dosya/`Env` iskelesi TASINMAZ — girdiler
donmus evren (Faz 1 snapshot), Faz 2 ailesi ve bu run'in sinyal asamalaridir.

Eksik sinyal SESSIZCE gecilmez: her satirin alt niyeti ve relevance'i olmak
ZORUNDADIR (BP yalniz `Rel >= 0,50` evreninde uretildigi icin None olabilir;
authority yalniz dar pencerede uretilir).
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence


class MissingSignalError(RuntimeError):
    """Satir birlestirmede sinyal eksik — kosu durur (fail-closed)."""


def build_rows(universe: Sequence[Any], *,
               family_by_id: Mapping[int, str],
               subintent: Mapping[Any, Any],
               relevance: Mapping[Any, Any],
               bp: Optional[Mapping[Any, Any]] = None,
               authority: Optional[Mapping[Any, Any]] = None
               ) -> List[Dict[str, Any]]:
    """Donmus evren + aile + sinyaller -> secim/skor satirlari.

    Sozluk anahtarlari int'e normalize edilir (JSON'dan string gelebilir).
    """
    sub = _by_int(subintent)
    rel = _by_int(relevance)
    bp_map = _by_int(bp or {})
    auth = _by_int(authority or {})
    fam = _by_int(family_by_id)

    rows: List[Dict[str, Any]] = []
    for item in universe:
        keyword_id = int(item.keyword_id)
        signal = sub.get(keyword_id)
        if not signal:
            raise MissingSignalError(
                f"keyword {keyword_id}: alt niyet sinyali yok")
        broad_intent, subintent_id = _subintent_pair(keyword_id, signal)

        rel_value = _value(rel.get(keyword_id), "relevance")
        if rel_value is None:
            raise MissingSignalError(f"keyword {keyword_id}: relevance yok")

        family_id = fam.get(keyword_id)
        if not family_id:
            raise MissingSignalError(f"keyword {keyword_id}: aile yok")

        rows.append({
            "keyword_id": keyword_id,
            "keyword_text": item.keyword_text,
            "volume": item.volume,
            "competition": item.competition,
            "trend_3m": item.trend_3m,
            "trend_12m": item.trend_12m,
            "family_id": family_id,
            "broad_intent": broad_intent,
            "subintent_id": subintent_id,
            "relevance": rel_value,
            "bp": _value(bp_map.get(keyword_id), "business_proximity"),
            "authority": _value(auth.get(keyword_id), "authority"),
        })
    return rows


def _by_int(mapping: Mapping[Any, Any]) -> Dict[int, Any]:
    out: Dict[int, Any] = {}
    for key, value in (mapping or {}).items():
        try:
            out[int(key)] = value
        except (TypeError, ValueError):
            raise MissingSignalError(f"sinyalde sayisal olmayan id: {key!r}")
    return out


def _subintent_pair(keyword_id: int, signal: Any):
    if isinstance(signal, Mapping):
        broad = signal.get("broad_intent")
        sub_id = signal.get("subintent_id")
    elif isinstance(signal, (list, tuple)) and len(signal) == 2:
        broad, sub_id = signal          # golden bicimi: [broad_intent, subintent_id]
    else:
        raise MissingSignalError(
            f"keyword {keyword_id}: alt niyet bicimi taninmadi: {type(signal).__name__}")
    if not broad or not sub_id:
        raise MissingSignalError(f"keyword {keyword_id}: alt niyet eksik")
    return broad, sub_id


def _value(item: Any, field: str) -> Any:
    """Sinyal ogesi sozluk ise alani, duz deger ise kendisini dondurur."""
    if item is None:
        return None
    if isinstance(item, Mapping):
        return item.get(field)
    return item
