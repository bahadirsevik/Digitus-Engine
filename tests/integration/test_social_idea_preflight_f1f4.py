# -*- coding: utf-8 -*-
"""Integration tests for Social Brief Ideas Preflight, Attempt and Plan Snapshot (F1-F.4).

Tüm testler gerçek PostgreSQL test veritabanını ve izole oturumu kullanır.
Test edilen senaryolar:
A. Başarı ve plan snapshot doğrulaması
B. Request strict Pydantic sözleşmesi
C. Workspace izolasyonu ve kategori uygunluğu
D. Brief durumu ve hazır olma kontrolleri
E. Same-key idempotency ve replay doğrulamaları
F. Çakışma ve önceden üretilmiş fikir kontrolleri
G. Transaction sınırları ve kanonik lock sırası
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import patch

import pytest
from pydantic import ValidationError
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.config import settings
from app.core.social.idea_flow import (
    SocialIdeaCategorySnapshot,
    SocialIdeaFlowError,
    SocialIdeaGenerationStart,
    begin_social_idea_generation,
)
from app.database.models import (
    ChannelPool,
    SocialBrief,
    SocialBriefKeyword,
    SocialBriefTarget,
    SocialCategory,
    SocialGenerationAttempt,
    SocialIdea,
)
from app.generators.social.attempt_state import AttemptConflictError
from app.schemas.social_brief import SocialBriefIdeasGenerateRequest

T0 = datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def enable_flag(monkeypatch):
    """Test süresince feature flag'i aktif kılar."""
    monkeypatch.setattr(settings, "ENABLE_SOCIAL_BRIEF_FLOW", True)
    yield


def _setup_fresh_environment(
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
    num_kws: int = 3,
    workspace_name: str = "Ideas Brand",
):
    """Merkezi freshness sözleşmesini karşılayan workspace/run/pool oluşturur."""
    workspace = make_workspace(name=workspace_name, status="confirmed")
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
            text_value=f"{workspace_name} kw {i + 1}",
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


def _create_test_brief(
    db_session: Session,
    run_id: int,
    keywords: list,
    is_stale: bool = False,
    channel_assignment_version: int = 1,
    locked_at: datetime | None = None,
    brand_name: str = "Test Marka",
    brand_context: str = "Test Context",
    targets_data: list[tuple[str, str]] | None = None,
) -> SocialBrief:
    """Test için SocialBrief ve bağlı keyword/target kayıtlarını oluşturur."""
    brief = SocialBrief(
        scoring_run_id=run_id,
        brand_name_snapshot=brand_name,
        brand_context_snapshot=brand_context,
        channel_assignment_version=channel_assignment_version,
        format_matrix_version="v1",
        is_stale=is_stale,
        locked_at=locked_at,
    )
    db_session.add(brief)
    db_session.flush()

    for pos, kw in enumerate(keywords):
        bk = SocialBriefKeyword(
            brief_id=brief.id,
            keyword_id=kw.id,
            keyword_snapshot=kw.keyword,
            position=pos,
        )
        db_session.add(bk)

    if targets_data is None:
        targets_data = [("instagram", "post"), ("twitter", "thread")]

    for plat, fmt in targets_data:
        t = SocialBriefTarget(
            brief_id=brief.id,
            platform=plat,
            content_format=fmt,
        )
        db_session.add(t)

    db_session.commit()
    db_session.refresh(brief)
    return brief


def _setup_environment_with_categories(
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
    num_categories: int = 4,
    targets_data: list[tuple[str, str]] | None = None,
    locked: bool = True,
    categories_attempt_status: str = "completed",
    workspace_name: str = "Ideas Brand",
):
    """Fikir üretimi testleri için eksiksiz hazır ortam (workspace, brief, categories)."""
    ws, run, kws = _setup_fresh_environment(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        num_kws=3,
        workspace_name=workspace_name,
    )
    brief = _create_test_brief(
        db_session,
        run.id,
        kws,
        locked_at=T0 if locked else None,
        targets_data=targets_data,
    )

    if categories_attempt_status is not None:
        cat_attempt = SocialGenerationAttempt(
            brief_id=brief.id,
            stage="categories",
            idempotency_key="cat-attempt-key-1",
            status=categories_attempt_status,
            heartbeat_at=T0,
            lease_expires_at=T0 + timedelta(seconds=1500),
            created_at=T0,
        )
        db_session.add(cat_attempt)
        db_session.flush()

    categories = []
    for i in range(num_categories):
        cat = SocialCategory(
            scoring_run_id=run.id,
            brief_id=brief.id,
            category_name=f"Kategori {i + 1}",
            category_type="educational",
            description=f"Açıklama {i + 1} için eğitici içerik yönü.",
            is_stale=False,
            relevance_score=0.9,
            suggested_keyword_ids=[kws[0].id],
        )
        db_session.add(cat)
        categories.append(cat)

    db_session.commit()
    for cat in categories:
        db_session.refresh(cat)
    return ws, run, brief, categories


# ==================== GRUP A: BAŞARI VE SNAPSHOT ====================

def test_a01_successful_ideas_preflight_and_plan_creation(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """A1. Geçerli brief/kategori/target girdileriyle pending attempt ve plan snapshot oluşur."""
    ws, run, brief, cats = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword, num_categories=3
    )

    selected_ids = [cats[1].id, cats[0].id]  # İstek sırası canonical olarak korunur
    req = SocialBriefIdeasGenerateRequest(
        idempotency_key="idea-key-01",
        category_ids=selected_ids,
        ideas_per_category=3,
    )

    result = begin_social_idea_generation(
        db_session,
        brief_id=brief.id,
        brand_profile_id=ws.id,
        request=req,
        now=T0,
    )

    assert result.attempt_created is True
    assert result.attempt_status == "pending"
    assert result.brief_id == brief.id
    assert result.scoring_run_id == run.id
    assert result.brand_profile_id == ws.id
    assert result.selected_category_ids == tuple(selected_ids)
    assert result.ideas_per_category == 3
    assert len(result.plan.category_plans) == 2
    assert result.plan.total_requested == 6

    # Category snapshot sırası istek sırasıyla birebir eşleşmeli
    assert [c.category_id for c in result.categories] == selected_ids
    assert result.categories[0].category_name == cats[1].category_name
    assert result.categories[1].category_name == cats[0].category_name

    # Coverage snapshot doğrulaması
    attempt = db_session.query(SocialGenerationAttempt).filter_by(id=result.attempt_id).one()
    cov = attempt.coverage
    assert cov is not None
    assert cov["schema_version"] == "ideas_plan_v1"
    assert cov["request"]["category_ids"] == selected_ids
    assert cov["request"]["ideas_per_category"] == 3
    assert cov["plan"]["total_requested"] == 6
    assert len(cov["plan"]["categories"]) == 2
    assert cov["generated"] == {"total_accepted": 0, "target_ids": []}
    assert "warnings" not in cov
    assert attempt.warnings == []

    # SocialIdea satırı oluşmamış olmalı
    assert db_session.query(SocialIdea).count() == 0


# ==================== GRUP B: REQUEST DOĞRULAMASI ====================

def test_b01_extra_fields_forbidden():
    """B1. Request şeması extra alanları fail-closed reddeder."""
    with pytest.raises(ValidationError):
        SocialBriefIdeasGenerateRequest(
            idempotency_key="key-b1",
            category_ids=[1, 2],
            ideas_per_category=3,
            extra_field="unwanted",  # type: ignore[call-arg]
        )


def test_b02_invalid_category_ids_container():
    """B2. category_ids liste olmalıdır; string, dict, None veya int reddedilir."""
    with pytest.raises(ValidationError):
        SocialBriefIdeasGenerateRequest(idempotency_key="key", category_ids="1,2")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        SocialBriefIdeasGenerateRequest(idempotency_key="key", category_ids=123)  # type: ignore[arg-type]


def test_b03_category_ids_cardinality_boundaries():
    """B3. category_ids boş olamaz ve 7 öğe içeremez; 1-6 kabul edilir."""
    # Boş liste reddedilir
    with pytest.raises(ValidationError):
        SocialBriefIdeasGenerateRequest(idempotency_key="key", category_ids=[])

    # 7 öğe reddedilir
    with pytest.raises(ValidationError):
        SocialBriefIdeasGenerateRequest(idempotency_key="key", category_ids=[1, 2, 3, 4, 5, 6, 7])

    # 1 ve 6 kabul edilir
    r1 = SocialBriefIdeasGenerateRequest(idempotency_key="key", category_ids=[1])
    assert len(r1.category_ids) == 1
    r6 = SocialBriefIdeasGenerateRequest(idempotency_key="key", category_ids=[1, 2, 3, 4, 5, 6])
    assert len(r6.category_ids) == 6


def test_b04_category_ids_element_types_and_positivity():
    """B4. category_ids içinde boolean, float, string, 0 veya negatif değerler reddedilir."""
    for invalid_val in [True, False, 0, -1, "1", 2.5]:
        with pytest.raises(ValidationError):
            SocialBriefIdeasGenerateRequest(idempotency_key="key", category_ids=[1, invalid_val])  # type: ignore[list-item]


def test_b05_duplicate_category_ids_rejected():
    """B5. category_ids içinde mükerrer ID'ler sessizce kabul edilmez, reddedilir."""
    with pytest.raises(ValidationError):
        SocialBriefIdeasGenerateRequest(idempotency_key="key", category_ids=[1, 2, 1])


def test_b06_ideas_per_category_boundaries_and_types():
    """B6. ideas_per_category 1-5 arası pozitif tamsayı olmalıdır."""
    # 0 ve 6 reddedilir
    with pytest.raises(ValidationError):
        SocialBriefIdeasGenerateRequest(idempotency_key="key", category_ids=[1], ideas_per_category=0)
    with pytest.raises(ValidationError):
        SocialBriefIdeasGenerateRequest(idempotency_key="key", category_ids=[1], ideas_per_category=6)

    # Boolean, float ve string reddedilir
    with pytest.raises(ValidationError):
        SocialBriefIdeasGenerateRequest(idempotency_key="key", category_ids=[1], ideas_per_category=True)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        SocialBriefIdeasGenerateRequest(idempotency_key="key", category_ids=[1], ideas_per_category="3")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        SocialBriefIdeasGenerateRequest(idempotency_key="key", category_ids=[1], ideas_per_category=3.0)  # type: ignore[arg-type]


def test_b07_idempotency_key_validation():
    """B7. idempotency_key boşluksuz, 1-128 karakter olmalıdır; whitespace-only reddedilir."""
    with pytest.raises(ValidationError):
        SocialBriefIdeasGenerateRequest(idempotency_key="   ", category_ids=[1])
    with pytest.raises(ValidationError):
        SocialBriefIdeasGenerateRequest(idempotency_key="", category_ids=[1])
    with pytest.raises(ValidationError):
        SocialBriefIdeasGenerateRequest(idempotency_key="k" * 129, category_ids=[1])


# ==================== GRUP C: İZOLASYON VE KATEGORİ UYGUNLUĞU ====================

def test_c01_cross_workspace_brief_not_found(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """C1. Başka bir workspace (brand_profile_id) ile erişim tek tip BRIEF_NOT_FOUND döner."""
    ws, run, brief, cats = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    req = SocialBriefIdeasGenerateRequest(idempotency_key="c1", category_ids=[cats[0].id])

    with pytest.raises(SocialIdeaFlowError) as exc:
        begin_social_idea_generation(
            db_session,
            brief_id=brief.id,
            brand_profile_id=ws.id + 9999,  # Farklı workspace
            request=req,
        )
    assert exc.value.error_code == "BRIEF_NOT_FOUND"


def test_c02_category_from_another_brief_rejected_mixed_brief(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """C2. İstek başka bir brief'e ait kategori içerirse MIXED_BRIEF döner."""
    ws1, run1, brief1, cats1 = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword, workspace_name="Brand 1"
    )
    ws2, run2, brief2, cats2 = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword, workspace_name="Brand 2"
    )

    req = SocialBriefIdeasGenerateRequest(
        idempotency_key="c2",
        category_ids=[cats1[0].id, cats2[0].id],  # cats2 başka brief'e ait
    )

    with pytest.raises(SocialIdeaFlowError) as exc:
        begin_social_idea_generation(
            db_session,
            brief_id=brief1.id,
            brand_profile_id=ws1.id,
            request=req,
        )
    assert exc.value.error_code == "MIXED_BRIEF"
    assert exc.value.field == "category_ids"


def test_c03_non_existent_or_stale_category_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """C3. Bulunamayan veya stale kategori seçildiğinde CATEGORY_NOT_ELIGIBLE döner."""
    ws, run, brief, cats = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )

    # 1. Bulunamayan kategori
    req_missing = SocialBriefIdeasGenerateRequest(idempotency_key="c3-1", category_ids=[999999])
    with pytest.raises(SocialIdeaFlowError) as exc1:
        begin_social_idea_generation(
            db_session,
            brief_id=brief.id,
            brand_profile_id=ws.id,
            request=req_missing,
        )
    assert exc1.value.error_code == "CATEGORY_NOT_ELIGIBLE"

    # 2. Stale kategori
    cats[0].is_stale = True
    db_session.commit()

    req_stale = SocialBriefIdeasGenerateRequest(idempotency_key="c3-2", category_ids=[cats[0].id])
    with pytest.raises(SocialIdeaFlowError) as exc2:
        begin_social_idea_generation(
            db_session,
            brief_id=brief.id,
            brand_profile_id=ws.id,
            request=req_stale,
        )
    assert exc2.value.error_code == "CATEGORY_NOT_ELIGIBLE"


# ==================== GRUP D: BRIEF DURUMU ====================

def test_d01_unlocked_brief_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """D1. locked_at null olan (kategori üretilmemiş) brief reddedilir."""
    ws, run, brief, cats = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword, locked=False
    )
    req = SocialBriefIdeasGenerateRequest(idempotency_key="d1", category_ids=[cats[0].id])

    with pytest.raises(SocialIdeaFlowError) as exc:
        begin_social_idea_generation(
            db_session,
            brief_id=brief.id,
            brand_profile_id=ws.id,
            request=req,
        )
    assert exc.value.error_code == "BRIEF_NOT_LOCKED"


def test_d02_stale_brief_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """D2. is_stale True olan brief reddedilir."""
    ws, run, brief, cats = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    brief.is_stale = True
    db_session.commit()

    req = SocialBriefIdeasGenerateRequest(idempotency_key="d2", category_ids=[cats[0].id])
    with pytest.raises(SocialIdeaFlowError) as exc:
        begin_social_idea_generation(
            db_session,
            brief_id=brief.id,
            brand_profile_id=ws.id,
            request=req,
        )
    assert exc.value.error_code == "BRIEF_STALE"


def test_d03_assignment_version_mismatch_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """D3. channel_assignment_version uyuşmazlığı ASSIGNMENT_CHANGED ile reddedilir."""
    ws, run, brief, cats = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    brief.channel_assignment_version = run.channel_assignment_version + 1
    db_session.commit()

    req = SocialBriefIdeasGenerateRequest(idempotency_key="d3", category_ids=[cats[0].id])
    with pytest.raises(SocialIdeaFlowError) as exc:
        begin_social_idea_generation(
            db_session,
            brief_id=brief.id,
            brand_profile_id=ws.id,
            request=req,
        )
    assert exc.value.error_code == "ASSIGNMENT_CHANGED"


def test_d04_categories_attempt_not_completed_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """D4. categories attempt tamamlanmamışsa CATEGORIES_NOT_READY döner."""
    ws, run, brief, cats = _setup_environment_with_categories(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        categories_attempt_status="running",
    )
    req = SocialBriefIdeasGenerateRequest(idempotency_key="d4", category_ids=[cats[0].id])

    with pytest.raises(SocialIdeaFlowError) as exc:
        begin_social_idea_generation(
            db_session,
            brief_id=brief.id,
            brand_profile_id=ws.id,
            request=req,
        )
    assert exc.value.error_code == "CATEGORIES_NOT_READY"


def test_d05_target_cardinality_and_canonical_matrix_validation(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """D5. Brief altında geçersiz hedef sayısı veya format matrisi dışı hedef IDEA_TARGETS_INVALID döner."""
    ws, run, brief, cats = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )

    # Hedef formatını geçersiz kıl
    target = db_session.query(SocialBriefTarget).filter_by(brief_id=brief.id).first()
    target.content_format = "unknown_format"
    db_session.commit()

    req = SocialBriefIdeasGenerateRequest(idempotency_key="d5", category_ids=[cats[0].id])
    with pytest.raises(SocialIdeaFlowError) as exc:
        begin_social_idea_generation(
            db_session,
            brief_id=brief.id,
            brand_profile_id=ws.id,
            request=req,
        )
    assert exc.value.error_code == "IDEA_TARGETS_INVALID"


# ==================== GRUP E: REPLAY DOĞRULAMASI ====================

def test_e01_same_key_same_request_replayed_idempotently(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """E1. Aynı key ve aynı parametrelerle yapılan istek mevcut attempt'i değiştirilmeden döndürür."""
    ws, run, brief, cats = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    req = SocialBriefIdeasGenerateRequest(
        idempotency_key="same-key-e1",
        category_ids=[cats[0].id, cats[1].id],
        ideas_per_category=3,
    )

    r1 = begin_social_idea_generation(
        db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T0
    )
    assert r1.attempt_created is True

    att1 = db_session.query(SocialGenerationAttempt).filter_by(id=r1.attempt_id).one()
    initial_lease = att1.lease_expires_at
    initial_cov = att1.coverage

    # İkinci çağrı: 10 saniye sonra
    T1 = T0 + timedelta(seconds=10)
    r2 = begin_social_idea_generation(
        db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req, now=T1
    )

    assert r2.attempt_created is False
    assert r2.attempt_id == r1.attempt_id
    assert r2.plan == r1.plan

    att2 = db_session.query(SocialGenerationAttempt).filter_by(id=r1.attempt_id).one()
    # Lease ve coverage değişmemiş olmalı
    assert att2.lease_expires_at == initial_lease
    assert att2.coverage == initial_cov


def test_e02_replay_category_order_mismatch(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """E2. Aynı key ile farklı kategori sırası verilirse IDEA_ATTEMPT_REQUEST_MISMATCH üretilir."""
    ws, run, brief, cats = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    req1 = SocialBriefIdeasGenerateRequest(
        idempotency_key="key-e2",
        category_ids=[cats[0].id, cats[1].id],
    )
    begin_social_idea_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req1)

    # Sırası ters istek
    req2 = SocialBriefIdeasGenerateRequest(
        idempotency_key="key-e2",
        category_ids=[cats[1].id, cats[0].id],
    )
    with pytest.raises(SocialIdeaFlowError) as exc:
        begin_social_idea_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req2)
    assert exc.value.error_code == "IDEA_ATTEMPT_REQUEST_MISMATCH"


def test_e03_replay_ideas_per_category_mismatch(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """E3. Aynı key ile farklı ideas_per_category verilirse IDEA_ATTEMPT_REQUEST_MISMATCH üretilir."""
    ws, run, brief, cats = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    req1 = SocialBriefIdeasGenerateRequest(
        idempotency_key="key-e3",
        category_ids=[cats[0].id],
        ideas_per_category=2,
    )
    begin_social_idea_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req1)

    req2 = SocialBriefIdeasGenerateRequest(
        idempotency_key="key-e3",
        category_ids=[cats[0].id],
        ideas_per_category=4,
    )
    with pytest.raises(SocialIdeaFlowError) as exc:
        begin_social_idea_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req2)
    assert exc.value.error_code == "IDEA_ATTEMPT_REQUEST_MISMATCH"


def test_e04_replay_corrupt_coverage_snapshot(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """E4. Coverage snapshot bozuksa IDEA_ATTEMPT_SNAPSHOT_INVALID üretilir."""
    ws, run, brief, cats = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    req = SocialBriefIdeasGenerateRequest(
        idempotency_key="key-e4",
        category_ids=[cats[0].id],
    )
    r = begin_social_idea_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req)

    # Coverage'ı bozalım
    att = db_session.query(SocialGenerationAttempt).filter_by(id=r.attempt_id).one()
    att.coverage = {"broken": True}
    db_session.commit()

    with pytest.raises(SocialIdeaFlowError) as exc:
        begin_social_idea_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req)
    assert exc.value.error_code == "IDEA_ATTEMPT_SNAPSHOT_INVALID"


def test_e05_completed_same_key_replay_reads_with_existing_ideas(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """E5. Tamamlanmış attempt ve mevcut fikirler varken same-key replay başarıyla okunabilir."""
    ws, run, brief, cats = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    req = SocialBriefIdeasGenerateRequest(
        idempotency_key="key-e5",
        category_ids=[cats[0].id],
    )
    r = begin_social_idea_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req)

    # Attempt'i tamamla ve fikir satırı ekle
    att = db_session.query(SocialGenerationAttempt).filter_by(id=r.attempt_id).one()
    att.status = "completed"

    idea = SocialIdea(
        category_id=cats[0].id,
        brief_id=brief.id,
        idea_title="Önceden Üretilmiş Fikir",
        is_stale=False,
    )
    db_session.add(idea)
    db_session.commit()

    # Same-key replay: fikirler mevcut olmasına rağmen hata vermemeli
    r_replay = begin_social_idea_generation(
        db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req
    )
    assert r_replay.attempt_created is False
    assert r_replay.attempt_status == "completed"


# ==================== GRUP F: ÇAKIŞMA VE ÖNCEDEN ÜRETİM ====================

def test_f01_active_attempt_conflict(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """F1. Aynı brief için farklı idempotency key ile aktif attempt varsa ATTEMPT_CONFLICT fırlatılır."""
    ws, run, brief, cats = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    req1 = SocialBriefIdeasGenerateRequest(idempotency_key="key-f1-a", category_ids=[cats[0].id])
    begin_social_idea_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req1)

    req2 = SocialBriefIdeasGenerateRequest(idempotency_key="key-f1-b", category_ids=[cats[0].id])
    with pytest.raises(AttemptConflictError) as exc:
        begin_social_idea_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req2)
    assert exc.value.error_code == "ATTEMPT_CONFLICT"


def test_f02_new_key_rejected_if_ideas_already_generated(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """F2. Brief altında non-stale fikirler varken farklı bir key ile yeni attempt açılması IDEAS_ALREADY_GENERATED ile reddedilir."""
    ws, run, brief, cats = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )

    # Brief'e ait bir fikir ekleyelim
    idea = SocialIdea(
        category_id=cats[0].id,
        brief_id=brief.id,
        idea_title="Mevcut Fikir",
        is_stale=False,
    )
    db_session.add(idea)
    db_session.commit()

    req = SocialBriefIdeasGenerateRequest(idempotency_key="new-key-f2", category_ids=[cats[0].id])
    with pytest.raises(SocialIdeaFlowError) as exc:
        begin_social_idea_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req)
    assert exc.value.error_code == "IDEAS_ALREADY_GENERATED"


# ==================== GRUP G: TRANSACTION VE KİLİT SIRASI ====================

def test_g01_service_does_not_commit_or_rollback(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """G1. Servis kendi içinde commit veya rollback çağırmaz."""
    ws, run, brief, cats = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    req = SocialBriefIdeasGenerateRequest(idempotency_key="key-g1", category_ids=[cats[0].id])

    with patch.object(db_session, "commit") as mock_c, patch.object(db_session, "rollback") as mock_r:
        res = begin_social_idea_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req)
        assert res.attempt_id > 0
        assert mock_c.call_count == 0
        assert mock_r.call_count == 0


def test_g02_caller_rollback_cleans_attempt_and_coverage(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """G2. Caller rollback yaptığında attempt ve coverage tamamen temizlenir."""
    ws, run, brief, cats = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    req = SocialBriefIdeasGenerateRequest(idempotency_key="key-g2", category_ids=[cats[0].id])

    res = begin_social_idea_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req)
    att_id = res.attempt_id
    assert att_id > 0

    # Caller rollback yapıyor
    db_session.rollback()

    assert db_session.query(SocialGenerationAttempt).filter_by(id=att_id).first() is None


def test_g03_sql_lock_order_is_canonical_and_not_attempt_first(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """G3. SQL lock sırası kesinlikle BrandProfile -> ScoringRun -> SocialBrief -> SocialGenerationAttempt'tir."""
    ws, run, brief, cats = _setup_environment_with_categories(
        db_session, make_workspace, make_scoring_run, make_keyword
    )
    req = SocialBriefIdeasGenerateRequest(idempotency_key="key-g3", category_ids=[cats[0].id])

    captured_locks: list[str] = []

    def _query_catcher(conn, cursor, statement, parameters, context, executemany):
        stmt_lower = statement.lower()
        if "for update" in stmt_lower:
            if "brand_profiles" in stmt_lower:
                captured_locks.append("BrandProfile")
            elif "scoring_runs" in stmt_lower:
                captured_locks.append("ScoringRun")
            elif "social_briefs" in stmt_lower:
                captured_locks.append("SocialBrief")
            elif "social_generation_attempts" in stmt_lower:
                captured_locks.append("SocialGenerationAttempt")

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", _query_catcher)
    try:
        begin_social_idea_generation(db_session, brief_id=brief.id, brand_profile_id=ws.id, request=req)
        db_session.commit()
    finally:
        event.remove(engine, "before_cursor_execute", _query_catcher)

    # Sıralamada ilk görünüşleri kontrol et
    bp_idx = captured_locks.index("BrandProfile")
    sr_idx = captured_locks.index("ScoringRun")
    sb_idx = captured_locks.index("SocialBrief")
    att_idx = captured_locks.index("SocialGenerationAttempt")

    assert bp_idx < sr_idx < sb_idx < att_idx
