# -*- coding: utf-8 -*-
"""Motor v3 kabul kapıları testleri (plan_algoritma_entegrasyonu.md K2/K10/
K13/K16, §7 Faz 1 son parça).

KAPSAM: yalnız "v3 bir run sürümü olarak kabul edilir / reddedilir" backend
şema/dispatch kapıları. AI koşusu YOK, üretim verisi YAZILMAZ, kilitli motor
(Faz 2-7) ÇALIŞTIRILMAZ — bu dosyadaki her 409, eski v2 hattının SESSİZCE
çağrılmadığını da kanıtlar (monkeypatch tuzağı: v2 ScoreEngine.run_scoring
/ Celery run_channel_assignment_task.apply_async çağrılırsa test patlar).
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.config import settings
from app.database.models import ScoringRun

RUN_BODY = {
    "run_name": "engine v3 kabul",
    "ads_capacity": 10,
    "seo_capacity": 10,
    "social_capacity": 10,
}

APPROVED_AUTHORITY_STRATEGY = {
    "product_definition": "Beyaz saçları eski rengine döndüren kozmetik ürün.",
    "content_strategy": "Ürün-problem alanının tamamı.",
    "social_mode": "authority",
    "schema_version": 1,
    "status": "approved",
}

APPROVED_HYPE_STRATEGY = {
    "product_definition": "Beyaz saçları eski rengine döndüren kozmetik ürün.",
    "content_strategy": "Ürün-problem alanının tamamı.",
    "social_mode": "hype",
    "schema_version": 1,
    "status": "approved",
}


def _create(client, ws_id, **overrides):
    body = {**RUN_BODY, "brand_profile_id": ws_id, **overrides}
    return client.post("/api/v1/scoring/runs", json=body)


def _create_v3_run(client, db_session, make_workspace, monkeypatch, name,
                   **overrides):
    """Yardımcı: bayrağı açar, v3 run'ı BAŞARIYLA oluşturur, (ws, run_id) döner.

    K14 tek koşu sözleşmesi: v3 run'ları auto_assign_channels=true
    ZORUNLU kabul edilir — varsayılan burada True'dur, çağıran
    override edebilir (409 senaryoları için)."""
    monkeypatch.setattr(settings, "ENABLE_ENGINE_V3", True)
    ws = make_workspace(name)
    overrides.setdefault("auto_assign_channels", True)
    res = _create(client, ws.id, algorithm_version="v3", **overrides)
    assert res.status_code == 201, res.text
    return ws, res.json()["id"]


class TestCapabilitiesEndpoint:
    """Codex Faz E deseninin v3 tekrarı: UI 409 duvarına çarpmadan bayrağı bilir."""

    def test_default_flag_is_on(self, client):
        res = client.get("/api/v1/scoring/capabilities")
        assert res.status_code == 200
        assert res.json()["engine_v3_enabled"] is True

    def test_flag_off(self, client, monkeypatch):
        monkeypatch.setattr(settings, "ENABLE_ENGINE_V3", False)
        res = client.get("/api/v1/scoring/capabilities")
        assert res.status_code == 200
        assert res.json()["engine_v3_enabled"] is False

    def test_flag_on(self, client, monkeypatch):
        monkeypatch.setattr(settings, "ENABLE_ENGINE_V3", True)
        res = client.get("/api/v1/scoring/capabilities")
        assert res.status_code == 200
        assert res.json()["engine_v3_enabled"] is True

    def test_v21_flag_unaffected_by_v3_flag(self, client, monkeypatch):
        """İki deney bayrağı birbirinden BAĞIMSIZ okunur."""
        monkeypatch.setattr(settings, "ENABLE_V21_EXPERIMENT", True)
        monkeypatch.setattr(settings, "ENABLE_ENGINE_V3", False)
        res = client.get("/api/v1/scoring/capabilities")
        body = res.json()
        assert body["v21_experiment_enabled"] is True
        assert body["engine_v3_enabled"] is False


class TestEngineV3CreateGates:
    def test_default_creates_v3_run(self, client, db_session, make_workspace):
        """Schema varsayılanı: algorithm_version=v3 ve auto_assign_channels=true."""
        ws = make_workspace("v3 default create")
        res = _create(client, ws.id)
        assert res.status_code == 201
        data = res.json()
        assert data["algorithm_version"] == "v3"
        assert data["auto_assign_channels"] is True
        db_session.expire_all()
        run = db_session.get(ScoringRun, data["id"])
        assert run.algorithm_version == "v3"
        assert run.auto_assign_channels is True

    def test_flag_off_default_create_returns_typed_409(
        self, client, db_session, make_workspace, monkeypatch
    ):
        """Bayrak kapalıyken varsayılan v3 run oluşturma 409 ENGINE_V3_DISABLED verir."""
        monkeypatch.setattr(settings, "ENABLE_ENGINE_V3", False)
        ws = make_workspace("v3 flag off default")
        res = _create(client, ws.id)
        assert res.status_code == 409
        assert res.json()["detail"]["code"] == "ENGINE_V3_DISABLED"

    def test_v3_create_does_not_start_provider_or_celery(
        self, client, db_session, make_workspace, monkeypatch
    ):
        """v3 run oluşturma tek başına provider veya Celery çağrısı başlatmaz."""
        from app.tasks.intent_tasks import run_channel_assignment_task
        from app.generators.ai_service import GeminiService

        def _boom_celery(*args, **kwargs):
            raise AssertionError("Celery run oluşturulurken çağrılmamalıydı")

        def _boom_ai(*args, **kwargs):
            raise AssertionError("AI provider run oluşturulurken çağrılmamalıydı")

        monkeypatch.setattr(run_channel_assignment_task, "apply_async", _boom_celery)
        monkeypatch.setattr(GeminiService, "_execute", _boom_ai)

        ws = make_workspace("v3 create no side effect")
        res = _create(client, ws.id)
        assert res.status_code == 201
        data = res.json()
        assert data["algorithm_version"] == "v3"
        assert data["status"] == "pending"

    def test_flag_off_returns_typed_409_no_silent_downgrade(
        self, client, db_session, make_workspace, monkeypatch
    ):
        """Plan K2: bayrak kapalıyken v3 SESSİZCE v2'ye düşürülmez."""
        monkeypatch.setattr(settings, "ENABLE_ENGINE_V3", False)
        ws = make_workspace("v3 flag off")

        res = _create(client, ws.id, algorithm_version="v3")

        assert res.status_code == 409
        assert res.json()["detail"]["code"] == "ENGINE_V3_DISABLED"
        # Run OLUŞMADI (sessiz v2 düşüşü yok)
        assert (
            db_session.query(ScoringRun)
            .filter(ScoringRun.brand_profile_id == ws.id)
            .count() == 0
        )

    def test_flag_on_creates_v3_run(
        self, client, db_session, make_workspace, monkeypatch
    ):
        monkeypatch.setattr(settings, "ENABLE_ENGINE_V3", True)
        ws = make_workspace("v3 flag on")

        res = _create(client, ws.id, algorithm_version="v3",
                      auto_assign_channels=True)

        assert res.status_code == 201
        data = res.json()
        assert data["algorithm_version"] == "v3"
        db_session.expire_all()
        run = db_session.get(ScoringRun, data["id"])
        assert run.algorithm_version == "v3"

    def test_authority_strategy_does_not_block_v3_social_run(
        self, client, db_session, make_workspace, monkeypatch
    ):
        """V3-only: V3 SOCIAL için eski authority engeli kaldırılmıştır.
        Her iki mod da v3 algoritmik girdisi değildir; eski authority kaydı
        v3 SOCIAL koşusunu engellemez."""
        monkeypatch.setattr(settings, "ENABLE_ENGINE_V3", True)
        ws = make_workspace("v3 authority social acik")
        ws.channel_strategy = dict(APPROVED_AUTHORITY_STRATEGY)
        ws.strategy_version = 1
        db_session.commit()

        res = _create(client, ws.id, algorithm_version="v3",
                      enable_social=True, auto_assign_channels=True)

        assert res.status_code == 201
        data = res.json()
        assert data["algorithm_version"] == "v3"
        assert data["enable_social"] is True

    def test_v3_creates_without_any_channel_strategy(
        self, client, db_session, make_workspace, monkeypatch
    ):
        """V3 hiçbir kanal stratejisi kaydı olmadan (channel_strategy=None) çalışır."""
        monkeypatch.setattr(settings, "ENABLE_ENGINE_V3", True)
        ws = make_workspace("v3 no strategy")
        ws.channel_strategy = None
        db_session.commit()

        res = _create(client, ws.id, algorithm_version="v3",
                      enable_social=True, auto_assign_channels=True)

        assert res.status_code == 201
        data = res.json()
        assert data["algorithm_version"] == "v3"

    def test_authority_strategy_allowed_when_social_disabled(
        self, client, db_session, make_workspace, monkeypatch
    ):
        """K16: enable_social=false ise v3 koşusu serbesttir; strateji
        SESSİZCE hype'a çevrilmez, olduğu gibi kalır."""
        monkeypatch.setattr(settings, "ENABLE_ENGINE_V3", True)
        ws = make_workspace("v3 authority social kapali")
        ws.channel_strategy = dict(APPROVED_AUTHORITY_STRATEGY)
        ws.strategy_version = 1
        db_session.commit()

        res = _create(client, ws.id, algorithm_version="v3",
                      enable_social=False, auto_assign_channels=True)

        assert res.status_code == 201
        run_id = res.json()["id"]
        db_session.expire_all()
        run = db_session.get(ScoringRun, run_id)
        assert run.algorithm_version == "v3"
        assert run.enable_social is False
        assert ws.channel_strategy["social_mode"] == "authority"

    def test_hype_strategy_unaffected_for_v3(
        self, client, db_session, make_workspace, monkeypatch
    ):
        """social_mode=hype (authority DEĞİL) v3 + SOCIAL açık koşuyu
        engellemez — K16 yalnız authority'yi hedefler."""
        monkeypatch.setattr(settings, "ENABLE_ENGINE_V3", True)
        ws = make_workspace("v3 hype strateji")
        ws.channel_strategy = dict(APPROVED_HYPE_STRATEGY)
        ws.strategy_version = 1
        db_session.commit()

        res = _create(client, ws.id, algorithm_version="v3",
                      enable_social=True, auto_assign_channels=True)

        assert res.status_code == 201
        assert res.json()["algorithm_version"] == "v3"


class TestEngineV3SingleRunGate:
    """K14 tek koşu sözleşmesi: v3'te execute bitince motor kendiliğinden
    başlar — auto_assign_channels=false ile oluşturulan bir v3 run'ı motorun
    asla tetiklenmeyeceği ölü bir run olur, sessizce kabul edilmemeli."""

    def test_v3_auto_assign_false_returns_409_single_run_required(
        self, client, db_session, make_workspace, monkeypatch
    ):
        monkeypatch.setattr(settings, "ENABLE_ENGINE_V3", True)
        ws = make_workspace("v3 auto assign false")

        res = _create(client, ws.id, algorithm_version="v3",
                      auto_assign_channels=False)

        assert res.status_code == 409
        assert res.json()["detail"]["code"] == "ENGINE_V3_SINGLE_RUN_REQUIRED"
        # Run OLUŞMADI (ölü run sessizce yazılmadı)
        assert (
            db_session.query(ScoringRun)
            .filter(ScoringRun.brand_profile_id == ws.id)
            .count() == 0
        )

    def test_v3_auto_assign_true_creates_run(
        self, client, db_session, make_workspace, monkeypatch
    ):
        monkeypatch.setattr(settings, "ENABLE_ENGINE_V3", True)
        ws = make_workspace("v3 auto assign true")

        res = _create(client, ws.id, algorithm_version="v3",
                      auto_assign_channels=True)

        assert res.status_code == 201
        data = res.json()
        assert data["algorithm_version"] == "v3"
        db_session.expire_all()
        run = db_session.get(ScoringRun, data["id"])
        assert run.auto_assign_channels is True

    def test_flag_off_wins_over_single_run_gate(
        self, client, db_session, make_workspace, monkeypatch
    ):
        """Kapı SIRASI: bayrak KAPALIYKEN v3 + auto_assign_channels=false
        gönderilirse dönen kod ENGINE_V3_DISABLED olmalı — yeni K14 kuralı
        bayrak kontrolünü GÖLGELEMEMELİ."""
        monkeypatch.setattr(settings, "ENABLE_ENGINE_V3", False)
        ws = make_workspace("v3 flag off single run order")

        res = _create(client, ws.id, algorithm_version="v3",
                      auto_assign_channels=False)

        assert res.status_code == 409
        assert res.json()["detail"]["code"] == "ENGINE_V3_DISABLED"
        assert (
            db_session.query(ScoringRun)
            .filter(ScoringRun.brand_profile_id == ws.id)
            .count() == 0
        )

    def test_v2_auto_assign_false_rejected_legacy_engine_retired(
        self, client, db_session, make_workspace
    ):
        """V3-only: v2 create isteği auto_assign_channels değerinden bağımsız olarak 400 LEGACY_ENGINE_RETIRED ile reddedilir."""
        ws = make_workspace("v2 auto assign false regression")

        res = _create(client, ws.id, algorithm_version="v2", auto_assign_channels=False)

        assert res.status_code == 400
        assert res.json()["detail"]["code"] == "LEGACY_ENGINE_RETIRED"

    def test_v21_auto_assign_false_rejected_legacy_engine_retired(
        self, client, db_session, make_workspace, monkeypatch
    ):
        """V3-only: v2_1 create isteği auto_assign_channels değerinden bağımsız olarak 400 LEGACY_ENGINE_RETIRED ile reddedilir."""
        monkeypatch.setattr(settings, "ENABLE_V21_EXPERIMENT", True)
        ws = make_workspace("v21 auto assign false regression")
        ws.channel_strategy = dict(APPROVED_HYPE_STRATEGY)
        ws.strategy_version = 1
        db_session.commit()

        res = _create(client, ws.id, algorithm_version="v2_1",
                      auto_assign_channels=False)

        assert res.status_code == 400
        assert res.json()["detail"]["code"] == "LEGACY_ENGINE_RETIRED"


class TestEngineV3Execute:
    def test_v3_execute_creates_snapshot_and_reaches_channel_assigning(
        self, client, db_session, make_workspace, make_keyword, monkeypatch
    ):
        """v3 execute akışı: evren snapshot'ını dondurur, ScoreEngine.run_scoring'i
        kesinlikle çağırmaz ve doğrudan channel_assigning durumuna ulaşır."""
        ws, run_id = _create_v3_run(
            client, db_session, make_workspace, monkeypatch,
            "v3 execute live",
            auto_assign_channels=True,
        )
        ws.status = "confirmed"
        ws.channel_strategy = dict(APPROVED_HYPE_STRATEGY)
        ws.strategy_version = 1
        make_keyword("yapay zeka analiz", brand_profile_id=ws.id)
        make_keyword("otonom kodlama", brand_profile_id=ws.id)
        db_session.commit()

        from app.core.scoring.score_engine import ScoreEngine
        from app.tasks.intent_tasks import run_channel_assignment_task

        def _boom(self, scoring_run_id):
            raise AssertionError(
                "v2 ScoreEngine.run_scoring ÇAĞRILDI — v3 run'da eski skorlama motoru "
                "SESSİZCE çağrılmamalıydı"
            )

        monkeypatch.setattr(ScoreEngine, "run_scoring", _boom)
        monkeypatch.setattr(run_channel_assignment_task, "apply_async", lambda *args, **kwargs: None)

        res = client.post(
            f"/api/v1/scoring/runs/{run_id}/execute",
            params={"brand_profile_id": ws.id},
        )

        assert res.status_code == 200, res.text
        data = res.json()
        assert data["status"] == "channel_assigning"
        assert "channel_assignment_task_id" in data

        # DB'de run durumu kontrol edilir
        db_session.expire_all()
        run = db_session.get(ScoringRun, run_id)
        assert run.status == "channel_assigning"

        # Evren snapshot'ı üretilmiştir
        from app.core.engine.context import load_universe
        univ = load_universe(db_session, run_id)
        assert len(univ.rows) >= 2

    def test_v3_execute_fails_closed_when_run_not_pending(
        self, client, db_session, make_workspace, make_keyword, monkeypatch
    ):
        """Yarış / idempotency koruması: pending olmayan run tekrar execute edilirse tipli 409 döner."""
        ws, run_id = _create_v3_run(
            client, db_session, make_workspace, monkeypatch,
            "v3 execute idempotency",
            auto_assign_channels=True,
        )
        ws.status = "confirmed"
        ws.channel_strategy = dict(APPROVED_HYPE_STRATEGY)
        ws.strategy_version = 1
        make_keyword("deneme kelime", brand_profile_id=ws.id)
        db_session.commit()

        from app.tasks.intent_tasks import run_channel_assignment_task
        monkeypatch.setattr(run_channel_assignment_task, "apply_async", lambda *args, **kwargs: None)

        # İlk execute başarılı
        res1 = client.post(
            f"/api/v1/scoring/runs/{run_id}/execute",
            params={"brand_profile_id": ws.id},
        )
        assert res1.status_code == 200

        # İkinci execute: artık pending değil -> tipli 409
        res2 = client.post(
            f"/api/v1/scoring/runs/{run_id}/execute",
            params={"brand_profile_id": ws.id},
        )
        assert res2.status_code == 409
        assert res2.json()["detail"]["code"] == "RUN_NOT_PENDING"


class TestEngineV3AssignGates:
    def test_assign_pending_run_returns_typed_409(
        self, client, db_session, make_workspace, monkeypatch
    ):
        """Henüz execute edilmemiş (pending) v3 run'a doğrudan /assign çağrısı 500 DEĞİL, tipli 409 döner."""
        ws, run_id = _create_v3_run(
            client, db_session, make_workspace, monkeypatch,
            "v3 assign pending",
        )

        res = client.post(
            f"/api/v1/channels/runs/{run_id}/assign",
            params={"brand_profile_id": ws.id},
            json={},
        )

        assert res.status_code == 409
        assert res.json()["detail"]["code"] == "RUN_STATUS_NOT_ASSIGNABLE"

    @pytest.mark.parametrize("mode", ["shadow", "assistive"])
    def test_assign_screening_mode_not_supported(
        self, client, db_session, make_workspace, monkeypatch, mode
    ):
        """K13: v3 run'da screening_mode zorunlu 'off'; shadow/assistive
        istenirse tipli 409 ENGINE_V3_SCREENING_NOT_SUPPORTED."""
        ws, run_id = _create_v3_run(
            client, db_session, make_workspace, monkeypatch,
            f"v3 assign screening {mode}")

        from app.tasks.intent_tasks import run_channel_assignment_task

        def _boom(*args, **kwargs):
            raise AssertionError(
                "Celery run_channel_assignment_task ENQUEUE edildi — v3 "
                "screening reddinde hiçbir şey kuyruğa girmemeliydi")

        monkeypatch.setattr(run_channel_assignment_task, "apply_async", _boom)

        res = client.post(
            f"/api/v1/channels/runs/{run_id}/assign",
            params={"brand_profile_id": ws.id},
            json={"screening_mode": mode},
        )

        assert res.status_code == 409
        assert (res.json()["detail"]["code"]
                == "ENGINE_V3_SCREENING_NOT_SUPPORTED")


class TestEngineV3WorkerBudgetGate:
    def test_v3_worker_fails_without_attempt_or_caps(
        self, db_session, make_workspace, make_scoring_run, monkeypatch
    ):
        """v3 worker: ChannelAssignmentAttempt veya onaylı cap'ler yoksa fail-closed durur, orkestratör çağrılmaz."""
        from unittest.mock import MagicMock
        from app.tasks.intent_tasks import run_channel_assignment_task
        import app.tasks.intent_tasks as intent_tasks

        ws = make_workspace("v3 worker no attempt")
        ws.status = "confirmed"
        ws.channel_strategy = dict(APPROVED_HYPE_STRATEGY)
        ws.strategy_version = 1
        db_session.commit()

        run = make_scoring_run(brand_profile_id=ws.id)
        run.algorithm_version = "v3"
        run.status = "scored"
        db_session.commit()

        mock_orch = MagicMock()
        monkeypatch.setattr(intent_tasks, "run_v3_orchestration", mock_orch)
        monkeypatch.setattr(run_channel_assignment_task, "update_state", lambda *args, **kwargs: None)

        result = run_channel_assignment_task.apply(args=[run.id], task_id="orphan-task-no-attempt").result

        assert result["status"] == "failed"
        assert mock_orch.call_count == 0, "Attempt/bütçe yokken orkestratör ÇAĞRILMAMALIDIR"

    def test_v3_orchestrator_fails_closed_without_snapshot(
        self, db_session, make_workspace, make_scoring_run
    ):
        """Orkestratör: Donmuş evren snapshot'ı yoksa fail-closed EngineInputError verir."""
        from unittest.mock import MagicMock
        from app.core.engine.orchestrator import run_v3_orchestration
        from app.core.engine.context import EngineInputError

        ws = make_workspace("v3 no snapshot")
        ws.status = "confirmed"
        ws.channel_strategy = dict(APPROVED_HYPE_STRATEGY)
        ws.strategy_version = 1
        ws.profile_data = {"company_name": "Test Co", "brand_terms": ["test"]}
        db_session.commit()

        run = make_scoring_run(brand_profile_id=ws.id)
        run.algorithm_version = "v3"
        run.status = "scored"
        run.enable_ads = True
        run.enable_seo = False
        run.enable_social = False
        db_session.commit()

        # freeze_universe_snapshot ÇAĞRILMADI — snapshot yok
        with pytest.raises(EngineInputError, match="evren"):
            run_v3_orchestration(db_session, run=run, ai=MagicMock())


class TestV2AndV21Unaffected:
    """Regresyon: v2 / v2_1 create emekliye ayrıldı (LEGACY_ENGINE_RETIRED); historical execute korunur."""

    def test_v2_explicit_create_returns_legacy_engine_retired(self, client, make_workspace):
        ws = make_workspace("v2 regression create")
        res = _create(client, ws.id, algorithm_version="v2")
        assert res.status_code == 400
        assert res.json()["detail"]["code"] == "LEGACY_ENGINE_RETIRED"

    def test_v2_execute_is_refused_read_only(
        self, client, make_workspace, make_scoring_run, monkeypatch
    ):
        """Ürün kararı (V3 tek motor): tarihsel v2 run'ı salt-okunurdur —
        execute tipli 409 LEGACY_RUN_READ_ONLY döner, eski v2 motoru ÇAĞRILMAZ."""
        ws = make_workspace("v2 execute regression")
        run = make_scoring_run(
            brand_profile_id=ws.id,
            algorithm_version="v2",
            status="pending",
        )

        from app.core.scoring.score_engine import ScoreEngine

        called = {"hit": False}

        def _stub(self, scoring_run_id):
            called["hit"] = True
            return {"status": "success", "should_compute_relevance": False}

        monkeypatch.setattr(ScoreEngine, "run_scoring", _stub)

        res = client.post(
            f"/api/v1/scoring/runs/{run.id}/execute",
            params={"brand_profile_id": ws.id},
        )

        assert res.status_code == 409
        assert res.json()["detail"]["code"] == "LEGACY_RUN_READ_ONLY"
        assert called["hit"] is False

    def test_v21_flag_off_returns_legacy_engine_retired(
        self, client, make_workspace, monkeypatch
    ):
        monkeypatch.setattr(settings, "ENABLE_V21_EXPERIMENT", False)
        ws = make_workspace("v21 regression flag off")
        res = _create(client, ws.id, algorithm_version="v2_1")
        assert res.status_code == 400
        assert res.json()["detail"]["code"] == "LEGACY_ENGINE_RETIRED"

    def test_v21_flag_on_returns_legacy_engine_retired(
        self, client, db_session, make_workspace, monkeypatch
    ):
        monkeypatch.setattr(settings, "ENABLE_V21_EXPERIMENT", True)
        ws = make_workspace("v21 regression flag on")
        ws.channel_strategy = dict(APPROVED_HYPE_STRATEGY)
        ws.strategy_version = 1
        db_session.commit()

        res = _create(client, ws.id, algorithm_version="v2_1")
        assert res.status_code == 400
        assert res.json()["detail"]["code"] == "LEGACY_ENGINE_RETIRED"


class TestAlgorithmDependentAutoAssignDefault:
    """Sözleşme doğrulaması: auto_assign_channels varsayılanı ve V3-only create sözleşmesi."""

    def test_payload_without_algorithm_and_auto_assign_defaults_to_v3_true(
        self, client, db_session, make_workspace
    ):
        """Payload algorithm_version ve auto_assign olmadan → v3 + true"""
        ws = make_workspace("raw default payload")
        body = {
            "brand_profile_id": ws.id,
            "run_name": "no algo no auto",
            "ads_capacity": 10,
            "seo_capacity": 10,
            "social_capacity": 10,
        }
        res = client.post("/api/v1/scoring/runs", json=body)
        assert res.status_code == 201, res.text
        data = res.json()
        assert data["algorithm_version"] == "v3"
        db_session.expire_all()
        run = db_session.get(ScoringRun, data["id"])
        assert run.algorithm_version == "v3"
        assert run.auto_assign_channels is True

    def test_payload_v3_without_auto_assign_defaults_to_v3_true(
        self, client, db_session, make_workspace
    ):
        """Payload algorithm_version=v3, auto_assign yok → v3 + true"""
        ws = make_workspace("v3 no auto payload")
        body = {
            "brand_profile_id": ws.id,
            "run_name": "v3 no auto",
            "ads_capacity": 10,
            "seo_capacity": 10,
            "social_capacity": 10,
            "algorithm_version": "v3",
        }
        res = client.post("/api/v1/scoring/runs", json=body)
        assert res.status_code == 201, res.text
        data = res.json()
        assert data["algorithm_version"] == "v3"
        db_session.expire_all()
        run = db_session.get(ScoringRun, data["id"])
        assert run.algorithm_version == "v3"
        assert run.auto_assign_channels is True

    def test_payload_v2_rejected_legacy_engine_retired(
        self, client, db_session, make_workspace
    ):
        """Payload algorithm_version=v2 → 400 LEGACY_ENGINE_RETIRED"""
        ws = make_workspace("v2 no auto payload")
        body = {
            "brand_profile_id": ws.id,
            "run_name": "v2 no auto",
            "ads_capacity": 10,
            "seo_capacity": 10,
            "social_capacity": 10,
            "algorithm_version": "v2",
        }
        res = client.post("/api/v1/scoring/runs", json=body)
        assert res.status_code == 400
        assert res.json()["detail"]["code"] == "LEGACY_ENGINE_RETIRED"

    def test_payload_v21_rejected_legacy_engine_retired(
        self, client, db_session, make_workspace
    ):
        """Payload algorithm_version=v2_1 → 400 LEGACY_ENGINE_RETIRED"""
        ws = make_workspace("v21 no auto payload")
        body = {
            "brand_profile_id": ws.id,
            "run_name": "v21 no auto",
            "ads_capacity": 10,
            "seo_capacity": 10,
            "social_capacity": 10,
            "algorithm_version": "v2_1",
        }
        res = client.post("/api/v1/scoring/runs", json=body)
        assert res.status_code == 400
        assert res.json()["detail"]["code"] == "LEGACY_ENGINE_RETIRED"

    def test_payload_v3_explicit_auto_assign_false_returns_409(
        self, client, db_session, make_workspace
    ):
        """Payload v3 + auto_assign=false → create kapısı 409"""
        ws = make_workspace("v3 explicit auto false")
        body = {
            "brand_profile_id": ws.id,
            "run_name": "v3 auto false",
            "ads_capacity": 10,
            "seo_capacity": 10,
            "social_capacity": 10,
            "algorithm_version": "v3",
            "auto_assign_channels": False,
        }
        res = client.post("/api/v1/scoring/runs", json=body)
        assert res.status_code == 409
        assert res.json()["detail"]["code"] == "ENGINE_V3_SINGLE_RUN_REQUIRED"

    def test_explicit_v2_execute_is_refused_read_only(
        self, client, make_workspace, make_scoring_run, monkeypatch
    ):
        """Ürün kararı (V3 tek motor): tarihsel v2 run'ı salt-okunurdur —
        execute tipli 409 LEGACY_RUN_READ_ONLY döner, eski v2 motoru ÇAĞRILMAZ."""
        ws = make_workspace("v2 raw execute regression")
        run = make_scoring_run(
            brand_profile_id=ws.id,
            algorithm_version="v2",
            status="pending",
        )

        from app.core.scoring.score_engine import ScoreEngine

        called = {"hit": False}

        def _stub(self, scoring_run_id):
            called["hit"] = True
            return {"status": "success", "should_compute_relevance": False}

        monkeypatch.setattr(ScoreEngine, "run_scoring", _stub)

        res = client.post(
            f"/api/v1/scoring/runs/{run.id}/execute",
            params={"brand_profile_id": ws.id},
        )
        assert res.status_code == 409
        assert res.json()["detail"]["code"] == "LEGACY_RUN_READ_ONLY"
        assert called["hit"] is False

    def test_score_engine_create_scoring_run_defaults_to_v3(
        self, db_session, make_workspace
    ):
        """Doğrudan ScoreEngine.create_scoring_run() varsayılanı 'v3'tür."""
        from app.core.scoring.score_engine import ScoreEngine

        ws = make_workspace("score engine default test")
        engine = ScoreEngine(db_session)
        run = engine.create_scoring_run(
            ads_capacity=10,
            seo_capacity=10,
            social_capacity=10,
            brand_profile_id=ws.id,
        )
        assert run.algorithm_version == "v3"

    def test_db_server_default_algorithm_version_is_v3(
        self, db_session, make_workspace
    ):
        """ScoringRun modeli veya raw insert üzerinden algorithm_version belirtilmezse 'v3' üretilir."""
        ws = make_workspace("db default algo version")
        run = ScoringRun(
            run_name="test_default_algo",
            brand_profile_id=ws.id,
            ads_capacity=10,
            seo_capacity=10,
            social_capacity=10,
            status="pending",
        )
        db_session.add(run)
        db_session.commit()
        db_session.refresh(run)
        assert run.algorithm_version == "v3"

    def test_workspace_response_does_not_contain_strategy_fields(
        self, client, make_workspace
    ):
        """WorkspaceResponse şeması ve GET /workspaces/{id} yanıtı channel_strategy ve strategy_version içermez."""
        ws = make_workspace("ws response strategy check")
        res = client.get(f"/api/v1/brand-profile/workspaces/{ws.id}")
        assert res.status_code == 200
        data = res.json()
        assert "channel_strategy" not in data
        assert "strategy_version" not in data
