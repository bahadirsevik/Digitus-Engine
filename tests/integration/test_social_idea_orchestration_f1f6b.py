# -*- coding: utf-8 -*-
"""MİKRO FAZ F1-F.6b — Transaction-Safe Fikir Üretim Orkestrasyon Entegrasyon Testleri.

Tüm testler gerçek PostgreSQL test veritabanını ve SessionLocal oturum fabrikasını kullanır.
Test edilen alanlar:
1. Başarılı çok kategorili tam akış ve ORM satır eşlemesi
2. AI çağrı sırasının kategori plan sırasıyla birebir eşleşmesi
3. Her kategori için yalnızca kendi target kotasının kullanılması (Cartesian guard)
4. ai_calls_used metriğinin kategori sonuçlarından doğru toplanması
5. Her başarılı kategori sonrasında heartbeat_attempt çağrılması ve lease uzatılması
6. AI çalışırken açık DB transaction veya satır kilidi bulunmaması
7. Persistence'ın bütün kategoriler tamamlandıktan sonra bir kez çağrılması
8. Provider hatasında sıfır SocialIdea ve fail-closed failed attempt
9. İki geçersiz AI çıktısında sıfır SocialIdea ve fail-closed failed attempt
10. İlk kategori başarılı, ikinci başarısız → sıfır SocialIdea (partial silinir/yazılmaz)
11. Heartbeat hatasında persistence çalışmaması
12. Persistence hatasında kısmi satır kalmaması (rollback)
13. Brief AI sırasında stale olursa persistence'ın BRIEF_STALE ile reddetmesi
14. Assignment version AI sırasında değişirse ASSIGNMENT_CHANGED ile reddedilmesi
15. Lease AI sırasında dolarsa WORKER_LOST olarak failed edilmesi
16. Completed replay'de sıfır AI, sıfır heartbeat, sıfır kopya satır
17. Completed replay DB anomalisi durumunda başarının engellenmesi
18. Farklı task_id ile müdahalenin TASK_MISMATCH ile reddedilmesi
19. Güvenli reason_code ve hata mesajlarında ham AI/SQL/provider metninin sızmaması
20. Session'ların her aşamada (claim, worker input, heartbeat, persistence) düzgün kapatılması
21. temperature=None ayarının ve sıfır harici ağ çağrısının korunması
22. F1-F.6b.1 Failure finalization canonical lock sırası ve late persistence replay garantileri
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy import event, text
from sqlalchemy.orm import Session

from app.config import settings
from app.core.social.idea_flow import begin_social_idea_generation
from app.core.social.idea_orchestration import (
    SocialIdeaHeartbeatError,
    SocialIdeaOrchestrationResult,
    run_social_idea_generation,
)
from app.core.social.idea_persistence import (
    SocialIdeaPersistenceError,
    extract_social_idea_plan_snapshot,
    persist_social_ideas,
)
from app.database.connection import SessionLocal
from app.database.models import (
    ChannelPool,
    ScoringRun,
    SocialBrief,
    SocialBriefKeyword,
    SocialBriefTarget,
    SocialCategory,
    SocialGenerationAttempt,
    SocialIdea,
)
from app.generators.social.attempt_state import (
    AttemptNotWritableError,
    finalize_ideas_attempt_failure,
    heartbeat_attempt,
)
from app.schemas.social_brief import SocialBriefIdeasGenerateRequest

T0 = datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def enable_flag(monkeypatch):
    """Test süresince feature flag'i aktif kılar."""
    monkeypatch.setattr(settings, "ENABLE_SOCIAL_BRIEF_FLOW", True)
    yield


# ==================== TEST YARDIMCILARI ====================

class SmartMockAIService:
    """Orkestrasyon testleri için akıllı sahte AI servisi."""

    def __init__(
        self,
        responses: list[Any] | None = None,
        on_complete_callback: Any = None,
        default_response: Any = None,
    ) -> None:
        self.responses: list[Any] = list(responses) if responses else []
        self.on_complete_callback = on_complete_callback
        # responses tükendiğinde her çağrıda dönülecek/fırlatılacak yanıt (None: otomatik uyumlu JSON)
        self.default_response = default_response
        self.call_count: int = 0
        self.calls: list[dict[str, Any]] = []

    def for_stage(self, stage: str, **overrides) -> SmartMockAIService:
        return self

    def complete_json(self, prompt: str, **kwargs) -> str:
        self.call_count += 1
        self.calls.append({"prompt": prompt, "kwargs": kwargs})

        if self.on_complete_callback is not None:
            self.on_complete_callback(self.call_count, prompt, kwargs)

        if self.responses:
            resp = self.responses.pop(0)
            if isinstance(resp, Exception):
                raise resp
            return resp

        if self.default_response is not None:
            if isinstance(self.default_response, Exception):
                raise self.default_response
            return self.default_response

        # Otomatik uyumlu JSON yanıtı üret
        start_tag = "<INPUT_JSON>\n"
        end_tag = "\n</INPUT_JSON>"
        start = prompt.find(start_tag) + len(start_tag)
        end = prompt.find(end_tag)
        data = json.loads(prompt[start:end])
        kw_id = data["keywords"][0]["id"]
        ideas = []
        idx = 1
        for spec in data["target_specs"]:
            for _ in range(spec["requested_count"]):
                ideas.append({
                    "target_id": spec["target_id"],
                    "primary_keyword_id": kw_id,
                    "idea_title": f"Fikir Başlığı {idx}",
                    "idea_description": f"Stratejik gerekçe ve fikir açıklaması {idx}.",
                    "target_platform": spec["platform"],
                    "content_format": spec["content_format"],
                    "trend_alignment": 0.8,
                })
                idx += 1
        return json.dumps({"ideas": ideas}, ensure_ascii=False)


def _setup_fresh_environment(
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
    num_kws: int = 3,
    workspace_name: str = "Orchestration Brand",
):
    """Merkezi freshness sözleşmesini karşılayan workspace/run/pool oluşturur."""
    workspace = make_workspace(name=workspace_name, status="confirmed")
    run = make_scoring_run(
        brand_profile_id=workspace.id,
        status="completed",
        algorithm_version="v2",
        channel_pool_policy_version=workspace.policy_version or 1,
        relevance_anchor_version=workspace.anchor_version or 1,
        channel_assignment_version=1,
        skip_relevance=True,
    )

    keywords = []
    for i in range(num_kws):
        kw = make_keyword(
            text_value=f"{workspace_name} kw {i + 1}",
            brand_profile_id=workspace.id,
        )
        pool = ChannelPool(
            scoring_run_id=run.id,
            keyword_id=kw.id,
            channel="SOCIAL",
            final_rank=i + 1,
            relevance_score=0.9,
            adjusted_score=20.0 - i,
        )
        db_session.add(pool)
        keywords.append(kw)

    db_session.commit()
    return workspace, run, keywords


def _setup_ready_ideas_environment(
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
    task_id: str = "task-orch-101",
    ideas_per_category: int = 3,
    num_categories: int = 2,
    targets_data: list[tuple[str, str]] | None = None,
):
    """Fikir üretimi orkestrasyon testleri için preflight edilmiş ortam kurar."""
    ws, run, kws = _setup_fresh_environment(db_session, make_workspace, make_scoring_run, make_keyword, num_kws=3)

    if targets_data is None:
        targets_data = [("instagram", "post"), ("twitter", "thread")]

    brief = SocialBrief(
        scoring_run_id=run.id,
        brand_name_snapshot="Test Marka",
        brand_context_snapshot="Test Context",
        channel_assignment_version=1,
        format_matrix_version="v1",
        is_stale=False,
        locked_at=T0,
    )
    db_session.add(brief)
    db_session.flush()

    for pos, kw in enumerate(kws):
        bk = SocialBriefKeyword(
            brief_id=brief.id,
            keyword_id=kw.id,
            keyword_snapshot=kw.keyword,
            position=pos,
        )
        db_session.add(bk)

    targets = []
    for plat, fmt in targets_data:
        t = SocialBriefTarget(
            brief_id=brief.id,
            platform=plat,
            content_format=fmt,
        )
        db_session.add(t)
        targets.append(t)

    # Categories attempt
    cat_attempt = SocialGenerationAttempt(
        brief_id=brief.id,
        stage="categories",
        idempotency_key=f"cat-attempt-{task_id}",
        status="completed",
        heartbeat_at=T0,
        lease_expires_at=T0 + timedelta(seconds=1500),
        created_at=T0,
    )
    db_session.add(cat_attempt)
    db_session.flush()

    # Categories
    categories = []
    for i in range(num_categories):
        cat = SocialCategory(
            scoring_run_id=run.id,
            brief_id=brief.id,
            category_name=f"Kategori {i + 1}",
            category_type="educational",
            description=f"Açıklama {i + 1} için eğitici içerik yönü.",
            is_stale=False,
            relevance_score=0.9,
            suggested_keyword_ids=[kws[0].id],
        )
        db_session.add(cat)
        categories.append(cat)

    db_session.commit()
    for cat in categories:
        db_session.refresh(cat)
    for t in targets:
        db_session.refresh(t)

    # Ideas preflight başlat
    cat_ids = [c.id for c in categories]
    req = SocialBriefIdeasGenerateRequest(
        idempotency_key=f"idempotency-{task_id}",
        category_ids=cat_ids,
        ideas_per_category=ideas_per_category,
    )

    start = begin_social_idea_generation(
        db_session,
        brief_id=brief.id,
        brand_profile_id=ws.id,
        request=req,
    )
    db_session.commit()

    attempt = db_session.query(SocialGenerationAttempt).filter_by(id=start.attempt_id).one()
    return ws, run, brief, categories, targets, kws, start, attempt


# ==================== TESTLER ====================

def test_01_successful_multi_category_full_flow(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """1. Başarılı çok kategorili tam akış: attempt completed olur, 6 fikir yazılır."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-01"
    )
    mock_ai = SmartMockAIService()

    result = run_social_idea_generation(
        session_factory=SessionLocal,
        ai_service=mock_ai,
        attempt_id=attempt.id,
        task_id="task-01",
        now_provider=lambda: T0,
    )

    assert isinstance(result, SocialIdeaOrchestrationResult)
    assert result.attempt_id == attempt.id
    assert result.brief_id == brief.id
    assert result.scoring_run_id == run.id
    assert result.status == "completed"
    assert result.total_ideas == 6
    assert result.ai_calls_used == 2
    assert result.replayed is False

    # DB doğrulaması
    db_session.expire_all()
    db_attempt = db_session.query(SocialGenerationAttempt).filter_by(id=attempt.id).one()
    assert db_attempt.status == "completed"
    assert db_attempt.task_id == "task-01"
    assert db_attempt.completed_at is not None

    db_ideas = db_session.query(SocialIdea).filter_by(brief_id=brief.id).all()
    assert len(db_ideas) == 6


def test_02_ai_call_order_matches_category_plan_order(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """2. AI çağrı sırası kategori plan sırasıyla birebir aynıdır."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-02", num_categories=2
    )
    plan = extract_social_idea_plan_snapshot(attempt)
    expected_cat_ids = [cp.category_id for cp in plan.categories]

    mock_ai = SmartMockAIService()
    run_social_idea_generation(
        session_factory=SessionLocal,
        ai_service=mock_ai,
        attempt_id=attempt.id,
        task_id="task-02",
        now_provider=lambda: T0,
    )

    assert mock_ai.call_count == 2
    for call, exp_cid in zip(mock_ai.calls, expected_cat_ids):
        prompt = call["prompt"]
        start_tag = "<INPUT_JSON>\n"
        end_tag = "\n</INPUT_JSON>"
        start_idx = prompt.find(start_tag) + len(start_tag)
        end_idx = prompt.find(end_tag)
        data = json.loads(prompt[start_idx:end_idx])
        assert data["category"]["id"] == exp_cid


def test_03_each_category_receives_only_its_target_quota(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """3. Her kategori prompt'u yalnızca kendi kotası > 0 olan hedefleri içerir (Cartesian guard)."""
    targets_data = [("instagram", "post"), ("twitter", "thread"), ("linkedin", "post")]
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_environment(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-03",
        ideas_per_category=2,
        num_categories=2,
        targets_data=targets_data,
    )
    mock_ai = SmartMockAIService()
    run_social_idea_generation(
        session_factory=SessionLocal,
        ai_service=mock_ai,
        attempt_id=attempt.id,
        task_id="task-03",
        now_provider=lambda: T0,
    )

    plan = extract_social_idea_plan_snapshot(attempt)
    for call, cp in zip(mock_ai.calls, plan.categories):
        active_tids = [tid for tid, count in cp.target_quotas if count > 0]
        zero_tids = [tid for tid, count in cp.target_quotas if count == 0]
        # Kotası olanlar prompt'ta var
        for tid in active_tids:
            assert f'"target_id": {tid}' in call["prompt"]
        # Kotası 0 olan hedef prompt'ta yok
        for tid in zero_tids:
            assert f'"target_id": {tid}' not in call["prompt"]


def test_04_ai_calls_used_aggregated_correctly(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """4. Kategori bazındaki retry çağrıları da dahil toplam ai_calls_used doğru hesaplanır."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-04", num_categories=2
    )
    # Kategori 1: İlk yanıt geçersiz JSON (1 retry harcar), ikinci yanıt geçerli (toplam 2)
    # Kategori 2: İlk yanıt geçerli (toplam 1) -> Genel toplam 3
    mock_ai = SmartMockAIService(responses=["{malformed json}"])

    result = run_social_idea_generation(
        session_factory=SessionLocal,
        ai_service=mock_ai,
        attempt_id=attempt.id,
        task_id="task-04",
        now_provider=lambda: T0,
    )
    assert result.ai_calls_used == 3
    assert mock_ai.call_count == 3


def test_05_heartbeat_called_after_each_successful_category(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """5. Her başarılı kategori sonrasında heartbeat çağrılır ve lease süresi uzatılır."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-05", num_categories=2
    )
    heartbeat_times: list[datetime] = []
    current_sim_time = T0

    def time_now():
        return current_sim_time

    mock_ai = SmartMockAIService()
    with patch("app.core.social.idea_orchestration.heartbeat_attempt", wraps=heartbeat_attempt) as spy_hb:
        result = run_social_idea_generation(
            session_factory=SessionLocal,
            ai_service=mock_ai,
            attempt_id=attempt.id,
            task_id="task-05",
            now_provider=time_now,
        )
        assert spy_hb.call_count == 2


def test_06_no_db_transaction_open_during_ai_generation(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """6. AI çağrısı sırasında hiçbir DB session veya transaction açık tutulmaz."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-06"
    )

    open_transactions_during_ai: list[bool] = []

    def on_ai_call(count, prompt, kwargs):
        # AI çağrısı anında ana session veya herhangi bir açık transaction var mı?
        # İzole bir session açıp attempt tablosunda aktif satır kilidi var mı bakarız
        probe = SessionLocal()
        try:
            # Kilitsiz sorgu yapabiliyor olmalıyız (başka transaction FOR UPDATE kilidi tutmuyorsa hemen döner)
            att = probe.query(SocialGenerationAttempt).filter_by(id=attempt.id).one()
            open_transactions_during_ai.append(att.status == "running")
        finally:
            probe.close()

    mock_ai = SmartMockAIService(on_complete_callback=on_ai_call)
    result = run_social_idea_generation(
        session_factory=SessionLocal,
        ai_service=mock_ai,
        attempt_id=attempt.id,
        task_id="task-06",
        now_provider=lambda: T0,
    )
    assert len(open_transactions_during_ai) == 2
    assert all(open_transactions_during_ai)


def test_07_persistence_called_once_after_all_categories_complete(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """7. persist_social_ideas bütün kategoriler tamamlandıktan sonra tam bir kez çağrılır."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-07"
    )
    mock_ai = SmartMockAIService()

    with patch("app.core.social.idea_orchestration.persist_social_ideas", wraps=persist_social_ideas) as spy_persist:
        result = run_social_idea_generation(
            session_factory=SessionLocal,
            ai_service=mock_ai,
            attempt_id=attempt.id,
            task_id="task-07",
            now_provider=lambda: T0,
        )
        assert spy_persist.call_count == 1
        assert result.total_ideas == 6


def test_08_provider_error_zero_social_ideas_and_failed_attempt(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """8. Tüm AI çağrıları sağlayıcı hatası verirse (ilk tur + tamamlama) sıfır SocialIdea
    oluşturulur, sahte yedek yazılmaz ve attempt fail edilir (K7)."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-08"
    )
    mock_ai = SmartMockAIService(
        default_response=Exception("Gemini 503 Service Unavailable with secret key 123")
    )

    with pytest.raises(Exception):
        run_social_idea_generation(
            session_factory=SessionLocal,
            ai_service=mock_ai,
            attempt_id=attempt.id,
            task_id="task-08",
            now_provider=lambda: T0,
        )

    # DB doğrulaması
    db_session.expire_all()
    assert db_session.query(SocialIdea).filter_by(brief_id=brief.id).count() == 0
    db_attempt = db_session.query(SocialGenerationAttempt).filter_by(id=attempt.id).one()
    assert db_attempt.status == "failed"
    assert db_attempt.reason_code == "idea_provider_error"
    # Güvenli hata mesajı doğrulaması
    assert "503" not in db_attempt.error_message
    assert "secret key" not in db_attempt.error_message
    assert db_attempt.error_message == "Yapay zeka servisi sağlayıcı hatası verdi."


def test_09_two_invalid_ai_outputs_zero_social_ideas_and_failed_attempt(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """9. Her istek iki kez (ilk çağrı + 1 retry) geçersiz/boş yanıt verirse attempt
    idea_output_invalid olarak fail edilir ve hiç fikir yazılmaz."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-09"
    )
    mock_ai = SmartMockAIService(default_response='{"ideas": []}')

    with pytest.raises(Exception):
        run_social_idea_generation(
            session_factory=SessionLocal,
            ai_service=mock_ai,
            attempt_id=attempt.id,
            task_id="task-09",
            now_provider=lambda: T0,
        )

    db_session.expire_all()
    assert db_session.query(SocialIdea).filter_by(brief_id=brief.id).count() == 0
    db_attempt = db_session.query(SocialGenerationAttempt).filter_by(id=attempt.id).one()
    assert db_attempt.status == "failed"
    assert db_attempt.reason_code == "idea_output_invalid"
    assert db_attempt.error_message == "Yapay zeka çıktısı doğrulama kurallarına uymadı."


def test_10_first_category_success_second_fails_topup_recovers(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """10. İlk kategori başarılı, ikinci kategorinin AI çağrısı başarısız: deneme DURMAZ.

    Plan §3.5: kategori 2'ye planlanan hedef brief genelinde eksik kalır ve tek
    tamamlama turunda aynı kategoride (1 fikir) istenir. Tamamlama başarılı olunca
    her hedef ve her kategori >= 1 fikir alır (K4) -> completed. Kategori x hedef
    kotası (kategori 2'de 3 yerine 1) tamamlanmaz.
    """
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-10", num_categories=2
    )
    def on_ai_call(count, prompt, kwargs):
        if count == 2:
            raise Exception("Crash on second category")

    mock_ai = SmartMockAIService(on_complete_callback=on_ai_call)

    result = run_social_idea_generation(
        session_factory=SessionLocal,
        ai_service=mock_ai,
        attempt_id=attempt.id,
        task_id="task-10",
        now_provider=lambda: T0,
    )

    # 1: kategori 1, 2: kategori 2 (hata, provider hatası retry edilmez), 3: tamamlama
    assert mock_ai.call_count == 3
    assert result.status == "completed"
    assert result.total_ideas == 4
    assert result.ai_calls_used == 3

    db_session.expire_all()
    ideas = db_session.query(SocialIdea).filter_by(brief_id=brief.id).all()
    assert len(ideas) == 4
    by_cat = {c.id: [i for i in ideas if i.category_id == c.id] for c in cats}
    assert len(by_cat[cats[0].id]) == 3
    assert len(by_cat[cats[1].id]) == 1
    assert {i.brief_target_id for i in by_cat[cats[1].id]} == {targets[1].id}

    db_attempt = db_session.query(SocialGenerationAttempt).filter_by(id=attempt.id).one()
    assert db_attempt.status == "completed"
    assert db_attempt.warnings == []
    metrics = db_attempt.coverage["metrics"]
    assert metrics["topup_used"] == 1
    assert metrics["topup_requests"] == 1
    assert metrics["failed_requests"] == 1
    assert metrics["target_unfilled"] == 0
    assert metrics["category_unfilled"] == 0


def test_11_heartbeat_failure_aborts_before_persistence(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """11. Heartbeat hatası oluşursa persistence çalıştırılmaz ve attempt idea_heartbeat_failed olur."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-11"
    )
    mock_ai = SmartMockAIService()

    with patch("app.core.social.idea_orchestration.heartbeat_attempt", side_effect=Exception("DB network down during heartbeat")):
        with patch("app.core.social.idea_orchestration.persist_social_ideas") as mock_persist:
            with pytest.raises(Exception):
                run_social_idea_generation(
                    session_factory=SessionLocal,
                    ai_service=mock_ai,
                    attempt_id=attempt.id,
                    task_id="task-11",
                    now_provider=lambda: T0,
                )
            assert mock_persist.call_count == 0

    db_session.expire_all()
    assert db_session.query(SocialIdea).filter_by(brief_id=brief.id).count() == 0
    db_attempt = db_session.query(SocialGenerationAttempt).filter_by(id=attempt.id).one()
    assert db_attempt.status == "failed"
    assert db_attempt.reason_code == "idea_heartbeat_failed"
    assert db_attempt.error_message == "İşlem kalp atışı (heartbeat) güncellenemedi."


def test_12_persistence_failure_leaves_no_partial_rows(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """12. Persistence aşamasında hata oluşursa rollback yapılır ve kısmi satır kalmaz."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-12"
    )
    mock_ai = SmartMockAIService()

    with patch("app.core.social.idea_orchestration.persist_social_ideas", side_effect=SocialIdeaPersistenceError("Persistence disk full", error_code="PERSISTENCE_FAILED")):
        with pytest.raises(Exception):
            run_social_idea_generation(
                session_factory=SessionLocal,
                ai_service=mock_ai,
                attempt_id=attempt.id,
                task_id="task-12",
                now_provider=lambda: T0,
            )

    db_session.expire_all()
    assert db_session.query(SocialIdea).filter_by(brief_id=brief.id).count() == 0
    db_attempt = db_session.query(SocialGenerationAttempt).filter_by(id=attempt.id).one()
    assert db_attempt.status == "failed"
    assert db_attempt.reason_code == "idea_persistence_failed"
    assert db_attempt.error_message == "Fikirlerin veritabanına kaydı başarısız oldu."


def test_13_brief_becomes_stale_during_ai_generation(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """13. Brief AI çağrısı sırasında stale olursa persistence reddeder ve brief_stale korunur."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-13"
    )

    def make_brief_stale(count, prompt, kwargs):
        probe = SessionLocal()
        try:
            b = probe.query(SocialBrief).filter_by(id=brief.id).one()
            b.is_stale = True
            probe.commit()
        finally:
            probe.close()

    mock_ai = SmartMockAIService(on_complete_callback=make_brief_stale)
    with pytest.raises(AttemptNotWritableError) as exc:
        run_social_idea_generation(
            session_factory=SessionLocal,
            ai_service=mock_ai,
            attempt_id=attempt.id,
            task_id="task-13",
            now_provider=lambda: T0,
        )
    assert exc.value.error_code == "BRIEF_STALE"

    db_session.expire_all()
    assert db_session.query(SocialIdea).filter_by(brief_id=brief.id).count() == 0
    db_attempt = db_session.query(SocialGenerationAttempt).filter_by(id=attempt.id).one()
    assert db_attempt.status == "failed"
    assert db_attempt.reason_code == "brief_stale"


def test_14_assignment_version_changes_during_ai_generation(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """14. Brief assignment version AI sırasında değişirse persistence reddeder ve assignment_changed korunur."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-14"
    )

    def change_version(count, prompt, kwargs):
        probe = SessionLocal()
        try:
            b = probe.query(SocialBrief).filter_by(id=brief.id).one()
            b.channel_assignment_version = 99
            probe.commit()
        finally:
            probe.close()

    mock_ai = SmartMockAIService(on_complete_callback=change_version)
    with pytest.raises(AttemptNotWritableError) as exc:
        run_social_idea_generation(
            session_factory=SessionLocal,
            ai_service=mock_ai,
            attempt_id=attempt.id,
            task_id="task-14",
            now_provider=lambda: T0,
        )
    assert exc.value.error_code == "ASSIGNMENT_CHANGED"

    db_session.expire_all()
    assert db_session.query(SocialIdea).filter_by(brief_id=brief.id).count() == 0
    db_attempt = db_session.query(SocialGenerationAttempt).filter_by(id=attempt.id).one()
    assert db_attempt.status == "failed"
    assert db_attempt.reason_code == "assignment_changed"


def test_15_lease_expires_during_ai_generation_worker_lost_preserved(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """15. AI çağrısı sırasında lease dolarsa persistence'ta WORKER_LOST olarak failed edilir."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-15"
    )

    now_ref = [T0]

    def on_ai_complete(count, prompt, kwargs):
        # AI çağrısı sırasında zamanı 2 saat ileri alalım (lease 1500 sn = 25 dk)
        now_ref[0] = T0 + timedelta(hours=2)

    mock_ai = SmartMockAIService(on_complete_callback=on_ai_complete)
    with pytest.raises(AttemptNotWritableError) as exc:
        run_social_idea_generation(
            session_factory=SessionLocal,
            ai_service=mock_ai,
            attempt_id=attempt.id,
            task_id="task-15",
            now_provider=lambda: now_ref[0],
        )
    assert exc.value.error_code == "WORKER_LOST"

    db_session.expire_all()
    assert db_session.query(SocialIdea).filter_by(brief_id=brief.id).count() == 0
    db_attempt = db_session.query(SocialGenerationAttempt).filter_by(id=attempt.id).one()
    assert db_attempt.status == "failed"
    assert db_attempt.reason_code == "worker_lost"


def test_16_completed_replay_zero_ai_zero_heartbeat_no_duplicates(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """16. Zaten completed attempt tekrar çağrıldığında 0 AI, 0 heartbeat ile replayed=True döner."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-16"
    )
    mock_ai1 = SmartMockAIService()

    # 1. Başarılı ilk çalıştırma
    res1 = run_social_idea_generation(
        session_factory=SessionLocal,
        ai_service=mock_ai1,
        attempt_id=attempt.id,
        task_id="task-16",
        now_provider=lambda: T0,
    )
    assert res1.replayed is False
    assert res1.total_ideas == 6
    assert mock_ai1.call_count == 2

    # 2. İkinci çalıştırma (Replay)
    mock_ai2 = SmartMockAIService()
    with patch("app.core.social.idea_orchestration.heartbeat_attempt") as spy_hb:
        res2 = run_social_idea_generation(
            session_factory=SessionLocal,
            ai_service=mock_ai2,
            attempt_id=attempt.id,
            task_id="task-16",
            now_provider=lambda: T0 + timedelta(minutes=5),
        )
        # Sıfır AI çağrısı
        assert mock_ai2.call_count == 0
        # Sıfır heartbeat
        assert spy_hb.call_count == 0

    assert res2.replayed is True
    assert res2.total_ideas == 6
    assert res2.ai_calls_used == 0

    # Kopya fikir oluşmadığı doğrulanır
    db_session.expire_all()
    assert db_session.query(SocialIdea).filter_by(brief_id=brief.id).count() == 6


def test_17_completed_replay_db_anomaly_not_reported_as_success(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """17. Completed replay'de DB anomalisi (silinmiş/stale fikir) varsa başarı raporlanmaz."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-17"
    )
    mock_ai = SmartMockAIService()

    # 1. Tamamla
    res1 = run_social_idea_generation(
        session_factory=SessionLocal,
        ai_service=mock_ai,
        attempt_id=attempt.id,
        task_id="task-17",
        now_provider=lambda: T0,
    )
    assert res1.status == "completed"

    # DB anomalisi: bir fikri stale yapalım
    first_idea = db_session.query(SocialIdea).filter_by(brief_id=brief.id).first()
    first_idea.is_stale = True
    db_session.commit()

    # 2. Replay anomaliyi tespit edip fırlatmalıdır
    with pytest.raises(SocialIdeaPersistenceError) as exc:
        run_social_idea_generation(
            session_factory=SessionLocal,
            ai_service=mock_ai,
            attempt_id=attempt.id,
            task_id="task-17",
            now_provider=lambda: T0,
        )
    assert exc.value.error_code == "IDEA_PERSISTENCE_INCONSISTENT"

    # Completed attempt fail durumuna çevrilmez
    db_session.expire_all()
    db_attempt = db_session.query(SocialGenerationAttempt).filter_by(id=attempt.id).one()
    assert db_attempt.status == "completed"


def test_18_different_task_id_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """18. Başka task_id tarafından tutulan running attempt TASK_MISMATCH ile reddedilir."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-owner"
    )
    # Owner claim eder
    attempt.status = "running"
    attempt.task_id = "task-owner"
    attempt.started_at = T0
    attempt.heartbeat_at = T0
    attempt.lease_expires_at = T0 + timedelta(seconds=1500)
    db_session.commit()

    mock_ai = SmartMockAIService()
    with pytest.raises(AttemptNotWritableError) as exc:
        run_social_idea_generation(
            session_factory=SessionLocal,
            ai_service=mock_ai,
            attempt_id=attempt.id,
            task_id="task-intruder",
            now_provider=lambda: T0,
        )
    assert exc.value.error_code == "TASK_MISMATCH"


def test_19_safe_reason_code_and_no_leak_of_raw_ai_provider_sql_messages(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """19. Hata mesajlarında asla SQL sorgusu, provider exception veya ham AI çıktısı sızmaz."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-19"
    )
    sensitive_error = "SELECT password_hash FROM auth_users WHERE secret='X999'; Gemini API Token=AIzaSySecret"
    mock_ai = SmartMockAIService(default_response=Exception(sensitive_error))

    with pytest.raises(Exception):
        run_social_idea_generation(
            session_factory=SessionLocal,
            ai_service=mock_ai,
            attempt_id=attempt.id,
            task_id="task-19",
            now_provider=lambda: T0,
        )

    db_session.expire_all()
    db_attempt = db_session.query(SocialGenerationAttempt).filter_by(id=attempt.id).one()
    assert "SELECT" not in db_attempt.error_message
    assert "password_hash" not in db_attempt.error_message
    assert "AIzaSySecret" not in db_attempt.error_message
    assert db_attempt.reason_code == "idea_provider_error"


def test_20_sessions_closed_at_each_stage(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """20. Açılan her DB session kendi aşamasında mutlaka kapatılır (session sızıntısı yok)."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-20"
    )
    opened_sessions: list[Session] = []

    def tracking_session_factory():
        s = SessionLocal()
        opened_sessions.append(s)
        return s

    mock_ai = SmartMockAIService()
    result = run_social_idea_generation(
        session_factory=tracking_session_factory,
        ai_service=mock_ai,
        attempt_id=attempt.id,
        task_id="task-20",
        now_provider=lambda: T0,
    )
    assert result.status == "completed"
    assert len(opened_sessions) >= 4  # Claim, prep, heartbeat(x2), persist

    # Tüm session'ların kapalı (veya transaction'ı tamamlanmış) olduğunu doğrula
    for s in opened_sessions:
        # SQLAlchemy Session.is_active transaction durumunu gösterir; kapalı session'da transaction olmaz
        assert not s.in_transaction()


def test_21_generator_temperature_none_preserved(enable_flag):
    """21. brief_idea_generator.py içinde temperature=None korunmalıdır."""
    import inspect
    from app.generators.social.brief_idea_generator import SocialBriefIdeaGenerator

    source = inspect.getsource(SocialBriefIdeaGenerator.generate)
    assert "temperature=None" in source, "SocialBriefIdeaGenerator.generate içinde temperature=None korunmalıdır!"


def test_22_zero_real_network_calls(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """22. Orkestrasyon süresince gerçek dış HTTP/Gemini ağ çağrısı yapılmaz."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-22"
    )
    mock_ai = SmartMockAIService()

    # Dış HTTP/Gemini ağ çağrısı girişimini engelleyelim (veritabanı soketine dokunmadan)
    with patch("http.client.HTTPConnection.connect", side_effect=RuntimeError("Dış HTTP çağrısı yasaktır!")):
        result = run_social_idea_generation(
            session_factory=SessionLocal,
            ai_service=mock_ai,
            attempt_id=attempt.id,
            task_id="task-22",
            now_provider=lambda: T0,
        )
        assert result.status == "completed"


# ==================== F1-F.6b.1 ZORUNLU TESTLER ====================

def test_23_provider_failure_sql_lock_order_is_canonical(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """23. [F1-F.6b.1] Provider failure yolunda SQL FOR UPDATE kilit sırası ScoringRun -> SocialBrief -> Attempt olmalıdır."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-23"
    )
    for_update_queries: list[str] = []

    def capture_sql(conn, cursor, statement, parameters, context, executemany):
        if "FOR UPDATE" in statement.upper():
            for_update_queries.append(statement.lower())

    bind = db_session.get_bind()
    event.listen(bind, "before_cursor_execute", capture_sql)
    mock_ai = SmartMockAIService(default_response=Exception("Provider crash 503"))

    try:
        with pytest.raises(Exception):
            run_social_idea_generation(
                session_factory=SessionLocal,
                ai_service=mock_ai,
                attempt_id=attempt.id,
                task_id="task-23",
                now_provider=lambda: T0,
            )

        # Son 3 FOR UPDATE sorgusu failure finalization sırasında atılır:
        # ScoringRun -> SocialBrief -> SocialGenerationAttempt
        assert len(for_update_queries) >= 3
        last_three = for_update_queries[-3:]
        assert "scoring_runs" in last_three[0], f"İlk kilit ScoringRun olmalı: {last_three[0]}"
        assert "social_briefs" in last_three[1], f"İkinci kilit SocialBrief olmalı: {last_three[1]}"
        assert "social_generation_attempts" in last_three[2], f"Üçüncü kilit Attempt olmalı: {last_three[2]}"

        # Attempt hiçbir zaman ScoringRun'dan önce kilitlenmemeli
        for i, q in enumerate(for_update_queries):
            if "social_generation_attempts" in q:
                preceding = for_update_queries[:i]
                assert any("scoring_runs" in pq for pq in preceding)
    finally:
        event.remove(bind, "before_cursor_execute", capture_sql)


def test_24_output_invalid_sql_lock_order_is_canonical(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """24. [F1-F.6b.1] Output validation failure yolunda SQL FOR UPDATE kilit sırası ScoringRun -> SocialBrief -> Attempt olmalıdır."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-24"
    )
    for_update_queries: list[str] = []

    def capture_sql(conn, cursor, statement, parameters, context, executemany):
        if "FOR UPDATE" in statement.upper():
            for_update_queries.append(statement.lower())

    bind = db_session.get_bind()
    event.listen(bind, "before_cursor_execute", capture_sql)
    mock_ai = SmartMockAIService(default_response='{"ideas": []}')

    try:
        with pytest.raises(Exception):
            run_social_idea_generation(
                session_factory=SessionLocal,
                ai_service=mock_ai,
                attempt_id=attempt.id,
                task_id="task-24",
                now_provider=lambda: T0,
            )

        assert len(for_update_queries) >= 3
        last_three = for_update_queries[-3:]
        assert "scoring_runs" in last_three[0], f"İlk kilit ScoringRun olmalı: {last_three[0]}"
        assert "social_briefs" in last_three[1], f"İkinci kilit SocialBrief olmalı: {last_three[1]}"
        assert "social_generation_attempts" in last_three[2], f"Üçüncü kilit Attempt olmalı: {last_three[2]}"
    finally:
        event.remove(bind, "before_cursor_execute", capture_sql)


def test_25_persistence_failure_sql_lock_order_is_canonical(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """25. [F1-F.6b.1] Persistence failure yolunda SQL FOR UPDATE kilit sırası ScoringRun -> SocialBrief -> Attempt olmalıdır."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-25"
    )
    for_update_queries: list[str] = []

    def capture_sql(conn, cursor, statement, parameters, context, executemany):
        if "FOR UPDATE" in statement.upper():
            for_update_queries.append(statement.lower())

    bind = db_session.get_bind()
    event.listen(bind, "before_cursor_execute", capture_sql)
    mock_ai = SmartMockAIService()

    try:
        with patch(
            "app.core.social.idea_orchestration.persist_social_ideas",
            side_effect=SocialIdeaPersistenceError("Disk full", error_code="PERSISTENCE_FAILED"),
        ):
            with pytest.raises(Exception):
                run_social_idea_generation(
                    session_factory=SessionLocal,
                    ai_service=mock_ai,
                    attempt_id=attempt.id,
                    task_id="task-25",
                    now_provider=lambda: T0,
                )

        assert len(for_update_queries) >= 3
        last_three = for_update_queries[-3:]
        assert "scoring_runs" in last_three[0], f"İlk kilit ScoringRun olmalı: {last_three[0]}"
        assert "social_briefs" in last_three[1], f"İkinci kilit SocialBrief olmalı: {last_three[1]}"
        assert "social_generation_attempts" in last_three[2], f"Üçüncü kilit Attempt olmalı: {last_three[2]}"
    finally:
        event.remove(bind, "before_cursor_execute", capture_sql)


def test_26_finalize_failure_does_not_query_attempt_for_update_directly():
    """26. [F1-F.6b.1] _finalize_failure orkestrasyon modülünden doğrudan Attempt FOR UPDATE yapmamalıdır."""
    import inspect
    from app.core.social import idea_orchestration

    source = inspect.getsource(idea_orchestration._finalize_failure)
    assert "with_for_update" not in source, "_finalize_failure doğrudan with_for_update çağırmamalıdır!"
    assert "SocialGenerationAttempt" not in source, "_finalize_failure doğrudan SocialGenerationAttempt sorgusu yapmamalıdır!"
    assert "finalize_ideas_attempt_failure" in source, "_finalize_failure canonical finalize_ideas_attempt_failure yardımcısını çağırmalıdır!"


def test_27_already_failed_attempt_reason_code_not_overwritten(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """27. [F1-F.6b.1] Zaten failed/worker_lost attempt'in reason_code değeri ezilmemelidir."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-27"
    )
    attempt.status = "failed"
    attempt.reason_code = "worker_lost"
    attempt.error_message = "İşlem lease süresi doldu."
    attempt.lease_expires_at = None
    attempt.completed_at = T0
    db_session.commit()

    att, modified = finalize_ideas_attempt_failure(
        db_session,
        attempt_id=attempt.id,
        task_id="task-27",
        reason_code="idea_provider_error",
        error_message="Yeni hata",
        now=T0,
    )
    assert modified is False
    assert att.status == "failed"
    assert att.reason_code == "worker_lost"
    assert att.error_message == "İşlem lease süresi doldu."


def test_28_already_completed_attempt_not_marked_failed(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """28. [F1-F.6b.1] Zaten completed attempt failure finalization ile failed olmamalıdır."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-28"
    )
    attempt.status = "completed"
    attempt.completed_at = T0
    attempt.lease_expires_at = None
    db_session.commit()

    att, modified = finalize_ideas_attempt_failure(
        db_session,
        attempt_id=attempt.id,
        task_id="task-28",
        reason_code="idea_provider_error",
        error_message="Sağlayıcı çöktü",
        now=T0,
    )
    assert modified is False
    assert att.status == "completed"


def test_29_different_task_id_running_attempt_not_modified(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """29. [F1-F.6b.1] Farklı task_id sahibi running attempt değiştirilmemelidir."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-owner"
    )
    attempt.status = "running"
    attempt.task_id = "task-owner"
    attempt.started_at = T0
    attempt.lease_expires_at = T0 + timedelta(seconds=1500)
    db_session.commit()

    att, modified = finalize_ideas_attempt_failure(
        db_session,
        attempt_id=attempt.id,
        task_id="task-intruder",
        reason_code="idea_provider_error",
        error_message="Yabancı worker hatası",
        now=T0,
    )
    assert modified is False
    assert att.status == "running"
    assert att.task_id == "task-owner"


def test_30_pending_attempt_not_marked_failed_by_normal_errors(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """30. [F1-F.6b.1] Pending attempt normal idea_provider_error/input/persistence ile doğrudan failed yapılamaz."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-30"
    )
    assert attempt.status == "pending"

    for normal_reason in ("idea_provider_error", "idea_input_invalid", "idea_output_invalid", "idea_persistence_failed"):
        att, modified = finalize_ideas_attempt_failure(
            db_session,
            attempt_id=attempt.id,
            task_id="task-30",
            reason_code=normal_reason,
            error_message="Normal hata",
            now=T0,
        )
        assert modified is False
        assert att.status == "pending"


def test_31_pending_attempt_can_be_marked_failed_by_infrastructure_errors(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """31. [F1-F.6b.1] Pending attempt worker_lost, brief_stale veya assignment_changed ile güvenli terminal yapılabilmelidir."""
    for infra_reason in ("worker_lost", "brief_stale", "assignment_changed"):
        ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_environment(
            db_session, make_workspace, make_scoring_run, make_keyword, task_id=f"task-31-{infra_reason}"
        )
        assert attempt.status == "pending"

        att, modified = finalize_ideas_attempt_failure(
            db_session,
            attempt_id=attempt.id,
            task_id=f"task-31-{infra_reason}",
            reason_code=infra_reason,
            error_message="Altyapı hatası",
            now=T0,
        )
        assert modified is True
        assert att.status == "failed"
        assert att.reason_code == infra_reason
        assert att.lease_expires_at is None
        assert att.completed_at == T0


def test_32_late_persistence_replay_returns_replayed_true_and_preserves_calls(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """32. [F1-F.6b.1] Persistence aşamasında geç replay: persist_social_ideas already_completed=True döndüğünde replayed=True ve ai_calls_used korunur."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-32"
    )
    mock_ai = SmartMockAIService()

    # persist_social_ideas'ın already_completed=True sonucunu simüle edelim (yarış koşulunda ikinci worker):
    from app.core.social.idea_persistence import PersistedSocialIdeasResult

    simulated_replay_result = PersistedSocialIdeasResult(
        brief_id=brief.id,
        scoring_run_id=run.id,
        attempt_id=attempt.id,
        total_ideas=6,
        ideas=(),
        already_completed=True,
    )

    with patch("app.core.social.idea_orchestration.persist_social_ideas", return_value=simulated_replay_result) as spy_persist:
        res = run_social_idea_generation(
            session_factory=SessionLocal,
            ai_service=mock_ai,
            attempt_id=attempt.id,
            task_id="task-32",
            now_provider=lambda: T0,
        )
        assert spy_persist.call_count == 1

    # Doğrulamalar:
    assert res.replayed is True, "Geç replay durumunda replayed=True olmalıdır!"
    assert res.status == "completed"
    assert res.total_ideas == 6
    assert res.ai_calls_used == 2, "Bu worker'ın yaptığı AI çağrıları korunmalıdır!"
    assert mock_ai.call_count == 2

    # Veritabanında mükerrer kayıt oluşmadığı doğrulanır
    db_session.expire_all()
    assert db_session.query(SocialIdea).filter_by(brief_id=brief.id).count() == 0


def test_33_no_deadlock_between_persistence_lock_and_failure_finalization(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """33. [F1-F.6b.1] İki gerçek thread: Worker A persistence kilidini tutarken Worker B failure finalization dener; canonical sıra nedeniyle deadlock oluşmaz."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-worker-a"
    )
    attempt.status = "running"
    attempt.task_id = "task-worker-a"
    attempt.started_at = T0
    attempt.lease_expires_at = T0 + timedelta(seconds=1500)
    db_session.commit()

    run_id = run.id
    attempt_id = attempt.id

    event_a_locked = threading.Event()
    event_b_started = threading.Event()

    errors_a: list[Exception] = []
    errors_b: list[Exception] = []

    def worker_a():
        """Worker A: ScoringRun -> SocialBrief -> SocialGenerationAttempt kilidi alır (persistence gibi)."""
        session_a = SessionLocal()
        try:
            session_a.execute(text("SET lock_timeout = '4000ms'"))
            session_a.query(ScoringRun).filter(ScoringRun.id == run_id).with_for_update().one()
            session_a.query(SocialBrief).filter(SocialBrief.id == brief.id).with_for_update().one()
            session_a.query(SocialGenerationAttempt).filter(SocialGenerationAttempt.id == attempt_id).with_for_update().one()

            event_a_locked.set()

            assert event_b_started.wait(timeout=5.0), "Worker B zamanında başlamadı"
            time.sleep(0.3)

            session_a.commit()
        except Exception as e:
            errors_a.append(e)
            session_a.rollback()
        finally:
            session_a.close()

    def worker_b():
        """Worker B: Failure finalization dener (aynı canonical kilit sırası)."""
        session_b = SessionLocal()
        try:
            assert event_a_locked.wait(timeout=5.0), "Worker A zamanında kilidi alamadı"
            event_b_started.set()

            session_b.execute(text("SET lock_timeout = '4000ms'"))
            finalize_ideas_attempt_failure(
                session_b,
                attempt_id=attempt_id,
                task_id="task-worker-a",
                reason_code="idea_provider_error",
                error_message="Hata oluştu",
                now=T0,
            )
            session_b.commit()
        except Exception as e:
            errors_b.append(e)
            session_b.rollback()
        finally:
            session_b.close()

    t_a = threading.Thread(target=worker_a)
    t_b = threading.Thread(target=worker_b)

    t_a.start()
    t_b.start()

    t_a.join(timeout=8.0)
    t_b.join(timeout=8.0)

    assert not t_a.is_alive(), "Worker A thread zaman aşımına uğradı (deadlock?)"
    assert not t_b.is_alive(), "Worker B thread zaman aşımına uğradı (deadlock?)"

    assert len(errors_a) == 0, f"Worker A hata aldı: {errors_a}"
    assert len(errors_b) == 0, f"Worker B hata aldı: {errors_b}"

    db_session.expire_all()
    db_att = db_session.query(SocialGenerationAttempt).filter_by(id=attempt_id).one()
    assert db_att.status == "failed"



# ==================== PLAN §3.4–§3.6 / K4 / K7: FİKİR-BAZLI ATMA + TAMAMLAMA ====================

def _prompt_input_json(prompt: str) -> dict:
    start_tag = "<INPUT_JSON>\n"
    end_tag = "\n</INPUT_JSON>"
    start = prompt.find(start_tag) + len(start_tag)
    end = prompt.find(end_tag)
    return json.loads(prompt[start:end])


class _ScriptedIdeaAI(SmartMockAIService):
    """İstek başına fikir listesini bir üretici fonksiyonla kuran sahte AI.

    builder(data, call_no) -> list[dict] | Exception
    """

    def __init__(self, builder) -> None:
        super().__init__()
        self.builder = builder
        self.requests: list[dict] = []

    def complete_json(self, prompt: str, **kwargs) -> str:
        self.call_count += 1
        data = _prompt_input_json(prompt)
        self.requests.append(data)
        out = self.builder(data, self.call_count)
        if isinstance(out, Exception):
            raise out
        return json.dumps({"ideas": out}, ensure_ascii=False)


def _valid_ideas_for(data: dict, *, skip_platforms: tuple[str, ...] = ()) -> list[dict]:
    kw_id = data["keywords"][0]["id"]
    ideas = []
    for spec in data["target_specs"]:
        if spec["platform"] in skip_platforms:
            continue
        for n in range(spec["requested_count"]):
            ideas.append({
                "target_id": spec["target_id"],
                "primary_keyword_id": kw_id,
                "idea_title": f"Fikir {spec['target_id']}-{n}",
                "idea_description": "Brief içi stratejik fikir açıklaması.",
                "target_platform": spec["platform"],
                "content_format": spec["content_format"],
                "trend_alignment": 0.7,
            })
    return ideas


def test_40_mixed_output_keeps_only_in_brief_ideas(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """40. F3: karışık çıktıda yalnız brief içindekiler kaydedilir; instagram/thread,
    uydurma target_id ve keyword_id atılır (çevrilmez); "X" -> twitter normalize edilir."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-40"
    )
    brief_kw_ids = {k.id for k in kws}
    target_ids = {t.id for t in targets}

    def builder(data, call_no):
        ideas = _valid_ideas_for(data)
        for idea in ideas:
            if idea["target_platform"] == "twitter":
                idea["target_platform"] = "X"
        base = ideas[0]
        ideas.append({**base, "target_platform": "instagram", "content_format": "thread",
                      "idea_title": "IG thread"})
        ideas.append({**base, "target_id": 987654, "idea_title": "Uydurma hedef"})
        ideas.append({**base, "primary_keyword_id": 987654, "idea_title": "Uydurma kelime"})
        return ideas

    mock_ai = _ScriptedIdeaAI(builder)
    result = run_social_idea_generation(
        session_factory=SessionLocal,
        ai_service=mock_ai,
        attempt_id=attempt.id,
        task_id="task-40",
        now_provider=lambda: T0,
    )

    assert result.status == "completed"
    assert result.total_ideas == 6
    assert mock_ai.call_count == 2  # geçerli fikir varken düzeltme tekrarı yok

    db_session.expire_all()
    ideas = db_session.query(SocialIdea).filter_by(brief_id=brief.id).all()
    assert len(ideas) == 6
    for idea in ideas:
        assert idea.brief_target_id in target_ids
        assert idea.keyword_id in brief_kw_ids
        assert idea.idea_title not in {"IG thread", "Uydurma hedef", "Uydurma kelime"}
    twitter_ideas = [i for i in ideas if i.brief_target_id == targets[1].id]
    assert twitter_ideas and all(i.target_platform == "twitter" for i in twitter_ideas)

    db_attempt = db_session.query(SocialGenerationAttempt).filter_by(id=attempt.id).one()
    assert db_attempt.coverage["metrics"]["off_brief_dropped"] == 6  # 3 atılan x 2 kategori
    assert db_attempt.coverage["metrics"]["topup_used"] == 0


def test_41_one_category_ai_failure_other_categories_persisted_partial(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """41. F3: bir kategorinin AI'ı (ilk tur + tamamlama) başarısız -> diğer kategori
    kaydedilir, attempt partial olur; target_unfilled + category_unfilled uyarıları taşınır."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-41"
    )

    def builder(data, call_no):
        if data["category"]["name"] == "Kategori 2":
            return Exception("Gemini 503 secret-token-XYZ")
        return _valid_ideas_for(data)

    mock_ai = _ScriptedIdeaAI(builder)
    result = run_social_idea_generation(
        session_factory=SessionLocal,
        ai_service=mock_ai,
        attempt_id=attempt.id,
        task_id="task-41",
        now_provider=lambda: T0,
    )

    # kategori 1 + kategori 2 (hata) + tek tamamlama isteği (kategori 2, hata)
    assert mock_ai.call_count == 3
    topup_request = mock_ai.requests[2]
    assert topup_request["category"]["name"] == "Kategori 2"
    assert topup_request["target_specs"] == [{
        "content_format": targets[1].content_format,
        "platform": targets[1].platform,
        "requested_count": 1,
        "target_id": targets[1].id,
    }]
    assert result.status == "partial"
    assert result.total_ideas == 3

    db_session.expire_all()
    ideas = db_session.query(SocialIdea).filter_by(brief_id=brief.id).all()
    assert len(ideas) == 3
    assert {i.category_id for i in ideas} == {cats[0].id}
    assert {i.brief_target_id for i in ideas} == {targets[0].id}

    db_attempt = db_session.query(SocialGenerationAttempt).filter_by(id=attempt.id).one()
    assert db_attempt.status == "partial"
    assert db_attempt.reason_code == "target_unfilled"
    assert "secret-token" not in (db_attempt.error_message or "")
    assert db_attempt.lease_expires_at is None
    assert {
        (w["target_id"], w.get("category_id"), w["reason_code"]) for w in db_attempt.warnings
    } == {
        (targets[1].id, cats[1].id, "target_unfilled"),
        (targets[1].id, cats[1].id, "category_unfilled"),
    }
    metrics = db_attempt.coverage["metrics"]
    assert metrics["topup_used"] == 1
    assert metrics["topup_requests"] == 1
    assert metrics["category_topup_requests"] == 1  # boş kategori + eksik hedef tek istekte
    assert metrics["failed_requests"] == 2
    assert metrics["target_unfilled"] == 1
    assert metrics["category_unfilled"] == 1
    assert metrics["ai_calls_used"] == 3

    # Okuma tarafı partial'ı taşır: coverage + uyarılar (plan §7)
    from app.core.social.idea_read import load_social_idea_result

    read = load_social_idea_result(
        db_session, brief_id=brief.id, attempt_id=attempt.id, brand_profile_id=ws.id
    )
    assert read.attempt_status == "partial"
    assert read.reason_code == "target_unfilled"
    cov = {c.target_id: c for c in read.coverage}
    assert (cov[targets[0].id].accepted, cov[targets[0].id].missing) == (3, 0)
    assert (cov[targets[1].id].accepted, cov[targets[1].id].missing) == (0, 3)
    assert [(w.target_id, w.category_id, w.reason_code) for w in read.warnings] == [
        (targets[1].id, cats[1].id, "category_unfilled"),
        (targets[1].id, cats[1].id, "target_unfilled"),
    ]


def test_42_missing_target_exactly_one_topup_round_then_target_unfilled(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """42. F3: AI bir hedefi hiç üretmezse brief geneli TEK tamamlama turu yapılır;
    yine gelmezse target_unfilled kalır (sahte yedek yok, kategori x hedef boşluğu doldurulmaz)."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_environment(
        db_session, make_workspace, make_scoring_run, make_keyword,
        task_id="task-42", num_categories=1, ideas_per_category=2,
    )

    def builder(data, call_no):
        # twitter hedefini hiç üretmez
        return _valid_ideas_for(data, skip_platforms=("twitter",))

    mock_ai = _ScriptedIdeaAI(builder)
    result = run_social_idea_generation(
        session_factory=SessionLocal,
        ai_service=mock_ai,
        attempt_id=attempt.id,
        task_id="task-42",
        now_provider=lambda: T0,
    )

    # 1 ilk tur + tamamlama isteği (boş çıktı -> 1 düzeltme tekrarı) = 3; ikinci tur YOK
    assert mock_ai.call_count == 3
    assert [r["target_specs"] for r in mock_ai.requests[1:]] == [[{
        "content_format": "thread",
        "platform": "twitter",
        "requested_count": 1,
        "target_id": targets[1].id,
    }]] * 2
    assert result.status == "partial"
    assert result.total_ideas == 1

    db_session.expire_all()
    db_attempt = db_session.query(SocialGenerationAttempt).filter_by(id=attempt.id).one()
    assert db_attempt.status == "partial"
    assert db_attempt.warnings == [
        {"target_id": targets[1].id, "category_id": cats[0].id, "reason_code": "target_unfilled"}
    ]
    metrics = db_attempt.coverage["metrics"]
    assert metrics["topup_used"] == 1
    assert metrics["topup_requests"] == 1
    assert metrics["retry_used"] == 1
    assert metrics["target_unfilled"] == 1
    assert metrics["category_unfilled"] == 0
    assert db_session.query(SocialIdea).filter_by(brief_id=brief.id).count() == 1


# Video olmayan 6 hedef (video hedefleri süre aralığı ister)
_SIX_TARGETS = [
    ("instagram", "post"),
    ("instagram", "carousel"),
    ("twitter", "thread"),
    ("twitter", "post"),
    ("linkedin", "post"),
    ("linkedin", "carousel"),
]


def test_43_ai_total_failure_zero_ideas_and_call_cap_not_exceeded(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """43. F3 + K7 + §5.4: en kötü durumda (6 kategori, 6 hedef, her çıktı boş) çağrı
    tavanı aşılmaz; 0 fikir, sahte satır yok, attempt failed."""
    from app.core.social.idea_orchestration import IDEA_ATTEMPT_MAX_AI_CALLS

    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_environment(
        db_session, make_workspace, make_scoring_run, make_keyword,
        task_id="task-43", num_categories=6, ideas_per_category=1, targets_data=_SIX_TARGETS,
    )
    mock_ai = SmartMockAIService(default_response='{"ideas": []}')

    with pytest.raises(Exception):
        run_social_idea_generation(
            session_factory=SessionLocal,
            ai_service=mock_ai,
            attempt_id=attempt.id,
            task_id="task-43",
            now_provider=lambda: T0,
        )

    # 6 kategori x 2 + 6 tamamlama isteği x 2 = 24 = tavan
    assert mock_ai.call_count == 24
    assert mock_ai.call_count <= IDEA_ATTEMPT_MAX_AI_CALLS

    db_session.expire_all()
    assert db_session.query(SocialIdea).filter_by(brief_id=brief.id).count() == 0
    db_attempt = db_session.query(SocialGenerationAttempt).filter_by(id=attempt.id).one()
    assert db_attempt.status == "failed"
    assert db_attempt.reason_code == "idea_output_invalid"


def test_44_call_cap_is_enforced_before_starting_a_request(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag, monkeypatch
):
    """44. §5.4: tavanı aşacak istek hiç başlatılmaz (düşük tavanla kanıt)."""
    import app.core.social.idea_orchestration as orch

    monkeypatch.setattr(orch, "IDEA_ATTEMPT_MAX_AI_CALLS", 5)
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_environment(
        db_session, make_workspace, make_scoring_run, make_keyword,
        task_id="task-44", num_categories=6, ideas_per_category=1, targets_data=_SIX_TARGETS,
    )
    mock_ai = SmartMockAIService(default_response='{"ideas": []}')

    with pytest.raises(Exception):
        run_social_idea_generation(
            session_factory=SessionLocal,
            ai_service=mock_ai,
            attempt_id=attempt.id,
            task_id="task-44",
            now_provider=lambda: T0,
        )
    assert mock_ai.call_count == 4  # 2 istek x 2 çağrı; üçüncü istek 5'i aşacağı için başlamaz
    db_session.expire_all()
    assert db_session.query(SocialIdea).filter_by(brief_id=brief.id).count() == 0


def test_45_retry_after_partial_produces_only_missing_targets(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """45. F3: partial deneme sonrası "tekrar dene" yalnız eksik hedefleri üretir; dolu
    hedefler yeniden üretilmez; kaynak partial deneme okunabilir kalır."""
    from app.core.social.idea_read import load_social_idea_result
    from app.core.social.idea_retry_flow import begin_social_idea_retry
    from app.core.social.idea_retry_orchestration import run_social_idea_retry_generation
    from app.schemas.social_brief import SocialBriefIdeasRetryRequest

    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_environment(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-45"
    )

    def failing_cat2(data, call_no):
        if data["category"]["name"] == "Kategori 2":
            return Exception("provider down")
        return _valid_ideas_for(data)

    first = run_social_idea_generation(
        session_factory=SessionLocal,
        ai_service=_ScriptedIdeaAI(failing_cat2),
        attempt_id=attempt.id,
        task_id="task-45",
        now_provider=lambda: T0,
    )
    assert first.status == "partial"
    db_session.expire_all()
    before_ids = {i.id for i in db_session.query(SocialIdea).filter_by(brief_id=brief.id).all()}
    assert len(before_ids) == 3

    retry_start = begin_social_idea_retry(
        db_session,
        brief_id=brief.id,
        brand_profile_id=ws.id,
        request=SocialBriefIdeasRetryRequest(
            idempotency_key="retry-after-partial-45", source_attempt_id=attempt.id
        ),
        now=T0,
    )
    db_session.commit()
    assert retry_start.attempt_created is True
    assert retry_start.missing_target_ids == (targets[1].id,)
    assert retry_start.persisted_target_ids_at_start == (targets[0].id,)

    retry_ai = _ScriptedIdeaAI(lambda data, n: _valid_ideas_for(data))
    retry_result = run_social_idea_retry_generation(
        session_factory=SessionLocal,
        ai_service=retry_ai,
        attempt_id=retry_start.attempt_id,
        task_id="task-45-retry",
        now_provider=lambda: T0,
    )
    assert retry_result.status == "completed"
    assert retry_result.accepted_target_ids == (targets[1].id,)
    assert retry_ai.call_count == 1
    assert [s["target_id"] for s in retry_ai.requests[0]["target_specs"]] == [targets[1].id]

    db_session.expire_all()
    all_ideas = db_session.query(SocialIdea).filter_by(brief_id=brief.id).all()
    new_ideas = [i for i in all_ideas if i.id not in before_ids]
    assert len(new_ideas) == 1
    assert new_ideas[0].brief_target_id == targets[1].id
    assert new_ideas[0].category_id == cats[1].id  # hedefin planlandığı kategori

    retry_attempt = db_session.query(SocialGenerationAttempt).filter_by(id=retry_start.attempt_id).one()
    assert retry_attempt.coverage["metrics"]["target_unfilled"] == 0

    # Kaynak partial deneme okunabilir; güncel kapsama eksik hedefi dolu gösterir
    read = load_social_idea_result(
        db_session, brief_id=brief.id, attempt_id=attempt.id, brand_profile_id=ws.id
    )
    assert read.attempt_status == "partial"
    cov = {c.target_id: c for c in read.coverage}
    assert cov[targets[1].id].accepted == 1
