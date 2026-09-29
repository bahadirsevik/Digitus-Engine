# -*- coding: utf-8 -*-
"""Salt-Okunur Fikir Tekrar Deneme (ideas_retry) Sonuç ve Coverage Servisi (F1-F.7.8 / F1-F.7.8a).

Bu modül SocialGenerationAttempt (stage='ideas_retry') sonucunu workspace güvenliğiyle
okur, fail-closed kurallarla doğrular, deterministik domain DTO'larına dönüştürür.

Kurallar:
- Salt-okunurdur: commit, rollback, flush, db.begin çağırmaz.
- with_for_update() kullanmaz.
- ORM modellerini mutate etmez.
- AI veya dış ağ çağrısı yapmaz.
- Workspace izolasyonunu sıkı korur (cross-workspace bilgi sızdırmaz).
- Deterministik sıralama garantisi sunar:
  (kanonik target sırası, kategori/source plan sırası, idea.id ASC).
- Coverage bütün kanonik target'ları içerir:
  requested=1, accepted=1 (en az bir fikir varsa), missing=0 (aksi halde accepted=0, missing=1).
- Pending/running durumunda baseline fikirlerin bulunması geçerlidir, generated blok boş olmalıdır.
- Completed durumda bütün kanonik target'lar covered olmalıdır, warnings ve reason_code boştur.
- Partial durumda doldurulamayan hedefler (target_unfilled) ve — kategori kapsamalı yeni
  snapshot'larda — doldurulamayan boş kategoriler (category_unfilled) warning ile birebir
  eşleşmeli; reason_code hedef eksikse 'target_unfilled', yalnız kategori eksikse
  'category_unfilled' olmalıdır. Legacy snapshot'larda eski sözleşme aynen geçerlidir.
  Önemli: Eski partial attempt'in warning hedeflerinin güncel DB'de hâlâ boş olması şart koşulmaz (tarihsel okuma).
- Failed durumda önceki baseline fikirler okunabilir.
"""
from __future__ import annotations

import math
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.social.idea_contract import ValidatedSocialIdea
from app.core.social.idea_persistence import extract_social_idea_plan_snapshot
from app.core.social.idea_planner import (
    IdeaCategoryPlan,
    IdeaTargetQuota,
    SocialIdeaGenerationPlan,
)
from app.core.social.idea_read import (
    SocialIdeaCoverage,
    SocialIdeaReadError,
    SocialIdeaReadItem,
    SocialIdeaReadNotFoundError,
    SocialIdeaReadResult,
    SocialIdeaWarning,
)
from app.core.social.idea_retry_snapshot import extract_social_idea_retry_plan_snapshot
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
from app.generators.social.format_matrix import (
    get_duration_preset,
    get_platform_format,
)


def _validate_positive_int(value: Any, name: str) -> int:
    """Pozitif tamsayı değerini doğrular (bool reddedilir)."""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise SocialIdeaReadError(
            f"{name} pozitif integer olmalıdır.",
            error_code="IDEA_READ_INVALID_INPUT",
            field=name,
        )
    return value


def _validate_and_sort_retry_warnings(
    attempt: SocialGenerationAttempt,
    *,
    canonical_target_ids: tuple[int, ...],
    category_ids: set[int],
    expected_warnings: list[tuple[int, Optional[int], str]],
    category_order: dict[int, int],
    allowed_reasons: frozenset[str],
) -> tuple[SocialIdeaWarning, ...]:
    """Attempt warnings listesini attempt status'üne duyarlı strict kurallarla doğrular.

    expected_warnings: partial durumda beklenen (target_id, category_id, reason_code)
    kümesi (snapshot'tan türetilir); warnings bununla birebir eşleşmelidir.
    """
    warnings_raw = attempt.warnings

    if attempt.status in ("pending", "running", "completed"):
        if warnings_raw is not None and warnings_raw != [] and warnings_raw != ():
            raise SocialIdeaReadError(
                f"{attempt.status} attempt warnings içermemelidir.",
                error_code="IDEA_READ_INCONSISTENT",
                field="warnings",
            )
        return ()

    if attempt.status == "partial":
        if not isinstance(warnings_raw, list):
            raise SocialIdeaReadError(
                "Partial attempt warnings bir liste olmalıdır.",
                error_code="IDEA_READ_INCONSISTENT",
                field="warnings",
            )
        if len(warnings_raw) != len(expected_warnings):
            raise SocialIdeaReadError(
                "Partial attempt warnings sayısı doldurulamayan hedef/kategori sayısıyla eşleşmelidir.",
                error_code="IDEA_READ_INCONSISTENT",
                field="warnings",
            )
        target_order = {tid: idx for idx, tid in enumerate(canonical_target_ids)}
        expected_set = set(expected_warnings)
        expected_targets = {t for t, _, _ in expected_warnings}
        seen_items: set[tuple[int, Optional[int], str]] = set()
        parsed_warnings: list[tuple[tuple[int, int, str], SocialIdeaWarning]] = []

        for item in warnings_raw:
            if not isinstance(item, dict):
                raise SocialIdeaReadError(
                    "Warning öğesi bir sözlük olmalıdır.",
                    error_code="IDEA_READ_INCONSISTENT",
                    field="warnings",
                )
            tid = item.get("target_id")
            if isinstance(tid, bool) or not isinstance(tid, int) or tid not in target_order:
                raise SocialIdeaReadError(
                    "Warning target_id kanonik hedefler arasında bulunamadı.",
                    error_code="IDEA_READ_INCONSISTENT",
                    field="warnings.target_id",
                )
            if tid not in expected_targets:
                raise SocialIdeaReadError(
                    "Warning hedefi doldurulamayan hedefler arasında değil.",
                    error_code="IDEA_READ_INCONSISTENT",
                    field="warnings.target_id",
                )

            reason = item.get("reason_code")
            if reason not in allowed_reasons:
                raise SocialIdeaReadError(
                    "Partial attempt warning reason_code 'target_unfilled' olmalıdır.",
                    error_code="IDEA_READ_INCONSISTENT",
                    field="warnings.reason_code",
                )

            cid = item.get("category_id")
            key = (tid, cid, reason)
            if key in seen_items:
                raise SocialIdeaReadError(
                    "Aynı hedef için mükerrer warning tespit edildi.",
                    error_code="IDEA_READ_INCONSISTENT",
                    field="warnings.target_id",
                )
            seen_items.add(key)
            if key not in expected_set:
                raise SocialIdeaReadError(
                    "Warning category_id retry planı ataması ile uyuşmuyor.",
                    error_code="IDEA_READ_INCONSISTENT",
                    field="warnings.category_id",
                )

            parsed_warnings.append(
                (
                    (
                        target_order[tid],
                        category_order.get(cid, -1) if cid is not None else -1,
                        reason,
                    ),
                    SocialIdeaWarning(
                        target_id=tid,
                        category_id=cid,
                        reason_code=reason,
                    ),
                )
            )

        if seen_items != expected_set:
            raise SocialIdeaReadError(
                "Warnings tüm doldurulamayan hedefleri içermelidir.",
                error_code="IDEA_READ_INCONSISTENT",
                field="warnings",
            )
        parsed_warnings.sort(key=lambda x: x[0])
        return tuple(w for _, w in parsed_warnings)

    if attempt.status == "failed":
        if warnings_raw is None or warnings_raw == [] or warnings_raw == ():
            return ()
        if not isinstance(warnings_raw, list):
            raise SocialIdeaReadError(
                "Attempt warnings bir liste olmalıdır.",
                error_code="IDEA_READ_INCONSISTENT",
                field="warnings",
            )
        target_order = {tid: idx for idx, tid in enumerate(canonical_target_ids)}
        seen_warned_targets = set()
        parsed_warnings = []
        for item in warnings_raw:
            if not isinstance(item, dict):
                raise SocialIdeaReadError(
                    "Warning öğesi bir sözlük olmalıdır.",
                    error_code="IDEA_READ_INCONSISTENT",
                    field="warnings",
                )
            tid = item.get("target_id")
            if isinstance(tid, bool) or not isinstance(tid, int) or tid not in target_order:
                raise SocialIdeaReadError(
                    "Warning target_id kanonik hedefler arasında bulunamadı.",
                    error_code="IDEA_READ_INCONSISTENT",
                    field="warnings.target_id",
                )
            if tid in seen_warned_targets:
                raise SocialIdeaReadError(
                    "Aynı hedef için mükerrer warning tespit edildi.",
                    error_code="IDEA_READ_INCONSISTENT",
                    field="warnings.target_id",
                )
            seen_warned_targets.add(tid)
            cid = item.get("category_id")
            if cid is not None:
                if isinstance(cid, bool) or not isinstance(cid, int) or cid not in category_ids:
                    raise SocialIdeaReadError(
                        "Warning category_id geçerli kategoriler arasında bulunamadı.",
                        error_code="IDEA_READ_INCONSISTENT",
                        field="warnings.category_id",
                    )
            reason = item.get("reason_code")
            if not isinstance(reason, str) or not reason.strip():
                raise SocialIdeaReadError(
                    "Warning reason_code boş olamaz.",
                    error_code="IDEA_READ_INCONSISTENT",
                    field="warnings.reason_code",
                )
            parsed_warnings.append(
                (
                    target_order[tid],
                    SocialIdeaWarning(
                        target_id=tid,
                        category_id=cid,
                        reason_code=reason,
                    ),
                )
            )
        parsed_warnings.sort(key=lambda x: x[0])
        return tuple(w for _, w in parsed_warnings)

    return ()


def load_social_idea_retry_result(
    db: Session,
    *,
    brief_id: int,
    attempt_id: int,
    brand_profile_id: int,
    replayed: bool = False,
) -> SocialIdeaReadResult:
    """Sosyal brief fikir tekrar deneme (ideas_retry) sonucunu workspace güvenliğiyle okur ve doğrular.

    Sözleşme:
    - db üzerinde hiçbir yazma işlemi (commit, rollback, flush) yapılmaz.
    - FOR UPDATE kullanılmaz.
    - BrandProfile, ScoringRun, SocialBrief ve SocialGenerationAttempt zinciri doğrulanır.
    - Bulunamama veya cross-workspace durumlarında SocialIdeaReadNotFoundError fırlatılır.
    - Snapshot, DB satırları ve coverage fail-closed doğrulanır.
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

    if attempt.stage != "ideas_retry":
        raise SocialIdeaReadError(
            "Attempt aşaması 'ideas_retry' olmalıdır.",
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

    # 3. Kaynak attempt ve plan snapshot çıkarımı
    cov = attempt.coverage
    if not isinstance(cov, dict):
        raise SocialIdeaReadError(
            "Attempt coverage dict olmalıdır.",
            error_code="IDEA_READ_INCONSISTENT",
            field="coverage",
        )

    req = cov.get("request")
    if not isinstance(req, dict):
        raise SocialIdeaReadError(
            "Attempt coverage request bloğu geçersiz.",
            error_code="IDEA_READ_INCONSISTENT",
            field="coverage.request",
        )

    source_attempt_id = req.get("source_attempt_id")
    if isinstance(source_attempt_id, bool) or not isinstance(source_attempt_id, int) or source_attempt_id <= 0:
        raise SocialIdeaReadError(
            "Attempt coverage source_attempt_id geçersiz.",
            error_code="IDEA_READ_INCONSISTENT",
            field="coverage.request.source_attempt_id",
        )

    source_attempt = (
        db.query(SocialGenerationAttempt)
        .filter(SocialGenerationAttempt.id == source_attempt_id)
        .first()
    )
    if source_attempt is None or source_attempt.brief_id != brief.id:
        raise SocialIdeaReadError(
            "Kaynak attempt bulunamadı veya brief uyuşmazlığı var.",
            error_code="IDEA_READ_INCONSISTENT",
            field="source_attempt",
        )
    if source_attempt.stage != "ideas":
        raise SocialIdeaReadError(
            "Kaynak attempt stage 'ideas' olmalıdır.",
            error_code="IDEA_READ_INCONSISTENT",
            field="source_attempt.stage",
        )
    if source_attempt.status not in ("completed", "partial", "failed"):
        raise SocialIdeaReadError(
            "Kaynak attempt durumu retry için uygun değil.",
            error_code="IDEA_READ_INCONSISTENT",
            field="source_attempt.status",
        )

    try:
        source_plan_snapshot = extract_social_idea_plan_snapshot(source_attempt)
    except Exception:
        raise SocialIdeaReadError(
            "Kaynak attempt plan snapshot geçersiz.",
            error_code="IDEA_READ_INCONSISTENT",
            field="source_plan",
        ) from None

    source_plan = SocialIdeaGenerationPlan(
        total_requested=source_plan_snapshot.total_requested,
        category_plans=tuple(
            IdeaCategoryPlan(
                category_id=cp.category_id,
                requested_count=cp.requested_count,
                target_quotas=tuple(
                    IdeaTargetQuota(target_id=t_id, requested_count=q)
                    for t_id, q in cp.target_quotas
                ),
            )
            for cp in source_plan_snapshot.categories
        ),
        covered_category_ids=tuple(cp.category_id for cp in source_plan_snapshot.categories),
        covered_target_ids=source_plan_snapshot.covered_target_ids,
    )

    # 4. Brief target'larını kanonik sırada yükle ve doğrula
    target_rows = (
        db.query(SocialBriefTarget)
        .filter(SocialBriefTarget.brief_id == brief.id)
        .order_by(SocialBriefTarget.id.asc())
        .all()
    )
    if not (1 <= len(target_rows) <= 6):
        raise SocialIdeaReadError(
            "Brief hedefleri 1 ile 6 arasında olmalıdır.",
            error_code="IDEA_READ_INCONSISTENT",
            field="brief_targets",
        )

    seen_target_ids: set[int] = set()
    for t in target_rows:
        if isinstance(t.id, bool) or not isinstance(t.id, int) or t.id <= 0:
            raise SocialIdeaReadError(
                "Hedef ID pozitif integer olmalıdır.",
                error_code="IDEA_READ_INCONSISTENT",
                field="brief_targets.id",
            )
        if t.id in seen_target_ids:
            raise SocialIdeaReadError(
                "Mükerrer hedef ID tespit edildi.",
                error_code="IDEA_READ_INCONSISTENT",
                field="brief_targets.id",
            )
        seen_target_ids.add(t.id)

        fmt = get_platform_format(t.platform, t.content_format)
        if fmt is None:
            raise SocialIdeaReadError(
                "Hedef platform ve format matriste bulunamadı.",
                error_code="IDEA_READ_INCONSISTENT",
                field="brief_targets.format",
            )

        if fmt.requires_duration:
            if t.duration_preset_id is None:
                raise SocialIdeaReadError(
                    "Video formatı için duration_preset_id zorunludur.",
                    error_code="IDEA_READ_INCONSISTENT",
                    field="brief_targets.duration_preset_id",
                )
            preset = get_duration_preset(t.duration_preset_id)
            if preset is None:
                raise SocialIdeaReadError(
                    "Geçersiz süre ön ayarı.",
                    error_code="IDEA_READ_INCONSISTENT",
                    field="brief_targets.duration_preset_id",
                )
            allowed_presets = {p.id for p in fmt.duration_presets}
            if preset.id not in allowed_presets:
                raise SocialIdeaReadError(
                    "Süre ön ayarı formatın süre profili ile uyumsuz.",
                    error_code="IDEA_READ_INCONSISTENT",
                    field="brief_targets.duration_preset_id",
                )
            if t.duration_min_sec != preset.min_sec or t.duration_max_sec != preset.max_sec:
                raise SocialIdeaReadError(
                    "Hedef süre sınırları preset ile uyumsuz.",
                    error_code="IDEA_READ_INCONSISTENT",
                    field="brief_targets.duration_bounds",
                )
        else:
            if (
                t.duration_preset_id is not None
                or t.duration_min_sec is not None
                or t.duration_max_sec is not None
            ):
                raise SocialIdeaReadError(
                    "Video olmayan formatta süre alanları boş olmalıdır.",
                    error_code="IDEA_READ_INCONSISTENT",
                    field="brief_targets.duration",
                )

    canonical_target_ids = tuple(t.id for t in target_rows)
    target_map = {t.id: t for t in target_rows}
    target_order = {tid: idx for idx, tid in enumerate(canonical_target_ids)}

    # 5. Retry plan snapshot'ını ayrıştır
    try:
        retry_snapshot = extract_social_idea_retry_plan_snapshot(
            attempt,
            source_plan=source_plan,
            expected_canonical_target_ids=canonical_target_ids,
            expected_source_attempt_id=source_attempt.id,
        )
    except Exception:
        raise SocialIdeaReadError(
            "Retry attempt snapshot geçersiz.",
            error_code="IDEA_READ_INCONSISTENT",
            field="retry_snapshot",
        ) from None

    # Hedef -> ilk atama kategorisi; kategori -> ilk atama hedefi (uyarı eşlemesi)
    retry_assignment_map: dict[int, int] = {}
    category_first_target: dict[int, int] = {}
    for a in retry_snapshot.plan.assignments:
        retry_assignment_map.setdefault(a.target_id, a.category_id)
        category_first_target.setdefault(a.category_id, a.target_id)
    is_legacy = retry_snapshot.is_legacy

    # Brief kategori ve anahtar kelimelerini yükle
    db_categories = (
        db.query(SocialCategory)
        .filter(SocialCategory.brief_id == brief.id)
        .all()
    )
    category_map = {c.id: c for c in db_categories}
    cat_order = {cid: idx for idx, cid in enumerate(source_plan.covered_category_ids)}

    db_keywords = (
        db.query(SocialBriefKeyword)
        .filter(SocialBriefKeyword.brief_id == brief.id)
        .all()
    )
    brief_keyword_ids = {k.keyword_id for k in db_keywords}

    # 6. DB'deki mevcut non-stale fikirleri çek ve doğrula
    db_ideas = (
        db.query(SocialIdea)
        .filter(SocialIdea.brief_id == brief.id, SocialIdea.is_stale.is_(False))
        .all()
    )

    validated_ideas: list[tuple[SocialIdea, float]] = []
    ideas_by_target: dict[int, list[SocialIdea]] = {}
    ideas_by_pair: dict[tuple[int, int], list[SocialIdea]] = {}
    represented_categories: set[int] = set()

    for idea in db_ideas:
        # Category validation
        if idea.category_id not in source_plan.covered_category_ids:
            raise SocialIdeaReadError(
                "Fikir kategori ID'si kaynak plan kategorileri arasında bulunamadı.",
                error_code="IDEA_READ_INCONSISTENT",
                field="ideas.category_id",
            )
        if idea.category_id not in category_map:
            raise SocialIdeaReadError(
                "Fikrin bağlı olduğu kategori bulunamadı.",
                error_code="IDEA_READ_INCONSISTENT",
                field="ideas.category_id",
            )
        cat = category_map[idea.category_id]
        if cat.brief_id != brief.id or cat.scoring_run_id != brief.scoring_run_id:
            raise SocialIdeaReadError(
                "Fikir kategorisi brief veya scoring run ile uyuşmuyor.",
                error_code="IDEA_READ_INCONSISTENT",
                field="ideas.category_id",
            )
        if cat.is_stale:
            raise SocialIdeaReadError(
                "Fikrin kategorisi stale durumda.",
                error_code="IDEA_READ_INCONSISTENT",
                field="ideas.category_id",
            )

        # Target validation
        if idea.brief_target_id not in target_map:
            raise SocialIdeaReadError(
                "Fikir brief hedefleri arasında olmayan hedefe bağlı.",
                error_code="IDEA_READ_INCONSISTENT",
                field="ideas.brief_target_id",
            )
        target = target_map[idea.brief_target_id]

        # Keyword validation
        if idea.keyword_id not in brief_keyword_ids:
            raise SocialIdeaReadError(
                "Fikrin anahtar kelimesi brief anahtar kelimeleri arasında bulunamadı.",
                error_code="IDEA_READ_INCONSISTENT",
                field="ideas.keyword_id",
            )

        # Platform and format validation
        if idea.target_platform != target.platform or idea.content_format != target.content_format:
            raise SocialIdeaReadError(
                "Fikrin platform veya formatı bağlı hedefle uyuşmuyor.",
                error_code="IDEA_READ_INCONSISTENT",
                field="ideas.platform_format",
            )
        if get_platform_format(idea.target_platform, idea.content_format) is None:
            raise SocialIdeaReadError(
                "Fikrin platform/format kombinasyonu kanonik matriste geçersiz.",
                error_code="IDEA_READ_INCONSISTENT",
                field="ideas.platform_format",
            )

        # Title bounds & whitespace
        if (
            not isinstance(idea.idea_title, str)
            or len(idea.idea_title) == 0
            or len(idea.idea_title) > 200
            or idea.idea_title != idea.idea_title.strip()
        ):
            raise SocialIdeaReadError(
                "Fikir başlığı geçersiz veya sınırlara uymuyor.",
                error_code="IDEA_READ_INCONSISTENT",
                field="ideas.idea_title",
            )

        # Description bounds & whitespace
        if (
            not isinstance(idea.idea_description, str)
            or len(idea.idea_description) == 0
            or len(idea.idea_description) > 2000
            or idea.idea_description != idea.idea_description.strip()
        ):
            raise SocialIdeaReadError(
                "Fikir açıklaması geçersiz veya sınırlara uymuyor.",
                error_code="IDEA_READ_INCONSISTENT",
                field="ideas.idea_description",
            )

        # Trend alignment validation (safely handles bool, OverflowError, NaN, Inf, bounds)
        if isinstance(idea.trend_alignment, bool) or not isinstance(idea.trend_alignment, (int, float)):
            raise SocialIdeaReadError(
                "trend_alignment sayısal bir değer olmalıdır.",
                error_code="IDEA_READ_INCONSISTENT",
                field="ideas.trend_alignment",
            )
        try:
            ta_val = float(idea.trend_alignment)
        except (OverflowError, ValueError, TypeError):
            raise SocialIdeaReadError(
                "trend_alignment sayıya dönüştürülemedi.",
                error_code="IDEA_READ_INCONSISTENT",
                field="ideas.trend_alignment",
            )
        if not math.isfinite(ta_val) or ta_val < 0.0 or ta_val > 1.0:
            raise SocialIdeaReadError(
                "trend_alignment 0.0 ile 1.0 arasında sonlu olmalıdır.",
                error_code="IDEA_READ_INCONSISTENT",
                field="ideas.trend_alignment",
            )

        validated_ideas.append((idea, ta_val))
        ideas_by_target.setdefault(idea.brief_target_id, []).append(idea)
        ideas_by_pair.setdefault((idea.category_id, idea.brief_target_id), []).append(idea)
        represented_categories.add(idea.category_id)

    # Baseline persisted hedeflerin DB'de temsil edildiği doğrulanmalı
    for tid in retry_snapshot.persisted_target_ids_at_start:
        if len(ideas_by_target.get(tid, [])) == 0:
            raise SocialIdeaReadError(
                "Baseline persisted hedef veritabanında temsil edilmiyor.",
                error_code="IDEA_READ_INCONSISTENT",
                field="persisted_target_ids_at_start",
            )

    for cid in retry_snapshot.persisted_category_ids_at_start or ():
        if cid not in represented_categories:
            raise SocialIdeaReadError(
                "Baseline persisted kategori veritabanında temsil edilmiyor.",
                error_code="IDEA_READ_INCONSISTENT",
                field="persisted_category_ids_at_start",
            )

    unfilled_target_ids = retry_snapshot.unfilled_target_ids
    unfilled_category_ids = retry_snapshot.unfilled_category_ids
    generated_pairs = retry_snapshot.generated_pairs

    def _check_generated_pairs() -> None:
        # Her generated (kategori, hedef) çiftinde TAM bir non-stale fikir bulunmalıdır.
        for cid, tid in generated_pairs:
            ideas_for_pair = ideas_by_pair.get((cid, tid), [])
            if len(ideas_for_pair) != 1:
                if len(ideas_for_pair) == 0 and ideas_by_target.get(tid):
                    raise SocialIdeaReadError(
                        "Fikrin kategorisi retry planı ataması ile uyuşmuyor.",
                        error_code="IDEA_READ_INCONSISTENT",
                        field="ideas.category_id",
                    )
                raise SocialIdeaReadError(
                    "Her generated hedef için veritabanında tam olarak bir non-stale fikir bulunmalıdır.",
                    error_code="IDEA_READ_INCONSISTENT",
                    field="ideas",
                )

    # 7. Status bazlı kontroller
    if attempt.status in ("pending", "running"):
        if retry_snapshot.generated_total_accepted != 0 or len(retry_snapshot.generated_target_ids) != 0:
            raise SocialIdeaReadError(
                "Pending veya running durumda generated bloğu boş olmalıdır.",
                error_code="IDEA_READ_INCONSISTENT",
                field="generated",
            )
        for tid in retry_snapshot.missing_target_ids:
            if len(ideas_by_target.get(tid, [])) > 0:
                raise SocialIdeaReadError(
                    "Pending veya running durumda eksik hedefler için fikir bulunamaz.",
                    error_code="IDEA_READ_INCONSISTENT",
                    field="ideas",
                )
        if attempt.warnings is not None and attempt.warnings != [] and attempt.warnings != ():
            raise SocialIdeaReadError(
                "Pending veya running attempt warnings içermemelidir.",
                error_code="IDEA_READ_INCONSISTENT",
                field="warnings",
            )
        if attempt.reason_code is not None or attempt.error_message is not None:
            raise SocialIdeaReadError(
                "Pending veya running attempt reason_code veya error_message içeremez.",
                error_code="IDEA_READ_INCONSISTENT",
                field="reason_code",
            )

    elif attempt.status == "completed":
        if unfilled_target_ids or unfilled_category_ids:
            raise SocialIdeaReadError(
                "Completed attempt generated hedefleri missing hedefleri ile tam eşleşmelidir.",
                error_code="IDEA_READ_INCONSISTENT",
                field="generated_target_ids",
            )
        if is_legacy and retry_snapshot.generated_target_ids != retry_snapshot.missing_target_ids:
            raise SocialIdeaReadError(
                "Completed attempt generated hedefleri missing hedefleri ile tam eşleşmelidir.",
                error_code="IDEA_READ_INCONSISTENT",
                field="generated_target_ids",
            )
        if retry_snapshot.generated_total_accepted != len(generated_pairs):
            raise SocialIdeaReadError(
                "Completed attempt generated_total_accepted hedef sayısıyla eşleşmelidir.",
                error_code="IDEA_READ_INCONSISTENT",
                field="generated_total_accepted",
            )
        _check_generated_pairs()
        for tid in canonical_target_ids:
            if len(ideas_by_target.get(tid, [])) == 0:
                raise SocialIdeaReadError(
                    "Completed attempt tüm hedefleri karşılamalıdır.",
                    error_code="IDEA_READ_INCONSISTENT",
                    field="coverage",
                )
        if not is_legacy:
            for cid in source_plan.covered_category_ids:
                if cid not in represented_categories:
                    raise SocialIdeaReadError(
                        "Completed attempt tüm kategorileri karşılamalıdır.",
                        error_code="IDEA_READ_INCONSISTENT",
                        field="coverage",
                    )
        if attempt.warnings is not None and attempt.warnings != [] and attempt.warnings != ():
            raise SocialIdeaReadError(
                "Completed attempt warnings içermemelidir.",
                error_code="IDEA_READ_INCONSISTENT",
                field="warnings",
            )
        if attempt.reason_code is not None:
            raise SocialIdeaReadError(
                "Completed attempt reason_code içermemelidir.",
                error_code="IDEA_READ_INCONSISTENT",
                field="reason_code",
            )
        if attempt.error_message is not None:
            raise SocialIdeaReadError(
                "Completed attempt error_message içermemelidir.",
                error_code="IDEA_READ_INCONSISTENT",
                field="error_message",
            )

    elif attempt.status == "partial":
        gen_ids = retry_snapshot.generated_target_ids
        if len(generated_pairs) == 0 or not (unfilled_target_ids or unfilled_category_ids):
            raise SocialIdeaReadError(
                "Partial attempt generated hedefleri missing hedeflerinin boş olmayan kesin alt kümesi olmalıdır.",
                error_code="IDEA_READ_INCONSISTENT",
                field="generated_target_ids",
            )
        if is_legacy:
            miss_ids = retry_snapshot.missing_target_ids
            if not (0 < len(gen_ids) < len(miss_ids)):
                raise SocialIdeaReadError(
                    "Partial attempt generated hedefleri missing hedeflerinin boş olmayan kesin alt kümesi olmalıdır.",
                    error_code="IDEA_READ_INCONSISTENT",
                    field="generated_target_ids",
                )
        if retry_snapshot.generated_total_accepted != len(generated_pairs):
            raise SocialIdeaReadError(
                "Partial attempt generated_total_accepted hedef sayısıyla eşleşmelidir.",
                error_code="IDEA_READ_INCONSISTENT",
                field="generated_total_accepted",
            )
        _check_generated_pairs()
        expected_reason = "target_unfilled" if unfilled_target_ids else "category_unfilled"
        if attempt.reason_code != expected_reason:
            raise SocialIdeaReadError(
                "Partial attempt reason_code 'target_unfilled' olmalıdır.",
                error_code="IDEA_READ_INCONSISTENT",
                field="reason_code",
            )
        # Tarihsel polling kuralı: Eski bir partial attempt okunduktan sonra başka bir retry
        # kalan hedefi/kategoriyi doldurmuş olabilir. Unfilled hedeflerin güncel DB'de hâlâ
        # boş olması şart koşulmaz; warnings o attempt'in tarihsel sonucudur, coverage günceldir.

    elif attempt.status == "failed":
        if retry_snapshot.generated_total_accepted != 0 or len(retry_snapshot.generated_target_ids) != 0:
            raise SocialIdeaReadError(
                "Failed durumda generated bloğu boş olmalıdır.",
                error_code="IDEA_READ_INCONSISTENT",
                field="generated",
            )
        # Tarihsel polling güvenliği: Başarısız attempt sonrası yeni retry fikir eklemiş olabilir.

    # 8. Warnings doğrulama ve sıralama
    all_cat_ids = set(category_map.keys())
    expected_warnings: list[tuple[int, Optional[int], str]] = [
        (tid, retry_assignment_map.get(tid), "target_unfilled") for tid in unfilled_target_ids
    ] + [
        (category_first_target[cid], cid, "category_unfilled") for cid in unfilled_category_ids
    ]
    warnings_tuple = _validate_and_sort_retry_warnings(
        attempt,
        canonical_target_ids=canonical_target_ids,
        category_ids=all_cat_ids,
        expected_warnings=expected_warnings,
        category_order=cat_order,
        allowed_reasons=(
            frozenset({"target_unfilled"})
            if is_legacy
            else frozenset({"target_unfilled", "category_unfilled"})
        ),
    )

    # 9. Coverage hesaplama (bütün canonical target'lar için)
    coverage_items: list[SocialIdeaCoverage] = []
    for tid in canonical_target_ids:
        has_idea = len(ideas_by_target.get(tid, [])) > 0
        coverage_items.append(
            SocialIdeaCoverage(
                target_id=tid,
                requested=1,
                accepted=1 if has_idea else 0,
                missing=0 if has_idea else 1,
            )
        )

    ta_by_idea_id = {idea.id: ta for idea, ta in validated_ideas}

    # 10. Fikirleri deterministik sıralama:
    # (kanonik target sırası, kategori plan sırası, idea.id ASC)
    sorted_ideas = sorted(
        db_ideas,
        key=lambda i: (
            target_order.get(i.brief_target_id, 999999),
            cat_order.get(i.category_id, 999999),
            i.id,
        ),
    )

    read_ideas = tuple(
        SocialIdeaReadItem(
            id=i.id,
            category_id=i.category_id,
            keyword_id=i.keyword_id,
            brief_id=i.brief_id,
            brief_target_id=i.brief_target_id,
            idea_title=i.idea_title,
            idea_description=i.idea_description,
            target_platform=i.target_platform,
            content_format=i.content_format,
            trend_alignment=ta_by_idea_id[i.id],
            is_stale=bool(i.is_stale),
        )
        for i in sorted_ideas
    )

    return SocialIdeaReadResult(
        brief_id=brief.id,
        scoring_run_id=brief.scoring_run_id,
        attempt_id=attempt.id,
        attempt_status=attempt.status,
        total_ideas=len(read_ideas),
        ideas=read_ideas,
        coverage=tuple(coverage_items),
        warnings=warnings_tuple,
        reason_code=attempt.reason_code,
        replayed=replayed,
    )
