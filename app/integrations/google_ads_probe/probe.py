"""
Standalone Google Ads API probe.

Purpose:
- Validate credentials and access level.
- Measure response shape and latency.
- Collect diagnostics before product integration.

DEPRECATED:
This module is kept only for backward compatibility.
Use isolated lab CLI instead:
    python -m labs.google_ads_lab.cli ideas-probe
"""

from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from dotenv import load_dotenv


DEFAULT_LANGUAGE_ID = "1037"  # Turkish
DEFAULT_GEO_TARGET_ID = "2792"  # Turkey
DEFAULT_PAGE_SIZE = 1000
DEFAULT_MAX_RESULTS = 300
DEFAULT_OUTPUT = "artifacts/google_ads_probe_report.json"


@dataclass
class ProbeConfig:
    developer_token: str
    client_id: str
    client_secret: str
    refresh_token: str
    login_customer_id: str
    customer_id: str
    language_id: str
    geo_target_id: str
    page_size: int
    max_results: int
    seeds: List[str]
    page_url: Optional[str]
    output: str

    @classmethod
    def from_env(
        cls,
        seeds_override: Optional[str] = None,
        page_url: Optional[str] = None,
        page_size: Optional[int] = None,
        max_results: Optional[int] = None,
        output: Optional[str] = None,
    ) -> "ProbeConfig":
        seeds_raw = (
            seeds_override
            if seeds_override is not None
            else os.getenv("GOOGLE_ADS_PROBE_SEEDS", "")
        )
        seeds = [item.strip() for item in seeds_raw.split(",") if item.strip()]

        return cls(
            developer_token=os.getenv("GOOGLE_ADS_DEVELOPER_TOKEN", "").strip(),
            client_id=os.getenv("GOOGLE_ADS_CLIENT_ID", "").strip(),
            client_secret=os.getenv("GOOGLE_ADS_CLIENT_SECRET", "").strip(),
            refresh_token=os.getenv("GOOGLE_ADS_REFRESH_TOKEN", "").strip(),
            login_customer_id=os.getenv("GOOGLE_ADS_LOGIN_CUSTOMER_ID", "").strip(),
            customer_id=os.getenv("GOOGLE_ADS_CUSTOMER_ID", "").strip(),
            language_id=os.getenv("GOOGLE_ADS_LANGUAGE_ID", DEFAULT_LANGUAGE_ID).strip(),
            geo_target_id=os.getenv("GOOGLE_ADS_GEO_TARGET_ID", DEFAULT_GEO_TARGET_ID).strip(),
            page_size=page_size or int(os.getenv("GOOGLE_ADS_PROBE_PAGE_SIZE", str(DEFAULT_PAGE_SIZE))),
            max_results=max_results or int(os.getenv("GOOGLE_ADS_PROBE_MAX_RESULTS", str(DEFAULT_MAX_RESULTS))),
            seeds=seeds,
            page_url=page_url,
            output=output or DEFAULT_OUTPUT,
        )

    def validate(self) -> List[str]:
        issues: List[str] = []
        required_fields = {
            "GOOGLE_ADS_DEVELOPER_TOKEN": self.developer_token,
            "GOOGLE_ADS_CLIENT_ID": self.client_id,
            "GOOGLE_ADS_CLIENT_SECRET": self.client_secret,
            "GOOGLE_ADS_REFRESH_TOKEN": self.refresh_token,
            "GOOGLE_ADS_LOGIN_CUSTOMER_ID": self.login_customer_id,
            "GOOGLE_ADS_CUSTOMER_ID": self.customer_id,
        }
        for key, value in required_fields.items():
            if not value:
                issues.append(f"Missing required env: {key}")

        if not self.seeds and not self.page_url:
            issues.append("Provide at least one seed keyword or --page-url.")
        if len(self.seeds) > 20:
            issues.append("Google Ads keyword_seed supports at most 20 keywords.")
        if self.page_size < 1 or self.page_size > 10000:
            issues.append("GOOGLE_ADS_PROBE_PAGE_SIZE must be between 1 and 10000.")
        if self.max_results < 1:
            issues.append("GOOGLE_ADS_PROBE_MAX_RESULTS must be >= 1.")

        return issues


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extract_error_code(error_code: Any) -> Dict[str, Optional[str]]:
    code_type = None
    code_value = None
    if hasattr(error_code, "WhichOneof"):
        code_type = error_code.WhichOneof("error_code")
        if code_type:
            enum_value = getattr(error_code, code_type, None)
            if enum_value is not None:
                code_value = getattr(enum_value, "name", str(enum_value))
    return {"type": code_type, "value": code_value}


def _extract_google_ads_errors(exc: Any) -> List[Dict[str, Any]]:
    parsed: List[Dict[str, Any]] = []
    failure = getattr(exc, "failure", None)
    if not failure:
        return parsed

    for item in getattr(failure, "errors", []):
        location = []
        if getattr(item, "location", None):
            for field in item.location.field_path_elements:
                location.append(getattr(field, "field_name", ""))
        parsed.append(
            {
                "message": getattr(item, "message", ""),
                "trigger": str(getattr(item, "trigger", "")) if getattr(item, "trigger", None) else None,
                "field_path": ".".join([x for x in location if x]) or None,
                "error_code": _extract_error_code(getattr(item, "error_code", None)),
            }
        )
    return parsed


def _build_client(config: ProbeConfig) -> Any:
    from google.ads.googleads.client import GoogleAdsClient

    payload = {
        "developer_token": config.developer_token,
        "client_id": config.client_id,
        "client_secret": config.client_secret,
        "refresh_token": config.refresh_token,
        "login_customer_id": config.login_customer_id,
        "use_proto_plus": True,
    }
    return GoogleAdsClient.load_from_dict(payload)


def _build_request(client: Any, config: ProbeConfig) -> Any:
    request = client.get_type("GenerateKeywordIdeasRequest")
    request.customer_id = config.customer_id
    request.language = client.get_service("GoogleAdsService").language_constant_path(config.language_id)
    request.geo_target_constants.append(
        client.get_service("GeoTargetConstantService").geo_target_constant_path(config.geo_target_id)
    )
    request.page_size = config.page_size
    request.include_adult_keywords = False
    request.keyword_plan_network = client.enums.KeywordPlanNetworkEnum.GOOGLE_SEARCH

    if config.seeds and config.page_url:
        request.keyword_and_url_seed.url = config.page_url
        request.keyword_and_url_seed.keywords.extend(config.seeds)
    elif config.seeds:
        request.keyword_seed.keywords.extend(config.seeds)
    elif config.page_url:
        request.url_seed.url = config.page_url

    return request


def _collect_results(response: Sequence[Any], max_results: int) -> Dict[str, Any]:
    ideas: List[Dict[str, Any]] = []
    coverage = {
        "with_avg_monthly_searches": 0,
        "with_competition_index": 0,
        "with_cpc_low": 0,
        "with_cpc_high": 0,
    }

    count = 0
    for row in response:
        count += 1
        metrics = getattr(row, "keyword_idea_metrics", None)

        avg_searches = getattr(metrics, "avg_monthly_searches", None) if metrics else None
        competition_index = getattr(metrics, "competition_index", None) if metrics else None
        cpc_low_micros = getattr(metrics, "low_top_of_page_bid_micros", None) if metrics else None
        cpc_high_micros = getattr(metrics, "high_top_of_page_bid_micros", None) if metrics else None
        competition = getattr(getattr(metrics, "competition", None), "name", None) if metrics else None

        if avg_searches:
            coverage["with_avg_monthly_searches"] += 1
        if competition_index is not None:
            coverage["with_competition_index"] += 1
        if cpc_low_micros:
            coverage["with_cpc_low"] += 1
        if cpc_high_micros:
            coverage["with_cpc_high"] += 1

        if len(ideas) < min(10, max_results):
            ideas.append(
                {
                    "keyword": getattr(row, "text", None),
                    "avg_monthly_searches": avg_searches or 0,
                    "competition": competition,
                    "competition_index": competition_index,
                    "cpc_low": round((cpc_low_micros or 0) / 1_000_000, 4),
                    "cpc_high": round((cpc_high_micros or 0) / 1_000_000, 4),
                    "monthly_points": len(getattr(metrics, "monthly_search_volumes", []) or []),
                }
            )

        if count >= max_results:
            break

    return {"count": count, "coverage": coverage, "sample_results": ideas}


def run_probe(config: ProbeConfig) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "started_at_utc": _utc_now(),
        "status": "failed",
        "request_summary": {
            "customer_id_suffix": config.customer_id[-4:] if config.customer_id else None,
            "login_customer_id_suffix": config.login_customer_id[-4:] if config.login_customer_id else None,
            "seed_count": len(config.seeds),
            "seeds": config.seeds,
            "page_url": config.page_url,
            "language_id": config.language_id,
            "geo_target_id": config.geo_target_id,
            "page_size": config.page_size,
            "max_results": config.max_results,
        },
        "timing_ms": {},
        "warnings": [],
        "errors": [],
    }

    issues = config.validate()
    if issues:
        result["errors"] = [{"type": "ConfigError", "message": msg} for msg in issues]
        result["finished_at_utc"] = _utc_now()
        return result

    t0 = time.perf_counter()

    try:
        client = _build_client(config)
        result["timing_ms"]["client_init"] = round((time.perf_counter() - t0) * 1000, 2)

        service = client.get_service("KeywordPlanIdeaService")
        request = _build_request(client, config)

        t1 = time.perf_counter()
        response = service.generate_keyword_ideas(request=request)
        result["timing_ms"]["api_call"] = round((time.perf_counter() - t1) * 1000, 2)

        collected = _collect_results(response, config.max_results)
        result["response_summary"] = collected

        if collected["count"] == 0:
            result["warnings"].append("API call succeeded but returned 0 ideas.")

        result["status"] = "success"
    except ModuleNotFoundError as exc:
        result["errors"].append(
            {
                "type": "DependencyError",
                "message": f"{exc}. Install dependencies with: pip install -r requirements.txt",
            }
        )
    except Exception as exc:  # pragma: no cover - network/API dependent
        error_type = type(exc).__name__
        entry: Dict[str, Any] = {"type": error_type, "message": str(exc)}

        request_id = getattr(exc, "request_id", None)
        if request_id:
            entry["request_id"] = request_id

        parsed_errors = _extract_google_ads_errors(exc)
        if parsed_errors:
            entry["google_ads_errors"] = parsed_errors

        result["errors"].append(entry)
    finally:
        result["timing_ms"]["total"] = round((time.perf_counter() - t0) * 1000, 2)
        result["finished_at_utc"] = _utc_now()

    return result


def _write_report(path: str, payload: Dict[str, Any]) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Standalone Google Ads API probe")
    parser.add_argument(
        "--seeds",
        type=str,
        default=None,
        help="Comma-separated seed keywords. Overrides GOOGLE_ADS_PROBE_SEEDS.",
    )
    parser.add_argument(
        "--page-url",
        type=str,
        default=None,
        help="Optional URL seed.",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=None,
        help="Google Ads page_size (1-10000). Overrides env.",
    )
    parser.add_argument(
        "--max-results",
        type=int,
        default=None,
        help="Maximum number of ideas to read from response. Overrides env.",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help=f"Report path. Default: {DEFAULT_OUTPUT}",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    load_dotenv()
    args = _build_arg_parser().parse_args(argv)
    config = ProbeConfig.from_env(
        seeds_override=args.seeds,
        page_url=args.page_url,
        page_size=args.page_size,
        max_results=args.max_results,
        output=args.output,
    )
    report = run_probe(config)
    _write_report(config.output, report)

    print(f"[google-ads-probe] status={report['status']}")
    print(f"[google-ads-probe] report={config.output}")
    if report.get("errors"):
        print(f"[google-ads-probe] errors={len(report['errors'])}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
