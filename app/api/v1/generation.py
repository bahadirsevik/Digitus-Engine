"""
Content generation endpoints.
Roadmap2.md - Bölüm 6, 7 güncellemesi dahil.
"""
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_ai
from app.generators.ai_service import AIService
from app.database.models import Keyword, ContentOutput, SEOGeoContent, ChannelPool, AdGroup
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


router = APIRouter()


# ==================== SEO+GEO ENDPOINTS (Roadmap2 Bölüm 6) ====================

@router.post("/seo-geo", response_model=None)
def generate_seo_geo_content(
    request: SEOGEOGenerateRequest,
    db: Session = Depends(get_db),
    ai: AIService = Depends(get_ai)
):
    """
    Generate SEO+GEO optimized content for a single keyword.
    
    Creates blog content that is optimized for both:
    - SEO (11 programmatic criteria)
    - GEO (7 AI-evaluated criteria for AI snippet optimization)
    
    Returns content with detailed compliance scores.
    """
    try:
        generator = SEOGEOGenerator(db, ai)
        result = generator.generate_content(request)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Content generation failed: {str(e)}")


@router.post("/seo-geo/bulk/{scoring_run_id}", response_model=None)
def generate_seo_geo_bulk(
    scoring_run_id: int,
    limit: Optional[int] = Query(None, ge=1, le=100, description="Maximum contents to generate"),
    tone: str = Query("informative", description="Content tone"),
    db: Session = Depends(get_db),
):
    """
    Generate SEO+GEO content in bulk as a background Celery task.
    
    Returns task_id for polling progress via /api/v1/tasks/{task_id}.
    
    Args:
        scoring_run_id: ID of the scoring run to use
        limit: Maximum number of contents to generate (optional)
        tone: Content tone (informative, professional, casual)
    """
    from app.database.models import ChannelPool, ScoringRun
    from app.tasks.generation_tasks import start_bulk_seo_generation

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
    task_id = start_bulk_seo_generation(scoring_run_id, keyword_ids)

    return {"task_id": task_id, "status": "pending", "total_keywords": len(keyword_ids)}


@router.get("/seo-geo/{content_id}")
def get_seo_geo_content(
    content_id: int,
    db: Session = Depends(get_db)
):
    """
    Get a specific SEO+GEO content by ID.
    """
    content = db.query(SEOGeoContent).filter(SEOGeoContent.id == content_id).first()
    
    if not content:
        raise HTTPException(status_code=404, detail="Content not found")
    
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
        'word_count': content.word_count,
        'keyword_density': float(content.keyword_density) if content.keyword_density else 0,
        'created_at': content.created_at.isoformat() if content.created_at else None
    }


@router.get("/seo-geo/{content_id}/compliance")
def get_compliance_report(
    content_id: int,
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
    
    keyword = db.query(Keyword).filter(Keyword.id == content.keyword_id).first()
    
    # SEO compliance
    seo_compliance = content.seo_compliance
    seo_result = None
    if seo_compliance:
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
            'total_passed': seo_compliance.total_passed,
            'total_checks': 11,
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
            'improvement_notes': geo_compliance.improvement_notes
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
        'checked_at': (seo_compliance.checked_at.isoformat() 
                      if seo_compliance and seo_compliance.checked_at else None)
    }


@router.get("/seo-geo/list/{scoring_run_id}")
def list_seo_geo_contents(
    scoring_run_id: int,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db)
):
    """
    List all SEO+GEO contents for a scoring run.
    """
    # Get keywords from SEO pool for this scoring run
    pool_items = db.query(ChannelPool).filter(
        ChannelPool.scoring_run_id == scoring_run_id,
        ChannelPool.channel == 'SEO'
    ).all()
    
    keyword_ids = [p.keyword_id for p in pool_items]
    
    # Get contents for these keywords
    query = db.query(SEOGeoContent).filter(
        SEOGeoContent.keyword_id.in_(keyword_ids)
    )
    
    total = query.count()
    contents = query.offset(skip).limit(limit).all()
    
    results = []
    for content in contents:
        keyword = db.query(Keyword).filter(Keyword.id == content.keyword_id).first()
        
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
            'created_at': content.created_at.isoformat() if content.created_at else None
        })
    
    return {
        'scoring_run_id': scoring_run_id,
        'total': total,
        'skip': skip,
        'limit': limit,
        'items': results
    }


# ==================== GOOGLE ADS RSA ENDPOINTS (Roadmap2 Bölüm 7) ====================

@router.post("/ads/rsa", response_model=AdsGenerateResponse)
def generate_ads_rsa(
    request: AdsGenerateRequest,
    db: Session = Depends(get_db),
    ai: AIService = Depends(get_ai)
):
    """
    Generate complete RSA (Responsive Search Ads) set for ADS pool keywords.
    
    This endpoint:
    1. Gets all keywords from the ADS pool for the specified scoring run
    2. Groups keywords by purchase intent (3-7 keywords per group)
    3. Generates RSA components for each group:
       - 3-15 Headlines (max 30 chars each)
       - 2-4 Descriptions (max 90 chars each)
       - 10+ Negative keywords
    4. Validates all components (DKI format, character limits)
    5. Saves to database with atomic transaction
    
    Args:
        request: Generation settings including scoring_run_id, brand info, options
    
    Returns:
        Complete RSA set with validation summary
    """
    try:
        resolver = BrandDefaultsResolver(db)
        defaults = resolver.resolve(request.scoring_run_id)
        effective_brand_name = resolver.safe_str(request.brand_name) or defaults.get("brand_name", "") or "Marka"
        effective_brand_usp = resolver.safe_str(request.brand_usp) or defaults.get("brand_usp", "") or "Kaliteli ürün ve hizmet"

        effective_request = request.model_copy(update={
            "brand_name": effective_brand_name,
            "brand_usp": effective_brand_usp,
        })

        generator = AdsGenerator(db, ai)
        result = generator.generate_ads(effective_request)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"RSA generation failed: {str(e)}")


@router.get("/ads/rsa/{scoring_run_id}", response_model=AdGroupListResponse)
def get_ads_groups(
    scoring_run_id: int,
    db: Session = Depends(get_db),
    ai: AIService = Depends(get_ai)
):
    """
    Get all ad groups for a scoring run.
    
    Returns list of ad groups with their headlines, descriptions, and negative keywords.
    """
    try:
        generator = AdsGenerator(db, ai)
        result = generator.get_ad_groups(scoring_run_id)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get ad groups: {str(e)}")


@router.get("/ads/rsa/group/{group_id}")
def get_ads_group_detail(
    group_id: int,
    db: Session = Depends(get_db),
    ai: AIService = Depends(get_ai)
):
    """
    Get detailed view of a single ad group.
    
    Returns complete ad group with all RSA components.
    """
    try:
        generator = AdsGenerator(db, ai)
        result = generator.get_ad_group_detail(group_id)
        
        if not result:
            raise HTTPException(status_code=404, detail="Ad group not found")
        
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get ad group: {str(e)}")


@router.post("/ads/rsa/group/{group_id}/regenerate")
def regenerate_ads_group(
    group_id: int,
    request: AdGroupRegenerateRequest = None,
    db: Session = Depends(get_db),
    ai: AIService = Depends(get_ai)
):
    """
    Regenerate RSA components for a specific ad group.
    
    Deletes existing headlines/descriptions and generates new ones.
    Useful when AI output quality was poor or requirements changed.
    """
    try:
        resolver = BrandDefaultsResolver(db)
        try:
            run_id = resolver.run_id_from_ad_group(group_id)
        except BrandResolveError as e:
            raise HTTPException(status_code=404, detail=str(e))

        defaults = resolver.resolve(run_id)
        effective_brand_name = (
            resolver.safe_str(request.brand_name if request else None)
            or defaults.get("brand_name", "")
            or "Marka"
        )
        effective_brand_usp = (
            resolver.safe_str(request.brand_usp if request else None)
            or defaults.get("brand_usp", "")
            or "Kaliteli ürün ve hizmet"
        )

        generator = AdsGenerator(db, ai)
        result = generator.regenerate_group(
            group_id=group_id,
            brand_name=effective_brand_name,
            brand_usp=effective_brand_usp
        )

        if not result:
            raise HTTPException(status_code=404, detail="Ad group not found")

        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to regenerate ad group: {str(e)}")


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
)


@router.post("/social/categories", response_model=SocialCategoriesResponse)
def generate_social_categories(
    request: SocialCategoriesRequest,
    db: Session = Depends(get_db),
    ai: AIService = Depends(get_ai)
):
    """
    Phase 1: Generate content categories from SOCIAL pool keywords.
    
    Categories help organize content by type:
    - educational, product_benefit, social_proof, brand_story, community, trending
    """
    try:
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
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate categories: {str(e)}")


@router.post("/social/ideas", response_model=List[SocialIdeasResponse])
def generate_social_ideas(
    request: SocialIdeasRequest,
    brand_name: Optional[str] = Query(None, description="Brand name for personalization"),
    db: Session = Depends(get_db),
    ai: AIService = Depends(get_ai)
):
    """
    Phase 2: Generate content ideas for selected categories.

    Each idea includes:
    - Target platform (instagram, tiktok, twitter, linkedin, youtube)
    - Content format (reels, carousel, story, post, thread, short)
    - Trend alignment score (0-1) - NOT viral guarantee
    """
    try:
        resolver = BrandDefaultsResolver(db)
        try:
            run_id = resolver.run_id_from_category(request.category_ids)
        except BrandResolveError as e:
            status = 400 if e.code in ("mixed_run", "empty_list") else 404
            raise HTTPException(status_code=status, detail=str(e))

        defaults = resolver.resolve(run_id)
        effective_brand_name = resolver.safe_str(brand_name) or defaults.get("brand_name", "") or "Marka"

        generator = SocialGenerator(db, ai)
        return generator.generate_ideas(request, effective_brand_name)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate ideas: {str(e)}")


@router.post("/social/contents", response_model=SocialContentsResponse)
def generate_social_contents(
    request: SocialContentsRequest,
    db: Session = Depends(get_db),
    ai: AIService = Depends(get_ai)
):
    """
    Phase 3: Generate full content packages for selected ideas.
    
    Each content includes:
    - JSONB hooks (flexible, 3+ different styles)
    - Caption with platform-specific limits
    - Scenario/script for video/carousel
    - Visual/video suggestions
    - CTA and hashtags (10-15)
    - Industry posting suggestion (NOT user-specific)
    """
    try:
        resolver = BrandDefaultsResolver(db)
        try:
            run_id = resolver.run_id_from_idea(request.idea_ids)
        except BrandResolveError as e:
            status = 400 if e.code in ("mixed_run", "empty_list") else 404
            raise HTTPException(status_code=status, detail=str(e))

        defaults = resolver.resolve(run_id)
        effective_brand_name = resolver.safe_str(request.brand_name) or defaults.get("brand_name", "") or "Marka"

        effective_request = request.model_copy(update={
            "brand_name": effective_brand_name,
        })

        generator = SocialGenerator(db, ai)
        return generator.generate_contents(effective_request)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate contents: {str(e)}")


@router.post("/social/bulk", response_model=SocialBulkResponse)
def generate_social_bulk(
    request: SocialBulkRequest,
    db: Session = Depends(get_db),
    ai: AIService = Depends(get_ai)
):
    """
    Full 3-phase pipeline with automatic idea selection.
    
    Automatically selects ideas based on trend_alignment threshold.
    """
    try:
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
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate social content: {str(e)}")


@router.get("/social/{scoring_run_id}", response_model=SocialFullResponse)
def get_social_content(
    scoring_run_id: int,
    db: Session = Depends(get_db),
    ai: AIService = Depends(get_ai)
):
    """
    Get all social media content for a scoring run.
    
    Returns categories, ideas, and contents.
    """
    try:
        generator = SocialGenerator(db, ai)
        return generator.get_all(scoring_run_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get social content: {str(e)}")


@router.post("/social/ideas/{idea_id}/select")
def select_social_idea(
    idea_id: int,
    selected: bool = Query(True, description="Select or deselect"),
    db: Session = Depends(get_db),
    ai: AIService = Depends(get_ai)
):
    """
    Select or deselect an idea for content generation.
    """
    try:
        generator = SocialGenerator(db, ai)
        success = generator.select_idea(idea_id, selected)
        if not success:
            raise HTTPException(status_code=404, detail="Idea not found")
        return {"idea_id": idea_id, "is_selected": selected}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update idea: {str(e)}")


@router.post("/social/ideas/{idea_id}/regenerate", response_model=SocialIdeaDBSchema)
def regenerate_social_idea(
    idea_id: int,
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

        defaults = resolver.resolve(run_id)
        effective_brand_name = (
            resolver.safe_str(brand_name)
            or resolver.safe_str(request.brand_name if request else None)
            or defaults.get("brand_name", "")
            or "Marka"
        )

        generator = SocialGenerator(db, ai)
        additional_context = request.additional_context if request else None
        result = generator.regenerate_idea(idea_id, effective_brand_name, additional_context)

        if not result:
            raise HTTPException(status_code=404, detail="Idea not found")
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to regenerate idea: {str(e)}")


@router.post("/social/contents/{content_id}/regenerate", response_model=SocialContentDBSchema)
def regenerate_social_content(
    content_id: int,
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

        defaults = resolver.resolve(run_id)
        effective_brand_name = (
            resolver.safe_str(brand_name)
            or resolver.safe_str(request.brand_name if request else None)
            or defaults.get("brand_name", "")
            or "Marka"
        )

        generator = SocialGenerator(db, ai)
        additional_context = request.additional_context if request else None
        result = generator.regenerate_content(content_id, effective_brand_name, additional_context)

        if not result:
            raise HTTPException(status_code=404, detail="Content not found")
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to regenerate content: {str(e)}")


# ==================== LEGACY ENDPOINTS ====================

@router.post("/ads")

def generate_ads_content(
    request: AdGroupRequest,
    db: Session = Depends(get_db),
    ai: AIService = Depends(get_ai)
):
    """
    Generate Google Ads content for keywords.
    Creates headlines, descriptions, and extensions.
    """
    keywords = db.query(Keyword).filter(Keyword.id.in_(request.keyword_ids)).all()
    
    if not keywords:
        raise HTTPException(status_code=404, detail="No keywords found")
    
    results = []
    
    for kw in keywords:
        prompt = f"""Aşağıdaki anahtar kelime için Google Ads içeriği oluştur:

Anahtar Kelime: {kw.keyword}
Sektör: {kw.sector or 'Genel'}

Lütfen şunları oluştur:
1. 5 adet başlık (her biri max 30 karakter)
2. 2 adet açıklama (her biri max 90 karakter)
3. 4 adet sitelink önerisi
4. 4 adet callout önerisi

JSON formatında yanıt ver."""
        
        try:
            response = ai.complete_json(prompt)
            import json
            content = json.loads(response)
            
            content_output = ContentOutput(
                keyword_id=kw.id,
                channel='ADS',
                content_type='ad_group',
                content_data=content
            )
            db.add(content_output)
            
            results.append({
                'keyword_id': kw.id,
                'keyword': kw.keyword,
                'content': content
            })
        except Exception as e:
            results.append({
                'keyword_id': kw.id,
                'keyword': kw.keyword,
                'error': str(e)
            })
    
    db.commit()
    
    return {
        'generated': len([r for r in results if 'content' in r]),
        'errors': len([r for r in results if 'error' in r]),
        'results': results
    }


@router.post("/social")
def generate_social_content(
    request: SocialPostRequest,
    db: Session = Depends(get_db),
    ai: AIService = Depends(get_ai)
):
    """
    Generate social media content.
    Creates platform-specific posts with hashtags.
    """
    keywords = db.query(Keyword).filter(Keyword.id.in_(request.keyword_ids)).all()
    
    if not keywords:
        raise HTTPException(status_code=404, detail="No keywords found")
    
    results = []
    
    for kw in keywords:
        platform_posts = []
        
        for platform in request.platforms:
            prompt = f"""Aşağıdaki anahtar kelime için {platform} paylaşımı oluştur:

Anahtar Kelime: {kw.keyword}
Platform: {platform}

JSON formatında şunları içer:
- caption: Paylaşım metni (platform limitine uygun)
- hashtags: İlgili hashtag listesi (5-10 adet)
- call_to_action: CTA önerisi
- suggested_media_type: image/video/carousel"""
            
            try:
                response = ai.complete_json(prompt)
                import json
                content = json.loads(response)
                content['platform'] = platform
                platform_posts.append(content)
            except Exception as e:
                platform_posts.append({
                    'platform': platform,
                    'error': str(e)
                })
        
        content_output = ContentOutput(
            keyword_id=kw.id,
            channel='SOCIAL',
            content_type='social_posts',
            content_data={'posts': platform_posts}
        )
        db.add(content_output)
        
        results.append({
            'keyword_id': kw.id,
            'keyword': kw.keyword,
            'posts': platform_posts
        })
    
    db.commit()
    
    return {
        'generated': len(results),
        'results': results
    }
