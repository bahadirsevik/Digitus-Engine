"""İddia denetimi rev.4 birim testleri (plan_iddia_denetimi.md): sınıflandırma, RSA incelemesi,
N3 kayıt biçimi, export gösterimi. DB gerektirmez."""
import json

from app.generators.ads.rsa_generator import RSAGenerator
from app.generators.ads.validators import (
    REASON_RISK_TR,
    REASON_UNVERIFIED_TR,
    build_standard_report,
    classify_claims,
    find_ungrounded_claims,
)
from app.generators.claim_review import (
    MAX_HISTORY,
    ads_set_warnings,
    changed_items,
    make_current,
    push_review,
    refresh_review,
    review_summary,
    set_needs_review,
    set_review,
    social_warning_display,
    with_set_review,
)
from app.schemas.ads import KeywordGroupSchema

GROUP = KeywordGroupSchema(
    name="Kanal", theme="t", keyword_ids=[1, 2], keywords=["kanal tedavisi", "diş kanal"],
)


# ---------------- validator: sınıflandırma ----------------

def test_classify_kinds_and_reasons():
    risk = classify_claims("Kazanç garantisi ve ağrısız tedavi", "")
    assert {(f.category, f.kind) for f in risk} == {("guarantee", "risk"), ("absolute", "risk")}
    assert all(f.reason_tr == REASON_RISK_TR for f in risk)

    unv = classify_claims("%80 başarı, en iyi lazer, ISO belgeli", "")
    assert {f.category for f in unv} == {"numeric", "superlative", "service", "cert"}
    assert all(f.kind == "unverified" and f.reason_tr == REASON_UNVERIFIED_TR for f in unv)


def test_find_ungrounded_claims_is_derived_from_classify_same_strings():
    text = "Kazanç garantisi, %80 başarı, lazer, ağrısız"
    assert find_ungrounded_claims(text, "") == [f.raw for f in classify_claims(text, "")]


def test_bare_dolgu_is_not_a_general_grant():
    # Codex: çıplak 'dolgu' izin sayılmaz — dental_filling alias'ı değil
    assert [f.category for f in classify_claims("Пломбування каналів", "Hizmetler: dolgu")] == ["service"]


def test_translation_ambiguity_warns_but_text_untouched():
    """Kabul 2: kaynak 'Kanal Tedavisi', metin 'пломбування каналів' → unverified (silinmez)."""
    findings = classify_claims("пломбування каналів", "Hizmetler: Kanal Tedavisi")
    assert [(f.kind, f.category) for f in findings] == [("unverified", "service")]


def test_recognised_alias_clears_and_risk_ignores_source():
    facts = "Hizmetler: Diş dolgusu, Kazanç garantisi"
    assert classify_claims("Пломбування каналів", facts) == []  # tanınan alias
    risk = classify_claims("Kazanç garantisi veriyoruz", facts)  # kaynakta geçse de risk
    assert [f.kind for f in risk] == ["risk"]


# ---------------- RSA ----------------

def _rsa_json(headlines, descriptions=None):
    return json.dumps({
        "headlines": [{"text": t, "type": "benefit", "position": "any"} for t in headlines],
        "descriptions": [
            {"text": t, "type": "value_prop", "position": "any"}
            for t in (descriptions or ["Kanal tedavisi için hemen arayın.", "Uzman kadroyla hizmet alın."])
        ],
        "negative_keywords": [
            {"keyword": f"neg{i}", "match_type": "phrase", "category": "bilgi_amacli", "reason": "r"}
            for i in range(10)
        ],
        "keyword_headlines": [],
    })


class CountingAI:
    def __init__(self, json_response, rewrite=""):
        self.json_response, self.rewrite = json_response, rewrite
        self.json_calls, self.complete_calls = 0, 0

    def complete_json(self, prompt, max_tokens=None, response_schema=None):
        self.json_calls += 1
        if self.json_calls > 1:
            return json.dumps({"headlines": [], "descriptions": [], "negative_keywords": [], "keyword_headlines": []})
        return self.json_response

    def complete(self, prompt, max_tokens=None):
        self.complete_calls += 1
        return self.rewrite


def test_rsa_profile_missing_service_survives_with_zero_extra_ai_calls():
    """Kabul 1 (RSA): 'Пломбування каналів' asset'i kalır + claim_review; rewrite çağrısı YOK."""
    ai = CountingAI(_rsa_json([SERVICE := "Пломбування каналів", "Kanal Tedavisi Uzmanı", "Hemen Arayın", "Randevu Alın"]),
                    rewrite="kullanılmamalı")
    gen = RSAGenerator(ai_service=ai, enable_ai_regeneration=True, enable_fallback=False)

    result = gen.generate_rsa(GROUP, brand_name="Marka", brand_usp="", grounding_facts="Hizmetler: Kanal Tedavisi")

    by_text = {h.text: h for h in result.headlines}
    assert by_text[SERVICE].validation_action == "claim_review"
    assert ai.complete_calls == 0  # rewrite/ek AI çağrısı yok
    finding = result.claim_findings[0]
    assert (finding["kind"], finding["category"], finding["asset_type"]) == ("unverified", "service", "headline")
    assert finding["reason"] == REASON_UNVERIFIED_TR
    assert finding["asset_text"] == SERVICE


def test_rsa_risk_rewrite_keeps_original_and_suggestion_and_flags_nothing_when_clean():
    """Kabul 3 (RSA): B rewrite'ında orijinal + öneri korunur; kalan bulgu yoksa asset işaretsiz."""
    ai = CountingAI(_rsa_json(["Kazanç Garantisi Sunuyoruz", "Kanal Tedavisi Uzmanı", "Hemen Arayın", "Randevu Alın"]),
                    rewrite="Güvenilir Kanal Tedavisi")
    gen = RSAGenerator(ai_service=ai, enable_ai_regeneration=True, enable_fallback=False)

    result = gen.generate_rsa(GROUP, brand_name="Marka", brand_usp="", grounding_facts="")

    assert ai.complete_calls == 1
    assert "Güvenilir Kanal Tedavisi" in [h.text for h in result.headlines]
    assert result.claim_findings == []
    applied = result.claim_applied[0]
    assert (applied["original"], applied["suggestion"]) == ("Kazanç Garantisi Sunuyoruz", "Güvenilir Kanal Tedavisi")


def test_rsa_30_char_limit_still_eliminates_class_a():
    """Kabul 4: teknik 30/90 karakter kontrolü değişmez (sınıf A: ele)."""
    too_long = "Çok Uzun Bir Başlık Metni Burada 31+"  # > 30
    ai = CountingAI(_rsa_json([too_long, "Kanal Tedavisi Uzmanı", "Hemen Arayın", "Randevu Alın"]))
    gen = RSAGenerator(ai_service=ai, enable_ai_regeneration=False, enable_fallback=False)

    result = gen.generate_rsa(GROUP, brand_name="Marka", brand_usp="", grounding_facts="")

    assert all(len(h.text) <= 30 for h in result.headlines)
    assert too_long not in [h.text for h in result.headlines]


def test_standard_report_claim_reason_only_for_risk_keywords():
    report = build_standard_report(
        ["kazanç garantisi kanal", "en iyi kanal tedavisi"], [1, 2], [], ["Başlık"], ["Açıklama metni"], "Türkçe",
    )
    reasons = report.unused_keyword_reasons
    assert reasons["kazanç garantisi kanal"] == "claim"
    assert reasons["en iyi kanal tedavisi"] == "not_used"  # süperlatif = unverified → 'claim' sebebi DEĞİL


# ---------------- N3 kayıt biçimi ----------------

def test_push_review_keeps_history_max_five_and_replaces_current():
    record = None
    for i in range(8):
        record = push_review(record, make_current([{"kind": "risk", "span": f"s{i}"}], evaluated_at=f"t{i}"))
    assert record["current"]["evaluated_at"] == "t7"
    assert [h["evaluated_at"] for h in record["history"]] == ["t2", "t3", "t4", "t5", "t6"]
    assert len(record["history"]) == MAX_HISTORY


def test_set_warnings_keep_other_entries_and_flag_review():
    drop = {"group": "g", "negative": "n"}
    warnings = ads_set_warnings([drop], [{"kind": "unverified", "span": "x"}], [])
    assert warnings[0] == drop and set_needs_review(warnings)
    summary = review_summary(warnings)
    assert (summary["review_required"], summary["claim_risk_count"], summary["claim_unverified_count"]) == (True, 0, 1)
    assert summary["claim_review"]["findings"] == [{"kind": "unverified", "span": "x"}]
    assert review_summary([drop])["claim_review"] is None
    # claim_review yalnız uygulanan düzeltme içeriyorsa (findings boş) işaret değildir
    assert not set_needs_review(ads_set_warnings([], [], [{"kind": "risk", "original": "a", "suggestion": "b"}]))
    # değiştirme diğer uyarıları korur
    replaced = with_set_review(warnings, push_review(set_review(warnings), make_current([])))
    assert replaced[0] == drop and len(replaced) == 2


def test_social_warning_display_hides_record_and_translates_flags():
    shown = social_warning_display(["claim_risk:x", "claim_unverified:y", 'claim_review:{"a":1}', "duration_mismatch"])
    assert shown == ["Risk — yayın öncesi düzelt: x", "Profilde doğrulanamadı — kontrol gerekli: y", "duration_mismatch"]


def test_refresh_review_noop_when_finding_keys_unchanged_else_push():
    f1 = {"kind": "risk", "category": "guarantee", "span": "garanti"}
    f2 = {"kind": "unverified", "category": "service", "span": "lazer"}
    record = push_review(None, make_current([f1, f2], evaluated_at="t0"))
    same = refresh_review(record, make_current([f2, f1], evaluated_at="t1"))
    assert same["history"] == [] and same["current"]["evaluated_at"] == "t1"   # no-op: yalnız zaman damgası
    changed = refresh_review(same, make_current([f1], evaluated_at="t2"))
    assert len(changed["history"]) == 1 and changed["history"][0]["evaluated_at"] == "t1"


def test_risk_failure_text_carries_readable_reason_like_unverified():
    from app.generators.claim_review import failure_text

    text = failure_text([{"kind": "risk", "span": "ağrisiz"}, {"kind": "unverified", "span": "lazer"}])
    assert text == (
        "risk: ağrisiz (Açık sonuç/kazanç vaadi — yayın öncesi düzelt); "
        "unverified: lazer (Profilde doğrulanamadı — kontrol gerekli)")


def test_changed_items_keep_full_text_no_truncation():
    """Codex rev.4: kayit TAM metni tutar (400 kesmesi kaldirildi); kisaltma yalniz gosterimde."""
    long_old = "A" * 691
    long_new = "B" * 650
    out = changed_items({"caption": long_old, "hook[0]": "ayni"}, {"caption": long_new, "hook[0]": "ayni"})
    assert out == [{"field": "caption", "original": long_old, "suggestion": long_new}]
    cur = make_current([], changed=out)
    assert len(cur["changed"][0]["original"]) == 691


def test_refresh_review_carries_repair_facts_when_findings_change():
    f1 = {"kind": "risk", "category": "guarantee", "span": "garanti"}
    f2 = {"kind": "unverified", "category": "service", "span": "lazer"}
    first = make_current([f1, f2], [{"original": "a", "suggestion": "b"}], evaluated_at="t0",
                         changed=[{"field": "caption", "original": "x", "suggestion": "y"}])
    first["original"] = {"intro_paragraph": "eski"}
    record = push_review(None, first)

    changed = refresh_review(record, make_current([f1], evaluated_at="t1"))

    cur = changed["current"]
    assert [f["kind"] for f in cur["findings"]] == ["risk"]
    assert cur["applied"] == first["applied"] and cur["changed"] == first["changed"]
    assert cur["original"] == {"intro_paragraph": "eski"}
    assert changed["history"][0]["evaluated_at"] == "t0"
    # açıkça verilen değer taşınanı ezer
    explicit = refresh_review(changed, make_current([], [{"original": "yeni"}], evaluated_at="t2"))
    assert explicit["current"]["applied"] == [{"original": "yeni"}]
