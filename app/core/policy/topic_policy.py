"""Dışlanan konu politikası (plan B — SOCIAL; plan v13 tek-kaynak senkronu).

Veri modeli (BrandProfile.topic_policy):
  {excluded_terms:  [{term, status, source, created_at}],
   excluded_aliases:[{term, status, source, created_at}]}

- profile_data.exclude_themes YALNIZ prompt yönlendirmesidir (semantik).
- Deterministik engel YALNIZCA kullanıcı onaylı term/alias'larla çalışır —
  otomatik kök türetme YOKTUR ("tekil hisse" -> "hisse" felaketi tasarımla kapalı).
- Eşleşme competitor_policy.match_term ile aynı token-sınırlı kuraldır.
- v13: excluded_info metni excluded_terms için TEK doğruluk kaynağıdır
  (sync_excluded_terms). Topic kayıtları rakip provenance alanları
  (manual_approved/source_urls) TAŞIMAZ — anlamsızdır.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, List, Optional

from app.core.policy.competitor_policy import (
    TERM_APPROVED,
    TERM_REJECTED,
    match_term,
)
from app.core.site_analyzer.turkish_normalizer import normalize_turkish


def approved_topic_terms(profile) -> List[str]:
    """Onaylı excluded_terms + excluded_aliases birleşimi."""
    policy = getattr(profile, "topic_policy", None) or {}
    terms: List[str] = []
    for key in ("excluded_terms", "excluded_aliases"):
        for entry in policy.get(key) or []:
            if entry.get("status") == TERM_APPROVED and entry.get("term"):
                terms.append(entry["term"])
    return terms


def match_topic_term(text: str, terms: List[str]) -> Optional[str]:
    """Token-sınırlı dışlanan-konu eşleşmesi (None = temiz)."""
    return match_term(text, terms)


# ── v13: excluded_info parse + tek-kaynak senkron yardımcıları ──
# (API katmanından core'a taşındı — brand_profile.py bunları import eder.)

def parse_excluded_info(value: Any) -> List[str]:
    """Kullanıcının mutlaka-olmasın metnini virgül/yeni satırla böl, dedup et."""
    if not value:
        return []
    if isinstance(value, list):
        candidates = [str(item) for item in value]
    else:
        candidates = re.split(r"[,\n]+", str(value))
    seen: set = set()
    parsed: List[str] = []
    for item in candidates:
        text = item.strip()
        key = normalize_turkish(text)
        if not text or not key or key in seen:
            continue
        seen.add(key)
        parsed.append(text)
    return parsed


def remove_normalized(items: Optional[List[str]], to_remove: List[str]) -> List[str]:
    """items'tan, normalize edilince to_remove'da olanları çıkar (sıra korunur)."""
    remove_keys = {normalize_turkish(t) for t in to_remove}
    return [
        item for item in (items or [])
        if normalize_turkish(str(item)) not in remove_keys
    ]


def merge_normalized(*lists: Optional[List[str]]) -> List[str]:
    """Listeleri sırayı koruyarak normalize-dedup ile birleştir."""
    seen: set = set()
    merged: List[str] = []
    for lst in lists:
        for item in lst or []:
            text = str(item).strip()
            key = normalize_turkish(text)
            if not text or not key or key in seen:
                continue
            seen.add(key)
            merged.append(text)
    return merged


def _upsert_topic_status(
    entries: Optional[List[dict]], term: str, status: str
) -> List[dict]:
    """Basit topic upsert: normalize_turkish ile bul, varsa status güncelle,
    yoksa {term, status, source: user, created_at} ekle. Rakip provenance
    alanları YAZILMAZ."""
    norm = normalize_turkish(term)
    updated: List[dict] = []
    found = False
    for entry in entries or []:
        if normalize_turkish(entry.get("term", "")) == norm:
            found = True
            updated.append({**entry, "status": status})
        else:
            updated.append(entry)
    if not found:
        updated.append({
            "term": term,
            "status": status,
            "source": "user",
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
    return updated


def upsert_topic_status(
    entries: Optional[List[dict]], term: str, status: str
) -> List[dict]:
    """Public alias — API'nin topic_term/topic_alias upsert'i bunu kullanır."""
    return _upsert_topic_status(entries, term, status)


def sync_excluded_terms(
    existing: Optional[List[dict]], wanted_terms: List[str]
) -> List[dict]:
    """excluded_info metnini topic_policy.excluded_terms için TEK doğruluk
    kaynağı yapar: wanted_terms onaylanır/eklenir; önceden approved olup artık
    listede olmayanlar rejected'e döner. excluded_aliases'a DOKUNULMAZ."""
    result = list(existing or [])
    for term in wanted_terms:
        result = _upsert_topic_status(result, term, TERM_APPROVED)
    wanted_norm = {normalize_turkish(t) for t in wanted_terms}
    return [
        {**entry, "status": TERM_REJECTED}
        if entry.get("status") == TERM_APPROVED
        and normalize_turkish(entry.get("term", "")) not in wanted_norm
        else entry
        for entry in result
    ]
