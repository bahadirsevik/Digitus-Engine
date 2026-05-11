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
