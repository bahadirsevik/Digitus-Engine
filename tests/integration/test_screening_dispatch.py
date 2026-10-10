# -*- coding: utf-8 -*-
"""Screening dispatch orkestrasyonu + preflight (plan §6.3, §7.1-7.2).

Kilitlenen sözleşmeler:
- Bayrak kapalıyken davranış BUGÜNKÜ off yolu: job yok, cap yok, mod off.
- Shadow'da parent TaskResult + attempt + mühürlü job AYNI commit'te doğar;
  çocuk ayrı kuyruğa commit SONRASI verilir ve canlı havuza dokunmaz.
- Preflight FAIL-CLOSED: bağlam/evren/fiyat/telemetri eksikse tahmin
  uydurulmaz. Shadow'da bu koşuyu DÜŞÜRMEZ (denetim işi) ama sebep
  görünür kalır; assistive tipli 409 verir (sessizce baseline'a düşmez).
- Job mührü runner'ın harcamadan önce yaptığı doğrulamayı GEÇER.
"""
from decimal import Decimal

import pytest

from app.core.channel.assignment_dispatcher import (
    ChannelAssignmentPreconditionError,
    enqueue_channel_assignment,
)
from app.core.screening.dispatch import fail_attempt, plan_dispatch
from app.core.screening.preflight import (
    MODE_OFF,
    MODE_SHADOW,
    PreflightError,
    build_assignment_preflight,
    preflight_sha_for,
    resolve_screening_mode,
)
from app.database.models import (
    AiUsageEvent,
    ChannelAssignmentAttempt,
    CorpusScreeningJob,
    Keyword,
    KeywordScore,
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
    "product_definition": "Borsa yatırımcıları için analiz platformu",
    "content_strategy": "Karar destek içerikleri ve hisse analizleri",
    "social_mode": "hype",
    "schema_version": 1,
    "approved_fingerprint": "fp-approved-1",
}
STAGES = ("intent", "ads_prefilter", "social_prefilter", "seo_metadata")


@pytest.fixture
def enabled(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "ENABLE_CORPUS_SCREENING", True)
    monkeypatch.setattr(settings, "CORPUS_SCREENING_MAX_APPROVED_USD", 5.0)
    monkeypatch.setattr(settings, "CORPUS_SCREENING_MAX_KEYWORDS", 1500)
    # Teknik UI (mod seçici + onay modalı) AYRI bayraktadır: sunucu
    # kontrollü modelde kullanıcıya gösterilmez, testlerde açıkça açılır
    monkeypatch.setattr(settings, "CORPUS_SCREENING_TECHNICAL_UI", True)
    return settings


@pytest.fixture(autouse=True)
def flag_off_by_default(monkeypatch):
    """Bayrak testte ACIKCA kapatilir.

    Canli `.env` uzerinde bayrak acik olabilir (smoke testi); testler
    ortamdan BAGIMSIZ olmali — `enabled` fixture'i acmak isteyen testler
    icin bunu geri ceviriyor.
    """
    from app.config import settings

    monkeypatch.setattr(settings, "ENABLE_CORPUS_SCREENING", False)


@pytest.fixture
def no_broker(monkeypatch):
    """Celery'ye GERÇEK teslim yapılmaz; çağrılar kaydedilir."""
    sent = {"assignment": [], "screening": []}
    monkeypatch.setattr(
        "app.tasks.intent_tasks.run_channel_assignment_task.apply_async",
        lambda *a, **kw: sent["assignment"].append(kw) or None)
    monkeypatch.setattr(
        "app.tasks.screening_tasks.run_corpus_screening_task.apply_async",
        lambda *a, **kw: sent["screening"].append(kw) or None)
    return sent


@pytest.fixture
def scenario(db_session, make_workspace, make_scoring_run):
    ws = make_workspace("Screening WS", channel_strategy=dict(STRATEGY),
                        strategy_version=3,
                        profile_data={"target_audience": "Yatırımcılar"})
    run = make_scoring_run(brand_profile_id=ws.id, status="scored",
                           ads_capacity=6, seo_capacity=6,
                           social_capacity=6, algorithm_version="v2_1")
    for i in range(9):
        kw = Keyword(keyword=f"hisse analizi {i}", monthly_volume=100 + i)
        db_session.add(kw)
        db_session.flush()
        db_session.add(KeywordScore(scoring_run_id=run.id, keyword_id=kw.id,
                                    ads_score=1, seo_score=1, social_score=1))
    db_session.commit()
    return {"ws": ws, "run": run}


@pytest.fixture
def telemetry(db_session, scenario):
    """Downstream birim maliyeti için geçmiş kullanım örneklemi."""
    from app.config import settings

    # Tarama tarafi icin de karma telemetri (KABA beklenti hesabi)
    from app.core.screening.candidate_union import (
        PRODUCTION_SCREENING_CONTRACT as _CONTRACT,
    )

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


def _preflight(db_session, scenario, mode=MODE_SHADOW):
    return build_assignment_preflight(
        db_session, scenario["run"], scenario["ws"], mode=mode,
        relevance_coefficient=1.0)


def _approved_dispatch(db_session, scenario, mode=MODE_SHADOW, **kwargs):
    """Kullanıcının GÖRDÜĞÜ preflight'ı onaylayıp dispatch eder.

    Ücretli tarama artık onaysız başlamaz (Codex 10. tur #4): mod + SHA +
    birlesik tavan istekle taşınır ve sunucu bunları yeniden hesaplayıp
    karşılaştırır.
    """
    scenario["run"].screening_preference = mode
    db_session.commit()
    pf = build_assignment_preflight(
        db_session, scenario["run"], scenario["ws"], mode=mode,
        relevance_coefficient=float(
            scenario["run"].default_relevance_coefficient or 1.0))
    return enqueue_channel_assignment(
        db_session, scenario["run"], from_status="scored",
        approved_screening_mode=mode,
        approved_preflight_sha256=pf["preflight_sha256"],
        approved_screening_hard_cap_usd=pf["enforced_hard_cap_usd"],
        **kwargs), pf


class TestPreflight:
    def test_happy_path_produces_bound_caps(self, db_session, scenario,
                                            enabled, telemetry):
        pf = _preflight(db_session, scenario)
        assert pf["universe_size"] == 9
        assert pf["planned_screening_requests"] >= 2      # iki görünüm
        assert pf["screening_hard_cap_usd"] > 0
        assert pf["downstream_hard_cap_usd"] > 0
        assert pf["combined_exposure_usd"] == pytest.approx(
            pf["screening_hard_cap_usd"] + pf["downstream_hard_cap_usd"],
            abs=1e-6)
        # İki ayrı cap aynı attempt ledger'ında ve toplamda uygulanır.
        assert pf["enforced_hard_cap_usd"] == pf["screening_hard_cap_usd"]
        assert pf["enforced_scopes"] == ["screening", "downstream"]
        assert pf["downstream_enforced"] is True
        assert pf["applied_candidate_multiplier"] == 1    # shadow
        assert pf["counterfactual_target_multiplier"] == 3
        assert preflight_sha_for(pf) == pf["preflight_sha256"]

    def test_sha_changes_when_inputs_change(self, db_session, scenario,
                                            enabled, telemetry):
        first = _preflight(db_session, scenario)
        scenario["run"].ads_capacity = 12
        db_session.commit()
        second = _preflight(db_session, scenario)
        assert second["preflight_sha256"] != first["preflight_sha256"]

    def test_sha_is_stable_for_same_inputs(self, db_session, scenario,
                                           enabled, telemetry):
        assert (_preflight(db_session, scenario)["preflight_sha256"]
                == _preflight(db_session, scenario)["preflight_sha256"])

    def test_flag_off_is_refused(self, db_session, scenario, telemetry):
        with pytest.raises(PreflightError) as exc:
            _preflight(db_session, scenario)
        assert exc.value.code == "SCREENING_DISABLED"

    def test_missing_strategy_fails_closed(self, db_session, scenario,
                                           enabled, telemetry):
        scenario["ws"].channel_strategy = None
        db_session.commit()
        with pytest.raises(PreflightError) as exc:
            _preflight(db_session, scenario)
        assert exc.value.code == "SCREENING_CONTEXT_MISSING"

    def test_empty_universe_fails_closed(self, db_session, scenario,
                                         enabled, telemetry):
        db_session.query(KeywordScore).filter_by(
            scoring_run_id=scenario["run"].id).delete()
        db_session.commit()
        with pytest.raises(PreflightError) as exc:
            _preflight(db_session, scenario)
        assert exc.value.code == "UNIVERSE_EMPTY"

    def test_universe_over_limit_fails_closed(self, db_session, scenario,
                                              enabled, telemetry,
                                              monkeypatch):
        monkeypatch.setattr(enabled, "CORPUS_SCREENING_MAX_KEYWORDS", 3)
        with pytest.raises(PreflightError) as exc:
            _preflight(db_session, scenario)
        assert exc.value.code == "UNIVERSE_TOO_LARGE"

    def test_cap_above_admin_limit_fails_closed(self, db_session, scenario,
                                                enabled, telemetry,
                                                monkeypatch):
        monkeypatch.setattr(enabled, "CORPUS_SCREENING_MAX_APPROVED_USD",
                            0.000001)
        with pytest.raises(PreflightError) as exc:
            _preflight(db_session, scenario)
        assert exc.value.code == "SCREENING_CAP_EXCEEDS_LIMIT"

    def test_missing_downstream_telemetry_fails_closed(self, db_session,
                                                       scenario, enabled):
        with pytest.raises(PreflightError) as exc:
            _preflight(db_session, scenario)
        assert exc.value.code == "DOWNSTREAM_PRICING_UNAVAILABLE"

    def test_off_mode_has_no_preflight(self, db_session, scenario, enabled,
                                       telemetry):
        with pytest.raises(PreflightError) as exc:
            _preflight(db_session, scenario, mode=MODE_OFF)
        assert exc.value.code == "SCREENING_MODE_INVALID"

    def test_mode_resolution_respects_kill_switch(self, scenario, enabled,
                                                  monkeypatch):
        run = scenario["run"]
        run.screening_preference = "shadow"
        assert resolve_screening_mode(run, enabled) == MODE_SHADOW
        monkeypatch.setattr(enabled, "ENABLE_CORPUS_SCREENING", False)
        assert resolve_screening_mode(run, enabled) == MODE_OFF


class TestOffDispatch:
    @LEGACY_DISPATCH_RETIRED
    def test_flag_off_creates_off_attempt_without_job(self, db_session,
                                                      scenario, no_broker):
        result = enqueue_channel_assignment(db_session, scenario["run"],
                                            from_status="scored")
        assert result["screening_mode"] == MODE_OFF
        assert result["screening_job_id"] is None
        attempt = db_session.query(ChannelAssignmentAttempt).filter_by(
            parent_task_id=result["task_id"]).one()
        assert attempt.screening_mode == MODE_OFF
        assert attempt.applied_candidate_multiplier == 1
        assert attempt.counterfactual_target_multiplier == 1
        # Cap YOK → ledger bu attempt adına harcama açamaz
        assert attempt.approved_screening_cap_usd is None
        assert attempt.approved_downstream_cap_usd is None
        assert db_session.query(CorpusScreeningJob).count() == 0
        assert no_broker["screening"] == []
        assert len(no_broker["assignment"]) == 1

    @LEGACY_DISPATCH_RETIRED
    def test_second_dispatch_reuses_active_task(self, db_session, scenario,
                                                no_broker):
        first = enqueue_channel_assignment(db_session, scenario["run"],
                                           from_status="scored")
        second = enqueue_channel_assignment(db_session, scenario["run"])
        assert second["already_active"] is True
        assert second["task_id"] == first["task_id"]
        assert db_session.query(ChannelAssignmentAttempt).count() == 1


class TestShadowDispatch:
    @LEGACY_DISPATCH_RETIRED
    def test_attempt_job_and_child_are_created(self, db_session, scenario,
                                               enabled, telemetry,
                                               no_broker):
        result, _pf = _approved_dispatch(db_session, scenario)
        assert result["screening_mode"] == MODE_SHADOW
        attempt = db_session.query(ChannelAssignmentAttempt).one()
        job = db_session.query(CorpusScreeningJob).one()
        assert attempt.screening_job_id == job.id
        assert attempt.applied_candidate_multiplier == 1     # canlı havuz 1x
        assert attempt.counterfactual_target_multiplier == 3
        assert Decimal(attempt.approved_screening_cap_usd) > 0
        assert attempt.preflight_sha256 and attempt.context_sha256
        assert job.status == "pending" and job.task_id == \
            result["screening_task_id"]
        assert job.planned_requests >= 2
        assert len(job.input_snapshot["rows"]) == 9
        # Assignment ve tarama AYRI kuyruklarda
        assert len(no_broker["assignment"]) == 1
        assert no_broker["screening"][0]["queue"] == "corpus_screening"
        kwargs = no_broker["screening"][0]["kwargs"]
        assert kwargs["job_id"] == job.id
        assert kwargs["attempt_id"] == attempt.id
        assert Decimal(kwargs["expected_screening_cap_usd"]) == Decimal(
            attempt.approved_screening_cap_usd)

    @LEGACY_DISPATCH_RETIRED
    def test_parent_task_carries_phase_and_cap(self, db_session, scenario,
                                               enabled, telemetry,
                                               no_broker):
        result, _pf = _approved_dispatch(db_session, scenario)
        parent = db_session.query(TaskResult).filter_by(
            task_id=result["task_id"]).one()
        data = parent.result_data
        assert data["phase"] == "screening"
        assert data["screening_mode"] == MODE_SHADOW
        assert data["combined_exposure_usd"] > 0
        assert data["enforced_hard_cap_usd"] > 0
        assert data["screening_job_id"] == result["screening_job_id"]

    @LEGACY_DISPATCH_RETIRED
    def test_job_seals_pass_runner_verification(self, db_session, scenario,
                                                enabled, telemetry,
                                                no_broker):
        """Dispatch'in mührü ile runner'ın doğrulaması BİREBİR uyuşur."""
        from app.core.screening.production_runner import (
            _rows_from_snapshot,
            _verify_frozen_inputs,
        )

        _approved_dispatch(db_session, scenario)
        job = db_session.query(CorpusScreeningJob).one()
        universe = _rows_from_snapshot(job)
        context = _verify_frozen_inputs(job, universe)
        assert context["context_sha256"] == job.context_sha256

    @LEGACY_DISPATCH_RETIRED
    def test_preflight_failure_degrades_to_off_but_records_reason(
            self, db_session, scenario, enabled, no_broker, monkeypatch):
        """Telemetri yok → shadow atlanır ama ATAMA sürer (görünür sebep)."""
        from app.config import settings

        monkeypatch.setattr(settings, "CORPUS_SCREENING_ALLOWLIST",
                            [scenario["ws"].id])
        # Run tercihi yönetici varsayılanını AŞAMAZ: shadow'un koşabilmesi
        # için varsayılan da en az shadow olmalı
        monkeypatch.setattr(settings, "CORPUS_SCREENING_DEFAULT_MODE",
                            "shadow")
        scenario["run"].screening_preference = "shadow"
        db_session.commit()
        result = enqueue_channel_assignment(db_session, scenario["run"],
                                            from_status="scored")
        assert result["screening_mode"] == MODE_OFF
        assert result["screening_skipped_reason"] == \
            "DOWNSTREAM_PRICING_UNAVAILABLE"
        assert db_session.query(CorpusScreeningJob).count() == 0
        assert len(no_broker["assignment"]) == 1          # atama SÜRDÜ
        attempt = db_session.query(ChannelAssignmentAttempt).one()
        assert attempt.manifest["screening_skipped_reason"] == \
            "DOWNSTREAM_PRICING_UNAVAILABLE"
        parent = db_session.query(TaskResult).filter_by(
            task_id=result["task_id"]).one()
        assert parent.result_data["screening_skipped_reason"] == \
            "DOWNSTREAM_PRICING_UNAVAILABLE"

    @LEGACY_DISPATCH_RETIRED
    def test_child_enqueue_failure_does_not_break_assignment(
            self, db_session, scenario, enabled, telemetry, monkeypatch,
            no_broker):
        def _boom(*a, **kw):
            raise RuntimeError("broker yok")

        monkeypatch.setattr(
            "app.tasks.screening_tasks.run_corpus_screening_task.apply_async",
            _boom)
        result, _pf = _approved_dispatch(db_session, scenario)
        assert result["screening_task_id"] is None
        assert result["status"] == "pending"              # atama etkilenmedi
        db_session.expire_all()
        job = db_session.query(CorpusScreeningJob).one()
        assert job.status == "failed" and job.error_code == "ENQUEUE_FAILED"
        assert scenario["run"].status == "channel_assigning"


class TestAssistiveGate:
    @LEGACY_DISPATCH_RETIRED
    def test_assistive_is_typed_refusal(self, db_session, scenario, enabled,
                                        telemetry, no_broker):
        scenario["run"].screening_preference = "assistive"
        db_session.commit()
        with pytest.raises(ChannelAssignmentPreconditionError) as exc:
            enqueue_channel_assignment(
                db_session, scenario["run"], from_status="scored",
                approved_screening_mode="assistive",
                approved_preflight_sha256="a" * 64)
        assert exc.value.code == "ASSISTIVE_NOT_ENABLED"
        # Sessiz shadow'a düşüş YOK: ne job ne de görev oluştu
        assert db_session.query(CorpusScreeningJob).count() == 0
        assert no_broker["assignment"] == []

    def test_plan_dispatch_refuses_assistive_directly(self, db_session,
                                                      scenario, enabled,
                                                      telemetry):
        scenario["run"].screening_preference = "assistive"
        with pytest.raises(PreflightError) as exc:
            plan_dispatch(db_session, scenario["run"], scenario["ws"],
                          parent_task_id="t-1")
        assert exc.value.code == "ASSISTIVE_NOT_ENABLED"


class TestApprovalBinding:
    """Codex 10. tur #4: ücretli tarama YALNIZ onayla başlar."""

    @LEGACY_DISPATCH_RETIRED
    def test_auto_dispatch_needs_no_user_approval(self, db_session,
                                                  scenario, enabled,
                                                  telemetry, no_broker,
                                                  monkeypatch):
        """Sunucu kontrollü akış: kullanıcı onayı YOK.

        Allowlist DIŞINDA sessiz baseline (sebep denetimde); allowlist
        İÇİNDE tarama onaysız başlar — sınır yönetici cap'leridir.
        """
        from app.config import settings

        scenario["run"].screening_preference = "shadow"
        db_session.commit()
        result = enqueue_channel_assignment(db_session, scenario["run"],
                                            from_status="scored")
        assert result["screening_mode"] == MODE_OFF
        assert result["screening_skipped_reason"] == \
            "WORKSPACE_NOT_ALLOWLISTED"
        assert result["auto_dispatch"] is True
        assert db_session.query(CorpusScreeningJob).count() == 0
        assert len(no_broker["assignment"]) == 1        # atama SÜRDÜ

    @LEGACY_DISPATCH_RETIRED
    def test_stale_approval_sha_is_refused(self, db_session, scenario,
                                           enabled, telemetry, no_broker):
        scenario["run"].screening_preference = "shadow"
        db_session.commit()
        pf = _preflight(db_session, scenario)
        scenario["run"].ads_capacity = 12               # girdi değişti
        db_session.commit()
        with pytest.raises(ChannelAssignmentPreconditionError) as exc:
            enqueue_channel_assignment(
                db_session, scenario["run"], from_status="scored",
                approved_screening_mode=MODE_SHADOW,
                approved_preflight_sha256=pf["preflight_sha256"],
                approved_screening_hard_cap_usd=pf["enforced_hard_cap_usd"])
        assert exc.value.code == "PREFLIGHT_MISMATCH"
        assert db_session.query(CorpusScreeningJob).count() == 0
        assert no_broker["assignment"] == []

    @LEGACY_DISPATCH_RETIRED
    def test_cap_mismatch_is_refused(self, db_session, scenario, enabled,
                                     telemetry, no_broker):
        scenario["run"].screening_preference = "shadow"
        db_session.commit()
        pf = _preflight(db_session, scenario)
        with pytest.raises(ChannelAssignmentPreconditionError) as exc:
            enqueue_channel_assignment(
                db_session, scenario["run"], from_status="scored",
                approved_screening_mode=MODE_SHADOW,
                approved_preflight_sha256=pf["preflight_sha256"],
                approved_screening_hard_cap_usd=0.01)
        assert exc.value.code == "PREFLIGHT_CAP_MISMATCH"

    @LEGACY_DISPATCH_RETIRED
    def test_approved_mode_beats_changed_run_preference(self, db_session,
                                                        scenario, enabled,
                                                        telemetry,
                                                        no_broker):
        """Kullanıcı off onayladıysa run tercihi shadow olsa da tarama yok."""
        scenario["run"].screening_preference = "shadow"
        db_session.commit()
        result = enqueue_channel_assignment(
            db_session, scenario["run"], from_status="scored",
            approved_screening_mode=MODE_OFF)
        assert result["screening_mode"] == MODE_OFF
        assert db_session.query(CorpusScreeningJob).count() == 0

    @LEGACY_DISPATCH_RETIRED
    def test_cap_written_to_attempt_matches_approval(self, db_session,
                                                     scenario, enabled,
                                                     telemetry, no_broker):
        result, pf = _approved_dispatch(db_session, scenario)
        attempt = db_session.query(ChannelAssignmentAttempt).one()
        assert attempt.preflight_sha256 == pf["preflight_sha256"]
        assert float(attempt.approved_screening_cap_usd) == \
            pf["screening_hard_cap_usd"]
        assert float(attempt.approved_downstream_cap_usd) == \
            pf["downstream_hard_cap_usd"]
        # Counterfactual'ın okuyacağı mühürler de dispatch anında donmuş
        assert attempt.channel_rank_snapshot_sha256 == \
            pf["channel_rank_snapshot_sha256"]


class TestAttemptLifecycle:
    @LEGACY_DISPATCH_RETIRED
    def test_failed_attempt_does_not_block_next_dispatch(self, db_session,
                                                         scenario, enabled,
                                                         telemetry,
                                                         no_broker):
        scenario["run"].screening_preference = "shadow"
        db_session.commit()
        pf = _preflight(db_session, scenario)
        attempt, job, _pf = plan_dispatch(
            db_session, scenario["run"], scenario["ws"],
            parent_task_id="t-stuck", relevance_coefficient=1.0,
            approved_screening_mode=MODE_SHADOW,
            approved_preflight_sha256=pf["preflight_sha256"],
            approved_screening_hard_cap_usd=pf["enforced_hard_cap_usd"])
        db_session.commit()
        fail_attempt(db_session, "t-stuck", code="TRANSITION_FAILED",
                     message="çöktü")
        db_session.expire_all()
        assert db_session.get(ChannelAssignmentAttempt,
                              attempt.id).status == "failed"
        assert db_session.get(CorpusScreeningJob, job.id).status == "failed"
        # Aktif-attempt partial unique'i artık serbest → yeni dispatch açılır
        result, _ = _approved_dispatch(db_session, scenario)
        assert result["screening_mode"] == MODE_SHADOW
        assert db_session.query(ChannelAssignmentAttempt).count() == 2

    @LEGACY_DISPATCH_RETIRED
    def test_off_attempt_is_closed_when_assignment_completes(self, db_session,
                                                             scenario,
                                                             no_broker):
        """Codex 10. tur #2: kapanmayan attempt bir sonraki atamayı bloke eder."""
        from app.core.screening.attempt_state import try_finish_attempt

        first = enqueue_channel_assignment(db_session, scenario["run"],
                                           from_status="scored")
        task = db_session.query(TaskResult).filter_by(
            task_id=first["task_id"]).one()
        task.status = "completed"
        db_session.commit()
        assert try_finish_attempt(
            db_session, parent_task_id=first["task_id"]) == "completed"
        db_session.expire_all()
        attempt = db_session.query(ChannelAssignmentAttempt).one()
        assert attempt.status == "completed" and attempt.phase == "completed"

    @LEGACY_DISPATCH_RETIRED
    def test_second_assignment_after_completion_is_allowed(self, db_session,
                                                           scenario,
                                                           no_broker):
        """Flag KAPALI yolun regresyon testi: ikinci atama açılabilmeli."""
        from app.core.screening.attempt_state import try_finish_attempt

        first = enqueue_channel_assignment(db_session, scenario["run"],
                                           from_status="scored")
        task = db_session.query(TaskResult).filter_by(
            task_id=first["task_id"]).one()
        task.status = "completed"
        db_session.commit()
        try_finish_attempt(db_session, parent_task_id=first["task_id"])
        scenario["run"].status = "scored"
        db_session.commit()
        second = enqueue_channel_assignment(db_session, scenario["run"],
                                            from_status="scored")
        assert second["task_id"] != first["task_id"]
        assert db_session.query(ChannelAssignmentAttempt).count() == 2

    @LEGACY_DISPATCH_RETIRED
    def test_shadow_attempt_stays_running_until_screening_ends(
            self, db_session, scenario, enabled, telemetry, no_broker):
        """Parent bitse de tarama sürüyorsa attempt KAPANMAZ (ledger sahibi)."""
        from app.core.screening.attempt_state import try_finish_attempt

        result, _pf = _approved_dispatch(db_session, scenario)
        task = db_session.query(TaskResult).filter_by(
            task_id=result["task_id"]).one()
        task.status = "completed"
        db_session.commit()
        assert try_finish_attempt(
            db_session, parent_task_id=result["task_id"]) is None
        job = db_session.query(CorpusScreeningJob).one()
        job.status = "completed"
        db_session.commit()
        assert try_finish_attempt(
            db_session, parent_task_id=result["task_id"]) == "completed"

    @LEGACY_DISPATCH_RETIRED
    def test_orphan_active_attempt_is_reconciled_not_blocking(
            self, db_session, scenario, no_broker):
        """Sahipsiz aktif attempt dispatch'i kalıcı kilitlemez."""
        from app.core.screening.attempt_state import reconcile_stale_attempts

        orphan = ChannelAssignmentAttempt(
            parent_task_id="t-orphan", scoring_run_id=scenario["run"].id,
            brand_profile_id=scenario["ws"].id, status="running",
            phase="assignment", screening_mode="off",
            applied_candidate_multiplier=1,
            counterfactual_target_multiplier=1)
        db_session.add(orphan)
        db_session.commit()
        assert reconcile_stale_attempts(db_session, scenario["run"].id) == 1
        db_session.expire_all()
        assert db_session.get(ChannelAssignmentAttempt,
                              orphan.id).status == "failed"
        result = enqueue_channel_assignment(db_session, scenario["run"],
                                            from_status="scored")
        assert result["already_active"] is False

    @LEGACY_DISPATCH_RETIRED
    def test_live_active_attempt_blocks_second_dispatch(self, db_session,
                                                        scenario, no_broker):
        """Gerçekten koşan attempt varken ikinci dispatch tipli reddedilir."""
        first = enqueue_channel_assignment(db_session, scenario["run"],
                                           from_status="scored")
        task = db_session.query(TaskResult).filter_by(
            task_id=first["task_id"]).one()
        task.status = "running"
        db_session.commit()
        # Aktif parent task guard'ı zaten erken döner; guard'ı atlayıp
        # doğrudan plan_dispatch'i çağırınca tipli hata gelir
        with pytest.raises(PreflightError) as exc:
            plan_dispatch(db_session, scenario["run"], scenario["ws"],
                          parent_task_id="t-second")
        assert exc.value.code == "ATTEMPT_ALREADY_ACTIVE"


class TestPreflightEndpoint:
    """`GET /channels/runs/{id}/assignment-preflight` — hiçbir şey başlatmaz."""

    def test_off_mode_needs_no_approval(self, client, scenario):
        resp = client.get(
            f"/api/v1/channels/runs/{scenario['run'].id}/assignment-preflight",
            params={"brand_profile_id": scenario["ws"].id})
        assert resp.status_code == 200
        body = resp.json()
        assert body["screening_mode"] == MODE_OFF
        assert body["requires_approval"] is False
        assert body["screening_enabled"] is False

    def test_shadow_returns_bound_caps(self, client, db_session, scenario,
                                       enabled, telemetry):
        scenario["run"].screening_preference = "shadow"
        db_session.commit()
        resp = client.get(
            f"/api/v1/channels/runs/{scenario['run'].id}/assignment-preflight",
            params={"brand_profile_id": scenario["ws"].id})
        assert resp.status_code == 200
        body = resp.json()
        assert body["requires_approval"] is True
        assert body["screening_mode"] == MODE_SHADOW
        assert body["combined_exposure_usd"] > 0
        assert body["downstream_enforced"] is True
        assert len(body["preflight_sha256"]) == 64
        # Prompt bağlamı ve evren satırları API'den SIZMAZ
        assert "universe_rows" not in body and "context" not in body

    def test_fail_closed_reason_is_typed_409(self, client, scenario,
                                             enabled):
        resp = client.get(
            f"/api/v1/channels/runs/{scenario['run'].id}/assignment-preflight",
            params={"brand_profile_id": scenario["ws"].id,
                    "mode": "shadow"})
        assert resp.status_code == 409
        assert resp.json()["detail"]["code"] == \
            "DOWNSTREAM_PRICING_UNAVAILABLE"

    def test_cross_workspace_is_404(self, client, scenario, make_workspace):
        other = make_workspace("Yabancı WS")
        resp = client.get(
            f"/api/v1/channels/runs/{scenario['run'].id}/assignment-preflight",
            params={"brand_profile_id": other.id})
        assert resp.status_code == 404


class TestFreshnessScreeningAxis:
    def test_off_pool_never_stale_from_screening(self, scenario):
        from app.core.policy.freshness import compute_pool_freshness

        run = scenario["run"]
        run.channel_pool_screening_mode = "off"
        run.channel_pool_screening_context_sha256 = None
        fresh = compute_pool_freshness(run, scenario["ws"])
        assert fresh.screening_context_stale is False

    def test_assistive_pool_stale_when_context_changes(self, db_session,
                                                       scenario):
        from app.core.policy.freshness import compute_pool_freshness
        from app.core.screening.context import canonical_screening_context

        run = scenario["run"]
        live = canonical_screening_context(scenario["ws"])["context_sha256"]
        run.channel_pool_screening_mode = "assistive"
        run.channel_pool_screening_context_sha256 = live
        assert compute_pool_freshness(
            run, scenario["ws"]).screening_context_stale is False
        strategy = dict(STRATEGY, content_strategy="Yepyeni içerik ekseni")
        scenario["ws"].channel_strategy = strategy
        db_session.commit()
        fresh = compute_pool_freshness(run, scenario["ws"])
        assert fresh.screening_context_stale is True
        assert fresh.channel_pool_stale is True

    def test_assistive_pool_stale_when_strategy_removed(self, db_session,
                                                        scenario):
        """Bağlam artık kurulamıyorsa havuz DOĞRULANAMAZ → fail-closed."""
        from app.core.policy.freshness import compute_pool_freshness

        run = scenario["run"]
        run.channel_pool_screening_mode = "assistive"
        run.channel_pool_screening_context_sha256 = "d" * 64
        scenario["ws"].channel_strategy = None
        db_session.commit()
        assert compute_pool_freshness(
            run, scenario["ws"]).screening_context_stale is True

    def test_assignment_in_progress_is_reported(self, db_session, scenario):
        from fastapi import HTTPException

        from app.core.policy.freshness import compute_pool_freshness
        from app.core.workspace import require_fresh_channel_pool

        run = scenario["run"]
        run.status = "channel_assigning"
        fresh = compute_pool_freshness(run, scenario["ws"])
        assert fresh.assignment_in_progress is True
        # Atama sürerken eski havuzdan generation/export yapılamaz.
        assert fresh.channel_pool_stale is True
        assert fresh.as_dict()["assignment_in_progress"] is True
        with pytest.raises(HTTPException) as exc:
            require_fresh_channel_pool(db_session, run)
        assert exc.value.status_code == 409
        assert exc.value.detail["assignment_in_progress"] is True


class TestParentAssignmentClaim:
    """Codex 11. tur #2: parent pipeline TEK SAHİPLİ çalışır."""

    @LEGACY_DISPATCH_RETIRED
    def test_second_delivery_is_refused(self, db_session, scenario,
                                        no_broker):
        from app.core.screening.attempt_state import (
            AssignmentNotClaimable,
            claim_assignment,
        )

        result = enqueue_channel_assignment(db_session, scenario["run"],
                                            from_status="scored")
        assert claim_assignment(db_session,
                                parent_task_id=result["task_id"]) is True
        with pytest.raises(AssignmentNotClaimable, match="CANLI assignment"):
            claim_assignment(db_session, parent_task_id=result["task_id"])
        attempt = db_session.query(ChannelAssignmentAttempt).one()
        assert attempt.assignment_dispatch_state == "started"
        assert attempt.status == "running"
        assert attempt.dispatch_attempts == 1

    @LEGACY_DISPATCH_RETIRED
    def test_expired_lease_allows_retake(self, db_session, scenario,
                                         no_broker):
        from datetime import datetime, timedelta, timezone

        from app.core.screening.attempt_state import (
            ASSIGNMENT_LEASE_SECONDS,
            claim_assignment,
        )

        result = enqueue_channel_assignment(db_session, scenario["run"],
                                            from_status="scored")
        claim_assignment(db_session, parent_task_id=result["task_id"])
        attempt = db_session.query(ChannelAssignmentAttempt).one()
        attempt.dispatch_last_at = datetime.now(timezone.utc) - timedelta(
            seconds=ASSIGNMENT_LEASE_SECONDS + 10)
        db_session.commit()
        assert claim_assignment(db_session,
                                parent_task_id=result["task_id"]) is True
        db_session.expire_all()
        assert db_session.query(
            ChannelAssignmentAttempt).one().dispatch_attempts == 2

    @LEGACY_DISPATCH_RETIRED
    def test_terminal_attempt_cannot_be_reclaimed(self, db_session,
                                                  scenario, no_broker):
        from app.core.screening.attempt_state import (
            AssignmentNotClaimable,
            claim_assignment,
        )

        result = enqueue_channel_assignment(db_session, scenario["run"],
                                            from_status="scored")
        attempt = db_session.query(ChannelAssignmentAttempt).one()
        attempt.status = "completed"
        db_session.commit()
        with pytest.raises(AssignmentNotClaimable, match="terminal"):
            claim_assignment(db_session, parent_task_id=result["task_id"])

    @LEGACY_DISPATCH_RETIRED
    def test_task_skips_when_not_claimable(self, db_session, scenario,
                                           no_broker, monkeypatch):
        """Mükerrer teslim pipeline'ı ÇALIŞTIRMAZ."""
        from app.tasks import intent_tasks

        result = enqueue_channel_assignment(db_session, scenario["run"],
                                            from_status="scored")
        attempt = db_session.query(ChannelAssignmentAttempt).one()
        attempt.status = "completed"
        db_session.commit()
        monkeypatch.setattr(
            "app.database.connection.SessionLocal",
            lambda: db_session.__class__(bind=db_session.get_bind()))
        called = []
        monkeypatch.setattr(
            "app.core.channel.channel_engine.ChannelEngine."
            "run_channel_assignment",
            lambda *a, **kw: called.append(1))
        out = intent_tasks.run_channel_assignment_task.apply(
            args=[scenario["run"].id], task_id=result["task_id"]).get()
        assert out["status"] == "skipped"
        assert out["reason"] == "ASSIGNMENT_NOT_CLAIMABLE"
        assert called == []          # pipeline HİÇ koşmadı


class TestStaleReconciliation:
    """Codex 11. tur #3: hard-kill sonrası run sonsuza dek aktif kalmaz."""

    @LEGACY_DISPATCH_RETIRED
    def test_dead_parent_task_is_finalized(self, db_session, scenario,
                                           no_broker):
        from datetime import datetime, timedelta, timezone

        from app.core.screening.attempt_state import (
            ASSIGNMENT_LEASE_SECONDS,
            reconcile_stale_attempts,
        )

        result = enqueue_channel_assignment(db_session, scenario["run"],
                                            from_status="scored")
        task = db_session.query(TaskResult).filter_by(
            task_id=result["task_id"]).one()
        task.status = "running"
        task.created_at = datetime.now(timezone.utc) - timedelta(
            seconds=ASSIGNMENT_LEASE_SECONDS + 60)
        db_session.commit()
        assert reconcile_stale_attempts(db_session, scenario["run"].id) == 1
        db_session.expire_all()
        assert db_session.query(TaskResult).filter_by(
            task_id=result["task_id"]).one().status == "failed"
        assert db_session.query(
            ChannelAssignmentAttempt).one().status == "failed"

    @LEGACY_DISPATCH_RETIRED
    def test_dead_screening_lease_is_finalized(self, db_session, scenario,
                                               enabled, telemetry,
                                               no_broker):
        from datetime import datetime, timedelta, timezone

        from app.core.screening.attempt_state import reconcile_stale_attempts

        result, _pf = _approved_dispatch(db_session, scenario)
        task = db_session.query(TaskResult).filter_by(
            task_id=result["task_id"]).one()
        task.status = "completed"
        job = db_session.query(CorpusScreeningJob).one()
        job.status = "running"
        job.execution_lease_expires_at = datetime.now(timezone.utc) - \
            timedelta(seconds=5)
        db_session.commit()
        assert reconcile_stale_attempts(db_session, scenario["run"].id) == 1
        db_session.expire_all()
        job = db_session.query(CorpusScreeningJob).one()
        assert job.status == "fallback"
        assert job.error_code == "SCREENING_STALE_RECONCILED"

    @LEGACY_DISPATCH_RETIRED
    def test_stale_active_task_does_not_block_new_dispatch(self, db_session,
                                                           scenario,
                                                           no_broker):
        """Aktif-task guard'ı reconcile'dan SONRA değerlendirilir."""
        from datetime import datetime, timedelta, timezone

        from app.core.screening.attempt_state import ASSIGNMENT_LEASE_SECONDS

        first = enqueue_channel_assignment(db_session, scenario["run"],
                                           from_status="scored")
        task = db_session.query(TaskResult).filter_by(
            task_id=first["task_id"]).one()
        task.status = "running"
        task.created_at = datetime.now(timezone.utc) - timedelta(
            seconds=ASSIGNMENT_LEASE_SECONDS + 60)
        db_session.commit()
        # Run durumu ELLE düzeltilmez: reconciler `channel_assigning`
        # kilidini de çözmeli (Codex 12. tur #3)
        second = enqueue_channel_assignment(db_session, scenario["run"],
                                            from_status=scenario["run"].status)
        assert second["already_active"] is False
        assert second["task_id"] != first["task_id"]


class TestRelevanceFreeze:
    """Codex 11. tur #4: yenilenecek relevance ile tarama başlatılmaz."""

    def test_pending_recompute_is_refused(self, db_session, scenario,
                                          enabled, telemetry, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "ENABLE_RELEVANCE_RERANK", True)
        scenario["run"].relevance_anchor_version = None
        db_session.commit()
        with pytest.raises(PreflightError) as exc:
            _preflight(db_session, scenario)
        assert exc.value.code == "RELEVANCE_RECOMPUTE_PENDING"

    def test_fresh_anchor_passes(self, db_session, scenario, enabled,
                                 telemetry, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "ENABLE_RELEVANCE_RERANK", True)
        scenario["run"].relevance_anchor_version = \
            scenario["ws"].anchor_version or 1
        db_session.commit()
        assert _preflight(db_session, scenario)["universe_size"] == 9


class TestApprovalCapRequired:
    """Codex 11. tur #5: SHA doğru olsa da cap ZORUNLU."""

    @LEGACY_DISPATCH_RETIRED
    def test_missing_cap_is_refused(self, db_session, scenario, enabled,
                                    telemetry, no_broker):
        scenario["run"].screening_preference = "shadow"
        db_session.commit()
        pf = _preflight(db_session, scenario)
        with pytest.raises(ChannelAssignmentPreconditionError) as exc:
            enqueue_channel_assignment(
                db_session, scenario["run"], from_status="scored",
                approved_screening_mode=MODE_SHADOW,
                approved_preflight_sha256=pf["preflight_sha256"])
        assert exc.value.code == "PREFLIGHT_CAP_REQUIRED"


class TestChildEnqueueFailureClosesAttempt:
    """Codex 11. tur #6: tarama hiç başlamadıysa attempt açık kalmaz."""

    @LEGACY_DISPATCH_RETIRED
    def test_attempt_closes_when_parent_already_done(self, db_session,
                                                     scenario, enabled,
                                                     telemetry, monkeypatch,
                                                     no_broker):
        def _boom(*a, **kw):
            raise RuntimeError("broker yok")

        monkeypatch.setattr(
            "app.tasks.screening_tasks.run_corpus_screening_task.apply_async",
            _boom)
        # Parent'ı önceden tamamlanmış say: child hatası attempt'i kapatmalı
        monkeypatch.setattr(
            "app.core.screening.attempt_state._side_states",
            lambda db, attempt, **kw: (True, True, True, False))
        result, _pf = _approved_dispatch(db_session, scenario)
        assert result["screening_task_id"] is None
        db_session.expire_all()
        attempt = db_session.query(ChannelAssignmentAttempt).one()
        assert attempt.status == "failed"


class TestJobAuditFields:
    """Codex 11. tur #8: strateji SÜRÜMÜ de job denetimine yazılır."""

    @LEGACY_DISPATCH_RETIRED
    def test_strategy_version_is_persisted(self, db_session, scenario,
                                           enabled, telemetry, no_broker):
        _approved_dispatch(db_session, scenario)
        job = db_session.query(CorpusScreeningJob).one()
        assert job.dispatch_strategy_version == scenario["ws"].strategy_version
        assert job.strategy_fingerprint == "fp-approved-1"


class TestTaskStatusOrdering:
    """Codex 12. tur #2: terminal TaskResult GERİYE taşınmaz."""

    @LEGACY_DISPATCH_RETIRED
    def test_completed_task_is_not_reopened(self, db_session, scenario,
                                            no_broker):
        from app.tasks.task_status import mark_task_running_if_active

        result = enqueue_channel_assignment(db_session, scenario["run"],
                                            from_status="scored")
        task = db_session.query(TaskResult).filter_by(
            task_id=result["task_id"]).one()
        task.status = "completed"
        db_session.commit()
        assert mark_task_running_if_active(result["task_id"]) is False
        db_session.expire_all()
        assert db_session.query(TaskResult).filter_by(
            task_id=result["task_id"]).one().status == "completed"

    @LEGACY_DISPATCH_RETIRED
    def test_pending_task_becomes_running(self, db_session, scenario,
                                          no_broker):
        from app.tasks.task_status import mark_task_running_if_active

        result = enqueue_channel_assignment(db_session, scenario["run"],
                                            from_status="scored")
        assert mark_task_running_if_active(result["task_id"]) is True
        db_session.expire_all()
        assert db_session.query(TaskResult).filter_by(
            task_id=result["task_id"]).one().status == "running"

    @LEGACY_DISPATCH_RETIRED
    def test_duplicate_delivery_leaves_task_terminal(self, db_session,
                                                     scenario, no_broker,
                                                     monkeypatch):
        """Mükerrer teslim tamamlanmış task'ı `running` YAPAMAZ."""
        from app.tasks import intent_tasks

        result = enqueue_channel_assignment(db_session, scenario["run"],
                                            from_status="scored")
        task = db_session.query(TaskResult).filter_by(
            task_id=result["task_id"]).one()
        task.status = "completed"
        attempt = db_session.query(ChannelAssignmentAttempt).one()
        attempt.status = "completed"
        db_session.commit()
        monkeypatch.setattr(
            "app.database.connection.SessionLocal",
            lambda: db_session.__class__(bind=db_session.get_bind()))
        out = intent_tasks.run_channel_assignment_task.apply(
            args=[scenario["run"].id], task_id=result["task_id"]).get()
        assert out["status"] == "skipped"
        db_session.expire_all()
        assert db_session.query(TaskResult).filter_by(
            task_id=result["task_id"]).one().status == "completed"


class TestStaleRunRecovery:
    """Codex 12. tur #3/#5: run durumu ve child kaydı da kurtarılır."""

    @LEGACY_DISPATCH_RETIRED
    def test_run_status_is_restored_on_stale_finalization(self, db_session,
                                                          scenario,
                                                          no_broker):
        from datetime import datetime, timedelta, timezone

        from app.core.screening.attempt_state import (
            ASSIGNMENT_LEASE_SECONDS,
            reconcile_stale_attempts,
        )

        first = enqueue_channel_assignment(db_session, scenario["run"],
                                           from_status="scored")
        assert scenario["run"].status == "channel_assigning"
        task = db_session.query(TaskResult).filter_by(
            task_id=first["task_id"]).one()
        task.status = "running"
        task.created_at = datetime.now(timezone.utc) - timedelta(
            seconds=ASSIGNMENT_LEASE_SECONDS + 60)
        db_session.commit()
        assert reconcile_stale_attempts(db_session, scenario["run"].id) == 1
        db_session.expire_all()
        run = db_session.get(type(scenario["run"]), scenario["run"].id)
        # Run ELLE düzeltilmeden atama öncesi duruma dönmeli
        assert run.status == "scored"
        second = enqueue_channel_assignment(db_session, run,
                                            from_status=run.status)
        assert second["task_id"] != first["task_id"]

    @LEGACY_DISPATCH_RETIRED
    def test_stale_child_marks_its_task_terminal(self, db_session, scenario,
                                                 enabled, telemetry,
                                                 no_broker):
        from datetime import datetime, timedelta, timezone

        from app.core.screening.attempt_state import reconcile_stale_attempts

        result, _pf = _approved_dispatch(db_session, scenario)
        child_id = result["screening_task_id"]
        child = db_session.query(TaskResult).filter_by(
            task_id=child_id).one()
        child.status = "running"
        job = db_session.query(CorpusScreeningJob).one()
        job.status = "running"
        job.execution_lease_expires_at = datetime.now(timezone.utc) - \
            timedelta(seconds=5)
        db_session.commit()
        reconcile_stale_attempts(db_session, scenario["run"].id)
        db_session.expire_all()
        child = db_session.query(TaskResult).filter_by(
            task_id=child_id).one()
        assert child.status == "completed"        # progress kaydı asılı kalmaz
        assert child.result_data["reason"] == "SCREENING_STALE_RECONCILED"


class TestAdminCapGuard:
    """Codex 12. tur #4: mesaj eyleme dönük, sınır fail-closed."""

    def test_error_reports_required_amount(self, db_session, scenario,
                                           enabled, telemetry, monkeypatch):
        monkeypatch.setattr(enabled, "CORPUS_SCREENING_MAX_APPROVED_USD",
                            0.000001)
        with pytest.raises(PreflightError) as exc:
            _preflight(db_session, scenario)
        assert exc.value.code == "SCREENING_CAP_EXCEEDS_LIMIT"
        assert "CORPUS_SCREENING_MAX_APPROVED_USD en az" in exc.value.message
        assert "tek geçiş" in exc.value.message

    def test_single_retry_priced_from_single_keyword_ceiling(self, db_session,
                                                             scenario,
                                                             enabled,
                                                             telemetry):
        """Tekil retry ortalama batch maliyetiyle DEĞİL, gerçek tavanla."""
        pf = _preflight(db_session, scenario)
        topology = pf["screening_retry_topology"]
        assert topology["single_retry_scope"] == "view"
        # Tek geçiş < hard cap ve worst-case istek sayısı topolojiden geliyor
        assert (pf["screening_single_pass_ceiling_usd"]
                < pf["screening_hard_cap_usd"])
        assert pf["screening_worst_case_requests"] >= (
            pf["planned_screening_requests"] * topology["attempts_per_batch"])


class TestChildTaskTerminalProtection:
    """Codex 13. tur #1: gecikmiş mükerrer child teslimi kaydı BOZAMAZ."""

    @LEGACY_DISPATCH_RETIRED
    def test_late_delivery_on_completed_job_keeps_child_terminal(
            self, db_session, scenario, enabled, telemetry, no_broker,
            monkeypatch):
        from app.tasks import screening_tasks

        result, _pf = _approved_dispatch(db_session, scenario)
        child_id = result["screening_task_id"]
        job = db_session.query(CorpusScreeningJob).one()
        # İlk çalıştırma bitti: job terminal, child kaydı completed
        job.status = "completed"
        child = db_session.query(TaskResult).filter_by(
            task_id=child_id).one()
        child.status = "completed"
        child.progress = 100
        child.result_data = {"status": "completed", "decisions": 27}
        db_session.commit()

        monkeypatch.setattr(
            "app.database.connection.SessionLocal",
            lambda: db_session.__class__(bind=db_session.get_bind()))
        # Gecikmiş ikinci teslim: claim terminal job'ı reddeder
        out = screening_tasks.run_corpus_screening_task.apply(
            kwargs={"job_id": job.id,
                    "attempt_id": result["assignment_attempt_id"],
                    "expected_screening_cap_usd": "0.50",
                    "expected_downstream_cap_usd": "4.00"},
            task_id=child_id).get()
        assert out["status"] in ("failed", "fallback")
        db_session.expire_all()
        child = db_session.query(TaskResult).filter_by(
            task_id=child_id).one()
        # Tamamlanmış child kaydı ve sonucu KORUNDU
        assert child.status == "completed"
        assert child.result_data["decisions"] == 27
        assert db_session.query(CorpusScreeningJob).one().status == \
            "completed"

    @LEGACY_DISPATCH_RETIRED
    def test_mark_child_task_refuses_terminal_row(self, db_session, scenario,
                                                  enabled, telemetry,
                                                  no_broker):
        from app.tasks.screening_tasks import _mark_child_task

        result, _pf = _approved_dispatch(db_session, scenario)
        child_id = result["screening_task_id"]
        child = db_session.query(TaskResult).filter_by(
            task_id=child_id).one()
        child.status = "failed"
        db_session.commit()
        assert _mark_child_task(db_session, child_id, status="running",
                                progress=5) is False
        db_session.expire_all()
        assert db_session.query(TaskResult).filter_by(
            task_id=child_id).one().status == "failed"


class TestApprovalIsScreeningOnly:
    """Codex 15. tur #1: onay YALNIZ uygulanan screening cap'ine bağlanır."""

    @LEGACY_DISPATCH_RETIRED
    def test_approval_binds_to_enforced_cap_not_exposure(self, db_session,
                                                         scenario, enabled,
                                                         telemetry,
                                                         no_broker):
        scenario["run"].screening_preference = "shadow"
        db_session.commit()
        pf = _preflight(db_session, scenario)
        # Toplam maruziyet onay alanı DEĞİLDİR: onunla gönderilirse reddedilir
        assert pf["combined_exposure_usd"] > pf["enforced_hard_cap_usd"]
        with pytest.raises(ChannelAssignmentPreconditionError) as exc:
            enqueue_channel_assignment(
                db_session, scenario["run"], from_status="scored",
                approved_screening_mode=MODE_SHADOW,
                approved_preflight_sha256=pf["preflight_sha256"],
                approved_screening_hard_cap_usd=pf["combined_exposure_usd"])
        assert exc.value.code == "PREFLIGHT_CAP_MISMATCH"

    @LEGACY_DISPATCH_RETIRED
    def test_enforced_cap_is_accepted(self, db_session, scenario, enabled,
                                      telemetry, no_broker):
        result, pf = _approved_dispatch(db_session, scenario)
        attempt = db_session.query(ChannelAssignmentAttempt).one()
        assert float(attempt.approved_screening_cap_usd) == \
            pf["enforced_hard_cap_usd"]
        assert result["screening_mode"] == MODE_SHADOW


class TestDownstreamGuardScope:
    """Downstream ledger'a bağlı her mod yönetici sınırına uyar."""

    def test_shadow_respects_downstream_admin_limit(self, db_session,
                                                    scenario, enabled,
                                                    telemetry, monkeypatch):
        monkeypatch.setattr(enabled, "CORPUS_DOWNSTREAM_MAX_APPROVED_USD",
                            0.000001)
        with pytest.raises(PreflightError) as exc:
            _preflight(db_session, scenario)
        assert exc.value.code == "DOWNSTREAM_ESTIMATE_EXCEEDS_LIMIT"

    def test_assistive_still_respects_downstream_limit(self, db_session,
                                                       scenario, enabled,
                                                       telemetry,
                                                       monkeypatch):
        monkeypatch.setattr(enabled, "CORPUS_DOWNSTREAM_MAX_APPROVED_USD",
                            0.000001)
        with pytest.raises(PreflightError) as exc:
            _preflight(db_session, scenario, mode="assistive")
        assert exc.value.code == "DOWNSTREAM_ESTIMATE_EXCEEDS_LIMIT"


class TestRoughExpectedLabel:
    """Codex 15. tur #3: karma telemetri KABA tahmindir."""

    def test_expected_is_labelled_rough_and_out_of_sha(self, db_session,
                                                       scenario, enabled,
                                                       telemetry):
        pf = _preflight(db_session, scenario)
        assert pf["screening_expected_rough_usd"] > 0
        assert pf["screening_expected_samples"] == 5
        assert "KABA TAHMİN" in pf["screening_expected_basis"]
        # KABA beklenti, rezervasyon tavanının ALTINDA olmalı
        assert (pf["screening_expected_rough_usd"]
                < pf["screening_single_pass_ceiling_usd"])
        # Beklenti onay hash'ine GİRMEZ (telemetri büyüdükçe onay bozulmasın)
        assert preflight_sha_for(pf) == pf["preflight_sha256"]


class TestApprovalShaScope:
    """Codex 16. tur: bilgi amaçlı tahminler onay hash'ine GİRMEZ."""

    def test_new_gemini_telemetry_rebinds_shadow_downstream_cap(
            self, db_session, scenario, enabled, telemetry):
        from app.config import settings

        before = _preflight(db_session, scenario)
        # Yeni downstream telemetrisi tahmini DEĞİŞTİRİR
        for i in range(20, 30):
            db_session.add(AiUsageEvent(
                scoring_run_id=scenario["run"].id,
                brand_profile_id=scenario["ws"].id,
                request_id=f"intent-late-{i}", attempt=1, stage="intent",
                model=settings.GEMINI_MODEL, prompt_tokens=9000 + i,
                candidates_tokens=4000 + i, thoughts_tokens=500,
                total_tokens=13500 + i,
                price_snapshot={"input_per_m": 0.3, "output_per_m": 2.5}))
        db_session.commit()
        after = _preflight(db_session, scenario)
        assert after["downstream_hard_cap_usd"] != \
            before["downstream_hard_cap_usd"]          # tahmin değişti
        assert after["combined_exposure_usd"] != before["combined_exposure_usd"]
        # Downstream artık ledger'da uygulandığı için değişen cap yeni kimlik
        # gerektirir; eski onayla farklı bir maliyet zarfı çalıştırılamaz.
        assert after["preflight_sha256"] != before["preflight_sha256"]

    def test_assistive_sha_includes_downstream_when_enforced(
            self, db_session, scenario, enabled, telemetry):
        pf = _preflight(db_session, scenario, mode="assistive")
        assert pf["enforced_scopes"] == ["screening", "downstream"]
        assert pf["downstream_enforced"] is True
        assert preflight_sha_for(pf) == pf["preflight_sha256"]
        drifted = {**pf, "downstream_hard_cap_usd": 99.0}
        assert preflight_sha_for(drifted) != pf["preflight_sha256"]


class TestCeleryBrokerBinding:
    """Canlı smoke testi bulgusu: task PROJE app'ine bağlı olmalı.

    `shared_task` çağrı anında `current_app`e bağlanır; FastAPI/uvicorn
    process'inde `-A` verilmediği için bu DEFAULT app olur (broker=None →
    amqp://localhost → ECONNREFUSED) ve shadow çocuğu hiç kuyruğa girmez.
    Worker'da `-A` verildiği için hata orada GÖRÜNMEZ.
    """

    def test_screening_task_uses_project_broker(self):
        from app.config import settings
        from app.tasks.celery_app import celery_app
        from app.tasks.screening_tasks import run_corpus_screening_task

        broker = run_corpus_screening_task.app.conf.broker_url
        assert broker, "task broker'ı YOK — default app'e bağlanmış"
        assert broker == celery_app.conf.broker_url == settings.REDIS_URL

    def test_all_dispatched_tasks_share_one_app(self):
        from app.tasks.celery_app import celery_app
        from app.tasks.intent_tasks import run_channel_assignment_task
        from app.tasks.screening_tasks import run_corpus_screening_task

        for task in (run_channel_assignment_task, run_corpus_screening_task):
            assert task.app.main == celery_app.main
            assert task.name in celery_app.tasks


class TestPoolScreeningProvenance:
    """Codex 17. tur #1: başarılı finalize künyeyi AÇIKÇA yazar."""

    def test_shadow_pool_is_marked_off(self, db_session, scenario):
        from app.core.channel.channel_engine import ChannelEngine
        from app.database.models import ChannelAssignmentAttempt

        run = scenario["run"]
        db_session.add(ChannelAssignmentAttempt(
            parent_task_id="prov-shadow", scoring_run_id=run.id,
            brand_profile_id=scenario["ws"].id, status="running",
            phase="screening", screening_mode="shadow",
            applied_candidate_multiplier=1,
            counterfactual_target_multiplier=3,
            context_sha256="a" * 64))
        db_session.commit()
        engine = ChannelEngine(db_session, ai_service=None)
        engine._finalize_screening_provenance(run)
        db_session.commit()
        # Shadow havuzu BASELINE'dır: mode 'off', bağlam SHA'sı YOK
        assert run.channel_pool_screening_mode == "off"
        assert run.channel_pool_screening_context_sha256 is None

    def test_assistive_pool_records_context(self, db_session, scenario):
        from app.core.channel.channel_engine import ChannelEngine
        from app.database.models import ChannelAssignmentAttempt

        run = scenario["run"]
        db_session.add(ChannelAssignmentAttempt(
            parent_task_id="prov-assistive", scoring_run_id=run.id,
            brand_profile_id=scenario["ws"].id, status="running",
            phase="screening", screening_mode="assistive",
            applied_candidate_multiplier=3,
            counterfactual_target_multiplier=3,
            context_sha256="b" * 64))
        db_session.commit()
        engine = ChannelEngine(db_session, ai_service=None)
        # Kunye KANIT ister: uygulama sonucu verilmezse 'off' (fail-closed)
        engine._finalize_screening_provenance(run)
        db_session.commit()
        assert run.channel_pool_screening_mode == "off"
        assert run.channel_pool_screening_context_sha256 is None
        # Gercek uygulama kaniti ile 'assistive'
        engine._finalize_screening_provenance(
            run, screening_apply={"applied": True,
                                  "materialization_identity_sha256": "c" * 64})
        db_session.commit()
        assert run.channel_pool_screening_mode == "assistive"
        assert run.channel_pool_screening_context_sha256 == "b" * 64

    def test_no_attempt_defaults_to_off(self, db_session, scenario):
        from app.core.channel.channel_engine import ChannelEngine

        run = scenario["run"]
        engine = ChannelEngine(db_session, ai_service=None)
        engine._finalize_screening_provenance(run)
        db_session.commit()
        assert run.channel_pool_screening_mode == "off"


class TestScreeningStatusEndpoint:
    """UI progress/özet ucu — salt okunur, hiçbir şey başlatmaz."""

    def test_no_job_returns_exists_false(self, client, scenario):
        resp = client.get(
            f"/api/v1/channels/runs/{scenario['run'].id}/screening",
            params={"brand_profile_id": scenario["ws"].id})
        assert resp.status_code == 200
        assert resp.json() == {"exists": False,
                               "scoring_run_id": scenario["run"].id}

    @LEGACY_DISPATCH_RETIRED
    def test_reports_job_state_and_live_pool_safety(self, client, db_session,
                                                    scenario, enabled,
                                                    telemetry, no_broker):
        _approved_dispatch(db_session, scenario)
        job = db_session.query(CorpusScreeningJob).one()
        job.status = "completed"
        job.actual_requests = 262
        job.cost_usd = Decimal("0.036041")
        job.ceiling_charges = 0
        job.coverage_resolved = 9
        job.unresolved_count = 0
        job.contract_violations = 0
        db_session.commit()
        resp = client.get(
            f"/api/v1/channels/runs/{scenario['run'].id}/screening",
            params={"brand_profile_id": scenario["ws"].id})
        assert resp.status_code == 200
        body = resp.json()
        assert body["exists"] is True
        assert body["status"] == "completed"
        assert body["screening_mode"] == "shadow"
        assert body["cost_usd"] == pytest.approx(0.036041)
        assert body["provider_calls"] == 262
        assert body["ceiling_charges"] == 0
        assert body["universe_size"] == 9
        # Shadow: canlı havuza UYGULANMAZ (çarpan 1)
        assert body["applied_to_live_pool"] is False

    def test_cross_workspace_is_404(self, client, scenario, make_workspace):
        other = make_workspace("Yabancı WS 2")
        resp = client.get(
            f"/api/v1/channels/runs/{scenario['run'].id}/screening",
            params={"brand_profile_id": other.id})
        assert resp.status_code == 404


class TestPreflightRelevanceBinding:
    """Codex 20. tur #1: katsayı SHA bileşenidir — preflight ve dispatch
    AYNI değeri kullanmalı, yoksa onay sonrası 409 patlar."""

    def test_endpoint_uses_given_override(self, client, db_session, scenario,
                                          enabled, telemetry):
        scenario["run"].screening_preference = "shadow"
        db_session.commit()
        base = client.get(
            f"/api/v1/channels/runs/{scenario['run'].id}/assignment-preflight",
            params={"brand_profile_id": scenario["ws"].id, "mode": "shadow"})
        with_override = client.get(
            f"/api/v1/channels/runs/{scenario['run'].id}/assignment-preflight",
            params={"brand_profile_id": scenario["ws"].id, "mode": "shadow",
                    "relevance_override": 1.7})
        assert base.status_code == with_override.status_code == 200
        # Katsayı SHA'yı DEĞİŞTİRİR
        assert (with_override.json()["preflight_sha256"]
                != base.json()["preflight_sha256"])

    @LEGACY_DISPATCH_RETIRED
    def test_matching_coefficient_dispatches(self, client, db_session,
                                             scenario, enabled, telemetry,
                                             no_broker):
        scenario["run"].screening_preference = "shadow"
        db_session.commit()
        pf = client.get(
            f"/api/v1/channels/runs/{scenario['run'].id}/assignment-preflight",
            params={"brand_profile_id": scenario["ws"].id, "mode": "shadow",
                    "relevance_override": 1.7}).json()
        resp = client.post(
            f"/api/v1/channels/runs/{scenario['run'].id}/assign",
            params={"brand_profile_id": scenario["ws"].id},
            json={"relevance_override": 1.7,
                  "screening_mode": "shadow",
                  "preflight_sha256": pf["preflight_sha256"],
                  "approved_screening_hard_cap_usd":
                      pf["enforced_hard_cap_usd"]})
        assert resp.status_code == 200, resp.text
        assert resp.json()["screening_mode"] == "shadow"
        assert resp.json()["screening_job_id"] is not None

    @LEGACY_DISPATCH_RETIRED
    def test_changed_coefficient_is_typed_409(self, client, db_session,
                                              scenario, enabled, telemetry,
                                              no_broker):
        scenario["run"].screening_preference = "shadow"
        db_session.commit()
        pf = client.get(
            f"/api/v1/channels/runs/{scenario['run'].id}/assignment-preflight",
            params={"brand_profile_id": scenario["ws"].id, "mode": "shadow",
                    "relevance_override": 1.7}).json()
        # Kullanıcı onaydan SONRA katsayıyı değiştirdi
        resp = client.post(
            f"/api/v1/channels/runs/{scenario['run'].id}/assign",
            params={"brand_profile_id": scenario["ws"].id},
            json={"relevance_override": 1.0,
                  "screening_mode": "shadow",
                  "preflight_sha256": pf["preflight_sha256"],
                  "approved_screening_hard_cap_usd":
                      pf["enforced_hard_cap_usd"]})
        assert resp.status_code == 409
        assert resp.json()["detail"]["code"] == "PREFLIGHT_MISMATCH"
        assert db_session.query(CorpusScreeningJob).count() == 0


class TestAppliedToLivePoolIsMeasured:
    """Codex 20. tur #4: varsayım değil ÖLÇÜM."""

    @LEGACY_DISPATCH_RETIRED
    def test_uses_real_applied_rows_not_multiplier(self, client, db_session,
                                                   scenario, enabled,
                                                   telemetry, no_broker):
        from app.database.models import CorpusCandidateSelection

        _approved_dispatch(db_session, scenario)
        job = db_session.query(CorpusScreeningJob).one()
        attempt = db_session.query(ChannelAssignmentAttempt).one()
        body = client.get(
            f"/api/v1/channels/runs/{scenario['run'].id}/screening",
            params={"brand_profile_id": scenario["ws"].id}).json()
        assert body["applied_to_live_pool"] is False
        assert body["applied_selection_rows"] == 0
        # Uygulanmış satır varsa çarpan 1 olsa BİLE True olmalı
        db_session.add(CorpusCandidateSelection(
            assignment_attempt_id=attempt.id,
            scoring_run_id=scenario["run"].id, screening_job_id=job.id,
            keyword_id=scenario["run"].id, channel="ADS",
            origin_source="screening", materialization_action="initial",
            is_applied=True))
        db_session.commit()
        body = client.get(
            f"/api/v1/channels/runs/{scenario['run'].id}/screening",
            params={"brand_profile_id": scenario["ws"].id}).json()
        assert body["applied_to_live_pool"] is True
        assert body["applied_selection_rows"] == 1
        assert body["applied_candidate_multiplier"] == 1


class TestPreflightAlwaysReportsFlag:
    """Canlı UI doğrulaması: `screening_enabled` HER dalda dönmeli.

    Yalnız off dalında dönerse, tercihi zaten `shadow` olan koşularda UI
    mod seçicisini HİÇ göstermez (kullanıcı arayüzde hiçbir şey göremez).
    """

    def test_off_branch_reports_flag(self, client, scenario, enabled,
                                     telemetry):
        resp = client.get(
            f"/api/v1/channels/runs/{scenario['run'].id}/assignment-preflight",
            params={"brand_profile_id": scenario["ws"].id})
        assert resp.status_code == 200
        body = resp.json()
        assert body["requires_approval"] is False
        assert body["screening_enabled"] is True

    def test_approval_branch_reports_flag(self, client, db_session, scenario,
                                          enabled, telemetry):
        scenario["run"].screening_preference = "shadow"
        db_session.commit()
        resp = client.get(
            f"/api/v1/channels/runs/{scenario['run'].id}/assignment-preflight",
            params={"brand_profile_id": scenario["ws"].id})
        assert resp.status_code == 200
        body = resp.json()
        assert body["requires_approval"] is True
        assert body["screening_enabled"] is True      # ESKİDEN YOKTU
        assert body["screening_mode"] == "shadow"

    def test_technical_ui_flag_gates_visibility(self, client, db_session,
                                                scenario, enabled, telemetry,
                                                monkeypatch):
        """Codex 22. tur #5: tarama AÇIK ama teknik UI KAPALIYSA görünmez.

        Yeni sözleşmede tarama sunucu kontrollüdür; eski selector/modal
        yalnız iç hata ayıklama bayrağıyla açılır.
        """
        monkeypatch.setattr(enabled, "CORPUS_SCREENING_TECHNICAL_UI", False)
        scenario["run"].screening_preference = "shadow"
        db_session.commit()
        body = client.get(
            f"/api/v1/channels/runs/{scenario['run'].id}/assignment-preflight",
            params={"brand_profile_id": scenario["ws"].id}).json()
        assert body["screening_enabled"] is False     # UI GÖRÜNMEZ
        assert body["requires_approval"] is True      # sunucu içi paket VAR

    def test_flag_off_reports_false(self, client, db_session, scenario):
        scenario["run"].screening_preference = "shadow"
        db_session.commit()
        resp = client.get(
            f"/api/v1/channels/runs/{scenario['run'].id}/assignment-preflight",
            params={"brand_profile_id": scenario["ws"].id})
        body = resp.json()
        assert body["screening_enabled"] is False
        assert body["requires_approval"] is False


class TestScreeningReuse:
    """Canlı UI koşusunun yakaladığı açık: AYNI kimlikte ÜCRETSİZ reuse.

    plan §5.6: "Aynı screening identity'de provider kararı ÜCRETSİZ reuse
    edilir." Uygulanmayınca sistem yeniden tarayıp parayı harcıyor ve
    finalize'da `uq_screening_job_completed_identity`e çarpıyordu.
    """

    def _complete_first(self, db_session, scenario):
        result, pf = _approved_dispatch(db_session, scenario)
        job = db_session.query(CorpusScreeningJob).one()
        job.status = "completed"
        job.actual_requests = 260
        job.cost_usd = Decimal("0.0255")
        db_session.commit()
        # İlk attempt kapatılır ki ikinci dispatch açılabilsin
        attempt = db_session.query(ChannelAssignmentAttempt).one()
        attempt.status = "completed"
        task = db_session.query(TaskResult).filter_by(
            task_id=result["task_id"]).one()
        task.status = "completed"
        scenario["run"].status = "scored"
        db_session.commit()
        return job, pf

    @LEGACY_DISPATCH_RETIRED
    def test_second_dispatch_reuses_and_spends_nothing(self, db_session,
                                                       scenario, enabled,
                                                       telemetry, no_broker):
        job, _pf = self._complete_first(db_session, scenario)
        no_broker["screening"].clear()
        no_broker["assignment"].clear()
        result, _ = _approved_dispatch(db_session, scenario)
        # YENİ job AÇILMAZ, çocuk task KUYRUĞA VERİLMEZ
        assert db_session.query(CorpusScreeningJob).count() == 1
        assert no_broker["screening"] == []
        assert result["screening_reused"] is True
        assert result["screening_job_id"] == job.id
        assert result["screening_task_id"] is None
        # Atama normal şekilde başlar
        assert len(no_broker["assignment"]) == 1

    @LEGACY_DISPATCH_RETIRED
    def test_reused_attempt_is_linked_to_existing_job(self, db_session,
                                                      scenario, enabled,
                                                      telemetry, no_broker):
        job, _pf = self._complete_first(db_session, scenario)
        _approved_dispatch(db_session, scenario)
        attempts = (db_session.query(ChannelAssignmentAttempt)
                    .order_by(ChannelAssignmentAttempt.id).all())
        assert len(attempts) == 2
        assert attempts[-1].screening_job_id == job.id

    def test_identity_conflict_is_typed_not_unexpected(self):
        from app.core.screening.production_runner import (
            ScreeningIdentityConflict,
        )
        from app.tasks.screening_tasks import _status_for

        exc = ScreeningIdentityConflict("x")
        assert exc.error_code == "SCREENING_IDENTITY_CONFLICT"
        # Kararlar zaten var: baseline sürer -> fallback (failed DEĞİL)
        assert _status_for(exc) == "fallback"


class TestScreeningStatusUsesAttemptJob:
    """Canlı koşuda görüldü: reuse başarılıyken UI 'failed' gösteriyordu.

    Endpoint run'ın EN YENİ job'ını değil, SON attempt'in BAĞLI OLDUĞU
    job'ı raporlamalı — reuse yolunda koşu eski (completed) job'ı kullanır.
    """

    @LEGACY_DISPATCH_RETIRED
    def test_reported_job_follows_attempt_binding(self, client, db_session,
                                                  scenario, enabled,
                                                  telemetry, no_broker):
        # 1) İlk koşu tamamlanır
        result, _pf = _approved_dispatch(db_session, scenario)
        good = db_session.query(CorpusScreeningJob).one()
        good.status = "completed"
        good.cost_usd = Decimal("0.0360")
        good.coverage_resolved = 9
        good.unresolved_count = 0
        attempt1 = db_session.query(ChannelAssignmentAttempt).one()
        attempt1.status = "completed"
        db_session.query(TaskResult).filter_by(
            task_id=result["task_id"]).one().status = "completed"
        scenario["run"].status = "scored"
        db_session.commit()

        # 2) DAHA YENİ ama BAŞARISIZ bir job (eski davranışta UI bunu
        #    gösteriyordu) + reuse ile açılan yeni attempt
        failed = CorpusScreeningJob(
            scoring_run_id=scenario["run"].id,
            brand_profile_id=scenario["ws"].id, status="failed",
            error_code="UNEXPECTED", provider=good.provider,
            model=good.model, prompt_version=good.prompt_version,
            temperature=good.temperature, batch_size=good.batch_size,
            view_salts=list(good.view_salts),
            applied_screening_channels=["ADS", "SEO"],
            screening_input_identity_sha256="c" * 64,
            universe_sha256=good.universe_sha256,
            context_sha256=good.context_sha256,
            input_snapshot=good.input_snapshot,
            screening_context=good.screening_context)
        db_session.add(failed)
        db_session.flush()
        db_session.add(ChannelAssignmentAttempt(
            parent_task_id="reuse-parent", scoring_run_id=scenario["run"].id,
            brand_profile_id=scenario["ws"].id, status="completed",
            phase="completed", screening_mode="shadow",
            applied_candidate_multiplier=1,
            counterfactual_target_multiplier=3,
            screening_job_id=good.id))          # REUSE: eski completed job
        db_session.commit()

        body = client.get(
            f"/api/v1/channels/runs/{scenario['run'].id}/screening",
            params={"brand_profile_id": scenario["ws"].id}).json()
        # Başarısız job DAHA YENİ ama koşunun kullandığı job bu DEĞİL
        assert body["screening_job_id"] == good.id
        assert body["status"] == "completed"
        assert body["error_code"] is None
        assert body["cost_usd"] == pytest.approx(0.036)
