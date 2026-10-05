"""
Main API router that includes all sub-routers.
"""
from fastapi import APIRouter, Depends

from app.api.v1.keywords import router as keywords_router
from app.api.v1.scoring import router as scoring_router
from app.api.v1.channels import router as channels_router
from app.api.v1.generation import router as generation_router
from app.api.v1.export import router as export_router
from app.api.v1.tasks import router as tasks_router
from app.api.v1.brand_profile import router as brand_profile_router
from app.api.v1.google_ads import router as google_ads_router
from app.api.v1.dashboard import router as dashboard_router
from app.core.security import verify_api_key
from app.core.login import require_login


# verify_api_key feature-flag'li: API_KEY set degilse no-op.
# Set ise tum /api/v1/* endpoint'leri X-API-Key header ister.
#
# require_login de feature-flag'li (LOGIN_ENABLED): set degilse no-op, set ise
# tum /api/v1/* uclari gecerli oturum cookie'si ister. IKI KAPI BAGIMSIZDIR —
# API key gecerli olsa bile giris kapisini ACMAZ (bkz. app/core/login.py).
# Auth uclarinin kendisi bu router'a BAGLI DEGILDIR (main.py'de ayri monte
# edilir), aksi halde giris ucu giris isterdi.
api_router = APIRouter(dependencies=[Depends(verify_api_key), Depends(require_login)])

# Include all sub-routers
api_router.include_router(
    keywords_router,
    prefix="/keywords",
    tags=["Keywords"]
)

api_router.include_router(
    scoring_router,
    prefix="/scoring",
    tags=["Scoring"]
)

api_router.include_router(
    channels_router,
    prefix="/channels",
    tags=["Channels"]
)

api_router.include_router(
    generation_router,
    prefix="/generation",
    tags=["Content Generation"]
)

api_router.include_router(
    export_router,
    prefix="/export",
    tags=["Export"]
)

api_router.include_router(
    tasks_router,
    prefix="/tasks",
    tags=["Tasks"]
)

api_router.include_router(
    brand_profile_router,
    prefix="/brand-profile",
    tags=["Brand Profile"]
)

api_router.include_router(
    google_ads_router,
    prefix="/google-ads",
    tags=["Google Ads"]
)

api_router.include_router(
    dashboard_router,
    prefix="/dashboard",
    tags=["Dashboard"]
)
