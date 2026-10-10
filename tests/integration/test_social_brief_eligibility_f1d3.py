# -*- coding: utf-8 -*-
"""Integration tests for SocialBrief DB Eligibility and Keyword Snapshot (F1-D.3).

Tüm testler gerçek PostgreSQL test DB'sini kullanır.
ScoringRun, BrandProfile, ChannelPool ve Keyword etkileşimlerini doğrular.
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
import inspect
from typing import Tuple

import pytest
from sqlalchemy import event, text

import app.core.social.brief_eligibility as be_mod
from app.core.social.brief_eligibility import (
    EligibleSocialBriefInput,
    EligibleSocialBriefKeyword,
    SocialBriefEligibilityError,
    validate_social_brief_eligibility,
)
from app.core.social.brief_validation import (
    ValidatedSocialBriefInput,
    ValidatedSocialBriefTarget,
)
from app.database.models import (
    BrandProfile,
    ChannelPool,
    Keyword,
    ScoringRun,
    SocialBrief,
    SocialBriefKeyword,
    SocialBriefTarget,
)


def _make_target(
    platform: str = "instagram",
    content_format: str = "post",
    preset_id: str | None = None,
    min_sec: int | None = None,
    max_sec: int | None = None,
) -> ValidatedSocialBriefTarget:
    return ValidatedSocialBriefTarget(
        platform=platform,
        content_format=content_format,
        duration_preset_id=preset_id,
        duration_min_sec=min_sec,
        duration_max_sec=max_sec,
    )


def _make_validated_input(
    scoring_run_id: int,
    keyword_ids: list[int] | Tuple[int, ...],
    targets: Tuple[ValidatedSocialBriefTarget, ...] | None = None,
    brand_name: str | None = "Test Marka",
    brand_context: str | None = "Test Bağlamı",
    format_matrix_version: str = "v1",
) -> ValidatedSocialBriefInput:
    if targets is None:
        targets = (_make_target("instagram", "post"),)
    return ValidatedSocialBriefInput(
        scoring_run_id=scoring_run_id,
        keyword_ids=tuple(keyword_ids),
        targets=targets,
        brand_name=brand_name,
        brand_context=brand_context,
        format_matrix_version=format_matrix_version,
    )


def _setup_fresh_environment(
    db_session,
    make_workspace,
    make_scoring_run,
    make_keyword,
    num_kws: int = 3,
):
    """Merkezi freshness sözleşmesini karşılayan taze bir workspace/run/pool kurgusu oluşturur."""
    workspace = make_workspace(name="Fresh Brand", status="confirmed")
    run = make_scoring_run(
        brand_profile_id=workspace.id,
        status="completed",
        algorithm_version="v2",
        channel_pool_policy_version=workspace.policy_version or 1,
        relevance_anchor_version=workspace.anchor_version or 1,
        channel_assignment_version=1,
        skip_relevance=True,
    )

    keywords = []
    for i in range(num_kws):
        kw = make_keyword(
            text_value=f"anahtar kelime {i + 1}",
            brand_profile_id=workspace.id,
        )
        pool = ChannelPool(
            scoring_run_id=run.id,
            keyword_id=kw.id,
            channel="SOCIAL",
            final_rank=i + 1,
            relevance_score=0.9,
            adjusted_score=15.0 - i,
        )
        db_session.add(pool)
        keywords.append(kw)

    db_session.commit()
    return workspace, run, keywords


# ==================== INTEGRATION TESTLERİ (1-30) ====================

def test_01_valid_workspace_run_fresh_social_pool_accepted(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """1. Geçerli workspace/run/fresh SOCIAL pool isteği başarıyla kabul edilir."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=2
    )
    validated = _make_validated_input(run.id, [kws[0].id, kws[1].id])

    result = validate_social_brief_eligibility(
        db_session, validated=validated, brand_profile_id=ws.id
    )

    assert isinstance(result, EligibleSocialBriefInput)
    assert result.scoring_run_id == run.id
    assert result.brand_profile_id == ws.id
    assert result.channel_assignment_version == 1
    assert len(result.keywords) == 2


def test_02_keyword_snapshot_and_position_preserve_user_order(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """2. Keyword snapshot ve position değerleri kullanıcının sırasını korur (0, 1, 2...)."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=3
    )
    # Kullanıcı sırası: [kw3, kw1, kw2]
    user_order_ids = [kws[2].id, kws[0].id, kws[1].id]
    validated = _make_validated_input(run.id, user_order_ids)

    result = validate_social_brief_eligibility(
        db_session, validated=validated, brand_profile_id=ws.id
    )

    assert len(result.keywords) == 3

    assert result.keywords[0].keyword_id == kws[2].id
    assert result.keywords[0].keyword_snapshot == kws[2].keyword
    assert result.keywords[0].position == 0

    assert result.keywords[1].keyword_id == kws[0].id
    assert result.keywords[1].keyword_snapshot == kws[0].keyword
    assert result.keywords[1].position == 1

    assert result.keywords[2].keyword_id == kws[1].id
    assert result.keywords[2].keyword_snapshot == kws[1].keyword
    assert result.keywords[2].position == 2


def test_03_target_order_preserved(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """3. Target sırası değişmeden korunur."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=1
    )
    t1 = _make_target("twitter", "thread")
    t2 = _make_target("instagram", "reels", "short_1_15", 1, 15)
    t3 = _make_target("linkedin", "post")
    validated = _make_validated_input(run.id, [kws[0].id], targets=(t1, t2, t3))

    result = validate_social_brief_eligibility(
        db_session, validated=validated, brand_profile_id=ws.id
    )

    assert len(result.targets) == 3
    assert result.targets[0] == t1
    assert result.targets[1] == t2
    assert result.targets[2] == t3


def test_04_channel_assignment_version_snapshot(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """4. channel_assignment_version doğru snapshot alınır."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=1
    )
    run.channel_assignment_version = 4
    db_session.commit()

    validated = _make_validated_input(run.id, [kws[0].id])
    result = validate_social_brief_eligibility(
        db_session, validated=validated, brand_profile_id=ws.id
    )

    assert result.channel_assignment_version == 4


def test_05_format_matrix_version_carried(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """5. format_matrix_version taşınır."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=1
    )
    validated = _make_validated_input(run.id, [kws[0].id], format_matrix_version="v1")
    result = validate_social_brief_eligibility(
        db_session, validated=validated, brand_profile_id=ws.id
    )
    assert result.format_matrix_version == "v1"


def test_06_brand_fields_carried_unchanged(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """6. Brand alanları (brand_name, brand_context) değişmeden taşınır."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=1
    )
    validated = _make_validated_input(
        run.id,
        [kws[0].id],
        brand_name="Özel Marka Adı",
        brand_context="Özel Brief Notları",
    )
    result = validate_social_brief_eligibility(
        db_session, validated=validated, brand_profile_id=ws.id
    )
    assert result.brand_name == "Özel Marka Adı"
    assert result.brand_context == "Özel Brief Notları"


def test_07_non_existent_workspace_rejected_run_not_found(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """7. Olmayan workspace RUN_NOT_FOUND ile reddedilir."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=1
    )
    validated = _make_validated_input(run.id, [kws[0].id])

    with pytest.raises(SocialBriefEligibilityError) as exc:
        validate_social_brief_eligibility(
            db_session, validated=validated, brand_profile_id=999999
        )
    assert exc.value.error_code == "RUN_NOT_FOUND"
    assert exc.value.field == "brand_profile_id"


def test_08_archived_workspace_rejected_run_not_found(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """8. Arşivlenmiş (deleted_at IS NOT NULL) workspace RUN_NOT_FOUND ile reddedilir."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=1
    )
    ws.deleted_at = datetime.now(timezone.utc)
    db_session.commit()

    validated = _make_validated_input(run.id, [kws[0].id])

    with pytest.raises(SocialBriefEligibilityError) as exc:
        validate_social_brief_eligibility(
            db_session, validated=validated, brand_profile_id=ws.id
        )
    assert exc.value.error_code == "RUN_NOT_FOUND"


def test_09_non_existent_run_rejected_run_not_found(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """9. Olmayan run RUN_NOT_FOUND ile reddedilir."""
    ws = make_workspace(name="WS Alone")
    validated = _make_validated_input(999999, [1])

    with pytest.raises(SocialBriefEligibilityError) as exc:
        validate_social_brief_eligibility(
            db_session, validated=validated, brand_profile_id=ws.id
        )
    assert exc.value.error_code == "RUN_NOT_FOUND"
    assert exc.value.field == "scoring_run_id"


def test_10_other_workspace_run_rejected_run_not_found(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """10. Başka workspace'in run'ı cross-workspace RUN_NOT_FOUND ile reddedilir."""
    ws_a = make_workspace(name="Workspace A")
    ws_b, run_b, kws_b = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=1
    )
    validated = _make_validated_input(run_b.id, [kws_b[0].id])

    with pytest.raises(SocialBriefEligibilityError) as exc:
        validate_social_brief_eligibility(
            db_session, validated=validated, brand_profile_id=ws_a.id
        )
    assert exc.value.error_code == "RUN_NOT_FOUND"


def test_11_policy_stale_pool_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """11. Policy stale havuz POOL_STALE ile reddedilir."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=1
    )
    # Workspace politika sürümü artırılır, run eski sürümde kalır
    ws.policy_version = 2
    db_session.commit()

    validated = _make_validated_input(run.id, [kws[0].id])

    with pytest.raises(SocialBriefEligibilityError) as exc:
        validate_social_brief_eligibility(
            db_session, validated=validated, brand_profile_id=ws.id
        )
    assert exc.value.error_code == "POOL_STALE"
    assert exc.value.details is not None
    assert exc.value.details.get("policy_stale") is True


def test_12_relevance_stale_pool_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """12. Relevance stale havuz POOL_STALE ile reddedilir (v3 firm_block_sha256 uyumsuzluğu)."""
    ws = make_workspace(name="Relevance WS", status="confirmed")
    run = make_scoring_run(
        brand_profile_id=ws.id,
        status="completed",
        algorithm_version="v3",
        channel_pool_policy_version=ws.policy_version or 1,
        channel_assignment_version=1,
        execution_manifest={"engine_v3": {"firm_block_sha256": "outdated_stale_hash_value"}},
    )
    kw = make_keyword("test kw", brand_profile_id=ws.id)
    pool = ChannelPool(
        scoring_run_id=run.id,
        keyword_id=kw.id,
        channel="SOCIAL",
        final_rank=1,
    )
    db_session.add(pool)
    db_session.commit()

    validated = _make_validated_input(run.id, [kw.id])

    with pytest.raises(SocialBriefEligibilityError) as exc:
        validate_social_brief_eligibility(
            db_session, validated=validated, brand_profile_id=ws.id
        )
    assert exc.value.error_code == "POOL_STALE"
    assert exc.value.details is not None
    assert exc.value.details.get("relevance_stale") is True


def test_13_assignment_in_progress_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """13. Assignment devam ederken (channel_assigning) havuz POOL_STALE kabul edilir."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=1
    )
    run.status = "channel_assigning"
    db_session.commit()

    validated = _make_validated_input(run.id, [kws[0].id])

    with pytest.raises(SocialBriefEligibilityError) as exc:
        validate_social_brief_eligibility(
            db_session, validated=validated, brand_profile_id=ws.id
        )
    assert exc.value.error_code == "POOL_STALE"
    assert exc.value.details is not None
    assert exc.value.details.get("assignment_in_progress") is True


def test_14_pool_stale_details_carries_central_freshness_fields(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """14. POOL_STALE details merkezi freshness alanlarını eksiksiz taşır."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=1
    )
    run.channel_pool_policy_version = 999  # stale
    db_session.commit()

    validated = _make_validated_input(run.id, [kws[0].id])

    with pytest.raises(SocialBriefEligibilityError) as exc:
        validate_social_brief_eligibility(
            db_session, validated=validated, brand_profile_id=ws.id
        )

    details = exc.value.details
    assert isinstance(details, dict)
    assert "channel_pool_stale" in details
    assert "policy_stale" in details
    assert "relevance_stale" in details
    assert "strategy_stale" in details
    assert "screening_context_stale" in details
    assert "assignment_in_progress" in details


def test_15_keyword_not_in_db_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """15. Keyword DB'de yoksa SOCIAL_KEYWORD_NOT_ELIGIBLE üretir."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=1
    )
    validated = _make_validated_input(run.id, [999999])

    with pytest.raises(SocialBriefEligibilityError) as exc:
        validate_social_brief_eligibility(
            db_session, validated=validated, brand_profile_id=ws.id
        )
    assert exc.value.error_code == "SOCIAL_KEYWORD_NOT_ELIGIBLE"
    assert 999999 in exc.value.details["missing_keyword_ids"]


def test_16_keyword_only_in_ads_pool_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """16. Keyword aynı run'ın yalnız ADS pool'undaysa reddedilir."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=1
    )
    ads_kw = make_keyword("ads only kw", brand_profile_id=ws.id)
    ads_pool = ChannelPool(
        scoring_run_id=run.id,
        keyword_id=ads_kw.id,
        channel="ADS",
        final_rank=1,
    )
    db_session.add(ads_pool)
    db_session.commit()

    validated = _make_validated_input(run.id, [ads_kw.id])

    with pytest.raises(SocialBriefEligibilityError) as exc:
        validate_social_brief_eligibility(
            db_session, validated=validated, brand_profile_id=ws.id
        )
    assert exc.value.error_code == "SOCIAL_KEYWORD_NOT_ELIGIBLE"
    assert ads_kw.id in exc.value.details["missing_keyword_ids"]


def test_17_keyword_only_in_seo_pool_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """17. Keyword aynı run'ın yalnız SEO pool'undaysa reddedilir."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=1
    )
    seo_kw = make_keyword("seo only kw", brand_profile_id=ws.id)
    seo_pool = ChannelPool(
        scoring_run_id=run.id,
        keyword_id=seo_kw.id,
        channel="SEO",
        final_rank=1,
    )
    db_session.add(seo_pool)
    db_session.commit()

    validated = _make_validated_input(run.id, [seo_kw.id])

    with pytest.raises(SocialBriefEligibilityError) as exc:
        validate_social_brief_eligibility(
            db_session, validated=validated, brand_profile_id=ws.id
        )
    assert exc.value.error_code == "SOCIAL_KEYWORD_NOT_ELIGIBLE"
    assert seo_kw.id in exc.value.details["missing_keyword_ids"]


def test_18_keyword_in_other_run_social_pool_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """18. Keyword başka run'ın SOCIAL pool'undaysa reddedilir."""
    ws, run_a, kws_a = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=1
    )
    _, run_b, kws_b = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=1
    )

    # run_a için run_b'nin SOCIAL keyword'ü talep edilir
    validated = _make_validated_input(run_a.id, [kws_b[0].id])

    with pytest.raises(SocialBriefEligibilityError) as exc:
        validate_social_brief_eligibility(
            db_session, validated=validated, brand_profile_id=ws.id
        )
    assert exc.value.error_code == "SOCIAL_KEYWORD_NOT_ELIGIBLE"
    assert kws_b[0].id in exc.value.details["missing_keyword_ids"]


def test_19_one_missing_among_multiple_rejects_entire_request(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """19. Birden fazla keyword'den biri eksikse tüm istek fail-closed reddedilir."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=2
    )
    missing_id = 888888
    validated = _make_validated_input(run.id, [kws[0].id, missing_id, kws[1].id])

    with pytest.raises(SocialBriefEligibilityError) as exc:
        validate_social_brief_eligibility(
            db_session, validated=validated, brand_profile_id=ws.id
        )
    assert exc.value.error_code == "SOCIAL_KEYWORD_NOT_ELIGIBLE"


def test_20_missing_keyword_ids_carries_only_requested_missing(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """20. missing_keyword_ids yalnız eksik istenen ID'leri içerir; diğer veriler sızdırılmaz."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=1
    )
    missing_id = 777777
    validated = _make_validated_input(run.id, [kws[0].id, missing_id])

    with pytest.raises(SocialBriefEligibilityError) as exc:
        validate_social_brief_eligibility(
            db_session, validated=validated, brand_profile_id=ws.id
        )

    assert exc.value.details["requested_keyword_ids"] == [kws[0].id, missing_id]
    assert exc.value.details["missing_keyword_ids"] == [missing_id]


def test_21_extra_keywords_in_pool_not_affect_result(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """21. Havuzdaki seçilmemiş ekstra keyword'ler sonucu etkilemez."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=5
    )
    # Kullanıcı 5 keyword'den sadece 2'sini seçer
    validated = _make_validated_input(run.id, [kws[1].id, kws[3].id])

    result = validate_social_brief_eligibility(
        db_session, validated=validated, brand_profile_id=ws.id
    )

    assert len(result.keywords) == 2
    assert result.keywords[0].keyword_id == kws[1].id
    assert result.keywords[1].keyword_id == kws[3].id


def test_22_empty_or_null_keyword_text_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """22. Keyword metni boş/null ise fail-closed SOCIAL_KEYWORD_NOT_ELIGIBLE üretir."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=1
    )
    kws[0].keyword = "   "  # Sadece boşluk
    db_session.commit()

    validated = _make_validated_input(run.id, [kws[0].id])

    with pytest.raises(SocialBriefEligibilityError) as exc:
        validate_social_brief_eligibility(
            db_session, validated=validated, brand_profile_id=ws.id
        )
    assert exc.value.error_code == "SOCIAL_KEYWORD_NOT_ELIGIBLE"
    assert kws[0].id in exc.value.details["missing_keyword_ids"]


def test_23_snapshot_text_preserved_verbatim(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """23. Snapshot metni strip/lower edilmeden canlı Keyword.keyword üzerinden aynen korunur."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=1
    )
    special_text = "  Özel & Karışık İÇERİK ve Trendler!  "
    kws[0].keyword = special_text
    db_session.commit()

    validated = _make_validated_input(run.id, [kws[0].id])
    result = validate_social_brief_eligibility(
        db_session, validated=validated, brand_profile_id=ws.id
    )

    assert result.keywords[0].keyword_snapshot == special_text


def test_24_result_dataclasses_are_immutable(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """24. Sonuç dataclass'ları immutable'dır (FrozenInstanceError)."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=1
    )
    validated = _make_validated_input(run.id, [kws[0].id])
    result = validate_social_brief_eligibility(
        db_session, validated=validated, brand_profile_id=ws.id
    )

    with pytest.raises(dataclasses.FrozenInstanceError):
        result.scoring_run_id = 9999  # type: ignore[misc]

    with pytest.raises(dataclasses.FrozenInstanceError):
        result.keywords[0].position = 99  # type: ignore[misc]


def test_25_validated_input_not_mutated(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """25. Doğrulama sırasında validated input nesnesi mutate edilmez."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=2
    )
    orig_kws = (kws[0].id, kws[1].id)
    t = _make_target("instagram", "post")
    validated = ValidatedSocialBriefInput(
        scoring_run_id=run.id,
        keyword_ids=orig_kws,
        targets=(t,),
        brand_name="Sabit",
        brand_context="Sabit Not",
        format_matrix_version="v1",
    )

    _ = validate_social_brief_eligibility(
        db_session, validated=validated, brand_profile_id=ws.id
    )

    assert validated.scoring_run_id == run.id
    assert validated.keyword_ids == orig_kws
    assert validated.targets == (t,)
    assert validated.brand_name == "Sabit"
    assert validated.brand_context == "Sabit Not"


def test_26_service_does_no_insert_update_delete(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """26. Servis DB'ye hiçbir insert/update/delete yapmaz."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=2
    )

    def _get_counts():
        return (
            db_session.query(BrandProfile).count(),
            db_session.query(ScoringRun).count(),
            db_session.query(ChannelPool).count(),
            db_session.query(Keyword).count(),
            db_session.query(SocialBrief).count(),
            db_session.query(SocialBriefKeyword).count(),
            db_session.query(SocialBriefTarget).count(),
        )

    counts_before = _get_counts()

    validated = _make_validated_input(run.id, [kws[0].id, kws[1].id])
    _ = validate_social_brief_eligibility(
        db_session, validated=validated, brand_profile_id=ws.id
    )

    counts_after = _get_counts()
    assert counts_before == counts_after


def test_27_service_does_not_commit_or_rollback(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """27. Servis commit veya rollback çağırmaz; oturumun transaction durumu korunur."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=1
    )
    # Oturumda henüz commit edilmemiş bir Keyword oluşturulur
    uncommitted_kw = Keyword(
        keyword="uncommitted_kw",
        normalized_keyword="uncommitted_kw",
        monthly_volume=10,
    )
    db_session.add(uncommitted_kw)
    assert uncommitted_kw in db_session.new

    validated = _make_validated_input(run.id, [kws[0].id])
    _ = validate_social_brief_eligibility(
        db_session, validated=validated, brand_profile_id=ws.id
    )

    # Oturum ne commit edilmiş ne de rollback edilmiştir
    assert uncommitted_kw in db_session.new


def test_28_no_ai_or_http_exception_dependencies():
    """28. Modülün AI veya HTTPException bağımlılığı olmadığını doğrula."""
    source = inspect.getsource(be_mod)
    assert "fastapi" not in source, "brief_eligibility modülü fastapi import etmemelidir"
    assert "HTTPException" not in source, "brief_eligibility modülü HTTPException kullanmamalıdır"
    assert "ai_service" not in source, "brief_eligibility modülü AI servisi çağırmamalıdır"
    assert "complete(" not in source, "brief_eligibility modülü LLM tamamlama çağrısı yapmamalıdır"


def test_29_lock_order_brand_profile_then_scoring_run_then_channel_pool(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """29. Kilit sırasının BrandProfile -> ScoringRun -> ChannelPool olduğunu deterministik doğrula."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=2
    )
    validated = _make_validated_input(run.id, [kws[0].id, kws[1].id])

    for_update_queries: list[str] = []

    def capture_sql(conn, cursor, statement, parameters, context, executemany):
        if "FOR UPDATE" in statement.upper():
            for_update_queries.append(statement.lower())

    bind = db_session.get_bind()
    event.listen(bind, "before_cursor_execute", capture_sql)
    try:
        _ = validate_social_brief_eligibility(
            db_session, validated=validated, brand_profile_id=ws.id
        )

        bp_idx = next(i for i, q in enumerate(for_update_queries) if "brand_profiles" in q)
        run_idx = next(i for i, q in enumerate(for_update_queries) if "scoring_runs" in q)
        pool_idx = next(i for i, q in enumerate(for_update_queries) if "channel_pools" in q)

        assert bp_idx < run_idx < pool_idx, (
            f"Kilit sırası hatalı: BrandProfile={bp_idx}, ScoringRun={run_idx}, ChannelPool={pool_idx}"
        )
    finally:
        event.remove(bind, "before_cursor_execute", capture_sql)


def test_30_failed_validation_creates_zero_social_briefs(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """30. Başarısız doğrulamada hiçbir SocialBrief veya alt satırı oluşmaz."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=1
    )

    initial_brief_count = db_session.query(SocialBrief).count()
    initial_keyword_count = db_session.query(SocialBriefKeyword).count()
    initial_target_count = db_session.query(SocialBriefTarget).count()

    # 1. Olmayan workspace hatası
    with pytest.raises(SocialBriefEligibilityError):
        validate_social_brief_eligibility(
            db_session, validated=_make_validated_input(run.id, [kws[0].id]), brand_profile_id=999999
        )

    # 2. Uygun olmayan keyword hatası
    with pytest.raises(SocialBriefEligibilityError):
        validate_social_brief_eligibility(
            db_session, validated=_make_validated_input(run.id, [999999]), brand_profile_id=ws.id
        )

    # 3. POOL_STALE hatası
    run.channel_pool_policy_version = 9999
    db_session.commit()
    with pytest.raises(SocialBriefEligibilityError):
        validate_social_brief_eligibility(
            db_session, validated=_make_validated_input(run.id, [kws[0].id]), brand_profile_id=ws.id
        )

    assert db_session.query(SocialBrief).count() == initial_brief_count
    assert db_session.query(SocialBriefKeyword).count() == initial_keyword_count
    assert db_session.query(SocialBriefTarget).count() == initial_target_count
