"""
Google Ads API endpoint'leri.
Mevcut hicbir endpoint'i degistirmez.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import socket
from typing import List, Optional
from urllib.parse import urljoin, urlparse
from fastapi import APIRouter, Depends, HTTPException, Query, Response
import httpx
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.dependencies import get_db
from app.config import settings
from app.core.keyword_normalize import normalize_keyword
from app.integrations.google_ads.service import GoogleAdsService

router = APIRouter()

URL_SEED_CACHE_TTL_SECONDS = 24 * 60 * 60
GOOGLE_ADS_CAMPAIGN_CACHE_TTL_SECONDS = 10 * 60
GOOGLE_ADS_LANGUAGE_ALIASES = {
    "1055": "1037",  # Windows/Turkish locale -> Google Ads Turkish criterion ID
}


def _normalize_language_id(language_id: Optional[str]) -> Optional[str]:
    if language_id is None:
        return None
    value = str(language_id).strip()
    return GOOGLE_ADS_LANGUAGE_ALIASES.get(value, value)


def _normalize_geo_target_id(geo_target_id: Optional[str]) -> Optional[str]:
    """Strip any resource-path prefix from geo target IDs (e.g. 'geoTargetConstants/2792' → '2792')."""
    if geo_target_id is None:
        return None
    value = str(geo_target_id).strip()
    if "/" in value:
        value = value.rsplit("/", 1)[-1]
    return value or None


def _translate_invalid_argument(raw: str) -> str:
    """Convert Google Ads InvalidArgument error text to a user-friendly Turkish message."""
    import re
    lower = raw.lower()

    m = re.search(r"languageconstants?/(\d+)", lower)
    if m:
        return (
            f"Geçersiz dil kimliği: {m.group(1)}. "
            "Türkçe için 1037 kullanın (Settings → varsayılan_dil)."
        )

    m = re.search(r"geotargetconstants?/(\d+)", lower)
    if m:
        return (
            f"Geçersiz coğrafi hedef kimliği: {m.group(1)}. "
            "Türkiye için 2792 kullanın (Settings → varsayılan_geo_hedef)."
        )

    if "customer" in lower and ("not found" in lower or "invalid" in lower):
        return "Müşteri hesabı bulunamadı veya erişim yetkiniz yok. customer_id değerini kontrol edin."

    return f"Google Ads geçersiz parametre: {raw}"


def _get_service() -> GoogleAdsService:
    svc = GoogleAdsService(settings)
    if not svc.is_configured():
        raise HTTPException(
            status_code=503,
            detail="Google Ads credentials not configured. Check .env file."
        )
    return svc


def _raise_google_ads_error(exc: Exception) -> None:
    exc_name = type(exc).__name__
    detail = str(exc)
    if exc_name == "RefreshError" or "invalid_grant" in detail:
        raise HTTPException(
            status_code=503,
            detail=(
                "Google Ads yetkilendirmesi gecersiz veya suresi dolmus. "
                "GOOGLE_ADS_REFRESH_TOKEN yenilenmeli."
            ),
        )
    if exc_name == "ResourceExhausted":
        raise HTTPException(
            status_code=429,
            detail="Google Ads kotasi doldu, daha sonra tekrar deneyin",
        )
    if exc_name in {"InvalidArgument", "GoogleAdsException"}:
        raise HTTPException(status_code=400, detail=_translate_invalid_argument(detail))
    raise HTTPException(status_code=502, detail=f"Google Ads isteği başarısız oldu: {detail}")


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
    skipped_junk: int = 0
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


class UrlSeedSourceStatus(BaseModel):
    reachable: bool
    http_status: Optional[int] = None
    final_url: Optional[str] = None
    warning: Optional[str] = None


class UrlSeedResponse(BaseModel):
    ideas: List[UrlSeedIdeaOut]
    total: int
    cached: bool
    cache_key: Optional[str]
    quota_remaining: Optional[int] = None
    warnings: List[str] = Field(default_factory=list)
    source_status: Optional[UrlSeedSourceStatus] = None


def _url_seed_cache_key(
    customer_id: str,
    workspace_token: str,
    url: str,
    language_id: str,
    geo_target_id: str,
    max_results: int,
    min_volume: int,
    include_keyword_seed: bool,
    seeds_hash: str = "none",
) -> str:
    url_hash = hashlib.md5(url.strip().lower().encode("utf-8")).hexdigest()
    seed_mode = "kwurl" if include_keyword_seed else "url"
    seed_part = seeds_hash if include_keyword_seed else "none"
    return (
        f"gads:url_seed:{customer_id}:{workspace_token}:{url_hash}:{language_id}:{geo_target_id}:"
        f"{max_results}:{min_volume}:{seed_mode}:{seed_part}"
    )


def _keyword_seed_hash(keyword_seeds: List[str]) -> str:
    normalized = sorted(
        {
            normalize_keyword(str(seed).strip())
            for seed in keyword_seeds or []
            if str(seed).strip()
        }
    )
    if not normalized:
        return "empty"
    return hashlib.md5(",".join(normalized).encode("utf-8")).hexdigest()


def _campaigns_cache_key(customer_id: str) -> str:
    # Campaign data is Google Ads customer-scoped read-only account data, not
    # workspace-owned data. If customer access becomes tenant-specific, add
    # account ownership checks before reusing this cache shape.
    return f"gads:campaigns:{customer_id}"


def _campaign_keywords_cache_key(
    customer_id: str,
    campaign_id: Optional[str],
    date_range: str,
    limit: int,
    min_impressions: int,
) -> str:
    campaign_scope = campaign_id or "all"
    return f"gads:campaign_kw:{customer_id}:{campaign_scope}:{date_range}:{limit}:{min_impressions}"


def _get_redis_client():
    # Plan E: tek kaynak core provider (router kopyaları buna delege eder;
    # dashboard.py ve keywords.py bu ismi import etmeye devam edebilir)
    from app.core.cache import get_redis_client
    return get_redis_client()


def _read_json_cache(cache_key: str) -> Optional[dict]:
    redis_client = _get_redis_client()
    if not redis_client:
        return None
    try:
        cached_raw = redis_client.get(cache_key)
        if not cached_raw:
            return None
        cached = json.loads(cached_raw)
        return cached if isinstance(cached, dict) else None
    except Exception:
        return None


def _write_json_cache(cache_key: str, payload: dict, ttl_seconds: int) -> None:
    redis_client = _get_redis_client()
    if not redis_client:
        return
    try:
        redis_client.setex(cache_key, ttl_seconds, json.dumps(payload))
    except Exception:
        pass


_URL_FETCH_MAX_REDIRECTS = 5


def _assert_ip_is_public(ip_str: str) -> None:
    """SSRF IP kontrolü — core guard'a delege (plan v13: app/core/url_guard.py)."""
    from app.core.url_guard import UnsafeUrlError, assert_ip_is_public

    try:
        assert_ip_is_public(ip_str)
    except UnsafeUrlError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


def _assert_url_host_is_public(url: str) -> None:
    """Şema + host public-IP kontrolü — core guard'a delege."""
    from app.core.url_guard import UnsafeUrlError, assert_url_host_is_public

    try:
        assert_url_host_is_public(url)
    except UnsafeUrlError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


def _assert_url_reachable(url: str) -> None:
    """URL'nin erişilebilirliğini doğrular.

    SSRF korumasi: şema (http/https) + her redirect adımında host'un public IP'lere
    çözümlendiği doğrulanır. Yönlendirmeler elle takip edilir (follow_redirects=False),
    böylece public bir URL iç bir adrese yönlendiremez.
    """
    current = url
    try:
        with httpx.Client(timeout=8.0, follow_redirects=False) as client:
            for _ in range(_URL_FETCH_MAX_REDIRECTS + 1):
                _assert_url_host_is_public(current)
                resp = client.get(current)
                if resp.is_redirect and resp.has_redirect_location:
                    location = resp.headers.get("location", "")
                    current = urljoin(str(resp.url), location)
                    continue
                if resp.status_code >= 400:
                    raise HTTPException(
                        status_code=422,
                        detail=f"URL erişilemedi veya hata döndü: HTTP {resp.status_code}",
                    )
                return
        raise HTTPException(
            status_code=422, detail="URL çok fazla yönlendirme içeriyor"
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"URL erişilemedi: {exc}")


def _inspect_url_for_seed(url: str) -> tuple[UrlSeedSourceStatus, list[str]]:
    """SSRF guard + non-blocking reachability check for URL seed only."""
    current = url
    final_url = url
    warnings: list[str] = []

    with httpx.Client(timeout=8.0, follow_redirects=False) as client:
        for _ in range(_URL_FETCH_MAX_REDIRECTS + 1):
            # Scheme/DNS/public-IP checks stay hard failures.
            _assert_url_host_is_public(current)
            final_url = current
            try:
                resp = client.get(current)
            except Exception as exc:
                warning = f"URL sunucu kontrolünde erişilemedi: {exc}"
                warnings.append(warning)
                return (
                    UrlSeedSourceStatus(
                        reachable=False,
                        final_url=current,
                        warning=warning,
                    ),
                    warnings,
                )

            if resp.is_redirect and resp.has_redirect_location:
                location = resp.headers.get("location", "")
                current = urljoin(str(resp.url), location)
                continue

            if resp.status_code >= 400:
                warning = f"URL sunucu kontrolünde HTTP {resp.status_code} döndü"
                warnings.append(warning)
                return (
                    UrlSeedSourceStatus(
                        reachable=False,
                        http_status=resp.status_code,
                        final_url=str(resp.url),
                        warning=warning,
                    ),
                    warnings,
                )

            return (
                UrlSeedSourceStatus(
                    reachable=True,
                    http_status=resp.status_code,
                    final_url=str(resp.url),
                ),
                warnings,
            )

    warning = "URL çok fazla yönlendirme içeriyor"
    warnings.append(warning)
    return (
        UrlSeedSourceStatus(reachable=False, final_url=final_url, warning=warning),
        warnings,
    )


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
    try:
        ids = svc.list_customer_ids()
    except Exception as exc:
        _raise_google_ads_error(exc)
    configured = settings.GOOGLE_ADS_CUSTOMER_ID
    if configured:
        ids = [configured] + [cid for cid in ids if cid != configured]
    return [{"customer_id": cid} for cid in ids]


@router.get("/customers/{customer_id}", response_model=CustomerDetailOut)
def get_customer_detail(customer_id: str):
    """
    Tek bir hesap icin isim, para birimi, zaman dilimi.
    """
    svc = _get_service()
    try:
        info = svc.get_customer_detail(customer_id)
    except Exception as exc:
        _raise_google_ads_error(exc)
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
    try:
        enriched, truncated, truncated_reason = svc.enrich_keywords(
            customer_id=payload.customer_id,
            seeds=payload.seeds,
            max_results=payload.max_results,
            language_id=_normalize_language_id(payload.language_id),
            geo_target_id=payload.geo_target_id,
        )
    except Exception as exc:
        _raise_google_ads_error(exc)

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

    language_id = _normalize_language_id(
        payload.language_id or workspace.default_language_id or settings.GOOGLE_ADS_LANGUAGE_ID
    )
    geo_target_id = _normalize_geo_target_id(
        payload.geo_target_id or workspace.default_geo_target_id or settings.GOOGLE_ADS_GEO_TARGET_ID
    )
    keyword_seeds = workspace.suggested_keywords or []
    seeds_hash = _keyword_seed_hash(keyword_seeds) if payload.include_keyword_seed else "none"
    workspace_token = f"ws{workspace.id}:{workspace.created_at.isoformat() if workspace.created_at else 'na'}"
    cache_key = _url_seed_cache_key(
        customer_id,
        workspace_token,
        payload.url,
        language_id,
        geo_target_id,
        payload.max_results,
        payload.min_volume,
        payload.include_keyword_seed,
        seeds_hash,
    )

    redis_client = _get_redis_client()
    if redis_client and not payload.refresh:
        cached = _read_json_cache(cache_key)
        if cached:
            return UrlSeedResponse(
                ideas=[UrlSeedIdeaOut(**idea) for idea in cached.get("ideas", [])],
                total=int(cached.get("total", 0)),
                cached=True,
                cache_key=cache_key,
                warnings=list(cached.get("warnings") or []),
                source_status=(
                    UrlSeedSourceStatus(**cached["source_status"])
                    if isinstance(cached.get("source_status"), dict)
                    else None
                ),
            )

    svc = _get_service()
    source_status, warnings = _inspect_url_for_seed(payload.url)
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
        if type(exc).__name__ == "ResourceExhausted":
            response.headers["retry-after"] = "600"
        _raise_google_ads_error(exc)

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

    payload_to_cache = {
        "ideas": [idea.model_dump() for idea in out_ideas],
        "total": len(out_ideas),
        "warnings": warnings,
        "source_status": source_status.model_dump() if source_status else None,
    }
    if redis_client and out_ideas:
        _write_json_cache(cache_key, payload_to_cache, URL_SEED_CACHE_TTL_SECONDS)

    return UrlSeedResponse(
        ideas=out_ideas,
        total=len(out_ideas),
        cached=False,
        cache_key=cache_key,
        quota_remaining=None,
        warnings=warnings,
        source_status=source_status,
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
    try:
        result = svc.import_keywords(
            db=db,
            customer_id=payload.customer_id,
            seeds=payload.seeds,
            max_results=payload.max_results,
            min_volume=payload.min_volume,
            sector=payload.sector,
            target_market=payload.target_market,
        )
    except Exception as exc:
        _raise_google_ads_error(exc)

    return ImportResponse(
        created=result.created,
        already_existing=result.already_existing,
        skipped_fuzzy=result.skipped_fuzzy,
        skipped_junk=result.skipped_junk,
        truncated=result.truncated,
        truncated_reason=result.truncated_reason,
        message=(
            f"{result.created} keyword olusturuldu (data_source=google_ads_api). "
            f"{result.already_existing} zaten mevcuttu (data_source degistirilmedi). "
            f"{result.skipped_fuzzy} fuzzy eslesme ile atildi. "
            f"{result.skipped_junk} gecersiz kelime (bos / yalniz sayi veya sembol) atildi."
        ),
    )


@router.get("/campaigns", response_model=List[CampaignInfo])
def list_campaigns(
    customer_id: str = Query(..., min_length=1),
    refresh: bool = Query(False),
):
    """List campaigns for selected customer account."""
    cache_key = _campaigns_cache_key(customer_id)
    if not refresh:
        cached = _read_json_cache(cache_key)
        if cached:
            return [CampaignInfo(**item) for item in cached.get("campaigns", [])]

    svc = _get_service()
    try:
        items = svc.list_campaigns(customer_id=customer_id)
    except Exception as exc:
        _raise_google_ads_error(exc)

    campaigns = [CampaignInfo(**vars(item)) for item in items]
    _write_json_cache(
        cache_key,
        {"campaigns": [campaign.model_dump() for campaign in campaigns]},
        GOOGLE_ADS_CAMPAIGN_CACHE_TTL_SECONDS,
    )
    return campaigns


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
    refresh: bool = Query(False),
):
    """Return campaign keywords from keyword_view without DB write."""
    cache_key = _campaign_keywords_cache_key(
        customer_id=customer_id,
        campaign_id=campaign_id,
        date_range=date_range,
        limit=limit,
        min_impressions=min_impressions,
    )
    if not refresh:
        cached = _read_json_cache(cache_key)
        if cached:
            return CampaignKeywordsResponse(
                count=int(cached.get("count", 0)),
                keywords=[CampaignKeywordOut(**kw) for kw in cached.get("keywords", [])],
            )

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
    except Exception as exc:
        _raise_google_ads_error(exc)

    out_keywords = [CampaignKeywordOut(**vars(kw)) for kw in keywords]
    _write_json_cache(
        cache_key,
        {
            "count": len(out_keywords),
            "keywords": [keyword.model_dump() for keyword in out_keywords],
        },
        GOOGLE_ADS_CAMPAIGN_CACHE_TTL_SECONDS,
    )
    return CampaignKeywordsResponse(
        count=len(out_keywords),
        keywords=out_keywords,
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
    except Exception as exc:
        _raise_google_ads_error(exc)

    return ImportResponse(
        created=result.created,
        already_existing=result.already_existing,
        skipped_fuzzy=result.skipped_fuzzy,
        skipped_junk=result.skipped_junk,
        truncated=result.truncated,
        truncated_reason=result.truncated_reason,
        message=(
            f"{result.created} keyword olusturuldu. "
            f"{result.already_existing} zaten mevcuttu. "
            f"{result.skipped_fuzzy} benzer atlandi. "
            f"{result.skipped_junk} gecersiz kelime atildi."
        ),
    )
