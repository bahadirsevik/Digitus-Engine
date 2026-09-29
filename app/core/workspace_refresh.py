"""Workspace keyword refresh helpers."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, Iterable, List

from app.core.keyword_normalize import normalize_keyword


@dataclass
class ExistingWorkspaceKeyword:
    keyword_id: int
    keyword: str
    monthly_volume: int
    trend_3m: Decimal | float
    trend_12m: Decimal | float
    competition_score: Decimal | float
    data_source: str
    sector: str | None = None
    target_market: str | None = None
    geo_target_id: str | None = None
    language_id: str | None = None
    notes: str | None = None


def build_refreshed_workspace_keywords(
    existing: Iterable[ExistingWorkspaceKeyword],
    refreshed_ideas: Iterable[Any],
    include_new_ideas: bool = False,
    data_source: str = "google_ads_api",
) -> Dict[str, Any]:
    """
    Merge Google Ads idea metrics into the existing workspace keyword set.

    Existing keywords are preserved if Google Ads does not return an exact
    normalized match. New ideas are included only when include_new_ideas=True.
    """
    existing_items = list(existing)
    existing_by_norm = {
        normalize_keyword(item.keyword): item
        for item in existing_items
    }
    idea_by_norm = {}
    for idea in refreshed_ideas:
        keyword = getattr(idea, "keyword", None)
        if not keyword:
            continue
        normalized = normalize_keyword(keyword)
        if normalized and normalized not in idea_by_norm:
            idea_by_norm[normalized] = idea

    refreshed_rows: List[Dict[str, Any]] = []
    refreshed = 0
    unchanged = 0

    for normalized, item in existing_by_norm.items():
        idea = idea_by_norm.get(normalized)
        if idea:
            refreshed += 1
            refreshed_rows.append({
                "keyword_id": item.keyword_id,
                "keyword": item.keyword,
                "monthly_volume": int(getattr(idea, "avg_monthly_searches", 0) or 0),
                "trend_3m": float(getattr(idea, "trend_3m", 0) or 0),
                "trend_12m": float(getattr(idea, "trend_12m", 0) or 0),
                "competition_score": float(getattr(idea, "competition_score", 0.5) or 0.5),
                "data_source": data_source,
                "sector": item.sector,
                "target_market": item.target_market,
                "geo_target_id": item.geo_target_id,
                "language_id": item.language_id,
                "notes": item.notes,
            })
        else:
            unchanged += 1
            refreshed_rows.append({
                "keyword_id": item.keyword_id,
                "keyword": item.keyword,
                "monthly_volume": int(item.monthly_volume or 0),
                "trend_3m": float(item.trend_3m or 0),
                "trend_12m": float(item.trend_12m or 0),
                "competition_score": float(item.competition_score or 0.5),
                "data_source": item.data_source,
                "sector": item.sector,
                "target_market": item.target_market,
                "geo_target_id": item.geo_target_id,
                "language_id": item.language_id,
                "notes": item.notes,
            })

    added_rows = []
    if include_new_ideas:
        for normalized, idea in idea_by_norm.items():
            if normalized in existing_by_norm:
                continue
            added_rows.append({
                "keyword_id": None,
                "keyword": idea.keyword,
                "monthly_volume": int(getattr(idea, "avg_monthly_searches", 0) or 0),
                "trend_3m": float(getattr(idea, "trend_3m", 0) or 0),
                "trend_12m": float(getattr(idea, "trend_12m", 0) or 0),
                "competition_score": float(getattr(idea, "competition_score", 0.5) or 0.5),
                "data_source": data_source,
                "sector": None,
                "target_market": None,
                "geo_target_id": None,
                "language_id": None,
                "notes": None,
            })

    return {
        "rows": refreshed_rows + added_rows,
        "diff": {
            "refreshed": refreshed,
            "unchanged": unchanged,
            "added": len(added_rows),
            "removed": 0,
        },
    }
