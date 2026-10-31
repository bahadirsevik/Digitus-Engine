"""Pazar kopyasi: POST /workspaces/{id}/markets (plan_pazar_dil_secimi Faz 3).

Gercek test DB'si; AI/ag yok. Kopya, onayli markanin bagimsiz, kelimesiz, yeni pazarli
satiridir; mevcut pazarlar `skipped`, tumu mevcutsa 409.
"""
from __future__ import annotations

import copy

import pytest

from app.api.v1 import brand_profile as brand_profile_api
from app.database.models import BrandProfile, ScoringRun, WorkspaceKeyword

BASE = "/api/v1/brand-profile"
REAL_JOB = brand_profile_api._run_keyword_suggestions_for_copy
URL = "https://www.example.test"
DE, RU, UA, GB, US = "2276", "2643", "2804", "2826", "2840"
L_TR, L_RU, L_UK, L_EN, L_DE = "1037", "1031", "1036", "1000", "1001"
BERLIN = "1003854"

PROFILE = {
    "company_name": "Acme",
    "sector": "Danismanlik",
    "services": ["Hizmet A"],
    "anchor_texts": ["Hizmet A"],
    "location_filter_mode": "focus_only",
    "focus_cities": ["Ankara"],
    "location_exempt_terms": ["ankara bilgisayar"],
}


@pytest.fixture(autouse=True)
def scheduled(monkeypatch):
    """Arka plan isi her testte kaydedilir (gercek AI/crawl yok); gorev govdesi REAL_JOB ile."""
    calls = []
    monkeypatch.setattr(
        brand_profile_api, "_run_keyword_suggestions_for_copy", lambda **kw: calls.append(kw)
    )
    return calls


@pytest.fixture
def source(make_workspace):
    return make_workspace(
        name="Acme",
        company_url=URL,
        status="confirmed",
        onboarding_flow="profile_first",
        profile_data=copy.deepcopy(PROFILE),
        competitor_urls=["https://rakip.test"],
        competitor_terms=[{"term": "Rakip", "status": "approved", "manual_approved": True}],
        competitor_url_decisions={"rakip.test": {"url": "https://rakip.test", "decision": "block"}},
        competitor_policy={"ads": "block", "seo": "allow", "social": "block"},
        topic_policy={"excluded_terms": [{"term": "kripto"}], "excluded_aliases": []},
        channel_strategy={"status": "approved", "social_mode": "x"},
        strategy_version=3,
        preliminary_info="mutlaka bunlar",
        excluded_info="kripto",
        source_pages=[{"url": URL, "title": "Ana", "status": 200}],
        crawl_content_cache="site icerigi",
        capabilities={"free_plan": {"value": True, "source": "user"}},
        suggested_keywords=["eski onerilen"],
        default_geo_target_id="2792",
        default_language_id=L_TR,
    )


def _post(client, workspace_id, *pairs):
    return client.post(
        f"{BASE}/workspaces/{workspace_id}/markets",
        json={"markets": [{"geo_target_id": g, "language_id": l} for g, l in pairs]},
    )


def test_single_copy_fields(client, db_session, source):
    resp = _post(client, source.id, (DE, L_RU))
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["source_id"] == source.id
    assert body["skipped"] == []
    (created,) = body["created"]
    assert created["name"] == "Acme — Almanya (DE)/Rusça"  # tür eki ("— Ülke") ada girmez
    assert created["status"] == "confirmed"
    assert created["onboarding_flow"] == "profile_first"
    assert created["profile_approved_at"] is not None
    assert created["default_geo_target_id"] == DE
    assert created["default_language_id"] == L_RU
    assert created["market_label"] == "Almanya (DE) · Rusça"
    assert created["market_location_label"] == "Almanya (DE) — Ülke"
    assert created["market_locked"] is False
    assert created["parent_brand_profile_id"] is None

    row = db_session.get(BrandProfile, created["id"])
    # kopyalananlar
    assert row.company_url == source.company_url and row.crawl_scope == "site"
    assert row.competitor_urls == source.competitor_urls
    assert row.competitor_terms == source.competitor_terms
    assert row.competitor_url_decisions == source.competitor_url_decisions
    assert row.competitor_policy == source.competitor_policy
    assert row.topic_policy == source.topic_policy
    assert row.channel_strategy == source.channel_strategy and row.strategy_version == 3
    assert row.preliminary_info == "mutlaka bunlar" and row.excluded_info == "kripto"
    assert row.source_pages == source.source_pages
    assert row.crawl_content_cache == "site icerigi"
    # konum politikasi alanlari atilir (alan yok = mod 'none'); gerisi aynen
    assert row.profile_data == {
        k: v for k, v in PROFILE.items()
        if k not in ("location_filter_mode", "focus_cities", "location_exempt_terms")
    }
    # kopyalanmayanlar
    assert row.capabilities is None
    assert row.suggested_keywords is None
    assert row.pending_focus_url is None and row.analysis_attempt_id is None
    assert row.validation_data is None
    assert db_session.query(WorkspaceKeyword).filter_by(brand_profile_id=row.id).count() == 0
    assert db_session.query(ScoringRun).filter_by(brand_profile_id=row.id).count() == 0
    # kaynak dokunulmadan
    db_session.refresh(source)
    assert source.profile_data["location_filter_mode"] == "focus_only"
    assert source.suggested_keywords == ["eski onerilen"]


def test_bulk_three_countries_and_one_city(client, db_session, source):
    resp = _post(client, source.id, (DE, L_DE), (RU, L_RU), (UA, L_UK), (BERLIN, L_RU))
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert len(body["created"]) == 4 and body["skipped"] == []
    assert len({c["id"] for c in body["created"]}) == 4
    assert any(c["name"].endswith("— Berlin, Almanya/Rusça") for c in body["created"])  # tür eki yok
    assert all(len(c["name"]) <= 200 for c in body["created"])


def test_partial_existing_is_201_with_skipped(client, db_session, source, make_workspace):
    make_workspace(
        name="Acme DE", company_url=URL, status="confirmed", onboarding_flow="profile_first",
        default_geo_target_id=DE, default_language_id=L_DE,
    )
    resp = _post(client, source.id, (DE, L_DE), (RU, L_RU), (UA, L_UK), (GB, L_EN))
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert len(body["created"]) == 3
    (skipped,) = body["skipped"]
    assert skipped["geo_target_id"] == DE and skipped["language_id"] == L_DE
    assert skipped["market_label"] == "Almanya (DE) · Almanca"
    assert skipped["reason"] == "Bu pazar için çalışma zaten var: Acme DE"


def test_source_own_market_is_skipped(client, source):
    resp = _post(client, source.id, ("2792", L_TR), (DE, L_DE))
    assert resp.status_code == 201
    body = resp.json()
    assert len(body["created"]) == 1
    assert body["skipped"][0]["reason"].endswith(": Acme")


def test_all_existing_is_409(client, db_session, source):
    resp = _post(client, source.id, ("2792", L_TR))
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "MARKET_COPY_ALL_EXIST"
    assert len(detail["skipped"]) == 1
    assert db_session.query(BrandProfile).count() == 1


def test_archived_market_does_not_block(client, make_workspace, source):
    from datetime import datetime, timezone

    make_workspace(
        name="Acme DE eski", company_url=URL, default_geo_target_id=DE,
        default_language_id=L_DE, deleted_at=datetime.now(timezone.utc),
    )
    assert _post(client, source.id, (DE, L_DE)).status_code == 201


def test_duplicate_in_list_is_422(client, db_session, source):
    # 1055 dil takma adi kanonik 1037'ye cozulur: ayni pazar iki kez
    resp = _post(client, source.id, (DE, L_TR), (DE, "1055"))
    assert resp.status_code == 422
    assert db_session.query(BrandProfile).count() == 1


def test_invalid_item_rejects_whole_list(client, db_session, source):
    resp = _post(client, source.id, (DE, L_DE), ("999999999", L_RU))
    assert resp.status_code == 422
    resp = _post(client, source.id, (DE, L_DE), (RU, "4242"))
    assert resp.status_code == 422
    assert db_session.query(BrandProfile).count() == 1


@pytest.mark.parametrize("n", [0, 31])
def test_list_size_bounds(client, source, n):
    items = [{"geo_target_id": DE, "language_id": L_DE}] * n
    resp = client.post(f"{BASE}/workspaces/{source.id}/markets", json={"markets": items})
    assert resp.status_code == 422


def test_source_focus_is_400(client, make_workspace, source):
    focus = make_workspace(
        name="Acme odak", company_url=f"{URL}/hizmet", crawl_scope="page", status="confirmed",
        parent_brand_profile_id=source.id,
    )
    resp = _post(client, focus.id, (DE, L_DE))
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "MARKET_COPY_SOURCE_INVALID"


@pytest.mark.parametrize("status", ["profile_review", "competitor_review", "keywords_review", "failed"])
def test_source_not_confirmed_is_400(client, make_workspace, status):
    ws = make_workspace(name="Acme", company_url=URL, status=status)
    resp = _post(client, ws.id, (DE, L_DE))
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "MARKET_COPY_SOURCE_INVALID"


def test_archived_or_missing_source_is_404(client, make_workspace):
    from datetime import datetime, timezone

    ws = make_workspace(name="Acme", company_url=URL, deleted_at=datetime.now(timezone.utc))
    assert _post(client, ws.id, (DE, L_DE)).status_code == 404
    assert _post(client, 999999, (DE, L_DE)).status_code == 404


@pytest.mark.parametrize("status", ["pending", "running"])
def test_running_source_is_409(client, make_workspace, status):
    ws = make_workspace(name="Acme", company_url=URL, status=status)
    resp = _post(client, ws.id, (DE, L_DE))
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "ANALYSIS_RUNNING"


def test_copy_is_independent(client, db_session, source):
    created = _post(client, source.id, (DE, L_DE)).json()["created"][0]
    row = db_session.get(BrandProfile, created["id"])
    before = copy.deepcopy(row.competitor_policy)

    source.competitor_policy = {"ads": "allow", "seo": "allow", "social": "allow"}
    source.profile_data = {**source.profile_data, "services": ["Baska"]}
    db_session.commit()

    db_session.refresh(row)
    assert row.competitor_policy == before
    assert row.profile_data["services"] == ["Hizmet A"]


def test_focus_with_same_market_does_not_block(client, make_workspace, source):
    make_workspace(
        name="Acme odak", company_url=f"{URL}/hizmet", crawl_scope="page", status="confirmed",
        parent_brand_profile_id=source.id, default_geo_target_id=RU, default_language_id=L_RU,
    )
    body = _post(client, source.id, (RU, L_RU)).json()
    assert len(body["created"]) == 1 and body["skipped"] == []


def test_different_site_with_same_market_does_not_block(client, make_workspace, source):
    make_workspace(
        name="Baska", company_url="https://baska.test", status="confirmed",
        default_geo_target_id=RU, default_language_id=L_RU,
    )
    assert len(_post(client, source.id, (RU, L_RU)).json()["created"]) == 1


def test_url_trailing_slash_and_case_still_match(client, make_workspace, source):
    make_workspace(
        name="Acme RU", company_url="HTTPS://WWW.EXAMPLE.TEST/", status="confirmed",
        default_geo_target_id=RU, default_language_id=L_RU,
    )
    body = _post(client, source.id, (RU, L_RU), (DE, L_DE)).json()
    assert len(body["created"]) == 1 and len(body["skipped"]) == 1


def test_copy_can_be_source_of_another_copy(client, db_session, source):
    first = _post(client, source.id, (DE, L_DE)).json()["created"][0]
    resp = _post(client, first["id"], (US, L_EN), (DE, L_DE))
    assert resp.status_code == 201, resp.text
    body = resp.json()
    (second,) = body["created"]
    assert second["name"] == "Acme — Amerika Birleşik Devletleri (US)/İngilizce"  # ek üst üste binmez
    assert body["skipped"][0]["reason"].startswith("Bu pazar için çalışma zaten var")
    row = db_session.get(BrandProfile, second["id"])
    assert "location_filter_mode" not in (row.profile_data or {})


def test_long_source_name_is_truncated(client, make_workspace):
    ws = make_workspace(name="A" * 200, company_url=URL, status="confirmed")
    created = _post(client, ws.id, (BERLIN, L_RU)).json()["created"][0]
    assert len(created["name"]) <= 200
    assert created["name"].endswith("Rusça")


# ── oneri kelime arka plan isi ─────────────────────────────────────


def test_job_scheduled_once_per_created_copy(client, scheduled, source, make_workspace):
    make_workspace(
        name="Acme DE", company_url=URL, status="confirmed",
        default_geo_target_id=DE, default_language_id=L_DE,
    )
    body = _post(client, source.id, (DE, L_DE), (RU, L_RU), (UA, L_UK)).json()
    assert sorted(c["workspace_id"] for c in scheduled) == sorted(c["id"] for c in body["created"])
    assert len(scheduled) == 2  # atlanan icin is yok


class _FakeExtractor:
    calls: list = []
    result: object = ["kelime a", "kelime b"]

    def __init__(self, ai):
        pass

    def suggest_keywords_from_profile(self, site_content, profile, **kw):
        type(self).calls.append((site_content, profile, kw))
        if isinstance(self.result, Exception):
            raise self.result
        return list(self.result)


@pytest.fixture
def fake_ai(monkeypatch):
    import types

    import app.core.site_analyzer.profile_extractor as pe
    import app.generators.ai_service as ai_mod

    _FakeExtractor.calls = []
    _FakeExtractor.result = ["kelime a", "kelime b"]
    monkeypatch.setattr(ai_mod, "get_ai_service", lambda **kw: types.SimpleNamespace(collector=None))
    monkeypatch.setattr(pe, "ProfileExtractor", _FakeExtractor)
    monkeypatch.setattr(pe, "resolve_site_content", lambda *a, **kw: ("icerik", None, None))
    return _FakeExtractor


def _make_copy(client, db_session, source):
    created = _post(client, source.id, (DE, L_RU)).json()["created"][0]
    return db_session.get(BrandProfile, created["id"])


def test_job_writes_only_suggested_keywords(client, db_session, source, fake_ai):
    row = _make_copy(client, db_session, source)
    before = (row.status, copy.deepcopy(row.profile_data), row.policy_version,
              row.anchor_version, row.profile_approved_at, row.analysis_attempt_id)
    REAL_JOB(workspace_id=row.id)
    db_session.refresh(row)
    assert row.suggested_keywords == ["kelime a", "kelime b"]
    assert (row.status, row.profile_data, row.policy_version,
            row.anchor_version, row.profile_approved_at, row.analysis_attempt_id) == before
    assert row.status == "confirmed"
    (call,) = fake_ai.calls
    assert call[2]["output_ctx"] is not None  # pazar dili baglami


def test_job_is_idempotent(client, db_session, source, fake_ai):
    row = _make_copy(client, db_session, source)
    row.suggested_keywords = ["var olan"]
    db_session.commit()
    REAL_JOB(workspace_id=row.id)
    db_session.refresh(row)
    assert row.suggested_keywords == ["var olan"]
    assert fake_ai.calls == []


def test_job_extractor_error_leaves_copy_confirmed(client, db_session, source, fake_ai):
    row = _make_copy(client, db_session, source)
    fake_ai.result = RuntimeError("gemini down")
    REAL_JOB(workspace_id=row.id)  # raise etmemeli
    db_session.refresh(row)
    assert row.suggested_keywords is None and row.status == "confirmed"


@pytest.mark.parametrize("kind", ["not_confirmed", "focus", "archived", "missing"])
def test_job_noop_when_not_eligible(client, db_session, source, make_workspace, fake_ai, kind):
    from datetime import datetime, timezone

    if kind == "not_confirmed":
        ws = make_workspace(name="X", company_url=URL, status="keywords_review")
    elif kind == "focus":
        ws = make_workspace(name="F", company_url=f"{URL}/h", crawl_scope="page",
                            status="confirmed", parent_brand_profile_id=source.id)
    elif kind == "archived":
        ws = make_workspace(name="A", company_url=URL, status="confirmed",
                            deleted_at=datetime.now(timezone.utc))
    else:
        REAL_JOB(workspace_id=999999)
        assert fake_ai.calls == []
        return
    REAL_JOB(workspace_id=ws.id)
    db_session.refresh(ws)
    assert ws.suggested_keywords is None and fake_ai.calls == []
