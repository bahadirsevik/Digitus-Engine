# -*- coding: utf-8 -*-
"""Pure and deterministic idea distribution planner for social brief flow (F1-F.1).

Business rules (plan_social_brief_akisi.md rev.4 §3 & §5):
- User selects >= 1 category from generated categories.
- Brief has 1-6 targets.
- Base ideas per category: 1-5 (default 3).
- Every selected category gets >= 1 idea.
- Every brief target gets >= 1 idea across the full plan.
- No Cartesian coverage (category × target).
- Max total ideas <= 30.
- Total requested: max(len(selected_category_ids) * ideas_per_category, len(target_ids)).
- Deterministic round-robin allocation preserving canonical input tuple ordering.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class IdeaPlanValidationError(ValueError):
    """Fikir üretim planı oluşturulurken ortaya çıkan doğrulama hatası."""

    def __init__(
        self,
        message: str,
        *,
        error_code: str = "IDEA_PLAN_INVALID_INPUT",
        field: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.field = field

    def __repr__(self) -> str:
        return (
            f"IdeaPlanValidationError(error_code={self.error_code!r}, "
            f"message={self.message!r}, field={self.field!r})"
        )


@dataclass(frozen=True)
class IdeaTargetQuota:
    """Belirli bir kategori için hedefe ayrılan fikir sayısı."""

    target_id: int
    requested_count: int


@dataclass(frozen=True)
class IdeaCategoryPlan:
    """Bir kategori için oluşturulan fikir üretim ve hedef kota planı."""

    category_id: int
    requested_count: int
    target_quotas: tuple[IdeaTargetQuota, ...]


@dataclass(frozen=True)
class SocialIdeaGenerationPlan:
    """Tüm seçili kategoriler ve hedefler için nihai fikir üretim planı."""

    total_requested: int
    category_plans: tuple[IdeaCategoryPlan, ...]
    covered_category_ids: tuple[int, ...]
    covered_target_ids: tuple[int, ...]


def _validate_inputs(
    *,
    selected_category_ids: Any,
    target_ids: Any,
    ideas_per_category: Any,
) -> None:
    """Girdileri fail-closed kurallarla doğrular. Dinamik veri hata mesajına sızdırılmaz."""
    # 1. selected_category_ids doğrulaması
    # Önce container tipi
    if type(selected_category_ids) is not tuple:
        raise IdeaPlanValidationError(
            "selected_category_ids must be a tuple.",
            error_code="IDEA_PLAN_INVALID_INPUT",
            field="selected_category_ids",
        )
    # Sonra boşluk / cardinality (1-6)
    if len(selected_category_ids) == 0:
        raise IdeaPlanValidationError(
            "selected_category_ids cannot be empty.",
            error_code="IDEA_PLAN_INVALID_INPUT",
            field="selected_category_ids",
        )
    if len(selected_category_ids) > 6:
        raise IdeaPlanValidationError(
            "selected_category_ids cannot exceed 6 categories.",
            error_code="IDEA_PLAN_INVALID_INPUT",
            field="selected_category_ids",
        )
    # Sonra eleman tipi ve pozitiflik
    for cat_id in selected_category_ids:
        if isinstance(cat_id, bool) or type(cat_id) is not int:
            raise IdeaPlanValidationError(
                "Category ID must be an integer.",
                error_code="IDEA_PLAN_INVALID_INPUT",
                field="selected_category_ids",
            )
        if cat_id <= 0:
            raise IdeaPlanValidationError(
                "Category ID must be positive.",
                error_code="IDEA_PLAN_INVALID_INPUT",
                field="selected_category_ids",
            )
    # Sonra duplicate kontrolü
    if len(set(selected_category_ids)) != len(selected_category_ids):
        raise IdeaPlanValidationError(
            "Duplicate category ID detected.",
            error_code="IDEA_PLAN_DUPLICATE_CATEGORY",
            field="selected_category_ids",
        )

    # 2. target_ids doğrulaması
    # Önce container tipi
    if type(target_ids) is not tuple:
        raise IdeaPlanValidationError(
            "target_ids must be a tuple.",
            error_code="IDEA_PLAN_INVALID_INPUT",
            field="target_ids",
        )
    # Sonra boşluk / cardinality (1-6)
    if len(target_ids) == 0:
        raise IdeaPlanValidationError(
            "target_ids cannot be empty.",
            error_code="IDEA_PLAN_INVALID_INPUT",
            field="target_ids",
        )
    if len(target_ids) > 6:
        raise IdeaPlanValidationError(
            "target_ids cannot exceed 6 targets.",
            error_code="IDEA_PLAN_INVALID_INPUT",
            field="target_ids",
        )
    # Sonra eleman tipi ve pozitiflik
    for t_id in target_ids:
        if isinstance(t_id, bool) or type(t_id) is not int:
            raise IdeaPlanValidationError(
                "Target ID must be an integer.",
                error_code="IDEA_PLAN_INVALID_INPUT",
                field="target_ids",
            )
        if t_id <= 0:
            raise IdeaPlanValidationError(
                "Target ID must be positive.",
                error_code="IDEA_PLAN_INVALID_INPUT",
                field="target_ids",
            )
    # Sonra duplicate kontrolü
    if len(set(target_ids)) != len(target_ids):
        raise IdeaPlanValidationError(
            "Duplicate target ID detected.",
            error_code="IDEA_PLAN_DUPLICATE_TARGET",
            field="target_ids",
        )

    # 3. ideas_per_category doğrulaması
    if isinstance(ideas_per_category, bool) or type(ideas_per_category) is not int:
        raise IdeaPlanValidationError(
            "ideas_per_category must be an integer.",
            error_code="IDEA_PLAN_INVALID_INPUT",
            field="ideas_per_category",
        )
    if ideas_per_category < 1 or ideas_per_category > 5:
        raise IdeaPlanValidationError(
            "ideas_per_category must be between 1 and 5.",
            error_code="IDEA_PLAN_INVALID_INPUT",
            field="ideas_per_category",
        )


def build_social_idea_generation_plan(
    *,
    selected_category_ids: tuple[int, ...],
    target_ids: tuple[int, ...],
    ideas_per_category: int = 3,
) -> SocialIdeaGenerationPlan:
    """Seçili kategoriler ve hedefler için saf ve deterministik fikir üretim planı oluşturur.

    - DB ve AI kullanmaz.
    - Global state değiştirmez.
    - Aynı girdiye daima birebir aynı dondurulmuş (frozen) dataclass çıktısını verir.
    - Girdi tuple sırasını canonical sıra olarak korur.
    """
    _validate_inputs(
        selected_category_ids=selected_category_ids,
        target_ids=target_ids,
        ideas_per_category=ideas_per_category,
    )

    total_requested = max(
        len(selected_category_ids) * ideas_per_category,
        len(target_ids),
    )
    if total_requested > 30:
        raise IdeaPlanValidationError(
            "Total planned ideas exceeds maximum limit of 30.",
            error_code="IDEA_PLAN_LIMIT_EXCEEDED",
            field="total_requested",
        )

    num_cats = len(selected_category_ids)
    num_targets = len(target_ids)

    # Her kategori için hedef kotaları ve toplam fikir sayaçları
    category_target_counts: dict[int, dict[int, int]] = {
        cat_id: {t_id: 0 for t_id in target_ids}
        for cat_id in selected_category_ids
    }
    category_total_counts: dict[int, int] = {
        cat_id: 0 for cat_id in selected_category_ids
    }

    # A. Önce her target tam bir kez kapsansın:
    # - target_ids giriş sırasıyla dolaşılır.
    # - kategorilere round-robin atanır.
    for i, t_id in enumerate(target_ids):
        cat_id = selected_category_ids[i % num_cats]
        category_target_counts[cat_id][t_id] += 1
        category_total_counts[cat_id] += 1

    current_total = num_targets

    # B. Sonra toplam planlanan fikir sayısı hedef toplamına ulaşana kadar:
    # - En az fikri bulunan kategori seçilir.
    # - Eşitlikte selected_category_ids giriş sırası kullanılır.
    # - Target ataması target_ids üzerinde round-robin devam eder.
    target_idx = num_targets % num_targets

    while current_total < total_requested:
        # En az fikri bulunan kategoriyi bul (eşitlikte ilk gelen)
        min_cat_id = selected_category_ids[0]
        min_count = category_total_counts[min_cat_id]
        for cat_id in selected_category_ids:
            if category_total_counts[cat_id] < min_count:
                min_count = category_total_counts[cat_id]
                min_cat_id = cat_id

        t_id = target_ids[target_idx]
        category_target_counts[min_cat_id][t_id] += 1
        category_total_counts[min_cat_id] += 1
        current_total += 1
        target_idx = (target_idx + 1) % num_targets

    # C. Çıktı yapılandırması:
    category_plans: list[IdeaCategoryPlan] = []
    covered_cats: list[int] = []
    covered_targets_set: set[int] = set()

    for cat_id in selected_category_ids:
        t_counts = category_target_counts[cat_id]
        target_quotas: list[IdeaTargetQuota] = []
        cat_requested_count = 0

        for t_id in target_ids:
            cnt = t_counts[t_id]
            if cnt > 0:
                target_quotas.append(
                    IdeaTargetQuota(target_id=t_id, requested_count=cnt)
                )
                cat_requested_count += cnt
                covered_targets_set.add(t_id)

        if cat_requested_count > 0:
            covered_cats.append(cat_id)

        category_plans.append(
            IdeaCategoryPlan(
                category_id=cat_id,
                requested_count=cat_requested_count,
                target_quotas=tuple(target_quotas),
            )
        )

    # Canonical sıra: target_ids giriş sırasına göre
    covered_targets = tuple(t_id for t_id in target_ids if t_id in covered_targets_set)

    return SocialIdeaGenerationPlan(
        total_requested=total_requested,
        category_plans=tuple(category_plans),
        covered_category_ids=tuple(covered_cats),
        covered_target_ids=covered_targets,
    )
