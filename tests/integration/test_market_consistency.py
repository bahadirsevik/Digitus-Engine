"""Pazar tutarliligi (plan_pazar_dil_secimi.md Faz 1, 3/4/6/7 backend):
pazar kilidi, satir damgasi, run kapisi, odak ayar uygulama, iki pencere eszamanliligi.

Gercek test DB'si; AI/ag yok.
"""
from __future__ import annotations

import threading
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from app.api.v1 import brand_profile as brand_profile_api
from app.config import settings
from app.core.workspace import (
    market_change_blocker,
    set_workspace_market,
    stamp_rows_market,
    verify_workspace,
)
from app.database.connection import SessionLocal
from app.database.models import BrandProfile, ExportJob, ScoringRun, WorkspaceKeyword

BP = "/api/v1/brand-profile"
DE, RU = "2276", "1031"  # Almanya / Rusca

PROFILE = {
    "company_name": "Acme",
    "sector": "Test",
    "brand_summary": "x",
    "target_audience": "y",
    "products": ["Urun"],
    "services": [],
    "use_cases": ["Kullanim"],
    "problems_solved": ["Sorun"],
    "brand_terms": ["Acme"],
    "exclude_themes": [],
    "anchor_texts": ["Urun", "Kullanim"],
}


def _wk(db_session, ws_id, keyword="mevcut kelime", geo=None, lang=None, vol=100):
    from app.database.models import Keyword

    kw = Keyword(keyword=keyword, normalized_keyword=keyword, monthly_volume=vol,
                 competition_score=0.5, trend_3m=0, trend_12m=0)
    db_session.add(kw)
    db_session.commit()
    db_session.add(WorkspaceKeyword(
        brand_profile_id=ws_id, keyword_id=kw.id, monthly_volume=vol, competition_score=0.5,
        trend_3m=0, trend_12m=0, data_source="csv", geo_target_id=geo, language_id=lang,
    ))
    db_session.commit()


def _wk_rows(db_session, ws_id):
    db_session.expire_all()
    return db_session.query(WorkspaceKeyword).filter(WorkspaceKeyword.brand_profile_id == ws_id).all()


def _import(client, ws_id, rows):
    return client.post("/api/v1/keywords/import", json={"brand_profile_id": ws_id, "keywords": rows})


def _kw(word, **extra):
    return {"keyword": word, "monthly_volume": 500, "competition_score": 0.4, **extra}


@pytest.fixture
def de_ws(make_workspace):
    return make_workspace(name="Berlin Ws", default_geo_target_id=DE, default_language_id=RU)


# ── kilit: set_workspace_market ───────────────────────────────────────────────


def test_blocker_none_on_empty_workspace_and_set_stores(db_session, make_workspace):
    ws = make_workspace(name="bos")
    assert market_change_blocker(db_session, ws) is None
    assert set_workspace_market(db_session, ws, DE, RU) is True
    assert (ws.default_geo_target_id, ws.default_language_id) == (DE, RU)


def test_blocker_keywords(db_session, make_workspace):
    ws = make_workspace(name="kelimeli")
    _wk(db_session, ws.id)
    assert market_change_blocker(db_session, ws)
    with pytest.raises(HTTPException) as exc:
        set_workspace_market(db_session, ws, DE, RU)
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "MARKET_LOCKED"
    assert exc.value.detail["reason"]
    assert ws.default_geo_target_id == "2792"  # yazilmadi


def test_blocker_run(db_session, make_workspace, make_scoring_run):
    ws = make_workspace(name="runli")
    make_scoring_run(brand_profile_id=ws.id)
    assert market_change_blocker(db_session, ws)


def test_blocker_export(db_session, make_workspace, make_scoring_run):
    ws = make_workspace(name="exportlu")
    db_session.add(ExportJob(id=str(uuid.uuid4()), brand_profile_id=ws.id, status="completed",
                             progress=100, format="docx", sections=["all"]))
    db_session.commit()
    assert market_change_blocker(db_session, ws)


def test_blocker_active_work(db_session, make_workspace, monkeypatch):
    ws = make_workspace(name="aktif is")
    monkeypatch.setattr("app.core.policy.guards.find_active_workspace_work",
                        lambda db, wid: "intent görevi sürüyor (task x)")
    assert market_change_blocker(db_session, ws) == "intent görevi sürüyor (task x)"
    with pytest.raises(HTTPException) as exc:
        set_workspace_market(db_session, ws, DE, RU)
    assert exc.value.detail["code"] == "MARKET_LOCKED"


def test_same_value_is_noop_even_when_locked_and_alias_counts_equal(db_session, make_workspace):
    ws = make_workspace(name="alias", default_language_id="1055")  # eski Windows locale
    _wk(db_session, ws.id)
    assert set_workspace_market(db_session, ws, "2792", "1037") is False
    assert ws.default_language_id == "1055"  # dokunulmadi


# ── profil onayi uc noktasi ───────────────────────────────────────────────────


def _profile_ws(make_workspace, **kw):
    return make_workspace(status="profile_review", onboarding_flow="profile_first",
                          profile_data=dict(PROFILE), **kw)


def test_approve_changes_market_on_empty_workspace(client, make_workspace):
    ws = _profile_ws(make_workspace)
    r = client.put(f"{BP}/workspaces/{ws.id}/profile/approve", json={
        "rerun_keywords": False, "default_geo_target_id": DE, "default_language_id": RU,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["default_geo_target_id"], body["default_language_id"]) == (DE, RU)
    assert body["market_label"] == "Almanya (DE) · Rusça"
    assert body["market_locked"] is False


def test_approve_market_change_409_when_data(client, db_session, make_workspace):
    ws = _profile_ws(make_workspace)
    _wk(db_session, ws.id)
    r = client.put(f"{BP}/workspaces/{ws.id}/profile/approve", json={
        "rerun_keywords": False, "default_geo_target_id": DE,
    })
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "MARKET_LOCKED"
    db_session.expire_all()
    assert db_session.get(BrandProfile, ws.id).default_geo_target_id == "2792"
    assert db_session.get(BrandProfile, ws.id).status == "profile_review"  # hicbir sey yazilmadi


def test_approve_same_market_with_data_is_ok(client, db_session, make_workspace):
    ws = _profile_ws(make_workspace)
    _wk(db_session, ws.id)
    r = client.put(f"{BP}/workspaces/{ws.id}/profile/approve", json={
        "rerun_keywords": False, "default_geo_target_id": "2792", "default_language_id": "1037",
    })
    assert r.status_code == 200, r.text
    assert r.json()["market_locked"] is True


def test_workspace_get_has_market_label_and_locked(client, db_session, de_ws):
    r = client.get(f"{BP}/workspaces/{de_ws.id}")
    assert r.status_code == 200
    assert r.json()["market_label"] == "Almanya (DE) · Rusça"
    assert r.json()["market_location_label"] == "Almanya (DE) — Ülke"  # frontend birleşik etiketi bölmez
    assert r.json()["market_locked"] is False
    _wk(db_session, de_ws.id, geo=DE, lang=RU)
    assert client.get(f"{BP}/workspaces/{de_ws.id}").json()["market_locked"] is True
    listed = client.get(f"{BP}/workspaces").json()
    assert listed[0]["market_label"] == "Almanya (DE) · Rusça"
    assert listed[0]["market_location_label"] == "Almanya (DE) — Ülke"


# ── odaklara ayar uygulama ────────────────────────────────────────────────────


def _brand_and_focuses(make_workspace, db_session):
    brand = make_workspace(
        name="Marka", company_url="https://m.test", onboarding_flow="profile_first",
        profile_data={"company_name": "M", "services": ["A"]},
        competitor_policy={"ads": "block", "seo": "allow", "social": "block"},
        default_geo_target_id=DE, default_language_id=RU,
    )

    def focus(n, **kw):
        return make_workspace(
            name=f"Marka — /f{n}", company_url=f"https://m.test/f{n}", crawl_scope="page",
            parent_brand_profile_id=brand.id, status="confirmed",
            profile_data={"company_name": "F", "services": ["B"]},
            competitor_policy={"ads": "allow", "seo": "allow", "social": "allow"}, **kw,
        )

    return brand, focus


def test_focus_apply_skips_market_when_focus_has_data_but_applies_rest(client, db_session, make_workspace):
    brand, focus = _brand_and_focuses(make_workspace, db_session)
    with_data = focus(1)  # TR/TR (marka DE/RU'dan farkli)
    _wk(db_session, with_data.id)
    empty = focus(2)

    r = client.post(f"{BP}/workspaces/{brand.id}/focuses/settings/apply")
    assert r.status_code == 200, r.text
    applied = {a["id"]: a for a in r.json()["applied"]}
    assert applied[with_data.id]["changed"] is True  # competitor_policy uygulandi
    assert applied[with_data.id]["warning"] == "pazar değişmez (odakta veri var)"
    assert applied[empty.id]["warning"] is None
    db_session.expire_all()
    f1, f2 = db_session.get(BrandProfile, with_data.id), db_session.get(BrandProfile, empty.id)
    assert (f1.default_geo_target_id, f1.default_language_id) == ("2792", "1037")
    assert f1.competitor_policy == brand.competitor_policy
    assert (f2.default_geo_target_id, f2.default_language_id) == (DE, RU)


def test_focus_apply_market_only_difference_locked_is_unchanged(client, db_session, make_workspace):
    brand, focus = _brand_and_focuses(make_workspace, db_session)
    f = focus(1)
    f.competitor_policy = brand.competitor_policy
    db_session.commit()
    _wk(db_session, f.id)
    r = client.post(f"{BP}/workspaces/{brand.id}/focuses/settings/apply")
    item = r.json()["applied"][0]
    assert item["changed"] is False and item["warning"] == "pazar değişmez (odakta veri var)"

    prev = client.post(f"{BP}/workspaces/{brand.id}/focuses/settings/preview").json()["focuses"][0]
    assert prev["market_locked"] is True
    assert all(c["field"] not in ("default_geo_target_id", "default_language_id") for c in prev["changes"])


def test_focus_apply_alias_market_is_not_a_change(client, db_session, make_workspace):
    brand, focus = _brand_and_focuses(make_workspace, db_session)
    brand.default_geo_target_id, brand.default_language_id = "2792", "1037"
    db_session.commit()
    f = focus(1, default_geo_target_id="2792", default_language_id="1055")
    f.competitor_policy = brand.competitor_policy
    db_session.commit()
    item = client.post(f"{BP}/workspaces/{brand.id}/focuses/settings/apply").json()["applied"][0]
    assert item["changed"] is False and item["warning"] is None


# ── satir damgasi ─────────────────────────────────────────────────────────────


def test_stamp_helper_fills_empty_and_canonicalizes(de_ws):
    rows = stamp_rows_market([{"keyword": "a"}, {"keyword": "b", "geo_target_id": "geoTargetConstants/2276",
                                                   "language_id": "1031"}], de_ws)
    assert all((r["geo_target_id"], r["language_id"]) == (DE, RU) for r in rows)


def test_import_without_market_fields_stores_workspace_market(client, db_session, de_ws):
    r = _import(client, de_ws.id, [_kw("birinci sorgu"), _kw("ikinci tamamen farkli")])
    assert r.status_code == 200, r.text
    rows = _wk_rows(db_session, de_ws.id)
    assert len(rows) == 2
    assert {(w.geo_target_id, w.language_id) for w in rows} == {(DE, RU)}


def test_import_explicit_equal_values_accepted(client, db_session, de_ws):
    r = _import(client, de_ws.id, [_kw("esit pazar", geo_target_id=DE, language_id=RU)])
    assert r.status_code == 200, r.text
    assert len(_wk_rows(db_session, de_ws.id)) == 1


def test_import_explicit_different_geo_is_409_and_writes_nothing(client, db_session, de_ws):
    r = _import(client, de_ws.id, [_kw("iyi satir"), _kw("kotu satir", geo_target_id="2792", language_id=RU)])
    assert r.status_code == 409
    d = r.json()["detail"]
    assert d["code"] == "MARKET_MISMATCH" and d["rows"] == 1
    assert "Türkiye" in d["message"] and "Almanya" in d["message"] and "Rusça" in d["message"]
    assert _wk_rows(db_session, de_ws.id) == []


def test_import_201_rows_mismatch_in_last_row_writes_zero(client, db_session, de_ws):
    rows = [_kw(f"alfa{chr(97 + i % 26)}{chr(97 + (i // 26) % 26)}{i}") for i in range(200)]
    rows.append(_kw("son satir", language_id="1000"))  # dil uyusmazligi, 201. satir
    r = _import(client, de_ws.id, rows)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "MARKET_MISMATCH"
    assert _wk_rows(db_session, de_ws.id) == []


def test_create_single_keyword_is_stamped(client, db_session, de_ws):
    r = client.post("/api/v1/keywords/", json={"brand_profile_id": de_ws.id, "keyword": "tekil kelime",
                                               "monthly_volume": 300})
    assert r.status_code == 201, r.text
    w = _wk_rows(db_session, de_ws.id)[0]
    assert (w.geo_target_id, w.language_id) == (DE, RU)
    bad = client.post("/api/v1/keywords/", json={"brand_profile_id": de_ws.id, "keyword": "baska pazar",
                                                 "monthly_volume": 300, "geo_target_id": "2792"})
    assert bad.status_code == 409 and bad.json()["detail"]["code"] == "MARKET_MISMATCH"


def test_market_not_set_blocks_import_and_run(client, make_workspace, monkeypatch):
    ws = make_workspace(name="pazarsiz", default_geo_target_id=None, default_language_id=None)
    r = _import(client, ws.id, [_kw("kelime x")])
    assert r.status_code == 409 and r.json()["detail"]["code"] == "MARKET_NOT_SET"
    monkeypatch.setattr(settings, "ENABLE_ENGINE_V3", True)
    run = client.post("/api/v1/scoring/runs", json={
        "brand_profile_id": ws.id, "algorithm_version": "v3", "auto_assign_channels": True,
        "ads_capacity": 10, "seo_capacity": 10, "social_capacity": 10})
    assert run.status_code == 409 and run.json()["detail"]["code"] == "MARKET_NOT_SET"


# ── yenileme ──────────────────────────────────────────────────────────────────


def test_refresh_writes_workspace_market_not_null(client, db_session, de_ws, monkeypatch):
    _wk(db_session, de_ws.id, keyword="eski kelime", geo=None, lang=None)  # legacy NULL
    calls = []

    class FakeSvc:
        def enrich_keywords(self, customer_id, seeds, max_results, language_id, geo_target_id):
            calls.append((language_id, geo_target_id))
            idea = lambda k: SimpleNamespace(keyword=k, avg_monthly_searches=900, trend_3m=1.0,  # noqa: E731
                                             trend_12m=2.0, competition_score=0.3)
            return [idea("eski kelime"), idea("yepyeni oneri")], False, None

    monkeypatch.setattr("app.api.v1.google_ads._get_service", lambda: FakeSvc())
    r = client.post(f"{BP}/workspaces/{de_ws.id}/keywords/refresh",
                    json={"customer_id": "123", "include_new_ideas": True})
    assert r.status_code == 200, r.text
    assert r.json()["added"] == 1
    assert calls == [(RU, DE)]
    rows = _wk_rows(db_session, de_ws.id)
    assert len(rows) == 2
    assert {(w.geo_target_id, w.language_id) for w in rows} == {(DE, RU)}


def test_refresh_rejects_results_if_market_changed_while_provider_was_running(
    client, db_session, de_ws, monkeypatch
):
    """Codex Faz 0-3 #1: saglayici beklenirken kelimeler silinip pazar degisirse eski
    pazarin olcumleri yeni pazarli calismaya GERI YAZILMAZ (409 MARKET_CHANGED, 0 satir)."""
    from app.core.site_analyzer.analysis_attempt import lock_workspace_row
    from app.core.workspace import set_workspace_market

    ws_id = de_ws.id
    _wk(db_session, ws_id, keyword="clinic", geo=DE, lang=RU)
    calls = []

    class FakeSvc:
        def enrich_keywords(self, customer_id, seeds, max_results, language_id, geo_target_id):
            calls.append((geo_target_id, language_id))
            # Refresh transaction'i commit edilmisken iki mesru mutasyon: kelimeler silinir,
            # bos calismanin pazari kilitli yoldan TR/Turkce yapilir.
            with SessionLocal() as other:
                other.query(WorkspaceKeyword).filter(
                    WorkspaceKeyword.brand_profile_id == ws_id
                ).delete(synchronize_session=False)
                other.commit()
                locked = lock_workspace_row(other, ws_id)
                set_workspace_market(other, locked, "2792", "1037")
                other.commit()
            return [SimpleNamespace(keyword="clinic", avg_monthly_searches=321, trend_3m=1.0,
                                    trend_12m=2.0, competition_score=0.3)], False, None

    monkeypatch.setattr("app.api.v1.google_ads._get_service", lambda: FakeSvc())
    r = client.post(f"{BP}/workspaces/{ws_id}/keywords/refresh",
                    json={"customer_id": "123", "min_volume": 0})
    assert calls == [(DE, RU)]
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "MARKET_CHANGED"
    db_session.expire_all()
    current = db_session.get(BrandProfile, ws_id)
    assert (current.default_geo_target_id, current.default_language_id) == ("2792", "1037")
    assert _wk_rows(db_session, ws_id) == []


# ── CSV ───────────────────────────────────────────────────────────────────────


class _FakeRedis:
    def __init__(self):
        self.store = {}

    def setex(self, key, ttl, value):
        self.store[key] = value
        return True

    def get(self, key):
        return self.store.get(key)


def _csv(*rows):
    header = ("Keyword\tAvg. monthly searches\tThree month change\tYoY change\t"
              "Competition\tCompetition (indexed value)\n")
    return ("Keyword Stats\n1 Ocak 2025 - 31 Aralık 2025\n" + header + "\n".join(rows)).encode("utf-16-le")


def test_csv_preview_notice_and_commit_stamps_workspace_market(client, db_session, de_ws, monkeypatch):
    from app.api.v1 import keywords

    redis = _FakeRedis()
    monkeypatch.setattr(keywords, "_get_redis_client", lambda: redis)
    r = client.post("/api/v1/keywords/upload-csv", data={"brand_profile_id": str(de_ws.id), "dry_run": "true"},
                    files={"file": ("k.csv", _csv("organik sampuan\t1.200\t10%\t20%\tHigh\t70"), "text/csv")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["market_notice"] == (
        "Bu dosyadaki ölçümler Almanya (DE) · Rusça olarak kaydedilecek "
        "(dosyada konum/dil bilgisi yok; başka konum için alındıysa yeni pazar çalışması açın)."
    )
    c = client.post("/api/v1/keywords/upload-csv/commit",
                    json={"brand_profile_id": de_ws.id, "dry_run_token": body["dry_run_token"]})
    assert c.status_code == 200, c.text
    rows = _wk_rows(db_session, de_ws.id)
    assert len(rows) == 1 and (rows[0].geo_target_id, rows[0].language_id) == (DE, RU)


def test_csv_header_not_found_message_mentions_ui_language(client, de_ws, monkeypatch):
    from app.api.v1 import keywords

    monkeypatch.setattr(keywords, "_get_redis_client", lambda: _FakeRedis())
    r = client.post("/api/v1/keywords/upload-csv", data={"brand_profile_id": str(de_ws.id), "dry_run": "true"},
                    files={"file": ("k.csv", "rastgele\nmetin\n".encode("utf-16-le"), "text/csv")})
    assert r.status_code == 422
    assert "arayüz dilini İngilizce/Türkçe yapıp yeniden dışa aktarın" in r.json()["detail"]


# ── run kapisi ────────────────────────────────────────────────────────────────


def _create_run(client, ws_id, monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_ENGINE_V3", True)
    return client.post("/api/v1/scoring/runs", json={
        "brand_profile_id": ws_id, "algorithm_version": "v3", "auto_assign_channels": True,
        "ads_capacity": 10, "seo_capacity": 10, "social_capacity": 10, "run_name": "pazar kapisi"})


def test_run_gate_blocks_explicit_mismatched_rows_with_count(client, db_session, de_ws, monkeypatch):
    _wk(db_session, de_ws.id, keyword="a1", geo=DE, lang=RU)          # uyumlu
    _wk(db_session, de_ws.id, keyword="b2", geo=None, lang=None)       # NULL = calisma degeri
    _wk(db_session, de_ws.id, keyword="c3", geo="2792", lang=RU)       # geo farkli
    _wk(db_session, de_ws.id, keyword="d4", geo=DE, lang="1000")       # dil farkli
    _wk(db_session, de_ws.id, keyword="e5", geo=None, lang="1037")     # dil farkli (geo NULL)
    r = _create_run(client, de_ws.id, monkeypatch)
    assert r.status_code == 409, r.text
    d = r.json()["detail"]
    assert d["code"] == "MARKET_MISMATCH_ROWS" and d["rows"] == 3
    assert d["message"].startswith("3 kelime satırı çalışma pazarından farklı")
    assert db_session.query(ScoringRun).count() == 0


def test_run_gate_passes_with_null_and_matching_rows(client, db_session, de_ws, monkeypatch):
    _wk(db_session, de_ws.id, keyword="a1", geo=None, lang=None)
    _wk(db_session, de_ws.id, keyword="b2", geo=DE, lang=RU)
    r = _create_run(client, de_ws.id, monkeypatch)
    assert r.status_code == 201, r.text


def test_run_gate_alias_language_counts_equal(client, db_session, make_workspace, monkeypatch):
    ws = make_workspace(name="alias run")  # 2792 / 1037
    _wk(db_session, ws.id, geo="2792", lang="1055")
    assert _create_run(client, ws.id, monkeypatch).status_code == 201


# ── eszamanlilik: iki pencere ─────────────────────────────────────────────────


def _lock_for_update_and_set(ws_id, out):
    """Pazar degisimi penceresi: onay yolundaki gibi FOR UPDATE + set_workspace_market."""
    s2 = SessionLocal()
    try:
        s2.execute(text("SET lock_timeout = '4s'"))
        ws2 = s2.query(BrandProfile).filter(BrandProfile.id == ws_id).with_for_update().first()
        try:
            out["changed"] = set_workspace_market(s2, ws2, "2840", "1000")
        except HTTPException as exc:
            out["exc"] = exc
        s2.rollback()
    except Exception as exc:  # lock_timeout vb.
        out["error"] = exc
    finally:
        s2.close()


def test_two_windows_market_change_waits_for_uncommitted_import_then_409(db_session, de_ws):
    from app.database.models import Keyword

    s1 = SessionLocal()
    try:
        ws1 = verify_workspace(s1, de_ws.id, lock=True)  # FOR SHARE
        rows = stamp_rows_market([{"keyword": "pencere bir"}], ws1)
        kw = Keyword(keyword="pencere bir", normalized_keyword="pencere bir", monthly_volume=10,
                     competition_score=0.5, trend_3m=0, trend_12m=0)
        s1.add(kw)
        s1.flush()
        s1.add(WorkspaceKeyword(brand_profile_id=de_ws.id, keyword_id=kw.id, monthly_volume=10,
                                competition_score=0.5, trend_3m=0, trend_12m=0, data_source="csv",
                                geo_target_id=rows[0]["geo_target_id"], language_id=rows[0]["language_id"]))
        s1.flush()  # yazildi, commit YOK

        out: dict = {}
        t = threading.Thread(target=_lock_for_update_and_set, args=(de_ws.id, out))
        t.start()
        t.join(timeout=0.7)
        assert t.is_alive(), "pazar degisimi FOR SHARE tutan yazim bitmeden ilerlememeliydi"
        s1.commit()
        t.join(timeout=5)
        assert not t.is_alive()
    finally:
        s1.close()
    assert "error" not in out, out.get("error")
    assert out["exc"].status_code == 409 and out["exc"].detail["code"] == "MARKET_LOCKED"
    wk = _wk_rows(db_session, de_ws.id)
    assert [(w.geo_target_id, w.language_id) for w in wk] == [(DE, RU)]


def test_two_windows_after_first_chunk_commit_market_change_is_immediate_409(db_session, de_ws):
    from app.database import crud

    s1 = SessionLocal()
    try:
        ws1 = verify_workspace(s1, de_ws.id, lock=True)
        rows = stamp_rows_market([{"keyword": "ilk parca", "monthly_volume": 50, "competition_score": 0.5}], ws1)
        crud.create_keywords_bulk(s1, rows, brand_profile_id=de_ws.id, return_details=True)  # chunk commit
        out: dict = {}
        t = threading.Thread(target=_lock_for_update_and_set, args=(de_ws.id, out))
        t.start()
        t.join(timeout=3)
        assert not t.is_alive(), "ilk commit'ten sonra kilit birakilmis olmali"
    finally:
        s1.close()
    assert out["exc"].detail["code"] == "MARKET_LOCKED"
    wk = _wk_rows(db_session, de_ws.id)
    assert len(wk) == 1 and (wk[0].geo_target_id, wk[0].language_id) == (DE, RU)
