"""
Pydantic schemas for Channel operations.
"""
from datetime import datetime
from typing import Optional, List, Dict, Any
from decimal import Decimal
from pydantic import BaseModel, Field, ConfigDict


class ChannelCandidateResponse(BaseModel):
    """Schema for channel candidate response."""
    keyword_id: int
    keyword: str
    channel: str
    raw_score: Decimal
    rank_in_channel: int
    
    model_config = ConfigDict(from_attributes=True)


class IntentAnalysisResponse(BaseModel):
    """Schema for intent analysis response."""
    keyword_id: int
    keyword: str
    channel: str
    intent_type: str
    confidence_score: Optional[Decimal]
    ai_reasoning: Optional[str]
    is_passed: bool
    
    model_config = ConfigDict(from_attributes=True)


class ChannelPoolResponse(BaseModel):
    """Schema for channel pool response."""
    keyword_id: int
    keyword: str
    channel: str
    final_rank: int
    is_strategic: bool
    
    model_config = ConfigDict(from_attributes=True)


class ChannelPoolsResponse(BaseModel):
    """Schema for all channel pools response."""
    scoring_run_id: int
    status: str
    channels: Dict[str, List[ChannelPoolResponse]]


class StrategicKeywordsResponse(BaseModel):
    """Schema for strategic keywords response."""
    scoring_run_id: int
    strategic_keywords: List[ChannelPoolResponse]
    count: int
    note: str = "Bu kelimeler hem ADS hem SEO kanalında yüksek potansiyele sahip."


class ChannelAssignmentSummary(BaseModel):
    """Summary of channel assignment process."""
    scoring_run_id: int
    pool_building: Dict[str, int]
    intent_analysis: Dict[str, Dict[str, int]]
    final_pools: Dict[str, int]
    strategic_count: int
    status: str
