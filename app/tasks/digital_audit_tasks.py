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
