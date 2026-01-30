"""
Celery tasks for content generation.
"""
from typing import List
from app.tasks.celery_app import celery_app
from app.database.connection import SessionLocal
from app.generators.ai_service import get_ai_service


@celery_app.task(bind=True, name="generation.generate_ads_content")
def generate_ads_content_task(self, keyword_ids: List[int]):
    """İçerik üretimi için arkaplan görevi."""
    from app.generators.ads.ads_generator import AdsGenerator
    from app.database.models import Keyword
    
    db = SessionLocal()
    ai = get_ai_service()
    
    try:
        generator = AdsGenerator(ai)
        keywords = db.query(Keyword).filter(Keyword.id.in_(keyword_ids)).all()
        
        results = []
        for kw in keywords:
            content = generator.generate_responsive_search_ad(
                keyword=kw.keyword,
                sector=kw.sector
            )
            results.append({'keyword_id': kw.id, 'content': content})
        
        return {'status': 'completed', 'results': results}
    
    except Exception as e:
        return {'status': 'failed', 'error': str(e)}
    
    finally:
        db.close()


@celery_app.task(bind=True, name="generation.generate_seo_content")
def generate_seo_content_task(self, keyword_ids: List[int], word_count: int = 1500):
    """SEO içerik üretimi için arkaplan görevi."""
    from app.generators.seo_geo.seo_geo_generator import SEOGeoGenerator
    from app.database.models import Keyword
    
    db = SessionLocal()
    ai = get_ai_service()
    
    try:
        generator = SEOGeoGenerator(ai)
        keywords = db.query(Keyword).filter(Keyword.id.in_(keyword_ids)).all()
        
        results = []
        for kw in keywords:
            content = generator.generate_blog_post(
                keyword=kw.keyword,
                target_word_count=word_count,
                sector=kw.sector
            )
            results.append({'keyword_id': kw.id, 'content': content})
        
        return {'status': 'completed', 'results': results}
    
    except Exception as e:
        return {'status': 'failed', 'error': str(e)}
    
    finally:
        db.close()
