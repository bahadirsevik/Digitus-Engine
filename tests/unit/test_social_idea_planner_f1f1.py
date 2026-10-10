# -*- coding: utf-8 -*-
"""Unit tests for pure and deterministic idea distribution planner (F1-F.1).

Doğrulanan senaryolar:
1. 2 kategori, 3 target, temel 3 -> toplam 6; tüm kategoriler ve target'lar kapsanır.
2. 1 kategori, 6 target, temel 3 -> toplam 6; altı target'ın tamamı kapsanır.
3. 6 kategori, 1 target, temel 1 -> toplam 6; bütün kategoriler en az bir fikir alır.
4. 3 kategori, 6 target, temel 1 -> toplam 6; bütün target'lar kapsanır.
5. 6 kategori, 6 target, temel 5 -> toplam tam 30 olur.
6. Aynı girdi iki kez çağrıldığında dataclass çıktısı birebir eşittir (determinizm).
7. Kategori ve target giriş sırasının çıktıda korunduğunu doğrula.
8. Her category requested_count değerinin target quota toplamına eşit olduğunu doğrula.
9. Bütün category toplamının total_requested değerine eşit olduğunu doğrula; sıfır kotası olan target eklenmez.
10. Boş kategori ve boş target ayrı ayrı reddedilir.
11. List/set girdiler fail-closed reddedilir.
12. Bool, string, float, sıfır ve negatif ID'ler reddedilir.
13. Mükerrer kategori ve target ID'leri uygun hata koduyla reddedilir.
14. ideas_per_category için 0, 6, True, "3" ve 3.0 reddedilir.
15. Tam 30 sınırı yalnız geçerli senaryoyla (6 kategori × 5 fikir) kabul edilir.
16. 7 veya üzeri kategori ve target girdileri fail-closed IDEA_PLAN_INVALID_INPUT ile reddedilir; 30 target artık kabul edilmez.
17. Exception mesajlarında ham kullanıcı girdisinin ve ID değerlerinin bulunmadığını doğrula.
18. Fonksiyonun girdi tuple'larını değiştirmediğini doğrula.
19. Farklı matris konfigürasyonlarında her kategori ve target'ın en az 1 fikir aldığı doğrulanır.
"""
from __future__ import annotations

import pytest

from app.core.social import (
    IdeaCategoryPlan,
    IdeaPlanValidationError,
    IdeaTargetQuota,
    SocialIdeaGenerationPlan,
    build_social_idea_generation_plan,
)


def test_01_two_categories_three_targets_base_three_gives_total_six():
    """1. 2 kategori, 3 target, temel 3: toplam 6; tüm kategoriler ve target'lar kapsanır."""
    plan = build_social_idea_generation_plan(
        selected_category_ids=(101, 102),
        target_ids=(201, 202, 203),
        ideas_per_category=3,
    )
    assert plan.total_requested == 6
    assert len(plan.category_plans) == 2
    assert plan.covered_category_ids == (101, 102)
    assert plan.covered_target_ids == (201, 202, 203)

    c1, c2 = plan.category_plans
    assert c1.category_id == 101
    assert c1.requested_count == 3
    assert c2.category_id == 102
    assert c2.requested_count == 3

    # Hedefler adil dağıtılmış olmalı (toplam her target 2 kez)
    target_sums = {201: 0, 202: 0, 203: 0}
    for cp in plan.category_plans:
        for tq in cp.target_quotas:
            target_sums[tq.target_id] += tq.requested_count
    assert target_sums == {201: 2, 202: 2, 203: 2}


def test_02_one_category_six_targets_base_three_gives_total_six():
    """2. 1 kategori, 6 target, temel 3: toplam 6; altı target'ın tamamı tek kategori altında kapsanır."""
    plan = build_social_idea_generation_plan(
        selected_category_ids=(101,),
        target_ids=(201, 202, 203, 204, 205, 206),
        ideas_per_category=3,
    )
    assert plan.total_requested == 6
    assert len(plan.category_plans) == 1
    assert plan.covered_category_ids == (101,)
    assert plan.covered_target_ids == (201, 202, 203, 204, 205, 206)

    cp = plan.category_plans[0]
    assert cp.category_id == 101
    assert cp.requested_count == 6
    assert len(cp.target_quotas) == 6
    assert [q.target_id for q in cp.target_quotas] == [201, 202, 203, 204, 205, 206]
    assert all(q.requested_count == 1 for q in cp.target_quotas)


def test_03_six_categories_one_target_base_one_gives_total_six():
    """3. 6 kategori, 1 target, temel 1: toplam 6; bütün kategoriler en az bir fikir alır."""
    plan = build_social_idea_generation_plan(
        selected_category_ids=(1, 2, 3, 4, 5, 6),
        target_ids=(10,),
        ideas_per_category=1,
    )
    assert plan.total_requested == 6
    assert len(plan.category_plans) == 6
    assert plan.covered_category_ids == (1, 2, 3, 4, 5, 6)
    assert plan.covered_target_ids == (10,)

    for cp in plan.category_plans:
        assert cp.requested_count == 1
        assert len(cp.target_quotas) == 1
        assert cp.target_quotas[0].target_id == 10
        assert cp.target_quotas[0].requested_count == 1


def test_04_three_categories_six_targets_base_one_gives_total_six():
    """4. 3 kategori, 6 target, temel 1: toplam 6; bütün target'lar kapsanır."""
    plan = build_social_idea_generation_plan(
        selected_category_ids=(11, 12, 13),
        target_ids=(21, 22, 23, 24, 25, 26),
        ideas_per_category=1,
    )
    assert plan.total_requested == 6
    assert len(plan.category_plans) == 3
    assert plan.covered_category_ids == (11, 12, 13)
    assert plan.covered_target_ids == (21, 22, 23, 24, 25, 26)

    # 3 kategoriye 6 hedef round-robin: her biri 2 hedef alır
    for cp in plan.category_plans:
        assert cp.requested_count == 2
        assert len(cp.target_quotas) == 2


def test_05_six_categories_six_targets_base_five_gives_total_thirty():
    """5. 6 kategori, 6 target, temel 5: toplam tam 30 olur."""
    plan = build_social_idea_generation_plan(
        selected_category_ids=(1, 2, 3, 4, 5, 6),
        target_ids=(10, 20, 30, 40, 50, 60),
        ideas_per_category=5,
    )
    assert plan.total_requested == 30
    assert len(plan.category_plans) == 6
    assert plan.covered_category_ids == (1, 2, 3, 4, 5, 6)
    assert plan.covered_target_ids == (10, 20, 30, 40, 50, 60)

    for cp in plan.category_plans:
        assert cp.requested_count == 5


def test_06_deterministic_repeat_call_gives_identical_dataclass():
    """6. Aynı girdi iki kez çağrıldığında dataclass çıktısı birebir eşittir."""
    kwargs = {
        "selected_category_ids": (42, 15, 88),
        "target_ids": (1001, 1002, 1003, 1004),
        "ideas_per_category": 3,
    }
    plan1 = build_social_idea_generation_plan(**kwargs)
    plan2 = build_social_idea_generation_plan(**kwargs)

    assert plan1 == plan2
    assert plan1.category_plans == plan2.category_plans
    assert plan1.covered_category_ids == plan2.covered_category_ids
    assert plan1.covered_target_ids == plan2.covered_target_ids


def test_07_category_and_target_input_ordering_preserved():
    """7. Kategori ve target giriş sırasının çıktıda korunduğunu doğrula."""
    categories = (99, 12, 45)
    targets = (500, 100, 300)

    plan = build_social_idea_generation_plan(
        selected_category_ids=categories,
        target_ids=targets,
        ideas_per_category=2,
    )
    # Kategori sırası
    assert tuple(cp.category_id for cp in plan.category_plans) == categories
    assert plan.covered_category_ids == categories

    # Her kategori planındaki target_quotas sırası, targets giriş sırasına göredir
    for cp in plan.category_plans:
        target_ids_in_quotas = [tq.target_id for tq in cp.target_quotas]
        expected_subset_order = [t for t in targets if t in target_ids_in_quotas]
        assert target_ids_in_quotas == expected_subset_order

    # Genel covered_target_ids sırası
    assert plan.covered_target_ids == targets


def test_08_category_requested_count_equals_sum_of_target_quotas():
    """8. Her category requested_count değerinin target quota toplamına eşit olduğunu doğrula."""
    plan = build_social_idea_generation_plan(
        selected_category_ids=(10, 20, 30),
        target_ids=(1, 2, 3, 4, 5),
        ideas_per_category=4,
    )
    for cp in plan.category_plans:
        sum_quotas = sum(tq.requested_count for tq in cp.target_quotas)
        assert cp.requested_count == sum_quotas
        assert cp.requested_count >= 1


def test_09_total_requested_equals_sum_of_category_plans_and_zero_quotas_omitted():
    """9. Toplam kotanın total_requested değerine eşit olduğunu doğrula; sıfır kotası olan target eklenmez."""
    plan = build_social_idea_generation_plan(
        selected_category_ids=(100, 200),
        target_ids=(1, 2, 3),
        ideas_per_category=2,
    )
    total_from_categories = sum(cp.requested_count for cp in plan.category_plans)
    assert plan.total_requested == total_from_categories

    # Sıfır kotası olan hiçbir target listeye dahil edilmemeli
    for cp in plan.category_plans:
        for tq in cp.target_quotas:
            assert tq.requested_count > 0


def test_10_empty_category_and_empty_target_rejected():
    """10. Boş kategori ve boş target ayrı ayrı fail-closed reddedilir."""
    with pytest.raises(IdeaPlanValidationError) as exc1:
        build_social_idea_generation_plan(
            selected_category_ids=(),
            target_ids=(1,),
            ideas_per_category=3,
        )
    assert exc1.value.error_code == "IDEA_PLAN_INVALID_INPUT"
    assert exc1.value.field == "selected_category_ids"

    with pytest.raises(IdeaPlanValidationError) as exc2:
        build_social_idea_generation_plan(
            selected_category_ids=(1,),
            target_ids=(),
            ideas_per_category=3,
        )
    assert exc2.value.error_code == "IDEA_PLAN_INVALID_INPUT"
    assert exc2.value.field == "target_ids"


def test_11_list_and_set_inputs_rejected():
    """11. Tuple yerine list/set girdiler fail-closed reddedilir."""
    with pytest.raises(IdeaPlanValidationError) as exc1:
        build_social_idea_generation_plan(
            selected_category_ids=[1, 2],  # type: ignore
            target_ids=(10,),
            ideas_per_category=3,
        )
    assert exc1.value.error_code == "IDEA_PLAN_INVALID_INPUT"
    assert exc1.value.field == "selected_category_ids"

    with pytest.raises(IdeaPlanValidationError) as exc2:
        build_social_idea_generation_plan(
            selected_category_ids={1, 2},  # type: ignore
            target_ids=(10,),
            ideas_per_category=3,
        )
    assert exc2.value.error_code == "IDEA_PLAN_INVALID_INPUT"
    assert exc2.value.field == "selected_category_ids"

    with pytest.raises(IdeaPlanValidationError) as exc3:
        build_social_idea_generation_plan(
            selected_category_ids=(1, 2),
            target_ids=[10, 20],  # type: ignore
            ideas_per_category=3,
        )
    assert exc3.value.error_code == "IDEA_PLAN_INVALID_INPUT"
    assert exc3.value.field == "target_ids"

    with pytest.raises(IdeaPlanValidationError) as exc4:
        build_social_idea_generation_plan(
            selected_category_ids=(1, 2),
            target_ids={10, 20},  # type: ignore
            ideas_per_category=3,
        )
    assert exc4.value.error_code == "IDEA_PLAN_INVALID_INPUT"
    assert exc4.value.field == "target_ids"


def test_12_invalid_id_types_and_values_rejected():
    """12. Bool, string, float, sıfır ve negatif ID'ler fail-closed reddedilir."""
    invalid_ids = [True, False, "1", 1.5, 0, -1, -99, None]

    for inv in invalid_ids:
        # Kategori için test
        with pytest.raises(IdeaPlanValidationError) as exc_cat:
            build_social_idea_generation_plan(
                selected_category_ids=(inv,),  # type: ignore
                target_ids=(1,),
                ideas_per_category=3,
            )
        assert exc_cat.value.error_code == "IDEA_PLAN_INVALID_INPUT"
        assert exc_cat.value.field == "selected_category_ids"

        # Target için test
        with pytest.raises(IdeaPlanValidationError) as exc_tgt:
            build_social_idea_generation_plan(
                selected_category_ids=(1,),
                target_ids=(inv,),  # type: ignore
                ideas_per_category=3,
            )
        assert exc_tgt.value.error_code == "IDEA_PLAN_INVALID_INPUT"
        assert exc_tgt.value.field == "target_ids"


def test_13_duplicate_category_and_target_ids_rejected():
    """13. Mükerrer kategori ve target ID'leri uygun hata koduyla reddedilir."""
    with pytest.raises(IdeaPlanValidationError) as exc1:
        build_social_idea_generation_plan(
            selected_category_ids=(10, 20, 10),
            target_ids=(1, 2),
            ideas_per_category=3,
        )
    assert exc1.value.error_code == "IDEA_PLAN_DUPLICATE_CATEGORY"
    assert exc1.value.field == "selected_category_ids"

    with pytest.raises(IdeaPlanValidationError) as exc2:
        build_social_idea_generation_plan(
            selected_category_ids=(10, 20),
            target_ids=(1, 2, 1),
            ideas_per_category=3,
        )
    assert exc2.value.error_code == "IDEA_PLAN_DUPLICATE_TARGET"
    assert exc2.value.field == "target_ids"


def test_14_invalid_ideas_per_category_rejected():
    """14. ideas_per_category için 0, 6, True, '3' ve 3.0 reddedilir."""
    invalid_ideas = [0, 6, -1, 10, True, False, "3", 3.0, None]

    for inv in invalid_ideas:
        with pytest.raises(IdeaPlanValidationError) as exc:
            build_social_idea_generation_plan(
                selected_category_ids=(1,),
                target_ids=(1,),
                ideas_per_category=inv,  # type: ignore
            )
        assert exc.value.error_code == "IDEA_PLAN_INVALID_INPUT"
        assert exc.value.field == "ideas_per_category"


def test_15_thirty_limit_boundary_accepted():
    """15. Tam 30 sınırı yalnız geçerli senaryoyla (6 kategori × 5 fikir) kabul edilir."""
    # 6 kategori * 5 fikir = 30 -> geçerli maksimum kombinasyon
    plan_30 = build_social_idea_generation_plan(
        selected_category_ids=(1, 2, 3, 4, 5, 6),
        target_ids=(10, 20, 30, 40, 50, 60),
        ideas_per_category=5,
    )
    assert plan_30.total_requested == 30
    assert len(plan_30.category_plans) == 6
    assert all(cp.requested_count == 5 for cp in plan_30.category_plans)
    assert plan_30.covered_category_ids == (1, 2, 3, 4, 5, 6)
    assert plan_30.covered_target_ids == (10, 20, 30, 40, 50, 60)


def test_16_cardinality_limits_exceeded_rejected():
    """16. 7 veya üzeri kategori ve target girdileri fail-closed IDEA_PLAN_INVALID_INPUT ile reddedilir; 30 target artık kabul edilmez."""
    # 7 kategori -> IDEA_PLAN_INVALID_INPUT
    with pytest.raises(IdeaPlanValidationError) as exc1:
        build_social_idea_generation_plan(
            selected_category_ids=(101, 102, 103, 104, 105, 106, 107),
            target_ids=(10,),
            ideas_per_category=5,
        )
    assert exc1.value.error_code == "IDEA_PLAN_INVALID_INPUT"
    assert exc1.value.field == "selected_category_ids"
    # Ham ID'lerin mesajda bulunmadığını doğrula
    assert "107" not in exc1.value.message
    assert "107" not in str(exc1.value)

    # 7 target -> IDEA_PLAN_INVALID_INPUT
    with pytest.raises(IdeaPlanValidationError) as exc2:
        build_social_idea_generation_plan(
            selected_category_ids=(1,),
            target_ids=(201, 202, 203, 204, 205, 206, 207),
            ideas_per_category=3,
        )
    assert exc2.value.error_code == "IDEA_PLAN_INVALID_INPUT"
    assert exc2.value.field == "target_ids"
    # Ham ID'lerin mesajda bulunmadığını doğrula
    assert "207" not in exc2.value.message
    assert "207" not in str(exc2.value)

    # 30 target artık kabul edilmemeli -> IDEA_PLAN_INVALID_INPUT
    with pytest.raises(IdeaPlanValidationError) as exc3:
        build_social_idea_generation_plan(
            selected_category_ids=(1,),
            target_ids=tuple(range(1, 31)),
            ideas_per_category=1,
        )
    assert exc3.value.error_code == "IDEA_PLAN_INVALID_INPUT"
    assert exc3.value.field == "target_ids"


def test_17_exception_messages_do_not_leak_raw_user_input():
    """17. Exception mesajlarında ham kullanıcı girdisinin bulunmadığını doğrula."""
    secret_cat_id = 987654321
    with pytest.raises(IdeaPlanValidationError) as exc_cat:
        build_social_idea_generation_plan(
            selected_category_ids=(secret_cat_id, secret_cat_id),
            target_ids=(1,),
            ideas_per_category=3,
        )
    assert str(secret_cat_id) not in str(exc_cat.value)
    assert str(secret_cat_id) not in exc_cat.value.message

    secret_tgt_id = 123456789
    with pytest.raises(IdeaPlanValidationError) as exc_tgt:
        build_social_idea_generation_plan(
            selected_category_ids=(1,),
            target_ids=(secret_tgt_id, secret_tgt_id),
            ideas_per_category=3,
        )
    assert str(secret_tgt_id) not in str(exc_tgt.value)
    assert str(secret_tgt_id) not in exc_tgt.value.message


def test_18_input_tuples_are_not_mutated():
    """18. Fonksiyonun girdi tuple'larını değiştirmediğini doğrula."""
    categories = (10, 20, 30)
    targets = (1, 2, 3, 4)
    cat_copy = (10, 20, 30)
    tgt_copy = (1, 2, 3, 4)

    plan = build_social_idea_generation_plan(
        selected_category_ids=categories,
        target_ids=targets,
        ideas_per_category=3,
    )

    # Girdi tuple'larının içeriği ve referansı korunmalı
    assert categories == cat_copy
    assert targets == tgt_copy
    assert categories == (10, 20, 30)
    assert targets == (1, 2, 3, 4)
    assert plan.total_requested == 9


@pytest.mark.parametrize("num_cats", [1, 2, 3, 4, 5, 6])
@pytest.mark.parametrize("num_targets", [1, 2, 3, 4, 5, 6])
@pytest.mark.parametrize("ideas_per_cat", [1, 2, 3, 4, 5])
def test_19_every_category_and_target_covered_matrix(num_cats: int, num_targets: int, ideas_per_cat: int):
    """19. Matristeki tüm geçerli kategori ve hedef kombinasyonlarında:
    - Her kategori en az 1 fikir alır,
    - Her hedef tüm planda en az 1 kez kapsanır,
    - Toplam kotalar ve planlar tutarlıdır.
    """
    cats = tuple(range(100, 100 + num_cats))
    targets = tuple(range(200, 200 + num_targets))

    total = max(num_cats * ideas_per_cat, num_targets)
    if total > 30:
        with pytest.raises(IdeaPlanValidationError) as exc:
            build_social_idea_generation_plan(
                selected_category_ids=cats,
                target_ids=targets,
                ideas_per_category=ideas_per_cat,
            )
        assert exc.value.error_code == "IDEA_PLAN_LIMIT_EXCEEDED"
        return

    plan = build_social_idea_generation_plan(
        selected_category_ids=cats,
        target_ids=targets,
        ideas_per_category=ideas_per_cat,
    )

    assert plan.total_requested == total
    assert len(plan.category_plans) == num_cats
    assert plan.covered_category_ids == cats
    assert plan.covered_target_ids == targets

    # Her kategori en az 1 fikir almalı
    for cp in plan.category_plans:
        assert cp.requested_count >= 1
        assert cp.requested_count == sum(tq.requested_count for tq in cp.target_quotas)

    # Her hedef en az 1 kez kapsanmalı
    covered_targets = set()
    for cp in plan.category_plans:
        for tq in cp.target_quotas:
            assert tq.requested_count >= 1
            covered_targets.add(tq.target_id)
    assert covered_targets == set(targets)
