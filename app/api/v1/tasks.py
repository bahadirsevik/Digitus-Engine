"""
Task Status API Endpoints.

Task durumunu sorgulamak için endpoint'ler.
"""
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.dependencies import get_db
from app.tasks.task_status import get_task_status, get_tasks_by_run, update_task_status
from app.database.models import TaskResult


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


@router.get("/{task_id}", response_model=TaskStatusResponse)
def get_task(task_id: str):
    """
    Tek bir task'ın durumunu getirir.
    
    Args:
        task_id: Celery task ID
        
    Returns:
        Task durumu ve detayları
    """
    status = get_task_status(task_id)
    
    if not status:
        raise HTTPException(status_code=404, detail="Task bulunamadı")
    
    return TaskStatusResponse(**status)


@router.get("/run/{run_id}", response_model=TaskListResponse)
def get_tasks_for_run(run_id: int):
    """
    Bir scoring run'a ait tüm task'ları listeler.
    
    Args:
        run_id: Scoring run ID
        
    Returns:
        Task listesi
    """
    tasks = get_tasks_by_run(run_id)
    
    return TaskListResponse(
        tasks=[TaskStatusResponse(**t) for t in tasks],
        total=len(tasks)
    )


@router.post("/{task_id}/cancel")
def cancel_task(task_id: str):
    """
    Çalışan bir task'ı iptal eder.
    
    Not: Celery task'ları revoke ile iptal edilebilir,
    ancak zaten çalışmaya başlamış task'lar hemen durmayabilir.
    """
    from app.tasks.celery_app import celery_app
    
    status = get_task_status(task_id)
    if not status:
        raise HTTPException(status_code=404, detail="Task bulunamadı")
    
    if status['status'] in ['completed', 'failed']:
        raise HTTPException(
            status_code=400, 
            detail=f"Task zaten tamamlanmış: {status['status']}"
        )
    
    # Celery task'ı iptal et
    celery_app.control.revoke(task_id, terminate=True)
    
    # DB'yi güncelle
    update_task_status(task_id, status='cancelled', error_message='User cancelled')
    
    return {"message": "Task iptal edildi", "task_id": task_id}


@router.get("/", response_model=TaskListResponse)
def list_recent_tasks(
    limit: int = 20,
    status_filter: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """
    Son task'ları listeler.
    
    Args:
        limit: Maksimum sayı (default: 20)
        status_filter: Durum filtresi (pending, running, completed, failed)
    """
    query = db.query(TaskResult).order_by(TaskResult.created_at.desc())
    
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
        total=len(tasks)
    )
