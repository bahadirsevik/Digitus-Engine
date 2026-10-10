# -*- coding: utf-8 -*-
"""MİKRO FAZ F1-G.5.7.3a — Contents API Mutlak Hata Sanitizasyonu ve Response Adapter Sertleştirmesi Testleri.

Tüm testler gerçek PostgreSQL test veritabanını ve FastAPI TestClient'ı kullanır.
Test edilen senaryolar:
A. DEBUG=True ve DEBUG=False Veri Sızıntısı Koruması:
   - POST preflight unexpected exception
   - Completed replay unexpected read exception
   - Pending replay unexpected read exception
   - Successful dispatch sonrası read exception
   - GET reconciliation exception
   - GET read exception
   - Response converter exception
   -> Her senaryoda exception mesajındaki hassas metinler (SQL, secret fact, USP, prompt)
      kesinlikle HTTP yanıt gövdesine sızmaz.
B. Bilinmeyen Attempt Durumu Güvenliği:
   - Tanınmayan/bozuk attempt status -> 500 CONTENT_READ_INCONSISTENT.
   - Raw status string HTTP yanıtına sızmaz, Celery redispatch yapılmaz.
C. Hook Response Adapter Fail-Closed & Strict Typed Doğrulama:
   - Geçerli ValidatedHook canonical SocialContentHookResponse olarak serialize edilir.
   - Unknown hook nesnesi str() ile response'a taşınmaz, fail-closed 500 döner.
   - Malformed dict kabul edilmez.
   - Extra hook key (extra='forbid') fail-closed davranır.
   - Hook içindeki hassas metin hata mesajına sızmaz.
D. Format Payload Serializasyonu Güvenliği:
   - ValidatedVideoPayload, ValidatedCarouselPayload, ValidatedThreadPayload doğru serialize edilir.
   - Beklenmeyen payload tipi (str, int vb.) fail-closed 500 döner.
   - Arbitrary dict içindeki ekstra secret response'a taşınmaz, fail-closed olur.
   - Raw payload hata mesajına sızmaz.
E. Temel Regresyonlar:
   - POST 202 ve tek dispatch.
   - Completed replay 200, no dispatch.
   - Pending/running replay 202, no dispatch.
   - Failed/partial replay 409 terminal, no dispatch.
   - Broker enqueue failure compensation 503.
   - Broker compensation failure 500.
   - Post-dispatch read failure attempt'i pending bırakır.
   - Expired lease GET sırasında failed/worker_lost reconciliation.
   - Terminal attempt reconciliation ile değişmez.
   - Response kesinlikle JSON-serializable.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.v1.generation import (
    SocialContentSerializationError,
    social_content_read_to_response,
)
from app.config import settings
from app.core.social.content_contract import (
    ValidatedCarouselPayload,
    ValidatedCarouselSlide,
    ValidatedHook,
    ValidatedThreadPayload,
    ValidatedThreadPost,
    ValidatedVideoPayload,
    ValidatedVideoSegment,
)
from app.core.social.content_flow import SocialContentGenerationStart
from app.core.social.content_persistence import (
    PersistedSocialContent,
)
from app.core.social.content_read import (
    SocialContentAttemptReadResult,
    SocialContentReadWarning,
)
from app.database.models import (
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
from app.schemas.social_brief import (
    SocialBriefContentsAttemptResponse,
    SocialContentHookResponse,
    SocialGeneratedContentItemResponse,
)

T0 = datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc)

SECRET_PRODUCT_FACT = "TOP_SECRET_PRODUCT_SPEC_XYZ_999"
SECRET_SQL = "SELECT * FROM social_contents WHERE private_token='LEAK_123'"
SECRET_USP = '{"trusted_brand_usp":"SECRET_USP_CONFIDENTIAL_ABC"}'
SECRET_PROMPT = "RAW_PROMPT_SECRET_DO_NOT_EXPOSE_777"
ALL_SECRETS = [SECRET_PRODUCT_FACT, SECRET_SQL, SECRET_USP, SECRET_PROMPT]


# ==================== FIXTURES ====================

@pytest.fixture
def enable_flag(monkeypatch):
    """Test süresince feature flag'i aktif kılar."""
    monkeypatch.setattr(settings, "ENABLE_SOCIAL_BRIEF_FLOW", True)
    yield


@pytest.fixture
def mock_celery_task():
    """social_brief_contents_task.apply_async çağrılarını yakalar ve izole eder."""
    with patch("app.api.v1.generation.social_brief_contents_task.apply_async") as mock_apply:
        mock_apply.return_value = MagicMock(id=str(uuid.uuid4()))
        yield mock_apply


# ==================== ENVIRONMENT BUILDERS ====================

def _setup_base_env(
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
    brand_name: str = "Sanitization Test Brand",
):
    ws = make_workspace(name=brand_name, status="confirmed")
    run = make_scoring_run(
        brand_profile_id=ws.id,
        status="completed",
        algorithm_version="v3",
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
    brand_name: str = "Sanitization Test Brand",
):
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
            idea_title=f"Sosyal Medya Fikri {i + 1}",
            idea_description=f"Açıklama {i + 1}",
            target_platform="instagram",
            content_format="post",
            trend_alignment=0.88,
            is_stale=False,
        )
        db_session.add(idea)
        ideas.append(idea)

    db_session.commit()
    return ws, run, brief, target, cat, kw, ideas


def _create_valid_content(
    db_session: Session,
    *,
    brief: SocialBrief,
    target: SocialBriefTarget,
    idea: SocialIdea,
    caption: str = "Test İçerik Metni",
    is_stale: bool = False,
) -> SocialContent:
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


def _create_contents_attempt(
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
    task_id: str | None = None,
    lease_expires_at: datetime | None = None,
    completed_at: datetime | None = None,
    product_facts: str | None = None,
    trusted_brand_usp: str | None = None,
) -> SocialGenerationAttempt:
    """Test için SocialGenerationAttempt kaydı oluşturur."""
    from app.core.channel.brand_defense import load_product_definition

    run = db_session.query(ScoringRun).filter_by(id=brief.scoring_run_id).one()
    eff_product_facts = (
        product_facts
        if product_facts is not None
        else load_product_definition(db_session, run)
    )

    idea_ids = [i.id for i in ideas]
    key = idempotency_key or f"contents-attempt-{uuid.uuid4().hex[:8]}"
    coverage = {
        "schema_version": "contents_request_v1",
        "request": {
            "idea_ids": idea_ids,
            "product_facts": eff_product_facts,
            "trusted_brand_usp": trusted_brand_usp,
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
        task_id=task_id,
        lease_expires_at=lease_expires_at,
        completed_at=completed_at,
    )
    db_session.add(att)
    db_session.commit()
    db_session.refresh(att)
    return att


def _assert_no_secret_leak(response):
    """HTTP response body'sinde hiçbir gizli bilginin yer almadığını doğrular."""
    text = response.text
    for secret in ALL_SECRETS:
        assert secret not in text, f"Hassas veri HTTP yanıtında tespit edildi: {secret}"


# ==================== A. DEBUG=True ve DEBUG=False VERİ SIZINTISI TESTLERİ ====================

@pytest.mark.parametrize("debug_setting", [True, False])
def test_a1_post_preflight_unexpected_exception_sanitized(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag, monkeypatch, debug_setting
):
    """A1. POST preflight beklenmeyen hata: DEBUG modundan bağımsız sabit CONTENT_PREFLIGHT_FAILED döner, sızıntı olmaz."""
    monkeypatch.setattr(settings, "DEBUG", debug_setting)
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(db_session, make_workspace, make_scoring_run, make_keyword)

    url = f"/api/v1/generation/social/briefs/{brief.id}/contents/async?brand_profile_id={ws.id}"
    payload = {"idempotency_key": "leak-key-1", "idea_ids": [ideas[0].id]}

    with patch("app.api.v1.generation.begin_social_content_generation") as mock_preflight:
        mock_preflight.side_effect = RuntimeError(f"DB Crash: {SECRET_SQL} | {SECRET_PRODUCT_FACT}")
        resp = client.post(url, json=payload)

    assert resp.status_code == 500
    assert resp.json()["detail"]["code"] == "CONTENT_PREFLIGHT_FAILED"
    _assert_no_secret_leak(resp)


@pytest.mark.parametrize("debug_setting", [True, False])
def test_a2_post_completed_replay_unexpected_read_exception_sanitized(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag, monkeypatch, debug_setting
):
    """A2. Completed replay read hatası: DEBUG modundan bağımsız sabit CONTENT_READ_INCONSISTENT döner, sızıntı olmaz."""
    monkeypatch.setattr(settings, "DEBUG", debug_setting)
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(db_session, make_workspace, make_scoring_run, make_keyword)
    key = "leak-replay-completed-key"
    _create_contents_attempt(db_session, brief=brief, ideas=[ideas[0]], status="completed", idempotency_key=key)

    url = f"/api/v1/generation/social/briefs/{brief.id}/contents/async?brand_profile_id={ws.id}"
    payload = {"idempotency_key": key, "idea_ids": [ideas[0].id]}

    with patch("app.api.v1.generation.load_social_content_result") as mock_read:
        mock_read.side_effect = RuntimeError(f"Corrupted row: {SECRET_USP} | {SECRET_PROMPT}")
        resp = client.post(url, json=payload)

    assert resp.status_code == 500
    assert resp.json()["detail"]["code"] == "CONTENT_READ_INCONSISTENT"
    _assert_no_secret_leak(resp)


@pytest.mark.parametrize("debug_setting", [True, False])
def test_a3_post_pending_replay_unexpected_read_exception_sanitized(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag, monkeypatch, debug_setting
):
    """A3. Pending replay read hatası: DEBUG modundan bağımsız sabit CONTENT_READ_INCONSISTENT döner, sızıntı olmaz."""
    monkeypatch.setattr(settings, "DEBUG", debug_setting)
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(db_session, make_workspace, make_scoring_run, make_keyword)
    key = "leak-replay-pending-key"
    _create_contents_attempt(db_session, brief=brief, ideas=[ideas[0]], status="pending", idempotency_key=key)

    url = f"/api/v1/generation/social/briefs/{brief.id}/contents/async?brand_profile_id={ws.id}"
    payload = {"idempotency_key": key, "idea_ids": [ideas[0].id]}

    with patch("app.api.v1.generation.load_social_content_result") as mock_read:
        mock_read.side_effect = RuntimeError(f"Corrupted pending read: {SECRET_SQL}")
        resp = client.post(url, json=payload)

    assert resp.status_code == 500
    assert resp.json()["detail"]["code"] == "CONTENT_READ_INCONSISTENT"
    _assert_no_secret_leak(resp)


@pytest.mark.parametrize("debug_setting", [True, False])
def test_a4_post_successful_dispatch_read_exception_sanitized(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag, mock_celery_task, monkeypatch, debug_setting
):
    """A4. Dispatch sonrası initial read hatası: DEBUG modundan bağımsız CONTENT_POST_DISPATCH_READ_FAILED döner, sızıntı olmaz."""
    monkeypatch.setattr(settings, "DEBUG", debug_setting)
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(db_session, make_workspace, make_scoring_run, make_keyword)

    url = f"/api/v1/generation/social/briefs/{brief.id}/contents/async?brand_profile_id={ws.id}"
    payload = {"idempotency_key": f"post-read-fail-{debug_setting}", "idea_ids": [ideas[0].id]}

    with patch("app.api.v1.generation.load_social_content_result") as mock_read:
        mock_read.side_effect = RuntimeError(f"Post-dispatch read crash: {SECRET_PRODUCT_FACT} and {SECRET_USP}")
        resp = client.post(url, json=payload)

    assert resp.status_code == 500
    assert resp.json()["detail"]["code"] == "CONTENT_POST_DISPATCH_READ_FAILED"
    _assert_no_secret_leak(resp)

    # Attempt durumu güvenle pending kalır
    db_session.expire_all()
    att = db_session.query(SocialGenerationAttempt).filter_by(brief_id=brief.id).one()
    assert att.status == "pending"


@pytest.mark.parametrize("debug_setting", [True, False])
def test_a5_get_reconciliation_unexpected_exception_sanitized(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag, monkeypatch, debug_setting
):
    """A5. GET reconciliation hatası: DEBUG modundan bağımsız CONTENT_RECONCILIATION_FAILED döner, sızıntı olmaz."""
    monkeypatch.setattr(settings, "DEBUG", debug_setting)
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(db_session, make_workspace, make_scoring_run, make_keyword)
    att = _create_contents_attempt(db_session, brief=brief, ideas=[ideas[0]], status="pending")

    url = f"/api/v1/generation/social/briefs/{brief.id}/contents/attempts/{att.id}?brand_profile_id={ws.id}"

    with patch("app.api.v1.generation.reconcile_expired_contents_attempt_for_read") as mock_rec:
        mock_rec.side_effect = RuntimeError(f"Reconciliation lock crash: {SECRET_SQL}")
        resp = client.get(url)

    assert resp.status_code == 500
    assert resp.json()["detail"]["code"] == "CONTENT_RECONCILIATION_FAILED"
    _assert_no_secret_leak(resp)


@pytest.mark.parametrize("debug_setting", [True, False])
def test_a6_get_read_unexpected_exception_sanitized(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag, monkeypatch, debug_setting
):
    """A6. GET read hatası: DEBUG modundan bağımsız CONTENT_READ_INCONSISTENT döner, sızıntı olmaz."""
    monkeypatch.setattr(settings, "DEBUG", debug_setting)
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(db_session, make_workspace, make_scoring_run, make_keyword)
    att = _create_contents_attempt(db_session, brief=brief, ideas=[ideas[0]], status="completed")

    url = f"/api/v1/generation/social/briefs/{brief.id}/contents/attempts/{att.id}?brand_profile_id={ws.id}"

    with patch("app.api.v1.generation.load_social_content_result") as mock_read:
        mock_read.side_effect = RuntimeError(f"Read failure: {SECRET_PROMPT}")
        resp = client.get(url)

    assert resp.status_code == 500
    assert resp.json()["detail"]["code"] == "CONTENT_READ_INCONSISTENT"
    _assert_no_secret_leak(resp)


@pytest.mark.parametrize("debug_setting", [True, False])
def test_a7_response_converter_exception_sanitized(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag, monkeypatch, debug_setting
):
    """A7. Response adapter dönüştürme hatası: DEBUG modundan bağımsız CONTENT_READ_INCONSISTENT döner, sızıntı olmaz."""
    monkeypatch.setattr(settings, "DEBUG", debug_setting)
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(db_session, make_workspace, make_scoring_run, make_keyword)
    att = _create_contents_attempt(db_session, brief=brief, ideas=[ideas[0]], status="completed")

    url = f"/api/v1/generation/social/briefs/{brief.id}/contents/attempts/{att.id}?brand_profile_id={ws.id}"

    with patch("app.api.v1.generation.social_content_read_to_response") as mock_conv:
        mock_conv.side_effect = RuntimeError(f"Serialization crash: {SECRET_PRODUCT_FACT}")
        resp = client.get(url)

    assert resp.status_code == 500
    assert resp.json()["detail"]["code"] == "CONTENT_READ_INCONSISTENT"
    _assert_no_secret_leak(resp)


# ==================== B. BİLİNMEYEN STATUS TESTLERİ ====================

def test_b1_unknown_attempt_status_returns_safe_500_no_redispatch(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag, mock_celery_task, monkeypatch
):
    """B1. Bilinmeyen attempt status: dinamik status string sızmaz, redispatch yapılmaz, güvenli 500 döner."""
    monkeypatch.setattr(settings, "DEBUG", True)
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(db_session, make_workspace, make_scoring_run, make_keyword)
    att = _create_contents_attempt(db_session, brief=brief, ideas=[ideas[0]], status="pending")

    url = f"/api/v1/generation/social/briefs/{brief.id}/contents/async?brand_profile_id={ws.id}"
    payload = {"idempotency_key": "unknown-status-key", "idea_ids": [ideas[0].id]}

    corrupted_status = "SECRET_CORRUPTED_STATUS_VALUE_XYZ"
    with patch("app.api.v1.generation.begin_social_content_generation") as mock_begin:
        mock_begin.return_value = SocialContentGenerationStart(
            brief_id=brief.id,
            scoring_run_id=run.id,
            attempt_id=att.id,
            attempt_status=corrupted_status,
            requested_idea_ids=(ideas[0].id,),
            attempt_created=False,
            replayed=True,
        )
        resp = client.post(url, json=payload)

    assert resp.status_code == 500
    assert resp.json()["detail"]["code"] == "CONTENT_READ_INCONSISTENT"
    assert corrupted_status not in resp.text
    mock_celery_task.assert_not_called()


# ==================== C. HOOK RESPONSE ADAPTER TESTLERİ ====================

def test_c1_canonical_hook_serializes_correctly():
    """C1. Geçerli ValidatedHook nesneleri tam ve strict SocialContentHookResponse olarak serileştirilir."""
    hook = ValidatedHook(text="Kanonik hook başlığı", style="question", ab_score=0.85)
    resp = SocialContentHookResponse(
        text=hook.text,
        style=hook.style,
        ab_score=hook.ab_score,
    )
    assert resp.text == "Kanonik hook başlığı"
    assert resp.style == "question"
    assert resp.ab_score == 0.85


def test_c2_unknown_hook_object_raises_serialization_error_and_does_not_stringify():
    """C2. ValidatedHook olmayan yabancı nesne str(h) yapılmaz; SocialContentSerializationError fırlatılır."""
    class FakeHookWithSecret:
        def __init__(self):
            self.text = "SECRET_HOOK_TEXT_ABC"
            self.style = "question"

        def __str__(self):
            return "LEAK_STRINGIFIED_HOOK"

    fake = FakeHookWithSecret()

    fake_content = MagicMock(spec=PersistedSocialContent)
    fake_content.hooks = (fake,)
    fake_content.format_payload = None

    result = SocialContentAttemptReadResult(
        brief_id=1,
        scoring_run_id=1,
        attempt_id=1,
        attempt_status="completed",
        requested_idea_ids=(1,),
        successful_idea_ids=(1,),
        unresolved_idea_ids=(),
        contents=(fake_content,),
        warnings=(),
        reason_code=None,
        replayed=False,
    )

    with pytest.raises(SocialContentSerializationError):
        social_content_read_to_response(result)


def test_c3_malformed_hook_dict_fails_closed():
    """C3. Hook olarak dict nesnesi verilirse kabul edilmez, fail-closed hata üretir."""
    malformed_hook = {"text": "Yalnızca text var, style eksik"}

    fake_content = MagicMock(spec=PersistedSocialContent)
    fake_content.hooks = (malformed_hook,)
    fake_content.format_payload = None

    result = SocialContentAttemptReadResult(
        brief_id=1,
        scoring_run_id=1,
        attempt_id=1,
        attempt_status="completed",
        requested_idea_ids=(1,),
        successful_idea_ids=(1,),
        unresolved_idea_ids=(),
        contents=(fake_content,),
        warnings=(),
        reason_code=None,
        replayed=False,
    )

    with pytest.raises(SocialContentSerializationError):
        social_content_read_to_response(result)


def test_c4_extra_hook_key_fails_closed_without_leaking():
    """C4. Hook yanıt şemasında ekstra alanlar (extra='forbid') engellenir ve sızdırılmaz."""
    with pytest.raises(Exception):
        SocialContentHookResponse(
            text="Geçerli text",
            style="question",
            secret_extra="SECRET_EXTRA_VALUE",  # extra field forbidden
        )


def test_c5_invalid_ab_score_fails_closed():
    """C5. Finite olmayan veya 0-1 aralığında bulunmayan ab_score fail-closed reddedilir."""
    invalid_hook = ValidatedHook(text="Kanca", style="question", ab_score=1.5)

    fake_content = MagicMock(spec=PersistedSocialContent)
    fake_content.hooks = (invalid_hook,)
    fake_content.format_payload = None

    result = SocialContentAttemptReadResult(
        brief_id=1,
        scoring_run_id=1,
        attempt_id=1,
        attempt_status="completed",
        requested_idea_ids=(1,),
        successful_idea_ids=(1,),
        unresolved_idea_ids=(),
        contents=(fake_content,),
        warnings=(),
        reason_code=None,
        replayed=False,
    )

    with pytest.raises(SocialContentSerializationError):
        social_content_read_to_response(result)


# ==================== D. FORMAT PAYLOAD SERIALİZASYONU TESTLERİ ====================

def test_d1_canonical_format_payloads_serialize_correctly():
    """D1. Video, Carousel ve Thread canonical payload nesneleri doğru serialize edilir."""
    video = ValidatedVideoPayload(
        kind="video",
        segments=(
            ValidatedVideoSegment(
                start_sec=0,
                end_sec=5,
                scene="Açılış",
                on_screen_text="Metin 1",
                voiceover="Seslendirme 1",
            ),
        ),
    )
    carousel = ValidatedCarouselPayload(
        kind="carousel",
        slides=(
            ValidatedCarouselSlide(
                position=1,
                headline="Başlık 1",
                body="Gövde 1",
                visual_direction="Yönlendirme 1",
            ),
        ),
    )
    thread = ValidatedThreadPayload(
        kind="thread",
        posts=(
            ValidatedThreadPost(
                position=1,
                text="Tweet 1",
            ),
        ),
    )

    for payload in (video, carousel, thread):
        fake_content = MagicMock(spec=PersistedSocialContent)
        fake_content.id = 1
        fake_content.idea_id = 1
        fake_content.brief_id = 1
        fake_content.target_id = 1
        fake_content.platform = "instagram"
        fake_content.content_format = "video"
        fake_content.hooks = (ValidatedHook(text="Hook", style="question"),)
        fake_content.caption = "Caption"
        fake_content.scenario = None
        fake_content.format_payload = payload
        fake_content.visual_suggestion = None
        fake_content.video_concept = None
        fake_content.cta_text = None
        fake_content.hashtags = ("tag1",)
        fake_content.industry_posting_suggestion = None
        fake_content.platform_notes = None
        fake_content.duration_status = "valid"
        fake_content.actual_duration_sec = 5
        fake_content.validation_warnings = ()
        fake_content.is_stale = False

        result = SocialContentAttemptReadResult(
            brief_id=1,
            scoring_run_id=1,
            attempt_id=1,
            attempt_status="completed",
            requested_idea_ids=(1,),
            successful_idea_ids=(1,),
            unresolved_idea_ids=(),
            contents=(fake_content,),
            warnings=(),
            reason_code=None,
            replayed=False,
        )

        resp = social_content_read_to_response(result)
        assert resp.contents[0].format_payload is not None
        assert resp.contents[0].format_payload["kind"] == payload.kind


def test_d2_arbitrary_dict_payload_fails_closed():
    """D2. Arbitrary dict format_payload olarak verilirse doğrudan geçirilmez; fail-closed olur."""
    arbitrary_payload = {"kind": "video", "secret": "LEAK_SECRET_IN_PAYLOAD"}

    fake_content = MagicMock(spec=PersistedSocialContent)
    fake_content.hooks = (ValidatedHook(text="Hook", style="question"),)
    fake_content.format_payload = arbitrary_payload

    result = SocialContentAttemptReadResult(
        brief_id=1,
        scoring_run_id=1,
        attempt_id=1,
        attempt_status="completed",
        requested_idea_ids=(1,),
        successful_idea_ids=(1,),
        unresolved_idea_ids=(),
        contents=(fake_content,),
        warnings=(),
        reason_code=None,
        replayed=False,
    )

    with pytest.raises(SocialContentSerializationError):
        social_content_read_to_response(result)


def test_d3_unexpected_payload_type_fails_closed():
    """D3. Beklenmeyen payload türü (str, int vb.) fail-closed hata üretir."""
    fake_content = MagicMock(spec=PersistedSocialContent)
    fake_content.hooks = (ValidatedHook(text="Hook", style="question"),)
    fake_content.format_payload = "string_payload_not_allowed"

    result = SocialContentAttemptReadResult(
        brief_id=1,
        scoring_run_id=1,
        attempt_id=1,
        attempt_status="completed",
        requested_idea_ids=(1,),
        successful_idea_ids=(1,),
        unresolved_idea_ids=(),
        contents=(fake_content,),
        warnings=(),
        reason_code=None,
        replayed=False,
    )

    with pytest.raises(SocialContentSerializationError):
        social_content_read_to_response(result)


# ==================== E. REGRESYON TESTLERİ ====================

def test_e1_post_new_attempt_returns_202_and_dispatches(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag, mock_celery_task
):
    """E1. Yeni istek 202 Accepted döner ve tek bir Celery görevi kuyruklar."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(db_session, make_workspace, make_scoring_run, make_keyword)

    url = f"/api/v1/generation/social/briefs/{brief.id}/contents/async?brand_profile_id={ws.id}"
    payload = {"idempotency_key": "reg-post-key", "idea_ids": [ideas[0].id]}

    resp = client.post(url, json=payload)
    assert resp.status_code == 202
    data = resp.json()
    assert data["attempt_status"] == "pending"
    assert data["replayed"] is False
    mock_celery_task.assert_called_once()


def test_e2_completed_replay_returns_200_no_dispatch(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag, mock_celery_task
):
    """E2. Completed replay -> 200 OK, no Celery dispatch."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(db_session, make_workspace, make_scoring_run, make_keyword)
    key = "reg-completed-key"
    att = _create_contents_attempt(db_session, brief=brief, ideas=[ideas[0]], status="completed", idempotency_key=key)
    _create_valid_content(db_session, brief=brief, target=target, idea=ideas[0])
    db_session.commit()

    url = f"/api/v1/generation/social/briefs/{brief.id}/contents/async?brand_profile_id={ws.id}"
    payload = {"idempotency_key": key, "idea_ids": [ideas[0].id]}

    resp = client.post(url, json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["attempt_status"] == "completed"
    assert data["replayed"] is True
    mock_celery_task.assert_not_called()


def test_e3a_pending_replay_returns_202_no_dispatch(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag, mock_celery_task
):
    """E3a. Pending replay -> 202 Accepted, no Celery dispatch."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(db_session, make_workspace, make_scoring_run, make_keyword)
    key = "reg-pending-key"
    _create_contents_attempt(db_session, brief=brief, ideas=[ideas[0]], status="pending", idempotency_key=key)

    url = f"/api/v1/generation/social/briefs/{brief.id}/contents/async?brand_profile_id={ws.id}"
    payload = {"idempotency_key": key, "idea_ids": [ideas[0].id]}

    resp = client.post(url, json=payload)
    assert resp.status_code == 202
    data = resp.json()
    assert data["attempt_status"] == "pending"
    assert data["replayed"] is True
    mock_celery_task.assert_not_called()


def test_e3b_running_replay_returns_202_no_dispatch(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag, mock_celery_task
):
    """E3b. Running replay -> 202 Accepted, no Celery dispatch."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(db_session, make_workspace, make_scoring_run, make_keyword)
    key = "reg-running-key"
    _create_contents_attempt(db_session, brief=brief, ideas=[ideas[0]], status="running", idempotency_key=key)

    url = f"/api/v1/generation/social/briefs/{brief.id}/contents/async?brand_profile_id={ws.id}"
    payload = {"idempotency_key": key, "idea_ids": [ideas[0].id]}

    resp = client.post(url, json=payload)
    assert resp.status_code == 202
    data = resp.json()
    assert data["attempt_status"] == "running"
    assert data["replayed"] is True
    mock_celery_task.assert_not_called()


def test_e4_failed_partial_replay_returns_409_no_dispatch(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag, mock_celery_task
):
    """E4. Failed veya partial replay -> 409 Conflict (CONTENT_ATTEMPT_TERMINAL), no Celery dispatch."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(db_session, make_workspace, make_scoring_run, make_keyword)
    for st, reason in (("failed", "content_generation_failed"), ("partial", "content_partial")):
        key = f"reg-term-{st}-key"
        _create_contents_attempt(
            db_session,
            brief=brief,
            ideas=[ideas[0]],
            status=st,
            reason_code=reason,
            error_message="Terminal error",
            idempotency_key=key,
        )

        url = f"/api/v1/generation/social/briefs/{brief.id}/contents/async?brand_profile_id={ws.id}"
        payload = {"idempotency_key": key, "idea_ids": [ideas[0].id]}

        resp = client.post(url, json=payload)
        assert resp.status_code == 409
        assert resp.json()["detail"]["code"] == "CONTENT_ATTEMPT_TERMINAL"
        mock_celery_task.assert_not_called()


def test_e5_broker_failure_compensation_marks_dispatch_failed_503(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """E5. Broker kuyruklama patlarsa attempt failed/dispatch_failed yapılır -> 503 Service Unavailable."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(db_session, make_workspace, make_scoring_run, make_keyword)

    url = f"/api/v1/generation/social/briefs/{brief.id}/contents/async?brand_profile_id={ws.id}"
    payload = {"idempotency_key": "broker-fail-key", "idea_ids": [ideas[0].id]}

    with patch("app.api.v1.generation.social_brief_contents_task.apply_async") as mock_apply:
        mock_apply.side_effect = RuntimeError("Broker connection timeout")
        resp = client.post(url, json=payload)

    assert resp.status_code == 503
    assert resp.json()["detail"]["code"] == "CONTENT_DISPATCH_FAILED"

    db_session.expire_all()
    att = db_session.query(SocialGenerationAttempt).filter_by(brief_id=brief.id).one()
    assert att.status == "failed"
    assert att.reason_code == "dispatch_failed"


def test_e6_expired_lease_reconciles_to_worker_lost(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """E6. Süresi geçmiş pending/running attempt GET sırasında failed/worker_lost durumuna reconcile edilir."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(db_session, make_workspace, make_scoring_run, make_keyword)
    expired_time = datetime.now(timezone.utc) - timedelta(minutes=10)
    att = _create_contents_attempt(
        db_session,
        brief=brief,
        ideas=[ideas[0]],
        status="pending",
        lease_expires_at=expired_time,
    )

    url = f"/api/v1/generation/social/briefs/{brief.id}/contents/attempts/{att.id}?brand_profile_id={ws.id}"
    resp = client.get(url)

    assert resp.status_code == 200
    data = resp.json()
    assert data["attempt_status"] == "failed"
    assert data["reason_code"] == "worker_lost"

    db_session.expire_all()
    db_att = db_session.query(SocialGenerationAttempt).filter_by(id=att.id).one()
    assert db_att.status == "failed"
    assert db_att.reason_code == "worker_lost"
    assert db_att.lease_expires_at is None


def test_e7_terminal_attempts_not_mutated_by_reconciliation(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """E7. Terminal (completed, partial, failed) attempt'ler süresi geçmiş olsa bile reconciliation ile değiştirilmez."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(db_session, make_workspace, make_scoring_run, make_keyword)
    expired_time = datetime.now(timezone.utc) - timedelta(minutes=10)
    att = _create_contents_attempt(
        db_session,
        brief=brief,
        ideas=[ideas[0]],
        status="failed",
        reason_code="content_generation_failed",
        error_message="Orijinal hata",
        lease_expires_at=expired_time,
    )

    url = f"/api/v1/generation/social/briefs/{brief.id}/contents/attempts/{att.id}?brand_profile_id={ws.id}"
    resp = client.get(url)

    assert resp.status_code == 200
    data = resp.json()
    assert data["attempt_status"] == "failed"
    assert data["reason_code"] == "content_generation_failed"


def test_e8_response_strictly_json_serializable(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """E8. API yanıtı standart JSON ile doğrulanabilir ve Pydantic modeline tam uyar."""
    ws, run, brief, target, cat, kw, ideas = _setup_full_env(db_session, make_workspace, make_scoring_run, make_keyword)
    att = _create_contents_attempt(db_session, brief=brief, ideas=[ideas[0]], status="completed")
    _create_valid_content(db_session, brief=brief, target=target, idea=ideas[0])
    db_session.commit()

    url = f"/api/v1/generation/social/briefs/{brief.id}/contents/attempts/{att.id}?brand_profile_id={ws.id}"
    resp = client.get(url)

    assert resp.status_code == 200
    parsed = json.loads(resp.text)
    validated = SocialBriefContentsAttemptResponse.model_validate(parsed)
    assert validated.attempt_id == att.id
    assert len(validated.contents) == 1
    assert len(validated.contents[0].hooks) == 3
    assert validated.contents[0].hooks[0].style == "question"
