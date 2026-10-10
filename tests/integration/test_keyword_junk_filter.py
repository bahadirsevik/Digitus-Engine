"""Ortak cop kelime filtresi (plan 3.3): HER import yolunda uygulanir.

Her yolda: cop kelime atlanir + raporlanir; barkod+metin ("8681234567890 sensodyne")
KALIR.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

GTIN_KW = "8681234567890 sensodyne"


class FakeRedis:
    def __init__(self):
        self.store = {}

    def setex(self, key, ttl, value):
        self.store[key] = value
        return True

    def get(self, key):
        return self.store.get(key)


def _csv_bytes(*rows: str) -> bytes:
    header = (
        "Keyword\tAvg. monthly searches\tThree month change\tYoY change\t"
        "Competition\tCompetition (indexed value)\n"
    )
    text = "Keyword Stats\n1 Ocak 2025 - 31 Aralık 2025\n" + header + "\n".join(rows)
    return text.encode("utf-16-le")


def _ws_keywords(db_session, ws_id):
    from app.database.models import Keyword, WorkspaceKeyword

    rows = (
        db_session.query(Keyword.keyword)
        .join(WorkspaceKeyword, WorkspaceKeyword.keyword_id == Keyword.id)
        .filter(WorkspaceKeyword.brand_profile_id == ws_id)
        .all()
    )
    return sorted(r[0] for r in rows)


# ── CSV preview + commit ──────────────────────────────────────────────────────

def test_csv_preview_and_commit_skip_junk_keep_gtin(client, make_workspace, db_session, monkeypatch):
    from app.api.v1 import keywords

    redis = FakeRedis()
    monkeypatch.setattr(keywords, "_get_redis_client", lambda: redis)
    ws = make_workspace(name="Junk CSV")
    resp = client.post(
        "/api/v1/keywords/upload-csv",
        data={"brand_profile_id": str(ws.id), "dry_run": "true"},
        files={
            "file": (
                "k.csv",
                _csv_bytes(
                    "!!!\t100\t0%\t0%\tHigh\t70",
                    "12.5\t100\t0%\t0%\tHigh\t70",
                    f"{GTIN_KW}\t900\t0%\t0%\tHigh\t70",
                ),
                "text/csv",
            )
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["accepted"] == 1
    assert len(data["junk_rows"]) == 2
    assert data["requested"] == 3

    commit = client.post(
        "/api/v1/keywords/upload-csv/commit",
        json={"brand_profile_id": ws.id, "dry_run_token": data["dry_run_token"]},
    )
    assert commit.status_code == 200
    assert commit.json()["created"] == 1
    assert _ws_keywords(db_session, ws.id) == [GTIN_KW]


def test_build_import_plan_defends_against_junk_from_non_csv_callers(db_session, make_workspace):
    from app.core.csv_import.import_plan import build_import_plan

    ws = make_workspace(name="Junk plan")
    plan = build_import_plan(
        db_session,
        brand_profile_id=ws.id,
        keyword_rows=[
            {"keyword": "123", "monthly_volume": 10, "competition_score": 0.5},
            {"keyword": "   ", "monthly_volume": 10, "competition_score": 0.5},
            {"keyword": GTIN_KW, "monthly_volume": 10, "competition_score": 0.5},
        ],
        parser_meta={},
        source_file_name="x.csv",
    )
    assert len(plan.junk_rows) == 2
    assert {j["matched"] for j in plan.junk_rows} == {"numeric_only", "empty"}
    assert [a["keyword_data"]["keyword"] for a in plan.accepted_plan] == [GTIN_KW]


# ── POST /keywords/ (single) ──────────────────────────────────────────────────

@pytest.mark.parametrize("junk", ["123", "!!!", "   "])
def test_single_create_junk_returns_400(client, make_workspace, junk):
    ws = make_workspace(name="Junk single")
    resp = client.post(
        "/api/v1/keywords/",
        json={"keyword": junk, "brand_profile_id": ws.id, "monthly_volume": 10},
    )
    assert resp.status_code == 400
    assert "Geçersiz kelime" in resp.json()["detail"]


def test_single_create_junk_force_include_still_400(client, make_workspace):
    ws = make_workspace(name="Junk single force")
    resp = client.post(
        "/api/v1/keywords/",
        json={"keyword": "123", "brand_profile_id": ws.id, "monthly_volume": 10, "force_include": True},
    )
    assert resp.status_code == 400


def test_single_create_gtin_keyword_is_kept(client, make_workspace):
    ws = make_workspace(name="Junk single keep")
    resp = client.post(
        "/api/v1/keywords/",
        json={"keyword": GTIN_KW, "brand_profile_id": ws.id, "monthly_volume": 10},
    )
    assert resp.status_code == 201


# ── POST /keywords/import (manual, Geri al, enrich, url-seed share this) ──────

def test_import_skips_junk_reports_and_keeps_gtin(client, make_workspace, db_session):
    ws = make_workspace(name="Junk import")
    resp = client.post(
        "/api/v1/keywords/import",
        json={
            "brand_profile_id": ws.id,
            "keywords": [
                {"keyword": "   ", "monthly_volume": 10},
                {"keyword": "123", "monthly_volume": 10},
                {"keyword": "%%", "monthly_volume": 10},
                {"keyword": GTIN_KW, "monthly_volume": 10},
            ],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["created"] == 1
    assert data["skipped_junk"] == 3
    assert data["skipped"] == 3
    junk_details = [d for d in data["skipped_details"] if d["reason"] == "skipped_junk"]
    assert len(junk_details) == 3
    assert {d["matched"] for d in junk_details} == {"empty", "numeric_only", "symbol_only"}
    assert _ws_keywords(db_session, ws.id) == [GTIN_KW]


def test_import_force_include_does_not_bypass_junk(client, make_workspace, db_session):
    ws = make_workspace(name="Junk import force")
    resp = client.post(
        "/api/v1/keywords/import",
        json={
            "brand_profile_id": ws.id,
            "keywords": [
                {"keyword": "123", "monthly_volume": 10, "force_include": True},
                {"keyword": GTIN_KW, "monthly_volume": 10, "force_include": True},
            ],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["skipped_junk"] == 1
    assert data["created"] == 1
    assert _ws_keywords(db_session, ws.id) == [GTIN_KW]


# ── POST /google-ads/import + /campaigns/keywords/import (legacy global) ──────

def _enriched(keyword, vol=100):
    from app.integrations.google_ads.service import EnrichedKeyword

    return EnrichedKeyword(
        keyword=keyword,
        avg_monthly_searches=vol,
        competition="HIGH",
        competition_index=70,
        competition_score=0.7,
        cpc_low=0.1,
        cpc_high=0.5,
        trend_3m=0.0,
        trend_12m=0.0,
        monthly_volumes_raw=[],
    )


def _global_keywords(db_session):
    from app.database.models import Keyword

    return sorted(r[0] for r in db_session.query(Keyword.keyword).all())


def test_google_ads_import_skips_junk_counts_separately(client, db_session, monkeypatch):
    from app.api.v1 import google_ads
    from app.integrations.google_ads.service import GoogleAdsService

    svc = GoogleAdsService(SimpleNamespace())
    monkeypatch.setattr(
        svc,
        "enrich_keywords",
        lambda **kw: ([_enriched("123"), _enriched("!!!"), _enriched(GTIN_KW)], False, None),
    )
    monkeypatch.setattr(google_ads, "_get_service", lambda: svc)

    resp = client.post(
        "/api/v1/google-ads/import",
        json={"customer_id": "1234567890", "seeds": ["x"]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["created"] == 1
    assert data["skipped_junk"] == 2
    assert data["skipped_fuzzy"] == 0  # cop kelimeler fuzzy diye sayilmaz
    assert _global_keywords(db_session) == [GTIN_KW]


def test_google_ads_campaign_import_skips_junk_counts_separately(client, db_session, monkeypatch):
    from app.api.v1 import google_ads
    from app.integrations.google_ads.service import CampaignKeyword, GoogleAdsService

    def _ck(text):
        return CampaignKeyword(
            keyword=text,
            match_type="BROAD",
            campaign_name="c",
            campaign_id="1",
            ad_group_name="g",
            impressions=500,
            clicks=5,
            cost=1.0,
            avg_cpc=0.2,
            ctr=0.01,
        )

    svc = GoogleAdsService(SimpleNamespace())
    monkeypatch.setattr(
        svc,
        "get_campaign_keywords",
        lambda **kw: [_ck("50%"), _ck("   "), _ck("iphone 15 pro 256")],
    )
    monkeypatch.setattr(google_ads, "_get_service", lambda: svc)

    resp = client.post(
        "/api/v1/google-ads/campaigns/keywords/import",
        json={"customer_id": "1234567890"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["created"] == 1
    assert data["skipped_junk"] == 2
    assert data["skipped_fuzzy"] == 0
    assert _global_keywords(db_session) == ["iphone 15 pro 256"]
