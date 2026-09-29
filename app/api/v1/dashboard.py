"""Dashboard workflow cockpit endpoint (P5.1).

Aggregates workspace + run + content + export + health into a single response.
Frontend renders the response as-is — no status-derivation on the client.

Caching:
- Summary: Redis `dashboard:summary:{workspace_id|none}:{run_id|latest}` 15s TTL.
- Google Ads health: Redis `dashboard:gads_health` 120s TTL (independent).
- `refresh=true` bypasses the summary cache. The gads_health cache is honored
  unless `refresh_health=true` is also set.
- Failed responses (exceptions) are not cached.
"""
from __future__ import annotations

import json
from typing import Optional, Tuple

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from loguru import logger
from sqlalchemy.orm import Session

from app.api.v1.google_ads import _get_redis_client
from app.core.dashboard.summary import build_summary
from app.dependencies import get_db
from app.schemas.dashboard import DashboardSummary


router = APIRouter()

SUMMARY_TTL_SECONDS = 15
GADS_HEALTH_TTL_SECONDS = 120


def _summary_cache_key(workspace_id: Optional[int], run_id: Optional[int]) -> str:
    ws = "none" if workspace_id is None else str(workspace_id)
    rid = "latest" if run_id is None else str(run_id)
    return f"dashboard:summary:{ws}:{rid}"


def _gads_health_cached() -> Optional[Tuple[Optional[str], Optional[str]]]:
    """Yalnizca CACHE'ten Google Ads sagligini okur; canli probe YAPMAZ.

    - Cache hit → (status, error)
    - Cache miss → None (cagiran taraf "unknown" doner + arka planda isitir)
    - Redis yoksa (test ortami) → eski bloklu davranisa duser ki mevcut
      testler ve cache'siz kurulumlar ayni sonucu almaya devam etsin.
    """
    redis_client = _get_redis_client()
    if not redis_client:
        return _gads_health(refresh=False)
    try:
        cached = redis_client.get("dashboard:gads_health")
        if cached:
            raw = cached.decode() if isinstance(cached, bytes) else str(cached)
            payload = json.loads(raw)
            if isinstance(payload, dict):
                return payload.get("status"), payload.get("error")
    except Exception:
        pass
    return None


def _gads_health(*, refresh: bool) -> Tuple[Optional[str], Optional[str]]:
    """Probe Google Ads connectivity.

    Returns `(status, error)` and mirrors `GoogleAdsService.health_check()`.
    This intentionally performs the real auth/permission probe, but behind a
    120s cache so the dashboard does not mask expired refresh tokens as "ok".
    """
    redis_client = _get_redis_client()
    key = "dashboard:gads_health"
    if redis_client and not refresh:
        try:
            cached = redis_client.get(key)
            if cached:
                raw = cached.decode() if isinstance(cached, bytes) else str(cached)
                payload = json.loads(raw)
                if isinstance(payload, dict):
                    return payload.get("status"), payload.get("error")
        except Exception:
            pass

    try:
        from app.config import settings
        from app.integrations.google_ads.service import GoogleAdsService

        svc = GoogleAdsService(settings)
        result = svc.health_check()
        status = result.get("status") or "error"
        error = result.get("error")
        if isinstance(error, str) and "invalid_grant" in error:
            error = (
                "Google Ads refresh token geçersiz veya süresi dolmuş. "
                "GOOGLE_ADS_REFRESH_TOKEN yenilenmeli."
            )
    except Exception as exc:
        logger.debug(f"google ads health probe failed: {exc}")
        status = "error"
        error = str(exc)

    if redis_client:
        try:
            redis_client.setex(
                key,
                GADS_HEALTH_TTL_SECONDS,
                json.dumps({"status": status, "error": error}),
            )
        except Exception:
            pass
    return status, error


def _resolve_active_run_id(
    db: Session, workspace_id: int, active_run_id: Optional[int]
) -> Optional[int]:
    """Pre-resolves 'latest' to a concrete run_id so the cache key is stable."""
    from app.database.models import ScoringRun

    if active_run_id is not None:
        run = db.query(ScoringRun).filter(ScoringRun.id == active_run_id).first()
        if run is None or run.brand_profile_id != workspace_id:
            raise HTTPException(status_code=404, detail="scoring run not found in workspace")
        return active_run_id

    latest = (
        db.query(ScoringRun.id)
        .filter(ScoringRun.brand_profile_id == workspace_id)
        .order_by(ScoringRun.created_at.desc())
        .first()
    )
    return int(latest[0]) if latest else None


@router.get("/workspace-summary", response_model=DashboardSummary)
def workspace_summary(
    background_tasks: BackgroundTasks,
    brand_profile_id: Optional[int] = Query(None, ge=1),
    active_run_id: Optional[int] = Query(None, ge=1),
    refresh: bool = Query(False),
    refresh_health: bool = Query(False),
    db: Session = Depends(get_db),
):
    """Single aggregate endpoint for the dashboard cockpit."""
    # Resolve 'latest' before building the cache key so polling matches.
    resolved_run_id: Optional[int]
    if brand_profile_id is not None:
        # validates run-belongs-to-workspace + returns 404 if mismatch
        from app.core.workspace import verify_workspace

        verify_workspace(db, brand_profile_id)
        resolved_run_id = _resolve_active_run_id(db, brand_profile_id, active_run_id)
    else:
        resolved_run_id = None

    cache_key = _summary_cache_key(brand_profile_id, resolved_run_id)

    redis_client = _get_redis_client()
    if redis_client and not refresh:
        try:
            raw = redis_client.get(cache_key)
            if raw:
                return DashboardSummary.model_validate(json.loads(raw))
        except Exception:
            pass

    # Google Ads sagligi sayfa acilisini BEKLETMESIN: cache'ten oku; yoksa
    # "unknown" don ve arka planda isit (canli probe ~1-4 sn surebiliyordu,
    # dashboard'in "yukleniyor" yavasliginin ana kaynagi buydu). Kullanicinin
    # acikca istedigi yenilemede (refresh_health) canli probe korunur.
    if refresh_health:
        google_ads_health, google_ads_error = _gads_health(refresh=True)
    else:
        cached_health = _gads_health_cached()
        if cached_health is not None:
            google_ads_health, google_ads_error = cached_health
        else:
            google_ads_health, google_ads_error = "unknown", None
            background_tasks.add_task(_gads_health, refresh=False)

    summary = build_summary(
        db,
        workspace_id=brand_profile_id,
        active_run_id=resolved_run_id,
        api_health="ok",
        google_ads_health=google_ads_health,
        google_ads_error=google_ads_error,
    )

    # "unknown" saglikli ozet SUMMARY_TTL boyunca cache'e yapismasIn —
    # bir sonraki poll (30 sn) isitilmis degeri alabilsin.
    if redis_client and google_ads_health != "unknown":
        try:
            redis_client.setex(
                cache_key,
                SUMMARY_TTL_SECONDS,
                summary.model_dump_json(),
            )
        except Exception:
            pass

    return summary
