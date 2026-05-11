"""
Export API Endpoints.

Plan2 §P0/C4 — minimal güvenlik hotfix'i (E1):
- create_export/list_exports_for_run: brand_profile_id zorunlu, verify_workspace.
- _run_export: kendi SessionLocal() açar (request-scope db'yi BackgroundTask'a
  taşıma anti-pattern'i kapandı).
- _export_status dict key: (brand_profile_id, export_id) tuple — cross-workspace
  leak engelleniyor.
- ExportJob tablosu + Celery refactor P2'de.
"""
import os
import tempfile
import uuid
from datetime import datetime
from typing import Dict, Optional, Tuple

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.error_responses import safe_500_detail
from app.core.workspace import verify_scoring_run, verify_workspace
from app.database.connection import SessionLocal
from app.database.models import ScoringRun
from app.dependencies import get_db
from app.exporters import CsvExporter, DocxExporter, ExcelExporter, PdfExporter
from app.schemas.export import (
    ExportFormatEnum,
    ExportListResponse,
    ExportRequest,
    ExportSectionEnum,
    ExportStatusEnum,
    ExportStatusResponse,
)


router = APIRouter()

# In-memory export status — DB-backed ExportJob tablosu P2'de gelecek.
# Key: (brand_profile_id, export_id) tuple — workspace-scoped lookup zorunlu.
_export_status: Dict[Tuple[int, str], dict] = {}


def _get_exporter(format: ExportFormatEnum, db: Session):
    exporters = {
        ExportFormatEnum.DOCX: DocxExporter,
        ExportFormatEnum.PDF: PdfExporter,
        ExportFormatEnum.EXCEL: ExcelExporter,
        ExportFormatEnum.CSV: CsvExporter,
    }
    exporter_class = exporters.get(format)
    if not exporter_class:
        raise HTTPException(status_code=400, detail=f"Unsupported format: {format}")
    return exporter_class(db)


def _get_file_extension(format: ExportFormatEnum) -> str:
    extensions = {
        ExportFormatEnum.DOCX: '.docx',
        ExportFormatEnum.PDF: '.pdf',
        ExportFormatEnum.EXCEL: '.xlsx',
        ExportFormatEnum.CSV: '.zip',
    }
    return extensions.get(format, '.bin')


def _run_export(brand_profile_id: int, export_id: str, request: ExportRequest):
    """Background export runner.

    Plan2 §P0/C4 fix: own SessionLocal — eski yapı request-scope `db` parametresini
    BackgroundTask'a aktarıyordu; FastAPI request bittiğinde session kapanır ve
    background task closed session üzerinde çalışmaya çalışırdı. Şimdi task
    kendi session'ını açıp kapatıyor.
    """
    key = (brand_profile_id, export_id)
    db = SessionLocal()
    try:
        _export_status[key]['status'] = ExportStatusEnum.PROCESSING
        _export_status[key]['progress'] = 10

        exporter = _get_exporter(request.format, db)
        exporter.data_collector.include_stale_content = request.include_stale_content

        ext = _get_file_extension(request.format)
        filename = f"digitus_report_{request.scoring_run_id}_{export_id[:8]}{ext}"
        filepath = os.path.join(tempfile.gettempdir(), filename)

        _export_status[key]['progress'] = 30

        exporter.export(
            scoring_run_id=request.scoring_run_id,
            sections=request.sections,
            filepath=filepath,
        )

        _export_status[key]['status'] = ExportStatusEnum.COMPLETED
        _export_status[key]['progress'] = 100
        _export_status[key]['file_name'] = filename
        _export_status[key]['filepath'] = filepath

    except Exception as e:
        _export_status[key]['status'] = ExportStatusEnum.FAILED
        _export_status[key]['error_message'] = safe_500_detail(e, "Export basarisiz oldu")
    finally:
        db.close()


def _lookup_export(brand_profile_id: int, export_id: str) -> dict:
    db = SessionLocal()
    try:
        verify_workspace(db, brand_profile_id)
    finally:
        db.close()

    key = (brand_profile_id, export_id)
    if key not in _export_status:
        raise HTTPException(status_code=404, detail="export not found in workspace")
    return _export_status[key]


@router.post("/", response_model=ExportStatusResponse)
def create_export(
    request: ExportRequest,
    background_tasks: BackgroundTasks,
    brand_profile_id: int = Query(..., description="Workspace owning the scoring run"),
    db: Session = Depends(get_db),
):
    """Export başlatır. Plan2 §P0/C4: brand_profile_id zorunlu."""
    # Run workspace'e ait olmalı.
    verify_scoring_run(db, request.scoring_run_id, brand_profile_id, mutating=True)

    export_id = str(uuid.uuid4())
    key = (brand_profile_id, export_id)
    _export_status[key] = {
        'export_id': export_id,
        'brand_profile_id': brand_profile_id,
        'status': ExportStatusEnum.PENDING,
        'progress': 0,
        'file_name': None,
        'filepath': None,
        'error_message': None,
        'created_at': datetime.utcnow(),
        'scoring_run_id': request.scoring_run_id,
        'format': request.format.value,
        'sections': [s.value for s in request.sections],
        'include_stale_content': request.include_stale_content,
    }

    # Background task — kendi SessionLocal açar, request-scope db taşımıyor.
    background_tasks.add_task(_run_export, brand_profile_id, export_id, request)

    return ExportStatusResponse(
        export_id=export_id,
        status=ExportStatusEnum.PENDING,
        progress=0,
        created_at=datetime.utcnow(),
    )


@router.get("/{export_id}/status", response_model=ExportStatusResponse)
def get_export_status(
    export_id: str,
    brand_profile_id: int = Query(..., description="Workspace owning the export"),
):
    """Export durumunu döner. Workspace zorunlu."""
    status = _lookup_export(brand_profile_id, export_id)
    return ExportStatusResponse(
        export_id=export_id,
        status=status['status'],
        progress=status['progress'],
        file_name=status['file_name'],
        error_message=status['error_message'],
        created_at=status['created_at'],
    )


@router.get("/{export_id}/download")
def download_export(
    export_id: str,
    brand_profile_id: int = Query(..., description="Workspace owning the export"),
):
    """Export dosyasını indirir. Workspace zorunlu."""
    status = _lookup_export(brand_profile_id, export_id)

    if status['status'] != ExportStatusEnum.COMPLETED:
        raise HTTPException(
            status_code=400,
            detail=f"Export henüz tamamlanmadı. Durum: {status['status']}",
        )

    filepath = status.get('filepath')
    filename = status.get('file_name')

    if not filepath or not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Dosya bulunamadı")

    mime_types = {
        '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        '.pdf': 'application/pdf',
        '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        '.zip': 'application/zip',
    }

    ext = os.path.splitext(filename)[1]
    media_type = mime_types.get(ext, 'application/octet-stream')

    return FileResponse(path=filepath, filename=filename, media_type=media_type)


@router.get("/run/{run_id}", response_model=ExportListResponse)
def list_exports_for_run(
    run_id: int,
    brand_profile_id: int = Query(..., description="Workspace owning the run"),
    db: Session = Depends(get_db),
):
    """Bir scoring run için workspace içindeki exportları listeler. Workspace zorunlu."""
    verify_scoring_run(db, run_id, brand_profile_id, mutating=True)

    exports = []
    for (bp_id, export_id), status in _export_status.items():
        if bp_id != brand_profile_id:
            continue
        if status.get('scoring_run_id') != run_id:
            continue
        exports.append(ExportStatusResponse(
            export_id=export_id,
            status=status['status'],
            progress=status['progress'],
            file_name=status['file_name'],
            error_message=status['error_message'],
            created_at=status['created_at'],
        ))

    return ExportListResponse(exports=exports, total=len(exports))


# ============================================================
# Basit/legacy export (workspace zorunlu)
# ============================================================

@router.post("/simple")
def create_simple_export(
    scoring_run_id: int,
    brand_profile_id: int = Query(..., description="Workspace owning the run"),
    format: str = "excel",
    db: Session = Depends(get_db),
):
    """Basit export. Plan2 §P0/C4: workspace zorunlu."""
    verify_scoring_run(db, scoring_run_id, brand_profile_id, mutating=True)

    try:
        format_enum = ExportFormatEnum(format.lower())
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid format: {format}")

    exporter = _get_exporter(format_enum, db)

    ext = _get_file_extension(format_enum)
    filename = f"digitus_simple_{scoring_run_id}{ext}"
    filepath = os.path.join(tempfile.gettempdir(), filename)

    exporter.export(
        scoring_run_id=scoring_run_id,
        sections=[ExportSectionEnum.CHANNELS],
        filepath=filepath,
    )

    return {
        'message': 'Export oluşturuldu',
        'format': format,
        'filename': filename,
        'filepath': filepath,
    }
