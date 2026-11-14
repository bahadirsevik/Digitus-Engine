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
