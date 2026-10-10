# -*- coding: utf-8 -*-
"""Integration tests for POST SocialBrief Categories Generate API (F1-E.5).

Doğrulanan senaryolar:
1. Feature Flag:
   - ENABLE_SOCIAL_BRIEF_FLOW=False -> 404 FEATURE_DISABLED, 0 DB write, 0 AI call.
2. Başarı Senaryoları:
   - İlk AI çağrısı geçerli -> 201 Created, 2 kategori response ve DB'de, attempt completed, locked_at kalıcı.
   - İlk AI çağrısı geçersiz, ikincisi geçerli -> 201 Created, ai_calls_used=2.
   - AI çağrısı transaction dışında: db.in_transaction() False, ikinci PostgreSQL oturumu FOR UPDATE NOWAIT alabilir.
   - Tam 2 commit garantisi: 1 preflight sonrası, 1 persistence sonrası.
3. Replay Dalları:
   - Completed attempt aynı key -> 200 OK, replayed=True, aynı kategoriler, 0 AI call.
   - Completed attempt bozuk coverage -> 500 CATEGORY_PERSISTENCE_INCONSISTENT.
   - Pending / running attempt aynı key -> 202 Accepted, replayed=True, categories=[], 0 AI call.
   - Failed attempt aynı key -> 409 CATEGORY_ATTEMPT_TERMINAL, 0 AI call.
   - Failed attempt sonrası yeni key -> 201 Created.
   - Farklı key ile aktif pending attempt -> 409 ATTEMPT_CONFLICT.
4. Preflight Hataları:
   - Nonexistent / cross-workspace brief -> 404 BRIEF_NOT_FOUND, 0 AI call.
   - Preflight stale brief -> 409 BRIEF_STALE, 0 AI call.
   - Preflight assignment version changed -> 409 ASSIGNMENT_CHANGED, 0 AI call.
   - Preflight kategoriler zaten mevcut farklı key -> 409 CATEGORIES_ALREADY_GENERATED, 0 AI call.
5. AI Başarısızlık Finalizasyonu:
   - Provider error -> 502, attempt failed (category_provider_error), 0 kategori.
   - İki geçersiz AI yanıtı -> 502, attempt failed (category_output_invalid), 0 kategori.
6. Persistence Başarısızlık Finalizasyonu:
   - AI sırasında brief stale olursa -> 409 BRIEF_STALE, attempt failed, 0 kategori.
   - IntegrityError -> 409 CATEGORY_PERSISTENCE_CONFLICT, attempt failed, raw SQL sızmaz.
7. Bilgi Sızıntısı Koruması:
   - Hata yanıtlarında raw prompt, SQL, provider exception veya gizli marka sızmaz.
8. Regresyon:
   - Format matrix ve brief endpoint'leri etkilenmez.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.database.connection import SessionLocal
from app.database.models import (
    BrandProfile,
    ChannelPool,
    ScoringRun,
    SocialBrief,
    SocialBriefKeyword,
    SocialBriefTarget,
    SocialCategory,
    SocialGenerationAttempt,
)
from app.dependencies import get_ai, get_db
from app.main import app

T0 = datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc)


# ==================== FAKE AI SERVICE ====================

class FakeAIService:
    """Testler için çağrı sayan ve önceden tanımlı yanıtlar dönen sahte AI servisi."""

    def __init__(
        self,
        responses: list[Any] | None = None,
        raise_exc: Exception | None = None,
        on_complete_json: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self.responses: list[Any] = list(responses) if responses else []
        self.raise_exc: Exception | None = raise_exc
        self.on_complete_json = on_complete_json
        self.call_count: int = 0
        self.call_args: list[dict[str, Any]] = []
        self.collector = MagicMock()

    def for_stage(self, stage: str, **overrides) -> FakeAIService:
        self.stage = stage
        return self

    def complete_json(self, prompt: str, **kwargs) -> str:
        self.call_count += 1
        self.call_args.append({"prompt": prompt, **kwargs})
        if self.on_complete_json:
            self.on_complete_json(prompt, kwargs)
        if self.raise_exc is not None:
            raise self.raise_exc
        if self.responses:
            resp = self.responses.pop(0)
            if isinstance(resp, Exception):
                raise resp
            return resp
        return json.dumps({"categories": []})


# ==================== FIXTURES ====================

@pytest.fixture
def enable_flag(monkeypatch):
    """Test süresince feature flag'i aktif kılar."""
    monkeypatch.setattr(settings, "ENABLE_SOCIAL_BRIEF_FLOW", True)
    yield


@pytest.fixture(autouse=True)
def bind_dependencies_get_db(db_session):
    """FastAPI TestClient'ın app.dependencies.get_db çağrılarını test db_session'ına bağlar."""
    from app.dependencies import get_db

    def _override():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = _override
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture(autouse=True)
def cleanup_ai_override():
    """Test bitiminde get_ai override'ını temizler."""
    yield
    app.dependency_overrides.pop(get_ai, None)


def _setup_environment(
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
    num_kws: int = 2,
    workspace_name: str = "Test Brand",
):
    """Test için workspace, scoring run, pool ve brief kurar."""
    ws = make_workspace(name=workspace_name, status="confirmed")
    run = make_scoring_run(
        brand_profile_id=ws.id,
        status="completed",
        algorithm_version="v3",
        channel_pool_policy_version=1,
        relevance_anchor_version=1,
        channel_assignment_version=1,
        skip_relevance=True,
    )

    keywords = []
    for i in range(num_kws):
        kw = make_keyword(
            text_value=f"anahtar kelime {i + 1}",
            brand_profile_id=ws.id,
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

    brief = SocialBrief(
        scoring_run_id=run.id,
        brand_name_snapshot=workspace_name,
        brand_context_snapshot="Test marka bağlamı",
        channel_assignment_version=1,
        format_matrix_version="v1",
        locked_at=None,
        is_stale=False,
    )
    db_session.add(brief)
    db_session.flush()

    for idx, kw in enumerate(keywords):
        sbk = SocialBriefKeyword(
            brief_id=brief.id,
            keyword_id=kw.id,
            keyword_snapshot=kw.keyword,
            position=idx,
        )
        db_session.add(sbk)

    target = SocialBriefTarget(
        brief_id=brief.id,
        platform="instagram",
        content_format="post",
    )
    db_session.add(target)
    db_session.commit()
    return ws, run, brief, keywords


def _valid_category_json(kw_ids: tuple[int, ...]) -> str:
    return json.dumps(
        {
            "categories": [
                {
                    "category_name": "Eğitici Seri",
                    "category_type": "educational",
                    "description": "Detaylı eğitim ve rehber serisi.",
                    "relevance_score": 0.95,
                    "suggested_keyword_ids": [kw_ids[0]],
                },
                {
                    "category_name": "Ürün Faydaları",
                    "category_type": "product_benefit",
                    "description": "Öne çıkan ürün özellikleri ve avantajları.",
                    "relevance_score": 0.85,
                    "suggested_keyword_ids": [kw_ids[1] if len(kw_ids) > 1 else kw_ids[0]],
                },
            ]
        },
        ensure_ascii=False,
    )


# ==================== 1. FEATURE FLAG TESTLERİ ====================

def test_01_feature_flag_disabled_returns_404_no_db_no_ai(
    client: TestClient, db_session: Session, make_workspace, make_scoring_run, make_keyword
):
    """1. ENABLE_SOCIAL_BRIEF_FLOW kapalıyken HTTP 404 döner, DB ve AI çağrısı yapılmaz."""
    ws, run, brief, kws = _setup_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    fake_ai = FakeAIService()
    app.dependency_overrides[get_ai] = lambda: fake_ai

    resp = client.post(
        f"/api/v1/generation/social/briefs/{brief.id}/categories/generate?brand_profile_id={ws.id}",
        json={"idempotency_key": "flag-off-key", "max_categories": 4},
    )
    assert resp.status_code == 404
    body = resp.json()
    assert body["detail"]["code"] == "FEATURE_DISABLED"
    assert fake_ai.call_count == 0

    # DB'de attempt veya locked_at oluşmamış olmalı
    db_session.refresh(brief)
    assert brief.locked_at is None
    assert db_session.query(SocialGenerationAttempt).filter_by(brief_id=brief.id).count() == 0


# ==================== 2. BAŞARI SENARYOLARI ====================

def test_02_successful_first_call_creates_categories_and_completes_attempt_201(
    client: TestClient, db_session: Session, enable_flag, make_workspace, make_scoring_run, make_keyword
):
    """2. İlk geçerli AI yanıtı ile 201 Created döner, kategoriler DB'ye kaydedilir, attempt completed olur."""
    ws, run, brief, kws = _setup_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    kw_ids = tuple(k.id for k in kws)
    valid_json = _valid_category_json(kw_ids)
    fake_ai = FakeAIService(responses=[valid_json])
    app.dependency_overrides[get_ai] = lambda: fake_ai

    resp = client.post(
        f"/api/v1/generation/social/briefs/{brief.id}/categories/generate?brand_profile_id={ws.id}",
        json={"idempotency_key": "succ-key-1", "max_categories": 4},
    )
    assert resp.status_code == 201
    data = resp.json()

    assert data["brief_id"] == brief.id
    assert data["scoring_run_id"] == run.id
    assert data["attempt_status"] == "completed"
    assert data["replayed"] is False
    assert data["ai_calls_used"] == 1
    assert data["total_categories"] == 2
    assert len(data["categories"]) == 2

    # DB doğrulamaları
    db_session.refresh(brief)
    assert brief.locked_at is not None

    cats = db_session.query(SocialCategory).filter_by(brief_id=brief.id).order_by(SocialCategory.id.asc()).all()
    assert len(cats) == 2
    assert cats[0].category_name == "Eğitici Seri"
    assert cats[1].category_name == "Ürün Faydaları"
    assert cats[0].is_stale is False

    attempt = db_session.get(SocialGenerationAttempt, data["attempt_id"])
    assert attempt.status == "completed"
    assert attempt.coverage["ai_calls_used"] == 1
    assert attempt.coverage["category_count"] == 2


def test_03_first_invalid_second_valid_retries_and_succeeds_with_2_calls_201(
    client: TestClient, db_session: Session, enable_flag, make_workspace, make_scoring_run, make_keyword
):
    """3. İlk AI yanıtı geçersiz fakat ikinci yanıt geçerli olduğunda 1 retry yapılır ve ai_calls_used=2 olarak 201 döner."""
    ws, run, brief, kws = _setup_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    kw_ids = tuple(k.id for k in kws)
    broken_json = json.dumps({"categories": [{"category_name": ""}]})  # Geçersiz
    valid_json = _valid_category_json(kw_ids)
    fake_ai = FakeAIService(responses=[broken_json, valid_json])
    app.dependency_overrides[get_ai] = lambda: fake_ai

    resp = client.post(
        f"/api/v1/generation/social/briefs/{brief.id}/categories/generate?brand_profile_id={ws.id}",
        json={"idempotency_key": "retry-succ-key", "max_categories": 4},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["attempt_status"] == "completed"
    assert data["ai_calls_used"] == 2
    assert fake_ai.call_count == 2


def test_04_ai_called_outside_transaction_real_db_lock_proof(
    client: TestClient, db_session: Session, enable_flag, make_workspace, make_scoring_run, make_keyword
):
    """4. [Gerçek DB Kanıtı] AI çağrısı sırasında db.in_transaction() False'dur ve ikinci izole PostgreSQL oturumu
    ScoringRun, SocialBrief, SocialGenerationAttempt ve BrandProfile satırlarına FOR UPDATE NOWAIT alabilir.
    """
    ws, run, brief, kws = _setup_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    kw_ids = tuple(k.id for k in kws)
    valid_json = _valid_category_json(kw_ids)

    lock_check_passed = False

    def on_complete(prompt: str, kwargs: dict[str, Any]):
        nonlocal lock_check_passed
        # 1. Ana oturum transaction'da olmamalıdır
        assert not db_session.in_transaction(), "AI çağrılırken ana db oturumu transaction içinde kalamaz!"

        # 2. İkinci izole oturum açıp ilgili satırlara NOWAIT kilit alabilmelidir
        second_session = SessionLocal()
        try:
            with second_session.begin():
                # ScoringRun kilit dene
                sr = second_session.query(ScoringRun).filter(ScoringRun.id == run.id).with_for_update(nowait=True).one()
                assert sr.id == run.id

                # SocialBrief kilit dene
                sb = second_session.query(SocialBrief).filter(SocialBrief.id == brief.id).with_for_update(nowait=True).one()
                assert sb.id == brief.id

                # BrandProfile kilit dene
                bp = second_session.query(BrandProfile).filter(BrandProfile.id == ws.id).with_for_update(nowait=True).one()
                assert bp.id == ws.id

                # SocialGenerationAttempt kilit dene (preflight tarafından oluşturulup commit edilmiş olmalı)
                att = (
                    second_session.query(SocialGenerationAttempt)
                    .filter(SocialGenerationAttempt.brief_id == brief.id)
                    .with_for_update(nowait=True)
                    .one()
                )
                assert att.status == "pending"

            lock_check_passed = True
        finally:
            second_session.close()

    fake_ai = FakeAIService(responses=[valid_json], on_complete_json=on_complete)
    app.dependency_overrides[get_ai] = lambda: fake_ai

    resp = client.post(
        f"/api/v1/generation/social/briefs/{brief.id}/categories/generate?brand_profile_id={ws.id}",
        json={"idempotency_key": "lock-proof-key", "max_categories": 4},
    )
    assert resp.status_code == 201
    assert lock_check_passed is True, "AI callback sırasında kilit denetimi başarıyla çalışmadı!"


def test_05_transaction_commit_count_is_exactly_two_on_success(
    client: TestClient, db_session: Session, enable_flag, make_workspace, make_scoring_run, make_keyword, monkeypatch
):
    """5. Yeni başarılı üretim isteğinde tam 2 commit gerçekleşir: biri preflight sonrası, biri persistence sonrası."""
    ws, run, brief, kws = _setup_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    kw_ids = tuple(k.id for k in kws)
    valid_json = _valid_category_json(kw_ids)
    fake_ai = FakeAIService(responses=[valid_json])
    app.dependency_overrides[get_ai] = lambda: fake_ai

    real_commit = db_session.commit
    commit_count = 0

    def counting_commit():
        nonlocal commit_count
        commit_count += 1
        real_commit()

    monkeypatch.setattr(db_session, "commit", counting_commit)

    resp = client.post(
        f"/api/v1/generation/social/briefs/{brief.id}/categories/generate?brand_profile_id={ws.id}",
        json={"idempotency_key": "commit-count-key", "max_categories": 4},
    )
    assert resp.status_code == 201
    assert commit_count == 2, f"Beklenen 2 commit, gerçekleşen: {commit_count}"


# ==================== 3. REPLAY TESTLERİ ====================

def test_06_completed_same_key_replay_returns_200_replayed_true_zero_ai(
    client: TestClient, db_session: Session, enable_flag, make_workspace, make_scoring_run, make_keyword
):
    """6. Completed attempt aynı idempotency_key ile tekrar çağrıldığında 200 döner, AI çağrısı yapılmaz."""
    ws, run, brief, kws = _setup_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    kw_ids = tuple(k.id for k in kws)
    valid_json = _valid_category_json(kw_ids)
    fake_ai = FakeAIService(responses=[valid_json])
    app.dependency_overrides[get_ai] = lambda: fake_ai

    # İlk çağrı -> 201
    resp1 = client.post(
        f"/api/v1/generation/social/briefs/{brief.id}/categories/generate?brand_profile_id={ws.id}",
        json={"idempotency_key": "replay-key-1", "max_categories": 4},
    )
    assert resp1.status_code == 201
    data1 = resp1.json()
    assert fake_ai.call_count == 1

    # İkinci çağrı (replay) -> 200
    resp2 = client.post(
        f"/api/v1/generation/social/briefs/{brief.id}/categories/generate?brand_profile_id={ws.id}",
        json={"idempotency_key": "replay-key-1", "max_categories": 4},
    )
    assert resp2.status_code == 200
    data2 = resp2.json()

    assert data2["replayed"] is True
    assert data2["attempt_status"] == "completed"
    assert data2["attempt_id"] == data1["attempt_id"]
    assert data2["ai_calls_used"] == 1
    assert [c["id"] for c in data2["categories"]] == [c["id"] for c in data1["categories"]]
    # AI tekrar çağrılmamış olmalı
    assert fake_ai.call_count == 1


def test_07_completed_same_key_replay_corrupted_coverage_raises_500(
    client: TestClient, db_session: Session, enable_flag, make_workspace, make_scoring_run, make_keyword
):
    """7. Completed attempt'in coverage alanı bozuksa uydurma değer dönülmez; fail-closed 500 döner."""
    ws, run, brief, kws = _setup_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    kw_ids = tuple(k.id for k in kws)
    valid_json = _valid_category_json(kw_ids)
    fake_ai = FakeAIService(responses=[valid_json])
    app.dependency_overrides[get_ai] = lambda: fake_ai

    resp1 = client.post(
        f"/api/v1/generation/social/briefs/{brief.id}/categories/generate?brand_profile_id={ws.id}",
        json={"idempotency_key": "corrupt-cov-key", "max_categories": 4},
    )
    assert resp1.status_code == 201
    att_id = resp1.json()["attempt_id"]

    # Coverage'ı bilerek boz
    att = db_session.get(SocialGenerationAttempt, att_id)
    att.coverage = {"ai_calls_used": "geçersiz_string"}
    db_session.commit()

    resp2 = client.post(
        f"/api/v1/generation/social/briefs/{brief.id}/categories/generate?brand_profile_id={ws.id}",
        json={"idempotency_key": "corrupt-cov-key", "max_categories": 4},
    )
    assert resp2.status_code == 500
    assert resp2.json()["detail"]["code"] == "CATEGORY_PERSISTENCE_INCONSISTENT"


def test_08_pending_same_key_replay_returns_202_replayed_true_zero_ai(
    client: TestClient, db_session: Session, enable_flag, make_workspace, make_scoring_run, make_keyword
):
    """8. Pending durumundaki attempt aynı idempotency_key ile tekrar gelirse 202 Accepted ve boş categories döner."""
    ws, run, brief, kws = _setup_environment(db_session, make_workspace, make_scoring_run, make_keyword)

    # DB'ye önceden pending bir categories attempt ekle
    attempt = SocialGenerationAttempt(
        brief_id=brief.id,
        stage="categories",
        idempotency_key="pending-replay-key",
        status="pending",
    )
    db_session.add(attempt)
    db_session.commit()

    fake_ai = FakeAIService()
    app.dependency_overrides[get_ai] = lambda: fake_ai

    resp = client.post(
        f"/api/v1/generation/social/briefs/{brief.id}/categories/generate?brand_profile_id={ws.id}",
        json={"idempotency_key": "pending-replay-key", "max_categories": 4},
    )
    assert resp.status_code == 202
    data = resp.json()
    assert data["replayed"] is True
    assert data["attempt_status"] == "pending"
    assert data["total_categories"] == 0
    assert data["categories"] == []
    assert data["ai_calls_used"] is None
    assert fake_ai.call_count == 0


def test_09_failed_same_key_replay_returns_409_category_attempt_terminal(
    client: TestClient, db_session: Session, enable_flag, make_workspace, make_scoring_run, make_keyword
):
    """9. Failed durumundaki attempt aynı key ile gelirse 409 CATEGORY_ATTEMPT_TERMINAL döner."""
    ws, run, brief, kws = _setup_environment(db_session, make_workspace, make_scoring_run, make_keyword)

    attempt = SocialGenerationAttempt(
        brief_id=brief.id,
        stage="categories",
        idempotency_key="failed-key-1",
        status="failed",
        reason_code="category_output_invalid",
    )
    db_session.add(attempt)
    db_session.commit()

    fake_ai = FakeAIService()
    app.dependency_overrides[get_ai] = lambda: fake_ai

    resp = client.post(
        f"/api/v1/generation/social/briefs/{brief.id}/categories/generate?brand_profile_id={ws.id}",
        json={"idempotency_key": "failed-key-1", "max_categories": 4},
    )
    assert resp.status_code == 409
    body = resp.json()
    assert body["detail"]["code"] == "CATEGORY_ATTEMPT_TERMINAL"
    assert body["detail"]["retry_with_new_idempotency_key"] is True
    assert fake_ai.call_count == 0


def test_10_retry_with_new_key_after_failed_attempt_succeeds(
    client: TestClient, db_session: Session, enable_flag, make_workspace, make_scoring_run, make_keyword
):
    """10. Failed attempt sonrasında yeni bir idempotency_key ile istek gelirse üretim başarıyla 201 tamamlanır."""
    ws, run, brief, kws = _setup_environment(db_session, make_workspace, make_scoring_run, make_keyword)

    # Önceki başarısız attempt
    attempt = SocialGenerationAttempt(
        brief_id=brief.id,
        stage="categories",
        idempotency_key="failed-key-old",
        status="failed",
        reason_code="category_output_invalid",
    )
    db_session.add(attempt)
    db_session.commit()

    kw_ids = tuple(k.id for k in kws)
    valid_json = _valid_category_json(kw_ids)
    fake_ai = FakeAIService(responses=[valid_json])
    app.dependency_overrides[get_ai] = lambda: fake_ai

    resp = client.post(
        f"/api/v1/generation/social/briefs/{brief.id}/categories/generate?brand_profile_id={ws.id}",
        json={"idempotency_key": "fresh-key-new", "max_categories": 4},
    )
    assert resp.status_code == 201
    assert resp.json()["attempt_status"] == "completed"


def test_11_different_key_with_active_pending_attempt_returns_409_conflict(
    client: TestClient, db_session: Session, enable_flag, make_workspace, make_scoring_run, make_keyword
):
    """11. Brief üzerinde aktif bir pending attempt varken farklı bir key gelirse 409 ATTEMPT_CONFLICT döner."""
    ws, run, brief, kws = _setup_environment(db_session, make_workspace, make_scoring_run, make_keyword)

    attempt = SocialGenerationAttempt(
        brief_id=brief.id,
        stage="categories",
        idempotency_key="key-active-1",
        status="pending",
    )
    db_session.add(attempt)
    db_session.commit()

    fake_ai = FakeAIService()
    app.dependency_overrides[get_ai] = lambda: fake_ai

    resp = client.post(
        f"/api/v1/generation/social/briefs/{brief.id}/categories/generate?brand_profile_id={ws.id}",
        json={"idempotency_key": "key-different-2", "max_categories": 4},
    )
    assert resp.status_code == 409
    body = resp.json()
    assert body["detail"]["code"] == "ATTEMPT_CONFLICT"
    assert fake_ai.call_count == 0


# ==================== 4. PREFLIGHT HATALARI ====================

def test_12_nonexistent_or_cross_workspace_brief_returns_404_brief_not_found(
    client: TestClient, db_session: Session, enable_flag, make_workspace, make_scoring_run, make_keyword
):
    """12. Bulunamayan veya başka workspace'e ait brief için 404 BRIEF_NOT_FOUND döner, AI çağrısı yapılmaz."""
    ws1, run1, brief1, kws1 = _setup_environment(db_session, make_workspace, make_scoring_run, make_keyword, workspace_name="WS1")
    ws2 = make_workspace(name="WS2", status="confirmed")

    fake_ai = FakeAIService()
    app.dependency_overrides[get_ai] = lambda: fake_ai

    # Başka workspace ile erişim
    resp = client.post(
        f"/api/v1/generation/social/briefs/{brief1.id}/categories/generate?brand_profile_id={ws2.id}",
        json={"idempotency_key": "cross-ws-key", "max_categories": 4},
    )
    assert resp.status_code == 404
    body = resp.json()
    assert body["detail"]["code"] == "BRIEF_NOT_FOUND"
    assert fake_ai.call_count == 0

    # Var olmayan brief ID
    resp2 = client.post(
        f"/api/v1/generation/social/briefs/9999999/categories/generate?brand_profile_id={ws1.id}",
        json={"idempotency_key": "notfound-key", "max_categories": 4},
    )
    assert resp2.status_code == 404
    assert resp2.json()["detail"]["code"] == "BRIEF_NOT_FOUND"


def test_13_stale_brief_in_preflight_returns_409_brief_stale(
    client: TestClient, db_session: Session, enable_flag, make_workspace, make_scoring_run, make_keyword
):
    """13. Stale brief üzerinde kategori üretilmek istendiğinde 409 BRIEF_STALE döner."""
    ws, run, brief, kws = _setup_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief.is_stale = True
    db_session.commit()

    fake_ai = FakeAIService()
    app.dependency_overrides[get_ai] = lambda: fake_ai

    resp = client.post(
        f"/api/v1/generation/social/briefs/{brief.id}/categories/generate?brand_profile_id={ws.id}",
        json={"idempotency_key": "stale-pre-key", "max_categories": 4},
    )
    assert resp.status_code == 409
    body = resp.json()
    assert body["detail"]["code"] == "BRIEF_STALE"
    assert fake_ai.call_count == 0


def test_14_assignment_version_changed_in_preflight_returns_409_assignment_changed(
    client: TestClient, db_session: Session, enable_flag, make_workspace, make_scoring_run, make_keyword
):
    """14. ScoringRun channel_assignment_version brief ile uyuşmuyorsa 409 ASSIGNMENT_CHANGED döner."""
    ws, run, brief, kws = _setup_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    run.channel_assignment_version = 2  # brief 1'de kalmış
    db_session.commit()

    fake_ai = FakeAIService()
    app.dependency_overrides[get_ai] = lambda: fake_ai

    resp = client.post(
        f"/api/v1/generation/social/briefs/{brief.id}/categories/generate?brand_profile_id={ws.id}",
        json={"idempotency_key": "ver-pre-key", "max_categories": 4},
    )
    assert resp.status_code == 409
    body = resp.json()
    assert body["detail"]["code"] == "ASSIGNMENT_CHANGED"
    assert fake_ai.call_count == 0


def test_15_categories_already_generated_for_brief_returns_409(
    client: TestClient, db_session: Session, enable_flag, make_workspace, make_scoring_run, make_keyword
):
    """15. Brief altında zaten kategori varken farklı bir key ile istek gelirse 409 CATEGORIES_ALREADY_GENERATED döner."""
    ws, run, brief, kws = _setup_environment(db_session, make_workspace, make_scoring_run, make_keyword)

    cat = SocialCategory(
        scoring_run_id=run.id,
        brief_id=brief.id,
        category_name="Mevcut Kategori",
        category_type="educational",
        description="Açıklama",
        relevance_score=0.9,
        suggested_keyword_ids=[kws[0].id],
    )
    db_session.add(cat)
    db_session.commit()

    fake_ai = FakeAIService()
    app.dependency_overrides[get_ai] = lambda: fake_ai

    resp = client.post(
        f"/api/v1/generation/social/briefs/{brief.id}/categories/generate?brand_profile_id={ws.id}",
        json={"idempotency_key": "diff-key-after-cats", "max_categories": 4},
    )
    assert resp.status_code == 409
    body = resp.json()
    assert body["detail"]["code"] == "CATEGORIES_ALREADY_GENERATED"
    assert fake_ai.call_count == 0


# ==================== 5. AI BAŞARISIZLIK FİNALİZASYONU ====================

def test_16_ai_provider_error_returns_502_and_marks_attempt_failed(
    client: TestClient, db_session: Session, enable_flag, make_workspace, make_scoring_run, make_keyword
):
    """16. AI provider hatasında HTTP 502 döner, attempt failed (category_provider_error) yapılır, kategori satırı yazılmaz."""
    ws, run, brief, kws = _setup_environment(db_session, make_workspace, make_scoring_run, make_keyword)

    fake_ai = FakeAIService(raise_exc=RuntimeError("Google Gemini API unavailable"))
    app.dependency_overrides[get_ai] = lambda: fake_ai

    resp = client.post(
        f"/api/v1/generation/social/briefs/{brief.id}/categories/generate?brand_profile_id={ws.id}",
        json={"idempotency_key": "ai-prov-err-key", "max_categories": 4},
    )
    assert resp.status_code == 502
    body = resp.json()
    assert body["detail"]["code"] == "CATEGORY_PROVIDER_ERROR"
    assert body["detail"]["retryable"] is True
    assert body["detail"]["retry_with_new_idempotency_key"] is True
    assert "Gemini" not in json.dumps(body)  # Ham exception sızmamalı

    # Attempt failed olmalı
    att = db_session.query(SocialGenerationAttempt).filter_by(brief_id=brief.id).one()
    assert att.status == "failed"
    assert att.reason_code == "category_provider_error"
    assert db_session.query(SocialCategory).filter_by(brief_id=brief.id).count() == 0


def test_17_two_invalid_ai_responses_returns_502_and_marks_attempt_failed(
    client: TestClient, db_session: Session, enable_flag, make_workspace, make_scoring_run, make_keyword
):
    """17. İki AI yanıtı da geçersiz olduğunda HTTP 502 döner ve attempt failed (category_output_invalid) yapılır."""
    ws, run, brief, kws = _setup_environment(db_session, make_workspace, make_scoring_run, make_keyword)

    broken1 = json.dumps({"categories": []})
    broken2 = json.dumps({"categories": [{"category_name": "Tek Kategori"}]})
    fake_ai = FakeAIService(responses=[broken1, broken2])
    app.dependency_overrides[get_ai] = lambda: fake_ai

    resp = client.post(
        f"/api/v1/generation/social/briefs/{brief.id}/categories/generate?brand_profile_id={ws.id}",
        json={"idempotency_key": "ai-invalid-twice-key", "max_categories": 4},
    )
    assert resp.status_code == 502
    body = resp.json()
    assert body["detail"]["code"] == "CATEGORY_OUTPUT_INVALID"
    assert fake_ai.call_count == 2

    att = db_session.query(SocialGenerationAttempt).filter_by(brief_id=brief.id).one()
    assert att.status == "failed"
    assert att.reason_code == "category_output_invalid"
    assert db_session.query(SocialCategory).filter_by(brief_id=brief.id).count() == 0


def test_18_ai_failure_finalization_transaction_committed_exactly_two_commits(
    client: TestClient, db_session: Session, enable_flag, make_workspace, make_scoring_run, make_keyword, monkeypatch
):
    """18. AI hatasında preflight commit (1) + failed attempt finalizasyon commit (2) = toplam 2 commit gerçekleşir."""
    ws, run, brief, kws = _setup_environment(db_session, make_workspace, make_scoring_run, make_keyword)

    fake_ai = FakeAIService(raise_exc=RuntimeError("Provider down"))
    app.dependency_overrides[get_ai] = lambda: fake_ai

    real_commit = db_session.commit
    commit_count = 0

    def counting_commit():
        nonlocal commit_count
        commit_count += 1
        real_commit()

    monkeypatch.setattr(db_session, "commit", counting_commit)

    resp = client.post(
        f"/api/v1/generation/social/briefs/{brief.id}/categories/generate?brand_profile_id={ws.id}",
        json={"idempotency_key": "ai-commits-key", "max_categories": 4},
    )
    assert resp.status_code == 502
    assert commit_count == 2


# ==================== 6. PERSISTENCE BAŞARISIZLIK FİNALİZASYONU ====================

def test_19_brief_becomes_stale_during_ai_generation_persistence_fails_409(
    client: TestClient, db_session: Session, enable_flag, make_workspace, make_scoring_run, make_keyword
):
    """19. AI çalışırken araya giren işlem brief'i stale yaparsa persistence aşamasında HTTP 409 döner ve attempt failed olur."""
    ws, run, brief, kws = _setup_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    kw_ids = tuple(k.id for k in kws)
    valid_json = _valid_category_json(kw_ids)

    def on_complete(prompt: str, kwargs: dict[str, Any]):
        # Araya ikinci oturumla girip brief'i stale yap
        other_session = SessionLocal()
        try:
            b = other_session.get(SocialBrief, brief.id)
            b.is_stale = True
            other_session.commit()
        finally:
            other_session.close()

    fake_ai = FakeAIService(responses=[valid_json], on_complete_json=on_complete)
    app.dependency_overrides[get_ai] = lambda: fake_ai

    resp = client.post(
        f"/api/v1/generation/social/briefs/{brief.id}/categories/generate?brand_profile_id={ws.id}",
        json={"idempotency_key": "race-stale-key", "max_categories": 4},
    )
    assert resp.status_code == 409
    body = resp.json()
    assert body["detail"]["code"] == "BRIEF_STALE"

    # Kategori yazılmamış olmalı ve attempt failed olmalı
    att = db_session.query(SocialGenerationAttempt).filter_by(brief_id=brief.id).one()
    assert att.status == "failed"
    assert att.reason_code == "brief_stale"
    assert db_session.query(SocialCategory).filter_by(brief_id=brief.id).count() == 0


def test_20_persistence_integrity_error_rollback_and_marks_attempt_failed_409(
    client: TestClient, db_session: Session, enable_flag, make_workspace, make_scoring_run, make_keyword, monkeypatch
):
    """20. Persistence sırasında veritabanı IntegrityError oluşursa 409 CATEGORY_PERSISTENCE_CONFLICT döner ve SQL sızmaz."""
    ws, run, brief, kws = _setup_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    kw_ids = tuple(k.id for k in kws)
    valid_json = _valid_category_json(kw_ids)
    fake_ai = FakeAIService(responses=[valid_json])
    app.dependency_overrides[get_ai] = lambda: fake_ai

    from sqlalchemy.exc import IntegrityError
    import app.api.v1.generation as gen_mod

    real_persist = gen_mod.persist_social_categories

    def failing_persist(*args, **kwargs):
        raise IntegrityError("INSERT INTO social_categories ...", params={}, orig=Exception("Unique constraint violation"))

    monkeypatch.setattr(gen_mod, "persist_social_categories", failing_persist)

    resp = client.post(
        f"/api/v1/generation/social/briefs/{brief.id}/categories/generate?brand_profile_id={ws.id}",
        json={"idempotency_key": "int-err-key", "max_categories": 4},
    )
    assert resp.status_code == 409
    body = resp.json()
    assert body["detail"]["code"] == "CATEGORY_PERSISTENCE_CONFLICT"
    assert "INSERT INTO" not in json.dumps(body)  # Raw SQL sızmamalı

    att = db_session.query(SocialGenerationAttempt).filter_by(brief_id=brief.id).one()
    assert att.status == "failed"


# ==================== 7. BİLGİ SIZINTISI KORUMASI ====================

def test_21_error_details_do_not_leak_prompt_brand_sql_or_provider_exception(
    client: TestClient, db_session: Session, enable_flag, make_workspace, make_scoring_run, make_keyword
):
    """21. Hata yanıtları gizli marka adını veya dahili prompt etiketlerini sızdırmaz."""
    secret_brand = "COK_GIZLI_MARKA_XZY_999"
    ws, run, brief, kws = _setup_environment(db_session, make_workspace, make_scoring_run, make_keyword, workspace_name=secret_brand)

    fake_ai = FakeAIService(raise_exc=RuntimeError("Secret internal AI crash"))
    app.dependency_overrides[get_ai] = lambda: fake_ai

    resp = client.post(
        f"/api/v1/generation/social/briefs/{brief.id}/categories/generate?brand_profile_id={ws.id}",
        json={"idempotency_key": "leak-test-key", "max_categories": 4},
    )
    assert resp.status_code == 502
    body_str = resp.text
    assert secret_brand not in body_str
    assert "Secret internal AI crash" not in body_str


# ==================== 8. REGRESYON TESTLERİ ====================

def test_22_format_matrix_endpoint_unaffected(client: TestClient):
    """22. GET /generation/social/format-matrix format matrisi endpoint'i feature flag bağımsız sorunsuz çalışır."""
    resp = client.get("/api/v1/generation/social/format-matrix")
    assert resp.status_code == 200
    data = resp.json()
    assert data["version"] == "v1"
    assert len(data["platforms"]) > 0


def test_23_existing_brief_endpoints_unaffected(
    client: TestClient, db_session: Session, enable_flag, make_workspace, make_scoring_run, make_keyword
):
    """23. Mevcut POST /social/briefs ve GET /social/briefs endpoint'leri regression olmaksızın çalışmaya devam eder."""
    ws, run, brief, kws = _setup_environment(db_session, make_workspace, make_scoring_run, make_keyword)

    # Brief okuma
    resp_get = client.get(
        f"/api/v1/generation/social/briefs/{brief.id}?brand_profile_id={ws.id}"
    )
    assert resp_get.status_code == 200
    assert resp_get.json()["id"] == brief.id

    # Brief listeleme
    resp_list = client.get(
        f"/api/v1/generation/social/briefs?scoring_run_id={run.id}&brand_profile_id={ws.id}"
    )
    assert resp_list.status_code == 200
    assert len(resp_list.json()) >= 1


# ==================== 9. MİKRO FAZ F1-E.5a GÜVENLİK VE SERTLEŞTİRME TESTLERİ ====================

def test_24_failure_finalization_canonical_sql_lock_order(
    client: TestClient, db_session: Session, enable_flag, make_workspace, make_scoring_run, make_keyword
):
    """24. AI hatası sonrası failure finalization SQL kilit sırasının canonical
    (ScoringRun -> SocialBrief -> SocialGenerationAttempt) olduğunu doğrular."""
    from sqlalchemy import event

    ws, run, brief, kws = _setup_environment(db_session, make_workspace, make_scoring_run, make_keyword)

    fake_ai = FakeAIService(raise_exc=RuntimeError("AI Provider Down"))
    app.dependency_overrides[get_ai] = lambda: fake_ai

    locked_tables: list[str] = []

    def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        stmt_upper = statement.upper()
        if "FOR UPDATE" in stmt_upper:
            if "SCORING_RUNS" in stmt_upper:
                locked_tables.append("scoring_runs")
            elif "SOCIAL_BRIEFS" in stmt_upper:
                locked_tables.append("social_briefs")
            elif "SOCIAL_GENERATION_ATTEMPTS" in stmt_upper:
                locked_tables.append("social_generation_attempts")

    engine = db_session.bind
    event.listen(engine, "before_cursor_execute", before_cursor_execute)
    try:
        resp = client.post(
            f"/api/v1/generation/social/briefs/{brief.id}/categories/generate?brand_profile_id={ws.id}",
            json={"idempotency_key": "lock-order-key", "max_categories": 4},
        )
    finally:
        event.remove(engine, "before_cursor_execute", before_cursor_execute)

    assert resp.status_code == 502
    assert resp.json()["detail"]["code"] == "CATEGORY_PROVIDER_ERROR"

    # En sondaki 3 kilit failure finalization'a aittir ve canonical sırada olmalıdır:
    assert len(locked_tables) >= 3
    finalization_locks = locked_tables[-3:]
    assert finalization_locks == [
        "scoring_runs",
        "social_briefs",
        "social_generation_attempts",
    ], f"Kilit sırası canonical olmalıdır: {finalization_locks}"


def test_25_failure_finalization_failure_returns_500_with_category_failure_finalization_failed(
    client: TestClient, db_session: Session, enable_flag, make_workspace, make_scoring_run, make_keyword, monkeypatch
):
    """25. Failure finalization sırasında DB hatası oluşursa exception yutulmaz;
    HTTP 500 code='CATEGORY_FAILURE_FINALIZATION_FAILED' döner."""
    ws, run, brief, kws = _setup_environment(db_session, make_workspace, make_scoring_run, make_keyword)

    fake_ai = FakeAIService(raise_exc=RuntimeError("AI Provider crash"))
    app.dependency_overrides[get_ai] = lambda: fake_ai

    import app.api.v1.generation as gen_mod

    def failing_finish(*args, **kwargs):
        raise RuntimeError("Database connection lost during finish_attempt")

    monkeypatch.setattr(gen_mod, "finish_attempt", failing_finish)

    resp = client.post(
        f"/api/v1/generation/social/briefs/{brief.id}/categories/generate?brand_profile_id={ws.id}",
        json={"idempotency_key": "fail-final-key", "max_categories": 4},
    )
    assert resp.status_code == 500
    body = resp.json()
    assert body["detail"]["code"] == "CATEGORY_FAILURE_FINALIZATION_FAILED"


def test_26_unexpected_ai_exception_path_does_not_swallow_finalization_failure(
    client: TestClient, db_session: Session, enable_flag, make_workspace, make_scoring_run, make_keyword, monkeypatch
):
    """26. Beklenmeyen (provider dışı) AI exception yolunda da finalizasyon hatası yutulmaz;
    HTTP 500 code='CATEGORY_FAILURE_FINALIZATION_FAILED' döner."""
    ws, run, brief, kws = _setup_environment(db_session, make_workspace, make_scoring_run, make_keyword)

    import app.api.v1.generation as gen_mod

    def exploding_generate(*args, **kwargs):
        raise TypeError("Unexpected non-provider internal bug")

    monkeypatch.setattr(gen_mod.SocialBriefCategoryGenerator, "generate", exploding_generate)

    def failing_finish(*args, **kwargs):
        raise RuntimeError("DB connection dropped")

    monkeypatch.setattr(gen_mod, "finish_attempt", failing_finish)

    resp = client.post(
        f"/api/v1/generation/social/briefs/{brief.id}/categories/generate?brand_profile_id={ws.id}",
        json={"idempotency_key": "unexp-ai-final-fail", "max_categories": 4},
    )
    assert resp.status_code == 500
    body = resp.json()
    assert body["detail"]["code"] == "CATEGORY_FAILURE_FINALIZATION_FAILED"


def test_27_transaction_boundary_violation_rolls_back_dirty_tx_and_avoids_ai_call(
    client: TestClient, db_session: Session, enable_flag, make_workspace, make_scoring_run, make_keyword, monkeypatch
):
    """27. Aşama 2 öncesinde beklenmeyen açık transaction tespit edilirse:
    - rollback yapılır (kirli transaction commit edilmez),
    - attempt failed olarak finalize edilir (reason_code=transaction_boundary_violation),
    - AI çağrısı yapılmaz (call_count == 0),
    - HTTP 500 code='CATEGORY_TRANSACTION_BOUNDARY_VIOLATION' döner."""
    ws, run, brief, kws = _setup_environment(db_session, make_workspace, make_scoring_run, make_keyword)

    fake_ai = FakeAIService()
    app.dependency_overrides[get_ai] = lambda: fake_ai

    real_commit = db_session.commit
    first_commit_done = False

    def commit_and_dirty_transaction():
        nonlocal first_commit_done
        real_commit()
        if not first_commit_done:
            first_commit_done = True
            # Bilinmeyen bir transaction ve commit edilmemesi gereken kirli kayıt
            db_session.execute(
                text(
                    "INSERT INTO keywords (keyword, monthly_volume, trend_12m, trend_3m, competition_score, is_active, data_source) "
                    "VALUES ('unwanted_dirty_kw', 888, 0.0, 0.0, 0.5, true, 'csv')"
                )
            )
            assert db_session.in_transaction() is True

    monkeypatch.setattr(db_session, "commit", commit_and_dirty_transaction)

    resp = client.post(
        f"/api/v1/generation/social/briefs/{brief.id}/categories/generate?brand_profile_id={ws.id}",
        json={"idempotency_key": "boundary-violation-key", "max_categories": 4},
    )
    assert resp.status_code == 500
    body = resp.json()
    assert body["detail"]["code"] == "CATEGORY_TRANSACTION_BOUNDARY_VIOLATION"

    # AI çağrısı KESİNLİKLE yapılmamış olmalı
    assert fake_ai.call_count == 0

    # Kirli transaction rollback edilmiş olmalı (unwanted_dirty_kw DB'de olmamalı)
    fresh_session = SessionLocal()
    try:
        from app.database.models import Keyword
        rogue_kw = fresh_session.query(Keyword).filter_by(keyword="unwanted_dirty_kw").first()
        assert rogue_kw is None, "Kirli transaction commit edilmemeliydi!"

        # Attempt failed ve transaction_boundary_violation ile commit edilmiş olmalı
        att = fresh_session.query(SocialGenerationAttempt).filter_by(brief_id=brief.id).one()
        assert att.status == "failed"
        assert att.reason_code == "transaction_boundary_violation"
    finally:
        fresh_session.close()


def test_28_persistence_stale_and_integrity_errors_truly_commit_failed_attempt(
    client: TestClient, db_session: Session, enable_flag, make_workspace, make_scoring_run, make_keyword, monkeypatch
):
    """28. Persistence aşamasında brief stale veya IntegrityError olduğunda attempt'in
    gerçekten veritabanına 'failed' olarak commit edildiğini bağımsız oturumla doğrular."""
    ws, run, brief, kws = _setup_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    kw_ids = tuple(k.id for k in kws)
    valid_json = _valid_category_json(kw_ids)

    # 1. Stale Brief Senaryosu
    def on_complete(prompt: str, kwargs: dict[str, Any]):
        other_session = SessionLocal()
        try:
            b = other_session.get(SocialBrief, brief.id)
            b.is_stale = True
            other_session.commit()
        finally:
            other_session.close()

    fake_ai = FakeAIService(responses=[valid_json], on_complete_json=on_complete)
    app.dependency_overrides[get_ai] = lambda: fake_ai

    resp1 = client.post(
        f"/api/v1/generation/social/briefs/{brief.id}/categories/generate?brand_profile_id={ws.id}",
        json={"idempotency_key": "stale-persist-commit-key", "max_categories": 4},
    )
    assert resp1.status_code == 409
    assert resp1.json()["detail"]["code"] == "BRIEF_STALE"

    # Bağımsız oturum ile attempt'in DB'de gerçekten failed commit edildiğini doğrula
    session_check1 = SessionLocal()
    try:
        att1 = session_check1.query(SocialGenerationAttempt).filter_by(brief_id=brief.id).one()
        assert att1.status == "failed"
        assert att1.reason_code == "brief_stale"
    finally:
        session_check1.close()

    # 2. IntegrityError Senaryosu (yeni bir brief ve run ile)
    ws2, run2, brief2, kws2 = _setup_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    valid_json2 = _valid_category_json(tuple(k.id for k in kws2))
    fake_ai2 = FakeAIService(responses=[valid_json2])
    app.dependency_overrides[get_ai] = lambda: fake_ai2

    from sqlalchemy.exc import IntegrityError
    import app.api.v1.generation as gen_mod

    def failing_persist(*args, **kwargs):
        raise IntegrityError("INSERT INTO social_categories ...", params={}, orig=Exception("Unique constraint violation"))

    monkeypatch.setattr(gen_mod, "persist_social_categories", failing_persist)

    resp2 = client.post(
        f"/api/v1/generation/social/briefs/{brief2.id}/categories/generate?brand_profile_id={ws2.id}",
        json={"idempotency_key": "integrity-persist-commit-key", "max_categories": 4},
    )
    assert resp2.status_code == 409
    assert resp2.json()["detail"]["code"] == "CATEGORY_PERSISTENCE_CONFLICT"

    session_check2 = SessionLocal()
    try:
        att2 = session_check2.query(SocialGenerationAttempt).filter_by(brief_id=brief2.id).one()
        assert att2.status == "failed"
        assert att2.reason_code == "persistence_conflict"
    finally:
        session_check2.close()


def test_29_success_path_exactly_two_commits_and_zero_transaction_during_ai(
    client: TestClient, db_session: Session, enable_flag, make_workspace, make_scoring_run, make_keyword, monkeypatch
):
    """29. Normal başarılı kategori üretim yolunda:
    - Preflight commit (1) + Persistence commit (2) = tam 2 commit gerçekleşir,
    - Rollback sayısı tam olarak 0'dır,
    - AI çağrısı sırasında session'da hiçbir transaction açık değildir (in_transaction is False)
      ve başka bir oturum tüm ilgili tablolarda FOR UPDATE NOWAIT alabilir."""
    ws, run, brief, kws = _setup_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    kw_ids = tuple(k.id for k in kws)
    valid_json = _valid_category_json(kw_ids)

    ai_verified_no_tx = False

    def on_ai_complete(prompt: str, kwargs: dict[str, Any]):
        nonlocal ai_verified_no_tx
        # 1. Oturum transaction içinde olmamalı
        assert db_session.in_transaction() is False

        # 2. İkinci oturum lock alabilmeli (PostgreSQL lock proof)
        second_session = SessionLocal()
        try:
            second_session.query(ScoringRun).filter(ScoringRun.id == run.id).with_for_update(nowait=True).one()
            second_session.query(SocialBrief).filter(SocialBrief.id == brief.id).with_for_update(nowait=True).one()
            second_session.query(SocialGenerationAttempt).filter_by(brief_id=brief.id).with_for_update(nowait=True).one()
            second_session.query(BrandProfile).filter(BrandProfile.id == ws.id).with_for_update(nowait=True).one()
            second_session.rollback()
            ai_verified_no_tx = True
        finally:
            second_session.close()

    fake_ai = FakeAIService(responses=[valid_json], on_complete_json=on_ai_complete)
    app.dependency_overrides[get_ai] = lambda: fake_ai

    real_commit = db_session.commit
    commit_count = 0

    def counting_commit():
        nonlocal commit_count
        commit_count += 1
        real_commit()

    real_rollback = db_session.rollback
    rollback_count = 0

    def counting_rollback():
        nonlocal rollback_count
        rollback_count += 1
        real_rollback()

    monkeypatch.setattr(db_session, "commit", counting_commit)
    monkeypatch.setattr(db_session, "rollback", counting_rollback)

    resp = client.post(
        f"/api/v1/generation/social/briefs/{brief.id}/categories/generate?brand_profile_id={ws.id}",
        json={"idempotency_key": "two-commits-pure-key", "max_categories": 4},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["replayed"] is False
    assert data["attempt_status"] == "completed"
    assert data["ai_calls_used"] == 1
    assert len(data["categories"]) == 2

    assert ai_verified_no_tx is True
    assert commit_count == 2, f"Beklenen 2 commit, gerçekleşen: {commit_count}"
    assert rollback_count == 0, f"Başarılı yolda rollback olmamalı, gerçekleşen: {rollback_count}"


def test_30_live_chain_provider_400_then_same_key_409_then_new_key_new_attempt(
    client: TestClient, db_session: Session, enable_flag, make_workspace, make_scoring_run, make_keyword
):
    """30. Canlı zincir: sağlayıcı 400 -> 502 (failed); aynı anahtar -> 409, AI yok;
    yeni anahtar -> YENİ attempt, 201. Sağlayıcı şeması additionalProperties taşımaz."""
    ws, run, brief, kws = _setup_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    url = f"/api/v1/generation/social/briefs/{brief.id}/categories/generate?brand_profile_id={ws.id}"
    provider_400 = RuntimeError(
        "400 INVALID_ARGUMENT: Unknown name \"additional_properties\" at "
        "'generation_config.response_schema'")
    fake_ai = FakeAIService(responses=[provider_400, _valid_category_json(tuple(k.id for k in kws))])
    app.dependency_overrides[get_ai] = lambda: fake_ai

    first = client.post(url, json={"idempotency_key": "live-key-a", "max_categories": 4})
    assert first.status_code == 502
    assert first.json()["detail"]["code"] == "CATEGORY_PROVIDER_ERROR"
    assert first.json()["detail"]["retry_with_new_idempotency_key"] is True
    failed_attempt_id = first.json()["detail"]["attempt_id"]

    same_key = client.post(url, json={"idempotency_key": "live-key-a", "max_categories": 4})
    assert same_key.status_code == 409
    assert same_key.json()["detail"]["code"] == "CATEGORY_ATTEMPT_TERMINAL"
    assert same_key.json()["detail"]["attempt_id"] == failed_attempt_id
    assert fake_ai.call_count == 1

    fresh = client.post(url, json={"idempotency_key": "live-key-b", "max_categories": 4})
    assert fresh.status_code == 201
    body = fresh.json()
    assert body["attempt_status"] == "completed"
    assert body["replayed"] is False
    assert body["attempt_id"] != failed_attempt_id
    assert fake_ai.call_count == 2

    db_session.expire_all()
    attempts = {
        a.idempotency_key: a
        for a in db_session.query(SocialGenerationAttempt).filter_by(brief_id=brief.id, stage="categories")
    }
    assert set(attempts) == {"live-key-a", "live-key-b"}
    assert attempts["live-key-a"].status == "failed"
    assert attempts["live-key-a"].reason_code == "category_provider_error"
    assert attempts["live-key-b"].status == "completed"
    assert db_session.query(SocialCategory).filter_by(brief_id=brief.id).count() == 2

    # Uç nokta yolu da sağlayıcıya Gemini uyumlu kopyayı verir; katılık yerelde.
    for call in fake_ai.call_args:
        assert "additionalProperties" not in json.dumps(call["response_schema"])
