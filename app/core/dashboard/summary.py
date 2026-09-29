"""Dashboard summary builder (P5.1).

Aggregates workspace state, active run, channel/content counts, latest export
and active tasks into a single DashboardSummary. Pure DB queries — no caching,
no HTTP. The endpoint layer (`api/v1/dashboard.py`) wraps this in Redis cache.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from sqlalchemy import exists, func
from sqlalchemy.orm import Session

from app.core.dashboard.next_action import (
    ChannelContext,
    ExportContext,
    NextActionContext,
    RunContext,
    TaskContext,
    build_next_action,
)
from app.database.models import (
    AdGroup,
    BrandProfile,
    ChannelPool,
    ContentOutput,
    ExportJob,
    KeywordRelevance,
    ScoringRun,
    SocialCategory,
    SocialContent,
    TaskResult,
    WorkspaceKeyword,
)
from app.schemas.dashboard import (
    ChannelGroup,
    ChannelProgress,
    DashboardSummary,
    ExportSummary,
    HealthSummary,
    NextAction,
    PipelineStep,
    RunSummary,
    TaskSummary,
    WorkspaceSummary,
)


CHANNELS = ("ADS", "SEO", "SOCIAL")


# --------- Workspace + Run resolution ---------


def _profile_ready(workspace: BrandProfile) -> bool:
    if workspace.status != "confirmed":
        return False
    profile = workspace.profile_data or {}
    anchors = profile.get("anchor_texts") if isinstance(profile, dict) else None
    if isinstance(anchors, list) and any(str(a or "").strip() for a in anchors):
        return True
    # profile_data dolu ama anchor_texts yoksa da confirmed kabul edilir
    return bool(profile)


def _workspace_summary(workspace: BrandProfile) -> WorkspaceSummary:
    return WorkspaceSummary(
        id=workspace.id,
        name=workspace.name,
        company_url=workspace.company_url,
        status=workspace.status or "pending",
        profile_ready=_profile_ready(workspace),
    )


def _run_summary(run: ScoringRun) -> RunSummary:
    return RunSummary(
        id=run.id,
        run_name=run.run_name,
        status=run.status or "pending",
        keyword_selection_mode=run.keyword_selection_mode,
        ads_capacity=run.ads_capacity or 0,
        seo_capacity=run.seo_capacity or 0,
        social_capacity=run.social_capacity or 0,
        enable_ads=bool(run.enable_ads),
        enable_seo=bool(run.enable_seo),
        enable_social=bool(run.enable_social),
        skip_relevance=bool(run.skip_relevance),
        algorithm_version=getattr(run, "algorithm_version", None) or "v2",
        created_at=run.created_at,
    )


def _resolve_active_run(
    db: Session, workspace_id: int, active_run_id: Optional[int]
) -> Optional[ScoringRun]:
    if active_run_id is not None:
        run = (
            db.query(ScoringRun)
            .filter(ScoringRun.id == active_run_id)
            .first()
        )
        if run is None or run.brand_profile_id != workspace_id:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="scoring run not found in workspace")
        return run
    return (
        db.query(ScoringRun)
        .filter(ScoringRun.brand_profile_id == workspace_id)
        .order_by(ScoringRun.created_at.desc())
        .first()
    )


def _list_runs(db: Session, workspace_id: int, limit: int = 20) -> List[ScoringRun]:
    return (
        db.query(ScoringRun)
        .filter(ScoringRun.brand_profile_id == workspace_id)
        .order_by(ScoringRun.created_at.desc())
        .limit(limit)
        .all()
    )


# --------- Channel + Generation counts ---------


def _channel_counts(db: Session, run_id: int) -> dict:
    """Returns {channel: pool_count} for all three channels (defaults to 0)."""
    rows = (
        db.query(ChannelPool.channel, func.count(ChannelPool.id))
        .filter(ChannelPool.scoring_run_id == run_id)
        .group_by(ChannelPool.channel)
        .all()
    )
    out = {ch: 0 for ch in CHANNELS}
    for ch, cnt in rows:
        if ch in out:
            out[ch] = int(cnt)
    return out


def _generation_counts(db: Session, run_id: int) -> dict:
    """Returns {channel: generated_count}.

    Source per channel:
    - ADS:    AdGroup rows for this run.
    - SEO:    ContentOutput rows with channel='SEO' for this run.
    - SOCIAL: SocialCategory rows for this run (Phase 1 deliverable).
    """
    # Versiyonlama (Faz E): yalnız AKTİF + NON-STALE setin grupları sayılır
    from app.generators.ads.generation_sets import active_ad_groups
    ads = active_ad_groups(db, run_id).count() or 0
    seo = (
        db.query(func.count(ContentOutput.id))
        .filter(
            ContentOutput.scoring_run_id == run_id,
            ContentOutput.channel == "SEO",
        )
        .scalar()
        or 0
    )
    social = (
        db.query(func.count(SocialCategory.id))
        .filter(
            SocialCategory.scoring_run_id == run_id,
            # Codex A+B-4: reassignment sonrasi stale SOCIAL "uretilmis" sayilmaz
            SocialCategory.is_stale.is_(False),
        )
        .scalar()
        or 0
    )
    return {"ADS": int(ads), "SEO": int(seo), "SOCIAL": int(social)}


def _build_channel_group(
    pool_counts: dict, gen_counts: dict
) -> ChannelGroup:
    # MVP: expected_count = pool_count fallback (plan2 §"Expected/generated
    # kararlari"). When AI grouping/templating yields a precise target the
    # expected_count can be replaced; until then we treat any generated >=
    # pool_count as "complete".
    def _progress(channel: str) -> ChannelProgress:
        pool = pool_counts.get(channel, 0)
        gen = gen_counts.get(channel, 0)
        expected = pool
        if pool == 0:
            status = "empty"
        elif gen == 0:
            status = "ready"
        elif expected > 0 and gen < expected:
            status = "partial"
        else:
            status = "complete"
        return ChannelProgress(
            pool_count=pool,
            expected_count=expected,
            generated_count=gen,
            status=status,
        )

    return ChannelGroup(
        ADS=_progress("ADS"),
        SEO=_progress("SEO"),
        SOCIAL=_progress("SOCIAL"),
    )


# --------- Tasks + Export ---------


def _active_tasks(db: Session, run_id: int) -> Tuple[List[TaskSummary], Optional[TaskSummary]]:
    tasks = (
        db.query(TaskResult)
        .filter(
            TaskResult.scoring_run_id == run_id,
            TaskResult.status.in_(("pending", "running")),
        )
        .order_by(TaskResult.created_at.desc())
        .limit(10)
        .all()
    )
    from app.core.dashboard.next_action import BLOCKING_TASK_TYPES

    summaries: List[TaskSummary] = []
    blocking: Optional[TaskSummary] = None
    for t in tasks:
        is_block = (t.task_type or "") in BLOCKING_TASK_TYPES
        s = TaskSummary(
            task_id=t.task_id,
            task_type=t.task_type,
            status=t.status or "pending",
            progress=int(t.progress or 0),
            error_message=t.error_message,
            is_blocking=is_block,
        )
        summaries.append(s)
        if is_block and blocking is None:
            blocking = s
    return summaries, blocking


def _latest_export(db: Session, workspace_id: int, run_id: Optional[int]) -> Optional[ExportJob]:
    q = db.query(ExportJob).filter(ExportJob.brand_profile_id == workspace_id)
    if run_id is not None:
        q = q.filter(ExportJob.scoring_run_id == run_id)
    return q.order_by(ExportJob.created_at.desc()).first()


def _latest_full_export(
    db: Session, workspace_id: int, run_id: Optional[int]
) -> Optional[ExportJob]:
    """Sections'ında 'all' olan en son job (plan v4 tur-6 #1).

    "Tam rapor hazır" akışı YALNIZ buna bakar — kanal-bölümü exportları
    (ads/seo_content/social) tam rapor sanılmaz. Filtre DB'de JSONB
    containment ile yapılır (codex post-review #5): keyfi son-N limiti yok,
    araya kaç kanal export'u girerse girsin mevcut tam rapor bulunur.
    """
    from sqlalchemy import cast
    from sqlalchemy.dialects.postgresql import JSONB

    q = db.query(ExportJob).filter(
        ExportJob.brand_profile_id == workspace_id,
        cast(ExportJob.sections, JSONB).contains(["all"]),
    )
    if run_id is not None:
        q = q.filter(ExportJob.scoring_run_id == run_id)
    return q.order_by(ExportJob.created_at.desc()).first()


def _export_summary(db: Session, job: ExportJob) -> ExportSummary:
    from app.core.policy.freshness import is_export_policy_outdated

    return ExportSummary(
        export_id=job.id,
        status=job.status,
        format=job.format,
        file_name=job.file_name,
        created_at=job.created_at,
        sections=list(job.sections or []),
        policy_outdated=is_export_policy_outdated(db, job),
    )


# --------- Pipeline timeline ---------


def _pipeline(
    workspace: Optional[BrandProfile],
    active_run: Optional[ScoringRun],
    keyword_count: int,
    relevance_exists: bool,
    channels: ChannelGroup,
    has_any_content: bool,
    latest_export: Optional[ExportJob],
) -> List[PipelineStep]:
    # NOTE: bound to a local with a non-`run*` name so the
    # check_scoring_status_assignments guard (which matches the assignment
    # regex against `==` comparisons too) does not false-positive below.
    ar = active_run
    ar_status = ar.status if ar else None
    qs = f"?run_id={ar.id}" if ar else ""

    def step(key, label, state, detail, path) -> PipelineStep:
        return PipelineStep(key=key, label=label, state=state, detail=detail, path=path)

    profile_state = (
        "complete"
        if workspace and workspace.status == "confirmed"
        else ("in_progress" if workspace else "pending")
    )
    keywords_state = "complete" if keyword_count > 0 else "pending"
    scoring_state = (
        "complete"
        if ar and ar_status not in {"pending", "scoring", "failed"}
        else ("in_progress" if ar and ar_status in {"pending", "scoring"} else "pending")
    ) if ar else "pending"
    if ar and ar_status == "failed":
        scoring_state = "blocked"

    if not ar:
        relevance_state = "pending"
    elif ar.skip_relevance:
        relevance_state = "skipped"
    elif relevance_exists:
        relevance_state = "complete"
    elif ar_status == "relevance_computing":
        relevance_state = "in_progress"
    else:
        relevance_state = "pending"

    total_pool = channels.ADS.pool_count + channels.SEO.pool_count + channels.SOCIAL.pool_count
    if total_pool > 0:
        channel_state = "complete"
    elif ar and ar_status == "channel_assigning":
        channel_state = "in_progress"
    else:
        channel_state = "pending"

    if has_any_content:
        partial = any(
            c.status == "partial" for c in (channels.ADS, channels.SEO, channels.SOCIAL)
        )
        complete = all(
            c.status == "complete"
            for c in (channels.ADS, channels.SEO, channels.SOCIAL)
            if c.pool_count > 0
        )
        content_state = "complete" if complete and not partial else "in_progress"
    else:
        content_state = "pending"

    if latest_export is None:
        export_state = "pending"
    elif latest_export.status == "completed":
        export_state = "complete"
    elif latest_export.status == "failed":
        export_state = "blocked"
    else:
        export_state = "in_progress"

    return [
        step(
            "brand_profile",
            "Marka Profili",
            profile_state,
            workspace.status if workspace else "seçim gerekli",
            "/brand-profile",
        ),
        step(
            "keywords",
            "Keyword Havuzu",
            keywords_state,
            f"{keyword_count} keyword",
            "/keywords",
        ),
        step(
            "scoring",
            "Skorlama",
            scoring_state,
            ar_status if ar else "run yok",
            f"/scoring{qs}",
        ),
        step(
            "relevance",
            "İlgi Skoru",
            relevance_state,
            "atlandı" if ar and ar.skip_relevance else ("hesaplandı" if relevance_exists else "bekliyor"),
            f"/relevance{qs}",
        ),
        step(
            "channels",
            "Kanal Ataması",
            channel_state,
            f"{total_pool} keyword" if total_pool else "bekliyor",
            f"/channels{qs}",
        ),
        step(
            "content",
            "İçerik Üretimi",
            content_state,
            (
                f"ADS {channels.ADS.generated_count}/{channels.ADS.expected_count} | "
                f"SEO {channels.SEO.generated_count}/{channels.SEO.expected_count} | "
                f"SOCIAL {channels.SOCIAL.generated_count}/{channels.SOCIAL.expected_count}"
            ),
            f"/generation{qs}",
        ),
        step(
            "export",
            "Dışa Aktarım",
            export_state,
            latest_export.status if latest_export else "bekliyor",
            # Export sayfası kalktı (plan v4) — İndir menüsü olan varsayılan
            # kanal sayfası hedeflenir
            f"/seo-geo{qs}",
        ),
    ]


# --------- Public entry ---------


def build_summary(
    db: Session,
    *,
    workspace_id: Optional[int],
    active_run_id: Optional[int],
    api_health: str,
    google_ads_health: Optional[str],
    google_ads_error: Optional[str] = None,
) -> DashboardSummary:
    if workspace_id is None:
        return _empty_summary(
            api_health=api_health,
            google_ads_health=google_ads_health,
            google_ads_error=google_ads_error,
        )

    workspace = (
        db.query(BrandProfile)
        .filter(BrandProfile.id == workspace_id, BrandProfile.deleted_at.is_(None))
        .first()
    )
    if workspace is None:
        return _empty_summary(
            api_health=api_health,
            google_ads_health=google_ads_health,
            google_ads_error=google_ads_error,
        )

    keyword_count = (
        db.query(func.count(WorkspaceKeyword.id))
        .filter(WorkspaceKeyword.brand_profile_id == workspace.id)
        .scalar()
        or 0
    )

    run = _resolve_active_run(db, workspace.id, active_run_id)
    runs = _list_runs(db, workspace.id)

    relevance_exists = False
    pool_counts = {c: 0 for c in CHANNELS}
    gen_counts = {c: 0 for c in CHANNELS}
    active_task_summaries: List[TaskSummary] = []
    blocking_task: Optional[TaskSummary] = None
    latest_export = None
    latest_full_export = None

    if run is not None:
        relevance_exists = db.query(
            exists().where(KeywordRelevance.scoring_run_id == run.id)
        ).scalar() or False
        pool_counts = _channel_counts(db, run.id)
        gen_counts = _generation_counts(db, run.id)
        active_task_summaries, blocking_task = _active_tasks(db, run.id)
        latest_export = _latest_export(db, workspace.id, run.id)
        latest_full_export = _latest_full_export(db, workspace.id, run.id)

    channels = _build_channel_group(pool_counts, gen_counts)
    has_any_content = sum(gen_counts.values()) > 0

    pipeline = _pipeline(
        workspace=workspace,
        active_run=run,
        keyword_count=int(keyword_count),
        relevance_exists=bool(relevance_exists),
        channels=channels,
        has_any_content=has_any_content,
        latest_export=latest_export,
    )

    ctx = NextActionContext(
        has_workspace=True,
        workspace_status=workspace.status,
        profile_ready=_profile_ready(workspace),
        keyword_count=int(keyword_count),
        active_run=(
            RunContext(
                id=run.id,
                status=run.status or "pending",
                skip_relevance=bool(run.skip_relevance),
                enable_ads=bool(run.enable_ads),
                enable_seo=bool(run.enable_seo),
                enable_social=bool(run.enable_social),
            )
            if run
            else None
        ),
        blocking_task=(
            TaskContext(
                task_id=blocking_task.task_id,
                task_type=blocking_task.task_type,
                status=blocking_task.status,
            )
            if blocking_task
            else None
        ),
        relevance_exists=bool(relevance_exists),
        channels={
            "ADS": ChannelContext(
                pool_count=channels.ADS.pool_count,
                expected_count=channels.ADS.expected_count,
                generated_count=channels.ADS.generated_count,
            ),
            "SEO": ChannelContext(
                pool_count=channels.SEO.pool_count,
                expected_count=channels.SEO.expected_count,
                generated_count=channels.SEO.generated_count,
            ),
            "SOCIAL": ChannelContext(
                pool_count=channels.SOCIAL.pool_count,
                expected_count=channels.SOCIAL.expected_count,
                generated_count=channels.SOCIAL.generated_count,
            ),
        },
        # Next-action "tam rapor" akışı yalnız FULL (sections='all') job'a
        # bakar — ADS-only export "tam rapor hazır" sayılmaz (tur-6 #1)
        latest_export=(
            ExportContext(
                status=latest_full_export.status,
                sections=tuple(latest_full_export.sections or []),
                export_id=latest_full_export.id,
            )
            if latest_full_export
            else None
        ),
    )

    next_action = build_next_action(ctx)

    # Plan v13: aktif run'ın havuz bayatlık sinyali
    pool_freshness = None
    if run is not None:
        from app.core.policy.freshness import compute_pool_freshness

        pool_freshness = compute_pool_freshness(run, workspace).as_dict()

    return DashboardSummary(
        workspace=_workspace_summary(workspace),
        active_run=_run_summary(run) if run else None,
        runs=[_run_summary(r) for r in runs],
        keyword_count=int(keyword_count),
        pipeline=pipeline,
        channels=channels,
        latest_export=_export_summary(db, latest_export) if latest_export else None,
        blocking_task=blocking_task,
        active_tasks=active_task_summaries,
        health=HealthSummary(
            api=api_health,
            google_ads=google_ads_health,
            google_ads_error=google_ads_error,
        ),
        next_action=next_action,
        pool_freshness=pool_freshness,
    )


def _empty_summary(
    *,
    api_health: str,
    google_ads_health: Optional[str],
    google_ads_error: Optional[str] = None,
) -> DashboardSummary:
    empty = ChannelProgress(pool_count=0, expected_count=0, generated_count=0, status="empty")
    ctx = NextActionContext(has_workspace=False)
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
        health=HealthSummary(
            api=api_health,
            google_ads=google_ads_health,
            google_ads_error=google_ads_error,
        ),
        next_action=build_next_action(ctx),
    )
