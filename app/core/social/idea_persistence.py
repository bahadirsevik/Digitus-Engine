# -*- coding: utf-8 -*-
"""Atomik Fikir Persistence ve Başarı Finalizasyonu (F1-F.5 / F1-F.5a).

Bu modül doğrulanmış fikir sonuçlarını SocialIdea satırlarına atomik ve idempotent
biçimde kaydeder; aynı transaction içinde ideas generation attempt'ini completed
(K4 kapsaması tam) veya partial (target_unfilled / category_unfilled uyarılarıyla) yapar.

Kurallar:
- Transaction açmaz (db.begin() çağırmaz).
- commit veya rollback çağırmaz.
- AI çağrısı yapmaz.
- HTTPException kullanmaz.
- Başarı sonunda db.flush() çağrılır.
- Transaction yönetimi tamamen çağıran katmana aittir.
- DB'yi (attempt.coverage içindeki request ve canonical planı) tek otorite kabul eder.
- Global kilit sırası (ScoringRun -> SocialBrief -> SocialGenerationAttempt) korunur.
- Yeni yazım ve completed replay ortak alan doğrulayıcılarını paylaşır.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.core.social.idea_contract import ValidatedSocialIdea
from app.core.social.idea_planner import (
    SocialIdeaGenerationPlan,
    build_social_idea_generation_plan,
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
    lock_ideas_attempt_for_finalize,
)
from app.generators.social.brief_idea_generator import SocialIdeaAIResult
from app.generators.social.format_matrix import get_platform_format


class SocialIdeaPersistenceError(ValueError):
    """Sosyal fikir persistence domain hatası."""

    def __init__(
        self,
        message: str,
        *,
        error_code: str,
        field: str | None = None,
        idea_index: int | None = None,
        category_id: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.field = field
        self.idea_index = idea_index
        self.category_id = category_id

    def __repr__(self) -> str:
        return (
            f"SocialIdeaPersistenceError(error_code={self.error_code!r}, "
            f"message={self.message!r}, field={self.field!r})"
        )


@dataclass(frozen=True)
class PersistedSocialIdea:
    """Veritabanına kaydedilmiş veya doğrulanmış fikir kaydı (immutable)."""

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


@dataclass(frozen=True)
class PersistedSocialIdeasResult:
    """Fikir persistence nihai sonucu (immutable)."""

    brief_id: int
    scoring_run_id: int
    attempt_id: int
    total_ideas: int
    ideas: tuple[PersistedSocialIdea, ...]
    already_completed: bool
    # completed | partial (failed denemeler persistence'a ulaşmaz)
    status: str = "completed"
    unfilled_target_ids: tuple[int, ...] = ()
    unfilled_category_ids: tuple[int, ...] = ()


# Deneme ölçüm bloğu (coverage["metrics"]): yalnız negatif olmayan tamsayı sayaçlar.
IDEA_ATTEMPT_METRIC_KEYS: frozenset[str] = frozenset({
    "ai_calls_used",
    "off_brief_dropped",
    "invalid_dropped",
    "over_quota_dropped",
    "retry_used",
    "topup_used",
    "topup_requests",
    "category_topup_requests",
    "failed_requests",
    "budget_skipped",
    "target_unfilled",
    "category_unfilled",
})


def validate_idea_attempt_metrics(metrics: Any) -> dict[str, int]:
    """coverage["metrics"] bloğunu fail-closed doğrular (bilinen anahtar, int >= 0)."""
    if not isinstance(metrics, dict):
        raise SocialIdeaPersistenceError(
            "Attempt metrics bloğu bir sözlük olmalıdır.",
            error_code="IDEA_PERSISTENCE_INCONSISTENT",
            field="metrics",
        )
    clean: dict[str, int] = {}
    for key, value in metrics.items():
        if key not in IDEA_ATTEMPT_METRIC_KEYS:
            raise SocialIdeaPersistenceError(
                "Attempt metrics bloğunda bilinmeyen alan var.",
                error_code="IDEA_PERSISTENCE_INCONSISTENT",
                field="metrics",
            )
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise SocialIdeaPersistenceError(
                "Attempt metrics değerleri negatif olmayan tamsayı olmalıdır.",
                error_code="IDEA_PERSISTENCE_INCONSISTENT",
                field="metrics",
            )
        clean[key] = value
    return clean


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
    error_code: str = "IDEA_PERSISTENCE_INVALID_INPUT",
    category_id: int | None = None,
    idea_index: int | None = None,
) -> float:
    """trend_alignment değerini sayısal taşma ve sınır kontrolleriyle doğrular."""
    if isinstance(val, bool) or val is None:
        raise SocialIdeaPersistenceError(
            "trend_alignment sayısal bir değer olmalıdır.",
            error_code=error_code,
            field="trend_alignment",
            category_id=category_id,
            idea_index=idea_index,
        )

    try:
        fval = float(val)
    except (OverflowError, ValueError, TypeError):
        raise SocialIdeaPersistenceError(
            "trend_alignment sayısal değere dönüştürülemedi veya taşma oluştu.",
            error_code=error_code,
            field="trend_alignment",
            category_id=category_id,
            idea_index=idea_index,
        )

    if not math.isfinite(fval):
        raise SocialIdeaPersistenceError(
            "trend_alignment sonlu bir sayı olmalıdır.",
            error_code=error_code,
            field="trend_alignment",
            category_id=category_id,
            idea_index=idea_index,
        )

    if not (0.0 <= fval <= 1.0):
        raise SocialIdeaPersistenceError(
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
    error_code: str = "IDEA_PERSISTENCE_INVALID_INPUT",
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
        raise SocialIdeaPersistenceError(
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
    error_code: str = "IDEA_PERSISTENCE_INVALID_INPUT",
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
        raise SocialIdeaPersistenceError(
            "idea_description başta/sonda boşluksuz 1-2000 karakter olmalıdır.",
            error_code=error_code,
            field="idea_description",
            category_id=category_id,
            idea_index=idea_index,
        )
    return desc


@dataclass(frozen=True)
class SocialIdeaCategoryPlanSnapshot:
    """Bir kategori için doğrulanmış plan kotası snapshot'ı (immutable)."""

    category_id: int
    requested_count: int
    target_quotas: tuple[tuple[int, int], ...]  # ((target_id, count), ...)


@dataclass(frozen=True)
class SocialIdeaPlanSnapshot:
    """Doğrulanmış ve kanonik fikir üretim planı snapshot'ı (immutable)."""

    total_requested: int
    categories: tuple[SocialIdeaCategoryPlanSnapshot, ...]
    covered_target_ids: tuple[int, ...]


# Geriye dönük uyumluluk takma adları
_ReconstructedCategoryPlan = SocialIdeaCategoryPlanSnapshot
_ReconstructedPlan = SocialIdeaPlanSnapshot


def extract_social_idea_plan_snapshot(attempt: SocialGenerationAttempt) -> SocialIdeaPlanSnapshot:
    """Attempt.coverage alanındaki snapshot'ı fail-closed doğrulayıp gerçek planı çıkarır.

    Kurallar:
    - coverage tam olarak schema_version, request, plan, generated alanlarını içermeli.
    - request tam olarak category_ids ve ideas_per_category taşımalı.
    - category_ids 1-6 pozitif benzersiz tamsayı olmalı (bool reddedilir).
    - ideas_per_category 1-5 tamsayı olmalı (bool reddedilir).
    - attempt.requested_target_ids 1-6 pozitif benzersiz tamsayı olmalı.
    - build_social_idea_generation_plan yeniden hesaplanır ve coverage.plan ile birebir eşleşir.
    - request.category_ids sırası plan.categories sırası ile birebir aynı olmalıdır.
    """
    cov = attempt.coverage
    if not isinstance(cov, dict):
        raise SocialIdeaPersistenceError(
            "Attempt coverage verisi bir sözlük (dict) olmalıdır.",
            error_code="IDEA_PERSISTENCE_INCONSISTENT",
        )

    if cov.get("schema_version") != "ideas_plan_v1":
        raise SocialIdeaPersistenceError(
            f"Geçersiz schema_version: {cov.get('schema_version')!r}.",
            error_code="IDEA_PERSISTENCE_INCONSISTENT",
        )

    expected_top_keys = {"schema_version", "request", "plan", "generated"}
    # "metrics" (plan §3 Ölçüm) opsiyoneldir; yalnız terminal yazımda eklenir.
    if set(cov.keys()) not in (expected_top_keys, expected_top_keys | {"metrics"}):
        raise SocialIdeaPersistenceError(
            "Attempt coverage yalnızca ve tam olarak schema_version, request, plan, generated (ve opsiyonel metrics) bloklarını içermelidir.",
            error_code="IDEA_PERSISTENCE_INCONSISTENT",
        )
    if "metrics" in cov:
        validate_idea_attempt_metrics(cov["metrics"])

    # 1. request doğrulaması
    req = cov.get("request")
    if not isinstance(req, dict) or set(req.keys()) != {"category_ids", "ideas_per_category"}:
        raise SocialIdeaPersistenceError(
            "Coverage request bloğu tam olarak category_ids ve ideas_per_category alanlarını içermelidir.",
            error_code="IDEA_PERSISTENCE_INCONSISTENT",
        )

    req_cat_ids = req.get("category_ids")
    if not isinstance(req_cat_ids, list) or len(req_cat_ids) < 1 or len(req_cat_ids) > 6:
        raise SocialIdeaPersistenceError(
            "Coverage request category_ids 1-6 arası öğe içeren bir liste olmalıdır.",
            error_code="IDEA_PERSISTENCE_INCONSISTENT",
        )

    seen_req_cats = set()
    for cid in req_cat_ids:
        if isinstance(cid, bool) or not isinstance(cid, int) or cid <= 0:
            raise SocialIdeaPersistenceError(
                "Coverage request category_ids pozitif tamsayılardan oluşmalıdır.",
                error_code="IDEA_PERSISTENCE_INCONSISTENT",
            )
        if cid in seen_req_cats:
            raise SocialIdeaPersistenceError(
                "Coverage request category_ids içinde mükerrer kategori ID tespit edildi.",
                error_code="IDEA_PERSISTENCE_INCONSISTENT",
            )
        seen_req_cats.add(cid)

    ideas_per_cat = req.get("ideas_per_category")
    if isinstance(ideas_per_cat, bool) or not isinstance(ideas_per_cat, int) or not (1 <= ideas_per_cat <= 5):
        raise SocialIdeaPersistenceError(
            "Coverage request ideas_per_category 1-5 arası tamsayı olmalıdır.",
            error_code="IDEA_PERSISTENCE_INCONSISTENT",
        )

    # 2. attempt.requested_target_ids strict doğrulama
    req_targets = attempt.requested_target_ids
    if not isinstance(req_targets, (list, tuple)) or len(req_targets) < 1 or len(req_targets) > 6:
        raise SocialIdeaPersistenceError(
            "attempt.requested_target_ids 1-6 arası hedef ID'si içermelidir.",
            error_code="IDEA_PERSISTENCE_INCONSISTENT",
        )

    seen_req_targets = set()
    sanitized_req_targets: list[int] = []
    for tid in req_targets:
        if isinstance(tid, bool) or not isinstance(tid, int) or tid <= 0:
            raise SocialIdeaPersistenceError(
                "attempt.requested_target_ids pozitif tamsayılar içermelidir.",
                error_code="IDEA_PERSISTENCE_INCONSISTENT",
            )
        if tid in seen_req_targets:
            raise SocialIdeaPersistenceError(
                "attempt.requested_target_ids içinde mükerrer hedef ID tespit edildi.",
                error_code="IDEA_PERSISTENCE_INCONSISTENT",
            )
        seen_req_targets.add(tid)
        sanitized_req_targets.append(tid)

    # 3. Kanonik planı yeniden hesapla
    try:
        canonical_plan = build_social_idea_generation_plan(
            selected_category_ids=tuple(req_cat_ids),
            target_ids=tuple(sanitized_req_targets),
            ideas_per_category=ideas_per_cat,
        )
    except Exception:
        raise SocialIdeaPersistenceError(
            "Kanonik plan yeniden hesaplaması başarısız oldu.",
            error_code="IDEA_PERSISTENCE_INCONSISTENT",
        )

    # 4. coverage.plan bloğunu kanonik plan ile birebir doğrula
    plan_dict = cov.get("plan")
    if not isinstance(plan_dict, dict) or set(plan_dict.keys()) != {"total_requested", "categories"}:
        raise SocialIdeaPersistenceError(
            "Coverage plan bloğu total_requested ve categories alanlarını içermelidir.",
            error_code="IDEA_PERSISTENCE_INCONSISTENT",
        )

    if plan_dict.get("total_requested") != canonical_plan.total_requested:
        raise SocialIdeaPersistenceError(
            "Coverage plan total_requested kanonik plan ile uyuşmuyor.",
            error_code="IDEA_PERSISTENCE_INCONSISTENT",
        )

    raw_categories = plan_dict.get("categories")
    if not isinstance(raw_categories, list) or len(raw_categories) != len(canonical_plan.category_plans):
        raise SocialIdeaPersistenceError(
            "Coverage plan categories adedi kanonik plan ile uyuşmuyor.",
            error_code="IDEA_PERSISTENCE_INCONSISTENT",
        )

    cat_plans: list[_ReconstructedCategoryPlan] = []
    for cp_raw, cp_canon in zip(raw_categories, canonical_plan.category_plans):
        if not isinstance(cp_raw, dict) or set(cp_raw.keys()) != {"category_id", "requested_count", "targets"}:
            raise SocialIdeaPersistenceError(
                "Coverage plan category öğesi şeması geçersiz.",
                error_code="IDEA_PERSISTENCE_INCONSISTENT",
            )

        # Kategori sırası kanonik plan ve request ile birebir eşleşmeli
        if cp_raw.get("category_id") != cp_canon.category_id:
            raise SocialIdeaPersistenceError(
                "Coverage plan category_id sırası request ve kanonik plan ile uyuşmuyor.",
                error_code="IDEA_PERSISTENCE_INCONSISTENT",
            )

        if cp_raw.get("requested_count") != cp_canon.requested_count:
            raise SocialIdeaPersistenceError(
                "Coverage plan requested_count kanonik plan ile uyuşmuyor.",
                error_code="IDEA_PERSISTENCE_INCONSISTENT",
            )

        raw_targets = cp_raw.get("targets")
        if not isinstance(raw_targets, list):
            raise SocialIdeaPersistenceError(
                "Coverage targets listesi geçersiz.",
                error_code="IDEA_PERSISTENCE_INCONSISTENT",
            )

        canon_targets = [
            {"target_id": tq.target_id, "requested_count": tq.requested_count}
            for tq in cp_canon.target_quotas
            if tq.requested_count > 0
        ]
        if raw_targets != canon_targets:
            raise SocialIdeaPersistenceError(
                "Coverage plan hedef kotaları kanonik plan ile birebir uyuşmuyor.",
                error_code="IDEA_PERSISTENCE_INCONSISTENT",
            )

        quotas = tuple((t["target_id"], t["requested_count"]) for t in raw_targets)
        cat_plans.append(
            _ReconstructedCategoryPlan(
                category_id=cp_canon.category_id,
                requested_count=cp_canon.requested_count,
                target_quotas=quotas,
            )
        )

    # 5. generated bloğu genel şema kontrolü
    gen_dict = cov.get("generated")
    if not isinstance(gen_dict, dict) or set(gen_dict.keys()) != {"total_accepted", "target_ids"}:
        raise SocialIdeaPersistenceError(
            "Coverage generated bloğu total_accepted ve target_ids alanlarını içermelidir.",
            error_code="IDEA_PERSISTENCE_INCONSISTENT",
        )

    return _ReconstructedPlan(
        total_requested=canonical_plan.total_requested,
        categories=tuple(cat_plans),
        covered_target_ids=canonical_plan.covered_target_ids,
    )


_extract_plan_from_attempt = extract_social_idea_plan_snapshot


def _first_planned_category_by_target(plan: SocialIdeaPlanSnapshot) -> dict[int, int]:
    """Her hedef için planda pozitif kotası olan İLK kategori (tamamlama kategorisi)."""
    mapping: dict[int, int] = {}
    for cp in plan.categories:
        for tid, count in cp.target_quotas:
            if count > 0 and tid not in mapping:
                mapping[tid] = cp.category_id
    return mapping


def _first_planned_target_of_category(cp: Any) -> int | None:
    """Kategorinin plandaki ilk pozitif kotalı hedefi (boş kategori tamamlama hedefi)."""
    for tid, count in cp.target_quotas:
        if count > 0:
            return tid
    return None


def build_idea_coverage_outcome(
    plan: SocialIdeaPlanSnapshot,
    accepted_pairs: dict[tuple[int, int], int],
) -> tuple[str, str | None, list[dict[str, Any]], tuple[int, ...], tuple[int, ...], tuple[int, ...]]:
    """Kabul edilen (kategori, hedef) sayılarından K4 kapsama sonucunu hesaplar.

    K4: her hedef tüm fikir kümesinde >= 1, her seçilen kategori >= 1 fikir.
    Kategori x hedef kotası garanti DEĞİLDİR (kota altı hücre uyarı üretmez).

    Dönüş: (status, reason_code, warnings, covered_target_ids,
            unfilled_target_ids, unfilled_category_ids)
    - status: 'completed' (K4 sağlandı) | 'partial' (en az 1 fikir var, K4 eksik)
    - warnings: [{target_id, category_id?, reason_code}]
        * brief genelinde hiç fikir almamış hedef -> target_unfilled
          (category_id = hedefin planlandığı/tamamlandığı kategori)
        * hiç fikir alamamış kategori -> kategori başına TEK category_unfilled
          (target_id = kategorinin plandaki ilk pozitif kotalı hedefi; tamamlama
          ve tekrar dene bu hedefi ister). Eski kayıtlarda bunun yerine kategorinin
          planlı her hedefi için ai_failed bulunabilir (tarihsel; okuma kabul eder).
    """
    target_order = list(plan.covered_target_ids)
    per_target: dict[int, int] = {tid: 0 for tid in target_order}
    per_category: dict[int, int] = {cp.category_id: 0 for cp in plan.categories}
    for (cid, tid), cnt in accepted_pairs.items():
        per_target[tid] = per_target.get(tid, 0) + cnt
        per_category[cid] = per_category.get(cid, 0) + cnt

    covered = tuple(tid for tid in target_order if per_target.get(tid, 0) > 0)
    unfilled_targets = tuple(tid for tid in target_order if per_target.get(tid, 0) == 0)
    unfilled_categories = tuple(
        cp.category_id for cp in plan.categories if per_category.get(cp.category_id, 0) == 0
    )

    topup_category = _first_planned_category_by_target(plan)
    warnings: list[dict[str, Any]] = []
    for tid in unfilled_targets:
        item: dict[str, Any] = {"target_id": tid, "reason_code": "target_unfilled"}
        if tid in topup_category:
            item["category_id"] = topup_category[tid]
        warnings.append(item)
    for cp in plan.categories:
        if cp.category_id not in unfilled_categories:
            continue
        first_tid = _first_planned_target_of_category(cp)
        if first_tid is not None:
            warnings.append(
                {
                    "target_id": first_tid,
                    "category_id": cp.category_id,
                    "reason_code": "category_unfilled",
                }
            )

    if not unfilled_targets and not unfilled_categories:
        return "completed", None, [], covered, (), ()
    reason = "target_unfilled" if unfilled_targets else "category_unfilled"
    return "partial", reason, warnings, covered, unfilled_targets, unfilled_categories


def persist_social_ideas(
    db: Session,
    *,
    attempt_id: int,
    task_id: str,
    category_results: tuple[SocialIdeaAIResult, ...],
    metrics: dict[str, int] | None = None,
    now: datetime | None = None,
) -> PersistedSocialIdeasResult:
    """Doğrulanmış fikirleri atomik olarak kaydeder ve attempt'i completed/partial yapar.

    Plan §3.4–§3.6 + K4/K7:
    - Her kategori sonucu en fazla bir kez ve plan sırasıyla gelir; AI'ı başarısız
      olan kategorinin sonucu hiç gelmez (o kategorinin hedefleri eksik kalır).
    - Bir (kategori, hedef) hücresi plan kotasını AŞAMAZ; kota altı serbesttir.
    - K4 sağlanırsa 'completed', aksi halde (>= 1 fikir ile) 'partial' +
      target_unfilled / category_unfilled uyarıları. Hiç fikir yoksa kayıt yapılmaz
      (hata; çağıran denemeyi failed yapar — sahte yedek yok).
    - Ölçüm sayaçları coverage["metrics"] altına yazılır.

    Transaction sözleşmesi:
    - db.begin(), commit() veya rollback() ÇAĞIRMAZ.
    - Başarı sonunda db.flush() çağrılır.
    - Transaction yönetimi tamamen çağıran katmana aittir.

    İdempotency:
    - Attempt zaten completed ise mevcut geçerli fikirleri doğrular ve
      already_completed=True döner; yeni satır eklemez.
    """
    current_time = _validate_now(now)

    # 1. Girdi container tipleri
    if not isinstance(task_id, str) or not task_id.strip():
        raise SocialIdeaPersistenceError(
            "task_id boş olmayan bir string olmalıdır.",
            error_code="IDEA_PERSISTENCE_INVALID_INPUT",
            field="task_id",
        )

    if isinstance(attempt_id, bool) or not isinstance(attempt_id, int) or attempt_id <= 0:
        raise SocialIdeaPersistenceError(
            "attempt_id pozitif bir tamsayı olmalıdır.",
            error_code="IDEA_PERSISTENCE_INVALID_INPUT",
            field="attempt_id",
        )

    if type(category_results) is not tuple:
        raise SocialIdeaPersistenceError(
            "category_results tam olarak bir tuple olmalıdır.",
            error_code="IDEA_PERSISTENCE_INVALID_INPUT",
            field="category_results",
        )

    for idx, item in enumerate(category_results):
        if type(item) is not SocialIdeaAIResult:
            raise SocialIdeaPersistenceError(
                "category_results öğeleri SocialIdeaAIResult olmalıdır.",
                error_code="IDEA_PERSISTENCE_INVALID_INPUT",
                field="category_results",
                category_id=getattr(item, "category_id", None) if hasattr(item, "category_id") else None,
            )

    clean_metrics: dict[str, int] = {}
    if metrics is not None:
        try:
            clean_metrics = validate_idea_attempt_metrics(metrics)
        except SocialIdeaPersistenceError as exc:
            raise SocialIdeaPersistenceError(
                exc.message,
                error_code="IDEA_PERSISTENCE_INVALID_INPUT",
                field="metrics",
            ) from None

    # 2. Canonical kilit: ScoringRun -> SocialBrief -> SocialGenerationAttempt
    attempt, brief, scoring_run, already_completed = lock_ideas_attempt_for_finalize(
        db,
        attempt_id=attempt_id,
        task_id=task_id,
        now=current_time,
    )

    # 3. DB otoritesinden gerçek planı çıkar
    plan = _extract_plan_from_attempt(attempt)
    cov_gen = attempt.coverage.get("generated")
    if not isinstance(cov_gen, dict) or set(cov_gen.keys()) != {"total_accepted", "target_ids"}:
        raise SocialIdeaPersistenceError(
            "Coverage generated bloğu şeması geçersiz.",
            error_code="IDEA_PERSISTENCE_INCONSISTENT",
        )

    expected_cat_target_quotas: dict[tuple[int, int], int] = {
        (cp.category_id, tid): count
        for cp in plan.categories
        for tid, count in cp.target_quotas
    }
    plan_cat_ids = {cp.category_id for cp in plan.categories}
    plan_target_ids = set(plan.covered_target_ids)

    # 4. Zaten tamamlanmış attempt yolu (Completed Replay)
    if already_completed:
        # Not: completed replay'de category_results yok sayılır (geç gelen worker'ın
        # çıktısı yazılmaz); DB'deki kayıtlar doğrulanır.
        # generated bloğu completed kontrolü: completed = tüm hedefler kapsandı (K4)
        total_acc = cov_gen.get("total_accepted")
        if (
            isinstance(total_acc, bool)
            or not isinstance(total_acc, int)
            or total_acc < 1
            or total_acc > plan.total_requested
            or cov_gen.get("target_ids") != list(plan.covered_target_ids)
        ):
            raise SocialIdeaPersistenceError(
                "Completed attempt generated bloğu plan ile tutarsız.",
                error_code="IDEA_PERSISTENCE_INCONSISTENT",
            )

        existing_ideas = (
            db.query(SocialIdea)
            .filter(SocialIdea.brief_id == brief.id)
            .order_by(SocialIdea.id.asc())
            .all()
        )

        if len(existing_ideas) != total_acc:
            raise SocialIdeaPersistenceError(
                "Completed attempt için kayıtlı fikir sayısı generated toplamıyla uyuşmuyor.",
                error_code="IDEA_PERSISTENCE_INCONSISTENT",
            )

        # DB referanslarını topla
        db_cats = {
            c.id: c
            for c in db.query(SocialCategory).filter(SocialCategory.brief_id == brief.id).all()
        }
        db_targets = {
            t.id: t
            for t in db.query(SocialBriefTarget).filter(SocialBriefTarget.brief_id == brief.id).all()
        }
        db_keywords = {
            k.keyword_id
            for k in db.query(SocialBriefKeyword).filter(SocialBriefKeyword.brief_id == brief.id).all()
            if k.keyword_id is not None
        }

        category_target_counts: dict[tuple[int, int], int] = {}
        persisted_existing: list[PersistedSocialIdea] = []

        for idea_row in existing_ideas:
            if idea_row.brief_id != brief.id:
                raise SocialIdeaPersistenceError(
                    "Mevcut fikir farklı brief_id değerine sahip.",
                    error_code="IDEA_PERSISTENCE_INCONSISTENT",
                )
            if idea_row.is_stale is not False:
                raise SocialIdeaPersistenceError(
                    "Mevcut fikir stale durumda.",
                    error_code="IDEA_PERSISTENCE_INCONSISTENT",
                )

            # Kategori doğrulaması
            if idea_row.category_id not in db_cats:
                raise SocialIdeaPersistenceError(
                    "Mevcut fikir brief dışı kategoriye ait.",
                    error_code="IDEA_PERSISTENCE_INCONSISTENT",
                )
            cat = db_cats[idea_row.category_id]
            if cat.scoring_run_id != scoring_run.id:
                raise SocialIdeaPersistenceError(
                    "Mevcut fikir farklı scoring_run kategorisine ait.",
                    error_code="IDEA_PERSISTENCE_INCONSISTENT",
                )
            if cat.is_stale is not False:
                raise SocialIdeaPersistenceError(
                    "Mevcut fikir stale kategoriye ait.",
                    error_code="IDEA_PERSISTENCE_INCONSISTENT",
                )
            if cat.id not in plan_cat_ids:
                raise SocialIdeaPersistenceError(
                    "Mevcut fikir plan dışı kategoriye ait.",
                    error_code="IDEA_PERSISTENCE_INCONSISTENT",
                )

            # Hedef doğrulaması
            if idea_row.brief_target_id not in db_targets:
                raise SocialIdeaPersistenceError(
                    "Mevcut fikir brief dışı hedefe bağlı.",
                    error_code="IDEA_PERSISTENCE_INCONSISTENT",
                )
            target = db_targets[idea_row.brief_target_id]
            if target.id not in plan_target_ids:
                raise SocialIdeaPersistenceError(
                    "Mevcut fikir plan dışı hedefe ait.",
                    error_code="IDEA_PERSISTENCE_INCONSISTENT",
                )
            if get_platform_format(target.platform, target.content_format) is None:
                raise SocialIdeaPersistenceError(
                    "Mevcut fikir hedef platform/format kanonik matriste geçersiz.",
                    error_code="IDEA_PERSISTENCE_INCONSISTENT",
                )
            if idea_row.target_platform != target.platform or idea_row.content_format != target.content_format:
                raise SocialIdeaPersistenceError(
                    "Mevcut fikir hedef platform/format uyuşmazlığına sahip.",
                    error_code="IDEA_PERSISTENCE_INCONSISTENT",
                )

            # Anahtar kelime doğrulaması
            if idea_row.keyword_id not in db_keywords:
                raise SocialIdeaPersistenceError(
                    "Mevcut fikir brief dışı anahtar kelimeye bağlı.",
                    error_code="IDEA_PERSISTENCE_INCONSISTENT",
                )

            # Ortak alan doğrulamaları
            _validate_idea_title(idea_row.idea_title, error_code="IDEA_PERSISTENCE_INCONSISTENT")
            _validate_idea_description(idea_row.idea_description, error_code="IDEA_PERSISTENCE_INCONSISTENT")
            ta = _validate_trend_alignment(idea_row.trend_alignment, error_code="IDEA_PERSISTENCE_INCONSISTENT")

            key = (idea_row.category_id, idea_row.brief_target_id)
            if key not in expected_cat_target_quotas:
                raise SocialIdeaPersistenceError(
                    "Mevcut fikir planda yer almayan kategori-hedef çiftine sahip.",
                    error_code="IDEA_PERSISTENCE_INCONSISTENT",
                )
            category_target_counts[key] = category_target_counts.get(key, 0) + 1
            if category_target_counts[key] > expected_cat_target_quotas[key]:
                raise SocialIdeaPersistenceError(
                    "Mevcut fikirler kategori-hedef plan kotasını aşıyor.",
                    error_code="IDEA_PERSISTENCE_INCONSISTENT",
                )

            persisted_existing.append(
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
                    trend_alignment=ta,
                )
            )

        # Completed = K4 sağlanmış olmalı (her hedef >= 1, her kategori >= 1)
        replay_status, _, _, _, _, _ = build_idea_coverage_outcome(plan, category_target_counts)
        if replay_status != "completed":
            raise SocialIdeaPersistenceError(
                "Completed attempt fikirleri kapsama kuralını (K4) karşılamıyor.",
                error_code="IDEA_PERSISTENCE_INCONSISTENT",
            )

        return PersistedSocialIdeasResult(
            brief_id=brief.id,
            scoring_run_id=scoring_run.id,
            attempt_id=attempt.id,
            total_ideas=len(persisted_existing),
            ideas=tuple(persisted_existing),
            already_completed=True,
            status="completed",
        )

    # 5. Yeni Yazım Yolu:
    # 5a. generated bloğu başlangıç durumunda olmalı
    if (
        isinstance(cov_gen.get("total_accepted"), bool)
        or cov_gen.get("total_accepted") != 0
        or cov_gen.get("target_ids") != []
    ):
        raise SocialIdeaPersistenceError(
            "Running attempt generated bloğu başlangıç durumunda (total_accepted=0, target_ids=[]) olmalıdır.",
            error_code="IDEA_PERSISTENCE_INCONSISTENT",
        )

    # 5b. Brief altında önceden mevcut satır bulunmamalı (yeni yazım öncesi koruma)
    existing_any_count = db.query(SocialIdea).filter(SocialIdea.brief_id == brief.id).count()
    if existing_any_count > 0:
        raise SocialIdeaPersistenceError(
            f"Running attempt altında brief için önceden mevcut {existing_any_count} adet fikir bulundu.",
            error_code="IDEA_PERSISTENCE_INCONSISTENT",
        )

    # 5c. category_results ile plan uyumunu doğrula:
    # - Sonuçlar plan kategorilerinin sıralı bir alt kümesidir (AI'ı başarısız
    #   kategori atlanır), her kategori en fazla bir kez gelir.
    # - Hiç fikir yoksa kayıt yapılmaz (K7: sahte yedek yok).
    if len(category_results) == 0:
        raise SocialIdeaPersistenceError(
            "Kaydedilecek fikir yok; boş sonuç completed/partial yapılamaz.",
            error_code="IDEA_PERSISTENCE_INVALID_INPUT",
            field="category_results",
        )

    plan_order = {cp.category_id: idx for idx, cp in enumerate(plan.categories)}
    last_order = -1
    accepted_pairs: dict[tuple[int, int], int] = {}
    for res in category_results:
        if res.category_id not in plan_order:
            raise SocialIdeaPersistenceError(
                f"Sonuç kategorisi ({res.category_id}) planda yok.",
                error_code="IDEA_PERSISTENCE_INVALID_INPUT",
                category_id=res.category_id,
            )
        if plan_order[res.category_id] <= last_order:
            raise SocialIdeaPersistenceError(
                "Sonuç kategori sırası plan sırasıyla uyuşmuyor veya kategori mükerrer.",
                error_code="IDEA_PERSISTENCE_INVALID_INPUT",
                category_id=res.category_id,
            )
        last_order = plan_order[res.category_id]
        if res.attempt_id != attempt.id:
            raise SocialIdeaPersistenceError(
                f"Sonuç attempt ID ({res.attempt_id}) kilitlenen attempt ({attempt.id}) ile uyuşmuyor.",
                error_code="IDEA_PERSISTENCE_INVALID_INPUT",
                category_id=res.category_id,
            )
        if type(res.ideas) is not tuple or len(res.ideas) == 0:
            raise SocialIdeaPersistenceError(
                f"Kategori {res.category_id} sonucu en az bir fikir içermelidir.",
                error_code="IDEA_PERSISTENCE_INVALID_INPUT",
                category_id=res.category_id,
            )

        for idea in res.ideas:
            if type(idea) is not ValidatedSocialIdea:
                raise SocialIdeaPersistenceError(
                    "Fikir öğesi ValidatedSocialIdea örneği olmalıdır.",
                    error_code="IDEA_PERSISTENCE_INVALID_INPUT",
                    category_id=res.category_id,
                )
            pair = (res.category_id, idea.target_id)
            if pair not in expected_cat_target_quotas:
                raise SocialIdeaPersistenceError(
                    f"Kategori {res.category_id}, hedef {idea.target_id} planda yer almıyor.",
                    error_code="IDEA_PERSISTENCE_INVALID_INPUT",
                    category_id=res.category_id,
                )
            accepted_pairs[pair] = accepted_pairs.get(pair, 0) + 1
            if accepted_pairs[pair] > expected_cat_target_quotas[pair]:
                raise SocialIdeaPersistenceError(
                    f"Kategori {res.category_id}, hedef {idea.target_id} için plan kotası aşıldı.",
                    error_code="IDEA_PERSISTENCE_INVALID_INPUT",
                    category_id=res.category_id,
                )

    # 6. DB referans bütünlüğünü doğrula (categories, targets, keywords)
    db_cats = {
        c.id: c
        for c in db.query(SocialCategory)
        .filter(SocialCategory.brief_id == brief.id, SocialCategory.is_stale.is_(False))
        .all()
    }
    db_targets = {
        t.id: t
        for t in db.query(SocialBriefTarget)
        .filter(SocialBriefTarget.brief_id == brief.id)
        .all()
    }
    db_keywords = {
        k.keyword_id
        for k in db.query(SocialBriefKeyword)
        .filter(SocialBriefKeyword.brief_id == brief.id)
        .all()
        if k.keyword_id is not None
    }

    # Her fikri alan düzeyinde denetle
    validated_idea_rows_data: list[tuple[int, ValidatedSocialIdea, float]] = []

    for res in category_results:
        cat_id = res.category_id
        if cat_id not in db_cats:
            raise SocialIdeaPersistenceError(
                f"Kategori {cat_id} brief altında bulunamadı veya stale durumda.",
                error_code="IDEA_PERSISTENCE_INCONSISTENT",
                category_id=cat_id,
            )
        cat = db_cats[cat_id]
        if cat.scoring_run_id != scoring_run.id:
            raise SocialIdeaPersistenceError(
                f"Kategori {cat_id} scoring_run uyuşmazlığına sahip.",
                error_code="IDEA_PERSISTENCE_INCONSISTENT",
                category_id=cat_id,
            )

        for idea_idx, idea in enumerate(res.ideas):
            # Target kontrolü
            if idea.target_id not in db_targets:
                raise SocialIdeaPersistenceError(
                    f"Hedef {idea.target_id} brief hedefleri arasında bulunamadı.",
                    error_code="IDEA_PERSISTENCE_INCONSISTENT",
                    category_id=cat_id,
                    idea_index=idea_idx,
                )
            target = db_targets[idea.target_id]
            if idea.target_platform != target.platform or idea.content_format != target.content_format:
                raise SocialIdeaPersistenceError(
                    "Fikrin platform veya formatı bağlı hedefle uyuşmuyor.",
                    error_code="IDEA_PERSISTENCE_INCONSISTENT",
                    category_id=cat_id,
                    idea_index=idea_idx,
                )
            if get_platform_format(idea.target_platform, idea.content_format) is None:
                raise SocialIdeaPersistenceError(
                    "Fikrin platform/format kombinasyonu kanonik matriste geçerli değil.",
                    error_code="IDEA_PERSISTENCE_INCONSISTENT",
                    category_id=cat_id,
                    idea_index=idea_idx,
                )

            # Keyword kontrolü
            if idea.primary_keyword_id not in db_keywords:
                raise SocialIdeaPersistenceError(
                    f"Anahtar kelime {idea.primary_keyword_id} brief anahtar kelimeleri arasında bulunamadı.",
                    error_code="IDEA_PERSISTENCE_INCONSISTENT",
                    category_id=cat_id,
                    idea_index=idea_idx,
                )

            # Trend alignment kontrolü (taşma güvenli)
            ta = _validate_trend_alignment(
                idea.trend_alignment,
                error_code="IDEA_PERSISTENCE_INVALID_INPUT",
                category_id=cat_id,
                idea_index=idea_idx,
            )

            # Başlık ve açıklama sınırları
            _validate_idea_title(
                idea.idea_title,
                error_code="IDEA_PERSISTENCE_INVALID_INPUT",
                category_id=cat_id,
                idea_index=idea_idx,
            )
            _validate_idea_description(
                idea.idea_description,
                error_code="IDEA_PERSISTENCE_INVALID_INPUT",
                category_id=cat_id,
                idea_index=idea_idx,
            )

            validated_idea_rows_data.append((cat_id, idea, ta))

    # 7. Kapsama sonucu (K4) — DB planı otoritedir
    (
        status,
        partial_reason,
        warnings_list,
        covered_target_ids,
        unfilled_target_ids,
        unfilled_category_ids,
    ) = build_idea_coverage_outcome(plan, accepted_pairs)

    # 8. Atomik DB eklemesi (tüm kontroller geçtikten sonra)
    persisted_ideas: list[PersistedSocialIdea] = []
    for cat_id, idea, ta in validated_idea_rows_data:
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
        db.flush()  # ID almak için

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

    # 9. Attempt'i finalize et (completed | partial)
    cov = dict(attempt.coverage) if isinstance(attempt.coverage, dict) else {}
    cov["generated"] = {
        "total_accepted": len(persisted_ideas),
        "target_ids": list(covered_target_ids),
    }
    final_metrics = dict(clean_metrics)
    final_metrics["target_unfilled"] = len(unfilled_target_ids)
    final_metrics["category_unfilled"] = len(unfilled_category_ids)
    cov["metrics"] = final_metrics
    attempt.coverage = cov
    attempt.status = status
    if status == "completed":
        attempt.reason_code = None
        attempt.error_message = None
        attempt.warnings = []
    else:
        attempt.reason_code = partial_reason
        attempt.error_message = "Bazı hedefler veya kategoriler için geçerli fikir üretilemedi."
        attempt.warnings = warnings_list
    attempt.completed_at = current_time
    attempt.lease_expires_at = None
    db.flush()

    return PersistedSocialIdeasResult(
        brief_id=brief.id,
        scoring_run_id=scoring_run.id,
        attempt_id=attempt.id,
        total_ideas=len(persisted_ideas),
        ideas=tuple(persisted_ideas),
        already_completed=False,
        status=status,
        unfilled_target_ids=unfilled_target_ids,
        unfilled_category_ids=unfilled_category_ids,
    )
