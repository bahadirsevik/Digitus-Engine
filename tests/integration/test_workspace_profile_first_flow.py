"""Profil-önce onboarding akışı (UI sadeleştirme Faz 1) entegrasyon testleri."""
from sqlalchemy import text

from app.api.v1 import brand_profile as brand_profile_api
from app.database.connection import SessionLocal
from app.database.models import AiUsageEvent, BrandProfile, TaskResult


VALID_PROFILE = {
    "company_name": "Hissefy",
    "sector": "Finansal teknoloji",
    "brand_summary": "Hisse analiz platformu",
    "target_audience": "Yatirimcilar",
    "products": ["Analiz Platformu"],
    "services": [],
    "use_cases": ["Borsa takibi"],
    "problems_solved": ["Dagitik veri"],
    "brand_terms": ["Hissefy"],
    "exclude_themes": ["kripto para"],
    "anchor_texts": ["Analiz Platformu", "Borsa takibi"],
}


# ── create: flow dallanması ────────────────────────────────────────

def test_create_profile_first_sets_column_and_dispatches_profile_task(
    client, monkeypatch,
):
    profile_calls = []
    keyword_calls = []
    monkeypatch.setattr(
        brand_profile_api, "_run_profile_analysis_first",
        lambda **kw: profile_calls.append(kw),
    )
    monkeypatch.setattr(
        brand_profile_api, "_run_keyword_suggestion",
        lambda **kw: keyword_calls.append(kw),
    )

    response = client.post(
        "/api/v1/brand-profile/workspaces",
        json={"company_url": "https://hissefy.com", "flow_version": "profile_first"},
    )

    assert response.status_code == 201
    data = response.json()
    assert data["onboarding_flow"] == "profile_first"
    assert data["name"] == "hissefy.com"  # URL'den türetildi
    assert len(profile_calls) == 1
    assert keyword_calls == []


def test_create_default_stays_legacy_and_dispatches_keyword_task(client, monkeypatch):
    profile_calls = []
    keyword_calls = []
    monkeypatch.setattr(
        brand_profile_api, "_run_profile_analysis_first",
        lambda **kw: profile_calls.append(kw),
    )
    monkeypatch.setattr(
        brand_profile_api, "_run_keyword_suggestion",
        lambda **kw: keyword_calls.append(kw),
    )

    response = client.post(
        "/api/v1/brand-profile/workspaces",
        json={"name": "Legacy WS", "company_url": "https://legacy.test"},
    )

    assert response.status_code == 201
    assert response.json()["onboarding_flow"] == "legacy"
    assert profile_calls == []
    assert len(keyword_calls) == 1


# ── refresh senaryosu: running fazında akış ayrımı response'tan yapılabilir ──

def test_running_profile_first_workspace_exposes_flow_fields(client, make_workspace):
    workspace = make_workspace(
        status="running", onboarding_flow="profile_first", profile_data=None,
    )

    response = client.get(f"/api/v1/brand-profile/workspaces/{workspace.id}")

    assert response.status_code == 200
    data = response.json()
    assert data["onboarding_flow"] == "profile_first"
    assert data["profile_approved_at"] is None  # → frontend fazı: analyzing_site


def test_profile_first_validation_finishes_before_workspace_row_lock(
    db_session, make_workspace, monkeypatch,
):
    """Competitor telemetry must not wait on this task's workspace row lock."""
    workspace = make_workspace(
        status="pending",
        onboarding_flow="profile_first",
        profile_data=None,
        competitor_urls=["https://competitor.test"],
    )
    workspace_id = workspace.id
    db_session.commit()

    class FakeAI:
        collector = None

        def close(self):
            return None

    class FakeExtractor:
        def __init__(self, _ai):
            pass

        def crawl_for_profile_content(self, _url):
            return {
                "error": None,
                "site_content": "Hisse analiz platformu",
                "source_pages": ["https://hissefy.test"],
            }

        def extract_profile_from_site_content(self, _content):
            return dict(VALID_PROFILE)

        def validate_with_competitors(self, _profile, competitor_urls):
            assert competitor_urls == ["https://competitor.test"]
            telemetry_db = SessionLocal()
            try:
                # If validation is moved under FOR UPDATE again, fail quickly
                # instead of leaving the test suite hanging like production.
                telemetry_db.execute(text("SET LOCAL lock_timeout = '250ms'"))
                telemetry_db.add(
                    AiUsageEvent(
                        brand_profile_id=workspace_id,
                        request_id=f"profile-lock-regression-{workspace_id}",
                        attempt=1,
                        stage="competitor_validation",
                        model="fake",
                    )
                )
                telemetry_db.commit()
            finally:
                telemetry_db.close()
            return {"competitors": [], "consistency_score": 1.0}

    import app.core.site_analyzer.profile_extractor as extractor_module
    import app.generators.ai_service as ai_service_module

    monkeypatch.setattr(ai_service_module, "get_ai_service", lambda **_kwargs: FakeAI())
    monkeypatch.setattr(extractor_module, "ProfileExtractor", FakeExtractor)

    brand_profile_api._run_profile_analysis_first(
        workspace_id=workspace_id,
        company_url="https://hissefy.test",
        competitor_urls=["https://competitor.test"],
    )

    db_session.expire_all()
    updated = db_session.get(BrandProfile, workspace_id)
    assert updated.status == "profile_review"
    assert updated.validation_data["consistency_score"] == 1.0
    assert (
        db_session.query(AiUsageEvent)
        .filter(AiUsageEvent.request_id == f"profile-lock-regression-{workspace_id}")
        .count()
        == 1
    )


def test_profile_first_db_write_failure_rolls_back_and_marks_failed(
    db_session, make_workspace, monkeypatch,
):
    """A failed final write must not leave the UI polling status=running."""
    workspace = make_workspace(
        status="pending",
        onboarding_flow="profile_first",
        profile_data=None,
    )
    workspace_id = workspace.id
    db_session.commit()

    class FakeAI:
        collector = None

        def close(self):
            return None

    class FakeExtractor:
        def __init__(self, _ai):
            pass

        def crawl_for_profile_content(self, _url):
            return {
                "error": None,
                "site_content": "Hisse analiz platformu",
                "source_pages": ["https://hissefy.test"],
            }

        def extract_profile_from_site_content(self, _content):
            return dict(VALID_PROFILE)

    def fail_final_write(db, _workspace, _profile):
        # Put the SQLAlchemy transaction into a failed state. The exception
        # handler must rollback before it can persist status=failed.
        db.execute(text("SELECT 1 / 0"))

    import app.core.site_analyzer.profile_extractor as extractor_module
    import app.generators.ai_service as ai_service_module

    monkeypatch.setattr(ai_service_module, "get_ai_service", lambda **_kwargs: FakeAI())
    monkeypatch.setattr(extractor_module, "ProfileExtractor", FakeExtractor)
    monkeypatch.setattr(brand_profile_api, "apply_profile_data_update", fail_final_write)

    brand_profile_api._run_profile_analysis_first(
        workspace_id=workspace_id,
        company_url="https://hissefy.test",
        competitor_urls=[],
    )

    db_session.expire_all()
    updated = db_session.get(BrandProfile, workspace_id)
    assert updated.status == "failed"
    assert updated.error_message


# ── profile/approve ───────────────────────────────────────────────

def test_profile_approve_merges_excludes_rebuilds_anchors_and_starts_keywords(
    client, db_session, make_workspace, monkeypatch,
):
    kw_calls = []
    monkeypatch.setattr(
        brand_profile_api, "_run_keyword_suggestion_from_profile",
        lambda **kw: kw_calls.append(kw),
    )
    workspace = make_workspace(
        status="profile_review",
        onboarding_flow="profile_first",
        profile_data=dict(VALID_PROFILE),
        suggested_keywords=["eski kw"],
    )

    response = client.put(
        f"/api/v1/brand-profile/workspaces/{workspace.id}/profile/approve",
        json={
            "profile_data": {
                "use_cases": ["Portfoy izleme"],
                "anchor_texts": ["MANUEL ANCHOR"],
            },
            "must_have_info": "günlük hisse önerileri",
            "excluded_info": "temettü takibi",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "running"
    assert data["profile_approved_at"] is not None
    assert data["suggested_keywords"] is None  # eski liste temizlendi
    profile = data["profile_data"]
    assert "temettü takibi" in profile["exclude_themes"]
    assert "kripto para" in profile["exclude_themes"]
    blob = " ".join(profile["anchor_texts"]).lower()
    assert "manuel anchor" not in blob
    assert "portfoy izleme" in blob
    # Dispatch (2.2): yeni attempt token'ı satıra yazılır ve task'a geçirilir
    assert len(kw_calls) == 1
    assert kw_calls[0]["workspace_id"] == workspace.id
    assert kw_calls[0]["attempt_id"]
    db_session.expire_all()
    assert db_session.get(BrandProfile, workspace.id).analysis_attempt_id == (
        kw_calls[0]["attempt_id"]
    )


def test_profile_approve_inline_edit_keeps_status_in_keywords_review(
    client, make_workspace, monkeypatch,
):
    monkeypatch.setattr(
        brand_profile_api, "_run_keyword_suggestion_from_profile",
        lambda **kw: (_ for _ in ()).throw(AssertionError("rerun=false iken çağrılmamalı")),
    )
    workspace = make_workspace(
        status="keywords_review",
        onboarding_flow="profile_first",
        profile_data=dict(VALID_PROFILE),
        suggested_keywords=["hisse analiz"],
    )

    response = client.put(
        f"/api/v1/brand-profile/workspaces/{workspace.id}/profile/approve",
        json={
            "profile_data": {"products": ["Analiz Platformu", "Tarama Motoru"]},
            "rerun_keywords": False,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "keywords_review"
    assert data["suggested_keywords"] == ["hisse analiz"]  # dokunulmadı
    assert "Tarama Motoru" in data["profile_data"]["products"]


def test_profile_approve_can_defer_keywords_until_competitor_review(
    client, make_workspace, monkeypatch,
):
    keyword_calls = []
    monkeypatch.setattr(
        brand_profile_api,
        "_run_keyword_suggestion_from_profile",
        lambda **kwargs: keyword_calls.append(kwargs),
    )
    workspace = make_workspace(
        status="profile_review",
        onboarding_flow="profile_first",
        profile_data=dict(VALID_PROFILE),
    )

    deferred = client.put(
        f"/api/v1/brand-profile/workspaces/{workspace.id}/profile/approve",
        json={"rerun_keywords": False},
    )
    assert deferred.status_code == 200
    assert deferred.json()["status"] == "competitor_review"
    assert deferred.json()["profile_approved_at"] is not None
    assert keyword_calls == []

    continued = client.put(
        f"/api/v1/brand-profile/workspaces/{workspace.id}/profile/approve",
        json={"rerun_keywords": True},
    )
    assert continued.status_code == 200
    assert continued.json()["status"] == "running"
    assert len(keyword_calls) == 1
    assert keyword_calls[0]["workspace_id"] == workspace.id
    assert keyword_calls[0]["attempt_id"]


def test_active_competitor_discovery_blocks_keyword_start(
    client, db_session, make_workspace,
):
    workspace = make_workspace(
        status="competitor_review",
        onboarding_flow="profile_first",
        profile_data=dict(VALID_PROFILE),
    )
    db_session.add(TaskResult(
        task_id="active-discovery",
        task_type="competitor_discovery",
        brand_profile_id=workspace.id,
        status="running",
        progress=30,
    ))
    db_session.commit()

    response = client.put(
        f"/api/v1/brand-profile/workspaces/{workspace.id}/profile/approve",
        json={"rerun_keywords": True},
    )
    assert response.status_code == 409
    assert "competitor_discovery" in str(response.json()["detail"])


def test_keyword_task_excludes_approved_competitor_terms(
    db_session, make_workspace, monkeypatch,
):
    captured_terms = []
    workspace = make_workspace(
        status="competitor_review",
        onboarding_flow="profile_first",
        profile_data=dict(VALID_PROFILE),
        # NOT: gercekci uzunlukta olmali (bkz. cache okuma kapisi).
        crawl_content_cache=(
            "Hisse analiz platformu. Borsa Istanbul hisseleri icin teknik ve temel "
            "analiz araclari sunuyoruz. Portfoy takibi, hisse tarama, finansal "
            "tablo analizi ve gercek zamanli fiyat verisi ozelliklerimiz vardir. "
            "Yatirimcilar icin karar destek raporlari uretiyoruz."
        ),
        competitor_terms=[{
            "term": "Fintables",
            "status": "approved",
            "source": "ai_discovery",
            "source_urls": [],
            "manual_approved": True,
        }],
    )
    workspace_id = workspace.id
    db_session.commit()

    class FakeAI:
        collector = None

        def close(self):
            return None

    class FakeExtractor:
        def __init__(self, _ai):
            pass

        def suggest_keywords_from_profile(self, _site, _profile, **kwargs):
            captured_terms.extend(kwargs["competitor_terms"])
            return ["fintables fiyat", "hisse analiz"]

    import app.core.site_analyzer.profile_extractor as extractor_module
    import app.generators.ai_service as ai_service_module

    monkeypatch.setattr(ai_service_module, "get_ai_service", lambda **_kwargs: FakeAI())
    monkeypatch.setattr(extractor_module, "ProfileExtractor", FakeExtractor)

    brand_profile_api._run_keyword_suggestion_from_profile(workspace_id)

    db_session.expire_all()
    updated = db_session.get(BrandProfile, workspace_id)
    assert captured_terms == ["Fintables"]
    assert updated.status == "keywords_review"
    assert updated.suggested_keywords == ["hisse analiz"]


def test_profile_approve_rejects_legacy_workspace_and_wrong_status(
    client, make_workspace,
):
    legacy = make_workspace(status="profile_review", onboarding_flow="legacy")
    response = client.put(
        f"/api/v1/brand-profile/workspaces/{legacy.id}/profile/approve", json={},
    )
    assert response.status_code == 400

    draft = make_workspace(
        name="Draft WS", status="draft", onboarding_flow="profile_first",
    )
    response = client.put(
        f"/api/v1/brand-profile/workspaces/{draft.id}/profile/approve", json={},
    )
    assert response.status_code == 400


# ── keywords/approve dallanması ───────────────────────────────────

def test_keywords_approve_profile_first_goes_confirmed_without_profile_task(
    client, make_workspace, monkeypatch,
):
    profile_calls = []
    monkeypatch.setattr(
        brand_profile_api, "_run_profile_from_keywords",
        lambda **kw: profile_calls.append(kw),
    )
    workspace = make_workspace(
        status="keywords_review",
        onboarding_flow="profile_first",
        profile_data=dict(VALID_PROFILE),
        suggested_keywords=["hisse analiz"],
    )

    response = client.put(
        f"/api/v1/brand-profile/workspaces/{workspace.id}/keywords/approve",
        json={"keywords": ["hisse analiz", "borsa takip"]},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "confirmed"
    assert data["suggested_keywords"] == ["hisse analiz", "borsa takip"]
    assert profile_calls == []  # legacy profil üretimi ÇAĞRILMADI


def test_keywords_approve_legacy_behavior_unchanged(client, make_workspace, monkeypatch):
    calls = []
    monkeypatch.setattr(
        brand_profile_api, "_run_profile_from_keywords",
        lambda workspace_id, keywords, attempt_id: calls.append((workspace_id, keywords)),
    )
    workspace = make_workspace(
        status="keywords_review",
        onboarding_flow="legacy",
        suggested_keywords=["hisse analiz"],
        profile_data=None,
    )

    response = client.put(
        f"/api/v1/brand-profile/workspaces/{workspace.id}/keywords/approve",
        json={"keywords": ["teknik analiz"]},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "running"
    assert calls == [(workspace.id, ["teknik analiz"])]


# ── anchors/preview ───────────────────────────────────────────────

def test_anchors_preview_groups_and_ignores_manual_anchor_payload(
    client, make_workspace,
):
    workspace = make_workspace(
        status="keywords_review",
        onboarding_flow="profile_first",
        profile_data=dict(VALID_PROFILE),
    )

    response = client.post(
        f"/api/v1/brand-profile/workspaces/{workspace.id}/anchors/preview",
        json={
            "profile_data": {
                "use_cases": ["Portfoy izleme"],
                "anchor_texts": ["MANUEL ANCHOR"],
            }
        },
    )

    assert response.status_code == 200
    groups = response.json()["groups"]
    assert groups, "en az bir anchor grubu dönmeli"
    blob = " ".join(g["anchor"] for g in groups).lower()
    assert "manuel anchor" not in blob
    assert "portfoy izleme" in blob
    fields = {g["source_field"] for g in groups}
    assert "products" in fields
    assert all(g["label"] for g in groups)


def test_anchors_preview_without_override_uses_stored_profile(client, make_workspace):
    workspace = make_workspace(
        status="confirmed",
        onboarding_flow="profile_first",
        profile_data=dict(VALID_PROFILE),
    )

    response = client.post(
        f"/api/v1/brand-profile/workspaces/{workspace.id}/anchors/preview",
        json={},
    )

    assert response.status_code == 200
    blob = " ".join(g["anchor"] for g in response.json()["groups"]).lower()
    assert "borsa takibi" in blob
