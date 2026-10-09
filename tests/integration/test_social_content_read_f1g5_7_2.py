# -*- coding: utf-8 -*-
"""Entegrasyon Testleri: Sosyal İçerik Attempt Salt-Okunur Read / Polling Servisi (F1-G.5.7.2).

Tüm testler gerçek PostgreSQL test veritabanını ve izole oturumu kullanır.
Test edilen 38 senaryo:
1. pending sonucu boş partition/content döndürür.
2. running sonucu boş partition/content döndürür.
3. completed bütün requested içerikleri snapshot sırasında döndürür.
4. partial warning partition’ını doğru kurar.
5. failed + warnings historical partition’ı doğru kurar.
6. failed + boş warnings tüm requested fikirleri unresolved yapar.
7. Partial attempt sonraki içerik üretiminden etkilenmez (tarihsel izolasyon).
8. Failed attempt sonraki içerik üretiminden etkilenmez (tarihsel izolasyon).
9. Cross-workspace erişim 404-benzeri not-found domain hatasıdır.
10. Başka brief attempt’i not-found üretir.
11. Yanlış stage not-found üretir.
12. Bozuk snapshot inconsistent üretir.
13. requested/snapshot sıra farkı reddedilir.
14. Warning dict extra/missing key reddedilir.
15. Duplicate warning idea ID reddedilir.
16. Request dışı warning idea ID reddedilir.
17. Warning sırası DTO'da kanonik biçimde requested_idea_ids sırasıyla döner.
18. Geçersiz reason_code reddedilir.
19. Geçersiz claims reddedilir.
20. Geçersiz ai_calls_used reddedilir.
21. completed + warnings reddedilir.
22. completed + reason_code/error_message reddedilir.
23. partial + boş warning reddedilir.
24. partial + yanlış reason_code reddedilir.
25. pending/running + warning reddedilir.
26. pending/running + reason/error reddedilir.
27. Eksik başarılı content reddedilir.
28. Stale content reddedilir.
29. Cross-brief content reddedilir.
30. Content contract bozulmuşsa reddedilir.
31. Platform/format/target/category/keyword bozuklukları reddedilir.
32. Fonksiyon commit/rollback/flush çağırmaz.
33. with_for_update kullanmaz.
34. DTO ve nested koleksiyonlar immutable’dır.
35. product_facts/trusted_brand_usp DTO’da bulunmaz.
36. Raw içerik ve ID değerleri hata mesajlarına sızmaz.
37. replayed exact bool doğrulanır.
38. replayed değeri DTO’ya exact aktarılır.
"""
from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from unittest.mock import MagicMock
import uuid

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.config import settings
from app.core.social.content_read import (
    CONTENT_ATTEMPT_NOT_FOUND,
    CONTENT_READ_INCONSISTENT,
    SocialContentAttemptReadResult,
    SocialContentReadError,
    SocialContentReadNotFoundError,
    SocialContentReadWarning,
    load_social_content_result,
)
from app.generators.social.attempt_state import ALLOWED_CONTENTS_FAILURE_REASONS
from app.database.models import (
    BrandProfile,
    ChannelPool,
    ScoringRun,
    SocialBrief,
    SocialBriefKeyword,
    SocialBriefTarget,
    SocialCategory,
    SocialContent,
    SocialGenerationAttempt,
    SocialIdea,
)

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
    brand_name: str = "Test Brand Read",
):
    """Workspace, scoring run ve keyword oluşturur."""
    ws = make_workspace(name=brand_name, status="confirmed")
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


def _setup_full_env(
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
    *,
    num_ideas: int = 3,
    brand_name: str = "Test Brand Read",
):
    """Eksiksiz otoriter DB zinciri (workspace, brief, target, category, ideas) kurar."""
    ws, run, kw = _setup_base_env(
        db_session, make_workspace, make_scoring_run, make_keyword, brand_name=brand_name
    )

    brief = SocialBrief(
        scoring_run_id=run.id,
        brand_name_snapshot=ws.name,
        brand_context_snapshot="Marka bağlamı özeti",
        channel_assignment_version=1,
        format_matrix_version="v1",
        is_stale=False,
        locked_at=T0,
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


def _create_valid_content(
    db_session: Session,
    *,
    brief: SocialBrief,
    target: SocialBriefTarget,
    idea: SocialIdea,
    caption: str = "Bu harika bir Instagram post içeriğidir.",
    is_stale: bool = False,
) -> SocialContent:
    """Doğrulanabilir geçerli bir SocialContent satırı oluşturur."""
    content = SocialContent(
        idea_id=idea.id,
        brief_id=brief.id,
        hooks=[
            {"text": "Dikkat çeken birinci kanca cümlesi?", "style": "question"},
            {"text": "Şaşırtıcı bir sektör gerçeği ortaya çıktı!", "style": "shocking"},
            {"text": "Hepimizin her gün yaşadığı o an...", "style": "relatable"},
        ],
        caption=caption,
        cta_text="Daha fazlası için profildeki linke tıklayın.",
        hashtags=["dijital", "pazarlama", "strateji", "icerik", "sosyalmedya"],
        format_payload=None,
        duration_status="not_applicable",
        actual_duration_sec=None,
        validation_warnings=[],
        is_stale=is_stale,
    )
    db_session.add(content)
    db_session.flush()
    return content


def _create_attempt(
    db_session: Session,
    *,
    brief: SocialBrief,
    ideas: list[SocialIdea],
    status: str = "pending",
    stage: str = "contents",
    reason_code: str | None = None,
    error_message: str | None = None,
    warnings: list[dict] | None = None,
    idempotency_key: str | None = None,
) -> SocialGenerationAttempt:
    """Test için SocialGenerationAttempt kaydı oluşturur."""
    idea_ids = [i.id for i in ideas]
    key = idempotency_key or f"attempt-{uuid.uuid4().hex[:8]}"
    coverage = {
        "schema_version": "contents_request_v1",
        "request": {
            "idea_ids": idea_ids,
            "product_facts": "Örnek ürün tanımı",
            "trusted_brand_usp": "Örnek USP",
        },
    }
    att = SocialGenerationAttempt(
        brief_id=brief.id,
        stage=stage,
        idempotency_key=key,
        status=status,
        requested_idea_ids=idea_ids,
        coverage=coverage,
        reason_code=reason_code,
        error_message=error_message,
        warnings=warnings,
    )
    db_session.add(att)
    db_session.commit()
    db_session.refresh(att)
    return att


# ==================== TESTLER (38 SENARYO) ====================


def test_01_pending_returns_empty_partitions_and_contents(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """1. pending sonucu boş partition/content döndürür."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    att = _create_attempt(db_session, brief=brief, ideas=ideas, status="pending")

    result = load_social_content_result(
        db_session,
        brief_id=brief.id,
        attempt_id=att.id,
        brand_profile_id=ws.id,
    )

    assert result.attempt_status == "pending"
    assert result.requested_idea_ids == tuple(i.id for i in ideas)
    assert result.successful_idea_ids == ()
    assert result.unresolved_idea_ids == ()
    assert result.contents == ()
    assert result.warnings == ()
    assert result.reason_code is None
    assert result.replayed is False


def test_02_running_returns_empty_partitions_and_contents(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """2. running sonucu boş partition/content döndürür."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    att = _create_attempt(db_session, brief=brief, ideas=ideas, status="running")

    result = load_social_content_result(
        db_session,
        brief_id=brief.id,
        attempt_id=att.id,
        brand_profile_id=ws.id,
    )

    assert result.attempt_status == "running"
    assert result.successful_idea_ids == ()
    assert result.unresolved_idea_ids == ()
    assert result.contents == ()
    assert result.warnings == ()


def test_03_completed_returns_all_requested_contents_in_snapshot_order(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """3. completed bütün requested içerikleri snapshot sırasında döndürür."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=3
    )
    # İçerikleri oluştur
    for idea in ideas:
        _create_valid_content(db_session, brief=brief, target=target, idea=idea)
    db_session.commit()

    att = _create_attempt(db_session, brief=brief, ideas=ideas, status="completed")

    result = load_social_content_result(
        db_session,
        brief_id=brief.id,
        attempt_id=att.id,
        brand_profile_id=ws.id,
    )

    assert result.attempt_status == "completed"
    assert result.successful_idea_ids == tuple(i.id for i in ideas)
    assert result.unresolved_idea_ids == ()
    assert len(result.contents) == 3
    assert tuple(c.idea_id for c in result.contents) == tuple(i.id for i in ideas)
    assert result.warnings == ()
    assert result.reason_code is None


def test_04_partial_builds_warning_partition_correctly(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """4. partial warning partition’ını doğru kurar."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=3
    )
    # Sadece idea 0 ve idea 2 için içerik üretildi, idea 1 başarısız oldu
    _create_valid_content(db_session, brief=brief, target=target, idea=ideas[0])
    _create_valid_content(db_session, brief=brief, target=target, idea=ideas[2])
    db_session.commit()

    warnings_data = [
        {
            "idea_id": ideas[1].id,
            "reason_code": "content_rejected",
            "claims": ["Yüzde 99 garanti iddiası"],
            "ai_calls_used": 2,
        }
    ]
    att = _create_attempt(
        db_session,
        brief=brief,
        ideas=ideas,
        status="partial",
        reason_code="content_partial",
        error_message="Bazı sosyal içerikler üretilemedi.",
        warnings=warnings_data,
    )

    result = load_social_content_result(
        db_session,
        brief_id=brief.id,
        attempt_id=att.id,
        brand_profile_id=ws.id,
    )

    assert result.attempt_status == "partial"
    assert result.successful_idea_ids == (ideas[0].id, ideas[2].id)
    assert result.unresolved_idea_ids == (ideas[1].id,)
    assert len(result.contents) == 2
    assert tuple(c.idea_id for c in result.contents) == (ideas[0].id, ideas[2].id)
    assert len(result.warnings) == 1
    assert result.warnings[0].idea_id == ideas[1].id
    assert result.warnings[0].claims == ("Yüzde 99 garanti iddiası",)
    assert result.reason_code == "content_partial"


def test_05_failed_with_warnings_historical_partition_correctly(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """5. failed + warnings historical partition’ı doğru kurar."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=3
    )
    # idea 0 persist edilmiş olsun ama attempt failed bitmiş
    _create_valid_content(db_session, brief=brief, target=target, idea=ideas[0])
    db_session.commit()

    warnings_data = [
        {
            "idea_id": ideas[1].id,
            "reason_code": "content_provider_error",
            "claims": [],
            "ai_calls_used": 1,
        },
        {
            "idea_id": ideas[2].id,
            "reason_code": "content_output_invalid",
            "claims": [],
            "ai_calls_used": 2,
        },
    ]
    att = _create_attempt(
        db_session,
        brief=brief,
        ideas=ideas,
        status="failed",
        reason_code="content_generation_failed",
        error_message="İçerik üretimi başarısız.",
        warnings=warnings_data,
    )

    result = load_social_content_result(
        db_session,
        brief_id=brief.id,
        attempt_id=att.id,
        brand_profile_id=ws.id,
    )

    assert result.attempt_status == "failed"
    assert result.successful_idea_ids == (ideas[0].id,)
    assert result.unresolved_idea_ids == (ideas[1].id, ideas[2].id)
    assert len(result.contents) == 1
    assert result.contents[0].idea_id == ideas[0].id
    assert len(result.warnings) == 2
    assert result.reason_code == "content_generation_failed"


def test_06_failed_empty_warnings_all_requested_unresolved(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """6. failed + boş warnings tüm requested fikirleri unresolved yapar."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=2
    )
    att = _create_attempt(
        db_session,
        brief=brief,
        ideas=ideas,
        status="failed",
        reason_code="worker_bootstrap_failed",
        error_message="Worker hazırlığı başarısız.",
        warnings=None,
    )

    result = load_social_content_result(
        db_session,
        brief_id=brief.id,
        attempt_id=att.id,
        brand_profile_id=ws.id,
    )

    assert result.attempt_status == "failed"
    assert result.successful_idea_ids == ()
    assert result.unresolved_idea_ids == tuple(i.id for i in ideas)
    assert result.contents == ()
    assert result.warnings == ()
    assert result.reason_code == "worker_bootstrap_failed"


def test_07_partial_attempt_not_affected_by_subsequent_generation(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """7. Partial attempt sonraki içerik üretiminden etkilenmez (tarihsel izolasyon)."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=2
    )
    # İlk denemede sadece idea 0 persist edildi
    _create_valid_content(db_session, brief=brief, target=target, idea=ideas[0])
    db_session.commit()

    att_1 = _create_attempt(
        db_session,
        brief=brief,
        ideas=ideas,
        status="partial",
        reason_code="content_partial",
        error_message="Kısmi içerik üretildi.",
        warnings=[
            {
                "idea_id": ideas[1].id,
                "reason_code": "content_rejected",
                "claims": [],
                "ai_calls_used": 2,
            }
        ],
        idempotency_key="attempt-first",
    )

    # Daha sonra başka bir süreç idea 1 için geçerli içerik yazdı
    _create_valid_content(db_session, brief=brief, target=target, idea=ideas[1])
    db_session.commit()

    # att_1 sorgulandığında hala idea 1'i unresolved görmeli ve contents'e eklememeli!
    res_1 = load_social_content_result(
        db_session,
        brief_id=brief.id,
        attempt_id=att_1.id,
        brand_profile_id=ws.id,
    )
    assert res_1.successful_idea_ids == (ideas[0].id,)
    assert res_1.unresolved_idea_ids == (ideas[1].id,)
    assert len(res_1.contents) == 1
    assert res_1.contents[0].idea_id == ideas[0].id


def test_08_failed_attempt_not_affected_by_subsequent_generation(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """8. Failed attempt sonraki içerik üretiminden etkilenmez (tarihsel izolasyon)."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=2
    )
    att_failed = _create_attempt(
        db_session,
        brief=brief,
        ideas=ideas,
        status="failed",
        reason_code="worker_bootstrap_failed",
        error_message="Worker başlatılamadı.",
        warnings=None,
    )

    # Daha sonra içerikler yazıldı
    _create_valid_content(db_session, brief=brief, target=target, idea=ideas[0])
    _create_valid_content(db_session, brief=brief, target=target, idea=ideas[1])
    db_session.commit()

    # Eski failed attempt hala boş contents ve tüm fikirleri unresolved döndürmelidir!
    res_failed = load_social_content_result(
        db_session,
        brief_id=brief.id,
        attempt_id=att_failed.id,
        brand_profile_id=ws.id,
    )
    assert res_failed.successful_idea_ids == ()
    assert res_failed.unresolved_idea_ids == tuple(i.id for i in ideas)
    assert res_failed.contents == ()


def test_09_cross_workspace_access_produces_not_found(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """9. Cross-workspace erişim 404-benzeri not-found domain hatasıdır."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    att = _create_attempt(db_session, brief=brief, ideas=ideas, status="pending")

    other_ws = make_workspace(name="Other WS", status="confirmed")
    db_session.commit()

    with pytest.raises(SocialContentReadNotFoundError) as exc_info:
        load_social_content_result(
            db_session,
            brief_id=brief.id,
            attempt_id=att.id,
            brand_profile_id=other_ws.id,
        )
    assert exc_info.value.error_code == CONTENT_ATTEMPT_NOT_FOUND


def test_09b_archived_workspace_produces_not_found(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """9b. Arşivlenmiş (deleted_at IS NOT NULL) workspace okuma tarafında da
    diğer salt-okunur akışlarla (idea_read, idea_retry_read) aynı şekilde
    not-found domain hatası üretir (defect fix)."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    att = _create_attempt(db_session, brief=brief, ideas=ideas, status="pending")

    ws.deleted_at = datetime.now(timezone.utc)
    db_session.commit()

    with pytest.raises(SocialContentReadNotFoundError) as exc_info:
        load_social_content_result(
            db_session,
            brief_id=brief.id,
            attempt_id=att.id,
            brand_profile_id=ws.id,
        )
    assert exc_info.value.error_code == CONTENT_ATTEMPT_NOT_FOUND


def test_10_another_brief_attempt_produces_not_found(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """10. Başka brief attempt’i not-found üretir."""
    ws, run, brief1, target1, cat1, kw1, ideas1 = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword, brand_name="Brand A"
    )
    _, _, brief2, target2, cat2, kw2, ideas2 = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword, brand_name="Brand B"
    )
    att2 = _create_attempt(db_session, brief=brief2, ideas=ideas2, status="pending")

    with pytest.raises(SocialContentReadNotFoundError) as exc_info:
        load_social_content_result(
            db_session,
            brief_id=brief1.id,
            attempt_id=att2.id,
            brand_profile_id=ws.id,
        )
    assert exc_info.value.error_code == CONTENT_ATTEMPT_NOT_FOUND


def test_11_wrong_stage_produces_not_found(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """11. Yanlış stage not-found üretir."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    att_ideas = _create_attempt(
        db_session, brief=brief, ideas=ideas, status="completed", stage="ideas"
    )

    with pytest.raises(SocialContentReadNotFoundError) as exc_info:
        load_social_content_result(
            db_session,
            brief_id=brief.id,
            attempt_id=att_ideas.id,
            brand_profile_id=ws.id,
        )
    assert exc_info.value.error_code == CONTENT_ATTEMPT_NOT_FOUND


def test_12_corrupted_snapshot_produces_inconsistent(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """12. Bozuk snapshot inconsistent üretir."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    att = _create_attempt(db_session, brief=brief, ideas=ideas, status="pending")
    att.coverage = {"corrupted": True}
    db_session.commit()

    with pytest.raises(SocialContentReadError) as exc_info:
        load_social_content_result(
            db_session,
            brief_id=brief.id,
            attempt_id=att.id,
            brand_profile_id=ws.id,
        )
    assert exc_info.value.error_code == CONTENT_READ_INCONSISTENT


def test_13_requested_snapshot_order_difference_rejected(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """13. requested/snapshot sıra farkı reddedilir."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=2
    )
    att = _create_attempt(db_session, brief=brief, ideas=ideas, status="pending")
    # requested_idea_ids sırasını ters çevir
    att.requested_idea_ids = [ideas[1].id, ideas[0].id]
    db_session.commit()

    with pytest.raises(SocialContentReadError) as exc_info:
        load_social_content_result(
            db_session,
            brief_id=brief.id,
            attempt_id=att.id,
            brand_profile_id=ws.id,
        )
    assert exc_info.value.error_code == CONTENT_READ_INCONSISTENT


def test_14_warning_dict_extra_or_missing_key_rejected(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """14. Warning dict extra/missing key reddedilir."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=2
    )
    _create_valid_content(db_session, brief=brief, target=target, idea=ideas[0])
    db_session.commit()

    # Ekstra alan
    att = _create_attempt(
        db_session,
        brief=brief,
        ideas=ideas,
        status="partial",
        reason_code="content_partial",
        error_message="Hata",
        warnings=[
            {
                "idea_id": ideas[1].id,
                "reason_code": "content_rejected",
                "claims": [],
                "ai_calls_used": 1,
                "extra_key": "bad",
            }
        ],
    )

    with pytest.raises(SocialContentReadError) as exc_info:
        load_social_content_result(
            db_session, brief_id=brief.id, attempt_id=att.id, brand_profile_id=ws.id
        )
    assert exc_info.value.error_code == CONTENT_READ_INCONSISTENT


def test_15_duplicate_warning_idea_id_rejected(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """15. Duplicate warning idea ID reddedilir."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=2
    )
    _create_valid_content(db_session, brief=brief, target=target, idea=ideas[0])
    db_session.commit()

    att = _create_attempt(
        db_session,
        brief=brief,
        ideas=ideas,
        status="partial",
        reason_code="content_partial",
        error_message="Hata",
        warnings=[
            {
                "idea_id": ideas[1].id,
                "reason_code": "content_rejected",
                "claims": [],
                "ai_calls_used": 1,
            },
            {
                "idea_id": ideas[1].id,
                "reason_code": "content_rejected",
                "claims": [],
                "ai_calls_used": 2,
            },
        ],
    )

    with pytest.raises(SocialContentReadError) as exc_info:
        load_social_content_result(
            db_session, brief_id=brief.id, attempt_id=att.id, brand_profile_id=ws.id
        )
    assert exc_info.value.error_code == CONTENT_READ_INCONSISTENT


def test_16_non_requested_warning_idea_id_rejected(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """16. Request dışı warning idea ID reddedilir."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=2
    )
    _create_valid_content(db_session, brief=brief, target=target, idea=ideas[0])
    db_session.commit()

    att = _create_attempt(
        db_session,
        brief=brief,
        ideas=ideas,
        status="partial",
        reason_code="content_partial",
        error_message="Hata",
        warnings=[
            {
                "idea_id": 999999,
                "reason_code": "content_rejected",
                "claims": [],
                "ai_calls_used": 1,
            }
        ],
    )

    with pytest.raises(SocialContentReadError) as exc_info:
        load_social_content_result(
            db_session, brief_id=brief.id, attempt_id=att.id, brand_profile_id=ws.id
        )
    assert exc_info.value.error_code == CONTENT_READ_INCONSISTENT


def test_17_warning_order_canonically_sorted_in_dto(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """17. Warning sırası DTO'da kanonik biçimde requested_idea_ids sırasıyla döner."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=3
    )
    _create_valid_content(db_session, brief=brief, target=target, idea=ideas[0])
    db_session.commit()

    # DB'ye kasıtlı olarak ters sırada warning koy (ideas[2] sonra ideas[1])
    raw_warnings = [
        {
            "idea_id": ideas[2].id,
            "reason_code": "content_rejected",
            "claims": [],
            "ai_calls_used": 1,
        },
        {
            "idea_id": ideas[1].id,
            "reason_code": "content_rejected",
            "claims": [],
            "ai_calls_used": 2,
        },
    ]
    att = _create_attempt(
        db_session,
        brief=brief,
        ideas=ideas,
        status="partial",
        reason_code="content_partial",
        error_message="Hata",
        warnings=raw_warnings,
    )

    result = load_social_content_result(
        db_session, brief_id=brief.id, attempt_id=att.id, brand_profile_id=ws.id
    )

    # DTO çıktısı requested_idea_ids sırasına (ideas[1] sonra ideas[2]) göre kanonik sıralanmış olmalıdır
    assert tuple(w.idea_id for w in result.warnings) == (ideas[1].id, ideas[2].id)
    assert result.unresolved_idea_ids == (ideas[1].id, ideas[2].id)


def test_18_invalid_warning_reason_code_rejected(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """18. Geçersiz reason_code reddedilir."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=2
    )
    _create_valid_content(db_session, brief=brief, target=target, idea=ideas[0])
    db_session.commit()

    att = _create_attempt(
        db_session,
        brief=brief,
        ideas=ideas,
        status="partial",
        reason_code="content_partial",
        error_message="Hata",
        warnings=[
            {
                "idea_id": ideas[1].id,
                "reason_code": "unknown_reason_code",
                "claims": [],
                "ai_calls_used": 1,
            }
        ],
    )

    with pytest.raises(SocialContentReadError) as exc_info:
        load_social_content_result(
            db_session, brief_id=brief.id, attempt_id=att.id, brand_profile_id=ws.id
        )
    assert exc_info.value.error_code == CONTENT_READ_INCONSISTENT


def test_19_invalid_warning_claims_rejected(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """19. Geçersiz claims reddedilir."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=2
    )
    _create_valid_content(db_session, brief=brief, target=target, idea=ideas[0])
    db_session.commit()

    # content_provider_error durumunda claims boş olmalıdır
    att = _create_attempt(
        db_session,
        brief=brief,
        ideas=ideas,
        status="partial",
        reason_code="content_partial",
        error_message="Hata",
        warnings=[
            {
                "idea_id": ideas[1].id,
                "reason_code": "content_provider_error",
                "claims": ["Geçersiz iddia"],
                "ai_calls_used": 1,
            }
        ],
    )

    with pytest.raises(SocialContentReadError) as exc_info:
        load_social_content_result(
            db_session, brief_id=brief.id, attempt_id=att.id, brand_profile_id=ws.id
        )
    assert exc_info.value.error_code == CONTENT_READ_INCONSISTENT


def test_20_invalid_warning_ai_calls_used_rejected(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """20. Geçersiz ai_calls_used reddedilir."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=2
    )
    _create_valid_content(db_session, brief=brief, target=target, idea=ideas[0])
    db_session.commit()

    att = _create_attempt(
        db_session,
        brief=brief,
        ideas=ideas,
        status="partial",
        reason_code="content_partial",
        error_message="Hata",
        warnings=[
            {
                "idea_id": ideas[1].id,
                "reason_code": "content_rejected",
                "claims": [],
                "ai_calls_used": 3,
            }
        ],
    )

    with pytest.raises(SocialContentReadError) as exc_info:
        load_social_content_result(
            db_session, brief_id=brief.id, attempt_id=att.id, brand_profile_id=ws.id
        )
    assert exc_info.value.error_code == CONTENT_READ_INCONSISTENT


def test_21_completed_with_warnings_rejected(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """21. completed + warnings reddedilir."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    for idea in ideas:
        _create_valid_content(db_session, brief=brief, target=target, idea=idea)
    db_session.commit()

    att = _create_attempt(
        db_session,
        brief=brief,
        ideas=ideas,
        status="completed",
        warnings=[
            {
                "idea_id": ideas[0].id,
                "reason_code": "content_rejected",
                "claims": [],
                "ai_calls_used": 1,
            }
        ],
    )

    with pytest.raises(SocialContentReadError) as exc_info:
        load_social_content_result(
            db_session, brief_id=brief.id, attempt_id=att.id, brand_profile_id=ws.id
        )
    assert exc_info.value.error_code == CONTENT_READ_INCONSISTENT


def test_22_completed_with_reason_or_error_rejected(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """22. completed + reason_code/error_message reddedilir."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    for idea in ideas:
        _create_valid_content(db_session, brief=brief, target=target, idea=idea)
    db_session.commit()

    att = _create_attempt(
        db_session,
        brief=brief,
        ideas=ideas,
        status="completed",
        reason_code="some_code",
    )

    with pytest.raises(SocialContentReadError) as exc_info:
        load_social_content_result(
            db_session, brief_id=brief.id, attempt_id=att.id, brand_profile_id=ws.id
        )
    assert exc_info.value.error_code == CONTENT_READ_INCONSISTENT


def test_23_partial_with_empty_warnings_rejected(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """23. partial + boş warning reddedilir."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    att = _create_attempt(
        db_session,
        brief=brief,
        ideas=ideas,
        status="partial",
        reason_code="content_partial",
        error_message="Hata",
        warnings=[],
    )

    with pytest.raises(SocialContentReadError) as exc_info:
        load_social_content_result(
            db_session, brief_id=brief.id, attempt_id=att.id, brand_profile_id=ws.id
        )
    assert exc_info.value.error_code == CONTENT_READ_INCONSISTENT


def test_24_partial_with_wrong_reason_code_rejected(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """24. partial + yanlış reason_code reddedilir."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=2
    )
    att = _create_attempt(
        db_session,
        brief=brief,
        ideas=ideas,
        status="partial",
        reason_code="wrong_reason_code",
        error_message="Hata",
        warnings=[
            {
                "idea_id": ideas[1].id,
                "reason_code": "content_rejected",
                "claims": [],
                "ai_calls_used": 1,
            }
        ],
    )

    with pytest.raises(SocialContentReadError) as exc_info:
        load_social_content_result(
            db_session, brief_id=brief.id, attempt_id=att.id, brand_profile_id=ws.id
        )
    assert exc_info.value.error_code == CONTENT_READ_INCONSISTENT


def test_25_pending_running_with_warning_rejected(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """25. pending/running + warning reddedilir."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    att = _create_attempt(
        db_session,
        brief=brief,
        ideas=ideas,
        status="pending",
        warnings=[
            {
                "idea_id": ideas[0].id,
                "reason_code": "content_rejected",
                "claims": [],
                "ai_calls_used": 1,
            }
        ],
    )

    with pytest.raises(SocialContentReadError) as exc_info:
        load_social_content_result(
            db_session, brief_id=brief.id, attempt_id=att.id, brand_profile_id=ws.id
        )
    assert exc_info.value.error_code == CONTENT_READ_INCONSISTENT


def test_26_pending_running_with_reason_or_error_rejected(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """26. pending/running + reason/error reddedilir."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    att = _create_attempt(
        db_session,
        brief=brief,
        ideas=ideas,
        status="running",
        reason_code="unexpected",
    )

    with pytest.raises(SocialContentReadError) as exc_info:
        load_social_content_result(
            db_session, brief_id=brief.id, attempt_id=att.id, brand_profile_id=ws.id
        )
    assert exc_info.value.error_code == CONTENT_READ_INCONSISTENT


def test_27_missing_successful_content_rejected(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """27. Eksik başarılı content reddedilir."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    # completed ama içerik satırı hiç oluşturulmamış
    att = _create_attempt(db_session, brief=brief, ideas=ideas, status="completed")

    with pytest.raises(SocialContentReadError) as exc_info:
        load_social_content_result(
            db_session, brief_id=brief.id, attempt_id=att.id, brand_profile_id=ws.id
        )
    assert exc_info.value.error_code == CONTENT_READ_INCONSISTENT


def test_28_stale_content_rejected(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """28. Stale content reddedilir."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=1
    )
    _create_valid_content(db_session, brief=brief, target=target, idea=ideas[0], is_stale=True)
    db_session.commit()

    att = _create_attempt(db_session, brief=brief, ideas=ideas, status="completed")

    with pytest.raises(SocialContentReadError) as exc_info:
        load_social_content_result(
            db_session, brief_id=brief.id, attempt_id=att.id, brand_profile_id=ws.id
        )
    assert exc_info.value.error_code == CONTENT_READ_INCONSISTENT


def test_29_cross_brief_content_rejected(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """29. Cross-brief content reddedilir."""
    ws, run, brief1, target1, cat1, kw1, ideas1 = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword, brand_name="Brand 1"
    )
    _, _, brief2, target2, cat2, kw2, ideas2 = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword, brand_name="Brand 2"
    )

    # brief1'deki idea1 için brief2 adına içerik satırı ekle
    content = _create_valid_content(db_session, brief=brief2, target=target2, idea=ideas1[0])
    db_session.commit()

    att = _create_attempt(db_session, brief=brief1, ideas=[ideas1[0]], status="completed")

    with pytest.raises(SocialContentReadError) as exc_info:
        load_social_content_result(
            db_session, brief_id=brief1.id, attempt_id=att.id, brand_profile_id=ws.id
        )
    assert exc_info.value.error_code == CONTENT_READ_INCONSISTENT


def test_30_contract_violating_content_rejected(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """30. Content contract bozulmuşsa reddedilir."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=1
    )
    content = _create_valid_content(db_session, brief=brief, target=target, idea=ideas[0])
    # Contract ihlali: boş hooks (en az 1 gereklidir)
    content.hooks = []
    db_session.commit()

    att = _create_attempt(db_session, brief=brief, ideas=ideas, status="completed")

    with pytest.raises(SocialContentReadError) as exc_info:
        load_social_content_result(
            db_session, brief_id=brief.id, attempt_id=att.id, brand_profile_id=ws.id
        )
    assert exc_info.value.error_code == CONTENT_READ_INCONSISTENT


def test_31_corrupted_target_category_keyword_rejected(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """31. Platform/format/target/category/keyword bozuklukları reddedilir."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=1
    )
    _create_valid_content(db_session, brief=brief, target=target, idea=ideas[0])
    # idea.target_platform'u boz
    ideas[0].target_platform = "tiktok"
    db_session.commit()

    att = _create_attempt(db_session, brief=brief, ideas=ideas, status="completed")

    with pytest.raises(SocialContentReadError) as exc_info:
        load_social_content_result(
            db_session, brief_id=brief.id, attempt_id=att.id, brand_profile_id=ws.id
        )
    assert exc_info.value.error_code == CONTENT_READ_INCONSISTENT


def test_32_function_does_not_call_commit_rollback_flush(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag, monkeypatch
):
    """32. Fonksiyon commit/rollback/flush çağırmaz."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    att = _create_attempt(db_session, brief=brief, ideas=ideas, status="pending")

    commit_spy = MagicMock(side_effect=AssertionError("commit çağrıldı"))
    rollback_spy = MagicMock(side_effect=AssertionError("rollback çağrıldı"))
    flush_spy = MagicMock(side_effect=AssertionError("flush çağrıldı"))

    monkeypatch.setattr(db_session, "commit", commit_spy)
    monkeypatch.setattr(db_session, "rollback", rollback_spy)
    monkeypatch.setattr(db_session, "flush", flush_spy)

    res = load_social_content_result(
        db_session,
        brief_id=brief.id,
        attempt_id=att.id,
        brand_profile_id=ws.id,
    )
    assert res.attempt_status == "pending"
    assert commit_spy.call_count == 0
    assert rollback_spy.call_count == 0
    assert flush_spy.call_count == 0


def test_33_with_for_update_not_used(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """33. with_for_update kullanmaz."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    att = _create_attempt(db_session, brief=brief, ideas=ideas, status="pending")

    executed_sqls = []

    def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        executed_sqls.append(statement)

    event.listen(db_session.bind, "before_cursor_execute", before_cursor_execute)
    try:
        load_social_content_result(
            db_session,
            brief_id=brief.id,
            attempt_id=att.id,
            brand_profile_id=ws.id,
        )
    finally:
        event.remove(db_session.bind, "before_cursor_execute", before_cursor_execute)

    for sql in executed_sqls:
        assert "FOR UPDATE" not in sql.upper()


def test_34_dto_and_nested_collections_are_immutable(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """34. DTO ve nested koleksiyonlar immutable’dır."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    att = _create_attempt(db_session, brief=brief, ideas=ideas, status="pending")

    result = load_social_content_result(
        db_session,
        brief_id=brief.id,
        attempt_id=att.id,
        brand_profile_id=ws.id,
    )

    assert isinstance(result.requested_idea_ids, tuple)
    assert isinstance(result.successful_idea_ids, tuple)
    assert isinstance(result.unresolved_idea_ids, tuple)
    assert isinstance(result.contents, tuple)
    assert isinstance(result.warnings, tuple)

    with pytest.raises(FrozenInstanceError):
        result.attempt_status = "completed"  # type: ignore


def test_35_product_facts_and_usp_not_in_dto(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """35. product_facts/trusted_brand_usp DTO’da bulunmaz."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    att = _create_attempt(db_session, brief=brief, ideas=ideas, status="pending")

    result = load_social_content_result(
        db_session,
        brief_id=brief.id,
        attempt_id=att.id,
        brand_profile_id=ws.id,
    )

    assert not hasattr(result, "product_facts")
    assert not hasattr(result, "trusted_brand_usp")


def test_36_no_raw_content_or_ids_in_error_messages(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """36. Raw içerik ve ID değerleri hata mesajlarına sızmaz."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    secret_id = 87654321
    with pytest.raises(SocialContentReadNotFoundError) as exc_info:
        load_social_content_result(
            db_session,
            brief_id=brief.id,
            attempt_id=secret_id,
            brand_profile_id=ws.id,
        )

    msg = str(exc_info.value)
    assert str(secret_id) not in msg
    assert str(brief.id) not in msg
    assert str(ws.id) not in msg


def test_37_replayed_exact_bool_validation(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """37. replayed exact bool doğrulanır."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    att = _create_attempt(db_session, brief=brief, ideas=ideas, status="pending")

    with pytest.raises(SocialContentReadError) as exc_info:
        load_social_content_result(
            db_session,
            brief_id=brief.id,
            attempt_id=att.id,
            brand_profile_id=ws.id,
            replayed="not_a_bool",  # type: ignore
        )
    assert exc_info.value.error_code == CONTENT_READ_INCONSISTENT


def test_38_replayed_value_forwarded_to_dto(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """38. replayed değeri DTO’ya exact aktarılır."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    att = _create_attempt(db_session, brief=brief, ideas=ideas, status="pending")

    res_true = load_social_content_result(
        db_session,
        brief_id=brief.id,
        attempt_id=att.id,
        brand_profile_id=ws.id,
        replayed=True,
    )
    assert res_true.replayed is True

    res_false = load_social_content_result(
        db_session,
        brief_id=brief.id,
        attempt_id=att.id,
        brand_profile_id=ws.id,
        replayed=False,
    )
    assert res_false.replayed is False


# ==================== F1-G.5.7.2a YENİ TESTLER ====================


def test_39_failed_empty_warnings_none_reason_code_rejected(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """39. failed + warnings boş + reason_code=None reddedilir."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=2
    )
    att = _create_attempt(
        db_session,
        brief=brief,
        ideas=ideas,
        status="failed",
        reason_code=None,
        error_message="Hata oluştu.",
        warnings=None,
    )
    with pytest.raises(SocialContentReadError) as exc_info:
        load_social_content_result(
            db_session, brief_id=brief.id, attempt_id=att.id, brand_profile_id=ws.id
        )
    assert exc_info.value.error_code == CONTENT_READ_INCONSISTENT
    assert exc_info.value.field == "reason_code"


def test_40_failed_empty_warnings_empty_str_reason_code_rejected(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """40. failed + warnings boş + boş reason_code reddedilir."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=2
    )
    att = _create_attempt(
        db_session,
        brief=brief,
        ideas=ideas,
        status="failed",
        reason_code="   ",
        error_message="Hata oluştu.",
        warnings=None,
    )
    with pytest.raises(SocialContentReadError) as exc_info:
        load_social_content_result(
            db_session, brief_id=brief.id, attempt_id=att.id, brand_profile_id=ws.id
        )
    assert exc_info.value.error_code == CONTENT_READ_INCONSISTENT
    assert exc_info.value.field == "reason_code"


def test_41_failed_empty_warnings_unknown_failure_rejected(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """41. failed + warnings boş + unknown_failure reddedilir."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=2
    )
    att = _create_attempt(
        db_session,
        brief=brief,
        ideas=ideas,
        status="failed",
        reason_code="unknown_failure",
        error_message="Bilinmeyen hata.",
        warnings=None,
    )
    with pytest.raises(SocialContentReadError) as exc_info:
        load_social_content_result(
            db_session, brief_id=brief.id, attempt_id=att.id, brand_profile_id=ws.id
        )
    assert exc_info.value.error_code == CONTENT_READ_INCONSISTENT
    assert exc_info.value.field == "reason_code"


def test_42_failed_empty_warnings_content_batch_failed_rejected(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """42. failed + warnings boş + content_batch_failed reddedilir."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=2
    )
    att = _create_attempt(
        db_session,
        brief=brief,
        ideas=ideas,
        status="failed",
        reason_code="content_batch_failed",
        error_message="Eski format hata.",
        warnings=None,
    )
    with pytest.raises(SocialContentReadError) as exc_info:
        load_social_content_result(
            db_session, brief_id=brief.id, attempt_id=att.id, brand_profile_id=ws.id
        )
    assert exc_info.value.error_code == CONTENT_READ_INCONSISTENT
    assert exc_info.value.field == "reason_code"


def test_43_failed_empty_warnings_worker_cancelled_rejected(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """43. failed + warnings boş + worker_cancelled reddedilir."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=2
    )
    att = _create_attempt(
        db_session,
        brief=brief,
        ideas=ideas,
        status="failed",
        reason_code="worker_cancelled",
        error_message="Worker iptal edildi.",
        warnings=None,
    )
    with pytest.raises(SocialContentReadError) as exc_info:
        load_social_content_result(
            db_session, brief_id=brief.id, attempt_id=att.id, brand_profile_id=ws.id
        )
    assert exc_info.value.error_code == CONTENT_READ_INCONSISTENT
    assert exc_info.value.field == "reason_code"


def test_44_failed_empty_warnings_dispatch_failed_accepted(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """44. failed + warnings boş + dispatch_failed kabul edilir."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=2
    )
    att = _create_attempt(
        db_session,
        brief=brief,
        ideas=ideas,
        status="failed",
        reason_code="dispatch_failed",
        error_message="Celery kuyruğuna dispatch edilemedi.",
        warnings=None,
    )
    res = load_social_content_result(
        db_session, brief_id=brief.id, attempt_id=att.id, brand_profile_id=ws.id
    )
    assert res.attempt_status == "failed"
    assert res.reason_code == "dispatch_failed"
    assert res.successful_idea_ids == ()
    assert res.unresolved_idea_ids == tuple(i.id for i in ideas)
    assert res.contents == ()
    assert res.warnings == ()


def test_45_failed_empty_warnings_brief_stale_accepted(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """45. failed + warnings boş + brief_stale kabul edilir."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=2
    )
    att = _create_attempt(
        db_session,
        brief=brief,
        ideas=ideas,
        status="failed",
        reason_code="brief_stale",
        error_message="Brief stale durumda.",
        warnings=None,
    )
    res = load_social_content_result(
        db_session, brief_id=brief.id, attempt_id=att.id, brand_profile_id=ws.id
    )
    assert res.attempt_status == "failed"
    assert res.reason_code == "brief_stale"
    assert res.successful_idea_ids == ()
    assert res.unresolved_idea_ids == tuple(i.id for i in ideas)


def test_46_failed_empty_warnings_assignment_changed_accepted(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """46. failed + warnings boş + assignment_changed kabul edilir."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=2
    )
    att = _create_attempt(
        db_session,
        brief=brief,
        ideas=ideas,
        status="failed",
        reason_code="assignment_changed",
        error_message="Kanal atama sürümü değişti.",
        warnings=None,
    )
    res = load_social_content_result(
        db_session, brief_id=brief.id, attempt_id=att.id, brand_profile_id=ws.id
    )
    assert res.attempt_status == "failed"
    assert res.reason_code == "assignment_changed"
    assert res.successful_idea_ids == ()
    assert res.unresolved_idea_ids == tuple(i.id for i in ideas)


def test_47_failed_empty_warnings_content_heartbeat_failed_accepted(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """47. failed + warnings boş + content_heartbeat_failed kabul edilir."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=2
    )
    att = _create_attempt(
        db_session,
        brief=brief,
        ideas=ideas,
        status="failed",
        reason_code="content_heartbeat_failed",
        error_message="Heartbeat güncellenemedi.",
        warnings=None,
    )
    res = load_social_content_result(
        db_session, brief_id=brief.id, attempt_id=att.id, brand_profile_id=ws.id
    )
    assert res.attempt_status == "failed"
    assert res.reason_code == "content_heartbeat_failed"
    assert res.successful_idea_ids == ()
    assert res.unresolved_idea_ids == tuple(i.id for i in ideas)


def test_48_failed_empty_warnings_content_persistence_failed_accepted(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """48. failed + warnings boş + content_persistence_failed kabul edilir."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=2
    )
    att = _create_attempt(
        db_session,
        brief=brief,
        ideas=ideas,
        status="failed",
        reason_code="content_persistence_failed",
        error_message="İçerik veritabanına kaydedilemedi.",
        warnings=None,
    )
    res = load_social_content_result(
        db_session, brief_id=brief.id, attempt_id=att.id, brand_profile_id=ws.id
    )
    assert res.attempt_status == "failed"
    assert res.reason_code == "content_persistence_failed"
    assert res.successful_idea_ids == ()
    assert res.unresolved_idea_ids == tuple(i.id for i in ideas)


@pytest.mark.parametrize("canonical_reason", sorted(ALLOWED_CONTENTS_FAILURE_REASONS))
def test_49_parametric_all_canonical_failure_reasons_accepted(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag, canonical_reason
):
    """49. Parametrik test: ALLOWED_CONTENTS_FAILURE_REASONS içindeki bütün 16 kod kabul edilir."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=2
    )
    att = _create_attempt(
        db_session,
        brief=brief,
        ideas=ideas,
        status="failed",
        reason_code=canonical_reason,
        error_message=f"Hata detayı: {canonical_reason}",
        warnings=None,
    )
    res = load_social_content_result(
        db_session, brief_id=brief.id, attempt_id=att.id, brand_profile_id=ws.id
    )
    assert res.attempt_status == "failed"
    assert res.reason_code == canonical_reason
    assert res.successful_idea_ids == ()
    assert res.unresolved_idea_ids == tuple(i.id for i in ideas)
    assert res.contents == ()
    assert res.warnings == ()


def test_50_failed_with_warnings_content_generation_failed_accepted(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """50. failed + warnings nonempty + content_generation_failed kabul edilir."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=2
    )
    _create_valid_content(db_session, brief=brief, target=target, idea=ideas[0])
    db_session.commit()

    att = _create_attempt(
        db_session,
        brief=brief,
        ideas=ideas,
        status="failed",
        reason_code="content_generation_failed",
        error_message="Batch üretimi başarısız.",
        warnings=[
            {
                "idea_id": ideas[1].id,
                "reason_code": "content_rejected",
                "claims": ["Yüzde 100 garantili"],
                "ai_calls_used": 2,
            }
        ],
    )
    res = load_social_content_result(
        db_session, brief_id=brief.id, attempt_id=att.id, brand_profile_id=ws.id
    )
    assert res.attempt_status == "failed"
    assert res.reason_code == "content_generation_failed"
    assert res.successful_idea_ids == (ideas[0].id,)
    assert res.unresolved_idea_ids == (ideas[1].id,)
    assert len(res.contents) == 1
    assert len(res.warnings) == 1


def test_51_failed_with_warnings_worker_bootstrap_failed_rejected(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """51. failed + warnings nonempty + worker_bootstrap_failed reddedilir."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=2
    )
    _create_valid_content(db_session, brief=brief, target=target, idea=ideas[0])
    db_session.commit()

    att = _create_attempt(
        db_session,
        brief=brief,
        ideas=ideas,
        status="failed",
        reason_code="worker_bootstrap_failed",
        error_message="Bootstrap hatası.",
        warnings=[
            {
                "idea_id": ideas[1].id,
                "reason_code": "content_rejected",
                "claims": [],
                "ai_calls_used": 1,
            }
        ],
    )
    with pytest.raises(SocialContentReadError) as exc_info:
        load_social_content_result(
            db_session, brief_id=brief.id, attempt_id=att.id, brand_profile_id=ws.id
        )
    assert exc_info.value.error_code == CONTENT_READ_INCONSISTENT
    assert exc_info.value.field == "reason_code"


def test_52_failed_with_warnings_dispatch_failed_rejected(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """52. failed + warnings nonempty + dispatch_failed reddedilir."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=2
    )
    _create_valid_content(db_session, brief=brief, target=target, idea=ideas[0])
    db_session.commit()

    att = _create_attempt(
        db_session,
        brief=brief,
        ideas=ideas,
        status="failed",
        reason_code="dispatch_failed",
        error_message="Dispatch hatası.",
        warnings=[
            {
                "idea_id": ideas[1].id,
                "reason_code": "content_rejected",
                "claims": [],
                "ai_calls_used": 1,
            }
        ],
    )
    with pytest.raises(SocialContentReadError) as exc_info:
        load_social_content_result(
            db_session, brief_id=brief.id, attempt_id=att.id, brand_profile_id=ws.id
        )
    assert exc_info.value.error_code == CONTENT_READ_INCONSISTENT
    assert exc_info.value.field == "reason_code"


def test_53_failed_with_warnings_worker_lost_rejected(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """53. failed + warnings nonempty + worker_lost reddedilir."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=2
    )
    _create_valid_content(db_session, brief=brief, target=target, idea=ideas[0])
    db_session.commit()

    att = _create_attempt(
        db_session,
        brief=brief,
        ideas=ideas,
        status="failed",
        reason_code="worker_lost",
        error_message="Worker kayboldu.",
        warnings=[
            {
                "idea_id": ideas[1].id,
                "reason_code": "content_rejected",
                "claims": [],
                "ai_calls_used": 1,
            }
        ],
    )
    with pytest.raises(SocialContentReadError) as exc_info:
        load_social_content_result(
            db_session, brief_id=brief.id, attempt_id=att.id, brand_profile_id=ws.id
        )
    assert exc_info.value.error_code == CONTENT_READ_INCONSISTENT
    assert exc_info.value.field == "reason_code"


def test_54_read_module_does_not_define_parallel_failure_allowlist():
    """54. content_read modülünde ikinci/paralel failure allowlist bulunmadığı doğrulanır."""
    import app.core.social.content_read as cr
    assert not hasattr(cr, "CONTENTS_FAILURE_REASON_ALLOWLIST")
    assert hasattr(cr, "ALLOWED_CONTENTS_FAILURE_REASONS")
    from app.generators.social.attempt_state import ALLOWED_CONTENTS_FAILURE_REASONS as AS_REASONS
    assert cr.ALLOWED_CONTENTS_FAILURE_REASONS is AS_REASONS


def test_55_no_raw_reason_code_leaked_in_error_message(
    db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """55. Geçersiz reason_code metni hata mesajında sızdırılmaz."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=2
    )
    secret_code = "super_secret_unallowed_failure_reason_999"
    att = _create_attempt(
        db_session,
        brief=brief,
        ideas=ideas,
        status="failed",
        reason_code=secret_code,
        error_message="Bir hata.",
        warnings=None,
    )
    with pytest.raises(SocialContentReadError) as exc_info:
        load_social_content_result(
            db_session, brief_id=brief.id, attempt_id=att.id, brand_profile_id=ws.id
        )
    assert secret_code not in str(exc_info.value)
    assert exc_info.value.error_code == CONTENT_READ_INCONSISTENT
    assert exc_info.value.field == "reason_code"

