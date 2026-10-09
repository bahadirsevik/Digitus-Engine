# -*- coding: utf-8 -*-
"""MİKRO FAZ F1-F.5 — Saf ve Atomik Fikir Persistence Entegrasyon Testleri.

Tüm testler gerçek PostgreSQL test veritabanını ve SQLAlchemy oturumunu kullanır.
Test edilen alanlar:
1. Başarılı atomik kayıt ve ORM alan eşlemesi
2. Kategori ve hedef kotalarının tam uygulanması (Cartesian olmama garantisi)
3. Girdi doğrulama ve sözleşme kısıtları (eksik/fazla/mükerrer/forged)
4. Brief ilişkilerinin DB'den yeniden doğrulanması (target, keyword, category)
5. Worker sahipliği, lease, stale brief ve version mismatch kontrolleri
6. Pending/failed/partial attempt reddi
7. Completed replay idempotency ve DB anomali tespiti
8. Transaction sınırları (commit/rollback çağırmama, caller rollback ile tam temizlik)
9. İki oturumlu eşzamanlılıkta kopya fikir oluşmama garantisi
10. SQL kilit sırası (ScoringRun -> SocialBrief -> SocialGenerationAttempt)
"""
from __future__ import annotations

import copy
import threading
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.config import settings
from app.core.social.idea_contract import ValidatedSocialIdea
from app.core.social.idea_flow import (
    begin_social_idea_generation,
)
from app.core.social.idea_persistence import (
    PersistedSocialIdeasResult,
    SocialIdeaPersistenceError,
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
    claim_attempt,
)
from app.generators.social.brief_idea_generator import SocialIdeaAIResult
from app.schemas.social_brief import SocialBriefIdeasGenerateRequest

T0 = datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def enable_flag(monkeypatch):
    """Test süresince feature flag'i aktif kılar."""
    monkeypatch.setattr(settings, "ENABLE_SOCIAL_BRIEF_FLOW", True)
    yield


def _setup_fresh_environment(
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
    num_kws: int = 3,
    workspace_name: str = "Ideas Persistence Brand",
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


def _setup_ready_ideas_attempt(
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
    task_id: str = "task-ideas-101",
    ideas_per_category: int = 3,
    num_categories: int = 2,
    targets_data: list[tuple[str, str]] | None = None,
    claim: bool = True,
):
    """Fikir üretimi persistence testleri için preflight edilmiş ve claim edilmiş ortam kurar."""
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
        idempotency_key="cat-attempt-key",
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

    if claim:
        claimed = claim_attempt(
            db_session,
            attempt_id=start.attempt_id,
            task_id=task_id,
        )
        db_session.commit()
    else:
        claimed = db_session.query(SocialGenerationAttempt).filter_by(id=start.attempt_id).one()

    return ws, run, brief, categories, targets, kws, start, claimed


def _build_valid_category_results(
    plan,
    attempt_id: int,
    brief_targets: list[SocialBriefTarget],
    brief_keywords: list,
) -> tuple[SocialIdeaAIResult, ...]:
    """Plan kotalarına tam uyan doğrulanmış sahte AI sonuçları üretir."""
    target_map = {t.id: t for t in brief_targets}
    kw_id = brief_keywords[0].id

    results = []
    for cp in plan.category_plans:
        ideas = []
        idea_num = 1
        for quota in cp.target_quotas:
            tid = quota.target_id
            count = quota.requested_count
            t = target_map[tid]
            for _ in range(count):
                ideas.append(
                    ValidatedSocialIdea(
                        target_id=tid,
                        primary_keyword_id=kw_id,
                        idea_title=f"Cat {cp.category_id} Fikir {idea_num}",
                        idea_description=f"Cat {cp.category_id} için açıklama metni {idea_num}.",
                        target_platform=t.platform,
                        content_format=t.content_format,
                        trend_alignment=0.85,
                    )
                )
                idea_num += 1

        results.append(
            SocialIdeaAIResult(
                attempt_id=attempt_id,
                category_id=cp.category_id,
                ideas=tuple(ideas),
                ai_calls_used=1,
            )
        )

    return tuple(results)


# ==================== TESTLER ====================

def test_01_successful_atomic_persistence(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """1. Başarılı atomik kayıt ve ORM alan eşlemesi doğrulanır."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-01"
    )

    results = _build_valid_category_results(start.plan, attempt.id, targets, kws)

    res = persist_social_ideas(
        db_session,
        attempt_id=attempt.id,
        task_id="task-01",
        category_results=results,
    )
    db_session.commit()

    assert res.already_completed is False
    assert res.total_ideas == 6
    assert len(res.ideas) == 6

    # ORM satırlarını kontrol et
    rows = db_session.query(SocialIdea).filter_by(brief_id=brief.id).order_by(SocialIdea.id.asc()).all()
    assert len(rows) == 6
    for r in rows:
        assert r.is_stale is False
        assert r.is_selected is False
        assert r.regeneration_count == 0
        assert r.trend_alignment == 0.85
        assert r.brief_id == brief.id
        assert r.brief_target_id in [t.id for t in targets]
        assert r.keyword_id in [k.id for k in kws]
        assert r.category_id in [c.id for c in cats]

    # Attempt completed durumu
    db_session.refresh(attempt)
    assert attempt.status == "completed"
    assert attempt.lease_expires_at is None
    assert attempt.completed_at is not None
    assert attempt.coverage["generated"]["total_accepted"] == 6


def test_02_multiple_categories_and_target_quotas_exactly_enforced(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """2. Birden fazla kategori ve hedef kotası tam uygulanır; Cartesian üretilmez."""
    # 2 kategori, 3 hedef kurgusu
    targets_data = [("instagram", "post"), ("twitter", "thread"), ("instagram", "carousel")]
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session,
        make_workspace,
        make_scoring_run,
        make_keyword,
        task_id="task-02",
        targets_data=targets_data,
        num_categories=2,
        ideas_per_category=3,
    )

    results = _build_valid_category_results(start.plan, attempt.id, targets, kws)
    res = persist_social_ideas(
        db_session,
        attempt_id=attempt.id,
        task_id="task-02",
        category_results=results,
    )
    db_session.commit()

    assert res.total_ideas == 6  # 2 x 3 = 6 (Cartesian 2x3=6 her kategori için değil, toplam 6)
    # Her kategori en az bir fikir almıştır
    cat_ids_in_ideas = {i.category_id for i in res.ideas}
    assert cat_ids_in_ideas == {cats[0].id, cats[1].id}

    # Her hedef genel planda en az bir fikir almıştır
    target_ids_in_ideas = {i.brief_target_id for i in res.ideas}
    assert target_ids_in_ideas == {t.id for t in targets}


def test_03_missing_category_result_is_partial_and_extra_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """3. Plan §3.6: AI'ı başarısız olan kategorinin sonucu gelmez; kalan kategoriler
    kaydedilir ve attempt partial olur (target_unfilled + category_unfilled uyarıları).
    Fazla/mükerrer kategori sonucu ise IDEA_PERSISTENCE_INVALID_INPUT ile reddedilir.
    """
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-03", num_categories=2
    )
    results = _build_valid_category_results(start.plan, attempt.id, targets, kws)

    # 1. Fazla kategori (3 adet) -> reddedilir, hiçbir şey yazılmaz
    with pytest.raises(SocialIdeaPersistenceError) as exc2:
        persist_social_ideas(
            db_session,
            attempt_id=attempt.id,
            task_id="task-03",
            category_results=(results[0], results[1], results[0]),
        )
    assert exc2.value.error_code == "IDEA_PERSISTENCE_INVALID_INPUT"
    db_session.rollback()

    # 2. Eksik kategori (yalnız kategori 1 başarılı) -> partial
    res = persist_social_ideas(
        db_session,
        attempt_id=attempt.id,
        task_id="task-03",
        category_results=(results[0],),
        metrics={"off_brief_dropped": 2, "topup_used": 1},
    )
    db_session.commit()

    assert res.status == "partial"
    assert res.total_ideas == len(results[0].ideas)
    # Plan: t0 -> kategori 1, t1 -> kategori 2 (round-robin); kategori 2 boş kaldı
    assert res.unfilled_target_ids == (targets[1].id,)
    assert res.unfilled_category_ids == (cats[1].id,)

    db_session.refresh(attempt)
    assert attempt.status == "partial"
    assert attempt.reason_code == "target_unfilled"
    assert attempt.completed_at is not None
    assert attempt.lease_expires_at is None
    assert attempt.coverage["generated"] == {
        "total_accepted": len(results[0].ideas),
        "target_ids": [targets[0].id],
    }
    assert attempt.coverage["metrics"]["off_brief_dropped"] == 2
    assert attempt.coverage["metrics"]["topup_used"] == 1
    assert attempt.coverage["metrics"]["target_unfilled"] == 1
    assert attempt.coverage["metrics"]["category_unfilled"] == 1
    assert {
        (w["target_id"], w.get("category_id"), w["reason_code"]) for w in attempt.warnings
    } == {
        (targets[1].id, cats[1].id, "target_unfilled"),
        (targets[1].id, cats[1].id, "category_unfilled"),
    }
    # Sahte yedek yok: yalnız gerçekten üretilen fikirler yazıldı
    assert db_session.query(SocialIdea).filter_by(brief_id=brief.id).count() == len(results[0].ideas)


def test_03b_empty_category_results_rejected_no_fake_rows(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """3b. K7: hiç kategori sonucu yoksa kayıt yapılmaz (çağıran attempt'i failed yapar)."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-03b", num_categories=2
    )
    with pytest.raises(SocialIdeaPersistenceError) as exc:
        persist_social_ideas(
            db_session,
            attempt_id=attempt.id,
            task_id="task-03b",
            category_results=(),
        )
    assert exc.value.error_code == "IDEA_PERSISTENCE_INVALID_INPUT"
    db_session.rollback()
    assert db_session.query(SocialIdea).filter_by(brief_id=brief.id).count() == 0


def test_04_duplicate_category_result_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """4. Mükerrer kategori sonucu IDEA_PERSISTENCE_INVALID_INPUT ile reddedilir."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-04", num_categories=2
    )
    results = _build_valid_category_results(start.plan, attempt.id, targets, kws)

    with pytest.raises(SocialIdeaPersistenceError) as exc:
        persist_social_ideas(
            db_session,
            attempt_id=attempt.id,
            task_id="task-04",
            category_results=(results[0], results[0]),  # mükerrer
        )
    assert exc.value.error_code == "IDEA_PERSISTENCE_INVALID_INPUT"


def test_05_category_result_order_mismatch_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """5. Kategori sonuç sırası plan sırasıyla uyuşmazsa IDEA_PERSISTENCE_INVALID_INPUT ile reddedilir."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-05", num_categories=2
    )
    results = _build_valid_category_results(start.plan, attempt.id, targets, kws)

    with pytest.raises(SocialIdeaPersistenceError) as exc:
        persist_social_ideas(
            db_session,
            attempt_id=attempt.id,
            task_id="task-05",
            category_results=(results[1], results[0]),  # ters sıra
        )
    assert exc.value.error_code == "IDEA_PERSISTENCE_INVALID_INPUT"


def test_06_wrong_attempt_id_in_result_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """6. Sonuçtaki attempt_id kilitlenen attempt ile uyuşmazsa reddedilir."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-06"
    )
    results = _build_valid_category_results(start.plan, attempt.id, targets, kws)

    # Yanlış attempt_id içeren sonuç
    bad_res = SocialIdeaAIResult(
        attempt_id=attempt.id + 9999,
        category_id=results[0].category_id,
        ideas=results[0].ideas,
        ai_calls_used=1,
    )
    with pytest.raises(SocialIdeaPersistenceError) as exc:
        persist_social_ideas(
            db_session,
            attempt_id=attempt.id,
            task_id="task-06",
            category_results=(bad_res, results[1]),
        )
    assert exc.value.error_code == "IDEA_PERSISTENCE_INVALID_INPUT"


def test_07_under_quota_category_accepted_over_quota_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """7. Kota altı kategori kabul edilir (kategori x hedef kotası garanti değil, K4);
    bir (kategori, hedef) hücresinin plan kotasını AŞMASI reddedilir."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-07"
    )
    results = _build_valid_category_results(start.plan, attempt.id, targets, kws)

    # 1. Kota aşımı (bir fikir fazla) -> reddedilir
    over_res = SocialIdeaAIResult(
        attempt_id=attempt.id,
        category_id=results[0].category_id,
        ideas=results[0].ideas + (results[0].ideas[0],),
        ai_calls_used=1,
    )
    with pytest.raises(SocialIdeaPersistenceError) as exc:
        persist_social_ideas(
            db_session,
            attempt_id=attempt.id,
            task_id="task-07",
            category_results=(over_res, results[1]),
        )
    assert exc.value.error_code == "IDEA_PERSISTENCE_INVALID_INPUT"
    db_session.rollback()

    # 2. 1 fikir eksik (atılan fikir) -> her hedef ve kategori >= 1 olduğundan completed
    short_res = SocialIdeaAIResult(
        attempt_id=attempt.id,
        category_id=results[0].category_id,
        ideas=results[0].ideas[:-1],
        ai_calls_used=1,
    )
    res = persist_social_ideas(
        db_session,
        attempt_id=attempt.id,
        task_id="task-07",
        category_results=(short_res, results[1]),
    )
    db_session.commit()
    assert res.status == "completed"
    assert res.total_ideas == 5
    db_session.refresh(attempt)
    assert attempt.status == "completed"
    assert attempt.warnings == []
    assert attempt.coverage["generated"]["total_accepted"] == 5


def test_08_target_quota_mismatch_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """8. Hedef kotalarının dağılımı planla uyuşmazsa reddedilir."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-08"
    )
    results = _build_valid_category_results(start.plan, attempt.id, targets, kws)

    # Fikirlerin hedef ID'sini değiştirerek hedef kotasını bozalım
    # İlk fikri ikinci hedefe kaydır
    cat0_ideas = list(results[0].ideas)
    swapped_idea = ValidatedSocialIdea(
        target_id=targets[1].id,  # targets[0] olması bekleniyordu
        primary_keyword_id=cat0_ideas[0].primary_keyword_id,
        idea_title=cat0_ideas[0].idea_title,
        idea_description=cat0_ideas[0].idea_description,
        target_platform=targets[1].platform,
        content_format=targets[1].content_format,
        trend_alignment=cat0_ideas[0].trend_alignment,
    )
    cat0_ideas[0] = swapped_idea

    mismatched_res = SocialIdeaAIResult(
        attempt_id=attempt.id,
        category_id=results[0].category_id,
        ideas=tuple(cat0_ideas),
        ai_calls_used=1,
    )
    with pytest.raises(SocialIdeaPersistenceError) as exc:
        persist_social_ideas(
            db_session,
            attempt_id=attempt.id,
            task_id="task-08",
            category_results=(mismatched_res, results[1]),
        )
    assert exc.value.error_code == "IDEA_PERSISTENCE_INVALID_INPUT"


def test_09_idea_with_target_outside_brief_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """9. Brief dışı target içeren fikir IDEA_PERSISTENCE_INCONSISTENT ile reddedilir."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-09"
    )
    results = _build_valid_category_results(start.plan, attempt.id, targets, kws)

    # Hedefi DB'den kaldıralım (brief altında geçerli hedef kalmaz)
    db_session.delete(targets[0])
    db_session.commit()

    with pytest.raises(SocialIdeaPersistenceError) as exc:
        persist_social_ideas(
            db_session,
            attempt_id=attempt.id,
            task_id="task-09",
            category_results=results,
        )
    assert exc.value.error_code == "IDEA_PERSISTENCE_INCONSISTENT"


def test_10_idea_with_keyword_outside_brief_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """10. Brief dışı keyword içeren fikir IDEA_PERSISTENCE_INCONSISTENT ile reddedilir."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-10"
    )
    results = _build_valid_category_results(start.plan, attempt.id, targets, kws)

    # Fikrin keyword'ünü 999999 yapalım
    cat0_ideas = list(results[0].ideas)
    bad_kw_idea = ValidatedSocialIdea(
        target_id=cat0_ideas[0].target_id,
        primary_keyword_id=999999,  # brief dışı
        idea_title=cat0_ideas[0].idea_title,
        idea_description=cat0_ideas[0].idea_description,
        target_platform=cat0_ideas[0].target_platform,
        content_format=cat0_ideas[0].content_format,
        trend_alignment=cat0_ideas[0].trend_alignment,
    )
    cat0_ideas[0] = bad_kw_idea
    bad_res = SocialIdeaAIResult(
        attempt_id=attempt.id,
        category_id=results[0].category_id,
        ideas=tuple(cat0_ideas),
        ai_calls_used=1,
    )

    with pytest.raises(SocialIdeaPersistenceError) as exc:
        persist_social_ideas(
            db_session,
            attempt_id=attempt.id,
            task_id="task-10",
            category_results=(bad_res, results[1]),
        )
    assert exc.value.error_code == "IDEA_PERSISTENCE_INCONSISTENT"


def test_11_idea_with_category_outside_brief_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """11. Brief dışı kategori IDEA_PERSISTENCE_INCONSISTENT ile reddedilir."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-11"
    )
    results = _build_valid_category_results(start.plan, attempt.id, targets, kws)

    # Kategoriyi stale yapalım (brief altında geçerli/taze kategori kalmaz)
    cats[0].is_stale = True
    db_session.commit()

    with pytest.raises(SocialIdeaPersistenceError) as exc:
        persist_social_ideas(
            db_session,
            attempt_id=attempt.id,
            task_id="task-11",
            category_results=results,
        )
    assert exc.value.error_code == "IDEA_PERSISTENCE_INCONSISTENT"


def test_12_platform_or_format_mismatch_with_target_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """12. Fikrin platform veya formatı bağlı hedefle uyuşmazsa IDEA_PERSISTENCE_INCONSISTENT döner."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-12"
    )
    results = _build_valid_category_results(start.plan, attempt.id, targets, kws)

    cat0_ideas = list(results[0].ideas)
    # Hedef instagram/post, ama fikir instagram/reels
    mismatched_idea = ValidatedSocialIdea(
        target_id=cat0_ideas[0].target_id,
        primary_keyword_id=cat0_ideas[0].primary_keyword_id,
        idea_title=cat0_ideas[0].idea_title,
        idea_description=cat0_ideas[0].idea_description,
        target_platform="instagram",
        content_format="reels",  # uyuşmazlık
        trend_alignment=cat0_ideas[0].trend_alignment,
    )
    cat0_ideas[0] = mismatched_idea
    bad_res = SocialIdeaAIResult(
        attempt_id=attempt.id,
        category_id=results[0].category_id,
        ideas=tuple(cat0_ideas),
        ai_calls_used=1,
    )

    with pytest.raises(SocialIdeaPersistenceError) as exc:
        persist_social_ideas(
            db_session,
            attempt_id=attempt.id,
            task_id="task-12",
            category_results=(bad_res, results[1]),
        )
    assert exc.value.error_code == "IDEA_PERSISTENCE_INCONSISTENT"


def test_13_non_canonical_format_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """13. Kanonik format matrisi dışındaki platform/format IDEA_PERSISTENCE_INCONSISTENT ile reddedilir."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-13"
    )
    results = _build_valid_category_results(start.plan, attempt.id, targets, kws)

    # Hedef satırının formatını bozalım
    targets[0].content_format = "unknown_format"
    db_session.commit()

    with pytest.raises(SocialIdeaPersistenceError) as exc:
        persist_social_ideas(
            db_session,
            attempt_id=attempt.id,
            task_id="task-13",
            category_results=results,
        )
    assert exc.value.error_code == "IDEA_PERSISTENCE_INCONSISTENT"


def test_14_invalid_trend_alignment_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """14. trend_alignment sınır dışı veya boolean olduğunda reddedilir."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-14"
    )
    results = _build_valid_category_results(start.plan, attempt.id, targets, kws)

    for invalid_val in [1.5, -0.1, True, False, float("nan"), float("inf"), -float("inf"), 10**10000, -(10**10000)]:
        cat0_ideas = list(results[0].ideas)
        cat0_ideas[0] = ValidatedSocialIdea(
            target_id=cat0_ideas[0].target_id,
            primary_keyword_id=cat0_ideas[0].primary_keyword_id,
            idea_title=cat0_ideas[0].idea_title,
            idea_description=cat0_ideas[0].idea_description,
            target_platform=cat0_ideas[0].target_platform,
            content_format=cat0_ideas[0].content_format,
            trend_alignment=invalid_val,  # type: ignore[arg-type]
        )
        bad_res = SocialIdeaAIResult(
            attempt_id=attempt.id,
            category_id=results[0].category_id,
            ideas=tuple(cat0_ideas),
            ai_calls_used=1,
        )
        with pytest.raises(SocialIdeaPersistenceError) as exc:
            persist_social_ideas(
                db_session,
                attempt_id=attempt.id,
                task_id="task-14",
                category_results=(bad_res, results[1]),
            )
        assert exc.value.error_code == "IDEA_PERSISTENCE_INVALID_INPUT"
        # Ham exception veya dinamik değer mesajda yer almamalıdır
        assert "OverflowError" not in exc.value.message
        assert "10000" not in exc.value.message


def test_15_forged_input_and_list_instead_of_tuple_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """15. category_results tuple olmalıdır; sahte nesne veya liste reddedilir."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-15"
    )
    results = _build_valid_category_results(start.plan, attempt.id, targets, kws)

    # 1. Liste verildiğinde reddedilir
    with pytest.raises(SocialIdeaPersistenceError) as exc1:
        persist_social_ideas(
            db_session,
            attempt_id=attempt.id,
            task_id="task-15",
            category_results=list(results),  # type: ignore[arg-type]
        )
    assert exc1.value.error_code == "IDEA_PERSISTENCE_INVALID_INPUT"

    # 2. Sahte nesne içeren tuple reddedilir
    with pytest.raises(SocialIdeaPersistenceError) as exc2:
        persist_social_ideas(
            db_session,
            attempt_id=attempt.id,
            task_id="task-15",
            category_results=("not_result",),  # type: ignore[arg-type]
        )
    assert exc2.value.error_code == "IDEA_PERSISTENCE_INVALID_INPUT"


def test_16_stale_brief_fails_and_marks_attempt_failed(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """16. Brief stale işaretlenmişse AttemptNotWritableError(BRIEF_STALE) fırlatılır ve attempt failed olur."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-16"
    )
    results = _build_valid_category_results(start.plan, attempt.id, targets, kws)

    brief.is_stale = True
    db_session.commit()

    with pytest.raises(AttemptNotWritableError) as exc:
        persist_social_ideas(
            db_session,
            attempt_id=attempt.id,
            task_id="task-16",
            category_results=results,
        )
    assert exc.value.error_code == "BRIEF_STALE"

    db_session.refresh(attempt)
    assert attempt.status == "failed"
    assert attempt.reason_code == "brief_stale"


def test_17_assignment_version_mismatch_fails_and_marks_attempt_failed(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """17. Assignment version değişmişse AttemptNotWritableError(ASSIGNMENT_CHANGED) üretilir."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-17"
    )
    results = _build_valid_category_results(start.plan, attempt.id, targets, kws)

    brief.channel_assignment_version += 1
    db_session.commit()

    with pytest.raises(AttemptNotWritableError) as exc:
        persist_social_ideas(
            db_session,
            attempt_id=attempt.id,
            task_id="task-17",
            category_results=results,
        )
    assert exc.value.error_code == "ASSIGNMENT_CHANGED"

    db_session.refresh(attempt)
    assert attempt.status == "failed"
    assert attempt.reason_code == "assignment_changed"


def test_18_lease_expiry_fails_and_marks_attempt_worker_lost(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """18. Lease süresi dolmuşsa AttemptNotWritableError(WORKER_LOST) üretilir."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-18"
    )
    results = _build_valid_category_results(start.plan, attempt.id, targets, kws)

    # Lease süresini geriye alalım
    attempt.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=10)
    db_session.commit()

    with pytest.raises(AttemptNotWritableError) as exc:
        persist_social_ideas(
            db_session,
            attempt_id=attempt.id,
            task_id="task-18",
            category_results=results,
        )
    assert exc.value.error_code == "WORKER_LOST"

    db_session.refresh(attempt)
    assert attempt.status == "failed"
    assert attempt.reason_code == "worker_lost"


def test_19_wrong_task_id_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """19. Yanlış worker task_id ile persistence çağrısı TASK_MISMATCH üretir."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-19"
    )
    results = _build_valid_category_results(start.plan, attempt.id, targets, kws)

    with pytest.raises(AttemptNotWritableError) as exc:
        persist_social_ideas(
            db_session,
            attempt_id=attempt.id,
            task_id="different-task-id",
            category_results=results,
        )
    assert exc.value.error_code == "TASK_MISMATCH"


def test_20_pending_failed_partial_attempt_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """20. Pending, failed veya partial durumundaki attempt üzerinden yazım reddedilir."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-20", claim=False
    )
    results = _build_valid_category_results(start.plan, attempt.id, targets, kws)

    # 1. Pending (claim edilmemiş)
    assert attempt.status == "pending"
    with pytest.raises(AttemptNotWritableError):
        persist_social_ideas(
            db_session,
            attempt_id=attempt.id,
            task_id="task-20",
            category_results=results,
        )

    # 2. Failed
    attempt.status = "failed"
    db_session.commit()
    with pytest.raises(AttemptNotWritableError):
        persist_social_ideas(
            db_session,
            attempt_id=attempt.id,
            task_id="task-20",
            category_results=results,
        )

    # 3. Partial
    attempt.status = "partial"
    db_session.commit()
    with pytest.raises(AttemptNotWritableError):
        persist_social_ideas(
            db_session,
            attempt_id=attempt.id,
            task_id="task-20",
            category_results=results,
        )


def test_21_completed_replay_does_not_create_duplicate_rows(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """21. Zaten completed attempt tekrar çağrıldığında yeni fikir satırı eklenmez (already_completed=True)."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-21"
    )
    results = _build_valid_category_results(start.plan, attempt.id, targets, kws)

    # İlk başarılı kayıt
    res1 = persist_social_ideas(
        db_session,
        attempt_id=attempt.id,
        task_id="task-21",
        category_results=results,
    )
    db_session.commit()
    assert res1.already_completed is False
    assert db_session.query(SocialIdea).filter_by(brief_id=brief.id).count() == 6

    # İkinci replay çağrısı
    res2 = persist_social_ideas(
        db_session,
        attempt_id=attempt.id,
        task_id="task-21",
        category_results=results,
    )
    db_session.commit()
    assert res2.already_completed is True
    assert res2.total_ideas == 6
    # Kopya satır oluşmadığını doğrula
    assert db_session.query(SocialIdea).filter_by(brief_id=brief.id).count() == 6


def test_22_completed_replay_db_anomaly_fail_closed(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """22. Completed attempt replay'inde DB anomalisi (stale satır, eksik satır) fail-closed reddedilir."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-22"
    )
    results = _build_valid_category_results(start.plan, attempt.id, targets, kws)

    persist_social_ideas(
        db_session,
        attempt_id=attempt.id,
        task_id="task-22",
        category_results=results,
    )
    db_session.commit()

    # Bir fikri stale yapalım (DB anomalisi)
    idea_row = db_session.query(SocialIdea).filter_by(brief_id=brief.id).first()
    idea_row.is_stale = True
    db_session.commit()

    with pytest.raises(SocialIdeaPersistenceError) as exc:
        persist_social_ideas(
            db_session,
            attempt_id=attempt.id,
            task_id="task-22",
            category_results=results,
        )
    assert exc.value.error_code == "IDEA_PERSISTENCE_INCONSISTENT"


def test_23_caller_rollback_reverts_ideas_and_attempt_status(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """23. Caller rollback yaptığında bütün fikir satırları ve attempt finalizasyonu geri alınır."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-23"
    )
    results = _build_valid_category_results(start.plan, attempt.id, targets, kws)

    res = persist_social_ideas(
        db_session,
        attempt_id=attempt.id,
        task_id="task-23",
        category_results=results,
    )
    assert res.total_ideas == 6

    # Caller rollback yapıyor
    db_session.rollback()

    assert db_session.query(SocialIdea).filter_by(brief_id=brief.id).count() == 0
    db_session.refresh(attempt)
    assert attempt.status == "running"  # completed olmadı


def test_24_service_does_not_call_commit_or_rollback(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """24. persist_social_ideas servisi kendi içinde commit veya rollback çağırmaz."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-24"
    )
    results = _build_valid_category_results(start.plan, attempt.id, targets, kws)

    with patch.object(db_session, "commit") as mock_c, patch.object(db_session, "rollback") as mock_r:
        res = persist_social_ideas(
            db_session,
            attempt_id=attempt.id,
            task_id="task-24",
            category_results=results,
        )
        assert res.total_ideas == 6
        assert mock_c.call_count == 0
        assert mock_r.call_count == 0


def test_25_two_sessions_concurrency_no_duplicate_ideas(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """25. İki oturum eşzamanlı finalizasyon denediğinde kopya fikir oluşmaz; ikinci oturum idempotent döner."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-25"
    )
    results = _build_valid_category_results(start.plan, attempt.id, targets, kws)

    session1 = SessionLocal()
    session2 = SessionLocal()

    t1_entered_and_flushed = threading.Event()
    t1_can_commit = threading.Event()
    t2_started = threading.Event()
    t2_finished = threading.Event()

    t1_result: list[PersistedSocialIdeasResult] = []
    t2_result: list[PersistedSocialIdeasResult] = []
    thread_errors: list[Exception] = []

    def worker_1():
        try:
            res = persist_social_ideas(
                session1,
                attempt_id=attempt.id,
                task_id="task-25",
                category_results=results,
            )
            # flush yapıldı, FOR UPDATE kilitleri tutuluyor, henüz commit edilmedi
            t1_result.append(res)
            t1_entered_and_flushed.set()

            # Thread 2'nin kilidi beklemeye başladığından emin olana kadar bekle
            if not t1_can_commit.wait(timeout=5.0):
                raise TimeoutError("worker_1 commit için beklerken zaman aşımına uğradı.")

            session1.commit()
        except Exception as exc:
            thread_errors.append(exc)
            session1.rollback()
        finally:
            session1.close()

    def worker_2():
        try:
            t2_started.set()
            # session1 kilidi tutarken bu çağrı FOR UPDATE satır kilidinde BLOKE OLMALIDIR
            res = persist_social_ideas(
                session2,
                attempt_id=attempt.id,
                task_id="task-25",
                category_results=results,
            )
            session2.commit()
            t2_result.append(res)
        except Exception as exc:
            thread_errors.append(exc)
            session2.rollback()
        finally:
            t2_finished.set()
            session2.close()

    th1 = threading.Thread(target=worker_1, daemon=True)
    th2 = threading.Thread(target=worker_2, daemon=True)

    th1.start()
    assert t1_entered_and_flushed.wait(timeout=5.0), "Thread 1 lock alamadı."

    # Thread 1 kilidi tutuyor; şimdi Thread 2'yi başlat
    th2.start()
    assert t2_started.wait(timeout=2.0), "Thread 2 başlayamadı."

    # Thread 2'nin lock nedeniyle beklediğini doğrula (ör. 0.3 saniye sonra hala bitmemiş olmalı)
    finished_early = t2_finished.wait(timeout=0.3)
    assert not finished_early, "Thread 2 Thread 1 commit etmeden önce bitti; kilit çalışmadı!"

    # Artık Thread 1'in commit etmesine izin ver
    t1_can_commit.set()

    th1.join(timeout=5.0)
    th2.join(timeout=5.0)

    assert len(thread_errors) == 0, f"Thread hataları: {thread_errors}"
    assert len(t1_result) == 1
    assert len(t2_result) == 1

    res1 = t1_result[0]
    res2 = t2_result[0]

    assert res1.already_completed is False
    assert res2.already_completed is True
    assert res1.total_ideas == 6
    assert res2.total_ideas == 6

    # DB'de toplam satır sayısı tam olarak plan.total_requested olmalı (asla 12 olmamalı)
    db_session.expire_all()
    total_db_ideas = db_session.query(SocialIdea).filter_by(brief_id=brief.id).count()
    assert total_db_ideas == 6


def test_26_sql_lock_order_is_canonical(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """26. Kilit sırası kesinlikle ScoringRun -> SocialBrief -> SocialGenerationAttempt'tir."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-26"
    )
    results = _build_valid_category_results(start.plan, attempt.id, targets, kws)

    captured_locks: list[str] = []

    def _query_catcher(conn, cursor, statement, parameters, context, executemany):
        stmt_lower = statement.lower()
        if "for update" in stmt_lower:
            if "scoring_runs" in stmt_lower:
                captured_locks.append("ScoringRun")
            elif "social_briefs" in stmt_lower:
                captured_locks.append("SocialBrief")
            elif "social_generation_attempts" in stmt_lower:
                captured_locks.append("SocialGenerationAttempt")

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", _query_catcher)
    try:
        persist_social_ideas(
            db_session,
            attempt_id=attempt.id,
            task_id="task-26",
            category_results=results,
        )
        db_session.commit()
    finally:
        event.remove(engine, "before_cursor_execute", _query_catcher)

    sr_idx = captured_locks.index("ScoringRun")
    sb_idx = captured_locks.index("SocialBrief")
    att_idx = captured_locks.index("SocialGenerationAttempt")

    assert sr_idx < sb_idx < att_idx


def test_27_zero_ai_calls_and_temperature_preserved():
    """27. Modül AI servisi bağımlılığı taşımaz ve sıcaklık ayarları korunur."""
    import inspect
    import app.core.social.idea_persistence as mod
    import app.generators.social.brief_idea_generator as gen_mod

    src = inspect.getsource(mod)
    assert "AIService" not in src
    assert "complete_json" not in src

    gen_src = inspect.getsource(gen_mod)
    assert "temperature=None" in gen_src


def test_28_coverage_request_missing_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """28. coverage.request eksik olduğunda IDEA_PERSISTENCE_INCONSISTENT ile reddedilir."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-28"
    )
    results = _build_valid_category_results(start.plan, attempt.id, targets, kws)

    cov = copy.deepcopy(attempt.coverage)
    del cov["request"]
    attempt.coverage = cov
    db_session.commit()

    with pytest.raises(SocialIdeaPersistenceError) as exc:
        persist_social_ideas(
            db_session,
            attempt_id=attempt.id,
            task_id="task-28",
            category_results=results,
        )
    assert exc.value.error_code == "IDEA_PERSISTENCE_INCONSISTENT"


def test_29_request_category_ids_order_mismatch_with_plan_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """29. request.category_ids ile plan kategori sırası uyuşmadığında IDEA_PERSISTENCE_INCONSISTENT üretilir."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-29", num_categories=2
    )
    results = _build_valid_category_results(start.plan, attempt.id, targets, kws)

    cov = copy.deepcopy(attempt.coverage)
    cov["request"]["category_ids"] = list(reversed(cov["request"]["category_ids"]))
    attempt.coverage = cov
    db_session.commit()

    with pytest.raises(SocialIdeaPersistenceError) as exc:
        persist_social_ideas(
            db_session,
            attempt_id=attempt.id,
            task_id="task-29",
            category_results=results,
        )
    assert exc.value.error_code == "IDEA_PERSISTENCE_INCONSISTENT"


def test_30_request_ideas_per_category_mismatch_with_plan_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """30. request.ideas_per_category ile plan uyuşmadığında IDEA_PERSISTENCE_INCONSISTENT üretilir."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-30", ideas_per_category=3
    )
    results = _build_valid_category_results(start.plan, attempt.id, targets, kws)

    cov = copy.deepcopy(attempt.coverage)
    cov["request"]["ideas_per_category"] = 1  # 3 yerine 1
    attempt.coverage = cov
    db_session.commit()

    with pytest.raises(SocialIdeaPersistenceError) as exc:
        persist_social_ideas(
            db_session,
            attempt_id=attempt.id,
            task_id="task-30",
            category_results=results,
        )
    assert exc.value.error_code == "IDEA_PERSISTENCE_INCONSISTENT"


def test_31_non_canonical_plan_with_matching_total_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """31. Toplamları tutsa dahi kanonik olmayan plan snapshot'ı IDEA_PERSISTENCE_INCONSISTENT ile reddedilir."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-31", num_categories=2, ideas_per_category=3
    )
    results = _build_valid_category_results(start.plan, attempt.id, targets, kws)

    cov = copy.deepcopy(attempt.coverage)
    # Kategori 0 ve 1 kotalarını kaydır (toplam 6 değişmez ama kanonik dağılım bozulur)
    cov["plan"]["categories"][0]["requested_count"] += 1
    cov["plan"]["categories"][0]["targets"][0]["requested_count"] += 1
    cov["plan"]["categories"][1]["requested_count"] -= 1
    cov["plan"]["categories"][1]["targets"][0]["requested_count"] -= 1
    attempt.coverage = cov
    db_session.commit()

    with pytest.raises(SocialIdeaPersistenceError) as exc:
        persist_social_ideas(
            db_session,
            attempt_id=attempt.id,
            task_id="task-31",
            category_results=results,
        )
    assert exc.value.error_code == "IDEA_PERSISTENCE_INCONSISTENT"


def test_32_running_attempt_with_prefilled_generated_block_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """32. Running attempt için generated bloğu başlangıç durumunda (0, []) değilse reddedilir."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-32"
    )
    results = _build_valid_category_results(start.plan, attempt.id, targets, kws)

    cov = copy.deepcopy(attempt.coverage)
    cov["generated"]["total_accepted"] = 1
    attempt.coverage = cov
    db_session.commit()

    with pytest.raises(SocialIdeaPersistenceError) as exc:
        persist_social_ideas(
            db_session,
            attempt_id=attempt.id,
            task_id="task-32",
            category_results=results,
        )
    assert exc.value.error_code == "IDEA_PERSISTENCE_INCONSISTENT"


def test_33_completed_replay_with_invalid_generated_target_ids_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """33. Completed replay yolunda generated.target_ids eksik/yanlışsa IDEA_PERSISTENCE_INCONSISTENT döner."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-33"
    )
    results = _build_valid_category_results(start.plan, attempt.id, targets, kws)

    # Başarıyla yaz ve completed yap
    persist_social_ideas(
        db_session,
        attempt_id=attempt.id,
        task_id="task-33",
        category_results=results,
    )
    db_session.commit()

    # Replay öncesi generated.target_ids listesini bozalım
    cov = copy.deepcopy(attempt.coverage)
    cov["generated"]["target_ids"] = [targets[1].id, targets[0].id]  # Yanlış sıra
    attempt.coverage = cov
    db_session.commit()

    with pytest.raises(SocialIdeaPersistenceError) as exc:
        persist_social_ideas(
            db_session,
            attempt_id=attempt.id,
            task_id="task-33",
            category_results=results,
        )
    assert exc.value.error_code == "IDEA_PERSISTENCE_INCONSISTENT"


def test_34_running_attempt_with_existing_social_ideas_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """34. Running attempt yazımı öncesinde aynı brief altında SocialIdea satırı varsa reddedilir ve mevcut satır korunur."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-34"
    )
    results = _build_valid_category_results(start.plan, attempt.id, targets, kws)

    # Önceden kalmış bir fikir ekleyelim
    pre_existing = SocialIdea(
        category_id=cats[0].id,
        keyword_id=kws[0].id,
        brief_id=brief.id,
        brief_target_id=targets[0].id,
        idea_title="Önceden Kalan Fikir",
        idea_description="Bu fikir veritabanında önceden mevcuttu.",
        target_platform=targets[0].platform,
        content_format=targets[0].content_format,
        trend_alignment=0.5,
        is_stale=False,
        is_selected=False,
        regeneration_count=0,
    )
    db_session.add(pre_existing)
    db_session.commit()
    pre_id = pre_existing.id

    with pytest.raises(SocialIdeaPersistenceError) as exc:
        persist_social_ideas(
            db_session,
            attempt_id=attempt.id,
            task_id="task-34",
            category_results=results,
        )
    assert exc.value.error_code == "IDEA_PERSISTENCE_INCONSISTENT"

    # Önceden kalan satırın silinmediği ve değişmediği doğrulanır
    db_session.expire_all()
    rem = db_session.query(SocialIdea).filter_by(id=pre_id).one()
    assert rem.is_stale is False
    assert rem.idea_title == "Önceden Kalan Fikir"
    assert db_session.query(SocialIdea).filter_by(brief_id=brief.id).count() == 1


def test_35_numeric_overflow_trend_alignment_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """35. trend_alignment için taşma değerleri (±10**10000) ve NaN/Inf temiz hata üretir."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-35"
    )
    results = _build_valid_category_results(start.plan, attempt.id, targets, kws)

    for overflow_val in [10**10000, -(10**10000), float("nan"), float("inf"), -float("inf")]:
        cat0_ideas = list(results[0].ideas)
        cat0_ideas[0] = ValidatedSocialIdea(
            target_id=cat0_ideas[0].target_id,
            primary_keyword_id=cat0_ideas[0].primary_keyword_id,
            idea_title=cat0_ideas[0].idea_title,
            idea_description=cat0_ideas[0].idea_description,
            target_platform=cat0_ideas[0].target_platform,
            content_format=cat0_ideas[0].content_format,
            trend_alignment=overflow_val,  # type: ignore[arg-type]
        )
        bad_res = SocialIdeaAIResult(
            attempt_id=attempt.id,
            category_id=results[0].category_id,
            ideas=tuple(cat0_ideas),
            ai_calls_used=1,
        )
        with pytest.raises(SocialIdeaPersistenceError) as exc:
            persist_social_ideas(
                db_session,
                attempt_id=attempt.id,
                task_id="task-35",
                category_results=(bad_res, results[1]),
            )
        assert exc.value.error_code == "IDEA_PERSISTENCE_INVALID_INPUT"
        assert "OverflowError" not in exc.value.message
        assert "10000" not in exc.value.message


def test_36_completed_replay_stale_category_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """36. Completed replay'de kategorilerden biri stale yapılmışsa IDEA_PERSISTENCE_INCONSISTENT fırlatılır."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-36"
    )
    results = _build_valid_category_results(start.plan, attempt.id, targets, kws)

    persist_social_ideas(
        db_session,
        attempt_id=attempt.id,
        task_id="task-36",
        category_results=results,
    )
    db_session.commit()

    # Kategoriyi stale yapalım
    cats[0].is_stale = True
    db_session.commit()

    with pytest.raises(SocialIdeaPersistenceError) as exc:
        persist_social_ideas(
            db_session,
            attempt_id=attempt.id,
            task_id="task-36",
            category_results=results,
        )
    assert exc.value.error_code == "IDEA_PERSISTENCE_INCONSISTENT"


def test_37_completed_replay_wrong_scoring_run_category_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """37. Completed replay'de kategori farklı scoring run'a aitse IDEA_PERSISTENCE_INCONSISTENT fırlatılır."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-37"
    )
    results = _build_valid_category_results(start.plan, attempt.id, targets, kws)

    persist_social_ideas(
        db_session,
        attempt_id=attempt.id,
        task_id="task-37",
        category_results=results,
    )
    db_session.commit()

    other_run = make_scoring_run(brand_profile_id=ws.id)
    cats[0].scoring_run_id = other_run.id
    db_session.commit()

    with pytest.raises(SocialIdeaPersistenceError) as exc:
        persist_social_ideas(
            db_session,
            attempt_id=attempt.id,
            task_id="task-37",
            category_results=results,
        )
    assert exc.value.error_code == "IDEA_PERSISTENCE_INCONSISTENT"


def test_38_completed_replay_whitespace_or_long_title_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """38. Completed replay'de başlıkta boşluk veya uzunluk anomalisi tespit edilirse reddedilir."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-38"
    )
    results = _build_valid_category_results(start.plan, attempt.id, targets, kws)

    persist_social_ideas(
        db_session,
        attempt_id=attempt.id,
        task_id="task-38",
        category_results=results,
    )
    db_session.commit()

    first_idea = db_session.query(SocialIdea).filter_by(brief_id=brief.id).first()
    first_idea.idea_title = " Boşluklu Başlık "
    db_session.commit()

    with pytest.raises(SocialIdeaPersistenceError) as exc:
        persist_social_ideas(
            db_session,
            attempt_id=attempt.id,
            task_id="task-38",
            category_results=results,
        )
    assert exc.value.error_code == "IDEA_PERSISTENCE_INCONSISTENT"


def test_39_completed_replay_invalid_description_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """39. Completed replay'de açıklama metni sınır dışı veya boşluklu ise IDEA_PERSISTENCE_INCONSISTENT döner."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-39"
    )
    results = _build_valid_category_results(start.plan, attempt.id, targets, kws)

    persist_social_ideas(
        db_session,
        attempt_id=attempt.id,
        task_id="task-39",
        category_results=results,
    )
    db_session.commit()

    first_idea = db_session.query(SocialIdea).filter_by(brief_id=brief.id).first()
    first_idea.idea_description = " " * 10
    db_session.commit()

    with pytest.raises(SocialIdeaPersistenceError) as exc:
        persist_social_ideas(
            db_session,
            attempt_id=attempt.id,
            task_id="task-39",
            category_results=results,
        )
    assert exc.value.error_code == "IDEA_PERSISTENCE_INCONSISTENT"


def test_40_completed_replay_nan_or_infinity_score_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """40. Completed replay'de trend_alignment NaN veya sonsuz ise IDEA_PERSISTENCE_INCONSISTENT döner."""
    ws, run, brief, cats, targets, kws, start, attempt = _setup_ready_ideas_attempt(
        db_session, make_workspace, make_scoring_run, make_keyword, task_id="task-40"
    )
    results = _build_valid_category_results(start.plan, attempt.id, targets, kws)

    persist_social_ideas(
        db_session,
        attempt_id=attempt.id,
        task_id="task-40",
        category_results=results,
    )
    db_session.commit()

    first_idea = db_session.query(SocialIdea).filter_by(brief_id=brief.id).first()
    first_idea.trend_alignment = float("nan")
    db_session.commit()

    with pytest.raises(SocialIdeaPersistenceError) as exc:
        persist_social_ideas(
            db_session,
            attempt_id=attempt.id,
            task_id="task-40",
            category_results=results,
        )
    assert exc.value.error_code == "IDEA_PERSISTENCE_INCONSISTENT"

