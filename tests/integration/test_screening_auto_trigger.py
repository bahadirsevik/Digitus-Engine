# -*- coding: utf-8 -*-
"""Sunucu kontrollü otomatik tetikleme (revize plan §3).

Kullanıcı YALNIZ kanal kapasitesini seçer; mod/sağlayıcı/SHA/cap arayüze
GİTMEZ. Kilitlenen davranışlar (Codex 27. tur onay şartları):

- Varsayılanlar KAPALI: kill switch kapalı, allowlist boş, mod `off`.
- Mükerrer scoring teslimi ikinci attempt/job açmaz.
- Broker enqueue hatası fail-closed kapanır.
- Canlı attempt tipli 409 verir (sessiz ikinci dispatch YOK).
- Screening reuse ücretsizdir (yeni job/çocuk yok).
- Eligibility kapıları kullanıcıya HATA dönmez; sebep denetime yazılır.
- Assistive'de sıra: ÖNCE tarama, SONRA parent atama.
"""
from decimal import Decimal

import pytest

from app.core.channel.assignment_dispatcher import (
    ChannelAssignmentPreconditionError,
    enqueue_channel_assignment,
)
from app.core.screening.auto_trigger import decide_screening_mode
from app.database.models import (
    ChannelAssignmentAttempt,
    ChannelCandidate,
    CorpusCandidateSelection,
    CorpusScreeningDecision,
    CorpusScreeningJob,
    Keyword,
    KeywordScore,
    ScoringRun,
    TaskResult,
)


# Ürün kararı (V3 tek motor): mevcut v2/v2_1 run'ları salt-okunurdur. Bu
# testler eski motor run'ı üzerinden YENİ kanal ataması / screening
# dispatch'inin çalıştığını iddia ediyordu; o yol artık API + dispatcher +
# task sınırında LEGACY_RUN_READ_ONLY ile kapalı (v3'te screening zaten
# zorunlu 'off'). Red davranışı tests/integration/test_legacy_run_read_only.py
# içinde kilitlidir. Testler silinmedi; eski sözleşmenin kaydı olarak kalır.
LEGACY_DISPATCH_RETIRED = pytest.mark.skip(
    reason="LEGACY_RUN_READ_ONLY: v2/v2_1 run'larında yeni dispatch "
           "emekliye ayrıldı (V3 tek motor)")

STRATEGY = {
    "status": "approved",
    "product_definition": "Borsa analiz platformu",
    "content_strategy": "Yatırımcı içerikleri",
    "social_mode": "hype",
    "schema_version": 1,
    "approved_fingerprint": "fp-auto",
}


@pytest.fixture
def no_broker(monkeypatch):
    """Celery'ye GİDİLMEZ: teslimler listelenir."""
    sent = {"assignment": [], "assignment_calls": [], "screening": []}

    def _assignment(*args, **kwargs):
        sent["assignment"].append(kwargs.get("task_id"))
        sent["assignment_calls"].append(dict(kwargs))
        return type("R", (), {"id": kwargs.get("task_id")})()

    def _screening(*args, **kwargs):
        sent["screening"].append(kwargs)
        return type("R", (), {"id": kwargs.get("task_id")})()

    monkeypatch.setattr(
        "app.tasks.intent_tasks.run_channel_assignment_task.apply_async",
        _assignment)
    monkeypatch.setattr(
        "app.tasks.screening_tasks.run_corpus_screening_task.apply_async",
        _screening)
    return sent


@pytest.fixture
def scenario(db_session, make_workspace):
    ws = make_workspace("Auto WS", channel_strategy=dict(STRATEGY),
                        strategy_version=1,
                        profile_data={"target_audience": "Yatırımcılar"})
    run = ScoringRun(run_name="auto", brand_profile_id=ws.id,
                     total_keywords=12, ads_capacity=3, seo_capacity=3,
                     social_capacity=3, status="scored",
                     skip_relevance=True,
                     default_relevance_coefficient=1.0,
                     algorithm_version="v2_1")
    db_session.add(run)
    db_session.commit()
    for i in range(12):
        kw = Keyword(keyword=f"oto kelime {i:02d}", monthly_volume=500 - i)
        db_session.add(kw)
        db_session.flush()
        db_session.add(KeywordScore(
            scoring_run_id=run.id, keyword_id=kw.id, ads_score=50 - i,
            seo_score=50 - i, social_score=50 - i, ads_rank=i + 1,
            seo_rank=i + 1, social_rank=i + 1))
    db_session.commit()
    return {"ws": ws, "run": run}


@pytest.fixture
def allowlisted(monkeypatch, scenario):
    from app.config import settings

    monkeypatch.setattr(settings, "ENABLE_CORPUS_SCREENING", True)
    monkeypatch.setattr(settings, "CORPUS_SCREENING_ALLOWLIST",
                        [scenario["ws"].id])
    monkeypatch.setattr(settings, "CORPUS_SCREENING_DEFAULT_MODE", "shadow")
    # Tavan açıkça sabitlenir: test ortamı yerel .env okumaz (config APP_ENV=test);
    # varsayılan $0.50 senaryonun preflight tahminine yetmez. Tavan-aşımı testi
    # kendi değerini ayrıca düşürür.
    monkeypatch.setattr(settings, "CORPUS_SCREENING_MAX_APPROVED_USD", 100.0)
    return settings


STAGES = ("intent", "ads_prefilter", "seo_prefilter", "social_prefilter",
          "seo_metadata")


@pytest.fixture
def telemetry(db_session, scenario):
    """Downstream/screening fiyat tahmini için asgari telemetri."""
    from app.config import settings
    from app.core.screening.candidate_union import (
        PRODUCTION_SCREENING_CONTRACT as _CONTRACT,
    )
    from app.database.models import AiUsageEvent

    for i in range(5):
        db_session.add(AiUsageEvent(
            scoring_run_id=scenario["run"].id,
            brand_profile_id=scenario["ws"].id,
            request_id=f"scr-{i}", attempt=1, stage="corpus_screening",
            model=_CONTRACT["model"], prompt_tokens=1300 + i,
            candidates_tokens=800 + i, thoughts_tokens=0,
            total_tokens=2100 + i,
            price_snapshot={"input_per_m": 0.28, "output_per_m": 0.42}))
    for stage in STAGES + ("brand_filter",):
        for i in range(5):
            db_session.add(AiUsageEvent(
                scoring_run_id=scenario["run"].id,
                brand_profile_id=scenario["ws"].id,
                request_id=f"{stage}-{i}", attempt=1, stage=stage,
                model=settings.GEMINI_MODEL, prompt_tokens=1200 + i,
                candidates_tokens=400 + i, thoughts_tokens=50,
                total_tokens=1650 + i,
                price_snapshot={"input_per_m": 0.3, "output_per_m": 2.5}))
    db_session.commit()


def _complete_job(db_session, payload):
    """Taramayi terminal yap (parent ancak o zaman salinabilir)."""
    job = db_session.get(CorpusScreeningJob, payload["screening_job_id"])
    job.status = "completed"
    db_session.commit()


class TestDefaultsAreClosed:
    """Varsayılanlar KAPALI: hiçbir şey kendiliğinden açılmaz."""

    def test_kill_switch_off_is_the_default(
            self, db_session, scenario, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "ENABLE_CORPUS_SCREENING", False)
        mode, reason = decide_screening_mode(scenario["run"],
                                             scenario["ws"],
                                             settings=settings)
        assert (mode, reason) == ("off", "KILL_SWITCH_OFF")

    def test_empty_allowlist_blocks_everyone(self, db_session, scenario,
                                             monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "ENABLE_CORPUS_SCREENING", True)
        monkeypatch.setattr(settings, "CORPUS_SCREENING_ALLOWLIST", [])
        mode, reason = decide_screening_mode(scenario["run"], scenario["ws"],
                                             settings=settings)
        assert (mode, reason) == ("off", "WORKSPACE_NOT_ALLOWLISTED")

    def test_default_mode_off_blocks_allowlisted_workspace(
            self, db_session, scenario, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "ENABLE_CORPUS_SCREENING", True)
        monkeypatch.setattr(settings, "CORPUS_SCREENING_ALLOWLIST",
                            [scenario["ws"].id])
        monkeypatch.setattr(settings, "CORPUS_SCREENING_DEFAULT_MODE", "off")
        mode, reason = decide_screening_mode(scenario["run"], scenario["ws"],
                                             settings=settings)
        assert (mode, reason) == ("off", "DEFAULT_MODE_OFF")

    def test_assistive_needs_allowlist(self, db_session, scenario,
                                       monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "ENABLE_CORPUS_SCREENING", True)
        monkeypatch.setattr(settings, "CORPUS_SCREENING_ALLOWLIST", [])
        monkeypatch.setattr(settings, "CORPUS_SCREENING_DEFAULT_MODE",
                            "assistive")
        mode, reason = decide_screening_mode(scenario["run"], scenario["ws"],
                                             settings=settings)
        assert mode == "off" and reason == "WORKSPACE_NOT_ALLOWLISTED"


class TestEligibilityGates:
    """Kapılar kullanıcıya HATA dönmez; sebep denetime yazılır."""

    def test_missing_strategy_falls_back(self, db_session, scenario,
                                         allowlisted):
        scenario["ws"].channel_strategy = None
        db_session.commit()
        mode, reason = decide_screening_mode(scenario["run"], scenario["ws"],
                                             settings=allowlisted)
        assert (mode, reason) == ("off", "STRATEGY_NOT_APPROVED")

    def test_v2_1_run_uses_same_candidate_screening_contract(
            self, db_session, scenario, allowlisted):
        scenario["run"].algorithm_version = "v2_1"
        db_session.commit()
        mode, reason = decide_screening_mode(scenario["run"], scenario["ws"],
                                             settings=allowlisted)
        assert (mode, reason) == ("shadow", None)

    def test_unknown_algorithm_is_not_screened(
            self, db_session, scenario, allowlisted):
        scenario["run"].algorithm_version = "v3"
        mode, reason = decide_screening_mode(scenario["run"], scenario["ws"],
                                             settings=allowlisted)
        assert (mode, reason) == ("off", "ALGORITHM_NOT_SUPPORTED")

    def test_fourth_candidate_canary_run_falls_back_to_baseline(
            self, db_session, scenario, allowlisted, monkeypatch):
        from app.core.screening.auto_trigger import (
            CANDIDATE_CANARY_CONTRACT_VERSION,
        )

        monkeypatch.setattr(
            allowlisted, "CORPUS_SCREENING_DEFAULT_MODE", "assistive")
        for ordinal in range(1, 4):
            db_session.add(ChannelAssignmentAttempt(
                parent_task_id=f"canary-{ordinal}",
                scoring_run_id=scenario["run"].id,
                brand_profile_id=scenario["ws"].id,
                status="completed",
                phase="completed",
                screening_mode="assistive",
                applied_candidate_multiplier=3,
                counterfactual_target_multiplier=3,
                manifest={
                    "candidate_canary_contract_version":
                        CANDIDATE_CANARY_CONTRACT_VERSION,
                    "candidate_canary_workspace_run_ordinal": ordinal,
                },
            ))
        db_session.commit()

        mode, reason = decide_screening_mode(
            scenario["run"], scenario["ws"],
            settings=allowlisted, db=db_session)
        assert (mode, reason) == ("off", "CANARY_RUN_LIMIT_REACHED")

    @LEGACY_DISPATCH_RETIRED
    def test_v21_assistive_attempt_is_stamped_with_canary_contract(
            self, db_session, scenario, allowlisted, telemetry, no_broker,
            monkeypatch):
        from app.core.screening.auto_trigger import (
            CANDIDATE_CANARY_CONTRACT_VERSION,
        )

        scenario["run"].algorithm_version = "v2_1"
        monkeypatch.setattr(
            allowlisted, "CORPUS_SCREENING_DEFAULT_MODE", "assistive")
        monkeypatch.setattr(
            allowlisted, "CORPUS_DOWNSTREAM_MAX_APPROVED_USD", 100.0)
        db_session.commit()

        result = enqueue_channel_assignment(
            db_session, scenario["run"], from_status="scored")
        attempt = db_session.query(ChannelAssignmentAttempt).one()

        assert result["screening_mode"] == "assistive"
        assert attempt.manifest["candidate_canary_contract_version"] == \
            CANDIDATE_CANARY_CONTRACT_VERSION
        assert attempt.manifest["candidate_canary_workspace_run_ordinal"] == 1

    @LEGACY_DISPATCH_RETIRED
    def test_gate_reason_lands_in_attempt_and_task(
            self, db_session, scenario, no_broker, monkeypatch):
        """Kullanıcı normal atamasını görür; sebep denetimde durur."""
        from app.config import settings

        monkeypatch.setattr(settings, "ENABLE_CORPUS_SCREENING", False)
        result = enqueue_channel_assignment(db_session, scenario["run"],
                                            from_status="scored")
        assert result["screening_mode"] == "off"
        assert result["screening_skipped_reason"] == "KILL_SWITCH_OFF"
        assert result["auto_dispatch"] is True
        assert len(no_broker["assignment"]) == 1      # atama SÜRDÜ
        attempt = db_session.query(ChannelAssignmentAttempt).one()
        assert attempt.manifest["screening_skipped_reason"] == \
            "KILL_SWITCH_OFF"
        task = db_session.query(TaskResult).filter_by(
            task_id=result["task_id"]).one()
        assert task.result_data["screening_skipped_reason"] == \
            "KILL_SWITCH_OFF"

    @LEGACY_DISPATCH_RETIRED
    def test_cap_over_admin_limit_falls_back_silently(
            self, db_session, scenario, allowlisted, telemetry, no_broker,
            monkeypatch):
        monkeypatch.setattr(allowlisted,
                            "CORPUS_SCREENING_MAX_APPROVED_USD", 0.000001)
        result = enqueue_channel_assignment(db_session, scenario["run"],
                                            from_status="scored")
        assert result["screening_mode"] == "off"
        assert result["screening_skipped_reason"] == \
            "SCREENING_CAP_EXCEEDS_LIMIT"
        assert db_session.query(CorpusScreeningJob).count() == 0
        assert len(no_broker["assignment"]) == 1
        assert no_broker["screening"] == []          # sağlayıcıya GİDİLMEZ


class TestDuplicateDelivery:
    """Mükerrer scoring teslimi ikinci koşu açmaz."""

    @LEGACY_DISPATCH_RETIRED
    def test_second_dispatch_returns_active_task(self, db_session, scenario,
                                                 allowlisted, telemetry,
                                                 no_broker):
        first = enqueue_channel_assignment(db_session, scenario["run"],
                                           from_status="scored")
        second = enqueue_channel_assignment(db_session, scenario["run"],
                                            from_status="scored")
        assert second["task_id"] == first["task_id"]
        assert second["already_active"] is True
        assert db_session.query(ChannelAssignmentAttempt).count() == 1
        assert db_session.query(CorpusScreeningJob).count() == 1
        assert len(no_broker["assignment"]) == 1
        assert len(no_broker["screening"]) == 1

    @LEGACY_DISPATCH_RETIRED
    def test_live_attempt_without_task_is_typed_409(self, db_session,
                                                    scenario, allowlisted,
                                                    telemetry, no_broker):
        """Attempt CANLI ama task terminal: sessiz ikinci dispatch YOK."""
        from datetime import datetime, timezone

        first = enqueue_channel_assignment(db_session, scenario["run"],
                                           from_status="scored")
        # Parent task terminal görünüyor (aktif-task guard'ı geçilir) ama
        # attempt hâlâ canlı lease taşıyor -> yarış koruması devrede
        task = db_session.query(TaskResult).filter_by(
            task_id=first["task_id"]).one()
        task.status = "completed"
        attempt = db_session.query(ChannelAssignmentAttempt).one()
        attempt.status = "running"
        attempt.assignment_dispatch_state = "started"
        attempt.dispatch_last_at = datetime.now(timezone.utc)
        scenario["run"].status = "scored"
        db_session.commit()
        with pytest.raises(ChannelAssignmentPreconditionError) as exc:
            enqueue_channel_assignment(db_session, scenario["run"],
                                       from_status="scored")
        assert exc.value.code == "ATTEMPT_ALREADY_ACTIVE"


class TestBrokerFailure:
    """Enqueue hatası fail-closed kapanır."""

    @LEGACY_DISPATCH_RETIRED
    def test_assignment_enqueue_failure_closes_attempt(
            self, db_session, scenario, monkeypatch):
        def _boom(*a, **kw):
            raise RuntimeError("broker yok")

        monkeypatch.setattr(
            "app.tasks.intent_tasks.run_channel_assignment_task.apply_async",
            _boom)
        with pytest.raises(RuntimeError):
            enqueue_channel_assignment(db_session, scenario["run"],
                                       from_status="scored")
        db_session.expire_all()
        attempt = db_session.query(ChannelAssignmentAttempt).one()
        assert attempt.status == "failed"
        assert attempt.error_code == "ENQUEUE_FAILED"
        run = db_session.get(ScoringRun, scenario["run"].id)
        assert run.status == "scored"          # durum geri alındı

    @LEGACY_DISPATCH_RETIRED
    def test_screening_enqueue_failure_keeps_assignment(
            self, db_session, scenario, allowlisted, telemetry, monkeypatch,
            no_broker):
        def _boom(*a, **kw):
            raise RuntimeError("tarama kuyruğu yok")

        monkeypatch.setattr(
            "app.tasks.screening_tasks.run_corpus_screening_task.apply_async",
            _boom)
        result = enqueue_channel_assignment(db_session, scenario["run"],
                                            from_status="scored")
        assert len(no_broker["assignment"]) == 1     # atama SÜRDÜ
        job = db_session.query(CorpusScreeningJob).one()
        assert job.status == "failed"
        assert job.error_code == "ENQUEUE_FAILED"
        assert result["screening_task_id"] is None


class TestScreeningReuse:
    """Aynı kimlikte tamamlanmış tarama ÜCRETSİZ kullanılır."""

    @LEGACY_DISPATCH_RETIRED
    def test_reuse_opens_no_new_job_or_child(self, db_session, scenario,
                                             allowlisted, telemetry,
                                             no_broker):
        first = enqueue_channel_assignment(db_session, scenario["run"],
                                           from_status="scored")
        job = db_session.query(CorpusScreeningJob).one()
        job.status = "completed"
        attempt = db_session.query(ChannelAssignmentAttempt).one()
        attempt.status = "completed"
        attempt.assignment_dispatch_state = "finished"
        db_session.query(TaskResult).filter_by(
            task_id=first["task_id"]).one().status = "completed"
        scenario["run"].status = "scored"
        db_session.commit()

        second = enqueue_channel_assignment(db_session, scenario["run"],
                                            from_status="scored")
        assert second["task_id"] != first["task_id"]
        assert second["screening_reused"] is True
        assert second["screening_job_id"] == job.id
        assert db_session.query(CorpusScreeningJob).count() == 1
        assert len(no_broker["screening"]) == 1      # ikinci çocuk YOK


class TestAssistiveOrdering:
    """Assistive: ÖNCE tarama, SONRA parent atama."""

    @pytest.fixture
    def assistive_settings(self, allowlisted, monkeypatch):
        monkeypatch.setattr(allowlisted, "CORPUS_SCREENING_DEFAULT_MODE",
                            "assistive")
        monkeypatch.setattr(allowlisted,
                            "CORPUS_DOWNSTREAM_MAX_APPROVED_USD", 100.0)
        return allowlisted

    @LEGACY_DISPATCH_RETIRED
    def test_parent_is_deferred_until_screening_finishes(
            self, db_session, scenario, assistive_settings, telemetry,
            no_broker):
        result = enqueue_channel_assignment(db_session, scenario["run"],
                                            from_status="scored")
        assert result["screening_mode"] == "assistive"
        assert result["parent_deferred"] is True
        # Tarama kuyrukta, parent HENÜZ DEĞİL
        assert len(no_broker["screening"]) == 1
        assert no_broker["assignment"] == []
        payload = no_broker["screening"][0]["kwargs"]["deferred_parent"]
        assert payload["parent_task_id"] == result["task_id"]
        assert payload["attempt_id"] and payload["screening_job_id"]

        from app.tasks.screening_tasks import _release_deferred_parent

        # Tarama BİTMEDEN parent salınmaz (fail-closed)
        assert _release_deferred_parent(payload) is False
        assert no_broker["assignment"] == []

        # Tarama biter → parent salınır
        _complete_job(db_session, payload)
        assert _release_deferred_parent(payload) is True
        assert no_broker["assignment"] == [result["task_id"]]

    @LEGACY_DISPATCH_RETIRED
    def test_second_delivery_does_not_dispatch_parent_twice(
            self, db_session, scenario, assistive_settings, telemetry,
            no_broker):
        result = enqueue_channel_assignment(db_session, scenario["run"],
                                            from_status="scored")
        payload = no_broker["screening"][0]["kwargs"]["deferred_parent"]
        _complete_job(db_session, payload)
        from app.tasks.screening_tasks import _release_deferred_parent

        assert _release_deferred_parent(payload) is True
        assert _release_deferred_parent(payload) is False   # CAS
        assert no_broker["assignment"] == [result["task_id"]]

    @LEGACY_DISPATCH_RETIRED
    def test_deferred_parent_broker_failure_closes_run(
            self, db_session, scenario, assistive_settings, telemetry,
            no_broker, monkeypatch):
        result = enqueue_channel_assignment(db_session, scenario["run"],
                                            from_status="scored")
        payload = no_broker["screening"][0]["kwargs"]["deferred_parent"]
        _complete_job(db_session, payload)

        def _boom(*a, **kw):
            raise RuntimeError("broker düştü")

        monkeypatch.setattr(
            "app.tasks.intent_tasks.run_channel_assignment_task.apply_async",
            _boom)
        from app.tasks.screening_tasks import _release_deferred_parent

        assert _release_deferred_parent(payload) is False
        db_session.expire_all()
        task = db_session.query(TaskResult).filter_by(
            task_id=result["task_id"]).one()
        assert task.status == "failed"
        attempt = db_session.query(ChannelAssignmentAttempt).one()
        assert attempt.status == "failed"
        run = db_session.get(ScoringRun, scenario["run"].id)
        assert run.status == "scored"

    @LEGACY_DISPATCH_RETIRED
    def test_screening_enqueue_failure_releases_parent_immediately(
            self, db_session, scenario, assistive_settings, telemetry,
            no_broker, monkeypatch):
        """Çocuk kuyruğa giremezse parent SONSUZA KADAR beklemez."""
        def _boom(*a, **kw):
            raise RuntimeError("tarama kuyruğu yok")

        monkeypatch.setattr(
            "app.tasks.screening_tasks.run_corpus_screening_task.apply_async",
            _boom)
        result = enqueue_channel_assignment(db_session, scenario["run"],
                                            from_status="scored")
        assert result["parent_deferred"] is False
        assert no_broker["assignment"] == [result["task_id"]]
        assert result["screening_skipped_reason"] == \
            "SCREENING_ENQUEUE_FAILED"


class TestDeferredReleaseIsFailClosed:
    """Codex 27. tur #2: bozuk/yabancı payload parent BAŞLATAMAZ."""

    @pytest.fixture
    def deferred(self, db_session, scenario, monkeypatch, allowlisted,
                 telemetry, no_broker):
        monkeypatch.setattr(allowlisted, "CORPUS_SCREENING_DEFAULT_MODE",
                            "assistive")
        monkeypatch.setattr(allowlisted,
                            "CORPUS_DOWNSTREAM_MAX_APPROVED_USD", 100.0)
        result = enqueue_channel_assignment(db_session, scenario["run"],
                                            from_status="scored")
        payload = no_broker["screening"][0]["kwargs"]["deferred_parent"]
        _complete_job(db_session, payload)
        return {"payload": payload, "result": result, "sent": no_broker}

    def _release(self, payload):
        from app.tasks.screening_tasks import _release_deferred_parent

        return _release_deferred_parent(payload)

    @LEGACY_DISPATCH_RETIRED
    def test_missing_attempt_releases_nothing(self, deferred):
        payload = {**deferred["payload"], "attempt_id": 999999}
        assert self._release(payload) is False
        assert deferred["sent"]["assignment"] == []

    @LEGACY_DISPATCH_RETIRED
    def test_foreign_parent_task_id_is_refused(self, deferred):
        payload = {**deferred["payload"], "parent_task_id": "baska-task"}
        assert self._release(payload) is False
        assert deferred["sent"]["assignment"] == []

    @LEGACY_DISPATCH_RETIRED
    def test_wrong_run_is_refused(self, deferred):
        payload = {**deferred["payload"], "scoring_run_id": 987654}
        assert self._release(payload) is False
        assert deferred["sent"]["assignment"] == []

    @LEGACY_DISPATCH_RETIRED
    def test_wrong_job_is_refused(self, deferred):
        payload = {**deferred["payload"], "screening_job_id": 987654}
        assert self._release(payload) is False
        assert deferred["sent"]["assignment"] == []

    @LEGACY_DISPATCH_RETIRED
    def test_arbitrary_celery_args_are_refused(self, deferred):
        payload = {**deferred["payload"], "args": [987654, 3.0]}
        assert self._release(payload) is False
        assert deferred["sent"]["assignment"] == []

    @LEGACY_DISPATCH_RETIRED
    def test_release_rebuilds_args_from_locked_attempt(self, db_session,
                                                       deferred):
        payload = deferred["payload"]
        assert "args" not in payload and "kwargs" not in payload
        assert self._release(payload) is True
        call = deferred["sent"]["assignment_calls"][-1]
        attempt = db_session.get(ChannelAssignmentAttempt,
                                 payload["attempt_id"])
        assert call["args"] == [
            attempt.scoring_run_id, float(attempt.relevance_coefficient)]
        assert call["kwargs"] == {
            "requested_policy_version": attempt.requested_policy_version,
            "requested_anchor_version": attempt.requested_anchor_version,
            "recompute_relevance": False,
            "requested_strategy_version": attempt.requested_strategy_version,
        }

    @LEGACY_DISPATCH_RETIRED
    def test_terminal_attempt_is_refused(self, db_session, deferred):
        attempt = db_session.get(ChannelAssignmentAttempt,
                                 deferred["payload"]["attempt_id"])
        attempt.status = "failed"
        db_session.commit()
        assert self._release(deferred["payload"]) is False
        assert deferred["sent"]["assignment"] == []

    @LEGACY_DISPATCH_RETIRED
    def test_valid_payload_releases_once(self, deferred):
        assert self._release(deferred["payload"]) is True
        assert deferred["sent"]["assignment"] == [
            deferred["result"]["task_id"]]


class TestLostPublishWindow:
    """Codex 27. tur #3: yayım penceresinde ölen process kurtarılır."""

    @LEGACY_DISPATCH_RETIRED
    def test_stale_publishing_is_republished_with_same_task_id(
            self, db_session, scenario, monkeypatch, allowlisted, telemetry,
            no_broker):
        from datetime import datetime, timedelta, timezone

        from app.core.channel.assignment_dispatcher import (
            republish_stale_deferred_parents,
        )

        monkeypatch.setattr(allowlisted, "CORPUS_SCREENING_DEFAULT_MODE",
                            "assistive")
        monkeypatch.setattr(allowlisted,
                            "CORPUS_DOWNSTREAM_MAX_APPROVED_USD", 100.0)
        result = enqueue_channel_assignment(db_session, scenario["run"],
                                            from_status="scored")
        payload = no_broker["screening"][0]["kwargs"]["deferred_parent"]
        _complete_job(db_session, payload)

        # Yayım penceresinde ölüm taklidi: publishing + lease dolmuş
        attempt = db_session.get(ChannelAssignmentAttempt,
                                 payload["attempt_id"])
        attempt.assignment_dispatch_state = "publishing"
        attempt.dispatch_last_at = (datetime.now(timezone.utc)
                                    - timedelta(seconds=3600))
        db_session.commit()

        again = republish_stale_deferred_parents(db_session,
                                                 scenario["run"].id)
        assert again["task_id"] == result["task_id"]   # AYNI kimlik
        assert again["republished"] is True
        assert no_broker["assignment"] == [result["task_id"]]
        db_session.expire_all()
        attempt = db_session.get(ChannelAssignmentAttempt,
                                 payload["attempt_id"])
        assert attempt.assignment_dispatch_state == "sent"

    @LEGACY_DISPATCH_RETIRED
    def test_live_publishing_lease_is_not_stolen(self, db_session, scenario,
                                                 monkeypatch, allowlisted,
                                                 telemetry, no_broker):
        from datetime import datetime, timezone

        from app.core.channel.assignment_dispatcher import (
            republish_stale_deferred_parents,
        )

        monkeypatch.setattr(allowlisted, "CORPUS_SCREENING_DEFAULT_MODE",
                            "assistive")
        monkeypatch.setattr(allowlisted,
                            "CORPUS_DOWNSTREAM_MAX_APPROVED_USD", 100.0)
        enqueue_channel_assignment(db_session, scenario["run"],
                                   from_status="scored")
        payload = no_broker["screening"][0]["kwargs"]["deferred_parent"]
        attempt = db_session.get(ChannelAssignmentAttempt,
                                 payload["attempt_id"])
        attempt.assignment_dispatch_state = "publishing"
        attempt.dispatch_last_at = datetime.now(timezone.utc)
        db_session.commit()
        assert republish_stale_deferred_parents(
            db_session, scenario["run"].id) is None
        assert no_broker["assignment"] == []

    @LEGACY_DISPATCH_RETIRED
    def test_periodic_task_recovers_without_new_dispatch(
            self, db_session, scenario, monkeypatch, allowlisted, telemetry,
            no_broker):
        from datetime import datetime, timedelta, timezone

        from app.tasks.celery_app import celery_app
        from app.tasks.screening_tasks import (
            reconcile_deferred_parents_task,
        )

        monkeypatch.setattr(allowlisted, "CORPUS_SCREENING_DEFAULT_MODE",
                            "assistive")
        monkeypatch.setattr(allowlisted,
                            "CORPUS_DOWNSTREAM_MAX_APPROVED_USD", 100.0)
        result = enqueue_channel_assignment(db_session, scenario["run"],
                                            from_status="scored")
        payload = no_broker["screening"][0]["kwargs"]["deferred_parent"]
        _complete_job(db_session, payload)
        attempt = db_session.get(ChannelAssignmentAttempt,
                                 payload["attempt_id"])
        attempt.assignment_dispatch_state = "publishing"
        attempt.dispatch_last_at = (
            datetime.now(timezone.utc) - timedelta(seconds=3600))
        db_session.commit()

        monkeypatch.setattr(
            "app.database.connection.SessionLocal",
            lambda: db_session.__class__(bind=db_session.get_bind()))
        out = reconcile_deferred_parents_task.run()

        assert out == {
            "scanned_runs": 1,
            "republished": 1,
            "task_ids": [result["task_id"]],
        }
        assert no_broker["assignment"] == [result["task_id"]]
        schedule = celery_app.conf.beat_schedule[
            "reconcile-deferred-screening-parents"]
        assert schedule["task"] == \
            "corpus_screening.reconcile_deferred_parents"
        assert schedule["schedule"] == 60.0


class TestAssistiveReuse:
    """Codex 27. tur #1: assistive reuse KARŞI-OLGU YAZMAZ."""

    @LEGACY_DISPATCH_RETIRED
    def test_reuse_writes_no_counterfactual_rows(self, db_session, scenario,
                                                 monkeypatch, allowlisted,
                                                 telemetry, no_broker):
        from app.database.models import CorpusCandidateSelection

        monkeypatch.setattr(allowlisted, "CORPUS_SCREENING_DEFAULT_MODE",
                            "assistive")
        monkeypatch.setattr(allowlisted,
                            "CORPUS_DOWNSTREAM_MAX_APPROVED_USD", 100.0)
        first = enqueue_channel_assignment(db_session, scenario["run"],
                                           from_status="scored")
        job = db_session.query(CorpusScreeningJob).one()
        job.status = "completed"
        attempt = db_session.query(ChannelAssignmentAttempt).one()
        attempt.status = "completed"
        attempt.assignment_dispatch_state = "finished"
        db_session.query(TaskResult).filter_by(
            task_id=first["task_id"]).one().status = "completed"
        scenario["run"].status = "scored"
        db_session.commit()

        second = enqueue_channel_assignment(db_session, scenario["run"],
                                            from_status="scored")
        assert second["screening_reused"] is True
        assert second["screening_mode"] == "assistive"
        # Sağlayıcıya gidilmedi VE sahte audit üretilmedi
        assert len(no_broker["screening"]) == 1
        assert db_session.query(CorpusCandidateSelection).count() == 0
        # Kararlar hazır: parent HEMEN koşar (ertelenmez)
        assert second["parent_deferred"] is False
        assert second["task_id"] in no_broker["assignment"]

    @LEGACY_DISPATCH_RETIRED
    def test_reuse_parent_applies_union_end_to_end(
            self, db_session, scenario, monkeypatch, allowlisted, telemetry,
            no_broker):
        """Reusable decisions reach the parent task and the live pool."""
        from app.core.channel.channel_engine import ChannelEngine
        from app.tasks.intent_tasks import run_channel_assignment_task

        monkeypatch.setattr(allowlisted, "CORPUS_SCREENING_DEFAULT_MODE",
                            "assistive")
        monkeypatch.setattr(allowlisted,
                            "CORPUS_DOWNSTREAM_MAX_APPROVED_USD", 100.0)
        first = enqueue_channel_assignment(db_session, scenario["run"],
                                           from_status="scored")
        job = db_session.query(CorpusScreeningJob).one()
        rows = list((job.input_snapshot or {}).get("rows") or [])
        assert rows
        for index, row in enumerate(rows):
            fit = 2 if index >= len(rows) - 4 else 0
            for channel in ("ADS", "SEO", "SOCIAL"):
                db_session.add(CorpusScreeningDecision(
                    screening_job_id=job.id,
                    keyword_score_id=row["keyword_score_id"],
                    keyword_id=row["keyword_id"],
                    channel=channel,
                    view_a_fit=fit,
                    view_b_fit=fit,
                    view_a_unresolved=False,
                    view_b_unresolved=False,
                    merged_fit=Decimal(fit),
                    passing=bool(fit),
                    disagreement=False,
                    uncertain=False,
                ))
        job.status = "completed"
        first_attempt = db_session.query(ChannelAssignmentAttempt).one()
        first_attempt.status = "completed"
        first_attempt.assignment_dispatch_state = "finished"
        db_session.query(TaskResult).filter_by(
            task_id=first["task_id"]).one().status = "completed"
        scenario["run"].status = "scored"
        db_session.commit()

        second = enqueue_channel_assignment(db_session, scenario["run"],
                                            from_status="scored")
        second_attempt = (
            db_session.query(ChannelAssignmentAttempt)
            .filter_by(parent_task_id=second["task_id"])
            .one()
        )
        assert second["screening_reused"] is True
        assert second["parent_deferred"] is False

        class _FakeAI:
            collector = None

            def close(self):
                return None

        class _FakeBinding:
            def summary(self):
                return {"settled_usd": "0"}

        monkeypatch.setattr(
            "app.tasks.intent_tasks.get_ai_service", lambda: _FakeAI())
        monkeypatch.setattr(
            "app.core.telemetry.downstream_ledger.require_ledgered_routes",
            lambda *args, **kwargs: None)
        monkeypatch.setattr(
            "app.core.telemetry.downstream_ledger.attach_downstream_ledger",
            lambda *args, **kwargs: _FakeBinding())

        def _apply_only(self, scoring_run_id, relevance_coefficient=1.0,
                        task_id=None, **kwargs):
            from app.core.screening.assistive import build_applied_plan

            plan, skip = build_applied_plan(
                self.db, scoring_run_id=scoring_run_id,
                parent_task_id=task_id,
                relevance_coefficient=relevance_coefficient)
            counts, info = self._build_pools_atomically(
                scoring_run_id,
                relevance_coefficient=relevance_coefficient,
                applied_plan=plan,
                screening_skip=skip,
                parent_task_id=task_id,
            )
            return {
                "steps": {
                    "pool_building": counts,
                    "screening_apply": info,
                },
                "selection_quality": {},
            }

        monkeypatch.setattr(ChannelEngine, "run_channel_assignment",
                            _apply_only)
        monkeypatch.setattr(run_channel_assignment_task, "update_state",
                            lambda *args, **kwargs: None)
        result = run_channel_assignment_task.apply(
            args=[scenario["run"].id, 1.0],
            kwargs={
                "requested_policy_version":
                    second_attempt.requested_policy_version,
                "requested_anchor_version":
                    second_attempt.requested_anchor_version,
                "recompute_relevance": False,
                "requested_strategy_version":
                    second_attempt.requested_strategy_version,
            },
            task_id=second["task_id"],
        ).get()

        assert result["status"] == "completed", result
        apply_info = result["result"]["steps"]["screening_apply"]
        assert apply_info["applied"] is True
        db_session.expire_all()
        applied = db_session.query(CorpusCandidateSelection).filter_by(
            assignment_attempt_id=second_attempt.id,
            is_applied=True).all()
        assert applied
        for channel in ("ADS", "SEO"):
            live = {
                row.keyword_id
                for row in db_session.query(ChannelCandidate).filter_by(
                    scoring_run_id=scenario["run"].id, channel=channel)
            }
            audited = {
                row.keyword_id for row in applied if row.channel == channel
            }
            assert live == audited
        assert not any(row.channel == "SOCIAL" for row in applied)


class TestPreferenceCannotEscalate:
    """Run tercihi yönetici varsayılanını AŞAMAZ (canlıda yakalandı).

    Benchmark scripti run 26'ya `screening_preference='assistive'` yazmıştı;
    `DEFAULT_MODE=off` (harcamayı durdurma kolu) çekildiğinde o run hâlâ
    assistive dönüyordu. Override artık yalnız DARALTABİLİR.
    """

    def test_stale_assistive_preference_cannot_beat_default_off(
            self, db_session, scenario, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "ENABLE_CORPUS_SCREENING", True)
        monkeypatch.setattr(settings, "CORPUS_SCREENING_ALLOWLIST",
                            [scenario["ws"].id])
        monkeypatch.setattr(settings, "CORPUS_SCREENING_DEFAULT_MODE", "off")
        scenario["run"].screening_preference = "assistive"
        db_session.commit()
        assert decide_screening_mode(
            scenario["run"], scenario["ws"], settings=settings,
            db=db_session) == ("off", "DEFAULT_MODE_OFF")

    def test_assistive_preference_is_clamped_to_shadow_default(
            self, db_session, scenario, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "ENABLE_CORPUS_SCREENING", True)
        monkeypatch.setattr(settings, "CORPUS_SCREENING_ALLOWLIST",
                            [scenario["ws"].id])
        monkeypatch.setattr(settings, "CORPUS_SCREENING_DEFAULT_MODE",
                            "shadow")
        scenario["run"].screening_preference = "assistive"
        db_session.commit()
        mode, reason = decide_screening_mode(
            scenario["run"], scenario["ws"], settings=settings,
            db=db_session)
        assert (mode, reason) == ("shadow", None)   # yükseltme YOK

    def test_preference_may_still_narrow(self, db_session, scenario,
                                         monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "ENABLE_CORPUS_SCREENING", True)
        monkeypatch.setattr(settings, "CORPUS_SCREENING_ALLOWLIST",
                            [scenario["ws"].id])
        monkeypatch.setattr(settings, "CORPUS_SCREENING_DEFAULT_MODE",
                            "assistive")
        scenario["run"].screening_preference = "shadow"
        db_session.commit()
        mode, reason = decide_screening_mode(
            scenario["run"], scenario["ws"], settings=settings,
            db=db_session)
        assert (mode, reason) == ("shadow", None)   # daraltma SERBEST
