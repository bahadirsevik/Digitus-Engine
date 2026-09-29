# -*- coding: utf-8 -*-
"""Social Brief Kategori Üretimi Preflight, Brief Lock ve Attempt Başlatma (F1-E.1).

Bu modül kategori AI üretimine başlamadan önce brief'i immutable hale getirmek ve
`categories` attempt kaydını aynı transaction içinde idempotent biçimde oluşturmak
için gereken preflight ve kilitleme mantığını uygular.

Kurallar:
- Transaction açmaz (db.begin() çağırmaz).
- commit veya rollback çağırmaz.
- AI çağrısı yapmaz.
- Kategori satırı oluşturmaz.
- Global kilit sırası (BrandProfile -> ScoringRun -> SocialBrief -> SocialGenerationAttempt)
  korunur.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.database.models import (
    BrandProfile,
    ScoringRun,
    SocialBrief,
    SocialBriefKeyword,
    SocialCategory,
)
from app.generators.social.attempt_state import (
    AttemptConflictError,
    AttemptNotWritableError,
    BriefNotFoundError,
    InvalidStageError,
    create_or_get_attempt,
)
from app.schemas.social_brief import SocialBriefCategoriesGenerateRequest


class SocialCategoryFlowError(ValueError):
    """Kategori üretim akışı domain hatası."""

    def __init__(
        self,
        message: str,
        *,
        error_code: str,
        field: str | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.field = field


@dataclass(frozen=True)
class CategoryKeywordSnapshot:
    """Kategori üretimine iletilecek tekil anahtar kelime snapshot'ı."""

    keyword_id: int
    keyword_snapshot: str
    position: int


@dataclass(frozen=True)
class SocialCategoryGenerationStart:
    """Kategori üretimi preflight ve attempt başlatma sonucu (immutable)."""

    brief_id: int
    scoring_run_id: int
    brand_profile_id: int
    attempt_id: int
    attempt_created: bool
    attempt_status: str
    idempotency_key: str
    max_categories: int | None
    locked_at: datetime
    brand_name_snapshot: str | None
    brand_context_snapshot: str | None
    keywords: tuple[CategoryKeywordSnapshot, ...]


def _validate_now(now: datetime | None) -> datetime:
    """Timezone-aware UTC zamanını doğrular veya üretir."""
    if now is not None:
        if now.tzinfo is None:
            raise ValueError(
                "Enjekte edilen 'now' parametresi timezone-aware olmalıdır (tzinfo is None)."
            )
        return now.astimezone(timezone.utc)
    return datetime.now(timezone.utc)


def begin_social_category_generation(
    db: Session,
    *,
    brief_id: int,
    brand_profile_id: int,
    request: SocialBriefCategoriesGenerateRequest,
    now: datetime | None = None,
) -> SocialCategoryGenerationStart:
    """Kategori AI üretimine başlamadan önce brief'i kilitler ve categories attempt başlatır.

    Args:
        db: Aktif SQLAlchemy oturumu (commit/rollback çağrılmaz).
        brief_id: İşlem yapılacak SocialBrief kimliği.
        brand_profile_id: Workspace (BrandProfile) kimliği.
        request: Doğrulanmış kategori üretim isteği (idempotency_key, max_categories).
        now: Opsiyonel test zaman enjeksiyonu (timezone-aware).

    Returns:
        SocialCategoryGenerationStart: Immutable preflight ve attempt durum nesnesi.

    Raises:
        SocialCategoryFlowError: BRIEF_NOT_FOUND veya CATEGORIES_ALREADY_GENERATED durumlarında.
        AttemptConflictError: Farklı idempotency_key ile aktif attempt varsa.
        AttemptNotWritableError: BRIEF_STALE veya ASSIGNMENT_CHANGED durumlarında.
        ValueError: Naive datetime verildiğinde.
    """
    current_time = _validate_now(now)

    # 1. Kilitsiz ön okuma: kimlik keşfi için scoring_run_id oku
    brief_pre = (
        db.query(SocialBrief.id, SocialBrief.scoring_run_id)
        .filter(SocialBrief.id == brief_id)
        .first()
    )
    if brief_pre is None:
        raise SocialCategoryFlowError(
            f"SocialBrief bulunamadı: {brief_id}",
            error_code="BRIEF_NOT_FOUND",
        )
    pre_run_id = brief_pre.scoring_run_id

    # 2. Global kilit sırası: BrandProfile -> ScoringRun -> SocialBrief
    brand_profile = (
        db.query(BrandProfile)
        .filter(BrandProfile.id == brand_profile_id, BrandProfile.deleted_at.is_(None))
        .populate_existing()
        .with_for_update()
        .one_or_none()
    )
    if brand_profile is None:
        raise SocialCategoryFlowError(
            f"SocialBrief bulunamadı: {brief_id}",
            error_code="BRIEF_NOT_FOUND",
        )

    scoring_run = (
        db.query(ScoringRun)
        .filter(
            ScoringRun.id == pre_run_id,
            ScoringRun.brand_profile_id == brand_profile_id,
        )
        .populate_existing()
        .with_for_update()
        .one_or_none()
    )
    if scoring_run is None:
        raise SocialCategoryFlowError(
            f"SocialBrief bulunamadı: {brief_id}",
            error_code="BRIEF_NOT_FOUND",
        )

    brief = (
        db.query(SocialBrief)
        .filter(
            SocialBrief.id == brief_id,
            SocialBrief.scoring_run_id == scoring_run.id,
        )
        .populate_existing()
        .with_for_update()
        .one_or_none()
    )
    if brief is None or brief.scoring_run_id != pre_run_id:
        raise SocialCategoryFlowError(
            f"SocialBrief bulunamadı: {brief_id}",
            error_code="BRIEF_NOT_FOUND",
        )

    # 3. Fail-closed stale ve version mismatch kontrolleri
    if brief.is_stale:
        raise AttemptNotWritableError(
            f"Brief {brief_id} stale durumda; yeni kategori üretimi başlatılamaz.",
            error_code="BRIEF_STALE",
        )
    if brief.channel_assignment_version != scoring_run.channel_assignment_version:
        raise AttemptNotWritableError(
            f"Assignment version uyuşmazlığı: brief_version={brief.channel_assignment_version}, "
            f"run_version={scoring_run.channel_assignment_version}; yeni kategori üretimi başlatılamaz.",
            error_code="ASSIGNMENT_CHANGED",
        )

    # 4. Categories attempt elde etme / oluşturma (ScoringRun -> Brief -> Attempt sırasıyla kilitlenir)
    attempt, attempt_created = create_or_get_attempt(
        db,
        brief_id=brief_id,
        stage="categories",
        idempotency_key=request.idempotency_key,
        now=current_time,
    )

    # 5. Mevcut kategorilere karşı koruma
    has_existing_categories = (
        db.query(SocialCategory.id)
        .filter(SocialCategory.brief_id == brief_id)
        .first()
        is not None
    )
    if attempt_created and has_existing_categories:
        raise SocialCategoryFlowError(
            f"Brief {brief_id} için kategoriler zaten üretilmiş.",
            error_code="CATEGORIES_ALREADY_GENERATED",
        )

    # 6. Brief lock: locked_at null ise current_time yaz, doluysa koru (asla ileri taşınmaz)
    if brief.locked_at is None:
        brief.locked_at = current_time

    # 7. Keyword snapshot'larını position ASC, id ASC sırasıyla yükle ve doğrula
    keywords_rows = (
        db.query(SocialBriefKeyword)
        .filter(SocialBriefKeyword.brief_id == brief_id)
        .order_by(SocialBriefKeyword.position.asc(), SocialBriefKeyword.id.asc())
        .all()
    )
    if not keywords_rows:
        raise SocialCategoryFlowError(
            f"Brief {brief_id} için anahtar kelime bulunamadı.",
            error_code="BRIEF_NOT_FOUND",
        )

    keyword_snapshots: list[CategoryKeywordSnapshot] = []
    for expected_pos, kw in enumerate(keywords_rows):
        if kw.keyword_id is None:
            raise SocialCategoryFlowError(
                f"SocialBriefKeyword id={kw.id} keyword_id alanı null olamaz.",
                error_code="BRIEF_NOT_FOUND",
            )
        if kw.position is None or kw.position != expected_pos:
            raise SocialCategoryFlowError(
                f"SocialBriefKeyword id={kw.id} geçersiz veya kesintili pozisyona sahip: {kw.position} (beklenen: {expected_pos}).",
                error_code="BRIEF_NOT_FOUND",
            )
        if not kw.keyword_snapshot or not kw.keyword_snapshot.strip():
            raise SocialCategoryFlowError(
                f"SocialBriefKeyword id={kw.id} keyword_snapshot alanı boş olamaz.",
                error_code="BRIEF_NOT_FOUND",
            )
        keyword_snapshots.append(
            CategoryKeywordSnapshot(
                keyword_id=kw.keyword_id,
                keyword_snapshot=kw.keyword_snapshot,
                position=kw.position,
            )
        )

    # Başarı durumunda flush (commit yapılmaz)
    db.flush()

    return SocialCategoryGenerationStart(
        brief_id=brief.id,
        scoring_run_id=scoring_run.id,
        brand_profile_id=brand_profile_id,
        attempt_id=attempt.id,
        attempt_created=attempt_created,
        attempt_status=attempt.status,
        idempotency_key=request.idempotency_key,
        max_categories=request.max_categories if attempt_created else None,
        locked_at=brief.locked_at,
        brand_name_snapshot=brief.brand_name_snapshot,
        brand_context_snapshot=brief.brand_context_snapshot,
        keywords=tuple(keyword_snapshots),
    )
