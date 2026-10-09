# -*- coding: utf-8 -*-
"""Integration tests for workspace-scoped SocialBrief GET and list endpoints (F1-D.6).

Tüm testler gerçek PostgreSQL test DB'sini ve FastAPI TestClient'ını kullanır.
Workspace izolasyonu, eager loading, 0-SQL serializer sözleşmesi, HTTP hata
eşlemeleri ve feature flag davranışlarını uçtan uca doğrular.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import inspect
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import event, select

from app.api.v1.generation import (
    create_social_brief_endpoint,
    get_social_brief_endpoint,
    list_social_briefs_endpoint,
)
from app.config import settings
from app.core.social import (
    SocialBriefNotFoundError,
    SocialBriefRunNotFoundError,
    get_social_brief,
    list_social_briefs,
    social_brief_to_response,
)
from app.database.models import (
    ChannelPool,
    SocialBrief,
    SocialBriefKeyword,
    SocialBriefTarget,
    SocialGenerationAttempt,
)


@pytest.fixture
def enable_flag(monkeypatch):
    """Test süresince feature flag'i aktif kılar, test bitiminde otomatik geri alır."""
    monkeypatch.setattr(settings, "ENABLE_SOCIAL_BRIEF_FLOW", True)
    yield


@pytest.fixture(autouse=True)
def bind_dependencies_get_db(db_session):
    """FastAPI TestClient'ın app.dependencies.get_db çağrılarını test db_session'ına bağlar."""
    from app.dependencies import get_db
    from app.main import app

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


def _setup_fresh_environment(
    db_session,
    make_workspace,
    make_scoring_run,
    make_keyword,
    num_kws: int = 3,
    workspace_name: str = "Read API Brand",
):
    """Merkezi freshness sözleşmesini karşılayan taze bir workspace/run/pool kurgusu oluşturur."""
    workspace = make_workspace(name=workspace_name, status="confirmed")
    run = make_scoring_run(
        brand_profile_id=workspace.id,
        status="completed",
        algorithm_version="v3",
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
    db_session,
    run_id: int,
    keywords: list,
    is_stale: bool = False,
    created_at: datetime | None = None,
    brand_name: str = "Read Brand",
    brand_context: str = "Read Context",
) -> SocialBrief:
    """Test için tam bağlı SocialBrief, brief_keywords ve targets kayıtları oluşturur."""
    brief = SocialBrief(
        scoring_run_id=run_id,
        brand_name_snapshot=brand_name,
        brand_context_snapshot=brand_context,
        channel_assignment_version=1,
        format_matrix_version="v1",
        is_stale=is_stale,
    )
    if created_at is not None:
        brief.created_at = created_at
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

    t1 = SocialBriefTarget(
        brief_id=brief.id,
        platform="instagram",
        content_format="post",
        duration_preset_id=None,
        duration_min_sec=None,
        duration_max_sec=None,
    )
    t2 = SocialBriefTarget(
        brief_id=brief.id,
        platform="instagram",
        content_format="reels",
        duration_preset_id="short_16_30",
        duration_min_sec=16,
        duration_max_sec=30,
    )
    db_session.add_all([t1, t2])
    db_session.commit()
    db_session.refresh(brief)
    return brief


# ==================== ENTEGRASYON TESTLERİ (1 - 36) ====================

def test_01_valid_brief_id_returns_200(
    client, db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """1. Geçerli brief ID HTTP 200 döner."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)

    r = client.get(f"/api/v1/generation/social/briefs/{brief.id}", params={"brand_profile_id": ws.id})
    assert r.status_code == 200
    assert r.json()["id"] == brief.id


def test_02_response_contains_all_parent_fields_complete(
    client, db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """2. Response parent alanlarını eksiksiz taşır."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws, brand_name="Özel Marka", brand_context="Özel Bağlam")

    r = client.get(f"/api/v1/generation/social/briefs/{brief.id}", params={"brand_profile_id": ws.id})
    assert r.status_code == 200
    data = r.json()

    assert data["id"] == brief.id
    assert data["scoring_run_id"] == run.id
    assert data["brand_name_snapshot"] == "Özel Marka"
    assert data["brand_context_snapshot"] == "Özel Bağlam"
    assert data["channel_assignment_version"] == 1
    assert data["format_matrix_version"] == "v1"
    assert data["locked_at"] is None
    assert data["is_stale"] is False
    assert "created_at" in data
    assert len(data["keywords"]) == len(kws)
    assert len(data["targets"]) == 2


def test_03_response_keywords_ordered_by_position(
    client, db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """3. Response keyword'leri position sırasındadır."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword, num_kws=3)
    brief = _create_test_brief(db_session, run.id, kws)

    r = client.get(f"/api/v1/generation/social/briefs/{brief.id}", params={"brand_profile_id": ws.id})
    assert r.status_code == 200
    keywords = r.json()["keywords"]
    positions = [k["position"] for k in keywords]
    assert positions == sorted(positions)
    assert positions == [0, 1, 2]


def test_04_response_targets_ordered_by_id(
    client, db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """4. Response target'ları ID sırasındadır."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)

    r = client.get(f"/api/v1/generation/social/briefs/{brief.id}", params={"brand_profile_id": ws.id})
    assert r.status_code == 200
    targets = r.json()["targets"]
    target_ids = [t["id"] for t in targets]
    assert target_ids == sorted(target_ids)


def test_05_video_and_non_video_duration_fields_return_correctly(
    client, db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """5. Video/non-video duration alanları doğru döner."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)

    r = client.get(f"/api/v1/generation/social/briefs/{brief.id}", params={"brand_profile_id": ws.id})
    assert r.status_code == 200
    targets = r.json()["targets"]

    post_tgt = next(t for t in targets if t["content_format"] == "post")
    reels_tgt = next(t for t in targets if t["content_format"] == "reels")

    assert post_tgt["duration_preset_id"] is None
    assert post_tgt["duration_min_sec"] is None
    assert post_tgt["duration_max_sec"] is None

    assert reels_tgt["duration_preset_id"] == "short_16_30"
    assert reels_tgt["duration_min_sec"] == 16
    assert reels_tgt["duration_max_sec"] == 30


def test_06_stale_brief_readable_by_id_and_returns_is_stale_true(
    client, db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """6. Stale brief ID ile okunabilir ve is_stale=true döner."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws, is_stale=True)

    r = client.get(f"/api/v1/generation/social/briefs/{brief.id}", params={"brand_profile_id": ws.id})
    assert r.status_code == 200
    data = r.json()
    assert data["id"] == brief.id
    assert data["is_stale"] is True


def test_07_non_existent_brief_returns_404_brief_not_found(
    client, db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """7. Olmayan brief HTTP 404 BRIEF_NOT_FOUND döner."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)

    r = client.get("/api/v1/generation/social/briefs/999999", params={"brand_profile_id": ws.id})
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "BRIEF_NOT_FOUND"
    assert r.json()["detail"]["message"] == "Social brief not found."


def test_08_other_workspace_brief_returns_404_brief_not_found(
    client, db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """8. Başka workspace'in brief'i HTTP 404 BRIEF_NOT_FOUND döner (varlık sızdırılmaz)."""
    ws1, run1, kws1 = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword, workspace_name="WS1")
    ws2, run2, kws2 = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword, workspace_name="WS2")
    brief1 = _create_test_brief(db_session, run1.id, kws1)

    # WS2 kimliği ile WS1'in brief'ini oku
    r = client.get(f"/api/v1/generation/social/briefs/{brief1.id}", params={"brand_profile_id": ws2.id})
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "BRIEF_NOT_FOUND"


def test_09_archived_workspace_brief_returns_404(
    client, db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """9. Arşivlenmiş (deleted_at dolu) workspace altındaki brief HTTP 404 döner."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)

    # Soft delete workspace
    ws.deleted_at = datetime.now(timezone.utc)
    db_session.commit()

    r = client.get(f"/api/v1/generation/social/briefs/{brief.id}", params={"brand_profile_id": ws.id})
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "BRIEF_NOT_FOUND"


def test_10_missing_brand_profile_id_returns_422(
    client, db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """10. brand_profile_id eksikse HTTP 422 döner."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)

    r = client.get(f"/api/v1/generation/social/briefs/{brief.id}")
    assert r.status_code == 422


def test_11_valid_run_list_returns_200(
    client, db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """11. Geçerli run listesi HTTP 200 döner."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    _create_test_brief(db_session, run.id, kws)

    r = client.get(
        "/api/v1/generation/social/briefs",
        params={"scoring_run_id": run.id, "brand_profile_id": ws.id},
    )
    assert r.status_code == 200
    assert isinstance(r.json(), list)
    assert len(r.json()) == 1


def test_12_list_only_contains_briefs_from_requested_run(
    client, db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """12. Liste yalnız istenen run'ın brief'lerini içerir."""
    ws, run1, kws1 = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword, workspace_name="Run1 WS")
    run2 = make_scoring_run(
        brand_profile_id=ws.id,
        status="completed",
        algorithm_version="v3",
        channel_pool_policy_version=1,
        relevance_anchor_version=1,
        channel_assignment_version=1,
        skip_relevance=True,
    )

    brief1 = _create_test_brief(db_session, run1.id, kws1)
    brief2 = _create_test_brief(db_session, run2.id, kws1)

    r = client.get(
        "/api/v1/generation/social/briefs",
        params={"scoring_run_id": run1.id, "brand_profile_id": ws.id},
    )
    assert r.status_code == 200
    brief_ids = [b["id"] for b in r.json()]
    assert brief1.id in brief_ids
    assert brief2.id not in brief_ids


def test_13_list_only_contains_briefs_from_requested_workspace(
    client, db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """13. Liste yalnız istenen workspace'in run'ını içerir."""
    ws1, run1, kws1 = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword, workspace_name="W1")
    ws2, run2, kws2 = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword, workspace_name="W2")
    _create_test_brief(db_session, run1.id, kws1)
    _create_test_brief(db_session, run2.id, kws2)

    r = client.get(
        "/api/v1/generation/social/briefs",
        params={"scoring_run_id": run1.id, "brand_profile_id": ws1.id},
    )
    assert r.status_code == 200
    assert len(r.json()) == 1
    assert r.json()[0]["scoring_run_id"] == run1.id


def test_14_list_ordered_by_created_at_desc_id_desc(
    client, db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """14. Liste created_at DESC, id DESC sırasındadır."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)

    base_time = datetime.now(timezone.utc) - timedelta(hours=5)
    b1 = _create_test_brief(db_session, run.id, kws, created_at=base_time)
    b2 = _create_test_brief(db_session, run.id, kws, created_at=base_time + timedelta(hours=1))
    b3 = _create_test_brief(db_session, run.id, kws, created_at=base_time + timedelta(hours=2))

    r = client.get(
        "/api/v1/generation/social/briefs",
        params={"scoring_run_id": run.id, "brand_profile_id": ws.id},
    )
    assert r.status_code == 200
    returned_ids = [b["id"] for b in r.json()]
    assert returned_ids == [b3.id, b2.id, b1.id]


def test_15_list_includes_stale_and_non_stale_briefs_together(
    client, db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """15. Liste stale ve non-stale brief'leri birlikte içerir."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    b_stale = _create_test_brief(db_session, run.id, kws, is_stale=True)
    b_fresh = _create_test_brief(db_session, run.id, kws, is_stale=False)

    r = client.get(
        "/api/v1/generation/social/briefs",
        params={"scoring_run_id": run.id, "brand_profile_id": ws.id},
    )
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 2
    stale_flags = {b["id"]: b["is_stale"] for b in data}
    assert stale_flags[b_stale.id] is True
    assert stale_flags[b_fresh.id] is False


def test_16_valid_run_without_briefs_returns_empty_list_200(
    client, db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """16. Geçerli fakat briefsiz run HTTP 200 ve boş liste ([]) döner."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)

    r = client.get(
        "/api/v1/generation/social/briefs",
        params={"scoring_run_id": run.id, "brand_profile_id": ws.id},
    )
    assert r.status_code == 200
    assert r.json() == []


def test_17_non_existent_run_returns_404_run_not_found(
    client, db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """17. Olmayan run HTTP 404 RUN_NOT_FOUND döner."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)

    r = client.get(
        "/api/v1/generation/social/briefs",
        params={"scoring_run_id": 999999, "brand_profile_id": ws.id},
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "RUN_NOT_FOUND"
    assert r.json()["detail"]["message"] == "Scoring run not found."


def test_18_other_workspace_run_returns_404_run_not_found(
    client, db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """18. Başka workspace'in run'ı HTTP 404 RUN_NOT_FOUND döner (varlık sızdırılmaz)."""
    ws1, run1, kws1 = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword, workspace_name="W1")
    ws2, run2, kws2 = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword, workspace_name="W2")

    r = client.get(
        "/api/v1/generation/social/briefs",
        params={"scoring_run_id": run1.id, "brand_profile_id": ws2.id},
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "RUN_NOT_FOUND"


def test_19_archived_workspace_run_returns_404_run_not_found(
    client, db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """19. Arşivlenmiş workspace altındaki run HTTP 404 RUN_NOT_FOUND döner."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    ws.deleted_at = datetime.now(timezone.utc)
    db_session.commit()

    r = client.get(
        "/api/v1/generation/social/briefs",
        params={"scoring_run_id": run.id, "brand_profile_id": ws.id},
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "RUN_NOT_FOUND"


def test_20_missing_scoring_run_id_returns_422(
    client, db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """20. scoring_run_id eksikse HTTP 422 döner."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)

    r = client.get(
        "/api/v1/generation/social/briefs",
        params={"brand_profile_id": ws.id},
    )
    assert r.status_code == 422


def test_21_feature_flag_disabled_returns_404_for_both_get_endpoints(
    client, db_session, make_workspace, make_scoring_run, make_keyword, monkeypatch
):
    """21. Feature flag kapalıyken iki GET de HTTP 404 FEATURE_DISABLED döner."""
    monkeypatch.setattr(settings, "ENABLE_SOCIAL_BRIEF_FLOW", False)
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)

    # 1. Single GET
    r1 = client.get(f"/api/v1/generation/social/briefs/{brief.id}", params={"brand_profile_id": ws.id})
    assert r1.status_code == 404
    assert r1.json()["detail"]["code"] == "FEATURE_DISABLED"

    # 2. List GET
    r2 = client.get(
        "/api/v1/generation/social/briefs",
        params={"scoring_run_id": run.id, "brand_profile_id": ws.id},
    )
    assert r2.status_code == 404
    assert r2.json()["detail"]["code"] == "FEATURE_DISABLED"


def test_22_flag_disabled_calls_zero_read_services(
    client, db_session, make_workspace, make_scoring_run, make_keyword, monkeypatch
):
    """22. Flag kapalıyken read servisleri çağrılmaz."""
    monkeypatch.setattr(settings, "ENABLE_SOCIAL_BRIEF_FLOW", False)
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)

    with patch("app.api.v1.generation.get_social_brief") as mock_get, patch(
        "app.api.v1.generation.list_social_briefs"
    ) as mock_list:
        client.get("/api/v1/generation/social/briefs/123", params={"brand_profile_id": ws.id})
        client.get("/api/v1/generation/social/briefs", params={"scoring_run_id": run.id, "brand_profile_id": ws.id})

    assert mock_get.call_count == 0
    assert mock_list.call_count == 0


def test_23_format_matrix_returns_200_when_flag_disabled(client, monkeypatch):
    """23. Format-matrix endpoint'i flag kapalıyken de HTTP 200 döner."""
    monkeypatch.setattr(settings, "ENABLE_SOCIAL_BRIEF_FLOW", False)
    r = client.get("/api/v1/generation/social/format-matrix")
    assert r.status_code == 200
    assert r.json()["version"] == "v1"


def test_24_post_brief_endpoint_continues_to_work(
    client, db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """24. POST brief endpoint'i çalışmaya devam eder (regresyon yok)."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    payload = {
        "scoring_run_id": run.id,
        "keyword_ids": [kws[0].id],
        "targets": [{"platform": "instagram", "content_format": "post"}],
        "brand_name": "API Brand",
    }
    r = client.post("/api/v1/generation/social/briefs", params={"brand_profile_id": ws.id}, json=payload)
    assert r.status_code == 201


def test_25_list_briefs_does_not_fall_into_dynamic_scoring_run_route(
    client, db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """25. /social/briefs dinamik /social/{scoring_run_id} rotasına düşmez."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    _create_test_brief(db_session, run.id, kws)

    # Calling GET /social/briefs
    r = client.get(
        "/api/v1/generation/social/briefs",
        params={"scoring_run_id": run.id, "brand_profile_id": ws.id},
    )
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_26_single_brief_hits_correct_endpoint(
    client, db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """26. /social/briefs/{brief_id} doğru tekil brief endpoint'ine düşer."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)

    r = client.get(f"/api/v1/generation/social/briefs/{brief.id}", params={"brand_profile_id": ws.id})
    assert r.status_code == 200
    assert r.json()["id"] == brief.id


def test_27_read_services_do_not_commit_or_rollback(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """27. Read servisleri (get/list) commit veya rollback yapmaz."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)

    with patch.object(db_session, "commit") as mock_c, patch.object(db_session, "rollback") as mock_r:
        b = get_social_brief(db_session, brief_id=brief.id, brand_profile_id=ws.id)
        assert b.id == brief.id
        assert mock_c.call_count == 0
        assert mock_r.call_count == 0

    with patch.object(db_session, "commit") as mock_c, patch.object(db_session, "rollback") as mock_r:
        items = list_social_briefs(db_session, scoring_run_id=run.id, brand_profile_id=ws.id)
        assert len(items) == 1
        assert mock_c.call_count == 0
        assert mock_r.call_count == 0


def test_28_successful_get_endpoints_do_not_commit(
    client, db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """28. Başarılı endpoint GET işlemleri commit yapmaz."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)

    with patch.object(db_session, "commit") as mock_c:
        r1 = client.get(f"/api/v1/generation/social/briefs/{brief.id}", params={"brand_profile_id": ws.id})
        assert r1.status_code == 200
        assert mock_c.call_count == 0

    with patch.object(db_session, "commit") as mock_c:
        r2 = client.get(
            "/api/v1/generation/social/briefs",
            params={"scoring_run_id": run.id, "brand_profile_id": ws.id},
        )
        assert r2.status_code == 200
        assert mock_c.call_count == 0


def test_29_serializer_does_not_emit_sql_on_eager_loaded_brief(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """29. Serializer eager-loaded nesnelerde ek SQL üretmez."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)

    # get_social_brief ile eager load et
    loaded_brief = get_social_brief(db_session, brief_id=brief.id, brand_profile_id=ws.id)

    # SQL listener ile serializer çalışırken sorgu atılmadığını doğrula
    queries = []

    def _query_catcher(conn, cursor, statement, parameters, context, executemany):
        queries.append(statement)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", _query_catcher)
    try:
        resp = social_brief_to_response(loaded_brief)
        assert resp.id == brief.id
        assert len(queries) == 0, f"Serializer SQL sorgusu üretti: {queries}"
    finally:
        event.remove(engine, "before_cursor_execute", _query_catcher)


def test_30_list_serializer_loop_does_not_produce_n_plus_one_queries(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """30. Liste serializer döngüsü N+1 sorgu üretmez."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    # 5 adet brief oluştur
    for i in range(5):
        _create_test_brief(db_session, run.id, kws, brand_name=f"Brief {i}")

    # list_social_briefs ile eager load et
    briefs = list_social_briefs(db_session, scoring_run_id=run.id, brand_profile_id=ws.id)
    assert len(briefs) == 5

    # Serializer döngüsü sırasında sıfır sorgu
    queries = []

    def _query_catcher(conn, cursor, statement, parameters, context, executemany):
        queries.append(statement)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", _query_catcher)
    try:
        responses = [social_brief_to_response(b) for b in briefs]
        assert len(responses) == 5
        assert len(queries) == 0, f"Liste serializer N+1 sorgu üretti: {queries}"
    finally:
        event.remove(engine, "before_cursor_execute", _query_catcher)


def test_31_endpoints_do_not_depend_on_get_ai():
    """31. Endpoint'ler get_ai dependency'sini almaz ve çağırmaz."""
    sig_get = inspect.signature(get_social_brief_endpoint)
    sig_list = inspect.signature(list_social_briefs_endpoint)

    assert "ai" not in sig_get.parameters
    assert "get_ai" not in str(sig_get)

    assert "ai" not in sig_list.parameters
    assert "get_ai" not in str(sig_list)


def test_32_unexpected_service_error_produces_safe_500_and_rollback(
    client, db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """32. Beklenmeyen servis hatası safe 500 ve rollback üretir."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)

    with patch("app.api.v1.generation.get_social_brief", side_effect=RuntimeError("Secret DB Failure")):
        r1 = client.get(f"/api/v1/generation/social/briefs/{brief.id}", params={"brand_profile_id": ws.id})
    assert r1.status_code == 500
    assert "Secret DB Failure" not in r1.text

    with patch("app.api.v1.generation.list_social_briefs", side_effect=RuntimeError("Secret List Failure")):
        r2 = client.get(
            "/api/v1/generation/social/briefs",
            params={"scoring_run_id": run.id, "brand_profile_id": ws.id},
        )
    assert r2.status_code == 500
    assert "Secret List Failure" not in r2.text


def test_33_corrupted_serializer_error_produces_safe_500_and_rollback(
    client, db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """33. Bozuk/null keyword_id serializer hatası safe 500 ve rollback üretir."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)

    # Serializer'ı bozan mock
    with patch("app.api.v1.generation.social_brief_to_response", side_effect=ValueError("Bozuk veri")):
        r = client.get(f"/api/v1/generation/social/briefs/{brief.id}", params={"brand_profile_id": ws.id})

    assert r.status_code == 500
    assert "Bozuk veri" not in r.text


def test_34_read_operations_do_not_mutate_brief_or_child_tables(
    client, db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """34. Read işlemleri SocialBrief veya child tablolarını değiştirmez."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)

    initial_locked_at = brief.locked_at
    initial_stale = brief.is_stale

    client.get(f"/api/v1/generation/social/briefs/{brief.id}", params={"brand_profile_id": ws.id})
    client.get("/api/v1/generation/social/briefs", params={"scoring_run_id": run.id, "brand_profile_id": ws.id})

    db_session.expire_all()
    reloaded = db_session.get(SocialBrief, brief.id)
    assert reloaded.locked_at == initial_locked_at
    assert reloaded.is_stale == initial_stale
    assert len(reloaded.brief_keywords) == len(kws)
    assert len(reloaded.targets) == 2


def test_35_attempt_status_heartbeat_lease_fields_are_untouched(
    client, db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """35. Attempt kayıtlarının status/heartbeat/lease alanlarına dokunulmaz."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)

    now = datetime.now(timezone.utc)
    attempt = SocialGenerationAttempt(
        brief_id=brief.id,
        stage="categories",
        idempotency_key="read-test-key-1",
        status="running",
        task_id="celery-task-read-test",
        heartbeat_at=now,
        lease_expires_at=now + timedelta(seconds=120),
    )
    db_session.add(attempt)
    db_session.commit()

    initial_status = attempt.status
    initial_heartbeat = attempt.heartbeat_at
    initial_lease = attempt.lease_expires_at

    client.get(f"/api/v1/generation/social/briefs/{brief.id}", params={"brand_profile_id": ws.id})
    client.get("/api/v1/generation/social/briefs", params={"scoring_run_id": run.id, "brand_profile_id": ws.id})

    db_session.expire_all()
    reloaded_attempt = db_session.get(SocialGenerationAttempt, attempt.id)
    assert reloaded_attempt.status == initial_status
    assert reloaded_attempt.heartbeat_at == initial_heartbeat
    assert reloaded_attempt.lease_expires_at == initial_lease


def test_36_no_real_ai_calls_in_endpoints(
    client, db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """36. Read endpoint'lerinde hiçbir gerçek AI çağrısı yapılmaz."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword)
    brief = _create_test_brief(db_session, run.id, kws)

    with patch("app.generators.ai_service.AIService.complete", side_effect=RuntimeError("AI must not be called")), patch(
        "app.generators.ai_service.AIService.complete_json", side_effect=RuntimeError("AI must not be called")
    ):
        r1 = client.get(f"/api/v1/generation/social/briefs/{brief.id}", params={"brand_profile_id": ws.id})
        assert r1.status_code == 200
        r2 = client.get("/api/v1/generation/social/briefs", params={"scoring_run_id": run.id, "brand_profile_id": ws.id})
        assert r2.status_code == 200

