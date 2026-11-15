"""Dijital varlik denetimi API'si. ScoringRun gerektirmez; workspace'e baglidir."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from loguru import logger
from sqlalchemy.orm import Session

from app.core.digital_audit import report_doc, research_service, service
from app.core.http_headers import content_disposition
from app.core.workspace import verify_workspace
from app.database.connection import get_db
from app.database.models import DigitalAudit, DigitalResearch
from app.schemas.digital_audit import (
    AnswersRequest,
    BriefsRequest,
    ClaimOverridesRequest,
    StartAuditRequest,
    StartResearchRequest,
)

router = APIRouter()


def _get_audit(db: Session, audit_id: int, brand_profile_id: int) -> DigitalAudit:
    """Denetim bu workspace'e ait degilse 404 (mevcudiyet sizdirmaz)."""
    verify_workspace(db, brand_profile_id)
    service.reconcile_expired(db, brand_profile_id)
    audit = (
        db.query(DigitalAudit)
        .filter(DigitalAudit.id == audit_id, DigitalAudit.brand_profile_id == brand_profile_id)
        .first()
    )
    if not audit:
        raise HTTPException(status_code=404, detail="Denetim bulunamadı")
    return audit


@router.post("/audits", status_code=202)
def start_audit(
    request: StartAuditRequest,
    brand_profile_id: int = Query(...),
    db: Session = Depends(get_db),
):
    """Denetimi baslatir. Suren denetim varsa onu dondurur (reused=true)."""
    workspace = verify_workspace(db, brand_profile_id)
    try:
        audit, reused = service.start_audit(db, workspace, request.own_url, request.competitor_urls)
    except service.AuditInputError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not reused:
        try:
            from app.tasks.digital_audit_tasks import run_digital_audit_task

            run_digital_audit_task.apply_async(args=[audit.id], task_id=audit.task_id)
        except Exception as exc:
            logger.error(f"Dijital denetim kuyruğa alınamadı: {exc}")
            service.mark_dispatch_failed(db, audit)
            raise HTTPException(
                status_code=502,
                detail={"code": "BROKER_ERROR", "message": "Denetim başlatılamadı. Lütfen tekrar deneyin."},
            )
    return {"audit_id": audit.id, "status": audit.status, "reused": reused}


@router.get("/audits")
def list_audits(brand_profile_id: int = Query(...), db: Session = Depends(get_db)):
    verify_workspace(db, brand_profile_id)
    service.reconcile_expired(db, brand_profile_id)
    audits = (
        db.query(DigitalAudit)
        .filter(DigitalAudit.brand_profile_id == brand_profile_id)
        .order_by(DigitalAudit.created_at.desc(), DigitalAudit.id.desc())
        .limit(20)
        .all()
    )
    return {"audits": [service.serialize(a) for a in audits]}


@router.get("/audits/{audit_id}")
def get_audit(audit_id: int, brand_profile_id: int = Query(...), db: Session = Depends(get_db)):
    return service.serialize(_get_audit(db, audit_id, brand_profile_id), detail=True)


@router.put("/audits/{audit_id}/answers")
def put_answers(
    audit_id: int,
    request: AnswersRequest,
    brand_profile_id: int = Query(...),
    db: Session = Depends(get_db),
):
    """Musteri soru formu. Otomatik bulgulari degistirmez."""
    audit = _get_audit(db, audit_id, brand_profile_id)
    try:
        service.save_answers(db, audit, {k: v.model_dump() for k, v in request.answers.items()})
    except service.AuditInputError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"answers": audit.answers or {}}


# --- Faz 2: arastirma, segmentler, briefler, rapor dosyasi -------------------

def _get_research(db: Session, report_id: int, brand_profile_id: int) -> DigitalResearch:
    verify_workspace(db, brand_profile_id)
    research_service.reconcile_expired(db, brand_profile_id)
    report = (
        db.query(DigitalResearch)
        .filter(DigitalResearch.id == report_id, DigitalResearch.brand_profile_id == brand_profile_id)
        .first()
    )
    if not report:
        raise HTTPException(status_code=404, detail="Araştırma bulunamadı")
    return report


def _dispatch_research(db: Session, report: DigitalResearch) -> None:
    try:
        from app.tasks.digital_audit_tasks import run_digital_research_task

        run_digital_research_task.apply_async(args=[report.id], task_id=report.task_id)
    except Exception as exc:
        logger.error(f"Araştırma kuyruğa alınamadı: {exc}")
        research_service.mark_dispatch_failed(db, report)
        raise HTTPException(
            status_code=502,
            detail={"code": "BROKER_ERROR", "message": "Araştırma başlatılamadı. Lütfen tekrar deneyin."},
        )


@router.post("/research", status_code=202)
def start_research(
    request: StartResearchRequest,
    brand_profile_id: int = Query(...),
    db: Session = Depends(get_db),
):
    """Kaynakli sektor + hedef kitle arastirmasini baslatir. Suren varsa onu dondurur."""
    workspace = verify_workspace(db, brand_profile_id)
    try:
        report, reused = research_service.start_research(
            db, workspace, request.sector, request.geography, request.focus)
    except research_service.ResearchInputError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not reused:
        _dispatch_research(db, report)
    return {"research_id": report.id, "status": report.status, "reused": reused}


@router.get("/research")
def list_research(brand_profile_id: int = Query(...), db: Session = Depends(get_db)):
    verify_workspace(db, brand_profile_id)
    research_service.reconcile_expired(db, brand_profile_id)
    reports = (
        db.query(DigitalResearch)
        .filter(DigitalResearch.brand_profile_id == brand_profile_id)
        .order_by(DigitalResearch.created_at.desc(), DigitalResearch.id.desc())
        .limit(20)
        .all()
    )
    return {"reports": [research_service.serialize(r) for r in reports]}


@router.get("/research/{report_id}")
def get_research(report_id: int, brand_profile_id: int = Query(...), db: Session = Depends(get_db)):
    return research_service.serialize(_get_research(db, report_id, brand_profile_id), detail=True)


@router.put("/research/{report_id}/claims")
def put_claim_overrides(
    report_id: int,
    request: ClaimOverridesRequest,
    brand_profile_id: int = Query(...),
    db: Session = Depends(get_db),
):
    """Analist baglami okuyup iddianin rapora girip girmeyecegini degistirir."""
    report = _get_research(db, report_id, brand_profile_id)
    try:
        research_service.save_claim_overrides(db, report, request.overrides)
    except research_service.ResearchInputError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"claim_overrides": report.claim_overrides or []}


@router.post("/research/{report_id}/briefs", status_code=202)
def start_briefs(
    report_id: int,
    request: BriefsRequest,
    brand_profile_id: int = Query(...),
    db: Session = Depends(get_db),
):
    """Secilen segment ve kanallar icin reklam + kreatif brief uretimini baslatir."""
    report = _get_research(db, report_id, brand_profile_id)
    try:
        research_service.request_briefs(db, report, request.segments, request.channels)
    except research_service.ResearchInputError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except research_service.ResearchStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    _dispatch_research(db, report)
    return {"research_id": report.id, "status": report.status}


@router.get("/export")
def export_report(
    brand_profile_id: int = Query(...),
    format: str = Query("docx", pattern="^(docx|pdf)$"),
    audit_id: Optional[int] = Query(None),
    research_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    """Bagimsiz rapor dosyasi (site denetimi ve/veya arastirma). Skorlama calismasi gerektirmez."""
    workspace = verify_workspace(db, brand_profile_id)
    if audit_id is None and research_id is None:
        raise HTTPException(status_code=400, detail="Rapor için bir denetim ya da araştırma seçin.")
    audit = _get_audit(db, audit_id, brand_profile_id) if audit_id is not None else None
    report = _get_research(db, research_id, brand_profile_id) if research_id is not None else None
    content, media_type = report_doc.render(workspace, audit, report, format)
    return Response(
        content=content, media_type=media_type,
        headers={"Content-Disposition": content_disposition(report_doc.filename(workspace, format))},
    )
