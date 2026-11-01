"""İddia denetimi rev.4 — belirsiz içerik KAYBOLMAZ, incelemeye işaretlenir (plan_iddia_denetimi.md).

Kabul testleri 1/3/5/6 (RSA + SEO DB zinciri): profilde doğrulanamayan gerçek hizmet içerikten
kaybolmaz; N1 işaretli ilk set `draft` kalır; re-evaluate yalnız `current`'ı yeniler, `history` korur,
temizlik vaat etmez (tanınmayan eş anlam uyarıda kalır); B (risk) kaynağa bakılmaz.
Sosyal DB zinciri: tests/integration/test_social_content_orchestration_f1g5_5.py (rev4 testleri).
"""
import json

from types import SimpleNamespace

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


# ==================== QA düzeltmeleri ====================


def test_reevaluate_noop_does_not_push_history(client, db_session, make_workspace, make_scoring_run):
    """Bulgu kümesi değişmediyse history'ye boş kayıt itilmez (yalnız evaluated_at yenilenir)."""
    ws = make_workspace("NoOp WS", status="confirmed", profile_data={"services": ["Kanal Tedavisi"]})
    run = make_scoring_run(brand_profile_id=ws.id)
    gen_set, _ = _flagged_set(db_session, run.id, "task-rev4-noop")
    url = f"/api/v1/generation/claims/re-evaluate?scoring_run_id={run.id}&brand_profile_id={ws.id}"

    client.post(url)  # gerçek metne göre kayıt yenilenir (el yapımı kayıttan farklı → history'ye iner)
    db_session.expire_all()
    first = set_review(db_session.get(AdGenerationSet, gen_set.id).warnings)
    for _ in range(5):  # boşta tıklamalar
        assert client.post(url).status_code == 200
    db_session.expire_all()
    after = set_review(db_session.get(AdGenerationSet, gen_set.id).warnings)

    assert len(after["history"]) == len(first["history"])  # gerçek geçmiş eskimedi
    assert after["current"]["findings"] == first["current"]["findings"]
    assert after["current"]["evaluated_at"] >= first["current"]["evaluated_at"]


def test_reevaluate_uses_set_snapshot_usp_for_rsa(client, db_session, make_workspace, make_scoring_run):
    """Üretimde kullanıcı USP'siyle dayanaklanan asset re-evaluate'te YENİ bulgu almaz."""
    ws = make_workspace("USP WS", status="confirmed", profile_data={"services": []})
    run = make_scoring_run(brand_profile_id=ws.id)
    gen_set = dispatch_ads_generation(
        db_session, run.id, task_id="task-rev4-usp",
        request_snapshot={"operation": "full", "trusted_brand_usp": "10 yıllık deneyim"})
    group = AdGroupFullSchema(
        name="G", theme="t", keyword_ids=[1], keywords=["kanal tedavisi"],
        headlines=[HeadlineSchema(text="10 Yıllık Deneyim", type="trust")],
        descriptions=[DescriptionSchema(text="Kanal tedavisi için arayın.", type="cta")],
        negative_keywords=[])
    AdsGenerator(db_session, ai_service=object())._save_to_database(
        [group], run.id, generation_set_id=gen_set.id, commit=False)
    finalize_ads_success(db_session, gen_set, groups_count=1, failed_groups=0,
                         warnings=ads_set_warnings([], [], []))

    res = client.post(
        f"/api/v1/generation/claims/re-evaluate?scoring_run_id={run.id}&brand_profile_id={ws.id}")

    assert res.status_code == 200, res.text
    assert res.json()["rsa"] == {"sets": 1, "cleared": 0, "still_flagged": 0}
    db_session.expire_all()
    assert db_session.query(AdHeadline).filter_by(headline_text="10 Yıllık Deneyim").one().validation_action is None


def test_reevaluate_social_keeps_brief_usp_grounded_content_clean(
    client, db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """Brief snapshot'ındaki güvenilir USP ile temiz üretilen sosyal içerik re-evaluate'te işaretlenmez."""
    ctx = _orch._setup_content_orchestration_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=1, task_id="task-r4-usp",
        trusted_brand_usp="20 yıllık deneyim",
    )
    caption = "20 yıllık deneyimle bakım rehberi."
    _orch.run_social_content_generation(
        session_factory=_orch.SessionLocal,
        ai_service=_orch.SmartMockAIService(responses=[_orch._make_valid_post_dict(caption=caption)]),
        attempt_id=ctx["attempt"].id, task_id="task-r4-usp", now_provider=lambda: _orch.T0,
    )
    row, warnings = _orch._persisted_warnings(db_session, ctx["brief"].id)
    assert not any(w.startswith("claim_") for w in warnings)  # üretimde temiz

    ws, run = ctx["workspace"], ctx["scoring_run"]
    res = client.post(
        f"/api/v1/generation/claims/re-evaluate?scoring_run_id={run.id}&brand_profile_id={ws.id}")

    assert res.status_code == 200, res.text
    assert res.json()["social"] == {"contents": 1, "cleared": 0, "still_flagged": 0}
    row, warnings = _orch._persisted_warnings(db_session, ctx["brief"].id)
    assert not any(w.startswith("claim_") for w in warnings)


def test_social_batch_one_flagged_one_clean_both_saved(
    db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """Aynı attempt'te biri işaretli biri temiz iki fikir: ikisi de kaydedilir, attempt completed,
    işaret doğru satırda."""
    from app.database.models import SocialContent, SocialGenerationAttempt

    ctx = _orch._setup_content_orchestration_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=2, task_id="task-r4-batch")
    id1, id2 = ctx["ideas"][0].id, ctx["ideas"][1].id
    ai = _orch.SmartMockAIService(responses=[
        _orch._make_valid_post_dict(caption="Lazer ile bakım rehberi."),      # id1: unverified
        _orch._make_valid_post_dict(caption="Doğal bakım rutini önerileri."),  # id2: temiz
    ])

    result = _orch.run_social_content_generation(
        session_factory=_orch.SessionLocal, ai_service=ai, attempt_id=ctx["attempt"].id,
        task_id="task-r4-batch", now_provider=lambda: _orch.T0,
    )

    assert result.status == "completed" and set(result.persisted_idea_ids) == {id1, id2}
    assert result.ai_calls_used == 2
    db_session.expire_all()
    assert db_session.get(SocialGenerationAttempt, ctx["attempt"].id).status == "completed"
    rows = {r.idea_id: r for r in db_session.query(SocialContent).filter_by(brief_id=ctx["brief"].id)}
    assert any(w.startswith("claim_unverified:") for w in rows[id1].validation_warnings)
    assert not any(str(w).startswith("claim_") for w in (rows[id2].validation_warnings or []))


def test_rsa_generate_ads_task_glue_unverified_headline_makes_draft_set(
    client, db_session, make_workspace, make_scoring_run, make_keyword, monkeypatch
):
    """Gerçek yapıştırıcı: dispatch → generate_ads_task → AdsGenerator → RSA (CannedAI). Profilde olmayan
    hizmet başlığı: asset kalır, set 'draft' (ilk set olsa da), set.warnings claim_review (group_id'li),
    /ads/sets review_required."""
    from app.database.models import ChannelPool
    from app.tasks import generation_tasks

    ws = make_workspace("Glue WS", status="confirmed", profile_data={"services": ["Kanal Tedavisi"]})
    run = make_scoring_run(brand_profile_id=ws.id, status="channel_assigned")
    kw = make_keyword(text_value="kanal tedavisi", brand_profile_id=ws.id)
    db_session.add(ChannelPool(scoring_run_id=run.id, keyword_id=kw.id, channel="ADS", final_rank=1))
    db_session.commit()
    gen_set = dispatch_ads_generation(
        db_session, run.id, task_id="task-rev4-glue", request_snapshot={"operation": "full"})

    def assets(texts, typ):
        return [{"text": t, "type": typ, "position": "any"} for t in texts]

    class CannedAI:
        collector = None

        def complete_json(self, prompt, max_tokens=None, response_schema=None, **kw):
            return json.dumps({
                "headlines": assets(["Пломбування каналів", "Kanal Tedavisi Uzmanı", "Hemen Arayın",
                                     "Randevu Alın", "Uzman Kadro"], "benefit"),
                "descriptions": assets(["Kanal tedavisi için hemen arayın.", "Uzman kadroyla hizmet alın."],
                                       "value_prop"),
                "negative_keywords": [{"keyword": f"neg{i}", "match_type": "phrase",
                                       "category": "bilgi_amacli", "reason": "r"} for i in range(10)],
                "keyword_headlines": [],
            })

        def complete(self, prompt, max_tokens=None, **kw):
            raise AssertionError("unverified için rewrite/ek AI çağrısı olmamalı")

    monkeypatch.setattr("app.generators.ai_service.get_ai_service", lambda *a, **k: CannedAI())

    result = generation_tasks.generate_ads_task.apply(
        kwargs={"scoring_run_id": run.id, "brand_name": "Marka", "generation_set_id": gen_set.id},
        task_id="task-rev4-glue",
    )
    assert result.result.get("status") == "completed", result.result
    assert result.result["set_status"] == "draft"  # N1: ilk set ama işaretli

    db_session.expire_all()
    saved = db_session.get(AdGenerationSet, gen_set.id)
    record = set_review(saved.warnings)
    findings = record["current"]["findings"]
    assert findings and all(f["group_id"] for f in findings)
    assert findings[0]["asset_text"] == "Пломбування каналів" and findings[0]["kind"] == "unverified"
    headline = db_session.query(AdHeadline).filter_by(headline_text="Пломбування каналів").one()
    assert headline.validation_action == "claim_review"

    listing = client.get(
        f"/api/v1/generation/ads/sets?scoring_run_id={run.id}&brand_profile_id={ws.id}").json()
    item = listing["sets"][0]
    assert item["review_required"] is True and item["claim_unverified_count"] >= 1
    assert item["claim_review"]["findings"][0]["asset_text"] == "Пломбування каналів"


def test_hybrid_run_ads_marks_set_and_keeps_it_active(db_session, make_workspace, make_scoring_run, monkeypatch):
    """Benchmark DB koşucusu üretimle aynı uyarıyı kurar: review_required doğru; ilk set eski akışla active kalır."""
    from app.core.benchmark import hybrid_run

    ws = make_workspace("Hybrid WS")
    run = make_scoring_run(brand_profile_id=ws.id)
    group = _full_group()

    def fake_generate(self, request, generation_set_id=None, defer_commit=False, trusted_brand_usp=""):
        self._save_to_database([group], request.scoring_run_id, generation_set_id=generation_set_id, commit=False)
        return SimpleNamespace(total_groups=1, failed_groups=0, ad_groups=[group])

    monkeypatch.setattr("app.generators.ads.ads_generator.AdsGenerator.generate_ads", fake_generate)

    out = hybrid_run._run_ads(db_session, object(), run, {})

    assert out["set_status"] == SET_ACTIVE
    db_session.expire_all()
    saved = db_session.get(AdGenerationSet, out["generation_set_id"])
    assert set_review(saved.warnings)["current"]["findings"]  # review_required=True ile active


def test_ads_export_has_set_warnings_table(db_session, make_workspace, make_scoring_run, tmp_path):
    """Aktif setin claim_review kaydı CSV (set_uyarilari.csv) ve Excel ('Set Uyarıları') ile dışarı verilir."""
    import csv
    import io
    import zipfile

    from openpyxl import load_workbook

    from app.exporters.csv_exporter import CsvExporter
    from app.exporters.excel_exporter import ExcelExporter
    from app.schemas.export import ExportSectionEnum

    ws = make_workspace("Export WS")
    run = make_scoring_run(brand_profile_id=ws.id)
    gen_set, _ = _flagged_set(db_session, run.id, "task-rev4-export")
    activate_set(db_session, gen_set)

    path = str(tmp_path / "ads.zip")
    CsvExporter(db_session).export(run.id, [ExportSectionEnum.ADS], path)
    with zipfile.ZipFile(path) as zf:
        rows = list(csv.DictReader(io.StringIO(zf.read("set_uyarilari.csv").decode("utf-8-sig"))))
        heads = zf.read("basliklar.csv").decode("utf-8-sig")
    assert {r["Durum"] for r in rows} == {"İnceleme gerekli"}
    assert any(r["Metin"] == SERVICE_TEXT and r["Sınıf"].startswith("Profilde doğrulanamadı") for r in rows)
    assert any(r["Metin"] == RISK_TEXT and r["Sınıf"].startswith("Risk") for r in rows)
    assert "İnceleme gerekli" in heads

    xlsx = str(tmp_path / "ads.xlsx")
    ExcelExporter(db_session).export(run.id, [ExportSectionEnum.ADS], xlsx)
    assert "Set Uyarıları" in load_workbook(xlsx).sheetnames


def test_legacy_social_regenerate_adopts_flagged_content_and_pushes_history(
    db_session, make_workspace, make_scoring_run
):
    """Legacy regenerate: riskli yeni içerik de benimsenir ve işaretlenir; önceki kayıt history'ye iner."""
    from integration.test_policy_a_b import _mk_social_tree

    from app.database.models import SocialContent
    from app.generators.claim_review import social_record
    from app.generators.social.social_generator import SocialGenerator
    from app.schemas.social import HookSchema, HookStyleEnum, SocialContentSchema

    ws = make_workspace("LegacyRegen WS")
    run = make_scoring_run(brand_profile_id=ws.id)
    _, idea, content = _mk_social_tree(db_session, run.id)
    content.validation_warnings = [
        "claim_unverified:lazer",
        'claim_review:{"current":{"evaluated_at":"t0","findings":[{"kind":"unverified","span":"lazer"}],"applied":[]},"history":[]}',
    ]
    db_session.commit()

    def regenerated(**kwargs):
        return SocialContentSchema(
            idea_id=idea.id, hooks=[HookSchema(text="Merak ettiniz mi?", style=HookStyleEnum.QUESTION)],
            caption="Kazanç garantisi ile bakım!", cta_text="Kaydet", hashtags=["a", "b", "c", "d", "e"])

    gen = SocialGenerator(db_session, ai_service=None)
    gen.content_gen = SimpleNamespace(regenerate=regenerated)

    out = gen.regenerate_content(content.id, "Marka")

    assert out is not None and out.caption == "Kazanç garantisi ile bakım!"
    db_session.expire_all()
    row = db_session.get(SocialContent, content.id)
    assert any(w.startswith("claim_risk:") for w in row.validation_warnings)
    assert not any(w.startswith("claim_unverified:") for w in row.validation_warnings)
    record = social_record(row.validation_warnings)
    assert [f["kind"] for f in record["current"]["findings"]] == ["risk"]
    assert record["history"][0]["findings"][0]["span"] == "lazer"


# ==================== Codex P2: sosyal dayanak kapsamı ====================


def _add_contents_attempt(db_session, brief_id, idea_ids, status, usp, key):
    from app.database.models import SocialGenerationAttempt

    attempt = SocialGenerationAttempt(
        brief_id=brief_id, stage="contents", idempotency_key=key, status=status,
        requested_idea_ids=idea_ids,
        coverage={"schema_version": "contents_request_v1",
                  "request": {"idea_ids": idea_ids, "product_facts": None, "trusted_brand_usp": usp}},
        created_at=_orch.T0,
    )
    db_session.add(attempt)
    db_session.commit()
    return attempt


def test_reevaluate_social_ignores_failed_and_other_idea_attempts(
    client, db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """Başarısız / başka fikre ait attempt'in USP'si bu içeriğin dayanağı DEĞİL: uyarı korunur."""
    ctx = _orch._setup_content_orchestration_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=2, task_id="task-r4-scope")
    id1, id2 = ctx["ideas"][0].id, ctx["ideas"][1].id
    _orch.run_social_content_generation(
        session_factory=_orch.SessionLocal,
        ai_service=_orch.SmartMockAIService(responses=[
            _orch._make_valid_post_dict(caption="Lazer ile bakım rehberi."),
            _orch._make_valid_post_dict(caption="Doğal bakım rutini önerileri."),
        ]),
        attempt_id=ctx["attempt"].id, task_id="task-r4-scope", now_provider=lambda: _orch.T0,
    )
    brief_id, ws, run = ctx["brief"].id, ctx["workspace"], ctx["scoring_run"]
    # profil yalnız cilt bakımı; ilgisiz attempt'ler 'Lazer hizmeti' USP'si taşıyor
    _add_contents_attempt(db_session, brief_id, [id1, id2], "failed", "Lazer hizmeti", "k-failed")
    _add_contents_attempt(db_session, brief_id, [id2], "completed", "Lazer hizmeti", "k-other-idea")

    res = client.post(
        f"/api/v1/generation/claims/re-evaluate?scoring_run_id={run.id}&brand_profile_id={ws.id}")

    assert res.status_code == 200, res.text
    assert res.json()["social"] == {"contents": 2, "cleared": 0, "still_flagged": 1}
    db_session.expire_all()
    from app.database.models import SocialContent
    row1 = db_session.query(SocialContent).filter_by(brief_id=brief_id, idea_id=id1).one()
    assert any(w.startswith("claim_unverified:") for w in row1.validation_warnings)


def test_reevaluate_social_producing_attempt_usp_grounds_its_own_content(
    client, db_session, make_workspace, make_scoring_run, make_keyword, enable_flag
):
    """İçeriği üreten attempt'in USP'si hâlâ o içeriği temiz tutar (üretimde temiz geçtiği için)."""
    ctx = _orch._setup_content_orchestration_env(
        db_session, make_workspace, make_scoring_run, make_keyword, num_ideas=1, task_id="task-r4-own",
        trusted_brand_usp="Lazer hizmeti")
    _orch.run_social_content_generation(
        session_factory=_orch.SessionLocal,
        ai_service=_orch.SmartMockAIService(
            responses=[_orch._make_valid_post_dict(caption="Lazer ile bakım rehberi.")]),
        attempt_id=ctx["attempt"].id, task_id="task-r4-own", now_provider=lambda: _orch.T0,
    )
    _, warnings = _orch._persisted_warnings(db_session, ctx["brief"].id)
    assert not any(w.startswith("claim_") for w in warnings)  # üretimde USP ile temiz

    ws, run = ctx["workspace"], ctx["scoring_run"]
    res = client.post(
        f"/api/v1/generation/claims/re-evaluate?scoring_run_id={run.id}&brand_profile_id={ws.id}")

    assert res.json()["social"] == {"contents": 1, "cleared": 0, "still_flagged": 0}
    _, warnings = _orch._persisted_warnings(db_session, ctx["brief"].id)
    assert not any(w.startswith("claim_") for w in warnings)
