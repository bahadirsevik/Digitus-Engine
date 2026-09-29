# -*- coding: utf-8 -*-
"""Workspace kanal seed'leri — doğrulama ve tek yazma kapısı (plan9 §5).

Neden ayrı modül: kanal tercihi `suggested_keywords` düz metninden
TÜRETİLEMEZ. Ölçülen sonuç (makro F1 0.590 → 0.771) bu tercihlerin yapısal
veri olmasına bağlıdır; düz metni kanal etiketi gibi yorumlamak yasaktır.

Sözleşme:
  - `Uygun değil` TEKİL seçenektir; kanallarla birlikte gönderilemez.
  - Her satır ya en az bir kanal ya da `not_suitable=true` taşımalıdır;
    ikisi de yoksa satır anlamsızdır ve reddedilir.
  - Aynı kanonik keyword workspace başına bir kez bulunur (upsert).
  - Seed'ler YALNIZ kendi workspace'inde kullanılır. Okuma yardımcıları
    her zaman `brand_profile_id` ister; global okuma yolu YOKTUR.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy.orm import Session

from app.core.keyword_normalize import normalize_keyword
from app.database.models import Keyword, WorkspaceChannelSeed, WorkspaceKeyword

CHANNELS = ("ADS", "SEO", "SOCIAL")
SEED_SOURCE = "patron_onboarding_channel_seed"
MAX_SEEDS = 20


class ChannelSeedError(ValueError):
    """Seed sözleşmesi ihlali — çağıran 400'e çevirir."""


def _clean_channels(raw: Any) -> List[str]:
    """Kanal listesini normalize eder: büyük harf, tekil, sabit sıra."""
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [raw]
    seen: List[str] = []
    for item in raw:
        value = str(item).strip().upper()
        if not value:
            continue
        if value not in CHANNELS:
            raise ChannelSeedError(
                f"Gecersiz kanal: {item!r} (izinli: {', '.join(CHANNELS)})")
        if value not in seen:
            seen.append(value)
    # Sabit sira: kayit ve prompt uretimi deterministik olsun
    return [channel for channel in CHANNELS if channel in seen]


def normalize_seed_rows(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Girdi satırlarını doğrular ve kanonikleştirir (DB'ye dokunmaz)."""
    prepared: List[Dict[str, Any]] = []
    seen_canonical: Dict[str, str] = {}
    for index, row in enumerate(rows or [], start=1):
        text = str((row or {}).get("keyword") or "").strip()
        if not text:
            raise ChannelSeedError(f"{index}. satirda keyword bos")
        canonical = normalize_keyword(text)
        if not canonical:
            raise ChannelSeedError(
                f"{text!r} normalize edildiginde bos kaldi")
        not_suitable = bool((row or {}).get("not_suitable"))
        channels = _clean_channels((row or {}).get("channels"))
        if not_suitable and channels:
            raise ChannelSeedError(
                f"{text!r}: 'Uygun degil' tekil secenektir, kanallarla "
                f"birlikte isaretlenemez")
        if not not_suitable and not channels:
            raise ChannelSeedError(
                f"{text!r}: en az bir kanal veya 'Uygun degil' gerekli")
        if canonical in seen_canonical:
            raise ChannelSeedError(
                f"{text!r} ile {seen_canonical[canonical]!r} ayni kelimeye "
                f"normalize oluyor; tekrar gonderilemez")
        seen_canonical[canonical] = text
        replaced = (row or {}).get("replaced_original_keyword")
        prepared.append({
            "keyword": text,
            "canonical_keyword": canonical,
            "channels": channels,
            "not_suitable": not_suitable,
            "replaced_original_keyword": (str(replaced).strip()
                                          if replaced else None),
        })
    if len(prepared) > MAX_SEEDS:
        raise ChannelSeedError(
            f"En fazla {MAX_SEEDS} seed gonderilebilir ({len(prepared)})")
    return prepared


def _resolve_keyword_ids(db: Session, workspace_id: int,
                         rows: List[Dict[str, Any]]) -> None:
    """Kelime evrende varsa `keyword_id` doldurulur (yoksa NULL kalır).

    Onboarding aninda oneri kelimeleri henuz `keywords` tablosunda
    olmayabilir; bu bir hata DEGILDIR. Eslesme her zaman kanonik metin
    uzerinden calisir, `keyword_id` yalniz kolaylik/denetim alanidir.
    """
    canonicals = {row["canonical_keyword"] for row in rows}
    if not canonicals:
        return
    found: Dict[str, int] = {}
    query = (db.query(Keyword.id, Keyword.keyword)
             .join(WorkspaceKeyword, WorkspaceKeyword.keyword_id == Keyword.id)
             .filter(WorkspaceKeyword.brand_profile_id == workspace_id))
    for keyword_id, text in query.all():
        canonical = normalize_keyword(text)
        if canonical in canonicals and canonical not in found:
            found[canonical] = int(keyword_id)
    for row in rows:
        row["keyword_id"] = found.get(row["canonical_keyword"])


def replace_channel_seeds(db: Session, workspace_id: int,
                          rows: Iterable[Dict[str, Any]], *,
                          labelled_by: Optional[str] = None
                          ) -> List[WorkspaceChannelSeed]:
    """Workspace'in kanal seed'lerini TAM OLARAK verilen kümeye eşitler.

    Kısmi güncelleme YOK: kullanıcı formu bir bütün olarak gönderir, kayıt
    da bir bütün olarak değişir. `commit` çağıranın sorumluluğundadır.
    """
    prepared = normalize_seed_rows(rows)
    _resolve_keyword_ids(db, workspace_id, prepared)

    existing = {seed.canonical_keyword: seed for seed in
                db.query(WorkspaceChannelSeed)
                .filter(WorkspaceChannelSeed.brand_profile_id == workspace_id)
                .all()}
    kept: List[WorkspaceChannelSeed] = []
    for row in prepared:
        seed = existing.pop(row["canonical_keyword"], None)
        if seed is None:
            seed = WorkspaceChannelSeed(
                brand_profile_id=workspace_id,
                canonical_keyword=row["canonical_keyword"],
                source=SEED_SOURCE,
            )
            db.add(seed)
        seed.keyword = row["keyword"]
        seed.keyword_id = row.get("keyword_id")
        seed.channels = list(row["channels"])
        seed.not_suitable = row["not_suitable"]
        seed.replaced_original_keyword = row["replaced_original_keyword"]
        # Her yazma SON kullanici kararidir: ilk kayit zamani ile guncel
        # karar ayirt edilebilsin diye timestamp YENILENIR. Python tarafi
        # kullanilir; `func.now()` ayni transaction'da degismez ve
        # guncelleme gorunmez kalirdi.
        seed.labelled_at = datetime.now(timezone.utc)
        if labelled_by:
            seed.labelled_by = labelled_by
        kept.append(seed)
    for stale in existing.values():          # formdan cikarilanlar silinir
        db.delete(stale)
    db.flush()
    return kept


def list_channel_seeds(db: Session,
                       workspace_id: int) -> List[WorkspaceChannelSeed]:
    """Workspace'e AİT seed'ler (global okuma yolu yoktur)."""
    return (db.query(WorkspaceChannelSeed)
            .filter(WorkspaceChannelSeed.brand_profile_id == workspace_id)
            .order_by(WorkspaceChannelSeed.canonical_keyword)
            .all())


def seeds_by_channel(db: Session, workspace_id: int) -> Dict[str, List[Dict]]:
    """Kanal başına pozitif seed'ler + ayrı `not_suitable` listesi."""
    result: Dict[str, List[Dict]] = {channel: [] for channel in CHANNELS}
    negatives: List[Dict] = []
    for seed in list_channel_seeds(db, workspace_id):
        payload = {"keyword": seed.keyword, "keyword_id": seed.keyword_id,
                   "canonical_keyword": seed.canonical_keyword}
        if seed.not_suitable:
            negatives.append(payload)
            continue
        for channel in (seed.channels or []):
            if channel in result:
                result[channel].append(payload)
    result["NOT_SUITABLE"] = negatives
    return result


def has_channel_seeds(db: Session, workspace_id: int) -> bool:
    """Seed kaydı olmayan workspace bugünkü davranışla devam eder."""
    return db.query(WorkspaceChannelSeed.id).filter(
        WorkspaceChannelSeed.brand_profile_id == workspace_id
    ).first() is not None


__all__ = ["CHANNELS", "SEED_SOURCE", "ChannelSeedError", "has_channel_seeds",
           "list_channel_seeds", "normalize_seed_rows",
           "replace_channel_seeds", "seeds_by_channel"]
