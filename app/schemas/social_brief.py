# -*- coding: utf-8 -*-
"""Pydantic v2 schemas for Social Brief flow and Platform/Format Matrix."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator



class DurationPresetResponse(BaseModel):
    """Süre aralığı ön ayar yanıtı."""
    id: str = Field(..., description="Ön ayar tekil kimliği, örn: short_1_15, x_91_140")
    label: str = Field(..., description="Kullanıcıya gösterilecek süre etiketi")
    min_sec: int = Field(..., ge=1, description="Minimum süre (saniye)")
    max_sec: int = Field(..., ge=1, description="Maksimum süre (saniye)")


class SocialFormatOptionResponse(BaseModel):
    """Platforma özel içerik formatı seçeneği."""
    id: str = Field(..., description="Format kanonik kimliği, örn: post, reels, video")
    label: str = Field(..., description="Kullanıcıya gösterilecek format etiketi")
    requires_duration: bool = Field(..., description="Bu format için süre seçimi zorunlu mu?")
    media_mode: Optional[str] = Field(None, description="Özel medya modu, örn: story için 'static'")
    duration_profile: Optional[str] = Field(None, description="Bağlı süre profili (short_video, long_video, x_video)")
    duration_presets: List[DurationPresetResponse] = Field(
        default_factory=list,
        description="Format için seçilebilir süre aralıkları"
    )


class SocialPlatformOptionResponse(BaseModel):
    """Desteklenen sosyal medya platformu ve format seçenekleri."""
    id: str = Field(..., description="Platform kanonik kimliği, örn: instagram, twitter")
    label: str = Field(..., description="Kullanıcıya gösterilecek platform etiketi, örn: Instagram, X")
    formats: List[SocialFormatOptionResponse] = Field(
        default_factory=list,
        description="Platform altında desteklenen formatlar"
    )


class SocialFormatMatrixLimitsResponse(BaseModel):
    """Sosyal brief akış sınırları."""
    min_keywords: int = Field(1, description="Seçilebilecek minimum keyword sayısı")
    max_keywords: int = Field(5, description="Seçilebilecek maksimum keyword sayısı")
    min_targets: int = Field(1, description="Seçilebilecek minimum hedef (platform-format) sayısı")
    max_targets: int = Field(6, description="Seçilebilecek maksimum hedef (platform-format) sayısı")


class SocialFormatMatrixResponse(BaseModel):
    """Sosyal brief platform, format ve süre matrisi ana yanıtı."""
    version: str = Field(..., description="Matris sözleşme sürümü, örn: v1")
    limits: SocialFormatMatrixLimitsResponse = Field(..., description="Akış sınırları")
    platforms: List[SocialPlatformOptionResponse] = Field(
        default_factory=list,
        description="Desteklenen platformlar ve format listeleri"
    )


# ==================== REQUEST SCHEMAS (F1-D.2) ====================

class SocialBriefTargetCreateRequest(BaseModel):
    """Sosyal brief hedefi oluşturma isteği."""
    model_config = ConfigDict(extra="forbid", strict=True)

    platform: str = Field(..., description="Sosyal medya platformu (örn: instagram, twitter)")
    content_format: str = Field(..., description="Platform formatı (örn: post, reels, video)")
    duration_preset_id: Optional[str] = Field(
        None,
        description="Opsiyonel süre ön ayar kimliği (video formatları için zorunludur)",
    )


class SocialBriefCreateRequest(BaseModel):
    """Sosyal brief oluşturma isteği."""
    model_config = ConfigDict(extra="forbid", strict=True)

    scoring_run_id: int = Field(..., gt=0, description="Bağlı scoring run kimliği")
    keyword_ids: List[int] = Field(..., description="Hedeflenen anahtar kelime kimlikleri (1-5 adet)")
    targets: List[SocialBriefTargetCreateRequest] = Field(
        ...,
        description="Hedef platform ve format listesi (1-6 adet)",
    )
    brand_name: Optional[str] = Field(None, max_length=200, description="Opsiyonel marka adı")
    brand_context: Optional[str] = Field(None, description="Opsiyonel marka bağlamı veya brief notları")


# ==================== RESPONSE SCHEMAS (F1-D.4) ====================

class SocialBriefKeywordResponse(BaseModel):
    """Sosyal brief'e ait anahtar kelime yanıtı."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    keyword_id: int
    keyword_snapshot: str
    position: int


class SocialBriefTargetResponse(BaseModel):
    """Sosyal brief'e ait hedef yanıtı."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    platform: str
    content_format: str
    duration_preset_id: Optional[str] = None
    duration_min_sec: Optional[int] = None
    duration_max_sec: Optional[int] = None


class SocialBriefResponse(BaseModel):
    """Sosyal brief ana yanıtı."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    scoring_run_id: int
    brand_name_snapshot: Optional[str] = None
    brand_context_snapshot: Optional[str] = None
    channel_assignment_version: int
    format_matrix_version: str
    locked_at: Optional[datetime] = None
    is_stale: bool
    created_at: Optional[datetime] = None
    keywords: List[SocialBriefKeywordResponse] = Field(default_factory=list)
    targets: List[SocialBriefTargetResponse] = Field(default_factory=list)


# ==================== CATEGORIES GENERATE REQUEST (F1-E.1) ====================

class SocialBriefCategoriesGenerateRequest(BaseModel):
    """Sosyal brief kategori üretimi başlatma isteği."""

    model_config = ConfigDict(extra="forbid", strict=True)

    idempotency_key: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="İşlem tekillik anahtarı (1-128 karakter, strict string)",
    )
    max_categories: int = Field(
        default=4,
        ge=2,
        le=6,
        description="Üretilecek maksimum kategori sayısı (2-6 arası, varsayılan 4)",
    )

    @field_validator("idempotency_key")
    @classmethod
    def validate_idempotency_key(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("idempotency_key whitespace-only olamaz.")
        return v


# ==================== IDEAS GENERATE REQUEST (F1-F.4) ====================

class SocialBriefIdeasGenerateRequest(BaseModel):
    """Sosyal brief fikir üretimi başlatma isteği."""

    model_config = ConfigDict(extra="forbid", strict=True)

    idempotency_key: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="İşlem tekillik anahtarı (1-128 karakter, strict string)",
    )
    category_ids: List[int] = Field(
        ...,
        min_length=1,
        max_length=6,
        description="Fikir üretilecek kategori ID listesi (1-6 adet)",
    )
    ideas_per_category: int = Field(
        default=3,
        ge=1,
        le=5,
        description="Kategori başına temel fikir kotası (1-5 arası, varsayılan 3)",
    )

    @field_validator("idempotency_key")
    @classmethod
    def validate_idempotency_key(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("idempotency_key whitespace-only olamaz.")
        return v

    @field_validator("category_ids")
    @classmethod
    def validate_category_ids(cls, v: List[int]) -> List[int]:
        if not (1 <= len(v) <= 6):
            raise ValueError("category_ids 1 ile 6 arasında öğe içermelidir.")
        seen: set[int] = set()
        for item in v:
            if isinstance(item, bool) or type(item) is not int or item <= 0:
                raise ValueError("Her category_id pozitif bir tamsayı olmalıdır.")
            if item in seen:
                raise ValueError("Mükerrer category_id tespit edildi.")
            seen.add(item)
        return v

    @field_validator("ideas_per_category")
    @classmethod
    def validate_ideas_per_category(cls, v: int) -> int:
        if isinstance(v, bool) or type(v) is not int:
            raise ValueError("ideas_per_category tamsayı olmalıdır.")
        if not (1 <= v <= 5):
            raise ValueError("ideas_per_category 1 ile 5 arasında olmalıdır.")
        return v


# ==================== IDEAS RETRY REQUEST (F1-F.7.1) ====================

class SocialBriefIdeasRetryRequest(BaseModel):
    """Sosyal brief fikir tekrar deneme (retry) başlatma isteği."""

    model_config = ConfigDict(extra="forbid", strict=True)

    idempotency_key: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="İşlem tekillik anahtarı (1-128 karakter, strict string)",
    )
    source_attempt_id: int = Field(
        ...,
        gt=0,
        description="Tekrar denenecek kaynak fikir attempt ID'si (pozitif tamsayı)",
    )

    @field_validator("idempotency_key")
    @classmethod
    def validate_idempotency_key(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("idempotency_key whitespace-only olamaz.")
        return v

    @field_validator("source_attempt_id")
    @classmethod
    def validate_source_attempt_id(cls, v: int) -> int:
        if isinstance(v, bool) or type(v) is not int or v <= 0:
            raise ValueError("source_attempt_id pozitif bir tamsayı olmalıdır.")
        return v


# ==================== CONTENTS GENERATE REQUEST (F1-G.5.7.1) ====================

class SocialBriefContentsGenerateRequest(BaseModel):
    """Sosyal brief içerik üretimi başlatma isteği (F1-G.5.7.1)."""

    model_config = ConfigDict(extra="forbid", strict=True)

    idempotency_key: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="İşlem tekillik anahtarı (1-128 karakter, strict string)",
    )
    idea_ids: List[int] = Field(
        ...,
        min_length=1,
        max_length=30,
        description="İçerik üretilecek fikir ID listesi (1-30 adet, pozitif int, benzersiz)",
    )
    trusted_brand_usp: Optional[str] = Field(
        default=None,
        max_length=5000,
        description="Opsiyonel kullanıcı marka USP'si (en fazla 5000 karakter, trim edilmiş)",
    )

    @field_validator("idempotency_key")
    @classmethod
    def validate_idempotency_key(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("idempotency_key whitespace-only olamaz.")
        return v

    @field_validator("idea_ids")
    @classmethod
    def validate_idea_ids(cls, v: List[int]) -> List[int]:
        if not (1 <= len(v) <= 30):
            raise ValueError("idea_ids 1 ile 30 arasında öğe içermelidir.")
        seen: set[int] = set()
        for item in v:
            if isinstance(item, bool) or type(item) is not int or item <= 0:
                raise ValueError("Her idea_id pozitif bir tamsayı olmalıdır.")
            if item in seen:
                raise ValueError("Mükerrer idea_id tespit edildi.")
            seen.add(item)
        return list(v)

    @field_validator("trusted_brand_usp")
    @classmethod
    def validate_trusted_brand_usp(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            if isinstance(v, bool) or not isinstance(v, str):
                raise ValueError("trusted_brand_usp string olmalıdır.")
            if not v.strip():
                raise ValueError("trusted_brand_usp boş veya whitespace-only olamaz.")
            if v != v.strip():
                raise ValueError("trusted_brand_usp başında ve sonunda boşluk içeremez.")
            if len(v) > 5000:
                raise ValueError("trusted_brand_usp 5000 karakter sınırını aşamaz.")
            return v
        return None


# ==================== CATEGORIES GENERATE RESPONSE (F1-E.5) ====================

class SocialGeneratedCategoryResponse(BaseModel):
    """Sosyal kategori üretim yanıtı tekil kategori şeması."""

    model_config = ConfigDict(extra="forbid")

    id: int = Field(..., description="Kayıtlı kategori tekil ID'si")
    category_name: str = Field(..., description="Kategori adı")
    category_type: str = Field(..., description="Kanonik kategori tipi")
    description: str = Field(..., description="Kategori açıklaması")
    relevance_score: float = Field(..., description="Uygunluk skoru (0.0 - 1.0)")
    suggested_keyword_ids: List[int] = Field(..., description="Önerilen keyword ID listesi")


class SocialCategoriesGenerateResponse(BaseModel):
    """Sosyal brief kategori üretim yanıtı ana şeması."""

    model_config = ConfigDict(extra="forbid")

    brief_id: int = Field(..., description="Bağlı brief ID")
    scoring_run_id: int = Field(..., description="Bağlı run ID")
    attempt_id: int = Field(..., description="Kategori attempt ID")
    attempt_status: str = Field(..., description="Attempt durumu (completed, pending, etc.)")
    total_categories: int = Field(..., description="Dönen toplam kategori sayısı")
    categories: List[SocialGeneratedCategoryResponse] = Field(
        default_factory=list,
        description="Üretilen / okunan kategoriler",
    )
    ai_calls_used: Optional[int] = Field(None, description="Kullanılan AI çağrısı sayısı (1 veya 2)")
    replayed: bool = Field(..., description="Sonuç replay (önceki attempt'ten) mi döndü?")


# ==================== IDEAS GENERATE RESPONSE (F1-F.6d.1) ====================

class SocialGeneratedIdeaResponse(BaseModel):
    """Sosyal brief fikir üretim yanıtı tekil fikir şeması."""

    model_config = ConfigDict(extra="forbid")

    id: int = Field(..., gt=0, description="Kayıtlı fikir tekil ID'si")
    category_id: int = Field(..., gt=0, description="Bağlı kategori ID'si")
    keyword_id: int = Field(..., gt=0, description="Bağlı anahtar kelime ID'si")
    brief_id: int = Field(..., gt=0, description="Bağlı brief ID'si")
    brief_target_id: int = Field(..., gt=0, description="Bağlı hedef (platform-format) ID'si")
    idea_title: str = Field(..., min_length=1, max_length=200, description="Fikir başlığı (1-200 karakter)")
    idea_description: str = Field(..., min_length=1, max_length=2000, description="Fikir açıklaması (1-2000 karakter)")
    target_platform: str = Field(..., min_length=1, max_length=50, description="Hedef sosyal medya platformu")
    content_format: str = Field(..., min_length=1, max_length=50, description="Hedef içerik formatı")
    trend_alignment: float = Field(..., ge=0.0, le=1.0, description="Trend uyum skoru (0.0 - 1.0)")
    is_stale: bool = Field(..., description="Fikrin stale (eski/geçersizleşmiş) olma durumu")


class SocialIdeaCoverageResponse(BaseModel):
    """Sosyal brief hedef bazlı fikir kapsama yanıt şeması."""

    model_config = ConfigDict(extra="forbid")

    target_id: int = Field(..., gt=0, description="Bağlı hedef (platform-format) ID'si")
    requested: int = Field(..., ge=0, description="Hedeflenen/planlanan fikir adedi")
    accepted: int = Field(..., ge=0, description="Kabul edilen/üretilmiş fikir adedi")
    missing: int = Field(..., ge=0, description="Eksik kalan fikir adedi")


class SocialIdeaWarningResponse(BaseModel):
    """Sosyal brief fikir üretimi deneme uyarısı şeması."""

    model_config = ConfigDict(extra="forbid")

    target_id: int = Field(..., gt=0, description="İlgili hedef ID'si")
    category_id: Optional[int] = Field(None, gt=0, description="Opsiyonel kategori ID'si")
    reason_code: str = Field(..., min_length=1, max_length=100, description="Uyarı gerekçe kodu")


class SocialIdeasGenerateResponse(BaseModel):
    """Sosyal brief fikir üretim yanıtı ana şeması."""

    model_config = ConfigDict(extra="forbid")

    brief_id: int = Field(..., gt=0, description="Bağlı brief ID")
    scoring_run_id: int = Field(..., gt=0, description="Bağlı scoring run ID")
    attempt_id: int = Field(..., gt=0, description="Fikir attempt ID")
    attempt_status: str = Field(..., min_length=1, max_length=30, description="Attempt durumu")
    total_ideas: int = Field(..., ge=0, description="Dönen toplam fikir sayısı")
    ideas: List[SocialGeneratedIdeaResponse] = Field(
        default_factory=list,
        description="Üretilen / okunan fikir listesi",
    )
    coverage: List[SocialIdeaCoverageResponse] = Field(
        default_factory=list,
        description="Hedef bazlı kapsama detayları",
    )
    warnings: List[SocialIdeaWarningResponse] = Field(
        default_factory=list,
        description="Üretim sırasında oluşan uyarılar",
    )
    reason_code: Optional[str] = Field(None, max_length=100, description="Varsa durum veya hata gerekçe kodu")
    replayed: bool = Field(..., description="Sonuç replay (önceki attempt'ten) mi döndü?")


# ==================== CONTENTS GENERATE RESPONSE (F1-G.5.7.3) ====================

class SocialContentHookResponse(BaseModel):
    """Sosyal içerik kanca (hook) tekil yanıt şeması (F1-G.5.7.3a)."""

    model_config = ConfigDict(extra="forbid", strict=True)

    text: str = Field(..., min_length=1, max_length=500, description="Kanca metni")
    style: str = Field(..., min_length=1, max_length=50, description="Kanca stili")
    ab_score: Optional[float] = Field(None, ge=0.0, le=1.0, description="Varsa A/B test skoru (0.0 - 1.0)")


class SocialContentWarningResponse(BaseModel):
    """Sosyal içerik deneme uyarısı şeması."""

    model_config = ConfigDict(extra="forbid")

    idea_id: int = Field(..., gt=0, description="Uyarının ait olduğu fikir ID'si")
    reason_code: str = Field(..., min_length=1, max_length=100, description="Uyarı gerekçe kodu")
    claims: List[str] = Field(default_factory=list, description="Varsa gerekçesiz iddia metinleri")
    ai_calls_used: int = Field(..., ge=0, description="Bu öğe için harcanan AI çağrı sayısı")


class SocialGeneratedContentItemResponse(BaseModel):
    """Kayıtlı veya üretilmiş sosyal içerik tekil yanıt şeması (20 alan)."""

    model_config = ConfigDict(extra="forbid")

    id: int = Field(..., gt=0, description="Kayıtlı içerik tekil ID'si")
    idea_id: int = Field(..., gt=0, description="Bağlı fikir ID'si")
    brief_id: int = Field(..., gt=0, description="Bağlı brief ID'si")
    target_id: int = Field(..., gt=0, description="Bağlı hedef ID'si")
    platform: str = Field(..., min_length=1, max_length=50, description="Hedef sosyal medya platformu")
    content_format: str = Field(..., min_length=1, max_length=50, description="Hedef içerik formatı")
    hooks: List[SocialContentHookResponse] = Field(..., description="Kanca alternatifleri listesi")
    caption: str = Field(..., description="İçerik ana metni / caption")
    scenario: Optional[str] = Field(None, description="Varsa video senaryosu")
    format_payload: Optional[Dict[str, Any]] = Field(None, description="Format-özel yük (video/carousel/thread)")
    visual_suggestion: Optional[str] = Field(None, description="Görsel önerisi")
    video_concept: Optional[str] = Field(None, description="Video konsepti")
    cta_text: Optional[str] = Field(None, description="Çağrı metni (CTA)")
    hashtags: List[str] = Field(default_factory=list, description="Hashtag listesi")
    industry_posting_suggestion: Optional[str] = Field(None, description="Sektörel paylaşım önerisi")
    platform_notes: Optional[str] = Field(None, description="Platforma özel notlar")
    duration_status: str = Field(..., description="Süre durumu")
    actual_duration_sec: Optional[int] = Field(None, ge=0, description="Varsa hesaplanan süre (saniye)")
    validation_warnings: List[str] = Field(default_factory=list, description="Doğrulama uyarıları")
    is_stale: bool = Field(..., description="İçeriğin stale olma durumu")


class SocialBriefContentsAttemptResponse(BaseModel):
    """Sosyal brief içerik üretim / polling yanıtı ana şeması."""

    model_config = ConfigDict(extra="forbid")

    brief_id: int = Field(..., gt=0, description="Bağlı brief ID")
    scoring_run_id: int = Field(..., gt=0, description="Bağlı scoring run ID")
    attempt_id: int = Field(..., gt=0, description="İçerik attempt ID")
    attempt_status: str = Field(..., min_length=1, max_length=30, description="Attempt durumu")
    requested_idea_ids: List[int] = Field(default_factory=list, description="Talep edilen fikir ID listesi")
    successful_idea_ids: List[int] = Field(default_factory=list, description="Başarıyla üretilen fikir ID listesi")
    unresolved_idea_ids: List[int] = Field(default_factory=list, description="Üretilemeyen/çözülemeyen fikir ID listesi")
    total_contents: int = Field(..., ge=0, description="Dönen toplam içerik sayısı")
    contents: List[SocialGeneratedContentItemResponse] = Field(
        default_factory=list,
        description="Üretilen / okunan içerik listesi",
    )
    warnings: List[SocialContentWarningResponse] = Field(
        default_factory=list,
        description="Üretim sırasında oluşan uyarılar",
    )
    reason_code: Optional[str] = Field(None, max_length=100, description="Varsa durum veya hata gerekçe kodu")
    replayed: bool = Field(..., description="Sonuç replay (önceki attempt'ten) mi döndü?")


# ==================== BRIEF STATE (F1-H.2, salt-okunur) ====================

class SocialAttemptSummaryResponse(BaseModel):
    """Ekranın attempt keşfi için gereken en küçük özet.

    task_id, lease/worker ayrıntısı, coverage snapshot'ı, product facts veya USP taşımaz.
    """

    model_config = ConfigDict(extra="forbid")

    id: int = Field(..., gt=0)
    stage: str
    status: str
    reason_code: Optional[str] = None
    created_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    lease_expired: bool = Field(
        False,
        description="pending/running iken lease süresi dolmuşsa true (worker yanıt vermiyor olabilir)",
    )
    requested_idea_ids: List[int] = Field(
        default_factory=list, description="Yalnız contents aşamasında dolu"
    )


class SocialBriefStateResponse(BaseModel):
    """Brief ekranını sunucudan yeniden kurmak için durum özeti."""

    model_config = ConfigDict(extra="forbid")

    brief_id: int
    scoring_run_id: int
    is_stale: bool
    locked_at: Optional[datetime] = None
    category_attempt: Optional[SocialAttemptSummaryResponse] = None
    categories: List[SocialGeneratedCategoryResponse] = Field(default_factory=list)
    ideas_attempt: Optional[SocialAttemptSummaryResponse] = None
    idea_retry_attempts: List[SocialAttemptSummaryResponse] = Field(
        default_factory=list, description="En yeniden eskiye"
    )
    content_attempts: List[SocialAttemptSummaryResponse] = Field(
        default_factory=list, description="En yeniden eskiye"
    )
    content_idea_ids: List[int] = Field(
        default_factory=list, description="Bu brief'te güncel içeriği bulunan fikir ID'leri"
    )


# ==================== CONTENT HISTORY (K13, salt-okunur) ====================

class SocialHistoryHookResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    style: Optional[str] = None


class SocialContentHistoryItemResponse(BaseModel):
    """Workspace geçmişindeki tek sosyal içerik (brief'li veya legacy)."""

    model_config = ConfigDict(extra="forbid")

    id: int
    idea_id: int
    idea_title: Optional[str] = None
    brief_id: Optional[int] = Field(None, description="Legacy (brief'siz) içerikte null")
    brief_is_stale: Optional[bool] = None
    scoring_run_id: int
    run_name: Optional[str] = None
    category_name: Optional[str] = None
    keyword: Optional[str] = None
    platform: Optional[str] = None
    content_format: Optional[str] = None
    hooks: List[SocialHistoryHookResponse] = Field(default_factory=list)
    caption: str
    scenario: Optional[str] = None
    format_payload: Optional[Dict[str, Any]] = None
    visual_suggestion: Optional[str] = None
    video_concept: Optional[str] = None
    cta_text: Optional[str] = None
    hashtags: List[str] = Field(default_factory=list)
    industry_posting_suggestion: Optional[str] = None
    platform_notes: Optional[str] = None
    duration_status: Optional[str] = None
    actual_duration_sec: Optional[int] = None
    duration_min_sec: Optional[int] = None
    duration_max_sec: Optional[int] = None
    validation_warnings: List[str] = Field(default_factory=list)
    is_stale: bool
    created_at: Optional[datetime] = None


class SocialContentHistoryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    brand_profile_id: int
    total: int
    limit: int
    offset: int
    has_more: bool
    items: List[SocialContentHistoryItemResponse] = Field(default_factory=list)

