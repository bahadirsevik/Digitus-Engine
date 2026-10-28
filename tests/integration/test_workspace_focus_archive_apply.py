"""Marka alt sayfa odaklari: arsiv/geri getirme (3c) + marka ayarlarini uygulama (3d).

Gercek test DB'si; AI/ag yok (plan_marka_alt_sayfa.md Faz 3c + 3d).
"""
from __future__ import annotations

import pytest

from app.database.models import BrandProfile, ContentOutput, TaskResult

BASE = "/api/v1/brand-profile"

BRAND_LOCATION = {
    "location_filter_mode": "focus_only",
    "focus_cities": ["Ankara"],
    "location_exempt_terms": ["ankara bilgisayar"],
}
BRAND_POLICY = {
    "competitor_urls": ["https://rakip.test"],
    "competitor_terms": [{"term": "Rakip", "status": "approved", "manual_approved": True}],
    "competitor_url_decisions": {"rakip.test": {"url": "https://rakip.test", "decision": "block"}},
    "competitor_policy": {"ads": "block", "seo": "allow", "social": "block"},
    "topic_policy": {"excluded_terms": [{"term": "kripto"}], "excluded_aliases": []},
    "excluded_info": "kripto",
    "default_geo_target_id": "2792",
    "default_language_id": "1037",
}


@pytest.fixture
def brand(make_workspace):
    return make_workspace(
        name="Acme",
        company_url="https://www.example.test",
        status="confirmed",
        onboarding_flow="profile_first",
        profile_data={"company_name": "Acme", "services": ["BrandService"], **BRAND_LOCATION},
        capabilities={"free_plan": {"value": True, "source": "brand"}},
        **BRAND_POLICY,
    )


@pytest.fixture
def make_focus(make_workspace, brand):
    n = {"i": 0}

    def _make(**kwargs):
        n["i"] += 1
        defaults = dict(
            name=f"Acme — /f{n['i']}",
            company_url=f"https://www.example.test/f{n['i']}",
            crawl_scope="page",
            parent_brand_profile_id=brand.id,
            status="confirmed",
            profile_data={"company_name": "Focus", "services": ["FocusService"]},
        )
        defaults.update(kwargs)
        return make_workspace(**defaults)

    return _make


def _reload(db_session, ws):
    db_session.expire_all()
    return db_session.get(BrandProfile, ws.id)


def _archive(client, ws):
    return client.post(f"{BASE}/workspaces/{ws.id}/archive")


def _restore(client, ws):
    return client.post(f"{BASE}/workspaces/{ws.id}/restore")


# ── 3c arsiv / geri getirme ────────────────────────────────────────


def test_brand_archive_archives_active_focuses_with_flag_and_keeps_manual_flag_false(
    client, db_session, brand, make_focus
):
    active = make_focus()
    manual = make_focus()
    assert _archive(client, manual).status_code == 200
    assert _reload(db_session, manual).archived_with_parent is False

    resp = _archive(client, brand)
    assert resp.status_code == 200
    assert resp.json()["deleted_at"] is not None

    active = _reload(db_session, active)
    assert active.deleted_at is not None and active.archived_with_parent is True
    manual = _reload(db_session, manual)
    assert manual.deleted_at is not None and manual.archived_with_parent is False
    assert _reload(db_session, brand).archived_with_parent is False


def test_focus_archive_directly_sets_flag_false(client, db_session, brand, make_focus):
    focus = make_focus()
    resp = _archive(client, focus)
    assert resp.status_code == 200
    focus = _reload(db_session, focus)
    assert focus.deleted_at is not None and focus.archived_with_parent is False
    assert _reload(db_session, brand).deleted_at is None


def test_manually_archived_focus_is_not_restored_when_brand_is_restored(
    client, db_session, brand, make_focus
):
    by_parent = make_focus()
    manual = make_focus()
    assert _archive(client, manual).status_code == 200
    assert _archive(client, brand).status_code == 200

    resp = _restore(client, brand)
    assert resp.status_code == 200
    assert resp.json()["deleted_at"] is None

    by_parent = _reload(db_session, by_parent)
    assert by_parent.deleted_at is None and by_parent.archived_with_parent is False
    manual = _reload(db_session, manual)
    assert manual.deleted_at is not None and manual.archived_with_parent is False


def test_focus_restore_with_archived_parent_409_and_with_active_parent_ok(
    client, db_session, brand, make_focus
):
    focus = make_focus()
    assert _archive(client, brand).status_code == 200  # focus archived with parent

    resp = _restore(client, focus)
    assert resp.status_code == 409
    assert "markayı arşivden çıkarın" in resp.json()["detail"]
    assert _reload(db_session, focus).deleted_at is not None

    assert _restore(client, brand).status_code == 200
    manual = make_focus()
    assert _archive(client, manual).status_code == 200
    resp = _restore(client, manual)
    assert resp.status_code == 200
    manual = _reload(db_session, manual)
    assert manual.deleted_at is None and manual.archived_with_parent is False


def test_archive_unknown_or_already_archived_404(client, brand):
    assert client.post(f"{BASE}/workspaces/99999/archive").status_code == 404
    assert _archive(client, brand).status_code == 200
    assert _archive(client, brand).status_code == 404


# ── 3d onizleme ────────────────────────────────────────────────────


def _preview(client, ws_id):
    return client.post(f"{BASE}/workspaces/{ws_id}/focuses/settings/preview")


def _apply(client, ws_id):
    return client.post(f"{BASE}/workspaces/{ws_id}/focuses/settings/apply")


def test_preview_lists_only_differing_fields_with_labels(
    client, brand, make_focus, db_session
):
    same = make_focus(
        profile_data={"company_name": "F", **BRAND_LOCATION},
        capabilities={"free_plan": {"value": False, "source": "x"}},
        **BRAND_POLICY,
    )
    diff = make_focus(
        profile_data={"company_name": "F", "location_filter_mode": "none"},
        competitor_policy={"ads": "allow", "seo": "allow", "social": "block"},
        default_geo_target_id="2840",
        excluded_info=None,
        competitor_urls=BRAND_POLICY["competitor_urls"],
        competitor_terms=BRAND_POLICY["competitor_terms"],
        competitor_url_decisions=BRAND_POLICY["competitor_url_decisions"],
        topic_policy=BRAND_POLICY["topic_policy"],
        default_language_id="1037",
    )
    archived = make_focus()
    assert _archive(client, archived).status_code == 200

    resp = _preview(client, brand.id)
    assert resp.status_code == 200
    items = {i["id"]: i for i in resp.json()["focuses"]}
    assert set(items) == {same.id, diff.id}  # archived focus excluded
    assert items[same.id]["changes"] == []
    assert items[same.id]["busy"] is False
    got = {c["field"]: c["label"] for c in items[diff.id]["changes"]}
    assert got == {
        "competitor_policy": "Rakip kanal politikası",
        "default_geo_target_id": "Ülke",
        "excluded_info": "Dışlanacak konular",
        "location_filter_mode": "Konum filtresi",
        "focus_cities": "Odak şehirler",
        "location_exempt_terms": "Konum muafiyetleri",
    }
    assert "capabilities" not in str(resp.json())
    # salt-okunur
    assert _reload(db_session, diff).competitor_policy["ads"] == "allow"


def test_preview_busy_for_running_status_and_active_task(client, brand, make_focus, db_session):
    running = make_focus(status="running")
    tasked = make_focus()
    idle = make_focus()
    db_session.add(TaskResult(
        task_id="focus-busy-1", task_type="seo_content",
        brand_profile_id=tasked.id, status="running",
    ))
    db_session.commit()

    items = {i["id"]: i for i in _preview(client, brand.id).json()["focuses"]}
    assert items[running.id]["busy"] is True
    assert items[tasked.id]["busy"] is True
    assert items[idle.id]["busy"] is False


def test_preview_and_apply_non_brand_400_and_unknown_404(client, make_workspace, brand, make_focus):
    focus = make_focus()
    page = make_workspace(company_url="https://x.test/p", crawl_scope="page", status="confirmed")
    for ws_id in (focus.id, page.id):
        for call in (_preview, _apply):
            resp = call(client, ws_id)
            assert resp.status_code == 400
            assert "Yalnız marka çalışmasında kullanılabilir" in resp.json()["detail"]
    for call in (_preview, _apply):
        assert call(client, 99999).status_code == 404


def test_preview_and_apply_archived_brand_404(client, brand):
    assert _archive(client, brand).status_code == 200
    assert _preview(client, brand.id).status_code == 404
    assert _apply(client, brand.id).status_code == 404


def test_isolation_other_brands_focuses_not_listed_or_touched(
    client, db_session, make_workspace, brand, make_focus
):
    mine = make_focus()
    other = make_workspace(
        name="Other", company_url="https://other.test", status="confirmed", **BRAND_POLICY,
    )
    theirs = make_workspace(
        name="Other focus", company_url="https://other.test/a", crawl_scope="page",
        parent_brand_profile_id=other.id, status="confirmed",
        profile_data={"company_name": "T"}, competitor_policy={"ads": "allow"},
    )
    ids = {i["id"] for i in _preview(client, brand.id).json()["focuses"]}
    assert ids == {mine.id}
    assert [a["id"] for a in _apply(client, brand.id).json()["applied"]] == [mine.id]
    assert _reload(db_session, theirs).competitor_policy == {"ads": "allow"}


# ── 3d uygulama ────────────────────────────────────────────────────


def test_apply_copies_fields_and_location_keeps_other_profile_keys_and_capabilities(
    client, db_session, brand, make_focus
):
    focus = make_focus(
        profile_data={
            "company_name": "Focus", "services": ["FocusService"],
            "location_filter_mode": "none", "focus_cities": ["Izmir"],
        },
        capabilities={"free_plan": {"value": False, "source": "focus"}},
        competitor_policy={"ads": "allow", "seo": "allow", "social": "allow"},
    )
    resp = _apply(client, brand.id)
    assert resp.status_code == 200
    assert resp.json() == {
        "applied": [{"id": focus.id, "name": focus.name, "changed": True}],
        "skipped": [],
    }
    focus = _reload(db_session, focus)
    brand = _reload(db_session, brand)
    for field in ("competitor_urls", "competitor_terms", "competitor_url_decisions",
                  "competitor_policy",
                  "topic_policy", "excluded_info", "default_geo_target_id",
                  "default_language_id"):
        assert getattr(focus, field) == getattr(brand, field), field
    for key, value in BRAND_LOCATION.items():
        assert focus.profile_data[key] == value
    # other profile keys intact, capabilities untouched
    assert focus.profile_data["company_name"] == "Focus"
    assert focus.profile_data["services"] == ["FocusService"]
    assert focus.capabilities == {"free_plan": {"value": False, "source": "focus"}}
    # deep copy: no shared JSON objects
    assert focus.competitor_terms is not brand.competitor_terms
    assert focus.topic_policy is not brand.topic_policy
    assert focus.profile_data["focus_cities"] is not brand.profile_data["focus_cities"]


def test_apply_removes_location_keys_the_brand_lacks_and_none_clears_field(
    client, db_session, make_workspace, make_focus
):
    bare = make_workspace(
        name="Bare", company_url="https://bare.test", status="confirmed",
        profile_data={"company_name": "Bare"},
    )
    focus = make_workspace(
        name="Bare focus", company_url="https://bare.test/a", crawl_scope="page",
        parent_brand_profile_id=bare.id, status="confirmed",
        profile_data={"company_name": "F", **BRAND_LOCATION},
        excluded_info="eski", competitor_policy={"ads": "allow"},
    )
    assert _apply(client, bare.id).json()["applied"][0]["changed"] is True
    focus = _reload(db_session, focus)
    assert focus.excluded_info is None and focus.competitor_policy is None
    assert not any(k in focus.profile_data for k in BRAND_LOCATION)
    assert focus.profile_data["company_name"] == "F"


@pytest.mark.parametrize(
    "brand_excluded,expected",
    [(None, ["kumar"]), ("bahis", ["bahis", "kumar"])],
)
def test_apply_rebuilds_exclude_themes_keeping_focus_ai_themes(
    client, db_session, make_workspace, brand_excluded, expected
):
    """Kaldirilan kullanici dislamasi temadan da cikar; odagin AI temasi kalir."""
    bare = make_workspace(
        name="B", company_url="https://b.test", status="confirmed",
        profile_data={"company_name": "B"}, excluded_info=brand_excluded,
    )
    focus = make_workspace(
        name="BF", company_url="https://b.test/a", crawl_scope="page",
        parent_brand_profile_id=bare.id, status="confirmed",
        profile_data={"company_name": "F", "exclude_themes": ["kripto", "kumar"]},
        excluded_info="kripto",
    )
    v0 = focus.policy_version or 1
    assert _apply(client, bare.id).json()["applied"][0]["changed"] is True
    focus = _reload(db_session, focus)
    assert focus.excluded_info == brand_excluded
    assert sorted(focus.profile_data["exclude_themes"]) == expected
    assert focus.policy_version == v0 + 1


def test_apply_skips_running_focus_and_leaves_its_fields_untouched(
    client, db_session, brand, make_focus
):
    running = make_focus(status="running", competitor_policy={"ads": "allow"})
    ok = make_focus(competitor_policy={"ads": "allow"})
    resp = _apply(client, brand.id)
    assert resp.status_code == 200
    body = resp.json()
    assert body["skipped"] == [{"id": running.id, "name": running.name, "reason": "işlem sürüyor"}]
    assert [a["id"] for a in body["applied"]] == [ok.id]
    running = _reload(db_session, running)
    assert running.competitor_policy == {"ads": "allow"}
    assert running.policy_version == 1 or running.policy_version is None
    assert _reload(db_session, ok).competitor_policy == BRAND_POLICY["competitor_policy"]


def test_apply_skips_focus_with_active_work(client, db_session, brand, make_focus):
    busy = make_focus(competitor_policy={"ads": "allow"})
    free = make_focus(competitor_policy={"ads": "allow"})
    db_session.add(TaskResult(
        task_id="focus-busy-2", task_type="channel_assignment",
        brand_profile_id=busy.id, status="running",
    ))
    db_session.commit()
    body = _apply(client, brand.id).json()
    assert [(s["id"], s["reason"]) for s in body["skipped"]] == [(busy.id, "işlem sürüyor")]
    assert [a["id"] for a in body["applied"]] == [free.id]
    assert _reload(db_session, busy).competitor_policy == {"ads": "allow"}
    assert _reload(db_session, free).competitor_policy == BRAND_POLICY["competitor_policy"]


def test_apply_bumps_policy_version_and_stales_outputs_only_for_changed_focuses(
    client, db_session, brand, make_focus, make_scoring_run, make_keyword
):
    changed = make_focus(competitor_policy={"ads": "allow", "seo": "allow", "social": "allow"},
                         **{k: v for k, v in BRAND_POLICY.items()
                            if k != "competitor_policy"})
    unchanged = make_focus(profile_data={"company_name": "F", **BRAND_LOCATION}, **BRAND_POLICY)
    contents = {}
    for focus in (changed, unchanged):
        run = make_scoring_run(brand_profile_id=focus.id, status="channel_assigned")
        kw = make_keyword(f"kw {focus.id}", brand_profile_id=focus.id)
        db_session.add(ContentOutput(
            scoring_run_id=run.id, keyword_id=kw.id, channel="SEO",
            content_type="blog_post", content_data={"title": "x"}, is_stale=False,
        ))
        db_session.commit()
        contents[focus.id] = run.id
    v_changed = changed.policy_version or 1
    v_unchanged = unchanged.policy_version or 1
    # make_scoring_run stamps the run with the current version

    body = _apply(client, brand.id).json()
    assert {a["id"]: a["changed"] for a in body["applied"]} == {
        changed.id: True, unchanged.id: False,
    }
    assert _reload(db_session, changed).policy_version == v_changed + 1
    assert _reload(db_session, unchanged).policy_version == v_unchanged

    def stale(focus):
        return db_session.query(ContentOutput).filter(
            ContentOutput.scoring_run_id == contents[focus.id]
        ).one().is_stale

    db_session.expire_all()
    assert stale(changed) is True
    assert stale(unchanged) is False


def test_apply_location_change_bumps_policy_version_exactly_once(
    client, db_session, brand, make_focus
):
    focus = make_focus(
        profile_data={"company_name": "F", "location_filter_mode": "none"},
        **BRAND_POLICY,
    )
    v0 = focus.policy_version or 1
    anchor0 = focus.anchor_version or 1
    assert _apply(client, brand.id).status_code == 200
    focus = _reload(db_session, focus)
    assert focus.policy_version == v0 + 1  # no double bump (location + snapshot)
    assert (focus.anchor_version or 1) == anchor0  # location keys never change anchors
    # idempotent: second apply is a no-op
    again = _apply(client, brand.id).json()
    assert again["applied"][0]["changed"] is False
    assert _reload(db_session, focus).policy_version == v0 + 1


def test_apply_skips_focus_without_analyzed_profile(client, db_session, brand, make_focus):
    empty = make_focus(status="failed", profile_data=dict(BRAND_LOCATION) | {"focus_cities": ["X"]})
    body = _apply(client, brand.id).json()
    assert body["skipped"] == [
        {"id": empty.id, "name": empty.name, "reason": "profil analizi tamamlanmadı"}
    ]
    assert _reload(db_session, empty).profile_data["focus_cities"] == ["X"]


def test_apply_one_failing_focus_does_not_abort_loop(
    client, db_session, brand, make_focus, monkeypatch
):
    from app.api.v1 import brand_profile as api

    bad = make_focus(competitor_policy={"ads": "allow"})
    good = make_focus(competitor_policy={"ads": "allow"})
    real = api.policy_effect_snapshot

    def flaky(ws):
        if ws.id == bad.id:
            raise RuntimeError("boom")
        return real(ws)

    monkeypatch.setattr(api, "policy_effect_snapshot", flaky)
    body = _apply(client, brand.id).json()
    assert [s["id"] for s in body["skipped"]] == [bad.id]
    assert body["skipped"][0]["reason"] == "uygulanamadı, tekrar deneyin"
    assert [a["id"] for a in body["applied"]] == [good.id]
    assert _reload(db_session, bad).competitor_policy == {"ads": "allow"}
    assert _reload(db_session, good).competitor_policy == BRAND_POLICY["competitor_policy"]


def test_create_focus_rechecks_archive_after_lock(client, db_session, brand, monkeypatch):
    """Kilit beklenirken marka arsivlendiyse (yaris) arsivli markaya odak eklenmez."""
    from datetime import datetime

    from app.api.v1 import brand_profile as brand_profile_api

    # verify_workspace'in kilitten ONCE gectigi ani taklit et
    monkeypatch.setattr(brand_profile_api, "verify_workspace", lambda *_a, **_k: None)
    row = _reload(db_session, brand)
    row.deleted_at = datetime.utcnow()
    db_session.commit()

    res = client.post(
        f"{BASE}/workspaces/{brand.id}/focuses", json={"url": "https://www.example.test/yeni"}
    )
    assert res.status_code == 404
    assert db_session.query(BrandProfile).filter(
        BrandProfile.parent_brand_profile_id == brand.id
    ).count() == 0


def test_focus_restore_waits_for_brand_archive_lock(db_session, brand, make_focus):
    """Geri getirme marka kilidini bekler: arada arsivlenen markanin altinda aktif odak kalmaz."""
    import threading
    from datetime import datetime

    from fastapi import HTTPException

    from app.api.v1.brand_profile import archive_workspace, restore_workspace
    from app.core.site_analyzer.analysis_attempt import lock_workspace_row
    from app.database.connection import SessionLocal

    manual = make_focus(deleted_at=datetime.utcnow())  # elle arsivli
    holder = SessionLocal()
    result = {}

    def _restore():
        session = SessionLocal()
        try:
            restore_workspace(manual.id, db=session)
            result["status"] = 200
        except HTTPException as exc:
            result["status"] = exc.status_code
        finally:
            session.close()

    try:
        lock_workspace_row(holder, brand.id)  # marka arsivi surerken kilit tutuluyor
        worker = threading.Thread(target=_restore, daemon=True)
        worker.start()
        worker.join(timeout=1.0)
        assert worker.is_alive(), "geri getirme marka kilidini beklemedi"
        archive_workspace(brand.id, db=holder)  # commit kilidi birakir
    finally:
        holder.rollback()
        holder.close()
    worker.join(timeout=10)

    assert result["status"] == 409
    assert _reload(db_session, brand).deleted_at is not None
    assert _reload(db_session, manual).deleted_at is not None
