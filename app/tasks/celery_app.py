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
        "app.tasks.generation_tasks"
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
)

# Optional: Beat schedule for periodic tasks
celery_app.conf.beat_schedule = {
    # Example: Run cleanup every day at midnight
    # 'cleanup-old-runs': {
    #     'task': 'app.tasks.cleanup_old_runs',
    #     'schedule': crontab(hour=0, minute=0),
    # },
}
