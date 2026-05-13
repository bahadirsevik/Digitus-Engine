"""
Workspace doğrulama helper'ları (plan2 §P0 — ortak guard isimlendirme).

Standart kontrat (hepsi için aynı):
- brand_profile_id None ise HTTPException 400 ("required") — mutating=True'da.
- brand_profile_id var ama workspace yoksa/arşivse 404 ("not found").
- İlişkili obje (run/task/keyword/export) workspace ile eşleşmiyorsa 404
  (mevcudiyet sızıntısını önlemek için 403 değil).

Endpoint kodu sadece tek satır `obj = verify_X(...)` çağırarak işini bitirir.
"""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.database.models import BrandProfile, ExportJob, ScoringRun, TaskResult


def verify_workspace(db: Session, brand_profile_id: int) -> BrandProfile:
    """Workspace var mı + arşivlenmiş değil mi?

    Raises HTTPException 404 if not found or soft-deleted.
    """
    workspace = db.query(BrandProfile).filter(
        BrandProfile.id == brand_profile_id,
        BrandProfile.deleted_at.is_(None),
    ).first()

    if not workspace:
        raise HTTPException(
            status_code=404,
            detail=f"Marka çalışması (id={brand_profile_id}) bulunamadı veya arşivlenmiş",
        )

    return workspace


def verify_scoring_run(
    db: Session,
    run_id: int,
    brand_profile_id: int,
) -> ScoringRun:
    """Scoring run var mı + verilen workspace'e ait mi?

    brand_profile_id her zaman zorunlu (read veya mutating fark etmez).
    mutating parametresi artık yalnızca dökümantasyon amaçlı; davranışı değiştirmez.

    Raises:
        HTTPException 400: brand_profile_id None.
        HTTPException 404: run bulunamadı veya workspace ile eşleşmiyor.
    """
    if brand_profile_id is None:
        raise HTTPException(
            status_code=400,
            detail="brand_profile_id is required",
        )

    verify_workspace(db, brand_profile_id)

    run = db.query(ScoringRun).filter(ScoringRun.id == run_id).first()
    if not run:
        raise HTTPException(
            status_code=404,
            detail="scoring run not found",
        )

    if run.brand_profile_id != brand_profile_id:
        raise HTTPException(
            status_code=404,
            detail="scoring run not found in workspace",
        )

    return run


def verify_task_in_workspace(
    db: Session,
    task_id: str,
    brand_profile_id: int,
) -> TaskResult:
    """Task var mı + ait olduğu run verilen workspace'e mi ait?

    Plan2 §P0/C4: tasks endpoint'leri read olsa da workspace zorunlu —
    cross-workspace task leak kritik.

    Raises:
        HTTPException 400: brand_profile_id None.
        HTTPException 404: task yoksa veya run workspace ile eşleşmiyorsa.
    """
    if brand_profile_id is None:
        raise HTTPException(
            status_code=400,
            detail="brand_profile_id is required",
        )

    verify_workspace(db, brand_profile_id)

    row = (
        db.query(TaskResult, ScoringRun)
        .join(ScoringRun, TaskResult.scoring_run_id == ScoringRun.id)
        .filter(TaskResult.task_id == task_id)
        .filter(ScoringRun.brand_profile_id == brand_profile_id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="task not found in workspace")
    return row[0]


def verify_export_in_workspace(
    db: Session,
    export_job_id: str,
    brand_profile_id: int,
) -> ExportJob:
    """ExportJob var mı + verilen workspace'e ait mi?

    Plan2 §P2: in-memory dict yerini DB-backed ExportJob aldı.

    Raises:
        HTTPException 400: brand_profile_id None.
        HTTPException 404: job yoksa veya workspace ile eşleşmiyorsa.
    """
    if brand_profile_id is None:
        raise HTTPException(
            status_code=400,
            detail="brand_profile_id is required",
        )

    verify_workspace(db, brand_profile_id)

    job = (
        db.query(ExportJob)
        .filter(
            ExportJob.id == export_job_id,
            ExportJob.brand_profile_id == brand_profile_id,
        )
        .first()
    )
    if not job:
        raise HTTPException(status_code=404, detail="export not found in workspace")
    return job