"""
Celery Tasks for Content Generation.

Chunked bulk operations ile time limit sorunlarını önler.
DB sync ile progress tracking sağlar.
"""
from typing import List, Optional
from celery import group, chord
from celery.exceptions import SoftTimeLimitExceeded

from app.tasks.celery_app import celery_app
from app.database.connection import SessionLocal
from app.tasks.task_status import update_task_status, create_task_record
from app.core.logging.config import get_task_logger

logger = get_task_logger()

# Chunk boyutları
SEO_CHUNK_SIZE = 5
SEO_CHUNK_SOFT_TIME_LIMIT = 900
SEO_CHUNK_HARD_TIME_LIMIT = 960
ADS_CHUNK_SIZE = 20
SOCIAL_CHUNK_SIZE = 5


# ==================== SEO+GEO TASKS ====================

@celery_app.task(
    bind=True,
    name="generation.seo_chunk",
    soft_time_limit=SEO_CHUNK_SOFT_TIME_LIMIT,
    time_limit=SEO_CHUNK_HARD_TIME_LIMIT,
    max_retries=2
)
def generate_seo_chunk_task(self, scoring_run_id: int, keyword_ids: List[int]):
    """
    Tek SEO chunk'ını işler.
    Max 10 kelime, max 10 dakika.
    """
    from app.generators.seo_geo.seo_geo_generator import SEOGeoGenerator
    from app.schemas.seo_geo import SEOGEOGenerateRequest
    from app.database.models import Keyword
    
    db = SessionLocal()
    task_id = self.request.id
    
    logger.info(f"SEO chunk started: {len(keyword_ids)} keywords")
    
    try:
        from app.generators.ai_service import get_ai_service
        ai = get_ai_service()
        generator = SEOGeoGenerator(db, ai)
        
        results = []
        for i, kw_id in enumerate(keyword_ids):
            kw = db.query(Keyword).filter(Keyword.id == kw_id).first()
            if not kw:
                continue
            
            try:
                request = SEOGEOGenerateRequest(
                    keyword_id=kw.id,
                    sector=getattr(kw, 'sector', None),
                    tone="informative"
                )
                content = generator.generate_content(request, scoring_run_id=scoring_run_id)
                
                if content:
                    results.append(content.get('id'))
                    logger.info(f"SEO content generated for: {kw.keyword}")
            except Exception as e:
                logger.error(f"SEO content failed for {kw.keyword}: {e}")
                continue
        
        return {'chunk_id': task_id, 'content_ids': results, 'count': len(results)}
        
    except SoftTimeLimitExceeded as e:
        logger.error(f"SEO chunk soft time limit exceeded: {e}")
        raise
    except Exception as e:
        logger.error(f"SEO chunk failed: {e}")
        raise
        
    finally:
        db.close()


@celery_app.task(name="generation.seo_finalize")
def finalize_seo_bulk(results: List[dict], parent_task_id: str, scoring_run_id: int):
    """Tüm SEO chunk'ları bitince çağrılır."""
    total_ids = []
    for r in results:
        if r and 'content_ids' in r:
            total_ids.extend(r['content_ids'])
    
    update_task_status(
        parent_task_id,
        status='completed',
        progress=100,
        result_data={'content_ids': total_ids, 'total': len(total_ids)}
    )
    
    logger.info(f"SEO bulk completed: {len(total_ids)} contents")
    return {'status': 'completed', 'total': len(total_ids)}


@celery_app.task(name="generation.seo_finalize_error")
def finalize_seo_bulk_error(parent_task_id: str, scoring_run_id: int):
    """
    Marks the parent seo_content task as failed when any chunk fails.
    """
    error_message = (
        "SEO bulk generation failed: at least one chunk timed out or crashed."
    )
    update_task_status(
        parent_task_id,
        status='failed',
        progress=100,
        result_data={'total': 0, 'error': error_message, 'scoring_run_id': scoring_run_id},
        error_message=error_message
    )
    logger.error(
        f"SEO bulk failed for run {scoring_run_id}, parent task {parent_task_id}"
    )
    return {'status': 'failed', 'error': error_message}


def start_bulk_seo_generation(scoring_run_id: int, keyword_ids: List[int]) -> str:
    """
    Bulk SEO üretimini başlatır.
    Chunk'lara böler ve paralel çalıştırır.
    
    Returns:
        Parent task ID
    """
    import uuid
    parent_task_id = str(uuid.uuid4())
    
    # Task kaydı oluştur
    create_task_record(parent_task_id, 'seo_content', scoring_run_id, {
        'total_keywords': len(keyword_ids)
    })
    update_task_status(parent_task_id, status='running', progress=0)
    
    # Chunk'lara böl
    chunks = [keyword_ids[i:i+SEO_CHUNK_SIZE] 
              for i in range(0, len(keyword_ids), SEO_CHUNK_SIZE)]
    
    logger.info(f"Starting SEO bulk: {len(keyword_ids)} keywords in {len(chunks)} chunks")
    
    # Paralel task'lar
    tasks = [
        generate_seo_chunk_task.s(scoring_run_id, chunk_ids)
        for chunk_ids in chunks
    ]
    
    # Chord: paralel çalış, sonra finalize
    finalize_sig = finalize_seo_bulk.s(parent_task_id, scoring_run_id).on_error(
        finalize_seo_bulk_error.si(parent_task_id, scoring_run_id)
    )
    chord(tasks)(finalize_sig)
    
    return parent_task_id


# ==================== GOOGLE ADS TASKS ====================

@celery_app.task(bind=True, name="generation.ads_generate", time_limit=900, max_retries=2)
def generate_ads_task(
    self,
    scoring_run_id: int,
    brand_name: str,
    website_url: Optional[str] = None,
    unique_selling_points: Optional[List[str]] = None,
    tone: str = "professional"
):
    """
    Google Ads RSA üretimi.
    ADS havuzundaki kelimeler için reklam grupları oluşturur.
    """
    from app.generators.ads.ads_generator import AdsGenerator
    from app.database.models import ChannelPool, Keyword
    
    db = SessionLocal()
    task_id = self.request.id
    
    create_task_record(task_id, 'ads', scoring_run_id)
    update_task_status(task_id, status='running', progress=0)
    
    logger.info(f"Ads generation started for run: {scoring_run_id}")
    
    try:
        from app.generators.ai_service import get_ai_service
        ai = get_ai_service()
        generator = AdsGenerator(db, ai)
        
        # ADS havuzundaki kelimeleri al
        pools = db.query(ChannelPool).filter(
            ChannelPool.scoring_run_id == scoring_run_id,
            ChannelPool.channel == 'ADS'
        ).all()
        
        keyword_ids = [p.keyword_id for p in pools]
        keywords = db.query(Keyword).filter(Keyword.id.in_(keyword_ids)).all()
        
        update_task_status(task_id, progress=20)
        
        # Reklam grupları oluştur
        result = generator.generate_full_pipeline(
            scoring_run_id=scoring_run_id,
            keywords=[kw.keyword for kw in keywords],
            brand_name=brand_name,
            website_url=website_url,
            unique_selling_points=unique_selling_points or [],
            tone=tone
        )
        
        update_task_status(
            task_id,
            status='completed',
            progress=100,
            result_data={
                'ad_group_count': result.get('ad_group_count', 0),
                'headline_count': result.get('headline_count', 0)
            }
        )
        
        logger.info(f"Ads generation completed: {result.get('ad_group_count', 0)} groups")
        return {'status': 'completed', 'result': result}
        
    except Exception as e:
        update_task_status(task_id, status='failed', error_message=str(e))
        logger.error(f"Ads generation failed: {e}")
        raise
        
    finally:
        db.close()


# ==================== SOCIAL MEDIA TASKS ====================

@celery_app.task(bind=True, name="generation.social_generate", time_limit=1200, max_retries=2)
def generate_social_task(
    self,
    scoring_run_id: int,
    brand_name: str,
    brand_context: Optional[str] = None,
    platforms: Optional[List[str]] = None,
    max_categories: int = 6,
    ideas_per_category: int = 5,
    max_contents: int = 10
):
    """
    Sosyal medya 3-aşamalı pipeline.
    Kategori → Fikir → İçerik
    """
    from app.generators.social.social_generator import SocialGenerator
    from app.schemas.social import SocialBulkRequest
    
    db = SessionLocal()
    task_id = self.request.id
    
    create_task_record(task_id, 'social', scoring_run_id)
    update_task_status(task_id, status='running', progress=0)
    
    logger.info(f"Social generation started for run: {scoring_run_id}")
    
    try:
        from app.generators.ai_service import get_ai_service
        ai = get_ai_service()
        generator = SocialGenerator(db, ai)
        
        update_task_status(task_id, progress=10)
        
        # Bulk request oluştur
        request = SocialBulkRequest(
            scoring_run_id=scoring_run_id,
            brand_name=brand_name,
            brand_context=brand_context,
            max_categories=max_categories,
            ideas_per_category=ideas_per_category,
            max_contents=max_contents
        )
        
        # Full pipeline çalıştır
        result = generator.generate_full_pipeline(request)
        
        update_task_status(
            task_id,
            status='completed',
            progress=100,
            result_data={
                'category_count': result.total_categories,
                'idea_count': result.total_ideas,
                'content_count': result.total_contents
            }
        )
        
        logger.info(f"Social generation completed: {result.total_contents} contents")
        return {'status': 'completed', 'total_contents': result.total_contents}
        
    except Exception as e:
        update_task_status(task_id, status='failed', error_message=str(e))
        logger.error(f"Social generation failed: {e}")
        raise
        
    finally:
        db.close()


# ==================== EXPORT TASKS ====================

@celery_app.task(bind=True, name="generation.export", time_limit=600)
def export_task(
    self,
    export_id: str,
    scoring_run_id: int,
    format: str,
    sections: List[str],
    filepath: str
):
    """
    Export dosyası oluşturma.
    Background'da çalışır, dosyayı diske yazar.
    """
    import os
    from app.exporters import DocxExporter, PdfExporter, ExcelExporter, CsvExporter
    from app.schemas.export import ExportFormatEnum, ExportSectionEnum
    
    db = SessionLocal()
    
    update_task_status(export_id, status='running', progress=0)
    
    logger.info(f"Export started: {format} for run {scoring_run_id}")
    
    try:
        # Format'a göre exporter seç
        exporters = {
            'docx': DocxExporter,
            'pdf': PdfExporter,
            'excel': ExcelExporter,
            'csv': CsvExporter,
        }
        
        exporter_class = exporters.get(format.lower())
        if not exporter_class:
            raise ValueError(f"Unknown format: {format}")
        
        exporter = exporter_class(db)
        
        update_task_status(export_id, progress=30)
        
        # Section enum'larına çevir
        section_enums = [
            ExportSectionEnum(s) if s != 'all' else ExportSectionEnum.ALL 
            for s in sections
        ]
        
        # Export yap
        exporter.export(scoring_run_id, section_enums, filepath)
        
        update_task_status(
            export_id,
            status='completed',
            progress=100,
            result_data={'filepath': filepath, 'format': format}
        )
        
        logger.info(f"Export completed: {filepath}")
        return {'status': 'completed', 'filepath': filepath}
        
    except Exception as e:
        update_task_status(export_id, status='failed', error_message=str(e))
        logger.error(f"Export failed: {e}")
        raise
        
    finally:
        db.close()
