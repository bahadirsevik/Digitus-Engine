"""
Application configuration using Pydantic Settings.
Loads settings from environment variables and .env file.
"""
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
    
    # Skorlama Katsayıları
    ADS_EPSILON: float = 0.01
    SEO_COMPETITION_WEIGHT: float = 1.0
    SOCIAL_TREND_WEIGHT: float = 3.0

    # Ads generation feature flags
    ADS_FALLBACK_ENABLED: bool = True

    # Site Analyzer Feature Flags
    ENABLE_SITE_PROFILE_ANALYSIS: bool = True
    ENABLE_RELEVANCE_RERANK: bool = True

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

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


# Global settings instance
settings = get_settings()
