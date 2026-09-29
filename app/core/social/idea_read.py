# -*- coding: utf-8 -*-
"""Salt-Okunur Fikir Sonuç ve Coverage Servisi (F1-F.6d.1).

Bu modül SocialGenerationAttempt fikir üretim sonucunu workspace güvenliğiyle okur,
fail-closed kurallarla doğrular, deterministik domain DTO'larına dönüştürür.

Kurallar:
- Salt-okunurdur: commit, rollback, flush, db.begin çağırmaz.
- with_for_update() kullanmaz.
- ORM modellerini mutate etmez.
- AI veya dış ağ çağrısı yapmaz.
- Workspace izolasyonunu sıkı korur (cross-workspace bilgi sızdırmaz).
- Stale kayıtları (brief/category/idea) tarihsel olarak okuyabilir.
- Format matrisini yeniden doğrulamaz; brief target eşleşmesi yeterlidir.
- Deterministik sıralama garantisi sunar.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.social.idea_persistence import extract_social_idea_plan_snapshot
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


class SocialIdeaReadError(ValueError):
    """Sosyal fikir okuma domain hatası."""

    def __init__(
        self,
        message: str,
        *,
        error_code: str,
        field: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.field = field

    def __repr__(self) -> str:
        return (
            f"SocialIdeaReadError(error_code={self.error_code!r}, "
            f"message={self.message!r}, field={self.field!r})"
        )


class SocialIdeaReadNotFoundError(SocialIdeaReadError):
    """Brief veya attempt bulunamadığında (workspace izolasyonu dahil) fırlatılır."""

    def __init__(
        self,
        message: str = "Sosyal brief veya attempt bulunamadı.",
        *,
        field: str | None = None,
    ) -> None:
        super().__init__(message, error_code="IDEA_READ_NOT_FOUND", field=field)


@dataclass(frozen=True)
class SocialIdeaCoverage:
    """Target bazında fikir üretim kapsama durumu (immutable)."""

    target_id: int
    requested: int
    accepted: int
    missing: int


@dataclass(frozen=True)
class SocialIdeaWarning:
    """Fikir üretim denemesine ait uyarı (immutable)."""

    target_id: int
    category_id: Optional[int]
    reason_code: str


@dataclass(frozen=True)
class SocialIdeaReadItem:
    """Salt-okunur fikir satırı DTO'su (immutable)."""

    id: int
    category_id: int
    keyword_id: int
    brief_id: int
    brief_target_id: int
    idea_title: str
    idea_description: str
    target_platform: str
    content_format: str
    trend_alignment: float
    is_stale: bool


@dataclass(frozen=True)
class SocialIdeaReadResult:
    """Fikir okuma nihai sonucu (immutable)."""

    brief_id: int
    scoring_run_id: int
    attempt_id: int
    attempt_status: str
    total_ideas: int
    ideas: tuple[SocialIdeaReadItem, ...]
    coverage: tuple[SocialIdeaCoverage, ...]
    warnings: tuple[SocialIdeaWarning, ...]
    reason_code: Optional[str]
    replayed: bool


def _validate_positive_int(value: Any, name: str) -> int:
    """Pozitif tamsayı değerini doğrular (bool reddedilir)."""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise SocialIdeaReadError(
            f"{name} pozitif integer olmalıdır.",
            error_code="IDEA_READ_INVALID_INPUT",
            field=name,
        )
    return value


def load_social_idea_result(
    db: Session,
    *,
    brief_id: int,
    attempt_id: int,
    brand_profile_id: int,
    replayed: bool = False,
) -> SocialIdeaReadResult:
    """Sosyal brief fikir attempt sonucunu workspace güvenliğiyle okur ve doğrular.

    Sözleşme:
    - db üzerinde hiçbir yazma işlemi (commit, rollback, flush) yapılmaz.
    - FOR UPDATE kullanılmaz.
    - BrandProfile, ScoringRun, SocialBrief ve SocialGenerationAttempt zinciri doğrulanır.
    - Bulunamama veya cross-workspace durumlarında SocialIdeaReadNotFoundError fırlatılır.
    - Snapshot ve coverage fail-closed doğrulanır.
    """
    _validate_positive_int(brief_id, "brief_id")
    _validate_positive_int(attempt_id, "attempt_id")
    _validate_positive_int(brand_profile_id, "brand_profile_id")

    if not isinstance(replayed, bool):
        raise SocialIdeaReadError(
            "replayed bool tipinde olmalıdır.",
            error_code="IDEA_READ_INVALID_INPUT",
            field="replayed",
        )

    # 1. Workspace izolasyonlu brief sorgusu
    stmt = (
        select(SocialBrief)
        .join(ScoringRun, SocialBrief.scoring_run_id == ScoringRun.id)
        .join(BrandProfile, ScoringRun.brand_profile_id == BrandProfile.id)
        .where(
            SocialBrief.id == brief_id,
            ScoringRun.brand_profile_id == brand_profile_id,
            BrandProfile.id == brand_profile_id,
            BrandProfile.deleted_at.is_(None),
        )
    )
    brief = db.scalars(stmt).first()
    if brief is None:
        raise SocialIdeaReadNotFoundError("Sosyal brief bulunamadı.")

    # 2. Attempt sorgusu ve doğrulaması
    attempt = (
        db.query(SocialGenerationAttempt)
        .filter(
            SocialGenerationAttempt.id == attempt_id,
            SocialGenerationAttempt.brief_id == brief.id,
        )
        .first()
    )
    if attempt is None:
        raise SocialIdeaReadNotFoundError("Fikir attempt bulunamadı.")

    if attempt.stage != "ideas":
        raise SocialIdeaReadError(
            "Attempt aşaması 'ideas' olmalıdır.",
            error_code="IDEA_READ_INCONSISTENT",
            field="stage",
        )

    allowed_statuses = {"pending", "running", "completed", "failed", "partial"}
    if attempt.status not in allowed_statuses:
        raise SocialIdeaReadError(
            "Bilinmeyen attempt status.",
            error_code="IDEA_READ_INCONSISTENT",
            field="status",
        )

    # 3. Plan snapshot çıkarımı (fail-closed, güvenli hata mesajı)
    try:
        plan = extract_social_idea_plan_snapshot(attempt)
    except Exception:
        raise SocialIdeaReadError(
            "Fikir üretim planı snapshot'ı tutarsız veya geçersiz.",
            error_code="IDEA_READ_INCONSISTENT",
        )

    if not attempt.requested_target_ids:
        raise SocialIdeaReadError(
            "Attempt requested_target_ids boş olamaz.",
            error_code="IDEA_READ_INCONSISTENT",
        )

    # 4. Brief keyword ve target'larını doğrula
    db_keywords = (
        db.query(SocialBriefKeyword)
        .filter(SocialBriefKeyword.brief_id == brief.id)
        .all()
    )
    brief_keyword_ids = {k.keyword_id for k in db_keywords}

    db_targets = (
        db.query(SocialBriefTarget)
        .filter(SocialBriefTarget.brief_id == brief.id)
        .all()
    )
    brief_target_map: dict[int, SocialBriefTarget] = {t.id: t for t in db_targets}

    # Plan kategorilerini yükle ve doğrula
    plan_cat_ids = [cat.category_id for cat in plan.categories]
    db_categories = (
        db.query(SocialCategory)
        .filter(SocialCategory.id.in_(plan_cat_ids))
        .all()
    )
    category_map: dict[int, SocialCategory] = {c.id: c for c in db_categories}
    for cid in plan_cat_ids:
        cat = category_map.get(cid)
        if cat is None or cat.brief_id != brief.id or cat.scoring_run_id != brief.scoring_run_id:
            raise SocialIdeaReadError(
                "Plan kategorisi brief veya scoring run ile tutarsız.",
                error_code="IDEA_READ_INCONSISTENT",
            )

    # Plan kotaları haritası: (category_id, target_id) -> quota
    plan_quotas: dict[tuple[int, int], int] = {}
    plan_cat_map = {cat.category_id: cat for cat in plan.categories}
    for cat in plan.categories:
        for tid, quota in cat.target_quotas:
            plan_quotas[(cat.category_id, tid)] = quota

    # 5. DB'deki mevcut fikirleri çek
    existing_ideas = (
        db.query(SocialIdea)
        .filter(SocialIdea.brief_id == brief.id)
        .all()
    )

    # 6. Status bazlı kontroller
    if attempt.status in ("pending", "running"):
        if len(existing_ideas) > 0:
            raise SocialIdeaReadError(
                "Pending veya running durumda brief için fikir satırı bulunamaz.",
                error_code="IDEA_READ_INCONSISTENT",
            )
        # Coverage: accepted=0, missing=requested
        coverage_items: list[SocialIdeaCoverage] = []
        for tid in attempt.requested_target_ids:
            req_cnt = sum(
                quota for (cid, target_id), quota in plan_quotas.items() if target_id == tid
            )
            coverage_items.append(
                SocialIdeaCoverage(
                    target_id=tid,
                    requested=req_cnt,
                    accepted=0,
                    missing=req_cnt,
                )
            )
        warnings_tuple = _validate_and_sort_warnings(attempt, plan_cat_ids)
        return SocialIdeaReadResult(
            brief_id=brief.id,
            scoring_run_id=brief.scoring_run_id,
            attempt_id=attempt.id,
            attempt_status=attempt.status,
            total_ideas=0,
            ideas=(),
            coverage=tuple(coverage_items),
            warnings=warnings_tuple,
            reason_code=attempt.reason_code,
            replayed=replayed,
        )

    if attempt.status == "failed":
        if len(existing_ideas) > 0:
            raise SocialIdeaReadError(
                "Failed durumda brief için fikir satırı bulunamaz.",
                error_code="IDEA_READ_INCONSISTENT",
            )
        coverage_items = []
        for tid in attempt.requested_target_ids:
            req_cnt = sum(
                quota for (cid, target_id), quota in plan_quotas.items() if target_id == tid
            )
            coverage_items.append(
                SocialIdeaCoverage(
                    target_id=tid,
                    requested=req_cnt,
                    accepted=0,
                    missing=req_cnt,
                )
            )
        warnings_tuple = _validate_and_sort_warnings(attempt, plan_cat_ids)
        return SocialIdeaReadResult(
            brief_id=brief.id,
            scoring_run_id=brief.scoring_run_id,
            attempt_id=attempt.id,
            attempt_status=attempt.status,
            total_ideas=0,
            ideas=(),
            coverage=tuple(coverage_items),
            warnings=warnings_tuple,
            reason_code=attempt.reason_code,
            replayed=replayed,
        )

    # completed veya partial durumu:
    validated_items: list[SocialIdeaReadItem] = []
    actual_quotas: dict[tuple[int, int], int] = {}

    for idea in existing_ideas:
        # brief_id kontrolü
        if idea.brief_id != brief.id:
            raise SocialIdeaReadError(
                "Fikir brief_id brief ile eşleşmiyor.",
                error_code="IDEA_READ_INCONSISTENT",
            )

        # category kontrolü
        if idea.category_id not in category_map:
            raise SocialIdeaReadError(
                "Fikir plan dışı kategoriye ait.",
                error_code="IDEA_READ_INCONSISTENT",
            )
        cat = category_map[idea.category_id]
        if cat.brief_id != brief.id or cat.scoring_run_id != brief.scoring_run_id:
            raise SocialIdeaReadError(
                "Fikir kategorisi brief veya scoring run ile uyuşmuyor.",
                error_code="IDEA_READ_INCONSISTENT",
            )

        # target kontrolü
        if idea.brief_target_id not in attempt.requested_target_ids:
            raise SocialIdeaReadError(
                "Fikir target_id attempt requested_targets içinde bulunamadı.",
                error_code="IDEA_READ_INCONSISTENT",
            )
        tgt = brief_target_map.get(idea.brief_target_id)
        if tgt is None or tgt.brief_id != brief.id:
            raise SocialIdeaReadError(
                "Fikir target_id brief target'ları ile uyuşmuyor.",
                error_code="IDEA_READ_INCONSISTENT",
            )

        # platform/format kontrolü
        if idea.target_platform != tgt.platform or idea.content_format != tgt.content_format:
            raise SocialIdeaReadError(
                "Fikir platform veya formatı bağlı hedef ile uyuşmuyor.",
                error_code="IDEA_READ_INCONSISTENT",
            )

        # keyword kontrolü
        if idea.keyword_id not in brief_keyword_ids:
            raise SocialIdeaReadError(
                "Fikir keyword_id brief keyword'leri arasında değil.",
                error_code="IDEA_READ_INCONSISTENT",
            )

        # title kontrolü
        if (
            not isinstance(idea.idea_title, str)
            or len(idea.idea_title) == 0
            or len(idea.idea_title) > 200
            or idea.idea_title != idea.idea_title.strip()
        ):
            raise SocialIdeaReadError(
                "Fikir başlığı geçersiz veya sınırlara uymuyor.",
                error_code="IDEA_READ_INCONSISTENT",
            )

        # description kontrolü
        if (
            not isinstance(idea.idea_description, str)
            or len(idea.idea_description) == 0
            or len(idea.idea_description) > 2000
            or idea.idea_description != idea.idea_description.strip()
        ):
            raise SocialIdeaReadError(
                "Fikir açıklaması geçersiz veya sınırlara uymuyor.",
                error_code="IDEA_READ_INCONSISTENT",
            )

        # trend_alignment kontrolü
        if isinstance(idea.trend_alignment, bool) or not isinstance(idea.trend_alignment, (int, float)):
            raise SocialIdeaReadError(
                "trend_alignment sayısal bir değer olmalıdır.",
                error_code="IDEA_READ_INCONSISTENT",
            )
        try:
            fval = float(idea.trend_alignment)
        except (OverflowError, ValueError, TypeError):
            raise SocialIdeaReadError(
                "trend_alignment sayıya dönüştürülemedi.",
                error_code="IDEA_READ_INCONSISTENT",
            )
        if not math.isfinite(fval) or fval < 0.0 or fval > 1.0:
            raise SocialIdeaReadError(
                "trend_alignment 0.0 ile 1.0 arasında sonlu olmalıdır.",
                error_code="IDEA_READ_INCONSISTENT",
            )

        # Kategori-target plan kotası kontrolü
        pair = (idea.category_id, idea.brief_target_id)
        if pair not in plan_quotas:
            raise SocialIdeaReadError(
                "Plan dışı kategori-target çifti için fikir bulundu.",
                error_code="IDEA_READ_INCONSISTENT",
            )
        actual_quotas[pair] = actual_quotas.get(pair, 0) + 1
        if actual_quotas[pair] > plan_quotas[pair]:
            raise SocialIdeaReadError(
                "Kategori-target kotası aşıldı.",
                error_code="IDEA_READ_INCONSISTENT",
            )

        validated_items.append(
            SocialIdeaReadItem(
                id=idea.id,
                category_id=idea.category_id,
                keyword_id=idea.keyword_id,
                brief_id=idea.brief_id,
                brief_target_id=idea.brief_target_id,
                idea_title=idea.idea_title,
                idea_description=idea.idea_description,
                target_platform=idea.target_platform,
                content_format=idea.content_format,
                trend_alignment=fval,
                is_stale=bool(idea.is_stale),
            )
        )

    if attempt.status == "completed":
        # Completed = K4 sağlandı: her hedef >= 1 ve her plan kategorisi >= 1 fikir.
        # Kategori x hedef kotası garanti değildir (atılan fikir kota altı bırakabilir).
        for tid in attempt.requested_target_ids:
            if not any(t == tid and cnt > 0 for (_, t), cnt in actual_quotas.items()):
                raise SocialIdeaReadError(
                    "Completed durumda her hedef en az bir fikir almalıdır.",
                    error_code="IDEA_READ_INCONSISTENT",
                )
        for cid in plan_cat_ids:
            if not any(c == cid and cnt > 0 for (c, _), cnt in actual_quotas.items()):
                raise SocialIdeaReadError(
                    "Completed durumda her kategori en az bir fikir almalıdır.",
                    error_code="IDEA_READ_INCONSISTENT",
                )
    elif len(existing_ideas) == 0:
        raise SocialIdeaReadError(
            "Partial durumda en az bir fikir bulunmalıdır.",
            error_code="IDEA_READ_INCONSISTENT",
        )

    # Coverage hesabı
    coverage_items = []
    for tid in attempt.requested_target_ids:
        req_cnt = sum(
            quota for (cid, target_id), quota in plan_quotas.items() if target_id == tid
        )
        acc_cnt = sum(1 for item in validated_items if item.brief_target_id == tid)
        missing_cnt = req_cnt - acc_cnt
        if missing_cnt < 0:
            raise SocialIdeaReadError(
                "Coverage accepted değeri requested değerini aşıyor.",
                error_code="IDEA_READ_INCONSISTENT",
            )
        coverage_items.append(
            SocialIdeaCoverage(
                target_id=tid,
                requested=req_cnt,
                accepted=acc_cnt,
                missing=missing_cnt,
            )
        )

    # Deterministik Ideas sıralaması:
    # 1. Plan kategori sırası
    # 2. Kategori içindeki target plan sırası
    # 3. idea.id
    cat_order = {cid: idx for idx, cid in enumerate(plan_cat_ids)}
    target_order_in_cat: dict[tuple[int, int], int] = {}
    for cat in plan.categories:
        for idx, (tid, _) in enumerate(cat.target_quotas):
            target_order_in_cat[(cat.category_id, tid)] = idx

    def _idea_sort_key(item: SocialIdeaReadItem) -> tuple[int, int, int]:
        c_idx = cat_order.get(item.category_id, 999)
        t_idx = target_order_in_cat.get((item.category_id, item.brief_target_id), 999)
        return (c_idx, t_idx, item.id)

    validated_items.sort(key=_idea_sort_key)

    warnings_tuple = _validate_and_sort_warnings(attempt, plan_cat_ids)

    return SocialIdeaReadResult(
        brief_id=brief.id,
        scoring_run_id=brief.scoring_run_id,
        attempt_id=attempt.id,
        attempt_status=attempt.status,
        total_ideas=len(validated_items),
        ideas=tuple(validated_items),
        coverage=tuple(coverage_items),
        warnings=warnings_tuple,
        reason_code=attempt.reason_code,
        replayed=replayed,
    )


def _validate_and_sort_warnings(
    attempt: SocialGenerationAttempt,
    plan_cat_ids: list[int],
) -> tuple[SocialIdeaWarning, ...]:
    """Attempt.warnings verisini strict olarak doğrular ve deterministik sıralar."""
    if attempt.warnings is None:
        return ()

    if not isinstance(attempt.warnings, (list, tuple)):
        raise SocialIdeaReadError(
            "Attempt warnings liste veya tuple olmalıdır.",
            error_code="IDEA_READ_INCONSISTENT",
        )

    req_target_ids = list(attempt.requested_target_ids or [])
    target_order = {tid: idx for idx, tid in enumerate(req_target_ids)}
    cat_order = {cid: idx for idx, cid in enumerate(plan_cat_ids)}

    parsed_warnings: list[SocialIdeaWarning] = []
    allowed_keys = {"target_id", "category_id", "reason_code"}

    for w in attempt.warnings:
        if not isinstance(w, dict):
            raise SocialIdeaReadError(
                "Her warning bir sözlük (dict) olmalıdır.",
                error_code="IDEA_READ_INCONSISTENT",
            )

        if not set(w.keys()).issubset(allowed_keys):
            raise SocialIdeaReadError(
                "Warning beklenmeyen ekstra alanlar içeriyor.",
                error_code="IDEA_READ_INCONSISTENT",
            )

        if "target_id" not in w or "reason_code" not in w:
            raise SocialIdeaReadError(
                "Warning target_id ve reason_code zorunludur.",
                error_code="IDEA_READ_INCONSISTENT",
            )

        tid = w["target_id"]
        if isinstance(tid, bool) or not isinstance(tid, int) or tid <= 0 or tid not in target_order:
            raise SocialIdeaReadError(
                "Warning target_id geçersiz veya requested target'lar arasında değil.",
                error_code="IDEA_READ_INCONSISTENT",
            )

        cid = w.get("category_id")
        if cid is not None:
            if isinstance(cid, bool) or not isinstance(cid, int) or cid <= 0 or cid not in cat_order:
                raise SocialIdeaReadError(
                    "Warning category_id geçersiz veya plan kategorileri arasında değil.",
                    error_code="IDEA_READ_INCONSISTENT",
                )

        rcode = w["reason_code"]
        if not isinstance(rcode, str) or len(rcode.strip()) == 0 or len(rcode) > 100 or rcode != rcode.strip():
            raise SocialIdeaReadError(
                "Warning reason_code boş olamaz ve 1-100 karakter olmalıdır.",
                error_code="IDEA_READ_INCONSISTENT",
            )

        parsed_warnings.append(
            SocialIdeaWarning(
                target_id=tid,
                category_id=cid,
                reason_code=rcode,
            )
        )

    # Deterministik Warnings sıralaması:
    # 1. Target sırası
    # 2. Kategori sırası
    # 3. reason_code
    def _warning_sort_key(item: SocialIdeaWarning) -> tuple[int, int, str]:
        t_idx = target_order.get(item.target_id, 999)
        c_idx = cat_order.get(item.category_id, -1) if item.category_id is not None else -1
        return (t_idx, c_idx, item.reason_code)

    parsed_warnings.sort(key=_warning_sort_key)
    return tuple(parsed_warnings)
