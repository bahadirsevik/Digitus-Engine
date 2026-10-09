# -*- coding: utf-8 -*-
"""Unit tests for pure idea retry planner and strict request schema (F1-F.7.1).

Covers:
1. Hiç persisted target yoksa bütün canonical hedefler birer kez planlanır.
2. Bazı hedefler doluysa yalnız eksikler planlanır.
3. Bütün hedefler doluysa IDEA_RETRY_NOT_NEEDED.
4. Canonical sıra korunur.
5. Kategori, source plan’daki ilk pozitif kotadan deterministik seçilir.
6. Aynı target source planda birden fazla kategoriye bağlıysa ilk kategori seçilir.
7. Source plan bir canonical target’ı içermiyorsa fail-closed (IDEA_RETRY_SOURCE_PLAN_INVALID).
8. Source plan kapsam dışı target içeriyorsa fail-closed (IDEA_RETRY_SOURCE_PLAN_INVALID).
9. persisted target canonical küme dışında ise reddedilir (IDEA_RETRY_TARGET_SCOPE_INVALID).
10. Duplicate canonical target reddedilir (IDEA_RETRY_PLAN_INVALID_INPUT).
11. Duplicate persisted target reddedilir (IDEA_RETRY_PLAN_INVALID_INPUT).
12. bool/string/float/negatif/sıfır ID reddedilir (IDEA_RETRY_PLAN_INVALID_INPUT).
13. canonical cardinality 0 ve 7 reddedilir (IDEA_RETRY_PLAN_INVALID_INPUT).
14. persisted tuple boş olabilir.
15. Çıktıda her assignment requested_count == 1.
16. total_requested == len(missing_target_ids) == len(assignments).
17. Girdiler mutate edilmez.
18. Aynı girdide deterministik eşit sonuç alınır.
19. Hata mesajlarında ham ID bulunmaz.
20. Request schema strict davranır (geçerli, whitespace, bool/str/float, extra alan).
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.social import (
    IdeaCategoryPlan,
    IdeaRetryAssignment,
    IdeaRetryPlanError,
    IdeaTargetQuota,
    SocialIdeaGenerationPlan,
    SocialIdeaRetryPlan,
    build_social_idea_generation_plan,
    build_social_idea_retry_plan,
)
from app.schemas.social_brief import SocialBriefIdeasRetryRequest


@pytest.fixture
def sample_source_plan_3targets() -> SocialIdeaGenerationPlan:
    """Canonical targets (101, 102, 103) ve 2 kategori için geçerli source plan."""
    return build_social_idea_generation_plan(
        selected_category_ids=(10, 20),
        target_ids=(101, 102, 103),
        ideas_per_category=3,
    )


def test_01_no_persisted_targets_plans_all_canonical_targets_once(sample_source_plan_3targets):
    """1. Hiç persisted target yoksa bütün canonical hedefler birer kez planlanır."""
    canonical = (101, 102, 103)
    persisted = ()

    plan = build_social_idea_retry_plan(
        source_plan=sample_source_plan_3targets,
        canonical_target_ids=canonical,
        persisted_target_ids=persisted,
    )

    assert isinstance(plan, SocialIdeaRetryPlan)
    assert plan.total_requested == 3
    assert plan.missing_target_ids == (101, 102, 103)
    assert len(plan.assignments) == 3
    assert [a.target_id for a in plan.assignments] == [101, 102, 103]
    assert all(a.requested_count == 1 for a in plan.assignments)


def test_02_partially_persisted_targets_plans_only_missing(sample_source_plan_3targets):
    """2. Bazı hedefler doluysa yalnız eksikler planlanır."""
    canonical = (101, 102, 103)
    persisted = (102,)

    plan = build_social_idea_retry_plan(
        source_plan=sample_source_plan_3targets,
        canonical_target_ids=canonical,
        persisted_target_ids=persisted,
    )

    assert plan.total_requested == 2
    assert plan.missing_target_ids == (101, 103)
    assert len(plan.assignments) == 2
    assert [a.target_id for a in plan.assignments] == [101, 103]
    assert all(a.requested_count == 1 for a in plan.assignments)


def test_03_all_targets_persisted_raises_retry_not_needed(sample_source_plan_3targets):
    """3. Bütün hedefler doluysa IDEA_RETRY_NOT_NEEDED."""
    canonical = (101, 102, 103)
    persisted = (101, 102, 103)

    with pytest.raises(IdeaRetryPlanError) as exc_info:
        build_social_idea_retry_plan(
            source_plan=sample_source_plan_3targets,
            canonical_target_ids=canonical,
            persisted_target_ids=persisted,
        )

    assert exc_info.value.error_code == "IDEA_RETRY_NOT_NEEDED"


def test_04_canonical_ordering_is_strictly_preserved(sample_source_plan_3targets):
    """4. Canonical sıra korunur."""
    # Canonical sıra: 103, 101, 102
    canonical = (103, 101, 102)
    source_plan = build_social_idea_generation_plan(
        selected_category_ids=(10, 20),
        target_ids=canonical,
        ideas_per_category=3,
    )
    persisted = (101,)

    plan = build_social_idea_retry_plan(
        source_plan=source_plan,
        canonical_target_ids=canonical,
        persisted_target_ids=persisted,
    )

    assert plan.missing_target_ids == (103, 102)
    assert [a.target_id for a in plan.assignments] == [103, 102]


def test_05_category_selected_from_first_positive_quota_in_source_plan():
    """5. Kategori, source plan’daki ilk pozitif kotadan deterministik seçilir."""
    # Category 10: target 101 kotası 1, target 102 kotası 0
    # Category 20: target 101 kotası 0, target 102 kotası 1
    cp1 = IdeaCategoryPlan(
        category_id=10,
        requested_count=1,
        target_quotas=(IdeaTargetQuota(target_id=101, requested_count=1),),
    )
    cp2 = IdeaCategoryPlan(
        category_id=20,
        requested_count=1,
        target_quotas=(IdeaTargetQuota(target_id=102, requested_count=1),),
    )
    source_plan = SocialIdeaGenerationPlan(
        total_requested=2,
        category_plans=(cp1, cp2),
        covered_category_ids=(10, 20),
        covered_target_ids=(101, 102),
    )

    plan = build_social_idea_retry_plan(
        source_plan=source_plan,
        canonical_target_ids=(101, 102),
        persisted_target_ids=(),
    )

    assert plan.assignments[0] == IdeaRetryAssignment(category_id=10, target_id=101, requested_count=1)
    assert plan.assignments[1] == IdeaRetryAssignment(category_id=20, target_id=102, requested_count=1)


def test_06_target_in_multiple_categories_picks_first_category():
    """6. Aynı target source planda birden fazla kategoriye bağlıysa ilk kategori seçilir."""
    # Target 101 hem Category 10 hem Category 20'de kota almıştır
    cp1 = IdeaCategoryPlan(
        category_id=10,
        requested_count=2,
        target_quotas=(IdeaTargetQuota(target_id=101, requested_count=2),),
    )
    cp2 = IdeaCategoryPlan(
        category_id=20,
        requested_count=1,
        target_quotas=(IdeaTargetQuota(target_id=101, requested_count=1),),
    )
    source_plan = SocialIdeaGenerationPlan(
        total_requested=3,
        category_plans=(cp1, cp2),
        covered_category_ids=(10, 20),
        covered_target_ids=(101,),
    )

    plan = build_social_idea_retry_plan(
        source_plan=source_plan,
        canonical_target_ids=(101,),
        persisted_target_ids=(),
    )

    assert len(plan.assignments) == 1
    assert plan.assignments[0].category_id == 10
    assert plan.assignments[0].target_id == 101


def test_07_source_plan_missing_canonical_target_raises_fail_closed(sample_source_plan_3targets):
    """7. Source plan bir canonical target’ı içermiyorsa fail-closed."""
    # Canonical has 101, 102, 103, 104, but sample_source_plan_3targets only has 101, 102, 103
    canonical = (101, 102, 103, 104)

    with pytest.raises(IdeaRetryPlanError) as exc_info:
        build_social_idea_retry_plan(
            source_plan=sample_source_plan_3targets,
            canonical_target_ids=canonical,
            persisted_target_ids=(),
        )

    assert exc_info.value.error_code == "IDEA_RETRY_SOURCE_PLAN_INVALID"


def test_08_source_plan_out_of_scope_target_raises_fail_closed(sample_source_plan_3targets):
    """8. Source plan kapsam dışı target içeriyorsa fail-closed."""
    # Canonical has only (101, 102), but sample_source_plan_3targets has 103 as well
    canonical = (101, 102)

    with pytest.raises(IdeaRetryPlanError) as exc_info:
        build_social_idea_retry_plan(
            source_plan=sample_source_plan_3targets,
            canonical_target_ids=canonical,
            persisted_target_ids=(),
        )

    assert exc_info.value.error_code == "IDEA_RETRY_SOURCE_PLAN_INVALID"


def test_09_persisted_target_outside_canonical_scope_raises_scope_error(sample_source_plan_3targets):
    """9. persisted target canonical küme dışında ise reddedilir."""
    canonical = (101, 102, 103)
    persisted = (101, 999)

    with pytest.raises(IdeaRetryPlanError) as exc_info:
        build_social_idea_retry_plan(
            source_plan=sample_source_plan_3targets,
            canonical_target_ids=canonical,
            persisted_target_ids=persisted,
        )

    assert exc_info.value.error_code == "IDEA_RETRY_TARGET_SCOPE_INVALID"


def test_10_duplicate_canonical_target_raises_invalid_input(sample_source_plan_3targets):
    """10. Duplicate canonical target reddedilir."""
    canonical = (101, 101, 102)

    with pytest.raises(IdeaRetryPlanError) as exc_info:
        build_social_idea_retry_plan(
            source_plan=sample_source_plan_3targets,
            canonical_target_ids=canonical,
            persisted_target_ids=(),
        )

    assert exc_info.value.error_code == "IDEA_RETRY_PLAN_INVALID_INPUT"


def test_11_duplicate_persisted_target_raises_invalid_input(sample_source_plan_3targets):
    """11. Duplicate persisted target reddedilir."""
    canonical = (101, 102, 103)
    persisted = (101, 101)

    with pytest.raises(IdeaRetryPlanError) as exc_info:
        build_social_idea_retry_plan(
            source_plan=sample_source_plan_3targets,
            canonical_target_ids=canonical,
            persisted_target_ids=persisted,
        )

    assert exc_info.value.error_code == "IDEA_RETRY_PLAN_INVALID_INPUT"


@pytest.mark.parametrize(
    "invalid_val",
    [True, False, "101", 101.5, 0, -1],
)
def test_12_non_positive_int_id_rejected_in_canonical_and_persisted(sample_source_plan_3targets, invalid_val):
    """12. bool/string/float/negatif/sıfır ID reddedilir."""
    # Test canonical
    with pytest.raises(IdeaRetryPlanError) as exc_info:
        build_social_idea_retry_plan(
            source_plan=sample_source_plan_3targets,
            canonical_target_ids=(101, invalid_val),  # type: ignore
            persisted_target_ids=(),
        )
    assert exc_info.value.error_code == "IDEA_RETRY_PLAN_INVALID_INPUT"

    # Test persisted
    with pytest.raises(IdeaRetryPlanError) as exc_info2:
        build_social_idea_retry_plan(
            source_plan=sample_source_plan_3targets,
            canonical_target_ids=(101, 102, 103),
            persisted_target_ids=(invalid_val,),  # type: ignore
        )
    assert exc_info2.value.error_code == "IDEA_RETRY_PLAN_INVALID_INPUT"


def test_13_canonical_cardinality_zero_and_seven_rejected(sample_source_plan_3targets):
    """13. canonical cardinality 0 ve 7 reddedilir."""
    # 0 targets
    with pytest.raises(IdeaRetryPlanError) as exc_info0:
        build_social_idea_retry_plan(
            source_plan=sample_source_plan_3targets,
            canonical_target_ids=(),
            persisted_target_ids=(),
        )
    assert exc_info0.value.error_code == "IDEA_RETRY_PLAN_INVALID_INPUT"

    # 7 targets
    canonical_7 = (1, 2, 3, 4, 5, 6, 7)
    with pytest.raises(IdeaRetryPlanError) as exc_info7:
        build_social_idea_retry_plan(
            source_plan=sample_source_plan_3targets,
            canonical_target_ids=canonical_7,
            persisted_target_ids=(),
        )
    assert exc_info7.value.error_code == "IDEA_RETRY_PLAN_INVALID_INPUT"


def test_14_persisted_tuple_can_be_empty(sample_source_plan_3targets):
    """14. persisted tuple boş olabilir."""
    plan = build_social_idea_retry_plan(
        source_plan=sample_source_plan_3targets,
        canonical_target_ids=(101, 102, 103),
        persisted_target_ids=(),
    )
    assert plan.total_requested == 3
    assert plan.missing_target_ids == (101, 102, 103)


def test_15_every_assignment_has_requested_count_equal_one(sample_source_plan_3targets):
    """15. Çıktıda her assignment requested_count == 1."""
    plan = build_social_idea_retry_plan(
        source_plan=sample_source_plan_3targets,
        canonical_target_ids=(101, 102, 103),
        persisted_target_ids=(101,),
    )
    for assign in plan.assignments:
        assert assign.requested_count == 1


def test_16_total_requested_equals_missing_and_assignments_len(sample_source_plan_3targets):
    """16. total_requested == len(missing_target_ids) == len(assignments)."""
    plan = build_social_idea_retry_plan(
        source_plan=sample_source_plan_3targets,
        canonical_target_ids=(101, 102, 103),
        persisted_target_ids=(101,),
    )
    assert plan.total_requested == len(plan.missing_target_ids) == len(plan.assignments) == 2


def test_17_inputs_are_not_mutated(sample_source_plan_3targets):
    """17. Girdiler mutate edilmez."""
    canonical = (101, 102, 103)
    persisted = (102,)

    canonical_copy = tuple(canonical)
    persisted_copy = tuple(persisted)

    build_social_idea_retry_plan(
        source_plan=sample_source_plan_3targets,
        canonical_target_ids=canonical,
        persisted_target_ids=persisted,
    )

    assert canonical == canonical_copy
    assert persisted == persisted_copy


def test_18_same_inputs_produce_equal_frozen_outputs(sample_source_plan_3targets):
    """18. Aynı girdide deterministik eşit sonuç alınır."""
    canonical = (101, 102, 103)
    persisted = (103,)

    plan1 = build_social_idea_retry_plan(
        source_plan=sample_source_plan_3targets,
        canonical_target_ids=canonical,
        persisted_target_ids=persisted,
    )
    plan2 = build_social_idea_retry_plan(
        source_plan=sample_source_plan_3targets,
        canonical_target_ids=canonical,
        persisted_target_ids=persisted,
    )

    assert plan1 == plan2
    assert plan1.assignments == plan2.assignments
    assert hash(plan1) == hash(plan2)


def test_19_error_messages_contain_no_raw_ids(sample_source_plan_3targets):
    """19. Hata mesajlarında ham ID bulunmaz."""
    test_id = 987654

    # Target outside scope
    with pytest.raises(IdeaRetryPlanError) as exc_info1:
        build_social_idea_retry_plan(
            source_plan=sample_source_plan_3targets,
            canonical_target_ids=(101, 102, 103),
            persisted_target_ids=(test_id,),
        )
    assert str(test_id) not in str(exc_info1.value.message)

    # Invalid ID type
    with pytest.raises(IdeaRetryPlanError) as exc_info2:
        build_social_idea_retry_plan(
            source_plan=sample_source_plan_3targets,
            canonical_target_ids=(101, 102, test_id),
            persisted_target_ids=(),
        )
    # Even when checking source plan mismatch, ID should not be in message
    assert str(test_id) not in str(exc_info2.value.message)


def test_20_request_schema_strict_validation():
    """20. Request schema strict davranır:

    - geçerli request kabul edilir,
    - whitespace-only key reddedilir,
    - bool/string/float source_attempt_id reddedilir,
    - extra alan reddedilir.
    """
    # Geçerli
    req = SocialBriefIdeasRetryRequest(
        idempotency_key="retry-key-001",
        source_attempt_id=42,
    )
    assert req.idempotency_key == "retry-key-001"
    assert req.source_attempt_id == 42

    # Whitespace-only key
    with pytest.raises(ValidationError):
        SocialBriefIdeasRetryRequest(
            idempotency_key="   ",
            source_attempt_id=42,
        )

    # Empty string key
    with pytest.raises(ValidationError):
        SocialBriefIdeasRetryRequest(
            idempotency_key="",
            source_attempt_id=42,
        )

    # bool source_attempt_id (True / False)
    with pytest.raises(ValidationError):
        SocialBriefIdeasRetryRequest(
            idempotency_key="key-1",
            source_attempt_id=True,  # type: ignore
        )

    # string source_attempt_id
    with pytest.raises(ValidationError):
        SocialBriefIdeasRetryRequest(
            idempotency_key="key-1",
            source_attempt_id="42",  # type: ignore
        )

    # float source_attempt_id
    with pytest.raises(ValidationError):
        SocialBriefIdeasRetryRequest(
            idempotency_key="key-1",
            source_attempt_id=42.5,  # type: ignore
        )

    # Sıfır veya negatif
    with pytest.raises(ValidationError):
        SocialBriefIdeasRetryRequest(
            idempotency_key="key-1",
            source_attempt_id=0,
        )
    with pytest.raises(ValidationError):
        SocialBriefIdeasRetryRequest(
            idempotency_key="key-1",
            source_attempt_id=-5,
        )

    # Extra alan (extra='forbid')
    with pytest.raises(ValidationError):
        SocialBriefIdeasRetryRequest(
            idempotency_key="key-1",
            source_attempt_id=42,
            target_id=101,  # type: ignore
        )
    with pytest.raises(ValidationError):
        SocialBriefIdeasRetryRequest(
            idempotency_key="key-1",
            source_attempt_id=42,
            category_id=10,  # type: ignore
        )


# ==================== F1-F.7.1a STRUCTURAL PARITY & FORGED DTO TESTS ====================

def _make_valid_source_plan_2cat_2target(
    *,
    total_requested: object = 2,
    category_plans: object = None,
    covered_category_ids: object = (10, 20),
    covered_target_ids: object = (101, 102),
) -> SocialIdeaGenerationPlan:
    """Canonical hedefler (101, 102) için geçerli temel plan üretici fixture yardımcısı."""
    if category_plans is None:
        cp1 = IdeaCategoryPlan(
            category_id=10,
            requested_count=1,
            target_quotas=(IdeaTargetQuota(target_id=101, requested_count=1),),
        )
        cp2 = IdeaCategoryPlan(
            category_id=20,
            requested_count=1,
            target_quotas=(IdeaTargetQuota(target_id=102, requested_count=1),),
        )
        category_plans = (cp1, cp2)
    return SocialIdeaGenerationPlan(
        total_requested=total_requested,  # type: ignore
        category_plans=category_plans,  # type: ignore
        covered_category_ids=covered_category_ids,  # type: ignore
        covered_target_ids=covered_target_ids,  # type: ignore
    )


def test_21_total_requested_bool_true_rejected():
    """1. total_requested=True reddedilir."""
    plan = _make_valid_source_plan_2cat_2target(total_requested=True)
    with pytest.raises(IdeaRetryPlanError) as exc_info:
        build_social_idea_retry_plan(
            source_plan=plan,
            canonical_target_ids=(101, 102),
            persisted_target_ids=(),
        )
    assert exc_info.value.error_code == "IDEA_RETRY_SOURCE_PLAN_INVALID"


def test_22_total_requested_bool_false_rejected():
    """2. total_requested=False reddedilir."""
    plan = _make_valid_source_plan_2cat_2target(total_requested=False)
    with pytest.raises(IdeaRetryPlanError) as exc_info:
        build_social_idea_retry_plan(
            source_plan=plan,
            canonical_target_ids=(101, 102),
            persisted_target_ids=(),
        )
    assert exc_info.value.error_code == "IDEA_RETRY_SOURCE_PLAN_INVALID"


@pytest.mark.parametrize("invalid_total", ["2", 2.0])
def test_23_total_requested_string_and_float_rejected(invalid_total):
    """3. total_requested string/float reddedilir."""
    plan = _make_valid_source_plan_2cat_2target(total_requested=invalid_total)
    with pytest.raises(IdeaRetryPlanError) as exc_info:
        build_social_idea_retry_plan(
            source_plan=plan,
            canonical_target_ids=(101, 102),
            persisted_target_ids=(),
        )
    assert exc_info.value.error_code == "IDEA_RETRY_SOURCE_PLAN_INVALID"


@pytest.mark.parametrize("non_positive_total", [0, -1])
def test_24_total_requested_non_positive_rejected(non_positive_total):
    """4. total_requested <= 0 reddedilir."""
    plan = _make_valid_source_plan_2cat_2target(total_requested=non_positive_total)
    with pytest.raises(IdeaRetryPlanError) as exc_info:
        build_social_idea_retry_plan(
            source_plan=plan,
            canonical_target_ids=(101, 102),
            persisted_target_ids=(),
        )
    assert exc_info.value.error_code == "IDEA_RETRY_SOURCE_PLAN_INVALID"


def test_25_total_requested_exceeding_thirty_rejected():
    """5. total_requested > 30 reddedilir."""
    plan = _make_valid_source_plan_2cat_2target(total_requested=31)
    with pytest.raises(IdeaRetryPlanError) as exc_info:
        build_social_idea_retry_plan(
            source_plan=plan,
            canonical_target_ids=(101, 102),
            persisted_target_ids=(),
        )
    assert exc_info.value.error_code == "IDEA_RETRY_SOURCE_PLAN_INVALID"


def test_26_category_plans_empty_tuple_rejected():
    """6. category_plans boş tuple reddedilir."""
    plan = _make_valid_source_plan_2cat_2target(
        total_requested=0,
        category_plans=(),
        covered_category_ids=(),
    )
    with pytest.raises(IdeaRetryPlanError) as exc_info:
        build_social_idea_retry_plan(
            source_plan=plan,
            canonical_target_ids=(101, 102),
            persisted_target_ids=(),
        )
    assert exc_info.value.error_code == "IDEA_RETRY_SOURCE_PLAN_INVALID"


def test_27_seven_category_plans_rejected():
    """7. 7 category plan reddedilir."""
    cps = tuple(
        IdeaCategoryPlan(
            category_id=i,
            requested_count=1,
            target_quotas=(IdeaTargetQuota(target_id=101, requested_count=1),),
        )
        for i in range(1, 8)
    )
    plan = SocialIdeaGenerationPlan(
        total_requested=7,
        category_plans=cps,
        covered_category_ids=tuple(range(1, 8)),
        covered_target_ids=(101,),
    )
    with pytest.raises(IdeaRetryPlanError) as exc_info:
        build_social_idea_retry_plan(
            source_plan=plan,
            canonical_target_ids=(101,),
            persisted_target_ids=(),
        )
    assert exc_info.value.error_code == "IDEA_RETRY_SOURCE_PLAN_INVALID"


def test_28_duplicate_category_id_in_category_plans_rejected():
    """8. Duplicate category_id reddedilir."""
    cp1 = IdeaCategoryPlan(
        category_id=10,
        requested_count=1,
        target_quotas=(IdeaTargetQuota(target_id=101, requested_count=1),),
    )
    cp2 = IdeaCategoryPlan(
        category_id=10,  # Duplicate!
        requested_count=1,
        target_quotas=(IdeaTargetQuota(target_id=102, requested_count=1),),
    )
    plan = _make_valid_source_plan_2cat_2target(
        category_plans=(cp1, cp2),
        covered_category_ids=(10, 10),
    )
    with pytest.raises(IdeaRetryPlanError) as exc_info:
        build_social_idea_retry_plan(
            source_plan=plan,
            canonical_target_ids=(101, 102),
            persisted_target_ids=(),
        )
    assert exc_info.value.error_code == "IDEA_RETRY_SOURCE_PLAN_INVALID"


def test_29_covered_category_ids_list_rejected():
    """9. covered_category_ids list ise reddedilir."""
    plan = _make_valid_source_plan_2cat_2target(covered_category_ids=[10, 20])
    with pytest.raises(IdeaRetryPlanError) as exc_info:
        build_social_idea_retry_plan(
            source_plan=plan,
            canonical_target_ids=(101, 102),
            persisted_target_ids=(),
        )
    assert exc_info.value.error_code == "IDEA_RETRY_SOURCE_PLAN_INVALID"


def test_30_covered_category_ids_duplicate_rejected():
    """10. covered_category_ids duplicate içerirse reddedilir."""
    plan = _make_valid_source_plan_2cat_2target(covered_category_ids=(10, 10))
    with pytest.raises(IdeaRetryPlanError) as exc_info:
        build_social_idea_retry_plan(
            source_plan=plan,
            canonical_target_ids=(101, 102),
            persisted_target_ids=(),
        )
    assert exc_info.value.error_code == "IDEA_RETRY_SOURCE_PLAN_INVALID"


def test_31_covered_category_ids_mismatched_from_category_plans_rejected():
    """11. covered_category_ids category planlarla farklıysa reddedilir."""
    plan = _make_valid_source_plan_2cat_2target(covered_category_ids=(10, 30))
    with pytest.raises(IdeaRetryPlanError) as exc_info:
        build_social_idea_retry_plan(
            source_plan=plan,
            canonical_target_ids=(101, 102),
            persisted_target_ids=(),
        )
    assert exc_info.value.error_code == "IDEA_RETRY_SOURCE_PLAN_INVALID"


def test_32_covered_category_ids_order_mismatched_rejected():
    """12. covered_category_ids sırası farklıysa reddedilir."""
    # Category plans sırası: 10, 20; covered sırası: 20, 10
    plan = _make_valid_source_plan_2cat_2target(covered_category_ids=(20, 10))
    with pytest.raises(IdeaRetryPlanError) as exc_info:
        build_social_idea_retry_plan(
            source_plan=plan,
            canonical_target_ids=(101, 102),
            persisted_target_ids=(),
        )
    assert exc_info.value.error_code == "IDEA_RETRY_SOURCE_PLAN_INVALID"


@pytest.mark.parametrize("invalid_cat_id", [True, "20", 20.5, 0, -1])
def test_33_covered_category_ids_invalid_types_rejected(invalid_cat_id):
    """13. covered_category_ids içinde bool/string/float/geçersiz sayı reddedilir."""
    plan = _make_valid_source_plan_2cat_2target(covered_category_ids=(10, invalid_cat_id))
    with pytest.raises(IdeaRetryPlanError) as exc_info:
        build_social_idea_retry_plan(
            source_plan=plan,
            canonical_target_ids=(101, 102),
            persisted_target_ids=(),
        )
    assert exc_info.value.error_code == "IDEA_RETRY_SOURCE_PLAN_INVALID"


def test_34_covered_target_ids_duplicate_rejected():
    """14. covered_target_ids duplicate içerirse reddedilir."""
    plan = _make_valid_source_plan_2cat_2target(covered_target_ids=(101, 101))
    with pytest.raises(IdeaRetryPlanError) as exc_info:
        build_social_idea_retry_plan(
            source_plan=plan,
            canonical_target_ids=(101, 102),
            persisted_target_ids=(),
        )
    assert exc_info.value.error_code == "IDEA_RETRY_SOURCE_PLAN_INVALID"


def test_35_covered_target_ids_order_mismatched_from_canonical_rejected():
    """15. covered_target_ids sırası canonical sıradan farklıysa reddedilir."""
    # Canonical sıra: 101, 102; covered_target_ids: 102, 101
    plan = _make_valid_source_plan_2cat_2target(covered_target_ids=(102, 101))
    with pytest.raises(IdeaRetryPlanError) as exc_info:
        build_social_idea_retry_plan(
            source_plan=plan,
            canonical_target_ids=(101, 102),
            persisted_target_ids=(),
        )
    assert exc_info.value.error_code == "IDEA_RETRY_SOURCE_PLAN_INVALID"


@pytest.mark.parametrize("invalid_target_id", [True, "102", 102.5, 0, -1])
def test_36_covered_target_ids_invalid_types_rejected(invalid_target_id):
    """16. covered_target_ids içinde bool/string/float/geçersiz sayı reddedilir."""
    plan = _make_valid_source_plan_2cat_2target(covered_target_ids=(101, invalid_target_id))
    with pytest.raises(IdeaRetryPlanError) as exc_info:
        build_social_idea_retry_plan(
            source_plan=plan,
            canonical_target_ids=(101, 102),
            persisted_target_ids=(),
        )
    assert exc_info.value.error_code == "IDEA_RETRY_SOURCE_PLAN_INVALID"


def test_37_duplicate_target_quota_in_same_category_plan_rejected():
    """17. Bir category plan içinde duplicate target quota reddedilir."""
    cp1 = IdeaCategoryPlan(
        category_id=10,
        requested_count=2,
        target_quotas=(
            IdeaTargetQuota(target_id=101, requested_count=1),
            IdeaTargetQuota(target_id=101, requested_count=1),  # Duplicate in same category!
        ),
    )
    cp2 = IdeaCategoryPlan(
        category_id=20,
        requested_count=1,
        target_quotas=(IdeaTargetQuota(target_id=102, requested_count=1),),
    )
    plan = _make_valid_source_plan_2cat_2target(
        total_requested=3,
        category_plans=(cp1, cp2),
    )
    with pytest.raises(IdeaRetryPlanError) as exc_info:
        build_social_idea_retry_plan(
            source_plan=plan,
            canonical_target_ids=(101, 102),
            persisted_target_ids=(),
        )
    assert exc_info.value.error_code == "IDEA_RETRY_SOURCE_PLAN_INVALID"


def test_38_empty_target_quotas_in_category_plan_rejected():
    """18. target_quotas boş tuple reddedilir."""
    cp1 = IdeaCategoryPlan(
        category_id=10,
        requested_count=0,
        target_quotas=(),  # Empty quotas!
    )
    cp2 = IdeaCategoryPlan(
        category_id=20,
        requested_count=1,
        target_quotas=(IdeaTargetQuota(target_id=102, requested_count=1),),
    )
    plan = _make_valid_source_plan_2cat_2target(
        total_requested=1,
        category_plans=(cp1, cp2),
    )
    with pytest.raises(IdeaRetryPlanError) as exc_info:
        build_social_idea_retry_plan(
            source_plan=plan,
            canonical_target_ids=(101, 102),
            persisted_target_ids=(),
        )
    assert exc_info.value.error_code == "IDEA_RETRY_SOURCE_PLAN_INVALID"


def test_39_category_requested_count_bool_rejected():
    """19. category requested_count=True reddedilir."""
    cp1 = IdeaCategoryPlan(
        category_id=10,
        requested_count=True,  # type: ignore
        target_quotas=(IdeaTargetQuota(target_id=101, requested_count=1),),
    )
    cp2 = IdeaCategoryPlan(
        category_id=20,
        requested_count=1,
        target_quotas=(IdeaTargetQuota(target_id=102, requested_count=1),),
    )
    plan = _make_valid_source_plan_2cat_2target(category_plans=(cp1, cp2))
    with pytest.raises(IdeaRetryPlanError) as exc_info:
        build_social_idea_retry_plan(
            source_plan=plan,
            canonical_target_ids=(101, 102),
            persisted_target_ids=(),
        )
    assert exc_info.value.error_code == "IDEA_RETRY_SOURCE_PLAN_INVALID"


def test_40_quota_requested_count_bool_rejected():
    """20. quota requested_count=True reddedilir."""
    cp1 = IdeaCategoryPlan(
        category_id=10,
        requested_count=1,
        target_quotas=(IdeaTargetQuota(target_id=101, requested_count=True),),  # type: ignore
    )
    cp2 = IdeaCategoryPlan(
        category_id=20,
        requested_count=1,
        target_quotas=(IdeaTargetQuota(target_id=102, requested_count=1),),
    )
    plan = _make_valid_source_plan_2cat_2target(category_plans=(cp1, cp2))
    with pytest.raises(IdeaRetryPlanError) as exc_info:
        build_social_idea_retry_plan(
            source_plan=plan,
            canonical_target_ids=(101, 102),
            persisted_target_ids=(),
        )
    assert exc_info.value.error_code == "IDEA_RETRY_SOURCE_PLAN_INVALID"


def test_41_same_target_across_different_categories_is_valid():
    """21. Aynı target’ın farklı kategorilerde bulunmasının hâlâ geçerli olduğu doğrulanır."""
    cp1 = IdeaCategoryPlan(
        category_id=10,
        requested_count=1,
        target_quotas=(IdeaTargetQuota(target_id=101, requested_count=1),),
    )
    cp2 = IdeaCategoryPlan(
        category_id=20,
        requested_count=2,
        target_quotas=(
            IdeaTargetQuota(target_id=101, requested_count=1),  # Same 101 in category 20
            IdeaTargetQuota(target_id=102, requested_count=1),
        ),
    )
    plan = SocialIdeaGenerationPlan(
        total_requested=3,
        category_plans=(cp1, cp2),
        covered_category_ids=(10, 20),
        covered_target_ids=(101, 102),
    )
    retry_plan = build_social_idea_retry_plan(
        source_plan=plan,
        canonical_target_ids=(101, 102),
        persisted_target_ids=(102,),
    )
    assert retry_plan.total_requested == 1
    assert retry_plan.missing_target_ids == (101,)
    assert retry_plan.assignments[0].category_id == 10  # First encountered category


@pytest.mark.parametrize("num_cats", [1, 2, 4, 6])
@pytest.mark.parametrize("num_targets", [1, 3, 6])
@pytest.mark.parametrize("ideas_per_cat", [1, 3, 5])
def test_42_all_normal_plans_from_builder_are_accepted(num_cats, num_targets, ideas_per_cat):
    """22. build_social_idea_generation_plan tarafından üretilen normal planların değişmeden kabul edildiği doğrulanır."""
    category_ids = tuple(range(10, 10 + num_cats))
    target_ids = tuple(range(101, 101 + num_targets))

    normal_plan = build_social_idea_generation_plan(
        selected_category_ids=category_ids,
        target_ids=target_ids,
        ideas_per_category=ideas_per_cat,
    )

    retry_plan = build_social_idea_retry_plan(
        source_plan=normal_plan,
        canonical_target_ids=target_ids,
        persisted_target_ids=(),
    )
    assert retry_plan.total_requested == len(target_ids)
    assert retry_plan.missing_target_ids == target_ids


def test_43_structural_error_messages_contain_no_test_ids():
    """23. Yeni hata mesajlarında kullanılan özel test ID’lerinin bulunmadığı doğrulanır."""
    sentinel_cat_id = 888777
    sentinel_target_id = 999666

    # 1. Invalid covered_category_id
    plan_bad_cat = _make_valid_source_plan_2cat_2target(
        covered_category_ids=(10, sentinel_cat_id)
    )
    with pytest.raises(IdeaRetryPlanError) as exc1:
        build_social_idea_retry_plan(
            source_plan=plan_bad_cat,
            canonical_target_ids=(101, 102),
            persisted_target_ids=(),
        )
    assert str(sentinel_cat_id) not in str(exc1.value.message)

    # 2. Invalid covered_target_id
    plan_bad_target = _make_valid_source_plan_2cat_2target(
        covered_target_ids=(101, sentinel_target_id)
    )
    with pytest.raises(IdeaRetryPlanError) as exc2:
        build_social_idea_retry_plan(
            source_plan=plan_bad_target,
            canonical_target_ids=(101, 102),
            persisted_target_ids=(),
        )
    assert str(sentinel_target_id) not in str(exc2.value.message)
