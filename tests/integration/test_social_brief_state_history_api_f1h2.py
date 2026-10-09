# -*- coding: utf-8 -*-
"""Entegrasyon: brief durum özeti + workspace sosyal içerik geçmişi (F1-H.2 / K13).

- GET /generation/social/briefs/{id}/state — kategori sonucunun DB'den geri
  gelmesi (başka oturum), attempt keşfi, sızıntı yok, workspace izolasyonu.
- GET /generation/social/contents/history — sayfalama, deterministik sıra,
  legacy + brief'li içerik birlikte, stale işaretli, workspace izolasyonu,
  sorgu sayısı içerik adediyle büyümez.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event

from app.config import settings
from app.database.models import (
    ChannelPool,
    SocialBrief,
    SocialBriefKeyword,
    SocialBriefTarget,
    SocialCategory,
    SocialContent,
    SocialGenerationAttempt,
    SocialIdea,
)

T0 = datetime(2026, 9, 27, 9, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def enable_flag(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_SOCIAL_BRIEF_FLOW", True)
    yield


def _base(db_session, make_workspace, make_scoring_run, make_keyword, name="WS State"):
    ws = make_workspace(name=name, status="confirmed")
    run = make_scoring_run(
        brand_profile_id=ws.id,
        name=f"{name} run",
        status="completed",
        algorithm_version="v2",
        channel_assignment_version=1,
        skip_relevance=True,
    )
    kw = make_keyword(text_value=f"{name} kelime", brand_profile_id=ws.id)
    db_session.add(
        ChannelPool(
            scoring_run_id=run.id, keyword_id=kw.id, channel="SOCIAL",
            final_rank=1, relevance_score=0.9, adjusted_score=20.0,
        )
    )
    db_session.commit()
    return ws, run, kw


def _brief(db_session, run, kw, *, fmt="post", platform="instagram", stale=False):
    brief = SocialBrief(
        scoring_run_id=run.id, brand_name_snapshot="Marka",
        channel_assignment_version=1, format_matrix_version="v1",
        is_stale=stale, locked_at=T0,
    )
    db_session.add(brief)
    db_session.flush()
    db_session.add(SocialBriefKeyword(
        brief_id=brief.id, keyword_id=kw.id, keyword_snapshot=kw.keyword, position=0,
    ))
    target_kwargs = {}
    if fmt in ("reels", "short", "video"):
        target_kwargs = dict(duration_preset_id="short_31_60", duration_min_sec=31, duration_max_sec=60)
    target = SocialBriefTarget(brief_id=brief.id, platform=platform, content_format=fmt, **target_kwargs)
    db_session.add(target)
    db_session.flush()
    return brief, target


def _categories(db_session, run, brief, kw, *, stale=False):
    cats = []
    for i, ctype in enumerate(("educational", "product_benefit")):
        c = SocialCategory(
            scoring_run_id=run.id, brief_id=brief.id, category_name=f"Kategori {i + 1}",
            category_type=ctype, description=f"Açıklama {i + 1}", is_stale=stale,
            relevance_score=0.8, suggested_keyword_ids=[kw.id],
        )
        db_session.add(c)
        cats.append(c)
    db_session.flush()
    return cats


def _attempt(db_session, brief, stage, status, **kw):
    a = SocialGenerationAttempt(
        brief_id=brief.id, stage=stage, idempotency_key=kw.pop("key", f"{stage}-{status}-{id(kw)}"),
        status=status, **kw,
    )
    db_session.add(a)
    db_session.flush()
    return a


def _idea(db_session, cat, kw, target, brief=None, title="Fikir", platform="instagram", fmt="post"):
    idea = SocialIdea(
        category_id=cat.id, keyword_id=kw.id, brief_id=brief.id if brief else None,
        brief_target_id=target.id if target else None, idea_title=title,
        idea_description="Açıklama", target_platform=platform, content_format=fmt,
        trend_alignment=0.5, is_stale=False,
    )
    db_session.add(idea)
    db_session.flush()
    return idea


def _content(db_session, idea, *, brief=None, created_at=None, stale=False, caption="Metin", **extra):
    c = SocialContent(
        idea_id=idea.id, brief_id=brief.id if brief else None, caption=caption,
        hooks=[{"text": "Kanca", "style": "question", "ab_score": None}],
        hashtags=["etiket"], is_stale=stale, **extra,
    )
    if created_at is not None:
        c.created_at = created_at
    db_session.add(c)
    db_session.flush()
    return c


# ==================== BRIEF STATE ====================


def test_state_feature_flag_off_returns_404(client, db_session, make_workspace, make_scoring_run, make_keyword, monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_SOCIAL_BRIEF_FLOW", False)
    ws, run, kw = _base(db_session, make_workspace, make_scoring_run, make_keyword)
    brief, _ = _brief(db_session, run, kw)
    db_session.commit()
    r = client.get(f"/api/v1/generation/social/briefs/{brief.id}/state", params={"brand_profile_id": ws.id})
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "FEATURE_DISABLED"


def test_state_restores_completed_categories_from_db(client, enable_flag, db_session, make_workspace, make_scoring_run, make_keyword):
    ws, run, kw = _base(db_session, make_workspace, make_scoring_run, make_keyword)
    brief, _ = _brief(db_session, run, kw)
    cats = _categories(db_session, run, brief, kw)
    _attempt(db_session, brief, "categories", "failed", key="c-old", reason_code="category_provider_error")
    done = _attempt(db_session, brief, "categories", "completed", key="c-new", coverage={"ai_calls_used": 1})
    db_session.commit()

    # İki bağımsız GET: tarayıcı önbelleği olmadan (başka cihaz/oturum) aynı sonuç.
    for _ in range(2):
        r = client.get(f"/api/v1/generation/social/briefs/{brief.id}/state", params={"brand_profile_id": ws.id})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["category_attempt"]["id"] == done.id
        assert body["category_attempt"]["status"] == "completed"
        assert [c["id"] for c in body["categories"]] == [c.id for c in cats]
        assert body["categories"][0]["suggested_keyword_ids"] == [kw.id]


def test_state_cross_workspace_is_404(client, enable_flag, db_session, make_workspace, make_scoring_run, make_keyword):
    ws, run, kw = _base(db_session, make_workspace, make_scoring_run, make_keyword)
    other, _, _ = _base(db_session, make_workspace, make_scoring_run, make_keyword, name="Baska WS")
    brief, _ = _brief(db_session, run, kw)
    db_session.commit()
    r = client.get(f"/api/v1/generation/social/briefs/{brief.id}/state", params={"brand_profile_id": other.id})
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "BRIEF_NOT_FOUND"


def test_state_attempt_discovery_ordering_and_no_leak(client, enable_flag, db_session, make_workspace, make_scoring_run, make_keyword):
    ws, run, kw = _base(db_session, make_workspace, make_scoring_run, make_keyword)
    brief, target = _brief(db_session, run, kw)
    cats = _categories(db_session, run, brief, kw)
    _attempt(db_session, brief, "categories", "completed", key="c1", coverage={"ai_calls_used": 1})
    _attempt(db_session, brief, "ideas", "failed", key="i1", reason_code="idea_provider_error")
    ideas_new = _attempt(db_session, brief, "ideas", "partial", key="i2", task_id="celery-secret-task")
    r1 = _attempt(db_session, brief, "ideas_retry", "partial", key="r1")
    r2 = _attempt(db_session, brief, "ideas_retry", "completed", key="r2")
    idea = _idea(db_session, cats[0], kw, target, brief)
    c_attempt = _attempt(
        db_session, brief, "contents", "running", key="k1",
        requested_idea_ids=[idea.id],
        coverage={"schema_version": "contents_request_v1",
                  "request": {"idea_ids": [idea.id], "product_facts": "GIZLI-URUN-GERCEGI",
                              "trusted_brand_usp": "GIZLI-USP"}},
        lease_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        task_id="celery-content-task",
    )
    _content(db_session, idea, brief=brief)
    db_session.commit()

    r = client.get(f"/api/v1/generation/social/briefs/{brief.id}/state", params={"brand_profile_id": ws.id})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ideas_attempt"]["id"] == ideas_new.id
    assert body["ideas_attempt"]["status"] == "partial"
    assert [a["id"] for a in body["idea_retry_attempts"]] == [r2.id, r1.id]
    assert body["content_attempts"][0]["id"] == c_attempt.id
    assert body["content_attempts"][0]["requested_idea_ids"] == [idea.id]
    assert body["content_attempts"][0]["lease_expired"] is True
    assert body["content_idea_ids"] == [idea.id]
    raw = r.text
    for secret in ("GIZLI-URUN-GERCEGI", "GIZLI-USP", "celery-secret-task", "celery-content-task",
                   "product_facts", "trusted_brand_usp", "task_id", "lease_expires_at", "idempotency_key"):
        assert secret not in raw


def test_state_stale_brief_hides_categories_but_keeps_attempts(client, enable_flag, db_session, make_workspace, make_scoring_run, make_keyword):
    ws, run, kw = _base(db_session, make_workspace, make_scoring_run, make_keyword)
    brief, _ = _brief(db_session, run, kw, stale=True)
    _categories(db_session, run, brief, kw, stale=True)
    done = _attempt(db_session, brief, "categories", "completed", key="c1")
    db_session.commit()
    r = client.get(f"/api/v1/generation/social/briefs/{brief.id}/state", params={"brand_profile_id": ws.id})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["is_stale"] is True
    assert body["categories"] == []
    assert body["category_attempt"]["id"] == done.id


def test_state_excludes_stale_content_ideas(client, enable_flag, db_session, make_workspace, make_scoring_run, make_keyword):
    ws, run, kw = _base(db_session, make_workspace, make_scoring_run, make_keyword)
    brief, target = _brief(db_session, run, kw)
    cats = _categories(db_session, run, brief, kw)
    fresh = _idea(db_session, cats[0], kw, target, brief, title="Güncel")
    old = _idea(db_session, cats[1], kw, target, brief, title="Eski")
    _content(db_session, fresh, brief=brief)
    _content(db_session, old, brief=brief, stale=True)
    db_session.commit()
    body = client.get(f"/api/v1/generation/social/briefs/{brief.id}/state", params={"brand_profile_id": ws.id}).json()
    assert body["content_idea_ids"] == [fresh.id]


# ==================== CONTENT HISTORY ====================


def _history(client, ws_id, **params):
    return client.get("/api/v1/generation/social/contents/history", params={"brand_profile_id": ws_id, **params})


def test_history_lists_workspace_contents_across_runs_briefs_and_legacy(client, db_session, make_workspace, make_scoring_run, make_keyword):
    ws, run1, kw = _base(db_session, make_workspace, make_scoring_run, make_keyword, name="Hist A")
    run2 = make_scoring_run(brand_profile_id=ws.id, name="Hist A run 2", status="completed",
                            algorithm_version="v2", channel_assignment_version=1, skip_relevance=True)
    other_ws, other_run, other_kw = _base(db_session, make_workspace, make_scoring_run, make_keyword, name="Hist B")

    b1, t1 = _brief(db_session, run1, kw, fmt="reels")
    c1 = _categories(db_session, run1, b1, kw)
    i1 = _idea(db_session, c1[0], kw, t1, b1, title="Reels fikri", fmt="reels")
    _content(db_session, i1, brief=b1, created_at=T0, duration_status="mismatch", actual_duration_sec=75,
             format_payload={"kind": "video", "segments": [{"start_sec": 0, "end_sec": 75}]},
             validation_warnings=["voiceover_duration_mismatch"])

    b2, t2 = _brief(db_session, run2, kw)
    c2 = _categories(db_session, run2, b2, kw)
    i2 = _idea(db_session, c2[0], kw, t2, b2, title="Post fikri")
    _content(db_session, i2, brief=b2, created_at=T0 + timedelta(hours=1))

    # Legacy (brief'siz) içerik, bozuk JSON alanlarıyla — istek düşmemeli.
    legacy_cat = SocialCategory(scoring_run_id=run1.id, category_name="Eski", category_type="educational",
                                description="x", relevance_score=0.5, suggested_keyword_ids=[kw.id])
    db_session.add(legacy_cat)
    db_session.flush()
    li = _idea(db_session, legacy_cat, kw, None, None, title="Legacy fikir")
    legacy = _content(db_session, li, created_at=T0 + timedelta(hours=2), stale=True)
    legacy.hooks = "bozuk"
    legacy.hashtags = {"x": 1}

    ob, ot = _brief(db_session, other_run, other_kw)
    oc = _categories(db_session, other_run, ob, other_kw)
    oi = _idea(db_session, oc[0], other_kw, ot, ob, title="BASKA WS FIKRI")
    _content(db_session, oi, brief=ob, created_at=T0 + timedelta(hours=3), caption="BASKA WS ICERIGI")
    db_session.commit()

    r = _history(client, ws.id)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 3
    titles = [it["idea_title"] for it in body["items"]]
    assert titles == ["Legacy fikir", "Post fikri", "Reels fikri"]
    assert "BASKA WS" not in r.text
    legacy_item, post_item, reels_item = body["items"]
    assert legacy_item["brief_id"] is None and legacy_item["is_stale"] is True
    assert legacy_item["hooks"] == [] and legacy_item["hashtags"] == []
    assert post_item["scoring_run_id"] == run2.id and post_item["run_name"] == "Hist A run 2"
    assert reels_item["duration_status"] == "mismatch"
    assert reels_item["actual_duration_sec"] == 75
    assert (reels_item["duration_min_sec"], reels_item["duration_max_sec"]) == (31, 60)
    assert reels_item["format_payload"]["kind"] == "video"
    assert reels_item["validation_warnings"] == ["voiceover_duration_mismatch"]
    assert reels_item["keyword"] == kw.keyword


def test_history_pagination_and_tie_break_by_id(client, db_session, make_workspace, make_scoring_run, make_keyword):
    ws, run, kw = _base(db_session, make_workspace, make_scoring_run, make_keyword, name="Hist Page")
    brief, target = _brief(db_session, run, kw)
    cats = _categories(db_session, run, brief, kw)
    ids = []
    for i in range(5):
        idea = _idea(db_session, cats[i % 2], kw, target, brief, title=f"F{i}")
        ids.append(_content(db_session, idea, brief=brief, created_at=T0).id)  # eşit zaman
    db_session.commit()
    expected = sorted(ids, reverse=True)

    p1 = _history(client, ws.id, limit=2, offset=0).json()
    p2 = _history(client, ws.id, limit=2, offset=2).json()
    p3 = _history(client, ws.id, limit=2, offset=4).json()
    assert [it["id"] for it in p1["items"] + p2["items"] + p3["items"]] == expected
    assert (p1["has_more"], p2["has_more"], p3["has_more"]) == (True, True, False)
    assert p1["total"] == 5

    only_brief = _history(client, ws.id, brief_id=brief.id).json()
    assert only_brief["total"] == 5


def test_history_limits_and_unknown_workspace(client, db_session, make_workspace, make_scoring_run, make_keyword):
    ws, _, _ = _base(db_session, make_workspace, make_scoring_run, make_keyword, name="Hist Lim")
    assert _history(client, ws.id, limit=51).status_code == 422
    assert _history(client, ws.id, limit=0).status_code == 422
    assert _history(client, ws.id, offset=-1).status_code == 422
    empty = _history(client, ws.id).json()
    assert empty["items"] == [] and empty["total"] == 0 and empty["has_more"] is False
    assert _history(client, 999999).status_code == 404


def test_history_query_count_does_not_grow_with_items(client, db_session, db_engine, make_workspace, make_scoring_run, make_keyword):
    ws, run, kw = _base(db_session, make_workspace, make_scoring_run, make_keyword, name="Hist N1")
    brief, target = _brief(db_session, run, kw)
    cats = _categories(db_session, run, brief, kw)

    def _count_for(limit):
        statements = []

        def _before(conn, cursor, statement, *args):
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement)

        event.listen(db_engine, "before_cursor_execute", _before)
        try:
            r = _history(client, ws.id, limit=limit)
            assert r.status_code == 200
        finally:
            event.remove(db_engine, "before_cursor_execute", _before)
        return len(statements), len(r.json()["items"])

    idea = _idea(db_session, cats[0], kw, target, brief, title="tek")
    _content(db_session, idea, brief=brief)
    db_session.commit()
    small_q, small_n = _count_for(50)

    for i in range(8):
        idea = _idea(db_session, cats[i % 2], kw, target, brief, title=f"çok {i}")
        _content(db_session, idea, brief=brief)
    db_session.commit()
    big_q, big_n = _count_for(50)

    assert (small_n, big_n) == (1, 9)
    assert big_q == small_q
