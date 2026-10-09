"""ADS uretiminde "completed ama 0 grup" sessiz basarisizligi guard'i.

SEO'daki 0-icerik->failed deseninin ADS karsiligi: generate_ads_task,
generator 0 grup dondurdugunde task'i failed isaretlemeli ve havuz-bos ile
AI-isleyemedi durumlarini farkli mesajlarla ayirmali. Kismi basari
(bazi gruplar duser, >=1 uretilir) completed kalir ve failed_groups tasir.
"""
from datetime import datetime

import pytest

from app.database.models import TaskResult
from app.schemas.ads import AdsGenerateResponse, ValidationSummary
from app.tasks import generation_tasks


def _response(total_keywords: int, total_groups: int, failed_groups: int) -> AdsGenerateResponse:
    return AdsGenerateResponse(
        scoring_run_id=1,
        total_keywords=total_keywords,
        total_groups=total_groups,
        failed_groups=failed_groups,
        ad_groups=[],
        total_headlines=0,
        total_descriptions=0,
        total_negative_keywords=0,
        validation_summary=ValidationSummary(
            total_headlines_generated=0,
            headlines_kept=0,
            headlines_shortened=0,
            headlines_regenerated=0,
            headlines_eliminated=0,
            dki_converted_to_plain=0,
            total_descriptions_generated=0,
            descriptions_kept=0,
            descriptions_sentence_trimmed=0,
            descriptions_truncated=0,
        ),
        generated_at=datetime.utcnow(),
    )


@pytest.fixture
def scoring_run(db_session, make_workspace, make_scoring_run):
    ws = make_workspace(name="ads-guard-ws", company_url="https://adsguard.example")
    return make_scoring_run(brand_profile_id=ws.id, status="channel_assigned")


def _run_task(monkeypatch, response: AdsGenerateResponse, run_id: int, task_id: str, db_session):
    # Versiyonlama (Faz E): worker generation_set_id zorunlu — dispatch
    # akışını taklit eden generating set + pending TaskResult yaratılır
    from app.database.models import AdGenerationSet

    version = (
        db_session.query(AdGenerationSet.version_number)
        .filter(AdGenerationSet.scoring_run_id == run_id)
        .order_by(AdGenerationSet.version_number.desc())
        .limit(1)
        .scalar()
        or 0
    ) + 1
    gen_set = AdGenerationSet(
        scoring_run_id=run_id, task_id=task_id,
        version_number=version, status="generating", is_stale=False,
    )
    db_session.add(gen_set)
    db_session.add(TaskResult(
        task_id=task_id, task_type="ads", scoring_run_id=run_id,
        status="pending", progress=0,
    ))
    db_session.commit()
    db_session.refresh(gen_set)

    monkeypatch.setattr(
        "app.generators.ads.ads_generator.AdsGenerator.generate_ads",
        lambda self, request, generation_set_id=None, defer_commit=False,
        trusted_brand_usp="": response,
    )
    # .apply() task'i lokal/senkron calistirir; task_id ile TaskResult izlenir
    return generation_tasks.generate_ads_task.apply(
        kwargs={
            "scoring_run_id": run_id,
            "brand_name": "Marka",
            "generation_set_id": gen_set.id,
        },
        task_id=task_id,
    )


def _task_row(db_session, task_id: str) -> TaskResult:
    db_session.expire_all()
    row = db_session.query(TaskResult).filter(TaskResult.task_id == task_id).first()
    assert row is not None, "TaskResult kaydi olusmali"
    return row


def test_zero_groups_empty_pool_marks_failed(db_session, monkeypatch, scoring_run):
    task_id = "adsguard-empty-pool"
    _run_task(
        monkeypatch,
        _response(total_keywords=0, total_groups=0, failed_groups=0),
        scoring_run.id,
        task_id,
        db_session,
    )

    row = _task_row(db_session, task_id)
    assert row.status == "failed"
    assert "havuzu bos" in (row.error_message or "")


def test_zero_groups_ai_failure_marks_failed(db_session, monkeypatch, scoring_run):
    task_id = "adsguard-ai-fail"
    _run_task(
        monkeypatch,
        _response(total_keywords=8, total_groups=0, failed_groups=3),
        scoring_run.id,
        task_id,
        db_session,
    )

    row = _task_row(db_session, task_id)
    assert row.status == "failed"
    assert "AI yanitlari islenemedi" in (row.error_message or "")
    assert (row.result_data or {}).get("failed_groups") == 3


def test_partial_failure_stays_completed_with_failed_groups(db_session, monkeypatch, scoring_run):
    task_id = "adsguard-partial"
    _run_task(
        monkeypatch,
        _response(total_keywords=8, total_groups=2, failed_groups=1),
        scoring_run.id,
        task_id,
        db_session,
    )

    row = _task_row(db_session, task_id)
    assert row.status == "completed"
    assert (row.result_data or {}).get("failed_groups") == 1
    assert (row.result_data or {}).get("ad_group_count") == 2


def test_normal_success_completed(db_session, monkeypatch, scoring_run):
    task_id = "adsguard-ok"
    _run_task(
        monkeypatch,
        _response(total_keywords=8, total_groups=3, failed_groups=0),
        scoring_run.id,
        task_id,
        db_session,
    )

    row = _task_row(db_session, task_id)
    assert row.status == "completed"
    assert (row.result_data or {}).get("failed_groups") == 0
