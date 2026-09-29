"""
Celery tasks for scoring operations.
"""


def _run_relevance_computation(scoring_run_id: int):
    """
    Compatibility entry point for FastAPI background relevance computation.

    The implementation lives in brand_profile.py because profile confirmation
    can also trigger it directly. Lazy import avoids coupling this module to
    the API router graph at Celery worker startup.
    """
    from app.api.v1.brand_profile import _run_relevance_computation as run_relevance

    return run_relevance(scoring_run_id)
