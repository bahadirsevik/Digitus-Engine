"""
Google Ads servis katmani — uretim kullanimi icin.
labs/google_ads_lab'dan BAGIMSIZ; ortak mantik kopyalanmis.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from loguru import logger

from app.integrations.google_ads.trend_calculator import compute_trends


# --- Exception Siniflandirmasi (class-based, string matching degil) ---
try:
    from grpc import RpcError
    from google.api_core.exceptions import (
        ResourceExhausted,
        ServiceUnavailable,
        GoogleAPICallError,
    )
    from google.auth.exceptions import TransportError
    _GRPC_AVAILABLE = True
except ImportError:
    _GRPC_AVAILABLE = False
    RpcError = Exception
    ResourceExhausted = Exception
    ServiceUnavailable = Exception
    GoogleAPICallError = Exception
    TransportError = Exception


MAX_RETRIES = 2
RETRY_BASE_DELAY = 2.0     # saniye
QUOTA_RETRY_DELAY = 10.0   # saniye


def _call_with_retry(fn, *args, **kwargs):
    """
    3 hata sinifi:
    - TransportError / ServiceUnavailable -> MAX_RETRIES ile eksponansiyel backoff
    - ResourceExhausted (quota) -> 1 kez QUOTA_RETRY_DELAY bekle
    - Diger (AuthError, InvalidArgument, vb.) -> aninda fail
    """
    last_exc = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            return fn(*args, **kwargs)
        except ResourceExhausted as exc:
            last_exc = exc
            if attempt == 0:
                logger.warning(f"Google Ads quota exhausted, bekleniyor {QUOTA_RETRY_DELAY}s...")
                time.sleep(QUOTA_RETRY_DELAY)
                continue
            raise
        except (ServiceUnavailable, TransportError, ConnectionError, OSError) as exc:
            last_exc = exc
            if attempt < MAX_RETRIES:
                delay = RETRY_BASE_DELAY * (attempt + 1)
                logger.warning(f"Google Ads gecici hata ({type(exc).__name__}), {delay}s sonra tekrar...")
                time.sleep(delay)
                continue
            raise
        except Exception:
            # Auth, InvalidArgument, vb. -> hemen yukari firla
            raise
    raise last_exc


@dataclass
class CustomerInfo:
    customer_id: str
    name: str
    currency_code: str
    time_zone: str


@dataclass
class EnrichedKeyword:
    keyword: str
    avg_monthly_searches: int
    competition: Optional[str]
    competition_index: Optional[int]
    competition_score: float          # competition_index / 100
    cpc_low: float
    cpc_high: float
    trend_3m: float
    trend_12m: float
    monthly_volumes_raw: list


@dataclass
class UrlSeedKeyword:
    keyword: str
    monthly_volume: int
    trend_3m: float
    trend_12m: float
    competition: float


@dataclass
class CampaignKeyword:
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


@dataclass
class CampaignInfo:
    campaign_id: str
    campaign_name: str
    status: str


@dataclass
class ImportResult:
    created: int
    already_existing: int
    skipped_fuzzy: int
    truncated: bool
    truncated_reason: Optional[str]


class GoogleAdsService:
    def __init__(self, settings):
        self.settings = settings
        self._valid_date_ranges = {"ALL_TIME", "LAST_7_DAYS", "LAST_14_DAYS", "LAST_30_DAYS"}

    def _build_client(self) -> Any:
        from google.ads.googleads.client import GoogleAdsClient
        payload = {
            "developer_token": self.settings.GOOGLE_ADS_DEVELOPER_TOKEN,
            "client_id": self.settings.GOOGLE_ADS_CLIENT_ID,
            "client_secret": self.settings.GOOGLE_ADS_CLIENT_SECRET,
            "refresh_token": self.settings.GOOGLE_ADS_REFRESH_TOKEN,
            "login_customer_id": self.settings.GOOGLE_ADS_LOGIN_CUSTOMER_ID,
            "use_proto_plus": True,
        }
        return GoogleAdsClient.load_from_dict(payload)

    def is_configured(self) -> bool:
        required = [
            self.settings.GOOGLE_ADS_DEVELOPER_TOKEN,
            self.settings.GOOGLE_ADS_CLIENT_ID,
            self.settings.GOOGLE_ADS_CLIENT_SECRET,
            self.settings.GOOGLE_ADS_REFRESH_TOKEN,
            self.settings.GOOGLE_ADS_LOGIN_CUSTOMER_ID,
        ]
        return all(required)

    def health_check(self) -> Dict[str, Any]:
        """Auth + permission kontrolu."""
        if not self.is_configured():
            return {"status": "not_configured", "auth_ok": False, "permission_ok": False}
        try:
            client = _call_with_retry(self._build_client)
            customer_service = client.get_service("CustomerService")
            resp = _call_with_retry(customer_service.list_accessible_customers)
            return {
                "status": "ok",
                "auth_ok": True,
                "permission_ok": True,
                "accessible_customers_count": len(list(resp.resource_names)),
            }
        except Exception as exc:
            return {
                "status": "error",
                "auth_ok": False,
                "permission_ok": False,
                "error": str(exc),
            }

    def list_customer_ids(self) -> List[str]:
        """
        Erisebilir tum customer ID'lerini dondur.
        Hizli cagri — sadece resource name listesi.
        Detay icin get_customer_detail() kullanin.
        """
        client = _call_with_retry(self._build_client)
        customer_service = client.get_service("CustomerService")
        resp = _call_with_retry(customer_service.list_accessible_customers)
        return [rn.split("/")[-1] for rn in resp.resource_names]

    def get_customer_detail(self, customer_id: str) -> CustomerInfo:
        """Tek bir customer icin isim, para birimi, timezone bilgisi."""
        client = _call_with_retry(self._build_client)
        svc = client.get_service("GoogleAdsService")
        query = """
            SELECT
                customer.id,
                customer.descriptive_name,
                customer.currency_code,
                customer.time_zone
            FROM customer
            LIMIT 1
        """

        def _fetch():
            stream = svc.search_stream(customer_id=customer_id, query=query)
            for batch in stream:
                if batch.results:
                    return batch.results[0].customer
            return None

        row = _call_with_retry(_fetch)
        if row is None:
            return CustomerInfo(
                customer_id=customer_id, name="Unknown",
                currency_code="", time_zone=""
            )
        return CustomerInfo(
            customer_id=customer_id,
            name=row.descriptive_name or customer_id,
            currency_code=row.currency_code or "",
            time_zone=row.time_zone or "",
        )

    def enrich_keywords(
        self,
        customer_id: str,
        seeds: List[str],
        max_results: int = 300,
        language_id: Optional[str] = None,
        geo_target_id: Optional[str] = None,
    ) -> tuple:
        """
        GenerateKeywordIdeas cagrisi. DB'ye yazmaz.
        Returns: (keywords, truncated, truncated_reason)
        """
        lang_id = language_id or getattr(self.settings, "GOOGLE_ADS_LANGUAGE_ID", None)
        geo_id = geo_target_id or getattr(self.settings, "GOOGLE_ADS_GEO_TARGET_ID", None)

        client = _call_with_retry(self._build_client)
        service = client.get_service("KeywordPlanIdeaService")

        request = client.get_type("GenerateKeywordIdeasRequest")
        request.customer_id = customer_id
        if lang_id:
            request.language = client.get_service("GoogleAdsService").language_constant_path(lang_id)
        if geo_id:
            request.geo_target_constants.append(
                client.get_service("GeoTargetConstantService").geo_target_constant_path(geo_id)
            )
        request.page_size = min(max_results, 1000)
        request.include_adult_keywords = False
        request.keyword_plan_network = client.enums.KeywordPlanNetworkEnum.GOOGLE_SEARCH
        request.keyword_seed.keywords.extend(seeds)

        def _fetch():
            return service.generate_keyword_ideas(request=request)

        response = _call_with_retry(_fetch)

        results: List[EnrichedKeyword] = []
        truncated = False
        truncated_reason = None

        for idx, row in enumerate(response, start=1):
            metrics = getattr(row, "keyword_idea_metrics", None)
            avg_searches = int(getattr(metrics, "avg_monthly_searches", 0) or 0)
            competition_index = getattr(metrics, "competition_index", None)
            cpc_low = getattr(metrics, "low_top_of_page_bid_micros", None)
            cpc_high = getattr(metrics, "high_top_of_page_bid_micros", None)
            competition = getattr(getattr(metrics, "competition", None), "name", None)

            monthly_volumes_raw = []
            for mv in (getattr(metrics, "monthly_search_volumes", []) or []):
                monthly_volumes_raw.append({
                    "year": int(getattr(mv, "year", 0)),
                    "month": int(getattr(mv, "month", 0)),
                    "monthly_searches": int(getattr(mv, "monthly_searches", 0) or 0),
                })

            trend_3m, trend_12m = compute_trends(monthly_volumes_raw)
            ci = int(competition_index) if competition_index is not None else None

            results.append(EnrichedKeyword(
                keyword=getattr(row, "text", ""),
                avg_monthly_searches=avg_searches,
                competition=competition,
                competition_index=ci,
                competition_score=round((ci or 0) / 100, 2),
                cpc_low=round((cpc_low or 0) / 1_000_000, 4),
                cpc_high=round((cpc_high or 0) / 1_000_000, 4),
                trend_3m=trend_3m,
                trend_12m=trend_12m,
                monthly_volumes_raw=monthly_volumes_raw,
            ))

            if idx >= max_results:
                truncated = True
                truncated_reason = "max_results reached"
                break

        return results, truncated, truncated_reason

    def _build_keyword_ideas_request(
        self,
        client: Any,
        customer_id: str,
        max_results: int,
        language_id: Optional[str] = None,
        geo_target_id: Optional[str] = None,
    ) -> Any:
        lang_id = language_id or getattr(self.settings, "GOOGLE_ADS_LANGUAGE_ID", None)
        geo_id = geo_target_id or getattr(self.settings, "GOOGLE_ADS_GEO_TARGET_ID", None)

        request = client.get_type("GenerateKeywordIdeasRequest")
        request.customer_id = customer_id
        if lang_id:
            request.language = client.get_service("GoogleAdsService").language_constant_path(lang_id)
        if geo_id:
            request.geo_target_constants.append(
                client.get_service("GeoTargetConstantService").geo_target_constant_path(geo_id)
            )
        request.page_size = min(max_results, 1000)
        request.include_adult_keywords = False
        request.keyword_plan_network = client.enums.KeywordPlanNetworkEnum.GOOGLE_SEARCH
        return request

    def _collect_url_seed_keywords(self, response: Any, max_results: int) -> tuple[List[UrlSeedKeyword], bool, Optional[str]]:
        results: List[UrlSeedKeyword] = []
        truncated = False
        truncated_reason = None

        for idx, row in enumerate(response, start=1):
            metrics = getattr(row, "keyword_idea_metrics", None)
            avg_searches = int(getattr(metrics, "avg_monthly_searches", 0) or 0)
            competition_index = getattr(metrics, "competition_index", None)

            monthly_volumes_raw = []
            for mv in (getattr(metrics, "monthly_search_volumes", []) or []):
                monthly_volumes_raw.append({
                    "year": int(getattr(mv, "year", 0)),
                    "month": int(getattr(mv, "month", 0)),
                    "monthly_searches": int(getattr(mv, "monthly_searches", 0) or 0),
                })

            trend_3m, trend_12m = compute_trends(monthly_volumes_raw)
            ci = int(competition_index) if competition_index is not None else 0

            results.append(UrlSeedKeyword(
                keyword=getattr(row, "text", ""),
                monthly_volume=avg_searches,
                trend_3m=trend_3m,
                trend_12m=trend_12m,
                competition=round(ci / 100, 2),
            ))

            if idx >= max_results:
                truncated = True
                truncated_reason = "max_results reached"
                break

        return results, truncated, truncated_reason

    def keyword_ideas_by_url(
        self,
        customer_id: str,
        url: str,
        max_results: int = 300,
        language_id: Optional[str] = None,
        geo_target_id: Optional[str] = None,
        min_volume: int = 0,
        include_keyword_seed: bool = False,
        keyword_seeds: Optional[List[str]] = None,
    ) -> tuple[List[UrlSeedKeyword], bool, Optional[str]]:
        """
        GenerateKeywordIdeas URL seed cagrisi. DB'ye yazmaz.
        Returns: (ideas, truncated, truncated_reason)
        """
        if not url or not url.strip():
            raise ValueError("url is required")

        client = _call_with_retry(self._build_client)
        service = client.get_service("KeywordPlanIdeaService")
        request = self._build_keyword_ideas_request(
            client=client,
            customer_id=customer_id,
            max_results=max_results,
            language_id=language_id,
            geo_target_id=geo_target_id,
        )

        clean_url = url.strip()
        clean_seeds = [s.strip() for s in (keyword_seeds or []) if s and s.strip()]
        if include_keyword_seed and clean_seeds:
            request.keyword_and_url_seed.url = clean_url
            request.keyword_and_url_seed.keywords.extend(clean_seeds[:20])
        else:
            request.url_seed.url = clean_url

        def _fetch():
            return service.generate_keyword_ideas(request=request)

        response = _call_with_retry(_fetch)
        ideas, truncated, truncated_reason = self._collect_url_seed_keywords(response, max_results)
        if min_volume > 0:
            ideas = [idea for idea in ideas if idea.monthly_volume >= min_volume]
        return ideas, truncated, truncated_reason

    def import_keywords(
        self,
        db,
        customer_id: str,
        seeds: List[str],
        max_results: int = 300,
        min_volume: int = 0,
        sector: Optional[str] = None,
        target_market: Optional[str] = None,
    ) -> ImportResult:
        """
        API'den keyword cekip DB'ye yazar.

        IS KURALI:
        Mevcut DB'de ayni text ile kayit varsa (herhangi bir data_source'dan)
        keyword skip edilir, data_source guncellenmez.
        keyword_source_filter="google_ads_api" scoring run'i sadece
        BU CAGRI ile olusturulan yeni kayitlari kapsar.
        """
        enriched, truncated, truncated_reason = self.enrich_keywords(
            customer_id=customer_id,
            seeds=seeds,
            max_results=max_results,
        )

        # min_volume filtresi
        if min_volume > 0:
            enriched = [e for e in enriched if e.avg_monthly_searches >= min_volume]

        # crud formatina donustur
        keyword_dicts = [
            {
                "keyword": e.keyword,
                "monthly_volume": e.avg_monthly_searches,
                "competition_score": e.competition_score,
                "trend_3m": e.trend_3m,
                "trend_12m": e.trend_12m,
                "data_source": "google_ads_api",
                "sector": sector,
                "target_market": target_market,
                "is_active": True,
            }
            for e in enriched
        ]

        from app.database.crud import create_keywords_bulk
        from app.database.models import Keyword as KWModel

        # already_existing sayisi icin: sadece import batch'teki textleri DB'de sorgula.
        # Tum aktif keywordleri belleğe almak yerine dar sorgu — buyuk veri setlerinde performansi korur.
        incoming_texts_lower = [kd["keyword"].lower().strip() for kd in keyword_dicts]
        existing_matches = (
            db.query(KWModel.keyword)
            .filter(KWModel.is_active == True)
            .filter(KWModel.keyword.in_([kd["keyword"] for kd in keyword_dicts]))
            .all()
        )
        existing_texts = {row.keyword.lower().strip() for row in existing_matches}
        # NOT: Bu sayac yaklasiktir. Fuzzy eslesmeler create_keywords_bulk icinde
        # hesaplanir; skipped_fuzzy = len(batch) - already_existing - actual_created
        # formuluyle turetidiginden concurrent import'larda hafif sapabilir.
        already_existing = sum(
            1 for t in incoming_texts_lower
            if t in existing_texts
        )

        created_count = int(create_keywords_bulk(db, keyword_dicts) or 0)
        skipped_fuzzy = len(keyword_dicts) - already_existing - created_count

        return ImportResult(
            created=created_count,
            already_existing=already_existing,
            skipped_fuzzy=max(0, skipped_fuzzy),
            truncated=truncated,
            truncated_reason=truncated_reason,
        )

    def list_campaigns(self, customer_id: str) -> List[CampaignInfo]:
        """List campaigns for customer. Used by UI dropdown."""
        client = _call_with_retry(self._build_client)
        svc = client.get_service("GoogleAdsService")
        query = """
            SELECT
                campaign.id,
                campaign.name,
                campaign.status
            FROM campaign
            WHERE campaign.status != 'REMOVED'
              AND campaign.advertising_channel_type = 'SEARCH'
            ORDER BY campaign.name ASC
        """

        def _fetch():
            stream = svc.search_stream(customer_id=customer_id, query=query)
            results: List[CampaignInfo] = []
            for batch in stream:
                for row in batch.results:
                    results.append(
                        CampaignInfo(
                            campaign_id=str(row.campaign.id),
                            campaign_name=row.campaign.name or "",
                            status=row.campaign.status.name,
                        )
                    )
            return results

        return _call_with_retry(_fetch)

    def get_campaign_keywords(
        self,
        customer_id: str,
        campaign_id: Optional[str] = None,
        min_impressions: int = 0,
        date_range: str = "ALL_TIME",
        limit: int = 2000,
    ) -> List[CampaignKeyword]:
        """Fetch active campaign keywords from keyword_view. Does not write DB."""
        safe_campaign_id: Optional[int] = None
        if campaign_id is not None:
            try:
                safe_campaign_id = int(campaign_id)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"campaign_id must be numeric, got: {campaign_id!r}") from exc

        if date_range not in self._valid_date_ranges:
            raise ValueError(
                f"Invalid date_range: {date_range!r}. Allowed: {sorted(self._valid_date_ranges)}"
            )

        safe_limit = min(max(limit, 1), 2000)

        client = _call_with_retry(self._build_client)
        svc = client.get_service("GoogleAdsService")

        where_clauses = [
            "campaign.status != 'REMOVED'",
            "ad_group.status != 'REMOVED'",
            "ad_group_criterion.status != 'REMOVED'",
        ]
        if date_range != "ALL_TIME":
            where_clauses.append(f"segments.date DURING {date_range}")
        if safe_campaign_id is not None:
            where_clauses.append(f"campaign.id = {safe_campaign_id}")
        if min_impressions > 0:
            where_clauses.append(f"metrics.impressions >= {int(min_impressions)}")

        query = f"""
            SELECT
                campaign.name,
                campaign.id,
                ad_group.name,
                ad_group_criterion.keyword.text,
                ad_group_criterion.keyword.match_type,
                metrics.impressions,
                metrics.clicks,
                metrics.cost_micros,
                metrics.average_cpc,
                metrics.ctr
            FROM keyword_view
            WHERE {" AND ".join(where_clauses)}
            ORDER BY metrics.impressions DESC
            LIMIT {safe_limit}
        """

        def _fetch():
            stream = svc.search_stream(customer_id=customer_id, query=query)
            results: List[CampaignKeyword] = []
            for batch in stream:
                for row in batch.results:
                    keyword_text = getattr(row.ad_group_criterion.keyword, "text", "") or ""
                    if not keyword_text:
                        continue
                    results.append(
                        CampaignKeyword(
                            keyword=keyword_text,
                            match_type=row.ad_group_criterion.keyword.match_type.name,
                            campaign_name=row.campaign.name or "",
                            campaign_id=str(row.campaign.id),
                            ad_group_name=row.ad_group.name or "",
                            impressions=int(row.metrics.impressions or 0),
                            clicks=int(row.metrics.clicks or 0),
                            cost=round((row.metrics.cost_micros or 0) / 1_000_000, 4),
                            avg_cpc=round((row.metrics.average_cpc or 0) / 1_000_000, 4),
                            ctr=round(float(row.metrics.ctr or 0), 6),
                        )
                    )
            return results

        return _call_with_retry(_fetch)

    def import_campaign_keywords(
        self,
        db,
        customer_id: str,
        campaign_id: Optional[str] = None,
        min_impressions: int = 0,
        date_range: str = "ALL_TIME",
        limit: int = 2000,
        sector: Optional[str] = None,
        target_market: Optional[str] = None,
    ) -> ImportResult:
        """
        Import campaign keywords into DB.
        monthly_volume is mapped from impressions proxy for selected period.
        """
        keywords = self.get_campaign_keywords(
            customer_id=customer_id,
            campaign_id=campaign_id,
            min_impressions=min_impressions,
            date_range=date_range,
            limit=limit,
        )

        keyword_dicts = [
            {
                "keyword": kw.keyword,
                "monthly_volume": kw.impressions,
                "competition_score": 0.50,
                "trend_3m": 0.0,
                "trend_12m": 0.0,
                "data_source": "google_ads_api",
                "sector": sector,
                "target_market": target_market,
                "is_active": True,
            }
            for kw in keywords
        ]

        from app.database.crud import create_keywords_bulk
        from app.database.models import Keyword as KWModel

        incoming_texts_lower = [kd["keyword"].lower().strip() for kd in keyword_dicts]
        existing_matches = (
            db.query(KWModel.keyword)
            .filter(KWModel.is_active == True)
            .filter(KWModel.keyword.in_([kd["keyword"] for kd in keyword_dicts]))
            .all()
        )
        existing_texts = {row.keyword.lower().strip() for row in existing_matches}
        already_existing = sum(1 for t in incoming_texts_lower if t in existing_texts)

        created_count = int(create_keywords_bulk(db, keyword_dicts) or 0)
        skipped_fuzzy = len(keyword_dicts) - already_existing - created_count

        return ImportResult(
            created=created_count,
            already_existing=already_existing,
            skipped_fuzzy=max(0, skipped_fuzzy),
            truncated=False,
            truncated_reason=None,
        )
