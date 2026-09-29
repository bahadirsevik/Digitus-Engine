# -*- coding: utf-8 -*-
"""Pure and deterministic idea retry planner for social brief flow (F1-F.7.1).

Business rules (plan_social_brief_akisi.md rev.4 §3, §5, §6 + K4):
- Pure domain contract independent of DB, API, AI and Celery.
- K4: every canonical target >= 1 non-stale idea AND every plan category >= 1 idea.
  Retry repairs both gaps; satisfied targets/categories are not replanned.
- Quota deficiencies are not topped up.
- Canonical target IDs tuple (1-6 targets) defines the authoritative target universe and ordering.
- Persisted target IDs must be a valid subset of canonical targets.
- Missing targets preserve canonical_target_ids ordering.
- Exactly 1 retry idea is planned per (category, target) assignment (requested_count == 1).
- Missing target assignment: among categories planning it with positive quota (plan
  order): first an EMPTY category that has no assignment yet (one idea fixes both
  gaps), else any EMPTY category (merges into its request), else the first one.
- Empty category assignment: an empty category without an assignment from the
  step above gets 1 idea for its first positive-quota target (plan order), even if
  that target is already covered elsewhere.
- Assignment order: missing-target assignments (canonical target order), then
  empty-category assignments (plan category order).
- persisted_category_ids=None is LEGACY mode (snapshots written before category
  coverage existed): no category is considered empty, i.e. the original
  missing-target-only plan is reproduced exactly.
- If all targets (and, outside legacy mode, all categories) are satisfied:
  raises IDEA_RETRY_NOT_NEEDED.
- Total requested <= len(missing) + len(empty categories) <= 12; one AI request per
  category (<= 6), so the retry call budget is unchanged.
- Inputs are immutable and never mutated; deterministic output.
- Error messages never expose dynamic IDs or user inputs.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.social.idea_planner import (
    IdeaCategoryPlan,
    IdeaTargetQuota,
    SocialIdeaGenerationPlan,
)


# En fazla 6 eksik hedef + 6 boş kategori ataması.
MAX_RETRY_ASSIGNMENTS = 12


class IdeaRetryPlanError(ValueError):
    """Fikir tekrar deneme planı oluşturulurken ortaya çıkan doğrulama hatası."""

    def __init__(
        self,
        message: str,
        *,
        error_code: str = "IDEA_RETRY_PLAN_INVALID_INPUT",
        field: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.field = field

    def __repr__(self) -> str:
        return (
            f"IdeaRetryPlanError(error_code={self.error_code!r}, "
            f"message={self.message!r}, field={self.field!r})"
        )


@dataclass(frozen=True)
class IdeaRetryAssignment:
    """Tek bir eksik hedef için atanan kategori ve kota bilgisi."""

    category_id: int
    target_id: int
    requested_count: int = 1


@dataclass(frozen=True)
class SocialIdeaRetryPlan:
    """Eksik hedefler ve boş kategoriler için saf, deterministik fikir tekrar deneme planı."""

    total_requested: int
    missing_target_ids: tuple[int, ...]
    assignments: tuple[IdeaRetryAssignment, ...]
    # Başlangıçta hiç non-stale fikri olmayan plan kategorileri (plan sırası).
    # Legacy (kategori bilgisi olmayan) snapshot'larda daima boş.
    empty_category_ids: tuple[int, ...] = ()

    @property
    def requested_target_ids(self) -> tuple[int, ...]:
        """Atamalardaki benzersiz hedefler, artan ID sırasıyla.

        attempt.requested_target_ids ile birebir karşılaştırılır; attempt_state bu
        listeyi artan sıraya normalize eder (kanonik hedef sırası da SocialBriefTarget.id
        ASC'dir). Legacy planda missing_target_ids ile birebir aynıdır.
        """
        return tuple(sorted({a.target_id for a in self.assignments}))

    @property
    def assignment_pairs(self) -> tuple[tuple[int, int], ...]:
        """(category_id, target_id) çiftleri, atama sırasıyla."""
        return tuple((a.category_id, a.target_id) for a in self.assignments)


def _validate_target_tuples(
    *,
    canonical_target_ids: Any,
    persisted_target_ids: Any,
) -> None:
    """Target ID tuple'larını fail-closed kurallarla doğrular. Ham ID sızdırılmaz."""
    # 1. canonical_target_ids doğrulaması
    if type(canonical_target_ids) is not tuple:
        raise IdeaRetryPlanError(
            "canonical_target_ids must be a tuple.",
            error_code="IDEA_RETRY_PLAN_INVALID_INPUT",
            field="canonical_target_ids",
        )
    if len(canonical_target_ids) == 0:
        raise IdeaRetryPlanError(
            "canonical_target_ids cannot be empty.",
            error_code="IDEA_RETRY_PLAN_INVALID_INPUT",
            field="canonical_target_ids",
        )
    if len(canonical_target_ids) > 6:
        raise IdeaRetryPlanError(
            "canonical_target_ids cannot exceed 6 targets.",
            error_code="IDEA_RETRY_PLAN_INVALID_INPUT",
            field="canonical_target_ids",
        )
    for t_id in canonical_target_ids:
        if isinstance(t_id, bool) or type(t_id) is not int:
            raise IdeaRetryPlanError(
                "Target ID in canonical_target_ids must be an integer.",
                error_code="IDEA_RETRY_PLAN_INVALID_INPUT",
                field="canonical_target_ids",
            )
        if t_id <= 0:
            raise IdeaRetryPlanError(
                "Target ID in canonical_target_ids must be positive.",
                error_code="IDEA_RETRY_PLAN_INVALID_INPUT",
                field="canonical_target_ids",
            )
    if len(set(canonical_target_ids)) != len(canonical_target_ids):
        raise IdeaRetryPlanError(
            "Duplicate target ID detected in canonical_target_ids.",
            error_code="IDEA_RETRY_PLAN_INVALID_INPUT",
            field="canonical_target_ids",
        )

    # 2. persisted_target_ids doğrulaması
    if type(persisted_target_ids) is not tuple:
        raise IdeaRetryPlanError(
            "persisted_target_ids must be a tuple.",
            error_code="IDEA_RETRY_PLAN_INVALID_INPUT",
            field="persisted_target_ids",
        )
    for t_id in persisted_target_ids:
        if isinstance(t_id, bool) or type(t_id) is not int:
            raise IdeaRetryPlanError(
                "Target ID in persisted_target_ids must be an integer.",
                error_code="IDEA_RETRY_PLAN_INVALID_INPUT",
                field="persisted_target_ids",
            )
        if t_id <= 0:
            raise IdeaRetryPlanError(
                "Target ID in persisted_target_ids must be positive.",
                error_code="IDEA_RETRY_PLAN_INVALID_INPUT",
                field="persisted_target_ids",
            )
    if len(set(persisted_target_ids)) != len(persisted_target_ids):
        raise IdeaRetryPlanError(
            "Duplicate target ID detected in persisted_target_ids.",
            error_code="IDEA_RETRY_PLAN_INVALID_INPUT",
            field="persisted_target_ids",
        )

    # 3. Kapsam (subset) doğrulaması
    canonical_set = set(canonical_target_ids)
    persisted_set = set(persisted_target_ids)
    if not persisted_set.issubset(canonical_set):
        raise IdeaRetryPlanError(
            "persisted_target_ids must be a subset of canonical_target_ids.",
            error_code="IDEA_RETRY_TARGET_SCOPE_INVALID",
            field="persisted_target_ids",
        )


def _validate_source_plan(
    *,
    source_plan: Any,
    canonical_target_ids: tuple[int, ...],
) -> None:
    """Source plan'ın kanonik hedeflerle tutarlılığını doğrular. Ham ID sızdırılmaz."""
    if not isinstance(source_plan, SocialIdeaGenerationPlan):
        raise IdeaRetryPlanError(
            "source_plan must be an instance of SocialIdeaGenerationPlan.",
            error_code="IDEA_RETRY_SOURCE_PLAN_INVALID",
            field="source_plan",
        )

    # 1. total_requested kontrolleri
    if isinstance(source_plan.total_requested, bool) or type(source_plan.total_requested) is not int:
        raise IdeaRetryPlanError(
            "source_plan total_requested must be an integer.",
            error_code="IDEA_RETRY_SOURCE_PLAN_INVALID",
            field="source_plan",
        )
    if source_plan.total_requested <= 0:
        raise IdeaRetryPlanError(
            "source_plan total_requested must be positive.",
            error_code="IDEA_RETRY_SOURCE_PLAN_INVALID",
            field="source_plan",
        )
    if source_plan.total_requested > 30:
        raise IdeaRetryPlanError(
            "source_plan total_requested cannot exceed 30.",
            error_code="IDEA_RETRY_SOURCE_PLAN_INVALID",
            field="source_plan",
        )

    canonical_set = set(canonical_target_ids)

    # 2. category_plans cardinality (1-6)
    if type(source_plan.category_plans) is not tuple:
        raise IdeaRetryPlanError(
            "source_plan category_plans must be a tuple.",
            error_code="IDEA_RETRY_SOURCE_PLAN_INVALID",
            field="source_plan",
        )
    if len(source_plan.category_plans) == 0:
        raise IdeaRetryPlanError(
            "source_plan category_plans cannot be empty.",
            error_code="IDEA_RETRY_SOURCE_PLAN_INVALID",
            field="source_plan",
        )
    if len(source_plan.category_plans) > 6:
        raise IdeaRetryPlanError(
            "source_plan category_plans cannot exceed 6 categories.",
            error_code="IDEA_RETRY_SOURCE_PLAN_INVALID",
            field="source_plan",
        )

    # 3. category_plans incelemesi (duplicate category_id, requested_count, target_quotas)
    seen_category_ids: set[int] = set()
    all_quota_targets: set[int] = set()
    total_calculated_quota = 0

    for cp in source_plan.category_plans:
        if not isinstance(cp, IdeaCategoryPlan):
            raise IdeaRetryPlanError(
                "source_plan category_plans must contain IdeaCategoryPlan instances.",
                error_code="IDEA_RETRY_SOURCE_PLAN_INVALID",
                field="source_plan",
            )
        if isinstance(cp.category_id, bool) or type(cp.category_id) is not int:
            raise IdeaRetryPlanError(
                "Category ID in source plan must be an integer.",
                error_code="IDEA_RETRY_SOURCE_PLAN_INVALID",
                field="source_plan",
            )
        if cp.category_id <= 0:
            raise IdeaRetryPlanError(
                "Category ID in source plan must be positive.",
                error_code="IDEA_RETRY_SOURCE_PLAN_INVALID",
                field="source_plan",
            )
        if cp.category_id in seen_category_ids:
            raise IdeaRetryPlanError(
                "Duplicate category ID detected in source plan category plans.",
                error_code="IDEA_RETRY_SOURCE_PLAN_INVALID",
                field="source_plan",
            )
        seen_category_ids.add(cp.category_id)

        # 7. Category requested_count
        if isinstance(cp.requested_count, bool) or type(cp.requested_count) is not int:
            raise IdeaRetryPlanError(
                "Category requested_count in source plan must be an integer.",
                error_code="IDEA_RETRY_SOURCE_PLAN_INVALID",
                field="source_plan",
            )
        if cp.requested_count <= 0:
            raise IdeaRetryPlanError(
                "Category requested_count in source plan must be positive.",
                error_code="IDEA_RETRY_SOURCE_PLAN_INVALID",
                field="source_plan",
            )

        # 6. target_quotas yapısı
        if type(cp.target_quotas) is not tuple:
            raise IdeaRetryPlanError(
                "Category target_quotas in source plan must be a tuple.",
                error_code="IDEA_RETRY_SOURCE_PLAN_INVALID",
                field="source_plan",
            )
        if len(cp.target_quotas) == 0:
            raise IdeaRetryPlanError(
                "Category target_quotas in source plan cannot be empty.",
                error_code="IDEA_RETRY_SOURCE_PLAN_INVALID",
                field="source_plan",
            )

        seen_targets_in_cp: set[int] = set()
        quota_sum = 0
        for tq in cp.target_quotas:
            if not isinstance(tq, IdeaTargetQuota):
                raise IdeaRetryPlanError(
                    "target_quotas must contain IdeaTargetQuota instances.",
                    error_code="IDEA_RETRY_SOURCE_PLAN_INVALID",
                    field="source_plan",
                )
            if isinstance(tq.target_id, bool) or type(tq.target_id) is not int:
                raise IdeaRetryPlanError(
                    "Target ID in source plan quota must be an integer.",
                    error_code="IDEA_RETRY_SOURCE_PLAN_INVALID",
                    field="source_plan",
                )
            if tq.target_id <= 0:
                raise IdeaRetryPlanError(
                    "Target ID in source plan quota must be positive.",
                    error_code="IDEA_RETRY_SOURCE_PLAN_INVALID",
                    field="source_plan",
                )
            if tq.target_id not in canonical_set:
                raise IdeaRetryPlanError(
                    "source_plan contains targets outside canonical target scope.",
                    error_code="IDEA_RETRY_SOURCE_PLAN_INVALID",
                    field="source_plan",
                )
            if tq.target_id in seen_targets_in_cp:
                raise IdeaRetryPlanError(
                    "Duplicate target quota detected in category plan.",
                    error_code="IDEA_RETRY_SOURCE_PLAN_INVALID",
                    field="source_plan",
                )
            seen_targets_in_cp.add(tq.target_id)

            if isinstance(tq.requested_count, bool) or type(tq.requested_count) is not int:
                raise IdeaRetryPlanError(
                    "Target quota requested_count in source plan must be an integer.",
                    error_code="IDEA_RETRY_SOURCE_PLAN_INVALID",
                    field="source_plan",
                )
            if tq.requested_count <= 0:
                raise IdeaRetryPlanError(
                    "Target quota requested_count in source plan must be positive.",
                    error_code="IDEA_RETRY_SOURCE_PLAN_INVALID",
                    field="source_plan",
                )
            all_quota_targets.add(tq.target_id)
            quota_sum += tq.requested_count

        if quota_sum != cp.requested_count:
            raise IdeaRetryPlanError(
                "Category requested_count in source plan does not match sum of target quotas.",
                error_code="IDEA_RETRY_SOURCE_PLAN_INVALID",
                field="source_plan",
            )
        total_calculated_quota += quota_sum

    # 4. covered_category_ids tam paritesi
    if type(source_plan.covered_category_ids) is not tuple:
        raise IdeaRetryPlanError(
            "source_plan covered_category_ids must be a tuple.",
            error_code="IDEA_RETRY_SOURCE_PLAN_INVALID",
            field="source_plan",
        )
    seen_covered_cat_ids: set[int] = set()
    for cat_id in source_plan.covered_category_ids:
        if isinstance(cat_id, bool) or type(cat_id) is not int:
            raise IdeaRetryPlanError(
                "Category ID in covered_category_ids must be an integer.",
                error_code="IDEA_RETRY_SOURCE_PLAN_INVALID",
                field="source_plan",
            )
        if cat_id <= 0:
            raise IdeaRetryPlanError(
                "Category ID in covered_category_ids must be positive.",
                error_code="IDEA_RETRY_SOURCE_PLAN_INVALID",
                field="source_plan",
            )
        if cat_id in seen_covered_cat_ids:
            raise IdeaRetryPlanError(
                "Duplicate category ID detected in covered_category_ids.",
                error_code="IDEA_RETRY_SOURCE_PLAN_INVALID",
                field="source_plan",
            )
        seen_covered_cat_ids.add(cat_id)

    expected_category_order = tuple(cp.category_id for cp in source_plan.category_plans)
    if source_plan.covered_category_ids != expected_category_order:
        raise IdeaRetryPlanError(
            "source_plan covered_category_ids must exactly match category plans order and IDs.",
            error_code="IDEA_RETRY_SOURCE_PLAN_INVALID",
            field="source_plan",
        )

    # 5. covered_target_ids tam paritesi
    if type(source_plan.covered_target_ids) is not tuple:
        raise IdeaRetryPlanError(
            "source_plan covered_target_ids must be a tuple.",
            error_code="IDEA_RETRY_SOURCE_PLAN_INVALID",
            field="source_plan",
        )
    seen_covered_target_ids: set[int] = set()
    for t_id in source_plan.covered_target_ids:
        if isinstance(t_id, bool) or type(t_id) is not int:
            raise IdeaRetryPlanError(
                "Target ID in covered_target_ids must be an integer.",
                error_code="IDEA_RETRY_SOURCE_PLAN_INVALID",
                field="source_plan",
            )
        if t_id <= 0:
            raise IdeaRetryPlanError(
                "Target ID in covered_target_ids must be positive.",
                error_code="IDEA_RETRY_SOURCE_PLAN_INVALID",
                field="source_plan",
            )
        if t_id in seen_covered_target_ids:
            raise IdeaRetryPlanError(
                "Duplicate target ID detected in covered_target_ids.",
                error_code="IDEA_RETRY_SOURCE_PLAN_INVALID",
                field="source_plan",
            )
        seen_covered_target_ids.add(t_id)

    if source_plan.covered_target_ids != canonical_target_ids:
        raise IdeaRetryPlanError(
            "source_plan covered_target_ids must exactly match canonical targets and order.",
            error_code="IDEA_RETRY_SOURCE_PLAN_INVALID",
            field="source_plan",
        )

    # 8. Global target kapsamı ve total_requested eşleşmesi
    if all_quota_targets != canonical_set:
        raise IdeaRetryPlanError(
            "source_plan target quotas do not cover all canonical targets.",
            error_code="IDEA_RETRY_SOURCE_PLAN_INVALID",
            field="source_plan",
        )

    if source_plan.total_requested != total_calculated_quota:
        raise IdeaRetryPlanError(
            "source_plan total_requested does not match sum of category quotas.",
            error_code="IDEA_RETRY_SOURCE_PLAN_INVALID",
            field="source_plan",
        )



def _validate_persisted_categories(
    *,
    persisted_category_ids: Any,
    source_plan: SocialIdeaGenerationPlan,
) -> None:
    """persisted_category_ids demetini doğrular (None = legacy mod)."""
    if persisted_category_ids is None:
        return
    if type(persisted_category_ids) is not tuple:
        raise IdeaRetryPlanError(
            "persisted_category_ids must be a tuple or None.",
            error_code="IDEA_RETRY_PLAN_INVALID_INPUT",
            field="persisted_category_ids",
        )
    plan_cat_ids = set(source_plan.covered_category_ids)
    seen: set[int] = set()
    for c_id in persisted_category_ids:
        if isinstance(c_id, bool) or type(c_id) is not int or c_id <= 0:
            raise IdeaRetryPlanError(
                "Category ID in persisted_category_ids must be a positive integer.",
                error_code="IDEA_RETRY_PLAN_INVALID_INPUT",
                field="persisted_category_ids",
            )
        if c_id in seen:
            raise IdeaRetryPlanError(
                "Duplicate category ID detected in persisted_category_ids.",
                error_code="IDEA_RETRY_PLAN_INVALID_INPUT",
                field="persisted_category_ids",
            )
        seen.add(c_id)
        if c_id not in plan_cat_ids:
            raise IdeaRetryPlanError(
                "persisted_category_ids must be a subset of source plan categories.",
                error_code="IDEA_RETRY_PLAN_INVALID_INPUT",
                field="persisted_category_ids",
            )


def build_social_idea_retry_plan(
    *,
    source_plan: SocialIdeaGenerationPlan,
    canonical_target_ids: tuple[int, ...],
    persisted_target_ids: tuple[int, ...],
    persisted_category_ids: tuple[int, ...] | None = None,
) -> SocialIdeaRetryPlan:
    """Eksik hedefler ve boş kategoriler için saf, deterministik tekrar deneme planı kurar.

    - DB, AI, Celery, zaman veya global state kullanmaz.
    - Girdileri kesinlikle mutate etmez.
    - Aynı girdiye karşılık daima deterministik eşit sonuç üretir.
    - persisted_category_ids: en az bir non-stale fikri olan plan kategorileri.
      None = legacy mod (kategori kapsaması bilinmiyor; yalnız eksik hedefler).
    - Hedefler ve (legacy dışında) kategoriler doluysa IDEA_RETRY_NOT_NEEDED fırlatır.
    """
    _validate_target_tuples(
        canonical_target_ids=canonical_target_ids,
        persisted_target_ids=persisted_target_ids,
    )
    _validate_source_plan(
        source_plan=source_plan,
        canonical_target_ids=canonical_target_ids,
    )
    _validate_persisted_categories(
        persisted_category_ids=persisted_category_ids,
        source_plan=source_plan,
    )

    persisted_set = set(persisted_target_ids)
    missing_targets = tuple(
        t_id for t_id in canonical_target_ids if t_id not in persisted_set
    )

    if persisted_category_ids is None:
        empty_categories: tuple[int, ...] = ()
    else:
        persisted_cat_set = set(persisted_category_ids)
        empty_categories = tuple(
            cp.category_id
            for cp in source_plan.category_plans
            if cp.category_id not in persisted_cat_set
        )
    empty_set = set(empty_categories)

    if len(missing_targets) == 0 and len(empty_categories) == 0:
        raise IdeaRetryPlanError(
            "All canonical targets and categories are already satisfied; retry is not needed.",
            error_code="IDEA_RETRY_NOT_NEEDED",
        )

    assignments: list[IdeaRetryAssignment] = []
    seen_assignments: set[tuple[int, int]] = set()
    assigned_categories: set[int] = set()

    # 1. Eksik hedefler (kanonik sıra): planlayan BOŞ kategori öncelikli
    for t_id in missing_targets:
        planners = [
            cp.category_id
            for cp in source_plan.category_plans
            if any(tq.target_id == t_id and tq.requested_count > 0 for tq in cp.target_quotas)
        ]
        if not planners:
            raise IdeaRetryPlanError(
                "Missing target has no category with positive quota in source plan.",
                error_code="IDEA_RETRY_SOURCE_PLAN_INVALID",
            )
        # Öncelik: (1) henüz talep almamış boş kategori, (2) boş kategori (mevcut
        # isteğe birleşir), (3) planlayan ilk kategori.
        assigned_cat_id = next(
            (c for c in planners if c in empty_set and c not in assigned_categories),
            next((c for c in planners if c in empty_set), planners[0]),
        )

        pair = (assigned_cat_id, t_id)
        if pair in seen_assignments:
            raise IdeaRetryPlanError(
                "Duplicate retry assignment generated.",
                error_code="IDEA_RETRY_SOURCE_PLAN_INVALID",
            )
        seen_assignments.add(pair)
        assigned_categories.add(assigned_cat_id)
        assignments.append(
            IdeaRetryAssignment(
                category_id=assigned_cat_id,
                target_id=t_id,
                requested_count=1,
            )
        )

    # 2. Hâlâ ataması olmayan boş kategoriler (plan sırası): ilk pozitif kotalı hedef
    for cp in source_plan.category_plans:
        if cp.category_id not in empty_set or cp.category_id in assigned_categories:
            continue
        first_tq = next((tq for tq in cp.target_quotas if tq.requested_count > 0), None)
        if first_tq is None:
            raise IdeaRetryPlanError(
                "Empty category has no target with positive quota in source plan.",
                error_code="IDEA_RETRY_SOURCE_PLAN_INVALID",
            )
        pair = (cp.category_id, first_tq.target_id)
        if pair in seen_assignments:
            raise IdeaRetryPlanError(
                "Duplicate retry assignment generated.",
                error_code="IDEA_RETRY_SOURCE_PLAN_INVALID",
            )
        seen_assignments.add(pair)
        assigned_categories.add(cp.category_id)
        assignments.append(
            IdeaRetryAssignment(
                category_id=cp.category_id,
                target_id=first_tq.target_id,
                requested_count=1,
            )
        )

    # Sınırlar: atama <= eksik hedef + boş kategori <= 12; kategori başına tek istek
    # (<= 6 kategori) olduğu için retry AI çağrı tavanı değişmez.
    total_requested = len(assignments)
    if (
        total_requested > len(missing_targets) + len(empty_categories)
        or total_requested > MAX_RETRY_ASSIGNMENTS
    ):
        raise IdeaRetryPlanError(
            "Total retry requests exceed maximum retry limit.",
            error_code="IDEA_RETRY_PLAN_INVALID_INPUT",
        )
    if len(assigned_categories) > 6:
        raise IdeaRetryPlanError(
            "Retry plan exceeds one request per category.",
            error_code="IDEA_RETRY_PLAN_INVALID_INPUT",
        )

    return SocialIdeaRetryPlan(
        total_requested=total_requested,
        missing_target_ids=missing_targets,
        assignments=tuple(assignments),
        empty_category_ids=empty_categories,
    )
