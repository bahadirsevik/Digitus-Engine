# -*- coding: utf-8 -*-
"""Legacy sosyal endpoint'lerin brief sınırı (plan_social_brief_akisi.md §7/§8).

Brief akışı devreye girdikten sonra legacy uçlar (`/social/ideas`,
`/social/contents`, `/social/contents/async`, `/social/ideas/{id}/select`,
`/social/ideas/{id}/regenerate`, `/social/contents/{id}/regenerate`,
`GET /social/{run_id}`) yalnız `brief_id IS NULL` verisiyle çalışmalıdır.
Brief'e ait bir kategori/fikir/içerik legacy uçtan görülürse/işlenirse
HİÇBİR AI çağrısı yapılmadan 409 dönmeli ve veri DEĞİŞMEMELİDİR (K10/K11).

Ayrıca:
- K10: içeriği zaten üretilmiş bir fikir legacy `/regenerate` ile tekrar
  üretilemez — 409 + fikir değişmez.
- K12: `ENABLE_SOCIAL_LEGACY_BULK` kapalıyken `/social/bulk` 410 döner.
- AI_STAGES kaydı brief AI aşamalarını içerir.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import settings
from app.core.constants import AI_STAGES
from app.database.models import (
    ChannelPool,
    SocialBrief,
    SocialBriefKeyword,
    SocialBriefTarget,
    SocialCategory,
    SocialContent,
    SocialIdea,
    TaskResult,
)
from app.dependencies import get_ai
from app.main import app


# ==================== AI GÜVENLİK DUVARI ====================


class NoAICallAllowed:
    """Herhangi bir AI metodu çağrılırsa AssertionError fırlatır.

    Brief sınırı ihlali testlerinde 'hiçbir AI çağrısı yapılmadı' iddiasını
    kanıtlamak için kullanılır — gerçek AI'ya asla gidilmez.
    """

    def __init__(self) -> None:
        self.collector = SimpleNamespace(scoring_run_id=None, brand_profile_id=None)
        self.call_count = 0

    def _fail(self, *args, **kwargs):
        self.call_count += 1
        raise AssertionError(
            "AI çağrısı yapılmamalıydı (brief sınırlaması ihlal edildi)"
        )

    def for_stage(self, *args, **kwargs):
        return self

    complete = _fail
    complete_json = _fail
    complete_grounded = _fail


@pytest.fixture
def ai_guard():
    """Tekil NoAICallAllowed nesnesi — istek boyunca AYNI nesne kullanılır,
    çağrı sayacı gerçek anlamda doğrulanabilsin."""
    guard = NoAICallAllowed()
    app.dependency_overrides[get_ai] = lambda: guard
    yield guard
    app.dependency_overrides.pop(get_ai, None)


# ==================== FIXTURE HELPERS ====================


def _setup_env(db_session: Session, make_workspace, make_scoring_run, make_keyword):
    """Workspace + fresh (non-stale) scoring run + SOCIAL havuzunda 1 kelime."""
    ws = make_workspace(name="Legacy Boundary WS", status="confirmed")
    run = make_scoring_run(
        brand_profile_id=ws.id,
        status="completed",
        algorithm_version="v3",
        channel_pool_policy_version=1,
        relevance_anchor_version=1,
        channel_assignment_version=1,
        skip_relevance=True,
    )
    kw = make_keyword(text_value="brief sinir testi", brand_profile_id=ws.id)
    pool = ChannelPool(
        scoring_run_id=run.id,
        keyword_id=kw.id,
        channel="SOCIAL",
        final_rank=1,
        relevance_score=0.9,
        adjusted_score=20.0,
    )
    db_session.add(pool)
    db_session.commit()
    return ws, run, kw


def _make_brief(db_session: Session, run, kw) -> SocialBrief:
    brief = SocialBrief(
        scoring_run_id=run.id,
        brand_name_snapshot="Legacy Boundary WS",
        brand_context_snapshot="Marka bağlamı",
        channel_assignment_version=1,
        format_matrix_version="v1",
        is_stale=False,
        locked_at=None,
    )
    db_session.add(brief)
    db_session.flush()

    db_session.add(SocialBriefKeyword(
        brief_id=brief.id, keyword_id=kw.id, keyword_snapshot=kw.keyword, position=0,
    ))
    target = SocialBriefTarget(
        brief_id=brief.id, platform="instagram", content_format="post",
    )
    db_session.add(target)
    db_session.commit()
    return brief, target


def _make_category(db_session, run, *, brief=None) -> SocialCategory:
    cat = SocialCategory(
        scoring_run_id=run.id,
        brief_id=brief.id if brief else None,
        category_name="Eğitici İçerikler",
        category_type="educational",
        description="Test kategori",
        relevance_score=0.9,
        suggested_keyword_ids=[],
    )
    db_session.add(cat)
    db_session.commit()
    return cat


def _make_idea(db_session, category, *, brief=None, target=None, keyword_id=None) -> SocialIdea:
    idea = SocialIdea(
        category_id=category.id,
        keyword_id=keyword_id,
        brief_id=brief.id if brief else None,
        brief_target_id=target.id if target else None,
        idea_title="Test Fikir",
        idea_description="Detaylı açıklama",
        target_platform="instagram",
        content_format="post",
        trend_alignment=0.8,
        is_selected=False,
        regeneration_count=0,
    )
    db_session.add(idea)
    db_session.commit()
    return idea


def _make_content(db_session, idea, *, brief=None) -> SocialContent:
    content = SocialContent(
        idea_id=idea.id,
        brief_id=brief.id if brief else None,
        hooks=[{"text": "Dikkat çekici soru?", "style": "question", "ab_score": None}],
        caption="Test caption",
        scenario="Test senaryo",
        cta_text="Hemen keşfet",
        hashtags=["test1", "test2", "test3", "test4", "test5"],
        regeneration_count=0,
    )
    db_session.add(content)
    db_session.commit()
    return content


# ==================== 1. POST /social/ideas ====================


def test_ideas_endpoint_rejects_brief_category_before_ai_call(
    client: TestClient, db_session, make_workspace, make_scoring_run, make_keyword, ai_guard,
):
    ws, run, kw = _setup_env(db_session, make_workspace, make_scoring_run, make_keyword)
    brief, target = _make_brief(db_session, run, kw)
    brief_cat = _make_category(db_session, run, brief=brief)

    resp = client.post(
        "/api/v1/generation/social/ideas",
        params={"brand_profile_id": ws.id},
        json={"category_ids": [brief_cat.id]},
    )

    assert resp.status_code == 409
    assert "BRIEF_SCOPED" in resp.json()["detail"]
    assert ai_guard.call_count == 0

    db_session.expire_all()
    assert db_session.query(SocialIdea).filter_by(category_id=brief_cat.id).count() == 0


# ==================== 2. POST /social/contents ====================


def test_contents_endpoint_rejects_brief_idea_before_ai_call(
    client: TestClient, db_session, make_workspace, make_scoring_run, make_keyword, ai_guard,
):
    ws, run, kw = _setup_env(db_session, make_workspace, make_scoring_run, make_keyword)
    brief, target = _make_brief(db_session, run, kw)
    brief_cat = _make_category(db_session, run, brief=brief)
    brief_idea = _make_idea(db_session, brief_cat, brief=brief, target=target, keyword_id=kw.id)

    resp = client.post(
        "/api/v1/generation/social/contents",
        params={"brand_profile_id": ws.id},
        json={"idea_ids": [brief_idea.id]},
    )

    assert resp.status_code == 409
    assert "BRIEF_SCOPED" in resp.json()["detail"]
    assert ai_guard.call_count == 0

    db_session.expire_all()
    assert db_session.query(SocialContent).filter_by(idea_id=brief_idea.id).count() == 0


# ==================== 3. POST /social/contents/async ====================


def test_contents_async_endpoint_rejects_brief_idea_before_dispatch(
    client: TestClient, db_session, make_workspace, make_scoring_run, make_keyword, monkeypatch,
):
    ws, run, kw = _setup_env(db_session, make_workspace, make_scoring_run, make_keyword)
    brief, target = _make_brief(db_session, run, kw)
    brief_cat = _make_category(db_session, run, brief=brief)
    brief_idea = _make_idea(db_session, brief_cat, brief=brief, target=target, keyword_id=kw.id)

    dispatched = []
    monkeypatch.setattr(
        "app.tasks.generation_tasks.social_contents_task.apply_async",
        lambda *a, **kw: dispatched.append((a, kw)),
    )

    before = db_session.query(TaskResult).count()

    resp = client.post(
        "/api/v1/generation/social/contents/async",
        params={"brand_profile_id": ws.id},
        json={"idea_ids": [brief_idea.id]},
    )

    assert resp.status_code == 409
    assert "BRIEF_SCOPED" in resp.json()["detail"]
    assert dispatched == []

    db_session.expire_all()
    assert db_session.query(TaskResult).count() == before


# ==================== 4. POST /social/ideas/{id}/select ====================


def test_select_idea_rejects_brief_idea(
    client: TestClient, db_session, make_workspace, make_scoring_run, make_keyword, ai_guard,
):
    ws, run, kw = _setup_env(db_session, make_workspace, make_scoring_run, make_keyword)
    brief, target = _make_brief(db_session, run, kw)
    brief_cat = _make_category(db_session, run, brief=brief)
    brief_idea = _make_idea(db_session, brief_cat, brief=brief, target=target, keyword_id=kw.id)

    resp = client.post(
        f"/api/v1/generation/social/ideas/{brief_idea.id}/select",
        params={"brand_profile_id": ws.id, "selected": True},
    )

    assert resp.status_code == 409
    assert "BRIEF_SCOPED" in resp.json()["detail"]
    assert ai_guard.call_count == 0

    db_session.expire_all()
    refreshed = db_session.get(SocialIdea, brief_idea.id)
    assert refreshed.is_selected is False


# ==================== 5. POST /social/ideas/{id}/regenerate — brief ====================


def test_regenerate_idea_rejects_brief_idea_before_ai_call(
    client: TestClient, db_session, make_workspace, make_scoring_run, make_keyword, ai_guard,
):
    ws, run, kw = _setup_env(db_session, make_workspace, make_scoring_run, make_keyword)
    brief, target = _make_brief(db_session, run, kw)
    brief_cat = _make_category(db_session, run, brief=brief)
    brief_idea = _make_idea(db_session, brief_cat, brief=brief, target=target, keyword_id=kw.id)

    resp = client.post(
        f"/api/v1/generation/social/ideas/{brief_idea.id}/regenerate",
        params={"brand_profile_id": ws.id},
    )

    assert resp.status_code == 409
    assert "BRIEF_SCOPED" in resp.json()["detail"]
    assert ai_guard.call_count == 0

    db_session.expire_all()
    refreshed = db_session.get(SocialIdea, brief_idea.id)
    assert refreshed.idea_title == "Test Fikir"
    assert refreshed.regeneration_count == 0


# ==================== 6. POST /social/ideas/{id}/regenerate — K10 ====================


def test_regenerate_idea_rejects_when_content_already_exists(
    client: TestClient, db_session, make_workspace, make_scoring_run, make_keyword, ai_guard,
):
    """K10: içeriği üretilmiş bir fikir yeniden üretilemez, fikir değişmez."""
    ws, run, kw = _setup_env(db_session, make_workspace, make_scoring_run, make_keyword)
    legacy_cat = _make_category(db_session, run)
    legacy_idea = _make_idea(db_session, legacy_cat, keyword_id=kw.id)
    _make_content(db_session, legacy_idea)

    resp = client.post(
        f"/api/v1/generation/social/ideas/{legacy_idea.id}/regenerate",
        params={"brand_profile_id": ws.id},
    )

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert "Bu fikrin içeriği var" in detail
    assert ai_guard.call_count == 0

    db_session.expire_all()
    refreshed = db_session.get(SocialIdea, legacy_idea.id)
    assert refreshed.idea_title == "Test Fikir"
    assert refreshed.regeneration_count == 0


# ==================== 7. POST /social/contents/{id}/regenerate — brief ====================


def test_regenerate_content_rejects_brief_content(
    client: TestClient, db_session, make_workspace, make_scoring_run, make_keyword, ai_guard,
):
    ws, run, kw = _setup_env(db_session, make_workspace, make_scoring_run, make_keyword)
    brief, target = _make_brief(db_session, run, kw)
    brief_cat = _make_category(db_session, run, brief=brief)
    brief_idea = _make_idea(db_session, brief_cat, brief=brief, target=target, keyword_id=kw.id)
    brief_content = _make_content(db_session, brief_idea, brief=brief)

    resp = client.post(
        f"/api/v1/generation/social/contents/{brief_content.id}/regenerate",
        params={"brand_profile_id": ws.id},
    )

    assert resp.status_code == 409
    assert "BRIEF_SCOPED" in resp.json()["detail"]
    assert ai_guard.call_count == 0

    db_session.expire_all()
    refreshed = db_session.get(SocialContent, brief_content.id)
    assert refreshed.caption == "Test caption"
    assert refreshed.regeneration_count == 0


# ==================== 8. GET /social/{run_id} — legacy view ====================


def test_get_all_excludes_brief_rows(
    client: TestClient, db_session, make_workspace, make_scoring_run, make_keyword, ai_guard,
):
    ws, run, kw = _setup_env(db_session, make_workspace, make_scoring_run, make_keyword)

    legacy_cat = _make_category(db_session, run)
    legacy_idea = _make_idea(db_session, legacy_cat, keyword_id=kw.id)
    legacy_content = _make_content(db_session, legacy_idea)

    brief, target = _make_brief(db_session, run, kw)
    brief_cat = _make_category(db_session, run, brief=brief)
    brief_idea = _make_idea(db_session, brief_cat, brief=brief, target=target, keyword_id=kw.id)
    brief_content = _make_content(db_session, brief_idea, brief=brief)

    resp = client.get(
        f"/api/v1/generation/social/{run.id}",
        params={"brand_profile_id": ws.id},
    )

    assert resp.status_code == 200
    body = resp.json()

    category_ids = {c["id"] for c in body["categories"]}
    idea_ids = {i["id"] for i in body["ideas"]}
    content_ids = {c["id"] for c in body["contents"]}

    assert category_ids == {legacy_cat.id}
    assert idea_ids == {legacy_idea.id}
    assert content_ids == {legacy_content.id}
    assert brief_cat.id not in category_ids
    assert brief_idea.id not in idea_ids
    assert brief_content.id not in content_ids


# ==================== 9. POST /social/bulk — K12 gate ====================


def test_bulk_endpoint_returns_410_when_legacy_flag_off(
    client: TestClient, db_session, make_workspace, make_scoring_run, make_keyword, ai_guard,
):
    ws, run, kw = _setup_env(db_session, make_workspace, make_scoring_run, make_keyword)
    assert settings.ENABLE_SOCIAL_LEGACY_BULK is False  # varsayılan kapalı

    resp = client.post(
        "/api/v1/generation/social/bulk",
        params={"brand_profile_id": ws.id},
        json={"scoring_run_id": run.id, "brand_name": "M"},
    )

    assert resp.status_code == 410
    assert ai_guard.call_count == 0


def test_bulk_endpoint_works_when_legacy_flag_on_is_not_gated(
    client: TestClient, db_session, make_workspace, make_scoring_run, make_keyword, monkeypatch,
):
    """Bayrak açıkken 410 gelmemeli (eski davranışa devam) — burada gerçek
    üretim tetiklemeden, kapının SADECE bayrağa baktığını doğrularız."""
    ws, run, kw = _setup_env(db_session, make_workspace, make_scoring_run, make_keyword)
    monkeypatch.setattr(settings, "ENABLE_SOCIAL_LEGACY_BULK", True)

    def fake_pipeline(self, request):
        return self._empty_response(request.scoring_run_id, policy_warnings=[])

    monkeypatch.setattr(
        "app.generators.social.social_generator.SocialGenerator.generate_full_pipeline",
        fake_pipeline,
    )

    resp = client.post(
        "/api/v1/generation/social/bulk",
        params={"brand_profile_id": ws.id},
        json={"scoring_run_id": run.id, "brand_name": "M"},
    )

    assert resp.status_code != 410
    assert resp.status_code == 200


# ==================== 10. AI_STAGES kaydı ====================


def test_ai_stages_registers_brief_stages():
    assert "social_brief_categories" in AI_STAGES
    assert "social_brief_ideas" in AI_STAGES
    assert "social_brief_contents" in AI_STAGES
