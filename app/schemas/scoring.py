"""
Pydantic schemas for Scoring operations.
"""
from datetime import datetime
from typing import Optional, List, Dict, Any
from decimal import Decimal
from pydantic import BaseModel, Field, ConfigDict


class ScoringRunCreate(BaseModel):
    """Schema for creating a new scoring run."""
    run_name: Optional[str] = Field(None, max_length=200, description="Çalıştırma adı")
    ads_capacity: int = Field(..., gt=0, description="ADS kanalı için kelime kapasitesi")
    seo_capacity: int = Field(..., gt=0, description="SEO kanalı için kelime kapasitesi")
    social_capacity: int = Field(..., gt=0, description="SOCIAL kanalı için kelime kapasitesi")


class ScoringRunResponse(BaseModel):
    """Schema for scoring run response."""
    id: int
    run_name: Optional[str]
    total_keywords: int
    ads_capacity: int
    seo_capacity: int
    social_capacity: int
    status: str
    created_at: datetime
    
    model_config = ConfigDict(from_attributes=True)


class ScoringRunStatus(BaseModel):
    """Schema for scoring run status."""
    id: int
    run_name: Optional[str]
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
