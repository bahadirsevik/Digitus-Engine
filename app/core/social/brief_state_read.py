# -*- coding: utf-8 -*-
"""Salt-okunur sosyal brief durum özeti (F1-H.2).

Arayüzün sayfa yenilemesi / başka oturum sonrası brief ekranını sunucudan
yeniden kurması için gereken en küçük bilgi kümesini döndürür:

- Tamamlanmış kategori sonucu (DB otoritedir; tarayıcı önbelleği değil).
- Aşama bazında attempt özetleri (id, stage, status, reason_code, zamanlar).
- Brief içinde içeriği bulunan fikir kimlikleri.

Sözleşme:
- commit / rollback / flush yapmaz, FOR UPDATE kullanmaz, satır mutate etmez.
- Süresi dolmuş lease'ler burada uzlaştırılmaz (kanonik kilit sırası gerektirir);
  yalnız ``lease_expired`` projeksiyonu verilir. Gerçek uzlaştırma attempt
  GET uçlarında ve yeni attempt açılırken yapılır.
- Product facts, USP, prompt, task_id, worker/lease ayrıntısı veya coverage
  snapshot'ı DIŞARI ÇIKMAZ.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.social.category_persistence import (
    PersistedSocialCategory,
    load_completed_social_categories,
)
from app.database.models import (
    BrandProfile,
    ScoringRun,
    SocialBrief,
    SocialContent,
    SocialGenerationAttempt,
)

# Bir brief'te gösterilecek en fazla tarihsel attempt (aşama başına).
BRIEF_STATE_ATTEMPT_HISTORY_LIMIT = 20


class SocialBriefStateNotFoundError(Exception):
    """Brief bulunamadı veya başka workspace'e ait (404 semantiği)."""


@dataclass(frozen=True)
class SocialAttemptSummary:
    id: int
    stage: str
    status: str
    reason_code: Optional[str]
    created_at: Optional[datetime]
    completed_at: Optional[datetime]
    lease_expired: bool
    requested_idea_ids: tuple[int, ...]


@dataclass(frozen=True)
class SocialBriefStateResult:
    brief_id: int
    scoring_run_id: int
    is_stale: bool
    locked_at: Optional[datetime]
    category_attempt: Optional[SocialAttemptSummary]
    categories: tuple[PersistedSocialCategory, ...]
    ideas_attempt: Optional[SocialAttemptSummary]
    idea_retry_attempts: tuple[SocialAttemptSummary, ...]
    content_attempts: tuple[SocialAttemptSummary, ...]
    content_idea_ids: tuple[int, ...]


def _to_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _summarize(attempt: SocialGenerationAttempt, now: datetime) -> SocialAttemptSummary:
    lease = _to_utc(attempt.lease_expires_at)
    lease_expired = (
        attempt.status in ("pending", "running") and lease is not None and lease <= now
    )
    requested: tuple[int, ...] = ()
    if attempt.stage == "contents" and isinstance(attempt.requested_idea_ids, list):
        requested = tuple(
            i for i in attempt.requested_idea_ids
            if isinstance(i, int) and not isinstance(i, bool) and i > 0
        )
    return SocialAttemptSummary(
        id=attempt.id,
        stage=attempt.stage,
        status=attempt.status,
        reason_code=attempt.reason_code,
        created_at=attempt.created_at,
        completed_at=attempt.completed_at,
        lease_expired=lease_expired,
        requested_idea_ids=requested,
    )


def load_social_brief_state(
    db: Session,
    *,
    brief_id: int,
    brand_profile_id: int,
    now: Optional[datetime] = None,
) -> SocialBriefStateResult:
    """Brief durum özetini workspace izolasyonuyla okur.

    Raises:
        SocialBriefStateNotFoundError: Brief yoksa, run başka workspace'e aitse
            veya workspace arşivlenmişse.
        SocialCategoryPersistenceError: Tamamlanmış kategori verisi tutarsızsa
            (fail-closed; çağıran 500'e çevirir).
    """
    current_time = _to_utc(now) or datetime.now(timezone.utc)

    brief = db.scalars(
        select(SocialBrief)
        .join(ScoringRun, SocialBrief.scoring_run_id == ScoringRun.id)
        .join(BrandProfile, ScoringRun.brand_profile_id == BrandProfile.id)
        .where(
            SocialBrief.id == brief_id,
            ScoringRun.brand_profile_id == brand_profile_id,
            BrandProfile.deleted_at.is_(None),
        )
    ).first()
    if brief is None:
        raise SocialBriefStateNotFoundError()

    attempts = (
        db.query(SocialGenerationAttempt)
        .filter(SocialGenerationAttempt.brief_id == brief.id)
        .order_by(SocialGenerationAttempt.id.desc())
        .all()
    )
    by_stage: dict[str, list[SocialGenerationAttempt]] = {}
    for a in attempts:
        by_stage.setdefault(a.stage, []).append(a)

    # Kategori: tamamlanmış attempt varsa o (en fazla bir tane olabilir), yoksa en yenisi.
    category_rows = by_stage.get("categories", [])
    completed_cat = next((a for a in category_rows if a.status == "completed"), None)
    category_row = completed_cat or (category_rows[0] if category_rows else None)

    categories: tuple[PersistedSocialCategory, ...] = ()
    # Eskimiş brief'in kategorileri de eskidir; okuma doğrulayıcısı stale satırı
    # kabul etmez. Brief zaten üretime kapalı olduğu için kategori listesi boş döner.
    if completed_cat is not None and not brief.is_stale:
        persisted = load_completed_social_categories(
            db,
            brief_id=brief.id,
            attempt_id=completed_cat.id,
            brand_profile_id=brand_profile_id,
        )
        categories = tuple(persisted.categories)

    ideas_rows = by_stage.get("ideas", [])
    retry_rows = by_stage.get("ideas_retry", [])[:BRIEF_STATE_ATTEMPT_HISTORY_LIMIT]
    content_rows = by_stage.get("contents", [])[:BRIEF_STATE_ATTEMPT_HISTORY_LIMIT]

    content_idea_ids = tuple(
        row[0]
        for row in (
            db.query(SocialContent.idea_id)
            .filter(
                SocialContent.brief_id == brief.id,
                SocialContent.is_stale.is_(False),
            )
            .order_by(SocialContent.idea_id.asc())
            .all()
        )
    )

    return SocialBriefStateResult(
        brief_id=brief.id,
        scoring_run_id=brief.scoring_run_id,
        is_stale=bool(brief.is_stale),
        locked_at=brief.locked_at,
        category_attempt=_summarize(category_row, current_time) if category_row else None,
        categories=categories,
        ideas_attempt=_summarize(ideas_rows[0], current_time) if ideas_rows else None,
        idea_retry_attempts=tuple(_summarize(a, current_time) for a in retry_rows),
        content_attempts=tuple(_summarize(a, current_time) for a in content_rows),
        content_idea_ids=content_idea_ids,
    )
