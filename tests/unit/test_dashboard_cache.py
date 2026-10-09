"""Unit tests for the dashboard summary cache behaviour (P5.1)."""
from __future__ import annotations

import json

from fastapi import BackgroundTasks

from app.api.v1 import dashboard as dashboard_module


class _FakeRedis:
    def __init__(self, preload: dict | None = None):
        self.store: dict = dict(preload or {})
        self.set_calls: list = []

    def get(self, key):
        return self.store.get(key)

    def setex(self, key, ttl, value):
        self.set_calls.append((key, ttl, value))
        self.store[key] = value


def test_cache_key_uses_resolved_run_id():
    assert (
        dashboard_module._summary_cache_key(workspace_id=7, run_id=42)
        == "dashboard:summary:7:42"
    )
    assert (
        dashboard_module._summary_cache_key(workspace_id=None, run_id=None)
        == "dashboard:summary:none:latest"
    )


def test_summary_cache_hit_skips_build(monkeypatch):
    """If the summary cache returns a payload, build_summary is never called."""
    cached_payload = {
        "workspace": None,
        "active_run": None,
        "runs": [],
        "keyword_count": 0,
        "pipeline": [],
        "channels": {
            "ADS": {
                "pool_count": 0,
                "expected_count": 0,
                "generated_count": 0,
                "status": "empty",
            },
            "SEO": {
                "pool_count": 0,
                "expected_count": 0,
                "generated_count": 0,
                "status": "empty",
            },
            "SOCIAL": {
                "pool_count": 0,
                "expected_count": 0,
                "generated_count": 0,
                "status": "empty",
            },
        },
        "latest_export": None,
        "blocking_task": None,
        "active_tasks": [],
        "health": {"api": "ok", "google_ads": "ok"},
        "next_action": {
            "key": "cached_action",
            "label": "Cached",
            "path": "/x",
            "severity": "primary",
            "reason": "cached",
        },
    }

    fake_redis = _FakeRedis(
        preload={"dashboard:summary:none:latest": json.dumps(cached_payload)}
    )
    monkeypatch.setattr(dashboard_module, "_get_redis_client", lambda: fake_redis)

    build_calls = {"n": 0}

    def _fake_build(*args, **kwargs):
        build_calls["n"] += 1
        raise AssertionError("build_summary should not run on cache hit")

    monkeypatch.setattr(dashboard_module, "build_summary", _fake_build)
    monkeypatch.setattr(dashboard_module, "_gads_health", lambda *, refresh: ("ok", None))

    result = dashboard_module.workspace_summary(
        background_tasks=BackgroundTasks(),
        brand_profile_id=None,
        active_run_id=None,
        refresh=False,
        refresh_health=False,
        db=None,  # not used on cache hit
    )

    assert build_calls["n"] == 0
    assert result.next_action.key == "cached_action"


def test_summary_refresh_bypasses_cache(monkeypatch):
    fake_redis = _FakeRedis(
        preload={"dashboard:summary:none:latest": json.dumps({"poisoned": True})}
    )
    monkeypatch.setattr(dashboard_module, "_get_redis_client", lambda: fake_redis)

    calls = {"n": 0}

    def _fake_build(*args, **kwargs):
        calls["n"] += 1
        from app.schemas.dashboard import (
            ChannelGroup,
            ChannelProgress,
            DashboardSummary,
            HealthSummary,
            NextAction,
        )

        empty = ChannelProgress()
        return DashboardSummary(
            workspace=None,
            active_run=None,
            runs=[],
            keyword_count=0,
            pipeline=[],
            channels=ChannelGroup(ADS=empty, SEO=empty, SOCIAL=empty),
            latest_export=None,
            blocking_task=None,
            active_tasks=[],
            health=HealthSummary(api="ok", google_ads="ok"),
            next_action=NextAction(
                key="fresh",
                label="Fresh",
                path="/x",
                severity="primary",
                reason="fresh",
            ),
        )

    monkeypatch.setattr(dashboard_module, "build_summary", _fake_build)
    monkeypatch.setattr(dashboard_module, "_gads_health", lambda *, refresh: ("ok", None))
    # Yeni davranis: health cache'i soguksa ozet "unknown" ile yazilMAZ; bu test
    # cache-yeniden-yazma davranisini dogruladigi icin isitilmis health varsayilir.
    monkeypatch.setattr(dashboard_module, "_gads_health_cached", lambda: ("ok", None))

    result = dashboard_module.workspace_summary(
        background_tasks=BackgroundTasks(),
        brand_profile_id=None,
        active_run_id=None,
        refresh=True,
        refresh_health=False,
        db=None,
    )

    assert calls["n"] == 1
    assert result.next_action.key == "fresh"
    # write happens after build
    assert fake_redis.set_calls and fake_redis.set_calls[0][1] == dashboard_module.SUMMARY_TTL_SECONDS


def test_summary_failure_does_not_write_cache(monkeypatch):
    fake_redis = _FakeRedis()
    monkeypatch.setattr(dashboard_module, "_get_redis_client", lambda: fake_redis)

    def _boom(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(dashboard_module, "build_summary", _boom)
    monkeypatch.setattr(dashboard_module, "_gads_health", lambda *, refresh: ("ok", None))

    try:
        dashboard_module.workspace_summary(
            background_tasks=BackgroundTasks(),
            brand_profile_id=None,
            active_run_id=None,
            refresh=False,
            refresh_health=False,
            db=None,
        )
    except RuntimeError:
        pass

    assert fake_redis.set_calls == []
