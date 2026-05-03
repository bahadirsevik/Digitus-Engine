"""
Task Status DB Synchronization.

Celery task durumunu hem Redis hem PostgreSQL'e yazan helper fonksiyonlar.
"""
from datetime import datetime
from typing import Optional, Dict, Any
from sqlalchemy.orm import Session

from app.database.connection import SessionLocal


def update_task_status(
    task_id: str,
    status: Optional[str] = None,
    progress: Optional[int] = None,
    result_data: Optional[Dict[str, Any]] = None,
    error_message: Optional[str] = None
) -> bool:
    """
    Task durumunu PostgreSQL'deki TaskResult tablosuna yazar.
    
    Args:
        task_id: Celery task ID
        status: pending, running, completed, failed
        progress: 0-100 arası ilerleme
        result_data: Sonuç verisi (JSON)
        error_message: Hata mesajı
        
    Returns:
        Güncelleme başarılı mı
    """
    from app.database.models import TaskResult
    
    db = SessionLocal()
    try:
        task = db.query(TaskResult).filter(TaskResult.task_id == task_id).first()
        
        if not task:
            # Task yoksa oluştur
            task = TaskResult(
                task_id=task_id,
                status=status or 'pending',
                progress=progress or 0,
                created_at=datetime.utcnow()
            )
            db.add(task)
        else:
            # Güncelle
            if status:
                task.status = status
                if status == 'running' and not task.started_at:
                    task.started_at = datetime.utcnow()
                elif status == 'completed':
                    task.completed_at = datetime.utcnow()
                elif status == 'failed':
                    task.completed_at = datetime.utcnow()
            
            if progress is not None:
                task.progress = progress
            
            if result_data:
                task.result_data = result_data
            
            if error_message:
                task.error_message = error_message
        
        db.commit()
        return True
        
    except Exception as e:
        db.rollback()
        # Log hatayı ama task'ı durdurmayalım
        print(f"Task status update failed: {e}")
        return False
        
    finally:
        db.close()


def create_task_record(
    task_id: str,
    task_type: str,
    scoring_run_id: int,
    metadata: Optional[Dict[str, Any]] = None
) -> bool:
    """
    Yeni task kaydı oluşturur.
    
    Args:
        task_id: Celery task ID
        task_type: seo_content, ads, social, export
        scoring_run_id: İlgili scoring run
        metadata: Ek bilgiler
    """
    from app.database.models import TaskResult
    
    db = SessionLocal()
    try:
        task = TaskResult(
            task_id=task_id,
            task_type=task_type,
            scoring_run_id=scoring_run_id,
            status='pending',
            progress=0,
            result_data=metadata or {},
            created_at=datetime.utcnow()
        )
        db.add(task)
        db.commit()
        return True
        
    except Exception as e:
        db.rollback()
        print(f"Task record creation failed: {e}")
        return False
        
    finally:
        db.close()


def get_task_status(task_id: str) -> Optional[Dict[str, Any]]:
    """
    Task durumunu getirir.
    """
    from app.database.models import TaskResult
    
    db = SessionLocal()
    try:
        task = db.query(TaskResult).filter(TaskResult.task_id == task_id).first()
        
        if not task:
            return None
        
        return {
            'task_id': task.task_id,
            'task_type': task.task_type,
            'status': task.status,
            'progress': task.progress,
            'result_data': task.result_data,
            'error_message': task.error_message,
            'started_at': task.started_at.isoformat() if task.started_at else None,
            'completed_at': task.completed_at.isoformat() if task.completed_at else None,
            'created_at': task.created_at.isoformat() if task.created_at else None,
        }
        
    finally:
        db.close()


def get_tasks_by_run(scoring_run_id: int) -> list:
    """
    Bir scoring run'a ait tüm task'ları getirir.
    """
    from app.database.models import TaskResult
    
    db = SessionLocal()
    try:
        tasks = db.query(TaskResult).filter(
            TaskResult.scoring_run_id == scoring_run_id
        ).order_by(TaskResult.created_at.desc()).all()
        
        return [
            {
                'task_id': t.task_id,
                'task_type': t.task_type,
                'status': t.status,
                'progress': t.progress,
                'created_at': t.created_at.isoformat() if t.created_at else None,
            }
            for t in tasks
        ]
        
    finally:
        db.close()
