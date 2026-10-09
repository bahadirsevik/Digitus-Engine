# -*- coding: utf-8 -*-
"""Unit tests for fail-closed social idea retry snapshot parser (F1-F.7.4).

Covers all 40 scenarios required for extract_social_idea_retry_plan_snapshot.
"""
from __future__ import annotations

import copy
from dataclasses import FrozenInstanceError, is_dataclass
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.core.social import (
    IdeaRetryPlanError,
    SocialIdeaGenerationPlan,
    SocialIdeaRetryPlanSnapshot,
    SocialIdeaRetrySnapshotError,
    build_social_idea_generation_plan,
    build_social_idea_retry_plan,
    extract_social_idea_retry_plan_snapshot,
)
from app.database.models import SocialGenerationAttempt


@pytest.fixture
def sample_source_plan() -> SocialIdeaGenerationPlan:
    """Canonical targets (101, 102, 103) ve 2 kategori için geçerli source plan."""
    return build_social_idea_generation_plan(
        selected_category_ids=(10, 20),
        target_ids=(101, 102, 103),
        ideas_per_category=3,
    )


def make_valid_coverage(
    source_plan: SocialIdeaGenerationPlan,
    *,
    source_attempt_id: int = 42,
    canonical_target_ids: tuple[int, ...] = (101, 102, 103),
    persisted_target_ids: tuple[int, ...] = (101,),
    generated_target_ids: list[int] | None = None,
    generated_total_accepted: int | None = None,
) -> dict[str, Any]:
    """Geçerli bir ideas_retry coverage snapshot dict'i üretir."""
    retry_plan = build_social_idea_retry_plan(
        source_plan=source_plan,
        canonical_target_ids=canonical_target_ids,
        persisted_target_ids=persisted_target_ids,
    )
    gen_ids = list(generated_target_ids) if generated_target_ids is not None else []
    tot_acc = (
        generated_total_accepted
        if generated_total_accepted is not None
        else len(gen_ids)
    )
    return {
        "schema_version": "ideas_retry_plan_v1",
        "request": {
            "source_attempt_id": source_attempt_id,
        },
        "baseline": {
            "canonical_target_ids": list(canonical_target_ids),
            "persisted_target_ids_at_start": list(persisted_target_ids),
        },
        "plan": {
            "total_requested": retry_plan.total_requested,
            "missing_target_ids": list(retry_plan.missing_target_ids),
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
            "total_accepted": tot_acc,
            "target_ids": gen_ids,
        },
    }


def make_attempt(
    coverage: dict[str, Any],
    *,
    stage: str = "ideas_retry",
    status: str = "pending",
    requested_target_ids: list[int] | None = None,
    attempt_id: int = 100,
) -> SocialGenerationAttempt:
    """Test için SocialGenerationAttempt nesnesi üretir."""
    if requested_target_ids is None:
        if isinstance(coverage, dict) and isinstance(coverage.get("plan"), dict):
            req_tids = coverage["plan"].get("missing_target_ids", [])
        else:
            req_tids = []
    else:
        req_tids = requested_target_ids

    att = SocialGenerationAttempt(
        id=attempt_id,
        brief_id=1,
        stage=stage,
        status=status,
        coverage=coverage,
        requested_target_ids=req_tids,
    )
    return att


# --------------------------------------------------------------------------
# 1-4: Geçerli durumlar (pending, running, partial, completed)
# --------------------------------------------------------------------------


def test_01_valid_pending_snapshot_parsed(sample_source_plan):
    """1. Geçerli pending snapshot başarıyla parse edilir."""
    cov = make_valid_coverage(sample_source_plan, persisted_target_ids=(101,))
    att = make_attempt(cov, status="pending")

    snapshot = extract_social_idea_retry_plan_snapshot(
        att,
        source_plan=sample_source_plan,
        expected_canonical_target_ids=(101, 102, 103),
        expected_source_attempt_id=42,
    )

    assert snapshot.source_attempt_id == 42
    assert snapshot.canonical_target_ids == (101, 102, 103)
    assert snapshot.persisted_target_ids_at_start == (101,)
    assert snapshot.missing_target_ids == (102, 103)
    assert snapshot.generated_total_accepted == 0
    assert snapshot.generated_target_ids == ()
    assert snapshot.plan.total_requested == 2


def test_02_valid_running_snapshot_parsed(sample_source_plan):
    """2. Geçerli running snapshot parse edilir."""
    cov = make_valid_coverage(sample_source_plan, persisted_target_ids=(101,))
    att = make_attempt(cov, status="running")

    snapshot = extract_social_idea_retry_plan_snapshot(
        att,
        source_plan=sample_source_plan,
        expected_canonical_target_ids=(101, 102, 103),
        expected_source_attempt_id=42,
    )

    assert snapshot.source_attempt_id == 42
    assert snapshot.missing_target_ids == (102, 103)


def test_03_valid_partial_generated_snapshot_parsed(sample_source_plan):
    """3. Geçerli partial generated snapshot parse edilir."""
    cov = make_valid_coverage(
        sample_source_plan,
        persisted_target_ids=(101,),
        generated_target_ids=[102],
        generated_total_accepted=1,
    )
    att = make_attempt(cov, status="partial")

    snapshot = extract_social_idea_retry_plan_snapshot(
        att,
        source_plan=sample_source_plan,
        expected_canonical_target_ids=(101, 102, 103),
        expected_source_attempt_id=42,
    )

    assert snapshot.generated_total_accepted == 1
    assert snapshot.generated_target_ids == (102,)


def test_04_valid_completed_generated_snapshot_parsed(sample_source_plan):
    """4. Geçerli completed generated snapshot parse edilir."""
    cov = make_valid_coverage(
        sample_source_plan,
        persisted_target_ids=(101,),
        generated_target_ids=[102, 103],
        generated_total_accepted=2,
    )
    att = make_attempt(cov, status="completed")

    snapshot = extract_social_idea_retry_plan_snapshot(
        att,
        source_plan=sample_source_plan,
        expected_canonical_target_ids=(101, 102, 103),
        expected_source_attempt_id=42,
    )

    assert snapshot.generated_total_accepted == 2
    assert snapshot.generated_target_ids == (102, 103)


# --------------------------------------------------------------------------
# 5-6: DTO Immutability & Input Non-Mutation
# --------------------------------------------------------------------------


def test_05_dto_is_frozen_and_deterministic(sample_source_plan):
    """5. DTO frozen ve deterministic sonuç üretir."""
    cov = make_valid_coverage(sample_source_plan)
    att = make_attempt(cov)

    s1 = extract_social_idea_retry_plan_snapshot(
        att,
        source_plan=sample_source_plan,
        expected_canonical_target_ids=(101, 102, 103),
        expected_source_attempt_id=42,
    )
    s2 = extract_social_idea_retry_plan_snapshot(
        att,
        source_plan=sample_source_plan,
        expected_canonical_target_ids=(101, 102, 103),
        expected_source_attempt_id=42,
    )

    assert is_dataclass(s1)
    assert s1 == s2

    with pytest.raises(FrozenInstanceError):
        s1.source_attempt_id = 999  # type: ignore

    with pytest.raises(FrozenInstanceError):
        s1.generated_total_accepted = 10  # type: ignore


def test_06_inputs_not_mutated(sample_source_plan):
    """6. Girdiler mutate edilmez."""
    cov = make_valid_coverage(sample_source_plan)
    cov_copy = copy.deepcopy(cov)
    canon_tuple = (101, 102, 103)
    att = make_attempt(cov)

    extract_social_idea_retry_plan_snapshot(
        att,
        source_plan=sample_source_plan,
        expected_canonical_target_ids=canon_tuple,
        expected_source_attempt_id=42,
    )

    assert att.coverage == cov_copy
    assert canon_tuple == (101, 102, 103)


# --------------------------------------------------------------------------
# 7-13: Stage, Coverage, Schema & Request Bloğu
# --------------------------------------------------------------------------


def test_07_invalid_stage_rejected(sample_source_plan):
    """7. Yanlış stage reddedilir."""
    cov = make_valid_coverage(sample_source_plan)
    att = make_attempt(cov, stage="ideas")

    with pytest.raises(SocialIdeaRetrySnapshotError) as exc_info:
        extract_social_idea_retry_plan_snapshot(
            att,
            source_plan=sample_source_plan,
            expected_canonical_target_ids=(101, 102, 103),
        )
    assert exc_info.value.error_code == "IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID"
    assert exc_info.value.field == "stage"

    # Non-SocialGenerationAttempt
    with pytest.raises(SocialIdeaRetrySnapshotError) as exc_info2:
        extract_social_idea_retry_plan_snapshot(
            "not_an_attempt",  # type: ignore
            source_plan=sample_source_plan,
            expected_canonical_target_ids=(101, 102, 103),
        )
    assert exc_info2.value.error_code == "IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID"
    assert exc_info2.value.field == "attempt"


def test_08_coverage_not_dict_rejected(sample_source_plan):
    """8. Coverage dict değilse reddedilir."""
    for bad_cov in (None, "string", [1, 2], 123):
        att = make_attempt(bad_cov)  # type: ignore
        with pytest.raises(SocialIdeaRetrySnapshotError) as exc_info:
            extract_social_idea_retry_plan_snapshot(
                att,
                source_plan=sample_source_plan,
                expected_canonical_target_ids=(101, 102, 103),
            )
        assert exc_info.value.error_code == "IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID"
        assert exc_info.value.field == "coverage"


def test_09_invalid_schema_version_rejected(sample_source_plan):
    """9. Yanlış schema_version reddedilir."""
    cov = make_valid_coverage(sample_source_plan)
    cov["schema_version"] = "ideas_plan_v1"
    att = make_attempt(cov)

    with pytest.raises(SocialIdeaRetrySnapshotError) as exc_info:
        extract_social_idea_retry_plan_snapshot(
            att,
            source_plan=sample_source_plan,
            expected_canonical_target_ids=(101, 102, 103),
        )
    assert exc_info.value.error_code == "IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID"
    assert exc_info.value.field == "schema_version"


def test_10_top_level_extra_or_missing_keys_rejected(sample_source_plan):
    """10. Üst blok extra veya eksik alan reddedilir."""
    cov = make_valid_coverage(sample_source_plan)
    cov["unexpected"] = 123
    att = make_attempt(cov)
    with pytest.raises(SocialIdeaRetrySnapshotError) as exc_info:
        extract_social_idea_retry_plan_snapshot(
            att,
            source_plan=sample_source_plan,
            expected_canonical_target_ids=(101, 102, 103),
        )
    assert exc_info.value.error_code == "IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID"

    cov2 = make_valid_coverage(sample_source_plan)
    del cov2["generated"]
    att2 = make_attempt(cov2)
    with pytest.raises(SocialIdeaRetrySnapshotError) as exc_info2:
        extract_social_idea_retry_plan_snapshot(
            att2,
            source_plan=sample_source_plan,
            expected_canonical_target_ids=(101, 102, 103),
        )
    assert exc_info2.value.error_code == "IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID"


def test_11_request_extra_or_missing_keys_rejected(sample_source_plan):
    """11. Request extra/eksik alan reddedilir."""
    cov = make_valid_coverage(sample_source_plan)
    cov["request"]["extra"] = "val"
    att = make_attempt(cov)
    with pytest.raises(SocialIdeaRetrySnapshotError) as exc_info:
        extract_social_idea_retry_plan_snapshot(
            att,
            source_plan=sample_source_plan,
            expected_canonical_target_ids=(101, 102, 103),
        )
    assert exc_info.value.error_code == "IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID"

    cov2 = make_valid_coverage(sample_source_plan)
    cov2["request"] = {}
    att2 = make_attempt(cov2)
    with pytest.raises(SocialIdeaRetrySnapshotError) as exc_info2:
        extract_social_idea_retry_plan_snapshot(
            att2,
            source_plan=sample_source_plan,
            expected_canonical_target_ids=(101, 102, 103),
        )
    assert exc_info2.value.error_code == "IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID"


def test_12_source_attempt_id_invalid_types_rejected(sample_source_plan):
    """12. source_attempt_id bool/string/float/0/negatif reddedilir."""
    for bad_id in (True, False, "42", 3.14, 0, -5):
        cov = make_valid_coverage(sample_source_plan)
        cov["request"]["source_attempt_id"] = bad_id
        att = make_attempt(cov)
        with pytest.raises(SocialIdeaRetrySnapshotError) as exc_info:
            extract_social_idea_retry_plan_snapshot(
                att,
                source_plan=sample_source_plan,
                expected_canonical_target_ids=(101, 102, 103),
            )
        assert exc_info.value.error_code == "IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID"


def test_13_expected_source_mismatch_raises_request_mismatch_code(sample_source_plan):
    """13. expected source mismatch özel request-mismatch kodu üretir."""
    cov = make_valid_coverage(sample_source_plan, source_attempt_id=42)
    att = make_attempt(cov)

    with pytest.raises(SocialIdeaRetrySnapshotError) as exc_info:
        extract_social_idea_retry_plan_snapshot(
            att,
            source_plan=sample_source_plan,
            expected_canonical_target_ids=(101, 102, 103),
            expected_source_attempt_id=99,
        )
    assert exc_info.value.error_code == "IDEA_RETRY_ATTEMPT_REQUEST_MISMATCH"

    # expected_source_attempt_id=None iken snapshot ID'si taşınır
    snap = extract_social_idea_retry_plan_snapshot(
        att,
        source_plan=sample_source_plan,
        expected_canonical_target_ids=(101, 102, 103),
        expected_source_attempt_id=None,
    )
    assert snap.source_attempt_id == 42


# --------------------------------------------------------------------------
# 14-20: Canonical & Persisted Target Paritesi
# --------------------------------------------------------------------------


def test_14_canonical_target_not_list_rejected(sample_source_plan):
    """14. Canonical target list değilse reddedilir."""
    cov = make_valid_coverage(sample_source_plan)
    cov["baseline"]["canonical_target_ids"] = "not_a_list"
    att = make_attempt(cov)
    with pytest.raises(SocialIdeaRetrySnapshotError) as exc_info:
        extract_social_idea_retry_plan_snapshot(
            att,
            source_plan=sample_source_plan,
            expected_canonical_target_ids=(101, 102, 103),
        )
    assert exc_info.value.error_code == "IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID"


def test_15_canonical_target_duplicates_and_invalid_types_rejected(sample_source_plan):
    """15. Canonical target duplicate veya geçersiz tip reddedilir."""
    for bad_canonical in (
        [101, 101, 102],
        [True, 102, 103],
        ["101", 102, 103],
        [0, 102, 103],
        [-1, 102, 103],
    ):
        cov = make_valid_coverage(sample_source_plan)
        cov["baseline"]["canonical_target_ids"] = bad_canonical
        att = make_attempt(cov)
        with pytest.raises(SocialIdeaRetrySnapshotError) as exc_info:
            extract_social_idea_retry_plan_snapshot(
                att,
                source_plan=sample_source_plan,
                expected_canonical_target_ids=(101, 102, 103),
            )
        assert exc_info.value.error_code == "IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID"


def test_16_expected_canonical_tuple_invalid_rejected(sample_source_plan):
    """16. Expected canonical tuple geçersizse reddedilir."""
    cov = make_valid_coverage(sample_source_plan)
    att = make_attempt(cov)

    for bad_exp in (
        [101, 102, 103],  # list, not tuple
        (),  # empty
        (101, 102, 103, 104, 105, 106, 107),  # 7 targets
        (101, 101, 102),  # duplicate
        (True, 102),  # bool
        (0, 102),  # zero
        (-1, 102),  # negative
    ):
        with pytest.raises(SocialIdeaRetrySnapshotError) as exc_info:
            extract_social_idea_retry_plan_snapshot(
                att,
                source_plan=sample_source_plan,
                expected_canonical_target_ids=bad_exp,  # type: ignore
            )
        assert exc_info.value.error_code == "IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID"


def test_17_snapshot_canonical_order_mismatch_rejected(sample_source_plan):
    """17. Snapshot canonical sıra mismatch reddedilir."""
    cov = make_valid_coverage(sample_source_plan)
    cov["baseline"]["canonical_target_ids"] = [103, 102, 101]
    att = make_attempt(cov)
    with pytest.raises(SocialIdeaRetrySnapshotError) as exc_info:
        extract_social_idea_retry_plan_snapshot(
            att,
            source_plan=sample_source_plan,
            expected_canonical_target_ids=(101, 102, 103),
        )
    assert exc_info.value.error_code == "IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID"


def test_18_persisted_target_duplicates_rejected(sample_source_plan):
    """18. Persisted target duplicate reddedilir."""
    cov = make_valid_coverage(sample_source_plan)
    cov["baseline"]["persisted_target_ids_at_start"] = [101, 101]
    att = make_attempt(cov)
    with pytest.raises(SocialIdeaRetrySnapshotError) as exc_info:
        extract_social_idea_retry_plan_snapshot(
            att,
            source_plan=sample_source_plan,
            expected_canonical_target_ids=(101, 102, 103),
        )
    assert exc_info.value.error_code == "IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID"


def test_19_persisted_canonical_out_of_scope_rejected(sample_source_plan):
    """19. Persisted canonical dışındaysa reddedilir."""
    cov = make_valid_coverage(sample_source_plan)
    cov["baseline"]["persisted_target_ids_at_start"] = [999]
    att = make_attempt(cov)
    with pytest.raises(SocialIdeaRetrySnapshotError) as exc_info:
        extract_social_idea_retry_plan_snapshot(
            att,
            source_plan=sample_source_plan,
            expected_canonical_target_ids=(101, 102, 103),
        )
    assert exc_info.value.error_code == "IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID"


def test_20_persisted_canonical_order_broken_rejected(sample_source_plan):
    """20. Persisted canonical sıra bozuksa reddedilir."""
    cov = make_valid_coverage(sample_source_plan, persisted_target_ids=(101, 102))
    cov["baseline"]["persisted_target_ids_at_start"] = [102, 101]
    att = make_attempt(cov)
    with pytest.raises(SocialIdeaRetrySnapshotError) as exc_info:
        extract_social_idea_retry_plan_snapshot(
            att,
            source_plan=sample_source_plan,
            expected_canonical_target_ids=(101, 102, 103),
        )
    assert exc_info.value.error_code == "IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID"


# --------------------------------------------------------------------------
# 21-28: Plan Bloğu & Saf Planner Paritesi
# --------------------------------------------------------------------------


def test_21_plan_extra_or_missing_keys_rejected(sample_source_plan):
    """21. Plan extra/eksik alan reddedilir."""
    cov = make_valid_coverage(sample_source_plan)
    del cov["plan"]["assignments"]
    att = make_attempt(cov)
    with pytest.raises(SocialIdeaRetrySnapshotError) as exc_info:
        extract_social_idea_retry_plan_snapshot(
            att,
            source_plan=sample_source_plan,
            expected_canonical_target_ids=(101, 102, 103),
        )
    assert exc_info.value.error_code == "IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID"


def test_22_total_requested_differs_from_planner_rejected(sample_source_plan):
    """22. total_requested planner sonucundan farklıysa reddedilir."""
    cov = make_valid_coverage(sample_source_plan)
    cov["plan"]["total_requested"] = 99
    att = make_attempt(cov)
    with pytest.raises(SocialIdeaRetrySnapshotError) as exc_info:
        extract_social_idea_retry_plan_snapshot(
            att,
            source_plan=sample_source_plan,
            expected_canonical_target_ids=(101, 102, 103),
        )
    assert exc_info.value.error_code == "IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID"


def test_23_missing_targets_differ_from_planner_rejected(sample_source_plan):
    """23. missing target planner sonucundan farklıysa reddedilir."""
    cov = make_valid_coverage(sample_source_plan)
    cov["plan"]["missing_target_ids"] = [101, 102]
    att = make_attempt(cov)
    with pytest.raises(SocialIdeaRetrySnapshotError) as exc_info:
        extract_social_idea_retry_plan_snapshot(
            att,
            source_plan=sample_source_plan,
            expected_canonical_target_ids=(101, 102, 103),
        )
    assert exc_info.value.error_code == "IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID"


def test_24_missing_order_broken_rejected(sample_source_plan):
    """24. Missing sıra bozuksa reddedilir."""
    cov = make_valid_coverage(sample_source_plan, persisted_target_ids=(101,))
    cov["plan"]["missing_target_ids"] = [103, 102]
    att = make_attempt(cov)
    with pytest.raises(SocialIdeaRetrySnapshotError) as exc_info:
        extract_social_idea_retry_plan_snapshot(
            att,
            source_plan=sample_source_plan,
            expected_canonical_target_ids=(101, 102, 103),
        )
    assert exc_info.value.error_code == "IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID"


def test_25_assignment_category_differs_from_planner_rejected(sample_source_plan):
    """25. Assignment category farklıysa reddedilir."""
    cov = make_valid_coverage(sample_source_plan)
    cov["plan"]["assignments"][0]["category_id"] = 9999
    att = make_attempt(cov)
    with pytest.raises(SocialIdeaRetrySnapshotError) as exc_info:
        extract_social_idea_retry_plan_snapshot(
            att,
            source_plan=sample_source_plan,
            expected_canonical_target_ids=(101, 102, 103),
        )
    assert exc_info.value.error_code == "IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID"


def test_26_assignment_target_differs_from_planner_rejected(sample_source_plan):
    """26. Assignment target farklıysa reddedilir."""
    cov = make_valid_coverage(sample_source_plan)
    cov["plan"]["assignments"][0]["target_id"] = 9999
    att = make_attempt(cov)
    with pytest.raises(SocialIdeaRetrySnapshotError) as exc_info:
        extract_social_idea_retry_plan_snapshot(
            att,
            source_plan=sample_source_plan,
            expected_canonical_target_ids=(101, 102, 103),
        )
    assert exc_info.value.error_code == "IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID"


def test_27_assignment_requested_count_not_one_rejected(sample_source_plan):
    """27. Assignment requested_count != 1 reddedilir."""
    for bad_rc in (0, 2, True, "1"):
        cov = make_valid_coverage(sample_source_plan)
        cov["plan"]["assignments"][0]["requested_count"] = bad_rc
        att = make_attempt(cov)
        with pytest.raises(SocialIdeaRetrySnapshotError) as exc_info:
            extract_social_idea_retry_plan_snapshot(
                att,
                source_plan=sample_source_plan,
                expected_canonical_target_ids=(101, 102, 103),
            )
        assert exc_info.value.error_code == "IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID"


def test_28_assignment_order_broken_rejected(sample_source_plan):
    """28. Assignment sırası bozuksa reddedilir."""
    cov = make_valid_coverage(sample_source_plan, persisted_target_ids=(101,))
    cov["plan"]["assignments"] = list(reversed(cov["plan"]["assignments"]))
    att = make_attempt(cov)
    with pytest.raises(SocialIdeaRetrySnapshotError) as exc_info:
        extract_social_idea_retry_plan_snapshot(
            att,
            source_plan=sample_source_plan,
            expected_canonical_target_ids=(101, 102, 103),
        )
    assert exc_info.value.error_code == "IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID"


# --------------------------------------------------------------------------
# 29-31: Requested Targets, Set Cebiri & Invariantlar
# --------------------------------------------------------------------------


def test_29_requested_target_ids_mismatch_with_missing_rejected(sample_source_plan):
    """29. requested_target_ids missing ile uyuşmazsa reddedilir."""
    cov = make_valid_coverage(sample_source_plan, persisted_target_ids=(101,))
    # missing is [102, 103]
    att = make_attempt(cov, requested_target_ids=[101])
    with pytest.raises(SocialIdeaRetrySnapshotError) as exc_info:
        extract_social_idea_retry_plan_snapshot(
            att,
            source_plan=sample_source_plan,
            expected_canonical_target_ids=(101, 102, 103),
        )
    assert exc_info.value.error_code == "IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID"

    # Sıra mismatch
    att2 = make_attempt(cov, requested_target_ids=[103, 102])
    with pytest.raises(SocialIdeaRetrySnapshotError) as exc_info2:
        extract_social_idea_retry_plan_snapshot(
            att2,
            source_plan=sample_source_plan,
            expected_canonical_target_ids=(101, 102, 103),
        )
    assert exc_info2.value.error_code == "IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID"


def test_30_persisted_and_missing_intersect_rejected(sample_source_plan):
    """30. Persisted ve missing kesişimi reddedilir."""
    cov = make_valid_coverage(sample_source_plan, persisted_target_ids=(101,))
    cov["plan"]["missing_target_ids"] = [101, 102, 103]
    att = make_attempt(cov)
    with pytest.raises(SocialIdeaRetrySnapshotError) as exc_info:
        extract_social_idea_retry_plan_snapshot(
            att,
            source_plan=sample_source_plan,
            expected_canonical_target_ids=(101, 102, 103),
        )
    assert exc_info.value.error_code == "IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID"


def test_31_persisted_union_missing_not_canonical_rejected(sample_source_plan):
    """31. Persisted ∪ missing canonical değilse reddedilir."""
    cov = make_valid_coverage(sample_source_plan, persisted_target_ids=(101,))
    cov["plan"]["missing_target_ids"] = [102]  # 103 eksik
    att = make_attempt(cov)
    with pytest.raises(SocialIdeaRetrySnapshotError) as exc_info:
        extract_social_idea_retry_plan_snapshot(
            att,
            source_plan=sample_source_plan,
            expected_canonical_target_ids=(101, 102, 103),
        )
    assert exc_info.value.error_code == "IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID"


# --------------------------------------------------------------------------
# 32-37: Generated Bloğu
# --------------------------------------------------------------------------


def test_32_generated_extra_or_missing_keys_rejected(sample_source_plan):
    """32. Generated extra/eksik alan reddedilir."""
    cov = make_valid_coverage(sample_source_plan)
    cov["generated"]["extra"] = "val"
    att = make_attempt(cov)
    with pytest.raises(SocialIdeaRetrySnapshotError) as exc_info:
        extract_social_idea_retry_plan_snapshot(
            att,
            source_plan=sample_source_plan,
            expected_canonical_target_ids=(101, 102, 103),
        )
    assert exc_info.value.error_code == "IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID"

    cov2 = make_valid_coverage(sample_source_plan)
    del cov2["generated"]["target_ids"]
    att2 = make_attempt(cov2)
    with pytest.raises(SocialIdeaRetrySnapshotError) as exc_info2:
        extract_social_idea_retry_plan_snapshot(
            att2,
            source_plan=sample_source_plan,
            expected_canonical_target_ids=(101, 102, 103),
        )
    assert exc_info2.value.error_code == "IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID"


def test_33_generated_total_accepted_invalid_types_rejected(sample_source_plan):
    """33. Generated total bool/string/float/negatif/aşırı ise reddedilir."""
    for bad_tot in (True, False, "1", 2.5, -1, 99):
        cov = make_valid_coverage(sample_source_plan)
        cov["generated"]["total_accepted"] = bad_tot
        att = make_attempt(cov)
        with pytest.raises(SocialIdeaRetrySnapshotError) as exc_info:
            extract_social_idea_retry_plan_snapshot(
                att,
                source_plan=sample_source_plan,
                expected_canonical_target_ids=(101, 102, 103),
            )
        assert exc_info.value.error_code == "IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID"


def test_34_generated_target_duplicates_rejected(sample_source_plan):
    """34. Generated target duplicate reddedilir."""
    cov = make_valid_coverage(sample_source_plan, persisted_target_ids=(101,))
    cov["generated"]["total_accepted"] = 2
    cov["generated"]["target_ids"] = [102, 102]
    att = make_attempt(cov)
    with pytest.raises(SocialIdeaRetrySnapshotError) as exc_info:
        extract_social_idea_retry_plan_snapshot(
            att,
            source_plan=sample_source_plan,
            expected_canonical_target_ids=(101, 102, 103),
        )
    assert exc_info.value.error_code == "IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID"


def test_35_generated_target_out_of_missing_scope_rejected(sample_source_plan):
    """35. Generated target missing kapsamı dışındaysa reddedilir."""
    cov = make_valid_coverage(sample_source_plan, persisted_target_ids=(101,))
    cov["generated"]["total_accepted"] = 1
    cov["generated"]["target_ids"] = [101]  # 101 persisted idi, missing değil!
    att = make_attempt(cov)
    with pytest.raises(SocialIdeaRetrySnapshotError) as exc_info:
        extract_social_idea_retry_plan_snapshot(
            att,
            source_plan=sample_source_plan,
            expected_canonical_target_ids=(101, 102, 103),
        )
    assert exc_info.value.error_code == "IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID"


def test_36_generated_order_broken_rejected(sample_source_plan):
    """36. Generated sıra bozuksa reddedilir."""
    cov = make_valid_coverage(sample_source_plan, persisted_target_ids=(101,))
    cov["generated"]["total_accepted"] = 2
    cov["generated"]["target_ids"] = [103, 102]  # Canonical sıra 102, 103 olmalı
    att = make_attempt(cov)
    with pytest.raises(SocialIdeaRetrySnapshotError) as exc_info:
        extract_social_idea_retry_plan_snapshot(
            att,
            source_plan=sample_source_plan,
            expected_canonical_target_ids=(101, 102, 103),
        )
    assert exc_info.value.error_code == "IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID"


def test_37_generated_total_accepted_length_mismatch_rejected(sample_source_plan):
    """37. generated_total_accepted uzunlukla uyuşmazsa reddedilir."""
    cov = make_valid_coverage(sample_source_plan, persisted_target_ids=(101,))
    cov["generated"]["total_accepted"] = 2
    cov["generated"]["target_ids"] = [102]  # len is 1, total_accepted is 2
    att = make_attempt(cov)
    with pytest.raises(SocialIdeaRetrySnapshotError) as exc_info:
        extract_social_idea_retry_plan_snapshot(
            att,
            source_plan=sample_source_plan,
            expected_canonical_target_ids=(101, 102, 103),
        )
    assert exc_info.value.error_code == "IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID"


# --------------------------------------------------------------------------
# 38-40: Planner Not Needed, Information Leakage & Pure Function Guards
# --------------------------------------------------------------------------


def test_38_planner_not_needed_rejects_snapshot(sample_source_plan):
    """38. Planner IDEA_RETRY_NOT_NEEDED üretirse snapshot reddedilir."""
    # Persisted targets all canonical targets
    cov = make_valid_coverage(sample_source_plan, persisted_target_ids=(101, 102))
    # Override persisted to full coverage (101, 102, 103)
    cov["baseline"]["persisted_target_ids_at_start"] = [101, 102, 103]
    att = make_attempt(cov)

    with pytest.raises(SocialIdeaRetrySnapshotError) as exc_info:
        extract_social_idea_retry_plan_snapshot(
            att,
            source_plan=sample_source_plan,
            expected_canonical_target_ids=(101, 102, 103),
        )
    assert exc_info.value.error_code == "IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID"


def test_39_error_messages_contain_no_sentinel_or_raw_json(sample_source_plan):
    """39. Hata mesajlarında sentinel ID veya snapshot JSON bulunmaz."""
    sentinel_str = "SECRET_SENTINEL_TOKEN_999888"
    sentinel_id = 999888777
    cov = make_valid_coverage(sample_source_plan)
    cov["request"]["source_attempt_id"] = sentinel_id
    att = make_attempt(cov)

    with pytest.raises(SocialIdeaRetrySnapshotError) as exc_info:
        extract_social_idea_retry_plan_snapshot(
            att,
            source_plan=sample_source_plan,
            expected_canonical_target_ids=(101, 102, 103),
            expected_source_attempt_id=12345,
        )

    err_msg = str(exc_info.value.message)
    assert str(sentinel_id) not in err_msg
    assert sentinel_str not in err_msg
    assert "schema_version" not in err_msg
    assert "{" not in err_msg


def test_40_parser_makes_no_db_ai_or_celery_calls(sample_source_plan):
    """40. Parser DB/AI/Celery çağrısı yapmaz."""
    cov = make_valid_coverage(sample_source_plan)
    att = make_attempt(cov)

    with (
        patch("sqlalchemy.orm.Session.execute", side_effect=RuntimeError("DB called!")),
        patch("app.generators.ai_service.get_ai_service", side_effect=RuntimeError("AI called!")),
        patch("celery.Celery.send_task", side_effect=RuntimeError("Celery called!")),
    ):
        snapshot = extract_social_idea_retry_plan_snapshot(
            att,
            source_plan=sample_source_plan,
            expected_canonical_target_ids=(101, 102, 103),
            expected_source_attempt_id=42,
        )

    assert snapshot.source_attempt_id == 42
    assert snapshot.plan.total_requested == 2
