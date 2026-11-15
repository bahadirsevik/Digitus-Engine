"""Dijital varlik denetimi Celery gorevi."""
from __future__ import annotations

from app.tasks.celery_app import celery_app


@celery_app.task(
    bind=True,
    name="digital_audit.run",
    # 4 hedef x (~240 sn tarama + hiz olcumleri). Asilirsa o ana kadarki sonuclar kalir.
    soft_time_limit=1500,
    time_limit=1560,
)
def run_digital_audit_task(self, audit_id: int):
    from app.core.digital_audit.service import run_audit
    from app.database.connection import SessionLocal

    db = SessionLocal()
    try:
        return {"status": run_audit(db, audit_id, self.request.id)}
    finally:
        db.close()


@celery_app.task(
    bind=True,
    name="digital_audit.research",
    # 2 aramali cagri + kaynak indirme (<=300 sn) + sentez. Asilirsa kaydedilen bolumler kalir.
    soft_time_limit=1500,
    time_limit=1560,
)
def run_digital_research_task(self, report_id: int):
    from app.core.digital_audit.research_service import run_research
    from app.database.connection import SessionLocal
    from app.generators.ai_service import get_ai_service

    db = SessionLocal()
    try:
        return {"status": run_research(db, report_id, self.request.id, get_ai_service())}
    finally:
        db.close()
