"""Kullaniciya giden 5xx detail ve task error_message ham istisna metni tasimaz."""
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.database.models import TaskResult

SECRET = "psycopg2 host=10.0.0.5 password=hunter2"


def test_global_handler_hides_raw_exception_outside_debug(monkeypatch):
    from app.config import settings
    from app.main import app

    monkeypatch.setattr(settings, "DEBUG", False)

    async def _boom():
        raise RuntimeError(SECRET)

    app.add_api_route("/__boom", _boom)
    try:
        res = TestClient(app, raise_server_exceptions=False).get("/__boom")
    finally:
        app.router.routes.pop()
    assert res.status_code == 500
    assert SECRET not in res.text
    assert "Beklenmeyen bir sunucu hatası" in res.json()["detail"]


def test_google_ads_unknown_error_502_hides_raw_text(monkeypatch):
    from app.api.v1.google_ads import _raise_google_ads_error
    from app.config import settings

    monkeypatch.setattr(settings, "DEBUG", False)
    with pytest.raises(HTTPException) as info:
        _raise_google_ads_error(RuntimeError(SECRET))
    assert info.value.status_code == 502
    assert SECRET not in info.value.detail


def test_ads_dispatch_failure_message_is_safe(db_session):
    from app.generators.ads.generation_sets import mark_dispatch_failed

    db_session.add(TaskResult(task_id="t-ads", task_type="ads", status="pending", progress=0))
    db_session.commit()
    mark_dispatch_failed(db_session, "t-ads", SECRET)
    row = db_session.query(TaskResult).filter_by(task_id="t-ads").one()
    assert row.status == "failed"
    assert SECRET not in row.error_message
    assert "kuyruğa alınamadı" in row.error_message


def test_preview_task_unexpected_error_is_safe(db_session):
    from app.tasks.policy_preview_tasks import run_competitor_preview_task

    db_session.add(TaskResult(task_id="t-prev", task_type="policy_preview", status="pending", progress=0))
    db_session.commit()
    with patch("app.core.policy.normalize_competitor_url", side_effect=RuntimeError(SECRET)):
        run_competitor_preview_task.apply(args=(1, ["https://a.com"]), task_id="t-prev")
    db_session.expire_all()
    row = db_session.query(TaskResult).filter_by(task_id="t-prev").one()
    assert row.status == "failed"
    assert SECRET not in row.error_message
    assert "Politika önizlemesi" in row.error_message
