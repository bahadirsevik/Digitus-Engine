# -*- coding: utf-8 -*-
"""Shadow counterfactual materyalizasyonu (plan §7.2).

Shadow'un ÖLÇÜM ALETİ: canlı aday havuzu 1x kalırken "3B union
sözleşmesi ne seçerdi" sorusu `is_applied=False` satırlara yazılır.

Kilitlenen sözleşmeler:
- Canlı `ChannelCandidate` tablosuna HİÇBİR satır yazılmaz.
- Kapsam v3: ADS/SEO additive (U >= B, ensemble katkısı görünür),
  SOCIAL `baseline_only` (kimlikler VE SIRA flag-off baseline ile birebir).
- Idempotent: ikinci çağrı yeniden yazmaz; sayı tutmuyorsa tipli hata.
- Fail-closed: job completed değilse / kararlar evrenle örtüşmüyorsa
  satır ÜRETİLMEZ.
"""
from decimal import Decimal

import pytest

from app.core.screening.candidate_union import build_union_candidate_plan
from app.core.screening.counterfactual import (
    CounterfactualError,
    channel_union_mode,
    counterfactual_summary,
    materialize_counterfactual,
)
from app.database.models import (
    ChannelAssignmentAttempt,
    ChannelCandidate,
    CorpusCandidateSelection,
    CorpusScreeningDecision,
    CorpusScreeningJob,
    Keyword,
    KeywordScore,
    ScoringRun,
)

UNIVERSE = 24
SALTS = ["scr-view-a", "scr-view-b"]


@pytest.fixture
def shadow_run(db_session, make_workspace):
    ws = make_workspace("CF WS")
    run = ScoringRun(run_name="cf-run", brand_profile_id=ws.id,
                     total_keywords=UNIVERSE, ads_capacity=3, seo_capacity=3,
                     social_capacity=3, status="channel_assigning",
                     default_relevance_coefficient=1.0)
    db_session.add(run)
    db_session.commit()
    rows = []
    for i in range(UNIVERSE):
        kw = Keyword(keyword=f"kelime {i:02d}", monthly_volume=1000 - i)
        db_session.add(kw)
        db_session.flush()
        score = KeywordScore(scoring_run_id=run.id, keyword_id=kw.id,
                             ads_score=100 - i, seo_score=100 - i,
                             social_score=100 - i,
                             ads_rank=i + 1, seo_rank=i + 1,
                             social_rank=i + 1)
        db_session.add(score)
        db_session.flush()
        # Codex 10. tur #5: ölçüm YALNIZ dispatch anındaki mühürlü
        # satırlardan üretilir — skor/rank/relevance snapshot'ta taşınır
        rows.append({"keyword_id": kw.id, "keyword_score_id": score.id,
                     "keyword": kw.keyword,
                     "scores": {"ADS": 100 - i, "SEO": 100 - i,
                                "SOCIAL": 100 - i},
                     "ranks": {"ADS": i + 1, "SEO": i + 1,
                               "SOCIAL": i + 1},
                     "relevance": None})
    attempt = ChannelAssignmentAttempt(
        parent_task_id="cf-parent", scoring_run_id=run.id,
        brand_profile_id=ws.id, status="running", phase="screening",
        screening_mode="shadow", applied_candidate_multiplier=1,
        counterfactual_target_multiplier=3,
        relevance_coefficient=Decimal("1.00"),
        approved_screening_cap_usd=Decimal("0.50"),
        approved_downstream_cap_usd=Decimal("4.00"))
    db_session.add(attempt)
    db_session.flush()
    job = CorpusScreeningJob(
        scoring_run_id=run.id, brand_profile_id=ws.id, status="completed",
        provider="deepseek", model="deepseek-v4-flash",
        prompt_version="SCR-2026-07-27-v3a", temperature=Decimal("0"),
        batch_size=10, view_salts=list(SALTS),
        applied_screening_channels=["ADS", "SEO"],
        screening_input_identity_sha256="a" * 64, universe_sha256="u" * 64,
        context_sha256="c" * 64, input_snapshot={"rows": rows},
        screening_context={"fields": {}})
    db_session.add(job)
    db_session.flush()
    attempt.screening_job_id = job.id
    # Kararlar: sıralamayı TERSİNE çeviren bir tarama sinyali —
    # baseline'da sonda olan kelimeler ensemble'da başa geçer, böylece
    # union'ın gerçekten yeni aday getirdiği görülür
    for index, row in enumerate(rows):
        for channel in ("ADS", "SEO", "SOCIAL"):
            fit = 2 if index >= UNIVERSE - 6 else 0
            db_session.add(CorpusScreeningDecision(
                screening_job_id=job.id,
                keyword_score_id=row["keyword_score_id"],
                keyword_id=row["keyword_id"], channel=channel,
                view_a_fit=fit, view_b_fit=fit, view_a_unresolved=False,
                view_b_unresolved=False, merged_fit=Decimal(fit),
                passing=bool(fit), disagreement=False, uncertain=False))
    db_session.commit()
    return {"ws": ws, "run": run, "attempt": attempt, "job": job,
            "rows": rows}


class TestScopeContract:
    def test_v3_scope_modes(self):
        assert channel_union_mode("ADS") == "additive_v2"
        assert channel_union_mode("SEO") == "additive_v2"
        assert channel_union_mode("SOCIAL") == "baseline_only"


class TestMaterialization:
    def test_writes_counterfactual_rows_only(self, db_session, shadow_run):
        result = materialize_counterfactual(db_session,
                                            job_id=shadow_run["job"].id)
        assert result["status"] == "materialized"
        rows = db_session.query(CorpusCandidateSelection).all()
        assert rows and len(rows) == result["rows"]
        # Canlı havuza DOKUNULMADI
        assert db_session.query(ChannelCandidate).count() == 0
        assert all(r.is_applied is False for r in rows)
        assert all(r.materialization_action == "initial" for r in rows)
        assert all(r.materialization_identity_sha256
                   == result["materialization_identity_sha256"]
                   for r in rows)

    def test_in_scope_union_exceeds_baseline_window(self, db_session,
                                                    shadow_run):
        materialize_counterfactual(db_session, job_id=shadow_run["job"].id)
        for channel in ("ADS", "SEO"):
            rows = db_session.query(CorpusCandidateSelection).filter_by(
                channel=channel).all()
            b_initial = rows[0].b_initial
            assert len(rows) > b_initial          # ensemble katkısı var
            origins = {r.origin_source for r in rows}
            # Yalnız tarama sinyalinden gelen adaylar VAR; baseline
            # penceresi de korunuyor (T büyük olduğu için baseline
            # satırları çoğunlukla `both` etiketlenir)
            assert "screening" in origins
            assert origins & {"baseline", "both"}
            assert rows[0].u_union_size == len(rows)

    def test_out_of_scope_channel_matches_baseline_exactly(self, db_session,
                                                           shadow_run):
        """SOCIAL: kimlikler VE SIRA flag-off baseline ile birebir."""
        from app.core.benchmark.candidate_replay import (
            budget_initial,
            raw_rank_ordering,
        )

        materialize_counterfactual(db_session, job_id=shadow_run["job"].id)
        rows = (db_session.query(CorpusCandidateSelection)
                .filter_by(channel="SOCIAL")
                .order_by(CorpusCandidateSelection.initial_materialized_rank)
                .all())
        universe = [{"keyword_id": r["keyword_id"],
                     "ranks": {"SOCIAL": i + 1}, "scores": {"SOCIAL": 1.0},
                     "relevance": None}
                    for i, r in enumerate(shadow_run["rows"])]
        baseline = raw_rank_ordering(universe, "SOCIAL")
        b = budget_initial("SOCIAL", shadow_run["run"].social_capacity)
        assert [r.keyword_id for r in rows] == baseline[:b]
        assert all(r.origin_source == "baseline" for r in rows)

    def test_plan_matches_direct_union_computation(self, db_session,
                                                   shadow_run):
        """Materyalizasyon ortak çekirdeği kullanır — ikinci kural YOK."""
        materialize_counterfactual(db_session, job_id=shadow_run["job"].id)
        rows = (db_session.query(CorpusCandidateSelection)
                .filter_by(channel="ADS")
                .order_by(CorpusCandidateSelection.initial_materialized_rank)
                .all())
        ids = [r["keyword_id"] for r in shadow_run["rows"]]
        baseline = ids                       # skor DESC = ekleme sırası
        ensemble = ids[-6:] + ids[:-6]       # kararların ürettiği sıra
        plan = build_union_candidate_plan(
            baseline_ordering=baseline, ensemble_ordering=ensemble,
            b_initial=rows[0].b_initial, universe_size=len(ids),
            multiplier=3, mode="additive_v2")
        assert [r.keyword_id for r in rows] == [
            item["keyword_id"] for item in plan["selected"]]

    def test_summary_reports_origin_breakdown(self, db_session, shadow_run):
        materialize_counterfactual(db_session, job_id=shadow_run["job"].id)
        summary = counterfactual_summary(
            db_session, attempt_id=shadow_run["attempt"].id)
        assert set(summary) == {"ADS", "SEO", "SOCIAL"}
        assert summary["ADS"]["origin"]["screening"] > 0
        assert summary["SOCIAL"]["origin"]["screening"] == 0


class TestSnapshotDiscipline:
    """Codex 10. tur #5: canlı tablo okunmaz, mühürlü snapshot kullanılır."""

    def test_live_score_change_does_not_move_the_measurement(
            self, db_session, shadow_run):
        """Parent skoru/relevance'ı tazelese bile ölçüm DEĞİŞMEZ."""
        first = materialize_counterfactual(db_session,
                                           job_id=shadow_run["job"].id)
        before = [(r.channel, r.keyword_id, r.initial_materialized_rank)
                  for r in db_session.query(CorpusCandidateSelection).all()]
        # Canlı tabloyu TERSİNE çevir
        for index, row in enumerate(shadow_run["rows"]):
            score = db_session.query(KeywordScore).filter_by(
                scoring_run_id=shadow_run["run"].id,
                keyword_id=row["keyword_id"]).one()
            score.ads_score = index          # sıralama tersine döner
            score.ads_rank = UNIVERSE - index
        db_session.commit()
        again = materialize_counterfactual(db_session,
                                           job_id=shadow_run["job"].id)
        after = [(r.channel, r.keyword_id, r.initial_materialized_rank)
                 for r in db_session.query(CorpusCandidateSelection).all()]
        assert again["status"] == "already_materialized"
        assert after == before
        assert again["materialization_identity_sha256"] == \
            first["materialization_identity_sha256"]

    def test_snapshot_without_scores_is_refused(self, db_session,
                                                shadow_run):
        job = shadow_run["job"]
        job.input_snapshot = {"rows": [
            {"keyword_id": r["keyword_id"],
             "keyword_score_id": r["keyword_score_id"],
             "keyword": r["keyword"]} for r in shadow_run["rows"]]}
        db_session.commit()
        with pytest.raises(CounterfactualError) as exc:
            materialize_counterfactual(db_session, job_id=job.id)
        assert exc.value.code == "SNAPSHOT_INCOMPLETE"
        assert db_session.query(CorpusCandidateSelection).count() == 0

    def test_rank_seal_mismatch_is_refused(self, db_session, shadow_run):
        shadow_run["attempt"].channel_rank_snapshot_sha256 = "e" * 64
        db_session.commit()
        with pytest.raises(CounterfactualError) as exc:
            materialize_counterfactual(db_session,
                                       job_id=shadow_run["job"].id)
        assert exc.value.code == "RANK_SNAPSHOT_MISMATCH"


class TestIdempotencyAndGuards:
    def test_second_call_does_not_rewrite(self, db_session, shadow_run):
        first = materialize_counterfactual(db_session,
                                           job_id=shadow_run["job"].id)
        second = materialize_counterfactual(db_session,
                                            job_id=shadow_run["job"].id)
        assert second["status"] == "already_materialized"
        assert second["rows"] == first["rows"]
        assert db_session.query(CorpusCandidateSelection).count() == \
            first["rows"]

    def test_tampered_row_count_is_refused(self, db_session, shadow_run):
        materialize_counterfactual(db_session, job_id=shadow_run["job"].id)
        row = db_session.query(CorpusCandidateSelection).first()
        db_session.delete(row)
        db_session.commit()
        with pytest.raises(CounterfactualError) as exc:
            materialize_counterfactual(db_session, job_id=shadow_run["job"].id)
        assert exc.value.code == "SELECTION_COUNT_MISMATCH"

    def test_identity_mismatch_is_refused(self, db_session, shadow_run):
        """Aynı SAYIDA ama farklı kimlikli satırlar geçemez (10. tur #10)."""
        materialize_counterfactual(db_session, job_id=shadow_run["job"].id)
        row = db_session.query(CorpusCandidateSelection).first()
        row.materialization_identity_sha256 = "f" * 64
        db_session.commit()
        with pytest.raises(CounterfactualError) as exc:
            materialize_counterfactual(db_session,
                                       job_id=shadow_run["job"].id)
        assert exc.value.code == "SELECTION_IDENTITY_MISMATCH"

    def test_reordered_row_is_refused(self, db_session, shadow_run):
        """Satır sayısı aynı ama SIRA farklıysa reddedilir."""
        materialize_counterfactual(db_session, job_id=shadow_run["job"].id)
        row = (db_session.query(CorpusCandidateSelection)
               .filter_by(channel="ADS").first())
        row.initial_materialized_rank = 999
        db_session.commit()
        with pytest.raises(CounterfactualError) as exc:
            materialize_counterfactual(db_session,
                                       job_id=shadow_run["job"].id)
        assert exc.value.code == "SELECTION_SET_MISMATCH"

    def test_incomplete_job_is_refused(self, db_session, shadow_run):
        shadow_run["job"].status = "fallback"
        db_session.commit()
        with pytest.raises(CounterfactualError) as exc:
            materialize_counterfactual(db_session, job_id=shadow_run["job"].id)
        assert exc.value.code == "JOB_NOT_COMPLETED"
        assert db_session.query(CorpusCandidateSelection).count() == 0

    def test_decision_universe_mismatch_is_refused(self, db_session,
                                                   shadow_run):
        victim = shadow_run["rows"][0]["keyword_id"]
        db_session.query(CorpusScreeningDecision).filter_by(
            screening_job_id=shadow_run["job"].id,
            keyword_id=victim).delete()
        db_session.commit()
        with pytest.raises(CounterfactualError) as exc:
            materialize_counterfactual(db_session, job_id=shadow_run["job"].id)
        assert exc.value.code == "UNIVERSE_DECISION_MISMATCH"
        assert db_session.query(CorpusCandidateSelection).count() == 0

    def test_missing_decisions_is_refused(self, db_session, shadow_run):
        db_session.query(CorpusScreeningDecision).filter_by(
            screening_job_id=shadow_run["job"].id).delete()
        db_session.commit()
        with pytest.raises(CounterfactualError) as exc:
            materialize_counterfactual(db_session, job_id=shadow_run["job"].id)
        assert exc.value.code == "DECISIONS_MISSING"


class TestTaskWiring:
    def test_task_reports_failed_measurement_without_losing_job(
            self, db_session, shadow_run, monkeypatch):
        """Ölçüm yazılamazsa tarama sonucu geçersiz SAYILMAZ."""
        from app.tasks import screening_tasks

        db_session.query(CorpusScreeningDecision).filter_by(
            screening_job_id=shadow_run["job"].id).delete()
        db_session.commit()
        monkeypatch.setattr(
            "app.database.connection.SessionLocal",
            lambda: db_session.__class__(bind=db_session.get_bind()))
        out = screening_tasks._materialize_counterfactual(
            shadow_run["job"].id)
        assert out["status"] == "failed"
        assert out["reason"] == "DECISIONS_MISSING"
        db_session.expire_all()
        assert db_session.get(CorpusScreeningJob,
                              shadow_run["job"].id).status == "completed"


class TestDeepIdempotency:
    """Codex 11. tur #7: kaynak metadata'sı da doğrulanır."""

    def test_origin_source_tamper_is_refused(self, db_session, shadow_run):
        materialize_counterfactual(db_session, job_id=shadow_run["job"].id)
        row = (db_session.query(CorpusCandidateSelection)
               .filter_by(channel="ADS", origin_source="screening").first()
               or db_session.query(CorpusCandidateSelection)
               .filter_by(channel="ADS").first())
        row.origin_source = "none"
        db_session.commit()
        with pytest.raises(CounterfactualError) as exc:
            materialize_counterfactual(db_session,
                                       job_id=shadow_run["job"].id)
        assert exc.value.code == "SELECTION_SET_MISMATCH"

    def test_is_applied_flip_is_refused(self, db_session, shadow_run):
        """Ölçüm satırı canlıymış gibi işaretlenirse yakalanır."""
        materialize_counterfactual(db_session, job_id=shadow_run["job"].id)
        row = db_session.query(CorpusCandidateSelection).first()
        row.is_applied = True
        db_session.commit()
        with pytest.raises(CounterfactualError) as exc:
            materialize_counterfactual(db_session,
                                       job_id=shadow_run["job"].id)
        assert exc.value.code == "SELECTION_SET_MISMATCH"

    def test_identity_uses_frozen_anchor_version(self, db_session,
                                                 shadow_run):
        """Codex 11. tur #4: canlı anchor sürümü kimliği oynatamaz."""
        shadow_run["attempt"].requested_anchor_version = 7
        db_session.commit()
        first = materialize_counterfactual(db_session,
                                           job_id=shadow_run["job"].id)
        shadow_run["run"].relevance_anchor_version = 99   # canlı değişti
        db_session.commit()
        again = materialize_counterfactual(db_session,
                                           job_id=shadow_run["job"].id)
        assert again["materialization_identity_sha256"] == \
            first["materialization_identity_sha256"]


class TestReuseMeasurementBinding:
    """Canlı koşuda görüldü: reuse'da ölçüm YENİ attempt'e bağlanmıyordu.

    `materialize_counterfactual` attempt'i job'dan `.first()` ile buluyor,
    ESKİ attempt'i görüp "already_materialized" deyip çıkıyordu; yeni
    attempt'in ölçüm satırı 0 ve kimliği NULL kalıyordu.
    """

    def test_reusing_attempt_gets_identity_without_duplicating_rows(
            self, db_session, shadow_run):
        first = materialize_counterfactual(db_session,
                                           job_id=shadow_run["job"].id,
                                           attempt_id=shadow_run["attempt"].id)
        assert first["status"] == "materialized"
        rows_before = db_session.query(CorpusCandidateSelection).count()

        # Aktif-attempt partial unique'i: önce ilkini kapat
        shadow_run["attempt"].status = "completed"
        db_session.commit()

        # AYNI job'ı yeniden kullanan İKİNCİ attempt
        second = ChannelAssignmentAttempt(
            parent_task_id="cf-parent-2",
            scoring_run_id=shadow_run["run"].id,
            brand_profile_id=shadow_run["ws"].id, status="running",
            phase="screening", screening_mode="shadow",
            applied_candidate_multiplier=1,
            counterfactual_target_multiplier=3,
            relevance_coefficient=Decimal("1.00"),
            screening_job_id=shadow_run["job"].id)
        db_session.add(second)
        db_session.commit()

        out = materialize_counterfactual(db_session,
                                         job_id=shadow_run["job"].id,
                                         attempt_id=second.id)
        assert out["status"] == "reused_measurement"
        assert out["source_attempt_id"] == shadow_run["attempt"].id
        # Satır ÇOĞALTILMAZ ama yeni attempt kimliği DAMGALANIR
        assert db_session.query(CorpusCandidateSelection).count() == rows_before
        db_session.refresh(second)
        assert second.materialization_identity_sha256 == \
            out["materialization_identity_sha256"]

    def test_summary_can_be_read_by_job(self, db_session, shadow_run):
        materialize_counterfactual(db_session, job_id=shadow_run["job"].id,
                                   attempt_id=shadow_run["attempt"].id)
        by_job = counterfactual_summary(db_session,
                                        job_id=shadow_run["job"].id)
        by_attempt = counterfactual_summary(
            db_session, attempt_id=shadow_run["attempt"].id)
        assert by_job == by_attempt
        assert set(by_job) == {"ADS", "SEO", "SOCIAL"}

    def test_wrong_attempt_is_refused(self, db_session, shadow_run):
        shadow_run["attempt"].status = "completed"
        db_session.commit()
        other = ChannelAssignmentAttempt(
            parent_task_id="cf-parent-3",
            scoring_run_id=shadow_run["run"].id,
            brand_profile_id=shadow_run["ws"].id, status="running",
            phase="assignment", screening_mode="off",
            applied_candidate_multiplier=1,
            counterfactual_target_multiplier=1)
        db_session.add(other)
        db_session.commit()
        with pytest.raises(CounterfactualError) as exc:
            materialize_counterfactual(db_session,
                                       job_id=shadow_run["job"].id,
                                       attempt_id=other.id)
        assert exc.value.code == "OWNERSHIP_MISMATCH"
