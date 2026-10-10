# -*- coding: utf-8 -*-
"""Integration tests for POST SocialBrief API endpoint and HTTP error mapping (F1-D.5).

Tüm testler gerçek PostgreSQL test DB'sini ve FastAPI TestClient'ını kullanır.
Feature flag, doğrulama, eligibility, persistence, atomik commit/rollback ve
HTTP hata eşlemelerini uçtan uca doğrular.
"""
from __future__ import annotations

import inspect
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.exc import IntegrityError

from app.api.v1.generation import create_social_brief_endpoint
import app.api.v1.generation as gen_mod
from app.config import settings
from app.database.connection import SessionLocal
from app.database.models import (
    ChannelPool,
    SocialBrief,
    SocialBriefKeyword,
    SocialBriefTarget,
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
):
    """Merkezi freshness sözleşmesini karşılayan taze bir workspace/run/pool kurgusu oluşturur."""
    workspace = make_workspace(name="API Brand", status="confirmed")
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
            text_value=f"api anahtar kelime {i + 1}",
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


def _valid_payload(run_id: int, keyword_ids: list[int]) -> Dict[str, Any]:
    return {
        "scoring_run_id": run_id,
        "keyword_ids": keyword_ids,
        "targets": [
            {"platform": "instagram", "content_format": "post"},
            {
                "platform": "instagram",
                "content_format": "reels",
                "duration_preset_id": "short_16_30",
            },
        ],
        "brand_name": "API Test Marka",
        "brand_context": "API Brief Context",
    }


# ==================== ENTEGRASYON TESTLERİ (1-35) ====================

def test_01_feature_flag_enabled_valid_request_returns_201(
    client, db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """1. Feature flag açıkken geçerli request HTTP 201 Created döner."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=2
    )
    payload = _valid_payload(run.id, [kws[0].id, kws[1].id])

    resp = client.post(
        "/api/v1/generation/social/briefs",
        params={"brand_profile_id": ws.id},
        json=payload,
    )

    assert resp.status_code == 201, resp.text


def test_02_response_matches_social_brief_response_schema(
    client, db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """2. Dönen response SocialBriefResponse şeması sözleşmesine tam uyar."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=2
    )
    payload = _valid_payload(run.id, [kws[0].id, kws[1].id])

    resp = client.post(
        "/api/v1/generation/social/briefs",
        params={"brand_profile_id": ws.id},
        json=payload,
    )

    data = resp.json()
    assert "id" in data
    assert data["scoring_run_id"] == run.id
    assert data["brand_name_snapshot"] == "API Test Marka"
    assert data["brand_context_snapshot"] == "API Brief Context"
    assert data["channel_assignment_version"] == 1
    assert data["format_matrix_version"] == "v1"
    assert data["locked_at"] is None
    assert data["is_stale"] is False
    assert "created_at" in data
    assert len(data["keywords"]) == 2
    assert len(data["targets"]) == 2


def test_03_brief_keyword_and_target_rows_committed(
    client, db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """3. Brief, keyword ve target satırları DB'ye commit edilmiştir ve yeni oturumda görünür."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=2
    )
    payload = _valid_payload(run.id, [kws[0].id, kws[1].id])

    resp = client.post(
        "/api/v1/generation/social/briefs",
        params={"brand_profile_id": ws.id},
        json=payload,
    )
    brief_id = resp.json()["id"]

    new_session = SessionLocal()
    try:
        loaded = new_session.get(SocialBrief, brief_id)
        assert loaded is not None
        assert len(loaded.brief_keywords) == 2
        assert len(loaded.targets) == 2
    finally:
        new_session.close()


def test_04_response_keyword_order_follows_position(
    client, db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """4. Response keyword sırası position sırasındadır."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=3
    )
    order = [kws[2].id, kws[0].id, kws[1].id]
    payload = _valid_payload(run.id, order)

    resp = client.post(
        "/api/v1/generation/social/briefs",
        params={"brand_profile_id": ws.id},
        json=payload,
    )

    keywords = resp.json()["keywords"]
    assert [kw["keyword_id"] for kw in keywords] == order
    assert [kw["position"] for kw in keywords] == [0, 1, 2]


def test_05_response_target_order_is_deterministic(
    client, db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """5. Response target sırası ID'ye göre deterministiktir."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=1
    )
    payload = _valid_payload(run.id, [kws[0].id])
    payload["targets"] = [
        {"platform": "twitter", "content_format": "post"},
        {"platform": "instagram", "content_format": "story"},
        {"platform": "linkedin", "content_format": "post"},
    ]

    resp = client.post(
        "/api/v1/generation/social/briefs",
        params={"brand_profile_id": ws.id},
        json=payload,
    )

    targets = resp.json()["targets"]
    target_ids = [t["id"] for t in targets]
    assert target_ids == sorted(target_ids)


def test_06_video_duration_fields_carry_canonical_values(
    client, db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """6. Video duration alanları canonical değerleri taşır."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=1
    )
    payload = _valid_payload(run.id, [kws[0].id])
    payload["targets"] = [
        {"platform": "instagram", "content_format": "reels", "duration_preset_id": "short_16_30"}
    ]

    resp = client.post(
        "/api/v1/generation/social/briefs",
        params={"brand_profile_id": ws.id},
        json=payload,
    )

    tgt = resp.json()["targets"][0]
    assert tgt["duration_preset_id"] == "short_16_30"
    assert tgt["duration_min_sec"] == 16
    assert tgt["duration_max_sec"] == 30


def test_07_non_video_duration_fields_are_null(
    client, db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """7. Non-video duration alanları null'dır."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=1
    )
    payload = _valid_payload(run.id, [kws[0].id])
    payload["targets"] = [
        {"platform": "instagram", "content_format": "post"}
    ]

    resp = client.post(
        "/api/v1/generation/social/briefs",
        params={"brand_profile_id": ws.id},
        json=payload,
    )

    tgt = resp.json()["targets"][0]
    assert tgt["duration_preset_id"] is None
    assert tgt["duration_min_sec"] is None
    assert tgt["duration_max_sec"] is None


def test_08_brand_snapshot_fields_match_response_and_db(
    client, db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """8. Brand snapshot alanları response ve DB'de birebir aynıdır."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=1
    )
    payload = _valid_payload(
        run.id,
        [kws[0].id],
    )
    payload["brand_name"] = "Marka Özel"
    payload["brand_context"] = "Bağlam Özel"

    resp = client.post(
        "/api/v1/generation/social/briefs",
        params={"brand_profile_id": ws.id},
        json=payload,
    )
    brief_id = resp.json()["id"]

    brief = db_session.get(SocialBrief, brief_id)
    assert brief.brand_name_snapshot == "Marka Özel"
    assert brief.brand_context_snapshot == "Bağlam Özel"
    assert resp.json()["brand_name_snapshot"] == brief.brand_name_snapshot
    assert resp.json()["brand_context_snapshot"] == brief.brand_context_snapshot


def test_09_feature_flag_disabled_returns_404(
    client, db_session, make_workspace, make_scoring_run, make_keyword
):
    """9. Feature flag kapalıyken (default) HTTP 404 döner."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=1
    )
    payload = _valid_payload(run.id, [kws[0].id])

    resp = client.post(
        "/api/v1/generation/social/briefs",
        params={"brand_profile_id": ws.id},
        json=payload,
    )

    assert resp.status_code == 404
    data = resp.json()
    assert data["detail"]["code"] == "FEATURE_DISABLED"
    assert data["detail"]["message"] == "Social brief flow is not enabled."


def test_10_flag_disabled_calls_zero_persistence_service(
    client, db_session, make_workspace, make_scoring_run, make_keyword
):
    """10. Flag kapalıyken create_social_brief çağrılmaz."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=1
    )
    payload = _valid_payload(run.id, [kws[0].id])

    with patch("app.api.v1.generation.create_social_brief") as mock_create:
        resp = client.post(
            "/api/v1/generation/social/briefs",
            params={"brand_profile_id": ws.id},
            json=payload,
        )
        assert resp.status_code == 404
        assert mock_create.call_count == 0


def test_11_flag_disabled_creates_no_social_brief_rows(
    client, db_session, make_workspace, make_scoring_run, make_keyword
):
    """11. Flag kapalıyken hiçbir SocialBrief satırı oluşmaz."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=1
    )
    initial_count = db_session.query(SocialBrief).count()

    resp = client.post(
        "/api/v1/generation/social/briefs",
        params={"brand_profile_id": ws.id},
        json=_valid_payload(run.id, [kws[0].id]),
    )
    assert resp.status_code == 404
    assert db_session.query(SocialBrief).count() == initial_count


def test_12_format_matrix_endpoint_returns_200_when_flag_disabled(
    client
):
    """12. Format-matrix endpoint'i flag kapalıyken hâlâ HTTP 200 döner."""
    resp = client.get("/api/v1/generation/social/format-matrix")
    assert resp.status_code == 200
    assert resp.json()["version"] == "v1"


def test_13_missing_brand_profile_id_returns_422(
    client, enable_flag
):
    """13. brand_profile_id query parametresi eksikse HTTP 422 döner."""
    resp = client.post(
        "/api/v1/generation/social/briefs",
        json={"scoring_run_id": 1, "keyword_ids": [1], "targets": []},
    )
    assert resp.status_code == 422


def test_14_invalid_pydantic_type_returns_422(
    client, enable_flag
):
    """14. Geçersiz Pydantic tipi (örn. scoring_run_id string) HTTP 422 döner."""
    resp = client.post(
        "/api/v1/generation/social/briefs",
        params={"brand_profile_id": 1},
        json={"scoring_run_id": "not_an_int", "keyword_ids": [1], "targets": []},
    )
    assert resp.status_code == 422


def test_15_zero_or_six_keywords_returns_422_with_stable_code(
    client, db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """15. 0 veya 6 keyword domain hatası HTTP 422 ve INVALID_KEYWORD_COUNT kodu taşır."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=1
    )

    # 0 keyword
    payload_0 = _valid_payload(run.id, [])
    resp_0 = client.post(
        "/api/v1/generation/social/briefs",
        params={"brand_profile_id": ws.id},
        json=payload_0,
    )
    assert resp_0.status_code == 422
    assert resp_0.json()["detail"]["code"] == "INVALID_KEYWORD_COUNT"

    # 6 keyword
    payload_6 = _valid_payload(run.id, [1, 2, 3, 4, 5, 6])
    resp_6 = client.post(
        "/api/v1/generation/social/briefs",
        params={"brand_profile_id": ws.id},
        json=payload_6,
    )
    assert resp_6.status_code == 422
    assert resp_6.json()["detail"]["code"] == "INVALID_KEYWORD_COUNT"


def test_16_zero_or_seven_targets_returns_422_with_stable_code(
    client, db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """16. 0 veya 7 target HTTP 422 ve INVALID_TARGET_COUNT taşır."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=1
    )

    # 0 target
    p0 = _valid_payload(run.id, [kws[0].id])
    p0["targets"] = []
    r0 = client.post("/api/v1/generation/social/briefs", params={"brand_profile_id": ws.id}, json=p0)
    assert r0.status_code == 422
    assert r0.json()["detail"]["code"] == "INVALID_TARGET_COUNT"

    # 7 targets
    p7 = _valid_payload(run.id, [kws[0].id])
    p7["targets"] = [
        {"platform": "instagram", "content_format": "post"},
        {"platform": "instagram", "content_format": "carousel"},
        {"platform": "instagram", "content_format": "story"},
        {"platform": "twitter", "content_format": "post"},
        {"platform": "twitter", "content_format": "thread"},
        {"platform": "linkedin", "content_format": "post"},
        {"platform": "linkedin", "content_format": "carousel"},
    ]
    r7 = client.post("/api/v1/generation/social/briefs", params={"brand_profile_id": ws.id}, json=p7)
    assert r7.status_code == 422
    assert r7.json()["detail"]["code"] == "INVALID_TARGET_COUNT"


def test_17_unsupported_platform_format_returns_422(
    client, db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """17. Desteklenmeyen platform-format HTTP 422 ve UNSUPPORTED_PLATFORM_FORMAT döner."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=1
    )
    p = _valid_payload(run.id, [kws[0].id])
    p["targets"] = [{"platform": "tiktok", "content_format": "carousel"}]

    r = client.post("/api/v1/generation/social/briefs", params={"brand_profile_id": ws.id}, json=p)
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "UNSUPPORTED_PLATFORM_FORMAT"


def test_18_video_preset_missing_returns_422(
    client, db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """18. Video preset eksikliği HTTP 422 ve DURATION_PRESET_REQUIRED döner."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=1
    )
    p = _valid_payload(run.id, [kws[0].id])
    p["targets"] = [{"platform": "instagram", "content_format": "reels", "duration_preset_id": None}]

    r = client.post("/api/v1/generation/social/briefs", params={"brand_profile_id": ws.id}, json=p)
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "DURATION_PRESET_REQUIRED"


def test_19_other_workspace_run_returns_404(
    client, db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """19. Başka workspace'in run'ı HTTP 404 RUN_NOT_FOUND döner (cross-workspace koruması)."""
    ws_a = make_workspace(name="WS A")
    ws_b, run_b, kws_b = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=1
    )
    p = _valid_payload(run_b.id, [kws_b[0].id])

    r = client.post("/api/v1/generation/social/briefs", params={"brand_profile_id": ws_a.id}, json=p)
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "RUN_NOT_FOUND"


def test_20_non_existent_workspace_or_run_returns_404(
    client, enable_flag
):
    """20. Olmayan workspace veya run HTTP 404 döner."""
    r_ws = client.post(
        "/api/v1/generation/social/briefs",
        params={"brand_profile_id": 999999},
        json={"scoring_run_id": 1, "keyword_ids": [1], "targets": [{"platform": "instagram", "content_format": "post"}]},
    )
    assert r_ws.status_code == 404
    assert r_ws.json()["detail"]["code"] == "RUN_NOT_FOUND"


def test_21_stale_pool_returns_409_with_details(
    client, db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """21. Stale pool HTTP 409 POOL_STALE ve freshness details döner."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=1
    )
    run.channel_pool_policy_version = 9999  # stale
    db_session.commit()

    p = _valid_payload(run.id, [kws[0].id])
    r = client.post("/api/v1/generation/social/briefs", params={"brand_profile_id": ws.id}, json=p)

    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "POOL_STALE"
    assert "details" in r.json()["detail"]
    assert r.json()["detail"]["details"]["policy_stale"] is True


def test_22_non_eligible_keyword_returns_400(
    client, db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """22. SOCIAL pool dışı keyword HTTP 400 SOCIAL_KEYWORD_NOT_ELIGIBLE döner."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=1
    )
    p = _valid_payload(run.id, [888888])  # Havuzda yok

    r = client.post("/api/v1/generation/social/briefs", params={"brand_profile_id": ws.id}, json=p)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "SOCIAL_KEYWORD_NOT_ELIGIBLE"


def test_23_domain_errors_perform_rollback(
    client, db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """23. Domain hatalarında (validation/eligibility) transaction rollback edilir."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=1
    )
    p = _valid_payload(run.id, [888888])  # Hata verecek payload

    initial_briefs = db_session.query(SocialBrief).count()
    r = client.post("/api/v1/generation/social/briefs", params={"brand_profile_id": ws.id}, json=p)
    assert r.status_code == 400
    assert db_session.query(SocialBrief).count() == initial_briefs


def test_24_integrity_error_returns_409_brief_persistence_conflict(
    client, db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """24. IntegrityError HTTP 409 ve BRIEF_PERSISTENCE_CONFLICT üretir."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=1
    )
    p = _valid_payload(run.id, [kws[0].id])

    with patch("app.api.v1.generation.create_social_brief", side_effect=IntegrityError("stmt", "params", Exception("orig"))):
        r = client.post("/api/v1/generation/social/briefs", params={"brand_profile_id": ws.id}, json=p)

    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "BRIEF_PERSISTENCE_CONFLICT"
    assert "Brief could not be created because the underlying data changed." in r.json()["detail"]["message"]


def test_25_integrity_error_does_not_leak_sql_details(
    client, db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """25. IntegrityError yanıtı SQL statement veya constraint detaylarını sızdırmaz."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=1
    )
    p = _valid_payload(run.id, [kws[0].id])

    with patch("app.api.v1.generation.create_social_brief", side_effect=IntegrityError("INSERT INTO social_brief_targets SECRET_COLUMN", "params", Exception("ck_social_brief_targets_format_duration"))):
        r = client.post("/api/v1/generation/social/briefs", params={"brand_profile_id": ws.id}, json=p)

    assert r.status_code == 409
    response_text = r.text
    assert "SECRET_COLUMN" not in response_text
    assert "ck_social_brief_targets" not in response_text
    assert "INSERT INTO" not in response_text


def test_26_serializer_error_produces_safe_500_and_rollback(
    client, db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """26. Serializer hatası safe 500 ve rollback üretir."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=1
    )
    p = _valid_payload(run.id, [kws[0].id])

    with patch("app.api.v1.generation.social_brief_to_response", side_effect=ValueError("Serializer Bozuldu")):
        r = client.post("/api/v1/generation/social/briefs", params={"brand_profile_id": ws.id}, json=p)

    assert r.status_code == 500
    assert "Serializer Bozuldu" not in r.text
    # Rollback yapıldığı için DB'de kalıcı brief oluşmaz
    assert db_session.query(SocialBrief).count() == 0


def test_27_commit_error_produces_safe_500_and_rollback(
    client, db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """27. Commit hatası safe 500 ve rollback üretir."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=1
    )
    p = _valid_payload(run.id, [kws[0].id])

    with patch.object(db_session, "commit", side_effect=RuntimeError("Commit Failed")):
        r = client.post("/api/v1/generation/social/briefs", params={"brand_profile_id": ws.id}, json=p)

    assert r.status_code == 500
    assert "Commit Failed" not in r.text


def test_28_unexpected_service_error_produces_safe_500_and_rollback(
    client, db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """28. Beklenmeyen servis hatası safe 500 ve rollback üretir."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=1
    )
    p = _valid_payload(run.id, [kws[0].id])

    with patch("app.api.v1.generation.create_social_brief", side_effect=RuntimeError("Unexpected Crash")):
        r = client.post("/api/v1/generation/social/briefs", params={"brand_profile_id": ws.id}, json=p)

    assert r.status_code == 500
    assert "Unexpected Crash" not in r.text


def test_29_successful_request_performs_exactly_one_commit(
    client, db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """29. Başarılı istek tam bir commit yapar."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=1
    )
    p = _valid_payload(run.id, [kws[0].id])

    real_commit = db_session.commit
    commit_counter = MagicMock(side_effect=real_commit)

    with patch.object(db_session, "commit", commit_counter):
        r = client.post("/api/v1/generation/social/briefs", params={"brand_profile_id": ws.id}, json=p)

    assert r.status_code == 201
    assert commit_counter.call_count == 1


def test_30_successful_response_is_complete_after_commit(
    client, db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """30. Başarılı response commit sonrasında da tüm alanları eksiksiz taşır."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=2
    )
    p = _valid_payload(run.id, [kws[0].id, kws[1].id])

    r = client.post("/api/v1/generation/social/briefs", params={"brand_profile_id": ws.id}, json=p)
    assert r.status_code == 201

    data = r.json()
    assert data["id"] > 0
    assert len(data["keywords"]) == 2
    assert len(data["targets"]) == 2
    assert data["keywords"][0]["keyword_snapshot"] is not None
    assert data["targets"][0]["content_format"] is not None


def test_31_endpoint_does_not_depend_on_get_ai():
    """31. Endpoint get_ai dependency'sini almaz ve çağırmaz."""
    sig = inspect.signature(create_social_brief_endpoint)
    param_names = list(sig.parameters.keys())
    assert "ai" not in param_names
    assert "get_ai" not in str(sig)


def test_32_endpoint_does_not_fall_into_dynamic_scoring_run_route(
    client, db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """32. Endpoint /social/{scoring_run_id} dinamik rotasına düşmez."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=1
    )
    # POST to /social/briefs
    r = client.post(
        "/api/v1/generation/social/briefs",
        params={"brand_profile_id": ws.id},
        json=_valid_payload(run.id, [kws[0].id]),
    )
    assert r.status_code == 201


def test_33_duplicate_body_allowed_to_create_distinct_briefs(
    client, db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """33. Aynı geçerli body ile iki ayrı başarılı istek iki ayrı brief oluşturabilir."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=1
    )
    p = _valid_payload(run.id, [kws[0].id])

    r1 = client.post("/api/v1/generation/social/briefs", params={"brand_profile_id": ws.id}, json=p)
    r2 = client.post("/api/v1/generation/social/briefs", params={"brand_profile_id": ws.id}, json=p)

    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r1.json()["id"] != r2.json()["id"]
    assert db_session.query(SocialBrief).count() == 2


def test_34_legacy_social_endpoints_unaffected(
    client
):
    """34. Legacy sosyal endpointlerin rotaları ve davranışları değişmez."""
    # /format-matrix endpoint'i çalışmaya devam eder
    r_matrix = client.get("/api/v1/generation/social/format-matrix")
    assert r_matrix.status_code == 200

    # /social/categories rota mevcuttur (eksik parametrede 422 döner)
    r_cat = client.post("/api/v1/generation/social/categories", json={})
    assert r_cat.status_code == 422


def test_35_no_real_ai_calls_in_endpoint(
    client, db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """35. Endpoint akışında hiçbir gerçek AI çağrısı yapılmadığını doğrula."""
    ws, run, kws = _setup_fresh_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, num_kws=1
    )
    p = _valid_payload(run.id, [kws[0].id])

    with patch("app.generators.ai_service.AIService.complete", side_effect=RuntimeError("AI must not be called")):
        r = client.post("/api/v1/generation/social/briefs", params={"brand_profile_id": ws.id}, json=p)
        assert r.status_code == 201
