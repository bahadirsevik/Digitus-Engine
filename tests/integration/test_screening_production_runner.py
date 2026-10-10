# -*- coding: utf-8 -*-
"""Üretim screening runner'ı: checkpoint/resume + ledger sırası (plan §5.4).

Codex zorunlu sırası burada kilitlenir:
  checkpoint → rezervasyon commit → provider çağrısı → settle/ceiling →
  doğrulanmış payload'lı checkpoint (atomik) → kapılar → kararlar.

Codex 9. tur senaryoları da burada: rezervasyon birimi GERÇEK HTTP
denemesi, settle-sonrası-checkpoint-yok (ücret kayıp) tipli fallback,
mükerrer task teslimi/sahiplik reddi, resume istatistiğinin DB'den
yeniden üretilmesi, dondurulmuş mühürlerin harcamadan önce doğrulanması,
atomik lease semaforu ve ayrı kuyruk worker'ının compose'da bulunması.

Provider AĞA ÇIKMAZ: sahte sağlayıcı deterministik sonuç döndürür.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
import sqlalchemy as sa

from app.core.screening.context import screening_context_from_snapshot
from app.core.screening.identity import (
    screening_runner_contract,
    universe_sha256,
)
from app.core.screening.inflight import (
    InflightBusy,
    InflightUnavailable,
    NullInflightLimiter,
    RedisInflightLimiter,
)
from app.core.screening.production_runner import (
    RunnerContractDrift,
    ScreeningJobNotClaimable,
    ScreeningResultLost,
    ScreeningRunError,
    batch_hash,
    finalize_job,
    request_id_for,
    run_screening_job,
)
from app.core.telemetry.ai_cost_budget import (
    BUDGET_SCREENING,
    STATE_CEILING,
    STATE_SETTLED,
    AiCostLedger,
)
from app.database.models import (
    AiCostReservation,
    ChannelAssignmentAttempt,
    CorpusScreeningBatchCheckpoint,
    CorpusScreeningDecision,
    CorpusScreeningJob,
    Keyword,
    KeywordScore,
    ScoringRun,
)

UNIVERSE_SIZE = 7
CAPS = {"screening": Decimal("0.50"), "downstream": Decimal("4.00")}
CONTEXT_FIELDS = {"product_definition": "Borsa analiz platformu",
                  "content_strategy": "Yatırımcı içerikleri",
                  "social_mode": "hype", "target_audience": "-"}
SALTS = ["scr-view-a", "scr-view-b"]
REPO_ROOT = Path(__file__).resolve().parents[2]


class FakeProvider:
    """`run_screening`'in beklediği sınır — ağa çıkmaz."""

    provider_name = "fake"

    def __init__(self, *, model="deepseek-v4-flash", fail=False,
                 usage_cost=0.001, unresolved_ids=(), calls=None,
                 transient_once=False):
        self.model = model
        self.fail = fail
        self.usage_cost = usage_cost
        self.unresolved_ids = set(unresolved_ids)
        self.calls = calls if calls is not None else []
        self.transient_once = transient_once
        self._call_counts = {}
        self.closed = 0

    def prompt_size_bytes(self, context, batch):
        return 1000

    def screen_batch(self, context, batch):
        from app.core.screening.contract import ScreeningItem
        from app.core.screening.providers import (
            ScreeningBatchResult,
            ScreeningProviderError,
            ScreeningUsage,
        )

        ids = tuple(k["id"] for k in batch)
        self.calls.append(list(ids))
        if self.fail:
            raise RuntimeError("provider patladı")
        seen = self._call_counts.get(ids, 0)
        self._call_counts[ids] = seen + 1
        if self.transient_once and seen % 2 == 0:
            # Her mantıksal batch'in İLK gerçek HTTP denemesi geçici
            # hatayla düşer; `run_screening` yeniden dener → İKİNCİ istek.
            # (Aynı id demeti iki GÖRÜNÜMDE de geldiği için sayaç ikili
            # ritimle çalışır; tek bir "görüldü" kümesi yetmez.)
            raise ScreeningProviderError("geçici hata", usage=None)
        items = []
        for k in batch:
            if k["id"] in self.unresolved_ids:
                # Sağlayıcı bu id'yi HİÇ döndürmez → runner eksik-ID
                # retry zincirini işletir, sonunda unresolved fallback
                continue
            items.append(ScreeningItem(
                id=k["id"], ads_fit=2, seo_fit=1, social_fit=0,
                reason_codes={"ads": "COMMERCIAL_INTENT",
                              "seo": "TOPIC_MATCH",
                              "social": "NO_SOCIAL_ANGLE"}))
        usage = (None if self.usage_cost is None
                 else ScreeningUsage(prompt_tokens=100,
                                     completion_tokens=50,
                                     total_tokens=150,
                                     cache_hit_tokens=0,
                                     cache_miss_tokens=100,
                                     latency_ms=10))
        return ScreeningBatchResult(items=items, usage=usage,
                                    raw_text="{}")

    def close(self):
        self.closed += 1


@pytest.fixture
def session_factory(db_session):
    return sa.orm.sessionmaker(bind=db_session.get_bind(),
                               expire_on_commit=False)


@pytest.fixture
def scenario(db_session, make_workspace):
    ws = make_workspace()
    run = ScoringRun(run_name="scr-runner", brand_profile_id=ws.id,
                     total_keywords=UNIVERSE_SIZE, ads_capacity=5,
                     seo_capacity=5, social_capacity=5, status="scored")
    db_session.add(run)
    db_session.commit()
    rows = []
    for i in range(UNIVERSE_SIZE):
        kw = Keyword(keyword=f"anahtar {i}", monthly_volume=100 + i)
        db_session.add(kw)
        db_session.flush()
        score = KeywordScore(scoring_run_id=run.id, keyword_id=kw.id,
                             ads_score=1, seo_score=1, social_score=1)
        db_session.add(score)
        db_session.flush()
        rows.append({"keyword_id": kw.id, "keyword_score_id": score.id,
                     "keyword": kw.keyword})
    attempt = ChannelAssignmentAttempt(
        parent_task_id=f"scr-{run.id}", scoring_run_id=run.id,
        brand_profile_id=ws.id, status="running", phase="screening",
        screening_mode="assistive", applied_candidate_multiplier=3,
        counterfactual_target_multiplier=3,
        approved_screening_cap_usd=CAPS["screening"],
        approved_downstream_cap_usd=CAPS["downstream"])
    db_session.add(attempt)
    db_session.flush()
    # Mühürler GERÇEK üreticilerden hesaplanır: runner harcamadan önce
    # bunları yeniden üretip karşılaştırır (Codex 9. tur #5)
    context = screening_context_from_snapshot(CONTEXT_FIELDS)
    universe_hash = universe_sha256(
        [{"keyword_id": r["keyword_id"], "text": r["keyword"]}
         for r in rows])
    job = CorpusScreeningJob(
        scoring_run_id=run.id, brand_profile_id=ws.id, status="pending",
        provider="deepseek", model="deepseek-v4-flash",
        prompt_version="SCR-2026-07-27-v3a", temperature=Decimal("0"),
        batch_size=3, view_salts=list(SALTS),
        applied_screening_channels=["ADS", "SEO"],
        screening_input_identity_sha256="a" * 64,
        universe_sha256=universe_hash,
        context_sha256=context["context_sha256"],
        input_snapshot={"rows": rows},
        screening_context={"fields": dict(CONTEXT_FIELDS)},
        # Dispatch bunu MÜHÜRLER; runner harcamadan önce canlı kodla
        # karşılaştırır (Codex 13. tur #2)
        runner_contract=screening_runner_contract())
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    db_session.refresh(attempt)
    return {"run": run, "job": job, "attempt": attempt, "rows": rows,
            "ws": ws}


def _ledger(session_factory, attempt):
    return AiCostLedger(
        session_factory, attempt_id=attempt.id,
        expected_screening_cap_usd=CAPS["screening"],
        expected_downstream_cap_usd=CAPS["downstream"])


def _run(session_factory, scenario, provider=None, **kw):
    provider = provider or FakeProvider()
    return run_screening_job(
        session_factory, job_id=scenario["job"].id,
        ledger=_ledger(session_factory, scenario["attempt"]),
        provider_factory=lambda: provider,
        limiter=NullInflightLimiter(allow_unlimited=True),
        sleep_fn=lambda _s: None, **kw), provider


def _reservations(db_session, scenario):
    return db_session.query(AiCostReservation).filter_by(
        budget_owner_attempt_id=scenario["attempt"].id).all()


def _reopen(db_session, scenario):
    """Çökmüş worker senaryosu: job `running`, execution lease SÜRESİ DOLMUŞ.

    Codex 10. tur #3: lease HÂLÂ CANLIYKEN ikinci çalıştırma reddedilir
    (mükerrer teslim). Devralma yalnız lease dolunca mümkündür — önceki
    worker Celery hard limit'iyle kesin ölmüştür.

    `expire_all` COMMIT'TEN SONRA çağrılır: önce çağrılırsa aynı
    session'daki bekleyen değişiklikler (bilerek bozulan checkpoint gibi)
    sessizce kaybolur.
    """
    job = db_session.get(CorpusScreeningJob, scenario["job"].id)
    job.status = "running"
    job.execution_lease_expires_at = datetime.now(timezone.utc) - timedelta(
        seconds=1)
    db_session.commit()
    db_session.expire_all()
    return job


def _unit_count(scenario, batch_size=3):
    """Gerçek batch sayısı sticky plandan gelir (sabit varsayılmaz)."""
    from app.core.screening.ensemble import sticky_plans

    return sum(len(plan) for plan in
               sticky_plans(_universe(scenario), batch_size, salts=SALTS))


def _reseal_contract(db_session, scenario):
    """Runner sabitleri deneysel olarak değiştiyse mühür de tazelenir.

    Drift kapısı (13. tur #2) BİLEREK katıdır: `SINGLE_RETRY_LIMIT` gibi
    bir sabit değişince mühürlü job canlı kodla uyuşmaz. Testler bu
    sabitleri patch'lediği için mührü açıkça yeniden yazarlar — üretimde
    bu, "eski job yeni kodla koşmaz" demektir.
    """
    job = db_session.get(CorpusScreeningJob, scenario["job"].id)
    job.runner_contract = screening_runner_contract()
    db_session.commit()


def _universe(scenario):
    return [{"id": r["keyword_id"], "keyword": r["keyword"]}
            for r in scenario["rows"]]


class TestHappyPath:
    def test_completes_and_writes_decisions(self, db_session,
                                            session_factory, scenario):
        result, provider = _run(session_factory, scenario)
        assert result["status"] == "completed"
        db_session.expire_all()
        job = db_session.get(CorpusScreeningJob, scenario["job"].id)
        assert job.status == "completed"
        assert job.actual_requests == result["provider_calls"]
        decisions = db_session.query(CorpusScreeningDecision).filter_by(
            screening_job_id=job.id).all()
        assert len(decisions) == UNIVERSE_SIZE * 3      # 3 kanal
        assert {d.channel for d in decisions} == {"ADS", "SEO", "SOCIAL"}
        ads = [d for d in decisions if d.channel == "ADS"][0]
        assert ads.passing is True and ads.merged_fit == Decimal("2.0000")

    def test_two_views_cover_universe(self, session_factory, scenario):
        result, provider = _run(session_factory, scenario)
        seen = [kid for call in provider.calls for kid in call]
        ids = {r["keyword_id"] for r in scenario["rows"]}
        # her görünüm evreni bir kez kapsar -> 2 x evren
        assert len(seen) == 2 * len(ids)
        assert set(seen) == ids

    def test_checkpoints_written_with_payload(self, db_session,
                                              session_factory, scenario):
        _run(session_factory, scenario)
        cps = db_session.query(CorpusScreeningBatchCheckpoint).filter_by(
            screening_job_id=scenario["job"].id).all()
        assert cps and all(c.state == "completed" for c in cps)
        assert all(c.payload and c.payload_sha256 and c.completed_at
                   for c in cps)
        assert all(c.logical_request_id for c in cps)

    def test_ledger_settled_per_batch(self, db_session, session_factory,
                                      scenario):
        result, provider = _run(session_factory, scenario)
        rows = _reservations(db_session, scenario)
        assert len(rows) == result["provider_calls"] == len(provider.calls)
        assert all(r.state == STATE_SETTLED for r in rows)
        assert all(r.budget_kind == BUDGET_SCREENING for r in rows)

    def test_decision_audit_fields_are_filled(self, db_session,
                                              session_factory, scenario):
        _run(session_factory, scenario)
        rows = db_session.query(CorpusScreeningDecision).filter_by(
            screening_job_id=scenario["job"].id,
            channel="SEO").all()
        assert rows
        for row in rows:
            assert row.view_a_reason == "TOPIC_MATCH"
            assert row.view_b_reason == "TOPIC_MATCH"
            assert row.view_a_unresolved is False
            assert row.view_b_unresolved is False
            assert row.contract_violations is None


class TestHttpAttemptAccounting:
    """Codex 9. tur #1: rezervasyon birimi GERÇEK HTTP denemesidir."""

    def test_every_real_request_gets_its_own_reservation(
            self, db_session, session_factory, scenario):
        provider = FakeProvider(transient_once=True)
        units = _unit_count(scenario)
        result, _ = _run(session_factory, scenario, provider=provider)
        rows = _reservations(db_session, scenario)
        # batch başına 2 gerçek istek: 1 geçici hata + 1 başarı
        assert len(provider.calls) == 2 * units
        assert len(rows) == len(provider.calls)
        assert result["provider_calls"] == len(provider.calls)
        assert result["status"] == "completed"
        # Aynı mantıksal batch'in denemeleri :c1/:c2 ile AYRIŞIR
        suffixes = {r.request_id.rsplit(":", 1)[-1] for r in rows}
        assert suffixes == {"c1", "c2"}
        # usage'sız başarısız deneme TAVANDAN yakılır, sessizce bedava
        # sayılmaz; başarılı deneme gerçek maliyetle kapanır
        assert sum(1 for r in rows if r.state == STATE_CEILING) == units
        assert sum(1 for r in rows if r.state == STATE_SETTLED) == units
        assert result["ceiling_charges"] == units

    def test_job_actual_requests_matches_http_calls(self, db_session,
                                                    session_factory,
                                                    scenario):
        provider = FakeProvider(transient_once=True)
        _run(session_factory, scenario, provider=provider)
        db_session.expire_all()
        job = db_session.get(CorpusScreeningJob, scenario["job"].id)
        assert job.actual_requests == len(provider.calls)


class TestCheckpointResume:
    def test_second_run_calls_no_provider(self, db_session,
                                          session_factory, scenario):
        first, provider1 = _run(session_factory, scenario)
        calls_first = len(provider1.calls)
        _reopen(db_session, scenario)
        provider2 = FakeProvider()
        second, _ = _run(session_factory, scenario, provider=provider2)
        assert calls_first > 0
        assert provider2.calls == []                      # ÜCRET YOK
        assert second["checkpoint_reused"] == _unit_count(scenario)
        assert second["status"] == "completed"

    def test_resume_adds_no_new_reservations(self, db_session,
                                             session_factory, scenario):
        _run(session_factory, scenario)
        before = len(_reservations(db_session, scenario))
        _reopen(db_session, scenario)
        _run(session_factory, scenario, provider=FakeProvider())
        assert len(_reservations(db_session, scenario)) == before

    def test_resume_statistics_come_from_db_not_memory(
            self, db_session, session_factory, scenario):
        """Codex 9. tur #2: resume özeti sıfırlanmaz."""
        first, _ = _run(session_factory, scenario)
        assert first["cost_usd"] > 0
        _reopen(db_session, scenario)
        second, _ = _run(session_factory, scenario, provider=FakeProvider())
        assert second["provider_calls"] == first["provider_calls"]
        assert second["cost_usd"] == first["cost_usd"]
        assert second["ceiling_charges"] == first["ceiling_charges"]
        assert second["unresolved"] == first["unresolved"]
        db_session.expire_all()
        job = db_session.get(CorpusScreeningJob, scenario["job"].id)
        assert float(job.cost_usd) == pytest.approx(first["cost_usd"])
        assert job.actual_requests == first["provider_calls"]

    def test_contract_drift_rejects_reuse(self, db_session,
                                          session_factory, scenario):
        _run(session_factory, scenario)
        cp = db_session.query(CorpusScreeningBatchCheckpoint).filter_by(
            screening_job_id=scenario["job"].id).first()
        cp.request_contract_sha256 = "f" * 64
        _reopen(db_session, scenario)
        with pytest.raises(ScreeningRunError, match="sözleşmesi uyuşmuyor"):
            _run(session_factory, scenario, provider=FakeProvider())

    def test_tampered_checkpoint_payload_is_rejected(self, db_session,
                                                     session_factory,
                                                     scenario):
        _run(session_factory, scenario)
        cp = db_session.query(CorpusScreeningBatchCheckpoint).filter_by(
            screening_job_id=scenario["job"].id).first()
        payload = dict(cp.payload)
        payload["results"] = list(payload["results"])[:-1]
        cp.payload = payload                    # mühür artık tutmuyor
        _reopen(db_session, scenario)
        provider = FakeProvider()
        with pytest.raises(ScreeningRunError, match="payload mührü"):
            _run(session_factory, scenario, provider=provider)
        assert provider.calls == []             # harcama BAŞLAMAZ


class TestResultLost:
    """Codex 9. tur #3: settle → checkpoint arası çökme."""

    def test_settled_without_checkpoint_is_typed_fallback(
            self, db_session, session_factory, scenario):
        _run(session_factory, scenario)
        cp = db_session.query(CorpusScreeningBatchCheckpoint).filter_by(
            screening_job_id=scenario["job"].id).first()
        db_session.delete(cp)                   # ücret ödendi, sonuç kayıp
        _reopen(db_session, scenario)
        provider = FakeProvider()
        with pytest.raises(ScreeningResultLost) as excinfo:
            _run(session_factory, scenario, provider=provider)
        assert excinfo.value.error_code == "RESULT_LOST_AFTER_SETTLE"
        assert provider.calls == []             # SESSİZ ücretli retry YOK

    def test_task_maps_result_lost_to_fallback(self, db_session, scenario):
        from app.tasks.screening_tasks import _mark_job, _status_for

        assert _status_for(ScreeningResultLost("x")) == "fallback"
        assert _status_for(ScreeningRunError("x")) == "failed"
        _reopen(db_session, scenario)
        rows = _mark_job(db_session, scenario["job"].id, status="fallback",
                         error_code="RESULT_LOST_AFTER_SETTLE",
                         error_message="kayıp", task_id=None)
        db_session.expire_all()
        job = db_session.get(CorpusScreeningJob, scenario["job"].id)
        assert rows == 1 and job.status == "fallback"


class TestOwnershipAndDuplicateDelivery:
    """Codex 9. tur #4: job/attempt/task sahipliği."""

    def test_foreign_task_id_cannot_claim(self, db_session,
                                          session_factory, scenario):
        job = db_session.get(CorpusScreeningJob, scenario["job"].id)
        job.task_id = "task-owner-1"
        job.status = "running"
        db_session.commit()
        provider = FakeProvider()
        with pytest.raises(ScreeningJobNotClaimable, match="başka task"):
            _run(session_factory, scenario, provider=provider,
                 task_id="task-duplicate-2")
        assert provider.calls == []
        assert _reservations(db_session, scenario) == []

    def test_owner_task_can_resume_its_own_job(self, session_factory,
                                               scenario):
        result, _ = _run(session_factory, scenario, task_id="task-owner-1")
        assert result["status"] == "completed"

    def test_job_of_another_run_is_refused(self, db_session,
                                           session_factory, scenario,
                                           make_workspace):
        other = make_workspace()
        job = db_session.get(CorpusScreeningJob, scenario["job"].id)
        job.brand_profile_id = other.id
        db_session.commit()
        with pytest.raises(ScreeningRunError, match="sahiplik reddi"):
            _run(session_factory, scenario)
        assert _reservations(db_session, scenario) == []

    def test_attempt_bound_to_other_job_is_refused(self, db_session,
                                                   session_factory,
                                                   scenario):
        job = db_session.get(CorpusScreeningJob, scenario["job"].id)
        other_job = CorpusScreeningJob(
            scoring_run_id=job.scoring_run_id,
            brand_profile_id=job.brand_profile_id, status="pending",
            provider=job.provider, model=job.model,
            prompt_version=job.prompt_version, temperature=job.temperature,
            batch_size=job.batch_size, view_salts=list(SALTS),
            applied_screening_channels=["ADS", "SEO"],
            screening_input_identity_sha256="b" * 64,
            universe_sha256=job.universe_sha256,
            context_sha256=job.context_sha256,
            input_snapshot=job.input_snapshot,
            screening_context=job.screening_context)
        db_session.add(other_job)
        db_session.flush()
        attempt = db_session.get(ChannelAssignmentAttempt,
                                 scenario["attempt"].id)
        attempt.screening_job_id = other_job.id
        db_session.commit()
        with pytest.raises(ScreeningRunError, match="başka bir job"):
            _run(session_factory, scenario)

    def test_terminal_job_is_not_downgraded_by_late_task(self, db_session,
                                                         scenario):
        from app.tasks.screening_tasks import _mark_job

        job = db_session.get(CorpusScreeningJob, scenario["job"].id)
        job.status = "completed"
        db_session.commit()
        rows = _mark_job(db_session, job.id, status="failed",
                         error_code="UNEXPECTED", error_message="geç hata",
                         task_id="task-duplicate-2")
        db_session.expire_all()
        assert rows == 0
        assert db_session.get(CorpusScreeningJob, job.id).status == \
            "completed"

    def test_mark_job_ignores_foreign_task_owner(self, db_session,
                                                 scenario):
        from app.tasks.screening_tasks import _mark_job

        job = db_session.get(CorpusScreeningJob, scenario["job"].id)
        job.status = "running"
        job.task_id = "task-owner-1"
        db_session.commit()
        rows = _mark_job(db_session, job.id, status="failed",
                         error_code="UNEXPECTED", error_message="yabancı",
                         task_id="task-duplicate-2")
        db_session.expire_all()
        assert rows == 0
        assert db_session.get(CorpusScreeningJob, job.id).status == "running"


class TestRunnerContractSeal:
    """Codex 13. tur #2: mühürlü sözleşme CANLI kodla karşılaştırılır."""

    def test_unsealed_contract_is_refused(self, db_session, session_factory,
                                          scenario):
        job = db_session.get(CorpusScreeningJob, scenario["job"].id)
        job.runner_contract = None
        db_session.commit()
        provider = FakeProvider()
        with pytest.raises(RunnerContractDrift, match="MÜHÜRLENMEMİŞ"):
            _run(session_factory, scenario, provider=provider)
        assert provider.calls == []          # harcama BAŞLAMAZ

    def test_drifted_contract_is_refused(self, db_session, session_factory,
                                         scenario):
        job = db_session.get(CorpusScreeningJob, scenario["job"].id)
        job.runner_contract = {**screening_runner_contract(),
                               "single_retry_scope": "batch"}
        db_session.commit()
        provider = FakeProvider()
        with pytest.raises(RunnerContractDrift, match="FARKLI"):
            _run(session_factory, scenario, provider=provider)
        assert provider.calls == []
        assert _reservations(db_session, scenario) == []

    def test_task_maps_drift_to_failed(self):
        from app.tasks.screening_tasks import _status_for

        assert _status_for(RunnerContractDrift("x")) == "failed"


class TestCheckpointSealVerification:
    """Codex 13. tur #3: `#single` checkpoint'leri de mühür kapısından geçer."""

    def test_tampered_single_checkpoint_blocks_resume(self, db_session,
                                                      session_factory,
                                                      scenario, monkeypatch):
        import app.core.screening.runner as runner_mod

        monkeypatch.setattr(runner_mod, "SINGLE_RETRY_LIMIT", 2)
        _reseal_contract(db_session, scenario)
        ids = [r["keyword_id"] for r in scenario["rows"]]
        _run(session_factory, scenario, provider=FakeProvider(
            unresolved_ids=ids))
        single = (db_session.query(CorpusScreeningBatchCheckpoint)
                  .filter(CorpusScreeningBatchCheckpoint.view.like("%#single"))
                  .first())
        assert single is not None
        payload = dict(single.payload)
        payload["results"] = []               # mühür artık tutmuyor
        single.payload = payload
        _reopen(db_session, scenario)
        provider = FakeProvider(unresolved_ids=ids)
        with pytest.raises(ScreeningRunError, match="payload mührü"):
            _run(session_factory, scenario, provider=provider)
        assert provider.calls == []           # bozuk kayıtla harcama YOK

    def test_finalize_rejects_tampered_checkpoint(self, db_session,
                                                  session_factory, scenario):
        _run(session_factory, scenario)
        cp = db_session.query(CorpusScreeningBatchCheckpoint).filter_by(
            screening_job_id=scenario["job"].id).first()
        cp.request_contract_sha256 = "0" * 64
        db_session.commit()
        with pytest.raises(ScreeningRunError, match="sözleşmesi uyuşmuyor"):
            finalize_job(session_factory, scenario["job"].id, SALTS,
                         _universe(scenario),
                         attempt_id=scenario["attempt"].id)


class TestExecutionLease:
    """Codex 10. tur #3: `pending -> running` TEK kazanan."""

    def test_live_lease_blocks_second_run(self, db_session, session_factory,
                                          scenario):
        _run(session_factory, scenario)          # job completed + lease var
        job = db_session.get(CorpusScreeningJob, scenario["job"].id)
        job.status = "running"                   # canlı lease korunuyor
        db_session.commit()
        provider = FakeProvider()
        with pytest.raises(ScreeningJobNotClaimable, match="CANLI execution"):
            _run(session_factory, scenario, provider=provider)
        assert provider.calls == []              # ikinci worker HARCAMAZ

    def test_expired_lease_allows_takeover(self, db_session, session_factory,
                                           scenario):
        first, _ = _run(session_factory, scenario)
        _reopen(db_session, scenario)            # lease süresi doldu
        second, _ = _run(session_factory, scenario, provider=FakeProvider())
        assert second["status"] == "completed"
        db_session.expire_all()
        job = db_session.get(CorpusScreeningJob, scenario["job"].id)
        assert job.execution_attempt >= 2        # devralma sayılıyor
        assert job.execution_lease_id

    def test_claim_starts_the_attempt(self, db_session, session_factory,
                                      scenario):
        """Ledger YALNIZ `running` sahipte harcar (10. tur #1)."""
        attempt = db_session.get(ChannelAssignmentAttempt,
                                 scenario["attempt"].id)
        attempt.status = "pending"
        db_session.commit()
        result, _ = _run(session_factory, scenario)
        assert result["status"] == "completed"
        db_session.expire_all()
        assert db_session.get(ChannelAssignmentAttempt,
                              scenario["attempt"].id).status == "running"


class TestSharedSingleRetryBudget:
    """Codex 11. tur #1: tekil retry kotası GÖRÜNÜM başına paylaşılır."""

    def test_budget_is_shared_across_checkpoint_batches(
            self, db_session, session_factory, scenario, monkeypatch):
        import app.core.screening.runner as runner_mod

        monkeypatch.setattr(runner_mod, "SINGLE_RETRY_LIMIT", 2)
        _reseal_contract(db_session, scenario)
        # Sağlayıcı HİÇBİR id döndürmez → her batch eksik-ID zincirine
        # girer; tekil retry AYRI fazda ve kota GÖRÜNÜM başına 2
        provider = FakeProvider(
            unresolved_ids=[r["keyword_id"] for r in scenario["rows"]])
        result, _ = _run(session_factory, scenario, provider=provider)
        batches_per_view = _unit_count(scenario) // 2
        # Görünüm başına: her batch (1 ana + 2 missing) + PAYLAŞILAN 2 tekil
        expected = 2 * (batches_per_view * 3 + 2)
        assert len(provider.calls) == expected
        assert result["status"] == "fallback"        # unresolved kapısı
        # Bütçe batch başına olsaydı her batch kendi tekilini harcardı
        assert len(provider.calls) < 2 * (batches_per_view * 5)

    def test_budget_object_is_thread_safe_and_bounded(self):
        import threading

        from app.core.screening.runner import SingleRetryBudget

        budget = SingleRetryBudget(limit=5)
        granted = []
        lock = threading.Lock()

        def _worker():
            for _ in range(10):
                if budget.try_consume():
                    with lock:
                        granted.append(1)

        threads = [threading.Thread(target=_worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(granted) == 5 and budget.remaining == 0

    def test_runner_contract_pins_single_retry_scope(self):
        from app.core.screening.identity import screening_runner_contract

        contract = screening_runner_contract()
        assert contract["single_retry_scope"] == "view"
        assert contract["runner_contract_version"].startswith("SCRRUN-")


class TestSingleRetryPhase:
    """Codex 12. tur #1: ayrı, sıralı, checkpoint'li tekil retry fazı."""

    def test_budget_survives_resume(self, db_session, session_factory,
                                    scenario, monkeypatch):
        """Resume'da kota SIFIRLANMAZ — checkpoint'lerden yeniden kurulur."""
        import app.core.screening.runner as runner_mod

        monkeypatch.setattr(runner_mod, "SINGLE_RETRY_LIMIT", 2)
        _reseal_contract(db_session, scenario)
        ids = [r["keyword_id"] for r in scenario["rows"]]
        first_provider = FakeProvider(unresolved_ids=ids)
        _run(session_factory, scenario, provider=first_provider)
        first_calls = len(first_provider.calls)
        _reopen(db_session, scenario)
        second_provider = FakeProvider(unresolved_ids=ids)
        _run(session_factory, scenario, provider=second_provider)
        # İkinci koşu ne batch ne de tekil retry için YENİDEN ÖDEME yapmaz
        assert second_provider.calls == []
        assert first_calls > 0
        singles = db_session.query(CorpusScreeningBatchCheckpoint).filter(
            CorpusScreeningBatchCheckpoint.screening_job_id
            == scenario["job"].id,
            CorpusScreeningBatchCheckpoint.view.like("%#single")).count()
        assert singles == 2 * 2          # görünüm başına 2, kota aşılmadı

    def test_phase_order_is_deterministic(self, db_session, session_factory,
                                          scenario, monkeypatch):
        """Öncelik (rank, keyword_id) — sağlayıcı gecikmesi belirlemez."""
        import app.core.screening.runner as runner_mod

        monkeypatch.setattr(runner_mod, "SINGLE_RETRY_LIMIT", 2)
        _reseal_contract(db_session, scenario)
        job = db_session.get(CorpusScreeningJob, scenario["job"].id)
        rows = [dict(r) for r in job.input_snapshot["rows"]]
        # Ters rank: en yüksek id EN İYİ rank'e sahip olsun
        for index, row in enumerate(rows):
            row["ranks"] = {"ADS": len(rows) - index, "SEO": len(rows) - index,
                            "SOCIAL": len(rows) - index}
            row["scores"] = {"ADS": 1.0, "SEO": 1.0, "SOCIAL": 1.0}
        job.input_snapshot = {"rows": rows}
        db_session.commit()
        ids = [r["keyword_id"] for r in scenario["rows"]]
        provider = FakeProvider(unresolved_ids=ids)
        _run(session_factory, scenario, provider=provider,
             concurrency=4)
        singles = (db_session.query(CorpusScreeningBatchCheckpoint)
                   .filter(CorpusScreeningBatchCheckpoint.screening_job_id
                           == scenario["job"].id,
                           CorpusScreeningBatchCheckpoint.view.like("%#single"))
                   .all())
        picked = {int(cp.payload["results"][0]["keyword_id"])
                  for cp in singles}
        # En iyi rank'e sahip (yani en yüksek id'li) iki kelime seçilmeli
        assert picked == set(sorted(ids)[-2:])

    def test_single_retry_resolves_and_wins_over_batch_result(
            self, db_session, session_factory, scenario):
        """Tekil retry çözerse ÇÖZÜLMÜŞ sonuç unresolved kaydı EZER."""
        target = scenario["rows"][0]["keyword_id"]

        class HealsOnSingle(FakeProvider):
            def screen_batch(self, context, batch):
                ids = [k["id"] for k in batch]
                if len(ids) > 1 or ids != [target]:
                    self.unresolved_ids = {target}
                else:
                    self.unresolved_ids = set()   # tekil çağrıda çözer
                return super().screen_batch(context, batch)

        result, _ = _run(session_factory, scenario, provider=HealsOnSingle())
        assert result["status"] == "completed"
        assert result["unresolved"] == 0
        decisions = db_session.query(CorpusScreeningDecision).filter_by(
            screening_job_id=scenario["job"].id, keyword_id=target).all()
        assert decisions and all(d.view_a_unresolved is False
                                 for d in decisions)


class TestFrozenSeals:
    """Codex 9. tur #5: mühürler harcamadan ÖNCE doğrulanır."""

    def test_context_seal_mismatch_blocks_spend(self, db_session,
                                                session_factory, scenario):
        job = db_session.get(CorpusScreeningJob, scenario["job"].id)
        job.screening_context = {"fields": dict(
            CONTEXT_FIELDS, product_definition="Başka ürün")}
        db_session.commit()
        provider = FakeProvider()
        with pytest.raises(ScreeningRunError, match="BAĞLAM MÜHRÜ"):
            _run(session_factory, scenario, provider=provider)
        assert provider.calls == []
        assert _reservations(db_session, scenario) == []

    def test_universe_seal_mismatch_blocks_spend(self, db_session,
                                                 session_factory, scenario):
        job = db_session.get(CorpusScreeningJob, scenario["job"].id)
        rows = [dict(r) for r in job.input_snapshot["rows"]]
        rows[0]["keyword"] = "değiştirilmiş kelime"
        job.input_snapshot = {"rows": rows}
        db_session.commit()
        provider = FakeProvider()
        with pytest.raises(ScreeningRunError, match="EVREN MÜHRÜ"):
            _run(session_factory, scenario, provider=provider)
        assert provider.calls == []
        assert _reservations(db_session, scenario) == []

    def test_missing_score_id_blocks_spend(self, db_session,
                                           session_factory, scenario):
        job = db_session.get(CorpusScreeningJob, scenario["job"].id)
        rows = [dict(r) for r in job.input_snapshot["rows"]]
        rows[0].pop("keyword_score_id")
        job.input_snapshot = {"rows": rows}
        db_session.commit()
        provider = FakeProvider()
        with pytest.raises(ScreeningRunError, match="keyword_score_id"):
            _run(session_factory, scenario, provider=provider)
        assert provider.calls == []


class TestCrashRecovery:
    def test_open_reservation_is_ceiling_charged_then_retried(
            self, db_session, session_factory, scenario):
        """Çökme: rezervasyon açık kalmış, checkpoint yok."""
        job = scenario["job"]
        from app.core.screening.ensemble import sticky_plans

        plan = sticky_plans(_universe(scenario), 3, salts=[SALTS[0]])[0]
        bhash = batch_hash(plan[0])
        base = request_id_for(job.id, SALTS[0], bhash)
        rid_str = f"{base}:c1"
        led = _ledger(session_factory, scenario["attempt"])
        stale_rid = led.reserve(kind=BUDGET_SCREENING, request_id=rid_str,
                                ceiling_usd="0.01")
        result, provider = _run(session_factory, scenario)
        db_session.expire_all()
        stale = db_session.get(AiCostReservation, stale_rid)
        assert stale.state == STATE_CEILING          # bilinmeyen harcama
        assert result["ceiling_charges"] >= 1
        retried = db_session.query(AiCostReservation).filter_by(
            budget_owner_attempt_id=scenario["attempt"].id,
            request_id=rid_str).order_by(AiCostReservation.attempt).all()
        assert [r.attempt for r in retried] == [1, 2]
        assert retried[1].state == STATE_SETTLED
        assert result["status"] == "completed"

    def test_provider_failure_charges_ceiling_and_raises(
            self, db_session, session_factory, scenario):
        with pytest.raises(RuntimeError, match="provider patladı"):
            _run(session_factory, scenario,
                 provider=FakeProvider(fail=True))
        rows = _reservations(db_session, scenario)
        assert rows and all(r.state == STATE_CEILING for r in rows)
        assert db_session.query(
            CorpusScreeningBatchCheckpoint).filter_by(
            screening_job_id=scenario["job"].id).count() == 0

    def test_missing_usage_charges_ceiling(self, db_session,
                                           session_factory, scenario):
        provider = FakeProvider(usage_cost=None)
        result, _ = _run(session_factory, scenario, provider=provider)
        rows = _reservations(db_session, scenario)
        assert rows and all(r.state == STATE_CEILING for r in rows)
        assert result["ceiling_charges"] == len(rows)


class TestGates:
    def test_unresolved_above_threshold_is_fallback(self, db_session,
                                                    session_factory,
                                                    scenario):
        bad = FakeProvider(
            unresolved_ids=[r["keyword_id"] for r in scenario["rows"][:3]])
        result, _ = _run(session_factory, scenario, provider=bad)
        assert result["status"] == "fallback"
        assert result["reason"] == "UNRESOLVED_ABOVE_THRESHOLD"
        db_session.expire_all()
        job = db_session.get(CorpusScreeningJob, scenario["job"].id)
        assert job.status == "fallback"
        assert job.error_code == "UNRESOLVED_ABOVE_THRESHOLD"
        # KISMİ sonuç aday üretimine GİRMEZ
        assert db_session.query(CorpusScreeningDecision).filter_by(
            screening_job_id=job.id).count() == 0

    def test_coverage_gap_is_fallback(self, db_session, session_factory,
                                      scenario):
        _run(session_factory, scenario)
        # Bir checkpoint'i sil → kapsama eksilir
        cp = db_session.query(CorpusScreeningBatchCheckpoint).filter_by(
            screening_job_id=scenario["job"].id).first()
        db_session.delete(cp)
        job = db_session.get(CorpusScreeningJob, scenario["job"].id)
        job.status = "running"
        db_session.query(CorpusScreeningDecision).filter_by(
            screening_job_id=job.id).delete()
        db_session.commit()
        result = finalize_job(
            session_factory, job.id, SALTS, _universe(scenario),
            attempt_id=scenario["attempt"].id)
        assert result["status"] == "fallback"
        assert result["reason"] == "COVERAGE_INCOMPLETE"

    def test_refinalize_is_idempotent(self, db_session, session_factory,
                                      scenario):
        _run(session_factory, scenario)
        before = db_session.query(CorpusScreeningDecision).filter_by(
            screening_job_id=scenario["job"].id).count()
        result = finalize_job(
            session_factory, scenario["job"].id, SALTS, _universe(scenario),
            attempt_id=scenario["attempt"].id)
        db_session.expire_all()
        after = db_session.query(CorpusScreeningDecision).filter_by(
            screening_job_id=scenario["job"].id).count()
        assert result["status"] == "completed" and after == before

    def test_decision_count_mismatch_is_refused(self, db_session,
                                                session_factory, scenario):
        _run(session_factory, scenario)
        row = db_session.query(CorpusScreeningDecision).filter_by(
            screening_job_id=scenario["job"].id).first()
        db_session.delete(row)
        db_session.commit()
        with pytest.raises(ScreeningRunError, match="karar sayısı"):
            finalize_job(session_factory, scenario["job"].id, SALTS,
                         _universe(scenario),
                         attempt_id=scenario["attempt"].id)


class TestGuards:
    def test_invalid_snapshot_fails_closed(self, db_session,
                                           session_factory, scenario):
        job = db_session.get(CorpusScreeningJob, scenario["job"].id)
        job.input_snapshot = {"rows": []}
        db_session.commit()
        with pytest.raises(ScreeningRunError, match="input_snapshot"):
            _run(session_factory, scenario)

    def test_terminal_job_cannot_run(self, db_session, session_factory,
                                     scenario):
        job = db_session.get(CorpusScreeningJob, scenario["job"].id)
        job.status = "completed"
        db_session.commit()
        with pytest.raises(ScreeningJobNotClaimable, match="terminal"):
            _run(session_factory, scenario)

    def test_inflight_unavailable_fails_closed(self, db_session,
                                               session_factory, scenario):
        """Limiter yoksa SINIRSIZ çağrıya düşülmez."""
        with pytest.raises(InflightUnavailable):
            run_screening_job(
                session_factory, job_id=scenario["job"].id,
                ledger=_ledger(session_factory, scenario["attempt"]),
                provider_factory=lambda: FakeProvider(),
                limiter=NullInflightLimiter(allow_unlimited=False),
                sleep_fn=lambda _s: None)
        rows = _reservations(db_session, scenario)
        assert rows and all(r.state == STATE_CEILING for r in rows)

    def test_request_id_is_stable_across_retries(self, scenario):
        a = request_id_for(scenario["job"].id, SALTS[0], "b" * 64)
        b = request_id_for(scenario["job"].id, SALTS[0], "b" * 64)
        assert a == b and a.startswith(f"scr:{scenario['job'].id}:")


class FakeScriptRedis:
    """Lua betiklerini gerçek ZSET semantiğiyle taklit eden istemci."""

    def __init__(self):
        self.zset = {}

    def register_script(self, source):
        if "ZCARD" in source:
            return self._acquire
        return self._renew

    def _acquire(self, keys, args):
        now, ttl, limit, token = (float(args[0]), float(args[1]),
                                  int(args[2]), args[3])
        for member, score in list(self.zset.items()):
            if score <= now - ttl:
                del self.zset[member]
        if len(self.zset) < limit:
            self.zset[token] = now
            return 1
        return 0

    def _renew(self, keys, args):
        now, token = float(args[0]), args[1]
        if token in self.zset:
            self.zset[token] = now
            return 1
        return 0

    def zrem(self, key, token):
        self.zset.pop(token, None)


class TestInflightLimiter:
    """Codex 9. tur #6: kapasite kontrolü + kayıt TEK atomik adımda."""

    def test_acquire_script_is_single_atomic_unit(self):
        from app.core.screening import inflight

        src = inflight._ACQUIRE_LUA
        for op in ("ZREMRANGEBYSCORE", "ZCARD", "ZADD"):
            assert op in src
        assert "SCAN" not in src        # SCAN tabanlı sayım semafor DEĞİL

    def test_limit_is_enforced_and_released(self):
        limiter = RedisInflightLimiter(FakeScriptRedis(), limit=1)
        first = limiter.try_acquire()
        assert first and limiter.try_acquire() is None
        limiter.release(first)
        assert limiter.try_acquire() is not None

    def test_expired_lease_frees_capacity(self):
        client = FakeScriptRedis()
        limiter = RedisInflightLimiter(client, limit=1, ttl_seconds=1)
        token = limiter.try_acquire()
        client.zset[token] = 0.0        # lease süresi geçti
        assert limiter.try_acquire() is not None

    def test_renew_only_works_while_owned(self):
        client = FakeScriptRedis()
        limiter = RedisInflightLimiter(client, limit=1)
        token = limiter.try_acquire()
        assert limiter.renew(token) is True
        limiter.release(token)
        assert limiter.renew(token) is False

    def test_slot_times_out_when_full(self):
        limiter = RedisInflightLimiter(FakeScriptRedis(), limit=1)
        limiter.try_acquire()
        with pytest.raises(InflightBusy):
            with limiter.slot(timeout_seconds=0, sleep_fn=lambda _s: None):
                pass

    def test_build_limiter_without_redis_is_fail_closed(self):
        limiter = __import__(
            "app.core.screening.inflight", fromlist=["build_limiter"]
        ).build_limiter(None, limit=4)
        with pytest.raises(InflightUnavailable):
            with limiter.slot():
                pass


class TestCeleryTaskWiring:
    def test_task_is_registered_on_dedicated_queue(self):
        import app.tasks.screening_tasks as st
        from app.tasks.celery_app import celery_app

        assert "corpus_screening.run" in celery_app.tasks
        assert celery_app.conf.task_routes["corpus_screening.run"] == {
            "queue": "corpus_screening"}
        assert st.SOFT_TIME_LIMIT == 1080 and st.HARD_TIME_LIMIT == 1200

    @pytest.mark.parametrize("compose", ["docker-compose.yml",
                                         "docker-compose.prod.yml"])
    def test_dedicated_worker_exists_in_compose(self, compose):
        """Codex 9. tur #7: kuyruğu dinleyen worker yoksa task asılı kalır."""
        text = (REPO_ROOT / compose).read_text(encoding="utf-8")
        assert "celery_screening_worker:" in text
        assert "-Q corpus_screening" in text
