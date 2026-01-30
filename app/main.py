"""
FastAPI application entry point.
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from app.config import settings
from app.api.v1.router import api_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    # Startup
    print("🚀 DIGITUS ENGINE starting...")
    
    # Initialize database tables (development only)
    if settings.DEBUG:
        from app.database.connection import init_db
        init_db()
        print("📦 Database initialized")
    
    yield
    
    # Shutdown
    print("👋 DIGITUS ENGINE shutting down...")


app = FastAPI(
    title="DIGITUS ENGINE API",
    description="""
    Anahtar kelime skorlama ve kanal atama motoru.
    
    ## Özellikler
    
    * **Keyword Yönetimi**: Anahtar kelime ekleme, güncelleme, silme
    * **Skorlama**: ADS, SEO, SOCIAL kanalları için skorlama
    * **Kanal Atama**: AI destekli niyet analizi ve kanal ataması
    * **İçerik Üretimi**: Kanal bazlı içerik üretimi
    * **Export**: DOCX, PDF, Excel formatlarında dışa aktarım
    """,
    version="2.0.0",
    lifespan=lifespan
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Production'da sınırlandırılmalı
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API router
app.include_router(api_router, prefix="/api/v1")


@app.get("/", tags=["Health"])
async def root():
    """Root endpoint - health check."""
    return {
        "message": "DIGITUS ENGINE API",
        "version": "2.0.0",
        "status": "running"
    }


@app.get("/health", tags=["Health"])
async def health_check():
    """Detailed health check."""
    return {
        "status": "healthy",
        "components": {
            "api": "up",
            "database": "up" if settings.DATABASE_URL else "not configured"
        }
    }
