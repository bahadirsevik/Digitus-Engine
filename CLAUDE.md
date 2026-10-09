# CLAUDE.md

## 0. Bu Klasor (Digitus-Engine-lean)

Urun gelistirmesi BURADA yapilir. Dal: `deploy/lean-taslak` (yetim/orphan dal —
`main` / `algoritma-kilitleri` ile ortak gecmisi YOK, aralarinda merge yapilamaz).

- **Commit + push hedefi**: `origin/deploy/lean-taslak`. Sunucu bu dali kendi
  `feat/auth-login` dalina `git fetch` + `git merge` ile alir (`git pull` degil);
  sunucu islemini kullanici yapar.
- **Arastirma ayri klasorde**: `../Digitus-Engine-main` (`algoritma-kilitleri`) —
  benchmark, olcum betikleri, Social V4 deneyi, algoritma kilit kaynaklari orada.
  Akis tek yonlu: arastirma -> urun. Arastirma kodu/testi buraya TASINMAZ.
- **Bu dalda olmayanlar**: kok `scripts/`, `benchmark/` (yalniz motor parity
  golden'lari: `ads_nihai_niche_golden_v1.json`, `seo_v31_kati2_golden_v2.json`),
  `app/core/benchmark`'in cogu, Social V4 authority deneyi. Asagidaki bolumlerde
  bunlara gecen referanslar ana repoyu anlatir.
- **Docker imaji**: `.dockerignore` `tests/`, `benchmark/`, `.claude/`, `*.md` vb.
  disarida tutar — test/dokuman eklemek imaji etkilemez.
- **Dev ortami**: `docker-compose.yml` eski klasorun volume'lerini kullanir
  (`digitus-engine-main_postgres_data`) — veri ortak. Container adlari iki klasorde
  AYNI (`digitus_app`, `digitus_db` ...): ayni anda tek stack. Gecis:
  `../Digitus-Engine-main`'de `docker-compose down`, burada `docker-compose up -d`.
- **Sunucuda** yalniz `docker-compose.prod.yml` kullanilir (README).
- **Satir sonu**: bu klon `core.autocrlf=false`, `core.eol=lf` — prettier LF bekler.

## 1. Proje Ozeti

Digitus Engine V2, AI destekli anahtar kelime analizi ve kanal atama motorudur. CSV ile yuklenen anahtar kelimeleri ADS/SEO/SOCIAL kanallarina skorlayip atar, Gemini AI ile niyet analizi yapar, her kanal icin icerik uretir (blog, Google Ads RSA, sosyal medya) ve sonuclari DOCX/PDF/Excel/CSV olarak disari aktarir. Workspace (marka calismasi) kavrami ile coklu marka izolasyonu saglar; Google Ads API entegrasyonu ile keyword fikirleri ve kampanya verileri cekilebilir.

- **Hedef kullanici**: Dijital pazarlama ajanslari ve SEO/SEM uzmanlari
- **Gelistirme asamasi**: Aktif gelistirme (V2; workspace, Google Ads entegrasyonu, site profil analizi, relevance skorlama ve DB-backed export eklenmis durumda)

---

## 2. Tech Stack

### Backend
| Teknoloji | Versiyon | Amac |
|-----------|----------|------|
| Python | 3.11 | Ana dil |
| FastAPI | 0.115.6 | Web framework |
| Uvicorn | 0.27.0 | ASGI server |
| SQLAlchemy | 2.0.25 | ORM |
| Alembic | 1.13.1 | Migration |
| PostgreSQL | 15 (Alpine) | Veritabani |
| Redis | 7 (Alpine) | Cache / Celery broker |
| Celery | 5.3.6 | Asenkron gorev kuyrugu |
| Pydantic | 2.12.5 | Validation |
| Pydantic-Settings | 2.12.0 | Konfiguration |
| Loguru | 0.7.2 | Logging |
| google-genai | 2.11.0 | Gemini AI entegrasyonu (google-generativeai'dan migrate edildi, bkz. Bolum 10) |
| google-ads | 30.0.0 | Google Ads API (keyword ideas, kampanya verileri) |
| python-docx | 1.1.0 | DOCX export |
| reportlab | 4.0.8 | PDF export |
| openpyxl | 3.1.2 | Excel export |
| thefuzz[speedup] | 0.22.1 | Turkce fuzzy matching (rapidfuzz varsa o tercih edilir) |
| beautifulsoup4 + lxml | 4.12.3 / 5.1.0 | Site analyzer HTML parsing |
| numpy | 1.26.4 | Embedding cosine similarity (relevance) |
| httpx | 0.28.1 | HTTP client |

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
| Vitest + Testing Library | 4.x / 16.x | Test |
| ESLint + Prettier | 8.x / 3.x | Lint + format |

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
|   |-- config.py                 # Pydantic Settings (env degiskenleri + production guard)
|   |-- dependencies.py           # FastAPI dependency injection (DB, AI)
|   |-- api/
|   |   |-- v1/
|   |       |-- router.py         # Tum router'lari birlestiren ana router (+ opt-in API key)
|   |       |-- keywords.py       # Keyword CRUD + CSV import (preview/commit)
|   |       |-- scoring.py        # Skorlama calistirma ve sonuc
|   |       |-- channels.py       # Kanal atama ve havuzlar
|   |       |-- generation.py     # Icerik uretimi (SEO/ADS/Social)
|   |       |-- export.py         # Rapor disari aktarim (ExportJob DB-backed)
|   |       |-- tasks.py          # Celery gorev durumu sorgulama
|   |       |-- brand_profile.py  # Workspace CRUD + site profil analizi + relevance
|   |       |-- google_ads.py     # Google Ads API (keyword ideas, url-seed, kampanya import)
|   |       |-- dashboard.py      # Workspace ozet paneli (Redis cache'li)
|   |-- core/                     # Is mantigi katmani
|   |   |-- constants.py          # Tum sabit degerler (100+ sabit)
|   |   |-- security.py           # Opt-in API key auth (X-API-Key, constant-time)
|   |   |-- workspace.py          # Workspace dogrulama helper'lari (verify_scoring_run vb.)
|   |   |-- workspace_refresh.py  # Workspace keyword refresh + is_stale semantigi
|   |   |-- keyword_dedup.py      # Turkce fuzzy dedup (suffix strip + normalizasyon)
|   |   |-- keyword_normalize.py  # Keyword normalizasyon
|   |   |-- error_responses.py    # safe_500_detail helper
|   |   |-- logging_config.py     # Loguru konfigurasyonu
|   |   |-- scoring/
|   |   |   |-- score_engine.py   # Ana skorlama orkestratoru
|   |   |   |-- ads_scorer.py     # ADS skor formulu
|   |   |   |-- seo_scorer.py     # SEO skor formulu
|   |   |   |-- social_scorer.py  # SOCIAL skor formulu
|   |   |   |-- normalizer.py     # Normalizasyon yardimcilari
|   |   |   |-- state_machine.py  # ScoringRun status gecisleri
|   |   |-- channel/
|   |   |   |-- channel_engine.py # Kanal atama pipeline'i (~920 LOC)
|   |   |   |-- intent_analyzer.py# Gemini ile niyet analizi (~450 LOC, inline prompt ~426)
|   |   |   |-- pool_builder.py   # Aday havuz olusturucu
|   |   |   |-- brand_filter.py   # Marka bazli exclusion filtresi
|   |   |   |-- pre_filters/      # Kanal bazli AI on-filtreler
|   |   |       |-- base_filter.py, ads_prefilter.py, seo_prefilter.py,
|   |   |       |-- social_prefilter.py, enricher.py
|   |   |-- csv_import/
|   |   |   |-- google_ads_parser.py  # Google Ads CSV export parser (UTF-16, header tespiti)
|   |   |   |-- import_plan.py        # Import preview/commit plani
|   |   |-- dashboard/
|   |   |   |-- summary.py        # Dashboard ozet hesaplama
|   |   |   |-- next_action.py    # "Sonraki adim" onerisi
|   |   |-- site_analyzer/
|   |       |-- crawler.py        # Site crawl (SSRF korumali)
|   |       |-- profile_extractor.py  # AI ile marka profili cikarma
|   |       |-- relevance_scorer.py   # Keyword-marka relevance skoru
|   |       |-- brand_defaults.py     # BrandDefaultsResolver
|   |       |-- turkish_normalizer.py
|   |-- compliance/
|   |   |-- seo_checker.py        # Programatik SEO kriterleri
|   |   |-- geo_checker.py        # AI destekli GEO kriterleri
|   |-- database/
|   |   |-- connection.py         # Engine, SessionLocal, init_db
|   |   |-- models.py             # 24 SQLAlchemy modeli (~870 LOC)
|   |   |-- crud.py               # CRUD islemleri (workspace-scoped import dahil)
|   |-- integrations/
|   |   |-- google_ads/
|   |       |-- service.py        # GoogleAdsService (health, keyword ideas, kampanyalar)
|   |       |-- trend_calculator.py
|   |-- generators/               # Icerik uretim motorlari
|   |   |-- ai_service.py         # AI servis wrapper (GeminiService/MockAIService)
|   |   |-- seo_geo/
|   |   |   |-- seo_geo_generator.py  # SEO+GEO blog uretimi
|   |   |   |-- prompt_templates.py   # SEO_GEO_GENERATION, GEO_COMPLIANCE_CHECK, SEO_COMPLIANCE_CHECK
|   |   |-- ads/
|   |   |   |-- ads_generator.py      # Google Ads RSA uretimi
|   |   |   |-- rsa_generator.py      # RSA headline/description uretici
|   |   |   |-- keyword_grouper.py    # AI ile keyword gruplama
|   |   |   |-- validators.py         # Karakter limiti dogrulama
|   |   |   |-- prompt_templates.py   # ADS_GROUPING, ADS_RSA_GENERATION, HEADLINE_REGENERATION, DESCRIPTION_SHORTENING
|   |   |-- social/
|   |       |-- social_generator.py   # 3-fazli sosyal icerik orkestratoru
|   |       |-- category_generator.py # Faz 1: Kategori uretici
|   |       |-- idea_generator.py     # Faz 2: Fikir uretici
|   |       |-- content_generator.py  # Faz 3: Icerik uretici
|   |       |-- prompt_templates.py   # SOCIAL_CATEGORY, SOCIAL_IDEA, SOCIAL_CONTENT, IDEA_REGENERATE, CONTENT_REGENERATE
|   |-- exporters/
|   |   |-- base_exporter.py, data_collector.py
|   |   |-- csv_exporter.py, docx_exporter.py, excel_exporter.py, pdf_exporter.py
|   |-- tasks/                    # Celery gorevleri
|   |   |-- celery_app.py         # Celery konfigurasyonu
|   |   |-- scoring_tasks.py      # Skorlama gorevleri
|   |   |-- intent_tasks.py       # Niyet analizi gorevleri
|   |   |-- generation_tasks.py   # Icerik uretim gorevleri (chunk bazli)
|   |   |-- export_tasks.py       # Export gorevleri (run_export_task)
|   |   |-- task_status.py        # TaskResult kayit helper'lari
|   |-- schemas/                  # Pydantic sema dosyalari
|       |-- keyword.py, scoring.py, channel.py, content.py, export.py,
|       |-- ads.py, seo_geo.py, social.py, brand_profile.py, dashboard.py
|-- frontend/                     # React frontend
|   |-- src/
|   |   |-- App.tsx               # React Router tanimlamalari
|   |   |-- main.tsx              # React entry point
|   |   |-- pages/                # Dashboard(+Cockpit/Empty), BrandProfile, Keywords, Scoring,
|   |   |                         # Relevance, Channels, Generation, Tasks, Export, GoogleAdsExplorer
|   |   |-- components/           # Layout, ErrorBanner, TaskProgress, SocialStepper,
|   |   |                         # GoogleAdsKeywordSearch, UrlKeywordExtractor, SectionSelect
|   |   |-- services/api.ts       # Axios API istemcisi (hata cevirisi + workspace 404 temizligi)
|   |   |-- stores/               # Zustand: brandStore (aktif workspace), socialStore (3-fazli akis)
|   |   |-- hooks/                # useTaskPolling vb.
|   |   |-- styles/               # Global CSS
|   |-- package.json
|   |-- vite.config.ts            # /api proxy: localhost:8000 (docker modda app:8000)
|-- migrations/                   # Alembic migration dosyalari
|   |-- env.py
|   |-- versions/                 # baseline_squash ... 20260716_001_add_ad_generation_sets (en yeni)
|-- tests/                        # Pytest test dosyalari (bkz. Bolum 8)
|-- docker-compose.yml            # Servisler: app, db, redis, celery_worker, celery_beat, frontend
|-- docker-compose.test.yml       # Test ortami: test_db + test_app (pytest burada calisir)
|-- docker-compose.prod.yml       # Production compose (env tabanli secret'lar)
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
- `core/` -> Is mantigi katmani (scoring, channel, site_analyzer, csv_import, constants)
- `database/` -> Veri erisim katmani (models, crud, connection)
- `integrations/` -> Dis servis adaptorleri (Google Ads)
- `generators/` -> Icerik uretim katmani (ai_service, generators)
- `tasks/` -> Asenkron gorev katmani (Celery)

### Onemli Tasarim Kararlari

1. **3 kanalli skorlama sistemi (v2 — liste-goreli toplamsal formuller)**:
   Kaynak: `Digitus_Engine_v2_Skorlama_Algoritmalari.md` (repo koku). Turetilmis
   degiskenler SKORLANAN LISTEYE gorelidir (H/MB ortalama-sira persentili,
   Ln min-max log); **skorlar run'lar arasi karsilastirilamaz, asla cache'lenmez**.
   - ADS (ROI Hunter): `N_ADS = 40*H - 15*R_N + 5*min(max(T3,0),1)` (~[-15,45])
   - SEO (Opportunity Engine): taban `N_SEO = 40*Ln + 3*max(TrK,-0.5)`;
     secim aninda `+ 15*G_T + 4*G_A` (AI niyet dereceleri, `IntentAnalysis.gt/ga`).
     Rekabet terimi BILINCLI yok (reklamveren rekabeti != organik zorluk)
   - SOCIAL (Hype Tracker): `N_SOC = 20*H + 50*MB`, MB = persentil(V*max(T3,0));
     T12 bilincli formul disi
   - On temizlik: V null/0 satir ATILIR (`skipped_invalid_metrics` doner);
     trendler DB'de yuzde -> /100 oran + [-1,+3] kirpma; C null -> 0
   - `KeywordScore.metrics_snapshot` olcek sozlesmesi: ust duzey trend_3m/12m
     HAM YUZDE; `derived.t3/t12` oran+kirpilmis; `derived` h/ln/trk/mb tasir
     (final secim MB/H'yi buradan okur)

2. **Kanal atama pipeline'i** (5 asamali, v2 secim semantigi):
   - Aday havuz -> Niyet analizi (AI, paralel) -> Marka filtresi + On-filtre (AI) -> Sinif-oncelikli secim -> Expansion
   - **Backfill KALDIRILDI**: elenen (sinif -1) kelime final havuza giremez;
     kapasite doldurma yalnizca expansion turlaridir
   - **AI'nin kanal basina yetkisi FARKLIDIR**:
     ADS siniflar+eler (hot_sale=2/lead=1/eliminate=-1 -> `PreFilterResult.ai_class`);
     SEO'da AI ELEMEZ (intent hep gecer, G_T/G_A derecesi ekler; SEO'da eleme
     yalnizca marka dislama + deterministik price/fiyat filtresi — bilincli istisna);
     SOCIAL 3 binary boyutla sinif 0-3 verir, elemeyi KOD verir (sinif 0 VE MB<=0.3)
   - Final siralama deterministik: `(-sinif, -adjusted, -hacim, keyword)`
   - Cift kanal etiketleri: ADS∩SEO -> `is_strategic`; SOCIAL ust %20 dilim +
     H>=0.7 -> `pool_label='rising_opportunity'` (Yukselen Firsat)
   - **Relevance bilincli istisna**: ham `KeywordScore` saf dokuman skorudur;
     relevance yalnizca havuz siralamasindaki `adjusted_score`'a islenir
     (`max(skor,0) * relevance * katsayi` — negatif skor ters cevirme korumasi)

3. **Cross-channel transfer**: ADS'den reddedilen kelimeler SEO'ya aktarilabilir

4. **Fallback mekanizmasi**: AI basarisiz olursa kanal bazli varsayilan niyet tipi atanir

5. **Batch isleme**: AI cagrilari 6'li batch'ler halinde yapilir (JSON truncation onleme)

6. **Turkce fuzzy dedup**: Ek kirpma + karakter normalizasyon + %85 benzerlik esigi

7. **Workspace izolasyonu**: Neredeyse tum endpoint'ler `brand_profile_id` query parametresi ile
   workspace'e baglanir; `verify_scoring_run` / `verify_export_in_workspace` cross-workspace
   erisimde 404 dondurur (`app/core/workspace.py`)

8. **Opt-in API key auth**: `API_KEY` env set edilirse tum `/api/v1/*` endpoint'leri `X-API-Key`
   header'i ister; production'da zorunludur (`app/config.py` model_validator)

9. **DB-backed export**: Export islemleri `ExportJob` tablosu + Celery `run_export_task` ile yurur;
   multi-worker safe, restart'a dayanikli

10. **Redis cache**: Dashboard ozeti ve Google Ads url-seed sonuclari Redis'te cache'lenir;
    Redis erisim hatalari sessizce yutulur ve normal akisa devam edilir (bilincli tasarim)

11. **Secim guvenilirligi (guvenilirlik plani Faz 0-D)**:
    - SEO seciminde AI YOK: secim deterministik (fiyat filtresi
      `_pre_brand_defense_filter` hook'unda, marka savunmasindan ONCE calisir;
      own-brand "fiyat" bypass'i kapali); SEO metadata (h1/h2) secim SONRASI
      `generate_metadata` adiminda uretilir ve `is_kept`'e dokunamaz
    - AI cikti sozlesmesi: intent + prefilter'lar Gemini `response_schema` +
      3000 token; eksik-ID hedefli retry + tekil final pass
      (`SINGLE_RETRY_MAX_PER_STAGE=10`, oncelik rank); parse hatasina 1 retry;
      fallback `IntentAnalysis.source='fallback'` (gt/ga NULL kalir)
    - Expansion kapasite odakli (%70 esigi KALKTI); hard butce: kanal basina
      `AiCallBudget` (`app/core/channel/ai_budget.py`), toplam <= 60; her AI
      katmani gercek `ai_calls_used` sayaci dondurur (erken donusler 0)
    - Transferler gercek GT/GA alir (`source='transfer_ai'`, yalniz yeni
      candidate'lar); donus dict'i paydalari AYRI verir
    - `selection_quality` (Faz G): transition oncesi toplanir, task
      result_data'ya yazilir (kanal sayaclari + transfers + expansion)

12. **ADS uretim versiyonlamasi (Faz E)**: Her RSA uretimi bir `AdGenerationSet`
    olur (`generating|active|draft|archived|failed` + `is_stale`);
    ilk basarili set otomatik `active`, sonrakiler `draft` (onay:
    `POST /generation/ads/sets/{id}/activate`). Dispatch ScoringRun satir
    kilidi ile serilestirilir (cift istek 409); final durumlar TEK
    transaction'da (`finalize_ads_success/failure` — `update_task_status` ile
    final YAZILMAZ). Kanal reassignment tum setleri `is_stale=True` yapar;
    tuketiciler (GET/dashboard/export) yalniz `active AND is_stale=False`
    okur (`active_ad_groups`). Grup regenerate ASYNC 202: kaynak set yeni
    draft'a klonlanir, stale kaynaktan regenerate 409.
    Yasam dongusu: `app/generators/ads/generation_sets.py`.

13. **Icerik grounding (Faz F)**: RSA + SOCIAL prompt'lari IDDIA KURALLARI +
    `{product_facts}` (confirmed profil) tasir; few-shot'lar iddiasiz.
    `find_ungrounded_claims` (`app/generators/ads/validators.py`) sayisal
    iddia/superlatif/kazanc-garantisini deterministik yakalar (Turkce sayi
    normalizasyonu; keyword muafiyeti dar). Whitelist = confirmed
    product_facts + kullanicinin ISTEKTE verdigi USP (`trusted_brand_usp`) —
    generic fallback USP whitelist'e GIRMEZ. ADS: ihlalli asset 1 kez
    CLAIM_REWRITE, cozulmezse elenir (fallback asset'ler dahil; minimum
    saglanamazsa grup failed). SOCIAL: ihlalde 1 regenerate; hala ihlalliyse
    icerik KAYDEDILMEZ, `warnings` ({idea_id, reason_code, claims}) response +
    task result_data'da tasinir; tumu reddedilirse task failed.

### Teknik Borclar ve Dikkat Edilecekler

- **Site profil analizi FastAPI BackgroundTasks ile web process icinde kosuyor**
  (`app/api/v1/brand_profile.py` -> `_run_profile_analysis`): app restart olursa calisan
  is sessizce olur. **Celery'ye TASINMAYACAK** (ADR-002): oldurulen bir Celery worker'i
  ayni deligi yeniden acar; bayatlik cozumu tasima degil, **okuma aninda** cozmektir.
  Iki katmanli savunma:
  1. `app/core/site_analyzer/stuck_janitor.py` -> `run_startup_janitor()`, app acilisinda
     bir kez; `PROFILE_STALE_MINUTES`(15)'ten eski running/pending profilleri failed yapar.
  2. `fail_if_stuck()` ayni esikle **okuma aninda**: `get_profile` ve `get_workspace`
     (frontend'in yokladigi uc). Acilis supurmesi restart aninda TAZE olan satiri atlar ve
     bir daha kosmaz — o satiri kurtaran yalnizca bu okuma-ani kontroludur.
  Bilinen artik risk (ADR-002'de kabul edildi): 15 dk'yi asan CANLI bir analiz de failed
  gorunur; kullanici tekrar denerse `analyze_profile`'da tek-ucus kilidi olmadigi icin
  ikinci kosu baslar ve `_run_profile_analysis`'in kosulsuz `status='draft'` yazimi
  yenisini ezebilir. Sahada bildirilirse cozum: ayri/uzun okuma esigi + 409 tek-ucus
  kilidi (Bolum 12'deki ADS dispatch kalibi).
- **`alembic.ini` icindeki `sqlalchemy.url`**: Statik deger var, `migrations/env.py` override ediyor (sorun degil ama kafa karistirici)
- **Backend bagimlilik pinleri eski** (2024 basi donemi); buyuk surum atlamalari dikkatli yapilmali
- `brand_profile.py` (~1000 LOC) API katmaninda is mantigi barindiriyor; sanitization ve
  background job fonksiyonlari `core/site_analyzer`'a tasinabilir

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
GEMINI_MODEL=<model-adi>         # default: config.py'deki deger
APP_ENV=development
DEBUG=true
SECRET_KEY=<secret-key>
API_KEY=                         # opsiyonel; set edilirse X-API-Key zorunlu (prod'da zorunlu)
CORS_ORIGINS=*                   # prod'da whitelist zorunlu
# Skorlama katsayilari artik env degil app/core/constants.py'de (Skorlama v2)
# Google Ads entegrasyonu (opsiyonel):
GOOGLE_ADS_DEVELOPER_TOKEN=, GOOGLE_ADS_CLIENT_ID=, GOOGLE_ADS_CLIENT_SECRET=,
GOOGLE_ADS_REFRESH_TOKEN=, GOOGLE_ADS_LOGIN_CUSTOMER_ID=, GOOGLE_ADS_CUSTOMER_ID=
```

### Sik Kullanilan Komutlar
```bash
# Gelistirme sunucusu
uvicorn app.main:app --reload

# Frontend
cd frontend && npm run dev

# Frontend kalite kontrolleri (commit oncesi zorunlu)
cd frontend && npm run lint && npm run format:check && npm run build && npm run test

# Celery worker
celery -A app.tasks.celery_app worker --loglevel=info

# Celery beat (zamanlanmis gorevler)
celery -A app.tasks.celery_app beat --loglevel=info

# Test calistirma (Docker — onerilen ve guvenli yol)
docker-compose -f docker-compose.test.yml run --rm test_app pytest tests/ -v

# Docker ile hepsini baslat
docker-compose up -d

# Migration olusturma
alembic revision --autogenerate -m "aciklama"
alembic upgrade head
```

**UYARI:** `pytest`'i ASLA calisan `digitus_app` container'inda veya production DATABASE_URL
ile calistirma — test fixture'lari tablolari truncate eder. Her zaman `docker-compose.test.yml`
kullan (izole `test_db` + `digitus_engine_test` veritabani).

---

## 6. Veritabani & Veri Modeli

### Ana Entity'ler (24 model)

```
Keyword (anahtar kelimeler, global havuz)
  |-- 1:N --> WorkspaceKeyword (workspace'e ozel snapshot: volume, competition, is_stale)
  |-- 1:N --> KeywordScore (ADS/SEO/SOCIAL skorlari)
  |-- 1:N --> ChannelCandidate (kanal aday havuzu)
  |-- 1:N --> IntentAnalysis (AI niyet analizi)
  |-- 1:N --> PreFilterResult (AI on-filtre)
  |-- 1:N --> ChannelPool (final kanal atamasi)
  |-- 1:N --> ContentOutput (uretilen icerik meta)
  |-- 1:N --> KeywordRelevance (marka-keyword relevance skoru)

BrandProfile (workspace / marka calismasi)
  |-- 1:N --> ScoringRun
  |-- 1:N --> WorkspaceKeyword

ScoringRun (skorlama calistirmasi, brand_profile_id ile workspace'e bagli)
  |-- 1:N --> KeywordScore, ChannelCandidate, IntentAnalysis, PreFilterResult, ChannelPool

Icerik Modelleri:
  - SEOGeoContent (+ SEOComplianceResult, GEOComplianceResult, ComplianceCheck)
  - AdGenerationSet (versiyonlu ADS set: status + is_stale) --> AdGroup
    --> AdHeadline, AdDescription, NegativeKeyword
    (AdGroup.generation_set_id NOT NULL; run kimligi setten turetilir)
  - SocialCategory --> SocialIdea --> SocialContent
  - Sosyal brief akisi (`plan_social_brief_akisi.md`, migration `20260923_001`):
    SocialBrief --> SocialBriefKeyword (1-5 kelime), SocialBriefTarget (<=6 platform/format/sure),
    SocialGenerationAttempt (stage categories|ideas|ideas_retry|contents; idempotency + lease).
    SocialCategory/SocialIdea/SocialContent'e nullable `brief_id` (+ SocialIdea.brief_target_id,
    SocialContent.format_payload/duration_status/actual_duration_sec/validation_warnings).
    `brief_id IS NULL` = eski (legacy) veri. Icerik tekilligi DB'de:
    `uq_social_content_idea_brief` (idea_id UNIQUE WHERE brief_id IS NOT NULL).
    Brief kategori uretiminde kilitlenir (`locked_at`, K11); reassignment brief zincirini stale yapar

Altyapi Modelleri:
  - TaskResult (Celery gorev takibi)
  - ExportJob (DB-backed export durumu)
```

### Migration Stratejisi
- **Alembic** kullaniliyor; `migrations/versions/` altinda 9 migration var
  (`20260511_001_baseline_squash` tum eski zincirin squash'i ... en yenisi
  `20260716_001_add_ad_generation_sets`: AdGenerationSet tablosu +
  AdGroup.generation_set_id NOT NULL + legacy backfill)
- **DIKKAT**: baseline squash canli `Base.metadata.create_all` kullanir —
  yeni kurulumda kolonlar baseline'da zaten olusur. Bu yuzden HER additive
  migration kolon-varlik kontrolu ile IDEMPOTENT yazilmalidir
  (ornek kalip: `20260712_001` / `20260713_001`)
- `migrations/env.py` icerisinde `settings.database_url` ile override yapiliyor
- Yeni migration: `alembic revision --autogenerate -m "aciklama"`
- Uygulama: `alembic upgrade head`

### Seed Data
- Bu dalda seed/init betigi yok (kok `scripts/` ana repoda kaldi)
- DEBUG modda uygulama baslarken `init_db()` otomatik calisir

---

## 7. API & Arayuzler

### REST API Endpoint'leri (prefix: `/api/v1`)

Neredeyse tum endpoint'ler `brand_profile_id` query parametresi ile workspace scope'u ister.

#### Keywords (`/api/v1/keywords`)
| Method | Path | Aciklama |
|--------|------|----------|
| GET | `/keywords/` | Anahtar kelimeleri listele (workspace-scoped) |
| POST | `/keywords/` | Tekil anahtar kelime ekle |
| POST | `/keywords/upload-csv` | CSV import on-izleme (dry-run, filtre raporu) |
| POST | `/keywords/upload-csv/commit` | On-izlemesi yapilan import'u kaydet |
| POST | `/keywords/import` | JSON body ile toplu import |
| GET | `/keywords/{keyword_id}` | Tekil kelime getir |
| PUT | `/keywords/{keyword_id}` | Anahtar kelime guncelle |
| DELETE | `/keywords/{wk_id}` | Workspace keyword sil |
| DELETE | `/keywords/all` | Workspace'in tum kelimelerini sil |
| POST | `/keywords/cleanup-duplicates` | Turkce fuzzy dedup calistir |

#### Scoring (`/api/v1/scoring`)
| Method | Path | Aciklama |
|--------|------|----------|
| POST | `/scoring/runs` | Yeni skorlama calistirmasi olustur |
| POST | `/scoring/runs/{run_id}/execute` | Skorlamayi calistir |
| GET | `/scoring/runs` | Calistirmalari listele |
| GET | `/scoring/runs/{run_id}` | Tekil calistirma getir |
| DELETE | `/scoring/runs/{run_id}` | Calistirmayi sil |
| GET | `/scoring/runs/{run_id}/scores` | Skorlama sonuclarini getir (sirali/sayfali) |
| GET | `/scoring/runs/{run_id}/top/{channel}` | Kanal bazli top-N |
| GET | `/scoring/runs/{run_id}/export/xlsx` | XLSX olarak disari aktar |

#### Channels (`/api/v1/channels`)
| Method | Path | Aciklama |
|--------|------|----------|
| POST | `/channels/runs/{run_id}/assign` | Kanal atamasini baslat (async) |
| GET | `/channels/runs/{run_id}/pools` | Tum kanal havuzlarini getir |
| GET | `/channels/runs/{run_id}/pools/{channel}` | Belirli kanal havuzunu getir |

#### Generation (`/api/v1/generation`)
| Method | Path | Aciklama |
|--------|------|----------|
| POST | `/generation/seo-geo` | SEO+GEO icerik uret (tekil) |
| POST | `/generation/seo-geo/bulk/{scoring_run_id}` | Toplu SEO+GEO uretimi (Celery chunk) |
| GET | `/generation/seo-geo/{content_id}` | Icerik getir |
| GET | `/generation/seo-geo/{content_id}/compliance` | Uyumluluk sonucu |
| GET | `/generation/seo-geo/list/{scoring_run_id}` | Run'a ait icerikleri listele |
| POST | `/generation/ads/rsa` | Google Ads RSA uret (async, versiyonlu set; surerken 409) |
| GET | `/generation/ads/sets` | Run'a ait ADS set gecmisi (status, is_stale, version) |
| POST | `/generation/ads/sets/{set_id}/activate` | Draft/archived seti aktif yap (eski aktif archived) |
| GET | `/generation/ads/rsa/{scoring_run_id}` | RSA sonuclari (varsayilan aktif non-stale set; `?set_id=` ile gecmis) |
| GET | `/generation/ads/rsa/group/{group_id}` | Tekil ad group getir |
| POST | `/generation/ads/rsa/group/{group_id}/regenerate` | Ad group yeniden uret (ASYNC 202 — yeni draft set; stale kaynak 409) |
| GET | `/generation/social/format-matrix` | Platform x format x sure matrisi (`format_matrix.py`) |
| POST | `/generation/social/briefs` | Brief olustur (kelimeler + hedefler + marka) |
| GET | `/generation/social/briefs` | Run'a ait brief'ler |
| GET | `/generation/social/briefs/{brief_id}` | Brief detay (hedefler, kapsama, uyarilar) |
| GET | `/generation/social/briefs/{brief_id}/state` | Ekran geri yukleme: kategoriler + attempt ozetleri (salt-okunur) |
| POST | `/generation/social/briefs/{brief_id}/categories/generate` | Kategori uret + brief'i kilitle |
| POST | `/generation/social/briefs/{brief_id}/ideas/generate` | Fikir uretimi (async attempt) |
| GET | `/generation/social/briefs/{brief_id}/ideas/attempts/{attempt_id}` | Fikir attempt durumu/sonucu |
| POST | `/generation/social/briefs/{brief_id}/ideas/retry` | Yalniz eksik hedef + bos kategori (K4); ikisi de tamsa 409 |
| GET | `/generation/social/briefs/{brief_id}/ideas/retry/attempts/{attempt_id}` | Retry attempt durumu |
| POST | `/generation/social/briefs/{brief_id}/contents/async` | Icerik uretimi (async attempt) |
| GET | `/generation/social/briefs/{brief_id}/contents/attempts/{attempt_id}` | Icerik attempt durumu/sonucu |
| GET | `/generation/social/contents/history` | Workspace geneli sosyal icerik gecmisi (flag'e bagli degil) |
| POST | `/generation/social/categories` | Legacy Faz 1 (yalniz `brief_id IS NULL`) |
| POST | `/generation/social/ideas` | Legacy Faz 2 (brief kategorisi -> 409) |
| POST | `/generation/social/contents` (+`/async`) | Legacy Faz 3 (brief fikri -> 409) |
| POST | `/generation/social/bulk` | `ENABLE_SOCIAL_LEGACY_BULK=false` (varsayilan) iken 410 (K12) |
| GET | `/generation/social/{scoring_run_id}` | Legacy gorunum: yalniz `brief_id IS NULL` satirlar |
| POST | `/generation/social/ideas/{idea_id}/select` | Legacy fikir sec/kaldir (brief fikri -> 409) |
| POST | `/generation/social/ideas/{idea_id}/regenerate` | Legacy fikir yeniden uret; brief fikri veya icerigi olan fikir -> 409 (K10) |
| POST | `/generation/social/contents/{content_id}/regenerate` | Legacy icerik yeniden uret (brief icerigi -> 409) |
| POST | `/generation/ads`, `/generation/social` | Basit/fallback uretim endpoint'leri |

#### Export (`/api/v1/export`)
| Method | Path | Aciklama |
|--------|------|----------|
| POST | `/export/` | Rapor olustur (DOCX/PDF/XLSX/CSV; Celery + ExportJob) |
| GET | `/export/{export_id}/status` | Export durumunu sorgula |
| GET | `/export/{export_id}/download` | Dosyayi indir |
| GET | `/export/run/{run_id}` | Run'a ait export'lari listele |
| POST | `/export/simple` | Senkron basit export |

#### Tasks (`/api/v1/tasks`)
| Method | Path | Aciklama |
|--------|------|----------|
| GET | `/tasks/` | Gorevleri listele |
| GET | `/tasks/{task_id}` | Gorev durumunu sorgula |
| GET | `/tasks/run/{run_id}` | Run'a ait gorevleri listele |
| POST | `/tasks/{task_id}/cancel` | Gorevi iptal et |

#### Brand Profile / Workspace (`/api/v1/brand-profile`)
| Method | Path | Aciklama |
|--------|------|----------|
| POST | `/brand-profile/workspaces` | Workspace olustur |
| GET | `/brand-profile/workspaces` | Workspace'leri listele |
| GET | `/brand-profile/workspaces/{id}` | Workspace getir |
| PUT | `/brand-profile/workspaces/{id}/confirm` | Profili onayla |
| POST | `/brand-profile/workspaces/{id}/archive` | Arsivle (soft delete) |
| POST | `/brand-profile/workspaces/{id}/restore` | Geri getir |
| POST | `/brand-profile/workspaces/{id}/keywords/refresh` | Keyword snapshot'larini yenile |
| GET | `/brand-profile/runs/{run_id}/profile` | Run'a bagli profili getir |
| POST | `/brand-profile/runs/{run_id}/profile/analyze` | Site profil analizi baslat (BackgroundTasks) |
| PUT | `/brand-profile/runs/{run_id}/profile/confirm` | Profili onayla |
| POST | `/brand-profile/runs/{run_id}/relevance/compute` | Relevance hesapla |
| GET | `/brand-profile/runs/{run_id}/relevance` | Relevance skorlarini getir |

#### Google Ads (`/api/v1/google-ads`)
| Method | Path | Aciklama |
|--------|------|----------|
| GET | `/google-ads/health` | Baglanti sagligi |
| GET | `/google-ads/customers` | Erisebilir musteri listesi |
| GET | `/google-ads/customers/{customer_id}` | Musteri detayi |
| POST | `/google-ads/enrich` | Keyword metriklerini zenginlestir |
| POST | `/google-ads/keyword-ideas-by-url` | URL seed ile keyword fikirleri (Redis cache, bkz. Bolum 13) |
| POST | `/google-ads/import` | Fikirleri workspace'e import et |
| GET | `/google-ads/campaigns` | Kampanya listesi |
| GET | `/google-ads/campaigns/keywords` | Kampanya keyword'leri |
| POST | `/google-ads/campaigns/keywords/import` | Kampanya keyword'lerini import et |

#### Dashboard (`/api/v1/dashboard`)
| Method | Path | Aciklama |
|--------|------|----------|
| GET | `/dashboard/workspace-summary` | Workspace ozet paneli (Redis cache'li, `refresh=true` ile bypass) |

### Health Check
| Method | Path | Aciklama |
|--------|------|----------|
| GET | `/` | Basit health check |
| GET | `/health` | Detayli health check |

### Auth Mekanizmasi
- **Opt-in API key**: `API_KEY` env set edilirse tum `/api/v1/*` endpoint'leri `X-API-Key`
  header'i ister (`app/core/security.py`, constant-time karsilastirma). Set edilmezse koruma
  devre disi. Production'da `API_KEY` zorunludur (`app/config.py` guard).
- Frontend'in anahtari gondermesi icin iki mekanizma vardir:
  1. **Dev/docker**: vite proxy (`frontend/vite.config.ts`) `API_KEY` env degiskeninden
     `X-API-Key` header'i enjekte eder — tarayici koda anahtar sizmaz.
  2. **Production build** (proxy yok): `frontend/src/services/api.ts` `VITE_API_KEY`
     build-time env degiskeninden header ekler.

### Middleware'ler
- **CORSMiddleware**: `CORS_ORIGINS` env'den okunur; `*` iken credentials kapali,
  production'da `*` yasak (config guard)
- **Global Exception Handler**: Yakalanmamis hatalari loglar; DEBUG'da detay, degilse
  jenerik mesaj doner

---

## 8. Test Stratejisi

### Test Turleri ve Araclar
- **Backend**: pytest 7.4.4 + pytest-asyncio + pytest-cov (gercek Postgres ister)
- **Frontend**: Vitest + Testing Library (`npm run test`)

**Bu daldaki taban (09.10)**: backend 4388 passed / 112 skipped / 0 kirmizi (~8 dk),
frontend 213 passed. Kok `scripts/` / `benchmark/` / arastirma modullerine baglanan
testler bu dala ALINMADI; yeni kirmizi = gercek regresyon. Tam kosudan once
`alembic upgrade head` (test_db tmpfs, sema her kosuda bos baslar):
`docker-compose -f docker-compose.test.yml run --rm test_app sh -c "alembic upgrade head && pytest tests/ -q"`

### Test Dosyalari
```
tests/
|-- conftest.py                              # Fixtures: db_session, client, make_workspace, truncate_all
|-- unit/
|   |-- test_ai_safe_response.py             # AI response guvenli parse
|   |-- test_dashboard_cache.py              # Dashboard Redis cache
|   |-- test_dashboard_next_action.py        # "Sonraki adim" mantigi
|   |-- test_google_ads_campaign_cache.py    # Kampanya cache
|   |-- test_google_ads_parser.py            # Google Ads CSV parser
|   |-- test_google_ads_parser_real_csvs.py  # Gercek CSV ornekleriyle parser
|   |-- test_keyword_normalize.py            # Turkce fuzzy dedup normalizasyon
|   |-- test_relevance_fallback.py           # Relevance fallback
|   |-- test_scoring_v2_formulas.py          # v2 formul golden vektorleri + on temizlik + clamp
|   |-- test_schema_fixes.py                 # Sema duzeltmeleri
|   |-- test_ssrf_url_guard.py               # SSRF URL korumasi
|   |-- test_status_lint_guard.py            # Status string lint
|   |-- test_workspace_model_constraints.py  # WorkspaceKeyword constraint'leri
|   |-- test_workspace_phase_b_migration.py  # Workspace migration yolu
|   |-- test_workspace_refresh.py            # Workspace refresh + is_stale
|   |-- test_workspace_url_seed.py           # URL seed keyword extraction
|-- integration/
|   |-- test_auth.py                         # API key enable/disable senaryolari
|   |-- test_brand_exclusion_filter.py       # Marka exclusion filtresi
|   |-- test_channel_expansion.py            # Kanal expansion
|   |-- test_csv_import_google_ads.py        # Google Ads CSV import akisi
|   |-- test_dashboard_summary.py            # Dashboard ozeti
|   |-- test_final_pool_ordering.py          # v2 sinif-oncelikli secim, gt/ga boost, etiketler, fiyat ayrimi
|   |-- test_keyword_import_snapshot.py      # Import + workspace snapshot
|   |-- test_migration_chain.py              # Bos DB'den alembic upgrade head
|   |-- test_pool_builder.py                 # Havuz olusturucu
|   |-- test_scoring_scores_sort.py          # Skor siralama
|   |-- test_workspace_isolation.py          # Cross-workspace 404/400 izolasyon

frontend/src/**/*.test.ts(x)                 # Vitest testleri (Dashboard, Scoring, BrandProfile)
```

### Test Calistirma
```bash
# Docker ile (onerilen — gercek Postgres gerektirir)
docker-compose -f docker-compose.test.yml run --rm test_app pytest tests/ -v

# Coverage ile
docker-compose -f docker-compose.test.yml run --rm test_app pytest tests/ --cov=app --cov-report=html

# Frontend
cd frontend && npm run test
```

### Kapsam Notlari
- Guclu alanlar: workspace izolasyonu, CSV import/dedup, dashboard, migration zinciri, auth
- **Zayif alanlar (dogrudan birim testi yok)**: `core/scoring/` formul dosyalari,
  `exporters/`, `generators/` (seo_geo, ads, social). Bu modullerde degisiklik yaparken
  ekstra dikkat + manuel dogrulama gerekir.

---

## 9. Deployment & CI/CD

### Ortamlar
- **Development**: Docker Compose ile lokal gelistirme
- **Production**: `docker-compose.prod.yml` (secret'lar env'den; API_KEY + CORS whitelist zorunlu)

### Docker Compose Servisleri (docker-compose.yml)
| Servis | Container | Port |
|--------|-----------|------|
| app | digitus_app | 8000 |
| db | digitus_db (postgres:15-alpine) | 5432 (yalnizca localhost) |
| redis | digitus_redis (redis:7-alpine) | 6379 (yalnizca localhost) |
| celery_worker | digitus_celery_worker | - |
| celery_beat | digitus_celery_beat | - |
| frontend | digitus_frontend | 3000 |

Test icin ayri: `docker-compose.test.yml` (test_db + test_app).

### Deploy Sureci
```bash
# Docker Compose ile
docker-compose up -d --build

# Sadece uygulamayi yeniden baslat
docker-compose restart app celery_worker

# Frontend degisikligi sonrasi (HMR cache tuzagi — bkz. Bolum 11)
docker-compose restart frontend
```

### CI/CD Pipeline
- **Mevcut durumda CI/CD yok**: `.github/` dizini bulunmuyor
- GitHub Actions ile test, lint ve deploy pipeline'i eklenmeli

---

## 10. Bilinen Sorunlar & Yapilacaklar

### Guvenlik / Bagimlilik
- **Backend bagimlilik pinleri eski** (2024 basi); duzenli guncelleme turu + `pip-audit` CI'a eklenmeli
- **google-genai'ya migrate edildi** (eski `google-generativeai==0.8.0` kaldirildi): `GeminiService`
  artik `genai.Client(api_key=...)` + `client.models.generate_content(...)` kullaniyor
  (`app/generators/ai_service.py`); embedding tarafi da `client.models.embed_content(...)`'a
  gecti (`app/core/site_analyzer/relevance_scorer.py`). `GeminiService.client` **lazy property**
  — eski SDK'nin `genai.configure()`'i bos/gecersiz api_key'de sessiz kalirken yeni SDK'nin
  `genai.Client()`'i **construction aninda** ValueError firlatiyor; lazy olmasaydi her istekte
  `Depends(get_ai)` DI cozumlemesi (GEMINI_API_KEY bos oldugunda, ör. test ortami) cagiranlardaki
  try/except fallback zincirlerini (intent fallback vb.) atlayip 500'e donusurdu.
  `_extract_text`'teki finish_reason karsilastirmasi da guncellendi: eski SDK int enum
  dondururken (`STOP=1`), yeni SDK string enum dondurüyor (`"STOP"`) — ikisi de kabul ediliyor.
  Zincirleme bagimlilik guncellemesi gerekti: `httpx` 0.26.0→0.28.1 (google-genai `httpx>=0.28.1`
  istiyor), `pydantic` 2.5.3→2.12.5 + `pydantic-settings` 2.1.0→2.12.0 (google-genai
  `pydantic>=2.12.5` istiyor), `fastapi` 0.109.1→0.115.6 (starlette 0.35.1'in TestClient'i
  httpx>=0.28'de kaldirilmis `app=` kwarg'ini geciriyordu — starlette 0.41.3'e otomatik yukseldi).
  `GEMINI_MODEL` hala preview model ise kaldirilma riskine karsi GA modele sabitlenmeli;
  `thinking_config` (yeni SDK'da mevcut) dusunme-token butce hipotezini test etmek icin
  kullanilabilir (bkz. güvenilirlik plani Faz 0).
- `.env` dosyasi lokal makinede mevcut (gitignore'da, repo'ya girmiyor) — icindeki gercek
  API anahtarlarina dikkat

### Altyapi
- **CI/CD pipeline'i yok** (GitHub Actions eklenmeli)
- **Site profil analizi BackgroundTasks ile web process icinde** — takili profil sorunu
  ADR-002 ile kapandi (startup janitoru + okuma-ani `fail_if_stuck`); Celery'ye tasima
  BILINCLI OLARAK REDDEDILDI, bkz. Bolum 4 Teknik Borclar
- Celery beat schedule'da aktif periyodik gorev tanimlanmamis (sadece ornek/yorum)

### Kod Kalitesi
- Backend'de linter/formatter yok (ruff onerisi); frontend'de ESLint 8 EOL, 9'a gecis gerekli
- Type checking araci (mypy) kullanilmiyor
- `brand_profile.py` ~1000 satir — is mantigi core'a tasinmali

### Yakin Vadede Yapilabilecek Refactor'lar
- `app/core/constants.py` icindeki 100+ sabitin gruplara ayrilmasi
- Rate limiting middleware eklenmesi
- API versiyonlama stratejisi netlestirilmesi (su an sadece v1)
- Scoring formulleri, exporters ve generators icin birim testler

---

## 11. Claude Icin Notlar

### Naming Convention
- **Python dosyalari**: snake_case (modeller, fonksiyonlar, degiskenler)
- **Siniflar**: PascalCase (`ScoreEngine`, `PoolBuilder`, `ChannelCandidate`)
- **Sabitler**: UPPER_SNAKE_CASE (`ADS_POOL_SIZE`, `SEO_TREND_3M_WEIGHT`)
- **API endpoint'leri**: kebab-case URL'ler (`/cleanup-duplicates`, `/seo-geo`)
- **Frontend**: PascalCase bilesenleri, camelCase degiskenler

### Gemini Prompt Sablonlari Haritasi

Tum Gemini AI prompt'lari `prompt_templates.py` dosyalarinda tanimli. Ek olarak 1 yerde inline prompt var.

| Dosya | Prompt Sabiti | Amac |
|-------|---------------|------|
| `generators/seo_geo/prompt_templates.py` | `SEO_GEO_GENERATION_PROMPT` | Blog icerigi uretimi (keyword, sector, target_market, tone, word_count) |
| `generators/seo_geo/prompt_templates.py` | `GEO_COMPLIANCE_CHECK_PROMPT` | 7 kriterli GEO uyumluluk degerlendirmesi |
| `generators/seo_geo/prompt_templates.py` | `SEO_COMPLIANCE_CHECK_PROMPT` | SEO sorunlari ve onerileri (opsiyonel, programatik kontrol oncelikli) |
| `generators/ads/prompt_templates.py` | `ADS_GROUPING_PROMPT` | Keyword'leri reklam gruplarina ayirma |
| `generators/ads/prompt_templates.py` | `ADS_RSA_GENERATION_PROMPT` | RSA headline + description + negatif kelime uretimi |
| `generators/ads/prompt_templates.py` | `HEADLINE_REGENERATION_PROMPT` | 30 karakter asiminda baslik kisaltma |
| `generators/ads/prompt_templates.py` | `DESCRIPTION_SHORTENING_PROMPT` | 90 karakter asiminda aciklama kisaltma |
| `generators/social/prompt_templates.py` | `SOCIAL_CATEGORY_PROMPT` | Faz 1: Icerik kategorileri |
| `generators/social/prompt_templates.py` | `SOCIAL_IDEA_PROMPT` | Faz 2: Kategori basina fikirler |
| `generators/social/prompt_templates.py` | `SOCIAL_CONTENT_PROMPT` | Faz 3: Tam icerik paketi |
| `generators/social/prompt_templates.py` | `IDEA_REGENERATE_PROMPT` | Begenilmeyen fikir icin yeniden uretim |
| `generators/social/prompt_templates.py` | `CONTENT_REGENERATE_PROMPT` | Begenilmeyen icerik icin yeniden uretim |

**Inline prompt'lar (template dosyasi disinda):**
| Dosya | Amac |
|-------|------|
| `core/channel/intent_analyzer.py` (`_build_intent_prompt`) | Batch niyet analizi; SEO dalinda ek olarak `gt`/`ga` dereceleri + musteri urun tanimi blogu ister |
| `core/channel/pre_filters/ads_prefilter.py` | ADS sinif sorulari (hot_sale/lead/eliminate) + musteri urun tanimi blogu |
| `core/channel/pre_filters/seo_prefilter.py` | SEO metadata-only (depth_label, geo_suitable, h1/h2 onerileri) — AI eleme YAPMAZ |
| `core/channel/pre_filters/social_prefilter.py` | SOCIAL konusulabilirlik 3 binary boyut + hook/scenario_note — eleme kararini KOD verir |

Site analyzer prompt'lari `core/site_analyzer/profile_extractor.py` icinde tanimlidir.

**Sosyal brief prompt'lari** (`generators/social/brief_category_prompt.py`, `brief_idea_prompt.py`,
`brief_content_prompt.py`; AI stage'leri `social_brief_categories/ideas/contents`): cikti
sozlesmeleri `app/core/social/*_contract.py`'de fail-closed dogrulanir. Gemini
`response_schema`'si `app/core/social/provider_schema.py` ile beyaz listeye cevrilir
(additionalProperties duser). Fikirde uymayan fikir TEK TEK atilir (cevrilmez; yalniz
"X"->twitter yazim normalizasyonu), eksik hedef + bos kategori icin tek tamamlama turu;
deneme basina tavan `IDEA_ATTEMPT_MAX_AI_CALLS=24`, retry `IDEA_RETRY_ATTEMPT_MAX_AI_CALLS=12`.
Icerik oge basina en fazla 2 cagri (ilk + tek birlesik onarim); batch zaman butcesi
`CONTENT_BATCH_TIME_BUDGET_SECONDS=900` -> `partial`. Markdown fence yalniz `ai_json` ile kurtarilir.

**Prompt degisikligi yaparken dikkat:**
- Tum prompt'lar JSON ciktisi bekler; format bozulursa downstream parsing patlar
  (`intent_analyzer.py` markdown fence temizligi + retry yapar ama garantisi yok)
- `{{` ve `}}` Python f-string escape'leri, prompt iceriginde literal `{` `}` icin kullanilir
- Prompt icindeki karakter limitleri (headline <=30, description <=90) is kurali, degistirilmemeli

### Dosya Yapisi Kurallari
- API endpoint'leri `app/api/v1/` altinda, her router ayri dosya
- Is mantigi `app/core/` altinda, her domain ayri alt dizin
- Dis servis adaptorleri `app/integrations/` altinda
- Pydantic semalari `app/schemas/` altinda
- Celery gorevleri `app/tasks/` altinda (yeni task dosyasi `celery_app.py` include listesine eklenmeli)
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
- `app/core/workspace.py` - Workspace izolasyonunun tek dogrulama noktasi

### Sik Yapilan Hatalar
- **Migration unutma**: `models.py` degisikligi sonrasi `alembic revision --autogenerate` unutuluyor
- **Sabit degisiklik etkisi**: `constants.py`'deki bir degisiklik birden fazla modulu etkiler
- **Celery task import**: Yeni task dosyasi `celery_app.py`'deki `include` listesine eklenmeli
- **Celery imza degisikligi**: Task imzasi/parametresi degisince `celery_worker` restart SART
  (once `task_results`'ta running gorev kontrolu) — eski worker yeni cagriyi patlatir
- **JSON parse hatalari**: Gemini bozuk JSON dondurebilir (fence, trailing virgul, truncation);
  yeni parse noktalarinda CIPLAK `json.loads` KULLANMA — `app/core/channel/ai_json.py`
  (`parse_ai_json_object` / `parse_ai_json_list`) kurtarma zincirinden gecir
- **Turkce karakter**: Fuzzy matching'de Turkce karakter normalizasyonu (i/I, g/G) dikkat gerektirir
- **Batch boyutu**: AI cagrilarinda batch_size=6 asildiginda JSON truncation riski artar
- **HTTPException yutma**: Endpoint'lerde genis `except Exception` bloklari `HTTPException`'i
  yutup 404/400'u 500'e cevirebilir; once `except HTTPException: raise` eklenmeli
- **Frontend HMR cache tuzagi**: Docker'da frontend dosya degisikligi sonrasi
  `docker-compose restart frontend` + tarayici DevTools ile dogrulama sart
- **pytest'i prod DB'ye karsi calistirma**: ASLA — `docker-compose.test.yml` kullan
- **Ayni anda TEK pytest kosusu**: `docker-compose.test.yml` tek bir paylasilan `test_db`
  kullanir; paralel kosular birbirinin tablolarini truncate eder → kilitlenme veya
  guvenilmez sonuc. Yeni kosudan once `docker ps --filter name=test_app-run` ile kontrol
  et. (10.09: uc eszamanli kosu 65 sn'lik paketi 300 sn'nin uzerine cikardi)
- **Test ortami `.env` okumaz**: `APP_ENV=test` iken `app/config.py` `env_file=None` kullanir
  (yerel `ENABLE_*` bayraklari testlere sizmasin). Bir test bayrak/tavan istiyorsa
  `monkeypatch.setattr(settings, ...)` ile KENDISI ayarlamalidir.
- **Sosyal brief bayraklari**: `ENABLE_SOCIAL_BRIEF_FLOW` (yeni akis; kapaliyken arayuz eski
  sihirbaza DONMEZ — frontend ile ayni deploy'da acilmali) ve `ENABLE_SOCIAL_LEGACY_BULK`
  (varsayilan kapali, `/social/bulk` 410). Brief task kodu degisince worker restart sart.
- **Legacy sosyal uclar brief verisine dokunmaz**: yeni kod eklerken `brief_id IS NULL`
  sinirini koru; brief satirlari yalniz `/social/briefs/*` akisindan yazilir (insert-only).

### Veri Akisi Ozeti
```
Workspace olustur (site profil analizi + onay) -> CSV/Google Ads Import -> Dedup ->
Scoring Run -> Skor Hesapla (v2, liste-goreli) -> Relevance (opsiyonel) -> Aday Havuz ->
Niyet Analizi (AI, SEO'da gt/ga, response_schema + retry zinciri) ->
Marka Filtresi + On-filtre (ADS sinif / SEO deterministik / SOCIAL boyut) ->
Sinif-oncelikli Final Havuz + Kapasite-odakli Expansion (backfill YOK, hard butce 60) ->
Transfer (gercek intent) -> SEO Metadata -> selection_quality ->
Icerik Uretimi (AI, grounding + claim validator; ADS versiyonlu set) ->
Compliance Check -> Export (Celery + ExportJob; ADS yalniz aktif non-stale set)
```

---

## 12. Dashboard Metrik Tanimlari

`frontend/src/pages/Dashboard.tsx` artik `GET /api/v1/dashboard/workspace-summary`
endpoint'inden beslenir (Redis cache'li, 30sn polling; bloklu islem varken 10sn).
`DashboardCockpit` dolu durumu, `DashboardEmpty` bos durumu render eder.

| Bilgi | Kaynak |
|-------|--------|
| Workspace ozet metrikleri | `GET /dashboard/workspace-summary?brand_profile_id=X` |
| Google Ads sagligi | Ayni response icinde (`google_ads_health`, 120sn Redis cache) |
| Sonraki adim onerisi | `app/core/dashboard/next_action.py` |

**Workspace secili degilse:** Bos durum (DashboardEmpty) gosterilir.

**Refresh davranisi:** "Yenile" butonu `refresh=true` ile cache bypass eder;
`activeWorkspace?.id` degisince yeniden ceker.

---

## 13. URL Seed Cache Davranis Kurallari

Kaynak: `app/api/v1/google_ads.py` — `POST /google-ads/keyword-ideas-by-url`

### Cache Key Formulu
```
gads:url_seed:{customer_id}:{workspace_token}:{md5(url)}:{language_id}:{geo_target_id}:{max_results}:{min_volume}:{seed_mode}
```

| Alan | Deger |
|------|-------|
| `customer_id` | Google Ads musterisi; request'ten veya workspace varsayilanindan |
| `workspace_token` | `ws{id}:{created_at.isoformat()}` — workspace'e ozel, farkli workspace'ler cakismaz |
| `md5(url)` | `url.strip().lower()` normalize sonrasi MD5 |
| `language_id` | Request → workspace default → env `GOOGLE_ADS_LANGUAGE_ID` zinciri; resource path prefix'i otomatik cikarilir |
| `geo_target_id` | Ayni zincir; resource path prefix'i otomatik cikarilir |
| `max_results` | Request parametresi (default 300) |
| `min_volume` | Request parametresi (default 0) |
| `seed_mode` | `include_keyword_seed=true` ise `"kwurl"`, degilse `"url"` |

### TTL ve Bypass
- **TTL:** 24 saat (`URL_SEED_CACHE_TTL_SECONDS = 86400`, `redis.setex`)
- **Force refresh:** Request body'de `refresh: true` gondermek cache okumayı atlar; Google Ads API'si yeniden cagrilir ve cache guncellenir
- **Cache hit gostergesi:** Response'da `cached: true` + `cache_key` string alaninda anahtar doner
- **Redis erisim hatasi:** Her iki yonde de (okuma/yazma) sessizce atlanir — cache calismazsa normal API akisina devam eder
- **Parametrelerden biri degisirse** (URL, dil, bolge, limitler, seed modu) farkli bir cache key olusur; onceki cache'e dokunulmaz
