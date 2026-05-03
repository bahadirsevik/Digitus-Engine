"""
Pydantic schemas for Scoring operations.
"""
from datetime import datetime
from typing import Optional, List, Dict, Any, Literal
from decimal import Decimal
from pydantic import BaseModel, Field, ConfigDict


class ScoringRunCreate(BaseModel):
    """Schema for creating a new scoring run."""
    run_name: Optional[str] = Field(None, max_length=200, description="Run name")
    brand_profile_id: Optional[int] = Field(None, description="Workspace ID")
    ads_capacity: int = Field(..., gt=0, description="ADS için İstenen Keyword Sayısı")
    seo_capacity: int = Field(..., gt=0, description="SEO için İstenen Keyword Sayısı")
    social_capacity: int = Field(..., gt=0, description="SOCIAL için İstenen Keyword Sayısı")
    default_relevance_coefficient: float = Field(
        1.0,
        ge=0.1,
        le=3.0,
        description="Varsayılan İlgi Katsayısı — ilgi skoru ile kanal skorunu birleştirmede kullanılır"
    )
    company_url: Optional[str] = Field(None, max_length=500, description="Company website URL")
    competitor_urls: Optional[List[str]] = Field(None, max_length=3, description="Competitor URL list (max 3)")
    keyword_source_filter: Optional[Literal["csv", "google_ads_api"]] = None
    enable_ads: bool = Field(True, description="ADS Skorlaması")
    enable_seo: bool = Field(True, description="SEO Skorlaması")
    enable_social: bool = Field(True, description="SOCIAL Skorlaması")
    keyword_selection_mode: Literal["all", "top_n", "specific"] = Field("all", description="Keyword seçim modu")
    keyword_limit: Optional[int] = Field(None, gt=0, le=1000, description="top_n modu için limit")
    selected_keyword_ids: Optional[List[int]] = Field(None, max_length=500, description="specific modu için keyword ID'leri")
    skip_relevance: bool = Field(False, description="Bu çalışma için ilgi skoru hesaplama")


class ScoringRunResponse(BaseModel):
    """Schema for scoring run response."""
    id: int
    run_name: Optional[str]
    total_keywords: int
    ads_capacity: int
    seo_capacity: int
    social_capacity: int
    default_relevance_coefficient: Decimal
    status: str
    keyword_source_filter: Optional[Literal["csv", "google_ads_api"]] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ScoringRunStatus(BaseModel):
    """Schema for scoring run status."""
    id: int
    run_name: Optional[str]
    ads_capacity: int
    seo_capacity: int
    social_capacity: int
    default_relevance_coefficient: Decimal
    status: str
    total_keywords: int
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    keyword_source_filter: Optional[Literal["csv", "google_ads_api"]] = None

    model_config = ConfigDict(from_attributes=True)


class KeywordScoreResponse(BaseModel):
    """Schema for keyword score response."""
    keyword_id: int
    keyword: str
    ads_score: Optional[Decimal]
    seo_score: Optional[Decimal]
    social_score: Optional[Decimal]
    ads_rank: Optional[int]
    seo_rank: Optional[int]
    social_rank: Optional[int]


class ScoringResultsResponse(BaseModel):
    """Schema for scoring results response."""
    scoring_run_id: int
    status: str
    total_scored: int
    scores: List[KeywordScoreResponse]


class ChannelScoresSummary(BaseModel):
    """Summary of scores for a channel."""
    channel: str
    top_keywords: List[Dict[str, Any]]
    total_count: int
