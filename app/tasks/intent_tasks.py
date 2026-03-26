"""
Celery tasks for intent analysis.
"""
from app.tasks.celery_app import celery_app
from app.database.connection import SessionLocal
from app.core.channel.channel_engine import ChannelEngine
from app.generators.ai_service import get_ai_service
from app.tasks.task_status import update_task_status


@celery_app.task(bind=True, name="intent.run_channel_assignment", time_limit=1800)
def run_channel_assignment_task(
    self,
    scoring_run_id: int,
    relevance_coefficient: float = 1.0
):
    """
    Arkaplan görevi olarak kanal ataması yapar.
    Progress tracking ile frontend'den takip edilebilir.
    """
    db = SessionLocal()
    ai = get_ai_service()
    task_id = self.request.id

    update_task_status(task_id, status='running', progress=0)

    try:
        self.update_state(state='PROGRESS', meta={'step': 'channel_assignment'})

        engine = ChannelEngine(db, ai)
        result = engine.run_channel_assignment(
            scoring_run_id,
            relevance_coefficient=relevance_coefficient
        )

        update_task_status(
            task_id,
            status='completed',
            progress=100,
            result_data={
                'scoring_run_id': scoring_run_id,
                'effective_relevance_coefficient': relevance_coefficient,
                'steps': result.get('steps', {})
            }
        )

        return {
            'status': 'completed',
            'scoring_run_id': scoring_run_id,
            'result': result
        }

    except Exception as e:
        update_task_status(task_id, status='failed', error_message=str(e))
        return {'status': 'failed', 'error': str(e)}

    finally:
        db.close()
