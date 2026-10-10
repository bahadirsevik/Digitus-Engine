"""apply_import_plan chunk-commit + SAVEPOINT davranış-kilit testleri.

Perf refactor (stem-once + preload + chunk commit) sonrası eski per-keyword
commit semantiğinin korunduğunu kilitler:
  - bozuk tek kelime yalnızca kendini geri alır (savepoint izolasyonu)
  - aynı-batch duplicate'ler eski gibi race-skip olur
  - chunk sınırları sonuçları etkilemez
"""
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

import app.core.csv_import.import_plan as ip
from app.database.models import Keyword, WorkspaceKeyword


def _plan_payload(brand_profile_id: int, items: list[dict]) -> dict:
    return {"brand_profile_id": brand_profile_id, "accepted_plan": items}


def _create_item(text: str, vol: int = 100, cs: str = "0.4") -> dict:
    return {
        "action": "create_keyword",
        "keyword_id": None,
        "keyword_data": {
            "keyword": text,
            "normalized_keyword": None,  # apply normalize eder
            "monthly_volume": vol,
            "trend_3m": Decimal("0"),
            "trend_12m": Decimal("0"),
            "competition_score": Decimal(cs),
            "data_source": "csv",
        },
    }


def _ws_count(db, ws_id: int) -> int:
    return db.query(WorkspaceKeyword).filter(
        WorkspaceKeyword.brand_profile_id == ws_id
    ).count()


def test_apply_creates_all_and_commits_in_chunks(db_session, make_workspace):
    ws = make_workspace()
    n = ip.IMPORT_COMMIT_CHUNK_SIZE * 2 + 30  # chunk sinirlarini kessin
    items = [_create_item(f"perf kelime {i}") for i in range(n)]

    res = ip.apply_import_plan(db_session, _plan_payload(ws.id, items))

    assert res["created_new"] == n
    assert res["skipped_due_to_race"] == 0
    assert res["total_after"] == n
    assert _ws_count(db_session, ws.id) == n


def test_same_batch_duplicate_is_race_skipped_like_before(db_session, make_workspace):
    """Eski davranış: 2. özdeş item, 1.'in commit'ini görüp link→snapshot→race.
    Yeni davranış (in-memory map/set): aynı sonuç — created=1, race=1."""
    ws = make_workspace()
    items = [_create_item("tekrarlanan kelime"), _create_item("tekrarlanan kelime")]

    res = ip.apply_import_plan(db_session, _plan_payload(ws.id, items))

    assert res["created_new"] == 1
    assert res["skipped_due_to_race"] == 1
    assert _ws_count(db_session, ws.id) == 1
    # Global Keyword da tek olmalı (duplicate create yok)
    assert db_session.query(Keyword).filter(
        Keyword.keyword == "tekrarlanan kelime"
    ).count() == 1


def test_savepoint_isolates_single_failure(db_session, make_workspace, monkeypatch):
    """Chunk ortasındaki tek IntegrityError yalnızca o kelimeyi düşürmeli;
    öncesi ve sonrası kaydedilmeli (per-keyword commit izolasyonu korunur)."""
    ws = make_workspace()
    items = [_create_item(f"izolasyon kelime {i}") for i in range(5)]

    original = ip._create_workspace_link

    def exploding(db, **kwargs):
        kw_text = (kwargs.get("keyword_data") or {}).get("keyword", "")
        if kw_text == "izolasyon kelime 2":
            raise IntegrityError("stmt", {}, Exception("simulated dup"))
        return original(db, **kwargs)

    monkeypatch.setattr(ip, "_create_workspace_link", exploding)

    res = ip.apply_import_plan(db_session, _plan_payload(ws.id, items))

    assert res["created_new"] == 4
    assert res["skipped_due_to_race"] == 1
    saved = {
        kw.keyword
        for kw in db_session.query(Keyword)
        .join(WorkspaceKeyword, WorkspaceKeyword.keyword_id == Keyword.id)
        .filter(WorkspaceKeyword.brand_profile_id == ws.id)
        .all()
    }
    assert "izolasyon kelime 2" not in saved
    assert {"izolasyon kelime 0", "izolasyon kelime 1",
            "izolasyon kelime 3", "izolasyon kelime 4"} <= saved


def test_link_action_race_skip_via_preloaded_snapshot(db_session, make_workspace, make_keyword):
    """link_existing_keyword: workspace'te aynı snapshot varsa race-skip (eski
    per-item SELECT davranışı, ön-yüklenmiş set ile korunur)."""
    ws = make_workspace()
    kw = make_keyword("mevcut kelime", brand_profile_id=ws.id,
                      monthly_volume=100, competition_score=0.4)
    item = {
        "action": "link_existing_keyword",
        "keyword_id": kw.id,
        "keyword_data": {
            "keyword": "mevcut kelime",
            "normalized_keyword": None,
            "monthly_volume": 100,
            "competition_score": Decimal("0.4"),
            "data_source": "csv",
        },
    }
    before = _ws_count(db_session, ws.id)

    res = ip.apply_import_plan(db_session, _plan_payload(ws.id, [item]))

    assert res["skipped_due_to_race"] == 1
    assert res["created"] == 0
    assert _ws_count(db_session, ws.id) == before
