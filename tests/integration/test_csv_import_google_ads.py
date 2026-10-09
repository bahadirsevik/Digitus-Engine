import pytest


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
    body = "\n".join(rows)
    text = "Keyword Stats\n1 Ocak 2025 - 31 Aralık 2025\n" + header + body
    return text.encode("utf-16-le")


@pytest.fixture
def fake_redis(monkeypatch):
    from app.api.v1 import keywords

    redis = FakeRedis()
    monkeypatch.setattr(keywords, "_get_redis_client", lambda: redis)
    return redis


# ── core dry-run + commit flow ─────────────────────────────────────────────────

def test_upload_csv_dry_run_and_commit_imports_google_ads_metrics(
    client, make_workspace, fake_redis, db_session
):
    from app.database.models import WorkspaceKeyword

    ws = make_workspace(name="CSV Import")
    resp = client.post(
        "/api/v1/keywords/upload-csv",
        data={"brand_profile_id": str(ws.id), "dry_run": "true"},
        files={"file": ("keywords.csv", _csv_bytes("organik sampuan\t1.200\t10%\t20%\tHigh\t70"), "text/csv")},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["parsed"] == 1
    assert data["accepted"] == 1
    assert data["dry_run_token"]
    assert data["parser_meta"]["header_row"] == 3

    commit = client.post(
        "/api/v1/keywords/upload-csv/commit",
        json={"brand_profile_id": ws.id, "dry_run_token": data["dry_run_token"]},
    )

    assert commit.status_code == 200
    committed = commit.json()
    assert committed["created"] == 1
    wk = db_session.query(WorkspaceKeyword).filter(WorkspaceKeyword.brand_profile_id == ws.id).one()
    assert wk.monthly_volume == 1200
    assert float(wk.competition_score) == 0.7


# ── exact-duplicate detection ──────────────────────────────────────────────────

def test_upload_csv_links_preexisting_global_keyword(
    client, make_workspace, make_keyword, fake_redis, db_session
):
    """Dry-run global preload must preserve the old per-row lookup behavior."""
    from app.database.models import WorkspaceKeyword

    ws = make_workspace(name="CSV global link")
    global_kw = make_keyword(
        "organik sampuan",
        monthly_volume=1200,
        competition_score=0.7,
    )

    resp = client.post(
        "/api/v1/keywords/upload-csv",
        data={"brand_profile_id": str(ws.id), "dry_run": "true"},
        files={"file": ("keywords.csv", _csv_bytes("organik sampuan\t1.200\t10%\t20%\tHigh\t70"), "text/csv")},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["accepted"] == 1
    assert data["accepted_keywords"][0]["reason"] == "link_existing_keyword"

    commit = client.post(
        "/api/v1/keywords/upload-csv/commit",
        json={"brand_profile_id": ws.id, "dry_run_token": data["dry_run_token"]},
    )

    assert commit.status_code == 200
    committed = commit.json()
    assert committed["created"] == 1
    assert committed["created_new"] == 0
    assert committed["linked_existing"] == 1
    wk = db_session.query(WorkspaceKeyword).filter(WorkspaceKeyword.brand_profile_id == ws.id).one()
    assert wk.keyword_id == global_kw.id


def test_upload_csv_reports_exact_duplicate_as_already_existing(
    client, make_workspace, make_keyword, fake_redis
):
    ws = make_workspace(name="CSV Duplicate")
    make_keyword(
        "organik sampuan",
        brand_profile_id=ws.id,
        monthly_volume=1200,
        competition_score=0.7,
    )

    resp = client.post(
        "/api/v1/keywords/upload-csv",
        data={"brand_profile_id": str(ws.id), "dry_run": "true"},
        files={"file": ("keywords.csv", _csv_bytes("organik sampuan\t1.200\t10%\t20%\tHigh\t70"), "text/csv")},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["accepted"] == 0
    assert data["skipped_exact"] == 1
    assert data["exact_duplicates"][0]["reason"] == "aynisi_onceden_eklendi"
    assert data["exact_duplicates"][0]["matched_keyword_id"] is not None
    assert data["exact_duplicates"][0]["matched_in_workspace"] is True


def test_upload_csv_exact_duplicate_different_metrics_adds_new_record(
    client, make_workspace, make_keyword, fake_redis, db_session
):
    """Same keyword with different metrics → dry-run reports it, commit adds a new
    WorkspaceKeyword row alongside the existing one (no update, no rejection)."""
    from app.database.models import WorkspaceKeyword

    ws = make_workspace(name="CSV diff metrics")
    make_keyword("organik sampuan", brand_profile_id=ws.id, monthly_volume=1200, competition_score=0.7)

    resp = client.post(
        "/api/v1/keywords/upload-csv",
        data={"brand_profile_id": str(ws.id), "dry_run": "true"},
        files={"file": ("keywords.csv", _csv_bytes("organik sampuan\t500\t5%\t10%\tLow\t20"), "text/csv")},
    )

    assert resp.status_code == 200
    data = resp.json()
    # keyword is accepted (as a new snapshot) and flagged for reporting
    assert data["accepted"] == 1
    assert data["exact_duplicate_different_metrics"] == 1
    assert data["exact_duplicates_different_metrics"][0]["reason"] == "exact_duplicate_different_metrics"

    commit = client.post(
        "/api/v1/keywords/upload-csv/commit",
        json={"brand_profile_id": ws.id, "dry_run_token": data["dry_run_token"]},
    )
    assert commit.status_code == 200
    committed = commit.json()
    assert committed["created"] == 1

    db_session.expire_all()
    rows = db_session.query(WorkspaceKeyword).filter(WorkspaceKeyword.brand_profile_id == ws.id).all()
    assert len(rows) == 2
    volumes = sorted(wk.monthly_volume for wk in rows)
    assert volumes == [500, 1200]


# ── within-batch dedup ─────────────────────────────────────────────────────────

def test_upload_csv_deduplicates_identical_rows_within_same_batch(
    client, make_workspace, fake_redis
):
    ws = make_workspace(name="CSV batch dedup")
    resp = client.post(
        "/api/v1/keywords/upload-csv",
        data={"brand_profile_id": str(ws.id), "dry_run": "true"},
        files={"file": ("keywords.csv", _csv_bytes(
            "organik sampuan\t1.200\t10%\t20%\tHigh\t70",
            "organik sampuan\t1.200\t10%\t20%\tHigh\t70",  # exact duplicate row
        ), "text/csv")},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["accepted"] == 1
    assert data["skipped_exact"] == 1


# ── fuzzy dedup ────────────────────────────────────────────────────────────────

def test_upload_csv_fuzzy_same_metrics_skips_similar_keyword(
    client, make_workspace, make_keyword, fake_redis
):
    """Similar keyword text + identical meaningful metrics → fuzzy-skip."""
    ws = make_workspace(name="CSV fuzzy skip")
    # "abcdefghi" is ≥85% similar to "abcdefghij" (ratio ≈ 94.7%)
    make_keyword("abcdefghi", brand_profile_id=ws.id, monthly_volume=1200, competition_score=0.7)

    resp = client.post(
        "/api/v1/keywords/upload-csv",
        data={"brand_profile_id": str(ws.id), "dry_run": "true"},
        files={"file": ("keywords.csv", _csv_bytes("abcdefghij\t1.200\t10%\t20%\tHigh\t70"), "text/csv")},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["skipped_fuzzy"] == 1
    assert data["accepted"] == 0
    assert data["fuzzy_skipped"][0]["reason"] == "fuzzy_same_metrics"


def test_upload_csv_fuzzy_different_metrics_keeps_similar_keyword(
    client, make_workspace, make_keyword, fake_redis
):
    """Similar keyword text + different meaningful metrics → keep (different product intent)."""
    ws = make_workspace(name="CSV fuzzy keep")
    make_keyword("abcdefghi", brand_profile_id=ws.id, monthly_volume=1200, competition_score=0.7)

    resp = client.post(
        "/api/v1/keywords/upload-csv",
        data={"brand_profile_id": str(ws.id), "dry_run": "true"},
        files={"file": ("keywords.csv", _csv_bytes("abcdefghij\t500\t5%\t3%\tLow\t20"), "text/csv")},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["accepted"] == 1
    assert data["fuzzy_kept"][0]["reason"] == "fuzzy_different_metrics_keep"


# ── security ───────────────────────────────────────────────────────────────────

def test_upload_csv_commit_rejects_workspace_mismatch(client, make_workspace, fake_redis):
    ws_a = make_workspace(name="CSV A")
    ws_b = make_workspace(name="CSV B")

    resp = client.post(
        "/api/v1/keywords/upload-csv",
        data={"brand_profile_id": str(ws_a.id), "dry_run": "true"},
        files={"file": ("keywords.csv", _csv_bytes("organik sampuan\t1.200\t10%\t20%\tHigh\t70"), "text/csv")},
    )
    assert resp.status_code == 200

    commit = client.post(
        "/api/v1/keywords/upload-csv/commit",
        json={"brand_profile_id": ws_b.id, "dry_run_token": resp.json()["dry_run_token"]},
    )
    assert commit.status_code == 403


# ── token lifecycle ────────────────────────────────────────────────────────────

def test_upload_csv_commit_expired_or_invalid_token_returns_410(
    client, make_workspace, fake_redis
):
    ws = make_workspace(name="CSV expired token")
    commit = client.post(
        "/api/v1/keywords/upload-csv/commit",
        json={"brand_profile_id": ws.id, "dry_run_token": "00000000-dead-beef-0000-000000000000"},
    )
    assert commit.status_code == 410


# ── infrastructure errors ──────────────────────────────────────────────────────

def test_upload_csv_redis_unavailable_returns_503(client, make_workspace, monkeypatch):
    from app.api.v1 import keywords

    ws = make_workspace(name="CSV Redis Down")
    monkeypatch.setattr(keywords, "_get_redis_client", lambda: None)

    resp = client.post(
        "/api/v1/keywords/upload-csv",
        data={"brand_profile_id": str(ws.id), "dry_run": "true"},
        files={"file": ("keywords.csv", _csv_bytes("organik sampuan\t1.200\t10%\t20%\tHigh\t70"), "text/csv")},
    )
    assert resp.status_code == 503


# ── request validation ─────────────────────────────────────────────────────────

def test_upload_csv_dry_run_false_returns_400(client, make_workspace):
    ws = make_workspace(name="CSV no dry_run")
    resp = client.post(
        "/api/v1/keywords/upload-csv",
        data={"brand_profile_id": str(ws.id), "dry_run": "false"},
        files={"file": ("keywords.csv", b"Keyword\ntest\n", "text/csv")},
    )
    assert resp.status_code == 400


def test_upload_csv_oversized_file_returns_413(client, make_workspace):
    ws = make_workspace(name="CSV 413")
    large_content = b"x" * (11 * 1024 * 1024)  # 11 MB > 10 MB limit
    resp = client.post(
        "/api/v1/keywords/upload-csv",
        data={"brand_profile_id": str(ws.id), "dry_run": "true"},
        files={"file": ("big.csv", large_content, "text/csv")},
    )
    assert resp.status_code == 413


def test_upload_csv_too_many_rows_returns_422(client, make_workspace):
    ws = make_workspace(name="CSV 422")
    lines = [
        "Keyword Stats",
        "Keyword\tAvg. monthly searches\tCompetition\tThree month change",
    ]
    lines += [f"kw{i}\t100\tLow\t1%" for i in range(50_001)]
    content = "\n".join(lines).encode("utf-16-le")

    resp = client.post(
        "/api/v1/keywords/upload-csv",
        data={"brand_profile_id": str(ws.id), "dry_run": "true"},
        files={"file": ("big.csv", content, "text/csv")},
    )
    assert resp.status_code == 422


# ── P6 snapshot row identity ───────────────────────────────────────────────────

def test_two_snapshots_have_different_wk_ids(
    client, make_workspace, make_keyword, fake_redis, db_session
):
    """Two metric snapshots for the same keyword text must expose distinct wk_id values."""
    from app.database.models import WorkspaceKeyword

    ws = make_workspace(name="P6 wk_id")
    make_keyword("organik sampuan", brand_profile_id=ws.id, monthly_volume=1200, competition_score=0.7)

    # Commit a second snapshot with different metrics
    resp = client.post(
        "/api/v1/keywords/upload-csv",
        data={"brand_profile_id": str(ws.id), "dry_run": "true"},
        files={"file": ("kw.csv", _csv_bytes("organik sampuan\t500\t5%\t10%\tLow\t20"), "text/csv")},
    )
    assert resp.status_code == 200
    commit = client.post(
        "/api/v1/keywords/upload-csv/commit",
        json={"brand_profile_id": ws.id, "dry_run_token": resp.json()["dry_run_token"]},
    )
    assert commit.status_code == 200

    list_resp = client.get("/api/v1/keywords/", params={"brand_profile_id": ws.id})
    assert list_resp.status_code == 200
    items = list_resp.json()["items"]
    assert len(items) == 2
    wk_ids = [item["wk_id"] for item in items]
    assert wk_ids[0] is not None and wk_ids[1] is not None
    assert wk_ids[0] != wk_ids[1]


def test_delete_one_snapshot_leaves_the_other(
    client, make_workspace, make_keyword, fake_redis, db_session
):
    """Deleting by wk_id removes exactly one snapshot; the other remains."""
    from app.database.models import WorkspaceKeyword

    ws = make_workspace(name="P6 delete one")
    make_keyword("organik sampuan", brand_profile_id=ws.id, monthly_volume=1200, competition_score=0.7)

    resp = client.post(
        "/api/v1/keywords/upload-csv",
        data={"brand_profile_id": str(ws.id), "dry_run": "true"},
        files={"file": ("kw.csv", _csv_bytes("organik sampuan\t500\t5%\t10%\tLow\t20"), "text/csv")},
    )
    assert resp.status_code == 200
    commit = client.post(
        "/api/v1/keywords/upload-csv/commit",
        json={"brand_profile_id": ws.id, "dry_run_token": resp.json()["dry_run_token"]},
    )
    assert commit.status_code == 200

    list_resp = client.get("/api/v1/keywords/", params={"brand_profile_id": ws.id})
    items = list_resp.json()["items"]
    assert len(items) == 2

    # Delete only the first snapshot
    wk_id_to_delete = items[0]["wk_id"]
    del_resp = client.delete(
        f"/api/v1/keywords/{wk_id_to_delete}",
        params={"brand_profile_id": ws.id},
    )
    assert del_resp.status_code == 204

    list_after = client.get("/api/v1/keywords/", params={"brand_profile_id": ws.id})
    remaining = list_after.json()["items"]
    assert len(remaining) == 1
    assert remaining[0]["wk_id"] != wk_id_to_delete


def test_same_text_same_metrics_still_skipped(
    client, make_workspace, make_keyword, fake_redis
):
    """Exact duplicate (same text + same metrics) must NOT create a second row."""
    ws = make_workspace(name="P6 no dupe")
    make_keyword("organik sampuan", brand_profile_id=ws.id, monthly_volume=1200, competition_score=0.7)

    resp = client.post(
        "/api/v1/keywords/upload-csv",
        data={"brand_profile_id": str(ws.id), "dry_run": "true"},
        files={"file": ("kw.csv", _csv_bytes("organik sampuan\t1.200\t10%\t20%\tHigh\t70"), "text/csv")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["accepted"] == 0
    assert data["skipped_exact"] == 1
