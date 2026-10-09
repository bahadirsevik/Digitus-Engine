"""Unit tests for the dashboard next-action decision function (P5.1)."""
from __future__ import annotations

import pytest

from app.core.dashboard.next_action import (
    ChannelContext,
    ExportContext,
    NextActionContext,
    RunContext,
    TaskContext,
    build_next_action,
)


def _ctx(**overrides) -> NextActionContext:
    base = NextActionContext(
        has_workspace=True,
        workspace_status="confirmed",
        profile_ready=True,
        keyword_count=10,
        channels={
            "ADS": ChannelContext(),
            "SEO": ChannelContext(),
            "SOCIAL": ChannelContext(),
        },
    )
    for k, v in overrides.items():
        setattr(base, k, v)
    return base


def test_no_workspace_returns_create_workspace():
    action = build_next_action(_ctx(has_workspace=False, profile_ready=False))
    assert action.key == "create_workspace"
    assert action.path == "/brand-profile"
    assert action.severity == "primary"


def test_draft_profile_returns_confirm():
    action = build_next_action(_ctx(workspace_status="draft", profile_ready=False))
    assert action.key == "confirm_profile"
    assert action.severity == "warning"


def test_competitor_review_returns_resume_step():
    """competitor_review'da profil ONAYLI — 'profil onaylanmadı' metni yerine
    rakip inceleme adımına yönlendirilir (yeni akış, 22.07)."""
    action = build_next_action(
        _ctx(workspace_status="competitor_review", profile_ready=False)
    )
    assert action.key == "resume_competitor_review"
    assert action.path == "/brand-profile"
    assert action.severity == "warning"


def test_no_keywords_returns_create_keywords():
    action = build_next_action(_ctx(keyword_count=0))
    assert action.key == "create_keywords"
    assert action.path == "/keywords"


def test_no_run_returns_start_scoring():
    action = build_next_action(_ctx())
    assert action.key == "start_scoring"
    assert action.path == "/keywords"


def test_failed_run_returns_danger():
    run = RunContext(id=7, status="failed")
    action = build_next_action(_ctx(active_run=run))
    assert action.key == "check_failed_run"
    assert action.severity == "danger"
    assert "run_id=7" in action.path


def test_blocking_task_returns_track_task():
    run = RunContext(id=4, status="scoring")
    block = TaskContext(task_id="t1", task_type="scoring", status="running")
    action = build_next_action(_ctx(active_run=run, blocking_task=block))
    assert action.key == "track_task"
    assert action.severity == "neutral"
    assert "/tasks?run_id=4" == action.path


def test_scored_without_relevance_returns_compute_relevance():
    run = RunContext(id=5, status="scored", skip_relevance=False)
    action = build_next_action(_ctx(active_run=run, relevance_exists=False))
    assert action.key == "compute_relevance"
    assert action.path == "/keywords?view=scores&run_id=5"


def test_scored_with_skip_relevance_returns_channels():
    run = RunContext(id=6, status="scored", skip_relevance=True)
    action = build_next_action(_ctx(active_run=run))
    assert action.key == "start_channels"
    assert action.path == "/keywords?view=scores&run_id=6"


def test_relevance_done_no_pool_returns_channels():
    run = RunContext(id=8, status="relevance_computed")
    action = build_next_action(_ctx(active_run=run, relevance_exists=True))
    assert action.key == "start_channels"


def test_single_ready_channel_returns_tabbed_generation():
    run = RunContext(id=11, status="channel_assigned")
    channels = {
        "ADS": ChannelContext(pool_count=5, expected_count=5, generated_count=0),
        "SEO": ChannelContext(),
        "SOCIAL": ChannelContext(),
    }
    action = build_next_action(
        _ctx(active_run=run, relevance_exists=True, channels=channels)
    )
    assert action.key == "generate_ads"
    assert action.path == "/ads?run_id=11"


def test_multiple_ready_channels_returns_generic_generation():
    run = RunContext(id=12, status="channel_assigned")
    channels = {
        "ADS": ChannelContext(pool_count=5, expected_count=5, generated_count=0),
        "SEO": ChannelContext(pool_count=3, expected_count=3, generated_count=0),
        "SOCIAL": ChannelContext(),
    }
    action = build_next_action(
        _ctx(active_run=run, relevance_exists=True, channels=channels)
    )
    assert action.key == "generate_content"
    assert action.path == "/seo-geo?run_id=12"


def test_partial_content_returns_warning():
    run = RunContext(id=13, status="channel_assigned")
    channels = {
        "ADS": ChannelContext(pool_count=5, expected_count=5, generated_count=2),
        "SEO": ChannelContext(pool_count=3, expected_count=3, generated_count=3),
        "SOCIAL": ChannelContext(),
    }
    action = build_next_action(
        _ctx(active_run=run, relevance_exists=True, channels=channels)
    )
    assert action.key == "complete_content"
    assert action.severity == "warning"


def test_all_complete_no_export_returns_create_export():
    run = RunContext(id=14, status="channel_assigned")
    channels = {
        "ADS": ChannelContext(pool_count=5, expected_count=5, generated_count=5),
        "SEO": ChannelContext(pool_count=3, expected_count=3, generated_count=3),
        "SOCIAL": ChannelContext(),
    }
    action = build_next_action(
        _ctx(active_run=run, relevance_exists=True, channels=channels)
    )
    assert action.key == "create_export"
    # Plan v4: export sayfası kalktı — hedef İndir menüsü olan kanal sayfası
    assert action.label == "Tam Rapor Oluştur"
    assert action.path == "/seo-geo?run_id=14"


def test_export_failed_returns_danger():
    run = RunContext(id=15, status="completed")
    channels = {
        "ADS": ChannelContext(pool_count=2, expected_count=2, generated_count=2),
        "SEO": ChannelContext(),
        "SOCIAL": ChannelContext(),
    }
    action = build_next_action(
        _ctx(
            active_run=run,
            relevance_exists=True,
            channels=channels,
            latest_export=ExportContext(status="failed"),
        )
    )
    assert action.key == "check_export_failed"
    assert action.severity == "danger"
    assert action.path == "/seo-geo?run_id=15"


def test_export_processing_returns_neutral():
    run = RunContext(id=16, status="completed")
    channels = {
        "ADS": ChannelContext(pool_count=2, expected_count=2, generated_count=2),
        "SEO": ChannelContext(),
        "SOCIAL": ChannelContext(),
    }
    action = build_next_action(
        _ctx(
            active_run=run,
            relevance_exists=True,
            channels=channels,
            latest_export=ExportContext(status="processing"),
        )
    )
    assert action.key == "track_export"
    assert action.severity == "neutral"
    assert action.path == "/seo-geo?run_id=16"


def test_export_completed_returns_download():
    run = RunContext(id=17, status="completed")
    channels = {
        "ADS": ChannelContext(pool_count=2, expected_count=2, generated_count=2),
        "SEO": ChannelContext(),
        "SOCIAL": ChannelContext(),
    }
    action = build_next_action(
        _ctx(
            active_run=run,
            relevance_exists=True,
            channels=channels,
            latest_export=ExportContext(status="completed", export_id="exp-abc"),
        )
    )
    assert action.key == "download_export"
    assert action.severity == "primary"
    # Plan v4: kart export_id ile doğrudan indirir; path fallback kanal sayfası
    assert action.export_id == "exp-abc"
    assert action.path == "/seo-geo?run_id=17"


def test_non_blocking_task_does_not_replace_main_cta():
    """Non-blocking task (e.g. seo_content running) should not produce track_task."""
    run = RunContext(id=18, status="channel_assigned")
    channels = {
        "ADS": ChannelContext(pool_count=5, expected_count=5, generated_count=0),
        "SEO": ChannelContext(),
        "SOCIAL": ChannelContext(),
    }
    # build_next_action only sees `blocking_task`; non-blocking tasks are filtered
    # in the summary builder before reaching here. We assert that with no
    # blocking_task the generation CTA still wins.
    action = build_next_action(
        _ctx(active_run=run, relevance_exists=True, channels=channels, blocking_task=None)
    )
    assert action.key.startswith("generate_")


@pytest.mark.parametrize(
    "task_type,is_blocking",
    [
        ("scoring", True),
        ("relevance", True),
        ("channel_assignment", True),
        ("SEMANTIC_SIMILARITY", True),
        ("seo_content", False),
        ("ads", False),
        ("social", False),
        ("export", False),
        ("competitor_discovery", False),
    ],
)
def test_task_context_blocking_classification(task_type, is_blocking):
    tc = TaskContext(task_id="x", task_type=task_type, status="running")
    assert tc.is_blocking is is_blocking


def test_channel_status_thresholds():
    assert ChannelContext().status == "empty"
    assert ChannelContext(pool_count=3, expected_count=3, generated_count=0).status == "ready"
    assert ChannelContext(pool_count=5, expected_count=5, generated_count=2).status == "partial"
    assert ChannelContext(pool_count=5, expected_count=5, generated_count=5).status == "complete"
    assert ChannelContext(pool_count=5, expected_count=5, generated_count=10).status == "complete"
