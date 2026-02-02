"""
FastAPI application entry point.
"""
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from loguru import logger

from app.config import settings
from app.api.v1.router import api_router
from app.core.logging_config import setup_logging


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    # Startup
    setup_logging()
    logger.info("🚀 DIGITUS ENGINE starting...")
    
    # Initialize database tables (development only)
    if settings.DEBUG:
        from app.database.connection import init_db
        init_db()
        logger.info("📦 Database initialized")
    
    yield
    
    # Shutdown
    logger.info("👋 DIGITUS ENGINE shutting down...")


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

# Global Exception Handler
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """
    Catch-all exception handler to log all unhandled exceptions with context.
    """
    logger.bind(
        url=str(request.url),
        method=request.method,
        client=request.client.host if request.client else "unknown"
    ).exception("Unhandled exception occurred")

    return JSONResponse(
        status_code=500,
        content={"message": "Internal Server Error", "detail": str(exc) if settings.DEBUG else "Unexpected error"},
    )

# Include API router
app.include_router(api_router, prefix="/api/v1")


@app.get("/", tags=["Health"])
async def root():
    """Root endpoint - health check."""
    logger.info("Root endpoint called")
    return {
        "message": "DIGITUS ENGINE API",
        "version": "2.0.0",
        "status": "running"
    }


@app.get("/health", tags=["Health"])
async def health_check():
    """Detailed health check."""
    logger.debug("Health check called")
    return {
        "status": "healthy",
        "components": {
            "api": "up",
            "database": "up" if settings.DATABASE_URL else "not configured"
        }
    }
