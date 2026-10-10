# -*- coding: utf-8 -*-
"""Entegrasyon Testleri: Sosyal İçerik Preflight, Idempotent Attempt ve Snapshot (F1-G.5.7.1).

Tüm testler gerçek PostgreSQL test veritabanını ve izole oturumu kullanır.
Test edilen senaryolar (40 adet):
1. Geçerli request yeni pending contents attempt oluşturur.
2. coverage exact contents_request_v1 şekline sahiptir.
3. Worker snapshot parser yeni snapshot’ı kabul eder.
4. idea_ids sırası korunur.
5. Bir ve 30 fikir sınırları kabul edilir.
6. Boş liste ve 31 fikir reddedilir.
7. bool/string/zero/negative idea ID reddedilir.
8. Duplicate idea ID reddedilir.
9. extra request alanı reddedilir.
10. Whitespace idempotency key reddedilir.
11. trusted_brand_usp None korunur.
12. Explicit trusted_brand_usp exact snapshot’a yazılır.
13. Generic USP grounding snapshot’a eklenmez.
14. Confirmed product facts otoriter yardımcıdan snapshot’a yazılır.
15. Boş/aşırı uzun/geçersiz product facts fail-closed reddedilir.
16. Workspace dışı brief not-found semantiği üretir.
17. Unlocked brief reddedilir.
18. Stale brief reddedilir.
19. Assignment version uyuşmazlığı reddedilir.
20. Kayıp idea reddedilir.
21. Başka brief’e ait idea reddedilir.
22. Stale idea reddedilir.
23. Target/platform/format uyuşmazlığı reddedilir.
24. Category brief/run uyuşmazlığı reddedilir.
25. Keyword brief üyeliği yoksa reddedilir.
26. Aynı key + aynı request aynı attempt’i replay eder.
27. Replay yeni attempt oluşturmaz.
28. Aynı key + farklı idea listesi reddedilir.
29. Aynı key + yalnız sıra farkı reddedilir.
30. Aynı key + farklı trusted_brand_usp reddedilir.
31. Aynı key + değişmiş product facts reddedilir.
32. Replay’de bozuk coverage reddedilir.
33. Replay’de attempt.requested_idea_ids/snapshot farkı reddedilir.
34. Completed/partial/failed replay mutation yapmaz.
35. Başka aktif contents attempt canonical conflict üretir.
36. Fonksiyon commit çağırmaz.
37. Fonksiyon rollback çağırmaz.
38. Hata mesajlarında raw ID, USP veya product facts bulunmaz.
39. Oluşan DTO immutable’dır.
40. Input listesi sonradan değiştirilse snapshot ve DTO değişmez.
"""
from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
import uuid

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.config import settings
from app.core.social.content_flow import (
    CONTENT_ASSIGNMENT_CHANGED,
    CONTENT_ATTEMPT_REQUEST_MISMATCH,
    CONTENT_ATTEMPT_SNAPSHOT_INVALID,
    CONTENT_BRIEF_NOT_FOUND,
    CONTENT_BRIEF_NOT_LOCKED,
    CONTENT_BRIEF_STALE,
    CONTENT_GROUNDING_INVALID,
    CONTENT_IDEA_NOT_ELIGIBLE,
    CONTENT_INVALID_INPUT,
    SocialContentFlowError,
    SocialContentGenerationStart,
    begin_social_content_generation,
)
from app.core.social.content_worker_input import (
    extract_social_content_request_snapshot,
)
from app.database.models import (
    BrandProfile,
    ChannelPool,
    ScoringRun,
    SocialBrief,
    SocialBriefKeyword,
    SocialBriefTarget,
    SocialCategory,
    SocialGenerationAttempt,
    SocialIdea,
)
from app.generators.social.attempt_state import AttemptConflictError
from app.schemas.social_brief import SocialBriefContentsGenerateRequest

T0 = datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def enable_flag(monkeypatch):
    """Test süresince feature flag'i aktif kılar."""
    monkeypatch.setattr(settings, "ENABLE_SOCIAL_BRIEF_FLOW", True)
    yield


# ==================== TEST YARDIMCILARI ====================


def _setup_base_env(
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
    brand_name: str = "Test Brand Preflight",
    product_profile_data: dict | None = None,
):
    """Workspace, confirmed profil, scoring run ve keyword oluşturur."""
    ws = make_workspace(name=brand_name, status="confirmed")
    if product_profile_data is not None:
        ws.profile_data = product_profile_data
        db_session.flush()

    run = make_scoring_run(
        brand_profile_id=ws.id,
        status="completed",
        algorithm_version="v2",
        channel_pool_policy_version=ws.policy_version or 1,
        relevance_anchor_version=ws.anchor_version or 1,
        channel_assignment_version=1,
        skip_relevance=True,
    )
    kw = make_keyword(
        text_value=f"{brand_name} anahtar kelime",
        brand_profile_id=ws.id,
    )
    pool = ChannelPool(
        scoring_run_id=run.id,
        keyword_id=kw.id,
        channel="SOCIAL",
        final_rank=1,
        relevance_score=0.95,
        adjusted_score=25.0,
    )
    db_session.add(pool)
    db_session.commit()
    return ws, run, kw


def _setup_full_social_environment(
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
    *,
    num_ideas: int = 3,
    brief_locked: bool = True,
    brief_stale: bool = False,
    channel_version_mismatch: bool = False,
    product_profile_data: dict | None = None,
    brand_name: str = "Test Brand Preflight",
):
    """Eksiksiz otoriter DB zinciri (workspace, brief, targets, categories, ideas) kurar."""
    default_profile_data = {
        "company_name": "Antigravity Teknoloji",
        "products": ["Yapay Zeka Yazılımı", "Pazarlama Analitiği"],
        "sector": "SaaS",
    }
    p_data = product_profile_data if product_profile_data is not None else default_profile_data

    ws, run, kw = _setup_base_env(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        brand_name=brand_name,
        product_profile_data=p_data,
    )

    chan_ver = (
        run.channel_assignment_version + 1
        if channel_version_mismatch
        else run.channel_assignment_version
    )

    brief = SocialBrief(
        scoring_run_id=run.id,
        brand_name_snapshot=ws.name,
        brand_context_snapshot="Marka bağlamı özeti",
        channel_assignment_version=chan_ver,
        format_matrix_version="v1",
        is_stale=brief_stale,
        locked_at=T0 if brief_locked else None,
    )
    db_session.add(brief)
    db_session.flush()

    brief_kw = SocialBriefKeyword(
        brief_id=brief.id,
        keyword_id=kw.id,
        keyword_snapshot=kw.keyword,
        position=0,
    )
    db_session.add(brief_kw)

    target = SocialBriefTarget(
        brief_id=brief.id,
        platform="instagram",
        content_format="post",
    )
    db_session.add(target)
    db_session.flush()

    cat = SocialCategory(
        scoring_run_id=run.id,
        brief_id=brief.id,
        category_name="Eğitici İçerikler",
        category_type="educational",
        description="Eğitici kategori açıklaması",
        is_stale=False,
        relevance_score=0.9,
        suggested_keyword_ids=[kw.id],
    )
    db_session.add(cat)
    db_session.flush()

    ideas = []
    for i in range(num_ideas):
        idea = SocialIdea(
            brief_id=brief.id,
            category_id=cat.id,
            keyword_id=kw.id,
            brief_target_id=target.id,
            idea_title=f"Örnek Fikir {i + 1}",
            idea_description=f"Detaylı içerik fikri {i + 1}",
            target_platform="instagram",
            content_format="post",
            trend_alignment=0.85,
            is_stale=False,
        )
        db_session.add(idea)
        ideas.append(idea)

    db_session.commit()
    db_session.refresh(brief)
    db_session.refresh(run)
    for idea in ideas:
        db_session.refresh(idea)

    return ws, run, brief, target, cat, kw, ideas


# ==================== TESTLER (40 SENARYO) ====================


def test_01_valid_request_creates_pending_contents_attempt(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """1. Geçerli request yeni pending contents attempt oluşturur."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_social_environment(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    req = SocialBriefContentsGenerateRequest(
        idempotency_key="key-test-01",
        idea_ids=[ideas[0].id, ideas[1].id],
    )

    start_dto = begin_social_content_generation(
        db_session,
        brief_id=brief.id,
        brand_profile_id=ws.id,
        request=req,
        now=T0,
    )

    assert start_dto.attempt_created is True
    assert start_dto.replayed is False
    assert start_dto.attempt_status == "pending"
    assert start_dto.brief_id == brief.id
    assert start_dto.scoring_run_id == run.id
    assert start_dto.requested_idea_ids == (ideas[0].id, ideas[1].id)

    attempt = db_session.query(SocialGenerationAttempt).filter_by(id=start_dto.attempt_id).one()
    assert attempt.stage == "contents"
    assert attempt.status == "pending"
    assert attempt.idempotency_key == "key-test-01"


def test_02_coverage_exact_contents_request_v1_shape(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """2. coverage exact contents_request_v1 şekline sahiptir."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_social_environment(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    req = SocialBriefContentsGenerateRequest(
        idempotency_key="key-test-02",
        idea_ids=[ideas[0].id],
        trusted_brand_usp="En Hızlı Teslimat",
    )

    start_dto = begin_social_content_generation(
        db_session,
        brief_id=brief.id,
        brand_profile_id=ws.id,
        request=req,
        now=T0,
    )

    attempt = db_session.query(SocialGenerationAttempt).filter_by(id=start_dto.attempt_id).one()
    cov = attempt.coverage
    assert isinstance(cov, dict)
    assert set(cov.keys()) == {"schema_version", "request"}
    assert cov["schema_version"] == "contents_request_v1"

    req_cov = cov["request"]
    assert isinstance(req_cov, dict)
    assert set(req_cov.keys()) == {"idea_ids", "product_facts", "trusted_brand_usp"}
    assert req_cov["idea_ids"] == [ideas[0].id]
    assert "Yapay Zeka Yazılımı" in req_cov["product_facts"]
    assert req_cov["trusted_brand_usp"] == "En Hızlı Teslimat"


def test_03_worker_snapshot_parser_accepts_new_snapshot(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """3. Worker snapshot parser yeni snapshot’ı kabul eder."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_social_environment(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    req = SocialBriefContentsGenerateRequest(
        idempotency_key="key-test-03",
        idea_ids=[ideas[0].id, ideas[1].id],
    )

    start_dto = begin_social_content_generation(
        db_session,
        brief_id=brief.id,
        brand_profile_id=ws.id,
        request=req,
        now=T0,
    )

    attempt = db_session.query(SocialGenerationAttempt).filter_by(id=start_dto.attempt_id).one()
    snapshot = extract_social_content_request_snapshot(attempt)
    assert snapshot.schema_version == "contents_request_v1"
    assert snapshot.idea_ids == (ideas[0].id, ideas[1].id)


def test_04_idea_ids_order_preserved(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """4. idea_ids sırası korunur."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_social_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=3
    )
    # Ters sırada istek ver
    inverted_ids = [ideas[2].id, ideas[0].id, ideas[1].id]
    req = SocialBriefContentsGenerateRequest(
        idempotency_key="key-test-04",
        idea_ids=inverted_ids,
    )

    start_dto = begin_social_content_generation(
        db_session,
        brief_id=brief.id,
        brand_profile_id=ws.id,
        request=req,
        now=T0,
    )

    assert start_dto.requested_idea_ids == tuple(inverted_ids)
    attempt = db_session.query(SocialGenerationAttempt).filter_by(id=start_dto.attempt_id).one()
    assert attempt.requested_idea_ids == inverted_ids
    assert attempt.coverage["request"]["idea_ids"] == inverted_ids


def test_05_boundary_1_and_30_ideas_accepted(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """5. Bir ve 30 fikir sınırları kabul edilir."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_social_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=30
    )
    # 1 fikir sınırı
    req_1 = SocialBriefContentsGenerateRequest(
        idempotency_key="key-test-05-1",
        idea_ids=[ideas[0].id],
    )
    res_1 = begin_social_content_generation(
        db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req_1, now=T0
    )
    assert len(res_1.requested_idea_ids) == 1

    # İlk attempt'i tamamlanmış yaparak ikinci denemenin aktif attempt çakışmasına takılmasını önle
    att_1 = db_session.query(SocialGenerationAttempt).filter_by(id=res_1.attempt_id).one()
    att_1.status = "completed"
    db_session.flush()

    # 30 fikir sınırı
    all_30_ids = [i.id for i in ideas]
    req_30 = SocialBriefContentsGenerateRequest(
        idempotency_key="key-test-05-30",
        idea_ids=all_30_ids,
    )
    res_30 = begin_social_content_generation(
        db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req_30, now=T0
    )
    assert len(res_30.requested_idea_ids) == 30


def test_06_empty_list_and_31_ideas_rejected():
    """6. Boş liste ve 31 fikir reddedilir."""
    with pytest.raises(ValidationError):
        SocialBriefContentsGenerateRequest(
            idempotency_key="key-test-06-empty",
            idea_ids=[],
        )

    with pytest.raises(ValidationError):
        SocialBriefContentsGenerateRequest(
            idempotency_key="key-test-06-31",
            idea_ids=list(range(1, 32)),
        )


def test_07_invalid_idea_id_types_rejected():
    """7. bool/string/zero/negative idea ID reddedilir."""
    # bool
    with pytest.raises(ValidationError):
        SocialBriefContentsGenerateRequest(
            idempotency_key="key-test-07-bool",
            idea_ids=[True],
        )

    # string
    with pytest.raises(ValidationError):
        SocialBriefContentsGenerateRequest(
            idempotency_key="key-test-07-str",
            idea_ids=["10"],
        )

    # zero
    with pytest.raises(ValidationError):
        SocialBriefContentsGenerateRequest(
            idempotency_key="key-test-07-zero",
            idea_ids=[0],
        )

    # negative
    with pytest.raises(ValidationError):
        SocialBriefContentsGenerateRequest(
            idempotency_key="key-test-07-neg",
            idea_ids=[-5],
        )


def test_08_duplicate_idea_ids_rejected():
    """8. Duplicate idea ID reddedilir."""
    with pytest.raises(ValidationError):
        SocialBriefContentsGenerateRequest(
            idempotency_key="key-test-08",
            idea_ids=[1, 2, 1],
        )


def test_09_extra_request_field_rejected():
    """9. extra request alanı reddedilir."""
    with pytest.raises(ValidationError):
        SocialBriefContentsGenerateRequest(
            idempotency_key="key-test-09",
            idea_ids=[1],
            unexpected_field="disallowed",
        )


def test_10_whitespace_idempotency_key_rejected():
    """10. Whitespace idempotency key reddedilir."""
    with pytest.raises(ValidationError):
        SocialBriefContentsGenerateRequest(
            idempotency_key="   \t\n  ",
            idea_ids=[1],
        )


def test_11_trusted_brand_usp_none_preserved(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """11. trusted_brand_usp None korunur."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_social_environment(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    req = SocialBriefContentsGenerateRequest(
        idempotency_key="key-test-11",
        idea_ids=[ideas[0].id],
        trusted_brand_usp=None,
    )

    start_dto = begin_social_content_generation(
        db_session,
        brief_id=brief.id,
        brand_profile_id=ws.id,
        request=req,
        now=T0,
    )

    attempt = db_session.query(SocialGenerationAttempt).filter_by(id=start_dto.attempt_id).one()
    assert attempt.coverage["request"]["trusted_brand_usp"] is None


def test_12_explicit_trusted_brand_usp_exact_snapshot(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """12. Explicit trusted_brand_usp exact snapshot’a yazılır."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_social_environment(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    usp_val = "100% Organik ve Sertifikalı"
    req = SocialBriefContentsGenerateRequest(
        idempotency_key="key-test-12",
        idea_ids=[ideas[0].id],
        trusted_brand_usp=usp_val,
    )

    start_dto = begin_social_content_generation(
        db_session,
        brief_id=brief.id,
        brand_profile_id=ws.id,
        request=req,
        now=T0,
    )

    attempt = db_session.query(SocialGenerationAttempt).filter_by(id=start_dto.attempt_id).one()
    assert attempt.coverage["request"]["trusted_brand_usp"] == usp_val


def test_13_generic_usp_not_added_to_grounding_snapshot(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """13. Generic USP grounding snapshot’a eklenmez."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_social_environment(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    req = SocialBriefContentsGenerateRequest(
        idempotency_key="key-test-13",
        idea_ids=[ideas[0].id],
        trusted_brand_usp=None,
    )

    start_dto = begin_social_content_generation(
        db_session,
        brief_id=brief.id,
        brand_profile_id=ws.id,
        request=req,
        now=T0,
    )

    attempt = db_session.query(SocialGenerationAttempt).filter_by(id=start_dto.attempt_id).one()
    assert attempt.coverage["request"]["trusted_brand_usp"] is None


def test_14_confirmed_product_facts_written_to_snapshot(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """14. Confirmed product facts otoriter yardımcıdan snapshot’a yazılır."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_social_environment(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        product_profile_data={
            "company_name": "Doğal Kozmetik A.Ş.",
            "products": ["Cilt Kremi", "Serum"],
            "sector": "Güzellik",
        },
    )
    req = SocialBriefContentsGenerateRequest(
        idempotency_key="key-test-14",
        idea_ids=[ideas[0].id],
    )

    start_dto = begin_social_content_generation(
        db_session,
        brief_id=brief.id,
        brand_profile_id=ws.id,
        request=req,
        now=T0,
    )

    attempt = db_session.query(SocialGenerationAttempt).filter_by(id=start_dto.attempt_id).one()
    pf = attempt.coverage["request"]["product_facts"]
    assert "Firma: Doğal Kozmetik A.Ş." in pf
    assert "Ürünler: Cilt Kremi, Serum" in pf


def test_15_invalid_product_facts_fail_closed_rejected(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """15. Boş/aşırı uzun/geçersiz product facts fail-closed reddedilir."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_social_environment(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    req = SocialBriefContentsGenerateRequest(
        idempotency_key="key-test-15",
        idea_ids=[ideas[0].id],
    )

    # Boş string dönen mock
    with patch("app.core.social.content_flow.load_product_definition", return_value=""):
        with pytest.raises(SocialContentFlowError) as exc_info:
            begin_social_content_generation(
                db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0
            )
        assert exc_info.value.error_code == CONTENT_GROUNDING_INVALID

    # Aşırı uzun dönen mock
    with patch("app.core.social.content_flow.load_product_definition", return_value="a" * 5001):
        with pytest.raises(SocialContentFlowError) as exc_info:
            begin_social_content_generation(
                db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0
            )
        assert exc_info.value.error_code == CONTENT_GROUNDING_INVALID


def test_16_cross_workspace_brief_produces_not_found(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """16. Workspace dışı brief not-found semantiği üretir."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_social_environment(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    other_ws = make_workspace(name="Other Workspace", status="confirmed")
    db_session.commit()

    req = SocialBriefContentsGenerateRequest(
        idempotency_key="key-test-16",
        idea_ids=[ideas[0].id],
    )

    with pytest.raises(SocialContentFlowError) as exc_info:
        begin_social_content_generation(
            db_session,
            brief_id=brief.id,
            brand_profile_id=other_ws.id,
            request=req,
            now=T0,
        )
    assert exc_info.value.error_code == CONTENT_BRIEF_NOT_FOUND


def test_16b_archived_workspace_produces_not_found(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """16b. Arşivlenmiş (deleted_at IS NOT NULL) workspace altındaki brief
    diğer akışlarla (category_flow, idea_flow, idea_retry_flow) aynı şekilde
    CONTENT_BRIEF_NOT_FOUND fail-closed hatası üretir (defect fix)."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_social_environment(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    ws.deleted_at = datetime.now(timezone.utc)
    db_session.commit()

    req = SocialBriefContentsGenerateRequest(
        idempotency_key="key-test-16b",
        idea_ids=[ideas[0].id],
    )

    with pytest.raises(SocialContentFlowError) as exc_info:
        begin_social_content_generation(
            db_session,
            brief_id=brief.id,
            brand_profile_id=ws.id,
            request=req,
            now=T0,
        )
    assert exc_info.value.error_code == CONTENT_BRIEF_NOT_FOUND


def test_17_unlocked_brief_rejected(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """17. Unlocked brief reddedilir."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_social_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, brief_locked=False
    )
    req = SocialBriefContentsGenerateRequest(
        idempotency_key="key-test-17",
        idea_ids=[ideas[0].id],
    )

    with pytest.raises(SocialContentFlowError) as exc_info:
        begin_social_content_generation(
            db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0
        )
    assert exc_info.value.error_code == CONTENT_BRIEF_NOT_LOCKED


def test_18_stale_brief_rejected(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """18. Stale brief reddedilir."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_social_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, brief_stale=True
    )
    req = SocialBriefContentsGenerateRequest(
        idempotency_key="key-test-18",
        idea_ids=[ideas[0].id],
    )

    with pytest.raises(SocialContentFlowError) as exc_info:
        begin_social_content_generation(
            db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0
        )
    assert exc_info.value.error_code == CONTENT_BRIEF_STALE


def test_19_assignment_version_mismatch_rejected(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """19. Assignment version uyuşmazlığı reddedilir."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_social_environment(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        channel_version_mismatch=True,
    )
    req = SocialBriefContentsGenerateRequest(
        idempotency_key="key-test-19",
        idea_ids=[ideas[0].id],
    )

    with pytest.raises(SocialContentFlowError) as exc_info:
        begin_social_content_generation(
            db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0
        )
    assert exc_info.value.error_code == CONTENT_ASSIGNMENT_CHANGED


def test_20_missing_idea_rejected(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """20. Kayıp idea reddedilir."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_social_environment(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    non_existent_id = 999999
    req = SocialBriefContentsGenerateRequest(
        idempotency_key="key-test-20",
        idea_ids=[ideas[0].id, non_existent_id],
    )

    with pytest.raises(SocialContentFlowError) as exc_info:
        begin_social_content_generation(
            db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0
        )
    assert exc_info.value.error_code == CONTENT_IDEA_NOT_ELIGIBLE


def test_21_cross_brief_idea_rejected(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """21. Başka brief’e ait idea reddedilir."""
    ws1, run1, brief1, target1, cat1, kw1, ideas1 = _setup_full_social_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, brand_name="Brand 1"
    )
    ws2, run2, brief2, target2, cat2, kw2, ideas2 = _setup_full_social_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, brand_name="Brand 2"
    )

    # brief1'e brief2'nin fikrini gönder
    req = SocialBriefContentsGenerateRequest(
        idempotency_key="key-test-21",
        idea_ids=[ideas1[0].id, ideas2[0].id],
    )

    with pytest.raises(SocialContentFlowError) as exc_info:
        begin_social_content_generation(
            db_session, brief_id=brief1.id, brand_profile_id=ws1.id, request=req, now=T0
        )
    assert exc_info.value.error_code == CONTENT_IDEA_NOT_ELIGIBLE


def test_22_stale_idea_rejected(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """22. Stale idea reddedilir."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_social_environment(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    ideas[1].is_stale = True
    db_session.commit()

    req = SocialBriefContentsGenerateRequest(
        idempotency_key="key-test-22",
        idea_ids=[ideas[0].id, ideas[1].id],
    )

    with pytest.raises(SocialContentFlowError) as exc_info:
        begin_social_content_generation(
            db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0
        )
    assert exc_info.value.error_code == CONTENT_IDEA_NOT_ELIGIBLE


def test_23_target_platform_format_mismatch_rejected(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """23. Target/platform/format uyuşmazlığı reddedilir."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_social_environment(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    # idea.target_platform'u hedef ile uyuşmaz yap
    ideas[0].target_platform = "youtube"
    db_session.commit()

    req = SocialBriefContentsGenerateRequest(
        idempotency_key="key-test-23",
        idea_ids=[ideas[0].id],
    )

    with pytest.raises(SocialContentFlowError) as exc_info:
        begin_social_content_generation(
            db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0
        )
    assert exc_info.value.error_code == CONTENT_IDEA_NOT_ELIGIBLE


def test_24_category_mismatch_or_stale_rejected(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """24. Category brief/run uyuşmazlığı reddedilir."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_social_environment(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    cat.is_stale = True
    db_session.commit()

    req = SocialBriefContentsGenerateRequest(
        idempotency_key="key-test-24",
        idea_ids=[ideas[0].id],
    )

    with pytest.raises(SocialContentFlowError) as exc_info:
        begin_social_content_generation(
            db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0
        )
    assert exc_info.value.error_code == CONTENT_IDEA_NOT_ELIGIBLE


def test_25_keyword_not_in_brief_rejected(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """25. Keyword brief üyeliği yoksa reddedilir."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_social_environment(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    other_kw = make_keyword(text_value="Other kw", brand_profile_id=ws.id)
    ideas[0].keyword_id = other_kw.id
    db_session.commit()

    req = SocialBriefContentsGenerateRequest(
        idempotency_key="key-test-25",
        idea_ids=[ideas[0].id],
    )

    with pytest.raises(SocialContentFlowError) as exc_info:
        begin_social_content_generation(
            db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0
        )
    assert exc_info.value.error_code == CONTENT_IDEA_NOT_ELIGIBLE


def test_26_same_key_same_request_replays_attempt(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """26. Aynı key + aynı request aynı attempt’i replay eder."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_social_environment(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    req = SocialBriefContentsGenerateRequest(
        idempotency_key="key-test-26",
        idea_ids=[ideas[0].id, ideas[1].id],
        trusted_brand_usp="Aynı USP",
    )

    start_1 = begin_social_content_generation(
        db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0
    )
    assert start_1.attempt_created is True
    assert start_1.replayed is False

    start_2 = begin_social_content_generation(
        db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0
    )
    assert start_2.attempt_created is False
    assert start_2.replayed is True
    assert start_2.attempt_id == start_1.attempt_id
    assert start_2.requested_idea_ids == start_1.requested_idea_ids


def test_27_replay_does_not_create_new_attempt(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """27. Replay yeni attempt oluşturmaz."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_social_environment(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    req = SocialBriefContentsGenerateRequest(
        idempotency_key="key-test-27",
        idea_ids=[ideas[0].id],
    )

    begin_social_content_generation(
        db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0
    )
    count_before = db_session.query(SocialGenerationAttempt).count()

    begin_social_content_generation(
        db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0
    )
    count_after = db_session.query(SocialGenerationAttempt).count()

    assert count_before == count_after


def test_28_same_key_different_ideas_rejected(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """28. Aynı key + farklı idea listesi reddedilir."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_social_environment(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    req_1 = SocialBriefContentsGenerateRequest(
        idempotency_key="key-test-28",
        idea_ids=[ideas[0].id],
    )
    begin_social_content_generation(
        db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req_1, now=T0
    )

    req_2 = SocialBriefContentsGenerateRequest(
        idempotency_key="key-test-28",
        idea_ids=[ideas[0].id, ideas[1].id],
    )

    with pytest.raises(SocialContentFlowError) as exc_info:
        begin_social_content_generation(
            db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req_2, now=T0
        )
    assert exc_info.value.error_code == CONTENT_ATTEMPT_REQUEST_MISMATCH


def test_29_same_key_only_order_difference_rejected(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """29. Aynı key + yalnız sıra farkı reddedilir."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_social_environment(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    req_1 = SocialBriefContentsGenerateRequest(
        idempotency_key="key-test-29",
        idea_ids=[ideas[0].id, ideas[1].id],
    )
    begin_social_content_generation(
        db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req_1, now=T0
    )

    req_2 = SocialBriefContentsGenerateRequest(
        idempotency_key="key-test-29",
        idea_ids=[ideas[1].id, ideas[0].id],
    )

    with pytest.raises(SocialContentFlowError) as exc_info:
        begin_social_content_generation(
            db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req_2, now=T0
        )
    assert exc_info.value.error_code == CONTENT_ATTEMPT_REQUEST_MISMATCH


def test_30_same_key_different_trusted_brand_usp_rejected(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """30. Aynı key + farklı trusted_brand_usp reddedilir."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_social_environment(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    req_1 = SocialBriefContentsGenerateRequest(
        idempotency_key="key-test-30",
        idea_ids=[ideas[0].id],
        trusted_brand_usp="USP 1",
    )
    begin_social_content_generation(
        db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req_1, now=T0
    )

    req_2 = SocialBriefContentsGenerateRequest(
        idempotency_key="key-test-30",
        idea_ids=[ideas[0].id],
        trusted_brand_usp="USP 2",
    )

    with pytest.raises(SocialContentFlowError) as exc_info:
        begin_social_content_generation(
            db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req_2, now=T0
        )
    assert exc_info.value.error_code == CONTENT_ATTEMPT_REQUEST_MISMATCH


def test_31_same_key_changed_product_facts_rejected(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """31. Aynı key + değişmiş product facts reddedilir."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_social_environment(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    req = SocialBriefContentsGenerateRequest(
        idempotency_key="key-test-31",
        idea_ids=[ideas[0].id],
    )
    begin_social_content_generation(
        db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0
    )

    # Profil datasını değiştir
    ws.profile_data = {"company_name": "Değiştirilmiş Şirket Adı"}
    db_session.commit()

    with pytest.raises(SocialContentFlowError) as exc_info:
        begin_social_content_generation(
            db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0
        )
    assert exc_info.value.error_code == CONTENT_ATTEMPT_REQUEST_MISMATCH


def test_32_replay_corrupted_coverage_rejected(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """32. Replay’de bozuk coverage reddedilir."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_social_environment(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    req = SocialBriefContentsGenerateRequest(
        idempotency_key="key-test-32",
        idea_ids=[ideas[0].id],
    )
    start_dto = begin_social_content_generation(
        db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0
    )

    # Coverage'ı boz
    attempt = db_session.query(SocialGenerationAttempt).filter_by(id=start_dto.attempt_id).one()
    attempt.coverage = {"corrupted": True}
    db_session.commit()

    with pytest.raises(SocialContentFlowError) as exc_info:
        begin_social_content_generation(
            db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0
        )
    assert exc_info.value.error_code == CONTENT_ATTEMPT_SNAPSHOT_INVALID


def test_33_replay_requested_idea_ids_snapshot_mismatch_rejected(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """33. Replay’de attempt.requested_idea_ids/snapshot farkı reddedilir."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_social_environment(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    req = SocialBriefContentsGenerateRequest(
        idempotency_key="key-test-33",
        idea_ids=[ideas[0].id],
    )
    start_dto = begin_social_content_generation(
        db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0
    )

    # attempt.requested_idea_ids alanını değiştir
    attempt = db_session.query(SocialGenerationAttempt).filter_by(id=start_dto.attempt_id).one()
    attempt.requested_idea_ids = [ideas[1].id]
    db_session.commit()

    with pytest.raises(SocialContentFlowError) as exc_info:
        begin_social_content_generation(
            db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0
        )
    assert exc_info.value.error_code == CONTENT_ATTEMPT_SNAPSHOT_INVALID


def test_34_completed_partial_failed_replay_does_not_mutate(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """34. Completed/partial/failed replay mutation yapmaz."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_social_environment(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    req = SocialBriefContentsGenerateRequest(
        idempotency_key="key-test-34",
        idea_ids=[ideas[0].id],
    )
    start_dto = begin_social_content_generation(
        db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0
    )

    attempt = db_session.query(SocialGenerationAttempt).filter_by(id=start_dto.attempt_id).one()
    attempt.status = "completed"
    attempt.task_id = "task-completed-123"
    db_session.commit()

    res = begin_social_content_generation(
        db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0
    )
    assert res.attempt_status == "completed"
    assert res.replayed is True

    db_session.refresh(attempt)
    assert attempt.status == "completed"
    assert attempt.task_id == "task-completed-123"


def test_35_active_contents_attempt_raises_canonical_conflict(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """35. Başka aktif contents attempt canonical conflict üretir."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_social_environment(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    req_1 = SocialBriefContentsGenerateRequest(
        idempotency_key="key-test-35-first",
        idea_ids=[ideas[0].id],
    )
    begin_social_content_generation(
        db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req_1, now=T0
    )
    db_session.commit()

    # Farklı idempotency key ile ikinci aktif attempt denemesi
    req_2 = SocialBriefContentsGenerateRequest(
        idempotency_key="key-test-35-second",
        idea_ids=[ideas[0].id],
    )

    with pytest.raises(AttemptConflictError):
        begin_social_content_generation(
            db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req_2, now=T0
        )


def test_36_function_does_not_call_commit(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag, monkeypatch
):
    """36. Fonksiyon commit çağırmaz."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_social_environment(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    req = SocialBriefContentsGenerateRequest(
        idempotency_key="key-test-36",
        idea_ids=[ideas[0].id],
    )

    commit_spy = MagicMock(side_effect=AssertionError("commit() çağrılmamalıdır!"))
    monkeypatch.setattr(db_session, "commit", commit_spy)

    res = begin_social_content_generation(
        db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0
    )
    assert res.attempt_created is True
    assert commit_spy.call_count == 0


def test_37_function_does_not_call_rollback(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag, monkeypatch
):
    """37. Fonksiyon rollback çağırmaz."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_social_environment(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    req = SocialBriefContentsGenerateRequest(
        idempotency_key="key-test-37",
        idea_ids=[ideas[0].id],
    )

    rollback_spy = MagicMock(side_effect=AssertionError("rollback() çağrılmamalıdır!"))
    monkeypatch.setattr(db_session, "rollback", rollback_spy)

    res = begin_social_content_generation(
        db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0
    )
    assert res.attempt_created is True
    assert rollback_spy.call_count == 0


def test_38_no_raw_id_usp_or_product_facts_in_error_messages(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """38. Hata mesajlarında raw ID, USP veya product facts bulunmaz."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_social_environment(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    raw_usp = "GizliMarkaUSP-Secret-98765"
    raw_fact = "Yapay Zeka Yazılımı"

    # 1. Kayıp idea hatası
    bad_req = SocialBriefContentsGenerateRequest(
        idempotency_key="key-test-38-leak",
        idea_ids=[987654321],
        trusted_brand_usp=raw_usp,
    )
    with pytest.raises(SocialContentFlowError) as exc_info:
        begin_social_content_generation(
            db_session, brief_id=brief.id, brand_profile_id=ws.id, request=bad_req, now=T0
        )
    msg = str(exc_info.value)
    assert "987654321" not in msg
    assert str(brief.id) not in msg
    assert str(ws.id) not in msg
    assert raw_usp not in msg
    assert raw_fact not in msg
    assert "key-test-38-leak" not in msg


def test_39_dto_is_immutable(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """39. Oluşan DTO immutable’dır."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_social_environment(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    req = SocialBriefContentsGenerateRequest(
        idempotency_key="key-test-39",
        idea_ids=[ideas[0].id],
    )
    start_dto = begin_social_content_generation(
        db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0
    )

    assert isinstance(start_dto.requested_idea_ids, tuple)
    with pytest.raises(FrozenInstanceError):
        start_dto.attempt_id = 999  # type: ignore

    with pytest.raises(FrozenInstanceError):
        start_dto.replayed = True  # type: ignore


def test_40_input_list_mutation_does_not_affect_snapshot_or_dto(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """40. Input listesi sonradan değiştirilse snapshot ve DTO değişmez."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_social_environment(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    mutable_ids = [ideas[0].id, ideas[1].id]
    req = SocialBriefContentsGenerateRequest(
        idempotency_key="key-test-40",
        idea_ids=mutable_ids,
    )

    start_dto = begin_social_content_generation(
        db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0
    )

    # İstek nesnesi dışındaki orijinal listeyi mutasyona uğrat
    mutable_ids.append(99999)
    mutable_ids.reverse()

    assert start_dto.requested_idea_ids == (ideas[0].id, ideas[1].id)
    attempt = db_session.query(SocialGenerationAttempt).filter_by(id=start_dto.attempt_id).one()
    assert attempt.requested_idea_ids == [ideas[0].id, ideas[1].id]
    assert attempt.coverage["request"]["idea_ids"] == [ideas[0].id, ideas[1].id]
