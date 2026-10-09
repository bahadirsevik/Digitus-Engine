"""
/keywords/import endpoint — Option B snapshot semantics + scoring collision tests.

P6 yayılımı: CSV upload ile aynı davranış artık /keywords/import içinde de geçerli.
"""
from __future__ import annotations


# ── /keywords/import Option B regression ──────────────────────────────────────

def test_import_same_keyword_same_metrics_is_skipped(client, make_workspace):
    ws = make_workspace(name="Import idempotency")

    def _do_import(vol, cs):
        return client.post(
            "/api/v1/keywords/import",
            json={
                "brand_profile_id": ws.id,
                "keywords": [{"keyword": "organik şampuan", "monthly_volume": vol, "competition_score": cs}],
            },
        )

    r1 = _do_import(1200, 0.7)
    assert r1.status_code == 200
    assert r1.json()["created"] == 1
    assert r1.json()["total_after"] == 1

    r2 = _do_import(1200, 0.7)
    assert r2.status_code == 200
    assert r2.json()["created"] == 0   # same snapshot → skipped_exact
    assert r2.json()["total_after"] == 1  # workspace count unchanged


def test_import_same_keyword_different_metrics_creates_new_snapshot(
    client, make_workspace
):
    ws = make_workspace(name="Import diff metrics")

    r1 = client.post(
        "/api/v1/keywords/import",
        json={"brand_profile_id": ws.id, "keywords": [
            {"keyword": "organik şampuan", "monthly_volume": 1200, "competition_score": 0.7},
        ]},
    )
    assert r1.status_code == 200
    assert r1.json()["created"] == 1

    r2 = client.post(
        "/api/v1/keywords/import",
        json={"brand_profile_id": ws.id, "keywords": [
            {"keyword": "organik şampuan", "monthly_volume": 500, "competition_score": 0.2},
        ]},
    )
    assert r2.status_code == 200
    assert r2.json()["created"] == 1   # different metrics → new snapshot
    assert r2.json()["total_after"] == 2


def test_import_two_snapshots_expose_distinct_wk_ids(client, make_workspace):
    ws = make_workspace(name="Import wk_id expose")

    client.post("/api/v1/keywords/import", json={
        "brand_profile_id": ws.id,
        "keywords": [{"keyword": "organik şampuan", "monthly_volume": 1200, "competition_score": 0.7}],
    })
    client.post("/api/v1/keywords/import", json={
        "brand_profile_id": ws.id,
        "keywords": [{"keyword": "organik şampuan", "monthly_volume": 500, "competition_score": 0.2}],
    })

    resp = client.get("/api/v1/keywords/", params={"brand_profile_id": ws.id})
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 2
    wk_ids = [item["wk_id"] for item in items]
    assert wk_ids[0] is not None and wk_ids[1] is not None
    assert wk_ids[0] != wk_ids[1]


def test_import_delete_one_snapshot_leaves_other(client, make_workspace):
    ws = make_workspace(name="Import delete one")

    client.post("/api/v1/keywords/import", json={
        "brand_profile_id": ws.id,
        "keywords": [{"keyword": "organik şampuan", "monthly_volume": 1200, "competition_score": 0.7}],
    })
    client.post("/api/v1/keywords/import", json={
        "brand_profile_id": ws.id,
        "keywords": [{"keyword": "organik şampuan", "monthly_volume": 500, "competition_score": 0.2}],
    })

    items = client.get("/api/v1/keywords/", params={"brand_profile_id": ws.id}).json()["items"]
    assert len(items) == 2

    wk_id_to_delete = items[0]["wk_id"]
    del_resp = client.delete(
        f"/api/v1/keywords/{wk_id_to_delete}",
        params={"brand_profile_id": ws.id},
    )
    assert del_resp.status_code == 204

    remaining = client.get("/api/v1/keywords/", params={"brand_profile_id": ws.id}).json()["items"]
    assert len(remaining) == 1
    assert remaining[0]["wk_id"] != wk_id_to_delete


# ── Scoring collision regression ──────────────────────────────────────────────

def test_two_metric_snapshots_produce_two_separate_keyword_scores(
    client, db_session, make_workspace, make_scoring_run
):
    """
    Option B garantisi: aynı keyword text, farklı metrikler → ayrı Keyword.id →
    score_engine combined[kw.id] çakışması olmaz → iki ayrı KeywordScore satırı.
    """
    from app.core.scoring.score_engine import ScoreEngine
    from app.database.models import KeywordScore

    ws = make_workspace(name="Scoring collision", status="confirmed")

    # İki farklı metrik snapshot'ı import et
    r1 = client.post("/api/v1/keywords/import", json={
        "brand_profile_id": ws.id,
        "keywords": [{"keyword": "test keyword", "monthly_volume": 1000, "competition_score": 0.3}],
    })
    assert r1.json()["created"] == 1

    r2 = client.post("/api/v1/keywords/import", json={
        "brand_profile_id": ws.id,
        "keywords": [{"keyword": "test keyword", "monthly_volume": 500, "competition_score": 0.7}],
    })
    assert r2.json()["created"] == 1

    # Workspace'de 2 WK satırı, 2 farklı wk_id
    items = client.get("/api/v1/keywords/", params={"brand_profile_id": ws.id}).json()["items"]
    assert len(items) == 2
    assert items[0]["wk_id"] != items[1]["wk_id"]

    # Scoring çalıştır
    run = make_scoring_run(brand_profile_id=ws.id, status="pending")
    ScoreEngine(db_session).run_scoring(run.id)

    # İki ayrı KeywordScore satırı
    scores = (
        db_session.query(KeywordScore)
        .filter(KeywordScore.scoring_run_id == run.id)
        .all()
    )
    assert len(scores) == 2, (
        f"Beklenen 2 KeywordScore, bulundu {len(scores)}. "
        "Option B collision var: iki WK aynı keyword_id'yi paylaşıyor olabilir."
    )

    # Farklı keyword_id'ler
    kw_ids = {s.keyword_id for s in scores}
    assert len(kw_ids) == 2, f"keyword_id'ler çakışıyor: {kw_ids}"

    # metrics_snapshot içinde farklı wk_id'ler
    snap_wk_ids = {s.metrics_snapshot.get("wk_id") for s in scores}
    assert len(snap_wk_ids) == 2, f"metrics_snapshot wk_id'ler çakışıyor: {snap_wk_ids}"
    assert None not in snap_wk_ids, "metrics_snapshot.wk_id boş olamaz"
