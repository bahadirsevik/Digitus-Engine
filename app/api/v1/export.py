"""
Export API Endpoints.

Plan2 §P2 — ExportJob DB-backed refactor:
- ExportJob Postgres table replaces in-memory _export_status dict.
  Multi-worker safe; survives app restart.
- Export runs as a Celery task (run_export_task) that owns its own
  SessionLocal — no request-scope db leak.
- All endpoints workspace-scoped via verify_export_in_workspace / verify_scoring_run.
"""
import os
import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.error_responses import safe_500_detail
from app.core.workspace import verify_export_in_workspace, verify_scoring_run
from app.database.models import ExportJob
from app.dependencies import get_db
from app.schemas.export import (
    ExportFormatEnum,
    ExportListResponse,
    ExportRequest,
    ExportStatusEnum,
    ExportStatusResponse,
)


router = APIRouter()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _to_status_response(job: ExportJob) -> ExportStatusResponse:
    return ExportStatusResponse(
        export_id=job.id,
        status=ExportStatusEnum(job.status),
        progress=job.progress,
        file_name=job.file_name,
        error_message=job.error_message,
        created_at=job.created_at,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/", response_model=ExportStatusResponse)
def create_export(
    request: ExportRequest,
    brand_profile_id: int = Query(..., description="Workspace owning the scoring run"),
    db: Session = Depends(get_db),
):
    """Export başlatır. Plan2 §P2: Celery task + DB-backed status."""
    verify_scoring_run(db, request.scoring_run_id, brand_profile_id)

    export_id = str(uuid.uuid4())
    job = ExportJob(
        id=export_id,
        brand_profile_id=brand_profile_id,
        scoring_run_id=request.scoring_run_id,
        status="pending",
        progress=0,
        format=request.format.value,
        sections=[s.value for s in (request.sections or [])],
        include_stale_content=request.include_stale_content or False,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    from app.tasks.export_tasks import run_export_task
    try:
        run_export_task.delay(export_id)
    except Exception as exc:
        job.status = "failed"
        job.error_message = safe_500_detail(exc, "Export kuyruğa alınamadı")
        db.commit()
        db.refresh(job)

    return _to_status_response(job)


@router.get("/{export_id}/status", response_model=ExportStatusResponse)
def get_export_status(
    export_id: str,
    brand_profile_id: int = Query(..., description="Workspace owning the export"),
    db: Session = Depends(get_db),
):
    """Export durumunu döner."""
    job = verify_export_in_workspace(db, export_id, brand_profile_id)
    return _to_status_response(job)


@router.get("/{export_id}/download")
def download_export(
    export_id: str,
    brand_profile_id: int = Query(..., description="Workspace owning the export"),
    db: Session = Depends(get_db),
):
    """Export dosyasını indirir."""
    job = verify_export_in_workspace(db, export_id, brand_profile_id)

    if job.status != "completed":
        raise HTTPException(
            status_code=400,
            detail=f"Export henüz tamamlanmadı. Durum: {job.status}",
        )

    filepath = job.filepath
    filename = job.file_name

    if not filepath or not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Dosya bulunamadı")

    mime_types = {
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".pdf": "application/pdf",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".zip": "application/zip",
    }
    ext = os.path.splitext(filename)[1]
    media_type = mime_types.get(ext, "application/octet-stream")

    return FileResponse(path=filepath, filename=filename, media_type=media_type)


@router.get("/run/{run_id}", response_model=ExportListResponse)
def list_exports_for_run(
    run_id: int,
    brand_profile_id: int = Query(..., description="Workspace owning the run"),
    db: Session = Depends(get_db),
):
    """Bir scoring run için workspace içindeki export job'larını listeler."""
    verify_scoring_run(db, run_id, brand_profile_id)

    jobs: List[ExportJob] = (
        db.query(ExportJob)
        .filter(
            ExportJob.brand_profile_id == brand_profile_id,
            ExportJob.scoring_run_id == run_id,
        )
        .order_by(ExportJob.created_at.desc())
        .all()
    )

    return ExportListResponse(exports=[_to_status_response(j) for j in jobs], total=len(jobs))


# ---------------------------------------------------------------------------
# Simple/legacy export (workspace-scoped, synchronous, kept for compatibility)
# ---------------------------------------------------------------------------

@router.post("/simple")
def create_simple_export(
    scoring_run_id: int,
    brand_profile_id: int = Query(..., description="Workspace owning the run"),
    format: str = "excel",
    db: Session = Depends(get_db),
):
    """Basit senkron export. Workspace zorunlu."""
    import tempfile

    from app.exporters import CsvExporter, DocxExporter, ExcelExporter, PdfExporter
    from app.schemas.export import ExportSectionEnum

    verify_scoring_run(db, scoring_run_id, brand_profile_id)

    try:
        format_enum = ExportFormatEnum(format.lower())
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid format: {format}")

    exporters = {
        ExportFormatEnum.DOCX: DocxExporter,
        ExportFormatEnum.PDF: PdfExporter,
        ExportFormatEnum.EXCEL: ExcelExporter,
        ExportFormatEnum.CSV: CsvExporter,
    }
    exporter = exporters[format_enum](db)

    extensions = {
        ExportFormatEnum.DOCX: ".docx",
        ExportFormatEnum.PDF: ".pdf",
        ExportFormatEnum.EXCEL: ".xlsx",
        ExportFormatEnum.CSV: ".zip",
    }
    ext = extensions.get(format_enum, ".bin")
    filename = f"digitus_simple_{scoring_run_id}{ext}"
    filepath = os.path.join(tempfile.gettempdir(), filename)

    exporter.export(
        scoring_run_id=scoring_run_id,
        sections=[ExportSectionEnum.CHANNELS],
        filepath=filepath,
    )

    return {
        "message": "Export oluşturuldu",
        "format": format,
        "filename": filename,
        "filepath": filepath,
    }
