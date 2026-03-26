"""
Pydantic schemas for Scoring operations.
"""
from datetime import datetime
from typing import Optional, List, Dict, Any
from decimal import Decimal
from pydantic import BaseModel, Field, ConfigDict


class ScoringRunCreate(BaseModel):
    """Schema for creating a new scoring run."""
    run_name: Optional[str] = Field(None, max_length=200, description="Run name")
    ads_capacity: int = Field(..., gt=0, description="ADS channel capacity")
    seo_capacity: int = Field(..., gt=0, description="SEO channel capacity")
    social_capacity: int = Field(..., gt=0, description="SOCIAL channel capacity")
    default_relevance_coefficient: float = Field(
        1.0,
        ge=0.1,
        le=3.0,
        description="Default relevance coefficient used in channel assignment"
    )
    company_url: Optional[str] = Field(None, max_length=500, description="Company website URL")
    competitor_urls: Optional[List[str]] = Field(None, max_length=3, description="Competitor URL list (max 3)")


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
