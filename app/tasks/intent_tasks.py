"""
Celery tasks for intent analysis.
"""
from app.tasks.celery_app import celery_app
from app.database.connection import SessionLocal
from app.core.channel.channel_engine import ChannelEngine
from app.generators.ai_service import get_ai_service


@celery_app.task(bind=True, name="intent.run_channel_assignment")
def run_channel_assignment_task(self, scoring_run_id: int):
    """
    Arkaplan görevi olarak kanal ataması yapar.
    """
    db = SessionLocal()
    ai = get_ai_service()
    
    try:
        self.update_state(state='PROGRESS', meta={'step': 'channel_assignment'})
        
        engine = ChannelEngine(db, ai)
        result = engine.run_channel_assignment(scoring_run_id)
        
        return {
            'status': 'completed',
            'scoring_run_id': scoring_run_id,
            'result': result
        }
    
    except Exception as e:
        return {'status': 'failed', 'error': str(e)}
    
    finally:
        db.close()
