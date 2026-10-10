"""Eski motor (v2 / v2_1) run'ları salt-okunurdur.

Ürün kararı: aktif tek motor v3. Mevcut v2/v2_1 run'ları üzerinden YENİ
üretim (skorlama, relevance/embedding, kanal ataması, screening, SEO/ADS/
SOCIAL içerik, social brief akışı) hem API
sınırında hem task/dispatcher sınırında, HERHANGİ bir ücretli çağrıdan ve
Celery dispatch'ten ÖNCE tipli olarak reddedilir. Okuma, geçmiş, listeleme ve
export değişmeden çalışır. v3 run'ları bu kapıdan etkilenmez.

Casuslar çağrılırsa PATLAR: AI servisi, embedding (relevance), screening
kararı/planı, Google Ads servisi, site crawl/profil analizi, Celery
apply_async/delay/chord. Her blok ucu için ayrıca "yeni satır yazılmadı,
mevcut satırlar değişmedi" kontrol edilir.
"""
from __future__ import annotations

import uuid

import pytest

from app.config import settings
from app.core.engine_version_gate import (
    LEGACY_RUN_READ_ONLY,
    LEGACY_RUN_READ_ONLY_REASON,
    LegacyRunReadOnlyError,
    is_legacy_run,
    legacy_run_detail,
)
from app.database.models import (
    AdGenerationSet,
    AdGroup,
    ChannelAssignmentAttempt,
    ChannelPool,
    ContentOutput,
    CorpusScreeningJob,
    ExportJob,
    KeywordRelevance,
    KeywordScore,
    ScoringRun,
    SEOGeoContent,
    SocialBrief,
    SocialCategory,
    SocialContent,
    SocialGenerationAttempt,
    SocialIdea,
    TaskResult,
)

LEGACY_VERSIONS = ["v2", "v2_1"]

# Satır sayımı yapılan tablolar: blok uçları HİÇBİRİNE yeni satır yazmamalı
COUNTED_MODELS = [
    TaskResult, ChannelAssignmentAttempt, CorpusScreeningJob, ChannelPool,
    KeywordScore, KeywordRelevance, ContentOutput, SEOGeoContent,
    AdGenerationSet, AdGroup, SocialBrief, SocialGenerationAttempt,
    SocialCategory, SocialIdea, SocialContent, ExportJob,
]


class PaidCallAttempted(AssertionError):
    """Ücretli/dispatch sınırına ulaşıldı — kapı çalışmadı."""


class ExplodingAI:
    """Her AI erişiminde patlayan servis; yalnız collector ataması ve
    close serbesttir (task/dependency iskeleti bunları yapar)."""

    def __init__(self, calls):
        object.__setattr__(self, "_calls", calls)
        object.__setattr__(self, "collector", None)

    def __setattr__(self, name, value):
        object.__setattr__(self, name, value)

    def close(self):
        return None

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        if name == "for_stage":
            # Aşama kapsamlama (ChannelEngine kurucusu vb.) çağrı DEĞİLDİR
            return lambda *a, **k: ExplodingAI(self._calls)

        def _call(*args, **kwargs):
            self._calls.append(f"ai.{name}")
            raise PaidCallAttempted(f"AI servisi kullanıldı: {name}")
        return _call


@pytest.fixture
def paid_calls(monkeypatch):
    """Tüm ücretli / dispatch sınırlarını patlayan casuslarla değiştirir."""
    calls: list[str] = []

    def boom(label):
        def _boom(*args, **kwargs):
            calls.append(label)
            raise PaidCallAttempted(label)
        return _boom

    # AI servisi (Celery task'ları ve endpoint'ler)
    monkeypatch.setattr("app.generators.ai_service.get_ai_service",
                        lambda *a, **k: ExplodingAI(calls))
    monkeypatch.setattr("app.tasks.intent_tasks.get_ai_service",
                        lambda *a, **k: ExplodingAI(calls))
    # Kanal motorları
    monkeypatch.setattr("app.tasks.intent_tasks.ChannelEngine",
                        boom("ChannelEngine"))
    monkeypatch.setattr("app.tasks.intent_tasks.run_v3_orchestration",
                        boom("run_v3_orchestration"))
    # Embedding / relevance
    monkeypatch.setattr("app.core.relevance.refresh_keyword_relevance",
                        boom("refresh_keyword_relevance"))
    monkeypatch.setattr("app.api.v1.brand_profile._run_relevance_computation",
                        boom("_run_relevance_computation"))
    # Skorlama motoru
    monkeypatch.setattr(
        "app.core.scoring.score_engine.ScoreEngine.run_scoring",
        boom("ScoreEngine.run_scoring"))
    # Screening kararı / planı / sağlayıcı
    monkeypatch.setattr(
        "app.core.screening.auto_trigger.decide_screening_mode",
        boom("decide_screening_mode"))
    monkeypatch.setattr("app.core.screening.dispatch.plan_dispatch",
                        boom("plan_dispatch"))
    monkeypatch.setattr(
        "app.core.screening.production_runner.run_screening_job",
        boom("run_screening_job"))
    monkeypatch.setattr("app.core.screening.inflight.build_limiter",
                        boom("build_limiter"))
    # Google Ads
    monkeypatch.setattr(
        "app.integrations.google_ads.service.GoogleAdsService.__init__",
        boom("GoogleAdsService"))
    # Celery dispatch
    from app.tasks import (
        export_tasks, generation_tasks, intent_tasks, screening_tasks,
    )
    for task in (
        intent_tasks.run_channel_assignment_task,
        generation_tasks.generate_seo_chunk_task,
        generation_tasks.generate_ads_task,
        generation_tasks.generate_social_task,
        generation_tasks.social_contents_task,
        generation_tasks.social_brief_ideas_task,
        generation_tasks.social_brief_ideas_retry_task,
        generation_tasks.social_brief_contents_task,
        screening_tasks.run_corpus_screening_task,
        export_tasks.run_export_task,
    ):
        monkeypatch.setattr(task, "apply_async",
                            boom(f"{task.name}.apply_async"))
        monkeypatch.setattr(task, "delay", boom(f"{task.name}.delay"))
    monkeypatch.setattr("app.tasks.generation_tasks.chord", boom("chord"))
    return calls


@pytest.fixture
def api(client, db_session, paid_calls, monkeypatch):
    """TestClient: iki get_db de test oturumuna, get_ai patlayan servise."""
    from app.dependencies import get_ai, get_db
    from app.main import app

    def _db():
        yield db_session

    def _ai():
        yield ExplodingAI(paid_calls)

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_ai] = _ai
    for flag in ("ENABLE_SOCIAL_BRIEF_FLOW", "ENABLE_SOCIAL_LEGACY_BULK",
                 "ENABLE_SITE_PROFILE_ANALYSIS", "ENABLE_RELEVANCE_RERANK"):
        monkeypatch.setattr(settings, flag, True)
    try:
        yield client
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_ai, None)


def _build_env(db_session, make_workspace, make_keyword, make_scoring_run,
               make_keyword_score, *, version: str, status: str = "completed",
               name: str = "legacy"):
    """Doğrudan eklenmiş tarihsel run + havuz + içerik + brief satırları."""
    ws = make_workspace(
        name=f"{name}-{version}-ws",
        profile_data={"anchor_texts": ["test çapa"], "brand_name": "Marka"},
    )
    kw = make_keyword(f"{name} kelime {version}", brand_profile_id=ws.id)
    extra = {}
    if version == "v2_1":
        # Tarihsel BAŞARILI v2_1 havuzu: strateji sürümü finalize'da yazılmıştı
        extra["channel_pool_strategy_version"] = int(ws.strategy_version or 0)
    run = make_scoring_run(brand_profile_id=ws.id, status=status,
                           algorithm_version=version, skip_relevance=False,
                           **extra)
    make_keyword_score(scoring_run_id=run.id, keyword_id=kw.id,
                       ads_score=10, seo_score=20, social_score=30,
                       ads_rank=1, seo_rank=1, social_rank=1)
    for channel in ("ADS", "SEO", "SOCIAL"):
        db_session.add(ChannelPool(scoring_run_id=run.id, keyword_id=kw.id,
                                   channel=channel, final_rank=1))
    output = ContentOutput(scoring_run_id=run.id, keyword_id=kw.id,
                           channel="SEO", content_type="blog_post",
                           content_data={"title": "Eski blog"})
    db_session.add(output)
    db_session.flush()
    seo = SEOGeoContent(content_output_id=output.id, keyword_id=kw.id,
                        title="Eski blog", intro_paragraph="Giriş",
                        body_content="Gövde")
    ads_set = AdGenerationSet(scoring_run_id=run.id, version_number=1,
                              status="active", is_stale=False)
    db_session.add_all([seo, ads_set])
    db_session.flush()
    group = AdGroup(scoring_run_id=run.id, generation_set_id=ads_set.id,
                    group_name="Eski grup", target_keyword_ids=[kw.id],
                    target_keywords=[kw.keyword])
    category = SocialCategory(scoring_run_id=run.id, category_name="Eğitim",
                              category_type="educational", description="d")
    db_session.add_all([group, category])
    db_session.flush()
    idea = SocialIdea(category_id=category.id, keyword_id=kw.id,
                      idea_title="Eski fikir", idea_description="d",
                      target_platform="instagram", content_format="post",
                      is_selected=True)
    db_session.add(idea)
    db_session.flush()
    content = SocialContent(idea_id=idea.id, caption="Eski içerik",
                            hooks=[{"text": "kanca", "style": "question"}],
                            hashtags=["#eski", "#bir", "#iki", "#uc", "#dort"])
    brief = SocialBrief(scoring_run_id=run.id,
                        channel_assignment_version=run.channel_assignment_version)
    db_session.add_all([content, brief])
    db_session.commit()
    for row in (run, kw, seo, ads_set, group, category, idea, content, brief):
        db_session.refresh(row)
    return {
        "ws": ws, "kw": kw, "run": run, "seo": seo, "ads_set": ads_set,
        "group": group, "category": category, "idea": idea,
        "content": content, "brief": brief,
    }


@pytest.fixture(params=LEGACY_VERSIONS)
def legacy(request, db_session, make_workspace, make_keyword,
           make_scoring_run, make_keyword_score):
    return _build_env(db_session, make_workspace, make_keyword,
                      make_scoring_run, make_keyword_score,
                      version=request.param)


def _counts(db_session):
    db_session.expire_all()
    return {m.__tablename__: db_session.query(m).count()
            for m in COUNTED_MODELS}


def _run_state(db_session, run_id):
    db_session.expire_all()
    run = db_session.get(ScoringRun, run_id)
    return {
        "status": run.status,
        "algorithm_version": run.algorithm_version,
        "channel_assignment_version": run.channel_assignment_version,
        "execution_manifest": run.execution_manifest,
        "company_url": run.company_url,
        "competitor_urls": run.competitor_urls,
    }


def _content_state(db_session, env):
    db_session.expire_all()
    return {
        "pools": sorted(
            (p.channel, p.keyword_id, p.final_rank)
            for p in db_session.query(ChannelPool)
            .filter(ChannelPool.scoring_run_id == env["run"].id)),
        "ads_set": (db_session.get(AdGenerationSet, env["ads_set"].id).status,
                    db_session.get(AdGenerationSet, env["ads_set"].id).is_stale),
        "idea": (db_session.get(SocialIdea, env["idea"].id).idea_title,
                 db_session.get(SocialIdea, env["idea"].id).is_selected),
        "content": db_session.get(SocialContent, env["content"].id).caption,
        "seo": db_session.get(SEOGeoContent, env["seo"].id).title,
        "brief_locked": db_session.get(SocialBrief, env["brief"].id).locked_at,
    }


def _blocked_requests(env):
    """(etiket, method, url, json) — eski run'da yeni üretim başlatan uçlar."""
    ws, run = env["ws"].id, env["run"].id
    q = f"brand_profile_id={ws}"
    brief = env["brief"].id
    return [
        ("scoring.execute", "post",
         f"/api/v1/scoring/runs/{run}/execute?{q}", None),
        ("channels.assign", "post",
         f"/api/v1/channels/runs/{run}/assign?{q}", {}),
        ("channels.assign.screening", "post",
         f"/api/v1/channels/runs/{run}/assign?{q}",
         {"screening_mode": "assistive"}),
        ("relevance.compute", "post",
         f"/api/v1/brand-profile/runs/{run}/relevance/compute?{q}", None),
        ("seo_geo.single", "post", f"/api/v1/generation/seo-geo?{q}",
         {"keyword_id": env["kw"].id, "scoring_run_id": run}),
        ("seo_geo.bulk", "post",
         f"/api/v1/generation/seo-geo/bulk/{run}?{q}", None),
        ("ads.rsa", "post", f"/api/v1/generation/ads/rsa?{q}",
         {"scoring_run_id": run}),
        ("ads.group_regenerate", "post",
         f"/api/v1/generation/ads/rsa/group/{env['group'].id}/regenerate?{q}",
         {}),
        ("social.categories", "post",
         f"/api/v1/generation/social/categories?{q}",
         {"scoring_run_id": run}),
        ("social.ideas", "post", f"/api/v1/generation/social/ideas?{q}",
         {"category_ids": [env["category"].id]}),
        ("social.contents", "post",
         f"/api/v1/generation/social/contents?{q}",
         {"idea_ids": [env["idea"].id]}),
        ("social.contents_async", "post",
         f"/api/v1/generation/social/contents/async?{q}",
         {"idea_ids": [env["idea"].id]}),
        ("social.bulk", "post", f"/api/v1/generation/social/bulk?{q}",
         {"scoring_run_id": run}),
        ("social.idea_regenerate", "post",
         f"/api/v1/generation/social/ideas/{env['idea'].id}/regenerate?{q}",
         {}),
        ("social.content_regenerate", "post",
         f"/api/v1/generation/social/contents/{env['content'].id}"
         f"/regenerate?{q}", {}),
        ("brief.create", "post", f"/api/v1/generation/social/briefs?{q}",
         {"scoring_run_id": run, "keyword_ids": [env["kw"].id],
          "targets": [{"platform": "instagram", "content_format": "post"}]}),
        ("brief.categories", "post",
         f"/api/v1/generation/social/briefs/{brief}/categories/generate?{q}",
         {"idempotency_key": "legacy-cat", "max_categories": 4}),
        ("brief.ideas", "post",
         f"/api/v1/generation/social/briefs/{brief}/ideas/generate?{q}",
         {"idempotency_key": "legacy-idea",
          "category_ids": [env["category"].id], "ideas_per_category": 3}),
        ("brief.ideas_retry", "post",
         f"/api/v1/generation/social/briefs/{brief}/ideas/retry?{q}",
         {"idempotency_key": "legacy-retry", "source_attempt_id": 1}),
        ("brief.contents", "post",
         f"/api/v1/generation/social/briefs/{brief}/contents/async?{q}",
         {"idempotency_key": "legacy-content",
          "idea_ids": [env["idea"].id]}),
    ]


BLOCKED_LABELS = [
    "scoring.execute", "channels.assign", "channels.assign.screening",
    "relevance.compute",
    "seo_geo.single", "seo_geo.bulk", "ads.rsa", "ads.group_regenerate",
    "social.categories", "social.ideas", "social.contents",
    "social.contents_async", "social.bulk", "social.idea_regenerate",
    "social.content_regenerate", "brief.create", "brief.categories",
    "brief.ideas", "brief.ideas_retry", "brief.contents",
]


# ==================== Saf kapı semantiği ====================

@pytest.mark.parametrize("value,legacy_expected", [
    ("v2", True), ("v2_1", True), (None, True), ("", True),
    ("v4", True), ("v3", False),
])
def test_is_legacy_run_semantics(value, legacy_expected):
    run = type("Run", (), {"algorithm_version": value})()
    assert is_legacy_run(run) is legacy_expected


def test_legacy_detail_contract():
    run = type("Run", (), {"algorithm_version": "v2_1"})()
    detail = legacy_run_detail(run)
    assert detail["code"] == LEGACY_RUN_READ_ONLY
    assert detail["algorithm_version"] == "v2_1"
    assert "yalnız okunabilir" in detail["message"]
    assert "V3" in detail["message"]


# ==================== API sınırı: blok ====================

@pytest.mark.parametrize("label", BLOCKED_LABELS)
def test_blocked_endpoint_refuses_legacy_run(label, api, db_session, legacy,
                                             paid_calls):
    request = {r[0]: r for r in _blocked_requests(legacy)}[label]
    _, method, url, body = request
    before_counts = _counts(db_session)
    before_run = _run_state(db_session, legacy["run"].id)
    before_content = _content_state(db_session, legacy)

    kwargs = {} if body is None else {"json": body}
    resp = getattr(api, method)(url, **kwargs)

    assert resp.status_code == 409, (label, resp.status_code, resp.text)
    detail = resp.json()["detail"]
    assert detail["code"] == LEGACY_RUN_READ_ONLY, (label, detail)
    assert detail["algorithm_version"] == legacy["run"].algorithm_version
    assert "yalnız okunabilir" in detail["message"]
    assert paid_calls == [], (label, paid_calls)
    assert _counts(db_session) == before_counts, label
    assert _run_state(db_session, legacy["run"].id) == before_run, label
    assert _content_state(db_session, legacy) == before_content, label


def test_blocked_endpoint_does_not_leak_other_workspace(
        api, db_session, legacy, make_workspace, paid_calls):
    """Kapı varlık sızdırmaz: başka workspace'in eski run'ı 404 kalır."""
    other = make_workspace(name="baska-ws")
    resp = api.post(
        f"/api/v1/channels/runs/{legacy['run'].id}/assign"
        f"?brand_profile_id={other.id}", json={})
    assert resp.status_code == 404
    resp = api.post(
        f"/api/v1/generation/social/briefs?brand_profile_id={other.id}",
        json={"scoring_run_id": legacy["run"].id,
              "keyword_ids": [legacy["kw"].id],
              "targets": [{"platform": "instagram",
                           "content_format": "post"}]})
    assert resp.status_code != 409
    assert paid_calls == []


# ==================== API sınırı: okuma çalışmaya devam eder ====================

def test_read_endpoints_still_work_for_legacy_run(api, db_session, legacy,
                                                  paid_calls):
    ws, run = legacy["ws"].id, legacy["run"].id
    q = f"brand_profile_id={ws}"
    reads = [
        f"/api/v1/scoring/runs?{q}",
        f"/api/v1/scoring/runs/{run}?{q}",
        f"/api/v1/scoring/runs/{run}/scores?{q}",
        f"/api/v1/channels/runs/{run}/pools?{q}",
        f"/api/v1/channels/runs/{run}/pools/SEO?{q}",
        f"/api/v1/generation/seo-geo/list/{run}?{q}",
        f"/api/v1/generation/ads/sets?scoring_run_id={run}&{q}",
        f"/api/v1/generation/ads/rsa/{run}?{q}",
        f"/api/v1/generation/ads/rsa/group/{legacy['group'].id}?{q}",
        f"/api/v1/generation/social/{run}?{q}",
        f"/api/v1/generation/social/briefs?scoring_run_id={run}&{q}",
        f"/api/v1/generation/social/briefs/{legacy['brief'].id}?{q}",
        f"/api/v1/generation/social/briefs/{legacy['brief'].id}/state?{q}",
        f"/api/v1/generation/social/contents/history?{q}",
        f"/api/v1/tasks/run/{run}?{q}",
        f"/api/v1/brand-profile/runs/{run}/relevance?{q}",
        f"/api/v1/export/run/{run}?{q}",
    ]
    for url in reads:
        resp = api.get(url)
        assert resp.status_code == 200, (url, resp.status_code, resp.text)

    scores = api.get(f"/api/v1/scoring/runs/{run}/scores?{q}").json()
    assert scores["total"] >= 1 if "total" in scores else True
    pools = api.get(f"/api/v1/channels/runs/{run}/pools?{q}").json()
    assert pools  # tarihsel havuz satırları okunur
    seo_list = api.get(f"/api/v1/generation/seo-geo/list/{run}?{q}").json()
    assert seo_list["total"] == 1
    social = api.get(f"/api/v1/generation/social/{run}?{q}").json()
    assert social  # tarihsel sosyal içerik okunur
    assert paid_calls == []


def test_export_still_works_for_legacy_run(api, db_session, legacy,
                                           paid_calls, monkeypatch):
    """Export OKUMADIR: eski run için rapor işi açılır ve kuyruğa verilir."""
    queued: list[str] = []
    from app.tasks import export_tasks

    monkeypatch.setattr(export_tasks.run_export_task, "delay",
                        lambda export_id: queued.append(export_id))
    ws, run = legacy["ws"].id, legacy["run"].id
    resp = api.post(f"/api/v1/export/?brand_profile_id={ws}",
                    json={"scoring_run_id": run, "format": "csv"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "pending"
    assert queued == [body["export_id"]]
    status = api.get(
        f"/api/v1/export/{body['export_id']}/status?brand_profile_id={ws}")
    assert status.status_code == 200
    assert paid_calls == []


# ==================== Dispatcher / otomatik zincir sınırı ====================

def test_dispatcher_refuses_legacy_before_screening(db_session, legacy,
                                                    paid_calls):
    from app.core.channel.assignment_dispatcher import (
        ChannelAssignmentPreconditionError,
        enqueue_channel_assignment,
    )

    before = _counts(db_session)
    before_run = _run_state(db_session, legacy["run"].id)
    for approved in (None, "assistive", "shadow"):
        with pytest.raises(ChannelAssignmentPreconditionError) as exc:
            enqueue_channel_assignment(
                db_session, legacy["run"], relevance_coefficient=1.0,
                from_status="completed", approved_screening_mode=approved)
        assert exc.value.code == LEGACY_RUN_READ_ONLY
    assert paid_calls == []
    assert _counts(db_session) == before
    assert _run_state(db_session, legacy["run"].id) == before_run


def test_auto_relevance_chain_refuses_legacy(db_session, make_workspace,
                                             make_keyword, make_scoring_run,
                                             make_keyword_score, paid_calls,
                                             monkeypatch):
    """Otomatik relevance (Path A + background) eski run'da embedding
    çağırmaz ve kanal atamasını zincirlemez."""
    env = _build_env(db_session, make_workspace, make_keyword,
                     make_scoring_run, make_keyword_score, version="v2",
                     status="scored", name="auto")
    monkeypatch.setattr(settings, "ENABLE_RELEVANCE_RERANK", True)
    from fastapi import BackgroundTasks

    from app.api.v1 import brand_profile as bp

    bg = BackgroundTasks()
    bp._trigger_latest_run_relevance(db_session, bg, env["ws"])
    assert bg.tasks == []
    assert _run_state(db_session, env["run"].id)["status"] == "scored"

    # Background giriş noktası doğrudan çağrılırsa da (ör. deploy öncesi
    # kuyruğa girmiş iş) embedding YOK; relevance_computing'de takılı kalmaz
    run = db_session.get(ScoringRun, env["run"].id)
    run.status = "relevance_computing"
    db_session.commit()
    monkeypatch.undo()  # _run_relevance_computation casusunu kaldır
    monkeypatch.setattr("app.core.relevance.refresh_keyword_relevance",
                        lambda *a, **k: paid_calls.append("embed"))
    monkeypatch.setattr(
        "app.core.channel.assignment_dispatcher.enqueue_channel_assignment",
        lambda *a, **k: paid_calls.append("enqueue"))
    from app.tasks.scoring_tasks import _run_relevance_computation

    _run_relevance_computation(scoring_run_id=env["run"].id)
    assert paid_calls == []
    assert _run_state(db_session, env["run"].id)["status"] == "failed"


# ==================== Task sınırı: fail-closed ====================

def _task(db_session, run_id, task_type, status="pending"):
    tid = str(uuid.uuid4())
    db_session.add(TaskResult(task_id=tid, task_type=task_type,
                              scoring_run_id=run_id, status=status,
                              progress=0))
    db_session.commit()
    return tid


def _task_row(db_session, tid):
    db_session.expire_all()
    return db_session.query(TaskResult).filter(TaskResult.task_id == tid).one()


def test_channel_assignment_task_fails_closed(db_session, legacy, paid_calls):
    from app.tasks.intent_tasks import run_channel_assignment_task

    run = db_session.get(ScoringRun, legacy["run"].id)
    run.status = "channel_assigning"
    db_session.commit()
    tid = _task(db_session, run.id, "channel_assignment")

    result = run_channel_assignment_task.apply(
        args=[run.id, 1.0], task_id=tid).get()

    assert result["status"] == "failed"
    assert result["reason"] == LEGACY_RUN_READ_ONLY
    assert paid_calls == []
    row = _task_row(db_session, tid)
    assert row.status == "failed"
    assert (row.result_data or {}).get("reason") == LEGACY_RUN_READ_ONLY
    assert _run_state(db_session, run.id)["status"] == "failed"
    # Tarihsel havuz silinmedi
    assert db_session.query(ChannelPool).filter(
        ChannelPool.scoring_run_id == run.id).count() == 3


def test_screening_task_fails_closed(db_session, legacy, paid_calls):
    from app.tasks.screening_tasks import run_corpus_screening_task

    attempt = ChannelAssignmentAttempt(
        parent_task_id=str(uuid.uuid4()), scoring_run_id=legacy["run"].id,
        brand_profile_id=legacy["ws"].id, status="pending",
        phase="screening", screening_mode="off")
    db_session.add(attempt)
    db_session.commit()
    child = _task(db_session, legacy["run"].id, "corpus_screening")

    result = run_corpus_screening_task.apply(
        kwargs={"job_id": 987654, "attempt_id": attempt.id,
                "expected_screening_cap_usd": "1.0",
                "expected_downstream_cap_usd": "1.0",
                # geçersiz payload: parent HİÇBİR koşulda kuyruğa girmez
                "deferred_parent": {"parent_task_id": "x",
                                    "attempt_id": attempt.id}},
        task_id=child).get()

    assert result["status"] == "failed"
    assert result["reason"] == LEGACY_RUN_READ_ONLY
    assert paid_calls == []
    assert _task_row(db_session, child).status == "failed"


def test_seo_tasks_fail_closed(db_session, legacy, paid_calls):
    from app.tasks.generation_tasks import (
        finalize_seo_bulk,
        generate_seo_chunk_task,
        start_bulk_seo_generation,
    )

    run_id = legacy["run"].id
    before = _counts(db_session)
    with pytest.raises(LegacyRunReadOnlyError):
        start_bulk_seo_generation(run_id, [legacy["kw"].id])
    assert _counts(db_session) == before  # TaskResult / chord YOK

    chunk = generate_seo_chunk_task.apply(
        args=[run_id, [legacy["kw"].id]]).get()
    assert chunk["reason"] == LEGACY_RUN_READ_ONLY
    assert chunk["content_ids"] == []

    parent = _task(db_session, run_id, "seo_content", status="running")
    out = finalize_seo_bulk.apply(args=[[chunk], parent, run_id]).get()
    assert out["reason"] == LEGACY_RUN_READ_ONLY
    row = _task_row(db_session, parent)
    assert row.status == "failed"
    assert (row.result_data or {}).get("reason") == LEGACY_RUN_READ_ONLY
    assert paid_calls == []
    assert db_session.query(SEOGeoContent).count() == 1


def test_ads_task_fails_closed(db_session, legacy, paid_calls):
    from app.tasks.generation_tasks import generate_ads_task

    tid = _task(db_session, legacy["run"].id, "ads", status="running")
    new_set = AdGenerationSet(scoring_run_id=legacy["run"].id,
                              version_number=2, status="generating",
                              is_stale=False, task_id=tid)
    db_session.add(new_set)
    db_session.commit()

    result = generate_ads_task.apply(
        kwargs={"scoring_run_id": legacy["run"].id,
                "generation_set_id": new_set.id}, task_id=tid).get()

    assert result["reason"] == LEGACY_RUN_READ_ONLY
    assert paid_calls == []
    db_session.expire_all()
    assert db_session.get(AdGenerationSet, new_set.id).status == "failed"
    assert _task_row(db_session, tid).status == "failed"
    # Tarihsel aktif set ve grubu DEĞİŞMEDİ
    assert db_session.get(AdGenerationSet, legacy["ads_set"].id).status \
        == "active"
    assert db_session.query(AdGroup).count() == 1


def test_social_tasks_fail_closed(db_session, legacy, paid_calls,
                                  monkeypatch):
    from app.tasks.generation_tasks import (
        generate_social_task,
        social_contents_task,
    )

    monkeypatch.setattr(settings, "ENABLE_SOCIAL_LEGACY_BULK", True)
    bulk_tid = str(uuid.uuid4())
    result = generate_social_task.apply(
        args=[legacy["run"].id, "Marka"], task_id=bulk_tid).get()
    assert result["reason"] == LEGACY_RUN_READ_ONLY
    assert _task_row(db_session, bulk_tid).status == "failed"

    tid = _task(db_session, legacy["run"].id, "social_content")
    result = social_contents_task.apply(
        args=[legacy["run"].id, [legacy["idea"].id], "Marka"],
        task_id=tid).get()
    assert result["reason"] == LEGACY_RUN_READ_ONLY
    assert _task_row(db_session, tid).status == "failed"
    assert paid_calls == []
    assert db_session.query(SocialContent).count() == 1


@pytest.mark.parametrize("stage,task_name", [
    ("ideas", "social_brief_ideas_task"),
    ("ideas_retry", "social_brief_ideas_retry_task"),
    ("contents", "social_brief_contents_task"),
])
def test_social_brief_tasks_fail_closed(stage, task_name, db_session, legacy,
                                        paid_calls):
    from app.tasks import generation_tasks

    attempt = SocialGenerationAttempt(
        brief_id=legacy["brief"].id, stage=stage,
        idempotency_key=f"legacy-{stage}", status="pending")
    db_session.add(attempt)
    db_session.commit()
    task = getattr(generation_tasks, task_name)

    result = task.apply(args=[attempt.id],
                        task_id=f"legacy-{stage}-task").get()

    assert result["reason"] == LEGACY_RUN_READ_ONLY
    assert result["attempt_id"] == attempt.id
    assert paid_calls == []
    db_session.expire_all()
    row = db_session.get(SocialGenerationAttempt, attempt.id)
    assert row.status == "failed"
    assert row.reason_code == LEGACY_RUN_READ_ONLY_REASON


# ==================== v3 kontrol: kapı v3'ü engellemez ====================

@pytest.fixture
def v3_env(db_session, make_workspace, make_keyword, make_scoring_run,
           make_keyword_score):
    return _build_env(db_session, make_workspace, make_keyword,
                      make_scoring_run, make_keyword_score, version="v3",
                      name="v3ctl")


def test_v3_run_is_not_blocked_by_guard(api, db_session, v3_env, paid_calls,
                                        monkeypatch):
    ws, run = v3_env["ws"].id, v3_env["run"].id
    reached: list[str] = []

    def _assign(db, scoring_run, **kwargs):
        reached.append("enqueue_channel_assignment")
        return {"task_id": "v3-task", "status": "pending",
                "effective_relevance_coefficient": 1.0}

    monkeypatch.setattr("app.api.v1.channels.enqueue_channel_assignment",
                        _assign)
    resp = api.post(
        f"/api/v1/channels/runs/{run}/assign?brand_profile_id={ws}", json={})
    assert resp.status_code == 200, resp.text
    assert reached == ["enqueue_channel_assignment"]

    def _dispatch_ads(db, scoring_run_id, **kwargs):
        reached.append("dispatch_ads")
        return {"task_id": "v3-ads"}

    monkeypatch.setattr("app.api.v1.generation._dispatch_ads", _dispatch_ads)
    resp = api.post(f"/api/v1/generation/ads/rsa?brand_profile_id={ws}",
                    json={"scoring_run_id": run})
    assert resp.status_code in (200, 202), resp.text
    assert reached[-1] == "dispatch_ads"
    assert paid_calls == []


def test_v3_task_is_not_blocked_by_guard(db_session, v3_env, paid_calls):
    """v3 run'ında task/dispatcher kapısı geçilir (ret üretmez)."""
    from app.core.engine_version_gate import legacy_run_by_id
    from app.tasks.generation_tasks import (
        _legacy_refusal,
        start_bulk_seo_generation,
    )

    run_id = v3_env["run"].id
    assert legacy_run_by_id(db_session, run_id) is None
    assert _legacy_refusal(db_session, run_id) is None
    # Dispatcher kapıyı geçer ve ilk Celery sınırına (chord casusu) ulaşır
    with pytest.raises(PaidCallAttempted):
        start_bulk_seo_generation(run_id, [v3_env["kw"].id])
    assert paid_calls == ["chord"]
