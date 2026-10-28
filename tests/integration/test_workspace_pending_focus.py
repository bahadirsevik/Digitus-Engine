"""Marka onayindan sonra otomatik alt sayfa + analizi tekrar dene
(plan_marka_alt_sayfa.md Faz 3e, B2-B6).

Gercek test DB'si; AI ve crawler yok. Dispatch edilen gorevler kaydedilir.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.api.v1 import brand_profile as brand_profile_api
from app.database.models import BrandProfile

BASE = "/api/v1/brand-profile"
BRAND_URL = "https://www.example.test"
PENDING = "https://www.example.test/hizmetler/toprak-analizi"

LOCATION = {
    "location_filter_mode": "focus_only",
    "focus_cities": ["Ankara"],
    "location_exempt_terms": ["ankara bilgisayar"],
}


@pytest.fixture
def dispatched(monkeypatch):
    calls = []
    monkeypatch.setattr(
        brand_profile_api, "_run_profile_analysis_first", lambda **kw: calls.append(kw)
    )
    return calls


@pytest.fixture
def review_brand(make_workspace):
    """Keyword onayi bekleyen, bekleyen alt sayfali profil-once marka."""
    return make_workspace(
        name="Acme",
        company_url=BRAND_URL,
        status="keywords_review",
        onboarding_flow="profile_first",
        suggested_keywords=["toprak analizi"],
        profile_data={
            "company_name": "Acme", "anchor_texts": ["toprak analizi"], **LOCATION,
        },
        competitor_urls=["https://rakip.test"],
        excluded_info="kripto",
        default_geo_target_id="2792",
        default_language_id="1037",
        pending_focus_url=PENDING,
    )


def _approve(client, ws_id):
    return client.put(
        f"{BASE}/workspaces/{ws_id}/keywords/approve", json={"keywords": ["toprak analizi"]}
    )


def _create(client, **overrides):
    body = {"company_url": BRAND_URL, "flow_version": "profile_first", **overrides}
    return client.post(f"{BASE}/workspaces", json=body)


def _focuses(db_session, brand_id):
    db_session.expire_all()
    return db_session.query(BrandProfile).filter(
        BrandProfile.parent_brand_profile_id == brand_id
    ).all()


def _brand_count(db_session):
    db_session.expire_all()
    return db_session.query(BrandProfile).count()


# ── B2: olusturma dogrulamasi ──────────────────────────────────────


def test_create_stores_normalized_pending_focus_url(client, dispatched):
    resp = _create(client, pending_focus_url="www.example.test/hizmetler/a")
    assert resp.status_code == 201
    body = resp.json()
    assert body["pending_focus_url"] == "https://www.example.test/hizmetler/a"
    assert body["pending_focus_error"] is None
    assert body["focus_handoff_id"] is None
    assert body["crawl_scope"] == "site"
    # yalniz marka analizi dispatch edilir, alt sayfa DEGIL
    assert len(dispatched) == 1 and dispatched[0]["workspace_id"] == body["id"]
    listed = {w["id"]: w for w in client.get(f"{BASE}/workspaces").json()}
    assert listed[body["id"]]["pending_focus_url"] == "https://www.example.test/hizmetler/a"


def test_create_without_pending_keeps_fields_null(client, dispatched):
    body = _create(client).json()
    assert body["pending_focus_url"] is None and body["pending_focus_error"] is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"company_url": "https://www.example.test/sayfa", "crawl_scope": "page"},  # page kapsam
        {"company_url": "https://www.example.test/sayfa"},  # turetilen page
        {"flow_version": "legacy"},  # legacy akis
    ],
)
def test_create_rejects_pending_focus_outside_site_profile_first(
    client, db_session, dispatched, overrides
):
    overrides = {"pending_focus_url": "https://www.example.test/a", **overrides}
    before = _brand_count(db_session)
    resp = _create(client, **overrides)
    assert resp.status_code == 400
    assert _brand_count(db_session) == before
    assert dispatched == []


@pytest.mark.parametrize(
    "bad",
    [
        "https://www.example.test",  # kok sayfa
        "https://www.example.test/",
        "https://baska.test/a",  # baska site
        "ftp://www.example.test/a",  # gecersiz sema
    ],
)
def test_create_rejects_invalid_pending_focus_url(client, db_session, dispatched, bad):
    before = _brand_count(db_session)
    assert _create(client, pending_focus_url=bad).status_code == 400
    assert _brand_count(db_session) == before
    assert dispatched == []


def test_create_accepts_www_equivalent_host(client, dispatched):
    resp = _create(
        client, company_url="https://example.test", pending_focus_url="www.example.test/x"
    )
    assert resp.status_code == 201
    assert resp.json()["pending_focus_url"] == "https://www.example.test/x"


# ── B4: onayda otomatik alt sayfa ──────────────────────────────────


def test_approve_creates_focus_inherits_and_clears_fields(
    client, db_session, review_brand, dispatched
):
    resp = _approve(client, review_brand.id)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "confirmed"
    assert body["pending_focus_url"] is None and body["pending_focus_error"] is None
    focuses = _focuses(db_session, review_brand.id)
    assert len(focuses) == 1
    focus = focuses[0]
    assert body["focus_handoff_id"] == focus.id
    assert focus.company_url == PENDING and focus.crawl_scope == "page"
    assert focus.status == "pending"
    assert focus.competitor_urls == ["https://rakip.test"]
    assert focus.excluded_info == "kripto"
    assert focus.profile_data == LOCATION  # yalniz konum alanlari tasinir
    # dispatch TEK sefer ve odak icin
    assert len(dispatched) == 1
    assert dispatched[0]["workspace_id"] == focus.id
    assert dispatched[0]["company_url"] == PENDING
    assert dispatched[0]["competitor_urls"] is None
    assert dispatched[0]["attempt_id"] == focus.analysis_attempt_id
    # sonraki okuma handoff tasimaz
    assert client.get(f"{BASE}/workspaces/{review_brand.id}").json()["focus_handoff_id"] is None


def test_approve_without_pending_has_no_handoff_and_no_dispatch(
    client, db_session, make_workspace, dispatched
):
    ws = make_workspace(
        company_url=BRAND_URL, status="keywords_review", onboarding_flow="profile_first",
        profile_data={"company_name": "A", "anchor_texts": ["a"]},
    )
    body = _approve(client, ws.id).json()
    assert body["status"] == "confirmed" and body["focus_handoff_id"] is None
    assert _focuses(db_session, ws.id) == [] and dispatched == []


def test_approve_reuses_existing_active_focus_without_second_row(
    client, db_session, review_brand, make_workspace, dispatched
):
    existing = make_workspace(
        name="Var", company_url="https://example.test/hizmetler/toprak-analizi/",
        crawl_scope="page", parent_brand_profile_id=review_brand.id, status="confirmed",
    )
    body = _approve(client, review_brand.id).json()
    assert body["focus_handoff_id"] == existing.id
    assert body["pending_focus_url"] is None
    assert [f.id for f in _focuses(db_session, review_brand.id)] == [existing.id]
    assert dispatched == []  # yeni olusmadi -> analiz yok


def test_approve_ignores_archived_focus_for_same_page(
    client, db_session, review_brand, make_workspace, dispatched
):
    make_workspace(
        name="Arsiv", company_url=PENDING, crawl_scope="page",
        parent_brand_profile_id=review_brand.id, status="confirmed",
        deleted_at=datetime.now(timezone.utc),
    )
    body = _approve(client, review_brand.id).json()
    assert body["focus_handoff_id"] is not None
    assert len(_focuses(db_session, review_brand.id)) == 2
    assert len(dispatched) == 1


def test_approve_core_validation_failure_keeps_brand_confirmed_and_url(
    client, db_session, review_brand, dispatched
):
    # depolanmis adres sonradan gecersiz hale gelmis
    review_brand.pending_focus_url = "https://baska.test/a"
    db_session.commit()
    body = _approve(client, review_brand.id).json()
    assert body["status"] == "confirmed"
    assert body["pending_focus_url"] == "https://baska.test/a"
    assert "aynı sitede" in body["pending_focus_error"]
    assert body["focus_handoff_id"] is None
    assert _focuses(db_session, review_brand.id) == [] and dispatched == []


def test_approve_unexpected_core_error_rolls_back_savepoint(
    client, db_session, review_brand, dispatched, monkeypatch
):
    real_core = brand_profile_api._create_focus_core

    def boom(db, parent, url):
        real_core(db, parent, url)  # satir flush edildi...
        raise RuntimeError("beklenmedik")  # ...sonra patla

    monkeypatch.setattr(brand_profile_api, "_create_focus_core", boom)
    resp = _approve(client, review_brand.id)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "confirmed"
    assert body["pending_focus_url"] == PENDING
    assert body["pending_focus_error"] == "Alt sayfa hazırlanamadı, lütfen tekrar deneyin."
    assert "beklenmedik" not in body["pending_focus_error"]
    assert _focuses(db_session, review_brand.id) == []  # savepoint geri alindi
    assert dispatched == []
    row = db_session.get(BrandProfile, review_brand.id)
    assert row.status == "confirmed" and row.suggested_keywords == ["toprak analizi"]


def test_legacy_confirm_path_does_not_trigger_pending_focus(
    client, db_session, make_workspace, dispatched
):
    ws = make_workspace(
        company_url=BRAND_URL, status="draft", onboarding_flow="legacy",
        profile_data={"company_name": "A", "products": ["Analiz Platformu"]},
        pending_focus_url=PENDING,
    )
    resp = client.put(
        f"{BASE}/workspaces/{ws.id}/confirm",
        json={"profile_data": {"products": ["Analiz Platformu"]}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "confirmed" and body["focus_handoff_id"] is None
    assert body["pending_focus_url"] == PENDING
    assert _focuses(db_session, ws.id) == [] and dispatched == []


# ── B5: tekrar dene / vazgec ───────────────────────────────────────


@pytest.fixture
def failed_pending_brand(make_workspace):
    return make_workspace(
        name="Acme", company_url=BRAND_URL, status="confirmed", onboarding_flow="profile_first",
        profile_data={"company_name": "Acme", **LOCATION},
        pending_focus_url=PENDING, pending_focus_error="Alt sayfa hazırlanamadı",
    )


def test_pending_retry_success_creates_focus_and_dispatches(
    client, db_session, failed_pending_brand, dispatched
):
    resp = client.post(f"{BASE}/workspaces/{failed_pending_brand.id}/pending-focus/retry")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == failed_pending_brand.id
    assert body["pending_focus_url"] is None and body["pending_focus_error"] is None
    focuses = _focuses(db_session, failed_pending_brand.id)
    assert len(focuses) == 1 and body["focus_handoff_id"] == focuses[0].id
    assert len(dispatched) == 1 and dispatched[0]["workspace_id"] == focuses[0].id


def test_pending_retry_returns_existing_focus_without_second_row(
    client, db_session, failed_pending_brand, make_workspace, dispatched
):
    existing = make_workspace(
        name="Var", company_url=PENDING, crawl_scope="page",
        parent_brand_profile_id=failed_pending_brand.id, status="confirmed",
    )
    body = client.post(
        f"{BASE}/workspaces/{failed_pending_brand.id}/pending-focus/retry"
    ).json()
    assert body["focus_handoff_id"] == existing.id and body["pending_focus_url"] is None
    assert len(_focuses(db_session, failed_pending_brand.id)) == 1
    assert dispatched == []


def test_pending_retry_core_failure_is_200_with_error_and_url_kept(
    client, db_session, failed_pending_brand, dispatched, monkeypatch
):
    def boom(db, parent, url):
        raise brand_profile_api.FocusCreateError(400, "Geçersiz alt sayfa adresi")

    monkeypatch.setattr(brand_profile_api, "_create_focus_core", boom)
    resp = client.post(f"{BASE}/workspaces/{failed_pending_brand.id}/pending-focus/retry")
    assert resp.status_code == 200
    body = resp.json()
    assert body["pending_focus_url"] == PENDING
    assert body["pending_focus_error"] == "Geçersiz alt sayfa adresi"
    assert body["focus_handoff_id"] is None and dispatched == []


def test_pending_retry_requires_confirmed_brand_and_pending_url(
    client, make_workspace, dispatched
):
    unconfirmed = make_workspace(
        company_url=BRAND_URL, status="keywords_review", onboarding_flow="profile_first",
        pending_focus_url=PENDING,
    )
    resp = client.post(f"{BASE}/workspaces/{unconfirmed.id}/pending-focus/retry")
    assert resp.status_code == 409 and "onaylanmadı" in resp.json()["detail"]
    no_pending = make_workspace(
        company_url=BRAND_URL, status="confirmed", onboarding_flow="profile_first"
    )
    assert client.post(
        f"{BASE}/workspaces/{no_pending.id}/pending-focus/retry"
    ).status_code == 409
    assert dispatched == []


def test_pending_focus_delete_clears_both_fields(client, db_session, failed_pending_brand):
    resp = client.delete(f"{BASE}/workspaces/{failed_pending_brand.id}/pending-focus")
    assert resp.status_code == 200
    body = resp.json()
    assert body["pending_focus_url"] is None and body["pending_focus_error"] is None
    db_session.expire_all()
    row = db_session.get(BrandProfile, failed_pending_brand.id)
    assert row.pending_focus_url is None and row.pending_focus_error is None


@pytest.mark.parametrize(
    "method,path",
    [
        ("post", "pending-focus/retry"),
        ("delete", "pending-focus"),
        ("post", "analysis/retry"),
    ],
)
def test_new_endpoints_404_for_missing_and_archived(client, make_workspace, method, path):
    assert getattr(client, method)(f"{BASE}/workspaces/99999/{path}").status_code == 404
    archived = make_workspace(
        company_url=BRAND_URL, status="failed", onboarding_flow="profile_first",
        pending_focus_url=PENDING, deleted_at=datetime.now(timezone.utc),
    )
    assert getattr(client, method)(f"{BASE}/workspaces/{archived.id}/{path}").status_code == 404


# ── B6: analizi tekrar dene ────────────────────────────────────────


def _failed(make_workspace, **kw):
    defaults = dict(
        company_url=BRAND_URL, status="failed", onboarding_flow="profile_first",
        error_message="crawl hatasi", analysis_attempt_id="eski-token",
        competitor_urls=["https://rakip.test"],
    )
    defaults.update(kw)
    return make_workspace(**defaults)


def test_analysis_retry_brand_resets_and_dispatches_once(
    client, db_session, make_workspace, dispatched
):
    ws = _failed(make_workspace)
    resp = client.post(f"{BASE}/workspaces/{ws.id}/analysis/retry")
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending"
    db_session.expire_all()
    row = db_session.get(BrandProfile, ws.id)
    assert row.error_message is None
    assert row.analysis_attempt_id not in (None, "eski-token")
    assert row.crawl_scope == "site"
    assert dispatched == [{
        "workspace_id": ws.id, "company_url": BRAND_URL,
        "competitor_urls": ["https://rakip.test"], "attempt_id": row.analysis_attempt_id,
    }]
    # ikinci istek: pending -> 409, cift dispatch yok
    assert client.post(f"{BASE}/workspaces/{ws.id}/analysis/retry").status_code == 409
    assert len(dispatched) == 1


def test_analysis_retry_focus_keeps_page_scope_and_no_competitors(
    client, db_session, make_workspace, dispatched
):
    brand = make_workspace(
        company_url=BRAND_URL, status="confirmed", onboarding_flow="profile_first"
    )
    focus = _failed(
        make_workspace, company_url=PENDING, crawl_scope="page",
        parent_brand_profile_id=brand.id, profile_data=dict(LOCATION),
    )
    assert client.post(f"{BASE}/workspaces/{focus.id}/analysis/retry").status_code == 200
    db_session.expire_all()
    row = db_session.get(BrandProfile, focus.id)
    assert row.crawl_scope == "page" and row.status == "pending"
    assert dispatched[0]["workspace_id"] == focus.id
    assert dispatched[0]["competitor_urls"] is None
    assert dispatched[0]["company_url"] == PENDING


def test_analysis_retry_rejects_wrong_states(client, make_workspace, dispatched):
    with_profile = _failed(make_workspace, profile_data={"company_name": "A", "sector": "x"})
    assert client.post(f"{BASE}/workspaces/{with_profile.id}/analysis/retry").status_code == 400
    confirmed = make_workspace(
        company_url=BRAND_URL, status="confirmed", onboarding_flow="profile_first"
    )
    assert client.post(f"{BASE}/workspaces/{confirmed.id}/analysis/retry").status_code == 400
    legacy = _failed(make_workspace, onboarding_flow="legacy")
    assert client.post(f"{BASE}/workspaces/{legacy.id}/analysis/retry").status_code == 400
    for state in ("pending", "running"):
        ws = make_workspace(
            company_url=BRAND_URL, status=state, onboarding_flow="profile_first",
        )
        assert client.post(f"{BASE}/workspaces/{ws.id}/analysis/retry").status_code == 409
    assert dispatched == []


def test_analysis_retry_location_only_profile_counts_as_no_profile(
    client, make_workspace, dispatched
):
    ws = _failed(make_workspace, crawl_scope="page", profile_data=dict(LOCATION))
    assert client.post(f"{BASE}/workspaces/{ws.id}/analysis/retry").status_code == 200
    assert len(dispatched) == 1
