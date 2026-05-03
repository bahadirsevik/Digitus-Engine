"""
Pydantic schemas for Google Ads RSA generation.
"""
from datetime import datetime
from typing import Optional, List, Dict
from pydantic import BaseModel, Field, ConfigDict


# ==================== REQUEST SCHEMAS ====================

class AdsGenerateRequest(BaseModel):
    """Request schema for generating Google Ads RSA content."""
    scoring_run_id: int = Field(..., description="Scoring run ID")
    brand_name: Optional[str] = Field(None, description="Marka adı")
    website_url: Optional[str] = Field(None, description="Web sitesi URL")
    brand_usp: Optional[str] = Field(None, description="Unique selling proposition")
    enable_dki: bool = Field(default=True, description="Dynamic Keyword Insertion aktif mi?")
    enable_ai_regeneration: bool = Field(default=True, description="Karakter limiti aÅŸÄ±mÄ±nda AI ile yeniden Ã¼ret")
    max_groups: Optional[int] = Field(None, description="Maksimum grup sayÄ±sÄ± (None = tÃ¼mÃ¼)")


class AdGroupRegenerateRequest(BaseModel):
    """Request for regenerating a specific ad group."""
    brand_name: Optional[str] = None
    brand_usp: Optional[str] = None


# ==================== KEYWORD GROUPING ====================

class KeywordForGrouping(BaseModel):
    """Keyword info for grouping."""
    id: int
    keyword: str
    intent: Optional[str] = None


class KeywordGroupSchema(BaseModel):
    """AI-generated keyword group."""
    name: str = Field(..., description="Grup adÄ± (aÃ§Ä±klayÄ±cÄ±)")
    theme: str = Field(..., description="Grup temasÄ±/ortak Ã¶zellik")
    keyword_ids: List[int]
    keywords: List[str]


class GroupingResponse(BaseModel):
    """Response from keyword grouping."""
    ad_groups: List[KeywordGroupSchema]


# ==================== HEADLINES ====================

class HeadlineSchema(BaseModel):
    """Single RSA headline."""
    text: str = Field(..., max_length=30, description="BaÅŸlÄ±k (max 30 karakter)")
    type: str = Field(..., description="keyword|cta|benefit|trust|dynamic")
    position_preference: str = Field(default="any", description="any|position_1|position_2|position_3")
    is_dki: bool = Field(default=False, description="Dynamic Keyword Insertion iÃ§eriyor mu?")


class HeadlineValidationResult(BaseModel):
    """Result of headline validation."""
    original: str
    original_length: int
    validated: Optional[str]
    validated_length: Optional[int]
    action: str  # kept, shortened, regenerated, eliminated
    reason: Optional[str] = None


class HeadlineDBSchema(HeadlineSchema):
    """Headline with DB fields."""
    id: int
    validation_action: Optional[str] = None
    original_length: Optional[int] = None
    sort_order: int = 0
    
    model_config = ConfigDict(from_attributes=True)


# ==================== DESCRIPTIONS ====================

class DescriptionSchema(BaseModel):
    """Single RSA description."""
    text: str = Field(..., max_length=90, description="AÃ§Ä±klama (max 90 karakter)")
    type: str = Field(..., description="value_prop|features|cta|trust")
    position_preference: str = Field(default="any")


class DescriptionValidationResult(BaseModel):
    """Result of description validation."""
    original: str
    validated: str
    action: str  # kept, sentence_trim, truncated


class DescriptionDBSchema(DescriptionSchema):
    """Description with DB fields."""
    id: int
    validation_action: Optional[str] = None
    sort_order: int = 0
    
    model_config = ConfigDict(from_attributes=True)


# ==================== NEGATIVE KEYWORDS ====================

class NegativeKeywordSchema(BaseModel):
    """Negative keyword for ad group."""
    keyword: str
    match_type: str = Field(default="phrase", description="exact|phrase|broad")
    category: Optional[str] = Field(None, description="bilgi_amacli|ucretsiz|dusuk_kalite|...")
    reason: Optional[str] = None


class NegativeKeywordDBSchema(NegativeKeywordSchema):
    """Negative keyword with DB fields."""
    id: int
    
    model_config = ConfigDict(from_attributes=True)


# ==================== AD GROUP FULL ====================

class AdGroupFullSchema(BaseModel):
    """Complete ad group with all RSA components."""
    id: Optional[int] = None
    name: str
    theme: str
    keyword_ids: List[int]
    keywords: List[str]
    
    headlines: List[HeadlineSchema]
    descriptions: List[DescriptionSchema]
    negative_keywords: List[NegativeKeywordSchema]
    
    # Validation stats
    headlines_generated: int = 0
    headlines_eliminated: int = 0
    headlines_shortened: int = 0
    headlines_regenerated: int = 0
    dki_converted_count: int = 0


class AdGroupDBSchema(BaseModel):
    """Ad group from database."""
    id: int
    group_name: str
    group_theme: Optional[str]
    target_keyword_ids: List[int]
    target_keywords: List[str]
    
    headlines: List[HeadlineDBSchema]
    descriptions: List[DescriptionDBSchema]
    negative_keywords: List[NegativeKeywordDBSchema]
    
    headlines_generated: int
    headlines_eliminated: int
    dki_converted_count: int
    
    created_at: datetime
    
    model_config = ConfigDict(from_attributes=True)


# ==================== RESPONSE SCHEMAS ====================

class ValidationSummary(BaseModel):
    """Summary of validation actions across all groups."""
    total_headlines_generated: int
    headlines_kept: int
    headlines_shortened: int
    headlines_regenerated: int
    headlines_eliminated: int
    dki_converted_to_plain: int
    
    total_descriptions_generated: int
    descriptions_kept: int
    descriptions_sentence_trimmed: int
    descriptions_truncated: int


class AdsGenerateResponse(BaseModel):
    """Response from ads generation."""
    scoring_run_id: int
    total_keywords: int
    total_groups: int
    
    ad_groups: List[AdGroupFullSchema]
    
    # Summary stats
    total_headlines: int
    total_descriptions: int
    total_negative_keywords: int
    
    validation_summary: ValidationSummary
    
    generated_at: datetime


class AdGroupListResponse(BaseModel):
    """List of ad groups for a scoring run."""
    scoring_run_id: int
    total_groups: int
    ad_groups: List[AdGroupDBSchema]


class AdGroupDetailResponse(BaseModel):
    """Detailed view of single ad group."""
    ad_group: AdGroupDBSchema
    validation_summary: Dict[str, int]

