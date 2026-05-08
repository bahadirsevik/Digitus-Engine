"""
Google Ads API endpoint'leri.
Mevcut hicbir endpoint'i degistirmez.
"""
from __future__ import annotations

import hashlib
import json
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.dependencies import get_db
from app.config import settings
from app.integrations.google_ads.service import GoogleAdsService

router = APIRouter()


def _get_service() -> GoogleAdsService:
    svc = GoogleAdsService(settings)
    if not svc.is_configured():
        raise HTTPException(
            status_code=503,
            detail="Google Ads credentials not configured. Check .env file."
        )
    return svc


# --- Request/Response Modelleri ---

class EnrichRequest(BaseModel):
    customer_id: str
    seeds: List[str] = Field(..., min_length=1, max_length=20)  # API limiti: max 20 seed
    max_results: int = Field(300, ge=1, le=5000)
    min_volume: int = Field(0, ge=0)
    language_id: Optional[str] = None
    geo_target_id: Optional[str] = None


class EnrichedKeywordOut(BaseModel):
    keyword: str
    avg_monthly_searches: int
    competition_index: Optional[int]
    competition_score: float
    trend_3m: float
    trend_12m: float
    cpc_low: float
    cpc_high: float


class EnrichResponse(BaseModel):
    count: int
    truncated: bool
    truncated_at: Optional[int]
    truncated_reason: Optional[str]
    keywords: List[EnrichedKeywordOut]


class ImportRequest(BaseModel):
    customer_id: str
    seeds: List[str] = Field(..., min_length=1, max_length=20)
    max_results: int = Field(300, ge=1, le=5000)
    min_volume: int = Field(0, ge=0)
    sector: Optional[str] = None
    target_market: Optional[str] = None


class ImportResponse(BaseModel):
    created: int
    already_existing: int
    skipped_fuzzy: int
    truncated: bool
    truncated_reason: Optional[str]
    message: str


class CustomerIdItem(BaseModel):
    customer_id: str


class CustomerDetailOut(BaseModel):
    customer_id: str
    name: str
    currency_code: str
    time_zone: str


class CampaignInfo(BaseModel):
    campaign_id: str
    campaign_name: str
    status: str


class CampaignKeywordOut(BaseModel):
    keyword: str
    match_type: str
    campaign_name: str
    campaign_id: str
    ad_group_name: str
    impressions: int
    clicks: int
    cost: float
    avg_cpc: float
    ctr: float


class CampaignKeywordsResponse(BaseModel):
    count: int
    keywords: List[CampaignKeywordOut]


class CampaignKeywordsImportRequest(BaseModel):
    customer_id: str
    campaign_id: Optional[str] = None
    min_impressions: int = Field(0, ge=0)
    date_range: str = Field(
        "ALL_TIME",
        pattern="^(ALL_TIME|LAST_7_DAYS|LAST_14_DAYS|LAST_30_DAYS)$",
    )
    limit: int = Field(2000, ge=1, le=2000)
    sector: Optional[str] = None
    target_market: Optional[str] = None


class UrlSeedRequest(BaseModel):
    url: str = Field(..., min_length=1)
    language_id: Optional[str] = None
    geo_target_id: Optional[str] = None
    max_results: int = Field(300, ge=1, le=5000)
    min_volume: int = Field(0, ge=0)
    include_keyword_seed: bool = False
    brand_profile_id: int
    customer_id: Optional[str] = None
    refresh: bool = False


class UrlSeedIdeaOut(BaseModel):
    keyword: str
    monthly_volume: int
    trend_3m: float
    trend_12m: float
    competition: float


class UrlSeedResponse(BaseModel):
    ideas: List[UrlSeedIdeaOut]
    total: int
    cached: bool
    cache_key: Optional[str]
    quota_remaining: Optional[int] = None


def _url_seed_cache_key(
    customer_id: str,
    url: str,
    language_id: str,
    geo_target_id: str,
    max_results: int,
    min_volume: int,
    include_keyword_seed: bool,
) -> str:
    url_hash = hashlib.md5(url.strip().lower().encode("utf-8")).hexdigest()
    seed_mode = "kwurl" if include_keyword_seed else "url"
    return (
        f"gads:url_seed:{customer_id}:{url_hash}:{language_id}:{geo_target_id}:"
        f"{max_results}:{min_volume}:{seed_mode}"
    )


def _get_redis_client():
    try:
        import redis
        return redis.from_url(settings.REDIS_URL, decode_responses=True)
    except Exception:
        return None


# --- Endpoint'ler ---

@router.get("/health")
def health_check():
    """
    Google Ads baglantisi ve yetkilendirme kontrolu.
    Credentials yoksa 503 yerine bilgilendirici JSON doner.
    """
    svc = GoogleAdsService(settings)
    return svc.health_check()


@router.get("/customers", response_model=List[CustomerIdItem])
def list_customers():
    """
    Erisebilir tum Google Ads musteri hesaplarini listele.
    Sadece customer_id doner — hafif cagri.
    Detay icin GET /google-ads/customers/{customer_id} kullan.
    """
    svc = _get_service()
    ids = svc.list_customer_ids()
    return [{"customer_id": cid} for cid in ids]


@router.get("/customers/{customer_id}", response_model=CustomerDetailOut)
def get_customer_detail(customer_id: str):
    """
    Tek bir hesap icin isim, para birimi, zaman dilimi.
    """
    svc = _get_service()
    info = svc.get_customer_detail(customer_id)
    return CustomerDetailOut(
        customer_id=info.customer_id,
        name=info.name,
        currency_code=info.currency_code,
        time_zone=info.time_zone,
    )


@router.post("/enrich", response_model=EnrichResponse)
def enrich_keywords(payload: EnrichRequest):
    """
    Keyword onerileri al — DB'ye yazmaz, onizleme icin.

    Notlar:
    - seeds max 20 (Google Ads API limiti)
    - max_results gercek donus sayisini garanti etmez; API kendi limitini uygular
    - truncated=True ise max_results'a ulasilmistir
    """
    svc = _get_service()
    enriched, truncated, truncated_reason = svc.enrich_keywords(
        customer_id=payload.customer_id,
        seeds=payload.seeds,
        max_results=payload.max_results,
        language_id=payload.language_id,
        geo_target_id=payload.geo_target_id,
    )

    # min_volume filtresi
    if payload.min_volume > 0:
        enriched = [e for e in enriched if e.avg_monthly_searches >= payload.min_volume]

    return EnrichResponse(
        count=len(enriched),
        truncated=truncated,
        truncated_at=len(enriched) if truncated else None,
        truncated_reason=truncated_reason,
        keywords=[
            EnrichedKeywordOut(
                keyword=e.keyword,
                avg_monthly_searches=e.avg_monthly_searches,
                competition_index=e.competition_index,
                competition_score=e.competition_score,
                trend_3m=e.trend_3m,
                trend_12m=e.trend_12m,
                cpc_low=e.cpc_low,
                cpc_high=e.cpc_high,
            )
            for e in enriched
        ],
    )


@router.post("/keyword-ideas-by-url", response_model=UrlSeedResponse)
def keyword_ideas_by_url(
    payload: UrlSeedRequest,
    response: Response,
    db: Session = Depends(get_db),
):
    """
    KeywordPlanIdeaService URL seed onerileri.
    DB'ye yazmaz; frontend onizleme ve sonra /keywords/import ile workspace bind yapar.
    """
    from app.core.workspace import verify_workspace

    workspace = verify_workspace(db, payload.brand_profile_id)
    customer_id = (
        payload.customer_id
        or settings.GOOGLE_ADS_CUSTOMER_ID
        or settings.GOOGLE_ADS_LOGIN_CUSTOMER_ID
    )
    if not customer_id:
        raise HTTPException(status_code=400, detail="customer_id gerekli")

    language_id = payload.language_id or workspace.default_language_id or settings.GOOGLE_ADS_LANGUAGE_ID
    geo_target_id = payload.geo_target_id or workspace.default_geo_target_id or settings.GOOGLE_ADS_GEO_TARGET_ID
    cache_key = _url_seed_cache_key(
        customer_id,
        payload.url,
        language_id,
        geo_target_id,
        payload.max_results,
        payload.min_volume,
        payload.include_keyword_seed,
    )

    redis_client = _get_redis_client()
    if redis_client and not payload.refresh:
        try:
            cached_raw = redis_client.get(cache_key)
            if cached_raw:
                cached = json.loads(cached_raw)
                return UrlSeedResponse(
                    ideas=[UrlSeedIdeaOut(**idea) for idea in cached.get("ideas", [])],
                    total=int(cached.get("total", 0)),
                    cached=True,
                    cache_key=cache_key,
                )
        except Exception:
            pass

    svc = _get_service()
    keyword_seeds = workspace.suggested_keywords or []
    try:
        ideas, truncated, truncated_reason = svc.keyword_ideas_by_url(
            customer_id=customer_id,
            url=payload.url,
            max_results=payload.max_results,
            language_id=language_id,
            geo_target_id=geo_target_id,
            min_volume=payload.min_volume,
            include_keyword_seed=payload.include_keyword_seed,
            keyword_seeds=keyword_seeds,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        exc_name = type(exc).__name__
        if exc_name == "ResourceExhausted":
            response.headers["retry-after"] = "600"
            raise HTTPException(status_code=429, detail="Google Ads kotasi doldu, daha sonra tekrar deneyin")
        if exc_name in {"InvalidArgument", "GoogleAdsException"}:
            raise HTTPException(status_code=400, detail=str(exc))
        raise

    out_ideas = [
        UrlSeedIdeaOut(
            keyword=idea.keyword,
            monthly_volume=idea.monthly_volume,
            trend_3m=idea.trend_3m,
            trend_12m=idea.trend_12m,
            competition=idea.competition,
        )
        for idea in ideas
    ]

    payload_to_cache = {"ideas": [idea.model_dump() for idea in out_ideas], "total": len(out_ideas)}
    if redis_client:
        try:
            redis_client.setex(cache_key, 24 * 60 * 60, json.dumps(payload_to_cache))
        except Exception:
            pass

    return UrlSeedResponse(
        ideas=out_ideas,
        total=len(out_ideas),
        cached=False,
        cache_key=cache_key,
        quota_remaining=None,
    )


@router.post("/import", response_model=ImportResponse)
def import_keywords(payload: ImportRequest, db: Session = Depends(get_db)):
    """
    Google Ads keyword onerilerini DB'ye aktar.

    IS KURALI — ONEMLI:
    Mevcut DB'de ayni metin ile kayit varsa (CSV'den veya baska herhangi bir
    kaynaktan) o keyword skip edilir ve data_source'u guncellenmez.

    Sonuc olarak: POST /scoring/runs icinde keyword_source_filter="google_ads_api"
    kullanildiginda sadece BU endpoint ile yeni olusturulan keywordler
    skorlanir. Ortusme gosteren mevcut keywordler o run'a dahil edilmez.

    Ortusenleri de dahil etmek istiyorsaniz keyword_source_filter=None
    ile run olusturun (tum aktif keywordler).
    """
    svc = _get_service()
    result = svc.import_keywords(
        db=db,
        customer_id=payload.customer_id,
        seeds=payload.seeds,
        max_results=payload.max_results,
        min_volume=payload.min_volume,
        sector=payload.sector,
        target_market=payload.target_market,
    )

    return ImportResponse(
        created=result.created,
        already_existing=result.already_existing,
        skipped_fuzzy=result.skipped_fuzzy,
        truncated=result.truncated,
        truncated_reason=result.truncated_reason,
        message=(
            f"{result.created} keyword olusturuldu (data_source=google_ads_api). "
            f"{result.already_existing} zaten mevcuttu (data_source degistirilmedi). "
            f"{result.skipped_fuzzy} fuzzy eslesme ile atildi."
        ),
    )


@router.get("/campaigns", response_model=List[CampaignInfo])
def list_campaigns(customer_id: str = Query(..., min_length=1)):
    """List campaigns for selected customer account."""
    svc = _get_service()
    items = svc.list_campaigns(customer_id=customer_id)
    return [CampaignInfo(**vars(item)) for item in items]


@router.get("/campaigns/keywords", response_model=CampaignKeywordsResponse)
def list_campaign_keywords(
    customer_id: str = Query(..., min_length=1),
    campaign_id: Optional[str] = Query(None),
    min_impressions: int = Query(0, ge=0),
    date_range: str = Query(
        "ALL_TIME",
        pattern="^(ALL_TIME|LAST_7_DAYS|LAST_14_DAYS|LAST_30_DAYS)$",
    ),
    limit: int = Query(2000, ge=1, le=2000),
):
    """Return campaign keywords from keyword_view without DB write."""
    svc = _get_service()
    try:
        keywords = svc.get_campaign_keywords(
            customer_id=customer_id,
            campaign_id=campaign_id,
            min_impressions=min_impressions,
            date_range=date_range,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    return CampaignKeywordsResponse(
        count=len(keywords),
        keywords=[CampaignKeywordOut(**vars(kw)) for kw in keywords],
    )


@router.post("/campaigns/keywords/import", response_model=ImportResponse)
def import_campaign_keywords(
    payload: CampaignKeywordsImportRequest,
    db: Session = Depends(get_db),
):
    """Import campaign keyword_view rows into keywords table."""
    svc = _get_service()
    try:
        result = svc.import_campaign_keywords(
            db=db,
            customer_id=payload.customer_id,
            campaign_id=payload.campaign_id,
            min_impressions=payload.min_impressions,
            date_range=payload.date_range,
            limit=payload.limit,
            sector=payload.sector,
            target_market=payload.target_market,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    return ImportResponse(
        created=result.created,
        already_existing=result.already_existing,
        skipped_fuzzy=result.skipped_fuzzy,
        truncated=result.truncated,
        truncated_reason=result.truncated_reason,
        message=(
            f"{result.created} keyword olusturuldu. "
            f"{result.already_existing} zaten mevcuttu. "
            f"{result.skipped_fuzzy} benzer atlandi."
        ),
    )
