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
from app.core.logging_config import get_task_logger

logger = get_task_logger()

# Chunk boyutları
SEO_CHUNK_SIZE = 5
SEO_CHUNK_SOFT_TIME_LIMIT = 900
SEO_CHUNK_HARD_TIME_LIMIT = 960
ADS_CHUNK_SIZE = 20
SOCIAL_CHUNK_SIZE = 5


def _close_ai(ai) -> None:
    """Plan G: task-scope shutdown — client'ı ROOT servis kapatır (fail-open).

    Task bitti, in-flight çağrı kalmadı; kapatılmayan httpx havuzları process
    ömrü boyunca birikmesin. Fake/mock servislerde no-op.
    """
    closer = getattr(ai, "close", None)
    if callable(closer):
        try:
            closer()
        except Exception:
            pass


def _legacy_refusal(db, scoring_run_id) -> Optional[dict]:
    """Eski motor (v2/v2_1) run'ı salt-okunur — task sınırı kapısı.

    Run eskiyse tipli red özeti döner (AI servisi HİÇ kurulmadan), değilse
    None. Kayıt yazımı çağıran task'ın mevcut fail-closed kalıbındadır.
    """
    from app.core.engine_version_gate import (
        LEGACY_RUN_READ_ONLY,
        legacy_run_by_id,
        legacy_run_message,
        run_algorithm_version,
    )

    run = legacy_run_by_id(db, scoring_run_id)
    if run is None:
        return None
    version = run_algorithm_version(run)
    logger.warning(
        f"Generation task reddedildi ({LEGACY_RUN_READ_ONLY}): run "
        f"{scoring_run_id} algorithm_version={version}"
    )
    return {
        "status": "failed",
        "reason": LEGACY_RUN_READ_ONLY,
        "algorithm_version": version,
        "scoring_run_id": scoring_run_id,
        "message": legacy_run_message(version),
    }


def _legacy_refusal_isolated(scoring_run_id) -> Optional[dict]:
    """`_legacy_refusal`'ın kendi kısa okuma oturumuyla çalışan sürümü.

    Brief task'larında scope oturumu kapatıldıktan SONRA çağrılır (scope
    okuma sözleşmesi: tek oturum, orkestrasyondan önce kapanır) — kapı
    kontrolü o oturumun yaşam döngüsüne karışmaz.
    """
    from app.database import connection as _connection

    db = _connection.SessionLocal()
    try:
        return _legacy_refusal(db, scoring_run_id)
    finally:
        db.close()


def _refuse_legacy_social_attempt(finalize_failure, *, attempt_id: int,
                                  task_id: str, refusal: dict) -> dict:
    """Brief attempt'ini tipli `legacy_run_read_only` sebebiyle kapatır.

    Canonical kilit sırası finalize fonksiyonundadır; kalıcılaştırma hatası
    red kararını değiştirmez (AI yine çağrılmaz).
    """
    from app.core.engine_version_gate import LEGACY_RUN_READ_ONLY_REASON

    db = None
    try:
        db = SessionLocal()
        finalize_failure(
            db,
            attempt_id=attempt_id,
            task_id=task_id,
            reason_code=LEGACY_RUN_READ_ONLY_REASON,
            error_message=refusal["message"],
        )
        db.commit()
    except Exception:
        if db is not None:
            try:
                db.rollback()
            except Exception:
                pass
        logger.warning("Legacy run refusal could not be persisted on attempt.")
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                pass
    return {"attempt_id": attempt_id, **refusal}


# ==================== SEO+GEO TASKS ====================

@celery_app.task(
    bind=True,
    name="generation.seo_chunk",
    soft_time_limit=SEO_CHUNK_SOFT_TIME_LIMIT,
    time_limit=SEO_CHUNK_HARD_TIME_LIMIT,
    max_retries=2
)
def generate_seo_chunk_task(self, scoring_run_id: int, keyword_ids: List[int], tone: str = "informative"):
    """
    Tek SEO chunk'ını işler.
    Max 10 kelime, max 10 dakika.
    """
    from app.generators.seo_geo.seo_geo_generator import SEOGeoGenerator
    from app.schemas.seo_geo import SEOGEOGenerateRequest
    from app.database.models import Keyword
    
    db = SessionLocal()
    task_id = self.request.id

    # Eski motor run'ı salt-okunur: collector/AI kurulmadan chunk reddedilir;
    # finalize_seo_bulk parent'ı tipli sebeple failed yapar
    refusal = _legacy_refusal(db, scoring_run_id)
    if refusal is not None:
        db.close()
        return {'chunk_id': task_id, 'content_ids': [], 'count': 0, **refusal}

    # Collector try DIŞINDA: exception/soft-timeout yolunda da finally
    # flush'ı event'leri kalıcılaştırır (Codex v8-2)
    from app.core.telemetry import UsageCollector
    collector = UsageCollector(scoring_run_id=scoring_run_id, task_id=task_id)
    ai = None  # finally'de kapatılır (plan G)

    logger.info(f"SEO chunk started: {len(keyword_ids)} keywords")

    try:
        from app.generators.ai_service import get_ai_service
        ai = get_ai_service()
        ai.collector = collector
        generator = SEOGeoGenerator(db, ai)

        results = []
        for i, kw_id in enumerate(keyword_ids):
            kw = db.query(Keyword).filter(Keyword.id == kw_id).first()
            if not kw:
                continue
            
            try:
                # P1.1: `sector` BILEREK gecirilmiyor. Legacy `kw.sector`
                # burada request.sector olarak gonderilirse generator onu
                # ACIK KULLANICI OVERRIDE'i sanar ve confirmed profilin
                # sektorunun ONUNE gecer. Generator zaten fallback zincirinin
                # ucuncu adiminda `keyword.sector`'u okuyor.
                request = SEOGEOGenerateRequest(
                    keyword_id=kw.id,
                    tone=tone,
                )
                content = generator.generate_content(request, scoring_run_id=scoring_run_id)
                
                if content:
                    results.append(content.get('id'))
                    logger.info(f"SEO content generated for: {kw.keyword}")
            except Exception as e:
                logger.error(f"SEO content failed for {kw.keyword}: {e}")
                continue
        
        usage = collector.finalize()
        return {'chunk_id': task_id, 'content_ids': results, 'count': len(results), 'usage': usage}

    except SoftTimeLimitExceeded as e:
        logger.error(f"SEO chunk soft time limit exceeded: {e}")
        raise
    except Exception as e:
        logger.error(f"SEO chunk failed: {e}")
        raise

    finally:
        # Exception/soft-timeout yolunda da event'ler kalıcı olur
        # (başarı yolunda finalize zaten flush etti — no-op)
        collector.flush()
        _close_ai(ai)
        db.close()

@celery_app.task(name="generation.seo_finalize")
def finalize_seo_bulk(results: List[dict], parent_task_id: str, scoring_run_id: int):
    """Tüm SEO chunk'ları bitince çağrılır."""
    from app.core.engine_version_gate import LEGACY_RUN_READ_ONLY

    legacy = next((r for r in results or []
                   if r and r.get('reason') == LEGACY_RUN_READ_ONLY), None)
    if legacy is not None:
        update_task_status(
            parent_task_id,
            status='failed',
            progress=100,
            result_data={'content_ids': [], 'total': 0,
                         'scoring_run_id': scoring_run_id,
                         'reason': LEGACY_RUN_READ_ONLY,
                         'algorithm_version': legacy.get('algorithm_version')},
            error_message=legacy.get('message'),
        )
        return {'status': 'failed', 'reason': LEGACY_RUN_READ_ONLY}

    total_ids = []
    for r in results:
        if r and 'content_ids' in r:
            total_ids.extend(r['content_ids'])

    # Havuz bos olamaz (endpoint 404 doner) — 0 icerik, tum kelimelerin
    # uretimde elendigi anlamina gelir. "Tamamlandi" diye yutma, failed isaretle
    # ki kullanici Gorevler'de ve kanal sayfasinda gercegi gorsun.
    if not total_ids:
        error_message = (
            "SEO icerik uretimi hic icerik uretemedi: tum kelimeler AI yaniti "
            "islenemedigi icin atlandi. Yeniden deneyin; sorun surerse "
            "worker loglarina bakin."
        )
        update_task_status(
            parent_task_id,
            status='failed',
            progress=100,
            result_data={'content_ids': [], 'total': 0, 'scoring_run_id': scoring_run_id},
            error_message=error_message
        )
        logger.error(f"SEO bulk produced zero contents for run {scoring_run_id}")
        return {'status': 'failed', 'total': 0}

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


def start_bulk_seo_generation(scoring_run_id: int, keyword_ids: List[int], tone: str = "informative") -> str:
    """
    Bulk SEO üretimini başlatır.
    Chunk'lara böler ve paralel çalıştırır.
    
    Returns:
        Parent task ID
    """
    import uuid
    from app.core.engine_version_gate import LegacyRunReadOnlyError

    # Dispatcher sınırı: eski motor run'ı için TaskResult/chord OLUŞMAZ
    _db = SessionLocal()
    try:
        refusal = _legacy_refusal(_db, scoring_run_id)
    finally:
        _db.close()
    if refusal is not None:
        raise LegacyRunReadOnlyError(refusal["algorithm_version"])

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
        generate_seo_chunk_task.s(scoring_run_id, chunk_ids, tone)
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
    brand_name: Optional[str] = None,
    brand_usp: Optional[str] = None,
    website_url: Optional[str] = None,
    max_groups: Optional[int] = None,
    enable_ai_regeneration: bool = True,
    generation_set_id: Optional[int] = None,
    operation: str = "full",
    source_set_id: Optional[int] = None,
    source_group_id: Optional[int] = None,
    trusted_brand_usp: Optional[str] = None,
):
    """
    Google Ads RSA üretimi (async Celery task — versiyonlanmış, Faz E).

    Set kimliği kwargs'tan gelir (task ID'den TAHMİN EDİLMEZ). İki operasyon:
    - 'full': ADS havuzundan komple set üretimi
    - 'group_regenerate': kaynak set klonlanır, yalnız hedef grup yeniden
      üretilir → yeni draft set (geçmiş set DEĞİŞMEZ)

    Atomiklik (E2b): AdGroup'lar + set final durumu + TaskResult final durumu
    TEK transaction'da (finalize_ads_success/failure). Ara progress'ler ayrı
    session'la (update_task_status) yazılabilir. Diriltme koruması: pahalı
    AI'a başlamadan VE sonuç yazmadan önce set 'generating' doğrulanır.
    """
    from app.generators.ads.ads_generator import AdsGenerator
    from app.generators.ads.generation_sets import (
        SET_GENERATING,
        finalize_ads_failure,
        finalize_ads_success,
    )
    from app.generators.ai_service import get_ai_service
    from app.database.models import AdGenerationSet, AdGroup
    from app.schemas.ads import AdsGenerateRequest

    db = SessionLocal()
    task_id = self.request.id
    collector = None  # exception yolunda da finalize edilir (Codex v8-2)
    ai = None  # finally'de kapatılır (plan G)
    set_warnings: list = []  # AdGenerationSet.warnings (atılan negatifler, plan 2.1)

    # İdempotent: kayıt endpoint dispatch kilidi altında zaten yaratıldı
    create_task_record(task_id, "ads", scoring_run_id)
    update_task_status(task_id, status="running", progress=0)

    logger.info(
        f"Ads generation started: run={scoring_run_id} set={generation_set_id} "
        f"operation={operation}"
    )

    try:
        gen_set = None
        if generation_set_id is not None:
            gen_set = (
                db.query(AdGenerationSet)
                .filter(AdGenerationSet.id == generation_set_id)
                .first()
            )
        if gen_set is None:
            # Set'siz çağrı desteklenmez (dispatch her zaman set yaratır)
            update_task_status(
                task_id, status="failed",
                error_message="generation_set_id eksik — dispatch akışı kullanılmalı",
            )
            return {"status": "failed", "reason": "missing_generation_set"}

        # Diriltme koruması 1: pahalı AI çağrılarına BAŞLAMADAN önce
        if gen_set.status != SET_GENERATING:
            logger.warning(
                f"Ads task iptal: set {gen_set.id} durumu '{gen_set.status}' "
                f"(generating değil) — AI çağrısı yapılmadı"
            )
            return {"status": "aborted", "set_status": gen_set.status}

        # Eski motor run'ı salt-okunur: set + task TEK transaction'da
        # failed, AI servisi kurulmaz
        refusal = _legacy_refusal(db, gen_set.scoring_run_id)
        if refusal is not None:
            finalize_ads_failure(
                db, gen_set,
                error_message=refusal["message"],
                extra_result_data={"reason": refusal["reason"],
                                   "algorithm_version":
                                       refusal["algorithm_version"]},
            )
            return refusal

        # Staleness yarışı (codex bulgusu): dispatch ile worker başlangıcı
        # arasında kanal ataması yenilendiyse set bayattır — eski keyword
        # havuzundan üretim yapmak anlamsız; AI maliyeti kesilir, set+task
        # failed (tek transaction, run 409 kilidinde kalmaz)
        if gen_set.is_stale:
            finalize_ads_failure(
                db, gen_set,
                error_message=(
                    "Kanal ataması üretim başlamadan yenilendi — set bayatladı. "
                    "Yeni ADS üretimi çalıştırın."
                ),
            )
            logger.warning(
                f"Ads task iptal: set {gen_set.id} bayat (kanal ataması "
                f"yenilendi) — AI çağrısı yapılmadı, set failed"
            )
            return {"status": "failed", "reason": "stale_before_generation"}

        ai = get_ai_service()
        from app.core.telemetry import UsageCollector
        collector = UsageCollector(
            scoring_run_id=gen_set.scoring_run_id, task_id=task_id
        )
        ai.collector = collector
        generator = AdsGenerator(db, ai)

        if operation == "group_regenerate":
            source_group = (
                db.query(AdGroup).filter(AdGroup.id == source_group_id).first()
            )
            if source_group is None or source_set_id is None:
                finalize_ads_failure(
                    db, gen_set,
                    error_message="Regenerate kaynağı bulunamadı",
                )
                return {"status": "failed", "reason": "missing_source"}

            update_task_status(task_id, progress=10)
            # Kaynak setin diğer grupları klonlanır (geçmiş set değişmez)
            cloned = generator.clone_groups_to_set(
                source_set_id, gen_set.id, exclude_group_id=source_group_id
            )
            update_task_status(task_id, progress=40)
            regenerated = generator.regenerate_group_into_set(
                source_group, gen_set.id,
                brand_name=brand_name or "", brand_usp=brand_usp or "",
                trusted_brand_usp=trusted_brand_usp or "",
            )
            set_warnings = list(regenerated.dropped_negatives or [])
            groups_count = cloned + 1
            failed_groups = 0
            extra = {"operation": "group_regenerate",
                     "source_set_id": source_set_id,
                     "source_group_id": source_group_id,
                     "usage": collector.finalize()}
        else:
            request = AdsGenerateRequest(
                scoring_run_id=gen_set.scoring_run_id,  # run kimliği SETTEN
                brand_name=brand_name,
                brand_usp=brand_usp,
                website_url=website_url,
                max_groups=max_groups,
                enable_ai_regeneration=enable_ai_regeneration,
            )
            update_task_status(task_id, progress=10)
            # defer_commit: gruplar flush'lanır, commit finalize'da (atomik)
            result = generator.generate_ads(
                request, generation_set_id=gen_set.id, defer_commit=True,
                trusted_brand_usp=trusted_brand_usp or "",
            )
            groups_count = result.total_groups
            failed_groups = result.failed_groups
            set_warnings = [
                w for g in result.ad_groups for w in (g.dropped_negatives or [])
            ]
            extra = {
                "operation": "full",
                "headline_count": result.total_headlines,
                "total_keywords": result.total_keywords,
                "usage": collector.finalize(),
            }

            # 0 grup = başarısızlık; mevcut aktif/draft setlere DOKUNULMAZ
            if groups_count == 0:
                if result.total_keywords == 0:
                    error_message = (
                        "ADS havuzu bos - once kanal atamasi yapin, sonra reklam uretin."
                    )
                else:
                    error_message = (
                        "Hic reklam grubu uretilemedi: AI yanitlari islenemedi. "
                        "Yeniden deneyin; sorun surerse worker loglarina bakin."
                    )
                finalize_ads_failure(
                    db, gen_set, error_message=error_message,
                    extra_result_data={
                        **extra,
                        "ad_group_count": 0,
                        "failed_groups": failed_groups,
                    },
                )
                logger.error(
                    f"Ads generation produced zero groups for run "
                    f"{gen_set.scoring_run_id} (set={gen_set.id})"
                )
                return {"status": "failed", "ad_group_count": 0}

        # Diriltme koruması 2 + atomik final: finalize set'i kilitleyip
        # hâlâ 'generating' doğrular; gruplar + set + TaskResult TEK commit
        new_status = finalize_ads_success(
            db, gen_set,
            groups_count=groups_count,
            failed_groups=failed_groups,
            warnings=set_warnings,
            extra_result_data=extra,
        )
        if new_status == "aborted":
            return {"status": "aborted", "set_status": "stale_failed"}

        logger.info(
            f"Ads generation completed: {groups_count} groups "
            f"({failed_groups} failed) → set {gen_set.id} '{new_status}'"
        )
        return {
            "status": "completed",
            "ad_group_count": groups_count,
            "generation_set_id": gen_set.id,
            "set_status": new_status,
        }

    except Exception as e:
        logger.error(f"Ads generation failed: {e}")
        try:
            # Hata yolunda da harcanan token'lar TaskResult'a taşınır
            usage_extra = (
                {"usage": collector.finalize()} if collector is not None else None
            )
            if gen_set is not None:
                # Set finalize edilmişse (geç hata) DOKUNMAZ — completed set
                # failed'a düşürülmez (finalize_ads_failure içinde korunur)
                finalize_ads_failure(
                    db, gen_set, error_message=str(e),
                    extra_result_data=usage_extra,
                )
            else:
                update_task_status(task_id, status="failed", error_message=str(e))
        except Exception as inner:
            logger.error(f"Ads failure finalize hatası: {inner}")
            update_task_status(task_id, status="failed", error_message=str(e))
        raise

    finally:
        if collector is not None:
            collector.flush()
        _close_ai(ai)
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

    K12 (plan_social_brief_akisi.md §0/§8): ENABLE_SOCIAL_LEGACY_BULK
    kapaliyken (varsayilan) bu gorev calismaz — yeni akis brief task'lari
    uzerinden yurur (social_brief_ideas_task / social_brief_contents_task).
    """
    from app.config import settings
    from app.generators.social.social_generator import SocialGenerator
    from app.schemas.social import SocialBulkRequest

    db = SessionLocal()
    task_id = self.request.id
    collector = None  # her yolda finalize edilir (Codex v8-2)
    ai = None  # finally'de kapatılır (plan G)
    bulk_warnings = []
    bulk_policy = []

    create_task_record(task_id, 'social', scoring_run_id)

    if not settings.ENABLE_SOCIAL_LEGACY_BULK:
        update_task_status(
            task_id,
            status='failed',
            progress=100,
            error_message=(
                "Bu gorev kaldirildi. Yeni sosyal icerik akisi icin brief "
                "uclarini kullanin (/generation/social/briefs)."
            ),
        )
        db.close()
        return {'status': 'failed', 'reason': 'legacy_bulk_disabled'}

    refusal = _legacy_refusal(db, scoring_run_id)
    if refusal is not None:
        update_task_status(
            task_id, status='failed', progress=100,
            error_message=refusal["message"],
            result_data={'reason': refusal["reason"],
                         'algorithm_version': refusal["algorithm_version"]},
        )
        db.close()
        return refusal

    update_task_status(task_id, status='running', progress=0)

    logger.info(f"Social generation started for run: {scoring_run_id}")

    try:
        from app.generators.ai_service import get_ai_service
        from app.core.telemetry import UsageCollector
        ai = get_ai_service()
        if hasattr(ai, "collector"):
            collector = UsageCollector(scoring_run_id=scoring_run_id, task_id=task_id)
            ai.collector = collector
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

        bulk_warnings = [w.model_dump() for w in (result.warnings or [])]
        bulk_policy = [w.model_dump() for w in (result.policy_warnings or [])]
        result_data = {
            'category_count': result.total_categories,
            'idea_count': result.total_ideas,
            'content_count': result.total_contents,
            'warnings': bulk_warnings,
            'policy_warnings': bulk_policy,
            'usage': collector.finalize() if collector is not None else None,
        }

        # Faz F: seçilen fikir vardı ama TÜM içerikler claim nedeniyle
        # reddedildiyse task failed — warnings korunur
        if result.total_contents == 0 and (bulk_warnings or bulk_policy):
            update_task_status(
                task_id,
                status='failed',
                progress=100,
                result_data=result_data,
                error_message=(
                    "Tum icerikler desteklenmeyen iddia nedeniyle reddedildi. "
                    "Ilgili fikirleri yeniden uretin."
                ),
            )
            logger.error("Social bulk: all contents claim-rejected")
            return {'status': 'failed', 'total_contents': 0, 'warnings': bulk_warnings}

        update_task_status(
            task_id,
            status='completed',
            progress=100,
            result_data=result_data,
        )

        logger.info(
            f"Social generation completed: {result.total_contents} contents, "
            f"{len(bulk_warnings)} claim-rejected"
        )
        return {'status': 'completed', 'total_contents': result.total_contents}
        
    except Exception as e:
        usage = collector.finalize() if collector is not None else None
        update_task_status(
            task_id,
            status='failed',
            progress=100,
            result_data={
                'scoring_run_id': scoring_run_id,
                'warnings': bulk_warnings,
                'policy_warnings': bulk_policy,
                'usage': usage,
            },
            error_message=str(e),
        )
        logger.error(f"Social generation failed: {e}")
        raise

    finally:
        # Exception yolunda da event'ler kalıcı olur (başarıda no-op)
        if collector is not None:
            collector.flush()
        _close_ai(ai)
        db.close()


@celery_app.task(bind=True, name="generation.social_contents", time_limit=1200, max_retries=2)
def social_contents_task(
    self,
    scoring_run_id: int,
    idea_ids: List[int],
    brand_name: str,
    brand_tone: Optional[str] = None,
):
    """
    Secilmis fikirler icin ICERIK fazi (P7 Adim 5).

    Bulk generate_social_task'tan farkli: yalniz Faz 3 kosar; kategori/fikir
    uretmez. TaskResult kaydi ENDPOINT'te pending olarak olusturulur
    (dispatcher deseni) — burada yalniz running'e cekilir.
    """
    from app.generators.social.social_generator import SocialGenerator
    from app.schemas.social import SocialContentsRequest

    db = SessionLocal()
    task_id = self.request.id
    collector = None  # her yolda finalize edilir (Codex v8-2)
    ai = None  # finally'de kapatılır (plan G)
    warnings = []
    policy_warnings = []

    # Eski motor run'ı salt-okunur: task running'e çekilmeden, AI kurulmadan
    refusal = _legacy_refusal(db, scoring_run_id)
    if refusal is not None:
        update_task_status(
            task_id, status='failed', progress=100,
            error_message=refusal["message"],
            result_data={'reason': refusal["reason"],
                         'algorithm_version': refusal["algorithm_version"]},
        )
        db.close()
        return refusal

    update_task_status(task_id, status='running', progress=5)
    logger.info(
        f"Social contents generation started for run {scoring_run_id}: "
        f"{len(idea_ids)} ideas"
    )

    try:
        from app.generators.ai_service import get_ai_service
        from app.core.telemetry import UsageCollector
        ai = get_ai_service()
        if hasattr(ai, "collector"):
            collector = UsageCollector(scoring_run_id=scoring_run_id, task_id=task_id)
            ai.collector = collector
        generator = SocialGenerator(db, ai)

        request = SocialContentsRequest(
            idea_ids=idea_ids,
            brand_name=brand_name,
            brand_tone=brand_tone,
        )

        update_task_status(task_id, progress=15)
        result = generator.generate_contents(request, scoring_run_id=scoring_run_id)

        # Yalniz DB'ye gercekten kaydedilmis SocialContent id'leri (codex):
        # generate_contents her icerigi _save_content ile commit edip id'li doner
        content_ids = [c.id for c in (result.contents or []) if c.id]
        # Grounding uyarilari (Faz F): kaydedilmeyen icerikler UI'a tasinir
        warnings = [w.model_dump() for w in (result.warnings or [])]
        # Topic-policy uyarilari (plan B): tipli sozlesme
        policy_warnings = [w.model_dump() for w in (result.policy_warnings or [])]

        usage = collector.finalize() if collector is not None else None

        # 0 icerik = sessiz basarisizlik; failed isaretle (SEO/ADS deseni).
        # Tum icerikler claim nedeniyle reddedildiyse de failed — ama
        # warnings listesi result_data'da KORUNUR (Faz F).
        if not content_ids:
            if warnings or policy_warnings:
                error_message = (
                    "Tum icerikler desteklenmeyen iddia nedeniyle reddedildi. "
                    "Ilgili fikirleri yeniden uretin."
                )
            else:
                error_message = (
                    "Hic sosyal icerik uretilemedi: fikirler bulunamadi veya AI "
                    "yanitlari islenemedi. Yeniden deneyin."
                )
            update_task_status(
                task_id,
                status='failed',
                progress=100,
                result_data={
                    'content_ids': [], 'total': 0,
                    'scoring_run_id': scoring_run_id,
                    'warnings': warnings,
                    'policy_warnings': policy_warnings,
                    'usage': usage,
                },
                error_message=error_message,
            )
            logger.error(f"Social contents produced zero items for run {scoring_run_id}")
            return {'status': 'failed', 'total': 0, 'warnings': warnings}

        update_task_status(
            task_id,
            status='completed',
            progress=100,
            result_data={
                'content_ids': content_ids,
                'total': len(content_ids),
                'scoring_run_id': scoring_run_id,
                'warnings': warnings,
                'policy_warnings': policy_warnings,
                'usage': usage,
            },
        )
        logger.info(
            f"Social contents completed: {len(content_ids)} contents, "
            f"{len(warnings)} claim-rejected"
        )
        return {'status': 'completed', 'total': len(content_ids), 'warnings': warnings}

    except Exception as e:
        usage = collector.finalize() if collector is not None else None
        update_task_status(
            task_id,
            status='failed',
            progress=100,
            result_data={
                'content_ids': [],
                'total': 0,
                'scoring_run_id': scoring_run_id,
                'idea_ids': idea_ids,
                'warnings': warnings,
                'policy_warnings': policy_warnings,
                'usage': usage,
            },
            error_message=str(e),
        )
        logger.error(f"Social contents generation failed: {e}")
        raise

    finally:
        if collector is not None:
            collector.flush()
        _close_ai(ai)
        db.close()


# ==================== SOCIAL BRIEF IDEAS TASK (F1-F.6c) ====================

def _load_ideas_attempt_telemetry_scope(db, attempt_id: int) -> tuple[int, int]:
    """attempt_id üzerinden yalnızca scoring_run_id ve brand_profile_id okur.

    Salt-okunur ve kilitsizdir.
    Attempt, brief veya scoring_run bulunamazsa veya tutarsızsa ValueError fırlatır.
    """
    from app.database.models import ScoringRun, SocialBrief, SocialGenerationAttempt

    attempt = (
        db.query(SocialGenerationAttempt.id, SocialGenerationAttempt.brief_id, SocialGenerationAttempt.stage)
        .filter(SocialGenerationAttempt.id == attempt_id)
        .first()
    )
    if attempt is None:
        raise ValueError(f"SocialGenerationAttempt bulunamadı: {attempt_id}")
    if attempt.stage != "ideas":
        raise ValueError(f"Geçersiz attempt stage: {attempt.stage!r}, 'ideas' bekleniyordu.")

    brief = (
        db.query(SocialBrief.id, SocialBrief.scoring_run_id)
        .filter(SocialBrief.id == attempt.brief_id)
        .first()
    )
    if brief is None:
        raise ValueError(f"SocialBrief bulunamadı: {attempt.brief_id}")

    run = (
        db.query(ScoringRun.id, ScoringRun.brand_profile_id)
        .filter(ScoringRun.id == brief.scoring_run_id)
        .first()
    )
    if run is None:
        raise ValueError(f"ScoringRun bulunamadı: {brief.scoring_run_id}")

    return run.id, run.brand_profile_id


def _finalize_social_ideas_bootstrap_failure(
    *,
    attempt_id: int,
    task_id: str,
) -> None:
    """Worker bootstrap aşamasında (telemetry scope, usage collector, AI init)
    oluşan hatalarda pending attempt'i atomik ve güvenli biçimde failed yapar.

    Canonical lock sırası (ScoringRun -> SocialBrief -> SocialGenerationAttempt) korunur.
    Doğrudan SocialGenerationAttempt sorgusu veya FOR UPDATE çalıştırmaz.
    DB'ye yalnız sabit güvenli mesaj yazılır; ham exception sızdırılmaz.
    Finalizasyonun kendi hatası asıl bootstrap hatasını asla maskelemez;
    SessionLocal, rollback ve close hataları yutulur ve sabit log üretilir.
    """
    from app.generators.social.attempt_state import finalize_ideas_attempt_failure

    db = None
    try:
        db = SessionLocal()
        finalize_ideas_attempt_failure(
            db,
            attempt_id=attempt_id,
            task_id=task_id,
            reason_code="worker_bootstrap_failed",
            error_message="Fikir üretimi worker hazırlığı tamamlanamadı.",
        )
        db.commit()
    except Exception:
        if db is not None:
            try:
                db.rollback()
            except Exception:
                pass
        logger.warning(
            "Social ideas bootstrap failure finalization could not be persisted."
        )
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                logger.warning(
                    "Social ideas bootstrap failure session could not be closed cleanly."
                )


@celery_app.task(
    bind=True,
    name="generation.social_brief_ideas",
    soft_time_limit=1140,
    time_limit=1200,
    max_retries=0,
)
def social_brief_ideas_task(self, attempt_id: int) -> dict:
    """Social brief fikir üretim Celery task wrapper'ı (F1-F.6c / F1-F.6c.1).

    Kurallar:
    - Broker argümanı yalnızca attempt_id'dir.
    - task_id yalnızca self.request.id üzerinden alınır.
    - Celery seviyesinde otomatik retry yapılmaz (max_retries=0).
    - Scope okuma session'ı AI/orkestrasyon başlamadan önce açılıp kapatılır.
    - Worker bootstrap hatasında (scope, collector, ai service init/bağlama)
      pending attempt atomik olarak failed yapılır ve asıl hata fırlatılır.
    - run_social_idea_generation orkestratörünü çağırır.
    - Task kendi başına fikir satırı veya attempt güncellemesi yapmaz.
    - Telemetry flush hatası ana sonucu veya exception'ı maskelemez.
    - Sonuç JSON-serializable dict olarak döndürülür.
    """
    task_id = getattr(getattr(self, "request", None), "id", None)
    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError("task_id boş olamaz.")

    if isinstance(attempt_id, bool) or not isinstance(attempt_id, int) or attempt_id <= 0:
        raise ValueError("attempt_id pozitif bir tamsayı olmalıdır.")

    # 1. Worker Bootstrap Aşaması (Scope, UsageCollector, AI Service ilklendirme ve bağlama)
    ai = None
    collector = None
    try:
        db = SessionLocal()
        try:
            scoring_run_id, brand_profile_id = _load_ideas_attempt_telemetry_scope(db, attempt_id)
        finally:
            db.close()
        refusal = _legacy_refusal_isolated(scoring_run_id)
        if refusal is not None:
            # Eski motor run'ı salt-okunur: collector/AI kurulmaz, attempt
            # tipli `legacy_run_read_only` sebebiyle kapanır
            from app.generators.social.attempt_state import finalize_ideas_attempt_failure

            return _refuse_legacy_social_attempt(
                finalize_ideas_attempt_failure, attempt_id=attempt_id, task_id=task_id,
                refusal=refusal)

        from app.core.telemetry import UsageCollector
        from app.generators.ai_service import get_ai_service

        collector = UsageCollector(
            scoring_run_id=scoring_run_id,
            brand_profile_id=brand_profile_id,
            task_id=task_id,
        )
        ai = get_ai_service()
        if hasattr(ai, "collector"):
            ai.collector = collector
    except Exception:
        if collector is not None:
            try:
                collector.flush()
            except Exception:
                logger.warning("Social ideas bootstrap telemetry could not be flushed.")
        _close_ai(ai)
        _finalize_social_ideas_bootstrap_failure(attempt_id=attempt_id, task_id=task_id)
        raise

    # 2. Orkestrasyon Çalıştırma (Hata finalizasyonu idea_orchestration.py tarafından yönetilir)
    from app.core.social.idea_orchestration import run_social_idea_generation

    try:
        result = run_social_idea_generation(
            session_factory=SessionLocal,
            ai_service=ai,
            attempt_id=attempt_id,
            task_id=task_id,
        )

        return {
            "attempt_id": result.attempt_id,
            "brief_id": result.brief_id,
            "scoring_run_id": result.scoring_run_id,
            "status": result.status,
            "total_ideas": result.total_ideas,
            "ai_calls_used": result.ai_calls_used,
            "replayed": result.replayed,
        }
    finally:
        if collector is not None:
            try:
                collector.flush()
            except Exception as exc:
                logger.warning(f"Telemetry flush failed for attempt {attempt_id}: {exc}")
        _close_ai(ai)


# ==================== SOCIAL BRIEF IDEAS RETRY TASK (F1-F.7.8) ====================

def _load_ideas_retry_attempt_telemetry_scope(db, attempt_id: int) -> tuple[int, int]:
    """attempt_id üzerinden yalnızca scoring_run_id ve brand_profile_id okur.

    Salt-okunur ve kilitsizdir.
    Attempt, brief veya scoring_run bulunamazsa veya tutarsızsa ValueError fırlatır.
    """
    from app.database.models import ScoringRun, SocialBrief, SocialGenerationAttempt

    attempt = (
        db.query(SocialGenerationAttempt.id, SocialGenerationAttempt.brief_id, SocialGenerationAttempt.stage)
        .filter(SocialGenerationAttempt.id == attempt_id)
        .first()
    )
    if attempt is None:
        raise ValueError(f"SocialGenerationAttempt bulunamadı: {attempt_id}")
    if attempt.stage != "ideas_retry":
        raise ValueError(f"Geçersiz attempt stage: {attempt.stage!r}, 'ideas_retry' bekleniyordu.")

    brief = (
        db.query(SocialBrief.id, SocialBrief.scoring_run_id)
        .filter(SocialBrief.id == attempt.brief_id)
        .first()
    )
    if brief is None:
        raise ValueError(f"SocialBrief bulunamadı: {attempt.brief_id}")

    run = (
        db.query(ScoringRun.id, ScoringRun.brand_profile_id)
        .filter(ScoringRun.id == brief.scoring_run_id)
        .first()
    )
    if run is None:
        raise ValueError(f"ScoringRun bulunamadı: {brief.scoring_run_id}")

    return run.id, run.brand_profile_id


def _finalize_social_ideas_retry_bootstrap_failure(
    *,
    attempt_id: int,
    task_id: str,
) -> None:
    """Worker bootstrap aşamasında (telemetry scope, usage collector, AI init)
    oluşan hatalarda pending retry attempt'i atomik ve güvenli biçimde failed yapar.
    """
    from app.generators.social.attempt_state import finalize_ideas_retry_attempt_failure

    db = None
    try:
        db = SessionLocal()
        finalize_ideas_retry_attempt_failure(
            db,
            attempt_id=attempt_id,
            task_id=task_id,
            reason_code="worker_bootstrap_failed",
            error_message="Fikir üretimi worker hazırlığı tamamlanamadı.",
        )
        db.commit()
    except Exception:
        if db is not None:
            try:
                db.rollback()
            except Exception:
                pass
        logger.warning(
            "Social ideas retry bootstrap failure finalization could not be persisted."
        )
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                logger.warning(
                    "Social ideas retry bootstrap failure session could not be closed cleanly."
                )


@celery_app.task(
    bind=True,
    name="generation.social_brief_ideas_retry",
    soft_time_limit=1140,
    time_limit=1200,
    max_retries=0,
)
def social_brief_ideas_retry_task(self, attempt_id: int) -> dict:
    """Social brief fikir tekrar deneme (ideas_retry) Celery task wrapper'ı (F1-F.7.8).

    Kurallar:
    - Broker argümanı yalnızca attempt_id'dir.
    - task_id yalnızca self.request.id üzerinden alınır.
    - Celery seviyesinde otomatik retry yapılmaz (max_retries=0).
    - Scope okuma session'ı AI/orkestrasyon başlamadan önce açılıp kapatılır.
    - Worker bootstrap hatasında pending attempt atomik olarak failed yapılır.
    - run_social_idea_retry_generation orkestratörünü çağırır.
    - Task kendi başına fikir satırı veya attempt güncellemesi yapmaz.
    - Telemetry flush hatası ana sonucu veya exception'ı maskelemez.
    - Sonuç JSON-serializable dict olarak döndürülür.
    """
    task_id = getattr(getattr(self, "request", None), "id", None)
    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError("task_id boş olamaz.")

    if isinstance(attempt_id, bool) or not isinstance(attempt_id, int) or attempt_id <= 0:
        raise ValueError("attempt_id pozitif bir tamsayı olmalıdır.")

    # 1. Worker Bootstrap Aşaması (Scope, UsageCollector, AI Service ilklendirme ve bağlama)
    ai = None
    collector = None
    try:
        db = SessionLocal()
        try:
            scoring_run_id, brand_profile_id = _load_ideas_retry_attempt_telemetry_scope(db, attempt_id)
        finally:
            db.close()
        refusal = _legacy_refusal_isolated(scoring_run_id)
        if refusal is not None:
            # Eski motor run'ı salt-okunur: collector/AI kurulmaz, attempt
            # tipli `legacy_run_read_only` sebebiyle kapanır
            from app.generators.social.attempt_state import finalize_ideas_retry_attempt_failure

            return _refuse_legacy_social_attempt(
                finalize_ideas_retry_attempt_failure, attempt_id=attempt_id, task_id=task_id,
                refusal=refusal)

        from app.core.telemetry import UsageCollector
        from app.generators.ai_service import get_ai_service

        collector = UsageCollector(
            scoring_run_id=scoring_run_id,
            brand_profile_id=brand_profile_id,
            task_id=task_id,
        )
        ai = get_ai_service()
        if hasattr(ai, "collector"):
            ai.collector = collector
    except Exception:
        if collector is not None:
            try:
                collector.flush()
            except Exception:
                logger.warning("Social ideas retry bootstrap telemetry could not be flushed.")
        _close_ai(ai)
        _finalize_social_ideas_retry_bootstrap_failure(attempt_id=attempt_id, task_id=task_id)
        raise

    # 2. Orkestrasyon Çalıştırma (Hata finalizasyonu idea_retry_orchestration.py tarafından yönetilir)
    from app.core.social.idea_retry_orchestration import run_social_idea_retry_generation

    try:
        result = run_social_idea_retry_generation(
            session_factory=SessionLocal,
            ai_service=ai,
            attempt_id=attempt_id,
            task_id=task_id,
        )

        return {
            "attempt_id": result.attempt_id,
            "brief_id": result.brief_id,
            "scoring_run_id": result.scoring_run_id,
            "status": result.status,
            "newly_persisted_count": result.newly_persisted_count,
            "accepted_target_ids": list(result.accepted_target_ids),
            "unfilled_target_ids": list(result.unfilled_target_ids),
            "ai_calls_used": result.ai_calls_used,
            "replayed": result.replayed,
        }
    finally:
        if collector is not None:
            try:
                collector.flush()
            except Exception as exc:
                logger.warning(f"Telemetry flush failed for retry attempt {attempt_id}: {exc}")
        _close_ai(ai)


# ==================== SOCIAL BRIEF CONTENTS TASK (F1-G.5.6 / F1-G.5.6a) ====================

def _load_contents_attempt_telemetry_scope(db, attempt_id: int) -> tuple[int, int]:
    """attempt_id üzerinden yalnızca scoring_run_id ve brand_profile_id okur.

    Salt-okunur ve kilitsizdir.
    Attempt, brief veya scoring_run bulunamazsa veya tutarsızsa ValueError fırlatır.
    Tüm hata mesajları tamamen statiktir; attempt_id, brief_id, scoring_run_id veya stage
    değerleri asla mesaj içine dahil edilmez (F1-G.5.6a).
    """
    from app.database.models import ScoringRun, SocialBrief, SocialGenerationAttempt

    attempt = (
        db.query(SocialGenerationAttempt.id, SocialGenerationAttempt.brief_id, SocialGenerationAttempt.stage)
        .filter(SocialGenerationAttempt.id == attempt_id)
        .first()
    )
    if attempt is None:
        raise ValueError("Social contents attempt could not be loaded.")
    if attempt.stage != "contents":
        raise ValueError("Social contents attempt stage is invalid.")

    brief = (
        db.query(SocialBrief.id, SocialBrief.scoring_run_id)
        .filter(SocialBrief.id == attempt.brief_id)
        .first()
    )
    if brief is None:
        raise ValueError("Social contents brief could not be loaded.")

    run = (
        db.query(ScoringRun.id, ScoringRun.brand_profile_id)
        .filter(ScoringRun.id == brief.scoring_run_id)
        .first()
    )
    if run is None:
        raise ValueError("Social contents scoring run could not be loaded.")

    if isinstance(run.id, bool) or not isinstance(run.id, int) or run.id <= 0:
        raise ValueError("Social contents scoring run could not be loaded.")
    if isinstance(run.brand_profile_id, bool) or not isinstance(run.brand_profile_id, int) or run.brand_profile_id <= 0:
        raise ValueError("Social contents scoring run could not be loaded.")

    return run.id, run.brand_profile_id


def _finalize_social_contents_bootstrap_failure(
    *,
    attempt_id: int,
    task_id: str,
) -> None:
    """Worker bootstrap aşamasında (telemetry scope, usage collector, AI init, orchestrator load)
    oluşan hatalarda pending attempt'i atomik ve güvenli biçimde failed yapar.

    Canonical lock sırası (ScoringRun -> SocialBrief -> SocialGenerationAttempt) korunur.
    Doğrudan SocialGenerationAttempt sorgusu veya FOR UPDATE çalıştırmaz.
    DB'ye yalnız sabit güvenli mesaj yazılır; ham exception sızdırılmaz.
    Finalizasyonun kendi hatası asıl bootstrap hatasını asla maskelemez;
    SessionLocal, rollback ve close hataları yutulur ve sabit log üretilir.
    """
    from app.generators.social.attempt_state import finalize_contents_attempt_failure

    db = None
    try:
        db = SessionLocal()
        finalize_contents_attempt_failure(
            db,
            attempt_id=attempt_id,
            task_id=task_id,
            reason_code="worker_bootstrap_failed",
            error_message="Sosyal içerik worker hazırlığı tamamlanamadı.",
        )
        db.commit()
    except Exception:
        if db is not None:
            try:
                db.rollback()
            except Exception:
                pass
        logger.warning(
            "Social contents bootstrap failure finalization could not be persisted."
        )
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                logger.warning(
                    "Social contents bootstrap failure session could not be closed cleanly."
                )


def _load_social_content_orchestrator():
    """Sosyal içerik üretim orkestratörünü dinamik olarak çözer (F1-G.5.6a).

    Orchestrator importu bootstrap try/except bloğu içinde korunarak
    import hatalarında bootstrap failure finalizasyonunun çalışmasını sağlar.
    """
    from app.core.social.content_orchestration import run_social_content_generation

    return run_social_content_generation


@celery_app.task(
    bind=True,
    name="generation.social_brief_contents",
    soft_time_limit=1140,
    time_limit=1200,
    max_retries=0,
)
def social_brief_contents_task(self, attempt_id: int) -> dict:
    """Social brief içerik üretim Celery task wrapper'ı (F1-G.5.6 / F1-G.5.6a).

    Kurallar:
    - Broker argümanı yalnızca attempt_id'dir.
    - task_id yalnızca self.request.id üzerinden alınır.
    - Celery seviyesinde otomatik retry yapılmaz (max_retries=0).
    - Scope okuma session'ı AI/orkestrasyon başlamadan önce açılıp kapatılır.
    - Worker bootstrap hatasında (scope, collector, ai service init/bağlama, orchestrator import)
      pending attempt atomik olarak failed yapılır ve asıl hata fırlatılır.
    - run_social_content_generation orkestratörünü çağırır.
    - Task kendi başına içerik satırı veya attempt güncellemesi yapmaz.
    - Telemetry flush hatası ana sonucu veya exception'ı maskelemez.
    - Loglarda ve hata mesajlarında dinamik ID ve exception metni sızdırılmaz.
    - Sonuç JSON-serializable dict olarak döndürülür.
    """
    task_id = getattr(getattr(self, "request", None), "id", None)
    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError("task_id boş olamaz.")

    if isinstance(attempt_id, bool) or not isinstance(attempt_id, int) or attempt_id <= 0:
        raise ValueError("attempt_id pozitif bir tamsayı olmalıdır.")

    # 1. Worker Bootstrap Aşaması (Scope, UsageCollector, AI Service ilklendirme ve Orkestratör importu)
    ai = None
    collector = None
    orchestrator = None
    try:
        db = SessionLocal()
        try:
            scoring_run_id, brand_profile_id = _load_contents_attempt_telemetry_scope(db, attempt_id)
        finally:
            db.close()
        refusal = _legacy_refusal_isolated(scoring_run_id)
        if refusal is not None:
            # Eski motor run'ı salt-okunur: collector/AI kurulmaz, attempt
            # tipli `legacy_run_read_only` sebebiyle kapanır
            from app.generators.social.attempt_state import finalize_contents_attempt_failure

            return _refuse_legacy_social_attempt(
                finalize_contents_attempt_failure, attempt_id=attempt_id, task_id=task_id,
                refusal=refusal)

        from app.core.telemetry import UsageCollector
        from app.generators.ai_service import get_ai_service

        collector = UsageCollector(
            scoring_run_id=scoring_run_id,
            brand_profile_id=brand_profile_id,
            task_id=task_id,
        )
        ai = get_ai_service()
        if hasattr(ai, "collector"):
            ai.collector = collector

        orchestrator = _load_social_content_orchestrator()
    except Exception:
        if collector is not None:
            try:
                collector.flush()
            except Exception:
                logger.warning("Social contents bootstrap telemetry could not be flushed.")
        _close_ai(ai)
        try:
            _finalize_social_contents_bootstrap_failure(attempt_id=attempt_id, task_id=task_id)
        except Exception:
            logger.warning("Social contents bootstrap failure finalization could not be persisted.")
        raise

    # 2. Orkestrasyon Çalıştırma (Hata finalizasyonu content_orchestration.py tarafından yönetilir)
    try:
        result = orchestrator(
            session_factory=SessionLocal,
            ai_service=ai,
            attempt_id=attempt_id,
            task_id=task_id,
        )

        return {
            "attempt_id": result.attempt_id,
            "brief_id": result.brief_id,
            "scoring_run_id": result.scoring_run_id,
            "status": result.status,
            "requested_idea_ids": list(result.requested_idea_ids),
            "already_present_idea_ids": list(result.already_present_idea_ids),
            "persisted_idea_ids": list(result.persisted_idea_ids),
            "unresolved_idea_ids": list(result.unresolved_idea_ids),
            "warnings": [
                {
                    "idea_id": w.idea_id,
                    "reason_code": w.reason_code,
                    "claims": list(w.claims),
                    "ai_calls_used": w.ai_calls_used,
                }
                for w in result.warnings
            ],
            "ai_calls_used": result.ai_calls_used,
            "replayed": result.replayed,
        }
    finally:
        if collector is not None:
            try:
                collector.flush()
            except Exception:
                logger.warning("Social contents telemetry could not be flushed.")
        _close_ai(ai)



