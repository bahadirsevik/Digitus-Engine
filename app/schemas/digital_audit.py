"""Dijital varlik denetimi istek semalari."""
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class StartAuditRequest(BaseModel):
    own_url: Optional[str] = Field(None, max_length=500, description="Boşsa workspace'in site adresi")
    competitor_urls: List[str] = Field(default_factory=list, max_length=10)


class AnswerItem(BaseModel):
    value: Optional[str] = Field(None, max_length=300)
    note: Optional[str] = Field(None, max_length=1000)


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
