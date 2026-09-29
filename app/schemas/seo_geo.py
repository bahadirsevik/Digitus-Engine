"""
Pydantic schemas for SEO+GEO Content Generation.
Roadmap2.md - Bölüm 6
"""
from datetime import datetime
from typing import Optional, List, Dict, Any
from decimal import Decimal
from pydantic import BaseModel, Field, ConfigDict


# ==================== REQUEST SCHEMAS ====================

class SEOGEOGenerateRequest(BaseModel):
    """Request schema for SEO+GEO content generation."""
    keyword_id: int = Field(..., description="Anahtar kelime ID")
    # Tekil endpoint workspace dogrulamasi + marka baglami icin kullanir
    # (codex: endpoint bu alani okuyordu ama semada yoktu -> 500)
    scoring_run_id: Optional[int] = Field(None, description="Scoring run ID (workspace scope)")
    sector: Optional[str] = Field(None, description="Sektör bilgisi")
    target_market: Optional[str] = Field(None, description="Hedef pazar")
    tone: str = Field(default="informative", description="İçerik tonu: informative, professional, casual")
    word_count_min: int = Field(default=500, ge=300, le=700, description="Minimum kelime sayısı")
    word_count_max: int = Field(default=600, ge=400, le=800, description="Maximum kelime sayısı")


class SEOGEOBulkRequest(BaseModel):
    """Request schema for bulk SEO+GEO content generation."""
    scoring_run_id: int = Field(..., description="Scoring run ID")
    limit: Optional[int] = Field(None, ge=1, le=100, description="Üretilecek maksimum içerik sayısı")
    tone: str = Field(default="informative", description="İçerik tonu")


# ==================== CONTENT STRUCTURE ====================

class BulletPoint(BaseModel):
    """Bullet point item."""
    text: str = Field(..., description="Madde metni")
    order: int = Field(..., description="Sıralama")


class FAQItem(BaseModel):
    """FAQ schema'ya hazır soru-cevap VERİSİ (şirket checklist P90).

    FAQPage JSON-LD üretilmez/uygulanmaz; yayın sisteminde eklenir."""
    question: str = Field(..., description="Soru")
    answer: str = Field(..., description="1-2 cümlelik net cevap")


class ContentStructure(BaseModel):
    """Generated content structure from AI."""
    title: str = Field(..., max_length=70, description="SEO uyumlu başlık (max 70 karakter)")
    url_suggestion: str = Field(..., description="URL önerisi (slug formatında)")
    intro_paragraph: str = Field(..., description="Snippet uyumlu giriş paragrafı (2-3 cümle)")
    subheadings: List[str] = Field(..., description="Alt başlıklar listesi")
    body_sections: List[str] = Field(..., description="Her alt başlık için içerik")
    bullet_points: List[BulletPoint] = Field(default_factory=list, description="Madde işaretli liste")
    internal_link_anchor: Optional[str] = Field(None, description="Internal link anchor text")
    internal_link_suggestion: Optional[str] = Field(None, description="Internal link URL önerisi")
    external_link_anchor: Optional[str] = Field(None, description="External link anchor text")
    external_link_url: Optional[str] = Field(None, description="External link URL")
    meta_description: str = Field(..., max_length=160, description="Meta description (max 160 karakter)")
    # Şirket checklist alanları — default'lu: eski AI yanıtları alansız da parse olur
    faq_items: List[FAQItem] = Field(default_factory=list, description="FAQ schema'ya hazır soru-cevap çiftleri")
    image_alt_texts: List[str] = Field(
        default_factory=list,
        description="Görsel alt metni ÖNERİLERİ (gerçek görsele uygulanmış etiket değil)",
    )
    word_count: int = Field(..., description="Toplam kelime sayısı")
    keyword_count: int = Field(..., description="Anahtar kelime tekrar sayısı")
    keyword_density: float = Field(..., description="Keyword yoğunluğu (%)")


# ==================== SEO COMPLIANCE ====================

class SEOCheckItem(BaseModel):
    """Single SEO compliance check result."""
    criterion: str = Field(..., description="Kontrol kriteri adı")
    status: str = Field(..., description="Durum: pass, partial, fail, not_evaluated (NULL/eksik kayıt)")
    details: Optional[str] = Field(None, description="Detay açıklama")
    importance: str = Field(default="medium", description="Önem: critical, high, medium, low")


class SEOComplianceResultSchema(BaseModel):
    """SEO compliance check results - 11 criteria."""
    # Individual criteria results
    title_has_keyword: bool = Field(..., description="Başlık keyword içeriyor mu?")
    title_length_ok: bool = Field(..., description="Başlık ≤70 karakter mi?")
    url_has_keyword: bool = Field(..., description="URL'de keyword var mı?")
    intro_keyword_count: int = Field(..., description="Giriş paragrafında keyword sayısı")
    word_count_in_range: bool = Field(..., description="Kelime sayısı 300-450 arasında mı?")
    subheading_count_ok: bool = Field(..., description="≥3 alt başlık var mı?")
    subheadings_have_kw: bool = Field(..., description="Alt başlıklarda keyword var mı?")
    has_internal_link: bool = Field(..., description="Internal link var mı?")
    has_external_link: bool = Field(..., description="External link var mı?")
    has_bullet_list: bool = Field(..., description="Bullet list var mı?")
    sentences_readable: bool = Field(..., description="Cümleler okunabilir mi (≤20 kelime)?")
    
    # Summary
    checks: List[SEOCheckItem] = Field(..., description="Tüm kontrol sonuçları")
    total_passed: int = Field(..., description="Geçen kriter sayısı")
    total_checks: int = Field(default=11, description="Toplam kriter sayısı")
    score: float = Field(..., ge=0, le=1, description="SEO uyumluluk skoru (0-1)")
    improvement_notes: Optional[str] = Field(None, description="İyileştirme önerileri")


# ==================== GEO COMPLIANCE ====================

class GEOCheckItem(BaseModel):
    """Single GEO compliance check result."""
    criterion: str = Field(..., description="Kontrol kriteri adı")
    passed: bool = Field(..., description="Geçti mi?")
    reasoning: Optional[str] = Field(None, description="AI değerlendirme açıklaması")


class GEOComplianceResultSchema(BaseModel):
    """GEO compliance check results - 7 criteria (AI evaluated)."""
    # Individual criteria results  
    intro_answers_question: bool = Field(..., description="İlk paragraf soruya yanıt veriyor mu?")
    snippet_extractable: bool = Field(..., description="AI snippet olarak alabilir mi?")
    info_hierarchy_strong: bool = Field(..., description="Özet→Detay→Örnek yapısı var mı?")
    tone_is_informative: bool = Field(..., description="Ton bilgilendirici mi?")
    no_fluff_content: bool = Field(..., description="Dolgu içerik yok mu?")
    direct_answer_present: bool = Field(..., description="İlk 50 kelimede yanıt var mı?")
    has_verifiable_info: bool = Field(..., description="Doğrulanabilir bilgi var mı?")
    
    # Summary
    checks: List[GEOCheckItem] = Field(..., description="Tüm kontrol sonuçları")
    total_passed: int = Field(..., description="Geçen kriter sayısı")
    total_checks: int = Field(default=7, description="Toplam kriter sayısı")
    score: float = Field(..., ge=0, le=1, description="GEO uyumluluk skoru (0-1)")
    ai_snippet_preview: Optional[str] = Field(None, description="AI'ın alıntılayacağı kısım")
    improvement_notes: Optional[str] = Field(None, description="İyileştirme önerileri")
    evaluation_source: Optional[str] = Field(
        None, description="'ai' = AI değerlendirdi; 'fallback' = değerlendirilemedi"
    )


# ==================== PUBLISH REVIEW ====================

class PublishReviewFailure(BaseModel):
    """Yayın öncesi incelemeyi gerektiren kritik madde."""
    criterion: str
    source: str = Field(..., description="seo_auto (programatik) | geo_ai (AI değerlendirmesi)")
    detail: str = ""


class PublishReview(BaseModel):
    """Yayın öncesi inceleme özeti (okuma anında türetilir, DB kolonu yok)."""
    required: bool = Field(..., description="Kritik checklist maddesi başarısız mı?")
    status: str = Field(..., description="review_required | manual_check_only")
    label: str
    critical_failures: List[PublishReviewFailure] = Field(default_factory=list)
    manual_checks: List[str] = Field(
        default_factory=list, description="Sistemde verisi olmayan, yayın ekibinin kontrol edeceği maddeler"
    )


# ==================== RESPONSE SCHEMAS ====================

class SEOGEOContentResponse(BaseModel):
    """Response schema for generated SEO+GEO content."""
    id: int
    keyword_id: int
    keyword: str
    
    # Content
    content: ContentStructure
    
    # Compliance scores
    seo_compliance: SEOComplianceResultSchema
    geo_compliance: GEOComplianceResultSchema
    combined_score: float = Field(..., description="(SEO + GEO) / 2")
    publish_review: Optional[PublishReview] = None

    generated_at: datetime
    
    model_config = ConfigDict(from_attributes=True)


class SEOGEOBulkResponse(BaseModel):
    """Response schema for bulk generation."""
    scoring_run_id: int
    total_keywords: int
    generated: int
    failed: int
    results: List[SEOGEOContentResponse]
    errors: List[Dict[str, Any]] = []
    average_seo_score: float
    average_geo_score: float
    average_combined_score: float


class ComplianceReportResponse(BaseModel):
    """Detailed compliance report for a content."""
    content_id: int
    keyword: str
    
    seo_compliance: SEOComplianceResultSchema
    geo_compliance: GEOComplianceResultSchema
    combined_score: float
    
    # Actionable improvements
    critical_issues: List[str] = Field(default=[], description="Kritik sorunlar")
    recommendations: List[str] = Field(default=[], description="İyileştirme önerileri")
    publish_review: Optional[PublishReview] = None
    
    checked_at: datetime
