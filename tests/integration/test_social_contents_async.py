"""P7 Adim 5 — /social/contents/async dispatch endpoint'i.

Kilitlenen davranislar:
- response modeli net: {task_id, scoring_run_id}
- pending TaskResult ENDPOINT'te olusur (UI polling'i 404 gormez)
- apply_async patlarsa task failed + 503 (sonsuz pending yok)
- yanlis workspace -> 404 (izolasyon)
- aktif social_content task'i varken pool delete -> 409 (guard listesi)
- eski sync /social/contents regresyonu: response modeli degismedi
"""
import pytest

from app.database.models import (
    ChannelPool,
    Keyword,
    SocialCategory,
    SocialIdea,
    TaskResult,
)


@pytest.fixture
def social_setup(db_session, make_workspace, make_scoring_run):
    """Workspace + channel_assigned run + kategori + 2 fikir."""
    ws = make_workspace(name="social-async-ws", company_url="https://socasync.example")
    run = make_scoring_run(brand_profile_id=ws.id, status="channel_assigned", algorithm_version="v3")

    category = SocialCategory(
        scoring_run_id=run.id,
        category_name="Egitim",
        category_type="educational",
        description="d",
    )
    db_session.add(category)
    db_session.commit()

    ideas = []
    for i in range(2):
        idea = SocialIdea(
            category_id=category.id,
            idea_title=f"Fikir {i}",
            idea_description="d",
            target_platform="instagram",
            content_format="post",
        )
        db_session.add(idea)
        ideas.append(idea)
    db_session.commit()
    for idea in ideas:
        db_session.refresh(idea)

    return ws, run, ideas


def test_async_dispatch_response_and_pending_task(client, db_session, social_setup, monkeypatch):
    ws, run, ideas = social_setup
    calls = {}

    def fake_apply_async(*args, **kwargs):
        calls["task_id"] = kwargs.get("task_id")
        calls["args"] = kwargs.get("args")

    monkeypatch.setattr(
        "app.tasks.generation_tasks.social_contents_task.apply_async", fake_apply_async
    )

    res = client.post(
        f"/api/v1/generation/social/contents/async?brand_profile_id={ws.id}",
        json={"idea_ids": [i.id for i in ideas], "brand_name": "Marka", "brand_tone": "samimi"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    # Response modeli net kilitlenir (codex)
    assert set(body.keys()) == {"task_id", "scoring_run_id"}
    assert body["scoring_run_id"] == run.id

    # Pending TaskResult endpoint'te olustu; task type guard listesindeki tip
    row = db_session.query(TaskResult).filter(TaskResult.task_id == body["task_id"]).first()
    assert row is not None
    assert row.task_type == "social_content"
    assert row.status == "pending"
    assert row.scoring_run_id == run.id

    # apply_async ayni task_id ile ve brand_tone tasiyarak cagrildi
    assert calls["task_id"] == body["task_id"]
    assert calls["args"][0] == run.id
    assert calls["args"][2] == "Marka"
    assert calls["args"][3] == "samimi"


def test_async_dispatch_wrong_workspace_404(client, social_setup, make_workspace):
    _, _, ideas = social_setup
    other = make_workspace(name="social-async-other", company_url="https://other.example")

    res = client.post(
        f"/api/v1/generation/social/contents/async?brand_profile_id={other.id}",
        json={"idea_ids": [i.id for i in ideas]},
    )
    assert res.status_code == 404


def test_async_dispatch_enqueue_failure_marks_failed_and_503(
    client, db_session, social_setup, monkeypatch
):
    ws, run, ideas = social_setup

    def boom(*args, **kwargs):
        raise RuntimeError("redis down")

    monkeypatch.setattr(
        "app.tasks.generation_tasks.social_contents_task.apply_async", boom
    )

    res = client.post(
        f"/api/v1/generation/social/contents/async?brand_profile_id={ws.id}",
        json={"idea_ids": [i.id for i in ideas]},
    )
    assert res.status_code == 503

    # Kullanici sonsuz pending'e bakmasin: task failed isaretlendi (codex)
    row = (
        db_session.query(TaskResult)
        .filter(TaskResult.scoring_run_id == run.id, TaskResult.task_type == "social_content")
        .order_by(TaskResult.id.desc())
        .first()
    )
    assert row is not None
    assert row.status == "failed"


def test_active_social_content_task_blocks_pool_delete(client, db_session, social_setup):
    ws, run, _ = social_setup

    kw = Keyword(keyword="social guard kelime", monthly_volume=100)
    db_session.add(kw)
    db_session.commit()
    pool_item = ChannelPool(
        scoring_run_id=run.id, keyword_id=kw.id, channel="SOCIAL", final_rank=1
    )
    db_session.add(pool_item)
    db_session.add(
        TaskResult(
            task_id="social-content-active",
            task_type="social_content",
            scoring_run_id=run.id,
            status="running",
            progress=40,
        )
    )
    db_session.commit()

    res = client.delete(
        f"/api/v1/channels/runs/{run.id}/pools/{pool_item.id}?brand_profile_id={ws.id}"
    )
    # GENERATION_TASK_TYPES'a social_content eklendi — aktif uretim varken 409
    assert res.status_code == 409


def test_sync_contents_endpoint_regression(client, social_setup, monkeypatch):
    """Eski sync endpoint sozlesmesi degismedi (geriye uyum + rollback yolu)."""
    ws, run, ideas = social_setup

    from app.schemas.social import SocialContentsResponse

    def fake_generate_contents(self, request, scoring_run_id=None):
        return SocialContentsResponse(
            idea_ids=request.idea_ids, total_contents=0, contents=[]
        )

    monkeypatch.setattr(
        "app.generators.social.social_generator.SocialGenerator.generate_contents",
        fake_generate_contents,
    )

    res = client.post(
        f"/api/v1/generation/social/contents?brand_profile_id={ws.id}",
        json={"idea_ids": [i.id for i in ideas], "brand_name": "Marka"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    # Faz F: warnings alanı eklendi (grounding reddi raporu)
    assert set(body.keys()) == {"idea_ids", "total_contents", "contents", "warnings", "policy_warnings"}
    assert body["idea_ids"] == [i.id for i in ideas]


def test_social_contents_task_writes_committed_content_ids(db_session, social_setup, monkeypatch):
    """Worker basarida TaskResult.result_data.content_ids'i kaydedilmis id'lerle yazar."""
    from types import SimpleNamespace

    from app.tasks.generation_tasks import social_contents_task
    from app.tasks.task_status import create_task_record

    _, run, ideas = social_setup
    task_id = "social-content-task-ok"

    monkeypatch.setattr(
        "app.generators.ai_service.get_ai_service",
        lambda: object(),
    )

    def fake_generate_contents(self, request, scoring_run_id=None):
        return SimpleNamespace(contents=[SimpleNamespace(id=501)], warnings=[], policy_warnings=[])

    monkeypatch.setattr(
        "app.generators.social.social_generator.SocialGenerator.generate_contents",
        fake_generate_contents,
    )

    create_task_record(task_id, "social_content", run.id, {"idea_count": 1})
    social_contents_task.apply(
        args=(run.id, [ideas[0].id], "Marka", "samimi"),
        task_id=task_id,
    )

    db_session.expire_all()
    row = db_session.query(TaskResult).filter(TaskResult.task_id == task_id).one()
    assert row.status == "completed"
    assert (row.result_data or {}).get("content_ids") == [501]
    assert (row.result_data or {}).get("scoring_run_id") == run.id


def test_social_contents_task_zero_content_marks_failed(db_session, social_setup, monkeypatch):
    """Worker 0 icerik uretirse completed degil failed yazar."""
    from types import SimpleNamespace

    from app.tasks.generation_tasks import social_contents_task
    from app.tasks.task_status import create_task_record

    _, run, ideas = social_setup
    task_id = "social-content-task-empty"

    monkeypatch.setattr(
        "app.generators.ai_service.get_ai_service",
        lambda: object(),
    )
    monkeypatch.setattr(
        "app.generators.social.social_generator.SocialGenerator.generate_contents",
        lambda self, request, scoring_run_id=None: SimpleNamespace(contents=[], warnings=[], policy_warnings=[]),
    )

    create_task_record(task_id, "social_content", run.id, {"idea_count": 1})
    social_contents_task.apply(
        args=(run.id, [ideas[0].id], "Marka", None),
        task_id=task_id,
    )

    db_session.expire_all()
    row = db_session.query(TaskResult).filter(TaskResult.task_id == task_id).one()
    assert row.status == "failed"
    assert (row.result_data or {}).get("content_ids") == []
