"""Sosyal "Revize Et": geri bildirimli revizyon → önizleme → kabul.

Kapsam: kapsam dışı alanların kodla korunması, önizlemede canlı içeriğin değişmemesi,
kabulde aynı satırın güncellenip regeneration_count'un artması (export kimliği değişir),
SocialGenerationAttempt üzerinden idempotency / brief başına tek üretim / lease / geç worker,
eski önerinin yeni içeriği ezememesi, AI hatası, kapsama sınırlı onarım ve workspace izolasyonu.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from app.database.models import (
    ChannelPool,
    ContentRevision,
    SocialBrief,
    SocialBriefKeyword,
    SocialBriefTarget,
    SocialCategory,
    SocialContent,
    SocialGenerationAttempt,
    SocialIdea,
)

T0 = datetime(2026, 11, 1, 9, 0, 0, tzinfo=timezone.utc)
SOURCE_HOOKS = [
    {"text": "Dikkat çeken birinci kanca cümlesi?", "style": "question"},
    {"text": "Hepimizin her gün yaşadığı o an...", "style": "relatable"},
]
SOURCE_CAPTION = "Bu harika bir Instagram gönderisi metnidir."
SOURCE_CTA = "Daha fazlası için profildeki linke tıklayın."
SOURCE_TAGS = ["dijital", "pazarlama", "strateji", "icerik", "sosyalmedya"]
NEW_HOOKS = [{"text": "Bunu daha önce hiç düşündünüz mü?", "style": "curiosity"}]


def _ai_content(**over):
    """Modelin döndürdüğü tam paket: varsayılan olarak HER alan kaynaktan farklı."""
    data = {
        "hooks": NEW_HOOKS,
        "caption": "Tamamen yeni yazılmış samimi bir metin.",
        "cta_text": "Hemen bize yazın.",
        "hashtags": ["yeni", "etiket", "liste", "deneme", "sosyal"],
        "format_payload": None,
        "visual_suggestion": "Sıcak tonlar",
        "video_concept": None,
        "industry_posting_suggestion": None,
        "platform_notes": None,
    }
    data.update(over)
    return data


class RevisionAI:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.prompts = []
        self.collector = None

    def complete_json(self, prompt, **kwargs):
        self.prompts.append(prompt)
        resp = self.responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return json.dumps(resp, ensure_ascii=False)

    def complete(self, prompt, **kwargs):
        return ""


@pytest.fixture(autouse=True)
def enable_flag(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_SOCIAL_BRIEF_FLOW", True)


def _segments(*bounds):
    return {"kind": "video", "segments": [
        {"start_sec": a, "end_sec": b, "scene": f"Sahne {i + 1}", "on_screen_text": f"Yazı {i + 1}",
         "voiceover": " ".join(["kelime"] * ((b - a) * 2))}
        for i, (a, b) in enumerate(bounds)
    ]}


def _source(db_session, make_workspace, make_scoring_run, make_keyword, name="Rev WS", fmt="post"):
    ws = make_workspace(name=name, status="confirmed")
    run = make_scoring_run(
        brand_profile_id=ws.id, status="completed", algorithm_version="v3",
        channel_pool_policy_version=ws.policy_version or 1,
        relevance_anchor_version=ws.anchor_version or 1,
        channel_assignment_version=1, skip_relevance=True,
    )
    kw = make_keyword(text_value=f"{name} kelime", brand_profile_id=ws.id)
    db_session.add(ChannelPool(scoring_run_id=run.id, keyword_id=kw.id, channel="SOCIAL",
                               final_rank=1, relevance_score=0.9, adjusted_score=20.0))
    brief = SocialBrief(scoring_run_id=run.id, brand_name_snapshot=ws.name,
                        brand_context_snapshot="Marka bağlamı", channel_assignment_version=1,
                        format_matrix_version="v1", is_stale=False, locked_at=T0)
    db_session.add(brief)
    db_session.flush()
    db_session.add(SocialBriefKeyword(brief_id=brief.id, keyword_id=kw.id,
                                      keyword_snapshot=kw.keyword, position=0))
    duration = (dict(duration_preset_id="short_31_60", duration_min_sec=31, duration_max_sec=60)
                if fmt == "reels" else {})
    target = SocialBriefTarget(brief_id=brief.id, platform="instagram", content_format=fmt, **duration)
    db_session.add(target)
    db_session.flush()
    cat = SocialCategory(scoring_run_id=run.id, brief_id=brief.id, category_name="Eğitici",
                         category_type="educational", description="Açıklama", is_stale=False,
                         relevance_score=0.9, suggested_keyword_ids=[kw.id])
    db_session.add(cat)
    db_session.flush()
    idea = SocialIdea(brief_id=brief.id, category_id=cat.id, keyword_id=kw.id,
                      brief_target_id=target.id, idea_title="Örnek Fikir",
                      idea_description="Detaylı fikir", target_platform="instagram",
                      content_format=fmt, trend_alignment=0.8, is_stale=False)
    db_session.add(idea)
    db_session.flush()
    # Kaynak satır kanonik sözleşme çıktısından (senaryo/süre türetilmiş alanları birebir)
    from app.core.social.content_contract import validate_social_content_output
    from app.core.social.content_revision import content_raw, target_spec_for

    raw = _ai_content(hooks=SOURCE_HOOKS, caption=SOURCE_CAPTION, cta_text=SOURCE_CTA,
                      hashtags=SOURCE_TAGS, visual_suggestion=None,
                      format_payload=_segments((0, 20), (20, 40)) if fmt == "reels" else None)
    valid = validate_social_content_output(raw, target_spec_for(target))
    content = SocialContent(
        idea_id=idea.id, brief_id=brief.id, **content_raw(valid), scenario=valid.scenario,
        duration_status=valid.duration_status, actual_duration_sec=valid.actual_duration_sec,
        validation_warnings=list(valid.validation_warnings), is_stale=False, regeneration_count=0,
    )
    db_session.add(content)
    db_session.commit()
    return ws, run, brief, content


def _start(client, ws, brief, content, monkeypatch, key="key-0001", scope=("hooks",),
           feedback="Kancaları daha merak uyandırıcı yap"):
    from app.tasks import generation_tasks

    sent = []
    monkeypatch.setattr(generation_tasks.revise_social_content_task, "apply_async",
                        lambda *a, **k: sent.append(k["args"]))
    res = client.post(
        f"/api/v1/generation/social/briefs/{brief.id}/contents/{content.id}/revise",
        params={"brand_profile_id": ws.id},
        json={"feedback": feedback, "scope": list(scope), "idempotency_key": key},
    )
    return res, sent


def _run_worker(monkeypatch, args, ai):
    from app.tasks import generation_tasks

    monkeypatch.setattr("app.generators.ai_service.get_ai_service", lambda *a, **k: ai)
    return generation_tasks.revise_social_content_task.apply(args=args).result


def _list(client, ws, brief):
    res = client.get(f"/api/v1/generation/social/briefs/{brief.id}/revisions",
                     params={"brand_profile_id": ws.id})
    assert res.status_code == 200, res.text
    return res.json()["items"]


def test_full_flow_scope_preview_accept_and_export_identity(
    client, db_session, make_workspace, make_scoring_run, make_keyword, monkeypatch,
):
    from app.exporters.data_collector import collect_exportable_identity

    ws, run, brief, content = _source(db_session, make_workspace, make_scoring_run, make_keyword)
    res, sent = _start(client, ws, brief, content, monkeypatch)
    assert res.status_code == 202, res.text
    [item] = res.json()["items"]
    assert item["status"] == "generating" and item["content_id"] == content.id
    rev_id = item["id"]

    # Aynı gönderim aynı revizyon (dispatch yok); süren üretimde farklı gönderim 409
    again, sent2 = _start(client, ws, brief, content, monkeypatch)
    assert again.status_code == 200 and again.json()["items"][0]["id"] == rev_id and sent2 == []
    clash, _ = _start(client, ws, brief, content, monkeypatch, key="key-0002")
    assert clash.status_code == 409 and clash.json()["detail"]["code"] == "ATTEMPT_CONFLICT"
    mismatch, _ = _start(client, ws, brief, content, monkeypatch, feedback="Başka bir talep yazdım")
    assert mismatch.status_code == 409
    assert mismatch.json()["detail"]["code"] == "REVISION_REQUEST_MISMATCH"

    ai = RevisionAI(_ai_content())  # model HER alanı değiştirdi
    result = _run_worker(monkeypatch, sent[-1], ai)
    assert result["status"] == "ready", result
    assert len(ai.prompts) == 1
    prompt = ai.prompts[0]
    assert "Kancaları daha merak uyandırıcı yap" in prompt and SOURCE_CAPTION in prompt
    assert "BİREBİR aynı döndür" in prompt

    [item] = _list(client, ws, brief)
    assert item["status"] == "ready" and item["changes"] == ["hooks"]
    proposed = item["proposed"]
    assert proposed["hooks"] == NEW_HOOKS
    # Kapsam dışı alanlar kaynaktan: model değiştirse de öneriye girmedi
    assert (proposed["caption"], proposed["cta_text"], proposed["hashtags"]) == (
        SOURCE_CAPTION, SOURCE_CTA, SOURCE_TAGS)
    assert proposed["visual_suggestion"] is None
    db_session.expire_all()
    live = db_session.get(SocialContent, content.id)
    assert live.hooks == SOURCE_HOOKS and live.regeneration_count == 0  # önizleme canlıya yazmadı
    before = collect_exportable_identity(db_session, run.id, "social")

    accept = f"/api/v1/generation/social/revisions/{rev_id}/accept"
    ok = client.post(accept, params={"brand_profile_id": ws.id})
    assert ok.status_code == 200, ok.text
    assert ok.json() == {"status": "accepted", "content_id": content.id, "regeneration_count": 1}
    # Tekrar kabul ikinci güncelleme yapmaz
    assert client.post(accept, params={"brand_profile_id": ws.id}).json()["regeneration_count"] == 1

    db_session.expire_all()
    live = db_session.get(SocialContent, content.id)
    assert live.hooks == NEW_HOOKS and live.caption == SOURCE_CAPTION
    assert live.cta_text == SOURCE_CTA and live.hashtags == SOURCE_TAGS
    assert live.regeneration_count == 1
    assert db_session.query(SocialContent).filter_by(brief_id=brief.id).count() == 1
    rev = db_session.query(ContentRevision).filter_by(channel="social").one()
    assert rev.status == "accepted" and rev.previous_payload["hooks"] == SOURCE_HOOKS
    assert collect_exportable_identity(db_session, run.id, "social") != before
    assert _list(client, ws, brief) == []


def test_stale_proposal_cannot_overwrite_newer_content(
    client, db_session, make_workspace, make_scoring_run, make_keyword, monkeypatch,
):
    ws, run, brief, content = _source(db_session, make_workspace, make_scoring_run, make_keyword)
    _, sent = _start(client, ws, brief, content, monkeypatch)
    _run_worker(monkeypatch, sent[-1], RevisionAI(_ai_content()))
    rev_id = _list(client, ws, brief)[0]["id"]

    # Öneriden sonra içerik başka bir kabulle değişti
    db_session.query(SocialContent).filter_by(id=content.id).update({"regeneration_count": 5})
    db_session.commit()
    res = client.post(f"/api/v1/generation/social/revisions/{rev_id}/accept",
                      params={"brand_profile_id": ws.id})
    assert res.status_code == 409 and res.json()["detail"]["code"] == "REVISION_SOURCE_CHANGED"
    db_session.expire_all()
    assert db_session.get(SocialContent, content.id).hooks == SOURCE_HOOKS
    assert db_session.query(ContentRevision).one().status == "discarded"


def test_worker_stops_without_ai_when_source_changed(
    client, db_session, make_workspace, make_scoring_run, make_keyword, monkeypatch,
):
    ws, run, brief, content = _source(db_session, make_workspace, make_scoring_run, make_keyword)
    _, sent = _start(client, ws, brief, content, monkeypatch)
    db_session.query(SocialContent).filter_by(id=content.id).update({"regeneration_count": 1})
    db_session.commit()
    ai = RevisionAI()
    assert _run_worker(monkeypatch, sent[-1], ai) == {
        "status": "failed", "reason": "revision_source_changed"}
    assert ai.prompts == []
    [item] = _list(client, ws, brief)
    assert item["status"] == "failed" and "değişti" in item["error_message"]


def test_expired_lease_fails_on_read_and_late_worker_cannot_write(
    client, db_session, make_workspace, make_scoring_run, make_keyword, monkeypatch,
):
    ws, run, brief, content = _source(db_session, make_workspace, make_scoring_run, make_keyword)
    _, sent = _start(client, ws, brief, content, monkeypatch)
    db_session.query(SocialGenerationAttempt).filter_by(stage="content_revision").update(
        {"lease_expires_at": datetime.now(timezone.utc) - timedelta(minutes=1)})
    db_session.commit()

    [item] = _list(client, ws, brief)
    assert item["status"] == "failed" and "zamanında" in item["error_message"]
    result = _run_worker(monkeypatch, sent[-1], RevisionAI(_ai_content()))
    assert result == {"status": "aborted"}
    assert db_session.query(ContentRevision).count() == 0
    db_session.expire_all()
    assert db_session.get(SocialContent, content.id).hooks == SOURCE_HOOKS


def test_ai_failure_keeps_content_and_discard_closes_notice(
    client, db_session, make_workspace, make_scoring_run, make_keyword, monkeypatch,
):
    ws, run, brief, content = _source(db_session, make_workspace, make_scoring_run, make_keyword)
    _, sent = _start(client, ws, brief, content, monkeypatch)
    result = _run_worker(monkeypatch, sent[-1], RevisionAI({"caption": "eksik alanlı yanıt"}))
    assert result == {"status": "failed", "reason": "revision_ai_failed"}
    [item] = _list(client, ws, brief)
    assert item["status"] == "failed"
    db_session.expire_all()
    assert db_session.get(SocialContent, content.id).hooks == SOURCE_HOOKS

    # Başarısız bildirim kapatılır; kabul edilemez
    accept = client.post(f"/api/v1/generation/social/revisions/{item['id']}/accept",
                         params={"brand_profile_id": ws.id})
    assert accept.status_code == 409
    closed = client.post(f"/api/v1/generation/social/revisions/{item['id']}/discard",
                         params={"brand_profile_id": ws.id})
    assert closed.status_code == 200, closed.text
    assert _list(client, ws, brief) == []

    # Bitti: yeni revizyon başlatılabilir (brief'te süren üretim kalmadı)
    again, _ = _start(client, ws, brief, content, monkeypatch, key="key-0003")
    assert again.status_code == 202


def test_scope_gate_blocks_save_when_merge_leaks(
    client, db_session, make_workspace, make_scoring_run, make_keyword, monkeypatch,
):
    from app.core.social import content_revision as scr

    ws, run, brief, content = _source(db_session, make_workspace, make_scoring_run, make_keyword)
    _, sent = _start(client, ws, brief, content, monkeypatch)
    # Birleştirme hatası taklidi: modelin TÜM alanları sızar → kayıt kapısı engellemeli
    monkeypatch.setattr(scr, "merge_scoped", lambda source, revised, scope: dict(revised))
    result = _run_worker(monkeypatch, sent[-1], RevisionAI(_ai_content()))
    assert result == {"status": "failed", "reason": "revision_scope_violation"}
    assert db_session.query(ContentRevision).count() == 0
    assert _list(client, ws, brief)[0]["status"] == "failed"


def test_claim_repair_only_touches_selected_section(
    client, db_session, make_workspace, make_scoring_run, make_keyword, monkeypatch,
):
    ws, run, brief, content = _source(db_session, make_workspace, make_scoring_run, make_keyword)
    _, sent = _start(client, ws, brief, content, monkeypatch, scope=("text",),
                     feedback="Metni daha iddialı yaz")
    risky = _ai_content(caption="Bizimle çalışan herkese garantili sonuç sunuyoruz.")
    repaired = _ai_content(caption="Müşterilerimizi dinleyerek çalışıyoruz.",
                           hooks=[{"text": "Onarımın sızdırdığı kanca?", "style": "question"}])
    ai = RevisionAI(risky, repaired)
    assert _run_worker(monkeypatch, sent[-1], ai)["status"] == "ready"
    assert len(ai.prompts) == 2  # tek onarım
    [item] = _list(client, ws, brief)
    proposed = item["proposed"]
    assert proposed["caption"] == "Müşterilerimizi dinleyerek çalışıyoruz."
    assert proposed["hooks"] == SOURCE_HOOKS  # onarımın kapsam dışı çıktısı atıldı
    assert set(item["changes"]) <= {"caption", "cta_text", "hashtags"}


def test_isolation_validation_and_body_scope(
    client, db_session, make_workspace, make_scoring_run, make_keyword, monkeypatch,
):
    ws, run, brief, content = _source(db_session, make_workspace, make_scoring_run, make_keyword)
    other, _, _, _ = _source(db_session, make_workspace, make_scoring_run, make_keyword, name="Baska WS")
    url = f"/api/v1/generation/social/briefs/{brief.id}/contents/{content.id}/revise"
    body = {"feedback": "Kancaları değiştir", "scope": ["hooks"], "idempotency_key": "key-iso-1"}

    assert client.post(url, params={"brand_profile_id": other.id}, json=body).status_code == 404
    assert client.get(f"/api/v1/generation/social/briefs/{brief.id}/revisions",
                      params={"brand_profile_id": other.id}).status_code == 404
    for bad in ({"feedback": " ab "}, {"scope": []}, {"scope": ["url"]}, {"feedback": "x" * 501}):
        assert client.post(url, params={"brand_profile_id": ws.id}, json={**body, **bad}).status_code == 422
    # Post formatında revize edilecek gövde yok
    res = client.post(url, params={"brand_profile_id": ws.id}, json={**body, "scope": ["body"]})
    assert res.status_code == 400 and res.json()["detail"]["code"] == "REVISION_INVALID_SCOPE"

    _, sent = _start(client, ws, brief, content, monkeypatch, key="key-iso-2")
    _run_worker(monkeypatch, sent[-1], RevisionAI(_ai_content()))
    rev_id = _list(client, ws, brief)[0]["id"]
    for action in ("accept", "discard"):
        r = client.post(f"/api/v1/generation/social/revisions/{rev_id}/{action}",
                        params={"brand_profile_id": other.id})
        assert r.status_code == 404
    db_session.expire_all()
    assert db_session.get(SocialContent, content.id).regeneration_count == 0


def test_video_body_revision_recomputes_duration_and_hooks_revision_keeps_script(
    client, db_session, make_workspace, make_scoring_run, make_keyword, monkeypatch,
):
    ws, run, brief, content = _source(db_session, make_workspace, make_scoring_run, make_keyword,
                                      fmt="reels")
    assert content.actual_duration_sec == 40
    source_scenario = content.scenario

    # Yalnız kancalar: model senaryoyu da değiştirse süre ve senaryo kaynaktan kalır
    _, sent = _start(client, ws, brief, content, monkeypatch, key="key-vid-1")
    ai = RevisionAI(_ai_content(format_payload=_segments((0, 25), (25, 55))))
    assert _run_worker(monkeypatch, sent[-1], ai)["status"] == "ready"
    [item] = _list(client, ws, brief)
    assert item["changes"] == ["hooks"]
    assert item["proposed"]["actual_duration_sec"] == 40
    assert item["proposed"]["scenario"] == source_scenario
    client.post(f"/api/v1/generation/social/revisions/{item['id']}/discard",
                params={"brand_profile_id": ws.id})

    # Gövde (sahneler): senaryo ve süre birleşik içerikten YENİDEN hesaplanır
    _, sent = _start(client, ws, brief, content, monkeypatch, key="key-vid-2", scope=("body",),
                     feedback="Sahneleri biraz uzat")
    ai = RevisionAI(_ai_content(format_payload=_segments((0, 25), (25, 55))))
    assert _run_worker(monkeypatch, sent[-1], ai)["status"] == "ready"
    [item] = _list(client, ws, brief)
    assert item["changes"] == ["format_payload"]
    proposed = item["proposed"]
    assert proposed["actual_duration_sec"] == 55 and proposed["duration_status"] == "valid"
    assert proposed["scenario"] != source_scenario and "Sahne 2" in proposed["scenario"]
    assert proposed["hooks"] == SOURCE_HOOKS

    ok = client.post(f"/api/v1/generation/social/revisions/{item['id']}/accept",
                     params={"brand_profile_id": ws.id})
    assert ok.status_code == 200, ok.text
    db_session.expire_all()
    live = db_session.get(SocialContent, content.id)
    assert live.actual_duration_sec == 55 and live.scenario == proposed["scenario"]


# ── QA turu: kenar durumları ────────────────────────────────────────────────

def test_late_worker_rejected_at_write_when_brief_turns_stale(
    client, db_session, make_workspace, make_scoring_run, make_keyword, monkeypatch,
):
    from app.database.connection import SessionLocal

    ws, run, brief, content = _source(db_session, make_workspace, make_scoring_run, make_keyword)
    _, sent = _start(client, ws, brief, content, monkeypatch)

    class StalingAI(RevisionAI):
        def complete_json(self, prompt, **kwargs):
            other = SessionLocal()  # AI çağrısı sürerken kanal ataması değişti
            other.query(SocialBrief).filter_by(id=brief.id).update({"is_stale": True})
            other.commit()
            other.close()
            return super().complete_json(prompt, **kwargs)

    assert _run_worker(monkeypatch, sent[-1], StalingAI(_ai_content())) == {"status": "aborted"}
    assert db_session.query(ContentRevision).count() == 0
    db_session.expire_all()
    attempt = db_session.query(SocialGenerationAttempt).filter_by(stage="content_revision").one()
    assert (attempt.status, attempt.reason_code) == ("failed", "brief_stale")


def test_accept_refused_when_brief_stale_and_discard_refused_while_running(
    client, db_session, make_workspace, make_scoring_run, make_keyword, monkeypatch,
):
    ws, run, brief, content = _source(db_session, make_workspace, make_scoring_run, make_keyword)
    _, sent = _start(client, ws, brief, content, monkeypatch)
    rev_id = _list(client, ws, brief)[0]["id"]
    running = client.post(f"/api/v1/generation/social/revisions/{rev_id}/discard",
                          params={"brand_profile_id": ws.id})
    assert running.status_code == 409 and running.json()["detail"]["code"] == "REVISION_RUNNING"

    _run_worker(monkeypatch, sent[-1], RevisionAI(_ai_content()))
    db_session.query(SocialBrief).filter_by(id=brief.id).update({"is_stale": True})
    db_session.commit()
    res = client.post(f"/api/v1/generation/social/revisions/{rev_id}/accept",
                      params={"brand_profile_id": ws.id})
    assert res.status_code == 409 and res.json()["detail"]["code"] == "REVISION_STALE"
    db_session.expire_all()
    live = db_session.get(SocialContent, content.id)
    assert live.hooks == SOURCE_HOOKS and live.regeneration_count == 0


def test_accept_scope_gate_blocks_tampered_proposal(
    client, db_session, make_workspace, make_scoring_run, make_keyword, monkeypatch,
):
    from sqlalchemy.orm.attributes import flag_modified

    ws, run, brief, content = _source(db_session, make_workspace, make_scoring_run, make_keyword)
    _, sent = _start(client, ws, brief, content, monkeypatch)  # kapsam: yalnız kancalar
    _run_worker(monkeypatch, sent[-1], RevisionAI(_ai_content()))
    rev = db_session.query(ContentRevision).one()
    rev.proposed_payload["content"]["caption"] = "Kapsam dışı sızan metin"
    flag_modified(rev, "proposed_payload")
    db_session.commit()
    res = client.post(f"/api/v1/generation/social/revisions/{_list(client, ws, brief)[0]['id']}/accept",
                      params={"brand_profile_id": ws.id})
    assert res.status_code == 409 and res.json()["detail"]["code"] == "REVISION_SCOPE_VIOLATION"
    db_session.expire_all()
    assert db_session.get(SocialContent, content.id).caption == SOURCE_CAPTION


def test_new_request_closes_older_pending_proposal(
    client, db_session, make_workspace, make_scoring_run, make_keyword, monkeypatch,
):
    ws, run, brief, content = _source(db_session, make_workspace, make_scoring_run, make_keyword)
    _, sent = _start(client, ws, brief, content, monkeypatch)
    _run_worker(monkeypatch, sent[-1], RevisionAI(_ai_content()))
    old_id = _list(client, ws, brief)[0]["id"]
    _, sent = _start(client, ws, brief, content, monkeypatch, key="key-0009")
    _run_worker(monkeypatch, sent[-1], RevisionAI({"caption": "bozuk"}))  # yeni talep başarısız
    assert _list(client, ws, brief)[0]["status"] == "failed"
    # Eski öneri görünmez kaldı ama artık kabul edilemez
    res = client.post(f"/api/v1/generation/social/revisions/{old_id}/accept",
                      params={"brand_profile_id": ws.id})
    assert res.status_code == 409


def test_pre_claim_failure_closes_attempt_and_dispatch_failure_allows_retry(
    client, db_session, make_workspace, make_scoring_run, make_keyword, monkeypatch,
):
    from app.tasks import generation_tasks

    ws, run, brief, content = _source(db_session, make_workspace, make_scoring_run, make_keyword)
    _, sent = _start(client, ws, brief, content, monkeypatch)

    def boom(*a, **k):
        raise RuntimeError("beklenmeyen")

    monkeypatch.setattr("app.generators.social.attempt_state.claim_attempt", boom)
    with pytest.raises(RuntimeError):
        generation_tasks.revise_social_content_task.apply(args=sent[-1], throw=True)
    db_session.expire_all()
    attempt = db_session.query(SocialGenerationAttempt).filter_by(stage="content_revision").one()
    assert (attempt.status, attempt.reason_code) == ("failed", "worker_bootstrap_failed")

    # Kuyruğa alınamayan istek kapanır; yeni anahtarla yeniden denenebilir (brief kilitli kalmaz)
    def no_broker(*a, **k):
        raise ConnectionError("broker yok")

    monkeypatch.setattr(generation_tasks.revise_social_content_task, "apply_async", no_broker)
    url = f"/api/v1/generation/social/briefs/{brief.id}/contents/{content.id}/revise"
    body = {"feedback": "Kancaları değiştir", "scope": ["hooks"], "idempotency_key": "key-disp-1"}
    res = client.post(url, params={"brand_profile_id": ws.id}, json=body)
    assert res.status_code == 503
    db_session.expire_all()
    failed = db_session.query(SocialGenerationAttempt).filter_by(idempotency_key="key-disp-1").one()
    assert (failed.status, failed.reason_code) == ("failed", "dispatch_failed")
    again, _ = _start(client, ws, brief, content, monkeypatch, key="key-disp-2")
    assert again.status_code == 202
