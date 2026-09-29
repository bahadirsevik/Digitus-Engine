# -*- coding: utf-8 -*-
"""Sosyal Brief Fikir Tekrar Deneme (ideas_retry) Persistence ve Atomik Finalization (F1-F.7.7).

Bu modül doğrulanmış ideas_retry AI sonuçlarını yalnızca retry snapshot'ındaki
(kategori, hedef) atamalarına kaydeder — eksik hedefler ve boş kategoriler (K4); boş
kategori ataması başka kategoride kapsanmış bir hedefi de isteyebilir. INSERT-ONLY:
mevcut SocialIdea/SocialContent satırlarını ve diğer denemeleri değiştirmez/silmez;
retry attempt'ini aynı transaction içinde completed veya partial olarak finalize eder.

Kurallar:
- Transaction açmaz (db.begin() çağırmaz).
- commit veya rollback çağırmaz; transaction çağıran katmana aittir.
- AI çağrısı yapmaz.
- Celery task veya HTTP exception içermez.
- Başarı sonunda db.flush() çağrılır.
- Global kilit sırası (ScoringRun -> SocialBrief -> SocialGenerationAttempt) korunur
  (lock_ideas_retry_attempt_for_finalize üzerinden sağlanır).
- Bütün kontroller bitmeden SocialIdea insert edilmez.
- Hata halinde çağıran rollback yaptığında hiçbir fikir veya state mutasyonu kalmaz.
- Hata mesajlarında ham AI çıktısı, SQL, dinamik ID veya kullanıcı metni sızdırılmaz.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.core.social.idea_contract import ValidatedSocialIdea
from app.core.social.idea_persistence import (
    PersistedSocialIdea,
    extract_social_idea_plan_snapshot,
)
from app.core.social.idea_planner import (
    IdeaCategoryPlan,
    IdeaTargetQuota,
    SocialIdeaGenerationPlan,
)
from app.core.social.idea_retry_snapshot import (
    SocialIdeaRetryPlanSnapshot,
    SocialIdeaRetrySnapshotError,
    extract_social_idea_retry_plan_snapshot,
)
from app.database.models import (
    ScoringRun,
    SocialBrief,
    SocialBriefKeyword,
    SocialBriefTarget,
    SocialCategory,
    SocialGenerationAttempt,
    SocialIdea,
)
from app.generators.social.attempt_state import (
    AttemptNotWritableError,
    lock_ideas_retry_attempt_for_finalize,
)
from app.generators.social.brief_idea_generator import SocialIdeaAIResult
from app.generators.social.format_matrix import get_platform_format


class SocialIdeaRetryPersistenceError(ValueError):
    """Sosyal fikir retry persistence domain hatası."""

    def __init__(
        self,
        message: str,
        *,
        error_code: str,
        field: str | None = None,
        category_id: int | None = None,
        idea_index: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.field = field
        self.category_id = category_id
        self.idea_index = idea_index

    def __repr__(self) -> str:
        return (
            f"SocialIdeaRetryPersistenceError(error_code={self.error_code!r}, "
            f"message={self.message!r}, field={self.field!r})"
        )


@dataclass(frozen=True)
class PersistedSocialIdeaRetryResult:
    """Fikir tekrar deneme persistence nihai sonucu (immutable)."""

    brief_id: int
    scoring_run_id: int
    attempt_id: int
    status: str
    newly_persisted_count: int
    newly_persisted_ideas: tuple[PersistedSocialIdea, ...]
    accepted_target_ids: tuple[int, ...]
    unfilled_target_ids: tuple[int, ...]
    already_completed: bool
    unfilled_category_ids: tuple[int, ...] = ()


# Mimari uyumluluk için alias
PersistedSocialIdeasRetryResult = PersistedSocialIdeaRetryResult


def _validate_now(now: datetime | None) -> datetime:
    """Timezone-aware UTC zamanını doğrular veya üretir."""
    if now is not None:
        if now.tzinfo is None:
            raise ValueError(
                "Enjekte edilen 'now' parametresi timezone-aware olmalıdır (tzinfo is None)."
            )
        return now.astimezone(timezone.utc)
    return datetime.now(timezone.utc)


def _validate_trend_alignment(
    val: Any,
    *,
    error_code: str = "IDEA_RETRY_PERSISTENCE_INVALID_INPUT",
    category_id: int | None = None,
    idea_index: int | None = None,
) -> float:
    """trend_alignment değerini sayısal taşma ve sınır kontrolleriyle doğrular."""
    if isinstance(val, bool) or val is None:
        raise SocialIdeaRetryPersistenceError(
            "trend_alignment sayısal bir değer olmalıdır.",
            error_code=error_code,
            field="trend_alignment",
            category_id=category_id,
            idea_index=idea_index,
        )

    try:
        fval = float(val)
    except (OverflowError, ValueError, TypeError):
        raise SocialIdeaRetryPersistenceError(
            "trend_alignment sayısal değere dönüştürülemedi veya taşma oluştu.",
            error_code=error_code,
            field="trend_alignment",
            category_id=category_id,
            idea_index=idea_index,
        ) from None

    if not math.isfinite(fval):
        raise SocialIdeaRetryPersistenceError(
            "trend_alignment sonlu bir sayı olmalıdır.",
            error_code=error_code,
            field="trend_alignment",
            category_id=category_id,
            idea_index=idea_index,
        )

    if not (0.0 <= fval <= 1.0):
        raise SocialIdeaRetryPersistenceError(
            "trend_alignment 0.0 ile 1.0 arasında olmalıdır.",
            error_code=error_code,
            field="trend_alignment",
            category_id=category_id,
            idea_index=idea_index,
        )

    return fval


def _validate_idea_title(
    title: Any,
    *,
    error_code: str = "IDEA_RETRY_PERSISTENCE_INVALID_INPUT",
    category_id: int | None = None,
    idea_index: int | None = None,
) -> str:
    """Fikir başlığını strict sınırlar ve boşluk kurallarıyla doğrular."""
    if (
        not isinstance(title, str)
        or len(title) == 0
        or title != title.strip()
        or len(title) > 200
    ):
        raise SocialIdeaRetryPersistenceError(
            "idea_title başta/sonda boşluksuz 1-200 karakter olmalıdır.",
            error_code=error_code,
            field="idea_title",
            category_id=category_id,
            idea_index=idea_index,
        )
    return title


def _validate_idea_description(
    desc: Any,
    *,
    error_code: str = "IDEA_RETRY_PERSISTENCE_INVALID_INPUT",
    category_id: int | None = None,
    idea_index: int | None = None,
) -> str:
    """Fikir açıklamasını strict sınırlar ve boşluk kurallarıyla doğrular."""
    if (
        not isinstance(desc, str)
        or len(desc) == 0
        or desc != desc.strip()
        or len(desc) > 2000
    ):
        raise SocialIdeaRetryPersistenceError(
            "idea_description başta/sonda boşluksuz 1-2000 karakter olmalıdır.",
            error_code=error_code,
            field="idea_description",
            category_id=category_id,
            idea_index=idea_index,
        )
    return desc


def persist_social_idea_retry_results(
    db: Session,
    *,
    attempt_id: int,
    task_id: str,
    category_results: tuple[SocialIdeaAIResult, ...],
    ai_calls_used: int = 0,
    metrics: dict[str, int] | None = None,
    now: datetime | None = None,
) -> PersistedSocialIdeaRetryResult:
    """Doğrulanmış retry AI sonuçlarını atomik olarak SocialIdea satırlarına yazar ve attempt'i finalize eder.

    Args:
        db: Aktif SQLAlchemy oturumu.
        attempt_id: Finalize edilecek ideas_retry attempt ID'si.
        task_id: Görevi yürüten Celery task ID'si.
        category_results: Kategori bazında AI sonuçları tuple'ı.
        ai_calls_used: AI çağrı adedi telemetrisi (varsayılan 0).
        now: Zaman enjeksiyonu (isteğe bağlı).

    Returns:
        PersistedSocialIdeaRetryResult: Immutable persistence sonucu.

    Raises:
        SocialIdeaRetryPersistenceError: Girdi, bütünlük veya çakışma durumunda.
        AttemptNotWritableError: Kilit veya attempt yaşam döngüsü durumunda.
    """
    current_time = _validate_now(now)

    # 1. Temel tip ve girdi doğrulamaları
    if isinstance(ai_calls_used, bool) or not isinstance(ai_calls_used, int) or ai_calls_used < 0:
        raise SocialIdeaRetryPersistenceError(
            "ai_calls_used negatif olmayan tamsayı olmalıdır.",
            error_code="IDEA_RETRY_PERSISTENCE_INVALID_INPUT",
            field="ai_calls_used",
        )

    if metrics is not None and (
        not isinstance(metrics, dict)
        or any(
            not isinstance(k, str) or isinstance(v, bool) or not isinstance(v, int) or v < 0
            for k, v in metrics.items()
        )
    ):
        raise SocialIdeaRetryPersistenceError(
            "metrics negatif olmayan tamsayı sayaçlardan oluşan bir sözlük olmalıdır.",
            error_code="IDEA_RETRY_PERSISTENCE_INVALID_INPUT",
            field="metrics",
        )

    if not isinstance(task_id, str) or not task_id.strip():
        raise SocialIdeaRetryPersistenceError(
            "task_id boş olmayan bir metin olmalıdır.",
            error_code="IDEA_RETRY_PERSISTENCE_INVALID_INPUT",
            field="task_id",
        )

    if type(category_results) is not tuple:
        raise SocialIdeaRetryPersistenceError(
            "category_results tuple tipinde olmalıdır.",
            error_code="IDEA_RETRY_PERSISTENCE_INVALID_INPUT",
            field="category_results",
        )

    for idx, r in enumerate(category_results):
        if type(r) is not SocialIdeaAIResult:
            raise SocialIdeaRetryPersistenceError(
                "category_results elemanları SocialIdeaAIResult örneği olmalıdır.",
                error_code="IDEA_RETRY_PERSISTENCE_INVALID_INPUT",
                field="category_results",
                idea_index=idx,
            )
        if (
            isinstance(r.ai_calls_used, bool)
            or not isinstance(r.ai_calls_used, int)
            or r.ai_calls_used < 0
        ):
            raise SocialIdeaRetryPersistenceError(
                "ai_calls_used negatif olmayan tamsayı olmalıdır.",
                error_code="IDEA_RETRY_PERSISTENCE_INVALID_INPUT",
                field="ai_calls_used",
                category_id=getattr(r, "category_id", None),
            )

    # 2. Canonical lock sırası: ScoringRun -> SocialBrief -> SocialGenerationAttempt
    attempt, brief, scoring_run, already_completed = lock_ideas_retry_attempt_for_finalize(
        db,
        attempt_id=attempt_id,
        task_id=task_id,
        now=current_time,
    )

    # 3. Kaynak attempt'i ve coverage'ı doğrula
    cov = attempt.coverage
    if not isinstance(cov, dict):
        raise SocialIdeaRetryPersistenceError(
            "Attempt coverage snapshot bir sözlük olmalıdır.",
            error_code="IDEA_RETRY_PERSISTENCE_INCONSISTENT",
            field="coverage",
        )

    req = cov.get("request")
    if not isinstance(req, dict):
        raise SocialIdeaRetryPersistenceError(
            "Attempt coverage request bloğu geçersiz.",
            error_code="IDEA_RETRY_PERSISTENCE_INCONSISTENT",
            field="coverage.request",
        )

    source_attempt_id = req.get("source_attempt_id")
    if isinstance(source_attempt_id, bool) or not isinstance(source_attempt_id, int) or source_attempt_id <= 0:
        raise SocialIdeaRetryPersistenceError(
            "Attempt coverage source_attempt_id geçersiz.",
            error_code="IDEA_RETRY_PERSISTENCE_INCONSISTENT",
            field="coverage.request.source_attempt_id",
        )

    source_attempt = (
        db.query(SocialGenerationAttempt)
        .filter(SocialGenerationAttempt.id == source_attempt_id)
        .first()
    )
    if source_attempt is None:
        raise SocialIdeaRetryPersistenceError(
            "Kaynak ideas attempt veritabanında bulunamadı.",
            error_code="IDEA_RETRY_PERSISTENCE_INCONSISTENT",
            field="source_attempt",
        )

    if source_attempt.brief_id != brief.id:
        raise SocialIdeaRetryPersistenceError(
            "Kaynak attempt brief_id uyuşmazlığına sahip.",
            error_code="IDEA_RETRY_PERSISTENCE_INCONSISTENT",
            field="source_attempt.brief_id",
        )

    if source_attempt.stage != "ideas":
        raise SocialIdeaRetryPersistenceError(
            "Kaynak attempt stage 'ideas' olmalıdır.",
            error_code="IDEA_RETRY_PERSISTENCE_INCONSISTENT",
            field="source_attempt.stage",
        )

    if source_attempt.status not in ("completed", "partial", "failed"):
        raise SocialIdeaRetryPersistenceError(
            "Kaynak attempt terminal durumda (completed/partial/failed) olmalıdır.",
            error_code="IDEA_RETRY_PERSISTENCE_INCONSISTENT",
            field="source_attempt.status",
        )

    try:
        source_plan_snapshot = extract_social_idea_plan_snapshot(source_attempt)
    except Exception:
        raise SocialIdeaRetryPersistenceError(
            "Kaynak attempt plan snapshot geçersiz.",
            error_code="IDEA_RETRY_PERSISTENCE_INCONSISTENT",
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

    # 4. Brief target'larını kanonik sırada yükle
    target_rows = (
        db.query(SocialBriefTarget)
        .filter(SocialBriefTarget.brief_id == brief.id)
        .order_by(SocialBriefTarget.id.asc())
        .all()
    )
    if not (1 <= len(target_rows) <= 6):
        raise SocialIdeaRetryPersistenceError(
            "Brief target sayısı 1 ile 6 arasında olmalıdır.",
            error_code="IDEA_RETRY_PERSISTENCE_INCONSISTENT",
            field="targets",
        )

    seen_target_ids: set[int] = set()
    target_map: dict[int, SocialBriefTarget] = {}
    for t in target_rows:
        if isinstance(t.id, bool) or not isinstance(t.id, int) or t.id <= 0:
            raise SocialIdeaRetryPersistenceError(
                "Brief target ID pozitif tamsayı olmalıdır.",
                error_code="IDEA_RETRY_PERSISTENCE_INCONSISTENT",
                field="targets",
            )
        if t.id in seen_target_ids:
            raise SocialIdeaRetryPersistenceError(
                "Brief hedefleri içinde mükerrer ID tespit edildi.",
                error_code="IDEA_RETRY_PERSISTENCE_INCONSISTENT",
                field="targets",
            )
        seen_target_ids.add(t.id)

        if not t.platform or not t.content_format or get_platform_format(t.platform, t.content_format) is None:
            raise SocialIdeaRetryPersistenceError(
                "Hedef platform veya format kanonik matriste geçersiz.",
                error_code="IDEA_RETRY_PERSISTENCE_INCONSISTENT",
                field="targets",
            )
        target_map[t.id] = t

    canonical_target_ids = tuple(t.id for t in target_rows)

    # 5. Retry plan snapshot'ını tek otoriter parser ile ayrıştır
    try:
        retry_snapshot = extract_social_idea_retry_plan_snapshot(
            attempt,
            source_plan=source_plan,
            expected_canonical_target_ids=canonical_target_ids,
            expected_source_attempt_id=source_attempt.id,
        )
    except Exception:
        raise SocialIdeaRetryPersistenceError(
            "Retry attempt snapshot geçersiz.",
            error_code="IDEA_RETRY_PERSISTENCE_INCONSISTENT",
            field="retry_snapshot",
        ) from None

    # Kategori bazında retry plan dağılımını hazırla
    planned_targets_by_category: dict[int, list[int]] = {}
    # Hedef -> ilk atama kategorisi (uyarı kategorisi); kategori -> ilk atama hedefi
    target_to_category: dict[int, int] = {}
    category_to_first_target: dict[int, int] = {}
    for asgn in retry_snapshot.plan.assignments:
        planned_targets_by_category.setdefault(asgn.category_id, []).append(asgn.target_id)
        target_to_category.setdefault(asgn.target_id, asgn.category_id)
        category_to_first_target.setdefault(asgn.category_id, asgn.target_id)
    missing_set = set(retry_snapshot.missing_target_ids)
    persisted_start_set = set(retry_snapshot.persisted_target_ids_at_start)
    empty_category_set = set(retry_snapshot.empty_category_ids)

    # Brief kategorilerini ve anahtar kelimelerini yükle
    db_cats = {
        c.id: c
        for c in db.query(SocialCategory)
        .filter(SocialCategory.brief_id == brief.id)
        .all()
    }
    db_keywords = {
        k.keyword_id
        for k in db.query(SocialBriefKeyword)
        .filter(SocialBriefKeyword.brief_id == brief.id)
        .all()
        if k.keyword_id is not None
    }

    # 6. COMPLETED REPLAY YOLU
    if already_completed:
        if len(category_results) > 0:
            raise SocialIdeaRetryPersistenceError(
                "Zaten completed attempt için category_results boş olmalıdır.",
                error_code="IDEA_RETRY_PERSISTENCE_INVALID_INPUT",
                field="category_results",
            )

        if attempt.status != "completed":
            raise SocialIdeaRetryPersistenceError(
                "Replay yolu yalnızca completed attempt'ler için geçerlidir.",
                error_code="IDEA_RETRY_PERSISTENCE_INCONSISTENT",
                field="status",
            )

        if retry_snapshot.generated_total_accepted != len(retry_snapshot.generated_pairs):
            raise SocialIdeaRetryPersistenceError(
                "Generated total_accepted ve target_ids sayısı uyuşmuyor.",
                error_code="IDEA_RETRY_PERSISTENCE_INCONSISTENT",
                field="generated",
            )

        # Tamamlanan retry'da tüm eksik hedefler ve boş kategoriler kapsanmış olmalıdır
        if retry_snapshot.unfilled_target_ids or retry_snapshot.unfilled_category_ids:
            raise SocialIdeaRetryPersistenceError(
                "Generated hedefler retry missing hedefleri ile tam uyuşmuyor.",
                error_code="IDEA_RETRY_PERSISTENCE_INCONSISTENT",
                field="generated_target_ids",
            )

        expected_canonical_generated = tuple(
            tid for tid in canonical_target_ids if tid in set(retry_snapshot.generated_target_ids)
        )
        if retry_snapshot.generated_target_ids != expected_canonical_generated:
            raise SocialIdeaRetryPersistenceError(
                "Generated hedefler kanonik sırada değil.",
                error_code="IDEA_RETRY_PERSISTENCE_INCONSISTENT",
                field="generated_target_ids",
            )

        db_ideas = (
            db.query(SocialIdea)
            .filter(SocialIdea.brief_id == brief.id, SocialIdea.is_stale.is_(False))
            .all()
        )
        ideas_by_target: dict[int, list[SocialIdea]] = {}
        ideas_by_pair: dict[tuple[int, int], list[SocialIdea]] = {}
        represented_categories: set[int] = set()
        for idea in db_ideas:
            ideas_by_target.setdefault(idea.brief_target_id, []).append(idea)
            ideas_by_pair.setdefault((idea.category_id, idea.brief_target_id), []).append(idea)
            represented_categories.add(idea.category_id)

        # Baseline persisted hedeflerin DB'de en az bir non-stale fikirle temsil edildiği kontrolü
        for tid in retry_snapshot.persisted_target_ids_at_start:
            if len(ideas_by_target.get(tid, [])) == 0:
                raise SocialIdeaRetryPersistenceError(
                    "Baseline persisted hedef DB'de temsil edilmiyor.",
                    error_code="IDEA_RETRY_PERSISTENCE_INCONSISTENT",
                    field="persisted_target_ids_at_start",
                )
        for cid in retry_snapshot.persisted_category_ids_at_start or ():
            if cid not in represented_categories:
                raise SocialIdeaRetryPersistenceError(
                    "Baseline persisted kategori DB'de temsil edilmiyor.",
                    error_code="IDEA_RETRY_PERSISTENCE_INCONSISTENT",
                    field="persisted_category_ids_at_start",
                )

        # Generated (kategori, hedef) çiftlerinin DB paritesi: her çiftte TAM bir non-stale
        # fikir. Çift başlangıçta boştu (hedef eksik veya kategori boş) ve sonraki retry'lar
        # yalnız eksik hedef / boş kategori çiftlerine yazdığı için bu değişmez.
        for cid, tid in retry_snapshot.generated_pairs:
            ideas_for_pair = ideas_by_pair.get((cid, tid), [])
            if len(ideas_for_pair) == 0:
                raise SocialIdeaRetryPersistenceError(
                    (
                        "Generated hedef fikrinin kategori_id'si retry planı ile uyuşmuyor."
                        if ideas_by_target.get(tid)
                        else "Generated hedef DB'de temsil edilmiyor."
                    ),
                    error_code="IDEA_RETRY_PERSISTENCE_INCONSISTENT",
                    field="category_id" if ideas_by_target.get(tid) else "generated_target_ids",
                    category_id=cid,
                )
            if len(ideas_for_pair) > 1:
                raise SocialIdeaRetryPersistenceError(
                    "Generated hedef için birden fazla non-stale fikir bulundu.",
                    error_code="IDEA_RETRY_PERSISTENCE_INCONSISTENT",
                    field="generated_target_ids",
                )

            idea = ideas_for_pair[0]

            # 3. Kategori kontrolü: aynı brief, aynı scoring run, non-stale
            cat = db_cats.get(idea.category_id)
            if cat is None or cat.brief_id != brief.id or cat.scoring_run_id != scoring_run.id or cat.is_stale:
                raise SocialIdeaRetryPersistenceError(
                    "Generated hedef fikrinin bağlı olduğu kategori geçersiz, stale veya başka brief/run'a ait.",
                    error_code="IDEA_RETRY_PERSISTENCE_INCONSISTENT",
                    field="category_id",
                    category_id=idea.category_id,
                )

            # 4. Fikrin brief_id, brief_target_id, target_platform, content_format, keyword_id alanları
            if idea.brief_id != brief.id:
                raise SocialIdeaRetryPersistenceError(
                    "Generated hedef fikrinin brief_id'si uyuşmuyor.",
                    error_code="IDEA_RETRY_PERSISTENCE_INCONSISTENT",
                    field="brief_id",
                )

            target = target_map.get(tid)
            if target is None:
                raise SocialIdeaRetryPersistenceError(
                    "Generated hedef brief hedefleri arasında bulunamadı.",
                    error_code="IDEA_RETRY_PERSISTENCE_INCONSISTENT",
                    field="brief_target_id",
                )

            if idea.target_platform != target.platform or idea.content_format != target.content_format:
                raise SocialIdeaRetryPersistenceError(
                    "Generated hedef fikrinin platform veya formatı bağlı hedefle uyuşmuyor.",
                    error_code="IDEA_RETRY_PERSISTENCE_INCONSISTENT",
                    field="target_platform",
                )

            # 5. Platform-format kanonik matriste geçerli olmalı
            if get_platform_format(idea.target_platform, idea.content_format) is None:
                raise SocialIdeaRetryPersistenceError(
                    "Generated hedef fikrinin platform/format kombinasyonu kanonik matriste geçerli değil.",
                    error_code="IDEA_RETRY_PERSISTENCE_INCONSISTENT",
                    field="content_format",
                )

            # 4 (devam). keyword_id brief anahtar kelimeleriyle uyumlu olmalı
            if idea.keyword_id not in db_keywords:
                raise SocialIdeaRetryPersistenceError(
                    "Generated hedef fikrinin anahtar kelimesi brief anahtar kelimeleri arasında bulunamadı.",
                    error_code="IDEA_RETRY_PERSISTENCE_INCONSISTENT",
                    field="keyword_id",
                )

            # 6. idea_title, idea_description ve trend_alignment doğrulama
            _validate_trend_alignment(
                idea.trend_alignment,
                error_code="IDEA_RETRY_PERSISTENCE_INCONSISTENT",
                category_id=idea.category_id,
            )
            _validate_idea_title(
                idea.idea_title,
                error_code="IDEA_RETRY_PERSISTENCE_INCONSISTENT",
                category_id=idea.category_id,
            )
            _validate_idea_description(
                idea.idea_description,
                error_code="IDEA_RETRY_PERSISTENCE_INCONSISTENT",
                category_id=idea.category_id,
            )

        return PersistedSocialIdeaRetryResult(
            brief_id=brief.id,
            scoring_run_id=scoring_run.id,
            attempt_id=attempt.id,
            status="completed",
            newly_persisted_count=0,
            newly_persisted_ideas=(),
            accepted_target_ids=retry_snapshot.generated_target_ids,
            unfilled_target_ids=(),
            already_completed=True,
        )

    # 7. YENİ YAZIM YOLU: category_results sözleşmesi kontrolleri
    if len(category_results) == 0:
        raise SocialIdeaRetryPersistenceError(
            "Yeni yazım yolunda category_results boş olamaz.",
            error_code="IDEA_RETRY_PERSISTENCE_INVALID_INPUT",
            field="category_results",
        )

    seen_categories: set[int] = set()
    for r in category_results:
        if r.category_id in seen_categories:
            raise SocialIdeaRetryPersistenceError(
                "Mükerrer kategori sonucu tespit edildi.",
                error_code="IDEA_RETRY_PERSISTENCE_INVALID_INPUT",
                field="category_results",
                category_id=r.category_id,
            )
        seen_categories.add(r.category_id)

        if r.attempt_id != attempt.id:
            raise SocialIdeaRetryPersistenceError(
                "Kategori sonucundaki attempt_id mevcut attempt ile uyuşmuyor.",
                error_code="IDEA_RETRY_PERSISTENCE_INVALID_INPUT",
                field="attempt_id",
                category_id=r.category_id,
            )

        if r.category_id not in planned_targets_by_category:
            raise SocialIdeaRetryPersistenceError(
                "Plan dışı kategori sonucu reddedildi.",
                error_code="IDEA_RETRY_PERSISTENCE_INVALID_INPUT",
                field="category_id",
                category_id=r.category_id,
            )

        expected_tids = planned_targets_by_category[r.category_id]
        cat_tids_in_result: list[int] = []

        for idea_idx, idea in enumerate(r.ideas):
            if type(idea) is not ValidatedSocialIdea:
                raise SocialIdeaRetryPersistenceError(
                    "Fikir öğesi ValidatedSocialIdea örneği olmalıdır.",
                    error_code="IDEA_RETRY_PERSISTENCE_INVALID_INPUT",
                    field="ideas",
                    category_id=r.category_id,
                    idea_index=idea_idx,
                )

            if idea.target_id not in expected_tids:
                raise SocialIdeaRetryPersistenceError(
                    "Kategori için planlanmamış hedef sonucu reddedildi.",
                    error_code="IDEA_RETRY_PERSISTENCE_INVALID_INPUT",
                    field="target_id",
                    category_id=r.category_id,
                    idea_index=idea_idx,
                )

            # Eksik hedef dışındaki (kapsanmış) bir hedef YALNIZ boş kategori onarımı
            # atamasıyla kabul edilir (K4); aksi halde reddedilir.
            is_empty_category_repair = r.category_id in empty_category_set
            if idea.target_id not in missing_set and not is_empty_category_repair:
                raise SocialIdeaRetryPersistenceError(
                    "Eksik hedefler listesinde olmayan hedef sonucu reddedildi.",
                    error_code="IDEA_RETRY_PERSISTENCE_INVALID_INPUT",
                    field="target_id",
                    category_id=r.category_id,
                    idea_index=idea_idx,
                )

            if idea.target_id in persisted_start_set and not is_empty_category_repair:
                raise SocialIdeaRetryPersistenceError(
                    "Önceden persisted hedef için yeni fikir reddedildi.",
                    error_code="IDEA_RETRY_PERSISTENCE_INVALID_INPUT",
                    field="target_id",
                    category_id=r.category_id,
                    idea_index=idea_idx,
                )

            if idea.target_id in cat_tids_in_result:
                raise SocialIdeaRetryPersistenceError(
                    "Aynı hedef için mükerrer fikir sonucu reddedildi.",
                    error_code="IDEA_RETRY_PERSISTENCE_INVALID_INPUT",
                    field="target_id",
                    category_id=r.category_id,
                    idea_index=idea_idx,
                )
            cat_tids_in_result.append(idea.target_id)

        # Kategori içi kısmi sonuç kabul edilir (plan §3.4: uymayan fikir atılır);
        # kapsanmayan hedefler target_unfilled uyarısıyla kalır. Boş sonuç kabul edilmez.
        if len(cat_tids_in_result) == 0:
            raise SocialIdeaRetryPersistenceError(
                "Kategori sonucu en az bir fikir içermelidir.",
                error_code="IDEA_RETRY_PERSISTENCE_INVALID_INPUT",
                field="target_id",
                category_id=r.category_id,
            )

    # 8. DB Bütünlük ve Alan Kontrolleri (Categories, Keywords, Targets, Format, String Bounds)
    validated_ideas_data: list[tuple[int, ValidatedSocialIdea, float]] = []

    for r in category_results:
        cat_id = r.category_id
        cat = db_cats.get(cat_id)
        if cat is None or cat.is_stale:
            raise SocialIdeaRetryPersistenceError(
                "Kategori brief altında bulunamadı veya stale durumda.",
                error_code="IDEA_RETRY_PERSISTENCE_INCONSISTENT",
                category_id=cat_id,
            )
        if cat.scoring_run_id != scoring_run.id:
            raise SocialIdeaRetryPersistenceError(
                "Kategori scoring_run uyuşmazlığına sahip.",
                error_code="IDEA_RETRY_PERSISTENCE_INCONSISTENT",
                category_id=cat_id,
            )

        for idea_idx, idea in enumerate(r.ideas):
            if idea.target_id not in target_map:
                raise SocialIdeaRetryPersistenceError(
                    "Hedef brief hedefleri arasında bulunamadı.",
                    error_code="IDEA_RETRY_PERSISTENCE_INCONSISTENT",
                    category_id=cat_id,
                    idea_index=idea_idx,
                )
            target = target_map[idea.target_id]
            if idea.target_platform != target.platform or idea.content_format != target.content_format:
                raise SocialIdeaRetryPersistenceError(
                    "Fikrin platform veya formatı bağlı hedefle uyuşmuyor.",
                    error_code="IDEA_RETRY_PERSISTENCE_INCONSISTENT",
                    category_id=cat_id,
                    idea_index=idea_idx,
                )
            if get_platform_format(idea.target_platform, idea.content_format) is None:
                raise SocialIdeaRetryPersistenceError(
                    "Fikrin platform/format kombinasyonu kanonik matriste geçerli değil.",
                    error_code="IDEA_RETRY_PERSISTENCE_INCONSISTENT",
                    category_id=cat_id,
                    idea_index=idea_idx,
                )

            if idea.primary_keyword_id not in db_keywords:
                raise SocialIdeaRetryPersistenceError(
                    "Anahtar kelime brief anahtar kelimeleri arasında bulunamadı.",
                    error_code="IDEA_RETRY_PERSISTENCE_INCONSISTENT",
                    category_id=cat_id,
                    idea_index=idea_idx,
                )

            ta = _validate_trend_alignment(
                idea.trend_alignment,
                error_code="IDEA_RETRY_PERSISTENCE_INVALID_INPUT",
                category_id=cat_id,
                idea_index=idea_idx,
            )
            _validate_idea_title(
                idea.idea_title,
                error_code="IDEA_RETRY_PERSISTENCE_INVALID_INPUT",
                category_id=cat_id,
                idea_index=idea_idx,
            )
            _validate_idea_description(
                idea.idea_description,
                error_code="IDEA_RETRY_PERSISTENCE_INVALID_INPUT",
                category_id=cat_id,
                idea_index=idea_idx,
            )

            validated_ideas_data.append((cat_id, idea, ta))

    # 9. Canlı DB Çakışma Kontrolü (Conflict Check)
    db_existing_ideas = (
        db.query(SocialIdea)
        .filter(SocialIdea.brief_id == brief.id, SocialIdea.is_stale.is_(False))
        .all()
    )
    existing_represented_targets = {idea.brief_target_id for idea in db_existing_ideas}

    # Baseline persisted hedeflerin DB'de temsil edildiği doğrulanmalı
    for tid in retry_snapshot.persisted_target_ids_at_start:
        if tid not in existing_represented_targets:
            raise SocialIdeaRetryPersistenceError(
                "Baseline persisted hedef DB'de temsil edilmiyor.",
                error_code="IDEA_RETRY_PERSISTENCE_INCONSISTENT",
                field="persisted_target_ids_at_start",
            )

    existing_represented_categories = {idea.category_id for idea in db_existing_ideas}
    for cid in retry_snapshot.persisted_category_ids_at_start or ():
        if cid not in existing_represented_categories:
            raise SocialIdeaRetryPersistenceError(
                "Baseline persisted kategori DB'de temsil edilmiyor.",
                error_code="IDEA_RETRY_PERSISTENCE_INCONSISTENT",
                field="persisted_category_ids_at_start",
            )

    # missing_target_ids içindeki hiçbir hedef için önceden non-stale fikir bulunmamalı
    for tid in retry_snapshot.missing_target_ids:
        if tid in existing_represented_targets:
            raise SocialIdeaRetryPersistenceError(
                "Eksik hedef canlı veritabanında başka bir işlem tarafından doldurulmuş.",
                error_code="IDEA_RETRY_PERSISTENCE_CONFLICT",
                field="missing_target_ids",
            )
    # Boş kategoriler de başka bir işlem tarafından doldurulmamış olmalı
    for cid in retry_snapshot.empty_category_ids:
        if cid in existing_represented_categories:
            raise SocialIdeaRetryPersistenceError(
                "Boş kategori canlı veritabanında başka bir işlem tarafından doldurulmuş.",
                error_code="IDEA_RETRY_PERSISTENCE_CONFLICT",
                field="empty_category_ids",
            )

    # 10. Hedef Sıralaması ve Sıfır Fikir Kontrolü
    accepted_targets_set = {idea.target_id for _, idea, _ in validated_ideas_data}
    if len(accepted_targets_set) == 0:
        raise SocialIdeaRetryPersistenceError(
            "Kabul edilen fikir sayısı sıfır olamaz.",
            error_code="IDEA_RETRY_PERSISTENCE_INVALID_INPUT",
            field="accepted_target_ids",
        )
    accepted_pairs_set = {(cat_id, idea.target_id) for cat_id, idea, _ in validated_ideas_data}
    accepted_categories_set = {cat_id for cat_id, _ in accepted_pairs_set}

    accepted_target_ids = tuple(tid for tid in canonical_target_ids if tid in accepted_targets_set)
    unfilled_target_ids = tuple(
        tid for tid in retry_snapshot.missing_target_ids if tid not in accepted_targets_set
    )
    unfilled_category_ids = tuple(
        cid for cid in retry_snapshot.empty_category_ids if cid not in accepted_categories_set
    )

    # 11. Atomik DB Yazımı
    persisted_ideas: list[PersistedSocialIdea] = []
    for cat_id, idea, ta in validated_ideas_data:
        idea_row = SocialIdea(
            category_id=cat_id,
            keyword_id=idea.primary_keyword_id,
            brief_id=brief.id,
            brief_target_id=idea.target_id,
            idea_title=idea.idea_title,
            idea_description=idea.idea_description,
            target_platform=idea.target_platform,
            content_format=idea.content_format,
            trend_alignment=ta,
            is_stale=False,
            is_selected=False,
            regeneration_count=0,
            created_at=current_time,
        )
        db.add(idea_row)
        db.flush()

        persisted_ideas.append(
            PersistedSocialIdea(
                id=idea_row.id,
                category_id=idea_row.category_id,
                keyword_id=idea_row.keyword_id,
                brief_id=idea_row.brief_id,
                brief_target_id=idea_row.brief_target_id,
                idea_title=idea_row.idea_title,
                idea_description=idea_row.idea_description,
                target_platform=idea_row.target_platform,
                content_format=idea_row.content_format,
                trend_alignment=idea_row.trend_alignment,
            )
        )

    # 12. Coverage Güncellemesi
    cov = dict(attempt.coverage) if isinstance(attempt.coverage, dict) else {}
    cov["generated"] = {
        "total_accepted": len(persisted_ideas),
        "target_ids": list(accepted_target_ids),
    }
    if not retry_snapshot.is_legacy:
        cov["generated"]["assignments"] = [
            {"category_id": c_id, "target_id": t_id}
            for c_id, t_id in retry_snapshot.plan.assignment_pairs
            if (c_id, t_id) in accepted_pairs_set
        ]
    if metrics is not None:
        final_metrics = dict(metrics)
        final_metrics["target_unfilled"] = len(unfilled_target_ids)
        if not retry_snapshot.is_legacy:
            final_metrics["category_unfilled"] = len(unfilled_category_ids)
        cov["metrics"] = final_metrics
    attempt.coverage = cov

    # 13. Attempt Finalization (Completed vs Partial) — K4: hedefler VE kategoriler
    if len(unfilled_target_ids) == 0 and len(unfilled_category_ids) == 0:
        attempt.status = "completed"
        attempt.reason_code = None
        attempt.error_message = None
        attempt.warnings = []
        attempt.completed_at = current_time
        attempt.lease_expires_at = None
    else:
        attempt.status = "partial"
        attempt.reason_code = "target_unfilled" if unfilled_target_ids else "category_unfilled"
        attempt.error_message = "Some retry targets or categories could not be filled."
        warnings_list: list[dict[str, Any]] = []
        for tid in unfilled_target_ids:
            w_item: dict[str, Any] = {
                "target_id": tid,
                "reason_code": "target_unfilled",
            }
            c_id = target_to_category.get(tid)
            if c_id is not None:
                w_item["category_id"] = c_id
            warnings_list.append(w_item)
        for c_id in unfilled_category_ids:
            warnings_list.append(
                {
                    "target_id": category_to_first_target[c_id],
                    "category_id": c_id,
                    "reason_code": "category_unfilled",
                }
            )
        attempt.warnings = warnings_list
        attempt.completed_at = current_time
        attempt.lease_expires_at = None

    db.flush()

    return PersistedSocialIdeaRetryResult(
        brief_id=brief.id,
        scoring_run_id=scoring_run.id,
        attempt_id=attempt.id,
        status=attempt.status,
        newly_persisted_count=len(persisted_ideas),
        newly_persisted_ideas=tuple(persisted_ideas),
        accepted_target_ids=accepted_target_ids,
        unfilled_target_ids=unfilled_target_ids,
        already_completed=False,
        unfilled_category_ids=unfilled_category_ids,
    )


# Alias
persist_social_ideas_retry = persist_social_idea_retry_results
