# -*- coding: utf-8 -*-
"""Social Brief Fikir Retry Preflight, DB Hazırlığı ve Attempt Snapshot (F1-F.7.2a).

Bu modül POST endpoint ve Celery worker öncesinde:
- Workspace, brief ve kaynak attempt uygunluğunu doğrular,
- Expired running/pending attempt'leri kaynak kontrolünden önce reconcile eder,
- Eksik hedefleri ve boş kategorileri (K4) yalnız non-stale SocialIdea DB satırlarından
  otoriter tespit eder (kaynak deneme + önceki tüm retry'ların fikirleri birlikte),
- Saf F1-F.7.1 retry planner'ını çağırır,
- stage="ideas_retry" SocialGenerationAttempt kaydını idempotent ve atomik hazırlar,
- Retry planını immutable coverage snapshot olarak saklar,
- Same-key replay durumunda canlı SocialIdea satırlarına dokunmadan snapshot paritesini
  saf planner ile doğrular.

Kurallar:
- Transaction açmaz (db.begin() çağırmaz).
- commit veya rollback çağırmaz.
- Başarı durumunda db.flush() kullanır.
- AI, Celery veya ağ çağrısı yapmaz.
- SocialIdea satırı oluşturmaz, silmez veya güncellemez.
- Global kilit sırası (BrandProfile -> ScoringRun -> SocialBrief -> SocialGenerationAttempt)
  kesinlikle korunur; Attempt-first FOR UPDATE yapılamaz.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.database.models import (
    BrandProfile,
    ScoringRun,
    SocialBrief,
    SocialBriefKeyword,
    SocialBriefTarget,
    SocialCategory,
    SocialGenerationAttempt,
    SocialIdea,
)
from app.generators.social.attempt_state import (
    AttemptConflictError,
    create_or_get_attempt,
    reconcile_expired_attempts,
)
from app.generators.social.format_matrix import get_platform_format
from app.core.social.idea_persistence import extract_social_idea_plan_snapshot
from app.core.social.idea_planner import (
    SocialIdeaGenerationPlan,
    build_social_idea_generation_plan,
)
from app.core.social.idea_retry_planner import (
    IdeaRetryAssignment,
    IdeaRetryPlanError,
    SocialIdeaRetryPlan,
    build_social_idea_retry_plan,
)
from app.core.social.idea_retry_snapshot import (
    SocialIdeaRetrySnapshotError,
    extract_social_idea_retry_plan_snapshot,
)
from app.schemas.social_brief import SocialBriefIdeasRetryRequest


class SocialIdeaRetryFlowError(ValueError):
    """Fikir tekrar deneme akışı domain hatası."""

    def __init__(
        self,
        message: str,
        *,
        error_code: str,
        field: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.field = field
        self.details = details

    def __repr__(self) -> str:
        return (
            f"SocialIdeaRetryFlowError(error_code={self.error_code!r}, "
            f"message={self.message!r}, field={self.field!r})"
        )


@dataclass(frozen=True)
class SocialIdeaRetryStart:
    """Fikir tekrar deneme preflight ve attempt başlatma sonucu (immutable)."""

    brief_id: int
    scoring_run_id: int
    brand_profile_id: int
    source_attempt_id: int
    attempt_id: int
    attempt_created: bool
    attempt_status: str
    idempotency_key: str
    canonical_target_ids: tuple[int, ...]
    persisted_target_ids_at_start: tuple[int, ...]
    missing_target_ids: tuple[int, ...]
    plan: SocialIdeaRetryPlan
    # None = legacy snapshot replay (kategori kapsaması kayıtlı değil)
    persisted_category_ids_at_start: tuple[int, ...] | None = None


def _validate_now(now: datetime | None) -> datetime:
    """Timezone-aware UTC zamanını doğrular veya üretir."""
    if now is not None:
        if now.tzinfo is None:
            raise ValueError(
                "Enjekte edilen 'now' parametresi timezone-aware olmalıdır (tzinfo is None)."
            )
        return now.astimezone(timezone.utc)
    return datetime.now(timezone.utc)


def _build_retry_coverage_snapshot(
    *,
    request: SocialBriefIdeasRetryRequest,
    canonical_target_ids: tuple[int, ...],
    persisted_target_ids_at_start: tuple[int, ...],
    persisted_category_ids_at_start: tuple[int, ...],
    retry_plan: SocialIdeaRetryPlan,
) -> dict[str, Any]:
    """Retry planını immutable ve versioned coverage JSON yapısına dönüştürür.

    Kategori kapsaması alanları (persisted_category_ids_at_start / empty_category_ids /
    generated.assignments) yeni moddur; bu alanlar olmayan eski snapshot'lar legacy
    olarak okunmaya devam eder (idea_retry_snapshot).
    """
    return {
        "schema_version": "ideas_retry_plan_v1",
        "request": {
            "source_attempt_id": request.source_attempt_id,
        },
        "baseline": {
            "canonical_target_ids": list(canonical_target_ids),
            "persisted_target_ids_at_start": list(persisted_target_ids_at_start),
            "persisted_category_ids_at_start": list(persisted_category_ids_at_start),
        },
        "plan": {
            "total_requested": retry_plan.total_requested,
            "missing_target_ids": list(retry_plan.missing_target_ids),
            "empty_category_ids": list(retry_plan.empty_category_ids),
            "assignments": [
                {
                    "category_id": a.category_id,
                    "target_id": a.target_id,
                    "requested_count": 1,
                }
                for a in retry_plan.assignments
            ],
        },
        "generated": {
            "total_accepted": 0,
            "target_ids": [],
            "assignments": [],
        },
    }


def _map_snapshot_error_to_flow_error(
    exc: SocialIdeaRetrySnapshotError,
) -> SocialIdeaRetryFlowError:
    """Snapshot doğrulama hatasını güvenli, iç veri sızdırmayan flow hatasına dönüştürür."""
    error_code = (
        "IDEA_RETRY_ATTEMPT_REQUEST_MISMATCH"
        if exc.error_code == "IDEA_RETRY_ATTEMPT_REQUEST_MISMATCH"
        else "IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID"
    )
    flow_message = (
        "Replay request source_attempt_id parametresi mevcut attempt ile eşleşmiyor."
        if error_code == "IDEA_RETRY_ATTEMPT_REQUEST_MISMATCH"
        else "Replay edilen attempt snapshot verisi geçersiz."
    )
    return SocialIdeaRetryFlowError(
        flow_message,
        error_code=error_code,
        field=exc.field,
    )


def begin_social_idea_retry(
    db: Session,
    *,
    brief_id: int,
    brand_profile_id: int,
    request: SocialBriefIdeasRetryRequest,
    now: datetime | None = None,
) -> SocialIdeaRetryStart:
    """Fikir tekrar deneme (retry) preflight, otoriter DB eksik hedef tespiti ve attempt snapshot başlatır.

    Args:
        db: Aktif SQLAlchemy oturumu (commit/rollback çağırmaz).
        brief_id: Hedef SocialBrief ID'si.
        brand_profile_id: Workspace (BrandProfile) ID'si.
        request: Doğrulanmış fikir retry istek nesnesi.
        now: Opsiyonel test zaman enjeksiyonu (timezone-aware).

    Returns:
        SocialIdeaRetryStart: Başlatılan veya replay edilen attempt ve plan verisi.

    Raises:
        SocialIdeaRetryFlowError: Uygunluk, durum, plan, snapshot veya doğrulama hatalarında.
        ValueError: Enjekte edilen zaman naive ise.
    """
    current_time = _validate_now(now)

    # 1. Kilitsiz ön okuma: kimlik keşfi için scoring_run_id oku
    brief_pre = (
        db.query(SocialBrief.id, SocialBrief.scoring_run_id)
        .filter(SocialBrief.id == brief_id)
        .first()
    )
    if brief_pre is None:
        raise SocialIdeaRetryFlowError(
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
        raise SocialIdeaRetryFlowError(
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
        raise SocialIdeaRetryFlowError(
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
        raise SocialIdeaRetryFlowError(
            f"SocialBrief bulunamadı: {brief_id}",
            error_code="BRIEF_NOT_FOUND",
        )

    # 3. Brief durum doğrulamaları
    if brief.locked_at is None:
        raise SocialIdeaRetryFlowError(
            f"Brief {brief_id} henüz kilitlenmemiş.",
            error_code="BRIEF_NOT_LOCKED",
        )
    if brief.is_stale:
        raise SocialIdeaRetryFlowError(
            f"Brief {brief_id} stale durumda; tekrar deneme başlatılamaz.",
            error_code="BRIEF_STALE",
        )
    if brief.channel_assignment_version != scoring_run.channel_assignment_version:
        raise SocialIdeaRetryFlowError(
            "Brief ve scoring run kanal atama sürüm uyuşmazlığı tespit edildi.",
            error_code="ASSIGNMENT_CHANGED",
        )

    # 4. Süresi dolmuş aktif attempt'leri uzlaştır (kaynak kontrolünden önce!)
    reconcile_expired_attempts(db, brief_id=brief_id, now=current_time)

    # 5. Brief hedefleri ve kanonik hedef sırası
    targets = (
        db.query(SocialBriefTarget)
        .filter(SocialBriefTarget.brief_id == brief_id)
        .order_by(SocialBriefTarget.id.asc())
        .all()
    )
    if not (1 <= len(targets) <= 6):
        raise SocialIdeaRetryFlowError(
            f"Brief {brief_id} için hedef sayısı geçersiz (1-6 arası bekleniyor).",
            error_code="BRIEF_TARGETS_INVALID",
        )

    seen_target_ids: set[int] = set()
    for t in targets:
        if t.id is None or t.id <= 0 or t.id in seen_target_ids:
            raise SocialIdeaRetryFlowError(
                "Geçersiz veya mükerrer target ID.",
                error_code="BRIEF_TARGETS_INVALID",
            )
        seen_target_ids.add(t.id)
        if not t.platform or not t.content_format or get_platform_format(t.platform, t.content_format) is None:
            raise SocialIdeaRetryFlowError(
                "Kanonik format matrisinde bulunmayan hedef tespit edildi.",
                error_code="BRIEF_TARGETS_INVALID",
            )

    canonical_target_ids = tuple(t.id for t in targets)
    target_by_id = {t.id: t for t in targets}

    # 6. Brief anahtar kelimeleri
    keywords_rows = (
        db.query(SocialBriefKeyword)
        .filter(SocialBriefKeyword.brief_id == brief_id)
        .order_by(SocialBriefKeyword.position.asc(), SocialBriefKeyword.id.asc())
        .all()
    )
    if not (1 <= len(keywords_rows) <= 5):
        raise SocialIdeaRetryFlowError(
            f"Brief {brief_id} için anahtar kelime sayısı geçersiz (1-5 arası bekleniyor).",
            error_code="BRIEF_KEYWORDS_INVALID",
        )

    brief_keyword_ids: set[int] = set()
    for expected_pos, kw in enumerate(keywords_rows):
        if (
            kw.keyword_id is None
            or kw.keyword_id <= 0
            or kw.position is None
            or kw.position != expected_pos
            or not kw.keyword_snapshot
            or not kw.keyword_snapshot.strip()
        ):
            raise SocialIdeaRetryFlowError(
                "Geçersiz veya kesintili anahtar kelime snapshot verisi.",
                error_code="BRIEF_KEYWORDS_INVALID",
            )
        brief_keyword_ids.add(kw.keyword_id)

    # 7. Non-stale kategori kontrolü
    non_stale_categories = (
        db.query(SocialCategory)
        .filter(
            SocialCategory.brief_id == brief_id,
            SocialCategory.is_stale.is_(False),
        )
        .all()
    )
    if len(non_stale_categories) == 0:
        raise SocialIdeaRetryFlowError(
            f"Brief {brief_id} altında geçerli (non-stale) kategori bulunamadı.",
            error_code="CATEGORIES_NOT_READY",
        )
    non_stale_category_ids = {c.id for c in non_stale_categories}

    # 8. Kaynak attempt doğrulamaları
    source_attempt = (
        db.query(SocialGenerationAttempt)
        .filter(SocialGenerationAttempt.id == request.source_attempt_id)
        .populate_existing()
        .first()
    )
    if (
        source_attempt is None
        or source_attempt.brief_id != brief_id
        or source_attempt.stage != "ideas"
    ):
        raise SocialIdeaRetryFlowError(
            "Kaynak fikir attempt kaydı bulunamadı veya bu brief'e ait değil.",
            error_code="IDEA_RETRY_SOURCE_NOT_FOUND",
        )

    if source_attempt.status in ("pending", "running"):
        raise SocialIdeaRetryFlowError(
            "Kaynak fikir attempt'i henüz tamamlanmamış (pending/running).",
            error_code="IDEA_RETRY_SOURCE_NOT_TERMINAL",
        )

    if source_attempt.status not in ("completed", "partial", "failed"):
        raise SocialIdeaRetryFlowError(
            "Kaynak fikir attempt durumu retry için uygun değil.",
            error_code="IDEA_RETRY_SOURCE_NOT_TERMINAL",
        )

    cov = source_attempt.coverage
    if (
        not isinstance(cov, dict)
        or cov.get("schema_version") != "ideas_plan_v1"
        or not isinstance(cov.get("request"), dict)
        or not isinstance(cov.get("plan"), dict)
    ):
        raise SocialIdeaRetryFlowError(
            "Kaynak fikir attempt snapshot verisi bozuk veya geçersiz.",
            error_code="IDEA_RETRY_SOURCE_SNAPSHOT_INVALID",
        )

    try:
        extract_social_idea_plan_snapshot(source_attempt)
    except Exception as exc:
        raise SocialIdeaRetryFlowError(
            "Kaynak fikir attempt snapshot ayrıştırması başarısız.",
            error_code="IDEA_RETRY_SOURCE_SNAPSHOT_INVALID",
        ) from exc

    req_block = cov["request"]
    source_cat_ids = req_block.get("category_ids")
    source_ideas_per_cat = req_block.get("ideas_per_category")
    if (
        not isinstance(source_cat_ids, list)
        or not isinstance(source_ideas_per_cat, int)
        or isinstance(source_ideas_per_cat, bool)
    ):
        raise SocialIdeaRetryFlowError(
            "Kaynak fikir attempt snapshot request bloğu geçersiz.",
            error_code="IDEA_RETRY_SOURCE_SNAPSHOT_INVALID",
        )

    if tuple(source_attempt.requested_target_ids or ()) != canonical_target_ids:
        raise SocialIdeaRetryFlowError(
            "Kaynak attempt hedef evreni brief kanonik hedefleriyle uyuşmuyor.",
            error_code="IDEA_RETRY_SOURCE_SNAPSHOT_INVALID",
        )

    try:
        source_plan: SocialIdeaGenerationPlan = build_social_idea_generation_plan(
            selected_category_ids=tuple(source_cat_ids),
            target_ids=canonical_target_ids,
            ideas_per_category=source_ideas_per_cat,
        )
    except Exception as exc:
        raise SocialIdeaRetryFlowError(
            "Kaynak plan yeniden oluşturulamadı.",
            error_code="IDEA_RETRY_SOURCE_SNAPSHOT_INVALID",
        ) from exc

    # 9. Replay kontrolü: Aynı (brief_id, "ideas_retry", idempotency_key) attempt'i ara
    # NOT: Replay durumunda canlı SocialIdea DB satırları coverage tespiti için okunmaz!
    existing_retry_attempt = (
        db.query(SocialGenerationAttempt)
        .filter(
            SocialGenerationAttempt.brief_id == brief_id,
            SocialGenerationAttempt.stage == "ideas_retry",
            SocialGenerationAttempt.idempotency_key == request.idempotency_key,
        )
        .populate_existing()
        .first()
    )

    if existing_retry_attempt is not None:
        try:
            snapshot = extract_social_idea_retry_plan_snapshot(
                existing_retry_attempt,
                source_plan=source_plan,
                expected_canonical_target_ids=canonical_target_ids,
                expected_source_attempt_id=request.source_attempt_id,
            )
        except SocialIdeaRetrySnapshotError as exc:
            raise _map_snapshot_error_to_flow_error(exc) from None

        return SocialIdeaRetryStart(
            brief_id=brief.id,
            scoring_run_id=scoring_run.id,
            brand_profile_id=brand_profile_id,
            source_attempt_id=request.source_attempt_id,
            attempt_id=existing_retry_attempt.id,
            attempt_created=False,
            attempt_status=existing_retry_attempt.status,
            idempotency_key=request.idempotency_key,
            canonical_target_ids=snapshot.canonical_target_ids,
            persisted_target_ids_at_start=snapshot.persisted_target_ids_at_start,
            missing_target_ids=snapshot.missing_target_ids,
            plan=snapshot.plan,
            persisted_category_ids_at_start=snapshot.persisted_category_ids_at_start,
        )

    # 10. Yalnız Yeni Attempt İçin: Otoriter Persisted Target Tespiti (SocialIdea tablosundan)
    existing_ideas = (
        db.query(SocialIdea)
        .filter(
            SocialIdea.brief_id == brief_id,
            SocialIdea.is_stale.is_(False),
        )
        .all()
    )

    persisted_target_ids_set: set[int] = set()
    persisted_category_ids_set: set[int] = set()
    for idea in existing_ideas:
        if idea.brief_target_id is None or idea.brief_target_id not in seen_target_ids:
            raise SocialIdeaRetryFlowError(
                "Mevcut fikir kaydında geçersiz veya kapsam dışı brief_target_id tespit edildi.",
                error_code="IDEA_RETRY_EXISTING_IDEA_INCONSISTENT",
            )
        if idea.category_id is None or idea.category_id not in non_stale_category_ids:
            raise SocialIdeaRetryFlowError(
                "Mevcut fikir kaydında geçersiz veya stale category_id tespit edildi.",
                error_code="IDEA_RETRY_EXISTING_IDEA_INCONSISTENT",
            )
        if idea.keyword_id is None or idea.keyword_id not in brief_keyword_ids:
            raise SocialIdeaRetryFlowError(
                "Mevcut fikir kaydında brief anahtar kelimeleriyle eşleşmeyen keyword_id tespit edildi.",
                error_code="IDEA_RETRY_EXISTING_IDEA_INCONSISTENT",
            )

        t_obj = target_by_id[idea.brief_target_id]
        if (
            idea.target_platform != t_obj.platform
            or idea.content_format != t_obj.content_format
        ):
            raise SocialIdeaRetryFlowError(
                "Mevcut fikir platform/format bilgisi bağlı hedef snapshot'ı ile uyuşmuyor.",
                error_code="IDEA_RETRY_EXISTING_IDEA_INCONSISTENT",
            )

        persisted_target_ids_set.add(idea.brief_target_id)
        persisted_category_ids_set.add(idea.category_id)

    persisted_target_ids_at_start = tuple(
        t_id for t_id in canonical_target_ids if t_id in persisted_target_ids_set
    )
    # K4 kategori kapsaması: kaynak plan kategorilerinden en az bir non-stale fikri olanlar
    # (plan sırası). Fikirler kaynak deneme + önceki retry'lardan gelir; tarihsel partial
    # denemeler de böylece DB durumundan onarılır.
    persisted_category_ids_at_start: tuple[int, ...] | None = tuple(
        c_id for c_id in source_plan.covered_category_ids if c_id in persisted_category_ids_set
    )

    # 11. Yeni attempt açılmadan önce aktif çakışma kontrolleri
    active_ideas = (
        db.query(SocialGenerationAttempt)
        .filter(
            SocialGenerationAttempt.brief_id == brief_id,
            SocialGenerationAttempt.stage == "ideas",
            SocialGenerationAttempt.status.in_(["pending", "running"]),
        )
        .first()
    )
    if active_ideas is not None:
        raise SocialIdeaRetryFlowError(
            "Aktif fikir üretimi devam ederken tekrar deneme başlatılamaz.",
            error_code="ATTEMPT_CONFLICT",
        )

    active_retry = (
        db.query(SocialGenerationAttempt)
        .filter(
            SocialGenerationAttempt.brief_id == brief_id,
            SocialGenerationAttempt.stage == "ideas_retry",
            SocialGenerationAttempt.status.in_(["pending", "running"]),
            SocialGenerationAttempt.idempotency_key != request.idempotency_key,
        )
        .first()
    )
    if active_retry is not None:
        raise SocialIdeaRetryFlowError(
            "Farklı bir idempotency key ile aktif tekrar deneme görevi bulunuyor.",
            error_code="ATTEMPT_CONFLICT",
        )

    # 12. Saf Retry Planlayıcısını Çağır
    try:
        retry_plan = build_social_idea_retry_plan(
            source_plan=source_plan,
            canonical_target_ids=canonical_target_ids,
            persisted_target_ids=persisted_target_ids_at_start,
            persisted_category_ids=persisted_category_ids_at_start,
        )
    except IdeaRetryPlanError as exc:
        if exc.error_code == "IDEA_RETRY_NOT_NEEDED":
            raise SocialIdeaRetryFlowError(
                "Tüm hedefler ve kategoriler dolu; tekrar denemeye ihtiyaç yok.",
                error_code="IDEA_RETRY_NOT_NEEDED",
            ) from exc
        raise SocialIdeaRetryFlowError(
            exc.message,
            error_code=exc.error_code,
        ) from exc

    # 13. Yeni Attempt Oluştur (create_or_get_attempt ile atomik kilit sırası)
    try:
        attempt, attempt_created = create_or_get_attempt(
            db,
            brief_id=brief_id,
            stage="ideas_retry",
            idempotency_key=request.idempotency_key,
            requested_target_ids=list(retry_plan.requested_target_ids),
            now=current_time,
        )
    except AttemptConflictError as exc:
        raise SocialIdeaRetryFlowError(
            "Aktif bir fikir tekrar deneme işlemi mevcut.",
            error_code="ATTEMPT_CONFLICT",
        ) from exc

    if attempt_created:
        attempt.coverage = _build_retry_coverage_snapshot(
            request=request,
            canonical_target_ids=canonical_target_ids,
            persisted_target_ids_at_start=persisted_target_ids_at_start,
            persisted_category_ids_at_start=persisted_category_ids_at_start or (),
            retry_plan=retry_plan,
        )
        attempt.warnings = []
        db.flush()
    else:
        # Eşzamanlı yarışta mevcut attempt'e düşülürse replay doğrulaması yap
        try:
            snapshot = extract_social_idea_retry_plan_snapshot(
                attempt,
                source_plan=source_plan,
                expected_canonical_target_ids=canonical_target_ids,
                expected_source_attempt_id=request.source_attempt_id,
            )
        except SocialIdeaRetrySnapshotError as exc:
            raise _map_snapshot_error_to_flow_error(exc) from None

        canonical_target_ids = snapshot.canonical_target_ids
        persisted_target_ids_at_start = snapshot.persisted_target_ids_at_start
        persisted_category_ids_at_start = snapshot.persisted_category_ids_at_start
        retry_plan = snapshot.plan

    return SocialIdeaRetryStart(
        brief_id=brief.id,
        scoring_run_id=scoring_run.id,
        brand_profile_id=brand_profile_id,
        source_attempt_id=request.source_attempt_id,
        attempt_id=attempt.id,
        attempt_created=attempt_created,
        attempt_status=attempt.status,
        idempotency_key=request.idempotency_key,
        canonical_target_ids=canonical_target_ids,
        persisted_target_ids_at_start=persisted_target_ids_at_start,
        missing_target_ids=retry_plan.missing_target_ids,
        plan=retry_plan,
        persisted_category_ids_at_start=persisted_category_ids_at_start,
    )
