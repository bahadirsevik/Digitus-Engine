"""
Pydantic schemas for Scoring operations.
"""
from datetime import datetime
from typing import Optional, List, Dict, Any, Literal
from decimal import Decimal
from pydantic import BaseModel, Field, ConfigDict, model_validator


class ScoringRunCreate(BaseModel):
    """Schema for creating a new scoring run."""
    run_name: Optional[str] = Field(None, max_length=200, description="Run name")
    brand_profile_id: Optional[int] = Field(None, description="Workspace ID")
    ads_capacity: int = Field(..., gt=0, description="ADS için İstenen Keyword Sayısı")
    seo_capacity: int = Field(..., gt=0, description="SEO için İstenen Keyword Sayısı")
    social_capacity: int = Field(..., gt=0, description="SOCIAL için İstenen Keyword Sayısı")
    default_relevance_coefficient: float = Field(
        1.0,
        ge=0.1,
        le=3.0,
        description="Varsayılan İlgi Katsayısı — ilgi skoru ile kanal skorunu birleştirmede kullanılır"
    )
    company_url: Optional[str] = Field(None, max_length=500, description="Company website URL")
    competitor_urls: Optional[List[str]] = Field(None, max_length=3, description="Competitor URL list (max 3)")
    keyword_source_filter: Optional[Literal["csv", "google_ads_api"]] = None
    enable_ads: bool = Field(True, description="ADS Skorlaması")
    enable_seo: bool = Field(True, description="SEO Skorlaması")
    enable_social: bool = Field(True, description="SOCIAL Skorlaması")
    keyword_selection_mode: Literal["all", "top_n", "specific"] = Field("all", description="Keyword seçim modu")
    keyword_limit: Optional[int] = Field(None, gt=0, le=1000, description="top_n modu için limit")
    selected_keyword_ids: Optional[List[int]] = Field(None, max_length=500, description="specific modu için keyword ID'leri")
    skip_relevance: bool = Field(False, description="Bu çalışma için ilgi skoru hesaplama")
    auto_assign_channels: Optional[bool] = Field(
        None,
        description="Skorlama/relevance bitince kanal atamasını otomatik başlat (v3 için varsayılan True, v2/v2_1 için varsayılan False)",
    )
    # Motor v3 (plan_algoritma_entegrasyonu.md + ADR-004): Yeni analizler yalnız v3 ile oluşturulabilir.
    # v2 ve v2_1 emekliye ayrılmıştır; istekte açıkça gönderilirse tipli 400 LEGACY_ENGINE_RETIRED döner.
    algorithm_version: Optional[str] = Field(
        "v3", description="Skorlama/seçim algoritma sürümü (yeni analizler için yalnız v3)"
    )

    @model_validator(mode="after")
    def resolve_auto_assign_default(self) -> "ScoringRunCreate":
        if not self.algorithm_version:
            self.algorithm_version = "v3"
        if self.auto_assign_channels is None:
            self.auto_assign_channels = (self.algorithm_version == "v3")
        return self


class ScoringRunResponse(BaseModel):
    """Schema for scoring run response."""
    id: int
    run_name: Optional[str]
    brand_profile_id: Optional[int] = None
    total_keywords: int
    ads_capacity: int
    seo_capacity: int
    social_capacity: int
    default_relevance_coefficient: Decimal
    status: str
    keyword_source_filter: Optional[Literal["csv", "google_ads_api"]] = None
    enable_ads: Optional[bool] = None
    enable_seo: Optional[bool] = None
    enable_social: Optional[bool] = None
    keyword_selection_mode: Optional[str] = None
    keyword_limit: Optional[int] = None
    skip_relevance: Optional[bool] = None
    auto_assign_channels: Optional[bool] = None
    algorithm_version: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ScoringRunStatus(BaseModel):
    """Schema for scoring run status."""
    id: int
    run_name: Optional[str]
    brand_profile_id: Optional[int] = None
    ads_capacity: int
    seo_capacity: int
    social_capacity: int
    default_relevance_coefficient: Decimal
    status: str
    total_keywords: int
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    keyword_source_filter: Optional[Literal["csv", "google_ads_api"]] = None
    enable_ads: Optional[bool] = None
    enable_seo: Optional[bool] = None
    enable_social: Optional[bool] = None
    keyword_selection_mode: Optional[str] = None
    keyword_limit: Optional[int] = None
    skip_relevance: Optional[bool] = None
    auto_assign_channels: Optional[bool] = None
    algorithm_version: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class KeywordScoreResponse(BaseModel):
    """Schema for keyword score response."""
    keyword_id: int
    keyword: str
    ads_score: Optional[Decimal]
    seo_score: Optional[Decimal]
    social_score: Optional[Decimal]
    ads_rank: Optional[int]
    seo_rank: Optional[int]
    social_rank: Optional[int]
    # Motor v3 sonuclari legacy KeywordScore kolonlarina yazilmaz;
    # EngineSelection'dan kanal bazinda okunur. Bu alanlar v2/v2.1 icin
    # bos kalir ve geriye uyumlulugu bozmaz.
    family_id: Optional[str] = None
    family_name: Optional[str] = None
    ads_final_rank: Optional[int] = None
    seo_final_rank: Optional[int] = None
    social_final_rank: Optional[int] = None
    ads_exclude_reason: Optional[str] = None
    seo_exclude_reason: Optional[str] = None
    social_exclude_reason: Optional[str] = None
    ads_pool_class: Optional[str] = None
    seo_pool_class: Optional[str] = None
    social_pool_class: Optional[str] = None
    social_priority: Optional[str] = None
    # Skor ekranı rozetleri (plan A, Codex A+B-3 sözleşmesi): dışlama skor
    # hesabında değil politika katmanlarında uygulanır; her neden GERÇEK
    # kapsamını (hangi kanal/yüzey) taşır ve birden fazla neden birlikte döner.
    exclusions: List[Dict[str, Any]] = []  # [{reason, channels, matched_term}]


class ScoringResultsResponse(BaseModel):
    """Schema for scoring results response."""
    scoring_run_id: int
    status: str
    algorithm_version: Optional[str] = None
    total_scored: int
    scores: List[KeywordScoreResponse]


class ChannelScoresSummary(BaseModel):
    """Summary of scores for a channel."""
    channel: str
    top_keywords: List[Dict[str, Any]]
    total_count: int
