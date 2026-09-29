"""
Content generation endpoints.
Roadmap2.md - Bölüm 6, 7 güncellemesi dahil.
"""
import math
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from loguru import logger
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_ai
from app.generators.ai_service import AIService
from app.database.models import Keyword, ContentOutput, SEOGeoContent, ChannelPool, AdGroup, SocialGenerationAttempt
from app.core.error_responses import safe_500_detail
from app.core.engine_version_gate import (
    require_non_legacy_brief,
    require_non_legacy_run,
    require_non_legacy_run_in_workspace,
)
from app.core.workspace import require_fresh_channel_pool, verify_scoring_run
from app.compliance.publish_review import (
    geo_evaluation_source,
    legacy_seo_checks,
    publish_review_from_rows,
)
from app.core.site_analyzer.brand_defaults import BrandDefaultsResolver, BrandResolveError
from app.schemas.content import (
    AdGroupRequest, AdGroupResponse,
    SocialPostRequest, SocialContentResponse,
    ContentGenerationSummary
)
from app.schemas.seo_geo import (
    SEOGEOGenerateRequest, SEOGEOBulkRequest,
    SEOGEOContentResponse, SEOGEOBulkResponse,
    ComplianceReportResponse
)
from app.schemas.ads import (
    AdsGenerateRequest,
    AdsGenerateResponse,
    AdGroupListResponse,
    AdGroupDBSchema,
    AdGroupRegenerateRequest,
)
from app.generators.seo_geo import SEOGEOGenerator
from app.generators.ads import AdsGenerator
from app.tasks.generation_tasks import (
    generate_ads_task,
    social_brief_contents_task,
    social_brief_ideas_task,
    social_brief_ideas_retry_task,
)
from sqlalchemy.exc import IntegrityError
from app.config import settings
from app.schemas.social_brief import (
    SocialAttemptSummaryResponse,
    SocialBriefStateResponse,
    SocialContentHistoryItemResponse,
    SocialContentHistoryResponse,
    SocialHistoryHookResponse,
    SocialBriefCategoriesGenerateRequest,
    SocialBriefContentsAttemptResponse,
    SocialBriefContentsGenerateRequest,
    SocialBriefCreateRequest,
    SocialBriefIdeasGenerateRequest,
    SocialBriefIdeasRetryRequest,
    SocialBriefResponse,
    SocialCategoriesGenerateResponse,
    SocialContentHookResponse,
    SocialContentWarningResponse,
    SocialFormatMatrixResponse,
    SocialGeneratedCategoryResponse,
    SocialGeneratedContentItemResponse,
    SocialGeneratedIdeaResponse,
    SocialIdeaCoverageResponse,
    SocialIdeaWarningResponse,
    SocialIdeasGenerateResponse,
)
from app.generators.social.format_matrix import get_format_matrix
from app.core.social import (
    SocialBriefEligibilityError,
    SocialBriefNotFoundError,
    SocialBriefRunNotFoundError,
    SocialBriefValidationError,
    SocialCategoryFlowError,
    SocialCategoryPersistenceError,
    SocialContentAttemptReadResult,
    SocialContentFlowError,
    SocialContentReadError,
    SocialContentReadNotFoundError,
    SocialIdeaFlowError,
    SocialIdeaReadError,
    SocialIdeaReadNotFoundError,
    SocialIdeaReadResult,
    PersistedSocialCategoriesResult,
    ValidatedCarouselPayload,
    ValidatedHook,
    ValidatedThreadPayload,
    ValidatedVideoPayload,
    begin_social_category_generation,
    begin_social_content_generation,
    begin_social_idea_generation,
    create_social_brief,
    get_social_brief,
    list_social_briefs,
    load_completed_social_categories,
    load_social_content_result,
    load_social_idea_result,
    persist_social_categories,
    serialize_content_format_payload,
    social_brief_to_response,
)
from app.core.social.idea_retry_flow import (
    SocialIdeaRetryFlowError,
    begin_social_idea_retry,
)
from app.core.social.idea_retry_read import load_social_idea_retry_result
from app.core.social.brief_state_read import (
    SocialAttemptSummary,
    SocialBriefStateNotFoundError,
    load_social_brief_state,
)
from app.core.social.content_history_read import (
    HISTORY_DEFAULT_LIMIT,
    HISTORY_MAX_LIMIT,
    HISTORY_MAX_OFFSET,
    SocialContentHistoryInputError,
    SocialContentHistoryNotFoundError,
    load_social_content_history,
)
from app.generators.social.attempt_state import (
    AttemptConflictError,
    AttemptNotFoundError,
    AttemptNotWritableError,
    BriefNotFoundError,
    InvalidStageError,
    finalize_contents_attempt_failure,
    finalize_ideas_attempt_failure,
    finalize_ideas_retry_attempt_failure,
    finish_attempt,
    reconcile_expired_contents_attempt_for_read,
    reconcile_expired_ideas_attempt_for_read,
    reconcile_expired_ideas_retry_attempt_for_read,
)
from app.generators.social.brief_category_generator import (
    SocialBriefCategoryGenerator,
    SocialCategoryGenerationError,
)
import uuid


router = APIRouter()


def _guard_generation_dispatch(db: Session, run_id: int) -> None:
    """Producer-taraf kilit protokolü (plan v13 invariant #4).

    Mutasyon endpoint'leriyle AYNI workspace satır kilidi alınır, ardından
    merkezi POLICY_STALE guard'ı çalışır — politika mutasyonu ile üretim
    dispatch'i yarışında yalnız biri kilit sonrası ilerler; stale havuzdan
    yeni içerik üretilemez (409 POLICY_STALE).
    """
    from app.database.models import BrandProfile, ScoringRun

    run = db.query(ScoringRun).filter(ScoringRun.id == run_id).first()
    if run is None:
        return
    # Eski motor (v2/v2_1) run'ı salt-okunurdur: TÜM üretim uçları
    # (SEO-GEO tekil/bulk, ADS RSA/regenerate, SOCIAL legacy uçları, fikir/
    # içerik regenerate) buradan geçer — kilit ve POLICY_STALE'den ÖNCE,
    # AI / Celery dispatch'ten önce tipli 409.
    require_non_legacy_run(run)
    if run.brand_profile_id:
        db.query(BrandProfile).filter(
            BrandProfile.id == run.brand_profile_id
        ).with_for_update().first()
    require_fresh_channel_pool(db, run)


def _scope_usage(ai, *, scoring_run_id=None, brand_profile_id=None) -> None:
    """İstek collector'ına run/workspace kimliği bağlar (Codex v8-3).

    get_ai dependency'si collector'ı kimliksiz yaratır; record() kimliği
    event anında okuduğu için bu çağrı AI kullanımından ÖNCE yapılmalıdır.
    """
    collector = getattr(ai, "collector", None)
    if collector is None:
        return
    collector.scoring_run_id = scoring_run_id
    collector.brand_profile_id = brand_profile_id


# ==================== SEO+GEO ENDPOINTS (Roadmap2 Bölüm 6) ====================

@router.post("/seo-geo", response_model=None)
def generate_seo_geo_content(
    request: SEOGEOGenerateRequest,
    brand_profile_id: int = Query(..., description="Workspace scope"),
    db: Session = Depends(get_db),
    ai: AIService = Depends(get_ai)
):
    """Generate SEO+GEO optimized content for a single keyword."""
    # Semada Optional (bulk yolu kullanmiyor) ama BU endpoint icin zorunlu:
    # workspace dogrulamasi scoring_run uzerinden yapilir (codex notu — eksikse
    # kafa karistiran 404 yerine okunur 400)
    if request.scoring_run_id is None:
        raise HTTPException(
            status_code=400,
            detail="scoring_run_id zorunludur (workspace doğrulaması bu run üzerinden yapılır)",
        )
    verify_scoring_run(db, request.scoring_run_id, brand_profile_id)
    _guard_generation_dispatch(db, request.scoring_run_id)
    _scope_usage(ai, scoring_run_id=request.scoring_run_id,
                 brand_profile_id=brand_profile_id)
    try:
        generator = SEOGEOGenerator(db, ai)
        result = generator.generate_content(request, scoring_run_id=request.scoring_run_id)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=safe_500_detail(e, "İçerik üretimi başarısız oldu")
        )


@router.post("/seo-geo/bulk/{scoring_run_id}", response_model=None)
def generate_seo_geo_bulk(
    scoring_run_id: int,
    limit: Optional[int] = Query(None, ge=1, le=100, description="Maximum contents to generate"),
    tone: str = Query("informative", description="Content tone"),
    brand_profile_id: int = Query(..., description="Workspace scope"),
    db: Session = Depends(get_db),
):
    """
    Generate SEO+GEO content in bulk as a background Celery task.

    Returns task_id for polling progress via /api/v1/tasks/{task_id}.
    """
    from app.database.models import ChannelPool, ScoringRun
    from app.tasks.generation_tasks import start_bulk_seo_generation

    verify_scoring_run(db, scoring_run_id, brand_profile_id)
    _guard_generation_dispatch(db, scoring_run_id)

    # Validate scoring run exists
    scoring_run = db.query(ScoringRun).filter(ScoringRun.id == scoring_run_id).first()
    if not scoring_run:
        raise HTTPException(status_code=404, detail=f"Scoring run {scoring_run_id} bulunamadı")

    # Get SEO pool keyword IDs
    pools = db.query(ChannelPool).filter(
        ChannelPool.scoring_run_id == scoring_run_id,
        ChannelPool.channel == 'SEO'
    ).all()

    if not pools:
        raise HTTPException(status_code=404, detail="SEO havuzunda keyword bulunamadı. Önce kanal ataması yapın.")

    keyword_ids = [p.keyword_id for p in pools]
    if limit:
        keyword_ids = keyword_ids[:limit]

    # Dispatch as Celery task
    task_id = start_bulk_seo_generation(scoring_run_id, keyword_ids, tone=tone)

    return {"task_id": task_id, "status": "pending", "total_keywords": len(keyword_ids)}


@router.get("/seo-geo/{content_id}")
def get_seo_geo_content(
    content_id: int,
    brand_profile_id: int = Query(..., description="Workspace scope"),
    db: Session = Depends(get_db)
):
    """
    Get a specific SEO+GEO content by ID.
    """
    content = db.query(SEOGeoContent).filter(SEOGeoContent.id == content_id).first()

    if not content:
        raise HTTPException(status_code=404, detail="Content not found")

    co = db.query(ContentOutput).filter(ContentOutput.id == content.content_output_id).first() if content.content_output_id else None
    run_id = co.scoring_run_id if co else None
    if not run_id:
        raise HTTPException(status_code=404, detail="Content not found")
    verify_scoring_run(db, run_id, brand_profile_id)

    keyword = db.query(Keyword).filter(Keyword.id == content.keyword_id).first()

    return {
        'id': content.id,
        'keyword_id': content.keyword_id,
        'keyword': keyword.keyword if keyword else None,
        'title': content.title,
        'url_suggestion': content.url_suggestion,
        'intro_paragraph': content.intro_paragraph,
        'body_content': content.body_content,
        'subheadings': content.subheadings,
        'body_sections': content.body_sections,
        'bullet_points': content.bullet_points,
        'internal_link': {
            'anchor': content.internal_link_anchor,
            'url': content.internal_link_url
        },
        'external_link': {
            'anchor': content.external_link_anchor,
            'url': content.external_link_url
        },
        'meta_description': content.meta_description,
        'faq_items': content.faq_items or [],
        'image_alt_texts': content.image_alt_texts or [],
        'word_count': content.word_count,
        'keyword_density': float(content.keyword_density) if content.keyword_density else 0,
        'created_at': content.created_at.isoformat() if content.created_at else None
    }


@router.get("/seo-geo/{content_id}/compliance")
def get_compliance_report(
    content_id: int,
    brand_profile_id: int = Query(..., description="Workspace scope"),
    db: Session = Depends(get_db)
):
    """
    Get detailed SEO+GEO compliance report for a content.

    Returns:
        Detailed compliance scores and improvement recommendations.
    """
    content = db.query(SEOGeoContent).filter(SEOGeoContent.id == content_id).first()

    if not content:
        raise HTTPException(status_code=404, detail="Content not found")

    co = db.query(ContentOutput).filter(ContentOutput.id == content.content_output_id).first() if content.content_output_id else None
    run_id = co.scoring_run_id if co else None
    if not run_id:
        raise HTTPException(status_code=404, detail="Content not found")
    verify_scoring_run(db, run_id, brand_profile_id)

    keyword = db.query(Keyword).filter(Keyword.id == content.keyword_id).first()

    # SEO compliance
    seo_compliance = content.seo_compliance
    seo_result = None
    if seo_compliance:
        # 'checks': yeni checklist kriterleri dahil kalici liste (checks_json).
        # Eski satirlarda null -> kolon-bazli AYNI formatta kurulur (codex),
        # frontend tek format gorur.
        checks = seo_compliance.checks_json or legacy_seo_checks(seo_compliance)
        seo_result = {
            'title_has_keyword': seo_compliance.title_has_keyword,
            'title_length_ok': seo_compliance.title_length_ok,
            'url_has_keyword': seo_compliance.url_has_keyword,
            'intro_keyword_count': seo_compliance.intro_keyword_count,
            'word_count_in_range': seo_compliance.word_count_in_range,
            'subheading_count_ok': seo_compliance.subheading_count_ok,
            'subheadings_have_kw': seo_compliance.subheadings_have_kw,
            'has_internal_link': seo_compliance.has_internal_link,
            'has_external_link': seo_compliance.has_external_link,
            'has_bullet_list': seo_compliance.has_bullet_list,
            'sentences_readable': seo_compliance.sentences_readable,
            'checks': checks,
            'total_passed': seo_compliance.total_passed,
            'total_checks': len(checks),
            'score': float(seo_compliance.total_score) if seo_compliance.total_score else 0,
            'improvement_notes': seo_compliance.improvement_notes
        }
    
    # GEO compliance
    geo_compliance = content.geo_compliance
    geo_result = None
    if geo_compliance:
        geo_result = {
            'intro_answers_question': geo_compliance.intro_answers_question,
            'snippet_extractable': geo_compliance.snippet_extractable,
            'info_hierarchy_strong': geo_compliance.info_hierarchy_strong,
            'tone_is_informative': geo_compliance.tone_is_informative,
            'no_fluff_content': geo_compliance.no_fluff_content,
            'direct_answer_present': geo_compliance.direct_answer_present,
            'has_verifiable_info': geo_compliance.has_verifiable_info,
            'total_passed': geo_compliance.total_passed,
            'total_checks': 7,
            'score': float(geo_compliance.total_score) if geo_compliance.total_score else 0,
            'ai_snippet_preview': geo_compliance.ai_snippet_preview,
            'improvement_notes': geo_compliance.improvement_notes,
            # 'ai' = kriterler AI tarafından değerlendirildi; 'fallback' = AI
            # çağrısı başarısız, kriterler DEĞERLENDİRİLMEDİ (geçti/kaldı değil)
            'evaluation_source': geo_evaluation_source(geo_compliance),
        }
    
    # Combined score
    seo_score = float(seo_compliance.total_score) if seo_compliance and seo_compliance.total_score else 0
    geo_score = float(geo_compliance.total_score) if geo_compliance and geo_compliance.total_score else 0
    combined_score = (seo_score + geo_score) / 2
    
    # Critical issues
    critical_issues = []
    if seo_compliance:
        if not seo_compliance.title_has_keyword:
            critical_issues.append("Başlıkta anahtar kelime yok")
        if not seo_compliance.title_length_ok:
            critical_issues.append("Başlık 70 karakterden uzun")
        if seo_compliance.intro_keyword_count and seo_compliance.intro_keyword_count < 2:
            critical_issues.append("Giriş paragrafında yeterli keyword yok")
    
    # Recommendations
    recommendations = []
    if seo_compliance and seo_compliance.improvement_notes:
        recommendations.append(seo_compliance.improvement_notes)
    if geo_compliance and geo_compliance.improvement_notes:
        recommendations.append(geo_compliance.improvement_notes)
    
    return {
        'content_id': content.id,
        'keyword': keyword.keyword if keyword else None,
        'seo_compliance': seo_result,
        'geo_compliance': geo_result,
        'combined_score': round(combined_score, 2),
        'critical_issues': critical_issues,
        'recommendations': recommendations,
        # Kritik checklist başarısızlığı -> "yayın öncesi inceleme gerekli";
        # sistemde verisi olmayan maddeler manual_checks'te (uydurulmaz)
        'publish_review': publish_review_from_rows(seo_compliance, geo_compliance),
        'checked_at': (seo_compliance.checked_at.isoformat() 
                      if seo_compliance and seo_compliance.checked_at else None)
    }


@router.get("/seo-geo/list/{scoring_run_id}")
def list_seo_geo_contents(
    scoring_run_id: int,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    brand_profile_id: int = Query(..., description="Workspace scope"),
    db: Session = Depends(get_db)
):
    """
    List all SEO+GEO contents for a scoring run.
    """
    verify_scoring_run(db, scoring_run_id, brand_profile_id)

    # Get keywords from SEO pool for this scoring run
    pool_items = db.query(ChannelPool).filter(
        ChannelPool.scoring_run_id == scoring_run_id,
        ChannelPool.channel == 'SEO'
    ).all()

    keyword_ids = [p.keyword_id for p in pool_items]

    # Get contents for these keywords — only content generated for this specific run
    query = (
        db.query(SEOGeoContent)
        .join(ContentOutput, SEOGeoContent.content_output_id == ContentOutput.id)
        .filter(
            SEOGeoContent.keyword_id.in_(keyword_ids),
            ContentOutput.scoring_run_id == scoring_run_id,
        )
    )
    
    total = query.count()
    contents = query.offset(skip).limit(limit).all()
    
    results = []
    for content in contents:
        keyword = db.query(Keyword).filter(Keyword.id == content.keyword_id).first()
        content_output = db.query(ContentOutput).filter(
            ContentOutput.id == content.content_output_id
        ).first() if content.content_output_id else None
        
        seo_score = float(content.seo_compliance.total_score) if content.seo_compliance and content.seo_compliance.total_score else 0
        geo_score = float(content.geo_compliance.total_score) if content.geo_compliance and content.geo_compliance.total_score else 0
        
        results.append({
            'id': content.id,
            'keyword_id': content.keyword_id,
            'keyword': keyword.keyword if keyword else None,
            'title': content.title,
            'word_count': content.word_count,
            'seo_score': seo_score,
            'geo_score': geo_score,
            'combined_score': round((seo_score + geo_score) / 2, 2),
            'is_stale': bool(content_output.is_stale) if content_output else False,
            'publish_review': publish_review_from_rows(
                content.seo_compliance, content.geo_compliance
            ),
            'created_at': content.created_at.isoformat() if content.created_at else None,
            # Kart ozeti alanlari (tam govde bilerek dahil edilmez)
            'intro_paragraph': content.intro_paragraph,
            'meta_description': content.meta_description,
            'url_suggestion': content.url_suggestion,
            'faq_items': content.faq_items or [],
            'image_alt_texts': content.image_alt_texts or [],
            'subheadings': content.subheadings or [],
            'subheading_count': content.subheading_count or len(content.subheadings or []),
            'keyword_density': float(content.keyword_density) if content.keyword_density is not None else None,
            'internal_link': {
                'anchor': content.internal_link_anchor,
                'url': content.internal_link_url,
            } if content.internal_link_anchor or content.internal_link_url else None,
            'external_link': {
                'anchor': content.external_link_anchor,
                'url': content.external_link_url,
            } if content.external_link_anchor or content.external_link_url else None,
        })
    
    return {
        'scoring_run_id': scoring_run_id,
        'total': total,
        'skip': skip,
        'limit': limit,
        'items': results
    }


# ==================== GOOGLE ADS RSA ENDPOINTS (Roadmap2 Bölüm 7) ====================

def _dispatch_ads(
    db: Session,
    scoring_run_id: int,
    *,
    operation: str,
    task_kwargs: dict,
    snapshot_extra: dict,
):
    """Ortak ADS dispatch (Faz E3): kilit + guard + set + enqueue.

    Full generation VE grup-regeneration AYNI yoldan geçer — aynı ScoringRun
    kilidi, aynı çift guard (aktif task + generating set), version numarası
    yalnız kilit altında ayrılır.
    """
    import uuid

    from app.generators.ads.generation_sets import (
        AdsGenerationConflict,
        dispatch_ads_generation,
        mark_dispatch_failed,
    )
    from app.schemas.ads import AdsDispatchResponse

    task_id = str(uuid.uuid4())
    try:
        gen_set = dispatch_ads_generation(
            db,
            scoring_run_id,
            task_id=task_id,
            request_snapshot={"operation": operation, **snapshot_extra},
        )
    except AdsGenerationConflict as conflict:
        raise HTTPException(
            status_code=409,
            detail={"message": conflict.message, "task_id": conflict.task_id},
        )

    try:
        generate_ads_task.apply_async(
            kwargs={
                "scoring_run_id": scoring_run_id,
                "generation_set_id": gen_set.id,
                "operation": operation,
                **task_kwargs,
            },
            task_id=task_id,
        )
    except Exception as enqueue_error:
        # Broker enqueue hatası: hem task hem set failed (E3 adım 4)
        mark_dispatch_failed(db, task_id, str(enqueue_error))
        raise HTTPException(
            status_code=500,
            detail=safe_500_detail(enqueue_error, "ADS görevi kuyruğa alınamadı"),
        )

    return AdsDispatchResponse(
        task_id=task_id,
        generation_set_id=gen_set.id,
        version_number=gen_set.version_number,
        status=gen_set.status,
    )


@router.post("/ads/rsa", response_model=None, status_code=202)
def generate_ads_rsa(
    request: AdsGenerateRequest,
    brand_profile_id: int = Query(..., description="Workspace scope"),
    db: Session = Depends(get_db),
):
    """
    Enqueue async RSA generation for ADS pool keywords (versiyonlanmış).

    Aynı run'da aktif ADS üretimi varken 409 döner. Her başarılı üretim
    ayrı set olur: ilki otomatik 'active', sonrakiler 'draft' (onayla
    aktifleşir). Cevap: task_id + generation_set_id + version_number.
    """
    try:
        verify_scoring_run(db, request.scoring_run_id, brand_profile_id)
        _guard_generation_dispatch(db, request.scoring_run_id)

        resolver = BrandDefaultsResolver(db)
        defaults = resolver.resolve(request.scoring_run_id)
        effective_brand_name = (
            resolver.safe_str(request.brand_name) or defaults.get("brand_name", "") or "Marka"
        )
        # display: prompt'ta gösterilecek effective değer (fallback olabilir);
        # trusted: YALNIZ kullanıcının istekte açıkça verdiği değer —
        # grounding whitelist'i yalnız trusted'ı kullanır (Faz F).
        trusted_brand_usp = resolver.safe_str(request.brand_usp) or ""
        effective_brand_usp = (
            trusted_brand_usp
            or defaults.get("brand_usp", "")
            or "Kaliteli ürün ve hizmet"
        )

        return _dispatch_ads(
            db,
            request.scoring_run_id,
            operation="full",
            task_kwargs={
                "brand_name": effective_brand_name,
                "brand_usp": effective_brand_usp,
                "trusted_brand_usp": trusted_brand_usp,
                "website_url": request.website_url,
                "max_groups": request.max_groups,
                "enable_ai_regeneration": request.enable_ai_regeneration,
            },
            snapshot_extra={
                "brand_name": effective_brand_name,
                "display_brand_usp": effective_brand_usp,
                "trusted_brand_usp": trusted_brand_usp,
                "max_groups": request.max_groups,
            },
        )
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=safe_500_detail(e, "RSA üretimi başarısız oldu")
        )


@router.get("/ads/sets")
def list_ads_generation_sets(
    scoring_run_id: int = Query(..., description="Scoring run"),
    brand_profile_id: int = Query(..., description="Workspace scope"),
    db: Session = Depends(get_db),
):
    """Run'a ait ADS set geçmişi (status, is_stale, version, sayılar)."""
    from app.database.models import AdGenerationSet
    from app.generators.ads.generation_sets import get_active_set
    from app.schemas.ads import AdGenerationSetListResponse, AdGenerationSetSummary

    try:
        verify_scoring_run(db, scoring_run_id, brand_profile_id)
        sets = (
            db.query(AdGenerationSet)
            .filter(AdGenerationSet.scoring_run_id == scoring_run_id)
            .order_by(AdGenerationSet.version_number.desc())
            .all()
        )
        active = get_active_set(db, scoring_run_id)
        return AdGenerationSetListResponse(
            scoring_run_id=scoring_run_id,
            sets=[AdGenerationSetSummary.model_validate(s) for s in sets],
            active_set_id=active.id if active else None,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=safe_500_detail(e, "Set geçmişi alınamadı"),
        )


@router.post("/ads/sets/{set_id}/activate")
def activate_ads_generation_set(
    set_id: int,
    brand_profile_id: int = Query(..., description="Workspace scope"),
    db: Session = Depends(get_db),
):
    """Draft/archived seti aktifleştirir; önceki aktif 'archived' olur (atomik).

    Stale, generating, failed veya boş setler aktive EDİLEMEZ.
    """
    from app.database.models import AdGenerationSet
    from app.schemas.ads import AdGenerationSetSummary
    from app.generators.ads.generation_sets import activate_set

    try:
        gen_set = (
            db.query(AdGenerationSet)
            .filter(AdGenerationSet.id == set_id)
            .first()
        )
        if gen_set is None:
            raise HTTPException(status_code=404, detail="Set bulunamadı")
        verify_scoring_run(db, gen_set.scoring_run_id, brand_profile_id)

        try:
            activated = activate_set(db, gen_set)
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e))

        return AdGenerationSetSummary.model_validate(activated)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=safe_500_detail(e, "Set aktive edilemedi"),
        )


@router.get("/ads/rsa/{scoring_run_id}", response_model=AdGroupListResponse)
def get_ads_groups(
    scoring_run_id: int,
    brand_profile_id: int = Query(..., description="Workspace scope"),
    set_id: Optional[int] = Query(None, description="Belirli set (geçmiş görüntüleme)"),
    db: Session = Depends(get_db),
    ai: AIService = Depends(get_ai)
):
    """
    Get ad groups for a scoring run.

    Varsayılan AKTİF + NON-STALE setin gruplarıdır; set_id ile geçmiş
    setler görüntülenebilir. Aktif non-stale set yoksa boş liste döner.
    """
    try:
        verify_scoring_run(db, scoring_run_id, brand_profile_id)

        generator = AdsGenerator(db, ai)
        try:
            result = generator.get_ad_groups(scoring_run_id, generation_set_id=set_id)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=safe_500_detail(e, "Reklam gruplari alinamadi")
        )


@router.get("/ads/rsa/group/{group_id}")
def get_ads_group_detail(
    group_id: int,
    brand_profile_id: int = Query(..., description="Workspace scope"),
    db: Session = Depends(get_db),
    ai: AIService = Depends(get_ai)
):
    """
    Get detailed view of a single ad group.

    Returns complete ad group with all RSA components.
    """
    try:
        resolver = BrandDefaultsResolver(db)
        try:
            run_id = resolver.run_id_from_ad_group(group_id)
        except BrandResolveError:
            raise HTTPException(status_code=404, detail="Ad group not found")
        verify_scoring_run(db, run_id, brand_profile_id)

        generator = AdsGenerator(db, ai)
        result = generator.get_ad_group_detail(group_id)
        
        if not result:
            raise HTTPException(status_code=404, detail="Ad group not found")
        
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=safe_500_detail(e, "Reklam grubu alinamadi")
        )


@router.post("/ads/rsa/group/{group_id}/regenerate", status_code=202)
def regenerate_ads_group(
    group_id: int,
    brand_profile_id: int = Query(..., description="Workspace scope"),
    request: AdGroupRegenerateRequest = None,
    db: Session = Depends(get_db),
    ai: AIService = Depends(get_ai)
):
    """
    Regenerate RSA for a specific ad group — ASYNC + VERSIYONLU (Faz E).

    Geçmiş set DEĞİŞTİRİLMEZ: kaynak set klonlanır, yalnız hedef grup
    yeniden üretilir, sonuç yeni 'draft' set olur. Cevap 202-benzeri:
    task_id + generation_set_id (UI task'ı poll eder). Stale kaynaktan
    regenerate 409 döner (tam ADS üretimi gerekir). Full üretimle AYNI
    dispatcher/kilitten geçer — çapraz yarış 409 ile engellenir.
    """
    from app.database.models import AdGroup as AdGroupModel
    from app.generators.ads.generation_sets import SUCCESSFUL_STATUSES

    try:
        resolver = BrandDefaultsResolver(db)
        try:
            run_id = resolver.run_id_from_ad_group(group_id)
        except BrandResolveError as e:
            raise HTTPException(status_code=404, detail=str(e))
        verify_scoring_run(db, run_id, brand_profile_id)
        _guard_generation_dispatch(db, run_id)

        group = db.query(AdGroupModel).filter(AdGroupModel.id == group_id).first()
        if group is None:
            raise HTTPException(status_code=404, detail="Ad group not found")
        source_set = group.generation_set
        if source_set is None:
            raise HTTPException(
                status_code=409,
                detail="Grubun üretim seti yok — tam ADS üretimi gerekli",
            )
        if source_set.is_stale:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Kaynak set stale (kanal ataması yenilendi) — bu setten "
                    "grup yenilenemez; tam ADS üretimi çalıştırın."
                ),
            )
        if source_set.status not in SUCCESSFUL_STATUSES:
            raise HTTPException(
                status_code=409,
                detail=f"Set durumu '{source_set.status}' — regenerate edilemez",
            )

        defaults = resolver.resolve(run_id)
        effective_brand_name = (
            resolver.safe_str(request.brand_name if request else None)
            or defaults.get("brand_name", "")
            or "Marka"
        )
        trusted_brand_usp = resolver.safe_str(
            request.brand_usp if request else None
        ) or ""
        effective_brand_usp = (
            trusted_brand_usp
            or defaults.get("brand_usp", "")
            or "Kaliteli ürün ve hizmet"
        )

        return _dispatch_ads(
            db,
            run_id,
            operation="group_regenerate",
            task_kwargs={
                "brand_name": effective_brand_name,
                "brand_usp": effective_brand_usp,
                "trusted_brand_usp": trusted_brand_usp,
                "source_set_id": source_set.id,
                "source_group_id": group_id,
            },
            snapshot_extra={
                "source_set_id": source_set.id,
                "source_group_id": group_id,
                "display_brand_usp": effective_brand_usp,
                "trusted_brand_usp": trusted_brand_usp,
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=safe_500_detail(e, "Reklam grubu yeniden üretilemedi")
        )


# ==================== SOCIAL MEDIA ENDPOINTS (Roadmap2 Bölüm 8) ====================

from app.generators.social import SocialGenerator
from app.schemas.social import (
    SocialCategoriesRequest,
    SocialIdeasRequest,
    SocialContentsRequest,
    SocialBulkRequest,
    SocialRegenerateRequest,
    SocialCategoriesResponse,
    SocialIdeasResponse,
    SocialContentsResponse,
    SocialBulkResponse,
    SocialFullResponse,
    SocialIdeaDBSchema,
    SocialContentDBSchema,
    TaskDispatchResponse,
)


@router.post("/social/categories", response_model=SocialCategoriesResponse)
def generate_social_categories(
    request: SocialCategoriesRequest,
    brand_profile_id: int = Query(..., description="Workspace scope"),
    db: Session = Depends(get_db),
    ai: AIService = Depends(get_ai)
):
    """Phase 1: Generate content categories from SOCIAL pool keywords."""
    try:
        verify_scoring_run(db, request.scoring_run_id, brand_profile_id)
        _guard_generation_dispatch(db, request.scoring_run_id)
        _scope_usage(ai, scoring_run_id=request.scoring_run_id,
                     brand_profile_id=brand_profile_id)

        resolver = BrandDefaultsResolver(db)
        defaults = resolver.resolve(request.scoring_run_id)
        effective_brand_name = resolver.safe_str(request.brand_name) or defaults.get("brand_name", "") or "Marka"
        effective_brand_context = resolver.safe_str(request.brand_context) or defaults.get("brand_context", "")

        effective_request = request.model_copy(update={
            "brand_name": effective_brand_name,
            "brand_context": effective_brand_context,
        })

        generator = SocialGenerator(db, ai)
        return generator.generate_categories(effective_request)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=safe_500_detail(e, "Sosyal kategoriler üretilemedi")
        )


@router.post("/social/ideas", response_model=List[SocialIdeasResponse])
def generate_social_ideas(
    request: SocialIdeasRequest,
    brand_name: Optional[str] = Query(None, description="Brand name for personalization"),
    brand_profile_id: int = Query(..., description="Workspace scope"),
    db: Session = Depends(get_db),
    ai: AIService = Depends(get_ai)
):
    """Phase 2: Generate content ideas for selected categories."""
    try:
        resolver = BrandDefaultsResolver(db)
        try:
            run_id = resolver.run_id_from_category(request.category_ids)
        except BrandResolveError as e:
            status = 400 if e.code in ("mixed_run", "empty_list") else 404
            raise HTTPException(status_code=status, detail=str(e))

        verify_scoring_run(db, run_id, brand_profile_id)
        _guard_generation_dispatch(db, run_id)
        _scope_usage(ai, scoring_run_id=run_id, brand_profile_id=brand_profile_id)

        defaults = resolver.resolve(run_id)
        effective_brand_name = resolver.safe_str(brand_name) or defaults.get("brand_name", "") or "Marka"

        generator = SocialGenerator(db, ai)
        try:
            return generator.generate_ideas(request, effective_brand_name)
        except ValueError as policy_error:
            # BRIEF_SCOPED: brief'e ait kategori legacy uctan islenemez (K11/§7-8)
            raise HTTPException(status_code=409, detail=str(policy_error))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=safe_500_detail(e, "Sosyal fikirler üretilemedi")
        )


@router.post("/social/contents", response_model=SocialContentsResponse)
def generate_social_contents(
    request: SocialContentsRequest,
    brand_profile_id: int = Query(..., description="Workspace scope"),
    db: Session = Depends(get_db),
    ai: AIService = Depends(get_ai)
):
    """Phase 3: Generate full content packages for selected ideas."""
    try:
        resolver = BrandDefaultsResolver(db)
        try:
            run_id = resolver.run_id_from_idea(request.idea_ids)
        except BrandResolveError as e:
            status = 400 if e.code in ("mixed_run", "empty_list") else 404
            raise HTTPException(status_code=status, detail=str(e))

        verify_scoring_run(db, run_id, brand_profile_id)
        _guard_generation_dispatch(db, run_id)
        _scope_usage(ai, scoring_run_id=run_id, brand_profile_id=brand_profile_id)

        defaults = resolver.resolve(run_id)
        effective_brand_name = resolver.safe_str(request.brand_name) or defaults.get("brand_name", "") or "Marka"

        effective_request = request.model_copy(update={
            "brand_name": effective_brand_name,
        })

        generator = SocialGenerator(db, ai)
        try:
            return generator.generate_contents(effective_request, scoring_run_id=run_id)
        except ValueError as policy_error:
            # BRIEF_SCOPED: brief'e ait fikir legacy uctan islenemez (§7-8)
            raise HTTPException(status_code=409, detail=str(policy_error))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=safe_500_detail(e, "Sosyal içerikler üretilemedi")
        )


@router.post("/social/contents/async", response_model=TaskDispatchResponse)
def generate_social_contents_async(
    request: SocialContentsRequest,
    brand_profile_id: int = Query(..., description="Workspace scope"),
    db: Session = Depends(get_db),
):
    """
    Phase 3 (async): Icerik uretimini Celery task'ina gonderir (P7 Adim 5).

    Sync /social/contents aynen durur (geriye uyum). Dispatch deseni
    assignment_dispatcher ile ayni: pending TaskResult ENDPOINT'te olusur ki
    UI polling'i hicbir an 404 gormesin; enqueue patlarsa task failed + 503.
    """
    import uuid

    from app.tasks.generation_tasks import social_contents_task
    from app.tasks.task_status import create_task_record, update_task_status

    try:
        resolver = BrandDefaultsResolver(db)
        try:
            run_id = resolver.run_id_from_idea(request.idea_ids)
        except BrandResolveError as e:
            status = 400 if e.code in ("mixed_run", "empty_list") else 404
            raise HTTPException(status_code=status, detail=str(e))

        verify_scoring_run(db, run_id, brand_profile_id)
        _guard_generation_dispatch(db, run_id)
        # NOT: bu endpoint AI kullanmaz (dispatch-only) — telemetri worker
        # task'ının kendi collector'ında toplanır, _scope_usage gerekmez

        # BRIEF_SCOPED (§7/§8): dispatch'ten ONCE reddedilir — aksi halde
        # kullanici 202 alip Celery worker'da 409'a esdeger bir failed
        # gorur (SocialGenerator.generate_contents de ayni korumayi tasir).
        try:
            SocialGenerator(db, None)._assert_ideas_not_brief_scoped(request.idea_ids)
        except ValueError as policy_error:
            raise HTTPException(status_code=409, detail=str(policy_error))

        defaults = resolver.resolve(run_id)
        effective_brand_name = (
            resolver.safe_str(request.brand_name) or defaults.get("brand_name", "") or "Marka"
        )

        task_id = str(uuid.uuid4())
        create_task_record(task_id, "social_content", run_id, {
            "idea_count": len(request.idea_ids),
        })

        try:
            social_contents_task.apply_async(
                args=(run_id, request.idea_ids, effective_brand_name, request.brand_tone),
                task_id=task_id,
            )
        except Exception as enqueue_error:
            # Kuyruk erisilemezse kullanici sonsuz pending'e bakmasin (codex)
            update_task_status(
                task_id,
                status="failed",
                error_message="Gorev kuyruga alinamadi; Celery/Redis erisimini kontrol edin.",
            )
            raise HTTPException(
                status_code=503,
                detail="Icerik uretimi kuyruga alinamadi. Lutfen tekrar deneyin.",
            ) from enqueue_error

        return TaskDispatchResponse(task_id=task_id, scoring_run_id=run_id)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=safe_500_detail(e, "Sosyal içerik üretimi başlatılamadı")
        )


@router.post("/social/bulk", response_model=SocialBulkResponse)
def generate_social_bulk(
    request: SocialBulkRequest,
    brand_profile_id: int = Query(..., description="Workspace scope"),
    db: Session = Depends(get_db),
    ai: AIService = Depends(get_ai)
):
    """Full 3-phase social pipeline with automatic idea selection.

    K12 (plan_social_brief_akisi.md §0/§8): ENABLE_SOCIAL_LEGACY_BULK
    kapaliyken bu uc 410 doner — yeni akis /generation/social/briefs.
    """
    if not settings.ENABLE_SOCIAL_LEGACY_BULK:
        raise HTTPException(
            status_code=410,
            detail=(
                "Bu uc kaldirildi. Yeni sosyal icerik akisi icin "
                "/generation/social/briefs kullanin."
            ),
        )
    try:
        verify_scoring_run(db, request.scoring_run_id, brand_profile_id)
        _guard_generation_dispatch(db, request.scoring_run_id)
        _scope_usage(ai, scoring_run_id=request.scoring_run_id,
                     brand_profile_id=brand_profile_id)
        resolver = BrandDefaultsResolver(db)
        defaults = resolver.resolve(request.scoring_run_id)
        effective_brand_name = resolver.safe_str(request.brand_name) or defaults.get("brand_name", "") or "Marka"
        effective_brand_context = resolver.safe_str(request.brand_context) or defaults.get("brand_context", "")

        effective_request = request.model_copy(update={
            "brand_name": effective_brand_name,
            "brand_context": effective_brand_context,
        })

        generator = SocialGenerator(db, ai)
        return generator.generate_full_pipeline(effective_request)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=safe_500_detail(e, "Sosyal medya içeriği üretilemedi")
        )


@router.get(
    "/social/format-matrix",
    response_model=SocialFormatMatrixResponse,
)
def get_social_format_matrix():
    """Sosyal brief platform, format ve süre matrisi (read-only, deterministik)."""
    return get_format_matrix()


@router.post(
    "/social/briefs",
    response_model=SocialBriefResponse,
    status_code=201,
)
def create_social_brief_endpoint(
    request: SocialBriefCreateRequest,
    brand_profile_id: int = Query(..., description="Workspace scope"),
    db: Session = Depends(get_db),
):
    """Sosyal brief oluşturma endpoint'i (atomik persistence)."""
    if not getattr(settings, "ENABLE_SOCIAL_BRIEF_FLOW", False):
        raise HTTPException(
            status_code=404,
            detail={
                "code": "FEATURE_DISABLED",
                "message": "Social brief flow is not enabled.",
            },
        )

    # Eski motor run'ı salt-okunur: yeni brief açılmaz (tipli 409)
    require_non_legacy_run_in_workspace(
        db, request.scoring_run_id, brand_profile_id)

    try:
        brief = create_social_brief(
            db,
            request=request,
            brand_profile_id=brand_profile_id,
        )
        response_data = social_brief_to_response(brief)
        db.commit()
        return response_data
    except SocialBriefValidationError as e:
        db.rollback()
        raise HTTPException(
            status_code=422,
            detail={
                "code": e.error_code,
                "message": e.message,
                "field": e.field,
            },
        )
    except SocialBriefEligibilityError as e:
        db.rollback()
        status_map = {
            "RUN_NOT_FOUND": 404,
            "POOL_STALE": 409,
            "SOCIAL_KEYWORD_NOT_ELIGIBLE": 400,
        }
        status_code = status_map.get(e.error_code, 400)
        detail = {
            "code": e.error_code,
            "message": e.message,
            "field": e.field,
        }
        if e.details:
            detail["details"] = e.details
        raise HTTPException(status_code=status_code, detail=detail)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail={
                "code": "BRIEF_PERSISTENCE_CONFLICT",
                "message": "Brief could not be created because the underlying data changed.",
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=safe_500_detail(e, "Sosyal medya brief'i oluşturulamadı"),
        )


@router.get(
    "/social/briefs",
    response_model=List[SocialBriefResponse],
)
def list_social_briefs_endpoint(
    scoring_run_id: int = Query(..., description="Scoring run ID"),
    brand_profile_id: int = Query(..., description="Workspace scope"),
    db: Session = Depends(get_db),
):
    """Run'a ait sosyal brief'leri listeleme endpoint'i."""
    if not getattr(settings, "ENABLE_SOCIAL_BRIEF_FLOW", False):
        raise HTTPException(
            status_code=404,
            detail={
                "code": "FEATURE_DISABLED",
                "message": "Social brief flow is not enabled.",
            },
        )

    try:
        briefs = list_social_briefs(
            db,
            scoring_run_id=scoring_run_id,
            brand_profile_id=brand_profile_id,
        )
        return [social_brief_to_response(b) for b in briefs]
    except SocialBriefRunNotFoundError as e:
        db.rollback()
        raise HTTPException(
            status_code=404,
            detail={
                "code": e.error_code,
                "message": e.message,
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=safe_500_detail(e, "Sosyal medya brief'leri listelenemedi"),
        )


@router.get(
    "/social/briefs/{brief_id}",
    response_model=SocialBriefResponse,
)
def get_social_brief_endpoint(
    brief_id: int,
    brand_profile_id: int = Query(..., description="Workspace scope"),
    db: Session = Depends(get_db),
):
    """Tekil sosyal brief okuma endpoint'i."""
    if not getattr(settings, "ENABLE_SOCIAL_BRIEF_FLOW", False):
        raise HTTPException(
            status_code=404,
            detail={
                "code": "FEATURE_DISABLED",
                "message": "Social brief flow is not enabled.",
            },
        )

    try:
        brief = get_social_brief(
            db,
            brief_id=brief_id,
            brand_profile_id=brand_profile_id,
        )
        return social_brief_to_response(brief)
    except SocialBriefNotFoundError as e:
        db.rollback()
        raise HTTPException(
            status_code=404,
            detail={
                "code": e.error_code,
                "message": e.message,
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=safe_500_detail(e, "Sosyal medya brief'i okunamadı"),
        )


def _attempt_summary_to_response(s: SocialAttemptSummary) -> SocialAttemptSummaryResponse:
    return SocialAttemptSummaryResponse(
        id=s.id,
        stage=s.stage,
        status=s.status,
        reason_code=s.reason_code,
        created_at=s.created_at,
        completed_at=s.completed_at,
        lease_expired=s.lease_expired,
        requested_idea_ids=list(s.requested_idea_ids),
    )


@router.get(
    "/social/briefs/{brief_id}/state",
    response_model=SocialBriefStateResponse,
)
def get_social_brief_state_endpoint(
    brief_id: int,
    brand_profile_id: int = Query(..., gt=0, description="Workspace scope"),
    db: Session = Depends(get_db),
):
    """Brief ekranını yeniden kurmak için salt-okunur durum özeti (F1-H.2).

    Tamamlanmış kategoriler + aşama bazında attempt kimlik/durumları + içeriği
    olan fikir ID'leri. Lease uzlaştırması attempt GET uçlarında yapılır.
    """
    if not getattr(settings, "ENABLE_SOCIAL_BRIEF_FLOW", False):
        raise HTTPException(
            status_code=404,
            detail={
                "code": "FEATURE_DISABLED",
                "message": "Social brief flow is not enabled.",
            },
        )
    try:
        state = load_social_brief_state(
            db, brief_id=brief_id, brand_profile_id=brand_profile_id
        )
    except SocialBriefStateNotFoundError:
        raise HTTPException(
            status_code=404,
            detail={"code": "BRIEF_NOT_FOUND", "message": "Social brief not found."},
        )
    except SocialCategoryPersistenceError:
        logger.exception("Brief durum okuması: kayıtlı kategori verisi tutarsız")
        raise HTTPException(
            status_code=500,
            detail={
                "code": "BRIEF_STATE_INCONSISTENT",
                "message": "Kayıtlı kategori verisi tutarsız durumda.",
            },
        )
    except Exception:
        logger.exception("Brief durum okuması sırasında beklenmeyen hata")
        raise HTTPException(
            status_code=500,
            detail={
                "code": "BRIEF_STATE_READ_FAILED",
                "message": "Brief durumu okunamadı.",
            },
        )

    return SocialBriefStateResponse(
        brief_id=state.brief_id,
        scoring_run_id=state.scoring_run_id,
        is_stale=state.is_stale,
        locked_at=state.locked_at,
        category_attempt=(
            _attempt_summary_to_response(state.category_attempt)
            if state.category_attempt
            else None
        ),
        categories=[
            SocialGeneratedCategoryResponse(
                id=c.id,
                category_name=c.category_name,
                category_type=c.category_type,
                description=c.description,
                relevance_score=c.relevance_score,
                suggested_keyword_ids=list(c.suggested_keyword_ids),
            )
            for c in state.categories
        ],
        ideas_attempt=(
            _attempt_summary_to_response(state.ideas_attempt) if state.ideas_attempt else None
        ),
        idea_retry_attempts=[_attempt_summary_to_response(a) for a in state.idea_retry_attempts],
        content_attempts=[_attempt_summary_to_response(a) for a in state.content_attempts],
        content_idea_ids=list(state.content_idea_ids),
    )


@router.get(
    "/social/contents/history",
    response_model=SocialContentHistoryResponse,
)
def get_social_content_history_endpoint(
    brand_profile_id: int = Query(..., gt=0, description="Workspace scope"),
    limit: int = Query(HISTORY_DEFAULT_LIMIT, ge=1, le=HISTORY_MAX_LIMIT),
    offset: int = Query(0, ge=0, le=HISTORY_MAX_OFFSET),
    brief_id: Optional[int] = Query(None, gt=0, description="Opsiyonel brief filtresi"),
    db: Session = Depends(get_db),
):
    """Workspace geneli sosyal içerik geçmişi (K13): en yeniden eskiye, sayfalı, salt-okunur.

    Brief'li ve legacy (brief'siz) içerikler birlikte listelenir; eskimiş kayıtlar
    gizlenmez, ``is_stale`` ile işaretlenir. Legacy içerikler de listelendiği için
    ENABLE_SOCIAL_BRIEF_FLOW bayrağına bağlı değildir.
    """
    try:
        page = load_social_content_history(
            db,
            brand_profile_id=brand_profile_id,
            limit=limit,
            offset=offset,
            brief_id=brief_id,
        )
    except SocialContentHistoryNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=f"Marka çalışması (id={brand_profile_id}) bulunamadı veya arşivlenmiş",
        )
    except SocialContentHistoryInputError:
        raise HTTPException(
            status_code=400,
            detail={"code": "HISTORY_INVALID_INPUT", "message": "Geçersiz sayfalama parametresi."},
        )
    except Exception:
        logger.exception("Sosyal içerik geçmişi okunurken beklenmeyen hata")
        raise HTTPException(
            status_code=500,
            detail={
                "code": "HISTORY_READ_FAILED",
                "message": "Sosyal içerik geçmişi okunamadı.",
            },
        )

    return SocialContentHistoryResponse(
        brand_profile_id=page.brand_profile_id,
        total=page.total,
        limit=page.limit,
        offset=page.offset,
        has_more=page.has_more,
        items=[
            SocialContentHistoryItemResponse(
                id=it.id,
                idea_id=it.idea_id,
                idea_title=it.idea_title,
                brief_id=it.brief_id,
                brief_is_stale=it.brief_is_stale,
                scoring_run_id=it.scoring_run_id,
                run_name=it.run_name,
                category_name=it.category_name,
                keyword=it.keyword,
                platform=it.platform,
                content_format=it.content_format,
                hooks=[SocialHistoryHookResponse(text=h.text, style=h.style) for h in it.hooks],
                caption=it.caption,
                scenario=it.scenario,
                format_payload=it.format_payload,
                visual_suggestion=it.visual_suggestion,
                video_concept=it.video_concept,
                cta_text=it.cta_text,
                hashtags=list(it.hashtags),
                industry_posting_suggestion=it.industry_posting_suggestion,
                platform_notes=it.platform_notes,
                duration_status=it.duration_status,
                actual_duration_sec=it.actual_duration_sec,
                duration_min_sec=it.duration_min_sec,
                duration_max_sec=it.duration_max_sec,
                validation_warnings=list(it.validation_warnings),
                is_stale=it.is_stale,
                created_at=it.created_at,
            )
            for it in page.items
        ],
    )


def persisted_categories_to_response(
    result: PersistedSocialCategoriesResult,
    *,
    attempt_status: str,
    ai_calls_used: Optional[int],
    replayed: bool,
) -> SocialCategoriesGenerateResponse:
    """ORM bağımsız, açık Pydantic serializer."""
    categories_resp = [
        SocialGeneratedCategoryResponse(
            id=cat.id,
            category_name=cat.category_name,
            category_type=cat.category_type,
            description=cat.description,
            relevance_score=cat.relevance_score,
            suggested_keyword_ids=list(cat.suggested_keyword_ids),
        )
        for cat in result.categories
    ]
    return SocialCategoriesGenerateResponse(
        brief_id=result.brief_id,
        scoring_run_id=result.scoring_run_id,
        attempt_id=result.attempt_id,
        attempt_status=attempt_status,
        total_categories=len(categories_resp),
        categories=categories_resp,
        ai_calls_used=ai_calls_used,
        replayed=replayed,
    )


def _finalize_failed_attempt(
    db: Session,
    *,
    attempt_id: int,
    reason_code: str,
    error_message: str,
) -> None:
    """Attempt'i canonical kilit sırasıyla finish_attempt üzerinden failed durumuna getirir.

    Canonical sıra: ScoringRun -> SocialBrief -> SocialGenerationAttempt.
    Doğrudan SocialGenerationAttempt.with_for_update() sorgusu yapmaz.
    Finalizasyon başarısız olursa rollback yapar ve HTTP 500
    (code="CATEGORY_FAILURE_FINALIZATION_FAILED") fırlatır.
    """
    try:
        finish_attempt(
            db,
            attempt_id=attempt_id,
            status="failed",
            task_id=None,
            reason_code=reason_code,
            error_message=error_message,
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "CATEGORY_FAILURE_FINALIZATION_FAILED",
                "message": "Attempt başarısızlık durumu kaydedilemedi.",
            },
        ) from exc


@router.post(
    "/social/briefs/{brief_id}/categories/generate",
    response_model=SocialCategoriesGenerateResponse,
    status_code=201,
)
def generate_social_categories_for_brief(
    brief_id: int,
    request: SocialBriefCategoriesGenerateRequest,
    response: Response,
    brand_profile_id: int = Query(..., description="Workspace scope"),
    db: Session = Depends(get_db),
    ai: AIService = Depends(get_ai),
):
    """Sosyal brief kategori üretimi endpoint'i (3 aşamalı işlem orkestrasyonu).

    Aşama 1: Preflight + brief lock + attempt oluştur -> COMMIT
    Aşama 2: AI çağrısı -> hiçbir DB transaction/row lock açık değil
    Aşama 3: Kategori persistence + attempt finalizasyonu -> COMMIT
    """
    if not getattr(settings, "ENABLE_SOCIAL_BRIEF_FLOW", False):
        raise HTTPException(
            status_code=404,
            detail={
                "code": "FEATURE_DISABLED",
                "message": "Social brief flow is not enabled.",
            },
        )

    # Eski motor run'ına bağlı brief salt-okunur: attempt/AI/Celery YOK
    require_non_legacy_brief(db, brief_id, brand_profile_id)

    # ==================== AŞAMA 1 — Preflight Transaction ====================
    try:
        start = begin_social_category_generation(
            db,
            brief_id=brief_id,
            brand_profile_id=brand_profile_id,
            request=request,
        )
        db.commit()
    except SocialBriefNotFoundError:
        db.rollback()
        raise HTTPException(
            status_code=404,
            detail={"code": "BRIEF_NOT_FOUND", "message": "Social brief not found."},
        )
    except SocialCategoryFlowError as e:
        db.rollback()
        if e.error_code == "BRIEF_NOT_FOUND":
            raise HTTPException(
                status_code=404,
                detail={"code": "BRIEF_NOT_FOUND", "message": "Social brief not found."},
            )
        if e.error_code == "CATEGORIES_ALREADY_GENERATED":
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "CATEGORIES_ALREADY_GENERATED",
                    "message": "Categories have already been generated for this brief.",
                },
            )
        raise HTTPException(
            status_code=409,
            detail={"code": e.error_code, "message": "Geçersiz kategori üretim isteği."},
        )
    except AttemptConflictError as e:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail={
                "code": e.error_code,
                "message": "Another category generation attempt is currently active for this brief.",
            },
        )
    except AttemptNotWritableError as e:
        db.rollback()
        safe_msg = "Attempt yazılamaz durumda."
        if e.error_code == "BRIEF_STALE":
            safe_msg = "Brief is stale; categories cannot be generated."
        elif e.error_code == "ASSIGNMENT_CHANGED":
            safe_msg = "Channel assignment version has changed; categories cannot be generated."
        raise HTTPException(
            status_code=409,
            detail={"code": e.error_code, "message": safe_msg},
        )
    except BriefNotFoundError:
        db.rollback()
        raise HTTPException(
            status_code=404,
            detail={"code": "BRIEF_NOT_FOUND", "message": "Social brief not found."},
        )
    except InvalidStageError as e:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail={"code": e.error_code, "message": "Geçersiz işlem aşaması."},
        )
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=safe_500_detail(e, "Kategori üretimi başlatılamadı"),
        )

    # ==================== REPLAY DALLARI ====================
    if not start.attempt_created:
        # a. Completed same-key replay -> 200
        if start.attempt_status == "completed":
            try:
                persisted = load_completed_social_categories(
                    db,
                    brief_id=start.brief_id,
                    attempt_id=start.attempt_id,
                    brand_profile_id=start.brand_profile_id,
                )
                attempt = (
                    db.query(SocialGenerationAttempt)
                    .filter(SocialGenerationAttempt.id == start.attempt_id)
                    .first()
                )
                coverage = attempt.coverage if (attempt and isinstance(attempt.coverage, dict)) else {}
                ai_calls_used = coverage.get("ai_calls_used")
                if (
                    isinstance(ai_calls_used, bool)
                    or not isinstance(ai_calls_used, int)
                    or ai_calls_used not in (1, 2)
                ):
                    raise HTTPException(
                        status_code=500,
                        detail={
                            "code": "CATEGORY_PERSISTENCE_INCONSISTENT",
                            "message": "Completed attempt coverage bilgisi eksik veya geçersiz.",
                        },
                    )

                response.status_code = status.HTTP_200_OK
                return persisted_categories_to_response(
                    persisted,
                    attempt_status="completed",
                    ai_calls_used=ai_calls_used,
                    replayed=True,
                )
            except SocialBriefNotFoundError:
                raise HTTPException(
                    status_code=404,
                    detail={"code": "BRIEF_NOT_FOUND", "message": "Social brief not found."},
                )
            except SocialCategoryPersistenceError as e:
                raise HTTPException(
                    status_code=500,
                    detail={
                        "code": e.error_code,
                        "message": "Kayıtlı kategori verisi tutarsız durumda.",
                    },
                )
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(
                    status_code=500,
                    detail=safe_500_detail(e, "Kayıtlı kategoriler okunamadı"),
                )

        # b. Pending / running same-key replay -> 202
        if start.attempt_status in ("pending", "running"):
            response.status_code = status.HTTP_202_ACCEPTED
            return SocialCategoriesGenerateResponse(
                brief_id=start.brief_id,
                scoring_run_id=start.scoring_run_id,
                attempt_id=start.attempt_id,
                attempt_status=start.attempt_status,
                total_categories=0,
                categories=[],
                ai_calls_used=None,
                replayed=True,
            )

        # c. Failed / partial same-key replay -> 409
        if start.attempt_status in ("failed", "partial"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "CATEGORY_ATTEMPT_TERMINAL",
                    "message": "Previous category generation attempt failed. Please retry with a new idempotency_key.",
                    "attempt_id": start.attempt_id,
                    "retryable": True,
                    "retry_with_new_idempotency_key": True,
                },
            )

        # Diğer beklenmeyen attempt durumları
        raise HTTPException(
            status_code=500,
            detail={
                "code": "CATEGORY_PERSISTENCE_INCONSISTENT",
                "message": f"Bilinmeyen attempt durumu: {start.attempt_status}",
            },
        )

    # ==================== AŞAMA 2 — AI Çağrısı (Transaction Dışında) ====================
    # db.in_transaction() kesinlikle False olmalı
    if db.in_transaction():
        db.rollback()
        _finalize_failed_attempt(
            db,
            attempt_id=start.attempt_id,
            reason_code="transaction_boundary_violation",
            error_message="Aşama 2 öncesinde beklenmeyen açık transaction tespit edildi.",
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "CATEGORY_TRANSACTION_BOUNDARY_VIOLATION",
                "message": "Aşama 2 öncesinde beklenmeyen açık transaction tespit edildi.",
            },
        )

    _scope_usage(
        ai,
        scoring_run_id=start.scoring_run_id,
        brand_profile_id=start.brand_profile_id,
    )

    generator = SocialBriefCategoryGenerator(ai)
    try:
        ai_result = generator.generate(start)
    except SocialCategoryGenerationError as e:
        ai_reason_map = {
            "CATEGORY_PROVIDER_ERROR": (
                "category_provider_error",
                502,
                "AI provider error occurred during category generation.",
            ),
            "CATEGORY_OUTPUT_INVALID": (
                "category_output_invalid",
                502,
                "AI output failed category validation.",
            ),
            "CATEGORY_GENERATION_NOT_ALLOWED": (
                "category_generation_not_allowed",
                409,
                "Category generation is not allowed for this state.",
            ),
        }
        reason_code, http_status, safe_msg = ai_reason_map.get(
            e.error_code,
            ("category_generation_error", 500, "Category generation failed."),
        )
        _finalize_failed_attempt(
            db,
            attempt_id=start.attempt_id,
            reason_code=reason_code,
            error_message=safe_msg,
        )
        raise HTTPException(
            status_code=http_status,
            detail={
                "code": e.error_code,
                "message": safe_msg,
                "attempt_id": start.attempt_id,
                "retryable": True,
                "retry_with_new_idempotency_key": True,
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        _finalize_failed_attempt(
            db,
            attempt_id=start.attempt_id,
            reason_code="category_generation_error",
            error_message="Unexpected error during category generation.",
        )
        raise HTTPException(
            status_code=500,
            detail=safe_500_detail(e, "Kategori üretimi sırasında beklenmeyen hata"),
        )

    # ==================== AŞAMA 3 — Persistence Transaction ====================
    try:
        persisted = persist_social_categories(
            db,
            start=start,
            ai_result=ai_result,
        )
        db.commit()
    except SocialCategoryPersistenceError as e:
        db.rollback()
        _finalize_failed_attempt(
            db,
            attempt_id=start.attempt_id,
            reason_code=e.error_code.lower(),
            error_message="Kategori persistence hatası.",
        )
        if e.error_code in (
            "BRIEF_STALE",
            "ASSIGNMENT_CHANGED",
            "WORKER_LOST",
            "CATEGORY_PERSISTENCE_CONFLICT",
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": e.error_code,
                    "message": "Kategori kaydedilemedi; veri durumu değişmiş.",
                    "attempt_id": start.attempt_id,
                    "retryable": True,
                    "retry_with_new_idempotency_key": True,
                },
            )
        raise HTTPException(
            status_code=500,
            detail={
                "code": e.error_code,
                "message": "Kategori verisi kalıcılaştırılamadı.",
            },
        )
    except AttemptNotWritableError as e:
        db.rollback()
        _finalize_failed_attempt(
            db,
            attempt_id=start.attempt_id,
            reason_code=e.error_code.lower(),
            error_message="Attempt yazılamaz durumda.",
        )
        raise HTTPException(
            status_code=409,
            detail={
                "code": e.error_code,
                "message": "Brief durumu değiştiği için kategoriler yazılamadı.",
                "attempt_id": start.attempt_id,
                "retryable": True,
                "retry_with_new_idempotency_key": True,
            },
        )
    except IntegrityError:
        db.rollback()
        _finalize_failed_attempt(
            db,
            attempt_id=start.attempt_id,
            reason_code="persistence_conflict",
            error_message="Veritabanı bütünlük kısıtı ihlali.",
        )
        raise HTTPException(
            status_code=409,
            detail={
                "code": "CATEGORY_PERSISTENCE_CONFLICT",
                "message": "Kategori çakışması oluştu.",
                "attempt_id": start.attempt_id,
                "retryable": True,
                "retry_with_new_idempotency_key": True,
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        _finalize_failed_attempt(
            db,
            attempt_id=start.attempt_id,
            reason_code="persistence_error",
            error_message="Beklenmeyen persistence hatası.",
        )
        raise HTTPException(
            status_code=500,
            detail=safe_500_detail(e, "Kategoriler kalıcılaştırılamadı"),
        )

    # Başarılı dönüş: HTTP 201
    response.status_code = status.HTTP_201_CREATED
    return persisted_categories_to_response(
        persisted,
        attempt_status="completed",
        ai_calls_used=ai_result.ai_calls_used,
        replayed=False,
    )


def social_idea_read_to_response(
    result: SocialIdeaReadResult,
) -> SocialIdeasGenerateResponse:
    """SocialIdeaReadResult DTO'sunu SocialIdeasGenerateResponse Pydantic yanıt şemasına dönüştürür."""
    ideas_resp = [
        SocialGeneratedIdeaResponse(
            id=i.id,
            category_id=i.category_id,
            keyword_id=i.keyword_id,
            brief_id=i.brief_id,
            brief_target_id=i.brief_target_id,
            idea_title=i.idea_title,
            idea_description=i.idea_description,
            target_platform=i.target_platform,
            content_format=i.content_format,
            trend_alignment=i.trend_alignment,
            is_stale=i.is_stale,
        )
        for i in result.ideas
    ]
    cov_resp = [
        SocialIdeaCoverageResponse(
            target_id=c.target_id,
            requested=c.requested,
            accepted=c.accepted,
            missing=c.missing,
        )
        for c in result.coverage
    ]
    warn_resp = [
        SocialIdeaWarningResponse(
            target_id=w.target_id,
            category_id=w.category_id,
            reason_code=w.reason_code,
        )
        for w in result.warnings
    ]
    return SocialIdeasGenerateResponse(
        brief_id=result.brief_id,
        scoring_run_id=result.scoring_run_id,
        attempt_id=result.attempt_id,
        attempt_status=result.attempt_status,
        total_ideas=result.total_ideas,
        ideas=ideas_resp,
        coverage=cov_resp,
        warnings=warn_resp,
        reason_code=result.reason_code,
        replayed=result.replayed,
    )


@router.post(
    "/social/briefs/{brief_id}/ideas/generate",
    response_model=SocialIdeasGenerateResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def generate_social_ideas_for_brief(
    brief_id: int,
    request: SocialBriefIdeasGenerateRequest,
    response: Response,
    brand_profile_id: int = Query(..., description="Workspace scope"),
    db: Session = Depends(get_db),
):
    """Sosyal brief fikir üretimi başlatma endpoint'i (F1-F.6d.2).

    Aşama 1: Feature flag kontrolü (kapalıysa 404).
    Aşama 2: Preflight transaction (begin_social_idea_generation -> commit).
    Aşama 3: Replay kontrolü (attempt_created == False ise Celery çağrısı yapılmaz).
      - completed: 200 OK + fikirler.
      - pending/running: 202 ACCEPTED.
      - failed/partial: 409 CONFLICT (IDEA_ATTEMPT_TERMINAL).
    Aşama 4: Yeni attempt Celery dispatch (apply_async, broker failure compensation).
    """
    if not getattr(settings, "ENABLE_SOCIAL_BRIEF_FLOW", False):
        raise HTTPException(
            status_code=404,
            detail={
                "code": "FEATURE_DISABLED",
                "message": "Social brief flow is not enabled.",
            },
        )

    # Eski motor run'ına bağlı brief salt-okunur: attempt/AI/Celery YOK
    require_non_legacy_brief(db, brief_id, brand_profile_id)

    # ==================== AŞAMA 2 — Preflight Transaction ====================
    try:
        start = begin_social_idea_generation(
            db,
            brief_id=brief_id,
            brand_profile_id=brand_profile_id,
            request=request,
        )
        db.commit()
    except SocialBriefNotFoundError:
        db.rollback()
        raise HTTPException(
            status_code=404,
            detail={"code": "BRIEF_NOT_FOUND", "message": "Social brief not found."},
        )
    except SocialIdeaFlowError as e:
        db.rollback()
        status_code_map = {
            "BRIEF_NOT_FOUND": (404, "Social brief not found."),
            "MIXED_BRIEF": (400, "Brief contains mixed brief items."),
            "CATEGORY_NOT_ELIGIBLE": (400, "Category is not eligible for idea generation."),
            "IDEA_PLAN_INVALID": (400, "Idea generation plan is invalid."),
            "IDEA_TARGETS_INVALID": (409, "Brief targets are invalid for idea generation."),
            "IDEA_KEYWORDS_INVALID": (409, "Brief keywords are invalid for idea generation."),
            "BRIEF_NOT_LOCKED": (409, "Social brief is not locked."),
            "BRIEF_STALE": (409, "Social brief is stale."),
            "ASSIGNMENT_CHANGED": (409, "Channel assignment version has changed."),
            "CATEGORIES_NOT_READY": (409, "Categories generation attempt is not completed."),
            "IDEAS_ALREADY_GENERATED": (409, "Ideas have already been generated for this brief."),
            "IDEA_ATTEMPT_SNAPSHOT_INVALID": (409, "Idea attempt plan snapshot is invalid."),
            "IDEA_ATTEMPT_REQUEST_MISMATCH": (409, "Idea attempt request snapshot does not match."),
        }
        http_status, default_msg = status_code_map.get(
            e.error_code, (409, "Fikir üretim kuralı ihlali.")
        )
        raise HTTPException(
            status_code=http_status,
            detail={"code": e.error_code, "message": default_msg},
        )
    except AttemptConflictError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "ATTEMPT_CONFLICT",
                "message": "Aynı brief için aktif bir attempt mevcut.",
            },
        )
    except AttemptNotWritableError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "ATTEMPT_NOT_WRITABLE",
                "message": "Attempt şu anda yazılamaz durumda.",
            },
        )
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "IDEA_ATTEMPT_CONFLICT",
                "message": "Veritabanı çakışması tespit edildi.",
            },
        )
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=safe_500_detail(e, "Fikir preflight işlemi sırasında beklenmeyen hata"),
        )

    # ==================== AŞAMA 3 — Replay Kontrolü ====================
    if not start.attempt_created:
        # a. Completed same-key replay -> 200
        if start.attempt_status == "completed":
            try:
                read_result = load_social_idea_result(
                    db,
                    brief_id=brief_id,
                    attempt_id=start.attempt_id,
                    brand_profile_id=brand_profile_id,
                    replayed=True,
                )
            except SocialIdeaReadNotFoundError:
                raise HTTPException(
                    status_code=404,
                    detail={"code": "BRIEF_NOT_FOUND", "message": "Social brief or attempt not found."},
                )
            except SocialIdeaReadError:
                raise HTTPException(
                    status_code=500,
                    detail={"code": "IDEA_READ_INCONSISTENT", "message": "Fikir okuma verisi tutarsız."},
                )
            response.status_code = status.HTTP_200_OK
            return social_idea_read_to_response(read_result)

        # b. Pending / running same-key replay -> 202
        if start.attempt_status in ("pending", "running"):
            try:
                read_result = load_social_idea_result(
                    db,
                    brief_id=brief_id,
                    attempt_id=start.attempt_id,
                    brand_profile_id=brand_profile_id,
                    replayed=True,
                )
            except SocialIdeaReadNotFoundError:
                raise HTTPException(
                    status_code=404,
                    detail={"code": "BRIEF_NOT_FOUND", "message": "Social brief or attempt not found."},
                )
            except SocialIdeaReadError:
                raise HTTPException(
                    status_code=500,
                    detail={"code": "IDEA_READ_INCONSISTENT", "message": "Fikir okuma verisi tutarsız."},
                )
            response.status_code = status.HTTP_202_ACCEPTED
            return social_idea_read_to_response(read_result)

        # c. Failed / partial same-key replay -> 409
        if start.attempt_status in ("failed", "partial"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "IDEA_ATTEMPT_TERMINAL",
                    "message": "Previous idea generation attempt is terminal. Retry with a new idempotency_key.",
                    "attempt_id": start.attempt_id,
                    "attempt_status": start.attempt_status,
                    "retryable": True,
                    "retry_with_new_idempotency_key": True,
                },
            )

        # Diğer beklenmeyen durumlar
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "IDEA_READ_INCONSISTENT",
                "message": f"Bilinmeyen attempt durumu: {start.attempt_status}",
            },
        )

    # ==================== AŞAMA 4 — Yeni Attempt Celery Dispatch ====================
    task_id = str(uuid.uuid4())
    try:
        social_brief_ideas_task.apply_async(
            args=[start.attempt_id],
            task_id=task_id,
        )
    except Exception:
        # Broker Enqueue Failure Compensation
        try:
            finalize_ideas_attempt_failure(
                db,
                attempt_id=start.attempt_id,
                task_id=task_id,
                reason_code="dispatch_failed",
                error_message="Fikir üretim görevi kuyruğa alınamadı.",
            )
            db.commit()
        except Exception:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "code": "IDEA_DISPATCH_FINALIZATION_FAILED",
                    "message": "Görev kuyruğa alınamadı ve attempt durumu güvenli biçimde kapatılamadı.",
                },
            )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "IDEA_DISPATCH_FAILED",
                "message": "Fikir üretim görevi kuyruğa alınamadı. Lütfen tekrar deneyin.",
                "attempt_id": start.attempt_id,
            },
        )

    # Enqueue başarılı: pending sonucunu oku ve 202 dön
    response.status_code = status.HTTP_202_ACCEPTED
    try:
        read_result = load_social_idea_result(
            db,
            brief_id=brief_id,
            attempt_id=start.attempt_id,
            brand_profile_id=brand_profile_id,
            replayed=False,
        )
        return social_idea_read_to_response(read_result)
    except Exception as e:
        # Worker kuyrukta veya çalışıyor olabilir; attempt'i dispatch_failed yapma!
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=safe_500_detail(e, "Görev kuyruğa alındı ancak başlangıç yanıtı okunamadı"),
        )


@router.get(
    "/social/briefs/{brief_id}/ideas/attempts/{attempt_id}",
    response_model=SocialIdeasGenerateResponse,
)
def get_social_ideas_attempt(
    brief_id: int,
    attempt_id: int,
    brand_profile_id: int = Query(..., description="Workspace scope"),
    db: Session = Depends(get_db),
):
    """Fikir üretim attempt durumunu ve varsa üretilen fikirleri döndürür (F1-F.6d.3).

    Aşama A: Süresi dolmuş aktif attempt'lerin reconciliation'ı (ScoringRun -> Brief -> Attempt kilidi).
    Aşama B: Salt-okunur DTO projeksiyonu (load_social_idea_result).
    """
    if not getattr(settings, "ENABLE_SOCIAL_BRIEF_FLOW", False):
        raise HTTPException(
            status_code=404,
            detail={
                "code": "FEATURE_DISABLED",
                "message": "Social brief flow is not enabled.",
            },
        )

    if (
        isinstance(brief_id, bool)
        or not isinstance(brief_id, int)
        or brief_id <= 0
        or isinstance(attempt_id, bool)
        or not isinstance(attempt_id, int)
        or attempt_id <= 0
        or isinstance(brand_profile_id, bool)
        or not isinstance(brand_profile_id, int)
        or brand_profile_id <= 0
    ):
        raise HTTPException(
            status_code=400,
            detail={"code": "IDEA_ATTEMPT_INVALID_INPUT", "message": "Geçersiz kimlik parametresi."},
        )

    # Aşama A — Reconciliation
    try:
        reconcile_expired_ideas_attempt_for_read(
            db,
            brief_id=brief_id,
            attempt_id=attempt_id,
            brand_profile_id=brand_profile_id,
        )
        db.commit()
    except (AttemptNotFoundError, BriefNotFoundError):
        db.rollback()
        raise HTTPException(
            status_code=404,
            detail={"code": "IDEA_ATTEMPT_NOT_FOUND", "message": "Idea generation attempt not found."},
        )
    except AttemptNotWritableError:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail={"code": "IDEA_READ_INCONSISTENT", "message": "Idea generation result is inconsistent."},
        )
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=safe_500_detail(e, "Attempt durumu senkronize edilemedi"),
        )

    # Aşama B — Salt-okunur sonuç
    try:
        result = load_social_idea_result(
            db,
            brief_id=brief_id,
            attempt_id=attempt_id,
            brand_profile_id=brand_profile_id,
            replayed=True,
        )
        return social_idea_read_to_response(result)
    except SocialIdeaReadNotFoundError:
        raise HTTPException(
            status_code=404,
            detail={"code": "IDEA_ATTEMPT_NOT_FOUND", "message": "Idea generation attempt not found."},
        )
    except SocialIdeaReadError:
        raise HTTPException(
            status_code=500,
            detail={"code": "IDEA_READ_INCONSISTENT", "message": "Idea generation result is inconsistent."},
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=safe_500_detail(e, "Fikir okuma işlemi sırasında beklenmeyen hata"),
        )


@router.post(
    "/social/briefs/{brief_id}/ideas/retry",
    response_model=SocialIdeasGenerateResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def retry_social_ideas_for_brief(
    brief_id: int,
    request: SocialBriefIdeasRetryRequest,
    response: Response,
    brand_profile_id: int = Query(..., description="Workspace scope"),
    db: Session = Depends(get_db),
):
    """Sosyal brief fikir tekrar deneme (ideas_retry) başlatma endpoint'i (F1-F.7.8).

    Aşama 1: Feature flag kontrolü (kapalıysa 404).
    Aşama 2: Preflight transaction (begin_social_idea_retry -> commit).
    Aşama 3: Replay kontrolü (attempt_created == False ise Celery çağrısı yapılmaz).
      - completed: 200 OK + fikirler.
      - pending/running: 202 ACCEPTED.
      - failed/partial: 409 CONFLICT (IDEA_RETRY_ATTEMPT_TERMINAL).
    Aşama 4: Yeni attempt Celery dispatch (apply_async, broker failure compensation).
    """
    if not getattr(settings, "ENABLE_SOCIAL_BRIEF_FLOW", False):
        raise HTTPException(
            status_code=404,
            detail={
                "code": "FEATURE_DISABLED",
                "message": "Social brief flow is not enabled.",
            },
        )

    # Eski motor run'ına bağlı brief salt-okunur: attempt/AI/Celery YOK
    require_non_legacy_brief(db, brief_id, brand_profile_id)

    # ==================== AŞAMA 2 — Preflight Transaction ====================
    try:
        start = begin_social_idea_retry(
            db,
            brief_id=brief_id,
            brand_profile_id=brand_profile_id,
            request=request,
        )
        db.commit()
    except SocialBriefNotFoundError:
        db.rollback()
        raise HTTPException(
            status_code=404,
            detail={"code": "BRIEF_NOT_FOUND", "message": "Social brief not found."},
        )
    except SocialIdeaRetryFlowError as e:
        db.rollback()
        status_code_map = {
            "BRIEF_NOT_FOUND": (404, "Social brief not found."),
            "SOURCE_ATTEMPT_NOT_FOUND": (404, "Kaynak fikir attempt bulunamadı."),
            "IDEA_RETRY_SOURCE_NOT_FOUND": (404, "Kaynak fikir attempt bulunamadı."),
            "SOURCE_ATTEMPT_RUNNING": (409, "Kaynak attempt henüz tamamlanmadı."),
            "IDEA_RETRY_SOURCE_NOT_TERMINAL": (409, "Kaynak attempt henüz tamamlanmadı."),
            "SOURCE_ATTEMPT_STAGE_INVALID": (409, "Kaynak attempt stage geçersiz."),
            "BRIEF_NOT_LOCKED": (409, "Social brief is not locked."),
            "BRIEF_STALE": (409, "Social brief is stale."),
            "ASSIGNMENT_CHANGED": (409, "Channel assignment version has changed."),
            "IDEA_RETRY_NOT_NEEDED": (409, "Bütün hedefler ve kategoriler zaten dolu, tekrar deneme gerekmiyor."),
            "IDEA_RETRY_SNAPSHOT_CORRUPTED": (409, "Idea retry attempt snapshot is corrupted."),
            "IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID": (409, "Idea retry attempt snapshot is invalid."),
            "IDEA_RETRY_ATTEMPT_REQUEST_MISMATCH": (409, "Idea retry attempt request snapshot does not match."),
            "IDEA_RETRY_PLAN_INVALID": (400, "Idea retry plan is invalid."),
        }
        http_status, default_msg = status_code_map.get(
            e.error_code, (409, "Fikir tekrar deneme kuralı ihlali.")
        )
        raise HTTPException(
            status_code=http_status,
            detail={"code": e.error_code, "message": default_msg},
        )
    except AttemptConflictError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "ATTEMPT_CONFLICT",
                "message": "Aynı brief için aktif bir attempt mevcut.",
            },
        )
    except AttemptNotWritableError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "ATTEMPT_NOT_WRITABLE",
                "message": "Attempt şu anda yazılamaz durumda.",
            },
        )
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "IDEA_ATTEMPT_CONFLICT",
                "message": "Veritabanı çakışması tespit edildi.",
            },
        )
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=safe_500_detail(e, "Fikir retry preflight işlemi sırasında beklenmeyen hata"),
        )

    # ==================== AŞAMA 3 — Replay Kontrolü ====================
    if not start.attempt_created:
        # a. Completed same-key replay -> 200
        if start.attempt_status == "completed":
            try:
                read_result = load_social_idea_retry_result(
                    db,
                    brief_id=brief_id,
                    attempt_id=start.attempt_id,
                    brand_profile_id=brand_profile_id,
                    replayed=True,
                )
            except SocialIdeaReadNotFoundError:
                raise HTTPException(
                    status_code=404,
                    detail={"code": "BRIEF_NOT_FOUND", "message": "Social brief or attempt not found."},
                )
            except SocialIdeaReadError:
                raise HTTPException(
                    status_code=500,
                    detail={"code": "IDEA_READ_INCONSISTENT", "message": "Fikir okuma verisi tutarsız."},
                )
            response.status_code = status.HTTP_200_OK
            return social_idea_read_to_response(read_result)

        # b. Pending / running same-key replay -> 202
        if start.attempt_status in ("pending", "running"):
            try:
                read_result = load_social_idea_retry_result(
                    db,
                    brief_id=brief_id,
                    attempt_id=start.attempt_id,
                    brand_profile_id=brand_profile_id,
                    replayed=True,
                )
            except SocialIdeaReadNotFoundError:
                raise HTTPException(
                    status_code=404,
                    detail={"code": "BRIEF_NOT_FOUND", "message": "Social brief or attempt not found."},
                )
            except SocialIdeaReadError:
                raise HTTPException(
                    status_code=500,
                    detail={"code": "IDEA_READ_INCONSISTENT", "message": "Fikir okuma verisi tutarsız."},
                )
            response.status_code = status.HTTP_202_ACCEPTED
            return social_idea_read_to_response(read_result)

        # c. Failed / partial same-key replay -> 409
        if start.attempt_status in ("failed", "partial"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "IDEA_RETRY_ATTEMPT_TERMINAL",
                    "message": "Previous idea retry attempt is terminal. Retry with a new idempotency_key.",
                    "attempt_id": start.attempt_id,
                    "attempt_status": start.attempt_status,
                    "retryable": True,
                    "retry_with_new_idempotency_key": True,
                },
            )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "IDEA_READ_INCONSISTENT",
                "message": f"Bilinmeyen attempt durumu: {start.attempt_status}",
            },
        )

    # ==================== AŞAMA 4 — Yeni Attempt Celery Dispatch ====================
    task_id = str(uuid.uuid4())
    try:
        social_brief_ideas_retry_task.apply_async(
            args=[start.attempt_id],
            task_id=task_id,
        )
    except Exception:
        # Broker Enqueue Failure Compensation
        try:
            finalize_ideas_retry_attempt_failure(
                db,
                attempt_id=start.attempt_id,
                task_id=task_id,
                reason_code="dispatch_failed",
                error_message="Fikir tekrar deneme görevi kuyruğa alınamadı.",
            )
            db.commit()
        except Exception:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "code": "IDEA_RETRY_DISPATCH_FINALIZATION_FAILED",
                    "message": "Görev kuyruğa alınamadı ve attempt durumu güvenli biçimde kapatılamadı.",
                },
            )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "IDEA_RETRY_DISPATCH_FAILED",
                "message": "Fikir tekrar deneme görevi kuyruğa alınamadı. Lütfen tekrar deneyin.",
                "attempt_id": start.attempt_id,
            },
        )

    response.status_code = status.HTTP_202_ACCEPTED
    try:
        read_result = load_social_idea_retry_result(
            db,
            brief_id=brief_id,
            attempt_id=start.attempt_id,
            brand_profile_id=brand_profile_id,
            replayed=False,
        )
        return social_idea_read_to_response(read_result)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=safe_500_detail(e, "Görev kuyruğa alındı ancak başlangıç yanıtı okunamadı"),
        )


@router.get(
    "/social/briefs/{brief_id}/ideas/retry/attempts/{attempt_id}",
    response_model=SocialIdeasGenerateResponse,
)
def get_social_ideas_retry_attempt(
    brief_id: int,
    attempt_id: int,
    brand_profile_id: int = Query(..., description="Workspace scope"),
    db: Session = Depends(get_db),
):
    """Fikir tekrar deneme attempt durumunu ve üretilen fikirleri döndürür (F1-F.7.8).

    Aşama A: Süresi dolmuş aktif attempt'lerin reconciliation'ı (ScoringRun -> Brief -> Attempt kilidi).
    Aşama B: Salt-okunur DTO projeksiyonu (load_social_idea_retry_result).
    """
    if not getattr(settings, "ENABLE_SOCIAL_BRIEF_FLOW", False):
        raise HTTPException(
            status_code=404,
            detail={
                "code": "FEATURE_DISABLED",
                "message": "Social brief flow is not enabled.",
            },
        )

    if (
        isinstance(brief_id, bool)
        or not isinstance(brief_id, int)
        or brief_id <= 0
        or isinstance(attempt_id, bool)
        or not isinstance(attempt_id, int)
        or attempt_id <= 0
        or isinstance(brand_profile_id, bool)
        or not isinstance(brand_profile_id, int)
        or brand_profile_id <= 0
    ):
        raise HTTPException(
            status_code=400,
            detail={"code": "IDEA_ATTEMPT_INVALID_INPUT", "message": "Geçersiz kimlik parametresi."},
        )

    # Aşama A — Reconciliation
    try:
        reconcile_expired_ideas_retry_attempt_for_read(
            db,
            brief_id=brief_id,
            attempt_id=attempt_id,
            brand_profile_id=brand_profile_id,
        )
        db.commit()
    except (AttemptNotFoundError, BriefNotFoundError):
        db.rollback()
        raise HTTPException(
            status_code=404,
            detail={"code": "IDEA_ATTEMPT_NOT_FOUND", "message": "Idea retry attempt not found."},
        )
    except AttemptNotWritableError:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail={"code": "IDEA_READ_INCONSISTENT", "message": "Idea retry result is inconsistent."},
        )
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=safe_500_detail(e, "Attempt durumu senkronize edilemedi"),
        )

    # Aşama B — Salt-okunur sonuç
    try:
        result = load_social_idea_retry_result(
            db,
            brief_id=brief_id,
            attempt_id=attempt_id,
            brand_profile_id=brand_profile_id,
            replayed=True,
        )
        return social_idea_read_to_response(result)
    except SocialIdeaReadNotFoundError:
        raise HTTPException(
            status_code=404,
            detail={"code": "IDEA_ATTEMPT_NOT_FOUND", "message": "Idea retry attempt not found."},
        )
    except SocialIdeaReadError:
        raise HTTPException(
            status_code=500,
            detail={"code": "IDEA_READ_INCONSISTENT", "message": "Idea retry result is inconsistent."},
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=safe_500_detail(e, "Fikir okuma işlemi sırasında beklenmeyen hata"),
        )


class SocialContentSerializationError(ValueError):
    """Sosyal içerik serileştirme hatası (fail-closed, sızıntısız)."""
    pass


def social_content_read_to_response(
    result: SocialContentAttemptReadResult,
) -> SocialBriefContentsAttemptResponse:
    """SocialContentAttemptReadResult DTO'sunu SocialBriefContentsAttemptResponse Pydantic yanıt şemasına dönüştürür."""
    contents_resp: list[SocialGeneratedContentItemResponse] = []
    for c in result.contents:
        hooks_data: list[SocialContentHookResponse] = []
        for h in c.hooks:
            if not isinstance(h, ValidatedHook):
                raise SocialContentSerializationError(
                    "Bozuk veya tanınmayan hook nesnesi."
                )

            ab_val: float | None = None
            if h.ab_score is not None:
                if (
                    isinstance(h.ab_score, bool)
                    or not isinstance(h.ab_score, (int, float))
                    or not math.isfinite(h.ab_score)
                    or not (0.0 <= float(h.ab_score) <= 1.0)
                ):
                    raise SocialContentSerializationError("Geçersiz hook A/B skoru.")
                ab_val = float(h.ab_score)

            try:
                hook_item = SocialContentHookResponse(
                    text=h.text,
                    style=h.style,
                    ab_score=ab_val,
                )
            except Exception:
                raise SocialContentSerializationError("Hook doğrulama başarısız.")
            hooks_data.append(hook_item)

        if c.format_payload is None:
            serialized_payload = None
        elif isinstance(
            c.format_payload,
            (ValidatedVideoPayload, ValidatedCarouselPayload, ValidatedThreadPayload),
        ):
            try:
                serialized_payload = serialize_content_format_payload(c.format_payload)
            except Exception:
                raise SocialContentSerializationError("Format payload serileştirme başarısız.")
        else:
            raise SocialContentSerializationError("Bozuk veya tanınmayan format payload nesnesi.")

        try:
            content_item = SocialGeneratedContentItemResponse(
                id=c.id,
                idea_id=c.idea_id,
                brief_id=c.brief_id,
                target_id=c.target_id,
                platform=c.platform,
                content_format=c.content_format,
                hooks=hooks_data,
                caption=c.caption,
                scenario=c.scenario,
                format_payload=serialized_payload,
                visual_suggestion=c.visual_suggestion,
                video_concept=c.video_concept,
                cta_text=c.cta_text,
                hashtags=list(c.hashtags),
                industry_posting_suggestion=c.industry_posting_suggestion,
                platform_notes=c.platform_notes,
                duration_status=c.duration_status,
                actual_duration_sec=c.actual_duration_sec,
                validation_warnings=list(c.validation_warnings),
                is_stale=c.is_stale,
            )
        except Exception:
            raise SocialContentSerializationError("İçerik öğesi yanıt modeli oluşturma başarısız.")
        contents_resp.append(content_item)

    try:
        warnings_resp = [
            SocialContentWarningResponse(
                idea_id=w.idea_id,
                reason_code=w.reason_code,
                claims=list(w.claims),
                ai_calls_used=w.ai_calls_used,
            )
            for w in result.warnings
        ]
    except Exception:
        raise SocialContentSerializationError("Uyarı öğesi yanıt modeli oluşturma başarısız.")

    try:
        return SocialBriefContentsAttemptResponse(
            brief_id=result.brief_id,
            scoring_run_id=result.scoring_run_id,
            attempt_id=result.attempt_id,
            attempt_status=result.attempt_status,
            requested_idea_ids=list(result.requested_idea_ids),
            successful_idea_ids=list(result.successful_idea_ids),
            unresolved_idea_ids=list(result.unresolved_idea_ids),
            total_contents=len(contents_resp),
            contents=contents_resp,
            warnings=warnings_resp,
            reason_code=result.reason_code,
            replayed=result.replayed,
        )
    except Exception:
        raise SocialContentSerializationError("Attempt yanıt modeli oluşturma başarısız.")


@router.post(
    "/social/briefs/{brief_id}/contents/async",
    response_model=SocialBriefContentsAttemptResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def generate_social_brief_contents_async(
    brief_id: int,
    request: SocialBriefContentsGenerateRequest,
    response: Response,
    brand_profile_id: int = Query(..., description="Workspace scope"),
    db: Session = Depends(get_db),
):
    """Sosyal brief içerik üretimi asenkron başlatma ve replay endpoint'i (F1-G.5.7.3).

    Aşama 1: Feature flag ve kimlik parametre doğrulaması.
    Aşama 2: Preflight ve idempotent attempt oluşturma / okuma (begin_social_content_generation).
    Aşama 3: Replay kontrolü:
        - completed -> 200 OK
        - pending/running -> 202 ACCEPTED
        - partial/failed -> 409 CONFLICT (CONTENT_ATTEMPT_TERMINAL)
    Aşama 4: Yeni attempt için Celery task dispatch (social_brief_contents_task.apply_async).
        - Broker enqueue hatasında attempt failed/dispatch_failed yapılır -> 503 SERVICE UNAVAILABLE.
        - Başarılı dispatch durumunda pending sonucu dönülür -> 202 ACCEPTED.
    """
    if not getattr(settings, "ENABLE_SOCIAL_BRIEF_FLOW", False):
        raise HTTPException(
            status_code=404,
            detail={
                "code": "FEATURE_DISABLED",
                "message": "Social brief flow is not enabled.",
            },
        )

    if (
        isinstance(brief_id, bool)
        or not isinstance(brief_id, int)
        or brief_id <= 0
        or isinstance(brand_profile_id, bool)
        or not isinstance(brand_profile_id, int)
        or brand_profile_id <= 0
    ):
        raise HTTPException(
            status_code=400,
            detail={"code": "CONTENT_ATTEMPT_INVALID_INPUT", "message": "Geçersiz kimlik parametresi."},
        )

    # Eski motor run'ına bağlı brief salt-okunur: attempt/AI/Celery YOK
    require_non_legacy_brief(db, brief_id, brand_profile_id)

    # ==================== AŞAMA 2 — Preflight ve Attempt ====================
    try:
        start = begin_social_content_generation(
            db,
            brief_id=brief_id,
            brand_profile_id=brand_profile_id,
            request=request,
        )
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except SocialBriefNotFoundError:
        db.rollback()
        raise HTTPException(
            status_code=404,
            detail={"code": "BRIEF_NOT_FOUND", "message": "Social brief not found."},
        )
    except SocialContentFlowError as e:
        db.rollback()
        status_code_map = {
            "CONTENT_INVALID_INPUT": (400, "Geçersiz içerik üretim isteği."),
            "CONTENT_BRIEF_NOT_FOUND": (404, "Social brief not found."),
            "CONTENT_BRIEF_NOT_LOCKED": (409, "Social brief is not locked."),
            "CONTENT_BRIEF_STALE": (409, "Social brief is stale."),
            "CONTENT_ASSIGNMENT_CHANGED": (409, "Channel assignment version has changed."),
            "CONTENT_IDEAS_INVALID": (400, "Geçersiz fikir seçimi."),
            "CONTENT_IDEA_NOT_ELIGIBLE": (400, "Fikir içerik üretimine uygun değil."),
            "CONTENT_GROUNDING_INVALID": (400, "Grounding girdisi geçersiz."),
            "CONTENT_ATTEMPT_SNAPSHOT_INVALID": (409, "İçerik attempt snapshot geçersiz."),
            "CONTENT_ATTEMPT_REQUEST_MISMATCH": (409, "İçerik attempt istek parametreleri uyuşmuyor."),
        }
        http_status, default_msg = status_code_map.get(
            e.error_code, (409, "İçerik üretim kuralı ihlali.")
        )
        raise HTTPException(
            status_code=http_status,
            detail={"code": e.error_code, "message": default_msg},
        )
    except AttemptConflictError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "ATTEMPT_CONFLICT",
                "message": "Aynı brief için aktif bir attempt mevcut.",
            },
        )
    except AttemptNotWritableError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "ATTEMPT_NOT_WRITABLE",
                "message": "Attempt şu anda yazılamaz durumda.",
            },
        )
    except IntegrityError:
        db.rollback()
        logger.exception("İçerik attempt preflight sırasında veritabanı kısıt ihlali")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "CONTENT_ATTEMPT_CONFLICT",
                "message": "Veritabanı çakışması tespit edildi.",
            },
        )
    except Exception:
        db.rollback()
        logger.exception("İçerik preflight işlemi sırasında beklenmeyen hata")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "CONTENT_PREFLIGHT_FAILED",
                "message": "İçerik preflight işlemi sırasında beklenmeyen hata oluştu.",
            },
        )

    # ==================== AŞAMA 3 — Replay Kontrolü ====================
    if not start.attempt_created:
        # a. Completed same-key replay -> 200
        if start.attempt_status == "completed":
            try:
                read_result = load_social_content_result(
                    db,
                    brief_id=brief_id,
                    attempt_id=start.attempt_id,
                    brand_profile_id=brand_profile_id,
                    replayed=True,
                )
                response.status_code = status.HTTP_200_OK
                return social_content_read_to_response(read_result)
            except HTTPException:
                raise
            except SocialContentReadNotFoundError:
                raise HTTPException(
                    status_code=404,
                    detail={"code": "CONTENT_ATTEMPT_NOT_FOUND", "message": "Content attempt not found."},
                )
            except (SocialContentReadError, SocialContentSerializationError):
                logger.exception("Replay edilen tamamlanmış içerik okuma/serileştirme verisi tutarsız")
                raise HTTPException(
                    status_code=500,
                    detail={"code": "CONTENT_READ_INCONSISTENT", "message": "İçerik okuma verisi tutarsız."},
                )
            except Exception:
                logger.exception("Replay edilen tamamlanmış içerik okuma sırasında beklenmeyen hata")
                raise HTTPException(
                    status_code=500,
                    detail={"code": "CONTENT_READ_INCONSISTENT", "message": "İçerik okuma verisi tutarsız."},
                )

        # b. Pending / running same-key replay -> 202
        if start.attempt_status in ("pending", "running"):
            try:
                read_result = load_social_content_result(
                    db,
                    brief_id=brief_id,
                    attempt_id=start.attempt_id,
                    brand_profile_id=brand_profile_id,
                    replayed=True,
                )
                response.status_code = status.HTTP_202_ACCEPTED
                return social_content_read_to_response(read_result)
            except HTTPException:
                raise
            except SocialContentReadNotFoundError:
                raise HTTPException(
                    status_code=404,
                    detail={"code": "CONTENT_ATTEMPT_NOT_FOUND", "message": "Content attempt not found."},
                )
            except (SocialContentReadError, SocialContentSerializationError):
                logger.exception("Replay edilen aktif içerik okuma/serileştirme verisi tutarsız")
                raise HTTPException(
                    status_code=500,
                    detail={"code": "CONTENT_READ_INCONSISTENT", "message": "İçerik okuma verisi tutarsız."},
                )
            except Exception:
                logger.exception("Replay edilen aktif içerik okuma sırasında beklenmeyen hata")
                raise HTTPException(
                    status_code=500,
                    detail={"code": "CONTENT_READ_INCONSISTENT", "message": "İçerik okuma verisi tutarsız."},
                )

        # c. Failed / partial same-key replay -> 409
        if start.attempt_status in ("failed", "partial"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "CONTENT_ATTEMPT_TERMINAL",
                    "message": "Previous content generation attempt is terminal. Retry with a new idempotency_key.",
                    "attempt_id": start.attempt_id,
                    "attempt_status": start.attempt_status,
                    "retryable": True,
                    "retry_with_new_idempotency_key": True,
                },
            )

        # Diğer beklenmeyen veya tanınmayan durumlar
        logger.error("Bilinmeyen attempt durumu tespit edildi")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "CONTENT_READ_INCONSISTENT",
                "message": "İçerik deneme durumu tutarsız veya tanınmıyor.",
            },
        )

    # ==================== AŞAMA 4 — Yeni Attempt Celery Dispatch ====================
    task_id = str(uuid.uuid4())
    try:
        social_brief_contents_task.apply_async(
            args=[start.attempt_id],
            task_id=task_id,
        )
    except Exception:
        # Broker Enqueue Failure Compensation
        try:
            finalize_contents_attempt_failure(
                db,
                attempt_id=start.attempt_id,
                task_id=task_id,
                reason_code="dispatch_failed",
                error_message="Sosyal içerik üretim görevi kuyruğa alınamadı.",
            )
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("Görev kuyruğa alınamadı ve attempt durumu güvenli biçimde kapatılamadı")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "code": "CONTENT_DISPATCH_FINALIZATION_FAILED",
                    "message": "Görev kuyruğa alınamadı ve attempt durumu güvenli biçimde kapatılamadı.",
                },
            )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "CONTENT_DISPATCH_FAILED",
                "message": "Sosyal içerik üretim görevi kuyruğa alınamadı. Lütfen tekrar deneyin.",
                "attempt_id": start.attempt_id,
            },
        )

    # Enqueue başarılı: pending sonucunu oku ve 202 dön
    response.status_code = status.HTTP_202_ACCEPTED
    try:
        read_result = load_social_content_result(
            db,
            brief_id=brief_id,
            attempt_id=start.attempt_id,
            brand_profile_id=brand_profile_id,
            replayed=False,
        )
        return social_content_read_to_response(read_result)
    except HTTPException:
        raise
    except Exception:
        # Worker kuyrukta veya çalışıyor olabilir; attempt'i dispatch_failed yapma!
        logger.exception("Görev kuyruğa alındı ancak başlangıç yanıtı okunamadı")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "CONTENT_POST_DISPATCH_READ_FAILED",
                "message": "Görev kuyruğa alındı ancak başlangıç yanıtı okunamadı.",
            },
        )


@router.get(
    "/social/briefs/{brief_id}/contents/attempts/{attempt_id}",
    response_model=SocialBriefContentsAttemptResponse,
)
def get_social_brief_contents_attempt(
    brief_id: int,
    attempt_id: int,
    brand_profile_id: int = Query(..., description="Workspace scope"),
    db: Session = Depends(get_db),
):
    """İçerik üretim attempt durumunu ve varsa üretilen içerikleri döndürür (F1-G.5.7.3).

    Aşama A: Süresi dolmuş aktif attempt'lerin reconciliation'ı (ScoringRun -> Brief -> Attempt kilidi).
    Aşama B: Salt-okunur DTO projeksiyonu (load_social_content_result).
    """
    if not getattr(settings, "ENABLE_SOCIAL_BRIEF_FLOW", False):
        raise HTTPException(
            status_code=404,
            detail={
                "code": "FEATURE_DISABLED",
                "message": "Social brief flow is not enabled.",
            },
        )

    if (
        isinstance(brief_id, bool)
        or not isinstance(brief_id, int)
        or brief_id <= 0
        or isinstance(attempt_id, bool)
        or not isinstance(attempt_id, int)
        or attempt_id <= 0
        or isinstance(brand_profile_id, bool)
        or not isinstance(brand_profile_id, int)
        or brand_profile_id <= 0
    ):
        raise HTTPException(
            status_code=400,
            detail={"code": "CONTENT_ATTEMPT_INVALID_INPUT", "message": "Geçersiz kimlik parametresi."},
        )

    # Aşama A — Reconciliation
    try:
        reconcile_expired_contents_attempt_for_read(
            db,
            brief_id=brief_id,
            attempt_id=attempt_id,
            brand_profile_id=brand_profile_id,
        )
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except (AttemptNotFoundError, BriefNotFoundError):
        db.rollback()
        raise HTTPException(
            status_code=404,
            detail={"code": "CONTENT_ATTEMPT_NOT_FOUND", "message": "Content generation attempt not found."},
        )
    except AttemptNotWritableError:
        db.rollback()
        logger.exception("Reconciliation sırasında attempt yazılamaz durumda")
        raise HTTPException(
            status_code=500,
            detail={"code": "CONTENT_READ_INCONSISTENT", "message": "Content generation result is inconsistent."},
        )
    except Exception:
        db.rollback()
        logger.exception("Attempt durumu senkronize edilirken beklenmeyen hata")
        raise HTTPException(
            status_code=500,
            detail={
                "code": "CONTENT_RECONCILIATION_FAILED",
                "message": "Attempt durumu senkronize edilirken beklenmeyen hata oluştu.",
            },
        )

    # Aşama B — Salt-okunur sonuç
    try:
        result = load_social_content_result(
            db,
            brief_id=brief_id,
            attempt_id=attempt_id,
            brand_profile_id=brand_profile_id,
            replayed=True,
        )
        return social_content_read_to_response(result)
    except HTTPException:
        raise
    except SocialContentReadNotFoundError:
        raise HTTPException(
            status_code=404,
            detail={"code": "CONTENT_ATTEMPT_NOT_FOUND", "message": "Content generation attempt not found."},
        )
    except (SocialContentReadError, SocialContentSerializationError):
        logger.exception("İçerik okuma/serileştirme verisi tutarsız")
        raise HTTPException(
            status_code=500,
            detail={"code": "CONTENT_READ_INCONSISTENT", "message": "İçerik okuma verisi tutarsız."},
        )
    except Exception:
        logger.exception("İçerik okuma sırasında beklenmeyen hata")
        raise HTTPException(
            status_code=500,
            detail={
                "code": "CONTENT_READ_INCONSISTENT",
                "message": "İçerik okuma sırasında beklenmeyen hata oluştu.",
            },
        )



@router.get("/social/{scoring_run_id}", response_model=SocialFullResponse)
def get_social_content(
    scoring_run_id: int,
    brand_profile_id: int = Query(..., description="Workspace scope"),
    include_stale: bool = Query(False, description="Audit: stale kayıtları da getir"),
    db: Session = Depends(get_db),
    ai: AIService = Depends(get_ai)
):
    """Get all social media content for a scoring run (varsayılan: non-stale)."""
    try:
        verify_scoring_run(db, scoring_run_id, brand_profile_id)
        generator = SocialGenerator(db, ai)
        return generator.get_all(scoring_run_id, include_stale=include_stale)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=safe_500_detail(e, "Sosyal medya icerigi alinamadi")
        )


@router.post("/social/ideas/{idea_id}/select")
def select_social_idea(
    idea_id: int,
    selected: bool = Query(True, description="Select or deselect"),
    brand_profile_id: int = Query(..., description="Workspace scope"),
    db: Session = Depends(get_db),
    ai: AIService = Depends(get_ai)
):
    """
    Select or deselect an idea for content generation.
    """
    try:
        resolver = BrandDefaultsResolver(db)
        try:
            run_id = resolver.run_id_from_idea([idea_id])
        except BrandResolveError as e:
            raise HTTPException(status_code=404, detail=str(e))
        verify_scoring_run(db, run_id, brand_profile_id)

        generator = SocialGenerator(db, ai)
        try:
            success = generator.select_idea(idea_id, selected)
        except ValueError as policy_error:
            raise HTTPException(status_code=409, detail=str(policy_error))
        if not success:
            raise HTTPException(status_code=404, detail="Idea not found")
        return {"idea_id": idea_id, "is_selected": selected}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=safe_500_detail(e, "Fikir secimi guncellenemedi")
        )


@router.post("/social/ideas/{idea_id}/regenerate", response_model=SocialIdeaDBSchema)
def regenerate_social_idea(
    idea_id: int,
    brand_profile_id: int = Query(..., description="Workspace scope"),
    request: SocialRegenerateRequest = None,
    brand_name: Optional[str] = Query(None, description="Brand name"),
    db: Session = Depends(get_db),
    ai: AIService = Depends(get_ai)
):
    """
    Regenerate an idea when user didn't like the previous one.

    Creates a completely different idea for the same category.
    """
    try:
        resolver = BrandDefaultsResolver(db)
        try:
            run_id = resolver.run_id_from_idea([idea_id])
        except BrandResolveError as e:
            raise HTTPException(status_code=404, detail=str(e))
        verify_scoring_run(db, run_id, brand_profile_id)
        _guard_generation_dispatch(db, run_id)
        _scope_usage(ai, scoring_run_id=run_id, brand_profile_id=brand_profile_id)

        defaults = resolver.resolve(run_id)
        effective_brand_name = (
            resolver.safe_str(brand_name)
            or resolver.safe_str(request.brand_name if request else None)
            or defaults.get("brand_name", "")
            or "Marka"
        )

        generator = SocialGenerator(db, ai)
        additional_context = request.additional_context if request else None
        try:
            result = generator.regenerate_idea(idea_id, effective_brand_name, additional_context)
        except ValueError as policy_error:
            # STALE / EXCLUDED_TOPIC — mevcut kayit korunur (plan B)
            raise HTTPException(status_code=409, detail=str(policy_error))

        if not result:
            raise HTTPException(status_code=404, detail="Idea not found")
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=safe_500_detail(e, "Fikir yeniden üretilemedi")
        )


@router.post("/social/contents/{content_id}/regenerate", response_model=SocialContentDBSchema)
def regenerate_social_content(
    content_id: int,
    brand_profile_id: int = Query(..., description="Workspace scope"),
    request: SocialRegenerateRequest = None,
    brand_name: Optional[str] = Query(None, description="Brand name"),
    db: Session = Depends(get_db),
    ai: AIService = Depends(get_ai)
):
    """
    Regenerate content when user didn't like the previous one.

    Creates different hooks, caption, and other content for the same idea.
    """
    try:
        resolver = BrandDefaultsResolver(db)
        try:
            run_id = resolver.run_id_from_content(content_id)
        except BrandResolveError as e:
            raise HTTPException(status_code=404, detail=str(e))
        verify_scoring_run(db, run_id, brand_profile_id)
        _guard_generation_dispatch(db, run_id)
        _scope_usage(ai, scoring_run_id=run_id, brand_profile_id=brand_profile_id)

        defaults = resolver.resolve(run_id)
        effective_brand_name = (
            resolver.safe_str(brand_name)
            or resolver.safe_str(request.brand_name if request else None)
            or defaults.get("brand_name", "")
            or "Marka"
        )

        generator = SocialGenerator(db, ai)
        additional_context = request.additional_context if request else None
        try:
            result = generator.regenerate_content(content_id, effective_brand_name, additional_context)
        except ValueError as policy_error:
            raise HTTPException(status_code=409, detail=str(policy_error))

        if not result:
            raise HTTPException(status_code=404, detail="Content not found")
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=safe_500_detail(e, "İçerik yeniden üretilemedi")
        )


@router.post("/ads")
def generate_ads_content(request: AdGroupRequest, db: Session = Depends(get_db), ai: AIService = Depends(get_ai)):
    """Removed. Use POST /ads/rsa?brand_profile_id=<id>."""
    raise HTTPException(status_code=410, detail="Removed. Use POST /generation/ads/rsa?brand_profile_id=<id>.")


@router.post("/social")
def generate_social_content(request: SocialPostRequest, db: Session = Depends(get_db), ai: AIService = Depends(get_ai)):
    """Removed. Use POST /social/categories?brand_profile_id=<id>."""
    raise HTTPException(status_code=410, detail="Removed. Use POST /generation/social/categories?brand_profile_id=<id>.")
