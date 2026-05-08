"""
Pydantic schemas for Keyword model.
"""
from datetime import datetime
from typing import Optional, List, Literal
from decimal import Decimal
from pydantic import BaseModel, Field, ConfigDict


class KeywordBase(BaseModel):
    """Base schema for Keyword."""
    keyword: str = Field(..., min_length=1, max_length=500, description="Anahtar kelime")
    monthly_volume: int = Field(default=0, ge=0, description="Aylık arama hacmi")
    trend_12m: Decimal = Field(default=Decimal("0.00"), description="12 aylık trend (% değişim)")
    trend_3m: Decimal = Field(default=Decimal("0.00"), description="3 aylık trend (% değişim)")
    competition_score: Decimal = Field(default=Decimal("0.50"), ge=0, le=1, description="Rekabet skoru (0-1)")
    sector: Optional[str] = Field(None, max_length=200, description="Sektör")
    target_market: Optional[str] = Field(None, max_length=200, description="Hedef pazar")
    data_source: Literal["csv", "google_ads_api", "url_seed", "manual"] = Field(
        "csv",
        description="Import source stored on the workspace keyword snapshot"
    )
    geo_target_id: Optional[str] = Field(None, max_length=20, description="Workspace keyword geo target")
    language_id: Optional[str] = Field(None, max_length=10, description="Workspace keyword language")


class KeywordCreate(KeywordBase):
    """Schema for creating a new keyword."""
    brand_profile_id: Optional[int] = Field(None, description="Workspace ID for workspace-scoped creation")


class KeywordUpdate(BaseModel):
    """Schema for updating a keyword."""
    keyword: Optional[str] = Field(None, min_length=1, max_length=500)
    monthly_volume: Optional[int] = Field(None, ge=0)
    trend_12m: Optional[Decimal] = None
    trend_3m: Optional[Decimal] = None
    competition_score: Optional[Decimal] = Field(None, ge=0, le=1)
    sector: Optional[str] = Field(None, max_length=200)
    target_market: Optional[str] = Field(None, max_length=200)
    is_active: Optional[bool] = None


class KeywordResponse(KeywordBase):
    """Schema for keyword response."""
    id: int
    is_active: bool
    data_source: str = "csv"
    wk_monthly_volume: Optional[int] = None
    wk_trend_3m: Optional[float] = None
    wk_trend_12m: Optional[float] = None
    wk_competition_score: Optional[float] = None
    wk_data_source: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class WorkspaceKeywordResponse(KeywordResponse):
    """Keyword response with workspace snapshot metrics."""
    wk_monthly_volume: int = 0
    wk_trend_3m: float = 0.0
    wk_trend_12m: float = 0.0
    wk_competition_score: float = 0.5
    wk_data_source: str = "csv"


class KeywordListResponse(BaseModel):
    """Schema for list of keywords response."""
    items: List[KeywordResponse]
    total: int
    skip: int
    limit: int


class KeywordImportRequest(BaseModel):
    """Schema for bulk keyword import."""
    keywords: List[KeywordCreate]
    brand_profile_id: Optional[int] = Field(None, description="Workspace ID for workspace-scoped import")


class KeywordImportResponse(BaseModel):
    """Schema for bulk import response."""
    created: int
    skipped: int
    message: str
