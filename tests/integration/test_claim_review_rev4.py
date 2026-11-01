"""İddia denetimi rev.4 — belirsiz içerik KAYBOLMAZ, incelemeye işaretlenir (plan_iddia_denetimi.md).

Kabul testleri 1/3/5/6 (RSA + SEO DB zinciri): profilde doğrulanamayan gerçek hizmet içerikten
kaybolmaz; N1 işaretli ilk set `draft` kalır; re-evaluate yalnız `current`'ı yeniler, `history` korur,
temizlik vaat etmez (tanınmayan eş anlam uyarıda kalır); B (risk) kaynağa bakılmaz.
Sosyal DB zinciri: tests/integration/test_social_content_orchestration_f1g5_5.py (rev4 testleri).
"""
import json

import pytest

from app.database.models import (
    AdDescription,
    AdGenerationSet,
    AdGroup,
    AdHeadline,
    SEOComplianceResult,
)
from app.generators.ads.ads_generator import AdsGenerator
from app.generators.ads.generation_sets import (
    SET_ACTIVE,
    SET_DRAFT,
    activate_set,
    dispatch_ads_generation,
    finalize_ads_success,
)
from app.generators.claim_review import ads_set_warnings, set_review
from integration import test_social_content_orchestration_f1g5_5 as _orch
from app.schemas.ads import AdGroupFullSchema, DescriptionSchema, HeadlineSchema

@pytest.fixture
def enable_flag(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "ENABLE_SOCIAL_BRIEF_FLOW", True)


SERVICE_TEXT = "Пломбування каналів"  # profilde yok → service (unverified)
RISK_TEXT = "Kazanç Garantisi Sunuyoruz"  # garanti → risk


def _full_group():
    return AdGroupFullSchema(
        name="Kanal", theme="t", keyword_ids=[1], keywords=["kanal tedavisi"],
        headlines=[
            HeadlineSchema(text=SERVICE_TEXT, type="benefit", validation_action="claim_review"),
            HeadlineSchema(text=RISK_TEXT, type="benefit", validation_action="claim_review"),
            HeadlineSchema(text="Hemen Arayın", type="cta"),
        ],
        descriptions=[
            DescriptionSchema(text="Kanal tedavisi için randevu alın.", type="cta"),
            DescriptionSchema(text="Uzman kadroyla hizmet.", type="value_prop"),
        ],
        negative_keywords=[],
        claim_findings=[
            {"kind": "unverified", "category": "service", "span": "пломб",
             "reason": "Profilde doğrulanamadı — kontrol gerekli",
             "asset_type": "headline", "asset_text": SERVICE_TEXT},
            {"kind": "risk", "category": "guarantee", "span": "kazanç garanti",
             "reason": "Açık sonuç/kazanç vaadi — yayın öncesi düzelt",
             "asset_type": "headline", "asset_text": RISK_TEXT},
        ],
        claim_applied=[],
    )


def _flagged_set(db, run_id, task_id):
    """Dispatch → AdsGenerator kayıt → finalize (worker akışının DB kısmı)."""
    gen_set = dispatch_ads_generation(
        db, run_id, task_id=task_id, request_snapshot={"operation": "full"})
    group = _full_group()
    AdsGenerator(db, ai_service=object())._save_to_database(
        [group], run_id, generation_set_id=gen_set.id, commit=False)
    status = finalize_ads_success(
        db, gen_set, groups_count=1, failed_groups=0,
        warnings=ads_set_warnings([], group.claim_findings, group.claim_applied),
    )
    return gen_set, status


def test_flagged_first_set_stays_draft_and_asset_kept(db_session, make_workspace, make_scoring_run):
    """Kabul 1+5 (N1): işaretli içerik silinmez (validation_action DB'de), ilk set olsa da draft kalır."""
    ws = make_workspace("Flagged WS")
    run = make_scoring_run(brand_profile_id=ws.id)

    gen_set, status = _flagged_set(db_session, run.id, "task-rev4-1")

    assert status == SET_DRAFT  # N1: temiz olsaydı 'active' olurdu
    db_session.expire_all()
    saved = db_session.query(AdGenerationSet).filter_by(id=gen_set.id).one()
    assert saved.status == SET_DRAFT
    record = set_review(saved.warnings)
    findings = record["current"]["findings"]
    assert {f["kind"] for f in findings} == {"unverified", "risk"}
    assert all(f["group_id"] for f in findings)  # grup kimlikli
    headlines = {h.headline_text: h for h in db_session.query(AdHeadline).all()}
    assert headlines[SERVICE_TEXT].validation_action == "claim_review"  # asset DB'de, aynen
    assert headlines[RISK_TEXT].validation_action == "claim_review"
    assert headlines["Hemen Arayın"].validation_action is None

    # Kullanıcı açıkça aktifleştirebilir (mevcut activate yolu)
    activated = activate_set(db_session, saved)
    assert activated.status == SET_ACTIVE


def test_clean_first_set_still_auto_activates(db_session, make_workspace, make_scoring_run):
    ws = make_workspace("Clean WS")
    run = make_scoring_run(brand_profile_id=ws.id)
    gen_set = dispatch_ads_generation(
        db_session, run.id, task_id="task-rev4-clean", request_snapshot={"operation": "full"})
    status = finalize_ads_success(
        db_session, gen_set, groups_count=0, failed_groups=0, warnings=ads_set_warnings([], [], []))
    assert status == SET_ACTIVE


def test_sets_listing_exposes_review_required(client, db_session, make_workspace, make_scoring_run):
    ws = make_workspace("List WS")
    run = make_scoring_run(brand_profile_id=ws.id)
    _flagged_set(db_session, run.id, "task-rev4-list")

    res = client.get(f"/api/v1/generation/ads/sets?scoring_run_id={run.id}&brand_profile_id={ws.id}")

    assert res.status_code == 200, res.text
    item = res.json()["sets"][0]
    assert item["status"] == "draft"
    assert item["review_required"] is True
    assert (item["claim_risk_count"], item["claim_unverified_count"]) == (1, 1)


def test_reevaluate_rsa_clears_recognised_alias_keeps_risk_and_history(
    client, db_session, make_workspace, make_scoring_run
):
    """Kabul 6 + N2: tanınan alias temizlenir, tanınmayan eş anlam ve B (risk) kalır; history korunur."""
    ws = make_workspace(
        "ReEval WS", status="confirmed",
        profile_data={"services": ["Kanal Tedavisi"]},  # eş anlam: sözlük 'пломб'u bu satırla eşlemiyor
    )
    run = make_scoring_run(brand_profile_id=ws.id)
    gen_set, _ = _flagged_set(db_session, run.id, "task-rev4-reeval")
    url = f"/api/v1/generation/claims/re-evaluate?scoring_run_id={run.id}&brand_profile_id={ws.id}"

    # 1) Profil 'Kanal Tedavisi' → eşleştirme tanımıyor: uyarı KORUNUR, 'temizlendi' denmez (N2)
    first = client.post(url)
    assert first.status_code == 200, first.text
    # kayıtlı bulgular _flagged_set'te el yazısı (asset_text yok) → ilk koşuda gerçek metne göre yeniden kurulur
    assert first.json()["rsa"]["sets"] == 1
    db_session.expire_all()
    headline = db_session.query(AdHeadline).filter_by(headline_text=SERVICE_TEXT).one()
    assert headline.validation_action == "claim_review"  # metin ve asset aynen

    # 2) Profile tanınan alias eklenir → unverified temizlenir; risk kaynağa bakılmadan kalır
    ws.profile_data = {"services": ["Kanal Tedavisi", "Diş dolgusu"]}
    db_session.commit()
    second = client.post(url)
    assert second.status_code == 200, second.text
    rsa = second.json()["rsa"]
    assert rsa["cleared"] == 1 and rsa["still_flagged"] == 1
    db_session.expire_all()
    headlines = {h.headline_text: h for h in db_session.query(AdHeadline).all()}
    assert headlines[SERVICE_TEXT].validation_action is None  # temizlendi
    assert headlines[RISK_TEXT].validation_action == "claim_review"  # B değişmez
    assert headlines[SERVICE_TEXT].headline_text == SERVICE_TEXT  # metne dokunulmadı
    saved = db_session.query(AdGenerationSet).filter_by(id=gen_set.id).one()
    assert saved.status == SET_DRAFT  # re-evaluate aktifleştirmez
    record = set_review(saved.warnings)
    assert [f["kind"] for f in record["current"]["findings"]] == ["risk"]
    assert len(record["history"]) == 2  # ilk kayıt + 1. re-evaluate sonucu geçmişte


SEO_BAD_INTRO = "Hisse takip, ISO ve CE belgeli lazer hizmetidir. Hisse takip anlik calisir."


def test_reevaluate_seo_partial_clear_keeps_history(
    client, db_session, make_workspace, make_scoring_run, monkeypatch
):
    """SEO: profile 'lazer' eklenince service temizlenir; kodsuz ISO/CE (cert) kalır; checks_json güncellenir."""
    from integration.test_seo_claim_persistence import _generate

    ws, run, result, ai = _generate(
        db_session, make_workspace, make_scoring_run, monkeypatch, SEO_BAD_INTRO, SEO_BAD_INTRO)
    assert ai.calls == 0  # unverified: AI yok
    ws.status = "confirmed"
    ws.profile_data = {"services": ["Lazer epilasyon"]}
    db_session.commit()

    res = client.post(
        f"/api/v1/generation/claims/re-evaluate?scoring_run_id={run.id}&brand_profile_id={ws.id}")

    assert res.status_code == 200, res.text
    seo = res.json()["seo"]
    assert seo["contents"] == 1 and seo["cleared"] == 1 and seo["still_flagged"] == 2  # ISO + CE kodsuz
    db_session.expire_all()
    row = db_session.query(SEOComplianceResult).filter_by(seo_geo_content_id=result["id"]).one()
    item = next(c for c in row.checks_json if c["criterion"] == "no_ungrounded_claims")
    assert item["status"] == "fail"
    assert item["details"].startswith("unverified:") and "lazer" not in item["details"]
    assert len(item["claim_review"]["history"]) == 1  # önceki 'current' korunur
    assert item["claim_review"]["history"][0]["findings"]  # eski bulgular silinmedi


def test_reevaluate_is_workspace_scoped(client, db_session, make_workspace, make_scoring_run):
    ws_a = make_workspace("A WS")
    ws_b = make_workspace("B WS")
    run_a = make_scoring_run(brand_profile_id=ws_a.id)
    res = client.post(
        f"/api/v1/generation/claims/re-evaluate?scoring_run_id={run_a.id}&brand_profile_id={ws_b.id}")
    assert res.status_code == 404


def test_reevaluate_social_clears_recognised_alias(
    client, db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """Kabul 6 (sosyal): profile hizmet eklenince unverified temizlenir; kayıt history'ye iner; metin değişmez."""
    ctx = _orch._setup_content_orchestration_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=1, task_id="task-r4-3"
    )
    caption = "Lazer ile bakım rehberi."
    _orch.run_social_content_generation(
        session_factory=_orch.SessionLocal,
        ai_service=_orch.SmartMockAIService(responses=[_orch._make_valid_post_dict(caption=caption)]),
        attempt_id=ctx["attempt"].id, task_id="task-r4-3", now_provider=lambda: _orch.T0,
    )
    brief, ws, run_id = ctx["brief"], ctx["workspace"], ctx["scoring_run"].id
    ws_id = ws.id
    ws.profile_data = {"services": ["Lazer epilasyon"]}
    db_session.commit()

    res = client.post(f"/api/v1/generation/claims/re-evaluate?scoring_run_id={run_id}&brand_profile_id={ws_id}")

    assert res.status_code == 200, res.text
    social = res.json()["social"]
    assert social["contents"] == 1 and social["cleared"] >= 1 and social["still_flagged"] == 0
    row, warnings = _orch._persisted_warnings(db_session, brief.id)
    assert row.caption == caption
    assert not any(w.startswith(("claim_unverified:", "claim_risk:")) for w in warnings)
    record = json.loads(next(w for w in warnings if w.startswith("claim_review:"))[len("claim_review:"):])
    assert record["current"]["findings"] == [] and len(record["history"]) == 1
