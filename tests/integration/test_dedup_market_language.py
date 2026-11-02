"""Dedup dil kurali uctan uca (plan_pazar_dil_secimi 2-e/2-f): onizleme, commit, /import ve
cleanup AYNI girdide AYNI karari verir; RU calisma exact, TR calisma fuzzy.
Rekey betigi (2-f) de burada.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from app.core.keyword_normalize import normalize_keyword
from app.database.models import Keyword, WorkspaceKeyword

RU = "1031"
A, B, C = "лечение волос", "лечения волос", "Лечение Волос"  # B ~ A (fuzzy 92), C == A (normalize)


def _csv_bytes(*rows: str) -> bytes:
    header = (
        "Keyword\tAvg. monthly searches\tThree month change\tYoY change\t"
        "Competition\tCompetition (indexed value)\n"
    )
    text = "Keyword Stats\n1 Ocak 2025 - 31 Aralık 2025\n" + header + "\n".join(rows)
    return text.encode("utf-16-le")


class _FakeRedis:
    def __init__(self):
        self.store = {}

    def setex(self, key, ttl, value):
        self.store[key] = value
        return True

    def get(self, key):
        return self.store.get(key)


@pytest.fixture(autouse=True)
def _fake_redis(monkeypatch):
    from app.api.v1 import keywords

    redis = _FakeRedis()
    monkeypatch.setattr(keywords, "_get_redis_client", lambda: redis)


def _row(word):
    return {"keyword": word, "monthly_volume": 100, "competition_score": 0.7}


def _seed(client, ws):
    r = client.post("/api/v1/keywords/import", json={"brand_profile_id": ws.id, "keywords": [_row(A)]})
    assert r.status_code == 200 and r.json()["created"] == 1


def _preview_and_commit(client, ws):
    rows = [f"{w}\t100\t0%\t0%\tHigh\t70" for w in (B, C)]
    prev = client.post(
        "/api/v1/keywords/upload-csv",
        data={"brand_profile_id": str(ws.id), "dry_run": "true"},
        files={"file": ("k.csv", _csv_bytes(*rows), "text/csv")},
    )
    assert prev.status_code == 200, prev.text
    commit = client.post(
        "/api/v1/keywords/upload-csv/commit",
        json={"brand_profile_id": ws.id, "dry_run_token": prev.json()["dry_run_token"]},
    )
    assert commit.status_code == 200, commit.text
    return prev.json()["accepted"], commit.json()["created"]


@pytest.mark.parametrize("lang, expected_new", [(RU, 1), ("1037", 0)])
def test_preview_commit_and_import_agree(client, make_workspace, lang, expected_new):
    ws_csv = make_workspace(name=f"csv {lang}", default_language_id=lang)
    ws_imp = make_workspace(name=f"imp {lang}", default_language_id=lang)
    _seed(client, ws_csv)
    _seed(client, ws_imp)

    accepted, committed = _preview_and_commit(client, ws_csv)
    imp = client.post(
        "/api/v1/keywords/import",
        json={"brand_profile_id": ws_imp.id, "keywords": [_row(B), _row(C)]},
    )
    assert imp.status_code == 200, imp.text

    assert accepted == committed == imp.json()["created"] == expected_new  # RU: yalniz B; TR: B fuzzy, C exact


@pytest.mark.parametrize("lang, removed", [(RU, 1), ("1037", 2)])
def test_cleanup_duplicates_follows_workspace_language(client, make_workspace, make_keyword, lang, removed):
    ws = make_workspace(name=f"cleanup {lang}", default_language_id=lang)
    for word in (A, B, C):
        make_keyword(word, brand_profile_id=ws.id, monthly_volume=100, competition_score=0.7)

    r = client.post(f"/api/v1/keywords/cleanup-duplicates?brand_profile_id={ws.id}")
    assert r.status_code == 200, r.text
    assert r.json()["links_removed"] == removed  # RU: yalniz C (exact); TR: B ve C


def test_same_batch_in_import_follows_language(client, make_workspace):
    ru = make_workspace(name="batch ru", default_language_id=RU)
    tr = make_workspace(name="batch tr", default_language_id="1037")
    payload = lambda ws: {"brand_profile_id": ws.id, "keywords": [_row(A), _row(B), _row(C)]}  # noqa: E731
    assert client.post("/api/v1/keywords/import", json=payload(ru)).json()["created"] == 2
    assert client.post("/api/v1/keywords/import", json=payload(tr)).json()["created"] == 1


# ── rekey betigi ──────────────────────────────────────────────────────────────


def _load_rekey():
    path = Path(__file__).resolve().parents[2] / "scripts" / "rekey_normalized_keywords.py"
    spec = importlib.util.spec_from_file_location("rekey_script", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _old_style_key(text):  # eski kural: NFKD + TUM combining mark'lar silinir
    import unicodedata

    s = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in s if not unicodedata.combining(ch))


def test_rekey_dry_run_apply_and_idempotent(db_session, make_keyword, capsys):
    rekey = _load_rekey()
    kw = make_keyword("йод крем")
    kw.normalized_keyword = _old_style_key("йод крем")  # 'иод крем'
    ok = make_keyword("kâr marjı")
    ok.normalized_keyword = normalize_keyword("kâr marjı")  # zaten guncel
    db_session.commit()

    assert rekey.run(db_session, apply=False) == 1
    assert "'иод крем'" in capsys.readouterr().out
    db_session.refresh(kw)
    assert kw.normalized_keyword == "иод крем"  # dry-run yazmaz

    assert rekey.run(db_session, apply=True) == 1
    db_session.refresh(kw)
    assert kw.normalized_keyword == "йод крем"
    assert rekey.run(db_session, apply=True) == 0  # idempotent
    assert rekey.run(db_session, apply=False) == 0


def test_reimport_after_rekey_does_not_duplicate(client, db_session, make_workspace, make_keyword):
    ws = make_workspace(name="rekey reimport", default_language_id=RU)
    kw = make_keyword("йод крем", brand_profile_id=ws.id, monthly_volume=100, competition_score=0.7)
    kw.normalized_keyword = _old_style_key("йод крем")
    db_session.commit()

    _load_rekey().run(db_session, apply=True)

    r = client.post("/api/v1/keywords/import", json={"brand_profile_id": ws.id, "keywords": [_row("йод крем")]})
    assert r.status_code == 200, r.text
    assert r.json()["created"] == 0
    assert db_session.query(Keyword).filter(Keyword.keyword == "йод крем").count() == 1
    assert db_session.query(WorkspaceKeyword).filter(WorkspaceKeyword.brand_profile_id == ws.id).count() == 1
