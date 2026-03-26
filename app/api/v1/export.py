"""
Export API Endpoints.

4 endpoint:
- POST /export - Export başlat
- GET /export/{id}/status - Durum kontrol
- GET /export/{id}/download - Dosya indir
- GET /export/run/{run_id} - Geçmiş exportlar
"""
import os
import uuid
import tempfile
from datetime import datetime
from typing import Optional, Dict

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.dependencies import get_db
from app.database.models import ScoringRun
from app.schemas.export import (
    ExportRequest, ExportStatusResponse, ExportListResponse,
    ExportFormatEnum, ExportSectionEnum, ExportStatusEnum
)
from app.exporters import DocxExporter, PdfExporter, ExcelExporter, CsvExporter


router = APIRouter()

# In-memory export status tracking (production'da Redis/DB kullanılmalı)
_export_status: Dict[str, dict] = {}


def _get_exporter(format: ExportFormatEnum, db: Session):
    """Format'a göre exporter döndürür."""
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
    """Format'a göre dosya uzantısı."""
    extensions = {
        ExportFormatEnum.DOCX: '.docx',
        ExportFormatEnum.PDF: '.pdf',
        ExportFormatEnum.EXCEL: '.xlsx',
        ExportFormatEnum.CSV: '.zip',  # CSV'ler ZIP olarak
    }
    return extensions.get(format, '.bin')


def _run_export(export_id: str, request: ExportRequest, db: Session):
    """Background'da export işlemini çalıştırır."""
    try:
        _export_status[export_id]['status'] = ExportStatusEnum.PROCESSING
        _export_status[export_id]['progress'] = 10
        
        exporter = _get_exporter(request.format, db)
        
        ext = _get_file_extension(request.format)
        filename = f"digitus_report_{request.scoring_run_id}_{export_id[:8]}{ext}"
        filepath = os.path.join(tempfile.gettempdir(), filename)
        
        _export_status[export_id]['progress'] = 30
        
        exporter.export(
            scoring_run_id=request.scoring_run_id,
            sections=request.sections,
            filepath=filepath
        )
        
        _export_status[export_id]['status'] = ExportStatusEnum.COMPLETED
        _export_status[export_id]['progress'] = 100
        _export_status[export_id]['file_name'] = filename
        _export_status[export_id]['filepath'] = filepath
        
    except Exception as e:
        _export_status[export_id]['status'] = ExportStatusEnum.FAILED
        _export_status[export_id]['error_message'] = str(e)


@router.post("/", response_model=ExportStatusResponse)
def create_export(
    request: ExportRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """
    Export işlemi başlatır.
    
    Body:
    - scoring_run_id: Scoring run ID
    - format: docx, pdf, excel, csv
    - sections: ["summary", "scoring", "channels", "seo_content", "ads", "social", "all"]
    - include_compliance_details: SEO/GEO detayları
    """
    # Scoring run kontrolü
    scoring_run = db.query(ScoringRun).filter(ScoringRun.id == request.scoring_run_id).first()
    if not scoring_run:
        raise HTTPException(status_code=404, detail="Scoring run bulunamadı")
    
    # Export ID oluştur
    export_id = str(uuid.uuid4())
    
    # Status kaydet
    _export_status[export_id] = {
        'export_id': export_id,
        'status': ExportStatusEnum.PENDING,
        'progress': 0,
        'file_name': None,
        'filepath': None,
        'error_message': None,
        'created_at': datetime.utcnow(),
        'scoring_run_id': request.scoring_run_id,
        'format': request.format.value,
        'sections': [s.value for s in request.sections]
    }
    
    # Background task olarak çalıştır
    background_tasks.add_task(_run_export, export_id, request, db)
    
    return ExportStatusResponse(
        export_id=export_id,
        status=ExportStatusEnum.PENDING,
        progress=0,
        created_at=datetime.utcnow()
    )


@router.get("/{export_id}/status", response_model=ExportStatusResponse)
def get_export_status(export_id: str):
    """
    Export durumunu kontrol eder.
    
    Returns:
    - status: pending, processing, completed, failed
    - progress: 0-100
    - file_name: Tamamlandığında dosya adı
    """
    if export_id not in _export_status:
        raise HTTPException(status_code=404, detail="Export bulunamadı")
    
    status = _export_status[export_id]
    
    return ExportStatusResponse(
        export_id=export_id,
        status=status['status'],
        progress=status['progress'],
        file_name=status['file_name'],
        error_message=status['error_message'],
        created_at=status['created_at']
    )


@router.get("/{export_id}/download")
def download_export(export_id: str):
    """
    Export dosyasını indirir.
    
    FileResponse olarak dosyayı döner.
    """
    if export_id not in _export_status:
        raise HTTPException(status_code=404, detail="Export bulunamadı")
    
    status = _export_status[export_id]
    
    if status['status'] != ExportStatusEnum.COMPLETED:
        raise HTTPException(
            status_code=400,
            detail=f"Export henüz tamamlanmadı. Durum: {status['status']}"
        )
    
    filepath = status.get('filepath')
    filename = status.get('file_name')
    
    if not filepath or not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Dosya bulunamadı")
    
    # MIME type
    mime_types = {
        '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        '.pdf': 'application/pdf',
        '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        '.zip': 'application/zip',
    }
    
    ext = os.path.splitext(filename)[1]
    media_type = mime_types.get(ext, 'application/octet-stream')
    
    return FileResponse(
        path=filepath,
        filename=filename,
        media_type=media_type
    )


@router.get("/run/{run_id}", response_model=ExportListResponse)
def list_exports_for_run(run_id: int):
    """
    Bir scoring run için daha önce oluşturulan exportları listeler.
    """
    exports = []
    
    for export_id, status in _export_status.items():
        if status.get('scoring_run_id') == run_id:
            exports.append(ExportStatusResponse(
                export_id=export_id,
                status=status['status'],
                progress=status['progress'],
                file_name=status['file_name'],
                error_message=status['error_message'],
                created_at=status['created_at']
            ))
    
    return ExportListResponse(exports=exports, total=len(exports))


# ============================================================
# ESKI UYUMLULUK (Basit export)
# ============================================================

@router.post("/simple")
def create_simple_export(
    scoring_run_id: int,
    format: str = "excel",
    db: Session = Depends(get_db)
):
    """
    Basit export (eski uyumluluk).
    Sadece kanal havuzlarını export eder.
    """
    scoring_run = db.query(ScoringRun).filter(ScoringRun.id == scoring_run_id).first()
    if not scoring_run:
        raise HTTPException(status_code=404, detail="Scoring run not found")
    
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
        filepath=filepath
    )
    
    return {
        'message': 'Export oluşturuldu',
        'format': format,
        'filename': filename,
        'filepath': filepath
    }
