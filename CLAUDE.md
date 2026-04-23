# CLAUDE.md

## 1. Proje Ozeti

Digitus Engine V2, AI destekli anahtar kelime analizi ve kanal atama motorudur. CSV ile yuklenen anahtar kelimeleri ADS/SEO/SOCIAL kanallarina skorlayip atar, Gemini AI ile niyet analizi yapar, her kanal icin icerik uretir (blog, Google Ads RSA, sosyal medya) ve sonuclari DOCX/PDF/Excel/CSV olarak disari aktarir.

- **Hedef kullanici**: Dijital pazarlama ajanslari ve SEO/SEM uzmanlari
- **Gelistirme asamasi**: Aktif gelistirme (V2, temel ozellikler calisiyor, icerik uretim ve compliance katmanlari eklenmis)

---

## 2. Tech Stack

### Backend
| Teknoloji | Versiyon | Amac |
|-----------|----------|------|
| Python | 3.11 | Ana dil |
| FastAPI | 0.109.0 | Web framework |
| Uvicorn | 0.27.0 | ASGI server |
| SQLAlchemy | 2.0.25 | ORM |
| Alembic | 1.13.1 | Migration |
| PostgreSQL | 15 (Alpine) | Veritabani |
| Redis | 7 (Alpine) | Cache / Celery broker |
| Celery | 5.3.6 | Asenkron gorev kuyrugu |
| Pydantic | 2.5.3 | Validation |
| Pydantic-Settings | 2.1.0 | Konfiguration |
| Loguru | 0.7.2 | Logging |
| google-generativeai | 0.8.0 | Gemini AI entegrasyonu |
| python-docx | 1.1.0 | DOCX export |
| reportlab | 4.0.8 | PDF export |
| openpyxl | 3.1.2 | Excel export |
| thefuzz[speedup] | 0.22.1 | Turkce fuzzy matching |
| httpx | 0.26.0 | HTTP client |

### Frontend
| Teknoloji | Versiyon | Amac |
|-----------|----------|------|
| React | 18.2.0 | UI framework |
| TypeScript | 5.3.3 | Tip guvenligi |
| Vite | 5.0.11 | Build araci |
| React Router | 6.21.0 | Sayfa yonlendirme |
| Zustand | 5.0.11 | State yonetimi |
| Axios | 1.6.5 | HTTP client |
| Lucide React | 0.303.0 | Ikon kutuphanesi |

### Build Araclari
- **Backend**: pip + requirements.txt
- **Frontend**: npm + Vite
- **Container**: Docker + docker-compose

---

## 3. Proje Yapisi

```
Digitus-Engine-main/
|-- app/                          # Backend ana modulu
|   |-- main.py                   # FastAPI entry point (app objesi burada)
|   |-- config.py                 # Pydantic Settings (env degiskenleri)
|   |-- dependencies.py           # FastAPI dependency injection (DB, AI)
|   |-- api/
|   |   |-- v1/
|   |       |-- router.py         # Tum router'lari birlestiren ana router
|   |       |-- keywords.py       # Keyword CRUD + CSV import
|   |       |-- scoring.py        # Skorlama calistirma ve sonuc
|   |       |-- channels.py       # Kanal atama ve havuzlar
|   |       |-- generation.py     # Icerik uretimi (SEO/ADS/Social)
|   |       |-- export.py         # Rapor disari aktarim
|   |       |-- tasks.py          # Celery gorev durumu sorgulama
|   |-- core/                     # Is mantigi katmani
|   |   |-- constants.py          # Tum sabit degerler (100+ sabit)
|   |   |-- scoring/
|   |   |   |-- score_engine.py   # Ana skorlama orkestratoru
|   |   |   |-- ads_scorer.py     # ADS skor formulu
|   |   |   |-- seo_scorer.py     # SEO skor formulu
|   |   |   |-- social_scorer.py  # SOCIAL skor formulu
|   |   |   |-- normalizer.py     # Normalizasyon yardimcilari
|   |   |-- channel/
|   |       |-- channel_engine.py # Kanal atama pipeline'i (~462 LOC)
|   |       |-- intent_analyzer.py# Gemini ile niyet analizi (~425 LOC)
|   |       |-- pool_builder.py   # Aday havuz olusturucu
|   |       |-- prefilter.py      # AI on-filtre katmani
|   |-- database/
|   |   |-- connection.py         # Engine, SessionLocal, init_db
|   |   |-- models.py             # 21 SQLAlchemy modeli (~579 LOC)
|   |   |-- crud.py               # CRUD islemleri
|   |-- generators/               # Icerik uretim motorlari
|   |   |-- ai_service.py         # AI servis wrapper (Gemini/Mock)
|   |   |-- seo_geo/
|   |   |   |-- seo_geo_generator.py  # SEO+GEO blog uretimi
|   |   |   |-- prompt_templates.py   # 3 prompt: SEO_GEO_GENERATION, GEO_COMPLIANCE_CHECK, SEO_COMPLIANCE_CHECK
|   |   |-- ads/
|   |   |   |-- ads_generator.py      # Google Ads RSA uretimi
|   |   |   |-- rsa_generator.py      # RSA headline/description uretici
|   |   |   |-- keyword_grouper.py    # AI ile keyword gruplama
|   |   |   |-- validators.py         # Karakter limiti dogrulama
|   |   |   |-- prompt_templates.py   # 4 prompt: ADS_GROUPING, ADS_RSA_GENERATION, HEADLINE_REGENERATION, DESCRIPTION_SHORTENING
|   |   |-- social/
|   |       |-- social_generator.py   # 3-fazli sosyal icerik orkestratoru
|   |       |-- category_generator.py # Faz 1: Kategori uretici
|   |       |-- idea_generator.py     # Faz 2: Fikir uretici
|   |       |-- content_generator.py  # Faz 3: Icerik uretici
|   |       |-- prompt_templates.py   # 5 prompt: SOCIAL_CATEGORY, SOCIAL_IDEA, SOCIAL_CONTENT, IDEA_REGENERATE, CONTENT_REGENERATE
|   |-- compliance/               # Uyumluluk kontrolleri
|   |   |-- seo_compliance.py     # 11 programatik SEO kriteri
|   |   |-- geo_compliance.py     # 7 AI destekli GEO kriteri
|   |-- exporters/                # Disari aktarim modulleri
|   |-- tasks/                    # Celery gorevleri
|   |   |-- celery_app.py         # Celery konfigurasyonu
|   |   |-- scoring_tasks.py      # Skorlama gorevleri
|   |   |-- intent_tasks.py       # Niyet analizi gorevleri
|   |   |-- generation_tasks.py   # Icerik uretim gorevleri
|   |-- schemas/                  # Pydantic sema dosyalari
|       |-- keyword.py
|       |-- scoring.py
|       |-- channel.py
|       |-- content.py
|-- frontend/                     # React frontend
|   |-- src/
|   |   |-- App.tsx               # React Router tanimlamalari
|   |   |-- main.tsx              # React entry point
|   |   |-- pages/                # Sayfa bilesenler (Dashboard, Keywords, Scoring, Channels, Export, vb.)
|   |   |-- components/           # Ortak bilesenlr (Layout, vb.)
|   |   |-- services/             # Axios API istemcileri
|   |   |-- stores/               # Zustand state yonetimi
|   |   |   |-- socialStore.ts    # 3-asamali sosyal icerik akisi (step, form data, categories, ideas, contents, task tracking; persist middleware ile localStorage'a kaydedilir)
|   |   |-- hooks/                # Ozel hook'lar (useTaskPolling vb.)
|   |   |-- styles/               # Global CSS
|   |-- package.json
|   |-- vite.config.ts
|   |-- tsconfig.node.json
|-- migrations/                   # Alembic migration dosyalari
|   |-- env.py
|   |-- versions/                 # 6 migration dosyasi
|-- scripts/
|   |-- init_db.py                # Veritabani ilklendirme
|   |-- seed_data.py              # Ornek veri yukleme
|-- tests/                        # Pytest test dosyalari
|   |-- conftest.py
|   |-- test_api.py
|   |-- test_channel.py
|   |-- test_database.py
|   |-- test_schemas.py
|   |-- test_structure.py
|   |-- unit/
|   |   |-- test_intent_analyzer.py
|   |   |-- test_services.py
|   |-- integration/
|   |   |-- test_api.py
|   |-- async/
|       |-- test_celery.py
|-- docker-compose.yml            # 5 servis: app, db, redis, celery_worker, celery_beat
|-- Dockerfile                    # Python 3.11-slim tabanli
|-- requirements.txt              # Python bagimliliklar
|-- alembic.ini                   # Alembic konfigurasyonu
|-- .env.example                  # Ornek ortam degiskenleri
```

### Entry Point'ler
- **Backend**: `app/main.py` -> `app` nesnesi (FastAPI)
- **Frontend**: `frontend/src/main.tsx` -> React root
- **Celery Worker**: `app/tasks/celery_app.py` -> `celery_app` nesnesi
- **Uvicorn baslat**: `uvicorn app.main:app --reload`

---

## 4. Mimari & Onemli Kararlar

### Mimari Pattern
**Katmanli Mimari (Layered Architecture)** + **Pipeline Pattern**:
- `api/` -> HTTP katmani (router, validation)
- `core/` -> Is mantigi katmani (scoring, channel, constants)
- `database/` -> Veri erisim katmani (models, crud, connection)
- `generators/` -> Icerik uretim katmani (ai_service, generators)
- `tasks/` -> Asenkron gorev katmani (Celery)

### Onemli Tasarim Kararlari

1. **3 kanalli skorlama sistemi**: Her kanal farkli formul kullanir:
   - ADS (ROI Hunter): `(sqrt(Volume+1) * Combined_Trend) / sqrt(Competition+1)`
   - SEO (Opportunity Engine): `log(Volume) * (Trend3*2 + Trend12) * (1-Competition)`
   - SOCIAL (Hype Tracker): `log(Volume) * (Trend3*3 + Trend12)` (rekabet yok)

2. **Kanal atama pipeline'i** (5 asamali):
   - Aday havuz (2x kapasite) -> Niyet analizi (AI) -> On-filtre (AI) -> Son secim -> Backfill (%30)

3. **Cross-channel transfer**: ADS'den reddedilen kelimeler SEO'ya aktarilabilir

4. **Fallback mekanizmasi**: AI basarisiz olursa kanal bazli varsayilan niyet tipi atanir

5. **Batch isleme**: AI cagrilari 6'li batch'ler halinde yapilir (JSON truncation onleme)

6. **Turkce fuzzy dedup**: Ek kirpma + karakter normalizasyon + %85 benzerlik esigi

### Teknik Borclar ve Dikkat Edilecekler

- **CORS `allow_origins=["*"]`**: Production'da sinirlandirilmali (`app/main.py:54`)
- **Export status in-memory dict**: Production'da Redis/DB'ye tasinmali
- **`alembic.ini` icindeki `sqlalchemy.url`**: Statik deger var, `migrations/env.py` override ediyor (sorun degil ama kafa karistirici)
- **Secret key default degeri**: Production'da model_validator ile kontrol ediliyor ama development'ta default kalabiliyor

---

## 5. Gelistirme Ortami

### Kurulum Adimlari

```bash
# 1. Repo'yu klonla
git clone <repo-url>
cd Digitus-Engine-main

# 2. .env dosyasini olustur
cp .env.example .env
# GEMINI_API_KEY degerini ekle

# 3a. Docker ile calistirma (onerilen)
docker-compose up -d

# 3b. Lokal gelistirme
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 4. Veritabani migration
alembic upgrade head

# 5. Backend baslat
uvicorn app.main:app --reload --port 8000

# 6. Frontend baslat
cd frontend
npm install
npm run dev
```

### Gerekli Environment Variable'lar
```
POSTGRES_USER=digitus
POSTGRES_PASSWORD=<sifre>
POSTGRES_DB=digitus_engine
POSTGRES_HOST=db                 # lokal: localhost
POSTGRES_PORT=5432
DATABASE_URL=postgresql://digitus:<sifre>@<host>:5432/digitus_engine
REDIS_URL=redis://redis:6379/0   # lokal: redis://localhost:6379/0
GEMINI_API_KEY=<gemini-api-key>
APP_ENV=development
DEBUG=true
SECRET_KEY=<secret-key>
ADS_EPSILON=0.01
SEO_COMPETITION_WEIGHT=1.0
SOCIAL_TREND_WEIGHT=3.0
```

### Sik Kullanilan Komutlar
```bash
# Gelistirme sunucusu
uvicorn app.main:app --reload

# Frontend
cd frontend && npm run dev

# Celery worker
celery -A app.tasks.celery_app worker --loglevel=info

# Celery beat (zamanlanmis gorevler)
celery -A app.tasks.celery_app beat --loglevel=info

# Test calistirma
pytest tests/ -v

# Docker ile hepsini baslat
docker-compose up -d

# Migration olusturma
alembic revision --autogenerate -m "aciklama"
alembic upgrade head
```

---

## 6. Veritabani & Veri Modeli

### Ana Entity'ler (21 tablo)

```
Keyword (anahtar kelimeler)
  |-- 1:N --> KeywordScore (ADS/SEO/SOCIAL skorlari)
  |-- 1:N --> ChannelCandidate (kanal aday havuzu)
  |-- 1:N --> IntentAnalysis (AI niyet analizi)
  |-- 1:N --> PreFilterResult (AI on-filtre)
  |-- 1:N --> ChannelPool (final kanal atamasi)
  |-- 1:N --> ContentOutput (uretilen icerik meta)

ScoringRun (skorlama calistirmasi)
  |-- 1:N --> KeywordScore
  |-- 1:N --> ChannelCandidate
  |-- 1:N --> IntentAnalysis
  |-- 1:N --> PreFilterResult
  |-- 1:N --> ChannelPool

Icerik Modelleri:
  - SEOGeoContent (blog icerigi: title, intro, body, subheadings, links)
  - AdGroup --> AdHeadline, AdDescription, NegativeKeyword
  - SocialCategory --> SocialIdea --> SocialContent

TaskResult (Celery gorev takibi)
```

### Migration Stratejisi
- **Alembic** kullaniliyor, `migrations/versions/` altinda 6 migration dosyasi mevcut
- `migrations/env.py` icerisinde `settings.database_url` ile override yapiliyor
- Yeni migration: `alembic revision --autogenerate -m "aciklama"`
- Uygulama: `alembic upgrade head`

### Seed Data
- `scripts/seed_data.py` ile ornek veri yuklenebilir
- `scripts/init_db.py` ile tablolar olusturulabilir
- DEBUG modda uygulama baslarken `init_db()` otomatik calisir

---

## 7. API & Arayuzler

### REST API Endpoint'leri (prefix: `/api/v1`)

#### Keywords (`/api/v1/keywords`)
| Method | Path | Aciklama |
|--------|------|----------|
| GET | `/keywords` | Tum anahtar kelimeleri listele |
| POST | `/keywords` | Tekil anahtar kelime ekle |
| POST | `/keywords/upload` | CSV dosyasindan toplu import |
| PUT | `/keywords/{id}` | Anahtar kelime guncelle |
| DELETE | `/keywords/{id}` | Anahtar kelime sil |
| POST | `/keywords/cleanup-duplicates` | Turkce fuzzy dedup calistir |

#### Scoring (`/api/v1/scoring`)
| Method | Path | Aciklama |
|--------|------|----------|
| POST | `/scoring/runs` | Yeni skorlama calistirmasi olustur |
| POST | `/scoring/runs/{id}/execute` | Skorlamayi calistir |
| GET | `/scoring/runs` | Tum calistirmalari listele |
| GET | `/scoring/runs/{id}/scores` | Skorlama sonuclarini getir |
| GET | `/scoring/runs/{id}/export` | XLSX olarak disari aktar |

#### Channels (`/api/v1/channels`)
| Method | Path | Aciklama |
|--------|------|----------|
| POST | `/channels/runs/{id}/assign` | Kanal atamasini baslat (async) |
| GET | `/channels/runs/{id}/pools` | Tum kanal havuzlarini getir |
| GET | `/channels/runs/{id}/pools/{channel}` | Belirli kanal havuzunu getir |

#### Generation (`/api/v1/generation`)
| Method | Path | Aciklama |
|--------|------|----------|
| POST | `/generation/seo-geo` | SEO+GEO icerik uret |
| POST | `/generation/ads` | Google Ads RSA uret |
| POST | `/generation/social` | Sosyal medya icerigi uret (3 fazli) |
| GET | `/generation/compliance/{id}` | Uyumluluk sonuclarini getir |

#### Export (`/api/v1/export`)
| Method | Path | Aciklama |
|--------|------|----------|
| POST | `/export` | Rapor olustur (DOCX/PDF/XLSX/CSV) |
| GET | `/export/{id}/status` | Export durumunu sorgula |
| GET | `/export/{id}/download` | Dosyayi indir |

#### Tasks (`/api/v1/tasks`)
| Method | Path | Aciklama |
|--------|------|----------|
| GET | `/tasks/{task_id}` | Gorev durumunu sorgula |
| GET | `/tasks/run/{run_id}` | Run'a ait gorevleri listele |
| POST | `/tasks/{task_id}/cancel` | Gorevi iptal et |

### Health Check
| Method | Path | Aciklama |
|--------|------|----------|
| GET | `/` | Basit health check |
| GET | `/health` | Detayli health check |

### Auth Mekanizmasi
- **Mevcut durumda auth yok**: Endpoint'ler acik erisimli
- Production icin JWT veya API key tabanli auth eklenmeli

### Middleware'ler
- **CORSMiddleware**: Tum origin'lere izin veriyor (production'da sinirlandirilmali)
- **Global Exception Handler**: Tum yakalanmamis hatalari loglar ve 500 doner

---

## 8. Test Stratejisi

### Test Turleri ve Araclar
- **Framework**: pytest 7.4.4
- **Async**: pytest-asyncio 0.23.3
- **Coverage**: pytest-cov 4.1.0

### Test Dosyalari
```
tests/
|-- conftest.py              # Fixture'lar (project_root, app_dir)
|-- test_api.py              # API endpoint testleri
|-- test_channel.py          # Kanal atama testleri
|-- test_database.py         # Veritabani testleri
|-- test_schemas.py          # Pydantic sema testleri
|-- test_structure.py        # Proje yapi testleri
|-- unit/
|   |-- test_intent_analyzer.py  # Niyet analizi unit testleri
|   |-- test_services.py         # Servis unit testleri
|-- integration/
|   |-- test_api.py              # API integration testleri
|-- async/
    |-- test_celery.py           # Celery gorev testleri
```

### Test Calistirma
```bash
# Tum testler
pytest tests/ -v

# Coverage ile
pytest tests/ --cov=app --cov-report=html

# Sadece unit testler
pytest tests/unit/ -v

# Sadece integration testler
pytest tests/integration/ -v
```

### Coverage Hedefi
- Tespit edilemedi (belirli bir hedef tanimlanmamis)

---

## 9. Deployment & CI/CD

### Ortamlar
- **Development**: Docker Compose ile lokal gelistirme
- **Staging/Production**: Tespit edilemedi (henuz CI/CD pipeline'i kurulmamis)

### Docker Compose Servisleri
| Servis | Container | Port |
|--------|-----------|------|
| app | digitus_app | 8000 |
| db | digitus_db (postgres:15-alpine) | 5432 |
| redis | digitus_redis (redis:7-alpine) | 6379 |
| celery_worker | digitus_celery_worker | - |
| celery_beat | digitus_celery_beat | - |

### Deploy Sureci
```bash
# Docker Compose ile
docker-compose up -d --build

# Sadece uygulamayi yeniden baslat
docker-compose restart app celery_worker
```

### CI/CD Pipeline
- **Mevcut durumda CI/CD yok**: `.github/` dizini bulunmuyor
- GitHub Actions ile test, lint ve deploy pipeline'i eklenmeli

---

## 10. Bilinen Sorunlar & Yapilacaklar

### Guvenlik
- CORS `allow_origins=["*"]` production'da sinirlandirilmali
- Auth mekanizmasi (JWT/API Key) eklenmeli
- `.env` dosyasi repo'da mevcut (gitignore'a eklenmis ama .env dosyasi hala duruyor)

### Altyapi
- CI/CD pipeline'i yok (GitHub Actions eklenmeli)
- Export status in-memory dict kullaniliyor, production'da Redis/DB'ye tasinmali
- Celery beat schedule'da aktif periyodik gorev tanimlanmamis (sadece ornek/yorum)

### Kod Kalitesi
- Linter/formatter konfigurasyonu yok (ruff, black, isort onerisi)
- `.vscode/settings.json` bos
- Type checking araci (mypy) kullanilmiyor
- Proje kodunda TODO/FIXME yorumu bulunmuyor (temiz)

### Yakin Vadede Yapilabilecek Refactor'lar
- `app/core/constants.py` icindeki 100+ sabitin gruplara ayrilmasi
- Export modulu icin asenkron kuyruk destegi (buyuk raporlar icin)
- Rate limiting middleware eklenmesi
- API versiyonlama stratejisi netlestirilmesi (su an sadece v1)

---

## 11. Claude Icin Notlar

### Naming Convention
- **Python dosyalari**: snake_case (modeller, fonksiyonlar, degiskenler)
- **Siniflar**: PascalCase (`ScoreEngine`, `PoolBuilder`, `ChannelCandidate`)
- **Sabitler**: UPPER_SNAKE_CASE (`ADS_POOL_SIZE`, `SEO_TREND_3M_WEIGHT`)
- **API endpoint'leri**: kebab-case URL'ler (`/cleanup-duplicates`, `/seo-geo`)
- **Frontend**: PascalCase bilesenleri, camelCase degiskenler

### Gemini Prompt Sablonlari Haritasi

Tum Gemini AI prompt'lari `prompt_templates.py` dosyalarinda tanimli. Ek olarak 2 yerde inline prompt var.

| Dosya | Prompt Sabiti | Amac |
|-------|---------------|------|
| `generators/seo_geo/prompt_templates.py` | `SEO_GEO_GENERATION_PROMPT` | Blog icerigi uretimi (keyword, sector, target_market, tone, word_count parametreleri) |
| `generators/seo_geo/prompt_templates.py` | `GEO_COMPLIANCE_CHECK_PROMPT` | 7 kriterli GEO uyumluluk degerlendirmesi (keyword, content parametreleri) |
| `generators/seo_geo/prompt_templates.py` | `SEO_COMPLIANCE_CHECK_PROMPT` | SEO sorunlari ve onerileri (opsiyonel, programatik kontrol oncelikli) |
| `generators/ads/prompt_templates.py` | `ADS_GROUPING_PROMPT` | Keyword'leri reklam gruplarina ayirma (keywords_json parametresi) |
| `generators/ads/prompt_templates.py` | `ADS_RSA_GENERATION_PROMPT` | RSA headline + description + negatif kelime uretimi (group_name, keywords, brand_name, brand_usp) |
| `generators/ads/prompt_templates.py` | `HEADLINE_REGENERATION_PROMPT` | 30 karakter asimi durumunda baslik kisaltma (original, keyword, char_count) |
| `generators/ads/prompt_templates.py` | `DESCRIPTION_SHORTENING_PROMPT` | 90 karakter asimi durumunda aciklama kisaltma (original, char_count) |
| `generators/social/prompt_templates.py` | `SOCIAL_CATEGORY_PROMPT` | Faz 1: Icerik kategorileri belirleme (brand_name, brand_context, keywords_json) |
| `generators/social/prompt_templates.py` | `SOCIAL_IDEA_PROMPT` | Faz 2: Kategori basina icerik fikirleri (category_name, category_type, keywords, brand_name) |
| `generators/social/prompt_templates.py` | `SOCIAL_CONTENT_PROMPT` | Faz 3: Tam icerik paketi (hook, caption, senaryo, gorsel, CTA, hashtag) |
| `generators/social/prompt_templates.py` | `IDEA_REGENERATE_PROMPT` | Begenilmeyen fikir icin yeniden uretim |
| `generators/social/prompt_templates.py` | `CONTENT_REGENERATE_PROMPT` | Begenilmeyen icerik icin yeniden uretim |

**Inline prompt'lar (template dosyasi disinda):**
| Dosya | Satir | Amac |
|-------|-------|------|
| `core/channel/intent_analyzer.py` | ~400 | Batch niyet analizi (transactional/informational/navigational/commercial/trend_worthy siniflandirmasi) |
| `api/v1/generation.py` | ~629 | Tekil Google Ads icerigi (fallback/basit uretim) |
| `api/v1/generation.py` | ~697 | Tekil sosyal medya paylasimlari (fallback/basit uretim) |

**Prompt degisikligi yaparken dikkat:**
- Tum prompt'lar JSON ciktisi bekler, format bozulursa downstream parsing patlar
- `{{` ve `}}` Python f-string escape'leri, prompt iceriginde literal `{` `}` icin kullanilir
- Prompt icindeki karakter limitleri (headline <=30, description <=90) is kurali, degistirilmemeli

### Dosya Yapisi Kurallari
- API endpoint'leri `app/api/v1/` altinda, her router ayri dosya
- Is mantigi `app/core/` altinda, her domain ayri alt dizin
- Pydantic semalari `app/schemas/` altinda
- Celery gorevleri `app/tasks/` altinda
- Tum sabitler `app/core/constants.py` icinde tanimli

### Dokunulmamasi Gereken Dosyalar
- `migrations/versions/` altindaki mevcut migration dosyalari (degistirilmemeli, yeni migration eklenebilir)
- `app/core/constants.py` icindeki formul katsayilari (is karari, dikkatle degistirilmeli)
- `.env` dosyasi (hassas bilgiler icerir)

### Kritik Moduller (Test Gerektiren)
- `app/core/scoring/` - Skorlama formullerinde yapilan her degisiklik sonuclari etkiler
- `app/core/channel/channel_engine.py` - Pipeline'daki her adim birbirine bagimli
- `app/core/channel/intent_analyzer.py` - AI JSON parsing robustness kritik
- `app/database/models.py` - Model degisiklikleri migration gerektirir

### Sik Yapilan Hatalar
- **Migration unutma**: `models.py` degisikligi sonrasi `alembic revision --autogenerate` unutuluyor
- **Sabit degisiklik etkisi**: `constants.py`'deki bir degisiklik birden fazla modulu etkiler
- **Celery task import**: Yeni task dosyasi `celery_app.py`'deki `include` listesine eklenmeli
- **JSON parse hatalari**: Gemini API'den gelen JSON bazen markdown fence iceriyor, `intent_analyzer.py`'deki temizleme mantigi onemli
- **Turkce karakter**: Fuzzy matching'de Turkce karakter normalizasyonu (i/I, g/G problemi) dikkat gerektirir
- **Batch boyutu**: AI cagrilarinda batch_size=6 asildginda JSON truncation riski artar

### Veri Akisi Ozeti
```
CSV Upload -> Dedup -> Scoring Run -> Skor Hesapla -> Aday Havuz ->
Niyet Analizi (AI) -> On-filtre (AI) -> Final Havuz + Backfill ->
Icerik Uretimi (AI) -> Compliance Check -> Export
```
