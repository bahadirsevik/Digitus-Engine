"""SEO toplu uretim: kismi sonuc ozeti (requested_count + failed_keywords)."""
import types

from app.tasks import generation_tasks


class _FakeGen:
    def __init__(self, db, ai):
        pass

    def generate_content(self, request, scoring_run_id=None):
        if request.keyword_id % 2 == 0:
            raise ValueError("boom: secret provider text")
        return {"id": request.keyword_id * 10}


def test_chunk_reports_failed_keywords_with_safe_reason(
    db_session, make_workspace, make_scoring_run, make_keyword, monkeypatch
):
    ws = make_workspace("Seo Partial WS")
    run = make_scoring_run(brand_profile_id=ws.id)
    kws = [make_keyword(f"kelime {i}", brand_profile_id=ws.id) for i in range(4)]
    ids = [k.id for k in kws]

    monkeypatch.setattr(
        "app.generators.seo_geo.seo_geo_generator.SEOGeoGenerator", _FakeGen)
    monkeypatch.setattr(
        "app.generators.ai_service.get_ai_service", lambda *a, **k: types.SimpleNamespace(collector=None))

    out = generation_tasks.generate_seo_chunk_task.apply(args=(run.id, ids)).get()

    failed = {f["keyword"] for f in out["failed_keywords"]}
    assert failed == {k.keyword for k in kws if k.id % 2 == 0}
    assert all(f["reason"] == "AI yanıtı işlenemedi" for f in out["failed_keywords"])
    assert "secret" not in str(out["failed_keywords"])
    assert out["requested"] == 4
    assert out["count"] == 2


def _finalize(monkeypatch, results):
    captured = {}
    monkeypatch.setattr(
        generation_tasks, "update_task_status", lambda *a, **kw: captured.update(kw))
    out = generation_tasks.finalize_seo_bulk(results, "parent", 5)
    return out, captured


def test_finalize_adds_requested_count_and_capped_failed_keywords(monkeypatch):
    many = [{"keyword": f"k{i}", "reason": "AI yanıtı işlenemedi"} for i in range(40)]
    out, cap = _finalize(monkeypatch, [
        {"content_ids": [1, 2], "requested": 10, "failed_keywords": many},
        {"content_ids": [3], "requested": 5, "failed_keywords": many},
        None,
    ])
    rd = cap["result_data"]
    assert out["status"] == "completed"
    assert rd["requested_count"] == 15
    assert rd["total"] == 3
    assert len(rd["failed_keywords"]) == 50
    assert rd["failed_keywords"][0] == {"keyword": "k0", "reason": "AI yanıtı işlenemedi"}


def test_finalize_zero_content_still_failed_and_carries_summary(monkeypatch):
    out, cap = _finalize(monkeypatch, [
        {"content_ids": [], "requested": 2,
         "failed_keywords": [{"keyword": "a", "reason": "AI yanıtı işlenemedi"}]},
    ])
    assert out["status"] == "failed"
    assert cap["status"] == "failed"
    assert cap["result_data"]["requested_count"] == 2
    assert cap["result_data"]["failed_keywords"][0]["keyword"] == "a"
