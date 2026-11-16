"""Faz 2 saf mantik: kaynakta rakam/alinti arama, bolum kurulumu, arama talebi ozeti, rapor bloklari.

Ag ve yapay zeka YOK.
"""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.core.digital_audit import report_doc, research, source_check
from app.generators.ai_service import GroundedCompletion

SOURCE = {"url": "u1", "final_url": "https://kaynak.test/rapor", "kind": "html",
          "text": "Türkiye'de 2023 yılında 45 000 diş hekimi görev yaptı. Kişi başı başvuru 1,5 oldu. "
                  "Muayene ücreti 750,00 TL olarak açıklandı. Kurumun 3 şubesi var. Randevu almak çok zor diyen hastalar var."}


# --- kaynakta rakam arama ---------------------------------------------------

@pytest.mark.parametrize("value,status", [
    ("45.000", "found"),          # binlik ayraci kaynakta bosluk
    ("45 000", "found"),
    ("46.000", "not_found"),
    ("750", "not_found"),         # 750,00 ile 750 ayni sey degil
    ("750,00", "found"),
    ("1,5", "short"),             # kisa rakam: bulundu ama elle kontrol
    ("2023 yılında 750", "not_found"),
    ("yaklaşık çok", "no_number"),
    ("4500", "not_found"),        # 45 000'in parcasi eslesmez
    # Degerdeki rakamlarin hepsi aranir: ilkinin bulunmasi digerini rapora tasimaz
    ("45.000 ile 99.999 arası", "not_found"),
    ("45.000 ile 750,00 arası", "found"),
    ("45.000 hekim, kişi başı 1,5", "short"),
    ("5 şube ve 45.000 hekim", "not_found"),   # tek haneli rakam kaynakta yok
    ("3 şube ve 45.000 hekim", "short"),       # kaynakta var: analist onayina kalir
])
def test_check_claim_statuses(value, status):
    assert source_check.check_claim(value, [SOURCE])["status"] == status


def test_found_claim_carries_context_source_and_year_flag():
    hit = source_check.check_claim("45.000", [SOURCE], year="2023")
    assert hit["source_url"] == "https://kaynak.test/rapor"
    assert "diş hekimi" in hit["context"] and hit["year_in_context"] is True
    assert source_check.check_claim("45.000", [SOURCE], year="2019")["year_in_context"] is False


def test_dates_and_years_are_not_claims():
    assert source_check.numbers_in("12.11.2025 tarihli yönetmelik, 2024 yılında") == []
    assert source_check.text_numbers_verified("12.11.2025 tarihli yönetmelik", [SOURCE]) is None


def test_narrative_number_must_exist_in_source():
    assert source_check.text_numbers_verified("Sektörde 45.000 hekim var", [SOURCE]) is True
    assert source_check.text_numbers_verified("Sektörde 99.000 hekim var", [SOURCE]) is False
    assert source_check.text_numbers_verified("5 şubesi var", [SOURCE]) is False  # tek haneli de sessiz gecmez
    assert source_check.text_numbers_verified("5 şubesi var", []) is False
    assert source_check.text_numbers_verified("3 şubesi var", [SOURCE]) is True


def test_three_digit_number_is_only_a_candidate():
    """Canli denemede 'yaklasik 250' iddiasi kaynaktaki sayfa numarasiyla eslesti."""
    page = {**SOURCE, "text": "Uluslararası Dergi, 5(3), 242-266. - 250 - Elde edilen verilere göre"}
    assert source_check.check_claim("yaklaşık 250", [page])["status"] == "short"


def test_quote_must_be_verbatim():
    assert source_check.quote_found("“Randevu almak çok zor”", [SOURCE]) is True
    curly = {**SOURCE, "text": "toplam 250.000 TL’ye ... anlaştım. Alt çeneme yapılan implantlar söküldü"}
    assert source_check.quote_found("toplam 250.000 TL'ye anlaştım", [curly]) is False  # arada kesilmis metin
    assert source_check.quote_found("toplam 250.000 TL'ye... Alt çeneme yapılan implantlar", [curly]) is True
    assert source_check.quote_found("Randevu bulmak imkansız gibi", [SOURCE]) is False
    assert source_check.quote_found("zor", [SOURCE]) is False  # cok kisa ifade kanit sayilmaz


def test_pdf_and_unreadable_sources(monkeypatch):
    from app.core.url_guard import FetchBudget, FetchResult, HttpStatusError

    def fake_fetch(url, **kw):
        if "engel" in url:
            raise HttpStatusError(403)
        return FetchResult(final_url=url, status=200, content_type="application/pdf",
                           content_encoding="", text="", content=b"%PDF-bozuk")

    monkeypatch.setattr(source_check, "safe_fetch", fake_fetch)
    monkeypatch.setattr(source_check, "_pdf_text", lambda content: "Rapor 12.345 kayıt içerir" if content else "")
    sources = source_check.fetch_sources(["https://a.test/r.pdf", "https://engel.test/", "https://a.test/r.pdf"],
                                         FetchBudget(max_requests=5, total_seconds=30))
    assert [s["kind"] for s in sources] == ["pdf", "unreadable"]  # tekrar eden adres bir kez
    assert source_check.check_claim("12.345", sources)["status"] == "found"


# --- bolum kurulumu ---------------------------------------------------------

RAW_SECTOR = {
    "urls": ["u1"], "queries": ["diş hekimi sayısı"],
    "parsed": {
        "definition": ["Özel ağız ve diş sağlığı hizmetleri"],
        "services": ["İmplant", "Pazarın 99.000 kliniği var"],
        "growth": "Talep artıyor",
        "developments": [{"text": "Yeni yönetmelik yayımlandı", "date": "12.11.2025", "source_name": "Resmî Gazete"}],
        "restrictions": [{"text": "Ücretli öne çıkarma yasak (madde 77777)", "source_name": "Yönetmelik"}],
        "directories": ["doktortakvimi"], "not_found": ["Pazar büyüklüğü"],
        "claims": [
            {"claim": "Diş hekimi sayısı", "value": "45.000", "unit": "kişi", "year": "2023",
             "geography": "Türkiye", "source_name": "TDB"},
            {"claim": "Pazar büyüklüğü", "value": "3,2 milyar", "unit": "USD", "year": "2024",
             "geography": "Türkiye", "source_name": "Blog"},
        ],
    },
}


def test_sector_section_separates_verified_from_unverified():
    sector = research.build_sector(RAW_SECTOR, [SOURCE])
    assert [c["check"]["status"] for c in sector["claims"]] == ["found", "not_found"]
    assert [c["claim"] for c in research.verified_claims(sector)] == ["Diş hekimi sayısı"]
    assert research.verified_claims(sector, overrides=["sector:0"]) == []
    assert research.verified_claims(sector, overrides=["sector:1"]) == research.verified_claims(sector)  # bulunamayan girmez
    # Rakami dogrulanamayan anlati maddesi kullanilmaz; kisit ise isaretli olarak KALIR
    assert [i["text"] for i in research.usable(sector["services"])] == ["İmplant"]
    assert sector["restrictions"][0]["numbers_ok"] is False


def test_analyst_can_approve_a_short_number_after_reading_context():
    raw = {**RAW_SECTOR, "parsed": {**RAW_SECTOR["parsed"], "claims": [
        {"claim": "Kişi başı başvuru", "value": "1,5", "unit": "", "year": "2023", "geography": "Türkiye", "source_name": "TDB"}]}}
    sector = research.build_sector(raw, [SOURCE])
    assert sector["claims"][0]["check"]["status"] == "short"
    assert research.verified_claims(sector) == []
    assert [c["value"] for c in research.verified_claims(sector, overrides=["sector:0"])] == ["1,5"]


def test_unverified_sector_has_no_usable_claims():
    """Kaynaklar indirilmeden kaydedilen ham bolum: hicbir rakam dogrulanmis sayilmaz."""
    assert research.verified_claims(research.build_sector(RAW_SECTOR, [])) == []


def test_audience_quote_shown_only_when_verbatim():
    raw = {"urls": ["u1"], "queries": ["q"], "parsed": {
        "needs": [{"theme": "Randevu bulma", "quote": "Randevu almak çok zor", "source_name": "Forum"},
                  {"theme": "Fiyat belirsizliği", "quote": "Fiyatlar hiç yazmıyor", "source_name": "Sözlük"}],
        "hesitations": [], "questions": ["İmplant kaç yıl dayanır?"], "trust": [], "not_found": [],
        "claims": [{"claim": "Bir hastanın ödediği tutar", "value": "250.000", "unit": "TL", "year": "",
                    "geography": "", "source_name": "Şikayet sitesi"}]}}
    audience = research.build_audience(raw, [SOURCE], [{"keyword": "implant nasıl yapılır", "monthly_volume": 900}])
    assert [n["quote_found"] for n in audience["needs"]] == [True, False]
    assert audience["question_keywords"][0]["monthly_volume"] == 900
    assert "claims" not in audience  # anekdot tutarlar "dogrulanan rakam" olmaz


# --- aramali cagri ----------------------------------------------------------

class FakeAI:
    def __init__(self, grounded, json_text="{}"):
        self.grounded, self.json_text, self.calls = list(grounded), json_text, []

    def complete_grounded(self, prompt, max_tokens=4000, response_schema=None, **kw):
        self.calls.append("grounded")
        return self.grounded.pop(0)

    def complete_json(self, prompt, max_tokens=6000, response_schema=None, **kw):
        self.calls.append("json")
        return self.json_text


def test_grounded_call_without_sources_fails():
    empty = GroundedCompletion(text="hafızadan yazılmış metin", search_queries=[], evidence_urls=[])
    ai = FakeAI([empty, empty])
    with pytest.raises(research.ResearchError):
        research.grounded_json(ai, "p", research.SECTOR_SCHEMA, "alanlar", {})
    assert ai.calls == ["grounded", "grounded"]  # kaynaksiz metin semaya DOKULMEZ


def test_grounded_call_retries_when_model_skips_search():
    """Model aramayi atlayip hafizadan yazarsa (canli denemede goruldu) bir kez daha denenir."""
    skipped = GroundedCompletion(text="hafızadan", search_queries=[], evidence_urls=[])
    searched = GroundedCompletion(text="bulgular", search_queries=["q"], evidence_urls=["u1"])
    ai, usage = FakeAI([skipped, searched], json_text='{"definition": ["x"]}'), {}
    out = research.grounded_json(ai, "p", research.SECTOR_SCHEMA, "alanlar", usage)
    assert out["parsed"] == {"definition": ["x"]} and out["urls"] == ["u1"]
    assert ai.calls == ["grounded", "grounded", "json"]
    assert usage == {"grounded_calls": 2, "json_calls": 1, "search_queries": 1}


def test_grounded_call_retries_after_provider_error():
    """Saglayici yaniti keserse (canli: alinti tekrari gerekcesiyle bos yanit) bir kez daha denenir."""
    searched = GroundedCompletion(text="bulgular", search_queries=["q"], evidence_urls=["u1"])

    class Flaky(FakeAI):
        def complete_grounded(self, prompt, **kw):
            if not self.calls:
                self.calls.append("error")
                raise RuntimeError("finish_reason=RECITATION")
            return super().complete_grounded(prompt)

    ai, usage = Flaky([searched], json_text='{"needs": []}'), {}
    assert research.grounded_json(ai, "p", research.AUDIENCE_SCHEMA, "alanlar", usage)["urls"] == ["u1"]
    assert ai.calls == ["error", "grounded", "json"] and usage["grounded_calls"] == 2


def test_search_prompt_carries_no_output_format():
    """Aramali istemde sema/alan adi/JSON olursa model aramiyor: bicim yalniz ikinci cagrida."""
    seen = {}

    class Recording(FakeAI):
        def complete_grounded(self, prompt, max_tokens=4000, response_schema=None, **kw):
            seen["search"], seen["search_schema"] = prompt, response_schema
            return super().complete_grounded(prompt)

        def complete_json(self, prompt, max_tokens=6000, response_schema=None, **kw):
            seen["extract"], seen["extract_schema"] = prompt, response_schema
            return super().complete_json(prompt)

    searched = GroundedCompletion(text="ARAMA BULGUSU", search_queries=["q"], evidence_urls=["u1"])
    inputs = {"sector": "diş kliniği", "geography": "İstanbul", "focus": None}
    research.research_sector(Recording([searched], json_text='{"definition": ["x"]}'), inputs, {})
    assert seen["search_schema"] is None
    for word in ("JSON", "json", "claims", "not_found", "definition"):
        assert word not in seen["search"]
    assert seen["extract_schema"] is research.SECTOR_SCHEMA and "ARAMA BULGUSU" in seen["extract"]


# --- arama talebi -----------------------------------------------------------

def test_demand_summary_finds_peak_and_low_months():
    series = [{"year": 2025, "month": m, "searches": 300 if m == 7 else 100} for m in range(1, 13)]
    summary = research.summarize_demand([
        {"keyword": "a", "series": series, "bid_low": 2.0, "bid_high": 8.0},
        {"keyword": "b", "series": series, "bid_low": 4.0, "bid_high": 10.0},
    ])
    assert summary["peak_months"] == ["Temmuz"] and summary["low_months"] == []
    assert summary["bid_range"] == {"low_median": 3.0, "high_median": 9.0, "min": 2.0, "max": 10.0}


def test_google_ads_month_enum_becomes_calendar_month():
    """Google Ads'te OCAK=2, ARALIK=13: Ocak ve Aralik korunur, toplam degismez, sira tarihe gore."""
    raw = [{"year": 2026, "month": 2, "monthly_searches": 500}]  # Ocak 2026, sona ait ama basta gelmis
    raw += [{"year": 2025, "month": m, "monthly_searches": 100} for m in range(3, 14)]  # Subat-Aralik 2025
    raw += [{"year": 2025, "month": 1, "monthly_searches": 999}]  # UNKNOWN: atilir
    series = research.calendar_series(raw)
    assert [(p["year"], p["month"]) for p in series][:1] == [(2025, 2)] and series[-1] == {
        "year": 2026, "month": 1, "searches": 500}
    summary = research.summarize_demand([{"keyword": "a", "series": series}])
    by_month = {m["month"]: m["searches"] for m in summary["by_month"]}
    assert by_month["Ocak"] == 500 and by_month["Aralık"] == 100 and sum(by_month.values()) == 1600
    assert summary["peak_months"] == ["Ocak"] and "Ocak" not in summary["low_months"]


def test_demand_summary_without_series_is_empty():
    assert research.summarize_demand([{"keyword": "a", "series": []}])["by_month"] == []


# --- sentez ve briefler -----------------------------------------------------

FACTS = {"marka": {"ad": "Marka"}, "reklam_kisitlari": ["Ücretli öne çıkarma yasak"], "dogrulanmis_rakamlar": []}


def test_synthesis_flags_claims_without_evidence():
    ai = FakeAI([], json_text='{"segments": [{"name": "Acil hasta", "need": "Hızlı randevu", "stage": "karar", '
                              '"evidence": "soru aramaları", "assumption": "gece arıyor", '
                              '"value_proposition": "%50 daha hızlı randevu", "messages": ["Aynı gün randevu"]}], '
                              '"opportunities": [], "google_ads": {"keyword_groups": [], "negatives": [], "note": ""}, '
                              '"meta_audiences": []}')
    result = research.synthesize(ai, FACTS, {})
    assert result["segments"][0]["warnings"]  # bulgularda olmayan rakam isaretlenir


def test_synthesis_without_segments_fails():
    with pytest.raises(research.ResearchError):
        research.synthesize(FakeAI([], json_text='{"segments": []}'), FACTS, {})


def test_briefs_carry_restriction_note_when_sector_is_restricted():
    payload = ('{"ad_ideas": [{"segment": "Acil hasta", "channel": "Google Arama Ağı", "insight": "i", "offer": "o", '
               '"message": "Aynı gün randevu", "cta": "Ara", "test_hypothesis": "h"}], "creative_briefs": []}')
    restricted = research.generate_briefs(FakeAI([], payload), FACTS, [{"name": "Acil hasta"}], ["google_search"], {})
    assert restricted["ad_ideas"][0]["restriction_note"] == research.RESTRICTION_NOTE
    free = research.generate_briefs(FakeAI([], payload), {**FACTS, "reklam_kisitlari": []},
                                    [{"name": "Acil hasta"}], ["google_search"], {})
    assert free["ad_ideas"][0]["restriction_note"] is None


# --- rapor ------------------------------------------------------------------

def _report():
    sector = research.build_sector(RAW_SECTOR, [SOURCE])
    return SimpleNamespace(
        created_at=datetime(2026, 11, 15, tzinfo=timezone.utc), inputs={"sector": "Diş", "geography": "İstanbul"},
        claim_overrides=[], briefs=None,
        sections={"sector": sector, "synthesis": {
            "status": "ok", "opportunities": [], "google_ads": {}, "meta_audiences": [],
            "segments": [{"name": "Acil hasta", "stage": "karar", "need": "n", "evidence": "e", "assumption": "a",
                          "value_proposition": "v", "messages": ["m"], "warnings": []}]}},
    )


def test_report_contains_only_verified_numbers_and_marks_hypotheses():
    workspace = SimpleNamespace(name="Marka", company_url="https://marka.test")
    text = repr(report_doc.build_blocks(workspace, None, _report()))
    assert "45.000" in text and "https://kaynak.test/rapor" in text
    assert "3,2 milyar" not in text and "99.000" not in text
    assert "[Hipotez]" in text and "hukuki görüş değildir" in text
    assert "rakam kaynakta doğrulanamadı" in text  # kisit silinmez, isaretlenir


def test_report_drops_synthesis_and_briefs_made_before_a_claim_decision_changed():
    """Analist 45.000'i reddederse, o rakamla uretilmis segment ve brief rapora girmez."""
    workspace = SimpleNamespace(name="Marka", company_url="https://marka.test")
    report = _report()
    report.sections["synthesis"]["segments"][0]["evidence"] = "45.000 hekim var"
    report.sections["synthesis"]["claim_ids"] = ["sector:0"]
    report.briefs = {"status": "ok", "claim_ids": ["sector:0"], "creative_briefs": [], "ad_ideas": [
        {"segment": "Acil hasta", "channel": "Meta", "insight": "45.000 hekim", "offer": "o", "message": "m",
         "cta": "c", "test_hypothesis": "h", "warnings": [], "restriction_note": None}]}
    assert "45.000 hekim" in repr(report_doc.build_blocks(workspace, None, report))
    report.claim_overrides = ["sector:0"]
    assert research.synthesis_stale(report) and research.briefs_stale(report)
    text = repr(report_doc.build_blocks(workspace, None, report))
    assert "45.000" not in text and "Acil hasta" not in text and "güncel olmadığı" in text
    report.claim_overrides = []  # karar geri alinirsa cikti yeniden gecerlidir
    assert not research.synthesis_stale(report) and not research.briefs_stale(report)


def test_audit_facts_carry_result_not_just_check_name():
    """'Kontrol yapildi' ile 'sorun bulundu' ayrilir: noindex yokken modele 'kapali sayfa gozlendi' gitmez."""
    def f(label, state, assessment=None, value=None, note=None, limits=None):
        return {"section": "seo", "check": label, "label": label, "state": state, "assessment": assessment,
                "value": value, "note": note, "source": None, "limits": limits or []}

    audit = SimpleNamespace(answers={}, targets=[SimpleNamespace(url="https://marka.test", kind="own", findings=[
        f("Arama motorlarına kapalı sayfa", "observed", "ok", {"noindex_pages": []}),
        f("Açılış hızı (mobil)", "observed", "problem", {"available": True, "scores": {"performance": 15}, "raw": "x" * 900},
          limits=["Laboratuvar ölçümüdür."]),
        f("Sitemap", "unknown", note="okunamadı"),
    ])])
    site = research.audit_facts(audit)["siteler"][0]
    noindex, speed = site["kontroller"]
    assert noindex["sonuc"] == "iyi" and "noindex_pages" in noindex["deger"]
    assert speed["sonuc"] == "sorun" and '"performance": 15' in speed["deger"] and "xxx" not in speed["deger"]
    assert speed["sinirlar"] == ["Laboratuvar ölçümüdür."]
    assert site["olculemeyen"] == ["Sitemap"]


@pytest.mark.parametrize("fmt,magic", [("docx", b"PK"), ("pdf", b"%PDF")])
def test_report_renders_both_formats(fmt, magic):
    workspace = SimpleNamespace(name="Marka Çalışması", company_url="https://marka.test")
    content, media_type = report_doc.render(workspace, None, _report(), fmt)
    assert content.startswith(magic) and len(content) > 1500
