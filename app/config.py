"""
Application configuration using Pydantic Settings.
Loads settings from environment variables and .env file.
"""
import os
from typing import Optional
from pydantic import model_validator
from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    """Application settings."""
    
    # App
    APP_ENV: str = "development"
    DEBUG: bool = True
    SECRET_KEY: str = "your-secret-key-change-in-production"
    
    # Database
    POSTGRES_USER: str = "digitus"
    POSTGRES_PASSWORD: str = "digitus_secret_123"
    POSTGRES_DB: str = "digitus_engine"
    POSTGRES_HOST: str = "db"
    POSTGRES_PORT: int = 5432
    DATABASE_URL: Optional[str] = None
    
    # Redis
    REDIS_URL: str = "redis://redis:6379/0"
    
    # AI API
    GEMINI_API_KEY: Optional[str] = None
    
    # Skorlama Katsayıları
    ADS_EPSILON: float = 0.01
    SEO_COMPETITION_WEIGHT: float = 1.0
    SOCIAL_TREND_WEIGHT: float = 3.0
    
    @property
    def database_url(self) -> str:
        """Get the database URL, constructing it if not provided."""
        if self.DATABASE_URL:
            return self.DATABASE_URL
        return f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
    
    @model_validator(mode='after')
    def check_production_security(self) -> 'Settings':
        if self.APP_ENV == "production":
            if self.SECRET_KEY == "your-secret-key-change-in-production":
                raise ValueError("Insecure SECRET_KEY usage in production environment!")
            if self.POSTGRES_PASSWORD == "digitus_secret_123":
                raise ValueError("Insecure POSTGRES_PASSWORD usage in production environment!")
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
