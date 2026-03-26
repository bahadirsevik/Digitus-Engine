"""
Channel assignment endpoints.
"""
from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_ai
from app.generators.ai_service import AIService
from app.core.channel.channel_engine import ChannelEngine
from app.schemas.channel import ChannelAssignRequest


router = APIRouter()


@router.post("/runs/{run_id}/assign")
def run_channel_assignment(
    run_id: int,
    request: ChannelAssignRequest | None = Body(default=None),
    db: Session = Depends(get_db),
):
    """
    Run full channel assignment process as a background Celery task.

    Returns task_id for polling progress via /api/v1/tasks/{task_id}.
    """
    import uuid
    from app.tasks.intent_tasks import run_channel_assignment_task
    from app.tasks.task_status import create_task_record
    from app.database.models import ScoringRun, TaskResult

    scoring_run = db.query(ScoringRun).filter(ScoringRun.id == run_id).first()
    if not scoring_run:
        raise HTTPException(status_code=404, detail=f"Scoring run {run_id} not found")

    # Guard: prevent re-assignment while content generation tasks are active.
    active_generation_task = (
        db.query(TaskResult)
        .filter(TaskResult.scoring_run_id == run_id)
        .filter(TaskResult.task_type.in_(["seo_content", "ads", "social"]))
        .filter(TaskResult.status.in_(["pending", "running"]))
        .first()
    )
    if active_generation_task:
        raise HTTPException(
            status_code=409,
            detail=(
                "Active content generation task exists for this run. "
                "Wait until it completes before re-running channel assignment."
            )
        )

    override = request.relevance_override if request else None
    effective_relevance_coefficient = float(
        override if override is not None else scoring_run.default_relevance_coefficient
    )

    task_id = str(uuid.uuid4())
    create_task_record(task_id, "channel_assignment", run_id, {
        "effective_relevance_coefficient": effective_relevance_coefficient
    })
    run_channel_assignment_task.apply_async(
        args=[run_id, effective_relevance_coefficient],
        task_id=task_id
    )

    return {
        "task_id": task_id,
        "status": "pending",
        "scoring_run_id": run_id,
        "effective_relevance_coefficient": effective_relevance_coefficient,
    }


@router.get("/runs/{run_id}/pools")
def get_channel_pools(
    run_id: int,
    db: Session = Depends(get_db),
    ai: AIService = Depends(get_ai)
):
    """Get all channel pools for a scoring run."""
    engine = ChannelEngine(db, ai)
    return engine.get_channel_pools(run_id)


@router.get("/runs/{run_id}/pools/{channel}")
def get_single_channel_pool(
    run_id: int,
    channel: str,
    db: Session = Depends(get_db),
    ai: AIService = Depends(get_ai)
):
    """Get pool for a specific channel."""
    if channel.upper() not in ["ADS", "SEO", "SOCIAL"]:
        raise HTTPException(status_code=400, detail="Invalid channel")

    engine = ChannelEngine(db, ai)
    pools = engine.get_channel_pools(run_id)

    return {
        "channel": channel.upper(),
        "keywords": pools.get("channels", {}).get(channel.upper(), [])
    }
