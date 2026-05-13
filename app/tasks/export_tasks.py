"""
Export Celery tasks (plan2 §P2).

_run_export background function replaced by this Celery task so that:
- Export jobs survive app restart (DB-backed ExportJob row).
- Multi-worker deployments share state through Postgres instead of per-process dict.
- Session lifecycle is owned by the task (no request-scope db leak).
"""
import os
import tempfile

from loguru import logger

from app.core.error_responses import safe_500_detail
from app.database.connection import SessionLocal
from app.database.models import ExportJob
from app.exporters import CsvExporter, DocxExporter, ExcelExporter, PdfExporter
from app.schemas.export import ExportFormatEnum
from app.tasks.celery_app import celery_app


def _get_exporter(format_enum: ExportFormatEnum, db):
    exporters = {
        ExportFormatEnum.DOCX: DocxExporter,
        ExportFormatEnum.PDF: PdfExporter,
        ExportFormatEnum.EXCEL: ExcelExporter,
        ExportFormatEnum.CSV: CsvExporter,
    }
    return exporters[format_enum](db)


def _file_extension(format_enum: ExportFormatEnum) -> str:
    return {
        ExportFormatEnum.DOCX: ".docx",
        ExportFormatEnum.PDF: ".pdf",
        ExportFormatEnum.EXCEL: ".xlsx",
        ExportFormatEnum.CSV: ".zip",
    }.get(format_enum, ".bin")


def execute_export_job(export_job_id: str) -> None:
    """Run an export job identified by export_job_id (UUID string).

    Reads ExportJob row for parameters, writes results back to same row.
    Opens its own SessionLocal — never receives a request-scope session.
    """
    db = SessionLocal()
    try:
        job: ExportJob | None = (
            db.query(ExportJob).filter(ExportJob.id == export_job_id).first()
        )
        if not job:
            logger.error("run_export_task: job {} not found", export_job_id)
            return

        job.status = "processing"
        job.progress = 10
        db.commit()

        try:
            format_enum = ExportFormatEnum(job.format)
        except ValueError:
            job.status = "failed"
            job.error_message = f"Unsupported format: {job.format}"
            db.commit()
            return

        exporter = _get_exporter(format_enum, db)
        exporter.data_collector.include_stale_content = job.include_stale_content

        ext = _file_extension(format_enum)
        filename = f"digitus_report_{job.scoring_run_id}_{export_job_id[:8]}{ext}"
        filepath = os.path.join(tempfile.gettempdir(), filename)

        job.progress = 30
        db.commit()

        from app.schemas.export import ExportSectionEnum

        sections = (
            [ExportSectionEnum(s) for s in job.sections]
            if job.sections
            else [ExportSectionEnum.CHANNELS]
        )

        exporter.export(
            scoring_run_id=job.scoring_run_id,
            sections=sections,
            filepath=filepath,
        )

        job.status = "completed"
        job.progress = 100
        job.file_name = filename
        job.filepath = filepath
        db.commit()
        logger.info("run_export_task: job {} completed → {}", export_job_id, filename)

    except Exception as exc:
        logger.exception("run_export_task: job {} failed", export_job_id)
        try:
            db.rollback()
            job = db.query(ExportJob).filter(ExportJob.id == export_job_id).first()
            if job:
                job.status = "failed"
                job.error_message = safe_500_detail(exc, "Export basarisiz oldu")
                db.commit()
        except Exception:
            pass
    finally:
        db.close()


@celery_app.task(bind=True, name="app.tasks.export_tasks.run_export_task")
def run_export_task(self, export_job_id: str):
    execute_export_job(export_job_id)
