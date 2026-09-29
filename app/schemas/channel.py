"""
Pydantic schemas for Channel operations.
"""
from datetime import datetime
from typing import Optional, List, Dict, Any
from decimal import Decimal
from pydantic import BaseModel, Field, ConfigDict


class ChannelAssignRequest(BaseModel):
    """Request schema for channel assignment execution."""
    relevance_override: Optional[float] = Field(
        None,
        ge=0.1,
        le=3.0,
        description="Optional relevance coefficient override for this assignment"
    )
    # Corpus screening onayi (plan §6.3 / Codex 10. tur #4): kullanicinin
    # GORDUGU preflight'in modu ve SHA'si dispatch'e TASINIR; sunucu bunu
    # yeniden hesaplayip karsilastirir. Onay yoksa ucretli tarama BASLAMAZ.
    screening_mode: Optional[str] = Field(
        None,
        pattern="^(off|shadow|assistive)$",
        description="Onaylanan tarama modu (off|shadow|assistive)")
    preflight_sha256: Optional[str] = Field(
        None,
        pattern="^[0-9a-f]{64}$",
        description="Onaylanan preflight paketinin SHA256'si")
    approved_screening_hard_cap_usd: Optional[float] = Field(
        None, ge=0,
        description=("Kullanicinin onayladigi UYGULANAN screening cap'i "
                     "(preflight.enforced_hard_cap_usd). Onay YALNIZ "
                     "screening sinirina baglanir. Shadow ve assistive "
                     "modlarinda downstream cap sunucu tarafinda ayrica "
                     "uygulanir ve preflight kimligine dahildir."))


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
    id: int
    keyword_id: int
    keyword: str
    channel: str
    final_rank: int
    is_strategic: bool
    pool_label: Optional[str] = None  # örn. 'rising_opportunity' (Yükselen Fırsat)
    relevance_score: Optional[Decimal] = None
    adjusted_score: Optional[Decimal] = None

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
