"""Google-grounded competitor discovery Celery workflow."""
from __future__ import annotations

from datetime import datetime, timezone

from app.tasks.celery_app import celery_app


@celery_app.task(
    bind=True,
    name="policy.competitor_discovery",
    soft_time_limit=360,
    time_limit=420,
)
def run_competitor_discovery_task(self, workspace_id: int, start_fingerprint: str):
    from app.core.policy.competitor_discovery import (
        canonical_profile_fingerprint,
        discover_raw_candidates,
        fresh_channel_run_count,
        persist_suggestions,
        verify_discovered_candidates,
    )
    from app.core.telemetry import UsageCollector
    from app.database.connection import SessionLocal
    from app.database.models import BrandProfile, TaskResult
    from app.generators.ai_service import get_ai_service
    from app.config import settings

    task_id = self.request.id
    db = SessionLocal()
    ai = None
    collector = UsageCollector(
        brand_profile_id=workspace_id,
        task_id=task_id,
        flush_every=1,
    )
    try:
        task = db.query(TaskResult).filter(TaskResult.task_id == task_id).first()
        workspace = db.query(BrandProfile).filter(BrandProfile.id == workspace_id).first()
        if not task or not workspace:
            return {"status": "failed", "code": "WORKSPACE_NOT_FOUND"}
        task.status = "running"
        task.started_at = datetime.now(timezone.utc)
        task.progress = 10
        db.commit()

        if (
            workspace.status not in ("competitor_review", "confirmed")
            or canonical_profile_fingerprint(workspace) != start_fingerprint
        ):
            usage = collector.finalize()
            task.status = "failed"
            task.progress = 100
            task.error_message = "PROFILE_CHANGED"
            task.completed_at = datetime.now(timezone.utc)
            task.result_data = {
                "code": "PROFILE_CHANGED", "candidates": [], "usage": usage
            }
            db.commit()
            return {"status": "failed", "code": "PROFILE_CHANGED"}

        ai = get_ai_service(api_key=settings.GEMINI_API_KEY)
        ai.collector = collector
        raw = discover_raw_candidates(ai, workspace)
        task.progress = 50
        task.result_data = {
            **(task.result_data or {}),
            "search_queries": raw.get("search_queries", []),
            "fallback_used": bool(raw.get("fallback_used")),
        }
        db.commit()

        verified = verify_discovered_candidates(ai, workspace, raw)
        usage = collector.finalize()

        # Provider çağrısından beri profil değişmediyse, row lock altında yaz.
        db.expire_all()
        workspace = (
            db.query(BrandProfile)
            .filter(BrandProfile.id == workspace_id)
            .with_for_update()
            .first()
        )
        task = db.query(TaskResult).filter(TaskResult.task_id == task_id).first()
        if canonical_profile_fingerprint(workspace) != start_fingerprint:
            task.status = "failed"
            task.progress = 100
            task.error_message = "PROFILE_CHANGED"
            task.completed_at = datetime.now(timezone.utc)
            task.result_data = {
                "code": "PROFILE_CHANGED", "candidates": [], "usage": usage
            }
            db.commit()
            return {"status": "failed", "code": "PROFILE_CHANGED"}

        added = persist_suggestions(workspace, verified, start_fingerprint)
        query_count = len(raw.get("search_queries") or [])
        cost = {
            "grounding_queries": query_count,
            "grounding_list_price_usd": round(query_count * 0.014, 6),
            "grounding_price_snapshot": {
                "usd_per_1000_queries": 14.0,
                "captured_at": "2026-07-22",
            },
            "usage": usage,
        }
        task.status = "completed"
        task.progress = 100
        task.completed_at = datetime.now(timezone.utc)
        task.error_message = None
        task.result_data = {
            "candidates": added,
            "warnings": [],
            "search_queries": raw.get("search_queries", []),
            "fallback_used": bool(raw.get("fallback_used")),
            "profile_fingerprint": start_fingerprint,
            "fresh_channel_run_count": fresh_channel_run_count(db, workspace_id),
            "cost": cost,
        }
        db.commit()
        return {"status": "completed", **task.result_data}
    except Exception as exc:
        db.rollback()
        usage = collector.finalize()
        task = db.query(TaskResult).filter(TaskResult.task_id == task_id).first()
        if task:
            task.status = "failed"
            task.progress = 100
            task.completed_at = datetime.now(timezone.utc)
            task.error_message = str(exc)[:500]
            task.result_data = {
                **(task.result_data or {}),
                "code": "DISCOVERY_FAILED",
                "usage": usage,
            }
            db.commit()
        return {"status": "failed", "error": str(exc)}
    finally:
        if ai is not None:
            try:
                ai.close()
            except Exception:
                pass
        db.close()
