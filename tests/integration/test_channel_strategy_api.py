# -*- coding: utf-8 -*-
"""v2.1 kanal stratejisi API + versiyonlama + yarış testleri (plan Faz C)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.database.models import BrandProfile, ScoringRun


# Ürün kararı (V3 tek motor): mevcut v2/v2_1 run'ları salt-okunurdur. Bu
# testler eski motor run'ı üzerinden YENİ kanal ataması / screening
# dispatch'inin çalıştığını iddia ediyordu; o yol artık API + dispatcher +
# task sınırında LEGACY_RUN_READ_ONLY ile kapalı (v3'te screening zaten
# zorunlu 'off'). Red davranışı tests/integration/test_legacy_run_read_only.py
# içinde kilitlidir. Testler silinmedi; eski sözleşmenin kaydı olarak kalır.
LEGACY_DISPATCH_RETIRED = pytest.mark.skip(
    reason="LEGACY_RUN_READ_ONLY: v2/v2_1 run'larında yeni dispatch "
           "emekliye ayrıldı (V3 tek motor)")


def _apply_strategy(db_session, ws, **overrides):
    from app.core.policy.channel_strategy import apply_strategy_update

    params = {
        "product_definition": "Beyaz saçları eski rengine döndüren kozmetik ürün.",
        "content_strategy": "Ürün-problem alanının tamamı.",
        "social_mode": "hype",
        "approve": True,
    }
    params.update(overrides)
    res = apply_strategy_update(
        db_session,
        ws,
        product_definition=params["product_definition"],
        content_strategy=params["content_strategy"],
        social_mode=params["social_mode"],
        approve=params["approve"],
    )
    db_session.commit()
    return res


def test_channel_strategy_endpoints_are_retired(client, make_workspace):
    """GET ve PUT /brand-profile/workspaces/{id}/strategy endpoint'leri emekliye ayrılmıştır (404)."""
    ws = make_workspace("Strateji endpoint retired")
    assert client.get(f"/api/v1/brand-profile/workspaces/{ws.id}/strategy").status_code == 404
    assert client.put(f"/api/v1/brand-profile/workspaces/{ws.id}/strategy", json={
        "product_definition": "x",
        "content_strategy": "y",
        "social_mode": "hype",
        "approve": True,
    }).status_code == 404


def test_approve_versioning_semantics(db_session, make_workspace):
    """Onay: fingerprint değişince +1; aynı içerik yeniden onay artırmaz;
    taslak düzenleme sürüm üretmez (plan §6)."""
    ws = make_workspace("Strateji onay")

    first = _apply_strategy(db_session, ws)
    assert first["strategy_version"] == 1
    assert first["version_bumped"] is True

    same = _apply_strategy(db_session, ws)  # aynı içerik yeniden onay
    assert same["strategy_version"] == 1
    assert same["version_bumped"] is False

    draft = _apply_strategy(db_session, ws, content_strategy="Bambaşka strateji.",
                            approve=False)
    assert draft["strategy_version"] == 1  # taslak artırmaz
    assert draft["strategy"]["status"] == "draft"
    # Taslak, son onay kaydını taşır (onay ezilmez)
    assert draft["strategy"]["approved_fingerprint"] == (
        first["fingerprint"]
    )

    changed = _apply_strategy(db_session, ws, content_strategy="Bambaşka strateji.")
    assert changed["strategy_version"] == 2
    assert changed["version_bumped"] is True


def test_approve_requires_mandatory_texts(db_session, make_workspace):
    from app.core.policy.channel_strategy import StrategyValidationError

    ws = make_workspace("Strateji zorunlu")
    with pytest.raises(StrategyValidationError, match="İçerik stratejisi"):
        _apply_strategy(db_session, ws, content_strategy="")
    with pytest.raises(StrategyValidationError, match="Ürün/hizmet tanımı"):
        _apply_strategy(db_session, ws, product_definition="", approve=True)


def test_strategy_change_stales_only_v21_pools(
    db_session, make_workspace, make_scoring_run
):
    """Onaylı strateji değişimi: v2 havuzu strategy ekseninden ETKİLENMEZ;
    v2_1 havuzu stale olur (merkezi freshness)."""
    from app.core.policy.freshness import compute_pool_freshness

    ws = make_workspace("Strateji freshness")
    _apply_strategy(db_session, ws)  # version 1

    v2_run = make_scoring_run(brand_profile_id=ws.id, status="channel_assigned")
    v2_run.channel_pool_policy_version = ws.policy_version or 1
    v21_run = make_scoring_run(brand_profile_id=ws.id, status="channel_assigned")
    v21_run.algorithm_version = "v2_1"
    v21_run.channel_pool_policy_version = ws.policy_version or 1
    v21_run.channel_pool_strategy_version = 1
    db_session.commit()

    db_session.refresh(ws)
    assert not compute_pool_freshness(v2_run, ws).strategy_stale
    assert not compute_pool_freshness(v21_run, ws).strategy_stale

    _apply_strategy(db_session, ws, content_strategy="Yeni yön.")
    db_session.expire_all()
    ws_fresh = db_session.get(BrandProfile, ws.id)
    assert ws_fresh.strategy_version == 2
    assert not compute_pool_freshness(v2_run, ws_fresh).strategy_stale
    assert compute_pool_freshness(v21_run, ws_fresh).strategy_stale


def test_finalize_strategy_guard_real_two_session_race(
    db_session, make_workspace, make_scoring_run
):
    """GERÇEK iki-session yarışı (Codex Faz C #1): worker session profili
    ÖNCE yükler (identity-map dolu), İKİNCİ SessionLocal sürümü değiştirip
    commit eder; finalize populate_existing sayesinde güncel sürümü görüp
    STRATEGY_CHANGED üretmeli — havuz aktive edilmemeli."""
    from app.core.channel.channel_engine import (
        ChannelEngine,
        StrategyChangedError,
    )
    from app.database.connection import SessionLocal

    ws = make_workspace("Strateji yarış")
    ws.strategy_version = 2
    run = make_scoring_run(brand_profile_id=ws.id, status="channel_assigning")
    run.algorithm_version = "v2_1"
    db_session.commit()

    engine = ChannelEngine(db_session, ai_service=None)

    # Worker davranışı: profil, havuz kurulumu sırasında session'a yüklenir
    loaded = db_session.get(BrandProfile, ws.id)
    assert loaded.strategy_version == 2  # identity-map'te eski değer

    # İKİNCİ session strateji sürümünü değiştirir ve commit eder
    other = SessionLocal()
    try:
        other_ws = other.get(BrandProfile, ws.id)
        other_ws.strategy_version = 3
        other.commit()
    finally:
        other.close()

    with pytest.raises(StrategyChangedError, match="STRATEGY_CHANGED"):
        engine._finalize_strategy_version(run, requested_strategy_version=2)
    db_session.expire_all()
    fresh_run = db_session.get(ScoringRun, run.id)
    assert fresh_run.channel_pool_strategy_version is None  # aktive edilmedi

    # Eşleşme yolu: güncel sürümle istenirse yazılır
    engine._finalize_strategy_version(fresh_run, requested_strategy_version=3)
    assert fresh_run.channel_pool_strategy_version == 3

    # v2 run guard'ı tamamen atlar (istenmeyen None bile sorun değil)
    v2_run = make_scoring_run(brand_profile_id=ws.id, status="channel_assigning")
    engine._finalize_strategy_version(v2_run, requested_strategy_version=None)
    assert v2_run.channel_pool_strategy_version is None


def test_policy_finalize_guard_real_two_session_race(
    db_session, make_workspace, make_scoring_run
):
    """Aynı identity-map tuzağı _finalize_pool_versions için de kapalı
    (Codex #1: 'her iki sorguya populate_existing')."""
    from app.core.channel.channel_engine import (
        ChannelEngine,
        PolicyVersionChangedError,
    )
    from app.database.connection import SessionLocal

    ws = make_workspace("Politika yarış")
    run = make_scoring_run(brand_profile_id=ws.id, status="channel_assigning",
                           skip_relevance=True)
    db_session.commit()
    requested = int(ws.policy_version or 1)

    engine = ChannelEngine(db_session, ai_service=None)
    _ = db_session.get(BrandProfile, ws.id)  # identity-map'i doldur

    other = SessionLocal()
    try:
        other_ws = other.get(BrandProfile, ws.id)
        other_ws.policy_version = requested + 5
        other.commit()
    finally:
        other.close()

    with pytest.raises(PolicyVersionChangedError):
        engine._finalize_pool_versions(
            run,
            requested_policy_version=requested,
            requested_anchor_version=None,
        )


def test_strategy_invalidation_targets_v21_but_not_v2_or_v3(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """Strateji girdisini yalnız legacy v2_1 kullanır (bayatlar); v2 ve v3 etkilenmez (ADR-004)."""
    from app.database.models import ContentOutput

    ws = make_workspace("Strateji içerik izolasyonu")
    kw = make_keyword("test kelimesi", brand_profile_id=ws.id)
    v2_run = make_scoring_run(brand_profile_id=ws.id, status="channel_assigned")
    v21_run = make_scoring_run(brand_profile_id=ws.id, status="channel_assigned")
    v21_run.algorithm_version = "v2_1"
    v3_run = make_scoring_run(brand_profile_id=ws.id, status="channel_assigned")
    v3_run.algorithm_version = "v3"
    v2_content = ContentOutput(
        scoring_run_id=v2_run.id, keyword_id=kw.id, channel="SEO",
        content_type="blog_post", content_data={"t": "v2"},
    )
    v21_content = ContentOutput(
        scoring_run_id=v21_run.id, keyword_id=kw.id, channel="SEO",
        content_type="blog_post", content_data={"t": "v21"},
    )
    v3_content = ContentOutput(
        scoring_run_id=v3_run.id, keyword_id=kw.id, channel="SEO",
        content_type="blog_post", content_data={"t": "v3"},
    )
    db_session.add_all([v2_content, v21_content, v3_content])
    db_session.commit()

    _apply_strategy(db_session, ws)  # ilk onay → version 1, invalidation koşar

    db_session.expire_all()
    assert db_session.get(ContentOutput, v2_content.id).is_stale is False
    assert db_session.get(ContentOutput, v21_content.id).is_stale is True
    assert db_session.get(ContentOutput, v3_content.id).is_stale is False


def test_draft_over_legacy_approved_preserves_approval(db_session, make_workspace):
    """Codex Faz C 2. tur #2: 738d858-öncesi biçim (approved_snapshot YOK,
    status=approved) üzerine taslak yazmak onayı KAYBETTİRMEZ — üst düzey
    alanlardan snapshot kurulur."""
    from app.core.policy.channel_strategy import strategy_snapshot_for_run

    ws = make_workspace("Legacy onay")
    ws.channel_strategy = {  # eski biçim: approved_snapshot alanı yok
        "product_definition": "Eski biçim ürün tanımı",
        "content_strategy": "Eski biçim strateji",
        "social_mode": "hype",
        "schema_version": 1,
        "status": "approved",
        "approved_at": "2026-07-23T00:00:00+00:00",
        "approved_fingerprint": "legacy-fp",
    }
    ws.strategy_version = 1
    db_session.commit()

    draft = _apply_strategy(db_session, ws, content_strategy="Yeni taslak.", approve=False)
    assert draft["strategy"]["status"] == "draft"

    db_session.expire_all()
    snap = strategy_snapshot_for_run(db_session.get(BrandProfile, ws.id))
    assert snap is not None
    assert snap["product_definition"] == "Eski biçim ürün tanımı"
    assert snap["content_strategy"] == "Eski biçim strateji"


def test_draft_does_not_destroy_approved_snapshot(db_session, make_workspace):
    """Codex #3: taslak kaydı onaylı snapshot'ı SİLMEZ — dispatch geçerli
    kalır (STRATEGY_REQUIRED'a düşmez), snapshot onaylı METNİ taşır."""
    from app.core.policy.channel_strategy import strategy_snapshot_for_run

    ws = make_workspace("Taslak koruması")
    approved = _apply_strategy(db_session, ws)
    approved_fp = approved["fingerprint"]

    draft = _apply_strategy(db_session, ws, content_strategy="Yeni taslak fikri.",
                            approve=False)
    assert draft["strategy"]["status"] == "draft"

    db_session.expire_all()
    snap = strategy_snapshot_for_run(db_session.get(BrandProfile, ws.id))
    assert snap is not None  # onay hâlâ geçerli
    assert snap["content_strategy"] == "Ürün-problem alanının tamamı."
    assert snap["fingerprint"] == approved_fp
    assert snap["strategy_version"] == 1


@LEGACY_DISPATCH_RETIRED
def test_dispatcher_requires_approved_strategy_for_v21(
    db_session, make_workspace, make_scoring_run, monkeypatch
):
    """v2_1 dispatch onaylı strateji olmadan reddedilir; authority modu
    deneysel — sessizce hype koşulmaz. Redler TİPLİ ön-koşul hatasıdır
    (Codex #5: API katmanı 409 {code, message}'a çevirir)."""
    from app.core.channel.assignment_dispatcher import (
        ChannelAssignmentPreconditionError,
        enqueue_channel_assignment,
    )

    monkeypatch.setattr(
        "app.tasks.intent_tasks.run_channel_assignment_task.apply_async",
        lambda *a, **k: None,
    )
    ws = make_workspace("v21 dispatch guard")
    run = make_scoring_run(brand_profile_id=ws.id, status="scored")
    run.algorithm_version = "v2_1"
    db_session.commit()

    with pytest.raises(ChannelAssignmentPreconditionError) as exc:
        enqueue_channel_assignment(db_session, run, relevance_coefficient=1.0,
                                   from_status="scored")
    assert exc.value.code == "STRATEGY_REQUIRED"

    ws.channel_strategy = {
        "product_definition": "x", "content_strategy": "y",
        "social_mode": "authority", "schema_version": 1,
        "status": "approved",
    }
    ws.strategy_version = 1
    db_session.commit()
    with pytest.raises(ChannelAssignmentPreconditionError) as exc:
        enqueue_channel_assignment(db_session, run, relevance_coefficient=1.0,
                                   from_status="scored")
    assert exc.value.code == "SOCIAL_AUTHORITY_EXPERIMENTAL"


@LEGACY_DISPATCH_RETIRED
def test_dispatch_allows_authority_when_social_disabled(
    db_session, make_workspace, make_scoring_run, monkeypatch
):
    """DAR KAPI dispatch tarafında da geçerli: authority + SOCIAL kapalı.

    Dispatcher API kapısını TEKRAR doğrular (run yaratıldıktan sonra strateji
    authority'ye çevrilmiş olabilir); kural aynı olmalı.
    """
    from app.core.channel.assignment_dispatcher import enqueue_channel_assignment

    monkeypatch.setattr(
        "app.tasks.intent_tasks.run_channel_assignment_task.apply_async",
        lambda *args, **kwargs: None,
    )
    ws = make_workspace("dispatch authority social kapali")
    ws.channel_strategy = {
        "product_definition": "x", "content_strategy": "y",
        "social_mode": "authority", "schema_version": 1,
        "status": "approved",
    }
    ws.strategy_version = 1
    run = make_scoring_run(brand_profile_id=ws.id, status="scored")
    run.algorithm_version = "v2_1"
    run.enable_social = False
    db_session.commit()

    result = enqueue_channel_assignment(
        db_session, run, relevance_coefficient=1.0, from_status="scored"
    )

    assert result["task_id"]
    db_session.refresh(run)
    manifest = run.execution_manifest
    assert manifest["strategy_snapshot"]["social_mode"] == "authority"
    assert manifest["algorithm_version"] == "v2_1"


def test_manual_assign_endpoint_maps_precondition_to_409(
    client, db_session, make_workspace, make_scoring_run
):
    """Codex #5: manuel assign endpoint'i tipli ön-koşulu 500 değil
    409 {code, message} olarak döndürür. V3 tek motor kararından sonra
    v2_1 run'ında İLK ön-koşul salt-okunur kapısıdır (STRATEGY_REQUIRED'a
    hiç gelinmez)."""
    ws = make_workspace("v21 409 sözleşmesi")
    run = make_scoring_run(brand_profile_id=ws.id, status="scored")
    run.algorithm_version = "v2_1"
    db_session.commit()

    res = client.post(
        f"/api/v1/channels/runs/{run.id}/assign",
        params={"brand_profile_id": ws.id},
    )
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "LEGACY_RUN_READ_ONLY"


@LEGACY_DISPATCH_RETIRED
def test_v21_dispatch_manifest_carries_strategy(
    db_session, make_workspace, make_scoring_run, monkeypatch
):
    from app.core.channel.assignment_dispatcher import enqueue_channel_assignment
    from app.core.policy.channel_strategy import (
        canonical_channel_strategy_fingerprint,
    )

    dispatched = {}
    monkeypatch.setattr(
        "app.tasks.intent_tasks.run_channel_assignment_task.apply_async",
        lambda *a, **k: dispatched.update(k.get("kwargs") or {}),
    )
    ws = make_workspace("v21 manifest")
    strategy = {
        "product_definition": "Beyaz saç çözümü", "content_strategy": "Alanın tamamı",
        "social_mode": "hype", "schema_version": 1, "status": "approved",
    }
    ws.channel_strategy = strategy
    ws.strategy_version = 4
    # Codex Faz D 2. tur #1: hedef kitle de dispatch snapshot'ının parçası
    ws.profile_data = {**(ws.profile_data or {}), "target_audience": "KOBİ'ler"}
    run = make_scoring_run(brand_profile_id=ws.id, status="scored")
    run.algorithm_version = "v2_1"
    db_session.commit()

    enqueue_channel_assignment(db_session, run, relevance_coefficient=1.0,
                               from_status="scored")
    db_session.expire_all()
    manifest = db_session.get(ScoringRun, run.id).execution_manifest
    assert manifest["algorithm_version"] == "v2_1"
    assert manifest["requested_strategy_version"] == 4
    snap = manifest["strategy_snapshot"]
    assert snap["strategy_version"] == 4
    assert snap["fingerprint"] == canonical_channel_strategy_fingerprint(strategy)
    assert snap["target_audience"] == "KOBİ'ler"
    assert dispatched.get("requested_strategy_version") == 4
    # Plan Faz E: aday modu / relevance / expansion manifest'te AÇIK alanlar
    assert manifest["candidate_mode"] == "current_window"
    assert isinstance(manifest["relevance_enabled"], bool)
    assert manifest["expansion_policy"] == {"max_rounds": 2, "ai_budget": 60}


def test_v3_dispatch_manifest_and_attempt_never_carries_strategy(
    db_session, make_workspace, make_scoring_run, monkeypatch
):
    """V3 dispatch doğrulaması: strategy_snapshot yok, celery kwargs'ta requested_strategy_version yok,
    ChannelAssignmentAttempt.requested_strategy_version is None, screening_mode='off'."""
    from app.core.channel.assignment_dispatcher import enqueue_channel_assignment
    from app.database.models import ChannelAssignmentAttempt

    dispatched = {}
    monkeypatch.setattr(
        "app.tasks.intent_tasks.run_channel_assignment_task.apply_async",
        lambda *a, **k: dispatched.update(k.get("kwargs") or {}),
    )
    ws = make_workspace("v3 dispatch regression ws")
    ws.channel_strategy = {
        "product_definition": "x",
        "content_strategy": "y",
        "social_mode": "hype",
        "status": "approved",
    }
    ws.strategy_version = 4
    run = make_scoring_run(brand_profile_id=ws.id, status="scored")
    run.algorithm_version = "v3"
    run.execution_manifest = {
        "engine_v3": {"firm_block_sha256": "fake_sha_v3"}
    }
    db_session.commit()

    result = enqueue_channel_assignment(
        db_session, run, relevance_coefficient=1.0, from_status="scored"
    )
    assert result["task_id"]

    db_session.expire_all()
    fresh_run = db_session.get(ScoringRun, run.id)
    manifest = fresh_run.execution_manifest

    # 1. execution_manifest içinde strategy_snapshot ve requested_strategy_version yok
    assert "strategy_snapshot" not in manifest
    assert "requested_strategy_version" not in manifest

    # 2. Celery task kwargs içinde requested_strategy_version yok
    assert "requested_strategy_version" not in dispatched

    # 3. ChannelAssignmentAttempt.requested_strategy_version is None (DB doğrulaması)
    attempt = (
        db_session.query(ChannelAssignmentAttempt)
        .filter_by(scoring_run_id=run.id)
        .order_by(ChannelAssignmentAttempt.id.desc())
        .first()
    )
    assert attempt is not None
    assert attempt.requested_strategy_version is None

    # 4. v3 screening_mode="off" kalmalı
    assert attempt.screening_mode == "off"
    assert result["screening_mode"] == "off"
