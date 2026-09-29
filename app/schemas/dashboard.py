"""Pydantic schemas for the dashboard workflow cockpit summary endpoint (P5.1)."""
from __future__ import annotations

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel


Severity = Literal["primary", "warning", "danger", "neutral"]
ChannelStatus = Literal["empty", "ready", "partial", "complete"]


class WorkspaceSummary(BaseModel):
    id: int
    name: Optional[str] = None
    company_url: Optional[str] = None
    status: str
    profile_ready: bool


class RunSummary(BaseModel):
    id: int
    run_name: Optional[str] = None
    status: str
    keyword_selection_mode: Optional[str] = None
    ads_capacity: int = 0
    seo_capacity: int = 0
    social_capacity: int = 0
    enable_ads: bool = True
    enable_seo: bool = True
    enable_social: bool = True
    skip_relevance: bool = False
    algorithm_version: Optional[str] = None
    created_at: Optional[datetime] = None


class ChannelProgress(BaseModel):
    pool_count: int = 0
    expected_count: int = 0
    generated_count: int = 0
    status: ChannelStatus = "empty"


class ChannelGroup(BaseModel):
    ADS: ChannelProgress
    SEO: ChannelProgress
    SOCIAL: ChannelProgress


class PipelineStep(BaseModel):
    key: str
    label: str
    state: Literal["pending", "in_progress", "complete", "blocked", "skipped"]
    detail: Optional[str] = None
    path: str


class ExportSummary(BaseModel):
    export_id: str
    status: Literal["pending", "processing", "completed", "failed"]
    format: Optional[str] = None
    file_name: Optional[str] = None
    created_at: Optional[datetime] = None
    # Plan v4: kart etiketi gerçek türden üretilir ("ADS İçerikleri · Word")
    # ve eski politikayla üretilmiş dosya rozet taşır
    sections: List[str] = []
    policy_outdated: bool = False


class TaskSummary(BaseModel):
    task_id: str
    task_type: Optional[str] = None
    status: str
    progress: int = 0
    error_message: Optional[str] = None
    is_blocking: bool = False


class HealthSummary(BaseModel):
    api: str
    google_ads: Optional[str] = None
    google_ads_error: Optional[str] = None


class NextAction(BaseModel):
    key: str
    label: str
    path: str
    severity: Severity
    reason: str
    # download_export aksiyonu: kart doğrudan bu id ile indirir (plan v4)
    export_id: Optional[str] = None


class DashboardSummary(BaseModel):
    workspace: Optional[WorkspaceSummary] = None
    active_run: Optional[RunSummary] = None
    runs: List[RunSummary] = []
    keyword_count: int = 0
    pipeline: List[PipelineStep] = []
    channels: ChannelGroup
    latest_export: Optional[ExportSummary] = None
    blocking_task: Optional[TaskSummary] = None
    active_tasks: List[TaskSummary] = []
    health: HealthSummary
    next_action: NextAction
    # Plan v13: aktif run'ın havuz bayatlığı (channel_pool_stale/policy_stale/
    # relevance_stale) — UI bandı iki nedeni ayrı gösterir
    pool_freshness: Optional[dict] = None
