"""
Pydantic schemas for Export operations.
"""
from datetime import datetime
from typing import Optional, List
from enum import Enum
from pydantic import BaseModel, Field


class ExportFormat(str, Enum):
    """Supported export formats."""
    DOCX = "docx"
    PDF = "pdf"
    EXCEL = "excel"


class ExportRequest(BaseModel):
    """Request schema for export."""
    scoring_run_id: int = Field(..., description="Skorlama çalıştırma ID")
    format: ExportFormat = Field(..., description="Export formatı")
    include_scores: bool = Field(default=True, description="Skorları dahil et")
    include_intent_analysis: bool = Field(default=False, description="Niyet analizini dahil et")
    include_content: bool = Field(default=False, description="Üretilmiş içerikleri dahil et")
    channels: Optional[List[str]] = Field(None, description="Dahil edilecek kanallar (boş = tümü)")


class ExportResponse(BaseModel):
    """Response schema for export."""
    filename: str
    format: str
    size_bytes: int
    download_url: str
    generated_at: datetime


class ExportStatusResponse(BaseModel):
    """Response schema for export status."""
    export_id: str
    status: str  # pending, processing, completed, failed
    progress: int  # 0-100
    filename: Optional[str] = None
    download_url: Optional[str] = None
    error: Optional[str] = None
