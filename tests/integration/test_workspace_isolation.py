"""
Cross-workspace isolation tests (plan2 §P0/C2-C5).

C2 phase: scoring + channels endpoints + verify_scoring_run helper.
Subsequent commits (C3, C4, C5) will extend this file with keywords/tasks/
export coverage.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.core.workspace import verify_scoring_run, verify_workspace


# ===========================================================================
# verify_scoring_run helper — direct unit tests
# ===========================================================================


def test_verify_scoring_run_mutating_requires_brand_profile_id(db_session, make_workspace, make_scoring_run):
    """Mutating call without brand_profile_id must 400 with the standard message."""
    ws = make_workspace(name="A", status="confirmed")
    run = make_scoring_run(brand_profile_id=ws.id)

    with pytest.raises(HTTPException) as exc:
        verify_scoring_run(db_session, run.id, brand_profile_id=None, mutating=True)
    assert exc.value.status_code == 400
    assert exc.value.detail == "brand_profile_id is required"


def test_verify_scoring_run_returns_run_when_workspace_matches(
    db_session, make_workspace, make_scoring_run
):
    ws = make_workspace(name="A", status="confirmed")
    run = make_scoring_run(brand_profile_id=ws.id)

    got = verify_scoring_run(db_session, run.id, brand_profile_id=ws.id, mutating=True)
    assert got.id == run.id


def test_verify_scoring_run_404_when_workspace_mismatch(
    db_session, make_workspace, make_scoring_run
):
    """A run from workspace A must be invisible to workspace B."""
    ws_a = make_workspace(name="A", status="confirmed")
    ws_b = make_workspace(name="B", status="confirmed")
    run_in_a = make_scoring_run(brand_profile_id=ws_a.id)

    with pytest.raises(HTTPException) as exc:
        verify_scoring_run(db_session, run_in_a.id, brand_profile_id=ws_b.id, mutating=True)
    assert exc.value.status_code == 404
    assert "not found" in exc.value.detail


def test_verify_scoring_run_404_when_run_missing(db_session, make_workspace):
    ws = make_workspace(name="A", status="confirmed")

    with pytest.raises(HTTPException) as exc:
        verify_scoring_run(db_session, run_id=99999, brand_profile_id=ws.id, mutating=True)
    assert exc.value.status_code == 404


def test_verify_scoring_run_legacy_read_path_warns_but_succeeds(
    db_session, make_workspace, make_scoring_run
):
    """Read-only access without workspace logs a warning but still returns the run."""
    ws = make_workspace(name="A", status="confirmed")
    run = make_scoring_run(brand_profile_id=ws.id)

    got = verify_scoring_run(db_session, run.id, brand_profile_id=None, mutating=False)
    assert got.id == run.id


# ===========================================================================
# Scoring endpoint isolation via TestClient
# ===========================================================================


def test_list_scoring_runs_filters_by_workspace(client, make_workspace, make_scoring_run):
    ws_a = make_workspace(name="A", status="confirmed")
    ws_b = make_workspace(name="B", status="confirmed")
    run_a = make_scoring_run(brand_profile_id=ws_a.id, name="run_in_a")
    run_b = make_scoring_run(brand_profile_id=ws_b.id, name="run_in_b")

    resp = client.get("/api/v1/scoring/runs", params={"brand_profile_id": ws_a.id})
    assert resp.status_code == 200
    ids = {r["id"] for r in resp.json()}
    assert run_a.id in ids
    assert run_b.id not in ids


def test_list_scoring_runs_requires_brand_profile_id(client, make_workspace, make_scoring_run):
    ws = make_workspace(name="A", status="confirmed")
    make_scoring_run(brand_profile_id=ws.id, name="run_in_a")

    resp = client.get("/api/v1/scoring/runs")
    assert resp.status_code == 422


def test_get_scoring_run_cross_workspace_returns_404(client, make_workspace, make_scoring_run):
    ws_a = make_workspace(name="A", status="confirmed")
    ws_b = make_workspace(name="B", status="confirmed")
    run_in_a = make_scoring_run(brand_profile_id=ws_a.id)

    resp = client.get(
        f"/api/v1/scoring/runs/{run_in_a.id}",
        params={"brand_profile_id": ws_b.id},
    )
    assert resp.status_code == 404


def test_delete_scoring_run_requires_brand_profile_id(client, make_workspace, make_scoring_run):
    ws = make_workspace(name="A", status="confirmed")
    run = make_scoring_run(brand_profile_id=ws.id)

    resp = client.delete(f"/api/v1/scoring/runs/{run.id}")
    assert resp.status_code in (400, 422)
    if resp.status_code == 400:
        assert "brand_profile_id" in resp.json().get("detail", "").lower()


def test_delete_scoring_run_cross_workspace_returns_404(
    client, make_workspace, make_scoring_run
):
    ws_a = make_workspace(name="A", status="confirmed")
    ws_b = make_workspace(name="B", status="confirmed")
    run_in_a = make_scoring_run(brand_profile_id=ws_a.id)

    resp = client.delete(
        f"/api/v1/scoring/runs/{run_in_a.id}",
        params={"brand_profile_id": ws_b.id},
    )
    assert resp.status_code == 404


def test_get_scoring_results_cross_workspace_returns_404(
    client, make_workspace, make_scoring_run
):
    ws_a = make_workspace(name="A", status="confirmed")
    ws_b = make_workspace(name="B", status="confirmed")
    run_in_a = make_scoring_run(brand_profile_id=ws_a.id, status="scored")

    resp = client.get(
        f"/api/v1/scoring/runs/{run_in_a.id}/scores",
        params={"brand_profile_id": ws_b.id},
    )
    assert resp.status_code == 404


def test_get_scoring_results_requires_brand_profile_id(
    client, make_workspace, make_scoring_run
):
    ws = make_workspace(name="A", status="confirmed")
    run = make_scoring_run(brand_profile_id=ws.id, status="scored")

    resp = client.get(f"/api/v1/scoring/runs/{run.id}/scores")
    assert resp.status_code == 422


def test_get_top_by_channel_cross_workspace_returns_404(
    client, make_workspace, make_scoring_run
):
    ws_a = make_workspace(name="A", status="confirmed")
    ws_b = make_workspace(name="B", status="confirmed")
    run_in_a = make_scoring_run(brand_profile_id=ws_a.id)

    resp = client.get(
        f"/api/v1/scoring/runs/{run_in_a.id}/top/ADS",
        params={"brand_profile_id": ws_b.id},
    )
    assert resp.status_code == 404


# ===========================================================================
# Channels endpoint isolation
# ===========================================================================


def test_get_channel_pools_cross_workspace_returns_404(
    client, make_workspace, make_scoring_run
):
    ws_a = make_workspace(name="A", status="confirmed")
    ws_b = make_workspace(name="B", status="confirmed")
    run_in_a = make_scoring_run(brand_profile_id=ws_a.id)

    resp = client.get(
        f"/api/v1/channels/runs/{run_in_a.id}/pools",
        params={"brand_profile_id": ws_b.id},
    )
    assert resp.status_code == 404


def test_get_channel_pools_requires_brand_profile_id(
    client, make_workspace, make_scoring_run
):
    ws = make_workspace(name="A", status="confirmed")
    run = make_scoring_run(brand_profile_id=ws.id)

    resp = client.get(f"/api/v1/channels/runs/{run.id}/pools")
    assert resp.status_code == 422


def test_get_single_channel_pool_cross_workspace_returns_404(
    client, make_workspace, make_scoring_run
):
    ws_a = make_workspace(name="A", status="confirmed")
    ws_b = make_workspace(name="B", status="confirmed")
    run_in_a = make_scoring_run(brand_profile_id=ws_a.id)

    resp = client.get(
        f"/api/v1/channels/runs/{run_in_a.id}/pools/ADS",
        params={"brand_profile_id": ws_b.id},
    )
    assert resp.status_code == 404


def test_run_channel_assignment_requires_brand_profile_id(
    client, make_workspace, make_scoring_run
):
    ws = make_workspace(name="A", status="confirmed")
    run = make_scoring_run(brand_profile_id=ws.id)

    resp = client.post(f"/api/v1/channels/runs/{run.id}/assign")
    assert resp.status_code in (400, 422)


# ===========================================================================
# Keywords endpoint isolation (plan2 §P0/C3)
# ===========================================================================


def test_list_keywords_requires_brand_profile_id(client):
    resp = client.get("/api/v1/keywords/")
    assert resp.status_code == 400
    assert "brand_profile_id" in resp.json()["detail"].lower()


def test_list_keywords_filters_by_workspace(client, make_workspace, make_keyword):
    ws_a = make_workspace(name="A")
    ws_b = make_workspace(name="B")
    make_keyword("kw_a", brand_profile_id=ws_a.id)
    make_keyword("kw_b", brand_profile_id=ws_b.id)

    resp = client.get("/api/v1/keywords/", params={"brand_profile_id": ws_a.id})
    assert resp.status_code == 200
    keywords = {item["keyword"] for item in resp.json()["items"]}
    assert "kw_a" in keywords
    assert "kw_b" not in keywords


def test_get_keyword_cross_workspace_returns_404(client, make_workspace, make_keyword):
    ws_a = make_workspace(name="A")
    ws_b = make_workspace(name="B")
    kw_in_a = make_keyword("private_to_a", brand_profile_id=ws_a.id)

    resp = client.get(
        f"/api/v1/keywords/{kw_in_a.id}",
        params={"brand_profile_id": ws_b.id},
    )
    assert resp.status_code == 404


def test_delete_all_keywords_requires_brand_profile_id(client):
    """The most dangerous endpoint: must 400 without workspace, never silently
    fall through to the (now removed) global delete path."""
    resp = client.delete("/api/v1/keywords/all")
    assert resp.status_code == 400
    assert "brand_profile_id" in resp.json()["detail"].lower()


def test_delete_all_keywords_only_unlinks_within_workspace(
    client, make_workspace, make_keyword, db_session
):
    """DELETE /keywords/all with workspace must NOT touch global Keyword rows
    nor other workspaces' links."""
    from app.database.models import Keyword, WorkspaceKeyword

    ws_a = make_workspace(name="A")
    ws_b = make_workspace(name="B")
    make_keyword("kw_in_a", brand_profile_id=ws_a.id)
    kw_b = make_keyword("kw_in_b", brand_profile_id=ws_b.id)

    resp = client.delete("/api/v1/keywords/all", params={"brand_profile_id": ws_a.id})
    assert resp.status_code == 204

    # ws_b's link must remain.
    remaining = (
        db_session.query(WorkspaceKeyword)
        .filter(WorkspaceKeyword.brand_profile_id == ws_b.id)
        .all()
    )
    assert any(link.keyword_id == kw_b.id for link in remaining)
    # Global Keyword rows untouched.
    total_keywords = db_session.query(Keyword).count()
    assert total_keywords == 2


def test_delete_keyword_cross_workspace_returns_404(client, make_workspace, make_keyword):
    ws_a = make_workspace(name="A")
    ws_b = make_workspace(name="B")
    kw_in_a = make_keyword("kw", brand_profile_id=ws_a.id)

    resp = client.delete(
        f"/api/v1/keywords/{kw_in_a.id}",
        params={"brand_profile_id": ws_b.id},
    )
    assert resp.status_code == 404


def test_delete_keyword_requires_brand_profile_id(client, make_workspace, make_keyword):
    ws = make_workspace(name="A")
    kw = make_keyword("kw", brand_profile_id=ws.id)

    resp = client.delete(f"/api/v1/keywords/{kw.id}")
    assert resp.status_code == 400


def test_update_shared_keyword_rejected_to_prevent_cross_workspace_mutation(
    client, db_session, make_workspace, make_keyword
):
    from app.database.models import Keyword, WorkspaceKeyword

    ws_a = make_workspace(name="A")
    ws_b = make_workspace(name="B")
    kw = make_keyword("shared keyword", brand_profile_id=ws_a.id)
    db_session.add(
        WorkspaceKeyword(
            brand_profile_id=ws_b.id,
            keyword_id=kw.id,
            data_source="manual",
            monthly_volume=10,
            trend_3m=0,
            trend_12m=0,
            competition_score=0.5,
        )
    )
    db_session.commit()

    resp = client.put(
        f"/api/v1/keywords/{kw.id}",
        params={"brand_profile_id": ws_a.id},
        json={"keyword": "changed globally"},
    )
    assert resp.status_code == 409
    db_session.refresh(kw)
    assert db_session.query(Keyword).filter(Keyword.id == kw.id).one().keyword == "shared keyword"


def test_import_keywords_requires_brand_profile_id(client):
    resp = client.post(
        "/api/v1/keywords/import",
        json={"keywords": [{"keyword": "x"}]},
    )
    assert resp.status_code in (400, 422)


def test_cleanup_duplicates_requires_brand_profile_id(client):
    resp = client.post("/api/v1/keywords/cleanup-duplicates")
    assert resp.status_code == 400


# ===========================================================================
# Tasks endpoint isolation (plan2 §P0/C4)
# ===========================================================================


def _make_task_for_run(db_session, run_id, task_id="t-1", status="running"):
    """Insert a TaskResult row for testing — bypasses Celery."""
    from app.database.models import TaskResult

    task = TaskResult(
        task_id=task_id,
        task_type="channel_assignment",
        scoring_run_id=run_id,
        status=status,
        progress=50,
    )
    db_session.add(task)
    db_session.commit()
    db_session.refresh(task)
    return task


def test_list_tasks_requires_brand_profile_id(client):
    resp = client.get("/api/v1/tasks/")
    assert resp.status_code == 400


def test_list_tasks_filtered_by_workspace(
    client, db_session, make_workspace, make_scoring_run
):
    ws_a = make_workspace(name="A")
    ws_b = make_workspace(name="B")
    run_a = make_scoring_run(brand_profile_id=ws_a.id)
    run_b = make_scoring_run(brand_profile_id=ws_b.id)
    _make_task_for_run(db_session, run_a.id, task_id="task-a")
    _make_task_for_run(db_session, run_b.id, task_id="task-b")

    resp = client.get("/api/v1/tasks/", params={"brand_profile_id": ws_a.id})
    assert resp.status_code == 200
    ids = {t["task_id"] for t in resp.json()["tasks"]}
    assert "task-a" in ids
    assert "task-b" not in ids


def test_get_task_cross_workspace_returns_404(
    client, db_session, make_workspace, make_scoring_run
):
    ws_a = make_workspace(name="A")
    ws_b = make_workspace(name="B")
    run_a = make_scoring_run(brand_profile_id=ws_a.id)
    _make_task_for_run(db_session, run_a.id, task_id="task-secret-a")

    resp = client.get(
        "/api/v1/tasks/task-secret-a",
        params={"brand_profile_id": ws_b.id},
    )
    assert resp.status_code == 404


def test_get_tasks_for_run_cross_workspace_returns_404(
    client, make_workspace, make_scoring_run
):
    ws_a = make_workspace(name="A")
    ws_b = make_workspace(name="B")
    run_a = make_scoring_run(brand_profile_id=ws_a.id)

    resp = client.get(
        f"/api/v1/tasks/run/{run_a.id}",
        params={"brand_profile_id": ws_b.id},
    )
    assert resp.status_code == 404


def test_cancel_task_requires_brand_profile_id(client):
    resp = client.post("/api/v1/tasks/some-id/cancel")
    assert resp.status_code == 400


# ===========================================================================
# Export endpoint isolation (plan2 §P0/C4)
# ===========================================================================


def test_create_export_requires_brand_profile_id(client, make_workspace, make_scoring_run):
    ws = make_workspace(name="A")
    run = make_scoring_run(brand_profile_id=ws.id)

    resp = client.post(
        "/api/v1/export/",
        json={
            "scoring_run_id": run.id,
            "format": "excel",
            "sections": ["summary"],
        },
    )
    assert resp.status_code in (400, 422)


def test_create_export_cross_workspace_returns_404(
    client, make_workspace, make_scoring_run
):
    """Workspace B cannot trigger export for workspace A's run."""
    ws_a = make_workspace(name="A")
    ws_b = make_workspace(name="B")
    run_in_a = make_scoring_run(brand_profile_id=ws_a.id)

    resp = client.post(
        "/api/v1/export/",
        params={"brand_profile_id": ws_b.id},
        json={
            "scoring_run_id": run_in_a.id,
            "format": "excel",
            "sections": ["summary"],
        },
    )
    assert resp.status_code == 404


def test_export_status_cross_workspace_returns_404(client, db_session, make_workspace):
    """An export id from workspace A must not be readable via workspace B."""
    import uuid
    from app.database.models import ExportJob

    ws_a = make_workspace(name="A")
    ws_b = make_workspace(name="B")

    export_id = str(uuid.uuid4())
    job = ExportJob(
        id=export_id,
        brand_profile_id=ws_a.id,
        scoring_run_id=None,
        status="completed",
        progress=100,
        format="excel",
    )
    db_session.add(job)
    db_session.commit()

    resp = client.get(
        f"/api/v1/export/{export_id}/status",
        params={"brand_profile_id": ws_b.id},
    )
    assert resp.status_code == 404


def test_list_exports_for_run_requires_brand_profile_id(
    client, make_workspace, make_scoring_run
):
    ws = make_workspace(name="A")
    run = make_scoring_run(brand_profile_id=ws.id)
    resp = client.get(f"/api/v1/export/run/{run.id}")
    assert resp.status_code in (400, 422)


def test_list_exports_for_run_cross_workspace_returns_404(
    client, make_workspace, make_scoring_run
):
    ws_a = make_workspace(name="A")
    ws_b = make_workspace(name="B")
    run_in_a = make_scoring_run(brand_profile_id=ws_a.id)

    resp = client.get(
        f"/api/v1/export/run/{run_in_a.id}",
        params={"brand_profile_id": ws_b.id},
    )
    assert resp.status_code == 404
