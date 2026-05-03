# Digitus Engine V2

AI destekli anahtar kelime analizi ve kanal atama motoru.

## Özellikler

- **Anahtar Kelime Skorlama**: ADS, SEO ve SOCIAL kanalları için otomatik skorlama
- **AI Niyet Analizi**: Gemini API ile kullanıcı niyeti analizi
- **Kanal Atama**: Skorlama ve niyet analizine göre kanal havuzları oluşturma
- **İçerik Üretimi**: AI destekli SEO/GEO içerik ve ADS metin önerileri

## Teknolojiler

### Backend
- FastAPI
- PostgreSQL
- Redis
- Celery
- Google Gemini AI

### Frontend
- React + TypeScript
- Vite

## Kurulum

### Docker ile Çalıştırma

```bash
# .env dosyasını oluşturun
cp .env.example .env

# GEMINI_API_KEY değerini ekleyin
# .env dosyasında: GEMINI_API_KEY=your-api-key

# Docker container'ları başlatın
docker-compose up -d

# Frontend'i başlatın
cd frontend
npm install
npm run dev
```

### Geliştirme Ortamı

```bash
# Python sanal ortam
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt

# Veritabanı migration
alembic upgrade head

# Backend başlatma
uvicorn app.main:app --reload

# Frontend başlatma
cd frontend
npm run dev
```

## API Endpoints

- `POST /api/v1/keywords/upload` - CSV dosyası yükleme
- `GET /api/v1/keywords` - Anahtar kelimeleri listeleme
- `POST /api/v1/scoring/runs` - Skorlama çalıştırma
- `POST /api/v1/channels/runs/{id}/assign` - Kanal ataması

## Site Fetch Smoke Test (Playwright)

Website icerigi cekme kalitesini ana pipeline'dan bagimsiz test etmek icin:

```bash
# Smoke image build
docker compose --profile smoke build site_fetch_smoke

# Company + competitors test run
docker compose --profile smoke run --rm site_fetch_smoke \
  --company-url https://example.com \
  --competitor-url https://competitor1.com \
  --competitor-url https://competitor2.com \
  --max-pages-main 5 \
  --max-pages-competitor 2 \
  --page-timeout-sec 15 \
  --out artifacts/site_fetch_report.json
```

Rapor dosyasi: `artifacts/site_fetch_report.json`

## Google Ads Lab (Urun Entegrasyonundan Tam Izole)

Google Ads API'yi urune baglamadan once izole modda test etmek icin:

```bash
# 0) local venv (varsayilan)
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# 1) env dosyasi hazirla
cp .env.google_ads_lab.example .env.google_ads_lab

# 2) keyword ideas probe
python -m labs.google_ads_lab.cli ideas-probe \
  --seeds "dijital pazarlama,seo ajansi" \
  --max-total-rows 1000

# 3) kampanya listesi
python -m labs.google_ads_lab.cli campaigns-list --date-range LAST_30_DAYS

# 4) secili kampanyalardan keyword/search term
python -m labs.google_ads_lab.cli campaign-keywords-probe \
  --campaign-ids 123456789,987654321 \
  --date-range LAST_30_DAYS

# 5) compare raporlari
python -m labs.google_ads_lab.cli compare-ideas-csv
python -m labs.google_ads_lab.cli compare-campaign-coverage
```

Varsayilan artifact klasoru: `artifacts/google_ads_lab/`
- `summary.json`
- `rows.jsonl`
- `errors.json`
- `compare_ideas_vs_csv.json`
- `campaign_coverage_vs_csv.json`

Eski komut uyumlulugu:
```bash
python scripts/run_google_ads_probe.py
```
Bu script yeni lab komutuna shim olarak yonlendirir.

## Proje Yapısı

```
├── app/
│   ├── api/v1/          # API endpoints
│   ├── core/            # Business logic
│   │   ├── channel/     # Kanal atama
│   │   └── scoring/     # Skorlama
│   ├── database/        # Veritabanı modelleri
│   ├── generators/      # İçerik üreticileri
│   └── tasks/           # Celery görevleri
├── frontend/            # React frontend
├── docker-compose.yml
└── requirements.txt
```

## Lisans

MIT
