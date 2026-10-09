import os
import sys
from decimal import Decimal
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.core.workspace_refresh import (
    ExistingWorkspaceKeyword,
    build_refreshed_workspace_keywords,
)


def test_refresh_merges_metrics_by_normalized_keyword():
    existing = [
        ExistingWorkspaceKeyword(
            keyword_id=1,
            keyword="FÖN TARAĞI",
            monthly_volume=10,
            trend_3m=Decimal("1.0"),
            trend_12m=Decimal("2.0"),
            competition_score=Decimal("0.20"),
            data_source="csv",
        )
    ]
    ideas = [
        SimpleNamespace(
            keyword="fon taragi",
            avg_monthly_searches=250,
            trend_3m=11.0,
            trend_12m=22.0,
            competition_score=0.7,
        )
    ]

    result = build_refreshed_workspace_keywords(existing, ideas)

    assert result["diff"] == {"refreshed": 1, "unchanged": 0, "added": 0, "removed": 0}
    assert result["rows"][0]["keyword_id"] == 1
    assert result["rows"][0]["monthly_volume"] == 250
    assert result["rows"][0]["data_source"] == "google_ads_api"


def test_refresh_preserves_existing_when_google_ads_has_no_exact_match():
    existing = [
        ExistingWorkspaceKeyword(
            keyword_id=7,
            keyword="sac bakim",
            monthly_volume=33,
            trend_3m=Decimal("3.0"),
            trend_12m=Decimal("4.0"),
            competition_score=Decimal("0.30"),
            data_source="manual",
        )
    ]
    ideas = [SimpleNamespace(keyword="baska kelime", avg_monthly_searches=100)]

    result = build_refreshed_workspace_keywords(existing, ideas)

    assert result["diff"]["unchanged"] == 1
    assert result["rows"][0]["keyword_id"] == 7
    assert result["rows"][0]["monthly_volume"] == 33
    assert result["rows"][0]["data_source"] == "manual"


def test_refresh_can_include_new_ideas():
    existing = [
        ExistingWorkspaceKeyword(
            keyword_id=1,
            keyword="sampuan",
            monthly_volume=10,
            trend_3m=0,
            trend_12m=0,
            competition_score=0.2,
            data_source="csv",
        )
    ]
    ideas = [
        SimpleNamespace(keyword="sampuan", avg_monthly_searches=20, trend_3m=1, trend_12m=2, competition_score=0.3),
        SimpleNamespace(keyword="sac kremi", avg_monthly_searches=40, trend_3m=3, trend_12m=4, competition_score=0.5),
    ]

    result = build_refreshed_workspace_keywords(existing, ideas, include_new_ideas=True)

    assert result["diff"]["refreshed"] == 1
    assert result["diff"]["added"] == 1
    assert result["rows"][1]["keyword_id"] is None
    assert result["rows"][1]["keyword"] == "sac kremi"
