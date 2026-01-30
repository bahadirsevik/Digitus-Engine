"""
Celery tasks for scoring operations.
"""
from celery import shared_task
from sqlalchemy.orm import Session

from app.tasks.celery_app import celery_app
from app.database.connection import SessionLocal
from app.core.scoring.score_engine import ScoreEngine


@celery_app.task(bind=True, name="scoring.run_scoring")
def run_scoring_task(self, scoring_run_id: int):
    """
    Arkaplan görevi olarak skorlama yapar.
    
    Args:
        scoring_run_id: Skorlama çalıştırma ID'si
    
    Returns:
        Sonuç özeti
    """
    self.update_state(state='STARTED', meta={'step': 'initializing'})
    
    db = SessionLocal()
    
    try:
        self.update_state(state='PROGRESS', meta={'step': 'scoring'})
        
        engine = ScoreEngine(db)
        result = engine.run_scoring(scoring_run_id)
        
        return {
            'status': 'completed',
            'scoring_run_id': scoring_run_id,
            'result': result
        }
    
    except Exception as e:
        return {
            'status': 'failed',
            'scoring_run_id': scoring_run_id,
            'error': str(e)
        }
    
    finally:
        db.close()


@celery_app.task(bind=True, name="scoring.create_and_run")
def create_and_run_scoring(
    self,
    ads_capacity: int,
    seo_capacity: int,
    social_capacity: int,
    run_name: str = None
):
    """
    Skorlama çalıştırması oluşturur ve çalıştırır.
    """
    db = SessionLocal()
    
    try:
        engine = ScoreEngine(db)
        
        # Create run
        self.update_state(state='PROGRESS', meta={'step': 'creating_run'})
        scoring_run = engine.create_scoring_run(
            ads_capacity=ads_capacity,
            seo_capacity=seo_capacity,
            social_capacity=social_capacity,
            run_name=run_name
        )
        
        # Execute scoring
        self.update_state(state='PROGRESS', meta={'step': 'scoring', 'run_id': scoring_run.id})
        result = engine.run_scoring(scoring_run.id)
        
        return {
            'status': 'completed',
            'scoring_run_id': scoring_run.id,
            'result': result
        }
    
    except Exception as e:
        return {'status': 'failed', 'error': str(e)}
    
    finally:
        db.close()
