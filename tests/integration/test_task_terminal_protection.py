"""Terminal TaskResult korumasi + okuma-ani bayat task uzlastirmasi."""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.core.constants import (
    TASK_PENDING_STALE_SECONDS,
    TASK_RUNNING_STALE_SECONDS,
    TASK_STALE_ERROR_MESSAGE,
)
from app.database.models import TaskResult
from app.tasks.task_status import fail_stale_tasks, update_task_status


def _row(db, task_id):
    db.expire_all()
    return db.query(TaskResult).filter_by(task_id=task_id).one()


def _mk(db, task_id, status, *, run_id=None, task_type="social_content", age_s=0, **kw):
    ts = datetime.now(timezone.utc) - timedelta(seconds=age_s)
    db.add(TaskResult(task_id=task_id, task_type=task_type, scoring_run_id=run_id,
                      status=status, progress=kw.pop("progress", 0),
                      created_at=ts, started_at=ts if status == "running" else None, **kw))
    db.commit()


def test_late_write_after_completed_changes_nothing(db_session):
    done_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    _mk(db_session, "t-done", "completed", progress=100,
        result_data={"total": 3}, completed_at=done_at)

    ok = update_task_status("t-done", status="failed", progress=10,
                            result_data={"total": 0}, error_message="late boom")

    assert ok is False
    row = _row(db_session, "t-done")
    assert row.status == "completed"
    assert row.progress == 100
    assert row.result_data == {"total": 3}
    assert row.error_message is None
    assert row.completed_at == done_at


def test_failed_and_cancelled_are_also_protected(db_session):
    _mk(db_session, "t-failed", "failed", error_message="orijinal")
    _mk(db_session, "t-canc", "cancelled")
    assert update_task_status("t-failed", status="running") is False
    assert update_task_status("t-canc", status="completed") is False
    assert _row(db_session, "t-failed").status == "failed"
    assert _row(db_session, "t-failed").error_message == "orijinal"
    assert _row(db_session, "t-canc").status == "cancelled"


def test_active_task_still_updates_and_missing_row_is_created(db_session):
    _mk(db_session, "t-run", "running", progress=40)
    assert update_task_status("t-run", progress=10) is True  # progress geri gitmez
    assert _row(db_session, "t-run").progress == 40
    assert update_task_status("t-run", status="completed", progress=100,
                              result_data={"ok": 1}) is True
    row = _row(db_session, "t-run")
    assert (row.status, row.progress, row.result_data) == ("completed", 100, {"ok": 1})
    assert row.completed_at is not None

    assert update_task_status("t-new", status="running", progress=5) is True
    assert _row(db_session, "t-new").status == "running"


def test_ads_reconcile_repair_still_works_and_result_is_then_protected(
    db_session, make_workspace, make_scoring_run
):
    from app.generators.ads.generation_sets import SET_ACTIVE, reconcile_stale_ads
    from app.database.models import AdGenerationSet

    ws = make_workspace("Terminal WS")
    run = make_scoring_run(brand_profile_id=ws.id)
    old = datetime.now(timezone.utc) - timedelta(seconds=5000)
    gen_set = AdGenerationSet(
        scoring_run_id=run.id, version_number=1, status=SET_ACTIVE,
        task_id="ads-rec", groups_count=2, failed_groups=0,
        request_snapshot={}, created_at=old)
    db_session.add(gen_set)
    _mk(db_session, "ads-rec", "running", run_id=run.id, task_type="ads", age_s=5000)

    reconcile_stale_ads(db_session, run.id)
    db_session.commit()
    assert _row(db_session, "ads-rec").status == "completed"

    # geç gelen worker yazımı onarılmış sonucu bozamaz
    assert update_task_status("ads-rec", status="failed", error_message="x") is False
    assert _row(db_session, "ads-rec").status == "completed"


def test_stale_running_task_failed_on_get(client, db_session, make_workspace, make_scoring_run):
    ws = make_workspace("Stale WS")
    run = make_scoring_run(brand_profile_id=ws.id)
    _mk(db_session, "t-stale", "running", run_id=run.id, age_s=TASK_RUNNING_STALE_SECONDS + 60)

    res = client.get(f"/api/v1/tasks/t-stale?brand_profile_id={ws.id}")

    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "failed"
    assert body["error_message"] == TASK_STALE_ERROR_MESSAGE
    assert body["completed_at"] is not None


def test_stale_pending_task_failed_on_get_and_list(client, db_session, make_workspace, make_scoring_run):
    ws = make_workspace("Stale Pending WS")
    run = make_scoring_run(brand_profile_id=ws.id)
    _mk(db_session, "t-pend", "pending", run_id=run.id, age_s=TASK_PENDING_STALE_SECONDS + 60)

    res = client.get(f"/api/v1/tasks/run/{run.id}?brand_profile_id={ws.id}")

    assert res.status_code == 200
    assert res.json()["tasks"][0]["status"] == "failed"


def test_fresh_and_exempt_tasks_untouched(client, db_session, make_workspace, make_scoring_run):
    ws = make_workspace("Fresh WS")
    run = make_scoring_run(brand_profile_id=ws.id)
    _mk(db_session, "t-fresh", "running", run_id=run.id, age_s=600)
    _mk(db_session, "t-pend-fresh", "pending", run_id=run.id, age_s=600)
    _mk(db_session, "t-ads", "running", run_id=run.id, task_type="ads",
        age_s=TASK_RUNNING_STALE_SECONDS + 600)
    _mk(db_session, "t-screen", "pending", run_id=run.id, task_type="corpus_screening",
        age_s=TASK_PENDING_STALE_SECONDS + 600)
    _mk(db_session, "t-done", "completed", run_id=run.id,
        age_s=TASK_RUNNING_STALE_SECONDS + 600)
    # SEO bulk parent chord boyunca running kalir; finalize'a kadar muaf
    _mk(db_session, "t-seo-parent", "running", run_id=run.id, task_type="seo_content",
        age_s=TASK_RUNNING_STALE_SECONDS + 600)

    for tid in ("t-fresh", "t-pend-fresh", "t-ads", "t-screen", "t-done", "t-seo-parent"):
        assert client.get(f"/api/v1/tasks/{tid}?brand_profile_id={ws.id}").status_code == 200
    assert fail_stale_tasks(scoring_run_id=run.id) == 0

    assert _row(db_session, "t-fresh").status == "running"
    assert _row(db_session, "t-pend-fresh").status == "pending"
    assert _row(db_session, "t-ads").status == "running"
    assert _row(db_session, "t-screen").status == "pending"
    assert _row(db_session, "t-done").status == "completed"
    assert _row(db_session, "t-seo-parent").status == "running"


def test_list_recent_and_workspace_scope(client, db_session, make_workspace, make_scoring_run):
    ws = make_workspace("List WS")
    other = make_workspace("Other WS")
    run = make_scoring_run(brand_profile_id=ws.id)
    other_run = make_scoring_run(brand_profile_id=other.id)
    old = TASK_RUNNING_STALE_SECONDS + 60
    _mk(db_session, "t-mine", "running", run_id=run.id, task_type="social_content", age_s=old)
    _mk(db_session, "t-theirs", "running", run_id=other_run.id, task_type="social_content", age_s=old)

    res = client.get(f"/api/v1/tasks/?brand_profile_id={ws.id}")

    assert res.status_code == 200
    assert {t["task_id"]: t["status"] for t in res.json()["tasks"]} == {"t-mine": "failed"}
    assert _row(db_session, "t-theirs").status == "running"  # baska workspace'e dokunulmaz


def test_active_task_lookups_ignore_stale_tasks(db_session, make_workspace, make_scoring_run):
    from app.core.channel.assignment_dispatcher import (
        get_active_channel_assignment_task,
        get_active_generation_task,
    )
    from app.core.policy.guards import find_active_workspace_work

    ws = make_workspace("Lookup WS")
    run = make_scoring_run(brand_profile_id=ws.id)
    old = TASK_RUNNING_STALE_SECONDS + 60
    _mk(db_session, "t-assign", "running", run_id=run.id, task_type="channel_assignment", age_s=old)
    _mk(db_session, "t-gen", "running", run_id=run.id, task_type="social_content", age_s=old)

    assert get_active_channel_assignment_task(db_session, run.id) is None
    assert get_active_generation_task(db_session, run.id) is None
    assert find_active_workspace_work(db_session, ws.id) is None


def test_cancel_conflict_when_task_already_finished(client, db_session, make_workspace, make_scoring_run):
    ws = make_workspace("Cancel WS")
    run = make_scoring_run(brand_profile_id=ws.id)
    _mk(db_session, "t-cancelled", "cancelled", run_id=run.id)
    res = client.post(f"/api/v1/tasks/t-cancelled/cancel?brand_profile_id={ws.id}")
    assert res.status_code == 400

    # kontrol ile yazim arasinda bitme yarisi: update False -> 409, revoke gitmez
    _mk(db_session, "t-race", "running", run_id=run.id)
    with patch("app.api.v1.tasks.update_task_status", return_value=False),             patch("app.tasks.celery_app.celery_app.control.revoke") as revoke:
        res = client.post(f"/api/v1/tasks/t-race/cancel?brand_profile_id={ws.id}")
    assert res.status_code == 409
    revoke.assert_not_called()


def test_mark_task_running_if_active_sets_started_at(db_session):
    from app.tasks.task_status import mark_task_running_if_active

    db_session.add(TaskResult(task_id="t-start", task_type="channel_assignment", status="pending"))
    db_session.commit()
    assert mark_task_running_if_active("t-start") is True
    row = _row(db_session, "t-start")
    assert row.status == "running" and row.started_at is not None
