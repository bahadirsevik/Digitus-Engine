"""
Task Status API Endpoints.

Plan2 §P0/C4 — tasks endpoint'leri workspace-zorunlu. Read olsalar bile
global task leak kritik (kullanıcı başka workspace'in görev listesini görür).
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.workspace import verify_task_in_workspace, verify_workspace
from app.database.models import ScoringRun, TaskResult
from app.dependencies import get_db
from app.tasks.task_status import get_task_status, get_tasks_by_run, update_task_status


router = APIRouter()


class TaskStatusResponse(BaseModel):
    """Task durum yanıtı."""
    task_id: str
    task_type: Optional[str] = None
    status: str
    progress: int = 0
    result_data: Optional[dict] = None
    error_message: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    created_at: Optional[str] = None

    class Config:
        from_attributes = True


class TaskListResponse(BaseModel):
    """Task listesi yanıtı."""
    tasks: List[TaskStatusResponse]
    total: int


def _require_workspace(brand_profile_id: Optional[int]) -> int:
    if brand_profile_id is None:
        raise HTTPException(status_code=400, detail="brand_profile_id is required")
    return brand_profile_id


@router.get("/{task_id}", response_model=TaskStatusResponse)
def get_task(
    task_id: str,
    brand_profile_id: Optional[int] = Query(None, description="Workspace scope (required)"),
    db: Session = Depends(get_db),
):
    """Tek bir task'ın durumunu getirir. Task'ın run'ı workspace'e ait olmalı."""
    workspace_id = _require_workspace(brand_profile_id)
    verify_task_in_workspace(db, task_id, workspace_id)

    status = get_task_status(task_id)
    if not status:
        raise HTTPException(status_code=404, detail="Task bulunamadı")

    return TaskStatusResponse(**status)


@router.get("/run/{run_id}", response_model=TaskListResponse)
def get_tasks_for_run(
    run_id: int,
    brand_profile_id: Optional[int] = Query(None, description="Workspace scope (required)"),
    db: Session = Depends(get_db),
):
    """Bir scoring run'a ait tüm task'ları listeler. Run workspace'e ait olmalı."""
    from app.core.workspace import verify_scoring_run

    workspace_id = _require_workspace(brand_profile_id)
    verify_scoring_run(db, run_id, workspace_id, mutating=True)

    tasks = get_tasks_by_run(run_id)
    return TaskListResponse(
        tasks=[TaskStatusResponse(**t) for t in tasks],
        total=len(tasks),
    )


@router.post("/{task_id}/cancel")
def cancel_task(
    task_id: str,
    brand_profile_id: Optional[int] = Query(None, description="Workspace scope (required)"),
    db: Session = Depends(get_db),
):
    """Çalışan bir task'ı iptal eder. Mutating: workspace zorunlu."""
    from app.tasks.celery_app import celery_app

    workspace_id = _require_workspace(brand_profile_id)
    verify_task_in_workspace(db, task_id, workspace_id)

    status = get_task_status(task_id)
    if not status:
        raise HTTPException(status_code=404, detail="Task bulunamadı")

    if status['status'] in ['completed', 'failed']:
        raise HTTPException(
            status_code=400,
            detail=f"Task zaten tamamlanmış: {status['status']}",
        )

    celery_app.control.revoke(task_id, terminate=True)
    update_task_status(task_id, status='cancelled', error_message='User cancelled')

    return {"message": "Task iptal edildi", "task_id": task_id}


@router.get("/", response_model=TaskListResponse)
def list_recent_tasks(
    limit: int = 20,
    status_filter: Optional[str] = None,
    brand_profile_id: Optional[int] = Query(None, description="Workspace scope (required)"),
    db: Session = Depends(get_db),
):
    """Son task'ları listeler. Workspace zorunlu — global task leak önlendi."""
    workspace_id = _require_workspace(brand_profile_id)
    verify_workspace(db, workspace_id)

    query = (
        db.query(TaskResult)
        .join(ScoringRun, TaskResult.scoring_run_id == ScoringRun.id)
        .filter(ScoringRun.brand_profile_id == workspace_id)
        .order_by(TaskResult.created_at.desc())
    )
    if status_filter:
        query = query.filter(TaskResult.status == status_filter)

    tasks = query.limit(limit).all()

    return TaskListResponse(
        tasks=[
            TaskStatusResponse(
                task_id=t.task_id,
                task_type=t.task_type,
                status=t.status,
                progress=t.progress or 0,
                result_data=t.result_data,
                error_message=t.error_message,
                started_at=t.started_at.isoformat() if t.started_at else None,
                completed_at=t.completed_at.isoformat() if t.completed_at else None,
                created_at=t.created_at.isoformat() if t.created_at else None,
            )
            for t in tasks
        ],
        total=len(tasks),
    )
