"""
Brand Profile API endpoints.
Site analysis, profile management, and relevance scoring.
"""
import logging
from datetime import datetime
from typing import List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_ai
from app.generators.ai_service import AIService
from app.config import settings
from app.core.error_responses import safe_500_detail
from app.database.models import (
    ScoringRun, BrandProfile, KeywordRelevance, Keyword, KeywordScore,
    WorkspaceKeyword,
)
from app.schemas.brand_profile import (
    ProfileAnalyzeRequest, ProfileConfirmRequest,
    BrandProfileResponse, KeywordRelevanceResponse, RelevanceComputeResponse,
    WorkspaceCreateRequest, WorkspaceResponse, WorkspaceListResponse,
    WorkspaceKeywordRefreshRequest, WorkspaceKeywordRefreshResponse,
)
from app.core.workspace import verify_workspace
from app.core.scoring.state_machine import transition, transition_atomic
from app.core.keyword_normalize import normalize_keyword
from app.core.workspace_refresh import (
    ExistingWorkspaceKeyword,
    build_refreshed_workspace_keywords,
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

    # Yeni mimari: ScoringRun.brand_profile_id üzerinden; eski 1:1 fallback
    scoring_run = db.query(ScoringRun).filter(ScoringRun.id == run_id).first()
    profile = None

    if scoring_run and scoring_run.brand_profile_id:
        profile = db.query(BrandProfile).filter(
            BrandProfile.id == scoring_run.brand_profile_id,
            BrandProfile.status == "confirmed",
            BrandProfile.deleted_at.is_(None),
        ).first()

    if not profile:
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
    db.commit()  # Commit the delete before inserting new records

    # Deduplicate results by keyword_id (keep highest score per keyword)
    deduped: dict = {}
    for result in relevance_results:
        kw_id = keyword_id_map.get(result["keyword"])
        if not kw_id:
            continue
        if kw_id not in deduped or result["relevance_score"] > deduped[kw_id]["relevance_score"]:
            deduped[kw_id] = result

    # Save results (batch insert, not N+1)
    computed = 0
    failed = len(relevance_results) - len(deduped)
    total_relevance = 0.0

    for kw_id, result in deduped.items():
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


# ==================== WORKSPACE CRUD ENDPOINTS ====================

@router.post(
    "/workspaces",
    response_model=WorkspaceResponse,
    status_code=201,
)
def create_workspace(
    request: WorkspaceCreateRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    ai: AIService = Depends(get_ai),
):
    """
    Yeni marka çalışması oluştur.
    Background task'ta site crawl + AI profile extraction başlatılır.
    """
    workspace = BrandProfile(
        name=request.name,
        company_url=request.company_url,
        competitor_urls=request.competitor_urls,
        preliminary_info=request.preliminary_info,
        default_geo_target_id=request.default_geo_target_id,
        default_language_id=request.default_language_id,
        status="pending",
    )
    db.add(workspace)
    db.commit()
    db.refresh(workspace)

    # Background task'ta profil analizi
    if settings.ENABLE_SITE_PROFILE_ANALYSIS:
        background_tasks.add_task(
            _run_workspace_profile_analysis,
            workspace_id=workspace.id,
            company_url=request.company_url,
            competitor_urls=request.competitor_urls,
            preliminary_info=request.preliminary_info,
        )

    return WorkspaceResponse.model_validate(workspace)


@router.get(
    "/workspaces",
    response_model=List[WorkspaceListResponse],
)
def list_workspaces(
    include_archived: bool = False,
    db: Session = Depends(get_db),
):
    """
    Marka çalışmaları listesi.
    Arşivlenenler default hariç.
    """
    query = db.query(BrandProfile)
    if not include_archived:
        query = query.filter(BrandProfile.deleted_at.is_(None))
    workspaces = query.order_by(BrandProfile.created_at.desc()).all()

    result = []
    for ws in workspaces:
        run_count = db.query(ScoringRun).filter(
            ScoringRun.brand_profile_id == ws.id
        ).count()
        result.append(WorkspaceListResponse(
            id=ws.id,
            name=ws.name,
            company_url=ws.company_url,
            status=ws.status,
            profile_data=ws.profile_data,
            suggested_keywords=ws.suggested_keywords,
            default_geo_target_id=ws.default_geo_target_id,
            default_language_id=ws.default_language_id,
            deleted_at=ws.deleted_at,
            created_at=ws.created_at,
            run_count=run_count,
        ))
    return result


@router.get(
    "/workspaces/{workspace_id}",
    response_model=WorkspaceResponse,
)
def get_workspace(workspace_id: int, db: Session = Depends(get_db)):
    """Marka çalışması detay."""
    workspace = verify_workspace(db, workspace_id)
    return WorkspaceResponse.model_validate(workspace)


@router.put(
    "/workspaces/{workspace_id}/confirm",
    response_model=WorkspaceResponse,
)
def confirm_workspace(
    workspace_id: int,
    request: ProfileConfirmRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """
    Marka çalışması profilini onayla.
    Path A: Workspace'in en son scored run'ı için relevance tetiklenir.
    """
    workspace = verify_workspace(db, workspace_id)

    if workspace.status not in ("draft", "confirmed"):
        raise HTTPException(
            status_code=400,
            detail=f"Profil durumu '{workspace.status}', onaylanamaz (draft veya confirmed olmalı)"
        )

    existing_data = workspace.profile_data if isinstance(workspace.profile_data, dict) else {}
    incoming_data = request.profile_data if isinstance(request.profile_data, dict) else {}
    workspace.profile_data = _sanitize_profile_data(incoming_data, existing_data)
    workspace.status = "confirmed"
    db.commit()
    db.refresh(workspace)

    # Path A: Workspace'in en son scored run'ı için relevance tetikle
    if settings.ENABLE_RELEVANCE_RERANK:
        anchor_texts = (workspace.profile_data or {}).get("anchor_texts", [])
        if anchor_texts:
            latest_scored_run = (
                db.query(ScoringRun)
                    .filter(
                        ScoringRun.brand_profile_id == workspace.id,
                        ScoringRun.status == "scored",
                        ScoringRun.skip_relevance == False,
                    )
                    .order_by(ScoringRun.created_at.desc())
                    .first()
            )
            if latest_scored_run and transition_atomic(
                db, latest_scored_run, "relevance_computing", "scored"
            ):
                background_tasks.add_task(
                    _run_relevance_computation,
                    scoring_run_id=latest_scored_run.id,
                )
                logger.info(
                    f"Path A: Auto-triggered relevance for run {latest_scored_run.id} "
                    f"(workspace {workspace_id})"
                )

    return WorkspaceResponse.model_validate(workspace)


@router.post(
    "/workspaces/{workspace_id}/archive",
    response_model=WorkspaceResponse,
)
def archive_workspace(workspace_id: int, db: Session = Depends(get_db)):
    """Marka çalışmasını arşivle (soft delete)."""
    workspace = verify_workspace(db, workspace_id)
    workspace.deleted_at = datetime.utcnow()
    db.commit()
    db.refresh(workspace)
    return WorkspaceResponse.model_validate(workspace)


@router.post(
    "/workspaces/{workspace_id}/restore",
    response_model=WorkspaceResponse,
)
def restore_workspace(workspace_id: int, db: Session = Depends(get_db)):
    """Arşivden çıkar."""
    workspace = db.query(BrandProfile).filter(
        BrandProfile.id == workspace_id,
        BrandProfile.deleted_at.isnot(None),
    ).first()
    if not workspace:
        raise HTTPException(status_code=404, detail="Arşivlenmiş çalışma bulunamadı")
    workspace.deleted_at = None
    db.commit()
    db.refresh(workspace)
    return WorkspaceResponse.model_validate(workspace)


@router.post(
    "/workspaces/{workspace_id}/keywords/refresh",
    response_model=WorkspaceKeywordRefreshResponse,
)
def refresh_workspace_keywords(
    workspace_id: int,
    request: WorkspaceKeywordRefreshRequest,
    db: Session = Depends(get_db),
):
    """
    Workspace keyword metriklerini Google Ads ile yeniler.
    Network çağrısı DB delete+insert transaction'ından önce tamamlanır.
    """
    workspace = verify_workspace(db, workspace_id)
    rows = (
        db.query(WorkspaceKeyword, Keyword)
        .join(Keyword, WorkspaceKeyword.keyword_id == Keyword.id)
        .filter(WorkspaceKeyword.brand_profile_id == workspace_id)
        .order_by(WorkspaceKeyword.id)
        .all()
    )
    if not rows:
        raise HTTPException(status_code=400, detail="Bu workspace'te yenilenecek keyword yok")

    existing = [
        ExistingWorkspaceKeyword(
            keyword_id=kw.id,
            keyword=kw.keyword,
            monthly_volume=wk.monthly_volume,
            trend_3m=wk.trend_3m,
            trend_12m=wk.trend_12m,
            competition_score=wk.competition_score,
            data_source=wk.data_source,
            sector=wk.sector,
            target_market=wk.target_market,
            geo_target_id=wk.geo_target_id,
            language_id=wk.language_id,
            notes=wk.notes,
        )
        for wk, kw in rows
    ]
    seed_keywords = [item.keyword for item in existing]

    db.commit()

    from app.api.v1.google_ads import _get_service

    customer_id = (
        request.customer_id
        or settings.GOOGLE_ADS_CUSTOMER_ID
        or settings.GOOGLE_ADS_LOGIN_CUSTOMER_ID
    )
    if not customer_id:
        raise HTTPException(status_code=400, detail="customer_id gerekli")

    svc = _get_service()
    refreshed_ideas = []
    for start in range(0, len(seed_keywords), 20):
        chunk = seed_keywords[start:start + 20]
        ideas, _, _ = svc.enrich_keywords(
            customer_id=customer_id,
            seeds=chunk,
            max_results=request.max_results,
            language_id=workspace.default_language_id,
            geo_target_id=workspace.default_geo_target_id,
        )
        if request.min_volume > 0:
            ideas = [idea for idea in ideas if idea.avg_monthly_searches >= request.min_volume]
        refreshed_ideas.extend(ideas)

    merged = build_refreshed_workspace_keywords(
        existing,
        refreshed_ideas,
        include_new_ideas=request.include_new_ideas,
        data_source="google_ads_api",
    )

    existing_keywords_by_norm = {
        normalize_keyword(item.keyword): item.keyword_id
        for item in existing
    }

    try:
        db.query(WorkspaceKeyword).filter(
            WorkspaceKeyword.brand_profile_id == workspace_id
        ).delete(synchronize_session=False)

        for row in merged["rows"]:
            keyword_id = row["keyword_id"]
            if keyword_id is None:
                normalized = normalize_keyword(row["keyword"])
                keyword_id = existing_keywords_by_norm.get(normalized)
                if keyword_id is None:
                    kw = db.query(Keyword).filter(Keyword.normalized_keyword == normalized).first()
                    if not kw:
                        kw = Keyword(
                            keyword=row["keyword"],
                            normalized_keyword=normalized,
                            monthly_volume=row["monthly_volume"],
                            trend_3m=row["trend_3m"],
                            trend_12m=row["trend_12m"],
                            competition_score=row["competition_score"],
                            data_source="google_ads_api",
                            is_active=True,
                        )
                        db.add(kw)
                        db.flush()
                    keyword_id = kw.id

            db.add(WorkspaceKeyword(
                brand_profile_id=workspace_id,
                keyword_id=keyword_id,
                monthly_volume=row["monthly_volume"],
                trend_3m=row["trend_3m"],
                trend_12m=row["trend_12m"],
                competition_score=row["competition_score"],
                data_source=row["data_source"],
                sector=row["sector"],
                target_market=row["target_market"],
                geo_target_id=row["geo_target_id"] or workspace.default_geo_target_id,
                language_id=row["language_id"] or workspace.default_language_id,
                notes=row["notes"],
            ))

        db.commit()
    except Exception:
        db.rollback()
        raise

    diff = merged["diff"]
    return WorkspaceKeywordRefreshResponse(
        workspace_id=workspace_id,
        refreshed=diff["refreshed"],
        unchanged=diff["unchanged"],
        added=diff["added"],
        removed=diff["removed"],
        total_after=len(merged["rows"]),
    )


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
                profile.error_message = safe_500_detail(
                    e,
                    "Profil analizi basarisiz oldu"
                )
                db.commit()
        except Exception:
            pass
    finally:
        db.close()


def _run_workspace_profile_analysis(
    workspace_id: int,
    company_url: str,
    competitor_urls: list = None,
    preliminary_info: str = None,
):
    """Background task: workspace için site crawl + AI profil çıkarma."""
    from app.database.connection import SessionLocal
    from app.generators.ai_service import get_ai_service
    from app.core.site_analyzer.profile_extractor import ProfileExtractor
    from app.core.keyword_normalize import normalize_keyword

    db = SessionLocal()
    try:
        workspace = db.query(BrandProfile).filter(BrandProfile.id == workspace_id).first()
        if not workspace:
            return

        workspace.status = "running"
        db.commit()

        ai = get_ai_service(api_key=settings.GEMINI_API_KEY)
        extractor = ProfileExtractor(ai)

        result = extractor.extract_profile(company_url, preliminary_info=preliminary_info)

        if result["error"] and not result["profile"]:
            workspace.status = "failed"
            workspace.error_message = result["error"]
            db.commit()
            return

        workspace.profile_data = result["profile"]
        workspace.source_pages = result["source_pages"]

        # AI'ın önerdiği keyword'leri hafif normalize et
        suggested_raw = (result.get("profile") or {}).get("suggested_keywords", [])
        if suggested_raw:
            normalized_suggestions = list(dict.fromkeys(
                kw.strip().lower() for kw in suggested_raw
                if kw and kw.strip()
            ))
            workspace.suggested_keywords = normalized_suggestions or None

        if competitor_urls and result["profile"]:
            validation = extractor.validate_with_competitors(
                result["profile"], competitor_urls
            )
            workspace.validation_data = validation

        workspace.status = "draft"
        workspace.error_message = None
        db.commit()

        logger.info(f"Workspace profile analysis completed for workspace_id={workspace_id}")

    except Exception as e:
        logger.error(f"Workspace profile analysis failed for workspace_id={workspace_id}: {e}")
        try:
            workspace = db.query(BrandProfile).filter(BrandProfile.id == workspace_id).first()
            if workspace:
                workspace.status = "failed"
                workspace.error_message = safe_500_detail(e, "Profil analizi başarısız oldu")
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
        # Yeni mimari: ScoringRun.brand_profile_id üzerinden; eski 1:1 fallback
        scoring_run = db.query(ScoringRun).filter(ScoringRun.id == scoring_run_id).first()
        profile = None

        if scoring_run and scoring_run.brand_profile_id:
            profile = db.query(BrandProfile).filter(
                BrandProfile.id == scoring_run.brand_profile_id,
                BrandProfile.status == "confirmed",
                BrandProfile.deleted_at.is_(None),
            ).first()

        if not profile:
            profile = db.query(BrandProfile).filter(
                BrandProfile.scoring_run_id == scoring_run_id,
                BrandProfile.status == "confirmed",
            ).first()

        if not profile:
            logger.warning(f"Relevance compute skipped: no confirmed profile for run {scoring_run_id}")
            if scoring_run:
                try:
                    transition(db, scoring_run, target="failed")
                except ValueError:
                    pass
            return

        anchor_texts = (profile.profile_data or {}).get("anchor_texts", [])
        if not anchor_texts:
            logger.warning(f"Relevance compute skipped: no anchor texts for run {scoring_run_id}")
            if scoring_run:
                try:
                    transition(db, scoring_run, target="failed")
                except ValueError:
                    pass
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
            if scoring_run:
                try:
                    transition(db, scoring_run, target="failed")
                except ValueError:
                    pass
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

        # Deduplicate results by keyword_id (keep highest score per keyword)
        deduped: dict = {}
        for result in relevance_results:
            kw_id = keyword_id_map.get(result["keyword"])
            if not kw_id:
                continue
            if kw_id not in deduped or result["relevance_score"] > deduped[kw_id]["relevance_score"]:
                deduped[kw_id] = result

        computed = 0
        for kw_id, result in deduped.items():
            db.add(KeywordRelevance(
                scoring_run_id=scoring_run_id,
                keyword_id=kw_id,
                relevance_score=result["relevance_score"],
                matched_anchor=result.get("matched_anchor", ""),
                method=result.get("method", "embedding"),
            ))
            computed += 1

        db.commit()

        # Status geçişi: relevance_computing → relevance_computed
        if scoring_run:
            try:
                transition(db, scoring_run, target="relevance_computed")
            except ValueError as ve:
                logger.warning(f"Relevance status transition failed: {ve}")

        logger.info(
            f"Auto relevance computation completed for run {scoring_run_id}: "
            f"{computed}/{len(keywords)} keywords scored"
        )

    except Exception as e:
        logger.error(f"Auto relevance computation failed for run {scoring_run_id}: {e}")
        # Fail-open: pipeline continues without relevance, using default scores
        if scoring_run:
            try:
                transition(db, scoring_run, target="failed")
            except ValueError:
                pass
    finally:
        db.close()
