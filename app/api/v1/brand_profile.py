"""
Brand Profile API endpoints.
Site analysis, profile management, and relevance scoring.
"""
import logging
from typing import List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_ai
from app.generators.ai_service import AIService
from app.config import settings
from app.database.models import (
    ScoringRun, BrandProfile, KeywordRelevance, Keyword, KeywordScore
)
from app.schemas.brand_profile import (
    ProfileAnalyzeRequest, ProfileConfirmRequest,
    BrandProfileResponse, KeywordRelevanceResponse, RelevanceComputeResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter()


LOCKED_PROFILE_FIELDS = {"company_name", "sector", "target_audience"}
EDITABLE_LIST_FIELDS = {
    "products",
    "services",
    "use_cases",
    "problems_solved",
    "brand_terms",
    "exclude_themes",
}


def _normalize_list_items(value: Any) -> List[str]:
    """Normalize list-like input while preserving commas inside items."""
    raw_items: List[str] = []
    if value is None:
        raw_items = []
    elif isinstance(value, list):
        raw_items = [str(item).strip() for item in value if str(item).strip()]
    elif isinstance(value, str):
        # Newline-only split. Commas are treated as part of the item text.
        raw_items = [line.strip() for line in value.splitlines() if line.strip()]
    else:
        raw_items = [str(value).strip()] if str(value).strip() else []

    seen = set()
    normalized: List[str] = []
    for item in raw_items:
        key = item.casefold()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(item)
    return normalized


def _generate_anchor_texts(profile: Dict[str, Any]) -> List[str]:
    """Generate anchor_texts from profile fields."""
    anchors: List[str] = []

    products = _normalize_list_items(profile.get("products", []))
    if products:
        anchors.append(" ".join(products))

    use_cases = _normalize_list_items(profile.get("use_cases", []))
    if use_cases:
        anchors.append(" ".join(use_cases))

    problems = _normalize_list_items(profile.get("problems_solved", []))
    if problems:
        anchors.append(" ".join(problems))

    brand_terms = _normalize_list_items(profile.get("brand_terms", []))
    if brand_terms:
        anchors.append(" ".join(brand_terms))

    sector = str(profile.get("sector", "") or "").strip()
    audience = str(profile.get("target_audience", "") or "").strip()
    if sector or audience:
        anchors.append(f"{sector} {audience}".strip())

    return anchors


def _sanitize_profile_data(
    incoming: Dict[str, Any],
    existing: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Protect locked fields, normalize editable lists, and handle anchor override.
    """
    sanitized: Dict[str, Any] = dict(existing or {})
    incoming = incoming or {}

    # Locked fields always stay as-is from existing profile.
    for field in LOCKED_PROFILE_FIELDS:
        if field in existing:
            sanitized[field] = existing[field]

    # Editable list fields are normalized with newline/list semantics.
    for field in EDITABLE_LIST_FIELDS:
        if field in incoming:
            sanitized[field] = _normalize_list_items(incoming.get(field))
        else:
            sanitized[field] = _normalize_list_items(existing.get(field, []))

    # Anchor behavior:
    # - payload includes anchor_texts: accept override (normalized), fallback to generated if empty
    # - payload omits anchor_texts: always regenerate
    if "anchor_texts" in incoming:
        overridden = _normalize_list_items(incoming.get("anchor_texts"))
        sanitized["anchor_texts"] = overridden or _generate_anchor_texts(sanitized)
    else:
        sanitized["anchor_texts"] = _generate_anchor_texts(sanitized)

    return sanitized


@router.get(
    "/runs/{run_id}/profile",
    response_model=BrandProfileResponse,
)
def get_profile(run_id: int, db: Session = Depends(get_db)):
    """Get the brand profile for a scoring run."""
    profile = db.query(BrandProfile).filter(
        BrandProfile.scoring_run_id == run_id
    ).first()

    if not profile:
        raise HTTPException(status_code=404, detail="Bu run için profil bulunamadı")

    return BrandProfileResponse.model_validate(profile)


@router.post(
    "/runs/{run_id}/profile/analyze",
    response_model=BrandProfileResponse,
)
def analyze_profile(
    run_id: int,
    request: ProfileAnalyzeRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    ai: AIService = Depends(get_ai),
):
    """
    Start site profile analysis (async).
    Crawls the company website, extracts brand profile, and optionally validates
    against competitor sites.
    """
    if not settings.ENABLE_SITE_PROFILE_ANALYSIS:
        raise HTTPException(
            status_code=400,
            detail="Site profil analizi devre dışı (ENABLE_SITE_PROFILE_ANALYSIS=false)"
        )

    scoring_run = db.query(ScoringRun).filter(ScoringRun.id == run_id).first()
    if not scoring_run:
        raise HTTPException(status_code=404, detail="Scoring run bulunamadı")

    # Upsert: varsa güncelle, yoksa oluştur
    profile = db.query(BrandProfile).filter(
        BrandProfile.scoring_run_id == run_id
    ).first()

    if not profile:
        profile = BrandProfile(
            scoring_run_id=run_id,
            company_url=request.company_url,
            competitor_urls=request.competitor_urls,
            status="pending",
        )
        db.add(profile)
    else:
        profile.company_url = request.company_url
        profile.competitor_urls = request.competitor_urls
        profile.status = "pending"
        profile.error_message = None

    # Update ScoringRun URL fields
    scoring_run.company_url = request.company_url
    scoring_run.competitor_urls = request.competitor_urls

    db.commit()
    db.refresh(profile)

    # Run analysis in background
    background_tasks.add_task(
        _run_profile_analysis,
        profile_id=profile.id,
        company_url=request.company_url,
        competitor_urls=request.competitor_urls,
    )

    return BrandProfileResponse.model_validate(profile)


@router.put(
    "/runs/{run_id}/profile/confirm",
    response_model=BrandProfileResponse,
)
def confirm_profile(
    run_id: int,
    request: ProfileConfirmRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """
    Confirm or edit a draft profile.
    Only confirmed profiles are used for relevance scoring.
    Automatically triggers relevance computation in the background.
    """
    profile = db.query(BrandProfile).filter(
        BrandProfile.scoring_run_id == run_id
    ).first()

    if not profile:
        raise HTTPException(status_code=404, detail="Profil bulunamadı")

    if profile.status not in ("draft", "confirmed"):
        raise HTTPException(
            status_code=400,
            detail=f"Profil durumu '{profile.status}', onaylanamaz (draft veya confirmed olmalı)"
        )

    existing_data = profile.profile_data if isinstance(profile.profile_data, dict) else {}
    incoming_data = request.profile_data if isinstance(request.profile_data, dict) else {}
    profile.profile_data = _sanitize_profile_data(incoming_data, existing_data)

    profile.status = "confirmed"
    db.commit()
    db.refresh(profile)

    # Auto-trigger relevance computation in the background
    if settings.ENABLE_RELEVANCE_RERANK:
        anchor_texts = (profile.profile_data or {}).get("anchor_texts", [])
        if anchor_texts:
            background_tasks.add_task(
                _run_relevance_computation,
                scoring_run_id=run_id,
            )
            logger.info(f"Auto-triggered relevance computation for run_id={run_id}")

    return BrandProfileResponse.model_validate(profile)


@router.post(
    "/runs/{run_id}/relevance/compute",
    response_model=RelevanceComputeResponse,
)
def compute_relevance(
    run_id: int,
    db: Session = Depends(get_db),
):
    """
    Compute keyword relevance scores using confirmed brand profile.
    Must be called after profile is confirmed and scoring is done.
    """
    if not settings.ENABLE_RELEVANCE_RERANK:
        raise HTTPException(
            status_code=400,
            detail="Relevance rerank devre dışı (ENABLE_RELEVANCE_RERANK=false)"
        )

    profile = db.query(BrandProfile).filter(
        BrandProfile.scoring_run_id == run_id,
        BrandProfile.status == "confirmed",
    ).first()

    if not profile:
        raise HTTPException(
            status_code=400,
            detail="Onaylanmış profil bulunamadı. Önce profili onaylayın."
        )

    anchor_texts = (profile.profile_data or {}).get("anchor_texts", [])
    if not anchor_texts:
        raise HTTPException(
            status_code=400,
            detail="Profilde anchor text bulunamadı."
        )

    # Get all keywords for this run
    keyword_scores = (
        db.query(KeywordScore, Keyword)
        .join(Keyword, KeywordScore.keyword_id == Keyword.id)
        .filter(KeywordScore.scoring_run_id == run_id)
        .all()
    )

    if not keyword_scores:
        raise HTTPException(status_code=404, detail="Bu run için keyword skoru bulunamadı")

    # Compute relevance
    from app.core.site_analyzer.relevance_scorer import RelevanceScorer

    scorer = RelevanceScorer(api_key=settings.GEMINI_API_KEY)
    keywords = [kw.keyword for _, kw in keyword_scores]
    keyword_id_map = {kw.keyword: kw.id for _, kw in keyword_scores}

    relevance_results = scorer.compute_relevance(keywords, anchor_texts)

    # Clear old relevance scores
    db.query(KeywordRelevance).filter(
        KeywordRelevance.scoring_run_id == run_id
    ).delete(synchronize_session=False)
    db.flush()

    # Save results (batch insert, not N+1)
    computed = 0
    failed = 0
    total_relevance = 0.0

    for result in relevance_results:
        kw_text = result["keyword"]
        kw_id = keyword_id_map.get(kw_text)
        if not kw_id:
            failed += 1
            continue

        db.add(KeywordRelevance(
            scoring_run_id=run_id,
            keyword_id=kw_id,
            relevance_score=result["relevance_score"],
            matched_anchor=result.get("matched_anchor", ""),
            method=result.get("method", "embedding"),
        ))
        total_relevance += result["relevance_score"]
        computed += 1

    db.commit()

    avg_relevance = total_relevance / computed if computed > 0 else 0.0

    return RelevanceComputeResponse(
        scoring_run_id=run_id,
        total_keywords=len(keyword_scores),
        computed=computed,
        failed=failed,
        average_relevance=round(avg_relevance, 3),
    )


@router.get(
    "/runs/{run_id}/relevance",
    response_model=List[KeywordRelevanceResponse],
)
def get_relevance_scores(
    run_id: int,
    min_score: float = 0.0,
    db: Session = Depends(get_db),
):
    """Get keyword relevance scores for a run."""
    results = (
        db.query(KeywordRelevance, Keyword)
        .join(Keyword, KeywordRelevance.keyword_id == Keyword.id)
        .filter(KeywordRelevance.scoring_run_id == run_id)
        .filter(KeywordRelevance.relevance_score >= min_score)
        .order_by(KeywordRelevance.relevance_score.desc())
        .all()
    )

    return [
        KeywordRelevanceResponse(
            keyword_id=kr.keyword_id,
            keyword=kw.keyword,
            relevance_score=float(kr.relevance_score),
            matched_anchor=kr.matched_anchor,
            method=kr.method,
        )
        for kr, kw in results
    ]


# ==================== Background task function ====================

def _run_profile_analysis(
    profile_id: int,
    company_url: str,
    competitor_urls: list = None,
):
    """Background task: crawl site, extract profile, validate with competitors."""
    from app.database.connection import SessionLocal
    from app.generators.ai_service import get_ai_service
    from app.core.site_analyzer.profile_extractor import ProfileExtractor

    db = SessionLocal()
    try:
        profile = db.query(BrandProfile).filter(BrandProfile.id == profile_id).first()
        if not profile:
            return

        profile.status = "running"
        db.commit()

        ai = get_ai_service(api_key=settings.GEMINI_API_KEY)
        extractor = ProfileExtractor(ai)

        # Extract profile
        result = extractor.extract_profile(company_url)

        if result["error"] and not result["profile"]:
            profile.status = "failed"
            profile.error_message = result["error"]
            db.commit()
            return

        profile.profile_data = result["profile"]
        profile.source_pages = result["source_pages"]

        # Validate with competitors if provided
        if competitor_urls and result["profile"]:
            validation = extractor.validate_with_competitors(
                result["profile"], competitor_urls
            )
            profile.validation_data = validation

        profile.status = "draft"
        profile.error_message = None
        db.commit()

        logger.info(f"Profile analysis completed for profile_id={profile_id}")

    except Exception as e:
        logger.error(f"Profile analysis failed for profile_id={profile_id}: {e}")
        try:
            profile = db.query(BrandProfile).filter(
                BrandProfile.id == profile_id
            ).first()
            if profile:
                profile.status = "failed"
                profile.error_message = str(e)
                db.commit()
        except Exception:
            pass
    finally:
        db.close()


def _run_relevance_computation(scoring_run_id: int):
    """
    Background task: compute keyword-brand relevance scores.
    Auto-triggered after profile confirmation.
    Fail-open: errors are logged but don't break the pipeline.
    """
    from app.database.connection import SessionLocal
    from app.core.site_analyzer.relevance_scorer import RelevanceScorer

    db = SessionLocal()
    try:
        profile = db.query(BrandProfile).filter(
            BrandProfile.scoring_run_id == scoring_run_id,
            BrandProfile.status == "confirmed",
        ).first()

        if not profile:
            logger.warning(f"Relevance compute skipped: no confirmed profile for run {scoring_run_id}")
            return

        anchor_texts = (profile.profile_data or {}).get("anchor_texts", [])
        if not anchor_texts:
            logger.warning(f"Relevance compute skipped: no anchor texts for run {scoring_run_id}")
            return

        # Get all keywords for this run
        keyword_scores = (
            db.query(KeywordScore, Keyword)
            .join(Keyword, KeywordScore.keyword_id == Keyword.id)
            .filter(KeywordScore.scoring_run_id == scoring_run_id)
            .all()
        )

        if not keyword_scores:
            logger.warning(f"Relevance compute skipped: no keyword scores for run {scoring_run_id}")
            return

        scorer = RelevanceScorer(api_key=settings.GEMINI_API_KEY)
        keywords = [kw.keyword for _, kw in keyword_scores]
        keyword_id_map = {kw.keyword: kw.id for _, kw in keyword_scores}

        relevance_results = scorer.compute_relevance(keywords, anchor_texts)

        # Clear old scores
        db.query(KeywordRelevance).filter(
            KeywordRelevance.scoring_run_id == scoring_run_id
        ).delete(synchronize_session=False)
        db.flush()

        computed = 0
        for result in relevance_results:
            kw_id = keyword_id_map.get(result["keyword"])
            if not kw_id:
                continue

            db.add(KeywordRelevance(
                scoring_run_id=scoring_run_id,
                keyword_id=kw_id,
                relevance_score=result["relevance_score"],
                matched_anchor=result.get("matched_anchor", ""),
                method=result.get("method", "embedding"),
            ))
            computed += 1

        db.commit()
        logger.info(
            f"Auto relevance computation completed for run {scoring_run_id}: "
            f"{computed}/{len(keywords)} keywords scored"
        )

    except Exception as e:
        logger.error(f"Auto relevance computation failed for run {scoring_run_id}: {e}")
        # Fail-open: pipeline continues without relevance, using default scores
    finally:
        db.close()
