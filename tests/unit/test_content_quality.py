"""SEO+GEO icerik kalitesi yardimcilarinin testleri.

Kapsam: internal link havuzu + backend dogrulama, external link format
kontrolu, brand context filtreleme, tek turluk revizyon dongusu guard'lari.
"""
import json
from types import SimpleNamespace

import pytest

from app.generators.seo_geo.content_quality import (
    build_internal_link_pool,
    build_link_pool_context,
    validate_internal_link,
    validate_external_link,
    is_valid_external_url,
    filter_brand_items_by_keyword,
)
from app.generators.seo_geo.seo_geo_generator import SEOGEOGenerator


def _profile(pages=None, company_url="https://vepafirca.com.tr"):
    return SimpleNamespace(source_pages=pages or [], company_url=company_url)


POOL_PAGES = [
    {"url": "https://vepafirca.com.tr/sac-fircalari", "title": "Saç Fırçaları", "status": 200},
    {"url": "https://vepafirca.com.tr/taraklar", "title": "Taraklar", "status": 200},
    {"url": "https://vepafirca.com.tr/404-sayfa", "title": "Olu", "status": 404},
]


# ── Internal link havuzu ─────────────────────────────────────────

def test_pool_includes_only_live_pages_plus_company_url():
    pool = build_internal_link_pool(_profile(POOL_PAGES))
    urls = [p["url"] for p in pool]
    assert "https://vepafirca.com.tr/sac-fircalari" in urls
    assert "https://vepafirca.com.tr/taraklar" in urls
    assert "https://vepafirca.com.tr" in urls          # company_url fallback
    assert "https://vepafirca.com.tr/404-sayfa" not in urls


def test_pool_dedups_schemeless_company_url_and_query_variants():
    pages = [
        {"url": "https://site.com/a?utm=1", "title": "A", "status": 200},
        {"url": "https://site.com/a#frag", "title": "A kopya", "status": 200},
        {"url": "https://site.com/b", "title": "B", "status": 0},  # status bilinmiyor → alinmaz
    ]
    pool = build_internal_link_pool(_profile(pages, company_url="site.com"))
    urls = [p["url"] for p in pool]
    # a'nin query/fragment varyantlari teklendi; semasiz company_url normalize edildi
    assert urls == ["https://site.com/a?utm=1", "https://site.com"]


def test_pool_context_lists_urls_and_empty_pool_gives_empty_context():
    pool = build_internal_link_pool(_profile(POOL_PAGES))
    ctx = build_link_pool_context(pool)
    assert "İZİNLİ INTERNAL LINK" in ctx
    assert "https://vepafirca.com.tr/taraklar" in ctx
    assert build_link_pool_context([]) == ""


def test_internal_link_exact_and_path_match_normalized():
    pool = build_internal_link_pool(_profile(POOL_PAGES))
    # Tam eslesme
    c = validate_internal_link({"internal_link_suggestion": "https://vepafirca.com.tr/taraklar"}, pool)
    assert c["internal_link_suggestion"] == "https://vepafirca.com.tr/taraklar"
    # AI path dondurdu → kanonik havuz URL'sine cevrilir
    c = validate_internal_link({"internal_link_suggestion": "/taraklar"}, pool)
    assert c["internal_link_suggestion"] == "https://vepafirca.com.tr/taraklar"


def test_hallucinated_internal_link_replaced_with_real_url():
    pool = build_internal_link_pool(_profile(POOL_PAGES))
    c = validate_internal_link(
        {"internal_link_suggestion": "/dogal-sac-fircasi-modelleri", "internal_link_anchor": "modeller"},
        pool,
    )
    assert c["internal_link_suggestion"] == pool[0]["url"]   # gercek URL
    assert c["internal_link_anchor"] == "modeller"           # anchor korunur


def test_missing_internal_link_filled_from_pool_and_empty_pool_noop():
    pool = build_internal_link_pool(_profile(POOL_PAGES))
    c = validate_internal_link({}, pool)
    assert c["internal_link_suggestion"] == pool[0]["url"]
    assert c["internal_link_anchor"]  # title'dan doldurulur
    # Havuz bos → dokunulmaz (eski davranis)
    c2 = validate_internal_link({"internal_link_suggestion": "/uydurma"}, [])
    assert c2["internal_link_suggestion"] == "/uydurma"


# ── External link format kontrolu ────────────────────────────────

@pytest.mark.parametrize("url,ok", [
    ("https://www.healthline.com/health/how-to-brush-your-hair", True),
    ("http://saglik.gov.tr/agiz-sagligi", True),
    ("https://guvenilir-kaynak.com/sayfa", False),   # prompt'taki placeholder
    ("https://example.com/x", False),
    ("/relative-path", False),
    ("ftp://dosya.com/a", False),
    ("", False),
    (None, False),
])
def test_external_url_format_validation(url, ok):
    assert is_valid_external_url(url) is ok


def test_invalid_external_link_cleared_but_valid_kept():
    c = validate_external_link({
        "external_link_url": "https://guvenilir-kaynak.com/sayfa",
        "external_link_anchor": "kaynak",
    })
    assert c["external_link_url"] is None and c["external_link_anchor"] is None
    c2 = validate_external_link({
        "external_link_url": "https://www.healthline.com/health/x",
        "external_link_anchor": "kaynak",
    })
    assert c2["external_link_url"] == "https://www.healthline.com/health/x"


# ── Brand context filtreleme ─────────────────────────────────────

VEPA_PRODUCTS = ["Doğal saç fırçası", "Diş fırçası", "Tarak", "Saç fırçası"]


def test_brand_items_filtered_by_keyword_topic():
    kept = filter_brand_items_by_keyword("saç açma tarağı", VEPA_PRODUCTS)
    assert "Diş fırçası" not in kept
    assert "Tarak" in kept
    assert "Doğal saç fırçası" in kept


def test_brand_items_no_match_returns_empty():
    # Konuyla hic ortusme yoksa kategori prompt'a hic girmemeli
    assert filter_brand_items_by_keyword("ağız duşu", ["Saç fırçası", "Tarak"]) == []


def test_brand_items_empty_inputs():
    assert filter_brand_items_by_keyword("saç tarağı", None) == []
    assert filter_brand_items_by_keyword("", VEPA_PRODUCTS) == VEPA_PRODUCTS


# ── Tek turluk revizyon dongusu ──────────────────────────────────

class QueueAI:
    """Sirayla verilen yanitlari donduren sahte AI."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def complete_json(self, prompt=None, **kwargs):
        self.calls += 1
        return self.responses.pop(0)


def _make_generator(ai):
    gen = SEOGEOGenerator.__new__(SEOGEOGenerator)  # __init__ atlanir (db gerekmez)
    gen.db = None
    gen.ai_service = ai
    return gen


PASSING_SEO = {k: True for k in SEOGEOGenerator._SEO_CRITERIA_TR}
PASSING_SEO.update({"intro_keyword_count": 2, "score": 1.0, "improvement_notes": ""})
PASSING_GEO = {k: True for k in SEOGEOGenerator._GEO_CRITERIA_TR}
PASSING_GEO.update({"score": 1.0, "improvement_notes": ""})


def test_no_revision_when_all_criteria_pass():
    ai = QueueAI([])
    gen = _make_generator(ai)
    content = {"title": "t"}
    out, seo, geo = gen._maybe_revise_once(
        keyword="x", content=content,
        seo_result=dict(PASSING_SEO), geo_result=dict(PASSING_GEO),
        word_count_min=300, word_count_max=450, link_pool=[],
    )
    assert out is content and ai.calls == 0  # AI'a hic gidilmedi


def test_revision_regression_guard_keeps_original():
    """Revize icerik daha kotu skor alirsa orijinal korunmali."""
    failing_seo = dict(PASSING_SEO, has_external_link=False, score=0.91)
    revised = {
        "title": "t", "intro_paragraph": "i",
        "subheadings": ["a"], "body_sections": ["b"],
    }
    ai = QueueAI([json.dumps(revised)])
    gen = _make_generator(ai)
    gen.seo_checker = SimpleNamespace(check=lambda **kw: dict(PASSING_SEO, score=0.5))
    gen.geo_checker = SimpleNamespace(check=lambda **kw: dict(PASSING_GEO, score=0.5))

    original = {"title": "orijinal", "intro_paragraph": "x",
                "subheadings": ["s"], "body_sections": ["b"]}
    out, seo, geo = gen._maybe_revise_once(
        keyword="x", content=original,
        seo_result=failing_seo, geo_result=dict(PASSING_GEO),
        word_count_min=300, word_count_max=450, link_pool=[],
    )
    assert ai.calls == 1                 # revizyon denendi
    assert out is original               # ama skor geriledigi icin reddedildi
    assert seo is failing_seo


def test_revision_adopted_when_score_improves():
    failing_seo = dict(PASSING_SEO, has_external_link=False, score=0.91)
    revised = {
        "title": "yeni", "intro_paragraph": "i",
        "subheadings": ["a"], "body_sections": ["b"],
    }
    ai = QueueAI([json.dumps(revised)])
    gen = _make_generator(ai)
    gen.seo_checker = SimpleNamespace(check=lambda **kw: dict(PASSING_SEO, score=1.0))
    gen.geo_checker = SimpleNamespace(check=lambda **kw: dict(PASSING_GEO, score=1.0))

    out, seo, geo = gen._maybe_revise_once(
        keyword="x", content={"title": "orijinal"},
        seo_result=failing_seo, geo_result=dict(PASSING_GEO),
        word_count_min=300, word_count_max=450, link_pool=[],
    )
    assert out["title"] == "yeni"
    assert seo["score"] == 1.0


def test_revision_ai_failure_is_fail_open():
    class BoomAI:
        def complete_json(self, *a, **k):
            raise RuntimeError("quota")

    gen = _make_generator(BoomAI())
    failing_seo = dict(PASSING_SEO, has_bullet_list=False, score=0.91)
    original = {"title": "orijinal"}
    out, seo, geo = gen._maybe_revise_once(
        keyword="x", content=original,
        seo_result=failing_seo, geo_result=dict(PASSING_GEO),
        word_count_min=300, word_count_max=450, link_pool=[],
    )
    assert out is original


def test_collect_failed_criteria_maps_both_checkers():
    gen = _make_generator(None)
    seo = dict(PASSING_SEO, has_internal_link=False, intro_keyword_count=1)
    geo = dict(PASSING_GEO, no_fluff_content=False)
    failed = gen._collect_failed_criteria(seo, geo)
    joined = " ".join(failed)
    assert "[SEO] Internal link eksik" in failed
    assert "keyword 2 kereden az" in joined
    assert "[GEO] Dolgu/tekrar eden cümleler var" in failed
