"""
Export Celery tasks (plan2 §P2).

_run_export background function replaced by this Celery task so that:
- Export jobs survive app restart (DB-backed ExportJob row).
- Multi-worker deployments share state through Postgres instead of per-process dict.
- Session lifecycle is owned by the task (no request-scope db leak).
"""
import os

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


def _export_output_dir() -> str:
    """Return a directory shared by the app and Celery containers."""
    path = os.environ.get("EXPORT_OUTPUT_DIR", "/app/exports")
    os.makedirs(path, exist_ok=True)
    return path


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
        filepath = os.path.join(_export_output_dir(), filename)

        job.progress = 30
        db.commit()

        from app.schemas.export import ExportSectionEnum

        sections = (
            [ExportSectionEnum(s) for s in job.sections]
            if job.sections
            else [ExportSectionEnum.CHANNELS]
        )

        # Icerik invariant'i (plan v4 §5 + codex post-review-2 #1): dosya
        # uretiminden ONCE bolum basina export edilebilir KIMLIK KUMESI
        # alinir (collector filtreleriyle birebir; include_stale_content
        # dahil). Bos kume → NO_CONTENT, dosya uretilmez. Dosya uretiminden
        # SONRA ayni kume yeniden alinir ve KARSILASTIRILIR: toplam sayi
        # ayni kalsa bile icerik degistiyse (esit sayida stale+yeni, ADS set
        # aktivasyonu, reassignment) dosya SILINIR, job failed olur — bayat
        # icerik "guncel rapor" diye teslim edilmez.
        _CONTENT_SECTIONS = {"ads", "seo_content", "social"}
        section_values = [s.value for s in sections]
        is_content_only = bool(section_values) and set(section_values) <= _CONTENT_SECTIONS

        def _content_identity() -> dict:
            from app.exporters.data_collector import collect_exportable_identity

            return {
                section: collect_exportable_identity(
                    db,
                    job.scoring_run_id,
                    section,
                    include_stale_content=job.include_stale_content,
                )
                for section in section_values
            }

        def _fail(message: str) -> None:
            job.status = "failed"
            job.error_message = message
            db.commit()

        _NO_CONTENT_MSG = (
            "NO_CONTENT: bu bölüm için export edilebilir içerik yok — "
            "önce içerik üretin."
        )

        pre_identity: dict = {}
        if is_content_only:
            pre_identity = _content_identity()
            if not any(pre_identity.values()):
                _fail(_NO_CONTENT_MSG)
                return

        exporter.export(
            scoring_run_id=job.scoring_run_id,
            sections=sections,
            filepath=filepath,
        )

        if is_content_only:
            post_identity = _content_identity()
            if not any(post_identity.values()):
                _fail(_NO_CONTENT_MSG)
                try:
                    os.remove(filepath)
                except OSError:
                    pass
                return
            if post_identity != pre_identity:
                _fail(
                    "CONTENT_CHANGED: export sürerken içerik değişti "
                    "(yeniden üretim/atama) — rapor kaydedilmedi, "
                    "yeniden deneyin."
                )
                try:
                    os.remove(filepath)
                except OSError:
                    pass
                return

        # Plan v13: rapor dosyasını KAYDETMEDEN önce başlangıç snapshot'ı
        # güncel workspace sürümleriyle karşılaştırılır — veri toplama
        # sürerken politika/profil değiştiyse eski rapor tamamlanamaz.
        if job.requested_policy_version is not None:
            from app.database.models import BrandProfile

            workspace = (
                db.query(BrandProfile)
                .filter(BrandProfile.id == job.brand_profile_id)
                .with_for_update()
                .first()
            )
            if workspace is not None and (
                int(workspace.policy_version or 1) != job.requested_policy_version
                or (
                    job.requested_anchor_version is not None
                    and int(workspace.anchor_version or 1)
                    != job.requested_anchor_version
                )
            ):
                db.rollback()
                job = db.query(ExportJob).filter(ExportJob.id == export_job_id).first()
                job.status = "failed"
                job.error_message = (
                    "POLICY_VERSION_CHANGED: export sürerken politika/profil "
                    "değişti — rapor kaydedilmedi, yeniden deneyin."
                )
                db.commit()
                try:
                    os.remove(filepath)
                except OSError:
                    pass
                return

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
