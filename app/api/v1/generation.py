"""
Content generation endpoints.
"""
from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_ai
from app.generators.ai_service import AIService
from app.database.models import Keyword, ContentOutput
from app.schemas.content import (
    AdGroupRequest, AdGroupResponse,
    SEOGeoRequest, SEOGeoContentResponse,
    SocialPostRequest, SocialContentResponse,
    ContentGenerationSummary
)


router = APIRouter()


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
        # AI prompt for ad generation
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
            
            # Save to database
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


@router.post("/seo-geo")
def generate_seo_geo_content(
    request: SEOGeoRequest,
    db: Session = Depends(get_db),
    ai: AIService = Depends(get_ai)
):
    """
    Generate SEO+GEO optimized content.
    Creates blog posts, landing pages with SEO compliance.
    """
    keywords = db.query(Keyword).filter(Keyword.id.in_(request.keyword_ids)).all()
    
    if not keywords:
        raise HTTPException(status_code=404, detail="No keywords found")
    
    results = []
    
    for kw in keywords:
        prompt = f"""Aşağıdaki anahtar kelime için SEO uyumlu blog içeriği oluştur:

Anahtar Kelime: {kw.keyword}
Hedef Kelime Sayısı: {request.target_word_count}
İçerik Tipi: {request.content_type}

Şunları içeren JSON formatında yanıt ver:
- title: SEO uyumlu başlık
- meta_description: 155 karakterlik meta açıklama
- h1: Ana başlık
- sections: Bölümler listesi (her biri heading ve content içerir)
- schema_markup: JSON-LD schema önerisi"""
        
        try:
            response = ai.complete_json(prompt, max_tokens=3000)
            import json
            content = json.loads(response)
            
            content_output = ContentOutput(
                keyword_id=kw.id,
                channel='SEO',
                content_type=request.content_type,
                content_data=content,
                seo_compliance_score=0.85,  # Placeholder
                geo_compliance_score=0.80
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
