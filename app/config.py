"""
Application configuration using Pydantic Settings.
Loads settings from environment variables and .env file.
"""
import math
import os
from typing import List, Optional
from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    """Application settings."""

    # App
    APP_ENV: str = "development"
    DEBUG: bool = True
    SECRET_KEY: str = "your-secret-key-change-in-production"

    # Auth (opt-in). None birakilirsa API key korumasi devre disi.
    # Production'da zorunlu: bos birakilamaz (bkz. check_production_security).
    API_KEY: Optional[str] = None

    # ── Giris (login) feature flag ──
    # LOGIN_ENABLED=false: sistem giris oncesi haliyle calisir. IPTAL YOLU
    # budur — .env'de bu degeri false yapip app konteynerini yeniden
    # baslatmak giris zorunlulugunu tamamen kaldirir (kod/veri degismez).
    # Frontend de /api/v1/auth/status ucundan bu degeri okur, boylece flag
    # uctan uca calisir.
    LOGIN_ENABLED: bool = False
    SESSION_COOKIE_NAME: str = "digitus_session"
    # Oturum TTL'i KAYAN penceredir: her istekte tazelenir, hareketsizlikte doler.
    SESSION_TTL_DAYS: int = 30
    # Kaba kuvvet yavaslatma: pencere icinde bu kadar basarisiz denemeden
    # sonra giris 429 doner.
    LOGIN_MAX_ATTEMPTS: int = 10
    LOGIN_ATTEMPT_WINDOW_MINUTES: int = 15

    # CORS allow-list. Virgul ile ayrilmis liste veya "*".
    # Production'da "*" kullanimi yasaklidir.
    CORS_ORIGINS: str = "*"
    
    # Database
    POSTGRES_USER: str = "digitus"
    POSTGRES_PASSWORD: str = "digitus_secret_123"
    POSTGRES_DB: str = "digitus_engine"
    POSTGRES_HOST: str = "db"
    POSTGRES_PORT: int = 5432
    DATABASE_URL: Optional[str] = None
    SQL_ECHO: bool = False
    
    # Redis
    REDIS_URL: str = "redis://redis:6379/0"
    
    # AI API
    GEMINI_API_KEY: Optional[str] = None
    GEMINI_MODEL: str = "gemini-3.8-flash"
    # Rakip keşfi Google Search Grounding gerektirir. Routing benchmark'i
    # tamamlanmadan Lite modellere taşınmamalıdır.
    COMPETITOR_DISCOVERY_MODEL: str = "gemini-3.8-flash"
    # Düşünme seviyesi (Gemini 3+ thinking modelleri): minimal|low|medium|high.
    # Varsayılan "low" — medium (API default) düşünme token'ları max_output_tokens
    # bütçesinin %70-95'ini yiyip yanıtı kesiyordu (run-16 ölçümü) ve $9/M çıktı
    # fiyatından faturalanıyordu. Boş string = config gönderilmez (API default'u).
    GEMINI_THINKING_LEVEL: str = "low"
    # Stage->model haritasi (plan D aktivasyonu; bos = tek model). Ornek env:
    # AI_STAGE_MODELS={"intent":"gemini-3.1-flash-lite"}
    AI_STAGE_MODELS: dict = {}
    # Tipli provider route'u (plan_v4pro 4.1). Bos ise eski davranis
    # (her sey varsayilan Gemini backend'i). Model adindan provider
    # TAHMIN EDILMEZ; bilinmeyen provider fail-closed.
    # AI_STAGE_ROUTES={"intent":{"provider":"deepseek","model":"deepseek-v4-pro"}}
    AI_STAGE_ROUTES: dict = {}
    # Docker build arg'indan gelir (runtime git YOK) — run manifest'ine yazilir
    APP_GIT_SHA: str = "dev"
    # DENEY ANAHTARI (Optimice pencere deneyi): aday havuzu pencerelerini
    # (min(POOL_SIZE, kapasite*3)) bu carpanla buyutur. URETIM DEFAULT=1;
    # degeri manifest'e yazilir, deney runlari raporda gorunur. Kalici
    # pencere degisikligi PATRON kararidir — bu anahtar yalniz olcum icin.
    CANDIDATE_POOL_MULTIPLIER: int = 1
    
    # DEPRECATED (Skorlama v2): katsayılar artık app/core/constants.py'de yaşar.
    # Alanlar yalnızca geriye uyumluluk için duruyor — mevcut .env/compose
    # dosyalarında bu değişkenler set'li olabilir ve Settings extra input'u
    # yasakladığı için alan silinirse uygulama açılmaz. Hiçbir kod okumuyor.
    ADS_EPSILON: float = 0.01
    SEO_COMPETITION_WEIGHT: float = 1.0
    SOCIAL_TREND_WEIGHT: float = 3.0

    # Ads generation feature flags
    ADS_FALLBACK_ENABLED: bool = True

    # Site Analyzer Feature Flags
    ENABLE_SITE_PROFILE_ANALYSIS: bool = True
    ENABLE_RELEVANCE_RERANK: bool = True

    # New social brief workflow. Keep false until rollout is approved.
    ENABLE_SOCIAL_BRIEF_FLOW: bool = False

    # Legacy /social/bulk gecis bayragi (K12, plan_social_brief_akisi.md §0/§8).
    # Yeni brief akisi yayina cikip kararli hale gelene kadar TRUE tutulabilir;
    # FALSE oldugunda POST /generation/social/bulk 410 Gone doner (eski
    # POST /generation/social gibi). Varsayilan FALSE: brief akisi hazir olana
    # kadar eski toplu uretim yolu kapali kabul edilir.
    ENABLE_SOCIAL_LEGACY_BULK: bool = False

    # v2.1 deney bayrağı (plan Faz E): kapalıyken istemci v2_1 istese bile
    # SESSİZCE v2'ye düşülmez — run oluşturma tipli 409 döner. Global
    # varsayılan daima v2; bayrak yalnız v2_1 SEÇİLEBİLİRLİĞİNİ açar.
    ENABLE_V21_EXPERIMENT: bool = False

    # ── Motor v3: kilitli ADS/SEO/SOCIAL algoritmaları ──────────────────
    # plan_algoritma_entegrasyonu.md K2/K10. Ürünün varsayılan üretim motoru.
    # Kapalıyken istemci v3 istese bile SESSİZCE v2'ye düşülmez — run
    # oluşturma tipli 409 döner.
    ENABLE_ENGINE_V3: bool = True
    # v3 koşusunun TEK tavan anahtarı (USD). Ledger cap otoritesi DB'dir ve
    # iki kolonda da pozitif değer ister; v3'te screening kapalı olduğu için
    # dispatch screening'e minimum temsil edilebilir pozitifi (0.000001),
    # downstream'e (HARD_CAP - 0.000001) yazar ve toplamı doğrular. Tüm v3
    # rezervasyonları `downstream` türü altındadır.
    ENGINE_V3_HARD_CAP_USD: float = 5.00

    # Politika özgüllük katmanı (`policy-specificity-v1`, karar kaydı
    # 604888df…). Varsayılan KAPALI ve TEK BAŞINA YETMEZ: workspace
    # profilinde tipli+sürümlü+onaylı opt-in şarttır. Açıkken bile şu an
    # YALNIZ SHADOW hesaplanır — canlı havuza uygulama AYRI ONAY ister.
    ENABLE_POLICY_SPECIFICITY_RESOLVER: bool = False

    # Corpus screening (plan_ai §12): Aşama 1 bake-off offline'dır ve bu
    # bayraklardan BAĞIMSIZ koşar; bayraklar üretim yolunu (shadow/assistive)
    # açar. Varsayılan KAPALI.
    ENABLE_CORPUS_SCREENING: bool = False
    # Faz 0 (UNION-2026-07-30-v3-scoped-additive) kapılarını geçen üretim
    # sözleşmesi: DeepSeek V4 Flash tarama + Gemini downstream karar.
    CORPUS_SCREENING_PROVIDER: str = "deepseek"   # gemini|deepseek
    CORPUS_SCREENING_MODEL: str = "deepseek-v4-flash"
    # Bu alan YALNIZ create UI varsayılanıdır; gerçek mode her assignment
    # attempt'ine bağlıdır ve run/attempt kararını EZMEZ (plan §6.1/§6.2).
    CORPUS_SCREENING_UI_DEFAULT_MODE: str = "off"   # off|shadow|assistive
    CORPUS_SCREENING_MAX_KEYWORDS: int = 1500
    CORPUS_SCREENING_CONCURRENCY: int = 6
    # Dağıtık (Redis lease) global inflight sınırı — process başına
    # concurrency tek başına global limit sayılmaz (plan §7.5)
    CORPUS_SCREENING_GLOBAL_INFLIGHT: int = 12
    # Sunucu tarafı MUTLAK üst sınırlar (kullanıcı bunların üstünde cap
    # onaylayamaz). Bayrak açıkken pozitif+sonlu olmaları ZORUNLUDUR.
    # Sunucu kontrollu model (plan_screening_server_controlled_revision):
    # otomatik tetikleme YALNIZ allowlist'teki workspace'lerde calisir.
    # VARSAYILAN BOS: hicbir workspace kendiliginden girmez.
    CORPUS_SCREENING_ALLOWLIST: List[int] = []
    # Allowlist'teki workspace'lerde kullanilacak mod (off = tetikleme yok)
    CORPUS_SCREENING_DEFAULT_MODE: str = "off"   # off|shadow|assistive
    # ESKI teknik UI (mod secici + onay modali) AYRI bayraktadir: yeni
    # sozlesmede kullaniciya gosterilmez, yalniz ic hata ayiklama icindir.
    CORPUS_SCREENING_TECHNICAL_UI: bool = False
    CORPUS_SCREENING_MAX_APPROVED_USD: float = 0.50
    CORPUS_DOWNSTREAM_MAX_APPROVED_USD: float = 8.00
    DEEPSEEK_API_KEY: Optional[str] = None
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com"
    # V3 motor paralelligi (plan_engine_paralellik.md). IKI AYRI ayar:
    #   ENGINE_AI_CONCURRENCY     -> TEK kosunun thread sayisi. 1 = bugunku
    #                                seri yol (varsayilan; davranis degismez).
    #   ENGINE_AI_GLOBAL_INFLIGHT -> TUM V3 kosularinin PAYLASTIGI Redis slotu.
    # Birden cok kosu global slotlari PAYLASIR; toplam saglayici hizi artmaz.
    ENGINE_AI_CONCURRENCY: int = 1
    ENGINE_AI_GLOBAL_INFLIGHT: int = 8
    # .env'de tasinan anahtar; hicbir kod okumuyor. Settings extra input'u
    # YASAKLADIGI icin alan tanimli olmazsa uygulama acilista coker.
    TYPESAFE_API_KEY: Optional[str] = None

    # ---- SERP saglayicisi (SerpApi) — YALNIZ deney tarafi.
    # Uretim akisinda (scoring/channel/generation) OKUNMAZ; yalniz
    # scripts/ altindaki olcum kosulari kullanir. Anahtar bossa SERP
    # deneyleri fail-closed durur (sessizce bos sonuc URETMEZ).
    # Sozlesme alanlari cache anahtarinin PARCASIDIR: biri degisirse
    # eski SERP cache'i yapisal olarak gecersizlesir.
    # NOT: kimlik dogrulama header DEGIL `api_key` query parametresidir;
    # ucretsiz plan AYLIK 250 arama.
    SERPAPI_API_KEY: Optional[str] = None
    SERPAPI_BASE_URL: str = "https://serpapi.com/search.json"
    SERP_GOOGLE_DOMAIN: str = "google.com.tr"
    SERP_LOCATION: str = "Turkey"
    SERP_GL: str = "tr"
    SERP_HL: str = "tr"
    SERP_DEVICE: str = "desktop"
    # Sert tavan: bu sayidan fazla SERP sorgusu YAPILMAZ.
    # Serper ucretsiz kotasi AYLIK 250 sorgu (2500 degil) — tavan bu yuzden
    # 250'de sabit. Kota AYLIK yenilenir, tek seferlik degildir.
    SERP_MAX_QUERIES: int = 250

    # Google Ads Probe / future integration envs
    GOOGLE_ADS_DEVELOPER_TOKEN: Optional[str] = None
    GOOGLE_ADS_CLIENT_ID: Optional[str] = None
    GOOGLE_ADS_CLIENT_SECRET: Optional[str] = None
    GOOGLE_ADS_REFRESH_TOKEN: Optional[str] = None
    GOOGLE_ADS_LOGIN_CUSTOMER_ID: Optional[str] = None
    GOOGLE_ADS_CUSTOMER_ID: Optional[str] = None
    GOOGLE_ADS_LANGUAGE_ID: str = "1037"
    GOOGLE_ADS_GEO_TARGET_ID: str = "2792"
    GOOGLE_ADS_PROBE_PAGE_SIZE: int = 1000
    GOOGLE_ADS_PROBE_MAX_RESULTS: int = 300
    GOOGLE_ADS_PROBE_SEEDS: str = ""
    
    @property
    def database_url(self) -> str:
        """Get the database URL, constructing it if not provided."""
        if self.DATABASE_URL:
            return self.DATABASE_URL
        return f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"

    @property
    def cors_origins_list(self) -> List[str]:
        raw = (self.CORS_ORIGINS or "").strip()
        if raw == "*":
            return ["*"]
        return [o.strip() for o in raw.split(",") if o.strip()]

    @property
    def auth_enabled(self) -> bool:
        """API key korumasi aktif mi? API_KEY env set ise aktif."""
        return bool(self.API_KEY)

    @model_validator(mode='after')
    def check_production_security(self) -> 'Settings':
        if self.APP_ENV == "production":
            if self.SECRET_KEY == "your-secret-key-change-in-production":
                raise ValueError("Insecure SECRET_KEY usage in production environment!")
            if self.POSTGRES_PASSWORD == "digitus_secret_123":
                raise ValueError("Insecure POSTGRES_PASSWORD usage in production environment!")
            if not self.API_KEY:
                raise ValueError(
                    "API_KEY zorunludur (production). Feature-flag'i aktive etmek icin "
                    "ortamda API_KEY=<rastgele-64-hex> ayarlayin."
                )
            if self.CORS_ORIGINS.strip() == "*":
                raise ValueError(
                    "CORS_ORIGINS='*' production'da yasaktir. Virgul ile ayrilmis "
                    "whitelist kullanin (ornek: https://app.example.com)."
                )
        return self

    @model_validator(mode='after')
    def check_engine_v3_hard_cap(self) -> 'Settings':
        """v3 tavani POZITIF ve SONLU olmalidir — fail-closed.

        Ledger cap otoritesi DB'dir ve iki kolonda da pozitif deger ister;
        dispatch screening'e minimum temsil edilebilir pozitifi (0.000001),
        downstream'e (HARD_CAP - 0.000001) yazar. Tavan bu farktan kucuk
        veya esitse downstream payi pozitif kalamaz, NaN/inf ise butun
        hard-cap aritmetigi anlamsizlasir: ikisi de burada durdurulur.
        """
        import math

        cap = self.ENGINE_V3_HARD_CAP_USD
        if not isinstance(cap, (int, float)) or isinstance(cap, bool):
            raise ValueError("ENGINE_V3_HARD_CAP_USD sayi olmalidir")
        if not math.isfinite(float(cap)):
            raise ValueError(
                f"ENGINE_V3_HARD_CAP_USD sonlu olmalidir: {cap!r}")
        if float(cap) <= 0:
            raise ValueError(
                f"ENGINE_V3_HARD_CAP_USD pozitif olmalidir: {cap!r}")
        # screening payi 0.000001 ayrildiktan sonra downstream'e POZITIF
        # deger kalmali (ledger <= 0 cap'i fail-closed reddeder).
        if float(cap) <= 0.000001:
            raise ValueError(
                "ENGINE_V3_HARD_CAP_USD 0.000001'den buyuk olmalidir "
                f"(screening payi ayrildiktan sonra downstream'e pozitif "
                f"deger kalmiyor): {cap!r}")
        return self

    @model_validator(mode='after')
    def check_corpus_screening_caps(self) -> 'Settings':
        """Bayrak aciksa iki yonetici maliyet ust siniri POZITIF+SONLU
        olmalidir (plan §6.2). Aksi halde kullanici sinirsiz cap
        onaylayabilir; uygulama fail-closed baslar."""
        if not self.ENABLE_CORPUS_SCREENING:
            return self
        for name in ("CORPUS_SCREENING_MAX_APPROVED_USD",
                     "CORPUS_DOWNSTREAM_MAX_APPROVED_USD"):
            value = getattr(self, name)
            if value is None or not math.isfinite(float(value)) \
                    or float(value) <= 0:
                raise ValueError(
                    f"{name} pozitif ve sonlu olmalidir "
                    f"(ENABLE_CORPUS_SCREENING=true iken zorunlu); "
                    f"verilen: {value!r}"
                )
        if self.CORPUS_SCREENING_MAX_KEYWORDS < 1:
            raise ValueError(
                "CORPUS_SCREENING_MAX_KEYWORDS >= 1 olmalidir"
            )
        if self.CORPUS_SCREENING_GLOBAL_INFLIGHT < 1:
            raise ValueError(
                "CORPUS_SCREENING_GLOBAL_INFLIGHT >= 1 olmalidir"
            )
        if self.CORPUS_SCREENING_CONCURRENCY < 1:
            raise ValueError(
                "CORPUS_SCREENING_CONCURRENCY >= 1 olmalidir"
            )
        if self.CORPUS_SCREENING_DEFAULT_MODE not in (
                "off", "shadow", "assistive"):
            raise ValueError(
                "CORPUS_SCREENING_DEFAULT_MODE off|shadow|assistive "
                f"olmalidir; verilen: {self.CORPUS_SCREENING_DEFAULT_MODE!r}"
            )
        for _ws_id in (self.CORPUS_SCREENING_ALLOWLIST or []):
            if not isinstance(_ws_id, int) or isinstance(_ws_id, bool) \
                    or _ws_id <= 0:
                raise ValueError(
                    "CORPUS_SCREENING_ALLOWLIST yalnizca pozitif workspace "
                    f"id'leri icerebilir; verilen: {_ws_id!r}"
                )
        from app.core.screening.auto_trigger import (
            CANDIDATE_CANARY_MAX_WORKSPACES,
        )
        if (
            self.CORPUS_SCREENING_DEFAULT_MODE == "assistive"
            and len(set(self.CORPUS_SCREENING_ALLOWLIST or []))
            > CANDIDATE_CANARY_MAX_WORKSPACES
        ):
            raise ValueError(
                "assistive canary en fazla "
                f"{CANDIDATE_CANARY_MAX_WORKSPACES} workspace allowlist'i "
                "kabul eder; genel rollout ayrica onaylanmadan kapsam "
                "genisletilemez"
            )
        if (self.CORPUS_SCREENING_DEFAULT_MODE != "off"
                and not self.CORPUS_SCREENING_ALLOWLIST):
            raise ValueError(
                "CORPUS_SCREENING_DEFAULT_MODE 'off' degilse "
                "CORPUS_SCREENING_ALLOWLIST BOS OLAMAZ (otomatik tetikleme "
                "tum workspace'lerde acilamaz)"
            )
        if self.CORPUS_SCREENING_UI_DEFAULT_MODE not in (
                "off", "shadow", "assistive"):
            raise ValueError(
                "CORPUS_SCREENING_UI_DEFAULT_MODE off|shadow|assistive "
                f"olmalidir; verilen: "
                f"{self.CORPUS_SCREENING_UI_DEFAULT_MODE!r}"
            )
        if self.CORPUS_SCREENING_PROVIDER not in ("gemini", "deepseek"):
            raise ValueError(
                "CORPUS_SCREENING_PROVIDER gemini|deepseek olmalidir; "
                f"verilen: {self.CORPUS_SCREENING_PROVIDER!r}"
            )
        if not (self.CORPUS_SCREENING_MODEL or "").strip():
            raise ValueError(
                "CORPUS_SCREENING_MODEL bos olamaz (model adindan "
                "provider TAHMIN EDILMEZ)"
            )
        return self

    class Config:
        # Test ortaminda .env OKUNMAZ: docker-compose.test.yml repo'yu
        # test_app'e bind-mount eder ve APP_ENV=test set eder; gelistiricinin
        # lokal .env dosyasindaki (ornek: ENABLE_SOCIAL_BRIEF_FLOW=true)
        # degerleri sessizce test surecine sizip flag-off testlerini bozar.
        # Production/development davranisi degismez — .env orada hala okunur.
        env_file = None if os.environ.get("APP_ENV") == "test" else ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


# Global settings instance
settings = get_settings()
