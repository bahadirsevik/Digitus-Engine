"""
SQLAlchemy ORM Models.
All database tables are defined here.
"""
from datetime import datetime
from typing import Optional, List
from sqlalchemy import (
    Column, Integer, String, Text, Boolean, DateTime, Float,
    ForeignKey, Numeric, JSON, UniqueConstraint, Index
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
    """
    __tablename__ = "keywords"
    
    id = Column(Integer, primary_key=True, index=True)
    keyword = Column(String(500), nullable=False, index=True)
    monthly_volume = Column(Integer, nullable=False, default=0)
    trend_12m = Column(Numeric(7, 2), nullable=False, default=0.00)  # -9999.99 ile 99999.99 arasÄ±
    trend_3m = Column(Numeric(7, 2), nullable=False, default=0.00)   # -9999.99 ile 99999.99 arasÄ±
    competition_score = Column(Numeric(3, 2), nullable=False, default=0.50)  # 0.00 ile 1.00 arasÄ±
    sector = Column(String(200), index=True)
    target_market = Column(String(200))
    is_active = Column(Boolean, default=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    # Relationships
    scores = relationship("KeywordScore", back_populates="keyword", cascade="all, delete-orphan")
    intent_analyses = relationship("IntentAnalysis", back_populates="keyword", cascade="all, delete-orphan")
    channel_pools = relationship("ChannelPool", back_populates="keyword", cascade="all, delete-orphan")
    content_outputs = relationship("ContentOutput", back_populates="keyword", cascade="all, delete-orphan")
    channel_candidates = relationship("ChannelCandidate", back_populates="keyword", cascade="all, delete-orphan")


class ScoringRun(Base):
    """
    Her skorlama Ã§alÄ±ÅŸtÄ±rmasÄ± iÃ§in meta bilgi.
    Bir run iÃ§inde tÃ¼m kelimeler skorlanÄ±r ve kanallara atanÄ±r.
    """
    __tablename__ = "scoring_runs"
    
    id = Column(Integer, primary_key=True, index=True)
    run_name = Column(String(200))
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
    status = Column(String(50), default="pending")   # pending, scoring, intent_analysis, completed, failed
    company_url = Column(String(500))  # Opsiyonel firma URL (site profil analizi iÃ§in)
    competitor_urls = Column(JSON)     # Opsiyonel rakip URL listesi (max 3)
    started_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    keyword_scores = relationship("KeywordScore", back_populates="scoring_run", cascade="all, delete-orphan")
    channel_candidates = relationship("ChannelCandidate", back_populates="scoring_run", cascade="all, delete-orphan")
    intent_analyses = relationship("IntentAnalysis", back_populates="scoring_run", cascade="all, delete-orphan")
    channel_pools = relationship("ChannelPool", back_populates="scoring_run", cascade="all, delete-orphan")
    ad_groups = relationship("AdGroup", back_populates="scoring_run", cascade="all, delete-orphan")
    social_categories = relationship("SocialCategory", back_populates="scoring_run", cascade="all, delete-orphan")



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
    
    calculated_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    scoring_run = relationship("ScoringRun", back_populates="keyword_scores")
    keyword = relationship("Keyword", back_populates="scores")
    
    __table_args__ = (
        UniqueConstraint('scoring_run_id', 'keyword_id', name='uq_keyword_scores_run_keyword'),
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
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    scoring_run = relationship("ScoringRun", back_populates="channel_candidates")
    keyword = relationship("Keyword", back_populates="channel_candidates")
    
    __table_args__ = (
        UniqueConstraint('scoring_run_id', 'keyword_id', 'channel', name='uq_channel_candidates'),
        Index('idx_channel_candidates_run_channel', 'scoring_run_id', 'channel'),
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
    analyzed_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    scoring_run = relationship("ScoringRun", back_populates="intent_analyses")
    keyword = relationship("Keyword", back_populates="intent_analyses")
    
    __table_args__ = (
        UniqueConstraint('scoring_run_id', 'keyword_id', 'channel', name='uq_intent_analysis'),
        Index('idx_intent_analysis_run_channel', 'scoring_run_id', 'channel'),
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
    is_strategic = Column(Boolean, default=False, index=True)  # Stratejik kelime mi?
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    scoring_run = relationship("ScoringRun", back_populates="channel_pools")
    keyword = relationship("Keyword", back_populates="channel_pools")
    
    __table_args__ = (
        UniqueConstraint('scoring_run_id', 'keyword_id', 'channel', name='uq_channel_pools'),
        Index('idx_channel_pools_run_channel', 'scoring_run_id', 'channel'),
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

class AdGroup(Base):
    """
    Google Ads Reklam Grubu.
    Niyet bazlÄ± gruplandÄ±rÄ±lmÄ±ÅŸ kelimeleri iÃ§erir.
    """
    __tablename__ = "ad_groups"
    
    id = Column(Integer, primary_key=True, index=True)
    content_output_id = Column(Integer, ForeignKey("content_outputs.id", ondelete="CASCADE"))
    scoring_run_id = Column(Integer, ForeignKey("scoring_runs.id", ondelete="CASCADE"), nullable=False)
    
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
    
    category_name = Column(String(100), nullable=False)
    category_type = Column(String(50))  # educational, product_benefit, social_proof, brand_story, community, trending
    description = Column(Text)
    relevance_score = Column(Float, default=0.0)  # 0.00 - 1.00
    suggested_keyword_ids = Column(JSON)  # [1, 5, 12, ...]
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    ideas = relationship("SocialIdea", back_populates="category", cascade="all, delete-orphan")
    scoring_run = relationship("ScoringRun", back_populates="social_categories")


class SocialIdea(Base):
    """
    Sosyal Medya Fikir.
    AÅŸama 2: Ä°Ã§erik fikirleri (platform, format, trend uyumu)
    """
    __tablename__ = "social_ideas"
    
    id = Column(Integer, primary_key=True, index=True)
    category_id = Column(Integer, ForeignKey("social_categories.id", ondelete="CASCADE"), nullable=False)
    keyword_id = Column(Integer, ForeignKey("keywords.id", ondelete="SET NULL"), nullable=True)
    
    idea_title = Column(String(200), nullable=False)
    idea_description = Column(Text)
    target_platform = Column(String(50))   # instagram, tiktok, twitter, linkedin, youtube
    content_format = Column(String(50))    # reels, carousel, story, post, thread, short
    
    # trend_alignment (viral_potential yerine - daha dÃ¼rÃ¼st isimlendirme)
    trend_alignment = Column(Float, default=0.0)  # 0.00 - 1.00, gÃ¼ncel trendlerle uyum
    
    is_selected = Column(Boolean, default=False)
    regeneration_count = Column(Integer, default=0)  # Regenerate sayacÄ±
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    category = relationship("SocialCategory", back_populates="ideas")
    content = relationship("SocialContent", back_populates="idea", uselist=False, cascade="all, delete-orphan")


class SocialContent(Base):
    """
    Sosyal Medya Ä°Ã§erik.
    AÅŸama 3: Tam iÃ§erik paketi (hooks, caption, hashtags, vb.)
    """
    __tablename__ = "social_contents"
    
    id = Column(Integer, primary_key=True, index=True)
    idea_id = Column(Integer, ForeignKey("social_ideas.id", ondelete="CASCADE"), nullable=False)
    content_output_id = Column(Integer, ForeignKey("content_outputs.id", ondelete="SET NULL"), nullable=True)
    
    # JSONB hooks - esnek, geniÅŸletilebilir
    # Format: [{"text": "...", "style": "question", "ab_score": null}]
    hooks = Column(JSON)
    
    # Ana iÃ§erik
    caption = Column(Text, nullable=False)
    scenario = Column(Text)  # Video kurgusu veya carousel slide'larÄ±
    visual_suggestion = Column(Text)  # GÃ¶rsel stili, renk paleti
    video_concept = Column(Text)  # B-roll ve geÃ§iÅŸ Ã¶nerileri
    
    # CTA ve etiketler
    cta_text = Column(Text)
    hashtags = Column(JSON)  # ["hashtag1", "hashtag2", ...]
    
    # Platform optimizasyonu (industry standard, kullanÄ±cÄ± hesabÄ±na Ã¶zel deÄŸil)
    industry_posting_suggestion = Column(Text)  # "Genel Ã¶neri: LinkedIn iÃ§in SalÄ± 08:00-10:00"
    platform_notes = Column(Text)  # Platform-specific tavsiyeler
    
    regeneration_count = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    idea = relationship("SocialIdea", back_populates="content")


# ==================== TASK TRACKING (Section 10) ====================

class TaskResult(Base):
    """
    Celery task sonuÃ§larÄ±.
    Task durumunu hem Redis hem PostgreSQL'de takip eder.
    """
    __tablename__ = "task_results"
    
    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(String(50), unique=True, index=True, nullable=False)
    task_type = Column(String(50), index=True)  # seo_content, ads, social, export
    scoring_run_id = Column(Integer, ForeignKey("scoring_runs.id", ondelete="SET NULL"), nullable=True)
    
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
    Firma profili - site crawl ile oluÅŸturulur.
    Her scoring run iÃ§in opsiyonel, 1:1 iliÅŸki.
    """
    __tablename__ = "brand_profiles"

    id = Column(Integer, primary_key=True, index=True)
    scoring_run_id = Column(
        Integer,
        ForeignKey("scoring_runs.id", ondelete="CASCADE"),
        nullable=False,
        unique=True
    )

    company_url = Column(String(500), nullable=False)
    competitor_urls = Column(JSON)  # ["url1", "url2", "url3"]

    status = Column(
        String(20),
        nullable=False,
        default="pending"
    )  # pending, running, draft, confirmed, failed

    # AI profil Ã§Ä±ktÄ±sÄ±
    profile_data = Column(JSON)
    # {
    #   "company_name": "Vepa",
    #   "sector": "SaÃ§ bakÄ±m aksesuarlarÄ±",
    #   "products": ["fÃ¶n taraÄŸÄ±", "masaj taraÄŸÄ±", ...],
    #   "services": [],
    #   "target_audience": "Bireysel tÃ¼keticiler, kuafÃ¶rler",
    #   "use_cases": ["saÃ§ ÅŸekillendirme", ...],
    #   "problems_solved": ["dolaÅŸÄ±k saÃ§", ...],
    #   "brand_terms": ["Vepa", ...],
    #   "exclude_themes": ["kimyasal Ã¼rÃ¼nler", ...],
    #   "anchor_texts": ["fÃ¶n taraÄŸÄ± masaj taraÄŸÄ± ...", ...]
    # }

    # Rakip doÄŸrulama
    validation_data = Column(JSON)
    # {
    #   "consistency_score": 0.85,
    #   "competitors": [
    #     {"url": "...", "status": "same_sector", "summary": "..."},
    #   ],
    #   "warnings": []
    # }

    # Crawl metadata
    source_pages = Column(JSON)  # [{"url": "...", "title": "...", "status": 200}]
    error_message = Column(Text)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    scoring_run = relationship("ScoringRun", backref="brand_profile")


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
    matched_anchor = Column(String(500))  # En yÃ¼ksek benzerlik veren anchor text
    method = Column(String(20), nullable=False, default="embedding")  # embedding, fuzzy_fallback

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    scoring_run = relationship("ScoringRun")
    keyword = relationship("Keyword")

    __table_args__ = (
        UniqueConstraint('scoring_run_id', 'keyword_id', name='uq_keyword_relevance'),
        Index('idx_keyword_relevance_run', 'scoring_run_id'),
    )
