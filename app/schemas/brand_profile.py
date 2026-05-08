"""
Pydantic schemas for Brand Profile & Keyword Relevance.
"""
from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, ConfigDict


# ==================== REQUEST SCHEMAS ====================

class ProfileAnalyzeRequest(BaseModel):
    """Request to trigger site profile analysis."""
    company_url: str = Field(..., description="Firma web sitesi URL'si")
    competitor_urls: Optional[List[str]] = Field(
        None,
        max_length=3,
        description="Rakip site URL'leri (max 3)"
    )


class ProfileConfirmRequest(BaseModel):
    """Request to confirm/edit a draft profile."""
    profile_data: Optional[Dict[str, Any]] = Field(
        None,
        description="Düzeltilmiş profil verisi (None ise mevcut draft onaylanır)"
    )


# ==================== RESPONSE SCHEMAS ====================

class ProfilePageInfo(BaseModel):
    """Crawled page summary."""
    url: str
    title: str
    status: int


class ProfileDataSchema(BaseModel):
    """Brand profile data structure."""
    company_name: Optional[str] = None
    sector: Optional[str] = None
    products: List[str] = []
    services: List[str] = []
    target_audience: Optional[str] = None
    use_cases: List[str] = []
    problems_solved: List[str] = []
    brand_terms: List[str] = []
    exclude_themes: List[str] = []
    anchor_texts: List[str] = []


class ValidationDataSchema(BaseModel):
    """Competitor validation result."""
    consistency_score: Optional[float] = None
    competitors: List[Dict[str, Any]] = []
    warnings: List[str] = []
    profile_adjustments: List[str] = []


class BrandProfileResponse(BaseModel):
    """Full brand profile response."""
    id: int
    scoring_run_id: int
    company_url: str
    competitor_urls: Optional[List[str]] = None
    status: str
    profile_data: Optional[Dict[str, Any]] = None
    validation_data: Optional[Dict[str, Any]] = None
    source_pages: Optional[List[Dict[str, Any]]] = None
    error_message: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class KeywordRelevanceResponse(BaseModel):
    """Single keyword relevance result."""
    keyword_id: int
    keyword: str
    relevance_score: float
    matched_anchor: Optional[str] = None
    method: str = "embedding"


class WorkspaceCreateRequest(BaseModel):
    """Yeni marka çalışması oluşturma isteği."""
    name: str = Field(..., max_length=200, description="Çalışma adı")
    company_url: str = Field(..., description="Şirket URL")
    competitor_urls: Optional[List[str]] = Field(None, max_length=3, description="Rakip URL'leri")
    preliminary_info: Optional[str] = Field(None, description="Marka vizyonu, hedef kitlesi, benzersiz satış noktaları")
    default_geo_target_id: Optional[str] = Field("2792", description="Geo target ID")
    default_language_id: Optional[str] = Field("1055", description="Language ID")


class WorkspaceKeywordRefreshRequest(BaseModel):
    """Workspace keyword metriklerini Google Ads ile yenileme isteği."""
    customer_id: Optional[str] = Field(None, description="Google Ads customer ID")
    max_results: int = Field(300, ge=1, le=5000)
    min_volume: int = Field(0, ge=0)
    include_new_ideas: bool = Field(False, description="Google Ads'in döndürdüğü yeni fikirleri de ekle")


class WorkspaceKeywordRefreshResponse(BaseModel):
    """Workspace keyword refresh diff raporu."""
    workspace_id: int
    refreshed: int
    unchanged: int
    added: int
    removed: int
    total_after: int


class WorkspaceResponse(BaseModel):
    """Marka çalışması detay yanıtı."""
    id: int
    name: Optional[str] = None
    company_url: str
    competitor_urls: Optional[List[str]] = None
    status: str
    profile_data: Optional[Dict[str, Any]] = None
    suggested_keywords: Optional[List[str]] = None
    preliminary_info: Optional[str] = None
    deleted_at: Optional[datetime] = None
    default_geo_target_id: Optional[str] = None
    default_language_id: Optional[str] = None
    is_system_default: bool = False
    scoring_run_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class WorkspaceListResponse(BaseModel):
    """Çalışma listesi öğesi (kart görünümü için)."""
    id: int
    name: Optional[str] = None
    company_url: str
    status: str
    profile_data: Optional[Dict[str, Any]] = None
    suggested_keywords: Optional[List[str]] = None
    default_geo_target_id: Optional[str] = None
    default_language_id: Optional[str] = None
    deleted_at: Optional[datetime] = None
    created_at: datetime
    run_count: int = 0

    model_config = ConfigDict(from_attributes=True)


class RelevanceComputeResponse(BaseModel):
    """Response after computing relevance scores."""
    scoring_run_id: int
    total_keywords: int
    computed: int
    failed: int
    average_relevance: float
