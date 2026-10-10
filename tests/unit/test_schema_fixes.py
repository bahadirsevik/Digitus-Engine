"""
Schema fix tests — smoke-level validation that doesn't need a live DB.
"""
from datetime import datetime


def test_brand_profile_response_scoring_run_id_optional():
    """BrandProfileResponse must accept scoring_run_id=None (nullable FK on model)."""
    from app.schemas.brand_profile import BrandProfileResponse

    now = datetime.utcnow()
    resp = BrandProfileResponse(
        id=1,
        scoring_run_id=None,
        company_url="https://example.com",
        status="draft",
        created_at=now,
        updated_at=now,
    )
    assert resp.scoring_run_id is None


def test_brand_profile_response_scoring_run_id_present():
    """BrandProfileResponse still accepts an integer scoring_run_id."""
    from app.schemas.brand_profile import BrandProfileResponse

    now = datetime.utcnow()
    resp = BrandProfileResponse(
        id=1,
        scoring_run_id=42,
        company_url="https://example.com",
        status="confirmed",
        created_at=now,
        updated_at=now,
    )
    assert resp.scoring_run_id == 42


# Pool builder capacity cap test lives in tests/integration/test_pool_builder.py
# (needs a real DB session to call PoolBuilder.build_candidate_pools())
