"""
Celery application configuration.
"""
from celery import Celery
import os

# Redis URL from environment
redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Create Celery app
celery_app = Celery(
    "digitus_engine",
    broker=redis_url,
    backend=redis_url,
    include=[
        "app.tasks.scoring_tasks",
        "app.tasks.intent_tasks",
        "app.tasks.generation_tasks",
        "app.tasks.export_tasks",
        "app.tasks.policy_preview_tasks",
        "app.tasks.competitor_discovery_tasks",
        "app.tasks.screening_tasks",
    ]
)

# Celery configuration
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=3600,  # 1 hour max
    worker_prefetch_multiplier=1,
    result_expires=86400,  # 24 hours
    # Corpus screening AYRI kuyrukta (plan §7.5): uzun tarama isleri
    # export/generation kuyrugunu ac birakmaz. Worker:
    #   celery -A app.tasks.celery_app worker -Q corpus_screening
    #          --concurrency=1 --prefetch-multiplier=1
    task_routes={
        "corpus_screening.run": {"queue": "corpus_screening"},
    },
)

# Optional: Beat schedule for periodic tasks
celery_app.conf.beat_schedule = {
    "reconcile-deferred-screening-parents": {
        "task": "corpus_screening.reconcile_deferred_parents",
        "schedule": 60.0,
    },
}
