"""Dijital varlik denetimi API'si. ScoringRun gerektirmez; workspace'e baglidir."""
from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger
from sqlalchemy.orm import Session

from app.core.digital_audit import service
from app.core.workspace import verify_workspace
from app.database.connection import get_db
from app.database.models import DigitalAudit
from app.schemas.digital_audit import AnswersRequest, StartAuditRequest

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
