"""
Task Status DB Synchronization.

Celery task durumunu hem Redis hem PostgreSQL'e yazan helper fonksiyonlar.
"""
from datetime import datetime
from typing import Optional, Dict, Any
from loguru import logger
from sqlalchemy.orm import Session

from app.database.connection import SessionLocal

ACTIVE_STATUSES = ("pending", "running")


def update_task_status(
    task_id: str,
    status: Optional[str] = None,
    progress: Optional[int] = None,
    result_data: Optional[Dict[str, Any]] = None,
    error_message: Optional[str] = None
) -> bool:
    """
    Task durumunu PostgreSQL'deki TaskResult tablosuna yazar.

    Terminal (completed/failed/cancelled) kayıt DEĞİŞMEZ: güncelleme tek bir
    koşullu UPDATE'tir (`WHERE status IN (pending, running)`), böylece geç
    gelen bir yazım (iptal/stale sonrası worker, mükerrer teslim) ne durumu ne
    progress/result_data/error_message/completed_at alanlarını bozabilir.
    Kayıt terminalse False döner ve loglanır. Kayıt yoksa oluşturulur.

    Args:
        task_id: Celery task ID
        status: pending, running, completed, failed
        progress: 0-100 arası ilerleme
        result_data: Sonuç verisi (JSON)
        error_message: Hata mesajı

    Returns:
        Güncelleme başarılı mı
    """
    from sqlalchemy import func, update

    from app.database.models import TaskResult

    db = SessionLocal()
    try:
        values: Dict[str, Any] = {}
        if status:
            values["status"] = status
            if status == 'running':
                values["started_at"] = func.coalesce(TaskResult.started_at, datetime.utcnow())
            elif status in ('completed', 'failed', 'cancelled'):
                values["completed_at"] = datetime.utcnow()
        if progress is not None:
            values["progress"] = func.greatest(func.coalesce(TaskResult.progress, 0), progress)
        if result_data:
            values["result_data"] = result_data
        if error_message:
            values["error_message"] = error_message

        exists = db.query(TaskResult.id).filter(TaskResult.task_id == task_id).first()
        if not exists:
            # Task yoksa oluştur
            db.add(TaskResult(
                task_id=task_id,
                status=status or 'pending',
                progress=progress or 0,
                created_at=datetime.utcnow()
            ))
            db.commit()
            return True

        if not values:
            return True

        result = db.execute(
            update(TaskResult)
            .where(TaskResult.task_id == task_id,
                   TaskResult.status.in_(ACTIVE_STATUSES))
            .values(**values)
        )
        db.commit()
        if not result.rowcount:
            logger.warning(
                f"Terminal task güncellemesi yok sayıldı: {task_id} "
                f"(istenen status={status!r})"
            )
            return False
        return True

    except Exception as e:
        db.rollback()
        # Log hatayı ama task'ı durdurmayalım
        print(f"Task status update failed: {e}")
        return False

    finally:
        db.close()


def fail_stale_tasks(
    *, task_id: Optional[str] = None, scoring_run_id: Optional[int] = None
) -> int:
    """Okuma-ani uzlaştırma (ADR-002 `fail_if_stuck` ile aynı fikir).

    Süresi dolmuş `running`/`pending` kayıtları koşullu UPDATE ile `failed`
    yapar (WHERE yazım anında durum + yaşı yeniden doğrular; bu arada biten
    task'a dokunulmaz). Kendi uzlaştırması olan türler muaftır
    (`TASK_STALE_EXEMPT_TYPES`). Kaç satır değiştiğini döndürür.
    """
    from datetime import timedelta, timezone

    from sqlalchemy import and_, func, or_, update

    from app.core.constants import (
        TASK_PENDING_STALE_SECONDS,
        TASK_RUNNING_STALE_SECONDS,
        TASK_STALE_ERROR_MESSAGE,
        TASK_STALE_EXEMPT_TYPES,
    )
    from app.database.models import TaskResult

    now = datetime.now(timezone.utc)
    conditions = [
        or_(TaskResult.task_type.is_(None),
            TaskResult.task_type.notin_(TASK_STALE_EXEMPT_TYPES)),
        or_(
            and_(TaskResult.status == "running",
                 func.coalesce(TaskResult.started_at, TaskResult.created_at)
                 < now - timedelta(seconds=TASK_RUNNING_STALE_SECONDS)),
            and_(TaskResult.status == "pending",
                 TaskResult.created_at
                 < now - timedelta(seconds=TASK_PENDING_STALE_SECONDS)),
        ),
    ]
    if task_id is not None:
        conditions.append(TaskResult.task_id == task_id)
    if scoring_run_id is not None:
        conditions.append(TaskResult.scoring_run_id == scoring_run_id)

    db = SessionLocal()
    try:
        result = db.execute(
            update(TaskResult).where(*conditions).values(
                status="failed", progress=100,
                error_message=TASK_STALE_ERROR_MESSAGE, completed_at=now))
        db.commit()
        if result.rowcount:
            logger.warning(f"Bayat task failed işaretlendi: {result.rowcount} kayıt "
                           f"(task_id={task_id}, run={scoring_run_id})")
        return result.rowcount or 0
    except Exception as e:
        db.rollback()
        logger.error(f"Bayat task uzlaştırması başarısız: {e}")
        return 0
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
        # İdempotent: kayıt endpoint tarafından (dispatch kilidi altında)
        # önceden yaratılmış olabilir — varsa BAŞARILI no-op (Faz E3).
        existing = db.query(TaskResult).filter(TaskResult.task_id == task_id).first()
        if existing:
            return True

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
            'scoring_run_id': task.scoring_run_id,
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
                'scoring_run_id': t.scoring_run_id,
                'status': t.status,
                'progress': t.progress,
                'result_data': t.result_data,
                'error_message': t.error_message,
                'started_at': t.started_at.isoformat() if t.started_at else None,
                'completed_at': t.completed_at.isoformat() if t.completed_at else None,
                'created_at': t.created_at.isoformat() if t.created_at else None,
            }
            for t in tasks
        ]
        
    finally:
        db.close()


def mark_task_running_if_active(task_id: str) -> bool:
    """`pending|running -> running` CAS (Codex 12. tur #2).

    `update_task_status(status="running")` koşulsuz yazdığı için gecikmiş
    mükerrer teslim TAMAMLANMIŞ bir task'ı yeniden `running` yapabiliyordu.
    Terminal kayıt hiçbir koşulda geriye taşınmaz.
    """
    from sqlalchemy import update

    from app.database.connection import SessionLocal
    from app.database.models import TaskResult

    session = SessionLocal()
    try:
        result = session.execute(
            update(TaskResult)
            .where(TaskResult.task_id == task_id,
                   TaskResult.status.in_(("pending", "running")))
            .values(status="running"))
        session.commit()
        return bool(result.rowcount)
    finally:
        session.close()
