import pytest

from app.api.v1.brand_profile import (
    _apply_profile_review_data,
    _merge_exclude_themes,
    _normalize_seed_keywords,
    _parse_excluded_info,
    _sanitize_profile_data,
)
from app.core.site_analyzer.profile_extractor import ProfileExtractor
from app.schemas.brand_profile import WorkspaceKeywordApproveRequest


def test_parse_excluded_info_splits_commas_and_newlines_with_dedup():
    assert _parse_excluded_info("temettu, grafik\nborsa disi, temettu") == [
        "temettu",
        "grafik",
        "borsa disi",
    ]


def test_merge_exclude_themes_keeps_user_must_not_first():
    merged = _merge_exclude_themes("temettu, grafik", ["Grafik", "forex", ""])
    assert merged == ["temettu", "grafik", "forex"]


def test_normalize_seed_keywords_dedups_and_caps_at_twenty():
    keywords = [" Hisse Analiz ", "hisse analiz", "", *[f"kw {idx}" for idx in range(25)]]

    normalized = _normalize_seed_keywords(keywords)

    assert normalized[0] == "Hisse Analiz"
    assert len(normalized) == 20
    assert "hisse analiz" not in normalized[1:]


def test_workspace_keyword_approve_request_rejects_empty_list():
    with pytest.raises(ValueError):
        WorkspaceKeywordApproveRequest(keywords=[])


def test_workspace_keyword_approve_request_allows_twenty_keywords():
    request = WorkspaceKeywordApproveRequest(keywords=[f"kw {idx}" for idx in range(20)])
    assert len(request.keywords) == 20


def test_sanitize_profile_data_ignores_manual_anchor_override_and_removes_deleted_items():
    existing = {
        "company_name": "Hissefy",
        "sector": "Finansal teknoloji",
        "target_audience": "Yatirimcilar",
        "products": ["Analiz Platformu"],
        "services": [],
        "use_cases": ["Borsa takibi", "Portfoy izleme"],
        "problems_solved": ["Dagitik veri"],
        "brand_terms": ["Hissefy"],
        "exclude_themes": [],
        "anchor_texts": ["Borsa takibi Portfoy izleme"],
    }
    incoming = {
        "use_cases": ["Portfoy izleme"],
        "anchor_texts": ["MANUEL ANCHOR KULLANILMAMALI"],
    }

    sanitized = _sanitize_profile_data(incoming, existing)
    blob = " ".join(sanitized["anchor_texts"]).lower()

    assert "manuel anchor" not in blob
    assert "borsa takibi" not in blob
    assert "portfoy izleme" in blob


def test_sanitize_profile_data_filters_excluded_anchor_items():
    existing = {
        "company_name": "Hissefy",
        "sector": "Finansal teknoloji",
        "target_audience": "Yatirimcilar",
        "products": ["Analiz Platformu", "Kripto para sinyal araci"],
        "services": [],
        "use_cases": ["Borsa takibi"],
        "problems_solved": [],
        "brand_terms": ["Hissefy"],
        "exclude_themes": ["kripto para"],
        "anchor_texts": [],
    }

    sanitized = _sanitize_profile_data({}, existing)
    blob = " ".join(sanitized["anchor_texts"]).lower()

    assert "kripto" not in blob
    assert "analiz platformu" in blob


def test_apply_profile_review_data_allows_summary_and_audience_but_locks_identity():
    existing = {
        "company_name": "Hissefy",
        "sector": "Finansal teknoloji",
        "brand_summary": "Eski özet",
        "target_audience": "Yatirimcilar",
        "products": ["Analiz Platformu"],
        "services": [],
        "use_cases": ["Borsa takibi"],
        "problems_solved": [],
        "brand_terms": ["Hissefy"],
        "exclude_themes": [],
        "anchor_texts": [],
    }
    incoming = {
        "company_name": "SAHTE",
        "sector": "SAHTE",
        "brand_summary": "Yeni marka özeti",
        "target_audience": "BIST yatirimcilari",
        "use_cases": ["Portfoy izleme"],
        "anchor_texts": ["MANUEL ANCHOR"],
    }

    result = _apply_profile_review_data(incoming, existing)

    assert result["company_name"] == "Hissefy"
    assert result["sector"] == "Finansal teknoloji"
    assert result["brand_summary"] == "Yeni marka özeti"
    assert result["target_audience"] == "BIST yatirimcilari"
    blob = " ".join(result["anchor_texts"]).lower()
    assert "manuel anchor" not in blob
    assert "portfoy izleme" in blob
    assert "borsa takibi" not in blob


def test_suggest_keywords_from_profile_uses_profile_and_returns_cleaned_list():
    class FakeAI:
        def __init__(self):
            self.prompts = []

        def complete_json(self, prompt, max_tokens=2000, temperature=0.3, response_schema=None):
            self.prompts.append(prompt)
            return '{"suggested_keywords": [" hisse analiz ", "", "borsa takip"]}'

    ai = FakeAI()
    extractor = ProfileExtractor(ai)

    keywords = extractor.suggest_keywords_from_profile(
        "site içeriği",
        {"company_name": "Hissefy", "products": ["Analiz Platformu"], "anchor_texts": ["X"]},
        must_have_info="günlük hisse önerileri",
        excluded_info="kripto",
        competitor_terms=["Fintables"],
    )

    assert keywords == ["hisse analiz", "borsa takip"]
    prompt = ai.prompts[0]
    assert "Analiz Platformu" in prompt
    assert "günlük hisse önerileri" in prompt
    assert "Fintables" in prompt
    assert "anchor_texts" not in prompt  # teknik alan prompt'a sızmaz


def test_revise_profile_with_requirements_applies_must_have_and_keeps_excludes():
    class FakeAI:
        def complete_json(self, prompt, max_tokens=2000, temperature=0.3, response_schema=None):
            return """
            {
              "company_name": "Hissefy",
              "sector": "Finansal teknoloji",
              "brand_summary": "Revize özet",
              "products": ["Analiz Platformu", "Günlük Hisse Önerileri"],
              "services": [],
              "target_audience": "Yatirimcilar",
              "use_cases": ["Hisse takibi"],
              "problems_solved": [],
              "brand_terms": ["Hissefy"],
              "exclude_themes": ["AI DEGISTIRDI"]
            }
            """

    extractor = ProfileExtractor(FakeAI())
    original = {
        "company_name": "Hissefy",
        "products": ["Analiz Platformu"],
        "exclude_themes": ["kripto para"],
        "anchor_texts": ["X"],
    }

    revised = extractor.revise_profile_with_requirements(
        original, must_have_info="günlük hisse önerileri", excluded_info="kripto",
    )

    assert "Günlük Hisse Önerileri" in revised["products"]
    # exclude_themes sistemce yönetilir — AI'ın değişikliği geri alınır
    assert revised["exclude_themes"] == ["kripto para"]


def test_revise_profile_with_requirements_fails_open():
    class BrokenAI:
        def complete_json(self, prompt, max_tokens=2000, temperature=0.3, response_schema=None):
            raise RuntimeError("AI down")

    extractor = ProfileExtractor(BrokenAI())
    original = {"company_name": "Hissefy", "products": ["Analiz Platformu"]}

    revised = extractor.revise_profile_with_requirements(
        original, must_have_info="günlük hisse önerileri",
    )

    assert revised is original  # girdi profili değişmeden döner


def test_revise_profile_skipped_when_no_must_have():
    class NeverCalledAI:
        def complete_json(self, *args, **kwargs):
            raise AssertionError("must_have yokken AI çağrılmamalı")

    extractor = ProfileExtractor(NeverCalledAI())
    original = {"company_name": "Hissefy"}

    assert extractor.revise_profile_with_requirements(original, must_have_info="  ") is original


def test_extract_profile_from_keywords_retries_when_profile_json_is_truncated():
    class FakeAI:
        def __init__(self):
            self.max_tokens = []

        def complete_json(self, prompt, max_tokens=2000, temperature=0.3, response_schema=None):
            self.max_tokens.append(max_tokens)
            if len(self.max_tokens) == 1:
                return '{"company_name":"Hissefy","sector":"Finans'
            return """
            {
              "company_name": "Hissefy",
              "sector": "Finansal teknoloji",
              "products": ["Hisse analiz platformu"],
              "services": ["Yapay zeka destekli analiz"],
              "target_audience": "Yatirimcilar",
              "use_cases": ["Hisse takibi"],
              "problems_solved": ["Dagitik veriyi anlamlandirma"],
              "brand_terms": ["Hissefy"],
              "exclude_themes": ["forex"]
            }
            """

    ai = FakeAI()
    extractor = ProfileExtractor(ai)

    profile = extractor.extract_profile_from_keywords(
        "Hissefy hisse analizi yapan bir platformdur.",
        ["hisse analiz"],
    )

    assert ai.max_tokens == [4000, 6000]
    assert profile["company_name"] == "Hissefy"
    assert profile["anchor_texts"]
