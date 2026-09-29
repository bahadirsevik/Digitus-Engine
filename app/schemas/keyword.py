"""
Pydantic schemas for Keyword model.
"""
from datetime import datetime
from typing import Optional, List, Literal, Any
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
    force_include: bool = Field(
        False,
        description=(
            "YALNIZ yasaklı-tema import kapısını atlar ('Geri al' akışı). "
            "Exact/fuzzy duplicate ve havuz limiti kontrollerini ATLAMAZ."
        ),
    )


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
    wk_id: Optional[int] = None
    wk_monthly_volume: int = 0
    wk_trend_3m: float = 0.0
    wk_trend_12m: float = 0.0
    wk_competition_score: float = 0.5
    wk_data_source: str = "csv"


class KeywordListResponse(BaseModel):
    """Schema for list of keywords response."""
    items: List[WorkspaceKeywordResponse]
    total: int
    skip: int
    limit: int


class KeywordImportRequest(BaseModel):
    """Schema for bulk keyword import."""
    keywords: List[KeywordCreate]
    brand_profile_id: Optional[int] = Field(None, description="Workspace ID for workspace-scoped import")


class SkippedKeywordDetail(BaseModel):
    keyword: str
    # 'skipped_exact' | 'skipped_fuzzy' | 'batch_duplicate' | 'skipped_theme' | 'limit_exceeded'
    reason: str
    matched: Optional[str] = None  # matching existing/kept keyword text OR matched exclude theme
    # 'Geri al' akışının minimum payload'ı response'tan kurabilmesi için
    # (özellikle CSV yolunda orijinal istek frontend'de olmayabilir)
    monthly_volume: Optional[int] = None
    trend_3m: Optional[float] = None
    trend_12m: Optional[float] = None
    competition_score: Optional[float] = None
    data_source: Optional[Literal["csv", "google_ads_api", "url_seed", "manual"]] = None
    geo_target_id: Optional[str] = None
    language_id: Optional[str] = None


class KeywordImportResponse(BaseModel):
    """Schema for bulk import response."""
    created: int
    skipped: int
    requested: int
    total_before: int
    total_after: int
    created_new: int = 0
    linked_existing: int = 0
    fuzzy_merged_in_batch: int = 0
    skipped_theme: int = 0
    skipped_limit: int = 0
    pool_limit: int = 0
    pool_total: int = 0
    skipped_details: List[SkippedKeywordDetail] = Field(default_factory=list)
    # P1.6: ELEME DEGIL — kabul edildi ama AI kaynakli dislama temasina takildi.
    # Kullanici onaylamadan hard-drop yapilmaz; bu liste gozden gecirme icindir.
    warned_theme: int = 0
    theme_warnings: List[SkippedKeywordDetail] = Field(default_factory=list)
    message: str


class KeywordImportDetail(BaseModel):
    keyword: Optional[str] = None
    source_file: Optional[str] = None
    source_row: Optional[int] = None
    reason: str
    matched_keyword: Optional[str] = None
    matched_keyword_id: Optional[int] = None
    matched_in_workspace: bool = False
    match_ratio: Optional[int] = None
    monthly_volume: Optional[int] = None
    trend_3m: Optional[float] = None
    trend_12m: Optional[float] = None
    competition_score: Optional[float] = None
    data_source: Optional[str] = None
    geo_target_id: Optional[str] = None
    language_id: Optional[str] = None
    competition_index: Optional[int] = None
    top_bid_high: Optional[float] = None


class KeywordUploadCsvResponse(KeywordImportResponse):
    parsed: int = 0
    accepted: int = 0
    skipped_exact: int = 0
    skipped_fuzzy: int = 0
    kept_fuzzy_different_metrics: int = 0
    kept_fuzzy_default_metrics: int = 0
    exact_duplicate_different_metrics: int = 0
    skipped_due_to_race: int = 0
    dry_run_token: Optional[str] = None
    parser_meta: dict[str, Any] = Field(default_factory=dict)
    accepted_keywords: List[KeywordImportDetail] = Field(default_factory=list)
    excluded_keywords: List[KeywordImportDetail] = Field(default_factory=list)
    exact_duplicates: List[KeywordImportDetail] = Field(default_factory=list)
    exact_duplicates_different_metrics_list: List[KeywordImportDetail] = Field(
        default_factory=list,
        alias="exact_duplicates_different_metrics",
    )
    fuzzy_skipped: List[KeywordImportDetail] = Field(default_factory=list)
    fuzzy_kept: List[KeywordImportDetail] = Field(default_factory=list)
    junk_rows: List[KeywordImportDetail] = Field(default_factory=list)
    theme_excluded: List[KeywordImportDetail] = Field(default_factory=list)
    # P1.6: kabul edilen ama AI temasina takilan satirlar (dry-run uyarisi)
    theme_warned: List[KeywordImportDetail] = Field(default_factory=list)


class KeywordUploadCsvCommitRequest(BaseModel):
    brand_profile_id: int
    dry_run_token: str
