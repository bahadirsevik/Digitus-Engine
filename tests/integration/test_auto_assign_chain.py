import pytest


class _FakeRelevanceScorer:
    """RelevanceScorer'ın AI/embedding çağrısı olmadan deterministik ikamesi.

    method="embedding" ZORUNLU: fallback yasağı (P1.2, relevance/service.py)
    'embedding' dışındaki her değeri BAŞARISIZ sayar ve hiçbir satır yazmadan
    RelevanceRefreshError fırlatır. Bu fake BAŞARILI bir hesaplamayı taklit
    ediyor, o yüzden gerçek başarı etiketini taşımalı.
    """

    def __init__(self, *args, **kwargs):
        pass

    def compute_relevance(self, keywords, anchor_texts):
        return [
            {
                "keyword": kw,
                "relevance_score": 0.9,
                "matched_anchor": anchor_texts[0] if anchor_texts else "",
                "method": "embedding",
            }
            for kw in keywords
        ]


CONFIRMED_PROFILE = {
    "company_name": "Test",
    "sector": "Test sektor",
    "products": ["Urun"],
    "anchor_texts": ["Urun", "Test sektor"],
    "exclude_themes": [],
}


# ── Uçtan uca: execute_scoring → otomatik atama (tetikleme B, skip yolu) ──


def _seed_scorable_workspace(make_workspace, make_keyword):
    workspace = make_workspace(profile_data=dict(CONFIRMED_PROFILE))
    make_keyword("zincir kelime bir", brand_profile_id=workspace.id, monthly_volume=500)
    make_keyword("zincir kelime iki", brand_profile_id=workspace.id, monthly_volume=300)
    return workspace


def test_execute_scoring_skip_relevance_auto_assigns(
    client, db_session, make_workspace, make_keyword, make_scoring_run, monkeypatch
):
    from app.database.models import ScoringRun, TaskResult

    workspace = _seed_scorable_workspace(make_workspace, make_keyword)
    run = make_scoring_run(
        brand_profile_id=workspace.id,
        status="pending",
        skip_relevance=True,
        auto_assign_channels=True,
    )

    calls = []
    monkeypatch.setattr(
        "app.tasks.intent_tasks.run_channel_assignment_task.apply_async",
        lambda *, args, task_id, **kwargs: calls.append({"args": args, "task_id": task_id}),
    )

    resp = client.post(
        f"/api/v1/scoring/runs/{run.id}/execute",
        params={"brand_profile_id": workspace.id},
    )

    assert resp.status_code == 200
    assert resp.json().get("channel_assignment_task_id")
    db_session.expire_all()
    assert db_session.get(ScoringRun, run.id).status == "channel_assigning"
    task = db_session.query(TaskResult).filter_by(scoring_run_id=run.id).one()
    assert task.task_type == "channel_assignment"
    assert task.status == "pending"
    assert len(calls) == 1


def test_execute_scoring_auto_false_does_not_assign(
    client, db_session, make_workspace, make_keyword, make_scoring_run, monkeypatch
):
    from app.database.models import ScoringRun, TaskResult

    workspace = _seed_scorable_workspace(make_workspace, make_keyword)
    run = make_scoring_run(
        brand_profile_id=workspace.id,
        status="pending",
        skip_relevance=True,
        auto_assign_channels=False,
    )

    calls = []
    monkeypatch.setattr(
        "app.tasks.intent_tasks.run_channel_assignment_task.apply_async",
        lambda *, args, task_id, **kwargs: calls.append(task_id),
    )

    resp = client.post(
        f"/api/v1/scoring/runs/{run.id}/execute",
        params={"brand_profile_id": workspace.id},
    )

    assert resp.status_code == 200
    db_session.expire_all()
    # Eski davranış: skorlama scored'da durur, atama TETİKLENMEZ (regresyon kilidi)
    assert db_session.get(ScoringRun, run.id).status == "scored"
    assert db_session.query(TaskResult).filter_by(scoring_run_id=run.id).count() == 0
    assert calls == []


# ── Uçtan uca: relevance tamamlanınca otomatik atama (tetikleme A) ──


def _seed_relevance_ready_run(
    make_workspace, make_keyword, make_scoring_run, make_keyword_score, *, auto: bool
):
    workspace = make_workspace(status="confirmed", profile_data=dict(CONFIRMED_PROFILE))
    kw = make_keyword("relevance kelime", brand_profile_id=workspace.id)
    run = make_scoring_run(
        brand_profile_id=workspace.id,
        status="relevance_computing",
        auto_assign_channels=auto,
    )
    make_keyword_score(
        scoring_run_id=run.id, keyword_id=kw.id,
        ads_score=10, seo_score=10, social_score=10,
        ads_rank=1, seo_rank=1, social_rank=1,
    )
    return workspace, run


def test_relevance_completion_triggers_auto_assign(
    db_session, make_workspace, make_keyword, make_scoring_run, make_keyword_score, monkeypatch
):
    from app.api.v1.brand_profile import _run_relevance_computation
    from app.database.models import ScoringRun, TaskResult

    _, run = _seed_relevance_ready_run(
        make_workspace, make_keyword, make_scoring_run, make_keyword_score, auto=True
    )

    monkeypatch.setattr(
        "app.core.site_analyzer.relevance_scorer.RelevanceScorer", _FakeRelevanceScorer
    )
    calls = []
    monkeypatch.setattr(
        "app.tasks.intent_tasks.run_channel_assignment_task.apply_async",
        lambda *, args, task_id, **kwargs: calls.append(task_id),
    )

    _run_relevance_computation(scoring_run_id=run.id)

    db_session.expire_all()
    assert db_session.get(ScoringRun, run.id).status == "channel_assigning"
    task = db_session.query(TaskResult).filter_by(scoring_run_id=run.id).one()
    assert task.task_type == "channel_assignment"
    assert len(calls) == 1


def test_relevance_completion_auto_false_stays_relevance_computed(
    db_session, make_workspace, make_keyword, make_scoring_run, make_keyword_score, monkeypatch
):
    from app.api.v1.brand_profile import _run_relevance_computation
    from app.database.models import ScoringRun, TaskResult

    _, run = _seed_relevance_ready_run(
        make_workspace, make_keyword, make_scoring_run, make_keyword_score, auto=False
    )

    monkeypatch.setattr(
        "app.core.site_analyzer.relevance_scorer.RelevanceScorer", _FakeRelevanceScorer
    )
    calls = []
    monkeypatch.setattr(
        "app.tasks.intent_tasks.run_channel_assignment_task.apply_async",
        lambda *, args, task_id, **kwargs: calls.append(task_id),
    )

    _run_relevance_computation(scoring_run_id=run.id)

    db_session.expire_all()
    assert db_session.get(ScoringRun, run.id).status == "relevance_computed"
    assert db_session.query(TaskResult).filter_by(scoring_run_id=run.id).count() == 0
    assert calls == []


# ── Manuel/otomatik çakışma (endpoint seviyesi) ──


def test_manual_assign_conflicts_with_active_auto_task(
    client, db_session, make_workspace, make_scoring_run
):
    from app.database.models import TaskResult

    workspace = make_workspace()
    run = make_scoring_run(brand_profile_id=workspace.id, status="channel_assigning")
    active = TaskResult(
        task_id="auto-task-123",
        task_type="channel_assignment",
        scoring_run_id=run.id,
        status="running",
        progress=40,
    )
    db_session.add(active)
    db_session.commit()

    resp = client.post(
        f"/api/v1/channels/runs/{run.id}/assign",
        params={"brand_profile_id": workspace.id},
        json={},
    )

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["task_id"] == "auto-task-123"
    # İkinci task oluşmadı
    assert db_session.query(TaskResult).filter_by(scoring_run_id=run.id).count() == 1


def test_enqueue_channel_assignment_transitions_and_queues_once(
    db_session, make_workspace, make_scoring_run, monkeypatch
):
    from app.core.channel.assignment_dispatcher import enqueue_channel_assignment
    from app.database.models import ScoringRun, TaskResult

    workspace = make_workspace()
    run = make_scoring_run(brand_profile_id=workspace.id, status="scored")
    calls = []

    def fake_apply_async(*, args, task_id, **kwargs):
      calls.append({"args": args, "task_id": task_id})

    monkeypatch.setattr(
        "app.tasks.intent_tasks.run_channel_assignment_task.apply_async",
        fake_apply_async,
    )

    result = enqueue_channel_assignment(
        db_session,
        run,
        relevance_coefficient=1.25,
        from_status="scored",
    )

    db_session.expire_all()
    refreshed = db_session.get(ScoringRun, run.id)
    task = db_session.query(TaskResult).filter_by(task_id=result["task_id"]).one()

    assert refreshed.status == "channel_assigning"
    assert task.status == "pending"
    assert task.task_type == "channel_assignment"
    assert task.result_data["effective_relevance_coefficient"] == 1.25
    assert calls == [{"args": [run.id, 1.25], "task_id": result["task_id"]}]

    second = enqueue_channel_assignment(
        db_session,
        refreshed,
        relevance_coefficient=1.25,
        from_status="channel_assigning",
    )

    assert second["task_id"] == result["task_id"]
    assert second["already_active"] is True
    assert len(calls) == 1


def test_enqueue_channel_assignment_apply_failure_rolls_run_back(
    db_session, make_workspace, make_scoring_run, monkeypatch
):
    from app.core.channel.assignment_dispatcher import enqueue_channel_assignment
    from app.database.models import ScoringRun, TaskResult

    workspace = make_workspace()
    run = make_scoring_run(brand_profile_id=workspace.id, status="relevance_computed")

    def fake_apply_async(*, args, task_id, **kwargs):
        raise RuntimeError("broker down")

    monkeypatch.setattr(
        "app.tasks.intent_tasks.run_channel_assignment_task.apply_async",
        fake_apply_async,
    )

    with pytest.raises(RuntimeError, match="broker down"):
        enqueue_channel_assignment(
            db_session,
            run,
            relevance_coefficient=1.0,
            from_status="relevance_computed",
        )

    db_session.expire_all()
    refreshed = db_session.get(ScoringRun, run.id)
    task = db_session.query(TaskResult).filter_by(scoring_run_id=run.id).one()

    assert refreshed.status == "relevance_computed"
    assert task.status == "failed"
    assert "broker down" in task.error_message
