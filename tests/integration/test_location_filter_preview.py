# -*- coding: utf-8 -*-
"""Faz 4 — kosu-oncesi lokasyon filtresi onizlemesi (read-only).

plan_v3_lokasyon_filtresi.md §5.7 + §8.3. Kapsam:
  * Onizleme, `build_universe_rows` (context.py) ile GERCEK motorun kullanacagi
    ayni satir kumesini sayar — hacim<=0 (A13) satir "lokasyon nedeniyle
    elendi" diye YANLIS raporlanmaz (bu dosyanin EN KRITIK testi).
  * Onizleme hicbir sey YAZMAZ (profil/keyword/run/havuz).
  * Neden/sehir kirilimi `LOCATION_CITY_FILTER` / `LOCATION_NON_FOCUS_CITY`
    AYRI sayar.
  * Muafiyetle korunan ornekler raporlanir.
  * Bos workspace acik "onizlenecek keyword yok" durumu doner.
  * Draft dogrulama Faz 2 (`_apply_location_policy`) ile AYNI tipli 400
    kodlarini uretir.
  * Ic yardimci paritesi: `build_universe_rows` (onizleme) ile
    `freeze_universe_snapshot` (gercek run) AYNI paylasilan yardimciyi
    kullandigindan AYNI satir kumesini dondurur (bkz. asagidaki NOT — bu
    plan §8.3'un TAMAMINI KANITLAMAZ, Faz 5 bekliyor).
"""
from __future__ import annotations

import pytest

from app.core.engine.context import build_universe_rows, freeze_universe_snapshot
from app.core.policy.location_policy import (
    MAX_EXEMPT_TERM_LENGTH,
    MAX_EXEMPT_TERMS,
    MODE_EXCLUDE_ALL,
    MODE_FOCUS_ONLY,
    TURKISH_PROVINCES,
    evaluate_keyword,
)
from app.database.models import ChannelPool, KeywordScore, ScoringRun

PREVIEW_PATH = "/api/v1/brand-profile/workspaces/{id}/policy/location-preview"


def _preview(client, workspace_id, **payload):
    return client.post(PREVIEW_PATH.format(id=workspace_id), json=payload)


# ── EN KRITIK: A13 (hacim<=0) satir lokasyon-elendi diye raporlanmaz ──────
def test_volume_zero_city_keyword_is_not_reported_as_location_excluded(
    client, make_workspace, make_keyword,
):
    ws = make_workspace()
    # Sehir icerir AMA hacmi 0 -> motor evrenine hic girmez (A13).
    make_keyword("ankara ofisi kiralama", brand_profile_id=ws.id, monthly_volume=0)
    # Kontrol: sehirsiz + gecerli hacim.
    make_keyword("yazilim gelistirme", brand_profile_id=ws.id, monthly_volume=100)

    resp = _preview(client, ws.id, location_filter_mode=MODE_EXCLUDE_ALL)
    assert resp.status_code == 200
    body = resp.json()

    assert body["has_keywords"] is True
    assert body["evaluated_count"] == 1          # yalniz gecerli hacimli satir
    assert body["kept_count"] == 1
    assert body["excluded_count"] == 0
    assert body["excluded_by_city"] == []         # "Ankara" HIC gorunmez
    assert body["excluded_by_reason"] == {}


# ── exclude_all: sehirli her keyword elenir, sehirsiz kalir ───────────────
def test_exclude_all_mode_counts_city_and_non_city_separately(
    client, make_workspace, make_keyword,
):
    ws = make_workspace()
    make_keyword("ankara implant fiyatlari", brand_profile_id=ws.id, monthly_volume=50)
    make_keyword("izmir dis kliniği", brand_profile_id=ws.id, monthly_volume=30)
    make_keyword("implant fiyatlari", brand_profile_id=ws.id, monthly_volume=80)

    resp = _preview(client, ws.id, location_filter_mode=MODE_EXCLUDE_ALL)
    assert resp.status_code == 200
    body = resp.json()

    assert body["has_keywords"] is True
    assert body["evaluated_count"] == 3
    assert body["kept_count"] == 1
    assert body["excluded_count"] == 2
    assert body["excluded_by_reason"] == {"LOCATION_CITY_FILTER": 2}
    cities = {row["city"]: row["count"] for row in body["excluded_by_city"]}
    assert cities == {"Ankara": 1, "İzmir": 1}
    for row in body["excluded_by_city"]:
        assert row["reason"] == "LOCATION_CITY_FILTER"
    sample_cities = {s["matched_city"] for s in body["sample_excluded"]}
    assert sample_cities == {"Ankara", "İzmir"}


# ── focus_only: yalniz odak-disi sehir elenir, kapsayici kural ────────────
def test_focus_only_mode_counts_non_focus_city_reason_separately(
    client, make_workspace, make_keyword,
):
    ws = make_workspace()
    make_keyword("istanbul ankara arasi nakliyat", brand_profile_id=ws.id, monthly_volume=20)
    make_keyword("ankara izmir nakliyat", brand_profile_id=ws.id, monthly_volume=20)
    make_keyword("sehirsiz nakliyat firmasi", brand_profile_id=ws.id, monthly_volume=20)

    resp = _preview(
        client, ws.id,
        location_filter_mode=MODE_FOCUS_ONLY,
        focus_cities=["İstanbul"],
    )
    assert resp.status_code == 200
    body = resp.json()

    assert body["evaluated_count"] == 3
    # istanbul-ankara kapsayici kuralla kalir + sehirsiz kalir = 2 kept
    assert body["kept_count"] == 2
    assert body["excluded_count"] == 1
    assert body["excluded_by_reason"] == {"LOCATION_NON_FOCUS_CITY": 1}
    assert body["excluded_by_city"] == [
        {"city": "Ankara", "reason": "LOCATION_NON_FOCUS_CITY", "count": 1}
    ]


# ── muafiyetle korunan ornekler raporlanir ─────────────────────────────────
def test_exemption_protected_keyword_is_reported_as_sample_exempted(
    client, make_workspace, make_keyword,
):
    ws = make_workspace()
    make_keyword("gaziantep fıstığı fiyat", brand_profile_id=ws.id, monthly_volume=40)
    make_keyword("ankara implant", brand_profile_id=ws.id, monthly_volume=40)

    resp = _preview(
        client, ws.id,
        location_filter_mode=MODE_EXCLUDE_ALL,
        location_exempt_terms=["gaziantep fıstığı"],
    )
    assert resp.status_code == 200
    body = resp.json()

    assert body["kept_count"] == 1
    assert body["excluded_count"] == 1
    assert len(body["sample_exempted"]) == 1
    assert body["sample_exempted"][0]["matched_exempt_term"] == "gaziantep fıstığı"
    assert "gaziantep" in body["sample_exempted"][0]["keyword"].lower()


# ── bos workspace: acik "onizlenecek keyword yok" durumu ─────────────────
def test_empty_workspace_returns_explicit_no_keywords_state(client, make_workspace):
    ws = make_workspace()
    resp = _preview(client, ws.id, location_filter_mode=MODE_EXCLUDE_ALL)
    assert resp.status_code == 200
    body = resp.json()

    assert body["has_keywords"] is False
    assert body["evaluated_count"] == 0
    assert body["kept_count"] == 0
    assert body["excluded_count"] == 0
    assert body["excluded_by_city"] == []
    assert body["sample_excluded"] == []
    assert body["sample_exempted"] == []


# ── hicbir sey YAZILMAZ ────────────────────────────────────────────────────
def test_preview_writes_nothing(client, db_session, make_workspace, make_keyword):
    ws = make_workspace()
    make_keyword("ankara implant fiyatlari", brand_profile_id=ws.id, monthly_volume=50)
    profile_data_before = dict(ws.profile_data or {})

    def _counts():
        return (
            db_session.query(KeywordScore).count(),
            db_session.query(ScoringRun).count(),
            db_session.query(ChannelPool).count(),
        )

    before = _counts()
    resp = _preview(
        client, ws.id,
        location_filter_mode=MODE_FOCUS_ONLY,
        focus_cities=["İstanbul"],
    )
    assert resp.status_code == 200
    after = _counts()
    assert before == after

    db_session.refresh(ws)
    assert dict(ws.profile_data or {}) == profile_data_before


# ── draft dogrulama Faz 2 ile AYNI tipli 400 kodlarini uretir ─────────────
@pytest.mark.parametrize("payload,expected_code", [
    ({"location_filter_mode": "hepsi"}, "INVALID_LOCATION_FILTER_MODE"),
    ({"location_filter_mode": None}, "INVALID_LOCATION_FILTER_MODE"),
    ({"location_filter_mode": MODE_EXCLUDE_ALL, "focus_cities": ["Kadıköy"]},
     "UNSUPPORTED_FOCUS_CITY"),
    ({"location_filter_mode": MODE_FOCUS_ONLY, "focus_cities": []},
     "FOCUS_CITIES_REQUIRED"),
    ({"location_filter_mode": MODE_EXCLUDE_ALL, "focus_cities": "İstanbul"},
     "INVALID_LOCATION_LIST"),
    ({"location_filter_mode": MODE_EXCLUDE_ALL, "location_exempt_terms": "x"},
     "INVALID_LOCATION_LIST"),
    ({"location_filter_mode": MODE_FOCUS_ONLY,
      "focus_cities": list(TURKISH_PROVINCES[:21])},
     "TOO_MANY_FOCUS_CITIES"),
    ({"location_filter_mode": MODE_EXCLUDE_ALL,
      "location_exempt_terms": ["a" * (MAX_EXEMPT_TERM_LENGTH + 1)]},
     "EXEMPT_TERM_TOO_LONG"),
    ({"location_filter_mode": MODE_EXCLUDE_ALL,
      "location_exempt_terms": [f"terim {i}" for i in range(MAX_EXEMPT_TERMS + 1)]},
     "TOO_MANY_EXEMPT_TERMS"),
])
def test_draft_validation_reuses_faz2_typed_400_codes(
    client, make_workspace, payload, expected_code,
):
    ws = make_workspace()
    resp = _preview(client, ws.id, **payload)
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == expected_code


def test_unknown_workspace_returns_404(client):
    resp = _preview(client, 999_999, location_filter_mode=MODE_EXCLUDE_ALL)
    assert resp.status_code == 404


# ── Ic yardimci paritesi (§8.3'un TAMAMI DEGIL — bkz. asagidaki NOT) ──────
def test_preview_and_freeze_share_the_same_universe_helper(
    db_session, make_workspace, make_keyword, make_scoring_run,
):
    """`build_universe_rows` (onizleme) ile `freeze_universe_snapshot` (gercek
    run) AYNI paylasilan satir-secim yardimcisini kullandigi icin AYNI eligible
    satir kumesini dondurur; bu test o ic paylasim gercegini dogrudan kanitlar.

    NOT (kapsam sinirinin ACIK itirafi): bu test motorun kendisine (Family/
    ADS/SEO/SOCIAL) veya engine-side lokasyon uygulamasina HIC DOKUNMAZ —
    cunku Faz 5 (orchestrator'da AI-oncesi lokasyon elemesi) HENUZ INSA
    EDILMEDI. Iki taraf da `evaluate_keyword` burada TESTIN KENDISI
    tarafindan, dogrudan cagrilarak degerlendiriliyor; onizleme HTTP
    endpoint'i de bu testte hic cagrilmiyor. Yani bu test "ayni yardimci iki
    kere cagrilinca ayni satirlari donduruyor" onermesini kanitlar — plan
    §8.3'un "onizlemenin eledigi kume ile CALISAN bir v3 pipeline'in eledigi
    kume AYNIDIR" iddiasinin UC-TAN-UCA halini KANITLAMAZ.

    TODO(Faz 5): orchestrator'a lokasyon uygulamasi eklendiginde, gercek bir
    v3 run'in (Family/ADS/SEO/SOCIAL motorlarindan gecmis) nihai ChannelPool
    disinda biraktigi keyword kumesini, ayni politika altinda onizleme
    endpoint'inin `excluded_by_*` ciktisiyla karsilastiran GERCEK uc-tan-uca
    bir parite testi eklenmelidir (plan_v3_lokasyon_filtresi.md §8.3, son
    madde: "Preview sonucu ile aynı policy altında gerçek run'ın dışladığı
    keyword kümesi aynıdır").
    """
    ws = make_workspace()
    kw_ankara = make_keyword("ankara implant", brand_profile_id=ws.id, monthly_volume=40)
    kw_izmir = make_keyword("izmir implant", brand_profile_id=ws.id, monthly_volume=40)
    kw_plain = make_keyword("implant fiyatlari", brand_profile_id=ws.id, monthly_volume=40)
    kw_zero = make_keyword("ankara zero", brand_profile_id=ws.id, monthly_volume=0)

    run = make_scoring_run(brand_profile_id=ws.id, algorithm_version="v3")

    # Gercek run yolu: freeze_universe_snapshot (KAYDEDER).
    real_universe = freeze_universe_snapshot(db_session, run)
    db_session.commit()

    # Onizleme yolu: build_universe_rows (KAYDETMEZ) — AYNI run parametreleriyle.
    preview_universe = build_universe_rows(db_session, run)

    def _excluded_ids(universe):
        excluded = set()
        for row in universe.rows:
            decision = evaluate_keyword(
                row.keyword_text, mode=MODE_EXCLUDE_ALL, focus_cities=[],
                exempt_terms=[])
            if not decision.is_kept:
                excluded.add(row.keyword_id)
        return excluded

    real_excluded = _excluded_ids(real_universe)
    preview_excluded = _excluded_ids(preview_universe)

    assert real_excluded == preview_excluded == {kw_ankara.id, kw_izmir.id}
    assert kw_zero.id not in real_excluded
    assert kw_zero.id not in preview_excluded
    assert kw_plain.id not in real_excluded

    # Evren satir kumeleri de birebir ayni (A13 sonrasi eligible set).
    real_ids = {r.keyword_id for r in real_universe.rows}
    preview_ids = {r.keyword_id for r in preview_universe.rows}
    assert real_ids == preview_ids == {kw_ankara.id, kw_izmir.id, kw_plain.id}
