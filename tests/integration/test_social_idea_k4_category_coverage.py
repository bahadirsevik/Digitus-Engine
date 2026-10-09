# -*- coding: utf-8 -*-
"""K4 kategori kapsaması entegrasyon testleri (gerçek test DB).

K4: her seçilen kategori >= 1 fikir VE her hedef >= 1 fikir.
1. İlk deneme: hedefler dolu ama bir kategori 0 fikir -> tamamlama turu o kategoriden
   1 fikir ister -> completed; tamamlama da başarısızsa partial + category_unfilled ve
   tekrar dene NOT_NEEDED değildir.
2. Tarihsel partial deneme (DB doğrudan kurulur; eski ai_failed uyarıları, tüm hedefler
   dolu, bir kategori boş, başka fikirde içerik var) retry ile onarılır: yalnız boş
   kategori istenir, 1 fikir eklenir, mevcut satırlar bayt-aynı kalır; ikinci retry 409.
3. Çağrı tavanları: ilk deneme en kötü durumda 24'ü, retry 12'yi aşmaz.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.core.social.idea_flow import begin_social_idea_generation
from app.core.social.idea_orchestration import (
    IDEA_ATTEMPT_MAX_AI_CALLS,
    run_social_idea_generation,
)
from app.core.social.idea_planner import build_social_idea_generation_plan
from app.core.social.idea_read import load_social_idea_result
from app.core.social.idea_retry_flow import begin_social_idea_retry
from app.core.social.idea_retry_orchestration import (
    IDEA_RETRY_ATTEMPT_MAX_AI_CALLS,
    run_social_idea_retry_generation,
)
from app.core.social.idea_retry_read import load_social_idea_retry_result
from app.database.connection import SessionLocal
from app.database.models import (
    ChannelPool,
    SocialBrief,
    SocialBriefKeyword,
    SocialBriefTarget,
    SocialCategory,
    SocialContent,
    SocialGenerationAttempt,
    SocialIdea,
)
from app.schemas.social_brief import (
    SocialBriefIdeasGenerateRequest,
    SocialBriefIdeasRetryRequest,
)

T0 = datetime(2026, 9, 28, 12, 0, 0, tzinfo=timezone.utc)

_SIX_TARGETS = [
    ("instagram", "post"),
    ("instagram", "carousel"),
    ("twitter", "thread"),
    ("twitter", "post"),
    ("linkedin", "post"),
    ("linkedin", "carousel"),
]


@pytest.fixture
def enable_flag(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_SOCIAL_BRIEF_FLOW", True)
    yield


# ==================== SAHTE AI ====================


def _prompt_input_json(prompt: str) -> dict:
    start_tag = "<INPUT_JSON>\n"
    end_tag = "\n</INPUT_JSON>"
    start = prompt.find(start_tag) + len(start_tag)
    end = prompt.find(end_tag)
    return json.loads(prompt[start:end])


def _valid_ideas_for(data: dict) -> list[dict]:
    kw_id = data["keywords"][0]["id"]
    ideas = []
    for spec in data["target_specs"]:
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


class _ScriptedIdeaAI:
    """builder(data, call_no) -> list[dict] | Exception; ağ çağrısı yapmaz."""

    def __init__(self, builder) -> None:
        self.builder = builder
        self.call_count = 0
        self.requests: list[dict] = []

    def for_stage(self, stage: str, **overrides):
        return self

    def complete_json(self, prompt: str, **kwargs) -> str:
        self.call_count += 1
        data = _prompt_input_json(prompt)
        self.requests.append(data)
        out = self.builder(data, self.call_count)
        if isinstance(out, Exception):
            raise out
        return json.dumps({"ideas": out}, ensure_ascii=False)


# ==================== ORTAM ====================


def _setup_brief(
    db_session: Session,
    make_workspace,
    make_scoring_run,
    make_keyword,
    *,
    num_categories: int,
    targets_data: list[tuple[str, str]],
):
    ws = make_workspace(name="K4 Kategori Marka", status="confirmed")
    run = make_scoring_run(
        brand_profile_id=ws.id,
        status="completed",
        algorithm_version="v3",
        channel_pool_policy_version=ws.policy_version or 1,
        relevance_anchor_version=ws.anchor_version or 1,
        channel_assignment_version=1,
        skip_relevance=True,
    )
    kws = []
    for i in range(3):
        kw = make_keyword(text_value=f"k4 kategori kw {i + 1}", brand_profile_id=ws.id)
        db_session.add(ChannelPool(
            scoring_run_id=run.id, keyword_id=kw.id, channel="SOCIAL",
            final_rank=i + 1, relevance_score=0.9, adjusted_score=20.0 - i,
        ))
        kws.append(kw)
    db_session.flush()

    brief = SocialBrief(
        scoring_run_id=run.id,
        brand_name_snapshot="K4 Marka",
        brand_context_snapshot="K4 bağlam",
        channel_assignment_version=1,
        format_matrix_version="v1",
        is_stale=False,
        locked_at=T0,
    )
    db_session.add(brief)
    db_session.flush()
    for pos, kw in enumerate(kws):
        db_session.add(SocialBriefKeyword(
            brief_id=brief.id, keyword_id=kw.id, keyword_snapshot=kw.keyword, position=pos,
        ))
    targets = []
    for plat, fmt in targets_data:
        t = SocialBriefTarget(brief_id=brief.id, platform=plat, content_format=fmt)
        db_session.add(t)
        targets.append(t)
    db_session.add(SocialGenerationAttempt(
        brief_id=brief.id, stage="categories", idempotency_key=f"cat-{uuid.uuid4()}",
        status="completed", heartbeat_at=T0, created_at=T0,
    ))
    cats = []
    for i in range(num_categories):
        cat = SocialCategory(
            scoring_run_id=run.id, brief_id=brief.id, category_name=f"Kategori {i + 1}",
            category_type="educational", description=f"Açıklama {i + 1} eğitici yön.",
            is_stale=False, relevance_score=0.9, suggested_keyword_ids=[kws[0].id],
        )
        db_session.add(cat)
        cats.append(cat)
    db_session.commit()
    for obj in cats + targets:
        db_session.refresh(obj)
    return ws, run, brief, cats, targets, kws


def _start_ideas_attempt(db_session, ws, brief, cats, ideas_per_category: int):
    start = begin_social_idea_generation(
        db_session,
        brief_id=brief.id,
        brand_profile_id=ws.id,
        request=SocialBriefIdeasGenerateRequest(
            idempotency_key=f"ideas-{uuid.uuid4()}",
            category_ids=[c.id for c in cats],
            ideas_per_category=ideas_per_category,
        ),
    )
    db_session.commit()
    return start.attempt_id


def _table_rows(db_session: Session, model, ids) -> list[tuple]:
    """Verilen satırların TÜM kolonlarını (tanım sırasıyla) id sırasında döner."""
    table = model.__table__
    rows = db_session.execute(
        select(table).where(table.c.id.in_(list(ids))).order_by(table.c.id)
    ).all()
    return [tuple(r) for r in rows]


# ==================== 1. İLK DENEME ====================


def test_01_initial_attempt_empty_category_gets_topup_and_completes(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """Hedef dolu, Kategori 2 ilk turda 0 geçerli fikir -> tamamlama turu Kategori 2'den
    1 fikir ister -> completed (K4)."""
    ws, run, brief, cats, targets, kws = _setup_brief(
        db_session, make_workspace, make_scoring_run, make_keyword,
        num_categories=2, targets_data=[("instagram", "post")],
    )
    attempt_id = _start_ideas_attempt(db_session, ws, brief, cats, ideas_per_category=2)
    seen: dict[str, int] = {}

    def builder(data, call_no):
        name = data["category"]["name"]
        seen[name] = seen.get(name, 0) + 1
        # Kategori 2'nin ilk turu (ana + düzeltme tekrarı) boş döner, tamamlama başarılı
        if name == "Kategori 2" and seen[name] <= 2:
            return []
        return _valid_ideas_for(data)

    ai = _ScriptedIdeaAI(builder)
    result = run_social_idea_generation(
        session_factory=SessionLocal, ai_service=ai, attempt_id=attempt_id,
        task_id="k4-01", now_provider=lambda: T0,
    )

    # 1 (Kat.1) + 2 (Kat.2 boş + düzeltme) + 1 (tamamlama, Kat.2) = 4
    assert ai.call_count == 4
    topup = ai.requests[-1]
    assert topup["category"]["name"] == "Kategori 2"
    assert [(s["target_id"], s["requested_count"]) for s in topup["target_specs"]] == [
        (targets[0].id, 1)
    ]
    assert result.status == "completed"

    db_session.expire_all()
    ideas = db_session.query(SocialIdea).filter_by(brief_id=brief.id).all()
    assert {i.category_id for i in ideas} == {cats[0].id, cats[1].id}
    att = db_session.query(SocialGenerationAttempt).filter_by(id=attempt_id).one()
    assert att.status == "completed"
    assert att.warnings == []
    metrics = att.coverage["metrics"]
    assert metrics["topup_requests"] == 1
    assert metrics["category_topup_requests"] == 1
    assert metrics["category_unfilled"] == 0
    assert metrics["target_unfilled"] == 0


def test_02_initial_topup_fails_partial_category_unfilled_and_retry_is_needed(
    client, db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """Tamamlama da başarısız -> partial + category_unfilled; tekrar dene 409-not-needed
    DEĞİL: retry yalnız boş kategoriyi ister, onarır, ikinci retry 409 NOT_NEEDED."""
    ws, run, brief, cats, targets, kws = _setup_brief(
        db_session, make_workspace, make_scoring_run, make_keyword,
        num_categories=2, targets_data=[("instagram", "post")],
    )
    attempt_id = _start_ideas_attempt(db_session, ws, brief, cats, ideas_per_category=2)

    def builder(data, call_no):
        if data["category"]["name"] == "Kategori 2":
            return []
        return _valid_ideas_for(data)

    ai = _ScriptedIdeaAI(builder)
    result = run_social_idea_generation(
        session_factory=SessionLocal, ai_service=ai, attempt_id=attempt_id,
        task_id="k4-02", now_provider=lambda: T0,
    )
    assert ai.call_count == 5  # 1 + 2 + tamamlama 2 (boş + düzeltme)
    assert result.status == "partial"

    db_session.expire_all()
    att = db_session.query(SocialGenerationAttempt).filter_by(id=attempt_id).one()
    assert att.reason_code == "category_unfilled"
    assert att.warnings == [
        {"target_id": targets[0].id, "category_id": cats[1].id, "reason_code": "category_unfilled"}
    ]
    assert att.coverage["metrics"]["category_unfilled"] == 1
    assert att.coverage["metrics"]["target_unfilled"] == 0

    read = load_social_idea_result(
        db_session, brief_id=brief.id, attempt_id=attempt_id, brand_profile_id=ws.id
    )
    assert read.attempt_status == "partial"
    assert [(w.target_id, w.category_id, w.reason_code) for w in read.warnings] == [
        (targets[0].id, cats[1].id, "category_unfilled")
    ]

    # Tekrar dene (API): NOT_NEEDED değil -> 202
    url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/retry?brand_profile_id={ws.id}"
    with patch(
        "app.api.v1.generation.social_brief_ideas_retry_task.apply_async",
        return_value=MagicMock(id=str(uuid.uuid4())),
    ) as mock_apply:
        resp = client.post(url, json={"idempotency_key": "k4-02-r1", "source_attempt_id": attempt_id})
    assert resp.status_code == 202, resp.text
    assert mock_apply.call_count == 1
    retry_id = resp.json()["attempt_id"]

    db_session.expire_all()
    retry_att = db_session.query(SocialGenerationAttempt).filter_by(id=retry_id).one()
    assert retry_att.requested_target_ids == [targets[0].id]
    assert retry_att.coverage["plan"]["missing_target_ids"] == []
    assert retry_att.coverage["plan"]["empty_category_ids"] == [cats[1].id]
    assert retry_att.coverage["plan"]["assignments"] == [
        {"category_id": cats[1].id, "target_id": targets[0].id, "requested_count": 1}
    ]

    retry_ai = _ScriptedIdeaAI(lambda data, n: _valid_ideas_for(data))
    retry_res = run_social_idea_retry_generation(
        session_factory=SessionLocal, ai_service=retry_ai, attempt_id=retry_id,
        task_id="k4-02-retry", now_provider=lambda: T0,
    )
    assert retry_ai.call_count == 1
    assert retry_ai.requests[0]["category"]["name"] == "Kategori 2"
    assert retry_res.status == "completed"
    assert retry_res.unfilled_category_ids == ()

    db_session.expire_all()
    retry_read = load_social_idea_retry_result(
        db_session, brief_id=brief.id, attempt_id=retry_id, brand_profile_id=ws.id
    )
    assert retry_read.attempt_status == "completed"
    assert {i.category_id for i in retry_read.ideas} == {cats[0].id, cats[1].id}

    with patch(
        "app.api.v1.generation.social_brief_ideas_retry_task.apply_async"
    ) as mock_apply2:
        resp2 = client.post(url, json={"idempotency_key": "k4-02-r2", "source_attempt_id": attempt_id})
    assert resp2.status_code == 409
    assert resp2.json()["detail"]["code"] == "IDEA_RETRY_NOT_NEEDED"
    mock_apply2.assert_not_called()


# ==================== 2. TARİHSEL PARTIAL ONARIMI ====================


def _build_historical_partial(db_session, ws, brief, cats, targets, kws):
    """Değişiklikten ÖNCE yazılmış gibi partial kaynak deneme: eski ai_failed uyarıları,
    tüm hedefler dolu, son kategori boş; boş olmayan bir fikrin içeriği var."""
    plan = build_social_idea_generation_plan(
        selected_category_ids=tuple(c.id for c in cats),
        target_ids=tuple(t.id for t in targets),
        ideas_per_category=2,
    )
    empty_cat = cats[-1]
    planned = {
        cp.category_id: [tq.target_id for tq in cp.target_quotas if tq.requested_count > 0]
        for cp in plan.category_plans
    }
    ideas: list[SocialIdea] = []
    for t in targets:
        cat_id = next(
            cid for cid in (c.id for c in cats) if cid != empty_cat.id and t.id in planned[cid]
        )
        ideas.append(SocialIdea(
            category_id=cat_id, keyword_id=kws[0].id, brief_id=brief.id,
            brief_target_id=t.id, idea_title=f"Tarihsel Fikir {t.id}",
            idea_description="Tarihsel partial denemeden kalan fikir.",
            target_platform=t.platform, content_format=t.content_format,
            trend_alignment=0.6, is_stale=False, is_selected=True,
            regeneration_count=0, created_at=T0,
        ))
    # Diğer dolu kategorilerde de en az bir fikir olsun (yalnız son kategori boş)
    covered = {i.category_id for i in ideas}
    for c in cats[:-1]:
        if c.id not in covered:
            t_id = planned[c.id][0]
            t = next(x for x in targets if x.id == t_id)
            ideas.append(SocialIdea(
                category_id=c.id, keyword_id=kws[0].id, brief_id=brief.id,
                brief_target_id=t.id, idea_title=f"Tarihsel Ek {c.id}",
                idea_description="Tarihsel ek fikir.", target_platform=t.platform,
                content_format=t.content_format, trend_alignment=0.6, is_stale=False,
                is_selected=False, regeneration_count=0, created_at=T0,
            ))

    source = SocialGenerationAttempt(
        brief_id=brief.id,
        stage="ideas",
        idempotency_key="historical-ideas-1",
        status="partial",
        reason_code="ai_failed",
        error_message="Bazı hedefler veya kategoriler için geçerli fikir üretilemedi.",
        requested_target_ids=[t.id for t in targets],
        coverage={
            "schema_version": "ideas_plan_v1",
            "request": {"category_ids": [c.id for c in cats], "ideas_per_category": 2},
            "plan": {
                "total_requested": plan.total_requested,
                "categories": [
                    {
                        "category_id": cp.category_id,
                        "requested_count": cp.requested_count,
                        "targets": [
                            {"target_id": tq.target_id, "requested_count": tq.requested_count}
                            for tq in cp.target_quotas
                            if tq.requested_count > 0
                        ],
                    }
                    for cp in plan.category_plans
                ],
            },
            "generated": {"total_accepted": len(ideas), "target_ids": [t.id for t in targets]},
        },
        warnings=[
            {"target_id": tid, "category_id": empty_cat.id, "reason_code": "ai_failed"}
            for tid in planned[empty_cat.id]
        ],
        heartbeat_at=T0,
        lease_expires_at=None,
        completed_at=T0 + timedelta(seconds=60),
    )
    db_session.add(source)
    db_session.add_all(ideas)
    db_session.flush()
    content = SocialContent(
        idea_id=ideas[0].id,
        brief_id=brief.id,
        is_stale=False,
        caption="Mevcut içerik metni.",
        hooks=["kanca"],
        hashtags=["#etiket"],
        format_payload={"caption": "Mevcut içerik metni."},
        regeneration_count=0,
    )
    db_session.add(content)
    db_session.commit()
    return source, ideas, content, empty_cat, planned


def test_03_retry_repairs_historical_partial_insert_only(
    client, db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    ws, run, brief, cats, targets, kws = _setup_brief(
        db_session, make_workspace, make_scoring_run, make_keyword,
        num_categories=3, targets_data=[("instagram", "post"), ("twitter", "thread")],
    )
    source, ideas, content, empty_cat, planned = _build_historical_partial(
        db_session, ws, brief, cats, targets, kws
    )
    idea_ids = [i.id for i in ideas]

    # Mevcut satırların tüm kolonları (INSERT-ONLY kanıtı için)
    before_ideas = _table_rows(db_session, SocialIdea, idea_ids)
    before_contents = _table_rows(db_session, SocialContent, [content.id])
    before_source = _table_rows(db_session, SocialGenerationAttempt, [source.id])
    assert len(before_ideas) == len(idea_ids)

    url = f"/api/v1/generation/social/briefs/{brief.id}/ideas/retry?brand_profile_id={ws.id}"
    with patch(
        "app.api.v1.generation.social_brief_ideas_retry_task.apply_async",
        return_value=MagicMock(id=str(uuid.uuid4())),
    ):
        resp = client.post(url, json={"idempotency_key": "hist-r1", "source_attempt_id": source.id})
    assert resp.status_code == 202, resp.text
    retry_id = resp.json()["attempt_id"]

    db_session.expire_all()
    retry_att = db_session.query(SocialGenerationAttempt).filter_by(id=retry_id).one()
    first_target = planned[empty_cat.id][0]
    assert retry_att.coverage["plan"]["missing_target_ids"] == []
    assert retry_att.coverage["plan"]["empty_category_ids"] == [empty_cat.id]
    assert retry_att.coverage["plan"]["assignments"] == [
        {"category_id": empty_cat.id, "target_id": first_target, "requested_count": 1}
    ]

    retry_ai = _ScriptedIdeaAI(lambda data, n: _valid_ideas_for(data))
    res = run_social_idea_retry_generation(
        session_factory=SessionLocal, ai_service=retry_ai, attempt_id=retry_id,
        task_id="hist-retry-1", now_provider=lambda: T0,
    )
    # Yalnız boş kategori, tek istek -> tek çağrı yolu
    assert retry_ai.call_count == 1
    assert retry_ai.requests[0]["category"]["name"] == empty_cat.category_name
    assert [s["target_id"] for s in retry_ai.requests[0]["target_specs"]] == [first_target]
    assert res.status == "completed"
    assert res.newly_persisted_count == 1

    db_session.expire_all()
    all_ideas = db_session.query(SocialIdea).filter_by(brief_id=brief.id).all()
    new_ideas = [i for i in all_ideas if i.id not in set(idea_ids)]
    assert len(new_ideas) == 1
    assert (new_ideas[0].category_id, new_ideas[0].brief_target_id) == (empty_cat.id, first_target)

    # INSERT-ONLY: mevcut fikir, içerik ve kaynak deneme satırları bayt-aynı
    assert _table_rows(db_session, SocialIdea, idea_ids) == before_ideas
    assert _table_rows(db_session, SocialContent, [content.id]) == before_contents
    assert _table_rows(db_session, SocialGenerationAttempt, [source.id]) == before_source
    assert db_session.query(SocialContent).filter_by(brief_id=brief.id).count() == 1

    # Brief tam kapsandı: her hedef ve her kategori >= 1
    assert {i.brief_target_id for i in all_ideas} == {t.id for t in targets}
    assert {i.category_id for i in all_ideas} == {c.id for c in cats}

    # Okuma yolları: retry completed, tarihsel kaynak partial okunabilir kalır
    retry_read = load_social_idea_retry_result(
        db_session, brief_id=brief.id, attempt_id=retry_id, brand_profile_id=ws.id
    )
    assert retry_read.attempt_status == "completed"
    assert retry_read.warnings == ()
    src_read = load_social_idea_result(
        db_session, brief_id=brief.id, attempt_id=source.id, brand_profile_id=ws.id
    )
    assert src_read.attempt_status == "partial"
    assert {w.reason_code for w in src_read.warnings} == {"ai_failed"}

    # İkinci retry -> 409 IDEA_RETRY_NOT_NEEDED
    with patch("app.api.v1.generation.social_brief_ideas_retry_task.apply_async") as mock2:
        resp2 = client.post(url, json={"idempotency_key": "hist-r2", "source_attempt_id": source.id})
    assert resp2.status_code == 409
    assert resp2.json()["detail"]["code"] == "IDEA_RETRY_NOT_NEEDED"
    mock2.assert_not_called()


def test_04_retry_category_repair_partial_failure_keeps_category_unfilled(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """Boş kategori onarımı (tek istek) başarısız -> retry failed (sahte yedek yok, 2 çağrı);
    sonraki retry aynı boş kategoriyi DB durumundan yeniden planlar."""
    ws, run, brief, cats, targets, kws = _setup_brief(
        db_session, make_workspace, make_scoring_run, make_keyword,
        num_categories=3, targets_data=[("instagram", "post"), ("twitter", "thread")],
    )
    source, ideas, content, empty_cat, planned = _build_historical_partial(
        db_session, ws, brief, cats, targets, kws
    )
    start = begin_social_idea_retry(
        db_session, brief_id=brief.id, brand_profile_id=ws.id,
        request=SocialBriefIdeasRetryRequest(idempotency_key="h4-r1", source_attempt_id=source.id),
        now=T0,
    )
    db_session.commit()

    fail_ai = _ScriptedIdeaAI(lambda data, n: [])
    with pytest.raises(Exception):
        run_social_idea_retry_generation(
            session_factory=SessionLocal, ai_service=fail_ai, attempt_id=start.attempt_id,
            task_id="h4-retry-1", now_provider=lambda: T0,
        )
    assert fail_ai.call_count == 2  # tek istek: ana + düzeltme tekrarı
    db_session.expire_all()
    att = db_session.query(SocialGenerationAttempt).filter_by(id=start.attempt_id).one()
    assert att.status == "failed"

    # Başarısız retry sonrası aynı boş kategori yeniden planlanır
    start2 = begin_social_idea_retry(
        db_session, brief_id=brief.id, brand_profile_id=ws.id,
        request=SocialBriefIdeasRetryRequest(idempotency_key="h4-r2", source_attempt_id=source.id),
        now=T0,
    )
    db_session.commit()
    assert start2.plan.empty_category_ids == (empty_cat.id,)
    assert start2.missing_target_ids == ()


# ==================== 3. ÇAĞRI TAVANLARI ====================


def test_05_worst_case_initial_attempt_never_exceeds_24_calls(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """6 kategori, 2 hedef, her çıktı boş: ilk tur 12 + tamamlama (eksik hedefler + 6 boş
    kategori, kategori başına tek istek) 12 = 24 = tavan; tavan artmadı."""
    assert IDEA_ATTEMPT_MAX_AI_CALLS == 24
    ws, run, brief, cats, targets, kws = _setup_brief(
        db_session, make_workspace, make_scoring_run, make_keyword,
        num_categories=6, targets_data=_SIX_TARGETS[:2],
    )
    attempt_id = _start_ideas_attempt(db_session, ws, brief, cats, ideas_per_category=1)
    ai = _ScriptedIdeaAI(lambda data, n: [])
    with pytest.raises(Exception):
        run_social_idea_generation(
            session_factory=SessionLocal, ai_service=ai, attempt_id=attempt_id,
            task_id="k4-05", now_provider=lambda: T0,
        )
    assert ai.call_count == 24
    assert ai.call_count <= IDEA_ATTEMPT_MAX_AI_CALLS
    # Tamamlama turunda her kategori tam bir istek aldı
    topup_names = [r["category"]["name"] for r in ai.requests[12:]]
    assert sorted(set(topup_names)) == sorted(c.category_name for c in cats)
    assert len(topup_names) == 12


def test_06_worst_case_retry_never_exceeds_12_calls_and_cap_enforced(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag, monkeypatch
):
    """Retry: 6 boş kategori + eksik hedefler, her çıktı boş -> 6 istek x 2 = 12 çağrı;
    düşük tavanda aşacak istek hiç başlatılmaz."""
    assert IDEA_RETRY_ATTEMPT_MAX_AI_CALLS == 12
    ws, run, brief, cats, targets, kws = _setup_brief(
        db_session, make_workspace, make_scoring_run, make_keyword,
        num_categories=6, targets_data=_SIX_TARGETS[:2],
    )
    attempt_id = _start_ideas_attempt(db_session, ws, brief, cats, ideas_per_category=1)
    with pytest.raises(Exception):
        run_social_idea_generation(
            session_factory=SessionLocal, ai_service=_ScriptedIdeaAI(lambda d, n: []),
            attempt_id=attempt_id, task_id="k4-06", now_provider=lambda: T0,
        )

    start = begin_social_idea_retry(
        db_session, brief_id=brief.id, brand_profile_id=ws.id,
        request=SocialBriefIdeasRetryRequest(idempotency_key="k4-06-r1", source_attempt_id=attempt_id),
        now=T0,
    )
    db_session.commit()
    assert len(start.plan.empty_category_ids) == 6
    assert len({a.category_id for a in start.plan.assignments}) == 6

    retry_ai = _ScriptedIdeaAI(lambda d, n: [])
    with pytest.raises(Exception):
        run_social_idea_retry_generation(
            session_factory=SessionLocal, ai_service=retry_ai, attempt_id=start.attempt_id,
            task_id="k4-06-retry", now_provider=lambda: T0,
        )
    assert retry_ai.call_count == 12
    assert retry_ai.call_count <= IDEA_RETRY_ATTEMPT_MAX_AI_CALLS

    # Düşük tavan: 5 -> yalnız 2 istek (4 çağrı) başlar
    import app.core.social.idea_retry_orchestration as retry_orch

    monkeypatch.setattr(retry_orch, "IDEA_RETRY_ATTEMPT_MAX_AI_CALLS", 5)
    start2 = begin_social_idea_retry(
        db_session, brief_id=brief.id, brand_profile_id=ws.id,
        request=SocialBriefIdeasRetryRequest(idempotency_key="k4-06-r2", source_attempt_id=attempt_id),
        now=T0,
    )
    db_session.commit()
    capped_ai = _ScriptedIdeaAI(lambda d, n: [])
    with pytest.raises(Exception):
        run_social_idea_retry_generation(
            session_factory=SessionLocal, ai_service=capped_ai, attempt_id=start2.attempt_id,
            task_id="k4-06-retry-2", now_provider=lambda: T0,
        )
    assert capped_ai.call_count == 4
    db_session.expire_all()
    assert db_session.query(SocialIdea).filter_by(brief_id=brief.id).count() == 0


def test_07_retry_partial_only_category_unfilled_read_contract(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """Retry'da eksik hedef dolar ama ayrı boş kategori isteği başarısız -> partial,
    reason_code=category_unfilled, tek category_unfilled uyarısı; okuma yolu doğrular."""
    ws, run, brief, cats, targets, kws = _setup_brief(
        db_session, make_workspace, make_scoring_run, make_keyword,
        num_categories=3, targets_data=[("instagram", "post"), ("twitter", "thread")],
    )
    attempt_id = _start_ideas_attempt(db_session, ws, brief, cats, ideas_per_category=2)
    # İlk deneme: yalnız Kategori 1 başarılı ve yalnız ilk hedefi üretir
    first_ai = _ScriptedIdeaAI(
        lambda data, n: [
            i for i in _valid_ideas_for(data) if i["target_id"] == targets[0].id
        ] if data["category"]["name"] == "Kategori 1" else []
    )
    run_social_idea_generation(
        session_factory=SessionLocal, ai_service=first_ai, attempt_id=attempt_id,
        task_id="k4-07", now_provider=lambda: T0,
    )
    db_session.expire_all()
    ideas_before = db_session.query(SocialIdea).filter_by(brief_id=brief.id).all()
    assert {i.category_id for i in ideas_before} == {cats[0].id}

    start = begin_social_idea_retry(
        db_session, brief_id=brief.id, brand_profile_id=ws.id,
        request=SocialBriefIdeasRetryRequest(idempotency_key="k4-07-r1", source_attempt_id=attempt_id),
        now=T0,
    )
    db_session.commit()
    missing = set(start.plan.missing_target_ids)
    by_cat: dict[int, set[int]] = {}
    for a in start.plan.assignments:
        by_cat.setdefault(a.category_id, set()).add(a.target_id)
    # Eksik hedef içermeyen boş kategori isteği başarısız olacak
    fail_cat_id = next(
        cid for cid in start.plan.empty_category_ids if not (by_cat.get(cid, set()) & missing)
    )
    fail_name = next(c.category_name for c in cats if c.id == fail_cat_id)

    retry_ai = _ScriptedIdeaAI(
        lambda data, n: [] if data["category"]["name"] == fail_name else _valid_ideas_for(data)
    )
    res = run_social_idea_retry_generation(
        session_factory=SessionLocal, ai_service=retry_ai, attempt_id=start.attempt_id,
        task_id="k4-07-retry", now_provider=lambda: T0,
    )
    assert res.status == "partial"
    assert res.unfilled_target_ids == ()
    assert res.unfilled_category_ids == (fail_cat_id,)

    db_session.expire_all()
    att = db_session.query(SocialGenerationAttempt).filter_by(id=start.attempt_id).one()
    assert att.reason_code == "category_unfilled"
    assert [(w["category_id"], w["reason_code"]) for w in att.warnings] == [
        (fail_cat_id, "category_unfilled")
    ]
    assert att.coverage["metrics"]["category_unfilled"] == 1
    read = load_social_idea_retry_result(
        db_session, brief_id=brief.id, attempt_id=start.attempt_id, brand_profile_id=ws.id
    )
    assert read.attempt_status == "partial"
    assert read.reason_code == "category_unfilled"
    assert [(w.category_id, w.reason_code) for w in read.warnings] == [
        (fail_cat_id, "category_unfilled")
    ]
    # Sonraki retry yalnız kalan boş kategoriyi planlar
    start2 = begin_social_idea_retry(
        db_session, brief_id=brief.id, brand_profile_id=ws.id,
        request=SocialBriefIdeasRetryRequest(idempotency_key="k4-07-r2", source_attempt_id=attempt_id),
        now=T0,
    )
    db_session.commit()
    assert start2.plan.missing_target_ids == ()
    assert start2.plan.empty_category_ids == (fail_cat_id,)
    assert [a.category_id for a in start2.plan.assignments] == [fail_cat_id]
