"""Dijital varlik denetimi istek semalari."""
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class VisibilityRequest(BaseModel):
    """Istege bagli ucretli olcum: arama gorunurlugu + YouTube + Google profili."""
    keywords: List[str] = Field(default_factory=list, max_length=30)
    location: Optional[str] = Field(None, max_length=120)
    brand_name: Optional[str] = Field(None, max_length=120)


class StartAuditRequest(BaseModel):
    own_url: Optional[str] = Field(None, max_length=500, description="Boşsa workspace'in site adresi")
    competitor_urls: List[str] = Field(default_factory=list, max_length=10)
    visibility: Optional[VisibilityRequest] = None


class AnswerItem(BaseModel):
    value: Optional[str] = Field(None, max_length=300)
    note: Optional[str] = Field(None, max_length=1000)
    # Analist kaniti: source, checked_at (YYYY-AA-GG), verified_by, period, account
    evidence: Optional[Dict[str, Optional[str]]] = None


class AnswersRequest(BaseModel):
    answers: Dict[str, AnswerItem]


class StartResearchRequest(BaseModel):
    sector: str = Field(..., min_length=3, max_length=200)
    geography: str = Field(..., min_length=2, max_length=120)
    focus: Optional[str] = Field(None, max_length=300)


class ClaimOverridesRequest(BaseModel):
    overrides: List[str] = Field(default_factory=list, max_length=200)


class BriefsRequest(BaseModel):
    segments: List[int] = Field(..., min_length=1, max_length=4)
    channels: List[str] = Field(..., min_length=1, max_length=5)


class StrategyEditRequest(BaseModel):
    """Analist duzeltmesi: tum icerik (ozet, yorum, SWOT, karar, aksiyon) tek govdede."""
    version: int
    revision: int  # ekranda gorulen icerik revizyonu; baskasi duzelttiyse istek 409 alir
    content: Dict


class StrategyApproveRequest(BaseModel):
    version: int
    revision: int  # onaylanan, analistin ekranda gordugu revizyondur
    approved_by: str = Field(..., min_length=2, max_length=120)
