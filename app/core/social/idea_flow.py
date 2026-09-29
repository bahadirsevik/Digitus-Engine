# -*- coding: utf-8 -*-
"""Social Brief Fikir Üretimi Preflight, Attempt ve Plan Snapshot (F1-F.4).

Bu modül async fikir AI üretimine başlamadan önce:
- İstek girdisini strict doğrular,
- Workspace, brief, kategori ve target uygunluk kontrollerini yapar,
- Deterministik fikir üretim planını (F1-F.1) hesaplar,
- 'ideas' aşaması SocialGenerationAttempt kaydını idempotent biçimde açar,
- Başlangıç plan snapshot'ını attempt.coverage alanında saklar.

Kurallar:
- Transaction açmaz (db.begin() çağırmaz).
- commit veya rollback çağırmaz.
- AI çağrısı yapmaz.
- SocialIdea satırı oluşturmaz.
- Global kilit sırası (BrandProfile -> ScoringRun -> SocialBrief -> SocialGenerationAttempt)
  kesin olarak korunur; Attempt-first FOR UPDATE yapılamaz.
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
)
from app.generators.social.format_matrix import get_platform_format
from app.core.social.idea_planner import (
    IdeaCategoryPlan,
    IdeaPlanValidationError,
    IdeaTargetQuota,
    SocialIdeaGenerationPlan,
    build_social_idea_generation_plan,
)
from app.schemas.social_brief import SocialBriefIdeasGenerateRequest


class SocialIdeaFlowError(ValueError):
    """Fikir üretim akışı preflight domain hatası."""

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
            f"SocialIdeaFlowError(error_code={self.error_code!r}, "
            f"message={self.message!r}, field={self.field!r})"
        )


@dataclass(frozen=True)
class SocialIdeaCategorySnapshot:
    """Fikir üretimine seçilen kategori snapshot'ı (immutable)."""

    category_id: int
    category_name: str
    category_description: str


@dataclass(frozen=True)
class SocialIdeaGenerationStart:
    """Fikir üretimi preflight, attempt ve plan başlatma sonucu (immutable)."""

    brief_id: int
    scoring_run_id: int
    brand_profile_id: int
    attempt_id: int
    attempt_created: bool
    attempt_status: str
    idempotency_key: str
    selected_category_ids: tuple[int, ...]
    requested_target_ids: tuple[int, ...]
    ideas_per_category: int
    plan: SocialIdeaGenerationPlan
    categories: tuple[SocialIdeaCategorySnapshot, ...]


def _validate_now(now: datetime | None) -> datetime:
    """Timezone-aware UTC zamanını doğrular veya üretir."""
    if now is not None:
        if now.tzinfo is None:
            raise ValueError(
                "Enjekte edilen 'now' parametresi timezone-aware olmalıdır (tzinfo is None)."
            )
        return now.astimezone(timezone.utc)
    return datetime.now(timezone.utc)


def _build_plan_coverage_snapshot(
    request: SocialBriefIdeasGenerateRequest,
    plan: SocialIdeaGenerationPlan,
) -> dict[str, Any]:
    """Plan verisini kanonik coverage JSON yapısına dönüştürür."""
    categories_list = []
    for cp in plan.category_plans:
        targets_list = []
        for tq in cp.target_quotas:
            if tq.requested_count > 0:
                targets_list.append({
                    "target_id": tq.target_id,
                    "requested_count": tq.requested_count,
                })
        categories_list.append({
            "category_id": cp.category_id,
            "requested_count": cp.requested_count,
            "targets": targets_list,
        })

    return {
        "schema_version": "ideas_plan_v1",
        "request": {
            "category_ids": list(request.category_ids),
            "ideas_per_category": request.ideas_per_category,
        },
        "plan": {
            "total_requested": plan.total_requested,
            "categories": categories_list,
        },
        "generated": {
            "total_accepted": 0,
            "target_ids": [],
        },
    }


def _reconstruct_plan_from_coverage(cov_plan: Any) -> SocialIdeaGenerationPlan:
    """Coverage JSON içindeki planı SocialIdeaGenerationPlan DTO'suna güvenli dönüştürür."""
    if not isinstance(cov_plan, dict):
        raise ValueError("Coverage plan must be a dict")

    total_requested = cov_plan.get("total_requested")
    if not isinstance(total_requested, int) or total_requested <= 0:
        raise ValueError("Invalid total_requested in coverage plan")

    categories = cov_plan.get("categories")
    if not isinstance(categories, list) or len(categories) == 0:
        raise ValueError("Invalid categories in coverage plan")

    cat_plans = []
    covered_cat_ids = []
    covered_target_ids = []

    for c in categories:
        if not isinstance(c, dict):
            raise ValueError("Category plan item must be dict")
        cid = c.get("category_id")
        rc = c.get("requested_count")
        targets = c.get("targets")
        if (
            isinstance(cid, bool)
            or type(cid) is not int
            or cid <= 0
            or isinstance(rc, bool)
            or type(rc) is not int
            or rc <= 0
            or not isinstance(targets, list)
        ):
            raise ValueError("Invalid category plan fields")
        covered_cat_ids.append(cid)

        t_quotas = []
        for t in targets:
            if not isinstance(t, dict):
                raise ValueError("Target item must be dict")
            tid = t.get("target_id")
            trc = t.get("requested_count")
            if (
                isinstance(tid, bool)
                or type(tid) is not int
                or tid <= 0
                or isinstance(trc, bool)
                or type(trc) is not int
                or trc <= 0
            ):
                raise ValueError("Invalid target quota fields")
            t_quotas.append(IdeaTargetQuota(target_id=tid, requested_count=trc))
            if tid not in covered_target_ids:
                covered_target_ids.append(tid)

        cat_plans.append(
            IdeaCategoryPlan(
                category_id=cid,
                requested_count=rc,
                target_quotas=tuple(t_quotas),
            )
        )

    return SocialIdeaGenerationPlan(
        total_requested=total_requested,
        category_plans=tuple(cat_plans),
        covered_category_ids=tuple(covered_cat_ids),
        covered_target_ids=tuple(covered_target_ids),
    )


def begin_social_idea_generation(
    db: Session,
    *,
    brief_id: int,
    brand_profile_id: int,
    request: SocialBriefIdeasGenerateRequest,
    now: datetime | None = None,
) -> SocialIdeaGenerationStart:
    """Fikir üretimine başlamadan önce brief'i doğrular, planı çıkarır ve ideas attempt'ini başlatır.

    Args:
        db: Aktif SQLAlchemy oturumu (commit/rollback çağrılmaz).
        brief_id: Hedef SocialBrief ID'si.
        brand_profile_id: Workspace (BrandProfile) ID'si.
        request: Doğrulanmış fikir üretimi istek nesnesi.
        now: Opsiyonel test zaman enjeksiyonu (timezone-aware).

    Returns:
        SocialIdeaGenerationStart: Başlatılan veya replay edilen attempt ve plan verisi.

    Raises:
        SocialIdeaFlowError: Uygunluk, durum, plan veya doğrulama hatalarında.
        AttemptConflictError: Farklı key ile aktif bir attempt zaten varsa.
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
        raise SocialIdeaFlowError(
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
        raise SocialIdeaFlowError(
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
        raise SocialIdeaFlowError(
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
        raise SocialIdeaFlowError(
            f"SocialBrief bulunamadı: {brief_id}",
            error_code="BRIEF_NOT_FOUND",
        )

    # 3. Brief durum doğrulamaları
    if brief.locked_at is None:
        raise SocialIdeaFlowError(
            f"Brief {brief_id} henüz kilitlenmemiş (kategori üretimi başlamamış).",
            error_code="BRIEF_NOT_LOCKED",
        )
    if brief.is_stale:
        raise SocialIdeaFlowError(
            f"Brief {brief_id} stale durumda; yeni fikir üretimi başlatılamaz.",
            error_code="BRIEF_STALE",
        )
    if brief.channel_assignment_version != scoring_run.channel_assignment_version:
        raise SocialIdeaFlowError(
            f"Assignment version uyuşmazlığı: brief_version={brief.channel_assignment_version}, "
            f"run_version={scoring_run.channel_assignment_version}; yeni fikir üretimi başlatılamaz.",
            error_code="ASSIGNMENT_CHANGED",
        )

    # 4. Kategori aşamasının tamamlanmış olma kontrolü
    cat_attempt = (
        db.query(SocialGenerationAttempt)
        .filter(
            SocialGenerationAttempt.brief_id == brief_id,
            SocialGenerationAttempt.stage == "categories",
        )
        .order_by(SocialGenerationAttempt.id.desc())
        .first()
    )
    if cat_attempt is None or cat_attempt.status != "completed":
        raise SocialIdeaFlowError(
            f"Brief {brief_id} için kategori üretimi henüz tamamlanmamış.",
            error_code="CATEGORIES_NOT_READY",
        )

    non_stale_categories_count = (
        db.query(SocialCategory.id)
        .filter(
            SocialCategory.brief_id == brief_id,
            SocialCategory.is_stale.is_(False),
        )
        .count()
    )
    if non_stale_categories_count == 0:
        raise SocialIdeaFlowError(
            f"Brief {brief_id} altında geçerli (non-stale) kategori bulunamadı.",
            error_code="CATEGORIES_NOT_READY",
        )

    # 5. Brief altındaki hedef (target) doğrulaması
    targets = (
        db.query(SocialBriefTarget)
        .filter(SocialBriefTarget.brief_id == brief_id)
        .order_by(SocialBriefTarget.id.asc())
        .all()
    )
    if not (1 <= len(targets) <= 6):
        raise SocialIdeaFlowError(
            f"Brief {brief_id} için hedef sayısı geçersiz: {len(targets)} (beklenen: 1-6).",
            error_code="IDEA_TARGETS_INVALID",
        )

    seen_target_ids: set[int] = set()
    for t in targets:
        if t.id is None or t.id <= 0 or t.id in seen_target_ids:
            raise SocialIdeaFlowError(
                "Geçersiz veya mükerrer target ID.",
                error_code="IDEA_TARGETS_INVALID",
            )
        seen_target_ids.add(t.id)
        if not t.platform or not t.content_format or get_platform_format(t.platform, t.content_format) is None:
            raise SocialIdeaFlowError(
                "Kanonik format matrisinde bulunmayan hedef tespit edildi.",
                error_code="IDEA_TARGETS_INVALID",
            )

    ordered_target_ids = tuple(t.id for t in targets)

    # 6. Brief altındaki anahtar kelime doğrulaması
    keywords_rows = (
        db.query(SocialBriefKeyword)
        .filter(SocialBriefKeyword.brief_id == brief_id)
        .order_by(SocialBriefKeyword.position.asc(), SocialBriefKeyword.id.asc())
        .all()
    )
    if not (1 <= len(keywords_rows) <= 5):
        raise SocialIdeaFlowError(
            f"Brief {brief_id} için anahtar kelime sayısı geçersiz: {len(keywords_rows)} (beklenen: 1-5).",
            error_code="IDEA_KEYWORDS_INVALID",
        )

    for expected_pos, kw in enumerate(keywords_rows):
        if (
            kw.keyword_id is None
            or kw.keyword_id <= 0
            or kw.position is None
            or kw.position != expected_pos
            or not kw.keyword_snapshot
            or not kw.keyword_snapshot.strip()
        ):
            raise SocialIdeaFlowError(
                "Geçersiz veya kesintili anahtar kelime snapshot verisi.",
                error_code="IDEA_KEYWORDS_INVALID",
            )

    # 7. Seçilen kategorilerin brief ve durum uygunluğu
    selected_cats_rows = (
        db.query(SocialCategory)
        .filter(SocialCategory.id.in_(request.category_ids))
        .all()
    )
    cats_by_id = {c.id: c for c in selected_cats_rows}

    for cid in request.category_ids:
        if cid not in cats_by_id:
            other = (
                db.query(SocialCategory.id, SocialCategory.brief_id)
                .filter(SocialCategory.id == cid)
                .first()
            )
            if other is not None and other.brief_id != brief_id:
                raise SocialIdeaFlowError(
                    "Seçilen kategori başka bir brief'e ait.",
                    error_code="MIXED_BRIEF",
                    field="category_ids",
                )
            raise SocialIdeaFlowError(
                f"Kategori bulunamadı: {cid}",
                error_code="CATEGORY_NOT_ELIGIBLE",
                field="category_ids",
            )

        cat = cats_by_id[cid]
        if cat.brief_id != brief_id or cat.scoring_run_id != scoring_run.id:
            raise SocialIdeaFlowError(
                "Seçilen kategori başka bir brief veya run'a ait.",
                error_code="MIXED_BRIEF",
                field="category_ids",
            )
        if cat.is_stale:
            raise SocialIdeaFlowError(
                f"Kategori {cid} stale durumda; kullanılamaz.",
                error_code="CATEGORY_NOT_ELIGIBLE",
                field="category_ids",
            )

        cn = cat.category_name
        if not isinstance(cn, str) or not cn.strip() or len(cn) > 100 or cn != cn.strip():
            raise SocialIdeaFlowError(
                "Kategori adı geçersiz veya sınır aşan uzunlukta.",
                error_code="CATEGORY_NOT_ELIGIBLE",
                field="category_ids",
            )

        cd = cat.description
        if not isinstance(cd, str) or not cd.strip() or len(cd) > 2000 or cd != cd.strip():
            raise SocialIdeaFlowError(
                "Kategori açıklaması geçersiz veya sınır aşan uzunlukta.",
                error_code="CATEGORY_NOT_ELIGIBLE",
                field="category_ids",
            )

    category_snapshots = tuple(
        SocialIdeaCategorySnapshot(
            category_id=cid,
            category_name=cats_by_id[cid].category_name,
            category_description=cats_by_id[cid].description,
        )
        for cid in request.category_ids
    )

    # 8. Deterministik plan oluşturma
    try:
        plan = build_social_idea_generation_plan(
            selected_category_ids=tuple(request.category_ids),
            target_ids=ordered_target_ids,
            ideas_per_category=request.ideas_per_category,
        )
    except IdeaPlanValidationError as exc:
        raise SocialIdeaFlowError(
            exc.message,
            error_code="IDEA_PLAN_INVALID",
            field=exc.field,
        ) from exc

    # 9. Önceden üretilmiş fikirler kontrolü (yeni attempt'ler için)
    existing_attempt = (
        db.query(SocialGenerationAttempt)
        .filter(
            SocialGenerationAttempt.brief_id == brief_id,
            SocialGenerationAttempt.stage == "ideas",
            SocialGenerationAttempt.idempotency_key == request.idempotency_key,
        )
        .first()
    )
    if existing_attempt is None:
        has_existing_ideas = (
            db.query(SocialIdea.id)
            .filter(
                SocialIdea.brief_id == brief_id,
                SocialIdea.is_stale.is_(False),
            )
            .first()
            is not None
        )
        if has_existing_ideas:
            raise SocialIdeaFlowError(
                f"Brief {brief_id} için fikirler zaten üretilmiş.",
                error_code="IDEAS_ALREADY_GENERATED",
            )

    # 10. Attempt elde etme / oluşturma (canonical kilit sırasıyla)
    attempt, attempt_created = create_or_get_attempt(
        db,
        brief_id=brief_id,
        stage="ideas",
        idempotency_key=request.idempotency_key,
        requested_target_ids=list(plan.covered_target_ids),
        now=current_time,
    )

    # 11. Plan snapshot kalıcılığı veya Replay doğrulaması
    if attempt_created:
        attempt.coverage = _build_plan_coverage_snapshot(request, plan)
        attempt.warnings = []
        db.flush()
    else:
        # Same-key replay: coverage snapshot doğrulama
        cov = attempt.coverage
        if (
            not isinstance(cov, dict)
            or cov.get("schema_version") != "ideas_plan_v1"
            or not isinstance(cov.get("request"), dict)
            or not isinstance(cov.get("plan"), dict)
        ):
            raise SocialIdeaFlowError(
                "Attempt coverage snapshot'ı bozuk veya eksik.",
                error_code="IDEA_ATTEMPT_SNAPSHOT_INVALID",
            )

        cov_req = cov["request"]
        if (
            cov_req.get("category_ids") != list(request.category_ids)
            or cov_req.get("ideas_per_category") != request.ideas_per_category
        ):
            raise SocialIdeaFlowError(
                "Replay request parametreleri mevcut attempt ile eşleşmiyor.",
                error_code="IDEA_ATTEMPT_REQUEST_MISMATCH",
            )

        try:
            reconstructed_plan = _reconstruct_plan_from_coverage(cov["plan"])
        except Exception as exc:
            raise SocialIdeaFlowError(
                "Snapshot plan verisi geçersiz veya bozuk.",
                error_code="IDEA_ATTEMPT_SNAPSHOT_INVALID",
            ) from exc

        if reconstructed_plan != plan:
            raise SocialIdeaFlowError(
                "Snapshot planı deterministik plan ile eşleşmiyor.",
                error_code="IDEA_ATTEMPT_SNAPSHOT_INVALID",
            )

    return SocialIdeaGenerationStart(
        brief_id=brief.id,
        scoring_run_id=scoring_run.id,
        brand_profile_id=brand_profile_id,
        attempt_id=attempt.id,
        attempt_created=attempt_created,
        attempt_status=attempt.status,
        idempotency_key=request.idempotency_key,
        selected_category_ids=tuple(request.category_ids),
        requested_target_ids=plan.covered_target_ids,
        ideas_per_category=request.ideas_per_category,
        plan=plan,
        categories=category_snapshots,
    )
