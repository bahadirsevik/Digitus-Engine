"""
SQLAlchemy ORM Models.
All database tables are defined here.
"""
from datetime import datetime
from typing import Optional, List
from sqlalchemy import (
    Column, Integer, String, Text, Boolean, DateTime,
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
    Her kelime için temel metrikler ve özellikler saklanır.
    """
    __tablename__ = "keywords"
    
    id = Column(Integer, primary_key=True, index=True)
    keyword = Column(String(500), unique=True, nullable=False, index=True)
    monthly_volume = Column(Integer, nullable=False, default=0)
    trend_12m = Column(Numeric(7, 2), nullable=False, default=0.00)  # -9999.99 ile 99999.99 arası
    trend_3m = Column(Numeric(7, 2), nullable=False, default=0.00)   # -9999.99 ile 99999.99 arası
    competition_score = Column(Numeric(3, 2), nullable=False, default=0.50)  # 0.00 ile 1.00 arası
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
    Her skorlama çalıştırması için meta bilgi.
    Bir run içinde tüm kelimeler skorlanır ve kanallara atanır.
    """
    __tablename__ = "scoring_runs"
    
    id = Column(Integer, primary_key=True, index=True)
    run_name = Column(String(200))
    total_keywords = Column(Integer, nullable=False, default=0)
    ads_capacity = Column(Integer, nullable=False)   # Kullanıcının istediği ADS kelime sayısı
    seo_capacity = Column(Integer, nullable=False)   # Kullanıcının istediği SEO kelime sayısı
    social_capacity = Column(Integer, nullable=False)  # Kullanıcının istediği SOCIAL kelime sayısı
    status = Column(String(50), default="pending")   # pending, scoring, intent_analysis, completed, failed
    started_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    keyword_scores = relationship("KeywordScore", back_populates="scoring_run", cascade="all, delete-orphan")
    channel_candidates = relationship("ChannelCandidate", back_populates="scoring_run", cascade="all, delete-orphan")
    intent_analyses = relationship("IntentAnalysis", back_populates="scoring_run", cascade="all, delete-orphan")
    channel_pools = relationship("ChannelPool", back_populates="scoring_run", cascade="all, delete-orphan")


class KeywordScore(Base):
    """
    Her kelime için hesaplanan skorlar.
    Bir scoring_run içinde her kelime için ADS, SEO, SOCIAL skorları hesaplanır.
    """
    __tablename__ = "keyword_scores"
    
    id = Column(Integer, primary_key=True, index=True)
    scoring_run_id = Column(Integer, ForeignKey("scoring_runs.id", ondelete="CASCADE"), nullable=False)
    keyword_id = Column(Integer, ForeignKey("keywords.id", ondelete="CASCADE"), nullable=False)
    
    # Ham skorlar (formülden çıkan değerler)
    ads_score = Column(Numeric(15, 4))
    seo_score = Column(Numeric(15, 4))
    social_score = Column(Numeric(15, 4))
    
    # Kanal içi sıralama
    ads_rank = Column(Integer, index=True)     # ADS skoruna göre sıralama (1 = en yüksek)
    seo_rank = Column(Integer, index=True)     # SEO skoruna göre sıralama
    social_rank = Column(Integer, index=True)  # SOCIAL skoruna göre sıralama
    
    calculated_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    scoring_run = relationship("ScoringRun", back_populates="keyword_scores")
    keyword = relationship("Keyword", back_populates="scores")
    
    __table_args__ = (
        UniqueConstraint('scoring_run_id', 'keyword_id', name='uq_keyword_scores_run_keyword'),
    )


class ChannelCandidate(Base):
    """
    Niyet analizine alınacak adaylar (2x kapasite).
    Her kanal için en yüksek skorlu kelimeler aday olarak seçilir.
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
    Niyet analizi sonuçları.
    AI ile her aday kelimenin kullanıcı niyeti analiz edilir.
    """
    __tablename__ = "intent_analysis"
    
    id = Column(Integer, primary_key=True, index=True)
    scoring_run_id = Column(Integer, ForeignKey("scoring_runs.id", ondelete="CASCADE"), nullable=False)
    keyword_id = Column(Integer, ForeignKey("keywords.id", ondelete="CASCADE"), nullable=False)
    channel = Column(String(20), nullable=False)  # Hangi kanal için analiz yapıldı
    intent_type = Column(String(50), nullable=False)  # transactional, informational, navigational, commercial, trend_worthy
    confidence_score = Column(Numeric(3, 2))  # 0.00 - 1.00
    ai_reasoning = Column(Text)  # AI'ın açıklaması
    is_passed = Column(Boolean, nullable=False, default=False, index=True)  # Filtreyi geçti mi?
    analyzed_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    scoring_run = relationship("ScoringRun", back_populates="intent_analyses")
    keyword = relationship("Keyword", back_populates="intent_analyses")
    
    __table_args__ = (
        UniqueConstraint('scoring_run_id', 'keyword_id', 'channel', name='uq_intent_analysis'),
        Index('idx_intent_analysis_run_channel', 'scoring_run_id', 'channel'),
    )


class ChannelPool(Base):
    """
    Final kanal havuzları.
    Niyet analizini geçen kelimeler kapasite kadar seçilir.
    """
    __tablename__ = "channel_pools"
    
    id = Column(Integer, primary_key=True, index=True)
    scoring_run_id = Column(Integer, ForeignKey("scoring_runs.id", ondelete="CASCADE"), nullable=False)
    keyword_id = Column(Integer, ForeignKey("keywords.id", ondelete="CASCADE"), nullable=False)
    channel = Column(String(20), nullable=False)  # 'ADS', 'SEO', 'SOCIAL'
    final_rank = Column(Integer, nullable=False)  # Final havuzdaki sıralama
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
    Üretilen içerikler.
    Her kelime için kanal bazlı içerik üretilir ve saklanır.
    """
    __tablename__ = "content_outputs"
    
    id = Column(Integer, primary_key=True, index=True)
    scoring_run_id = Column(Integer, ForeignKey("scoring_runs.id", ondelete="SET NULL"))
    keyword_id = Column(Integer, ForeignKey("keywords.id", ondelete="CASCADE"), nullable=False)
    channel = Column(String(20), nullable=False, index=True)
    content_type = Column(String(50), nullable=False)  # 'blog_post', 'ad_group', 'social_post'
    content_data = Column(JSON, nullable=False)  # Üretilen içerik (JSON formatında)
    
    # Uyumluluk skorları (SEO+GEO için)
    seo_compliance_score = Column(Numeric(3, 2))
    geo_compliance_score = Column(Numeric(3, 2))
    
    generated_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    keyword = relationship("Keyword", back_populates="content_outputs")
    compliance_checks = relationship("ComplianceCheck", back_populates="content_output", cascade="all, delete-orphan")

    __table_args__ = (
        Index('idx_content_outputs_run_channel', 'scoring_run_id', 'channel'),
    )


class ComplianceCheck(Base):
    """
    Uyumluluk kontrol detayları.
    SEO ve GEO uyumluluğu için detaylı kontrol sonuçları.
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
