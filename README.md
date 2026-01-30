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
