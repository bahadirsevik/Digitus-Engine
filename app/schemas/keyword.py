"""
Pydantic schemas for Keyword model.
"""
from datetime import datetime
from typing import Optional, List
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


class KeywordCreate(KeywordBase):
    """Schema for creating a new keyword."""
    pass


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
    created_at: datetime
    updated_at: datetime
    
    model_config = ConfigDict(from_attributes=True)


class KeywordListResponse(BaseModel):
    """Schema for list of keywords response."""
    items: List[KeywordResponse]
    total: int
    skip: int
    limit: int


class KeywordImportRequest(BaseModel):
    """Schema for bulk keyword import."""
    keywords: List[KeywordCreate]


class KeywordImportResponse(BaseModel):
    """Schema for bulk import response."""
    created: int
    skipped: int
    message: str
