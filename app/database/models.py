"""
SQLAlchemy ORM Models.
All database tables are defined here.
"""
from datetime import datetime
from typing import Optional, List
from sqlalchemy import (
    Column, Integer, SmallInteger, String, Text, Boolean, DateTime, Float,
    ForeignKey, ForeignKeyConstraint, Numeric, JSON, UniqueConstraint,
    Index, CheckConstraint, text, event, inspect
)
from sqlalchemy.orm import relationship, DeclarativeBase
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    """Base class for all models."""
    pass


class Keyword(Base):
    """
    Ana anahtar kelime tablosu.
    Her kelime iÃ§in temel metrikler ve Ã¶zellikler saklanÄ±r.
    normalized_keyword = case/diacritic-insensitive normalize edilmiÅŸ hali.
    monthly_volume, trend_12m, trend_3m, competition_score, sector, target_market, data_source:
        LEGACY — yeni scoring kodu WorkspaceKeyword snapshot'larÄ±nÄ± kullanÄ±r.
    """
    __tablename__ = "keywords"
    
    id = Column(Integer, primary_key=True, index=True)
    keyword = Column(String(500), nullable=False, index=True)
    normalized_keyword = Column(String(500), nullable=True, index=True)  # Phase A nullable; Phase C NOT NULL + UNIQUE
    monthly_volume = Column(Integer, nullable=False, default=0)         # LEGACY
    trend_12m = Column(Numeric(7, 2), nullable=False, default=0.00)     # LEGACY
    trend_3m = Column(Numeric(7, 2), nullable=False, default=0.00)      # LEGACY
    competition_score = Column(Numeric(3, 2), nullable=False, default=0.50)  # LEGACY
    sector = Column(String(200), index=True)                            # LEGACY
    target_market = Column(String(200))                                 # LEGACY
    is_active = Column(Boolean, default=True, index=True)
    data_source = Column(String(20), nullable=False, server_default="csv", index=True)  # LEGACY
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        CheckConstraint(
            "data_source IN ('csv', 'google_ads_api')",
            name='ck_keywords_data_source'
        ),
        Index('idx_keywords_normalized', 'normalized_keyword'),  # non-unique (Phase C'de UNIQUE olacak)
    )

    # Relationships
    scores = relationship("KeywordScore", back_populates="keyword", cascade="all, delete-orphan")
    intent_analyses = relationship("IntentAnalysis", back_populates="keyword", cascade="all, delete-orphan")
    channel_pools = relationship("ChannelPool", back_populates="keyword", cascade="all, delete-orphan")
    content_outputs = relationship("ContentOutput", back_populates="keyword", cascade="all, delete-orphan")
    channel_candidates = relationship("ChannelCandidate", back_populates="keyword", cascade="all, delete-orphan")
    workspace_keywords = relationship("WorkspaceKeyword", back_populates="keyword", cascade="all, delete-orphan")


class ScoringRun(Base):
    """
    Her skorlama Ã§alÄ±ÅŸtÄ±rmasÄ± iÃ§in meta bilgi.
    Bir run iÃ§inde tÃ¼m kelimeler skorlanÄ±r ve kanallara atanÄ±r.
    """
    __tablename__ = "scoring_runs"
    
    id = Column(Integer, primary_key=True, index=True)
    run_name = Column(String(200))
    brand_profile_id = Column(
        Integer,
        ForeignKey("brand_profiles.id", ondelete="RESTRICT"),
        nullable=True
    )  # Workspace baÄŸlantÄ±sÄ± (1:N)
    total_keywords = Column(Integer, nullable=False, default=0)
    ads_capacity = Column(Integer, nullable=False)   # KullanÄ±cÄ±nÄ±n istediÄŸi ADS kelime sayÄ±sÄ±
    seo_capacity = Column(Integer, nullable=False)   # KullanÄ±cÄ±nÄ±n istediÄŸi SEO kelime sayÄ±sÄ±
    social_capacity = Column(Integer, nullable=False)  # KullanÄ±cÄ±nÄ±n istediÄŸi SOCIAL kelime sayÄ±sÄ±
    default_relevance_coefficient = Column(
        Numeric(4, 2),
        nullable=False,
        server_default="1.0",
        default=1.0
    )  # Kanal atamasi relevance katsayisi (0.1 - 3.0)
    # Kanal enable/disable flag'leri
    enable_ads = Column(Boolean, nullable=False, default=True, server_default="true")
    enable_seo = Column(Boolean, nullable=False, default=True, server_default="true")
    enable_social = Column(Boolean, nullable=False, default=True, server_default="true")
    # Keyword seÃ§im modu
    keyword_selection_mode = Column(String(20), nullable=False, default="all", server_default="all")
    keyword_limit = Column(Integer, nullable=True)          # top_n modu iÃ§in
    selected_keyword_ids = Column(JSON, nullable=True)      # specific modu iÃ§in
    skip_relevance = Column(Boolean, nullable=False, default=False, server_default="false")
    auto_assign_channels = Column(Boolean, nullable=False, default=False, server_default="false")
    relevance_started_at = Column(DateTime(timezone=True))
    relevance_completed_at = Column(DateTime(timezone=True))
    status = Column(String(50), default="pending")   # pending, scoring, scored, relevance_computing, relevance_computed, channel_assigning, channel_assigned, intent_analysis, completed, failed
    company_url = Column(String(500))  # Opsiyonel firma URL (site profil analizi iÃ§in)
    competitor_urls = Column(JSON)     # Opsiyonel rakip URL listesi (max 3)
    keyword_source_filter = Column(String(20), nullable=True)
    # Run manifest (plan C): {git_sha, model_map, thinking_level, prompt_version}
    # SHA Docker build arg'indan gelir (runtime git komutu YOK)
    execution_manifest = Column(JSON, nullable=True)
    # Reassignment sayaci (Codex A+B bulgusu-1): SOCIAL uretimi uzun AI
    # cagrisi sirasinda reassignment olursa cikti "fresh" dogamaz — uretim
    # basinda snapshot alinir, kayittan once run kilitlenip karsilastirilir.
    channel_assignment_version = Column(
        Integer, nullable=False, default=1, server_default="1"
    )
    # Freshness sozlesmesi (plan v13): basariyla MATERYALIZE edilmis havuzlarin
    # uretildigi politika/anchor surumu. Yalniz basarili channel_assigned
    # finalize'inda, transition ile AYNI transaction'da yazilir. Karsilastirma
    # != ile yapilir (restore/backfill sonrasi run > workspace da stale sayilir).
    channel_pool_policy_version = Column(Integer, nullable=True)
    relevance_anchor_version = Column(Integer, nullable=True)
    # v2.1 (plan Faz C/E): algoritma sürümü run'da IMMUTABLE'dır; mevcut ve
    # yeni normal run'larda 'v2'. channel_pool_strategy_version yalnız
    # BAŞARILI v2_1 finalize'da dispatch snapshot'ından yazılır (strategy
    # freshness ekseni bunu okur; v2 run'lar stratejiden bağımsızdır).
    algorithm_version = Column(
        String(10), nullable=False, default="v3", server_default="v3"
    )
    channel_pool_strategy_version = Column(Integer, nullable=True)
    # Corpus screening (plan §6.1): YALNIZ create UI varsayilani; gercek
    # mode her assignment attempt'ine baglidir ve bunu EZMEZ.
    screening_preference = Column(
        String(20), nullable=False, server_default="off", default="off")
    # Son BASARILI havuz materyalizasyonunun screening kunyesi (plan §7.4);
    # freshness bu alanlari canli baglam SHA'si ile karsilastirir.
    channel_pool_screening_mode = Column(String(20), nullable=True)
    channel_pool_screening_context_sha256 = Column(String(64), nullable=True)
    started_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "keyword_source_filter IS NULL "
            "OR keyword_source_filter IN ('csv', 'google_ads_api')",
            name='ck_scoring_runs_source_filter'
        ),
        CheckConstraint(
            "keyword_selection_mode IN ('all', 'top_n', 'specific')",
            name='ck_scoring_runs_selection_mode'
        ),
    )

    # Relationships
    keyword_scores = relationship("KeywordScore", back_populates="scoring_run", cascade="all, delete-orphan")
    channel_candidates = relationship("ChannelCandidate", back_populates="scoring_run", cascade="all, delete-orphan")
    intent_analyses = relationship("IntentAnalysis", back_populates="scoring_run", cascade="all, delete-orphan")
    channel_pools = relationship("ChannelPool", back_populates="scoring_run", cascade="all, delete-orphan")
    ad_groups = relationship("AdGroup", back_populates="scoring_run", cascade="all, delete-orphan")
    social_categories = relationship("SocialCategory", back_populates="scoring_run", cascade="all, delete-orphan")
    social_briefs = relationship("SocialBrief", back_populates="scoring_run", cascade="all, delete-orphan")
    brand_profile_workspace = relationship(
        "BrandProfile",
        back_populates="scoring_runs",
        foreign_keys=[brand_profile_id],
    )



class KeywordScore(Base):
    """
    Her kelime iÃ§in hesaplanan skorlar.
    Bir scoring_run iÃ§inde her kelime iÃ§in ADS, SEO, SOCIAL skorlarÄ± hesaplanÄ±r.
    """
    __tablename__ = "keyword_scores"
    
    id = Column(Integer, primary_key=True, index=True)
    scoring_run_id = Column(Integer, ForeignKey("scoring_runs.id", ondelete="CASCADE"), nullable=False)
    keyword_id = Column(Integer, ForeignKey("keywords.id", ondelete="CASCADE"), nullable=False)
    
    # Ham skorlar (formÃ¼lden Ã§Ä±kan deÄŸerler)
    ads_score = Column(Numeric(15, 4))
    seo_score = Column(Numeric(15, 4))
    social_score = Column(Numeric(15, 4))
    
    # Kanal iÃ§i sÄ±ralama
    ads_rank = Column(Integer, index=True)     # ADS skoruna gÃ¶re sÄ±ralama (1 = en yÃ¼ksek)
    seo_rank = Column(Integer, index=True)     # SEO skoruna gÃ¶re sÄ±ralama
    social_rank = Column(Integer, index=True)  # SOCIAL skoruna gÃ¶re sÄ±ralama
    
    # Scoring anÄ±ndaki metrik snapshot'Ä± (app-level zorunlu)
    metrics_snapshot = Column(JSON, nullable=True)
    
    calculated_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    scoring_run = relationship("ScoringRun", back_populates="keyword_scores")
    keyword = relationship("Keyword", back_populates="scores")
    
    __table_args__ = (
        UniqueConstraint('scoring_run_id', 'keyword_id', name='uq_keyword_scores_run_keyword'),
        Index('idx_keyword_scores_run_ads_rank', 'scoring_run_id', 'ads_rank'),
        Index('idx_keyword_scores_run_seo_rank', 'scoring_run_id', 'seo_rank'),
        Index('idx_keyword_scores_run_social_rank', 'scoring_run_id', 'social_rank'),
    )


class ChannelCandidate(Base):
    """
    Niyet analizine alÄ±nacak adaylar (2x kapasite).
    Her kanal iÃ§in en yÃ¼ksek skorlu kelimeler aday olarak seÃ§ilir.
    """
    __tablename__ = "channel_candidates"
    
    id = Column(Integer, primary_key=True, index=True)
    scoring_run_id = Column(Integer, ForeignKey("scoring_runs.id", ondelete="CASCADE"), nullable=False)
    keyword_id = Column(Integer, ForeignKey("keywords.id", ondelete="CASCADE"), nullable=False)
    channel = Column(String(20), nullable=False)  # 'ADS', 'SEO', 'SOCIAL'
    raw_score = Column(Numeric(15, 4), nullable=False)
    rank_in_channel = Column(Integer, nullable=False)
    # Corpus screening kaynak atfi (plan §5.5) — NULL = screening'siz
    # (flag-off) yol; mevcut satirlar ve off run'lari etkilenmez.
    screening_job_id = Column(
        Integer, ForeignKey("corpus_screening_jobs.id", ondelete="SET NULL"),
        nullable=True)
    candidate_origin_source = Column(String(20), nullable=True)
    candidate_materialization_action = Column(String(20), nullable=True)
    screening_fit = Column(Numeric(6, 4), nullable=True)
    screening_rank = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    scoring_run = relationship("ScoringRun", back_populates="channel_candidates")
    keyword = relationship("Keyword", back_populates="channel_candidates")
    
    __table_args__ = (
        UniqueConstraint('scoring_run_id', 'keyword_id', 'channel', name='uq_channel_candidates'),
        Index('idx_channel_candidates_run_channel', 'scoring_run_id', 'channel'),
        Index('idx_channel_candidates_run_ch_rank', 'scoring_run_id', 'channel', 'rank_in_channel'),
        Index('idx_channel_candidates_screening_job', 'screening_job_id'),
        CheckConstraint(
            "candidate_origin_source IS NULL OR candidate_origin_source IN "
            "('baseline', 'screening', 'both', 'none')",
            name="ck_channel_candidates_origin_source"),
        CheckConstraint(
            "candidate_materialization_action IS NULL OR "
            "candidate_materialization_action IN "
            "('initial', 'transfer', 'expansion')",
            name="ck_channel_candidates_materialization_action"),
    )


class IntentAnalysis(Base):
    """
    Niyet analizi sonuÃ§larÄ±.
    AI ile her aday kelimenin kullanÄ±cÄ± niyeti analiz edilir.
    """
    __tablename__ = "intent_analysis"
    
    id = Column(Integer, primary_key=True, index=True)
    scoring_run_id = Column(Integer, ForeignKey("scoring_runs.id", ondelete="CASCADE"), nullable=False)
    keyword_id = Column(Integer, ForeignKey("keywords.id", ondelete="CASCADE"), nullable=False)
    channel = Column(String(20), nullable=False)  # Hangi kanal iÃ§in analiz yapÄ±ldÄ±
    intent_type = Column(String(50), nullable=False)  # transactional, informational, navigational, commercial, trend_worthy
    confidence_score = Column(Numeric(3, 2))  # 0.00 - 1.00
    ai_reasoning = Column(Text)  # AI'Ä±n aÃ§Ä±klamasÄ±
    is_passed = Column(Boolean, nullable=False, default=False, index=True)  # Filtreyi geÃ§ti mi?
    source = Column(String(20), server_default="ai")  # 'ai' veya 'transfer'
    # SEO niyet dereceleri (Skorlama v2 Aşama 2): seçim anında
    # N_SEO + SEO_W_GT*gt + SEO_W_GA*ga olarak eklenir. NULL -> 0 sayılır.
    gt = Column(Boolean, nullable=True)  # G_T: satın alma niyeti
    ga = Column(Boolean, nullable=True)  # G_A: ürün-kategori araması
    # v2.1 S_G (plan Faz C/D): içerik stratejisi beyanına uyum. gt'nin
    # anlamı DEĞİŞMEZ (denetim kaydı); v2_1 seçimi strategy_fit okur.
    # Eksik/parse fallback NULL kalır — sessizce 0 yapılmaz.
    strategy_fit = Column(Boolean, nullable=True)
    analyzed_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    scoring_run = relationship("ScoringRun", back_populates="intent_analyses")
    keyword = relationship("Keyword", back_populates="intent_analyses")
    
    __table_args__ = (
        UniqueConstraint('scoring_run_id', 'keyword_id', 'channel', name='uq_intent_analysis'),
        Index('idx_intent_analysis_run_channel', 'scoring_run_id', 'channel'),
        Index(
            'idx_intent_analysis_passed_lookup',
            'scoring_run_id', 'channel', 'keyword_id',
            postgresql_where=text('is_passed = true'),
        ),
    )

class PreFilterResult(Base):
    """
    AI Pre-Filter sonuÃ§larÄ±.
    Intent analizini geÃ§en keyword'lerin kanal-Ã¶zel filtreleme sonuÃ§larÄ±.
    """
    __tablename__ = "pre_filter_results"
    
    id = Column(Integer, primary_key=True, index=True)
    scoring_run_id = Column(Integer, ForeignKey("scoring_runs.id", ondelete="CASCADE"))
    keyword_id = Column(Integer, ForeignKey("keywords.id", ondelete="CASCADE"))
    channel = Column(String(20), nullable=False)
    
    is_kept = Column(Boolean, nullable=False, server_default="true")
    label = Column(String(50))
    # Skorlama v2 Aşama 2 sınıfı: ADS 2/1/-1 (öncelikli/uygun/elendi),
    # SOCIAL 0-3 (konuşulabilirlik boyutları toplamı). NULL = legacy/fallback.
    ai_class = Column(SmallInteger, nullable=True)
    ai_reasoning = Column(Text)
    extra_data = Column(JSON)  # Kanal-Ã¶zel ek veri (metadata DEÄÄ°L - SQLAlchemy Ã§akÄ±ÅŸmasÄ±)
    transfer_channel = Column(String(20))
    is_fallback = Column(Boolean, server_default="false")
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    scoring_run = relationship("ScoringRun")
    keyword = relationship("Keyword")
    
    __table_args__ = (
        UniqueConstraint('scoring_run_id', 'keyword_id', 'channel', name='uq_pre_filter'),
        Index('idx_pre_filter_run_channel', 'scoring_run_id', 'channel'),
        Index('idx_pre_filter_transfer', 'scoring_run_id', 'channel', 'is_kept', 'transfer_channel'),
    )


class ChannelPool(Base):
    """
    Final kanal havuzlarÄ±.
    Niyet analizini geÃ§en kelimeler kapasite kadar seÃ§ilir.
    """
    __tablename__ = "channel_pools"
    
    id = Column(Integer, primary_key=True, index=True)
    scoring_run_id = Column(Integer, ForeignKey("scoring_runs.id", ondelete="CASCADE"), nullable=False)
    keyword_id = Column(Integer, ForeignKey("keywords.id", ondelete="CASCADE"), nullable=False)
    channel = Column(String(20), nullable=False)  # 'ADS', 'SEO', 'SOCIAL'
    final_rank = Column(Integer, nullable=False)  # Final havuzdaki sÄ±ralama
    relevance_score = Column(Numeric(4, 3), nullable=True)  # 0.000 - 1.000 vector yakÄ±nlÄ±ÄŸÄ±
    adjusted_score = Column(Numeric(15, 4), nullable=True)  # raw_score * relevance blend sonucu
    is_strategic = Column(Boolean, default=False, index=True)  # Stratejik kelime mi? (ADS ∩ SEO)
    pool_label = Column(String(30), nullable=True)  # örn. 'rising_opportunity' (Yükselen Fırsat)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    scoring_run = relationship("ScoringRun", back_populates="channel_pools")
    keyword = relationship("Keyword", back_populates="channel_pools")
    
    __table_args__ = (
        UniqueConstraint('scoring_run_id', 'keyword_id', 'channel', name='uq_channel_pools'),
        Index('idx_channel_pools_run_channel', 'scoring_run_id', 'channel'),
        Index('idx_channel_pools_run_channel_rank', 'scoring_run_id', 'channel', 'final_rank'),
    )


class ContentOutput(Base):
    """
    Ãœretilen iÃ§erikler.
    Her kelime iÃ§in kanal bazlÄ± iÃ§erik Ã¼retilir ve saklanÄ±r.
    """
    __tablename__ = "content_outputs"
    
    id = Column(Integer, primary_key=True, index=True)
    scoring_run_id = Column(Integer, ForeignKey("scoring_runs.id", ondelete="SET NULL"))
    keyword_id = Column(Integer, ForeignKey("keywords.id", ondelete="CASCADE"), nullable=False)
    channel = Column(String(20), nullable=False, index=True)
    content_type = Column(String(50), nullable=False)  # 'blog_post', 'ad_group', 'social_post'
    content_data = Column(JSON, nullable=False)  # Ãœretilen iÃ§erik (JSON formatÄ±nda)
    
    # Uyumluluk skorlarÄ± (SEO+GEO iÃ§in)
    seo_compliance_score = Column(Numeric(3, 2))
    geo_compliance_score = Column(Numeric(3, 2))
    
    # Re-run sonrasÄ± eski iÃ§erik iÅŸaretleme
    is_stale = Column(Boolean, nullable=False, default=False, server_default="false")
    
    generated_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    keyword = relationship("Keyword", back_populates="content_outputs")
    compliance_checks = relationship("ComplianceCheck", back_populates="content_output", cascade="all, delete-orphan")


class ComplianceCheck(Base):
    """
    Uyumluluk kontrol detaylarÄ±.
    SEO ve GEO uyumluluÄŸu iÃ§in detaylÄ± kontrol sonuÃ§larÄ±.
    """
    __tablename__ = "compliance_checks"
    
    id = Column(Integer, primary_key=True, index=True)
    content_output_id = Column(Integer, ForeignKey("content_outputs.id", ondelete="CASCADE"), nullable=False)
    check_type = Column(String(20), nullable=False)  # 'SEO', 'GEO'
    criteria = Column(String(200), nullable=False)    # Kontrol edilen kriter
    status = Column(String(20), nullable=False)       # 'pass', 'partial', 'fail'
    notes = Column(Text)
    checked_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    content_output = relationship("ContentOutput", back_populates="compliance_checks")


# ==================== SEO+GEO Ä°Ã‡ERÄ°K MOTORU (Roadmap2 BÃ¶lÃ¼m 6) ====================

class SEOGeoContent(Base):
    """
    SEO+GEO uyumlu iÃ§erik tablosu.
    Tek iÃ§erik Ã¼retilir, hem SEO hem GEO kurallarÄ±na uygun.
    """
    __tablename__ = "seo_geo_contents"
    
    id = Column(Integer, primary_key=True, index=True)
    content_output_id = Column(Integer, ForeignKey("content_outputs.id", ondelete="CASCADE"))
    keyword_id = Column(Integer, ForeignKey("keywords.id", ondelete="CASCADE"), nullable=False)
    
    # Ä°Ã§erik bileÅŸenleri
    title = Column(String(100), nullable=False)  # SEO uyumlu baÅŸlÄ±k (max 70 kar)
    url_suggestion = Column(String(200))  # anahtar-kelime-url
    intro_paragraph = Column(Text, nullable=False)  # Snippet uyumlu giriÅŸ (2-3 cÃ¼mle)
    body_content = Column(Text, nullable=False)  # Ana iÃ§erik
    subheadings = Column(JSON)  # ["Alt BaÅŸlÄ±k 1", "Alt BaÅŸlÄ±k 2", ...]
    body_sections = Column(JSON)  # ["BÃ¶lÃ¼m 1 iÃ§eriÄŸi", ...]
    bullet_points = Column(JSON)  # [{"text": "...", "order": 1}, ...]
    
    # Link Ã¶nerileri
    internal_link_anchor = Column(String(200))
    internal_link_url = Column(String(500))
    external_link_anchor = Column(String(200))
    external_link_url = Column(String(500))
    
    # Sirket checklist alanlari (P90 FAQ schema verisi, P60 gorsel SEO)
    faq_items = Column(JSON)  # [{"question": "...", "answer": "..."}]
    image_alt_texts = Column(JSON)  # ["aciklayici alt text 1", ...]

    # Meta bilgiler
    meta_description = Column(String(160))  # 155 karakterlik aÃ§Ä±klama
    word_count = Column(Integer)
    subheading_count = Column(Integer)
    keyword_count = Column(Integer)
    keyword_density = Column(Numeric(4, 2))  # Ã–rn: 1.58%
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    keyword = relationship("Keyword")
    seo_compliance = relationship("SEOComplianceResult", back_populates="seo_geo_content", uselist=False, cascade="all, delete-orphan")
    geo_compliance = relationship("GEOComplianceResult", back_populates="seo_geo_content", uselist=False, cascade="all, delete-orphan")


class SEOComplianceResult(Base):
    """
    SEO Uyumluluk Kontrol SonuÃ§larÄ±.
    11 kriter programatik olarak kontrol edilir.
    """
    __tablename__ = "seo_compliance_results"
    
    id = Column(Integer, primary_key=True, index=True)
    seo_geo_content_id = Column(Integer, ForeignKey("seo_geo_contents.id", ondelete="CASCADE"), nullable=False)
    
    # 11 Kontrol Kriteri
    title_has_keyword = Column(Boolean)  # BaÅŸlÄ±k anahtar kelimeyi iÃ§eriyor mu?
    title_length_ok = Column(Boolean)  # â‰¤70 karakter mi?
    url_has_keyword = Column(Boolean)  # URL Ã¶nerisinde keyword var mÄ±?
    intro_keyword_count = Column(Integer)  # Ä°lk paragrafta keyword sayÄ±sÄ± (â‰¥2 olmalÄ±)
    word_count_in_range = Column(Boolean)  # 300-450 arasÄ± mÄ±?
    subheading_count_ok = Column(Boolean)  # â‰¥3 alt baÅŸlÄ±k var mÄ±?
    subheadings_have_kw = Column(Boolean)  # En az 1 alt baÅŸlÄ±kta keyword var mÄ±?
    has_internal_link = Column(Boolean)  # Internal link Ã¶nerisi var mÄ±?
    has_external_link = Column(Boolean)  # External link var mÄ±?
    has_bullet_list = Column(Boolean)  # Bullet list var mÄ±?
    sentences_readable = Column(Boolean)  # Ortalama cÃ¼mle â‰¤20 kelime mi?
    
    # SonuÃ§
    total_passed = Column(Integer)  # GeÃ§en kriter sayÄ±sÄ±
    total_score = Column(Numeric(3, 2))  # 0.00 - 1.00
    improvement_notes = Column(Text)  # Ä°yileÅŸtirme Ã¶nerileri
    # check() ciktisindaki TUM kriter listesi (checklist kriterleri dahil).
    # Eski satirlarda null -> rapor kolon-bazli kurulur (geriye uyum).
    checks_json = Column(JSON)
    checked_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    seo_geo_content = relationship("SEOGeoContent", back_populates="seo_compliance")


class GEOComplianceResult(Base):
    """
    GEO (AI Snippet) Uyumluluk Kontrol SonuÃ§larÄ±.
    7 kriter AI ile deÄŸerlendirilir.
    """
    __tablename__ = "geo_compliance_results"
    
    id = Column(Integer, primary_key=True, index=True)
    seo_geo_content_id = Column(Integer, ForeignKey("seo_geo_contents.id", ondelete="CASCADE"), nullable=False)
    
    # 7 Kontrol Kriteri (AI deÄŸerlendirmesi)
    intro_answers_question = Column(Boolean)  # Ä°lk paragraf soruya yanÄ±t veriyor mu?
    snippet_extractable = Column(Boolean)  # AI snippet olarak alabilir mi?
    info_hierarchy_strong = Column(Boolean)  # Ã–zetâ†’Detayâ†’Ã–rnek yapÄ±sÄ± var mÄ±?
    tone_is_informative = Column(Boolean)  # Bilgilendirici ve tarafsÄ±z ton mu?
    no_fluff_content = Column(Boolean)  # Gereksiz dolgu yok mu?
    direct_answer_present = Column(Boolean)  # Ä°lk 50 kelimede doÄŸrudan yanÄ±t var mÄ±?
    has_verifiable_info = Column(Boolean)  # Somut veri/Ã¶rnek/kaynak var mÄ±?
    
    # SonuÃ§
    total_passed = Column(Integer)  # GeÃ§en kriter sayÄ±sÄ±
    total_score = Column(Numeric(3, 2))  # 0.00 - 1.00
    ai_snippet_preview = Column(Text)  # AI'Ä±n muhtemelen alÄ±ntÄ±layacaÄŸÄ± kÄ±sÄ±m
    improvement_notes = Column(Text)  # Ä°yileÅŸtirme Ã¶nerileri
    checked_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    seo_geo_content = relationship("SEOGeoContent", back_populates="geo_compliance")


# ==================== GOOGLE ADS MODELS ====================

class AdGenerationSet(Base):
    """ADS üretim seti (versiyonlama — Seçim Güvenilirliği planı Faz E).

    Her başarılı üretim ayrı set; ilk başarılı set otomatik 'active',
    sonrakiler 'draft' (kullanıcı onayıyla aktifleşir, eski aktif 'archived').
    İki AYRI stale kavramı vardır:
      - task-timeout staleness (üretim yarım kaldı) -> status='failed'
      - içerik staleness (kanal reassignment) -> status KORUNUR, is_stale=True
    Export/dashboard yalnız status='active' AND is_stale=False okur.
    Eski setler ASLA silinmez.
    """
    __tablename__ = "ad_generation_sets"

    id = Column(Integer, primary_key=True, index=True)
    scoring_run_id = Column(
        Integer, ForeignKey("scoring_runs.id", ondelete="CASCADE"), nullable=False
    )
    # Legacy (backfill) setlerde NULL; yeni dispatch'lerde uygulama zorunlu kılar
    task_id = Column(String(155), nullable=True, unique=True)
    version_number = Column(Integer, nullable=False)
    # generating | active | draft | archived | failed
    status = Column(String(20), nullable=False, server_default="generating")
    is_stale = Column(Boolean, nullable=False, server_default="false")
    # operation ('full'|'group_regenerate'), brand parametreleri,
    # source_set_id/source_group_id (regenerate) vb.
    request_snapshot = Column(JSON)
    groups_count = Column(Integer)
    failed_groups = Column(Integer)
    warnings = Column(JSON)  # ayrıntılı uyarılar (grounding vb.)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True))

    # Relationships
    scoring_run = relationship("ScoringRun")
    ad_groups = relationship("AdGroup", back_populates="generation_set")

    __table_args__ = (
        UniqueConstraint(
            'scoring_run_id', 'version_number',
            name='uq_ad_generation_set_version',
        ),
        # Run başına TEK aktif set (partial unique index) — aktivasyon
        # sırası: eski aktif archive + FLUSH, sonra hedef active
        Index(
            'uq_ad_generation_set_single_active',
            'scoring_run_id',
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        Index('idx_ad_generation_set_run_status', 'scoring_run_id', 'status'),
    )


class AdGroup(Base):
    """
    Google Ads Reklam Grubu.
    Niyet bazlÄ± gruplandÄ±rÄ±lmÄ±ÅŸ kelimeleri iÃ§erir.
    """
    __tablename__ = "ad_groups"
    
    id = Column(Integer, primary_key=True, index=True)
    content_output_id = Column(Integer, ForeignKey("content_outputs.id", ondelete="CASCADE"))
    scoring_run_id = Column(Integer, ForeignKey("scoring_runs.id", ondelete="CASCADE"), nullable=False)
    # Versiyonlama: her grup bir üretim setine ait. scoring_run_id HER ZAMAN
    # generation_set.scoring_run_id'den türetilir (istemciden alınmaz).
    generation_set_id = Column(
        Integer,
        ForeignKey("ad_generation_sets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    group_name = Column(String(200), nullable=False)
    group_theme = Column(Text)
    target_keyword_ids = Column(JSON)  # [1, 5, 12, ...]
    target_keywords = Column(JSON)     # ["kelime1", "kelime2", ...]
    
    # Validation stats
    headlines_generated = Column(Integer, default=0)
    headlines_eliminated = Column(Integer, default=0)
    dki_converted_count = Column(Integer, default=0)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    headlines = relationship("AdHeadline", back_populates="ad_group", cascade="all, delete-orphan")
    descriptions = relationship("AdDescription", back_populates="ad_group", cascade="all, delete-orphan")
    negative_keywords = relationship("NegativeKeyword", back_populates="ad_group", cascade="all, delete-orphan")
    scoring_run = relationship("ScoringRun", back_populates="ad_groups")
    generation_set = relationship("AdGenerationSet", back_populates="ad_groups")


class AdHeadline(Base):
    """
    Google Ads RSA BaÅŸlÄ±ÄŸÄ±.
    Max 30 karakter, farklÄ± tipler.
    """
    __tablename__ = "ad_headlines"
    
    id = Column(Integer, primary_key=True, index=True)
    ad_group_id = Column(Integer, ForeignKey("ad_groups.id", ondelete="CASCADE"), nullable=False)
    
    headline_text = Column(String(30), nullable=False)  # Google Ads limiti
    headline_type = Column(String(20))  # keyword, cta, benefit, trust, dynamic
    position_preference = Column(String(20), default='any')  # any, position_1, position_2, position_3
    is_dki = Column(Boolean, default=False)  # Dynamic Keyword Insertion flag
    sort_order = Column(Integer, default=0)
    
    # Validation info
    validation_action = Column(String(20))  # kept, shortened, regenerated
    original_length = Column(Integer)
    
    # Relationships
    ad_group = relationship("AdGroup", back_populates="headlines")


class AdDescription(Base):
    """
    Google Ads RSA AÃ§Ä±klamasÄ±.
    Max 90 karakter, farklÄ± tipler.
    """
    __tablename__ = "ad_descriptions"
    
    id = Column(Integer, primary_key=True, index=True)
    ad_group_id = Column(Integer, ForeignKey("ad_groups.id", ondelete="CASCADE"), nullable=False)
    
    description_text = Column(String(90), nullable=False)  # Google Ads limiti
    description_type = Column(String(20))  # value_prop, features, cta, trust
    position_preference = Column(String(20), default='any')
    sort_order = Column(Integer, default=0)
    
    # Validation info
    validation_action = Column(String(20))  # kept, sentence_trim, truncated
    
    # Relationships
    ad_group = relationship("AdGroup", back_populates="descriptions")


class NegativeKeyword(Base):
    """
    Negatif Anahtar Kelime.
    Reklam grubunda gÃ¶sterilmemesi gereken aramalar.
    """
    __tablename__ = "negative_keywords"
    
    id = Column(Integer, primary_key=True, index=True)
    ad_group_id = Column(Integer, ForeignKey("ad_groups.id", ondelete="CASCADE"), nullable=False)
    
    keyword = Column(String(200), nullable=False)
    match_type = Column(String(20), default='phrase')  # exact, phrase, broad
    category = Column(String(50))  # bilgi_amacli, ucretsiz, dusuk_kalite, vb.
    reason = Column(String(500))
    
    # Relationships
    ad_group = relationship("AdGroup", back_populates="negative_keywords")


# ==================== SOCIAL MEDIA MODELS ====================

class SocialCategory(Base):
    """
    Sosyal Medya Kategori.
    AÅŸama 1: Ä°Ã§erik kategorileri (educational, product_benefit, vb.)
    """
    __tablename__ = "social_categories"
    
    id = Column(Integer, primary_key=True, index=True)
    scoring_run_id = Column(Integer, ForeignKey("scoring_runs.id", ondelete="CASCADE"), nullable=False)
    brief_id = Column(Integer, ForeignKey("social_briefs.id", ondelete="CASCADE"), nullable=True, index=True)
    
    category_name = Column(String(100), nullable=False)
    category_type = Column(String(50))  # educational, product_benefit, social_proof, brand_story, community, trending
    description = Column(Text)
    # Icerik staleness (kanal reassignment) — parent-child invariant:
    # okuma sorgulari category+idea+content UCUNUN de non-stale olmasini dogrular
    is_stale = Column(Boolean, nullable=False, default=False, server_default="false")
    relevance_score = Column(Float, default=0.0)  # 0.00 - 1.00
    suggested_keyword_ids = Column(JSON)  # [1, 5, 12, ...]
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    ideas = relationship("SocialIdea", back_populates="category", cascade="all, delete-orphan")
    scoring_run = relationship("ScoringRun", back_populates="social_categories")
    brief = relationship("SocialBrief", back_populates="categories")


class SocialIdea(Base):
    """
    Sosyal Medya Fikir.
    Aşama 2: İçerik fikirleri (platform, format, trend uyumu)
    """
    __tablename__ = "social_ideas"
    
    id = Column(Integer, primary_key=True, index=True)
    category_id = Column(Integer, ForeignKey("social_categories.id", ondelete="CASCADE"), nullable=False)
    keyword_id = Column(Integer, ForeignKey("keywords.id", ondelete="SET NULL"), nullable=True)
    brief_id = Column(Integer, ForeignKey("social_briefs.id", ondelete="CASCADE"), nullable=True, index=True)
    brief_target_id = Column(Integer, ForeignKey("social_brief_targets.id", ondelete="SET NULL"), nullable=True, index=True)
    
    idea_title = Column(String(200), nullable=False)
    idea_description = Column(Text)
    is_stale = Column(Boolean, nullable=False, default=False, server_default="false")
    target_platform = Column(String(50))   # instagram, tiktok, twitter, linkedin, youtube
    content_format = Column(String(50))    # reels, carousel, story, post, thread, short
    
    # trend_alignment (viral_potential yerine - daha dürüst isimlendirme)
    trend_alignment = Column(Float, default=0.0)  # 0.00 - 1.00, güncel trendlerle uyum
    
    is_selected = Column(Boolean, default=False)
    regeneration_count = Column(Integer, default=0)  # Regenerate sayacı
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    category = relationship("SocialCategory", back_populates="ideas")
    content = relationship("SocialContent", back_populates="idea", uselist=False, cascade="all, delete-orphan")
    brief = relationship("SocialBrief", back_populates="ideas")
    brief_target = relationship("SocialBriefTarget", back_populates="ideas")


class SocialContent(Base):
    """
    Sosyal Medya İçerik.
    Aşama 3: Tam içerik paketi (hooks, caption, hashtags, vb.)
    """
    __tablename__ = "social_contents"
    
    id = Column(Integer, primary_key=True, index=True)
    idea_id = Column(Integer, ForeignKey("social_ideas.id", ondelete="CASCADE"), nullable=False)
    brief_id = Column(Integer, ForeignKey("social_briefs.id", ondelete="CASCADE"), nullable=True, index=True)
    content_output_id = Column(Integer, ForeignKey("content_outputs.id", ondelete="SET NULL"), nullable=True)
    is_stale = Column(Boolean, nullable=False, default=False, server_default="false")
    
    # Format-spesifik payload ve süre durumu (Faz F1-A)
    format_payload = Column(JSON, nullable=True)
    duration_status = Column(String(30), nullable=True)
    actual_duration_sec = Column(Integer, nullable=True)
    validation_warnings = Column(JSON, nullable=True)
    
    # JSONB hooks - esnek, genişletilebilir
    # Format: [{"text": "...", "style": "question", "ab_score": null}]
    hooks = Column(JSON)
    
    # Ana içerik
    caption = Column(Text, nullable=False)
    scenario = Column(Text)  # Video kurgusu veya carousel slide'ları
    visual_suggestion = Column(Text)  # Görsel stili, renk paleti
    video_concept = Column(Text)  # B-roll ve geçiş önerileri
    
    # CTA ve etiketler
    cta_text = Column(Text)
    hashtags = Column(JSON)  # ["hashtag1", "hashtag2", ...]
    
    # Platform optimizasyonu (industry standard, kullanıcı hesabına özel değil)
    industry_posting_suggestion = Column(Text)  # "Genel öneri: LinkedIn için Salı 08:00-10:00"
    platform_notes = Column(Text)  # Platform-specific tavsiyeler
    
    regeneration_count = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    idea = relationship("SocialIdea", back_populates="content")
    brief = relationship("SocialBrief", back_populates="contents")

    __table_args__ = (
        Index(
            "uq_social_content_idea_brief",
            "idea_id",
            unique=True,
            postgresql_where=text("brief_id IS NOT NULL"),
        ),
    )


class SocialBrief(Base):
    """
    Sosyal Medya Brief (kelime -> platform/format/sure secimi).
    Yeni brief akisi kayitlari immutable kabul edilir.
    """
    __tablename__ = "social_briefs"

    id = Column(Integer, primary_key=True, index=True)
    scoring_run_id = Column(
        Integer, ForeignKey("scoring_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    brand_name_snapshot = Column(String(200), nullable=True)
    brand_context_snapshot = Column(Text, nullable=True)
    channel_assignment_version = Column(Integer, nullable=False, default=1, server_default="1")
    format_matrix_version = Column(String(50), nullable=False, default="v1", server_default="v1")
    locked_at = Column(DateTime(timezone=True), nullable=True)
    is_stale = Column(Boolean, nullable=False, default=False, server_default="false")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_social_briefs_run_stale", "scoring_run_id", "is_stale"),
    )

    scoring_run = relationship("ScoringRun", back_populates="social_briefs")
    brief_keywords = relationship(
        "SocialBriefKeyword",
        back_populates="brief",
        cascade="all, delete-orphan",
        order_by="SocialBriefKeyword.position",
    )
    targets = relationship(
        "SocialBriefTarget",
        back_populates="brief",
        cascade="all, delete-orphan",
    )
    attempts = relationship(
        "SocialGenerationAttempt",
        back_populates="brief",
        cascade="all, delete-orphan",
    )
    categories = relationship("SocialCategory", back_populates="brief")
    ideas = relationship("SocialIdea", back_populates="brief")
    contents = relationship("SocialContent", back_populates="brief")


class SocialBriefKeyword(Base):
    """
    Brief'e bagli secili keyword'ler (1-5 adet).
    """
    __tablename__ = "social_brief_keywords"

    id = Column(Integer, primary_key=True, index=True)
    brief_id = Column(
        Integer, ForeignKey("social_briefs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    keyword_id = Column(
        Integer, ForeignKey("keywords.id", ondelete="SET NULL"), nullable=True, index=True
    )
    keyword_snapshot = Column(String(255), nullable=False)
    position = Column(Integer, nullable=False, default=0, server_default="0")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("brief_id", "keyword_id", name="uq_social_brief_keyword"),
    )

    brief = relationship("SocialBrief", back_populates="brief_keywords")
    keyword = relationship("Keyword")


class SocialBriefTarget(Base):
    """
    Brief'e bagli platform/format/sure hedefleri (en fazla 6 adet).
    """
    __tablename__ = "social_brief_targets"

    id = Column(Integer, primary_key=True, index=True)
    brief_id = Column(
        Integer, ForeignKey("social_briefs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    platform = Column(String(50), nullable=False)
    content_format = Column(String(50), nullable=False)
    duration_preset_id = Column(String(50), nullable=True)
    duration_min_sec = Column(Integer, nullable=True)
    duration_max_sec = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "brief_id", "platform", "content_format",
            name="uq_social_brief_target_platform_format",
        ),
        CheckConstraint(
            "((duration_min_sec IS NULL AND duration_max_sec IS NULL) OR "
            "(duration_min_sec IS NOT NULL AND duration_max_sec IS NOT NULL AND "
            "duration_min_sec >= 1 AND duration_max_sec >= duration_min_sec))",
            name="ck_social_brief_targets_duration_bounds",
        ),
        CheckConstraint(
            "((content_format IN ('video', 'reels', 'short') AND "
            "duration_preset_id IS NOT NULL AND duration_min_sec IS NOT NULL AND duration_max_sec IS NOT NULL) OR "
            "(content_format NOT IN ('video', 'reels', 'short') AND "
            "duration_preset_id IS NULL AND duration_min_sec IS NULL AND duration_max_sec IS NULL))",
            name="ck_social_brief_targets_format_duration",
        ),
    )

    brief = relationship("SocialBrief", back_populates="targets")
    ideas = relationship("SocialIdea", back_populates="brief_target")


class SocialGenerationAttempt(Base):
    """
    Brief bazli uretim denemeleri (stage: categories|ideas|contents).
    Idempotency ve lease tabanli tekil aktif deneme garantisi.
    """
    __tablename__ = "social_generation_attempts"

    id = Column(Integer, primary_key=True, index=True)
    brief_id = Column(
        Integer, ForeignKey("social_briefs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    stage = Column(String(50), nullable=False)
    idempotency_key = Column(String(128), nullable=False)
    status = Column(String(30), nullable=False, default="pending", server_default="pending")
    requested_target_ids = Column(JSON, nullable=True)
    requested_idea_ids = Column(JSON, nullable=True)
    coverage = Column(JSON, nullable=True)
    warnings = Column(JSON, nullable=True)
    task_id = Column(String(155), nullable=True, unique=True)
    heartbeat_at = Column(DateTime(timezone=True), nullable=True)
    lease_expires_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    error_message = Column(Text, nullable=True)
    reason_code = Column(String(50), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "brief_id", "stage", "idempotency_key",
            name="uq_social_gen_attempt_idempotency",
        ),
        Index(
            "uq_social_gen_attempt_active",
            "brief_id", "stage",
            unique=True,
            postgresql_where=text("status IN ('pending', 'running')"),
        ),
        Index("idx_social_gen_attempt_brief_status", "brief_id", "status"),
    )

    brief = relationship("SocialBrief", back_populates="attempts")


# ==================== TASK TRACKING (Section 10) ====================

class AiUsageEvent(Base):
    """AI kullanim telemetrisi (plan C) — kaynak gercek HAM token sayilaridir.

    Run'in TUM AI kullanimi tek Celery task'inda yasamaz (sync endpoint +
    BackgroundTasks dahil); bu tablo scoring_run_id uzerinden "bu run kaca
    mal oldu?" sorusunu tek sorguyla cevaplar. Basarisiz istekler de yazilir
    (token alanlari NULL + retry_reason). (request_id, attempt) UNIQUE —
    bounded-flush yazimi idempotenttir.
    """
    __tablename__ = "ai_usage_events"

    id = Column(Integer, primary_key=True, index=True)
    scoring_run_id = Column(
        Integer, ForeignKey("scoring_runs.id", ondelete="SET NULL"), nullable=True
    )
    brand_profile_id = Column(
        Integer, ForeignKey("brand_profiles.id", ondelete="SET NULL"), nullable=True
    )
    task_id = Column(String(64), nullable=True)
    request_id = Column(String(64), nullable=False)
    attempt = Column(Integer, nullable=False, default=1, server_default="1")
    stage = Column(String(60), nullable=False)
    model = Column(String(100), nullable=False)
    prompt_tokens = Column(Integer, nullable=True)
    candidates_tokens = Column(Integer, nullable=True)
    thoughts_tokens = Column(Integer, nullable=True)
    total_tokens = Column(Integer, nullable=True)
    finish_reason = Column(String(40), nullable=True)
    latency_ms = Column(Integer, nullable=True)
    retry_reason = Column(String(255), nullable=True)
    cache_status = Column(String(20), nullable=True)
    price_snapshot = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("request_id", "attempt", name="uq_ai_usage_request_attempt"),
        Index("idx_ai_usage_run_created", "scoring_run_id", "created_at"),
        Index("idx_ai_usage_task", "task_id"),
        Index("idx_ai_usage_stage_model", "stage", "model"),
    )


class TaskResult(Base):
    """
    Celery task sonuÃ§larÄ±.
    Task durumunu hem Redis hem PostgreSQL'de takip eder.
    """
    __tablename__ = "task_results"
    
    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(String(50), unique=True, index=True, nullable=False)
    task_type = Column(String(50), index=True)  # seo_content, ads, social, export, policy_preview
    scoring_run_id = Column(Integer, ForeignKey("scoring_runs.id", ondelete="SET NULL"), nullable=True)
    # Workspace sahipligi (plan v13): run'suz tasklar icin (or. policy_preview
    # onboarding'de ScoringRun olmadan calisir). Sorgulanabilir sahiplik +
    # workspace-basina-tek-aktif-preview kurali bu kolonla uygulanir.
    brand_profile_id = Column(
        Integer, ForeignKey("brand_profiles.id", ondelete="SET NULL"), nullable=True
    )
    
    status = Column(String(20), default="pending", index=True)  # pending, running, completed, failed
    progress = Column(Integer, default=0)  # 0-100
    
    result_data = Column(JSON)  # SonuÃ§ Ã¶zeti
    error_message = Column(Text)
    
    started_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    scoring_run = relationship("ScoringRun", backref="task_results")


# ==================== BRAND PROFILE & RELEVANCE (Site Analyzer) ====================

class BrandProfile(Base):
    """
    Marka Ã§alÄ±ÅŸmasÄ± (Workspace Container).
    Bir kullanÄ±cÄ±nÄ±n bir marka iÃ§in yaptÄ±ÄŸÄ± tÃ¼m analizleri gruplar.
    scoring_run_id: DEPRECATED — yeni kod kullanmaz. Eski 1:1 iliÅŸki iÃ§in tutulur.
    """
    __tablename__ = "brand_profiles"

    id = Column(Integer, primary_key=True, index=True)
    scoring_run_id = Column(
        Integer,
        ForeignKey("scoring_runs.id", ondelete="SET NULL"),
        nullable=True,
        unique=False
    )  # DEPRECATED — Phase A'da SET NULL yapÄ±lÄ±r

    # Workspace metadata
    name = Column(String(200), nullable=True)  # Phase C NOT NULL
    preliminary_info = Column(Text, nullable=True)  # KullanÄ±cÄ± Ã¶n bilgisi
    excluded_info = Column(Text, nullable=True)  # Kullanici tarafindan dislanacak konular
    suggested_keywords = Column(JSON, nullable=True)  # AI'Ä±n Ã¶nerdiÄŸi ~10 kw
    deleted_at = Column(DateTime(timezone=True), nullable=True)  # Soft delete
    default_geo_target_id = Column(String(20), nullable=True)  # Workspace market context
    default_language_id = Column(String(10), nullable=True)
    is_system_default = Column(Boolean, nullable=False, default=False, server_default="false")

    company_url = Column(String(500), nullable=False)
    competitor_urls = Column(JSON)  # ["url1", "url2", "url3"] — yalniz oneri kaynagi

    # ── Kalite+maliyet plani A/B/F (plan_kalite_maliyet.md) ──
    # DIKKAT: JSON kolon guncellemelerinde in-place mutation'a guvenme —
    # her zaman YENI dict/list ata (SQLAlchemy change tracking).
    # competitor_terms (plan v13 semasi): [{term, status, source: domain|user,
    #   source_urls: [canonical_key], manual_approved: bool, created_at}]
    # status TURETILMIS alandir: approved = manual_approved OR source_urls dolu.
    # Eski kayitlarda source_urls/manual_approved YOK — reconciliation onlara
    # dokunmaz (gorunur kalirlar, elle kaldirilabilirler).
    # YALNIZCA status='approved' olan term'ler filtreye girer.
    competitor_terms = Column(JSON, nullable=True)
    # competitor_url_decisions (plan v13): kullanicinin URL bazli KALICI kararlari.
    # {canonical_key: {url, term, decision: block|not_competitor, updated_at}}
    # validation_data'dan AYRI tutulur — validation_data yalniz AI ciktisidir ve
    # background analizler tarafindan serbestce yeniden yazilabilir.
    competitor_url_decisions = Column(JSON, nullable=True)
    # competitor_policy: {ads|seo|social: block|allow} — default hepsi block
    competitor_policy = Column(JSON, nullable=True)
    # topic_policy: {excluded_terms: [...term kaydi...], excluded_aliases: [...]}
    # profile_data.exclude_themes yalniz prompt yonlendirmesi olarak kalir.
    topic_policy = Column(JSON, nullable=True)
    # capabilities: {free_plan|trial_available|support_24_7|refund_policy|
    #   licensed|product_guarantee: {value, source: user|ai|fallback,
    #   approved_at, approved_by}} — YALNIZ source=user VE value=true yetkilendirir.
    capabilities = Column(JSON, nullable=True)

    # Freshness surumleri (plan v13): iki BAGIMSIZ eksen.
    # policy_version: etkin rakip/konu politikasi degisince artar.
    # anchor_version: MATERYALIZE anchor metni (canonical fingerprint) degisince
    #   artar — endpoint'e degil gercek anchor farkina baglidir
    #   (apply_profile_data_update tek yazma kapisi).
    policy_version = Column(Integer, nullable=False, default=1, server_default="1")
    anchor_version = Column(Integer, nullable=False, default=1, server_default="1")
    # v2.1 kanal stratejisi (plan Faz C): {product_definition, content_strategy,
    # social_mode, schema_version, status: draft|approved, approved_at,
    # approved_fingerprint}. Güncelleme her zaman YENİ dict atamasıyla yapılır
    # (in-place mutation YOK). strategy_version yalnız ONAYLI semantik
    # fingerprint gerçekten değişince artar (draft düzenleme artırmaz);
    # 0 = hiç onaylı strateji yok.
    channel_strategy = Column(JSON, nullable=True)
    strategy_version = Column(Integer, nullable=False, default=0, server_default="0")

    status = Column(
        String(20),
        nullable=False,
        default="pending"
    )  # pending, running, profile_review, competitor_review, keywords_review, draft, confirmed, failed

    # Onboarding akis diskriminatoru: "legacy" (keyword-once) | "profile_first" (profil-once)
    onboarding_flow = Column(String(20), nullable=False, default="legacy", server_default="legacy")
    # Profil onay zamani (yalniz audit; dallanma onboarding_flow uzerinden yapilir)
    profile_approved_at = Column(DateTime(timezone=True), nullable=True)

    # AI profil Ã§Ä±ktÄ±sÄ±
    profile_data = Column(JSON)

    # Rakip doÄŸrulama
    validation_data = Column(JSON)

    # Crawl metadata
    source_pages = Column(JSON)  # [{"url": "...", "title": "...", "status": 200}]
    crawl_content_cache = Column(Text, nullable=True)  # AI'a verilen hazirlanmis site icerigi
    error_message = Column(Text)

    # Arka plan profil analizi "attempt" token'i (plan_yapilacaklar.md 2.2).
    # Her baslatma (create / keywords-approve / profile-approve rerun) satir
    # kilidi altinda yeni bir uuid4 yazar; task'in butun status/profile
    # yazimlari ve janitor yazimlari bu degere KOSULLUDUR. Token'i degismis
    # (eski) bir attempt hicbir sey yazamaz. Mantik: app/core/site_analyzer/
    # analysis_attempt.py. NULL = hic analiz baslatilmamis / eski satir.
    analysis_attempt_id = Column(String(36), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    scoring_run = relationship(
        "ScoringRun",
        backref="brand_profile",
        foreign_keys="BrandProfile.scoring_run_id",
        primaryjoin="BrandProfile.scoring_run_id == ScoringRun.id",
        viewonly=True
    )
    scoring_runs = relationship(
        "ScoringRun",
        back_populates="brand_profile_workspace",
        foreign_keys="ScoringRun.brand_profile_id"
    )
    workspace_keywords = relationship("WorkspaceKeyword", back_populates="brand_profile", cascade="all, delete-orphan")


class KeywordRelevance(Base):
    """
    Keyword-marka iliÅŸki skoru.
    Embedding cosine similarity ile hesaplanÄ±r.
    """
    __tablename__ = "keyword_relevance"

    id = Column(Integer, primary_key=True, index=True)
    scoring_run_id = Column(
        Integer,
        ForeignKey("scoring_runs.id", ondelete="CASCADE"),
        nullable=False
    )
    keyword_id = Column(
        Integer,
        ForeignKey("keywords.id", ondelete="CASCADE"),
        nullable=False
    )

    relevance_score = Column(Numeric(4, 3), nullable=False)  # 0.000 - 1.000
    # Profil/strateji anchor'lari zenginlestikce 500 karakteri asabilir.
    # Tam metin denetim ve aciklanabilirlik icin korunur.
    matched_anchor = Column(Text)
    method = Column(String(20), nullable=False, default="embedding")  # embedding, fuzzy_fallback

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    scoring_run = relationship("ScoringRun")
    keyword = relationship("Keyword")

    __table_args__ = (
        UniqueConstraint('scoring_run_id', 'keyword_id', name='uq_keyword_relevance'),
        Index('idx_keyword_relevance_run', 'scoring_run_id'),
    )


# ==================== WORKSPACE KEYWORD (M2M Snapshot) ====================

class WorkspaceKeyword(Base):
    """
    Workspace-Keyword M2M iliÅŸki tablosu.
    Import-time snapshot metrikleri immutable'dir.
    """
    __tablename__ = "workspace_keywords"

    id = Column(Integer, primary_key=True, index=True)
    brand_profile_id = Column(
        Integer,
        ForeignKey("brand_profiles.id", ondelete="CASCADE"),
        nullable=False
    )
    keyword_id = Column(
        Integer,
        ForeignKey("keywords.id", ondelete="CASCADE"),
        nullable=False
    )

    # Snapshot metrikler (import-time, immutable)
    monthly_volume = Column(Integer, nullable=False, default=0)
    trend_3m = Column(Numeric(7, 2), nullable=False, default=0)
    trend_12m = Column(Numeric(7, 2), nullable=False, default=0)
    competition_score = Column(Numeric(3, 2), nullable=False, default=0.50)

    # Source metadata
    data_source = Column(String(20), nullable=False, default="csv")  # csv, google_ads_api, url_seed, manual
    sector = Column(String(200), nullable=True)
    target_market = Column(String(200), nullable=True)
    geo_target_id = Column(String(20), nullable=True)
    language_id = Column(String(10), nullable=True)

    notes = Column(Text, nullable=True)

    imported_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    keyword = relationship("Keyword", back_populates="workspace_keywords")
    brand_profile = relationship("BrandProfile", back_populates="workspace_keywords")

    __table_args__ = (
        CheckConstraint(
            "data_source IN ('csv', 'google_ads_api', 'url_seed', 'manual')",
            name="ck_workspace_keywords_data_source",
        ),
        Index('idx_workspace_kw_workspace', 'brand_profile_id'),
        Index('idx_workspace_kw_keyword', 'keyword_id'),
        Index('idx_workspace_kw_volume', 'brand_profile_id', 'monthly_volume'),
    )


class WorkspaceChannelSeed(Base):
    """Kullanicinin onboarding'de verdigi KANAL tercihi (plan9 §5).

    Neden ayri tablo: `BrandProfile.suggested_keywords` duz metin listesidir
    ve kanal bilgisi tasiyamaz. Olculen sonuc (makro F1 0.590 -> 0.771) bu
    tercihlerin YAPISAL veri olarak saklanmasini gerektiriyor; duz metni
    kanal etiketi gibi yorumlamak YASAK.

    Kapsam: seed'ler YALNIZ kendi workspace'inde kullanilir. Baska
    workspace'in seed'i prompt'a giremez (coklu-firma ilkesi: mekanizma
    global, icerik workspace'e ozel).

    `keyword_id` NULL olabilir: onboarding aninda oneri kelimeleri henuz
    `keywords` tablosunda olmayabilir. Eslesme kanonik metin uzerinden
    yapilir; keyword sonradan evrene girerse `keyword_id` doldurulur.
    """

    __tablename__ = "workspace_channel_seeds"

    id = Column(Integer, primary_key=True, index=True)
    brand_profile_id = Column(
        Integer,
        ForeignKey("brand_profiles.id", ondelete="CASCADE"),
        nullable=False,
    )
    keyword_id = Column(
        Integer,
        ForeignKey("keywords.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Kullaniciya gosterilen metin (goruntuleme icin birebir korunur)
    keyword = Column(String(500), nullable=False)
    # Eslesme anahtari: normalize_keyword() ciktisi
    canonical_keyword = Column(String(500), nullable=False)
    # ["ADS","SEO","SOCIAL"] alt kumesi. not_suitable=true ise BOS liste.
    channels = Column(JSON, nullable=False, default=list)
    # "Uygun degil" TEKIL secenektir: kanallarla birlikte isaretlenemez
    # (ck_workspace_channel_seed_exclusive ile kod disinda da garanti).
    not_suitable = Column(Boolean, nullable=False, default=False,
                          server_default="false")
    # Kullanici oneriyi degistirdiyse ESKI metin denetim icin korunur
    replaced_original_keyword = Column(String(500), nullable=True)
    labelled_by = Column(String(120), nullable=True)
    labelled_at = Column(DateTime(timezone=True), server_default=func.now())
    source = Column(String(50), nullable=False,
                    default="patron_onboarding_channel_seed",
                    server_default="patron_onboarding_channel_seed")

    brand_profile = relationship("BrandProfile")
    keyword_ref = relationship("Keyword")

    __table_args__ = (
        UniqueConstraint(
            "brand_profile_id", "canonical_keyword",
            name="uq_workspace_channel_seed",
        ),
        # IKI YONLU: her satir ya EN AZ BIR kanal ya da not_suitable
        # tasimak ZORUNDA. Tek yonlu kural `not_suitable=false + channels=[]`
        # bos kararini DB duzeyinde serbest birakirdi.
        # (PostgreSQL `json` tipinde esitlik operatoru yok -> uzunluk.)
        CheckConstraint(
            "(not_suitable AND json_array_length(channels) = 0) OR "
            "(NOT not_suitable AND json_array_length(channels) > 0)",
            name="ck_workspace_channel_seed_exclusive",
        ),
        Index("idx_workspace_channel_seed_ws", "brand_profile_id"),
    )


class ExportJob(Base):
    """DB-backed export job — replaces in-memory _export_status dict (plan2 §P2)."""

    __tablename__ = "export_jobs"

    id = Column(String(36), primary_key=True)  # UUID
    brand_profile_id = Column(Integer, ForeignKey("brand_profiles.id"), nullable=False)
    scoring_run_id = Column(Integer, ForeignKey("scoring_runs.id"), nullable=True)
    status = Column(String(20), nullable=False, default="pending")
    progress = Column(Integer, nullable=False, default=0)
    format = Column(String(10), nullable=True)
    sections = Column(JSON, nullable=True)
    include_stale_content = Column(Boolean, nullable=False, default=False)
    # Baslangic surum snapshot'lari (plan v13): worker rapor dosyasini
    # KAYDETMEDEN once guncel workspace surumleriyle karsilastirir; esit
    # degilse job POLICY_VERSION_CHANGED ile failed olur. NULL (migration
    # oncesi kayit): completed ise indirilebilir + policy_outdated=true.
    requested_policy_version = Column(Integer, nullable=True)
    requested_anchor_version = Column(Integer, nullable=True)
    # Export anindaki UYGULANMIS havuz screening kunyesi (plan §7.4):
    # eski dosya indirilebilir kalir ama "eski baglam" rozeti tasir.
    pool_screening_mode = Column(String(20), nullable=True)
    pool_screening_context_sha256 = Column(String(64), nullable=True)
    file_name = Column(String(500), nullable=True)
    filepath = Column(String(1000), nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    brand_profile = relationship("BrandProfile")
    scoring_run = relationship("ScoringRun", foreign_keys=[scoring_run_id])

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'processing', 'completed', 'failed')",
            name="ck_export_jobs_status",
        ),
        Index("idx_export_jobs_workspace", "brand_profile_id"),
        Index("idx_export_jobs_run", "scoring_run_id"),
        Index("idx_export_jobs_status", "status"),
    )




# ── Corpus screening / assignment attempt veri modeli (plan §5) ──────
# Tasarim kisitlari (Codex): para alanlari Numeric; materyalizasyon unique
# anahtari run ID icerir; screening reuse ve materyalizasyon kimlikleri
# AYRI indekslenir; reservation durum gecisleri DB constraint'leri ile
# sinirlanir.

class ChannelAssignmentAttempt(Base):
    """Her manual/automatic kanal atamasinin kalici ust kaydi (plan §5.1).

    Parent TaskResult kullanici ilerlemesidir; audit ve orkestrasyon
    otoritesi BURASIDIR. Manifest/kimlik/cap alanlari immutable, yalniz
    tanimli state-machine alanlari CAS ile degisir.
    """

    __tablename__ = "channel_assignment_attempts"

    id = Column(Integer, primary_key=True, index=True)
    parent_task_id = Column(String(64), nullable=False, unique=True)
    scoring_run_id = Column(
        Integer, ForeignKey("scoring_runs.id", ondelete="CASCADE"),
        nullable=False)
    brand_profile_id = Column(
        Integer, ForeignKey("brand_profiles.id", ondelete="CASCADE"),
        nullable=False)

    status = Column(String(20), nullable=False, default="pending")
    phase = Column(String(20), nullable=False, default="screening")

    screening_mode = Column(String(20), nullable=False, default="off")
    applied_candidate_multiplier = Column(Integer, nullable=False, default=1)
    counterfactual_target_multiplier = Column(
        Integer, nullable=False, default=1)
    relevance_coefficient = Column(Numeric(4, 2), nullable=True)
    applied_screening_channels = Column(JSON, nullable=True)

    preflight_sha256 = Column(String(64), nullable=True)
    approved_screening_cap_usd = Column(Numeric(12, 6), nullable=True)
    approved_downstream_cap_usd = Column(Numeric(12, 6), nullable=True)

    requested_policy_version = Column(Integer, nullable=True)
    requested_anchor_version = Column(Integer, nullable=True)
    requested_strategy_version = Column(Integer, nullable=True)
    requested_assignment_version = Column(Integer, nullable=True)

    context_sha256 = Column(String(64), nullable=True)
    channel_rank_snapshot_sha256 = Column(String(64), nullable=True)
    relevance_rows_sha256 = Column(String(64), nullable=True)
    materialization_identity_sha256 = Column(String(64), nullable=True)

    screening_job_id = Column(
        Integer, ForeignKey("corpus_screening_jobs.id", ondelete="SET NULL"),
        nullable=True)

    # Orkestrasyon CAS alanlari (plan §7.3): broker teslimi "exactly once"
    # VARSAYILMAZ; idempotency DB'dedir.
    assignment_task_id = Column(String(64), nullable=True)
    assignment_dispatch_state = Column(
        String(20), nullable=False, default="pending")
    dispatch_attempts = Column(Integer, nullable=False, default=0)
    dispatch_last_at = Column(DateTime(timezone=True), nullable=True)

    manifest = Column(JSON, nullable=True)
    error_code = Column(String(64), nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    scoring_run = relationship("ScoringRun")
    brand_profile = relationship("BrandProfile")

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed')",
            name="ck_assignment_attempt_status"),
        CheckConstraint(
            "phase IN ('screening', 'assignment', 'completed')",
            name="ck_assignment_attempt_phase"),
        CheckConstraint(
            "screening_mode IN ('off', 'shadow', 'assistive')",
            name="ck_assignment_attempt_mode"),
        CheckConstraint(
            "assignment_dispatch_state IN "
            "('pending', 'publishing', 'sent', 'started', 'finished')",
            name="ck_assignment_attempt_dispatch_state"),
        # plan §6.1 CHECK: mod ile carpanlar tutarli olmali
        CheckConstraint(
            "(screening_mode = 'off' AND applied_candidate_multiplier = 1 "
            " AND counterfactual_target_multiplier = 1) OR "
            "(screening_mode = 'shadow' AND applied_candidate_multiplier = 1 "
            " AND counterfactual_target_multiplier = 3) OR "
            "(screening_mode = 'assistive' AND "
            " applied_candidate_multiplier = 3 AND "
            " counterfactual_target_multiplier = 3)",
            name="ck_assignment_attempt_multiplier_matrix"),
        CheckConstraint(
            "approved_screening_cap_usd IS NULL OR "
            "approved_screening_cap_usd > 0",
            name="ck_assignment_attempt_screening_cap_positive"),
        CheckConstraint(
            "approved_downstream_cap_usd IS NULL OR "
            "approved_downstream_cap_usd > 0",
            name="ck_assignment_attempt_downstream_cap_positive"),
        # Codex 6. tur #4: selection satirinin run'i ile attempt'in run'i
        # AYNI olmali — composite FK'nin hedefi bu unique'tir
        UniqueConstraint("id", "scoring_run_id",
                         name="uq_assignment_attempt_id_run"),
        Index("idx_assignment_attempt_run", "scoring_run_id", "status"),
        Index("idx_assignment_attempt_workspace",
              "brand_profile_id", "created_at"),
        Index("idx_assignment_attempt_materialization",
              "materialization_identity_sha256"),
        # Run basina TEK aktif attempt (partial unique)
        Index("uq_assignment_attempt_active_run", "scoring_run_id",
              unique=True,
              postgresql_where=text("status IN ('pending', 'running')")),
    )


class CorpusScreeningJob(Base):
    """Tam-evren tarama isi (plan §5.2). Worker canli Keyword/KeywordScore/
    profile OKUMAZ; dispatch aninda dondurulmus snapshot'i kullanir."""

    __tablename__ = "corpus_screening_jobs"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(String(64), nullable=True, unique=True)
    scoring_run_id = Column(
        Integer, ForeignKey("scoring_runs.id", ondelete="CASCADE"),
        nullable=False)
    brand_profile_id = Column(
        Integer, ForeignKey("brand_profiles.id", ondelete="CASCADE"),
        nullable=False)
    # Audit etiketi; reuse kardinalitesinin FK'si DEGILDIR (plan §5.2)
    created_by_parent_task_id = Column(String(64), nullable=True)

    status = Column(String(20), nullable=False, default="pending")

    provider = Column(String(20), nullable=False)
    model = Column(String(100), nullable=False)
    prompt_version = Column(String(40), nullable=False)
    temperature = Column(Numeric(4, 2), nullable=False)
    batch_size = Column(Integer, nullable=False)
    view_salts = Column(JSON, nullable=False)
    applied_screening_channels = Column(JSON, nullable=False)

    screening_input_identity_sha256 = Column(String(64), nullable=False)
    universe_sha256 = Column(String(64), nullable=False)
    context_sha256 = Column(String(64), nullable=False)
    input_snapshot = Column(JSON, nullable=False)
    screening_context = Column(JSON, nullable=False)
    runner_contract = Column(JSON, nullable=True)

    strategy_fingerprint = Column(String(128), nullable=True)
    dispatch_policy_version = Column(Integer, nullable=True)
    dispatch_anchor_version = Column(Integer, nullable=True)
    dispatch_strategy_version = Column(Integer, nullable=True)

    planned_requests = Column(Integer, nullable=True)
    actual_requests = Column(Integer, nullable=True)
    cost_usd = Column(Numeric(12, 6), nullable=True)
    ceiling_charges = Column(Integer, nullable=False, default=0)
    latency_ms = Column(Integer, nullable=True)

    coverage_resolved = Column(Integer, nullable=True)
    unresolved_count = Column(Integer, nullable=True)
    contract_violations = Column(Integer, nullable=True)
    decisions_available = Column(Boolean, nullable=False, default=True)

    error_code = Column(String(64), nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    # Codex 10. tur #3: `task_id` worker LEASE'i DEGILDIR. Ayni Celery
    # task'inin mukerrer teslimi de tek kazanan olmali; devralma yalniz
    # lease suresi dolunca (onceki worker hard-limit ile olmus demektir).
    execution_lease_id = Column(String(64), nullable=True)
    execution_lease_expires_at = Column(DateTime(timezone=True), nullable=True)
    execution_attempt = Column(Integer, nullable=False, default=0,
                               server_default="0")

    scoring_run = relationship("ScoringRun")
    brand_profile = relationship("BrandProfile")

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed', "
            "'fallback', 'not_needed')",
            name="ck_screening_job_status"),
        CheckConstraint("provider IN ('gemini', 'deepseek')",
                        name="ck_screening_job_provider"),
        CheckConstraint("batch_size >= 1",
                        name="ck_screening_job_batch_size"),
        CheckConstraint("cost_usd IS NULL OR cost_usd >= 0",
                        name="ck_screening_job_cost_nonneg"),
        # Ayni kimlikte TEK aktif is
        Index("uq_screening_job_active_identity",
              "screening_input_identity_sha256", unique=True,
              postgresql_where=text("status IN ('pending', 'running')")),
        # Ayni kimlikte TEK dogrulanmis completed is (reuse anahtari)
        Index("uq_screening_job_completed_identity",
              "screening_input_identity_sha256", unique=True,
              postgresql_where=text("status = 'completed'")),
        Index("idx_screening_job_run_status", "scoring_run_id", "status"),
        Index("idx_screening_job_workspace", "brand_profile_id",
              "created_at"),
    )


class CorpusScreeningDecision(Base):
    """Modelin IMMUTABLE ciktisi (plan §5.3).

    Relevance snapshot / aday kaynagi / 'not selected' BURADA TUTULMAZ:
    ayni model karari farkli assignment parametreleriyle yeniden
    materyalize edildiginde bunlar degisir.
    """

    __tablename__ = "corpus_screening_decisions"

    id = Column(Integer, primary_key=True, index=True)
    screening_job_id = Column(
        Integer, ForeignKey("corpus_screening_jobs.id", ondelete="CASCADE"),
        nullable=False)
    keyword_score_id = Column(
        Integer, ForeignKey("keyword_scores.id", ondelete="CASCADE"),
        nullable=False)
    keyword_id = Column(
        Integer, ForeignKey("keywords.id", ondelete="CASCADE"),
        nullable=False)
    channel = Column(String(20), nullable=False)

    view_a_fit = Column(SmallInteger, nullable=True)
    view_a_reason = Column(String(60), nullable=True)
    view_a_unresolved = Column(Boolean, nullable=False, default=False)
    view_b_fit = Column(SmallInteger, nullable=True)
    view_b_reason = Column(String(60), nullable=True)
    view_b_unresolved = Column(Boolean, nullable=False, default=False)

    merged_fit = Column(Numeric(6, 4), nullable=True)
    passing = Column(Boolean, nullable=False, default=True)
    disagreement = Column(Boolean, nullable=False, default=False)
    uncertain = Column(Boolean, nullable=False, default=False)
    contract_violations = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    screening_job = relationship("CorpusScreeningJob")

    __table_args__ = (
        UniqueConstraint("screening_job_id", "keyword_score_id", "channel",
                         name="uq_screening_decision"),
        CheckConstraint("channel IN ('ADS', 'SEO', 'SOCIAL')",
                        name="ck_screening_decision_channel"),
        Index("idx_screening_decision_job_channel",
              "screening_job_id", "channel"),
    )


class CorpusScreeningBatchCheckpoint(Base):
    """Batch duzeyinde yeniden baslatilabilirlik (plan §5.4).

    Celery retry/hard-kill sonrasi ayni job YALNIZ eksik batch'leri
    cagirir; tamamlanmis batch TEKRAR UCRETLENDIRILMEZ.
    """

    __tablename__ = "corpus_screening_batch_checkpoints"

    id = Column(Integer, primary_key=True, index=True)
    screening_job_id = Column(
        Integer, ForeignKey("corpus_screening_jobs.id", ondelete="CASCADE"),
        nullable=False)
    view = Column(String(40), nullable=False)
    batch_ordinal = Column(Integer, nullable=False)
    batch_hash = Column(String(64), nullable=False)
    request_contract_sha256 = Column(String(64), nullable=False)

    state = Column(String(20), nullable=False, default="pending")
    payload = Column(JSON, nullable=True)
    payload_sha256 = Column(String(64), nullable=True)

    logical_request_id = Column(String(64), nullable=True)
    attempt = Column(Integer, nullable=False, default=1)
    usage = Column(JSON, nullable=True)
    cost_usd = Column(Numeric(12, 6), nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)

    screening_job = relationship("CorpusScreeningJob")

    __table_args__ = (
        UniqueConstraint("screening_job_id", "view", "batch_hash",
                         name="uq_screening_checkpoint"),
        # Codex 6. tur #5: resume icin ordinal de tekil olmali
        UniqueConstraint("screening_job_id", "view", "batch_ordinal",
                         name="uq_screening_checkpoint_ordinal"),
        CheckConstraint("state IN ('pending', 'completed', 'failed')",
                        name="ck_screening_checkpoint_state"),
        # Codex 6. tur #3: completed checkpoint GERCEK payload tasimali —
        # aksi halde resume batch'i ucretsiz atlar ama kararlari kuramaz
        CheckConstraint(
            "state <> 'completed' OR (payload IS NOT NULL "
            "AND payload_sha256 IS NOT NULL AND completed_at IS NOT NULL "
            "AND json_typeof(payload) IN ('object', 'array'))",
            name="ck_screening_checkpoint_completed_payload"),
        CheckConstraint("cost_usd IS NULL OR cost_usd >= 0",
                        name="ck_screening_checkpoint_cost_nonneg"),
        Index("idx_screening_checkpoint_job_state",
              "screening_job_id", "state"),
    )


class CorpusCandidateSelection(Base):
    """Her materyalizasyonun IMMUTABLE audit satiri (plan §5.5).

    `is_applied=False` shadow counterfactual'dir: canli ChannelCandidate'a
    DOKUNMAZ. ChannelCandidate silinse bile audit BURADA kalir.
    """

    __tablename__ = "corpus_candidate_selections"

    id = Column(Integer, primary_key=True, index=True)
    # Composite FK (asagida) attempt ile run'in AYNI olmasini zorlar;
    # tekil attempt FK'si BILINCLI olarak yok (Codex 6. tur #4)
    assignment_attempt_id = Column(Integer, nullable=False)
    # Codex: unique anahtar run ID icerir
    scoring_run_id = Column(
        Integer, ForeignKey("scoring_runs.id", ondelete="CASCADE"),
        nullable=False)
    screening_job_id = Column(
        Integer, ForeignKey("corpus_screening_jobs.id", ondelete="SET NULL"),
        nullable=True)
    keyword_id = Column(
        Integer, ForeignKey("keywords.id", ondelete="CASCADE"),
        nullable=False)
    channel = Column(String(20), nullable=False)

    origin_source = Column(String(20), nullable=False)
    materialization_action = Column(String(20), nullable=False)
    materialization_identity_sha256 = Column(String(64), nullable=True)

    baseline_rank = Column(Integer, nullable=True)
    screening_rank = Column(Integer, nullable=True)
    initial_materialized_rank = Column(Integer, nullable=True)
    screening_fit = Column(Numeric(6, 4), nullable=True)

    relevance_score = Column(Numeric(6, 4), nullable=True)
    adjusted_score = Column(Numeric(15, 4), nullable=True)

    active_channel = Column(Boolean, nullable=False, default=True)
    capacity = Column(Integer, nullable=True)
    b_initial = Column(Integer, nullable=True)
    t_target = Column(Integer, nullable=True)
    u_union_size = Column(Integer, nullable=True)

    is_initial_set = Column(Boolean, nullable=False, default=True)
    is_applied = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    assignment_attempt = relationship("ChannelAssignmentAttempt")
    screening_job = relationship("CorpusScreeningJob")

    __table_args__ = (
        # Codex 6. tur #4: run 25 secimi run 24 attempt'ine BAGLANAMAZ
        ForeignKeyConstraint(
            ["assignment_attempt_id", "scoring_run_id"],
            ["channel_assignment_attempts.id",
             "channel_assignment_attempts.scoring_run_id"],
            ondelete="CASCADE",
            name="fk_candidate_selection_attempt_run"),
        UniqueConstraint("scoring_run_id", "assignment_attempt_id",
                         "keyword_id", "channel", "materialization_action",
                         name="uq_candidate_selection"),
        CheckConstraint("channel IN ('ADS', 'SEO', 'SOCIAL')",
                        name="ck_candidate_selection_channel"),
        CheckConstraint(
            "origin_source IN ('baseline', 'screening', 'both', 'none')",
            name="ck_candidate_selection_origin"),
        CheckConstraint(
            "materialization_action IN ('initial', 'transfer', 'expansion')",
            name="ck_candidate_selection_action"),
        Index("idx_candidate_selection_run_channel",
              "scoring_run_id", "channel", "is_applied"),
        Index("idx_candidate_selection_attempt", "assignment_attempt_id"),
        # Iki kimlik AYRI indekslenir (Codex): materyalizasyon kimligi
        Index("idx_candidate_selection_materialization",
              "materialization_identity_sha256"),
    )


class AiCostReservation(Base):
    """Kalici maliyet ledger'i (plan §5.7) — hard-cap OTORITESI.

    Bellek sayaci tek process icinde yeterlidir; worker hard-kill veya
    retry sonrasi harcanmis butceyi unutur. USD alanlari Numeric'tir
    (binary float CAS/limit karsilastirmasinda KULLANILMAZ).
    """

    __tablename__ = "ai_cost_reservations"

    id = Column(Integer, primary_key=True, index=True)
    budget_owner_attempt_id = Column(
        Integer,
        ForeignKey("channel_assignment_attempts.id", ondelete="CASCADE"),
        nullable=False)
    budget_kind = Column(String(20), nullable=False)

    request_id = Column(String(64), nullable=False)
    attempt = Column(Integer, nullable=False, default=1)
    stage = Column(String(60), nullable=True)
    provider = Column(String(20), nullable=True)
    model = Column(String(100), nullable=True)

    ceiling_usd = Column(Numeric(12, 6), nullable=False)
    actual_usd = Column(Numeric(12, 6), nullable=True)
    # Codex 7. tur #3: muhasebe tavani yakabilir ama GERCEK saglayici
    # maliyeti denetim icin burada korunur (actual_usd <= ceiling CHECK'i
    # bu alani baglamaz)
    observed_actual_usd = Column(Numeric(12, 6), nullable=True)
    state = Column(String(20), nullable=False, default="reserved")

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    settled_at = Column(DateTime(timezone=True), nullable=True)

    assignment_attempt = relationship("ChannelAssignmentAttempt")

    __table_args__ = (
        UniqueConstraint("budget_owner_attempt_id", "budget_kind",
                         "request_id", "attempt",
                         name="uq_ai_cost_reservation"),
        CheckConstraint("budget_kind IN ('screening', 'downstream')",
                        name="ck_ai_cost_reservation_kind"),
        CheckConstraint(
            "state IN ('reserved', 'settled', 'ceiling_charged')",
            name="ck_ai_cost_reservation_state"),
        CheckConstraint("ceiling_usd >= 0",
                        name="ck_ai_cost_reservation_ceiling_nonneg"),
        CheckConstraint("actual_usd IS NULL OR actual_usd >= 0",
                        name="ck_ai_cost_reservation_actual_nonneg"),
        CheckConstraint(
            "observed_actual_usd IS NULL OR observed_actual_usd >= 0",
            name="ck_ai_cost_reservation_observed_nonneg"),
        # CAS gecis kisiti: reserved -> actual YOK; settle/ceiling -> actual VAR
        CheckConstraint(
            # Codex 6. tur #2: terminal durumda settled_at ZORUNLU
            "(state = 'reserved' AND actual_usd IS NULL AND "
            " settled_at IS NULL) OR "
            "(state IN ('settled', 'ceiling_charged') AND "
            " actual_usd IS NOT NULL AND settled_at IS NOT NULL)",
            name="ck_ai_cost_reservation_state_transition"),
        # Gercek maliyet rezervasyon tavanini ASAMAZ (hard-cap sozlesmesi);
        # asarsa saglayici sinirlari delinmis demektir ve kosu durur
        CheckConstraint(
            "actual_usd IS NULL OR actual_usd <= ceiling_usd",
            name="ck_ai_cost_reservation_actual_le_ceiling"),
        # Ceiling charge TAM tavan kadar yanar
        CheckConstraint(
            "state <> 'ceiling_charged' OR actual_usd = ceiling_usd",
            name="ck_ai_cost_reservation_ceiling_charge_amount"),
        Index("idx_ai_cost_reservation_owner",
              "budget_owner_attempt_id", "budget_kind", "state"),
    )


class EngineStageResult(Base):
    """Motor v3 AI asama ciktilari — TEK tablo (plan_algoritma_entegrasyonu §4).

    Kelime disi sonuclar (aile atamasi, URL grubu, NormBounds) ayri tabloya
    degil `scope_type` + `scope_key` ile buraya yazilir.

    YAZMA KURALI (fail-closed): satir YALNIZ eksiksiz sema ve ID dogrulamasi
    gectikten sonra yazilir. Yarim/basarisiz cevap icin satir OLUSTURULMAZ —
    yarim cevap checkpoint sayilmaz.

    RESUME KURALI (fail-closed): mevcut satir tamamlanmis sayilmak icin
    `model`, `prompt_sha` ve `firm_block_sha256` alanlarinin run'in mühürlü
    baglamiyla (ScoringRun.execution_manifest) uyusmasi ZORUNLUDUR;
    uyusmazlikta sessiz yeniden kullanim YAPILMAZ.
    """
    __tablename__ = "engine_stage_results"

    id = Column(Integer, primary_key=True, index=True)
    scoring_run_id = Column(
        Integer, ForeignKey("scoring_runs.id", ondelete="CASCADE"),
        nullable=False)
    stage = Column(String(40), nullable=False)
    scope_type = Column(String(20), nullable=False)   # keyword|family|url_group|run
    scope_key = Column(String(120), nullable=False)
    payload = Column(JSON, nullable=False)
    # Mühürlü baglam — resume bu üçünü karsilastirir
    model = Column(String(60), nullable=False)
    prompt_sha = Column(String(64), nullable=False)
    firm_block_sha256 = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("scoring_run_id", "stage", "scope_type", "scope_key",
                         name="uq_engine_stage_results_scope"),
        CheckConstraint(
            "scope_type IN ('keyword', 'family', 'url_group', 'run')",
            name="ck_engine_stage_results_scope_type"),
        Index("idx_engine_stage_results_run_stage", "scoring_run_id", "stage"),
    )


class EngineSelection(Base):
    """Motor v3 secim sonucu — kilit ciktisi ve policy sonucu AYRI kolonlarda.

    `algorithm_rank` kilitli motorun DEGISMEYEN sonucudur; post-policy kapisi
    ona DOKUNMAZ. `final_rank` policy sonrasi teslim sirasidir; NULL ise satir
    policy tarafindan elenmistir ve ChannelPool'a YAZILMAZ (geri doldurma yok).
    """
    __tablename__ = "engine_selections"

    id = Column(Integer, primary_key=True, index=True)
    scoring_run_id = Column(
        Integer, ForeignKey("scoring_runs.id", ondelete="CASCADE"),
        nullable=False)
    keyword_id = Column(
        Integer, ForeignKey("keywords.id", ondelete="CASCADE"), nullable=False)
    channel = Column(String(20), nullable=False)      # ADS|SEO|SOCIAL
    algorithm_rank = Column(Integer, nullable=False)
    scores = Column(JSON, nullable=True)
    pool_class = Column(String(20), nullable=True)    # primary|secondary|deferred
    family_id = Column(String(120), nullable=True)
    priority = Column(String(30), nullable=True)
    final_rank = Column(Integer, nullable=True)       # NULL = policy eledi
    exclude_reason = Column(String(60), nullable=True)
    policy_version = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("scoring_run_id", "keyword_id", "channel",
                         name="uq_engine_selections_run_keyword_channel"),
        CheckConstraint("channel IN ('ADS', 'SEO', 'SOCIAL')",
                        name="ck_engine_selections_channel"),
        # XOR: satir ya SECILDI (final_rank dolu, exclude_reason bos) ya da
        # ELENDI (exclude_reason dolu, final_rank bos). Ikisi birden dolu
        # olamaz — teslim edilen bir satirin ayni anda elenmis gorunmesi
        # algorithm_rank/final_rank iki katman sozlesmesini bozar.
        CheckConstraint(
            "(final_rank IS NULL) <> (exclude_reason IS NULL)",
            name="ck_engine_selections_selected_xor_excluded"),
        Index("idx_engine_selections_run_channel_rank",
              "scoring_run_id", "channel", "algorithm_rank"),
    )


@event.listens_for(WorkspaceKeyword, "before_update")
def _prevent_workspace_keyword_snapshot_update(mapper, connection, target):
    """WorkspaceKeyword import-time snapshot fields are immutable; notes remains mutable."""
    immutable_fields = {
        "brand_profile_id",
        "keyword_id",
        "monthly_volume",
        "trend_3m",
        "trend_12m",
        "competition_score",
        "data_source",
        "sector",
        "target_market",
        "geo_target_id",
        "language_id",
        "imported_at",
    }
    state = inspect(target)
    changed = [
        field for field in immutable_fields
        if state.attrs[field].history.has_changes()
    ]
    if changed:
        raise ValueError(
            "WorkspaceKeyword snapshot fields are immutable: "
            + ", ".join(sorted(changed))
        )


class User(Base):
    """
    Uygulama kullanicisi (e-posta + parola ile giris).

    KAPSAM: yalniz KIMLIK. Rol, kiraci ve yetki alani BILEREK yok — giris
    yapan her kullanici tum workspace'leri gorur. Veri izolasyonu ayri bir
    istir (bkz. optimice/kullanici-yapisi-plan.md).

    Oturumlar bu tabloda DEGIL, Redis'te tutulur (app/core/sessions.py);
    bu yuzden token/oturum kolonu yoktur.

    email: DAIMA kucuk harfe normalize edilmis halde yazilir. Okuma
      tarafindaki sorgular da normalize edilmis deger ile arar; aksi halde
      UNIQUE kisiti buyuk/kucuk harf farkiyla atlatilabilir.
    deleted_at: soft delete (repo genelindeki desen). Dolu olan kullanici
      giris yapamaz ve acik oturumlari gecersizdir.
    """

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(320), nullable=False, unique=True, index=True)
    password_hash = Column(String(255), nullable=False)  # argon2id
    full_name = Column(String(200), nullable=True)
    is_active = Column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    # Gecici parola ile acilan kullanici, parolasini degistirmeden hicbir
    # veri ucuna erisemez (bkz. app/core/login.py require_login).
    must_change_password = Column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    last_login_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - tani amacli
        return f"<User id={self.id} email={self.email!r} active={self.is_active}>"
