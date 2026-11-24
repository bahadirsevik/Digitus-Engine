# -*- coding: utf-8 -*-
"""Kullanıcı revizyonu yaşam döngüsü (kanaldan bağımsız; tablo: content_revisions).

Durumlar: generating → ready → accepted | discarded; hata / lease aşımı → failed.
Güvenceler:
- Aynı istek tekrarı (idempotency_key) aynı revizyonu döndürür.
- Aynı içerikte aynı anda TEK 'generating' (partial unique index + açık kontrol).
- Worker kaybolursa kayıt sonsuza dek 'generating' kalmaz: okuma anında lease
  aşımı 'failed' yapılır (stuck_janitor/fail_if_stuck ile aynı yaklaşım).
- Worker yazımları attempt_token + status='generating' koşullu UPDATE'tir: geç kalan
  worker kapatılmış (failed/discarded) revizyonu yeniden 'ready' yapamaz.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Iterable, List, Optional, Tuple

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.sql import func

from app.database.models import ContentRevision

GENERATING, READY, ACCEPTED, DISCARDED, FAILED = (
    "generating", "ready", "accepted", "discarded", "failed",
)
# Worker görev süresi (time_limit 600 sn) + kuyruk payı; aşılırsa okuma anında failed
REVISION_LEASE_SECONDS = 900
STALE_MESSAGE = "Revizyon zamanında tamamlanmadı (sunucu yanıt vermeyi bıraktı). Yeniden deneyin."


class RevisionConflict(Exception):
    """Aynı içerikte süren bir revizyon var."""

    def __init__(self, revision_id: Optional[int] = None):
        super().__init__("Bu içerik için süren bir revizyon var; bitmesini bekleyin.")
        self.revision_id = revision_id


def _now() -> datetime:
    return datetime.now(timezone.utc)


def fail_expired(db: Session, channel: str, source_ids: Iterable[int]) -> int:
    """Lease'i dolmuş 'generating' revizyonları 'failed' yapar (okuma anı). Commit ETMEZ."""
    ids = list(source_ids)
    if not ids:
        return 0
    result = db.execute(
        update(ContentRevision)
        .where(
            ContentRevision.channel == channel,
            ContentRevision.source_content_id.in_(ids),
            ContentRevision.status == GENERATING,
            ContentRevision.lease_expires_at < func.now(),
        )
        .values(status=FAILED, error_message=STALE_MESSAGE, completed_at=func.now())
        .execution_options(synchronize_session=False)
    )
    return result.rowcount or 0


def create_revision(
    db: Session,
    *,
    channel: str,
    source_content_id: int,
    scoring_run_id: int,
    brand_profile_id: int,
    feedback: str,
    scope: List[str],
    source_version: int,
    idempotency_key: Optional[str],
) -> Tuple[ContentRevision, bool]:
    """Revizyonu açar. Dönüş: (revizyon, yeni_mi). Commit ETMEZ.

    - Aynı idempotency_key → mevcut revizyon (yeni_mi=False), hiçbir şey değişmez.
    - Süren (lease'i geçerli) 'generating' → RevisionConflict.
    - Bekleyen eski öneriler ('ready') yeni talep gelince 'discarded' olur.
    """
    def _replay() -> Optional[ContentRevision]:
        if not idempotency_key:
            return None
        existing = (
            db.query(ContentRevision)
            .filter_by(channel=channel, source_content_id=source_content_id,
                       idempotency_key=idempotency_key)
            .first()
        )
        if existing is not None and (existing.feedback != feedback or list(existing.scope) != list(scope)):
            # Aynı anahtar farklı talep için: eski talep sessizce kazanmasın
            raise RevisionConflict(existing.id)
        return existing

    existing = _replay()
    if existing is not None:
        return existing, False

    fail_expired(db, channel, [source_content_id])
    running = (
        db.query(ContentRevision.id)
        .filter_by(channel=channel, source_content_id=source_content_id, status=GENERATING)
        .first()
    )
    if running is not None:
        raise RevisionConflict(running.id)

    db.execute(
        update(ContentRevision)
        .where(
            ContentRevision.channel == channel,
            ContentRevision.source_content_id == source_content_id,
            ContentRevision.status == READY,
        )
        .values(status=DISCARDED, completed_at=func.now())
        .execution_options(synchronize_session=False)
    )
    revision = ContentRevision(
        channel=channel,
        source_content_id=source_content_id,
        scoring_run_id=scoring_run_id,
        brand_profile_id=brand_profile_id,
        feedback=feedback,
        scope=list(scope),
        source_version=source_version,
        status=GENERATING,
        attempt_token=uuid.uuid4().hex,
        idempotency_key=idempotency_key,
        lease_expires_at=_now() + timedelta(seconds=REVISION_LEASE_SECONDS),
    )
    savepoint = db.begin_nested()
    try:
        db.add(revision)
        db.flush()
    except IntegrityError:
        # Eşzamanlı ikinci istek partial unique index'e takıldı: aynı anahtarlı
        # gönderimse kazanan revizyon döner, değilse süren üretim çakışmasıdır
        savepoint.rollback()
        existing = _replay()
        if existing is not None:
            return existing, False
        raise RevisionConflict()
    savepoint.commit()
    return revision, True


def _guarded_write(db: Session, revision_id: int, token: str, **values) -> bool:
    result = db.execute(
        update(ContentRevision)
        .where(
            ContentRevision.id == revision_id,
            ContentRevision.attempt_token == token,
            ContentRevision.status == GENERATING,
        )
        .values(completed_at=func.now(), **values)
        .execution_options(synchronize_session=False)
    )
    db.commit()
    return bool(result.rowcount)


def mark_ready(db: Session, revision_id: int, token: str, proposed: dict) -> bool:
    """Öneriyi yazar; revizyon artık bu denemeye ait değilse (kapatılmış) False."""
    return _guarded_write(db, revision_id, token, status=READY, proposed_payload=proposed)


def mark_failed(db: Session, revision_id: int, token: str, message: str) -> bool:
    return _guarded_write(db, revision_id, token, status=FAILED, error_message=message[:500])
