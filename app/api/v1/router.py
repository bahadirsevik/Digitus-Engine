"""
Main API router that includes all sub-routers.
"""
from fastapi import APIRouter

from app.api.v1.keywords import router as keywords_router
from app.api.v1.scoring import router as scoring_router
from app.api.v1.channels import router as channels_router
from app.api.v1.generation import router as generation_router
from app.api.v1.export import router as export_router
from app.api.v1.tasks import router as tasks_router
from app.api.v1.brand_profile import router as brand_profile_router


api_router = APIRouter()

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
