"""Rakip marka-adı preview Celery task'ı (plan v13).

BackgroundTasks DEĞİL Celery: web process restart'ında pending/running iş
takılı kalmasın (bilinen proje sorunu). Fail-open bir kolaylıktır — hata
durumunda UI domain-fallback ile devam eder, kullanıcı adı elle düzenler.
Sonuç PERSIST EDİLMEZ (yalnız TaskResult.result_data); kalıcı kayıt review
submit'inde yazılır.
"""
from __future__ import annotations

import json

from app.tasks.celery_app import celery_app

PREVIEW_CACHE_TTL_SECONDS = 24 * 3600


def _cache_key(workspace_id: int, canonical_domain: str) -> str:
    return f"policy-preview:{workspace_id}:{canonical_domain}:v1"


@celery_app.task(bind=True, name="policy.competitor_preview",
                 soft_time_limit=120, time_limit=180)
def run_competitor_preview_task(self, workspace_id: int, urls: list):
    from app.core.policy import normalize_competitor_url
    from app.tasks.task_status import update_task_status

    task_id = self.request.id
    ai = None
    try:
        update_task_status(task_id, status="running", progress=10)

        redis_client = None
        try:
            from app.core.cache import get_redis_client

            redis_client = get_redis_client()
        except Exception:
            redis_client = None  # cache fail-open (proje kuralı)

        urls = list(urls or [])[:3]
        results = []
        to_fetch = []
        for url in urls:
            key = normalize_competitor_url(url)
            if not key:
                results.append({
                    "url": url, "detected_name": "", "source": "invalid",
                    "error": "Geçersiz URL",
                })
                continue
            cached = None
            if redis_client is not None:
                try:
                    raw = redis_client.get(_cache_key(workspace_id, key))
                    cached = json.loads(raw) if raw else None
                except Exception:
                    cached = None
            if isinstance(cached, dict):
                results.append({**cached, "url": url, "cached": True})
            else:
                to_fetch.append(url)

        if to_fetch:
            from app.core.site_analyzer.profile_extractor import (
                preview_competitor_names,
            )
            from app.core.telemetry import UsageCollector
            from app.generators.ai_service import get_ai_service
            from app.config import settings

            ai = get_ai_service(api_key=settings.GEMINI_API_KEY)
            ai.collector = UsageCollector(
                brand_profile_id=workspace_id, task_id=task_id, flush_every=1
            )
            fetched = preview_competitor_names(ai, to_fetch)
            for entry in fetched:
                results.append(entry)
                key = normalize_competitor_url(entry.get("url", ""))
                if redis_client is not None and key and not entry.get("error"):
                    try:
                        # Stampede kontrolü: yalnız yoksa yaz (nx)
                        redis_client.set(
                            _cache_key(workspace_id, key),
                            json.dumps({
                                "detected_name": entry.get("detected_name", ""),
                                "source": entry.get("source", ""),
                            }),
                            ex=PREVIEW_CACHE_TTL_SECONDS,
                            nx=True,
                        )
                    except Exception:
                        pass

        update_task_status(
            task_id,
            status="completed",
            progress=100,
            result_data={"competitors": results},
        )
        return {"status": "completed", "competitors": results}
    except Exception as exc:
        update_task_status(task_id, status="failed", error_message=str(exc))
        return {"status": "failed", "error": str(exc)}
    finally:
        if ai is not None:
            closer = getattr(ai, "close", None)
            if callable(closer):
                try:
                    closer()
                except Exception:
                    pass
