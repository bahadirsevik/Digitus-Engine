# -*- coding: utf-8 -*-
"""Lokasyon politikasının run mührü ve finalize fail-closed davranışı.

plan_v3_lokasyon_filtresi.md §5.4. Kapsam:
  * Yeni mühür lokasyon snapshot'ı OLMADAN atılamaz (sema surumu 2).
  * Mühürden sonra profil lokasyon politikası değişirse finalize DURUR.
  * Eski sema (surum 1) YALNIZ canlı filtre `none` iken tamamlanabilir.
  * Fail-closed halde EngineSelection ve ChannelPool YAZILMAZ.

KAPSAM DIŞI: `write_stage_results` checkpoint davranışı ayrı bir çalışmadır.
Burada yalnız "checkpoint varsa bile havuz yazılmaz" doğrulanır.
"""
from __future__ import annotations

import pytest

from app.core.engine.context import (
    build_firm_profile,
    firm_block,
    firm_block_sha256,
    freeze_universe_snapshot,
)
from app.core.engine.persistence import (
    MANIFEST_KEY,
    MANIFEST_SCHEMA_VERSION,
    StageContextMismatch,
    manifest_location_policy,
    seal_manifest,
)
from app.core.engine.policy_gate import EngineInputError, finalize_engine_delivery
from app.core.policy.location_policy import policy_snapshot as _loc_snap
from app.database.models import ChannelPool, EngineSelection


def _prepare(db_session, make_workspace, make_scoring_run, make_keyword,
             *, profile_location=None):
    ws = make_workspace()
    ws.status = "confirmed"
    ws.profile_data = {
        "company_name": "Antigravity", "sector": "yazilim",
        "brand_summary": "ozet", "target_audience": "hedef",
        "products": ["agent"], "services": [],
        **(profile_location or {}),
    }
    run = make_scoring_run(brand_profile_id=ws.id)
    run.algorithm_version = "v3"
    run.enable_ads, run.enable_seo, run.enable_social = True, False, False
    run.ads_capacity = 10
    kw = make_keyword("akilli agent platformu", brand_profile_id=ws.id)
    db_session.commit()
    freeze_universe_snapshot(db_session, run)
    return ws, run, kw


def _seal(run, ws, location_policy):
    return seal_manifest(
        run,
        firm_block_sha256=firm_block_sha256(firm_block(build_firm_profile(ws))),
        algorithm_versions={"ads": "nihai_niche_v1"},
        models={"ads": "gemini-2.5"},
        prompt_shas={"ads": "sha_ads"},
        location_policy=location_policy,
    )


def _selections(kw):
    return {"ADS": [{"keyword_id": kw.id, "algorithm_rank": 1,
                     "scores": {"Core": 0.9, "Selection": 0.9},
                     "pool_class": "primary"}]}


def _delivery_rows(db_session, run):
    return (
        db_session.query(EngineSelection)
        .filter(EngineSelection.scoring_run_id == run.id).count(),
        db_session.query(ChannelPool)
        .filter(ChannelPool.scoring_run_id == run.id).count(),
    )


# ── 1. Lokasyon snapshot'ı olmadan mühürlenemez ─────────────────────────
@pytest.mark.parametrize("bad", [None, {}, {"mode": "none"}, "none", 5])
def test_new_manifest_cannot_be_sealed_without_location_snapshot(
        db_session, make_workspace, make_scoring_run, make_keyword, bad):
    ws, run, _ = _prepare(db_session, make_workspace, make_scoring_run,
                          make_keyword)
    with pytest.raises(StageContextMismatch):
        _seal(run, ws, bad)


def test_sealed_section_carries_schema_version_and_policy(
        db_session, make_workspace, make_scoring_run, make_keyword):
    ws, run, _ = _prepare(db_session, make_workspace, make_scoring_run,
                          make_keyword)
    manifest = _seal(run, ws, _loc_snap(ws.profile_data))
    section = manifest[MANIFEST_KEY]
    assert section["manifest_schema_version"] == MANIFEST_SCHEMA_VERSION
    assert section["location_policy"]["enforcement_fingerprint"]


def test_v2_manifest_with_deleted_policy_is_refused_even_when_filter_is_off(
        db_session, make_workspace, make_scoring_run, make_keyword):
    """Politikanın YOKLUĞU tek başına 'eski mühür' sayılmaz — sema karar verir."""
    ws, run, kw = _prepare(db_session, make_workspace, make_scoring_run,
                           make_keyword)
    _seal(run, ws, _loc_snap(ws.profile_data))          # sema 2, mod=none
    section = {k: v for k, v in run.execution_manifest[MANIFEST_KEY].items()
               if k != "location_policy"}               # sema 2 KALIYOR
    run.execution_manifest = {**run.execution_manifest, MANIFEST_KEY: section}
    db_session.commit()

    with pytest.raises(StageContextMismatch, match="eski muhur SAYILMAZ"):
        manifest_location_policy(run)
    # Canlı mod `none` olsa bile teslimat açılmaz.
    with pytest.raises(StageContextMismatch):
        finalize_engine_delivery(db_session, run=run,
                                 channel_selections=_selections(kw))
    assert _delivery_rows(db_session, run) == (0, 0)


@pytest.mark.parametrize("drop", ["mode", "focus_cities", "exempt_terms",
                                  "city_lexicon_version", "city_lexicon_sha256"])
def test_v2_policy_missing_any_required_field_is_refused(
        db_session, make_workspace, make_scoring_run, make_keyword, drop):
    """Fingerprint tek başına YETMEZ; denetim ve sözlük kimliği de zorunludur."""
    ws, run, _ = _prepare(db_session, make_workspace, make_scoring_run,
                          make_keyword)
    _seal(run, ws, _loc_snap(ws.profile_data))
    section = dict(run.execution_manifest[MANIFEST_KEY])
    section["location_policy"] = {
        k: v for k, v in section["location_policy"].items() if k != drop}
    run.execution_manifest = {**run.execution_manifest, MANIFEST_KEY: section}
    db_session.commit()

    with pytest.raises(StageContextMismatch, match="eksik alan"):
        manifest_location_policy(run)


def test_seal_refuses_policy_missing_required_fields(
        db_session, make_workspace, make_scoring_run, make_keyword):
    ws, run, _ = _prepare(db_session, make_workspace, make_scoring_run,
                          make_keyword)
    partial = {"enforcement_fingerprint": "abc"}          # diger alanlar YOK
    with pytest.raises(StageContextMismatch, match="zorunlu alan"):
        _seal(run, ws, partial)


def _reseal_section(db_session, run, **overrides):
    """Mührün `engine_v3` bölümünü doğrudan kurcalar (bozuk manifest simülasyonu)."""
    section = dict(run.execution_manifest[MANIFEST_KEY])
    for key, value in overrides.items():
        if value is _DROP:
            section.pop(key, None)
        else:
            section[key] = value
    run.execution_manifest = {**run.execution_manifest, MANIFEST_KEY: section}
    db_session.commit()
    return section


_DROP = object()


@pytest.mark.parametrize("version", [3, 99, 0, -1, "2", 2.0, True, None])
def test_unsupported_schema_version_is_refused(
        db_session, make_workspace, make_scoring_run, make_keyword, version):
    """Açık `None` dahil — sürüm anahtarı VARSA değeri geçerli olmalıdır."""
    ws, run, _ = _prepare(db_session, make_workspace, make_scoring_run,
                          make_keyword)
    _seal(run, ws, _loc_snap(ws.profile_data))
    _reseal_section(db_session, run, manifest_schema_version=version)

    with pytest.raises(StageContextMismatch):
        manifest_location_policy(run)


def test_versionless_manifest_carrying_a_policy_is_refused(
        db_session, make_workspace, make_scoring_run, make_keyword):
    """Gerçek legacy mühürde politika BULUNMAZ; karışımı tutarsız şemadır."""
    ws, run, _ = _prepare(db_session, make_workspace, make_scoring_run,
                          make_keyword)
    _seal(run, ws, _loc_snap(ws.profile_data))
    _reseal_section(db_session, run, manifest_schema_version=_DROP)

    with pytest.raises(StageContextMismatch, match="tutarsiz muhur"):
        manifest_location_policy(run)


def test_schema_v1_carrying_a_policy_is_refused(
        db_session, make_workspace, make_scoring_run, make_keyword):
    ws, run, _ = _prepare(db_session, make_workspace, make_scoring_run,
                          make_keyword)
    _seal(run, ws, _loc_snap(ws.profile_data))
    _reseal_section(db_session, run, manifest_schema_version=1)

    with pytest.raises(StageContextMismatch, match="TASIYAMAZ"):
        manifest_location_policy(run)


def test_explicit_schema_v1_without_policy_is_accepted_as_legacy(
        db_session, make_workspace, make_scoring_run, make_keyword):
    ws, run, kw = _prepare(db_session, make_workspace, make_scoring_run,
                           make_keyword)
    _seal(run, ws, _loc_snap(ws.profile_data))
    _reseal_section(db_session, run, manifest_schema_version=1,
                    location_policy=_DROP)

    assert manifest_location_policy(run) is None
    summary = finalize_engine_delivery(db_session, run=run,
                                       channel_selections=_selections(kw))
    db_session.commit()
    assert summary["channels"]["ADS"]["pool_count"] == 1


def test_corrupt_sealed_policy_is_not_silently_defaulted(
        db_session, make_workspace, make_scoring_run, make_keyword):
    ws, run, _ = _prepare(db_session, make_workspace, make_scoring_run,
                          make_keyword)
    _seal(run, ws, _loc_snap(ws.profile_data))
    section = dict(run.execution_manifest[MANIFEST_KEY])
    section["location_policy"] = {"mode": "exclude_all"}   # fingerprint YOK
    run.execution_manifest = {**run.execution_manifest, MANIFEST_KEY: section}
    db_session.commit()
    with pytest.raises(StageContextMismatch):
        manifest_location_policy(run)


# ── 2. Mühürden sonra politika değişirse finalize durur ─────────────────
def test_finalize_fails_closed_when_location_policy_changes_after_seal(
        db_session, make_workspace, make_scoring_run, make_keyword):
    ws, run, kw = _prepare(db_session, make_workspace, make_scoring_run,
                           make_keyword)
    _seal(run, ws, _loc_snap(ws.profile_data))          # mühür: mode=none
    db_session.commit()

    ws.profile_data = {**ws.profile_data,
                       "location_filter_mode": "exclude_all"}
    db_session.commit()

    with pytest.raises(EngineInputError, match="lokasyon politikasi"):
        finalize_engine_delivery(db_session, run=run,
                                 channel_selections=_selections(kw))
    assert _delivery_rows(db_session, run) == (0, 0)


def test_finalize_fails_closed_when_focus_cities_change_in_focus_only(
        db_session, make_workspace, make_scoring_run, make_keyword):
    loc = {"location_filter_mode": "focus_only", "focus_cities": ["İstanbul"]}
    ws, run, kw = _prepare(db_session, make_workspace, make_scoring_run,
                           make_keyword, profile_location=loc)
    _seal(run, ws, _loc_snap(ws.profile_data))
    db_session.commit()

    ws.profile_data = {**ws.profile_data, "focus_cities": ["Ankara"]}
    db_session.commit()

    with pytest.raises(EngineInputError, match="lokasyon politikasi"):
        finalize_engine_delivery(db_session, run=run,
                                 channel_selections=_selections(kw))
    assert _delivery_rows(db_session, run) == (0, 0)


def test_finalize_succeeds_when_location_policy_is_unchanged(
        db_session, make_workspace, make_scoring_run, make_keyword):
    loc = {"location_filter_mode": "exclude_all",
           "location_exempt_terms": ["gaziantep fıstığı"]}
    ws, run, kw = _prepare(db_session, make_workspace, make_scoring_run,
                           make_keyword, profile_location=loc)
    _seal(run, ws, _loc_snap(ws.profile_data))
    db_session.commit()

    summary = finalize_engine_delivery(db_session, run=run,
                                       channel_selections=_selections(kw))
    db_session.commit()
    assert summary["channels"]["ADS"]["pool_count"] == 1


def test_focus_cities_change_outside_focus_only_does_not_block_finalize(
        db_session, make_workspace, make_scoring_run, make_keyword):
    """Sonucu etkilemeyen metadata değişimi koşuyu DURDURMAZ."""
    loc = {"location_filter_mode": "exclude_all", "focus_cities": ["İzmir"]}
    ws, run, kw = _prepare(db_session, make_workspace, make_scoring_run,
                           make_keyword, profile_location=loc)
    _seal(run, ws, _loc_snap(ws.profile_data))
    db_session.commit()

    ws.profile_data = {**ws.profile_data, "focus_cities": ["Ankara", "Bursa"]}
    db_session.commit()

    summary = finalize_engine_delivery(db_session, run=run,
                                       channel_selections=_selections(kw))
    db_session.commit()
    assert summary["channels"]["ADS"]["pool_count"] == 1


# ── 3. Eski sema uyumluluğu ─────────────────────────────────────────────
def _downgrade_to_schema_v1(db_session, run):
    section = {k: v for k, v in run.execution_manifest[MANIFEST_KEY].items()
               if k not in ("location_policy", "manifest_schema_version")}
    run.execution_manifest = {**run.execution_manifest, MANIFEST_KEY: section}
    db_session.commit()


def test_legacy_manifest_completes_only_while_filter_is_disabled(
        db_session, make_workspace, make_scoring_run, make_keyword):
    ws, run, kw = _prepare(db_session, make_workspace, make_scoring_run,
                           make_keyword)
    _seal(run, ws, _loc_snap(ws.profile_data))
    _downgrade_to_schema_v1(db_session, run)

    assert manifest_location_policy(run) is None
    summary = finalize_engine_delivery(db_session, run=run,
                                       channel_selections=_selections(kw))
    db_session.commit()
    assert summary["channels"]["ADS"]["pool_count"] == 1


@pytest.mark.parametrize("mode,extra", [
    ("exclude_all", {}),
    ("focus_only", {"focus_cities": ["İstanbul"]}),
])
def test_legacy_manifest_is_refused_once_filter_is_enabled(
        db_session, make_workspace, make_scoring_run, make_keyword, mode, extra):
    """Yeni kullanıcı tercihi eski koşuya SESSİZCE uygulanmaz."""
    ws, run, kw = _prepare(db_session, make_workspace, make_scoring_run,
                           make_keyword)
    _seal(run, ws, _loc_snap(ws.profile_data))
    _downgrade_to_schema_v1(db_session, run)

    ws.profile_data = {**ws.profile_data,
                       "location_filter_mode": mode, **extra}
    db_session.commit()

    with pytest.raises(EngineInputError, match="yeni kosu gerekir"):
        finalize_engine_delivery(db_session, run=run,
                                 channel_selections=_selections(kw))
    assert _delivery_rows(db_session, run) == (0, 0)


# ── 4. Checkpoint varken de havuz yazılmaz ──────────────────────────────
def test_existing_stage_checkpoint_does_not_produce_pools_on_fail_closed(
        db_session, make_workspace, make_scoring_run, make_keyword):
    """Aşama checkpoint'i kalıcı olsa bile teslimat satırı ÜRETİLMEZ.

    Checkpoint yazımının kendisi bu testin konusu DEĞİLDİR; burada yalnız
    fail-closed sonrası teslimat tablolarının boş kaldığı doğrulanır.
    """
    from app.database.models import EngineStageResult

    ws, run, kw = _prepare(db_session, make_workspace, make_scoring_run,
                           make_keyword)
    _seal(run, ws, _loc_snap(ws.profile_data))
    db_session.add(EngineStageResult(
        scoring_run_id=run.id, stage="ads_funnel", scope_type="keyword",
        scope_key=str(kw.id), payload={"funnel": "transactional"},
        model="gemini-2.5", prompt_sha="sha_ads",
        firm_block_sha256=firm_block_sha256(firm_block(build_firm_profile(ws)))))
    db_session.commit()

    ws.profile_data = {**ws.profile_data,
                       "location_filter_mode": "exclude_all"}
    db_session.commit()

    with pytest.raises(EngineInputError):
        finalize_engine_delivery(db_session, run=run,
                                 channel_selections=_selections(kw))

    assert _delivery_rows(db_session, run) == (0, 0)
    # Checkpoint satırı YERİNDE kalır (ayrı çalışmanın sözleşmesi).
    assert db_session.query(EngineStageResult).filter(
        EngineStageResult.scoring_run_id == run.id).count() == 1
