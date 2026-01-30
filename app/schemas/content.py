"""
Pydantic schemas for Content generation.
"""
from datetime import datetime
from typing import Optional, List, Dict, Any
from decimal import Decimal
from pydantic import BaseModel, Field, ConfigDict


# ==================== ADS CONTENT ====================

class AdGroupRequest(BaseModel):
    """Request schema for generating Google Ads content."""
    keyword_ids: List[int] = Field(..., description="Kelime ID'leri")
    campaign_name: Optional[str] = Field(None, description="Kampanya adı")


class AdHeadline(BaseModel):
    """Single ad headline."""
    text: str = Field(..., max_length=30, description="Başlık metni (max 30 karakter)")


class AdDescription(BaseModel):
    """Single ad description."""
    text: str = Field(..., max_length=90, description="Açıklama metni (max 90 karakter)")


class AdGroupResponse(BaseModel):
    """Response schema for generated ad group."""
    keyword_id: int
    keyword: str
    headlines: List[AdHeadline]
    descriptions: List[AdDescription]
    sitelinks: Optional[List[str]] = None
    callouts: Optional[List[str]] = None


# ==================== SEO+GEO CONTENT ====================

class SEOGeoRequest(BaseModel):
    """Request schema for generating SEO+GEO content."""
    keyword_ids: List[int] = Field(..., description="Kelime ID'leri")
    content_type: str = Field(default="blog_post", description="İçerik tipi")
    target_word_count: int = Field(default=1500, ge=300, le=5000, description="Hedef kelime sayısı")


class SEOGeoContentResponse(BaseModel):
    """Response schema for generated SEO+GEO content."""
    keyword_id: int
    keyword: str
    content_type: str
    title: str
    meta_description: str
    h1: str
    body_sections: List[Dict[str, str]]
    schema_markup: Optional[Dict[str, Any]] = None
    seo_compliance_score: Decimal
    geo_compliance_score: Decimal
    word_count: int


# ==================== SOCIAL CONTENT ====================

class SocialPostRequest(BaseModel):
    """Request schema for generating social media content."""
    keyword_ids: List[int] = Field(..., description="Kelime ID'leri")
    platforms: List[str] = Field(
        default=["instagram", "twitter", "linkedin"],
        description="Hedef platformlar"
    )


class SocialPostResponse(BaseModel):
    """Response schema for generated social post."""
    keyword_id: int
    keyword: str
    platform: str
    caption: str
    hashtags: List[str]
    call_to_action: Optional[str] = None
    suggested_media_type: Optional[str] = None  # image, video, carousel


class SocialContentResponse(BaseModel):
    """Response schema for all social content."""
    keyword_id: int
    keyword: str
    posts: List[SocialPostResponse]


# ==================== GENERAL CONTENT ====================

class ContentOutputResponse(BaseModel):
    """General content output response."""
    id: int
    keyword_id: int
    channel: str
    content_type: str
    content_data: Dict[str, Any]
    seo_compliance_score: Optional[Decimal]
    geo_compliance_score: Optional[Decimal]
    generated_at: datetime
    
    model_config = ConfigDict(from_attributes=True)


class ContentGenerationSummary(BaseModel):
    """Summary of content generation."""
    total_generated: int
    by_channel: Dict[str, int]
    errors: List[str] = []
