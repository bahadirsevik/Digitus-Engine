# Digitus Engine V2 — Production Dağıtımı

Bu dal yalnız sunucuda çalışan uygulamayı içerir (API, Celery worker/beat,
frontend, Alembic migration'ları). Geliştirme ortamı, testler ve deney
dosyaları bu dalda yoktur. Sunucuda **yalnız `docker-compose.prod.yml`**
kullanılır.

## Servisler

| Servis | Açıklama |
|---|---|
| `app` | FastAPI (uvicorn, 2 worker). Açılışta `alembic upgrade head` çalışır. Dışarıya port açmaz. |
| `celery_worker` | Varsayılan Celery kuyruğu |
| `celery_screening_worker` | `corpus_screening` kuyruğu (concurrency 1) |
| `celery_beat` | Zamanlayıcı |
| `db` | PostgreSQL 15 (kalıcı volume `postgres_data`) |
| `redis` | Redis 7 (kalıcı volume `redis_data`) |
| `frontend` | nginx; statik arayüz + `/api` proxy'si. Yalnız `127.0.0.1:9005`'te dinler. |

## 1. `.env` dosyası

`.env` git'e ve Docker imajına girmez; yalnız sunucuda, repo kökünde durur.
Şablon: `.env.example`.

```bash
cp .env.example .env
chmod 600 .env
```

Uygulama `APP_ENV=production` iken aşağıdakiler eksik veya güvensizse
**açılmayı reddeder**:

| Değişken | Kural |
|---|---|
| `APP_ENV` | `production` |
| `DEBUG` | `false` |
| `SECRET_KEY` | Varsayılan değer olamaz; uzun rastgele değer (ör. `openssl rand -hex 32`) |
| `API_KEY` | Zorunlu; tüm `/api/v1/*` uçları `X-API-Key` ister. Frontend nginx'i bu değeri proxy'de ekler. |
| `CORS_ORIGINS` | `*` yasak; virgülle ayrılmış whitelist (ör. `https://app.ornek.com`) |
| `POSTGRES_PASSWORD` | Güçlü, varsayılandan farklı bir parola |

Ayrıca gerekenler:

| Değişken | Değer |
|---|---|
| `POSTGRES_USER`, `POSTGRES_DB` | Veritabanı kullanıcı/adı (`DATABASE_URL` compose'da bunlardan kurulur) |
| `POSTGRES_HOST` | `db` |
| `REDIS_URL` | `redis://redis:6379/0` |
| `GEMINI_API_KEY` | Gemini anahtarı |
| `GEMINI_MODEL` | `gemini-3.8-flash` |
| `ENABLE_SOCIAL_BRIEF_FLOW` | `true` — bu dağıtımdaki arayüz yeni sosyal brief akışını kullanır; kapalıyken eski sihirbaza dönmez |
| `GOOGLE_ADS_*` | Google Ads entegrasyonu kullanılacaksa |
| `CORS_ORIGINS` | Subdomain'in tam adresi, ör. `https://digitus.ornek.com` |
| `FRONTEND_BIND_ADDR` / `FRONTEND_PORT` | İsteğe bağlı; varsayılan `127.0.0.1` / `9005` |

Gizli değerleri loglara, issue'lara veya sohbetlere yapıştırmayın.

## Subdomain ve erişim koruması

Uygulamanın kendi kullanıcı girişi yoktur ve frontend nginx'i her `/api`
isteğine `API_KEY`'i kendisi ekler. Bu yüzden siteye erişen herkes API'yi
tam yetkiyle kullanabilir; **erişim koruması (kullanıcı adı/şifre) sunucudaki
reverse proxy'de zorunludur.**

- Frontend yalnız `127.0.0.1:9005`'te dinler; dışarıdan doğrudan erişilemez.
  Subdomain'i (HTTPS + giriş) sunucudaki reverse proxy bu adrese yönlendirir.
- Reverse proxy aynı makinede değilse veya konteyner içinde çalışıyorsa
  `FRONTEND_BIND_ADDR`'ı ona göre ayarlayın; `0.0.0.0` yapılırsa port 9005
  girişi atlayarak dışarıdan açılır (güvenlik duvarıyla kapatılmalı).
- Reverse proxy'de de aynı sınırlar olmalı: istek gövdesi ≥ 12 MB
  (CSV importu), okuma zaman aşımı ≥ 300 sn (senkron AI uçları).
- Export dosyaları `exports_data` volume'unda tutulur (worker yazar, app sunar).

## 2. Build ve başlatma

```bash
# Imaja girecek surum bilgisi (.git imaja girmez; manifest SHA'si bu arg'dan gelir)
export APP_GIT_SHA=$(git rev-parse --short=12 HEAD)

docker compose -f docker-compose.prod.yml build
docker compose -f docker-compose.prod.yml up -d
```

Migration'lar `app` servisi açılırken otomatik uygulanır.

## 3. Doğrulama

```bash
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs --tail=50 app celery_worker

# nginx uzerinden saglik kontrolu
curl -s http://localhost:9005/health
```

Beklenen: `{"status":"healthy","components":{"api":"up","database":"up"}}`.

## 4. Güncelleme

```bash
git pull
export APP_GIT_SHA=$(git rev-parse --short=12 HEAD)
docker compose -f docker-compose.prod.yml build
docker compose -f docker-compose.prod.yml up -d
```

- Worker'ları yeniden başlatmadan önce çalışan görev olmadığını kontrol edin;
  restart yarım kalan Celery görevlerini öldürür.
- `.env` değişikliği `restart` ile işlenmez; `up -d` ile konteynerler yeniden
  oluşturulmalıdır.
- Celery görev imzası değişen sürümlerde worker'lar yeniden oluşturulmalıdır.

## Uyarılar

- Sunucuda `pytest` çalıştırmayın: test fixture'ları tabloları truncate eder.
  Testler geliştirme reposunda, izole test veritabanıyla koşulur.
- Bu dalda geliştirme compose'u yoktur; `docker compose up -d` (dosya
  belirtmeden) burada çalışmaz ve kullanılmamalıdır.
