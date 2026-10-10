# -*- coding: utf-8 -*-
"""Integration tests for SocialBrief atomic persistence and response serialization (F1-D.4).

Tüm testler gerçek PostgreSQL test DB'sini kullanır.
SocialBrief, SocialBriefKeyword ve SocialBriefTarget kayıtlarının tek transaction
içinde atomik oluşturulmasını ve yanıt modellerine serileştirilmesini doğrular.
"""
from __future__ import annotations

import dataclasses
import inspect
from typing import Tuple
from unittest.mock import patch

import pytest
from sqlalchemy.exc import IntegrityError

import app.core.social.brief_service as bs_mod
from app.core.social.brief_eligibility import (
    EligibleSocialBriefInput,
    EligibleSocialBriefKeyword,
    SocialBriefEligibilityError,
)
from app.core.social.brief_service import (
    create_social_brief,
    social_brief_to_response,
)
from app.core.social.brief_validation import (
    SocialBriefValidationError,
    ValidatedSocialBriefTarget,
)
from app.database.connection import SessionLocal
from app.database.models import (
    BrandProfile,
    ChannelPool,
    Keyword,
    ScoringRun,
    SocialBrief,
    SocialBriefKeyword,
    SocialBriefTarget,
    SocialCategory,
    SocialContent,
    SocialIdea,
)
from app.schemas.social_brief import (
    SocialBriefCreateRequest,
    SocialBriefKeywordResponse,
    SocialBriefResponse,
    SocialBriefTargetCreateRequest,
    SocialBriefTargetResponse,
)


def _setup_fresh_environment(
    db_session,
    make_workspace,
    make_scoring_run,
    make_keyword,
    num_kws: int = 5,
):
    """Merkezi freshness sözleşmesini karşılayan taze bir workspace/run/pool kurgusu oluşturur."""
    workspace = make_workspace(name="Persistence Brand", status="confirmed")
    run = make_scoring_run(
        brand_profile_id=workspace.id,
        status="completed",
        algorithm_version="v2",
        channel_pool_policy_version=workspace.policy_version or 1,
        relevance_anchor_version=workspace.anchor_version or 1,
        channel_assignment_version=2,
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
            adjusted_score=20.0 - i,
        )
        db_session.add(pool)
        keywords.append(kw)

    db_session.commit()
    return workspace, run, keywords


def _valid_request(
    scoring_run_id: int,
    keyword_ids: list[int],
    targets: list[SocialBriefTargetCreateRequest] | None = None,
    brand_name: str | None = "Persistence Test Brand",
    brand_context: str | None = "Brief bağlam notları",
) -> SocialBriefCreateRequest:
    if targets is None:
        targets = [
            SocialBriefTargetCreateRequest(platform="instagram", content_format="post"),
            SocialBriefTargetCreateRequest(
                platform="instagram",
                content_format="reels",
                duration_preset_id="short_16_30",
            ),
        ]
    return SocialBriefCreateRequest(
        scoring_run_id=scoring_run_id,
        keyword_ids=keyword_ids,
        targets=targets,
        brand_name=brand_name,
        brand_context=brand_context,
    )


# ==================== ENTEGRASYON TESTLERİ (1-32) ====================

def test_01_valid_request_creates_one_social_brief(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """1. Geçerli request 1 SocialBrief nesnesi oluşturur ve döndürür."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=2
    )
    req = _valid_request(run.id, [kws[0].id, kws[1].id])

    brief = create_social_brief(db_session, request=req, brand_profile_id=ws.id)

    assert isinstance(brief, SocialBrief)
    assert brief.id is not None
    assert brief.id > 0


def test_02_keyword_count_boundaries_saved_correctly(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """2. 1 ve 5 keyword sınırları doğru şekilde SocialBriefKeyword olarak kaydedilir."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=5
    )

    # 1 keyword
    req_1 = _valid_request(run.id, [kws[0].id])
    brief_1 = create_social_brief(db_session, request=req_1, brand_profile_id=ws.id)
    assert len(brief_1.brief_keywords) == 1

    # 5 keyword
    req_5 = _valid_request(run.id, [k.id for k in kws])
    brief_5 = create_social_brief(db_session, request=req_5, brand_profile_id=ws.id)
    assert len(brief_5.brief_keywords) == 5


def test_03_target_count_boundaries_saved_correctly(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """3. 1 ve 6 target sınırları doğru şekilde SocialBriefTarget olarak kaydedilir."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=1
    )

    # 1 target
    t1 = [SocialBriefTargetCreateRequest(platform="instagram", content_format="post")]
    req_1 = _valid_request(run.id, [kws[0].id], targets=t1)
    brief_1 = create_social_brief(db_session, request=req_1, brand_profile_id=ws.id)
    assert len(brief_1.targets) == 1

    # 6 target
    t6 = [
        SocialBriefTargetCreateRequest(platform="instagram", content_format="post"),
        SocialBriefTargetCreateRequest(platform="instagram", content_format="carousel"),
        SocialBriefTargetCreateRequest(platform="instagram", content_format="story"),
        SocialBriefTargetCreateRequest(platform="twitter", content_format="post"),
        SocialBriefTargetCreateRequest(platform="twitter", content_format="thread"),
        SocialBriefTargetCreateRequest(platform="linkedin", content_format="post"),
    ]
    req_6 = _valid_request(run.id, [kws[0].id], targets=t6)
    brief_6 = create_social_brief(db_session, request=req_6, brand_profile_id=ws.id)
    assert len(brief_6.targets) == 6


def test_04_scoring_run_id_saved_correctly(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """4. scoring_run_id brief kaydına doğru yazılır."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=1
    )
    req = _valid_request(run.id, [kws[0].id])
    brief = create_social_brief(db_session, request=req, brand_profile_id=ws.id)

    assert brief.scoring_run_id == run.id


def test_05_brand_snapshot_fields_preserved_verbatim(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """5. brand_name_snapshot ve brand_context_snapshot alanları birebir korunur."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=1
    )
    req = _valid_request(
        run.id,
        [kws[0].id],
        brand_name="Özel Marka Adı A.Ş.",
        brand_context="Özel Brief Bağlamı ve USP",
    )
    brief = create_social_brief(db_session, request=req, brand_profile_id=ws.id)

    assert brief.brand_name_snapshot == "Özel Marka Adı A.Ş."
    assert brief.brand_context_snapshot == "Özel Brief Bağlamı ve USP"


def test_06_none_brand_fields_saved_as_null(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """6. None verilen brand alanları veritabanına null yazılır."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=1
    )
    req = _valid_request(run.id, [kws[0].id], brand_name=None, brand_context=None)
    brief = create_social_brief(db_session, request=req, brand_profile_id=ws.id)

    assert brief.brand_name_snapshot is None
    assert brief.brand_context_snapshot is None


def test_07_channel_assignment_version_snapshot_from_run(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """7. channel_assignment_version ScoringRun'dan snapshot olarak alınır."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=1
    )
    run.channel_assignment_version = 7
    db_session.commit()

    req = _valid_request(run.id, [kws[0].id])
    brief = create_social_brief(db_session, request=req, brand_profile_id=ws.id)

    assert brief.channel_assignment_version == 7


def test_08_format_matrix_version_saved_as_v1(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """8. format_matrix_version 'v1' olarak yazılır."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=1
    )
    req = _valid_request(run.id, [kws[0].id])
    brief = create_social_brief(db_session, request=req, brand_profile_id=ws.id)

    assert brief.format_matrix_version == "v1"


def test_09_locked_at_is_null_on_creation(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """9. Kategori üretimi başlamadığı için brief oluşturulurken locked_at null olur."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=1
    )
    req = _valid_request(run.id, [kws[0].id])
    brief = create_social_brief(db_session, request=req, brand_profile_id=ws.id)

    assert brief.locked_at is None


def test_10_is_stale_is_false_on_creation(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """10. Yeni oluşturulan brief'in is_stale değeri False olur."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=1
    )
    req = _valid_request(run.id, [kws[0].id])
    brief = create_social_brief(db_session, request=req, brand_profile_id=ws.id)

    assert brief.is_stale is False


def test_11_keyword_positions_zero_based_and_contiguous(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """11. Keyword position değerleri 0'dan başlayıp kesintisiz sıralanır (0, 1, 2...)."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=3
    )
    # Kullanıcı sırası: [kw2, kw0, kw1]
    user_order = [kws[2].id, kws[0].id, kws[1].id]
    req = _valid_request(run.id, user_order)
    brief = create_social_brief(db_session, request=req, brand_profile_id=ws.id)

    sorted_kws = sorted(brief.brief_keywords, key=lambda k: k.position)
    assert len(sorted_kws) == 3
    assert [k.position for k in sorted_kws] == [0, 1, 2]
    assert sorted_kws[0].keyword_id == kws[2].id
    assert sorted_kws[1].keyword_id == kws[0].id
    assert sorted_kws[2].keyword_id == kws[1].id


def test_12_keyword_snapshot_texts_saved_unmodified(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """12. Keyword snapshot metinleri değiştirilmeden, strip/lower yapılmadan saklanır."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=1
    )
    raw_text = "  Özel & Karışık İÇERİK!  "
    kws[0].keyword = raw_text
    db_session.commit()

    req = _valid_request(run.id, [kws[0].id])
    brief = create_social_brief(db_session, request=req, brand_profile_id=ws.id)

    assert brief.brief_keywords[0].keyword_snapshot == raw_text


def test_13_video_target_preset_and_bounds_saved_correctly(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """13. Video target preset/min/max değerleri DB'ye eksiksiz ve doğru yazılır."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=1
    )
    t = [
        SocialBriefTargetCreateRequest(
            platform="instagram", content_format="reels", duration_preset_id="short_16_30"
        ),
        SocialBriefTargetCreateRequest(
            platform="twitter", content_format="video", duration_preset_id="x_91_140"
        ),
    ]
    req = _valid_request(run.id, [kws[0].id], targets=t)
    brief = create_social_brief(db_session, request=req, brand_profile_id=ws.id)

    reels_tgt = next(tgt for tgt in brief.targets if tgt.content_format == "reels")
    assert reels_tgt.duration_preset_id == "short_16_30"
    assert reels_tgt.duration_min_sec == 16
    assert reels_tgt.duration_max_sec == 30

    x_video_tgt = next(tgt for tgt in brief.targets if tgt.content_format == "video")
    assert x_video_tgt.duration_preset_id == "x_91_140"
    assert x_video_tgt.duration_min_sec == 91
    assert x_video_tgt.duration_max_sec == 140


def test_14_non_video_target_duration_fields_are_null(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """14. Non-video target duration alanları veritabanında null olarak saklanır."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=1
    )
    t = [
        SocialBriefTargetCreateRequest(platform="instagram", content_format="post"),
        SocialBriefTargetCreateRequest(platform="twitter", content_format="thread"),
    ]
    req = _valid_request(run.id, [kws[0].id], targets=t)
    brief = create_social_brief(db_session, request=req, brand_profile_id=ws.id)

    for tgt in brief.targets:
        assert tgt.duration_preset_id is None
        assert tgt.duration_min_sec is None
        assert tgt.duration_max_sec is None


def test_15_all_relationships_linked_to_same_brief(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """15. Oluşturulan brief altında tüm child kayıtlarının brief_id referansı eşleşir."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=2
    )
    req = _valid_request(run.id, [kws[0].id, kws[1].id])
    brief = create_social_brief(db_session, request=req, brand_profile_id=ws.id)

    for kw in brief.brief_keywords:
        assert kw.brief_id == brief.id
    for tgt in brief.targets:
        assert tgt.brief_id == brief.id


def test_16_service_result_carries_ids_after_flush(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """16. Servis sonucu tek flush sonrası veritabanı tarafından üretilen ID'leri taşır."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=2
    )
    req = _valid_request(run.id, [kws[0].id, kws[1].id])
    brief = create_social_brief(db_session, request=req, brand_profile_id=ws.id)

    assert brief.id is not None
    assert all(kw.id is not None for kw in brief.brief_keywords)
    assert all(tgt.id is not None for tgt in brief.targets)


def test_17_service_does_not_call_commit(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """17. Servis commit çağırmaz; uncommitted değişiklikler ayrı oturumda görünmez."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=1
    )
    uncommitted_kw = Keyword(keyword="uncommitted", normalized_keyword="uncommitted", monthly_volume=10)
    db_session.add(uncommitted_kw)

    req = _valid_request(run.id, [kws[0].id])
    brief = create_social_brief(db_session, request=req, brand_profile_id=ws.id)

    # brief flush edildiği için ID'si vardır, ancak commit çağrılmadığı için
    # bağımsız bir SessionLocal oturumunda görünmez.
    isolated_session = SessionLocal()
    try:
        assert isolated_session.get(SocialBrief, brief.id) is None
        assert isolated_session.get(Keyword, uncommitted_kw.id) is None
    finally:
        isolated_session.close()


def test_18_service_does_not_call_rollback(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """18. Servis rollback çağırmaz; oturum aktif transaction durumunu korur."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=1
    )
    uncommitted_kw = Keyword(keyword="uncommitted_2", normalized_keyword="uncommitted_2", monthly_volume=10)
    db_session.add(uncommitted_kw)

    req = _valid_request(run.id, [kws[0].id])
    brief = create_social_brief(db_session, request=req, brand_profile_id=ws.id)

    # Rollback çağrılmadığı için brief ve uncommitted_kw oturumda mevcuttur
    assert brief.id is not None
    assert uncommitted_kw.id is not None
    assert db_session.get(Keyword, uncommitted_kw.id) is not None
    assert db_session.get(SocialBrief, brief.id) is not None



def test_19_caller_rollback_removes_brief_and_all_children(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """19. Çağıran rollback yaptığında brief ve tüm child kayıtları veritabanından tamamen silinir."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=2
    )
    req = _valid_request(run.id, [kws[0].id, kws[1].id])
    brief = create_social_brief(db_session, request=req, brand_profile_id=ws.id)
    brief_id = brief.id

    # Çağıran rollback çağırır
    db_session.rollback()

    assert db_session.get(SocialBrief, brief_id) is None
    assert db_session.query(SocialBriefKeyword).filter(SocialBriefKeyword.brief_id == brief_id).count() == 0
    assert db_session.query(SocialBriefTarget).filter(SocialBriefTarget.brief_id == brief_id).count() == 0


def test_20_caller_commit_persists_readable_in_new_session(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """20. Çağıran commit yaptığında kayıtlar yeni ve bağımsız bir Session'dan okunabilir."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=2
    )
    req = _valid_request(run.id, [kws[0].id, kws[1].id])
    brief = create_social_brief(db_session, request=req, brand_profile_id=ws.id)
    brief_id = brief.id

    # Çağıran commit eder
    db_session.commit()

    # Yeni session
    new_session = SessionLocal()
    try:
        loaded = new_session.get(SocialBrief, brief_id)
        assert loaded is not None
        assert len(loaded.brief_keywords) == 2
        assert len(loaded.targets) == 2
    finally:
        new_session.close()


def test_21_eligibility_error_creates_zero_rows(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """21. Eligibility hatasında (ör. SOCIAL havuzunda olmayan keyword) sıfır satır oluşur."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=1
    )
    req = _valid_request(run.id, [999999])

    initial_briefs = db_session.query(SocialBrief).count()
    initial_kws = db_session.query(SocialBriefKeyword).count()
    initial_tgts = db_session.query(SocialBriefTarget).count()

    with pytest.raises(SocialBriefEligibilityError):
        create_social_brief(db_session, request=req, brand_profile_id=ws.id)

    assert db_session.query(SocialBrief).count() == initial_briefs
    assert db_session.query(SocialBriefKeyword).count() == initial_kws
    assert db_session.query(SocialBriefTarget).count() == initial_tgts


def test_22_validation_error_creates_zero_rows(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """22. Saf validator hatasında (ör. 0 keyword) DB'ye hiçbir satır eklenmez."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=1
    )
    req = SocialBriefCreateRequest.model_construct(
        scoring_run_id=run.id,
        keyword_ids=[],  # Geçersiz adet
        targets=[SocialBriefTargetCreateRequest(platform="instagram", content_format="post")],
    )

    initial_briefs = db_session.query(SocialBrief).count()

    with pytest.raises(SocialBriefValidationError):
        create_social_brief(db_session, request=req, brand_profile_id=ws.id)

    assert db_session.query(SocialBrief).count() == initial_briefs


def test_23_child_constraint_violation_raises_integrity_error(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """23. Child constraint ihlalinde (yalnız test içi monkeypatch ile) flush IntegrityError üretir."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=1
    )
    req = _valid_request(run.id, [kws[0].id])

    # Sadece bu teste özel: eligibility çıktısında DB CheckConstraint'ini ihlal eden bir target enjekte edilir
    # (ck_social_brief_targets_duration_bounds: min >= max ihlali)
    invalid_target = ValidatedSocialBriefTarget(
        platform="instagram",
        content_format="reels",
        duration_preset_id="short_16_30",
        duration_min_sec=30,
        duration_max_sec=16,  # max < min -> DB CHECK ihlali!
    )
    mock_eligible = EligibleSocialBriefInput(
        scoring_run_id=run.id,
        brand_profile_id=ws.id,
        channel_assignment_version=1,
        format_matrix_version="v1",
        brand_name="Test",
        brand_context=None,
        keywords=(EligibleSocialBriefKeyword(kws[0].id, kws[0].keyword, 0),),
        targets=(invalid_target,),
    )

    with patch("app.core.social.brief_service.validate_social_brief_eligibility", return_value=mock_eligible):
        with pytest.raises(IntegrityError):
            create_social_brief(db_session, request=req, brand_profile_id=ws.id)


def test_24_integrity_error_rollback_leaves_no_partial_rows(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """24. IntegrityError sonrası çağıran rollback yaptığında hiçbir kısmi kayıt kalmaz."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=1
    )
    req = _valid_request(run.id, [kws[0].id])

    initial_briefs = db_session.query(SocialBrief).count()
    initial_kws = db_session.query(SocialBriefKeyword).count()
    initial_tgts = db_session.query(SocialBriefTarget).count()

    invalid_target = ValidatedSocialBriefTarget(
        platform="instagram",
        content_format="reels",
        duration_preset_id="short_16_30",
        duration_min_sec=50,
        duration_max_sec=10,  # DB check ihlali
    )
    mock_eligible = EligibleSocialBriefInput(
        scoring_run_id=run.id,
        brand_profile_id=ws.id,
        channel_assignment_version=1,
        format_matrix_version="v1",
        brand_name="Test",
        brand_context=None,
        keywords=(EligibleSocialBriefKeyword(kws[0].id, kws[0].keyword, 0),),
        targets=(invalid_target,),
    )

    with patch("app.core.social.brief_service.validate_social_brief_eligibility", return_value=mock_eligible):
        with pytest.raises(IntegrityError):
            create_social_brief(db_session, request=req, brand_profile_id=ws.id)

    db_session.rollback()

    assert db_session.query(SocialBrief).count() == initial_briefs
    assert db_session.query(SocialBriefKeyword).count() == initial_kws
    assert db_session.query(SocialBriefTarget).count() == initial_tgts


def test_25_same_session_and_transaction_used_end_to_end(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """25. Aynı Session ve transaction eligibility'den persistence'a kadar kesintisiz kullanılır."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=1
    )
    req = _valid_request(run.id, [kws[0].id])

    brief = create_social_brief(db_session, request=req, brand_profile_id=ws.id)

    # brief nesnesi db_session içinde aktif oturum nesnesidir
    assert brief in db_session


def test_26_keyword_order_preserved_via_db_positions(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """26. Keyword sırası DB position değerleriyle (0, 1, 2...) kalıcı olarak korunur."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=4
    )
    # Ters sırada talep
    requested_ids = [kws[3].id, kws[1].id, kws[2].id, kws[0].id]
    req = _valid_request(run.id, requested_ids)

    brief = create_social_brief(db_session, request=req, brand_profile_id=ws.id)
    db_session.commit()

    # Yeni session ile DB'den tekrar sorgula
    fresh_session = SessionLocal()
    try:
        reloaded_brief = fresh_session.get(SocialBrief, brief.id)
        assert reloaded_brief is not None
        sorted_kws = sorted(reloaded_brief.brief_keywords, key=lambda k: k.position)
        assert [k.keyword_id for k in sorted_kws] == requested_ids
        assert [k.position for k in sorted_kws] == [0, 1, 2, 3]
    finally:
        fresh_session.close()


def test_27_target_response_order_is_deterministic_by_id(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """27. Target response sırası ID'ye göre deterministiktir."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=1
    )
    t = [
        SocialBriefTargetCreateRequest(platform="twitter", content_format="post"),
        SocialBriefTargetCreateRequest(platform="instagram", content_format="story"),
        SocialBriefTargetCreateRequest(platform="linkedin", content_format="post"),
    ]
    req = _valid_request(run.id, [kws[0].id], targets=t)
    brief = create_social_brief(db_session, request=req, brand_profile_id=ws.id)

    resp = social_brief_to_response(brief)
    target_ids = [tgt.id for tgt in resp.targets]
    assert target_ids == sorted(target_ids)


def test_28_serializer_populates_all_response_fields(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """28. Serializer response alanlarını eksiksiz doldurur."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=2
    )
    req = _valid_request(
        run.id,
        [kws[0].id, kws[1].id],
        brand_name="Test Markası",
        brand_context="Test Bağlamı",
    )
    brief = create_social_brief(db_session, request=req, brand_profile_id=ws.id)

    resp = social_brief_to_response(brief)

    assert isinstance(resp, SocialBriefResponse)
    assert resp.id == brief.id
    assert resp.scoring_run_id == run.id
    assert resp.brand_name_snapshot == "Test Markası"
    assert resp.brand_context_snapshot == "Test Bağlamı"
    assert resp.channel_assignment_version == brief.channel_assignment_version
    assert resp.format_matrix_version == "v1"
    assert resp.locked_at is None
    assert resp.is_stale is False
    assert len(resp.keywords) == 2
    assert len(resp.targets) == 2
    assert isinstance(resp.keywords[0], SocialBriefKeywordResponse)
    assert isinstance(resp.targets[0], SocialBriefTargetResponse)


def test_29_serializer_does_not_mutate_orm_instance(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """29. Serializer ORM nesnesini mutate etmez."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=1
    )
    req = _valid_request(run.id, [kws[0].id])
    brief = create_social_brief(db_session, request=req, brand_profile_id=ws.id)

    orig_brief_id = brief.id
    orig_kw_count = len(brief.brief_keywords)
    orig_target_count = len(brief.targets)

    _ = social_brief_to_response(brief)

    assert brief.id == orig_brief_id
    assert len(brief.brief_keywords) == orig_kw_count
    assert len(brief.targets) == orig_target_count


def test_30_serializer_fail_closed_on_null_keyword_id(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """30. Serializer null keyword_id bulunan bozuk kayıt için fail-closed ValueError üretir."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=1
    )
    req = _valid_request(run.id, [kws[0].id])
    brief = create_social_brief(db_session, request=req, brand_profile_id=ws.id)

    # Bozuk kayıt simülasyonu
    brief.brief_keywords[0].keyword_id = None

    with pytest.raises(ValueError) as exc:
        social_brief_to_response(brief)
    assert "keyword_id alanı null olamaz" in str(exc.value)


def test_31_legacy_brief_id_null_records_untouched(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """31. Legacy brief_id IS NULL olan kategori/fikir/içerik kayıtlarına dokunulmaz."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=1
    )

    # Legacy kategori ve fikir oluştur
    cat = SocialCategory(
        scoring_run_id=run.id,
        brief_id=None,
        category_name="Legacy Eğitim",
        category_type="educational",
        is_stale=False,
    )
    db_session.add(cat)
    db_session.flush()

    idea = SocialIdea(
        category_id=cat.id,
        keyword_id=kws[0].id,
        brief_id=None,
        idea_title="Legacy Fikir",
        idea_description="Legacy Açıklama",
        is_stale=False,
    )
    db_session.add(idea)
    db_session.commit()

    # Yeni brief oluştur
    req = _valid_request(run.id, [kws[0].id])
    brief = create_social_brief(db_session, request=req, brand_profile_id=ws.id)
    db_session.commit()

    # Legacy satırlar null kalmaya devam eder
    db_session.refresh(cat)
    db_session.refresh(idea)
    assert cat.brief_id is None
    assert idea.brief_id is None


def test_32_no_ai_calls_in_persistence_service():
    """32. Persistence servisinde gerçek AI çağrısı veya AI modülü importu olmadığını doğrula."""
    source = inspect.getsource(bs_mod)
    assert "ai_service" not in source, "brief_service modülü ai_service import etmemelidir"
    assert "google-generativeai" not in source, "brief_service modülü generativeai kullanmamalıdır"
    assert "complete(" not in source, "brief_service modülü LLM çağrısı yapmamalıdır"
    assert "fastapi" not in source, "brief_service modülü fastapi import etmemelidir"
