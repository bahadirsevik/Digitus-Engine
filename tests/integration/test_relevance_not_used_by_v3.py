"""V3 motoru embedding relevance'ı kullanmaz (plan_yapilacaklar 1.4).

- Manuel `POST /runs/{id}/relevance/compute` v3 run'ında tipli 409
  RELEVANCE_NOT_USED_BY_V3 döner; embedding çağrısı ve DB yazımı YOK.
- Otomatik tetikleyici (`_trigger_latest_run_relevance`) v3 `scored` run'ı
  için relevance seçmez / dispatch etmez.
- Okuma yolu (`GET .../relevance`) eski veri için çalışmaya devam eder.
"""
from __future__ import annotations

import pytest
from fastapi import BackgroundTasks

from app.config import settings
from app.database.models import KeywordRelevance, ScoringRun


@pytest.fixture
def embed_spy(monkeypatch):
    """Embedding/relevance çağrılırsa kayıt düşer ve patlar."""
    calls: list[str] = []

    def boom(label):
        def _boom(*a, **k):
            calls.append(label)
            raise AssertionError(label)
        return _boom

    monkeypatch.setattr("app.core.relevance.refresh_keyword_relevance",
                        boom("refresh_keyword_relevance"))
    monkeypatch.setattr("app.api.v1.brand_profile._run_relevance_computation",
                        boom("_run_relevance_computation"))
    monkeypatch.setattr("app.core.site_analyzer.relevance_scorer.RelevanceScorer",
                        boom("RelevanceScorer"))
    monkeypatch.setattr(settings, "ENABLE_RELEVANCE_RERANK", True)
    return calls


@pytest.fixture
def api(client, db_session):
    from app.dependencies import get_db
    from app.main import app

    def _db():
        yield db_session

    app.dependency_overrides[get_db] = _db
    try:
        yield client
    finally:
        app.dependency_overrides.pop(get_db, None)


def _env(make_workspace, make_keyword, make_scoring_run, *, version="v3",
         status="scored"):
    ws = make_workspace(
        name=f"rel-{version}-ws",
        status="confirmed",
        profile_data={"anchor_texts": ["çapa"], "brand_name": "Marka"},
    )
    kw = make_keyword(f"rel kelime {version}", brand_profile_id=ws.id)
    run = make_scoring_run(brand_profile_id=ws.id, status=status,
                           algorithm_version=version, skip_relevance=False)
    return ws, kw, run


def test_compute_endpoint_rejects_v3_with_typed_409(
        api, db_session, make_workspace, make_keyword, make_scoring_run,
        embed_spy):
    ws, kw, run = _env(make_workspace, make_keyword, make_scoring_run)

    resp = api.post(f"/api/v1/brand-profile/runs/{run.id}/relevance/compute"
                    f"?brand_profile_id={ws.id}")

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "RELEVANCE_NOT_USED_BY_V3"
    assert detail["message"]
    assert embed_spy == []
    db_session.expire_all()
    assert db_session.query(KeywordRelevance).filter(
        KeywordRelevance.scoring_run_id == run.id).count() == 0
    assert db_session.get(ScoringRun, run.id).status == "scored"


def test_compute_endpoint_legacy_gate_unchanged(
        api, db_session, make_workspace, make_keyword, make_scoring_run,
        embed_spy):
    ws, kw, run = _env(make_workspace, make_keyword, make_scoring_run,
                       version="v2")

    resp = api.post(f"/api/v1/brand-profile/runs/{run.id}/relevance/compute"
                    f"?brand_profile_id={ws.id}")

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "LEGACY_RUN_READ_ONLY"
    assert embed_spy == []


@pytest.mark.parametrize("version", ["v3", "v2"])
def test_auto_trigger_never_dispatches_relevance(
        db_session, make_workspace, make_keyword, make_scoring_run,
        embed_spy, version):
    """Profil/keyword onayı sonrası otomatik tetikleyici: v3 `scored` run'ı
    relevance_computing'e ALINMAZ ve arka plan işi kuyruğa girmez."""
    from app.api.v1 import brand_profile as bp

    ws, kw, run = _env(make_workspace, make_keyword, make_scoring_run,
                       version=version)
    bg = BackgroundTasks()

    bp._trigger_latest_run_relevance(db_session, bg, ws)

    assert bg.tasks == []
    assert embed_spy == []
    db_session.expire_all()
    assert db_session.get(ScoringRun, run.id).status == "scored"


def test_get_relevance_still_reads_old_data(
        api, db_session, make_workspace, make_keyword, make_scoring_run,
        embed_spy):
    ws, kw, run = _env(make_workspace, make_keyword, make_scoring_run)
    db_session.add(KeywordRelevance(
        scoring_run_id=run.id, keyword_id=kw.id, relevance_score=0.812,
        matched_anchor="çapa", method="embedding"))
    db_session.commit()

    resp = api.get(f"/api/v1/brand-profile/runs/{run.id}/relevance"
                   f"?brand_profile_id={ws.id}")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["keyword_id"] == kw.id
    assert body[0]["relevance_score"] == pytest.approx(0.812)
    assert embed_spy == []
