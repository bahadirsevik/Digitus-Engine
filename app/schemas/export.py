"""
Export Pydantic Schemas.

Schemas for export functionality:
- ExportRequest, ExportStatusResponse
- Data collection schemas for each section
- FullReport aggregate
"""
from datetime import datetime
from typing import List, Optional, Dict, Any
from enum import Enum
from pydantic import BaseModel, Field


# ==================== ENUMS ====================

class ExportFormatEnum(str, Enum):
    """Desteklenen export formatları."""
    DOCX = "docx"
    PDF = "pdf"
    EXCEL = "excel"
    CSV = "csv"


class ExportSectionEnum(str, Enum):
    """Seçilebilir rapor bölümleri."""
    SUMMARY = "summary"
    BRAND_PROFILE = "brand_profile"
    SCORING = "scoring"
    CHANNELS = "channels"
    SEO_CONTENT = "seo_content"
    ADS = "ads"
    SOCIAL = "social"
    ALL = "all"


class ExportStatusEnum(str, Enum):
    """Export durum değerleri."""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


# ==================== REQUEST/RESPONSE ====================

class ExportRequest(BaseModel):
    """Export başlatma isteği."""
    scoring_run_id: int = Field(..., description="Scoring run ID")
    format: ExportFormatEnum = Field(..., description="Export formatı")
    sections: List[ExportSectionEnum] = Field(
        default=[ExportSectionEnum.ALL],
        description="Dahil edilecek bölümler"
    )
    include_compliance_details: bool = Field(
        default=True,
        description="SEO/GEO compliance detayları dahil mi?"
    )
    include_stale_content: bool = Field(
        default=False,
        description="Yeniden kanal ataması sonrası güncel değil işaretlenen içerikler dahil mi?"
    )


class ExportStatusResponse(BaseModel):
    """Export durum yanıtı."""
    export_id: str
    status: ExportStatusEnum
    progress: int = Field(ge=0, le=100)
    file_name: Optional[str] = None
    error_message: Optional[str] = None
    created_at: Optional[datetime] = None
    # Plan v13: rapor, üretildiğinden beri politika/profil değiştiyse true —
    # dosya indirilebilir kalır ama "eski politikayla üretildi" uyarısı gösterilir.
    policy_outdated: bool = False
    # Plan v4 (export dağıtımı): geçmiş satırları tür etiketi üretebilsin
    # ("İçerikler · Word" / "Tam rapor · Excel") — job'da zaten saklı alanlar
    format: Optional[str] = None
    sections: List[str] = Field(default_factory=list)


class ExportListResponse(BaseModel):
    """Geçmiş exportlar listesi."""
    exports: List[ExportStatusResponse]
    total: int


# ==================== DATA COLLECTION SCHEMAS ====================

class SummaryData(BaseModel):
    """Özet bölümü verileri."""
    scoring_run_id: int
    created_at: datetime
    total_keywords: int
    
    # Kanal dağılımı
    ads_count: int = 0
    seo_count: int = 0
    social_count: int = 0
    strategic_count: int = 0
    
    # Üretilen içerik
    seo_content_count: int = 0
    ad_group_count: int = 0
    social_content_count: int = 0


class KeywordScoreData(BaseModel):
    """Tek kelime skoru."""
    keyword_id: int
    keyword: str
    volume: Optional[int] = None
    trend_3m: Optional[float] = None
    trend_12m: Optional[float] = None
    competition: Optional[float] = None
    
    ads_score: Optional[float] = None
    seo_score: Optional[float] = None
    social_score: Optional[float] = None
    
    ads_rank: Optional[int] = None
    seo_rank: Optional[int] = None
    social_rank: Optional[int] = None
    vector_similarity: Optional[float] = None
    vector_adjusted_score: Optional[float] = None
    
    primary_channel: Optional[str] = None
    intent: Optional[str] = None

    # Kanal havuzu satirlari icin (plan v4 §4 — "neden secildi" analizi)
    final_rank: Optional[int] = None
    is_strategic: Optional[bool] = None
    pool_label: Optional[str] = None


class ScoringData(BaseModel):
    """Skorlama sonuçları."""
    keywords: List[KeywordScoreData]
    total: int


class ChannelPoolData(BaseModel):
    """Tek kanal havuzu."""
    channel: str
    keywords: List[KeywordScoreData]
    total: int


class ChannelsData(BaseModel):
    """Tüm kanal havuzları."""
    ads: ChannelPoolData
    seo: ChannelPoolData
    social: ChannelPoolData
    strategic: List[KeywordScoreData]


class SEOContentData(BaseModel):
    """Tek SEO+GEO içerik."""
    id: int
    keyword_id: int
    keyword: str
    
    # İçerik bileşenleri
    title: str
    url_suggestion: Optional[str] = None
    intro_paragraph: Optional[str] = None
    subheadings: Optional[List[str]] = None
    body_sections: Optional[List[str]] = None
    body_content: Optional[str] = None
    bullet_points: Optional[List[Dict[str, Any]]] = None
    
    # Link önerileri
    internal_link_anchor: Optional[str] = None
    internal_link_url: Optional[str] = None
    external_link_anchor: Optional[str] = None
    external_link_url: Optional[str] = None
    
    # Meta bilgiler
    meta_description: Optional[str] = None
    faq_items: Optional[List[Dict[str, Any]]] = None
    image_alt_texts: Optional[List[str]] = None
    word_count: int = 0
    keyword_count: int = 0
    keyword_density: Optional[float] = None
    
    # Skorlar
    seo_score: Optional[float] = None
    geo_score: Optional[float] = None
    combined_score: Optional[float] = None
    
    # Compliance detayları (11 SEO + 7 GEO kriter)
    seo_checks: Optional[Dict[str, Any]] = None
    geo_checks: Optional[Dict[str, Any]] = None

    # Güncel checklist (checks_json dahil) madde bazlı — status:
    # pass | partial | fail | not_evaluated (NULL/eksik/GEO fallback)
    checklist_items: List[Dict[str, Any]] = Field(default_factory=list)
    # 'ai' | 'fallback' (AI başarısız, değerlendirilmedi) | None (GEO kaydı yok)
    geo_evaluation_source: Optional[str] = None
    # Ekrandaki publish_review ile aynı (app/compliance/publish_review.py)
    publish_review: Optional[Dict[str, Any]] = None


class SEOContentsData(BaseModel):
    """Tüm SEO+GEO içerikler."""
    contents: List[SEOContentData]
    total: int


class HeadlineData(BaseModel):
    """Reklam başlığı."""
    headline_text: str
    headline_type: Optional[str] = None
    is_dki: bool = False


class DescriptionData(BaseModel):
    """Reklam açıklaması."""
    description_text: str
    description_type: Optional[str] = None


class NegativeKeywordData(BaseModel):
    """Negatif kelime."""
    keyword: str
    match_type: Optional[str] = None
    reason: Optional[str] = None


class AdGroupData(BaseModel):
    """Tek reklam grubu."""
    id: int
    group_name: str
    group_theme: Optional[str] = None
    target_keywords: List[str] = []
    
    headlines: List[HeadlineData] = []
    descriptions: List[DescriptionData] = []
    negative_keywords: List[NegativeKeywordData] = []


class AdsData(BaseModel):
    """Tüm reklam grupları."""
    ad_groups: List[AdGroupData]
    total: int


class HookData(BaseModel):
    """Sosyal medya hook."""
    text: str
    style: str


class SocialContentData(BaseModel):
    """Tek sosyal medya içeriği."""
    id: int
    idea_title: str
    category_name: Optional[str] = None

    platform: str
    content_format: str
    trend_alignment: float = 0.0

    hooks: List[HookData] = []
    caption: str = ""
    scenario: Optional[str] = None
    hashtags: List[str] = []
    cta_text: Optional[str] = None
    # Plan v4 alan matrisi (tur-4 #2): DB'de olup export'a taşınmayan alanlar
    visual_suggestion: Optional[str] = None
    video_concept: Optional[str] = None
    industry_posting_suggestion: Optional[str] = None
    platform_notes: Optional[str] = None

    # Brief akışı (plan_social_brief_akisi.md §8) — hepsi opsiyonel, geriye
    # uyumlu: brief_id=None olan (legacy) içeriklerde dolmaz
    brief_id: Optional[int] = None
    idea_keyword: Optional[str] = Field(
        default=None, description="Fikrin bağlı olduğu ana kelime (varsa)"
    )
    duration_preset_id: Optional[str] = None
    duration_min_sec: Optional[int] = None
    duration_max_sec: Optional[int] = None
    actual_duration_sec: Optional[int] = None
    duration_status: Optional[str] = None
    validation_warnings: List[str] = Field(default_factory=list)
    format_payload: Optional[Dict[str, Any]] = Field(
        default=None, description="Formata özel yapılandırılmış çıktı (video/carousel/thread)"
    )


class SocialBriefKeywordData(BaseModel):
    """Brief'e bağlı tek anahtar kelime (rapor grubu başlığı için)."""
    keyword_id: Optional[int] = None
    keyword: str
    position: int = 0


class SocialBriefGroupData(BaseModel):
    """Sosyal rapor brief bazında gruplama (plan §8).

    `brief_id=None` legacy (brief'siz) grubu temsil eder ve raporda
    "Eski (brief'siz)" başlığıyla gösterilir.
    """
    brief_id: Optional[int] = None
    label: str
    created_at: Optional[datetime] = None
    is_stale: bool = False
    keywords: List[SocialBriefKeywordData] = Field(default_factory=list)
    contents: List[SocialContentData] = Field(default_factory=list)


class SocialData(BaseModel):
    """Tüm sosyal medya verileri."""
    categories: List[Dict[str, Any]] = []
    ideas: List[Dict[str, Any]] = []
    contents: List[SocialContentData] = []
    # Plan §8: rapor brief bazında gruplanır; contents alanı geriye uyumluluk
    # için düz liste olarak kalır (brief_groups'un birleşimiyle aynı içerik).
    brief_groups: List[SocialBriefGroupData] = Field(default_factory=list)


class BrandProfileCompetitorData(BaseModel):
    """Rakip site analizi ozeti."""
    url: str
    status: Optional[str] = None
    summary: Optional[str] = None
    consistency_score: Optional[float] = None


class BrandProfileData(BaseModel):
    """Site analizinden uretilen marka/firma profili."""
    status: str
    company_url: Optional[str] = None
    competitor_urls: List[str] = Field(default_factory=list)
    company_name: Optional[str] = None
    sector: Optional[str] = None
    target_audience: Optional[str] = None
    products: List[str] = Field(default_factory=list)
    services: List[str] = Field(default_factory=list)
    use_cases: List[str] = Field(default_factory=list)
    problems_solved: List[str] = Field(default_factory=list)
    brand_terms: List[str] = Field(default_factory=list)
    protected_themes: List[str] = Field(default_factory=list)
    exclude_themes: List[str] = Field(default_factory=list)
    anchor_texts: List[str] = Field(default_factory=list)
    source_pages: List[Dict[str, Any]] = Field(default_factory=list)
    competitors: List[BrandProfileCompetitorData] = Field(default_factory=list)
    validation_warnings: List[str] = Field(default_factory=list)
    error_message: Optional[str] = None


# ==================== FULL REPORT ====================

class FullReport(BaseModel):
    """Tüm export verilerini birleştiren ana model."""
    scoring_run_id: int
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    
    summary: Optional[SummaryData] = None
    brand_profile: Optional[BrandProfileData] = None
    scoring: Optional[ScoringData] = None
    channels: Optional[ChannelsData] = None
    seo_contents: Optional[SEOContentsData] = None
    ads: Optional[AdsData] = None
    social: Optional[SocialData] = None
