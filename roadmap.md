**DIGITUS ENGINE - DETAYLI GELİŞTİRME ROADMAP**

Bu doküman Claude Code'a verilecek ve adım adım takip
edilecektir. Her bölüm tamamlandığında bir sonrakine geçilecektir.

---

**BÖLÜM 0: PROJE GENEL BAKIŞ**

**0.1 Sistem Amacı**

Digitus Engine, anahtar kelime analizi ve çok kanallı
pazarlama içerik üretimi yapan bir sistemdir.

**0.2 Üç Temel Bileşen**

1. **Skorlama
   Motoru** : ADS, SEO, SOCIAL için ayrı algoritmalar
2. **Kanal
   Atama Sistemi** : Her kanal bağımsız değerlendirilir, kesişim stratejik
   kelime olur
3. **İçerik
   Üretim Motorları** : AI destekli içerik üretimi (Google Ads, SEO+GEO,
   Social Media)

**0.3 Teknoloji Stack**

* Python
  3.11+
* FastAPI
  (API katmanı)
* PostgreSQL
  15 (veritabanı)
* Redis
  (Celery queue için)
* Celery
  (arka plan görevleri)
* Docker
  & Docker Compose
* OpenAI
  API veya Claude API (içerik üretimi)

---

**BÖLÜM 1: PROJE YAPISINI OLUŞTUR**

**1.1 Ana Dizin Yapısı**

digitus_engine/

├── docker-compose.yml

├── docker-compose.prod.yml

├── Dockerfile

├── .env.example

├── .env

├── .gitignore

├── requirements.txt

├── alembic.ini

├── README.md

│

├── app/

│   ├── __init__.py

│   ├── main.py

# FastAPI app entry point

│   ├── config.py

# Ayarlar ve environment variables

│   ├── dependencies.py

# Dependency injection

│   │

│   ├── database/

│   │   ├──
__init__.py

│   │   ├──
connection.py          # Database engine
ve session

│   │   ├──
models.py              # SQLAlchemy
modelleri (TÜM TABLOLAR)

│   │   └── crud.py                # CRUD operasyonları

│   │

│   ├── schemas/

│   │   ├──
__init__.py

│   │   ├──
keyword.py             # Keyword Pydantic
şemaları

│   │   ├──
scoring.py             # Skorlama şemaları

│   │   ├──
channel.py             # Kanal atama şemaları

│   │   ├──
content.py             # İçerik üretim şemaları

│   │   └── export.py              # Export şemaları

│   │

│   ├── core/

│   │   ├──
__init__.py

│   │   ├──
scoring/

│   │   │   ├── __init__.py

│   │   │   ├── normalizer.py      #
Veri normalizasyonu

│   │   │   ├── ads_scorer.py      #
ADS skorlama algoritması

│   │   │   ├── seo_scorer.py      #
SEO skorlama algoritması

│   │   │   ├── social_scorer.py   #
SOCIAL skorlama algoritması

│   │   │
└── score_engine.py    # Tüm
skorlamaları orkestra eden ana modül

│   │   │

│   │   ├──
channel/

│   │   │   ├── __init__.py

│   │   │   ├── pool_builder.py    #
Kanal havuzlarını
oluşturur

│   │   │   ├── intent_analyzer.py # AI ile niyet analizi

│   │   │   ├── strategic_finder.py # Stratejik kelimeleri bulur

│   │   │
└── channel_engine.py  # Kanal
atama ana modülü

│   │   │

│   │   └── constants.py           # Sabit değerler, katsayılar

│   │

│   ├── generators/

│   │   ├──
__init__.py

│   │   ├──
ai_service.py          # OpenAI/Claude
API wrapper

│   │   ├──
ads/

│   │   │   ├── __init__.py

│   │   │   ├── prompt_templates.py

│   │   │
└── ads_generator.py

│   │   ├──
seo_geo/

│   │   │   ├── __init__.py

│   │   │   ├── prompt_templates.py

│   │   │
└── content_generator.py

│   │   └── social/

│   │       ├──
__init__.py

│   │       ├──
prompt_templates.py

│   │       └── social_generator.py

│   │

│   ├── compliance/

│   │   ├──
__init__.py

│   │   ├──
seo_checker.py         # SEO uyum kontrolü

│   │   └── geo_checker.py         # GEO uyum kontrolü

│   │

│   ├── exporters/

│   │   ├──
__init__.py

│   │   ├──
docx_exporter.py

│   │   ├──
pdf_exporter.py

│   │   └── excel_exporter.py

│   │

│   ├── api/

│   │   ├──
__init__.py

│   │   └── v1/

│   │       ├──
__init__.py

│   │       ├──
router.py          # Ana router

│   │       ├──
keywords.py        # /keywords endpoints

│   │       ├──
scoring.py         # /scoring endpoints

│   │       ├──
channels.py        # /channels endpoints

│   │       ├──
generation.py      # /generate endpoints

│   │       └── export.py          # /export endpoints

│   │

│   └── tasks/

│       ├── __init__.py

│       ├── celery_app.py

# Celery konfigürasyonu

│       ├── scoring_tasks.py

# Skorlama görevleri

│       ├── intent_tasks.py

# Niyet analizi görevleri

│       └──
generation_tasks.py    # İçerik üretim
görevleri

│

├── migrations/

│   └── versions/                  # Alembic migration dosyaları

│

├── scripts/

│   ├── seed_data.py

# Test verisi ekleme

│   └──
init_db.py                 # Veritabanı
başlatma

│

└── tests/

    ├──__init__.py

    ├── conftest.py

# Pytest fixtures

    ├── test_scoring.py

    ├── test_channels.py

    ├── test_generators.py

    └── test_api.py

**1.2 Oluşturulacak Dosyalar (Sırasıyla)**

 **ADIM 1.2.1** : Önce tüm boş __init__.py dosyalarını
oluştur.

 **ADIM 1.2.2** : .gitignore dosyasını oluştur:

__pycache__/

*.py[cod]

.env

.venv/

venv/

*.log

.idea/

.vscode/

*.egg-info/

dist/

build/

.pytest_cache/

htmlcov/

.coverage

 **ADIM 1.2.3** : .env.example dosyasını oluştur (şablon
olarak):

# Database

POSTGRES_USER=digitus

POSTGRES_PASSWORD=digitus_secret_123

POSTGRES_DB=digitus_engine

POSTGRES_HOST=db

POSTGRES_PORT=5432

DATABASE_URL=postgresql://digitus:digitus_secret_123@db:5432/digitus_engine

# Redis

REDIS_URL=redis://redis:6379/0

# AI API

OPENAI_API_KEY=sk-your-key-here

# veya

ANTHROPIC_API_KEY=sk-ant-your-key-here

AI_PROVIDER=openai  #
openai veya anthropic

# App

APP_ENV=development

DEBUG=true

SECRET_KEY=your-secret-key-change-in-production

# Skorlama Katsayıları

ADS_EPSILON=0.01

SEO_COMPETITION_WEIGHT=1.0

SOCIAL_TREND_WEIGHT=3.0

---

**BÖLÜM 2: DOCKER KONFIGÜRASYONU**

**2.1 Dockerfile**

FROM python:3.11-slim

WORKDIR /app

# Sistem bağımlılıkları

RUN apt-get update && apt-get install -y \

    gcc \

    libpq-dev \

    && rm -rf
/var/lib/apt/lists/*

# Python bağımlılıkları

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

# Uygulama kodu

COPY . .

# Port

EXPOSE 8000

# Başlatma komutu

CMD ["uvicorn", "app.main:app",
"--host", "0.0.0.0", "--port", "8000",
"--reload"]

**2.2 docker-compose.yml (Development)**

version: '3.8'

services:

  app:

    build: .

    container_name:
digitus_app

    ports:

    -
"8000:8000"

    volumes:

    - .:/app

    environment:

    -
DATABASE_URL=postgresql://digitus:digitus_secret_123@db:5432/digitus_engine

    -
REDIS_URL=redis://redis:6379/0

    env_file:

    - .env

    depends_on:

    db:

    condition:
service_healthy

    redis:

    condition:
service_started

    networks:

    -
digitus_network

    restart:
unless-stopped

  db:

    image:
postgres:15-alpine

    container_name:
digitus_db

    environment:

    POSTGRES_USER:
digitus

    POSTGRES_PASSWORD:
digitus_secret_123

    POSTGRES_DB:
digitus_engine

    volumes:

    -
postgres_data:/var/lib/postgresql/data

    ports:

    -
"5432:5432"

    healthcheck:

    test:
["CMD-SHELL", "pg_isready -U digitus -d digitus_engine"]

    interval: 5s

    timeout: 5s

    retries: 5

    networks:

    -
digitus_network

    restart:
unless-stopped

  redis:

    image:
redis:7-alpine

    container_name:
digitus_redis

    ports:

    -
"6379:6379"

    volumes:

    -
redis_data:/data

    networks:

    -
digitus_network

    restart:
unless-stopped

  celery_worker:

    build: .

    container_name:
digitus_celery_worker

    command: celery -A
app.tasks.celery_app worker --loglevel=info

    volumes:

    - .:/app

    environment:

    -
DATABASE_URL=postgresql://digitus:digitus_secret_123@db:5432/digitus_engine

    -
REDIS_URL=redis://redis:6379/0

    env_file:

    - .env

    depends_on:

    - db

    - redis

    networks:

    -
digitus_network

    restart:
unless-stopped

  celery_beat:

    build: .

    container_name:
digitus_celery_beat

    command: celery -A
app.tasks.celery_app beat --loglevel=info

    volumes:

    - .:/app

    environment:

    -
DATABASE_URL=postgresql://digitus:digitus_secret_123@db:5432/digitus_engine

    -
REDIS_URL=redis://redis:6379/0

    env_file:

    - .env

    depends_on:

    - db

    - redis

    networks:

    -
digitus_network

    restart:
unless-stopped

volumes:

  postgres_data:

  redis_data:

networks:

  digitus_network:

    driver: bridge

**2.3 requirements.txt**

# Web Framework

fastapi==0.109.0

uvicorn[standard]==0.27.0

python-multipart==0.0.6

# Database

sqlalchemy==2.0.25

psycopg2-binary==2.9.9

alembic==1.13.1

# Validation

pydantic==2.5.3

pydantic-settings==2.1.0

# Task Queue

celery==5.3.6

redis==5.0.1

# AI APIs

openai==1.10.0

anthropic==0.18.0

# Export

python-docx==1.1.0

reportlab==4.0.8

openpyxl==3.1.2

# Utilities

python-dotenv==1.0.0

httpx==0.26.0

# Testing

pytest==7.4.4

pytest-asyncio==0.23.3

pytest-cov==4.1.0

# Logging

loguru==0.7.2

**2.4 Docker Başlatma Komutları**

# İlk kez çalıştırma

docker-compose up --build -d

# Logları izleme

docker-compose logs -f app

# Veritabanına bağlanma

docker exec -it digitus_db psql -U digitus -d digitus_engine

# Uygulamayı durdurma

docker-compose down

# Veritabanı dahil her şeyi silme

docker-compose down -v

---

**BÖLÜM 3: VERİTABANI ŞEMASI**

**3.1 Tüm Tablolar (Detaylı)**

**TABLO 1: keywords**

Ana anahtar kelime tablosu.

CREATE TABLE keywords (

    id SERIAL PRIMARY
KEY,

    keyword
VARCHAR(500) NOT NULL UNIQUE,

    monthly_volume
INTEGER NOT NULL DEFAULT 0,

    trend_12m
DECIMAL(5,2) NOT NULL DEFAULT 0.00,
-- -100.00 ile 999.99 arası (% değişim)

    trend_3m
DECIMAL(5,2) NOT NULL DEFAULT 0.00,
-- -100.00 ile 999.99 arası (% değişim)

    competition_score
DECIMAL(3,2) NOT NULL DEFAULT 0.50, -- 0.00 ile 1.00 arası (normalize)

    sector
VARCHAR(200),

    target_market
VARCHAR(200),

    is_active BOOLEAN
DEFAULT TRUE,

    created_at
TIMESTAMP WITH TIME ZONE DEFAULT NOW(),

    updated_at
TIMESTAMP WITH TIME ZONE DEFAULT NOW()

);

CREATE INDEX idx_keywords_keyword ON keywords(keyword);

CREATE INDEX idx_keywords_sector ON keywords(sector);

CREATE INDEX idx_keywords_active ON keywords(is_active);

**TABLO 2: scoring_runs**

Her skorlama çalıştırması için meta bilgi.

CREATE TABLE scoring_runs (

    id SERIAL PRIMARY
KEY,

    run_name
VARCHAR(200),

    total_keywords
INTEGER NOT NULL DEFAULT 0,

    ads_capacity
INTEGER NOT NULL,          --
Kullanıcının istediği ADS kelime sayısı

    seo_capacity
INTEGER NOT NULL,          --
Kullanıcının istediği SEO kelime sayısı

    social_capacity
INTEGER NOT NULL,       -- Kullanıcının
istediği SOCIAL kelime sayısı

    status VARCHAR(50)
DEFAULT 'pending',   -- pending, scoring,
intent_analysis, completed, failed

    started_at
TIMESTAMP WITH TIME ZONE,

    completed_at
TIMESTAMP WITH TIME ZONE,

    created_at
TIMESTAMP WITH TIME ZONE DEFAULT NOW()

);

**TABLO 3: keyword_scores**

Her kelime için hesaplanan skorlar.

CREATE TABLE keyword_scores (

    id SERIAL PRIMARY
KEY,

    scoring_run_id
INTEGER NOT NULL REFERENCES scoring_runs(id) ON DELETE CASCADE,

    keyword_id INTEGER
NOT NULL REFERENCES keywords(id) ON DELETE CASCADE,

    -- Ham skorlar
(formülden çıkan değerler)

    ads_score
DECIMAL(15,4),

    seo_score
DECIMAL(15,4),

    social_score
DECIMAL(15,4),

    -- Kanal içi
sıralama

    ads_rank
INTEGER,           -- ADS skoruna göre
sıralama (1 = en yüksek)

    seo_rank
INTEGER,           -- SEO skoruna göre
sıralama

    social_rank
INTEGER,        -- SOCIAL skoruna göre
sıralama

    calculated_at
TIMESTAMP WITH TIME ZONE DEFAULT NOW(),

UNIQUE(scoring_run_id, keyword_id)

);

CREATE INDEX idx_keyword_scores_run ON
keyword_scores(scoring_run_id);

CREATE INDEX idx_keyword_scores_ads_rank ON
keyword_scores(ads_rank);

CREATE INDEX idx_keyword_scores_seo_rank ON
keyword_scores(seo_rank);

CREATE INDEX idx_keyword_scores_social_rank ON
keyword_scores(social_rank);

**TABLO 4: channel_candidates**

Niyet analizine alınacak adaylar (2x kapasite).

CREATE TABLE channel_candidates (

    id SERIAL PRIMARY
KEY,

    scoring_run_id
INTEGER NOT NULL REFERENCES scoring_runs(id) ON DELETE CASCADE,

    keyword_id INTEGER
NOT NULL REFERENCES keywords(id) ON DELETE CASCADE,

    channel
VARCHAR(20) NOT NULL,           -- 'ADS',
'SEO', 'SOCIAL'

    raw_score
DECIMAL(15,4) NOT NULL,

    rank_in_channel
INTEGER NOT NULL,

    created_at
TIMESTAMP WITH TIME ZONE DEFAULT NOW(),

UNIQUE(scoring_run_id, keyword_id, channel)

);

CREATE INDEX idx_channel_candidates_run_channel ON
channel_candidates(scoring_run_id, channel);

**TABLO 5: intent_analysis**

Niyet analizi sonuçları.

CREATE TABLE intent_analysis (

    id SERIAL PRIMARY
KEY,

    scoring_run_id
INTEGER NOT NULL REFERENCES scoring_runs(id) ON DELETE CASCADE,

    keyword_id INTEGER
NOT NULL REFERENCES keywords(id) ON DELETE CASCADE,

    channel
VARCHAR(20) NOT NULL,           -- Hangi
kanal için analiz yapıldı

    intent_type
VARCHAR(50) NOT NULL,       --
transactional, informational, navigational, commercial, trend_worthy

    confidence_score
DECIMAL(3,2),          -- 0.00 - 1.00

    ai_reasoning
TEXT,                      -- AI'ın
açıklaması

    is_passed BOOLEAN
NOT NULL DEFAULT FALSE,  -- Filtreyi
geçti mi?

    analyzed_at
TIMESTAMP WITH TIME ZONE DEFAULT NOW(),

UNIQUE(scoring_run_id, keyword_id, channel)

);

CREATE INDEX idx_intent_analysis_run_channel ON
intent_analysis(scoring_run_id, channel);

CREATE INDEX idx_intent_analysis_passed ON
intent_analysis(is_passed);

**TABLO 6: channel_pools**

Final kanal havuzları.

CREATE TABLE channel_pools (

    id SERIAL PRIMARY
KEY,

    scoring_run_id
INTEGER NOT NULL REFERENCES scoring_runs(id) ON DELETE CASCADE,

    keyword_id INTEGER
NOT NULL REFERENCES keywords(id) ON DELETE CASCADE,

    channel
VARCHAR(20) NOT NULL,           -- 'ADS',
'SEO', 'SOCIAL'

    final_rank INTEGER
NOT NULL,            -- Final havuzdaki
sıralama

    is_strategic
BOOLEAN DEFAULT FALSE,     -- Stratejik
kelime mi?

    created_at
TIMESTAMP WITH TIME ZONE DEFAULT NOW(),

UNIQUE(scoring_run_id, keyword_id, channel)

);

CREATE INDEX idx_channel_pools_run_channel ON
channel_pools(scoring_run_id, channel);

CREATE INDEX idx_channel_pools_strategic ON
channel_pools(is_strategic);

**TABLO 7: content_outputs**

Üretilen içerikler.

CREATE TABLE content_outputs (

    id SERIAL PRIMARY
KEY,

    scoring_run_id
INTEGER REFERENCES scoring_runs(id) ON DELETE SET NULL,

    keyword_id INTEGER
NOT NULL REFERENCES keywords(id) ON DELETE CASCADE,

    channel
VARCHAR(20) NOT NULL,

    content_type
VARCHAR(50) NOT NULL,      --
'blog_post', 'ad_group', 'social_post'

    content_data JSONB
NOT NULL,            -- Üretilen içerik
(JSON formatında)

    -- Uyumluluk
skorları (SEO+GEO için)

seo_compliance_score DECIMAL(3,2),

geo_compliance_score DECIMAL(3,2),

    generated_at
TIMESTAMP WITH TIME ZONE DEFAULT NOW()

);

CREATE INDEX idx_content_outputs_keyword ON
content_outputs(keyword_id);

CREATE INDEX idx_content_outputs_channel ON
content_outputs(channel);

**TABLO 8: compliance_checks**

Uyumluluk kontrol detayları.

CREATE TABLE compliance_checks (

    id SERIAL PRIMARY
KEY,

    content_output_id
INTEGER NOT NULL REFERENCES content_outputs(id) ON DELETE CASCADE,

    check_type
VARCHAR(20) NOT NULL,        -- 'SEO',
'GEO'

    criteria
VARCHAR(200) NOT NULL,         -- Kontrol
edilen kriter

    status VARCHAR(20)
NOT NULL,            -- 'pass',
'partial', 'fail'

    notes TEXT,

    checked_at
TIMESTAMP WITH TIME ZONE DEFAULT NOW()

);

CREATE INDEX idx_compliance_checks_output ON
compliance_checks(content_output_id);

**3.2 SQLAlchemy Modelleri**

app/database/models.py dosyasında tüm modeller şu şekilde
tanımlanacak:

from datetime import datetime

from typing import Optional

from sqlalchemy import (

    Column, Integer,
String, Text, Boolean, DateTime,

    ForeignKey,
Numeric, JSON, UniqueConstraint, Index

)

from sqlalchemy.orm import relationship, DeclarativeBase

from sqlalchemy.sql import func

class Base(DeclarativeBase):

    pass

class Keyword(Base):

    __tablename__ =
"keywords"

    id =
Column(Integer, primary_key=True, index=True)

    keyword =
Column(String(500), unique=True, nullable=False, index=True)

    monthly_volume =
Column(Integer, nullable=False, default=0)

    trend_12m =
Column(Numeric(5, 2), nullable=False, default=0.00)

    trend_3m =
Column(Numeric(5, 2), nullable=False, default=0.00)

    competition_score
= Column(Numeric(3, 2), nullable=False, default=0.50)

    sector =
Column(String(200), index=True)

    target_market =
Column(String(200))

    is_active =
Column(Boolean, default=True, index=True)

    created_at =
Column(DateTime(timezone=True), server_default=func.now())

    updated_at =
Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships

    scores =
relationship("KeywordScore", back_populates="keyword",
cascade="all, delete-orphan")

    intent_analyses =
relationship("IntentAnalysis", back_populates="keyword",
cascade="all, delete-orphan")

    channel_pools =
relationship("ChannelPool", back_populates="keyword",
cascade="all, delete-orphan")

    content_outputs =
relationship("ContentOutput", back_populates="keyword",
cascade="all, delete-orphan")

class ScoringRun(Base):

    __tablename__ =
"scoring_runs"

    id =
Column(Integer, primary_key=True, index=True)

    run_name =
Column(String(200))

    total_keywords =
Column(Integer, nullable=False, default=0)

    ads_capacity =
Column(Integer, nullable=False)

    seo_capacity =
Column(Integer, nullable=False)

    social_capacity =
Column(Integer, nullable=False)

    status =
Column(String(50), default="pending")

    started_at =
Column(DateTime(timezone=True))

    completed_at =
Column(DateTime(timezone=True))

    created_at =
Column(DateTime(timezone=True), server_default=func.now())

    # Relationships

    keyword_scores =
relationship("KeywordScore", back_populates="scoring_run",
cascade="all, delete-orphan")

    channel_candidates
= relationship("ChannelCandidate",
back_populates="scoring_run", cascade="all, delete-orphan")

    intent_analyses =
relationship("IntentAnalysis",
back_populates="scoring_run", cascade="all, delete-orphan")

    channel_pools =
relationship("ChannelPool", back_populates="scoring_run",
cascade="all, delete-orphan")

class KeywordScore(Base):

    __tablename__ =
"keyword_scores"

    id =
Column(Integer, primary_key=True, index=True)

    scoring_run_id =
Column(Integer, ForeignKey("scoring_runs.id",
ondelete="CASCADE"), nullable=False)

    keyword_id =
Column(Integer, ForeignKey("keywords.id",
ondelete="CASCADE"), nullable=False)

    ads_score =
Column(Numeric(15, 4))

    seo_score =
Column(Numeric(15, 4))

    social_score =
Column(Numeric(15, 4))

    ads_rank =
Column(Integer, index=True)

    seo_rank =
Column(Integer, index=True)

    social_rank =
Column(Integer, index=True)

    calculated_at =
Column(DateTime(timezone=True), server_default=func.now())

    # Relationships

    scoring_run =
relationship("ScoringRun", back_populates="keyword_scores")

    keyword =
relationship("Keyword", back_populates="scores")

    __table_args__ = (

UniqueConstraint('scoring_run_id', 'keyword_id',
name='uq_keyword_scores_run_keyword'),

    )

class ChannelCandidate(Base):

    __tablename__ =
"channel_candidates"

    id =
Column(Integer, primary_key=True, index=True)

    scoring_run_id =
Column(Integer, ForeignKey("scoring_runs.id",
ondelete="CASCADE"), nullable=False)

    keyword_id =
Column(Integer, ForeignKey("keywords.id",
ondelete="CASCADE"), nullable=False)

    channel =
Column(String(20), nullable=False)

    raw_score =
Column(Numeric(15, 4), nullable=False)

    rank_in_channel =
Column(Integer, nullable=False)

    created_at =
Column(DateTime(timezone=True), server_default=func.now())

    # Relationships

    scoring_run =
relationship("ScoringRun",
back_populates="channel_candidates")

    __table_args__ = (

    UniqueConstraint('scoring_run_id',
'keyword_id', 'channel', name='uq_channel_candidates'),

Index('idx_channel_candidates_run_channel', 'scoring_run_id',
'channel'),

    )

class IntentAnalysis(Base):

    __tablename__ =
"intent_analysis"

    id =
Column(Integer, primary_key=True, index=True)

    scoring_run_id =
Column(Integer, ForeignKey("scoring_runs.id",
ondelete="CASCADE"), nullable=False)

    keyword_id =
Column(Integer, ForeignKey("keywords.id",
ondelete="CASCADE"), nullable=False)

    channel =
Column(String(20), nullable=False)

    intent_type =
Column(String(50), nullable=False)

    confidence_score =
Column(Numeric(3, 2))

    ai_reasoning =
Column(Text)

    is_passed =
Column(Boolean, nullable=False, default=False, index=True)

    analyzed_at =
Column(DateTime(timezone=True), server_default=func.now())

    # Relationships

    scoring_run =
relationship("ScoringRun",
back_populates="intent_analyses")

    keyword =
relationship("Keyword", back_populates="intent_analyses")

    __table_args__ = (

UniqueConstraint('scoring_run_id', 'keyword_id', 'channel',
name='uq_intent_analysis'),

    Index('idx_intent_analysis_run_channel',
'scoring_run_id', 'channel'),

    )

class ChannelPool(Base):

    __tablename__ =
"channel_pools"

    id =
Column(Integer, primary_key=True, index=True)

    scoring_run_id =
Column(Integer, ForeignKey("scoring_runs.id",
ondelete="CASCADE"), nullable=False)

    keyword_id =
Column(Integer, ForeignKey("keywords.id",
ondelete="CASCADE"), nullable=False)

    channel =
Column(String(20), nullable=False)

    final_rank =
Column(Integer, nullable=False)

    is_strategic =
Column(Boolean, default=False, index=True)

    created_at =
Column(DateTime(timezone=True), server_default=func.now())

    # Relationships

    scoring_run =
relationship("ScoringRun", back_populates="channel_pools")

    keyword =
relationship("Keyword", back_populates="channel_pools")

    __table_args__ = (

UniqueConstraint('scoring_run_id', 'keyword_id', 'channel',
name='uq_channel_pools'),

Index('idx_channel_pools_run_channel', 'scoring_run_id', 'channel'),

    )

class ContentOutput(Base):

    __tablename__ =
"content_outputs"

    id =
Column(Integer, primary_key=True, index=True)

    scoring_run_id =
Column(Integer, ForeignKey("scoring_runs.id", ondelete="SET
NULL"))

    keyword_id =
Column(Integer, ForeignKey("keywords.id",
ondelete="CASCADE"), nullable=False)

    channel =
Column(String(20), nullable=False, index=True)

    content_type =
Column(String(50), nullable=False)

    content_data =
Column(JSON, nullable=False)

seo_compliance_score = Column(Numeric(3, 2))

geo_compliance_score = Column(Numeric(3, 2))

    generated_at =
Column(DateTime(timezone=True), server_default=func.now())

    # Relationships

    keyword =
relationship("Keyword", back_populates="content_outputs")

    compliance_checks
= relationship("ComplianceCheck",
back_populates="content_output", cascade="all,
delete-orphan")

class ComplianceCheck(Base):

    __tablename__ =
"compliance_checks"

    id =
Column(Integer, primary_key=True, index=True)

    content_output_id
= Column(Integer, ForeignKey("content_outputs.id",
ondelete="CASCADE"), nullable=False)

    check_type =
Column(String(20), nullable=False)

    criteria =
Column(String(200), nullable=False)

    status =
Column(String(20), nullable=False)

    notes =
Column(Text)

    checked_at =
Column(DateTime(timezone=True), server_default=func.now())

    # Relationships

    content_output =
relationship("ContentOutput",
back_populates="compliance_checks")

---

**BÖLÜM 4: SKORLAMA ALGORİTMALARI**

**4.1 Sabit Değerler (app/core/constants.py)**

# Skorlama katsayıları

ADS_EPSILON = 0.01  #
Sıfıra bölme hatası önleme

# Trend ağırlıkları

ADS_TREND_3M_WEIGHT = 0.6

ADS_TREND_12M_WEIGHT = 0.4

SEO_TREND_3M_WEIGHT = 2.0

SEO_TREND_12M_WEIGHT = 1.0

SOCIAL_TREND_3M_WEIGHT = 3.0

SOCIAL_TREND_12M_WEIGHT = 1.0

# Kanal havuzu çarpanı (kapasite x bu değer = aday sayısı)

POOL_MULTIPLIER = 2

# Niyet tipleri

INTENT_TYPES = {

"transactional": "Satın alma niyetli",

"informational": "Bilgi arayışı",

    "navigational":
"Marka/site yönelimli",

"commercial": "Araştırma + satın alma karışık",

"trend_worthy": "Trend/viral potansiyeli"

}

# Kanal bazlı kabul edilen niyet tipleri

CHANNEL_ACCEPTED_INTENTS = {

    "ADS":
["transactional", "commercial"],

    "SEO":
["informational", "commercial"],

"SOCIAL": ["trend_worthy",
"informational"]

}

**4.2 Veri Normalizasyonu (app/core/scoring/normalizer.py)**

import math

from typing import List, Dict, Any

from decimal import Decimal

def normalize_competition(value: float, min_val: float = 0,
max_val: float = 100) -> float:

    """

    Rekabet skorunu
0-1 arasına normalize eder.

    Gelen değer 0-100
arası ise 0-1'e çevirir.

    """

    if value <= 1:

    return
value  # Zaten normalize

    return (value -
min_val) / (max_val - min_val)

def normalize_trend(value: float) -> float:

    """

    Trend değerini
işlenebilir hale getirir.

    Negatif değerler
0.1'e çekilir (sıfır olmasın).

    Pozitif değerler 1

+ (değer/100) olarak hesaplanır.

  Örnek:

  -50% trend
  -> 0.5

  0% trend ->
  1.0

  50% trend
  -> 1.5

  100% trend
  -> 2.0

  """

  if value < 0:

  return
  max(0.1, 1 + (value / 100))

  return 1 + (value
  / 100)

def safe_log(value: float, base: float = 10) -> float:

    """

    Güvenli logaritma
hesaplama.

    0 ve negatif
değerler için minimum 1 kullanılır.

    """

    if value <= 0:

    return 0

    return
math.log(max(1, value), base)

def calculate_combined_trend(

    trend_3m: float,

    trend_12m: float,

    weight_3m: float,

    weight_12m: float

) -> float:

    """

    Ağırlıklı trend
ortalaması hesaplar.

    """

    norm_3m =
normalize_trend(trend_3m)

    norm_12m =
normalize_trend(trend_12m)

    total_weight =
weight_3m + weight_12m

    return (norm_3m *
weight_3m + norm_12m * weight_12m) / total_weight

**4.3 ADS Skorlama (app/core/scoring/ads_scorer.py)**

"""

ADS SKORLAMA - The ROI Hunter

Amaç: Talebi yüksek, trendi yükselen, maliyeti makul
kelimeleri bul.

Formül:

ADS_Skor = (√(Hacim+1) × Trend_Kombine) / √(Rekabet + ε)

Açıklama:

- Hacim yükseldikçe skor artar
- Trend pozitifse skor artar
- Rekabet yükseldikçe skor AZALIR (ama √ ile yumuşatılır)
- ε sıfıra bölmeyi önler

"""

import math

from decimal import Decimal

from typing import List, Dict, Any

from app.core.constants import (

    ADS_EPSILON,

ADS_TREND_3M_WEIGHT,

ADS_TREND_12M_WEIGHT

)

from app.core.scoring.normalizer import (

normalize_competition,

calculate_combined_trend

)

def calculate_ads_score(

    monthly_volume:
int,

    trend_3m: float,

    trend_12m: float,

    competition_score:
float

) -> float:

    """

    Tek bir kelime
için ADS skoru hesaplar.

    Args:

monthly_volume: Aylık arama hacmi

    trend_3m: Son
3 aylık trend (% değişim)

    trend_12m: Son
12 aylık trend (% değişim)

competition_score: Rekabet skoru (0-1 arası normalize)

    Returns:

    ADS skoru
(float)

    """

    # Hacim 0 ise skor
0

    if monthly_volume
<= 0:

    return 0.0

    # Trend kombine
hesapla

    trend_combined =
calculate_combined_trend(

trend_3m=trend_3m,

    trend_12m=trend_12m,

weight_3m=ADS_TREND_3M_WEIGHT,

weight_12m=ADS_TREND_12M_WEIGHT

    )

    # Rekabet
normalize et (0-1 arası olmalı)

competition_normalized = normalize_competition(competition_score)

    # Formül: (Hacim ×
Trend) / √(Rekabet + ε)

    numerator =
monthly_volume * trend_combined

    denominator =
math.sqrt(competition_normalized + ADS_EPSILON)

    score = numerator
/ denominator

    return
round(score, 4)

def calculate_bulk_ads_scores(keywords: List[Dict[str,
Any]]) -> List[Dict[str, Any]]:

    """

    Birden fazla
kelime için ADS skorlarını hesaplar.

    Args:

    keywords:
Kelime listesi, her biri şu alanları içermeli:

    - id

    -
monthly_volume

    - trend_3m

    -
trend_12m

    -
competition_score

    Returns:

    Skor eklenmiş
kelime listesi

    """

    results = []

    for kw in
keywords:

    score =
calculate_ads_score(

monthly_volume=kw['monthly_volume'],

trend_3m=float(kw['trend_3m']),

trend_12m=float(kw['trend_12m']),

competition_score=float(kw['competition_score'])

    )

results.append({

'keyword_id': kw['id'],

'ads_score': score

    })

    # Skora göre
sırala (büyükten küçüğe)

results.sort(key=lambda x: x['ads_score'], reverse=True)

    # Sıralama
numarası ekle

    for rank, item in
enumerate(results, 1):

    item['ads_rank']
= rank

    return results

**4.4 SEO Skorlama (app/core/scoring/seo_scorer.py)**

"""

SEO SKORLAMA - The Opportunity Engine

Amaç: Rekabeti düşük, trendi yükselen, uzun vadeli otorite
kelimelerini bul.

Formül:

SEO_Skor = log(Hacim) × (Trend3 × 2 + Trend12) ×
Rekabet_Çarpanı

Açıklama:

- log(Hacim): Dev hacimlerin niş kelimeleri ezmesini
  engeller
- Trend3 daha ağırlıklı: Güncel momentum önemli
- Rekabet_Çarpanı: Düşük rekabet = yüksek fırsat (1 -
  rekabet)

"""

import math

from decimal import Decimal

from typing import List, Dict, Any

from app.core.constants import (

SEO_TREND_3M_WEIGHT,

SEO_TREND_12M_WEIGHT

)

from app.core.scoring.normalizer import (

normalize_competition,

    normalize_trend,

    safe_log

)

def calculate_seo_score(

    monthly_volume:
int,

    trend_3m: float,

    trend_12m: float,

    competition_score:
float

) -> float:

    """

    Tek bir kelime
için SEO skoru hesaplar.

    Args:

monthly_volume: Aylık arama hacmi

    trend_3m: Son
3 aylık trend (% değişim)

    trend_12m: Son
12 aylık trend (% değişim)

competition_score: Rekabet skoru (0-1 arası normalize)

    Returns:

    SEO skoru
(float)

    """

    # Hacim 0 ise skor
0

    if monthly_volume
<= 0:

    return 0.0

    # log(Hacim)

    log_volume =
safe_log(monthly_volume)

    # Trend hesapla

    norm_trend_3m =
normalize_trend(trend_3m)

    norm_trend_12m =
normalize_trend(trend_12m)

    # Trend kombine:
(Trend3 × 2 + Trend12)

    trend_factor =
(norm_trend_3m * SEO_TREND_3M_WEIGHT) + (norm_trend_12m * SEO_TREND_12M_WEIGHT)

    # Rekabet çarpanı:
Düşük rekabet = yüksek çarpan

    # Rekabet 0 ise
çarpan 1, rekabet 1 ise çarpan 0.1 (minimum)

competition_normalized = normalize_competition(competition_score)

competition_multiplier = max(0.1, 1 - competition_normalized)

    # Formül

    score = log_volume

* trend_factor * competition_multiplier

  return
  round(score, 4)

def calculate_bulk_seo_scores(keywords: List[Dict[str,
Any]]) -> List[Dict[str, Any]]:

    """

    Birden fazla
kelime için SEO skorlarını hesaplar.

    """

    results = []

    for kw in
keywords:

    score =
calculate_seo_score(

monthly_volume=kw['monthly_volume'],

trend_3m=float(kw['trend_3m']),

trend_12m=float(kw['trend_12m']),

competition_score=float(kw['competition_score'])

    )

results.append({

    'keyword_id':
kw['id'],

'seo_score': score

    })

    # Skora göre
sırala

results.sort(key=lambda x: x['seo_score'], reverse=True)

    # Sıralama
numarası ekle

    for rank, item in
enumerate(results, 1):

item['seo_rank'] = rank

    return results

**4.5 SOCIAL Skorlama (app/core/scoring/social_scorer.py)**

"""

SOCIAL SKORLAMA - The Hype Tracker

Amaç: Kitle karşılığı olan, güncel konuşulma ivmesi yüksek
konuları bul.

Formül:

SOCIAL_Skor = log(Hacim) × (Trend3 × 3 + Trend12)

Açıklama:

- log(Hacim): Sosyal erişim tabanı
- Trend3 ağırlığı 3x: Sosyal medyada anlık trend çok önemli
- Rekabet faktörü YOK: Sosyal medyada rekabet farklı işler

"""

import math

from decimal import Decimal

from typing import List, Dict, Any

from app.core.constants import (

SOCIAL_TREND_3M_WEIGHT,

SOCIAL_TREND_12M_WEIGHT

)

from app.core.scoring.normalizer import (

    normalize_trend,

    safe_log

)

def calculate_social_score(

    monthly_volume:
int,

    trend_3m: float,

    trend_12m: float

) -> float:

    """

    Tek bir kelime
için SOCIAL skoru hesaplar.

    NOT: Rekabet
parametresi yok, sosyal medyada farklı dinamikler var.

    Args:

    monthly_volume:
Aylık arama hacmi

    trend_3m: Son
3 aylık trend (% değişim)

    trend_12m: Son
12 aylık trend (% değişim)

    Returns:

    SOCIAL skoru
(float)

    """

    # Hacim 0 ise skor
0

    if monthly_volume
<= 0:

    return 0.0

    # log(Hacim)

    log_volume =
safe_log(monthly_volume)

    # Trend hesapla

    norm_trend_3m =
normalize_trend(trend_3m)

    norm_trend_12m =
normalize_trend(trend_12m)

    # Trend kombine:
(Trend3 × 3 + Trend12)

    trend_factor =
(norm_trend_3m * SOCIAL_TREND_3M_WEIGHT) + (norm_trend_12m *
SOCIAL_TREND_12M_WEIGHT)

    # Formül

    score = log_volume

* trend_factor

  return
  round(score, 4)

def calculate_bulk_social_scores(keywords: List[Dict[str,
Any]]) -> List[Dict[str, Any]]:

    """

    Birden fazla
kelime için SOCIAL skorlarını hesaplar.

    """

    results = []

    for kw in
keywords:

    score =
calculate_social_score(

monthly_volume=kw['monthly_volume'],

    trend_3m=float(kw['trend_3m']),

trend_12m=float(kw['trend_12m'])

    )

results.append({

'keyword_id': kw['id'],

'social_score': score

    })

    # Skora göre
sırala

results.sort(key=lambda x: x['social_score'], reverse=True)

    # Sıralama
numarası ekle

    for rank, item in
enumerate(results, 1):

item['social_rank'] = rank

    return results

**4.6 Skor Motoru (app/core/scoring/score_engine.py)**

"""

Ana skorlama motoru.

Tüm skorlamaları orkestra eder ve veritabanına kaydeder.

"""

from typing import List, Dict, Any, Tuple

from sqlalchemy.orm import Session

from datetime import datetime

from app.database.models import Keyword, ScoringRun,
KeywordScore

from app.core.scoring.ads_scorer import
calculate_bulk_ads_scores

from app.core.scoring.seo_scorer import
calculate_bulk_seo_scores

from app.core.scoring.social_scorer import
calculate_bulk_social_scores

class ScoreEngine:

    def__init__(self,
db: Session):

    self.db = db

    def
create_scoring_run(

    self,

    ads_capacity:
int,

    seo_capacity:
int,

social_capacity: int,

    run_name: str
= None

    ) ->
ScoringRun:

"""Yeni bir skorlama çalıştırması
oluşturur."""

    # Aktif kelime
sayısını al

    total_keywords
= self.db.query(Keyword).filter(Keyword.is_active == True).count()

    scoring_run =
ScoringRun(

run_name=run_name or
f"Run_{datetime.now().strftime('%Y%m%d_%H%M%S')}",

total_keywords=total_keywords,

ads_capacity=ads_capacity,

seo_capacity=seo_capacity,

    social_capacity=social_capacity,

status="pending"

    )

self.db.add(scoring_run)

self.db.commit()

self.db.refresh(scoring_run)

    return
scoring_run

    def
run_scoring(self, scoring_run_id: int) -> Dict[str, Any]:

"""

    Tüm
skorlamaları çalıştırır.

    Returns:

    Skorlama
özeti

"""

    # Scoring
run'ı al

    scoring_run =
self.db.query(ScoringRun).filter(ScoringRun.id == scoring_run_id).first()

    if not
scoring_run:

    raise
ValueError(f"Scoring run {scoring_run_id} bulunamadı")

    # Durumu
güncelle

scoring_run.status = "scoring"

scoring_run.started_at = datetime.utcnow()

self.db.commit()

    try:

    # Aktif
kelimeleri al

    keywords =
self.db.query(Keyword).filter(Keyword.is_active == True).all()

    # Dict
listesine çevir

keyword_dicts = [

    {

    'id': kw.id,

'monthly_volume': kw.monthly_volume,

'trend_3m': kw.trend_3m,

'trend_12m': kw.trend_12m,

'competition_score': kw.competition_score

    }

    for kw
in keywords

    ]

    # Her
kanal için skorla

ads_results = calculate_bulk_ads_scores(keyword_dicts)

seo_results = calculate_bulk_seo_scores(keyword_dicts)

social_results = calculate_bulk_social_scores(keyword_dicts)

    #
Sonuçları birleştir (keyword_id bazında)

    combined =
{}

    for item
in ads_results:

    kid =
item['keyword_id']

combined[kid] = {

'keyword_id': kid,

'ads_score': item['ads_score'],

'ads_rank': item['ads_rank']

    }

    for item
in seo_results:

    kid =
item['keyword_id']

combined[kid]['seo_score'] = item['seo_score']

combined[kid]['seo_rank'] = item['seo_rank']

    for item
in social_results:

    kid =
item['keyword_id']

combined[kid]['social_score'] = item['social_score']

combined[kid]['social_rank'] = item['social_rank']

    #
Veritabanına kaydet

    for kid,
scores in combined.items():

keyword_score = KeywordScore(

scoring_run_id=scoring_run_id,

keyword_id=kid,

ads_score=scores['ads_score'],

seo_score=scores['seo_score'],

social_score=scores['social_score'],

ads_rank=scores['ads_rank'],

seo_rank=scores['seo_rank'],

social_rank=scores['social_rank']

    )

self.db.add(keyword_score)

self.db.commit()

    # Durumu
güncelle

scoring_run.status = "scored"

self.db.commit()

    return {

'scoring_run_id': scoring_run_id,

'total_scored': len(combined),

'status': 'scored'

    }

    except
Exception as e:

scoring_run.status = "failed"

self.db.commit()

    raise e

---

**BÖLÜM 5: KANAL ATAMA SİSTEMİ**

**5.1 Havuz Oluşturucu (app/core/channel/pool_builder.py)**

"""

Kanal havuzlarını oluşturur.

Her kanal için 2x kapasite kadar aday seçer.

"""

from typing import List, Dict, Any

from sqlalchemy.orm import Session

from sqlalchemy import desc

from app.database.models import KeywordScore,
ChannelCandidate, ScoringRun

from app.core.constants import POOL_MULTIPLIER

class PoolBuilder:

    def__init__(self,
db: Session):

    self.db = db

    def
build_candidate_pools(self, scoring_run_id: int) -> Dict[str, int]:

"""

    Her kanal için
aday havuzlarını oluşturur.

    Returns:

    Her kanal
için seçilen aday sayısı

"""

    # Scoring
run'ı al

    scoring_run =
self.db.query(ScoringRun).filter(ScoringRun.id == scoring_run_id).first()

    if not
scoring_run:

    raise
ValueError(f"Scoring run {scoring_run_id} bulunamadı")

    # Kapasite
hesapla (2x)

    ads_pool_size
= scoring_run.ads_capacity * POOL_MULTIPLIER

    seo_pool_size
= scoring_run.seo_capacity * POOL_MULTIPLIER

social_pool_size = scoring_run.social_capacity * POOL_MULTIPLIER

    # ADS
adaylarını seç

    ads_candidates
= (

self.db.query(KeywordScore)

.filter(KeywordScore.scoring_run_id == scoring_run_id)

.order_by(KeywordScore.ads_rank)

.limit(ads_pool_size)

    .all()

    )

    for candidate
in ads_candidates:

self.db.add(ChannelCandidate(

scoring_run_id=scoring_run_id,

keyword_id=candidate.keyword_id,

channel="ADS",

raw_score=candidate.ads_score,

rank_in_channel=candidate.ads_rank

    ))

    # SEO
adaylarını seç

    seo_candidates
= (

self.db.query(KeywordScore)

.filter(KeywordScore.scoring_run_id == scoring_run_id)

.order_by(KeywordScore.seo_rank)

.limit(seo_pool_size)

    .all()

    )

    for candidate
in seo_candidates:

self.db.add(ChannelCandidate(

scoring_run_id=scoring_run_id,

keyword_id=candidate.keyword_id,

channel="SEO",

raw_score=candidate.seo_score,

rank_in_channel=candidate.seo_rank

    ))

    # SOCIAL
adaylarını seç

social_candidates = (

    self.db.query(KeywordScore)

.filter(KeywordScore.scoring_run_id == scoring_run_id)

.order_by(KeywordScore.social_rank)

.limit(social_pool_size)

    .all()

    )

    for candidate
in social_candidates:

self.db.add(ChannelCandidate(

scoring_run_id=scoring_run_id,

keyword_id=candidate.keyword_id,

channel="SOCIAL",

raw_score=candidate.social_score,

rank_in_channel=candidate.social_rank

    ))

self.db.commit()

    return {

    'ADS':
len(ads_candidates),

    'SEO':
len(seo_candidates),

    'SOCIAL':
len(social_candidates)

    }

**5.2 Niyet Analizcisi
(app/core/channel/intent_analyzer.py)**

"""

AI ile niyet analizi yapar.

Her kanal için uygun niyet tiplerini filtreler.

"""

from typing import List, Dict, Any, Optional

from sqlalchemy.orm import Session

import json

from app.database.models import (

    ChannelCandidate,
IntentAnalysis, Keyword, ScoringRun

)

from app.core.constants import CHANNEL_ACCEPTED_INTENTS,
INTENT_TYPES

from app.generators.ai_service import AIService

class IntentAnalyzer:

    def__init__(self,
db: Session, ai_service: AIService):

    self.db = db

self.ai_service = ai_service

    def
analyze_candidates(self, scoring_run_id: int, channel: str) -> Dict[str,
Any]:

"""

    Belirli bir
kanal için tüm adayların niyet analizini yapar.

    Args:

scoring_run_id: Skorlama çalıştırma ID'si

    channel:
Kanal adı ('ADS', 'SEO', 'SOCIAL')

    Returns:

    Analiz
özeti

"""

    # Adayları al

    candidates = (

self.db.query(ChannelCandidate, Keyword)

.join(Keyword, ChannelCandidate.keyword_id == Keyword.id)

.filter(ChannelCandidate.scoring_run_id == scoring_run_id)

.filter(ChannelCandidate.channel == channel)

.order_by(ChannelCandidate.rank_in_channel)

    .all()

    )

    if not
candidates:

    return
{'analyzed': 0, 'passed': 0}

    # Kabul edilen
niyet tipleri

accepted_intents = CHANNEL_ACCEPTED_INTENTS.get(channel, [])

    # Toplu analiz
için kelimeleri hazırla

keywords_to_analyze = [

    {'id':
candidate.keyword_id, 'keyword': keyword.keyword}

    for
candidate, keyword in candidates

    ]

    # AI ile toplu
niyet analizi

    intent_results
= self._batch_analyze_intent(keywords_to_analyze)

    passed_count =
0

    for
(candidate, keyword), intent_result in zip(candidates, intent_results):

    intent_type = intent_result.get('intent_type',
'informational')

    confidence
= intent_result.get('confidence', 0.5)

    reasoning
= intent_result.get('reasoning', '')

    # Filtreyi
geçti mi?

    is_passed
= intent_type in accepted_intents

    if
is_passed:

passed_count += 1

    #
Veritabanına kaydet

intent_analysis = IntentAnalysis(

scoring_run_id=scoring_run_id,

keyword_id=candidate.keyword_id,

channel=channel,

intent_type=intent_type,

confidence_score=confidence,

ai_reasoning=reasoning,

is_passed=is_passed

    )

self.db.add(intent_analysis)

self.db.commit()

    return {

    'channel':
channel,

'analyzed': len(candidates),

    'passed':
passed_count,

'filtered_out': len(candidates) - passed_count

    }

    def
_batch_analyze_intent(

    self,

    keywords:
List[Dict[str, Any]],

    batch_size:
int = 20

    ) ->
List[Dict[str, Any]]:

"""

    Kelimeleri
batch halinde AI'a gönderir.

    Maliyet
optimizasyonu için toplu işlem yapar.

"""

    all_results =
[]

    for i in
range(0, len(keywords), batch_size):

    batch =
keywords[i:i + batch_size]

    prompt =
self._build_intent_prompt(batch)

    response =
self.ai_service.complete(

prompt=prompt,

max_tokens=2000,

temperature=0.3

    )

    # JSON
parse

    try:

batch_results = json.loads(response)

all_results.extend(batch_results)

    except
json.JSONDecodeError:

    #
Fallback: her kelime için varsayılan değer

    for kw
in batch:

all_results.append({

'keyword_id': kw['id'],

'intent_type': 'informational',

'confidence': 0.5,

'reasoning': 'Parse error, varsayılan değer atandı'

    })

    return
all_results

    def
_build_intent_prompt(self, keywords: List[Dict[str, Any]]) -> str:

"""Niyet analizi için prompt oluşturur."""

    keyword_list =
"\n".join([f"- {kw['id']}: {kw['keyword']}" for kw in
keywords])

    prompt =
f"""Aşağıdaki anahtar kelimelerin kullanıcı niyetini analiz et.

Her kelime için şu niyet tiplerinden birini belirle:

- transactional: Satın alma niyeti (ör: "laptop satın
  al", "en ucuz telefon")
- informational: Bilgi arayışı (ör: "python
  nedir", "grip belirtileri")
- navigational: Marka/site yönelimi (ör: "facebook
  giriş", "amazon")
- commercial: Araştırma + potansiyel satın alma (ör:
  "en iyi laptop 2024", "iphone vs samsung")
- trend_worthy: Viral/trend potansiyeli (ör: "yeni
  tiktok trendi", "viral challenge")

Anahtar Kelimeler:

{keyword_list}

SADECE aşağıdaki JSON formatında yanıt ver, başka hiçbir şey
yazma:

[

{{"keyword_id": 1, "intent_type":
"transactional", "confidence": 0.9, "reasoning":
"..."}},

  ...

]

"""

    return prompt

**5.3 Stratejik Kelime Bulucu
(app/core/channel/strategic_finder.py)**

"""

ADS ve SEO havuzlarının kesişimini bularak stratejik
kelimeleri belirler.

"""

from typing import List, Set, Dict, Any

from sqlalchemy.orm import Session

from app.database.models import ChannelPool, ScoringRun

class StrategicFinder:

    def__init__(self,
db: Session):

    self.db = db

    def
find_strategic_keywords(self, scoring_run_id: int) -> List[int]:

"""

    ADS ve SEO
final havuzlarının kesişimini bulur.

    Bu kelimeler
"Stratejik Anahtar Kelime" olarak işaretlenir.

    Returns:

    Stratejik
kelime ID'leri listesi

"""

    # ADS
havuzundaki kelime ID'leri

    ads_keywords =
(

self.db.query(ChannelPool.keyword_id)

    .filter(ChannelPool.scoring_run_id
== scoring_run_id)

.filter(ChannelPool.channel == "ADS")

    .all()

    )

    ads_set:
Set[int] = {kw[0] for kw in ads_keywords}

    # SEO
havuzundaki kelime ID'leri

    seo_keywords =
(

self.db.query(ChannelPool.keyword_id)

.filter(ChannelPool.scoring_run_id == scoring_run_id)

.filter(ChannelPool.channel == "SEO")

    .all()

    )

    seo_set:
Set[int] = {kw[0] for kw in seo_keywords}

    # Kesişim =
Stratejik kelimeler

    strategic_ids
= ads_set.intersection(seo_set)

    # Stratejik
olarak işaretle

    if
strategic_ids:

    (

self.db.query(ChannelPool)

.filter(ChannelPool.scoring_run_id == scoring_run_id)

.filter(ChannelPool.keyword_id.in_(strategic_ids))

.update({ChannelPool.is_strategic: True}, synchronize_session=False)

    )

    self.db.commit()

    return
list(strategic_ids)

    def
get_strategic_summary(self, scoring_run_id: int) -> Dict[str, Any]:

"""Stratejik kelimeler hakkında özet bilgi
döner."""

strategic_count = (

self.db.query(ChannelPool)

.filter(ChannelPool.scoring_run_id == scoring_run_id)

.filter(ChannelPool.is_strategic == True)

.distinct(ChannelPool.keyword_id)

    .count()

    )

    return {

'scoring_run_id': scoring_run_id,

'strategic_keyword_count': strategic_count,

    'note':
'Bu kelimeler hem ADS hem SEO kanalında yüksek potansiyele sahip.'

    }

**5.4 Kanal Motoru (app/core/channel/channel_engine.py)**

"""

Kanal atama sürecini orkestra eden ana modül.

"""

from typing import Dict, Any

from sqlalchemy.orm import Session

from datetime import datetime

from app.database.models import (

    ScoringRun,
ChannelCandidate, IntentAnalysis, ChannelPool

)

from app.core.channel.pool_builder import PoolBuilder

from app.core.channel.intent_analyzer import IntentAnalyzer

from app.core.channel.strategic_finder import
StrategicFinder

from app.generators.ai_service import AIService

class ChannelEngine:

    def__init__(self,
db: Session, ai_service: AIService):

    self.db = db

self.ai_service = ai_service

self.pool_builder = PoolBuilder(db)

self.intent_analyzer = IntentAnalyzer(db, ai_service)

self.strategic_finder = StrategicFinder(db)

    def
run_channel_assignment(self, scoring_run_id: int) -> Dict[str, Any]:

"""

    Tam kanal
atama sürecini çalıştırır.

    Adımlar:

    1. Aday
havuzları oluştur (2x kapasite)

    2. Her kanal
için niyet analizi yap

    3. Filtreyi
geçenleri final havuza al

    4. Stratejik
kelimeleri bul

    Returns:

    Süreç
özeti

"""

    scoring_run =
self.db.query(ScoringRun).filter(ScoringRun.id == scoring_run_id).first()

    if not
scoring_run:

    raise
ValueError(f"Scoring run {scoring_run_id} bulunamadı")

    # Durumu
güncelle

scoring_run.status = "intent_analysis"

self.db.commit()

    results = {

'scoring_run_id': scoring_run_id,

    'steps':
{}

    }

    try:

    # Adım 1:
Aday havuzları oluştur

pool_counts = self.pool_builder.build_candidate_pools(scoring_run_id)

results['steps']['pool_building'] = pool_counts

    # Adım 2:
Her kanal için niyet analizi

intent_results = {}

    for
channel in ['ADS', 'SEO', 'SOCIAL']:

intent_results[channel] = self.intent_analyzer.analyze_candidates(

scoring_run_id, channel

    )

results['steps']['intent_analysis'] = intent_results

    # Adım 3:
Final havuzları oluştur

final_counts = self._build_final_pools(scoring_run_id, scoring_run)

results['steps']['final_pools'] = final_counts

    # Adım 4:
Stratejik kelimeleri bul

    strategic_ids
= self.strategic_finder.find_strategic_keywords(scoring_run_id)

results['steps']['strategic'] = {

'count': len(strategic_ids),

'keyword_ids': strategic_ids

    }

    # Durumu
güncelle

scoring_run.status = "completed"

scoring_run.completed_at = datetime.utcnow()

self.db.commit()

results['status'] = 'completed'

    except
Exception as e:

scoring_run.status = "failed"

self.db.commit()

results['status'] = 'failed'

results['error'] = str(e)

    raise e

    return results

    def
_build_final_pools(

    self,

scoring_run_id: int,

    scoring_run:
ScoringRun

    ) -> Dict[str,
int]:

"""

    Niyet
analizini geçen kelimelerden final havuzları oluşturur.

"""

    final_counts =
{}

    for channel,
capacity in [

    ('ADS',
scoring_run.ads_capacity),

    ('SEO',
scoring_run.seo_capacity),

    ('SOCIAL',
scoring_run.social_capacity)

    ]:

    # Niyet
analizini geçen adayları al

passed_candidates = (

self.db.query(IntentAnalysis, ChannelCandidate)

    .join(

ChannelCandidate,

(IntentAnalysis.keyword_id == ChannelCandidate.keyword_id) &

(IntentAnalysis.scoring_run_id == ChannelCandidate.scoring_run_id) &

(IntentAnalysis.channel == ChannelCandidate.channel)

    )

.filter(IntentAnalysis.scoring_run_id == scoring_run_id)

.filter(IntentAnalysis.channel == channel)

.filter(IntentAnalysis.is_passed == True)

.order_by(ChannelCandidate.rank_in_channel)

.limit(capacity)

    .all()

    )

    # Final
havuza ekle

    for rank, (intent, candidate) in
enumerate(passed_candidates, 1):

pool_entry = ChannelPool(

scoring_run_id=scoring_run_id,

keyword_id=candidate.keyword_id,

channel=channel,

final_rank=rank,

is_strategic=False  # Sonra
güncellenecek

    )

self.db.add(pool_entry)

final_counts[channel] = len(passed_candidates)

self.db.commit()

    return
final_counts

---

**BÖLÜM 6: AI SERVİSİ**

**6.1 AI Service Wrapper (app/generators/ai_service.py)**

"""

OpenAI ve Anthropic API'leri için birleşik wrapper.

"""

import os

from typing import Optional

from abc import ABC, abstractmethod

import openai

import anthropic

class AIService(ABC):

    @abstractmethod

    def complete(

    self,

    prompt: str,

    max_tokens:
int = 1000,

    temperature:
float = 0.7

    ) -> str:

    pass

class OpenAIService(AIService):

    def__init__(self,
api_key: Optional[str] = None):

    self.client =
openai.OpenAI(

api_key=api_key or os.getenv("OPENAI_API_KEY")

    )

    self.model =
"gpt-4-turbo-preview"

    def complete(

    self,

    prompt: str,

    max_tokens:
int = 1000,

    temperature:
float = 0.7

    ) -> str:

    response =
self.client.chat.completions.create(

model=self.model,

messages=[{"role": "user", "content":
prompt}],

max_tokens=max_tokens,

temperature=temperature

    )

    return
response.choices[0].message.content

class AnthropicService(AIService):

    def__init__(self,
api_key: Optional[str] = None):

    self.client =
anthropic.Anthropic(

api_key=api_key or os.getenv("ANTHROPIC_API_KEY")

    )

    self.model =
"claude-3-sonnet-20240229"

    def complete(

    self,

    prompt: str,

    max_tokens:
int = 1000,

    temperature:
float = 0.7

    ) -> str:

    response =
self.client.messages.create(

model=self.model,

max_tokens=max_tokens,

temperature=temperature,

messages=[{"role": "user", "content":
prompt}]

    )

    return
response.content[0].text

def get_ai_service() -> AIService:

"""Konfigürasyona göre doğru AI servisini
döner."""

    provider =
os.getenv("AI_PROVIDER", "openai").lower()

    if provider ==
"anthropic":

    return
AnthropicService()

    else:

    return
OpenAIService()

---

**BÖLÜM 7: API ENDPOINTS**

**7.1 Ana Router (app/api/v1/router.py)**

from fastapi import APIRouter

from app.api.v1 import keywords, scoring, channels,
generation, export

api_router = APIRouter()

api_router.include_router(keywords.router,
prefix="/keywords", tags=["Keywords"])

api_router.include_router(scoring.router,
prefix="/scoring", tags=["Scoring"])

api_router.include_router(channels.router,
prefix="/channels", tags=["Channels"])

api_router.include_router(generation.router,
prefix="/generate", tags=["Generation"])

api_router.include_router(export.router,
prefix="/export", tags=["Export"])

**7.2 Scoring Endpoints (app/api/v1/scoring.py)**

from fastapi import APIRouter, Depends, HTTPException,
BackgroundTasks

from sqlalchemy.orm import Session

from typing import List

from app.database.connection import get_db

from app.schemas.scoring import (

    ScoringRunCreate,

ScoringRunResponse,

    ScoringRunStatus

)

from app.core.scoring.score_engine import ScoreEngine

from app.core.channel.channel_engine import ChannelEngine

from app.generators.ai_service import get_ai_service

router = APIRouter()

@router.post("/run",
response_model=ScoringRunResponse)

def create_scoring_run(

    data:
ScoringRunCreate,

    background_tasks:
BackgroundTasks,

    db: Session =
Depends(get_db)

):

    """

    Yeni bir skorlama
çalıştırması başlatır.

    Bu endpoint:

    1. Scoring run
kaydı oluşturur

    2. Arka planda
skorlama ve kanal atama işlemlerini başlatır

    """

    engine =
ScoreEngine(db)

    scoring_run =
engine.create_scoring_run(

ads_capacity=data.ads_capacity,

seo_capacity=data.seo_capacity,

social_capacity=data.social_capacity,

run_name=data.run_name

    )

    # Arka planda
çalıştır

background_tasks.add_task(

run_full_pipeline,

scoring_run.id,

    db

    )

    return scoring_run

@router.get("/run/{run_id}",
response_model=ScoringRunStatus)

def get_scoring_run_status(run_id: int, db: Session =
Depends(get_db)):

"""Skorlama çalıştırmasının durumunu
döner."""

    from
app.database.models import ScoringRun

    run =
db.query(ScoringRun).filter(ScoringRun.id == run_id).first()

    if not run:

    raise
HTTPException(status_code=404, detail="Scoring run bulunamadı")

    return run

@router.get("/run/{run_id}/results")

def get_scoring_results(run_id: int, db: Session =
Depends(get_db)):

"""Skorlama sonuçlarını döner."""

    from
app.database.models import ScoringRun, ChannelPool, Keyword

    run =
db.query(ScoringRun).filter(ScoringRun.id == run_id).first()

    if not run:

    raise
HTTPException(status_code=404, detail="Scoring run bulunamadı")

    if run.status !=
"completed":

    return
{"status": run.status, "message": "İşlem henüz
tamamlanmadı"}

    # Final havuzları
getir

    results = {

'scoring_run_id': run_id,

    'status':
run.status,

    'channels': {}

    }

    for channel in
['ADS', 'SEO', 'SOCIAL']:

    pool_items = (

db.query(ChannelPool, Keyword)

.join(Keyword, ChannelPool.keyword_id == Keyword.id)

.filter(ChannelPool.scoring_run_id == run_id)

.filter(ChannelPool.channel == channel)

.order_by(ChannelPool.final_rank)

    .all()

    )

results['channels'][channel] = [

    {

'rank': pool.final_rank,

'keyword_id': pool.keyword_id,

'keyword': keyword.keyword,

'is_strategic': pool.is_strategic

    }

    for pool,
keyword in pool_items

    ]

    return results

def run_full_pipeline(scoring_run_id: int, db: Session):

"""Tam pipeline'ı çalıştırır (arka plan
görevi)."""

    try:

    # Skorlama

    score_engine =
ScoreEngine(db)

score_engine.run_scoring(scoring_run_id)

    # Kanal atama

    ai_service =
get_ai_service()

    channel_engine
= ChannelEngine(db, ai_service)

channel_engine.run_channel_assignment(scoring_run_id)

    except Exception
as e:

    from
app.database.models import ScoringRun

    run =
db.query(ScoringRun).filter(ScoringRun.id == scoring_run_id).first()

    if run:

    run.status
= "failed"

db.commit()

    raise e

**7.3 Tüm Endpoint Listesi**

| **Method** | **Endpoint**                  | **Açıklama**       |
| ---------------- | ----------------------------------- | -------------------------- |
| POST             | /api/v1/keywords/import             | CSV'den kelime import et   |
| GET              | /api/v1/keywords                    | Tüm kelimeleri listele    |
| GET              | /api/v1/keywords/{id}               | Tek kelime detayı         |
| POST             | /api/v1/keywords                    | Yeni kelime ekle           |
| PUT              | /api/v1/keywords/{id}               | Kelime güncelle           |
| DELETE           | /api/v1/keywords/{id}               | Kelime sil                 |
| POST             | /api/v1/scoring/run                 | Skorlama başlat           |
| GET              | /api/v1/scoring/run/{id}            | Skorlama durumu            |
| GET              | /api/v1/scoring/run/{id}/results    | Skorlama sonuçları       |
| GET              | /api/v1/channels/{run_id}/pools     | Kanal havuzları           |
| GET              | /api/v1/channels/{run_id}/strategic | Stratejik kelimeler        |
| POST             | /api/v1/generate/seo-geo            | SEO+GEO içerik üret      |
| POST             | /api/v1/generate/ads                | Google Ads üret           |
| POST             | /api/v1/generate/social             | Sosyal medya içerik üret |
| GET              | /api/v1/export/{run_id}/docx        | DOCX export                |
| GET              | /api/v1/export/{run_id}/pdf         | PDF export                 |
| GET              | /api/v1/export/{run_id}/excel       | Excel export               |

---

**BÖLÜM 8: UYGULAMA ADIMLARI (SIRALAMA)**

**ADIM 1: Proje İskeleti (30 dk)**

1. Tüm
   dizin yapısını oluştur
2. Boş
   __init__.py dosyalarını ekle
3. .gitignore,
   .env.example, requirements.txt oluştur

**ADIM 2: Docker Kurulumu (30 dk)**

1. Dockerfile
   oluştur
2. docker-compose.yml
   oluştur
3. docker-compose
   up --build ile test et
4. PostgreSQL'e
   bağlanabildiğini doğrula

**ADIM 3: Veritabanı Katmanı (1 saat)**

1. app/config.py
   - Ayarları yaz
2. app/database/connection.py
   - DB bağlantısı
3. app/database/models.py
   - Tüm modeller
4. Alembic
   kurulumu ve ilk migration
5. docker-compose
   exec app alembic upgrade head

**ADIM 4: Pydantic Şemaları (30 dk)**

1. app/schemas/
   altındaki tüm şemaları yaz
2. Request
   ve Response modelleri

**ADIM 5: Skorlama Modülleri (1.5 saat)**

1. app/core/constants.py
2. app/core/scoring/normalizer.py
3. app/core/scoring/ads_scorer.py
4. app/core/scoring/seo_scorer.py
5. app/core/scoring/social_scorer.py
6. app/core/scoring/score_engine.py
7. Unit
   testler

**ADIM 6: Kanal Atama Modülleri (1.5 saat)**

1. app/core/channel/pool_builder.py
2. app/generators/ai_service.py
3. app/core/channel/intent_analyzer.py
4. app/core/channel/strategic_finder.py
5. app/core/channel/channel_engine.py

**ADIM 7: API Katmanı (1 saat)**

1. app/main.py
   - FastAPI app
2. app/api/v1/router.py
3. app/api/v1/keywords.py
4. app/api/v1/scoring.py
5. app/api/v1/channels.py

**ADIM 8: İçerik Üretim Motorları (2 saat)**

1. Prompt
   templates
2. SEO+GEO
   generator
3. Ads
   generator
4. Social
   generator
5. Compliance
   checkers

**ADIM 9: Export Modülleri (1 saat)**

1. DOCX
   exporter
2. PDF
   exporter
3. Excel
   exporter
4. Export
   API endpoints

**ADIM 10: Celery Entegrasyonu (45 dk)**

1. app/tasks/celery_app.py
2. Scoring
   tasks
3. Generation
   tasks
4. docker-compose
   worker test

**ADIM 11: Test ve Debug (1 saat)**

1. Seed
   data scripti
2. Tüm
   endpoint'leri test et
3. Hataları
   düzelt

**ADIM 12: Dokümantasyon (30 dk)**

1. README.md
2. API
   dokümantasyonu (Swagger otomatik)

---

**BÖLÜM 9: ÖNEMLİ NOTLAR**

**9.1 Dikkat Edilmesi Gerekenler**

1. **Sıralama
   önemli** : Her adımı sırasıyla tamamla, atlama
2. **Docker
   her zaman çalışır durumda olsun** : Değişiklikleri container içinde test
   et
3. **Migration'ları
   unutma** : Model değişikliklerinde alembic revision --autogenerate
4. **Env
   değişkenleri** : .env dosyasını düzgün yapılandır
5. **AI
   API anahtarı** : Test için düşük limitli plan kullan

**9.2 Hata Durumunda**

1. docker-compose
   logs -f app ile hataları incele
2. Veritabanı
   bağlantı hatası: PostgreSQL container'ın ayakta olduğunu kontrol et
3. Import
   hatası: __init__.py dosyalarını kontrol et
4. Migration
   hatası: alembic downgrade -1 ile geri al

**9.3 Test Verisi Formatı**

keyword,monthly_volume,trend_12m,trend_3m,competition_score,sector

laptop fiyatları,45000,15.5,22.3,0.75,teknoloji

en iyi telefon 2024,32000,45.2,67.8,0.82,teknoloji

python öğren,28000,12.0,18.5,0.35,eğitim

---

**SON KONTROL LİSTESİ**

* [
  ] Docker container'lar çalışıyor
* [
  ] PostgreSQL'e bağlanılabiliyor
* [
  ] Migration'lar uygulandı
* [
  ] API ayakta (http://localhost:8000/docs)
* [
  ] Keyword import çalışıyor
* [
  ] Skorlama çalışıyor
* [
  ] Kanal atama çalışıyor
* [
  ] Stratejik kelimeler belirleniyor
* [
  ] Export çalışıyor
* [
  ] Celery worker çalışıyor

---

**Bu dokümanı Claude Code'a ver ve "Bu roadmap'i adım
adım takip et" de.**
