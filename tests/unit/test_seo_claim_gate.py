"""SEO+GEO son-metin iddia kapısı (plan_iddia_denetimi A4): genişletme sonrası tek kapı,
tek onarım, kalıcı `no_ungrounded_claims` maddesi, skor/ağırlık dokunulmaz."""
import json
from datetime import datetime

from app.compliance.publish_review import publish_review_from_results
from app.generators.seo_geo.seo_geo_generator import SEOGEOGenerator

KEYWORD = "bist 100"


def _content(title="BIST 100 Rehberi", intro="", words=550):
    body = " ".join([KEYWORD] + ["kelime"] * (words - 2))
    return {
        "title": title,
        "intro_paragraph": intro,
        "subheadings": ["BIST 100 Nedir?"],
        "body_sections": [body],
        "faq_items": [{"question": "Nedir?", "answer": "Bir endekstir."}],
        "word_count": words,
    }


class FakeAI:
    """İlk çağrı genişletme, sonrakiler onarım; prompt'ları kaydeder."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.prompts = []

    def complete_json(self, prompt, max_tokens=None, response_schema=None):
        self.prompts.append(prompt)
        return json.dumps(self.responses.pop(0), ensure_ascii=False)

    @property
    def repair_calls(self):
        return [p for p in self.prompts if "DAYANAKSIZ İDDİALAR" in p]


class RecChecker:
    def __init__(self, score):
        self.score = score
        self.contents = []

    def check(self, content=None, keyword=None, **kwargs):
        self.contents.append(content)
        return {"score": self.score, "checks": [
            {"criterion": "title_has_keyword", "status": "pass",
             "details": "", "importance": "critical"}]}


def _gen(ai, seo=0.9, geo=0.9):
    gen = SEOGEOGenerator.__new__(SEOGEOGenerator)  # db'siz unit kurulum
    gen.db = None  # scoring_run_id=None yolunda DB'ye dokunulmaz
    gen.ai_service = ai
    gen.seo_checker = RecChecker(seo)
    gen.geo_checker = RecChecker(geo)
    return gen


def _expand_then_gate(gen, content):
    seo0 = {"score": 0.85, "checks": []}
    geo0 = {"score": 0.85, "checks": []}
    content, seo, geo = gen._maybe_expand_once(
        KEYWORD, content, seo0, geo0, 500, 600, link_pool=[])
    return gen._claim_gate(
        KEYWORD, content, seo, geo, scoring_run_id=None,
        word_count_min=500, word_count_max=600, link_pool=[])


def test_expansion_adds_claim_gate_catches_single_repair_cleans():
    # rev.4: yalnız RİSK (mutlak sonuç/garanti) onarım tetikler; eski girdi 'ISO ve CE belgeli'
    # artık unverified (onarım yok) — bkz. test_unverified_only_triggers_no_repair_and_is_review_required
    short = _content(words=410)
    expanded = _content(intro="Ağrısız hizmet sunuyoruz.", words=550)
    repaired = _content(title="BIST 100 Rehberi (düzeltildi)", words=550)
    ai = FakeAI(expanded, repaired)
    gen = _gen(ai)

    content, seo, geo, claim = _expand_then_gate(gen, short)

    assert claim["status"] == "pass" and claim["details"] == ""
    assert len(ai.repair_calls) == 1
    # SEO/GEO nihai (onarılmış) metinde yeniden hesaplandı
    assert content["title"].endswith("(düzeltildi)")
    assert gen.seo_checker.contents[-1]["title"].endswith("(düzeltildi)")
    assert gen.geo_checker.contents[-1]["title"].endswith("(düzeltildi)")
    # Madde kalıcı listede, skor ağırlığına girmedi
    assert seo["checks"][-1] == claim
    assert seo["score"] == 0.9
    # N3: benimsenen onarımın orijinal alan metni + çözülen risk kayıtta
    current = claim["claim_review"]["current"]
    assert current["findings"] == []
    assert current["applied"][0]["kind"] == "risk"
    assert current["original"]["intro_paragraph"] == "Ağrısız hizmet sunuyoruz."
    assert claim["claim_review"]["history"] == []


def test_unresolved_claim_is_saved_as_fail_and_review_required():
    short = _content(words=410)
    expanded = _content(intro="Ağrısız hizmet sunuyoruz.", words=550)
    still_bad = _content(intro="Ağrısız hizmet sunuyoruz.", words=550)
    ai = FakeAI(expanded, still_bad)
    gen = _gen(ai)

    content, seo, geo, claim = _expand_then_gate(gen, short)

    assert claim["status"] == "fail"
    assert claim["details"].startswith("risk:") and "ağrisiz" in claim["details"]
    assert len(ai.repair_calls) == 1  # tek tur, döngü yok
    # Daha iyi olmayan onarım benimsenmedi: orijinal (genişletilmiş) metin kaldı
    assert content["intro_paragraph"] == "Ağrısız hizmet sunuyoruz."
    review = publish_review_from_results(seo, {"score": 0.9, "evaluation_source": "ai"})
    assert review["status"] == "review_required"
    assert "no_ungrounded_claims" in [f["criterion"] for f in review["critical_failures"]]
    detail = review["critical_failures"][0]["detail"]
    assert detail.startswith("Risk — yayın öncesi düzelt")


def test_partial_repair_is_adopted_but_remains_fail():
    expanded = _content(intro="Ağrısız ve ISO 9001 belgeli hizmet.", words=550)
    partial = _content(intro="ISO 9001 belgeli hizmet.", words=550)
    gen = _gen(FakeAI(partial))

    content, seo, geo, claim = gen._claim_gate(
        KEYWORD, expanded, {"score": 0.9, "checks": []}, {"score": 0.9},
        scoring_run_id=None, word_count_min=500, word_count_max=600, link_pool=[])

    assert content is not expanded and content["intro_paragraph"] == "ISO 9001 belgeli hizmet."
    assert claim["status"] == "fail"  # kaynak yok → ISO 9001 de dayanaksız (unverified kalır)
    assert claim["details"].startswith("unverified:")
    assert "Profilde doğrulanamadı — kontrol gerekli" in claim["details"]
    assert "risk" not in claim["details"].split("unverified")[0] or claim["details"].startswith("unverified")
    assert claim["claim_review"]["current"]["applied"][0]["kind"] == "risk"  # çözülen risk kayıtlı


def test_unverified_only_triggers_no_repair_and_is_review_required():
    """rev.4: profilde doğrulanamayan iddia AI açmaz, içerik/metin AYNEN kalır, kayıt fail + review_required."""
    ai = FakeAI()
    gen = _gen(ai)
    content_in = _content(intro="ISO ve CE belgeli hizmet.", words=550)

    content, seo, geo, claim = gen._claim_gate(
        KEYWORD, content_in, {"score": 0.9, "checks": []}, {"score": 0.9, "evaluation_source": "ai"},
        scoring_run_id=None, word_count_min=500, word_count_max=600, link_pool=[])

    assert ai.prompts == []  # ek AI çağrısı yok
    assert content is content_in and content["intro_paragraph"] == "ISO ve CE belgeli hizmet."
    assert claim["status"] == "fail"
    assert claim["details"].startswith("unverified:")
    assert {f["kind"] for f in claim["claim_review"]["current"]["findings"]} == {"unverified"}
    review = publish_review_from_results(seo, {"score": 0.9, "evaluation_source": "ai"})
    assert review["status"] == "review_required"
    assert "Profilde doğrulanamadı — kontrol gerekli" in review["critical_failures"][0]["detail"]


def test_clean_content_makes_no_repair_call_and_keeps_scores():
    ai = FakeAI()
    gen = _gen(ai)
    clean = _content(intro="Endeks yatırımcıları için rehber.")

    content, seo, geo, claim = gen._claim_gate(
        KEYWORD, clean, {"score": 0.9, "checks": []}, {"score": 0.8},
        scoring_run_id=None, word_count_min=500, word_count_max=600, link_pool=[])

    assert content is clean and ai.prompts == []
    assert claim["status"] == "pass"
    assert (seo["score"], geo["score"]) == (0.9, 0.8)


def test_scans_faq_alt_text_and_link_anchor_fields():
    gen = _gen(FakeAI())
    content = _content()
    content["faq_items"] = [{"question": "Ağrısız mı?", "answer": "Evet."}]
    content["image_alt_texts"] = ["lazer ile tedavi"]
    content["external_link_anchor"] = "ISO 13485 kaynağı"
    found = gen._scan_claims(content, "", [KEYWORD])
    kinds = {v.split(":")[0] for v in found if ":" in v}
    assert kinds == {"absolute", "service", "cert"}


def test_slug_is_not_scanned_and_keyword_number_in_title_exempt():
    gen = _gen(FakeAI())
    content = _content(title="20 Yıllık Implant Rehberi")
    content["url_suggestion"] = "20-yıllık-implant"
    assert gen._scan_claims(content, "", ["20 yıllık implant"]) == []
    # slug tek başına ihlal üretmez
    content["title"] = "Implant Rehberi"
    assert gen._scan_claims(content, "", ["20 yıllık implant"]) == []


def test_repair_keeps_fields_the_model_omitted():
    original = _content(intro="ISO belgeli hizmet.")
    original["image_alt_texts"] = ["alt metin"]
    original["meta_description"] = "Meta."
    reply = {k: v for k, v in _content(intro="Temiz giriş.").items() if k in (
        "title", "intro_paragraph", "subheadings", "body_sections")}
    gen = _gen(FakeAI(reply))
    out = gen._repair_claims(KEYWORD, original, ["cert:ISO"], "", 500, 600, [])
    assert out["intro_paragraph"] == "Temiz giriş."
    assert out["image_alt_texts"] == ["alt metin"] and out["meta_description"] == "Meta."
    assert out["faq_items"]


def test_claim_item_is_not_in_score_weights():
    from app.compliance.seo_checker import SEOComplianceChecker
    assert "no_ungrounded_claims" not in SEOComplianceChecker.CRITERIA_WEIGHTS


def test_generate_bulk_counts_claim_blocked(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    gen = _gen(FakeAI())
    pools = [SimpleNamespace(keyword_id=i) for i in (1, 2, 3)]
    gen.db = MagicMock()
    gen.db.query.return_value.filter.return_value.order_by.return_value.all.return_value = pools
    statuses = iter(["fail", "pass", "fail"])

    def fake_generate(self, request, scoring_run_id=None):
        return {"id": request.keyword_id, "seo_compliance": {"score": 0.5},
                "geo_compliance": {"score": 0.5},
                "claim_check": {"status": next(statuses)}}

    monkeypatch.setattr(SEOGEOGenerator, "generate_content", fake_generate)
    out = gen.generate_bulk(7)
    assert out["generated"] == 3 and out["claim_blocked_count"] == 2


def test_finalize_seo_bulk_sums_claim_blocked(monkeypatch):
    from app.tasks import generation_tasks

    captured = {}
    monkeypatch.setattr(
        generation_tasks, "update_task_status", lambda *a, **kw: captured.update(kw))
    out = generation_tasks.finalize_seo_bulk(
        [{"content_ids": [1, 2], "claim_blocked": 1},
         {"content_ids": [3], "claim_blocked": 2}, None], "parent", 5)
    assert out["status"] == "completed"
    assert captured["result_data"]["claim_blocked_count"] == 3
    assert captured["result_data"]["total"] == 3


def test_current_year_and_keyword_numbers_are_not_claims():
    gen = _gen(FakeAI())
    year = datetime.now().year
    content = _content(intro=f"{year} yılında BIST 100 endeksi yatırımcıların gündeminde.")
    assert gen._scan_claims(content, "", [KEYWORD, str(year)]) == []
