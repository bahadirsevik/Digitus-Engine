"""Codex A+B inceleme düzeltmeleri (v7) — regresyon testleri.

1. SOCIAL üretim-ortası reassignment yarışı: AI sırasında reassignment
   olursa çıktı fresh doğamaz (channel_assignment_version snapshot +
   kayıt-öncesi kilitli doğrulama; iki ayrı session ile gerçek yarış).
2. Tüm kategoriler reddedilirse warnings kaybolmaz, bulk task failed olur.
3. Migration upgrade-path: önceki revision'dan 20260717_003'e upgrade
   (empty-DB baseline create_all maskesini kırar).
"""
import pytest

from app.config import settings
from app.database.models import ScoringRun, SocialCategory
from app.schemas.social import PolicyWarning, SocialCategoriesRequest


class TestMidGenerationReassignmentRace:
    def test_categories_not_saved_when_reassigned_mid_generation(
        self, db_session, make_workspace, make_scoring_run, monkeypatch
    ):
        """Gerçek yarış: üretim (uzun AI) sırasında BAŞKA session'da
        reassignment → kategori kaydı REDDEDİLİR + assignment_changed uyarısı.

        Codex v8-1: reassignment PRODUCTION yolundan tetiklenir
        (enqueue_channel_assignment) — transition() değil; dispatcher yan
        etkileri (version artışı) uygulamazsa bu test kırılır."""
        from app.core.channel.assignment_dispatcher import enqueue_channel_assignment
        from app.database.connection import SessionLocal
        from app.generators.social.social_generator import SocialGenerator

        ws = make_workspace("Race WS")
        run = make_scoring_run(brand_profile_id=ws.id, status="channel_assigned")
        run_id = run.id
        # SOCIAL havuzu keyword'ü (generate_categories havuzdan okur)
        from app.database.models import ChannelPool, Keyword
        kw = Keyword(keyword="borsa analiz", normalized_keyword="borsa analiz")
        db_session.add(kw)
        db_session.flush()
        db_session.add(ChannelPool(
            scoring_run_id=run_id, keyword_id=kw.id, channel="SOCIAL",
            final_rank=1, adjusted_score=10,
        ))
        db_session.commit()

        from app.schemas.social import CategoryTypeEnum, SocialCategorySchema

        monkeypatch.setattr(
            "app.tasks.intent_tasks.run_channel_assignment_task.apply_async",
            lambda *, args, task_id, **kwargs: None,
        )

        def fake_generate(self, keywords, brand_name=None, brand_context=None,
                          max_categories=6, excluded_topics="—"):
            # "Uzun AI çağrısı" sırasında reassignment: AYRI session,
            # GERÇEK dispatch yolu (manuel/otomatik assign bunu kullanır)
            other = SessionLocal()
            try:
                other_run = other.get(ScoringRun, run_id)
                assert enqueue_channel_assignment(
                    other, other_run,
                    relevance_coefficient=1.0,
                    from_status=other_run.status,
                )["status"] == "pending"
            finally:
                other.close()
            return [SocialCategorySchema(
                category_name="Eğitim", category_type=CategoryTypeEnum.EDUCATIONAL,
                description="d", relevance_score=0.8, suggested_keywords=[],
            )]

        monkeypatch.setattr(
            "app.generators.social.category_generator.CategoryGenerator.generate",
            fake_generate,
        )

        worker_db = SessionLocal()
        try:
            gen = SocialGenerator(worker_db, ai_service=None)
            resp = gen.generate_categories(
                SocialCategoriesRequest(scoring_run_id=run_id, brand_name="M")
            )
        finally:
            worker_db.close()

        assert resp.total_categories == 0
        assert any(
            w.reason_code == "assignment_changed" for w in resp.policy_warnings
        )
        # Hiçbir fresh kategori kaydedilmedi
        db_session.expire_all()
        fresh = db_session.query(SocialCategory).filter(
            SocialCategory.scoring_run_id == run_id,
            SocialCategory.is_stale.is_(False),
        ).count()
        assert fresh == 0

    def test_version_increments_on_reassignment(
        self, db_session, make_workspace, make_scoring_run
    ):
        from app.core.scoring.state_machine import transition

        ws = make_workspace("Ver WS")
        run = make_scoring_run(brand_profile_id=ws.id, status="scored")
        v1 = run.channel_assignment_version or 1
        transition(db_session, run, "channel_assigning")
        db_session.expire_all()
        assert db_session.get(ScoringRun, run.id).channel_assignment_version == v1 + 1


class TestDispatcherSideEffects:
    def test_enqueue_path_applies_version_and_all_stale_marks(
        self, db_session, make_workspace, make_scoring_run, monkeypatch
    ):
        """Codex v8-1: PRODUCTION dispatch yolu (enqueue_channel_assignment)
        transition_atomic kullanıyordu — version artışı ve ADS/SOCIAL stale
        işaretleri sahada HİÇ koşmuyordu. begin_channel_assignment CAS +
        tüm yan etkileri tek transaction'da uygular."""
        from app.core.channel.assignment_dispatcher import enqueue_channel_assignment
        from app.database.models import (
            AdGenerationSet,
            ContentOutput,
            Keyword,
            SocialCategory,
            SocialContent,
            SocialIdea,
        )

        ws = make_workspace("Dispatch WS")
        run = make_scoring_run(brand_profile_id=ws.id, status="scored")
        run_id = run.id
        v0 = run.channel_assignment_version or 1

        kw = Keyword(keyword="dispatch kw", normalized_keyword="dispatch kw")
        db_session.add(kw)
        db_session.flush()
        db_session.add(ContentOutput(
            scoring_run_id=run_id, keyword_id=kw.id, channel="SEO",
            content_type="blog_post", content_data={},
        ))
        db_session.add(AdGenerationSet(
            scoring_run_id=run_id, version_number=1, status="active",
        ))
        cat = SocialCategory(scoring_run_id=run_id, category_name="Eğitim")
        db_session.add(cat)
        db_session.flush()
        idea = SocialIdea(category_id=cat.id, idea_title="Fikir")
        db_session.add(idea)
        db_session.flush()
        db_session.add(SocialContent(idea_id=idea.id, caption="c"))
        db_session.commit()
        cat_id, idea_id = cat.id, idea.id

        monkeypatch.setattr(
            "app.tasks.intent_tasks.run_channel_assignment_task.apply_async",
            lambda *, args, task_id, **kwargs: None,
        )

        result = enqueue_channel_assignment(
            db_session, run, relevance_coefficient=1.0, from_status="scored",
        )
        assert result["status"] == "pending"

        db_session.expire_all()
        fresh = db_session.get(ScoringRun, run_id)
        assert fresh.status == "channel_assigning"
        assert fresh.channel_assignment_version == v0 + 1
        assert db_session.query(AdGenerationSet).filter_by(
            scoring_run_id=run_id
        ).one().is_stale is True
        assert db_session.get(SocialCategory, cat_id).is_stale is True
        assert db_session.get(SocialIdea, idea_id).is_stale is True
        assert db_session.query(SocialContent).filter_by(
            idea_id=idea_id
        ).one().is_stale is True
        assert db_session.query(ContentOutput).filter_by(
            scoring_run_id=run_id
        ).one().is_stale is True

    def test_enqueue_cas_conflict_applies_no_side_effects(
        self, db_session, make_workspace, make_scoring_run, monkeypatch
    ):
        """CAS kaybedilirse (status başka aşamaya geçmiş) yan etki UYGULANMAZ:
        version artmaz, stale işareti düşmez, task failed olur."""
        from app.core.channel.assignment_dispatcher import enqueue_channel_assignment
        from app.database.models import AdGenerationSet

        ws = make_workspace("CAS WS")
        run = make_scoring_run(brand_profile_id=ws.id, status="scoring")
        run_id = run.id
        v0 = run.channel_assignment_version or 1
        db_session.add(AdGenerationSet(
            scoring_run_id=run_id, version_number=1, status="active",
        ))
        db_session.commit()

        monkeypatch.setattr(
            "app.tasks.intent_tasks.run_channel_assignment_task.apply_async",
            lambda *, args, task_id, **kwargs: None,
        )

        # from_status="scored" iddia edilir ama gerçek status "scoring" →
        # CAS başarısız (geçiş izinli görünür ama satır eşleşmez)
        result = enqueue_channel_assignment(
            db_session, run, relevance_coefficient=1.0, from_status="scored",
        )
        assert result["status"] == "failed"

        db_session.expire_all()
        fresh = db_session.get(ScoringRun, run_id)
        assert fresh.status == "scoring"
        assert (fresh.channel_assignment_version or 1) == v0
        assert db_session.query(AdGenerationSet).filter_by(
            scoring_run_id=run_id
        ).one().is_stale is False


class TestAllCategoriesRejected:
    def test_empty_response_carries_policy_warnings(
        self, db_session, make_workspace, make_scoring_run
    ):
        from app.generators.social.social_generator import SocialGenerator

        gen = SocialGenerator(db_session, ai_service=None)
        resp = gen._empty_response(1, policy_warnings=[PolicyWarning(
            entity_type="category", display_name="X",
            reason_code="excluded_topic", matched_term="temettü",
        )])
        assert len(resp.policy_warnings) == 1

    def test_bulk_task_fails_when_all_categories_rejected(
        self, db_session, make_workspace, make_scoring_run, monkeypatch
    ):
        """Codex A+B-2 zorunlu test: tüm kategoriler policy reddi →
        task FAILED + warnings result_data'da KORUNUR.

        K12: legacy bulk gorevi varsayilan olarak kapali (ENABLE_SOCIAL_
        LEGACY_BULK=False) — bu test generate_full_pipeline'in kendi
        davranisini olctugu icin bayragi acar, gate'i test etmez."""
        from types import SimpleNamespace

        from app.tasks import generation_tasks
        from app.tasks.task_status import create_task_record
        from app.database.models import TaskResult

        monkeypatch.setattr(settings, "ENABLE_SOCIAL_LEGACY_BULK", True)

        ws = make_workspace("AllRej WS")
        run = make_scoring_run(brand_profile_id=ws.id, status="channel_assigned")
        task_id = "social-all-rejected"
        create_task_record(task_id, "social", run.id)

        monkeypatch.setattr(
            "app.generators.ai_service.get_ai_service", lambda: SimpleNamespace()
        )

        rejected = PolicyWarning(
            entity_type="category", display_name="Temettü Rehberi",
            reason_code="excluded_topic", matched_term="temettü",
        )

        def fake_pipeline(self, request):
            return self._empty_response(
                request.scoring_run_id, policy_warnings=[rejected]
            )

        monkeypatch.setattr(
            "app.generators.social.social_generator.SocialGenerator."
            "generate_full_pipeline",
            fake_pipeline,
        )

        generation_tasks.generate_social_task.apply(
            kwargs={"scoring_run_id": run.id, "brand_name": "M"},
            task_id=task_id,
        )

        db_session.expire_all()
        row = db_session.query(TaskResult).filter_by(task_id=task_id).one()
        assert row.status == "failed"
        warnings = (row.result_data or {}).get("policy_warnings") or []
        assert warnings and warnings[0]["matched_term"] == "temettü"


class TestPolicyMigrationUpgradePath:
    def test_upgrade_from_previous_revision_adds_columns(self, db_engine, db_session):
        """Empty-DB testi baseline create_all nedeniyle kolon-ekleme yolunu
        maskeler — bu test 20260716_001'e inip yeniden head'e çıkar."""
        import subprocess

        def alembic(*args):
            result = subprocess.run(
                ["alembic", *args], capture_output=True, text=True, cwd="/app"
            )
            assert result.returncode == 0, result.stderr
            return result.stdout

        try:
            alembic("downgrade", "20260716_001")
            from sqlalchemy import inspect as sa_inspect
            insp = sa_inspect(db_engine)
            cols = {c["name"] for c in insp.get_columns("brand_profiles")}
            assert "competitor_terms" not in cols  # gerçekten indik

            alembic("upgrade", "head")
            insp = sa_inspect(db_engine)
            cols = {c["name"] for c in insp.get_columns("brand_profiles")}
            for name in ("competitor_terms", "competitor_policy",
                         "topic_policy", "capabilities"):
                assert name in cols, name
            run_cols = {c["name"] for c in insp.get_columns("scoring_runs")}
            assert "execution_manifest" in run_cols
            assert "channel_assignment_version" in run_cols
            assert "ai_usage_events" in insp.get_table_names()
        finally:
            # Her durumda head'e dön (diğer testler tam şema bekler)
            alembic("upgrade", "head")
