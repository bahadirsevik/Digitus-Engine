# -*- coding: utf-8 -*-
"""K4 kategori kapsaması: saf retry planner + snapshot parser birim testleri.

K4: her seçilen kategori >= 1 fikir VE her hedef >= 1 fikir. Retry planı eksik
hedeflerin yanında boş kategorileri de onarır; eski (kategori alanı olmayan)
snapshot'lar legacy olarak aynen okunur.
"""
from __future__ import annotations

import copy
from typing import Any

import pytest

from app.core.social.idea_planner import (
    IdeaCategoryPlan,
    IdeaTargetQuota,
    SocialIdeaGenerationPlan,
)
from app.core.social.idea_retry_planner import (
    IdeaRetryAssignment,
    IdeaRetryPlanError,
    MAX_RETRY_ASSIGNMENTS,
    build_social_idea_retry_plan,
)
from app.core.social.idea_retry_snapshot import (
    SocialIdeaRetrySnapshotError,
    extract_social_idea_retry_plan_snapshot,
)
from app.database.models import SocialGenerationAttempt


def _plan(categories: list[tuple[int, list[tuple[int, int]]]], targets: tuple[int, ...]):
    """Elle kurulmuş kaynak plan: [(category_id, [(target_id, quota), ...]), ...]."""
    cps = tuple(
        IdeaCategoryPlan(
            category_id=cid,
            requested_count=sum(q for _, q in quotas),
            target_quotas=tuple(IdeaTargetQuota(target_id=t, requested_count=q) for t, q in quotas),
        )
        for cid, quotas in categories
    )
    return SocialIdeaGenerationPlan(
        total_requested=sum(cp.requested_count for cp in cps),
        category_plans=cps,
        covered_category_ids=tuple(cp.category_id for cp in cps),
        covered_target_ids=targets,
    )


# Kategori 10: T1, T2 | Kategori 20: T2, T1 | Kategori 30: T3
PLAN = _plan([(10, [(1, 1), (2, 1)]), (20, [(2, 1), (1, 1)]), (30, [(3, 2)])], (1, 2, 3))
TARGETS = (1, 2, 3)


# ==================== PLANNER ====================


def test_missing_target_merged_into_empty_category_that_plans_it():
    """Eksik hedef onu planlayan BOŞ kategoriye verilir: tek fikir iki boşluğu kapatır."""
    plan = build_social_idea_retry_plan(
        source_plan=PLAN,
        canonical_target_ids=TARGETS,
        persisted_target_ids=(1, 3),
        persisted_category_ids=(10, 30),  # 20 boş
    )
    assert plan.missing_target_ids == (2,)
    assert plan.empty_category_ids == (20,)
    # T2'yi planlayan ilk kategori 10'dur ama 20 boş olduğu için 20 seçilir
    assert plan.assignments == (IdeaRetryAssignment(category_id=20, target_id=2),)
    assert plan.total_requested == 1
    assert plan.requested_target_ids == (2,)


def test_missing_target_and_separate_empty_category():
    """Eksik hedef dolu kategoride, ayrı boş kategori kendi ilk hedefiyle istenir."""
    plan = build_social_idea_retry_plan(
        source_plan=PLAN,
        canonical_target_ids=TARGETS,
        persisted_target_ids=(1, 2),
        persisted_category_ids=(10, 30),  # 20 boş; T3 yalnız 30'da planlı
    )
    assert plan.missing_target_ids == (3,)
    assert plan.empty_category_ids == (20,)
    assert plan.assignments == (
        IdeaRetryAssignment(category_id=30, target_id=3),
        IdeaRetryAssignment(category_id=20, target_id=2),  # 20'nin ilk planlı hedefi
    )
    assert plan.total_requested == 2
    assert plan.requested_target_ids == (2, 3)


def test_all_targets_covered_but_category_empty_is_needed():
    """Tüm hedefler dolu ama bir kategori boş -> NOT_NEEDED DEĞİL (K4)."""
    plan = build_social_idea_retry_plan(
        source_plan=PLAN,
        canonical_target_ids=TARGETS,
        persisted_target_ids=(1, 2, 3),
        persisted_category_ids=(10, 30),
    )
    assert plan.missing_target_ids == ()
    assert plan.empty_category_ids == (20,)
    assert plan.assignments == (IdeaRetryAssignment(category_id=20, target_id=2),)


def test_nothing_missing_raises_not_needed():
    with pytest.raises(IdeaRetryPlanError) as exc:
        build_social_idea_retry_plan(
            source_plan=PLAN,
            canonical_target_ids=TARGETS,
            persisted_target_ids=(1, 2, 3),
            persisted_category_ids=(10, 20, 30),
        )
    assert exc.value.error_code == "IDEA_RETRY_NOT_NEEDED"


def test_legacy_mode_reproduces_missing_target_only_plan():
    """persisted_category_ids=None (legacy): kategori boşluğu yok sayılır, eski plan aynen."""
    legacy = build_social_idea_retry_plan(
        source_plan=PLAN, canonical_target_ids=TARGETS, persisted_target_ids=(1, 3)
    )
    assert legacy.empty_category_ids == ()
    assert legacy.assignments == (IdeaRetryAssignment(category_id=10, target_id=2),)
    with pytest.raises(IdeaRetryPlanError) as exc:
        build_social_idea_retry_plan(
            source_plan=PLAN, canonical_target_ids=TARGETS, persisted_target_ids=(1, 2, 3)
        )
    assert exc.value.error_code == "IDEA_RETRY_NOT_NEEDED"


def test_spreads_missing_targets_over_unassigned_empty_categories():
    """Hiç fikir yokken eksik hedefler talep almamış boş kategorilere yayılır; fazladan
    fikir istenmez (hedef sayısı kadar atama, her kategori tek istek)."""
    plan = build_social_idea_retry_plan(
        source_plan=PLAN,
        canonical_target_ids=TARGETS,
        persisted_target_ids=(),
        persisted_category_ids=(),
    )
    assert plan.missing_target_ids == (1, 2, 3)
    assert plan.empty_category_ids == (10, 20, 30)
    assert plan.assignments == (
        IdeaRetryAssignment(category_id=10, target_id=1),
        IdeaRetryAssignment(category_id=20, target_id=2),
        IdeaRetryAssignment(category_id=30, target_id=3),
    )


def test_worst_case_bounds_one_request_per_category():
    """6 kategori x 6 hedef, hiçbir şey dolu değil: atama <= 12, kategori başına tek istek."""
    targets = (1, 2, 3, 4, 5, 6)
    cats = [(100 + i, [(targets[i], 1)]) for i in range(6)]
    plan = build_social_idea_retry_plan(
        source_plan=_plan(cats, targets),
        canonical_target_ids=targets,
        persisted_target_ids=(),
        persisted_category_ids=(),
    )
    assert plan.total_requested <= MAX_RETRY_ASSIGNMENTS
    assert len({a.category_id for a in plan.assignments}) <= 6
    assert plan.total_requested == 6


def test_empty_category_may_request_already_covered_target_and_duplicates_pairs_never():
    """İki boş kategori aynı ilk hedefi planlıyorsa iki ayrı (kategori, hedef) çifti oluşur."""
    plan_src = _plan([(10, [(1, 1)]), (20, [(1, 1)]), (30, [(1, 1), (2, 1)])], (1, 2))
    plan = build_social_idea_retry_plan(
        source_plan=plan_src,
        canonical_target_ids=(1, 2),
        persisted_target_ids=(2,),
        persisted_category_ids=(30,),
    )
    assert plan.missing_target_ids == (1,)
    assert plan.empty_category_ids == (10, 20)
    assert plan.assignments == (
        IdeaRetryAssignment(category_id=10, target_id=1),
        IdeaRetryAssignment(category_id=20, target_id=1),
    )
    assert plan.requested_target_ids == (1,)
    assert len(set(plan.assignment_pairs)) == len(plan.assignment_pairs)


def test_persisted_category_ids_validation():
    for bad in ([10], (10, 10), (999,), (True,), (0,)):
        with pytest.raises(IdeaRetryPlanError):
            build_social_idea_retry_plan(
                source_plan=PLAN,
                canonical_target_ids=TARGETS,
                persisted_target_ids=(1,),
                persisted_category_ids=bad,  # type: ignore[arg-type]
            )


# ==================== SNAPSHOT ====================


def _coverage(plan_src, *, persisted_targets, persisted_cats, generated_pairs=(), legacy=False):
    retry_plan = build_social_idea_retry_plan(
        source_plan=plan_src,
        canonical_target_ids=TARGETS,
        persisted_target_ids=persisted_targets,
        persisted_category_ids=None if legacy else persisted_cats,
    )
    gen_targets = [t for t in TARGETS if any(p[1] == t for p in generated_pairs)]
    cov: dict[str, Any] = {
        "schema_version": "ideas_retry_plan_v1",
        "request": {"source_attempt_id": 42},
        "baseline": {
            "canonical_target_ids": list(TARGETS),
            "persisted_target_ids_at_start": list(persisted_targets),
        },
        "plan": {
            "total_requested": retry_plan.total_requested,
            "missing_target_ids": list(retry_plan.missing_target_ids),
            "assignments": [
                {"category_id": a.category_id, "target_id": a.target_id, "requested_count": 1}
                for a in retry_plan.assignments
            ],
        },
        "generated": {"total_accepted": len(generated_pairs), "target_ids": gen_targets},
    }
    if not legacy:
        cov["baseline"]["persisted_category_ids_at_start"] = list(persisted_cats)
        cov["plan"]["empty_category_ids"] = list(retry_plan.empty_category_ids)
        cov["generated"]["assignments"] = [
            {"category_id": c, "target_id": t} for c, t in generated_pairs
        ]
    return cov, retry_plan


def _attempt(cov, requested_target_ids):
    return SocialGenerationAttempt(
        id=100,
        brief_id=1,
        stage="ideas_retry",
        status="pending",
        coverage=cov,
        requested_target_ids=list(requested_target_ids),
    )


def test_old_snapshot_without_category_fields_loads_as_legacy():
    """Alan eklenmeden önce yazılmış snapshot (kategori alanları yok) okunmaya devam eder."""
    cov, retry_plan = _coverage(
        PLAN, persisted_targets=(1, 3), persisted_cats=(), legacy=True,
        generated_pairs=((10, 2),),
    )
    assert "persisted_category_ids_at_start" not in cov["baseline"]
    snap = extract_social_idea_retry_plan_snapshot(
        _attempt(cov, retry_plan.missing_target_ids),
        source_plan=PLAN,
        expected_canonical_target_ids=TARGETS,
        expected_source_attempt_id=42,
    )
    assert snap.is_legacy is True
    assert snap.persisted_category_ids_at_start is None
    assert snap.empty_category_ids == ()
    assert snap.plan.assignments == (IdeaRetryAssignment(category_id=10, target_id=2),)
    assert snap.generated_pairs == ((10, 2),)
    assert snap.unfilled_target_ids == ()
    assert snap.unfilled_category_ids == ()


def test_new_snapshot_round_trip_with_category_repair():
    cov, retry_plan = _coverage(
        PLAN, persisted_targets=(1, 2, 3), persisted_cats=(10, 30), generated_pairs=(),
    )
    snap = extract_social_idea_retry_plan_snapshot(
        _attempt(cov, retry_plan.requested_target_ids),
        source_plan=PLAN,
        expected_canonical_target_ids=TARGETS,
        expected_source_attempt_id=42,
    )
    assert snap.is_legacy is False
    assert snap.missing_target_ids == ()
    assert snap.empty_category_ids == (20,)
    assert snap.unfilled_category_ids == (20,)
    assert snap.requested_target_ids == (2,)
    assert snap.plan.assignments == (IdeaRetryAssignment(category_id=20, target_id=2),)


def test_new_snapshot_generated_pairs_parsed():
    cov, retry_plan = _coverage(
        PLAN, persisted_targets=(1, 2), persisted_cats=(10, 30),
        generated_pairs=((20, 2),),
    )
    snap = extract_social_idea_retry_plan_snapshot(
        _attempt(cov, retry_plan.requested_target_ids),
        source_plan=PLAN,
        expected_canonical_target_ids=TARGETS,
    )
    assert snap.generated_pairs == ((20, 2),)
    assert snap.unfilled_category_ids == ()
    assert snap.unfilled_target_ids == (3,)


@pytest.mark.parametrize("drop", ["baseline", "plan", "generated"])
def test_mixed_snapshot_with_partial_category_fields_rejected(drop):
    cov, retry_plan = _coverage(PLAN, persisted_targets=(1, 2, 3), persisted_cats=(10, 30))
    bad = copy.deepcopy(cov)
    if drop == "baseline":
        del bad["baseline"]["persisted_category_ids_at_start"]
    elif drop == "plan":
        del bad["plan"]["empty_category_ids"]
    else:
        del bad["generated"]["assignments"]
    with pytest.raises(SocialIdeaRetrySnapshotError):
        extract_social_idea_retry_plan_snapshot(
            _attempt(bad, retry_plan.requested_target_ids),
            source_plan=PLAN,
            expected_canonical_target_ids=TARGETS,
        )


def test_generated_pair_outside_plan_rejected():
    cov, retry_plan = _coverage(PLAN, persisted_targets=(1, 2, 3), persisted_cats=(10, 30))
    cov["generated"] = {
        "total_accepted": 1,
        "target_ids": [2],
        "assignments": [{"category_id": 10, "target_id": 2}],  # plan çifti (20, 2)
    }
    with pytest.raises(SocialIdeaRetrySnapshotError):
        extract_social_idea_retry_plan_snapshot(
            _attempt(cov, retry_plan.requested_target_ids),
            source_plan=PLAN,
            expected_canonical_target_ids=TARGETS,
        )
