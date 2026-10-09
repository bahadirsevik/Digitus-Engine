"""Import kapısı: yasaklı tema eleme + havuz limiti (UI sadeleştirme Faz 2).

Zorunlu senaryolar (plan):
- exclude "temettü takibi" → "temettü takip programı" elenir AMA
  "portföy takip programı" geçer (tam-stem kuralı).
- force_include YALNIZ tema filtresini atlar; duplicate ve limiti ATLAMAZ.
- Duplicate satır limit dolunca 'limit_exceeded' DEĞİL kendi nedeniyle raporlanır.
- requested = created + skipped değişmezi.
- exclude_themes'siz workspace'te davranış değişmez (regresyon kilidi).

P1.6 GÜNCELLEMESİ (07.08): hard eleme artık YALNIZ kullanıcı onaylı temalardan
gelir. Bu dosyadaki senaryolar `excluded_info` (kullanıcının "mutlaka olmasın"
metni) ile kurulur — eskiden yalnız `profile_data.exclude_themes` yeterliydi ve
bu, AI'ın ürettiği temanın da hard-drop yapması demekti. AI-only temanın artık
elemediği `test_import_theme_policy.py` içinde doğrulanır.
"""
from __future__ import annotations

import app.api.v1.keywords as keywords_api
from app.core.csv_import.import_plan import (
    apply_import_plan,
    build_import_plan,
    compact_plan_payload,
    import_plan_to_payload,
)
from app.core.policy.import_gate import resolve_import_theme_policy


THEMED_PROFILE = {
    "company_name": "Hissefy",
    "sector": "Finansal teknoloji",
    "target_audience": "Yatirimcilar",
    "products": ["Analiz Platformu"],
    "brand_terms": ["Hissefy"],
    "exclude_themes": ["temettü takibi", "kripto para"],
    "anchor_texts": ["Analiz Platformu"],
}

# P1.6: temaları KULLANICI onaylı yapan alan — hard eleme bunu gerektirir.
USER_EXCLUDED_INFO = "temettü takibi, kripto para"


def _import(client, ws_id, keywords):
    return client.post(
        "/api/v1/keywords/import",
        json={"brand_profile_id": ws_id, "keywords": keywords},
    )


# ── Tema eleme ────────────────────────────────────────────────────


def test_theme_gate_blocks_matching_and_passes_partial_overlap(client, make_workspace):
    ws = make_workspace(
        name="Theme gate",
        profile_data=dict(THEMED_PROFILE),
        excluded_info=USER_EXCLUDED_INFO,
    )

    res = _import(client, ws.id, [
        {"keyword": "temettü takip programı", "monthly_volume": 100},
        {"keyword": "portföy takip programı", "monthly_volume": 100},
        {"keyword": "kripto para sinyalleri", "monthly_volume": 50},
    ])

    assert res.status_code == 200
    data = res.json()
    assert data["created"] == 1  # yalnız portföy takip programı
    assert data["skipped_theme"] == 2
    assert data["requested"] == data["created"] + data["skipped"]
    themed = [d for d in data["skipped_details"] if d["reason"] == "skipped_theme"]
    assert {d["keyword"] for d in themed} == {"temettü takip programı", "kripto para sinyalleri"}
    assert {d["matched"] for d in themed} == {"temettü takibi", "kripto para"}
    # Geri-al payload'ı için metrikler detayda taşınır
    assert all(d.get("monthly_volume") is not None for d in themed)


def test_force_include_bypasses_theme_only(client, make_workspace):
    ws = make_workspace(
        name="Force include",
        profile_data=dict(THEMED_PROFILE),
        excluded_info=USER_EXCLUDED_INFO,
    )

    r1 = _import(client, ws.id, [
        {"keyword": "temettü takip programı", "monthly_volume": 100, "force_include": True},
    ])
    assert r1.status_code == 200
    assert r1.json()["created"] == 1
    assert r1.json()["skipped_theme"] == 0

    # force_include duplicate kontrolünü ATLAMAZ → aynı satır yine skipped_exact
    r2 = _import(client, ws.id, [
        {"keyword": "temettü takip programı", "monthly_volume": 100, "force_include": True},
    ])
    assert r2.status_code == 200
    data = r2.json()
    assert data["created"] == 0
    assert data["skipped_details"][0]["reason"] == "skipped_exact"


def test_no_exclude_themes_behavior_unchanged(client, make_workspace):
    ws = make_workspace(name="No themes", profile_data=None)

    res = _import(client, ws.id, [
        {"keyword": "temettü takip programı", "monthly_volume": 100},
    ])
    assert res.status_code == 200
    assert res.json()["created"] == 1
    assert res.json()["skipped_theme"] == 0


# ── Havuz limiti ──────────────────────────────────────────────────


def test_pool_limit_blocks_new_but_reports_duplicates_correctly(
    client, make_workspace, monkeypatch
):
    ws = make_workspace(name="Pool limit", profile_data=None)
    monkeypatch.setattr(keywords_api, "WORKSPACE_KEYWORD_LIMIT", 2)

    r1 = _import(client, ws.id, [
        {"keyword": "kelime bir", "monthly_volume": 10},
        {"keyword": "kelime iki", "monthly_volume": 20},
    ])
    assert r1.json()["created"] == 2
    assert r1.json()["pool_limit"] == 2
    assert r1.json()["pool_total"] == 2

    r2 = _import(client, ws.id, [
        {"keyword": "kelime bir", "monthly_volume": 10},   # exact dup → skipped_exact
        {"keyword": "kelime üç", "monthly_volume": 30},    # yeni → limit_exceeded
    ])
    data = r2.json()
    assert data["created"] == 0
    assert data["skipped_limit"] == 1
    assert data["requested"] == data["created"] + data["skipped"]
    reasons = {d["keyword"]: d["reason"] for d in data["skipped_details"]}
    assert reasons["kelime bir"] == "skipped_exact"
    assert reasons["kelime üç"] == "limit_exceeded"
    assert data["total_after"] == 2  # havuz büyümedi


def test_force_include_does_not_bypass_pool_limit(client, make_workspace, monkeypatch):
    ws = make_workspace(
        name="Limit force",
        profile_data=dict(THEMED_PROFILE),
        excluded_info=USER_EXCLUDED_INFO,
    )
    monkeypatch.setattr(keywords_api, "WORKSPACE_KEYWORD_LIMIT", 1)

    r1 = _import(client, ws.id, [{"keyword": "kelime bir", "monthly_volume": 10}])
    assert r1.json()["created"] == 1

    r2 = _import(client, ws.id, [
        {"keyword": "temettü takip programı", "monthly_volume": 100, "force_include": True},
    ])
    data = r2.json()
    assert data["created"] == 0
    assert data["skipped_limit"] == 1
    assert data["skipped_details"][0]["reason"] == "limit_exceeded"


# ── CSV yolu ──────────────────────────────────────────────────────


def _csv_rows(*rows):
    return [
        {
            "keyword": kw,
            "monthly_volume": vol,
            "competition_score": 0.5,
            "trend_3m": 0,
            "trend_12m": 0,
            "data_source": "csv",
        }
        for kw, vol in rows
    ]


def test_csv_plan_marks_theme_rows_and_warns_on_limit(db_session, make_workspace):
    ws = make_workspace(
        name="CSV theme",
        profile_data=dict(THEMED_PROFILE),
        excluded_info=USER_EXCLUDED_INFO,
    )

    plan = build_import_plan(
        db_session,
        brand_profile_id=ws.id,
        keyword_rows=_csv_rows(
            ("temettü takip programı", 100),
            ("portföy takip programı", 100),
        ),
        parser_meta={},
        source_file_name="test.csv",
        theme_policy=resolve_import_theme_policy(ws),
        pool_limit=1,
    )

    assert plan.summary["skipped_theme"] == 1
    assert plan.summary["accepted"] == 1
    payload = import_plan_to_payload(plan)
    assert payload["skipped_theme"] == 1
    assert payload["theme_excluded"][0]["keyword"] == "temettü takip programı"
    assert payload["theme_excluded"][0]["matched_keyword"] == "temettü takibi"
    # accepted(1) + total_before(0) = 1 → limit(1) aşılmıyor; uyarı yok
    assert "UYARI" not in payload["message"]

    plan2 = build_import_plan(
        db_session,
        brand_profile_id=ws.id,
        keyword_rows=_csv_rows(("kelime bir", 10), ("kelime iki", 20)),
        parser_meta={},
        source_file_name="test.csv",
        pool_limit=1,
    )
    payload2 = import_plan_to_payload(plan2)
    assert "UYARI" in payload2["message"]  # 2 aday > limit 1


def test_csv_apply_enforces_pool_limit(db_session, make_workspace):
    ws = make_workspace(name="CSV limit", profile_data=None)

    plan = build_import_plan(
        db_session,
        brand_profile_id=ws.id,
        keyword_rows=_csv_rows(("kelime bir", 10), ("kelime iki", 20), ("kelime üç", 30)),
        parser_meta={},
        source_file_name="test.csv",
    )
    result = apply_import_plan(db_session, compact_plan_payload(plan), pool_limit=2)

    assert result["created"] == 2
    assert result["skipped_limit"] == 1
    assert result["total_after"] == 2
    assert "havuz limiti" in result["message"]
