"""
Pydantic schemas for Brand Profile & Keyword Relevance.
"""
from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, ConfigDict


# ==================== REQUEST SCHEMAS ====================

class ProfileAnalyzeRequest(BaseModel):
    """Request to trigger site profile analysis."""
    company_url: str = Field(..., description="Firma web sitesi URL'si")
    competitor_urls: Optional[List[str]] = Field(
        None,
        max_length=3,
        description="Rakip site URL'leri (max 3)"
    )


class ProfileConfirmRequest(BaseModel):
    """Request to confirm/edit a draft profile."""
    profile_data: Optional[Dict[str, Any]] = Field(
        None,
        description="Düzeltilmiş profil verisi (None ise mevcut draft onaylanır)"
    )


# ==================== RESPONSE SCHEMAS ====================

class ProfilePageInfo(BaseModel):
    """Crawled page summary."""
    url: str
    title: str
    status: int


class ProfileDataSchema(BaseModel):
    """Brand profile data structure."""
    company_name: Optional[str] = None
    sector: Optional[str] = None
    products: List[str] = []
    services: List[str] = []
    target_audience: Optional[str] = None
    use_cases: List[str] = []
    problems_solved: List[str] = []
    brand_terms: List[str] = []
    exclude_themes: List[str] = []
    anchor_texts: List[str] = []


class ValidationDataSchema(BaseModel):
    """Competitor validation result."""
    consistency_score: Optional[float] = None
    competitors: List[Dict[str, Any]] = []
    warnings: List[str] = []
    profile_adjustments: List[str] = []


class BrandProfileResponse(BaseModel):
    """Full brand profile response."""
    id: int
    scoring_run_id: int
    company_url: str
    competitor_urls: Optional[List[str]] = None
    status: str
    profile_data: Optional[Dict[str, Any]] = None
    validation_data: Optional[Dict[str, Any]] = None
    source_pages: Optional[List[Dict[str, Any]]] = None
    error_message: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class KeywordRelevanceResponse(BaseModel):
    """Single keyword relevance result."""
    keyword_id: int
    keyword: str
    relevance_score: float
    matched_anchor: Optional[str] = None
    method: str = "embedding"


class RelevanceComputeResponse(BaseModel):
    """Response after computing relevance scores."""
    scoring_run_id: int
    total_keywords: int
    computed: int
    failed: int
    average_relevance: float
