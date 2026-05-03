"""
Pydantic schemas for Social Media content generation.
3-phase pipeline: Categories → Ideas → Contents
"""
from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict
from pydantic import BaseModel, Field, ConfigDict


# ==================== ENUMS ====================

class CategoryTypeEnum(str, Enum):
    EDUCATIONAL = "educational"
    PRODUCT_BENEFIT = "product_benefit"
    SOCIAL_PROOF = "social_proof"
    BRAND_STORY = "brand_story"
    COMMUNITY = "community"
    TRENDING = "trending"


class PlatformEnum(str, Enum):
    INSTAGRAM = "instagram"
    TIKTOK = "tiktok"
    TWITTER = "twitter"
    LINKEDIN = "linkedin"
    YOUTUBE = "youtube"


class ContentFormatEnum(str, Enum):
    REELS = "reels"
    CAROUSEL = "carousel"
    STORY = "story"
    POST = "post"
    THREAD = "thread"
    SHORT = "short"


class HookStyleEnum(str, Enum):
    QUESTION = "question"
    SHOCKING = "shocking"
    RELATABLE = "relatable"
    CURIOSITY = "curiosity"


# ==================== REQUEST SCHEMAS ====================

class SocialCategoriesRequest(BaseModel):
    """Aşama 1: Kategori üretimi için istek."""
    scoring_run_id: int
    brand_name: Optional[str] = None
    brand_context: Optional[str] = Field(None, description="Marka hakkında ek bilgi")
    max_categories: int = Field(default=6, ge=4, le=10)


class SocialIdeasRequest(BaseModel):
    """Aşama 2: Fikir üretimi için istek."""
    category_ids: List[int]
    ideas_per_category: int = Field(default=5, ge=3, le=10)
    target_platforms: Optional[List[PlatformEnum]] = None


class SocialContentsRequest(BaseModel):
    """Aşama 3: İçerik üretimi için istek."""
    idea_ids: List[int]
    brand_name: Optional[str] = None
    brand_tone: Optional[str] = Field(None, description="samimi, profesyonel, eğlenceli")


class SocialBulkRequest(BaseModel):
    """Tam pipeline (3 aşama otomatik)."""
    scoring_run_id: int
    brand_name: Optional[str] = None
    brand_context: Optional[str] = None
    auto_select_threshold: float = Field(default=0.7, ge=0.5, le=0.9, description="trend_alignment eşiği")
    max_contents: int = Field(default=10, ge=5, le=50)


class SocialRegenerateRequest(BaseModel):
    """Regenerate için istek."""
    brand_name: Optional[str] = None
    additional_context: Optional[str] = None


# ==================== CATEGORY SCHEMAS ====================

class SocialCategorySchema(BaseModel):
    """Sosyal medya kategori şeması."""
    id: Optional[int] = None
    category_name: str = Field(..., max_length=100)
    category_type: CategoryTypeEnum
    description: str
    relevance_score: float = Field(ge=0.0, le=1.0)
    suggested_keywords: List[str] = []
    
    model_config = ConfigDict(from_attributes=True)


class SocialCategoryDBSchema(SocialCategorySchema):
    """DB'den okunan kategori."""
    id: int
    scoring_run_id: int
    suggested_keyword_ids: Optional[List[int]] = None
    created_at: datetime


# ==================== IDEA SCHEMAS ====================

class SocialIdeaSchema(BaseModel):
    """Sosyal medya fikir şeması."""
    id: Optional[int] = None
    category_id: int
    idea_title: str = Field(..., max_length=200)
    idea_description: str
    target_platform: PlatformEnum
    content_format: ContentFormatEnum
    trend_alignment: float = Field(ge=0.0, le=1.0, description="Güncel trendlerle uyum (viral_potential değil)")
    related_keyword: Optional[str] = None
    is_selected: bool = False
    
    model_config = ConfigDict(from_attributes=True)


class SocialIdeaDBSchema(SocialIdeaSchema):
    """DB'den okunan fikir."""
    id: int
    keyword_id: Optional[int] = None
    regeneration_count: int = 0
    created_at: datetime


# ==================== HOOK SCHEMAS ====================

class HookSchema(BaseModel):
    """Tek bir hook."""
    text: str = Field(..., max_length=500)
    style: HookStyleEnum
    ab_score: Optional[float] = Field(None, ge=0.0, le=1.0, description="A/B test skoru (opsiyonel)")


# ==================== CONTENT SCHEMAS ====================

class SocialContentSchema(BaseModel):
    """Sosyal medya içerik şeması."""
    id: Optional[int] = None
    idea_id: int
    
    # JSONB hooks - esnek yapı
    hooks: List[HookSchema] = Field(..., min_length=1, description="En az 1 hook, önerilen 3+")
    
    # Ana içerik
    caption: str
    scenario: Optional[str] = Field(None, description="Video kurgusu veya carousel slide'ları")
    visual_suggestion: Optional[str] = Field(None, description="Görsel stili, renk paleti")
    video_concept: Optional[str] = Field(None, description="B-roll ve geçiş önerileri")
    
    # CTA ve etiketler
    cta_text: str
    hashtags: List[str] = Field(..., min_length=5, max_length=20)
    
    # Platform optimizasyonu
    industry_posting_suggestion: Optional[str] = Field(
        None, 
        description="Genel endüstri standardı, kullanıcı hesabına özel değil"
    )
    platform_notes: Optional[str] = None
    
    model_config = ConfigDict(from_attributes=True)


class SocialContentDBSchema(SocialContentSchema):
    """DB'den okunan içerik."""
    id: int
    content_output_id: Optional[int] = None
    regeneration_count: int = 0
    created_at: datetime


# ==================== RESPONSE SCHEMAS ====================

class SocialCategoriesResponse(BaseModel):
    """Aşama 1 response."""
    scoring_run_id: int
    total_categories: int
    categories: List[SocialCategoryDBSchema]


class SocialIdeasResponse(BaseModel):
    """Aşama 2 response."""
    category_id: int
    category_name: str
    total_ideas: int
    ideas: List[SocialIdeaDBSchema]


class SocialContentsResponse(BaseModel):
    """Aşama 3 response."""
    idea_ids: List[int]
    total_contents: int
    contents: List[SocialContentDBSchema]


class SocialBulkResponse(BaseModel):
    """Tam pipeline response."""
    scoring_run_id: int
    total_categories: int
    total_ideas: int
    total_contents: int
    categories: List[SocialCategoryDBSchema]
    selected_ideas: List[SocialIdeaDBSchema]
    contents: List[SocialContentDBSchema]
    generated_at: datetime


class SocialFullResponse(BaseModel):
    """Scoring run için tüm sosyal medya verisi."""
    scoring_run_id: int
    categories: List[SocialCategoryDBSchema]
    ideas: List[SocialIdeaDBSchema]
    contents: List[SocialContentDBSchema]
