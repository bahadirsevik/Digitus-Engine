# -*- coding: utf-8 -*-
"""Cache OKUMA KAPISI regresyon testleri.

Arka plan: crawler duzeltmesi `extract_profile_from_site_content` icine kalite kapisi
koydu; fakat `crawl_content_cache` DOLU oldugunda iki akis (keyword onerisi ve profil
uretimi) crawl'i tamamen atlayip cache'i DOGRUDAN AI'a veriyordu. Gecmiste brotli
nedeniyle bozulmus cache'ler bu yoldan kapiyi asiyordu.

Sozlesme (dort kural):
  1. Gecerli cache KULLANILIR (yeniden crawl YOK).
  2. Bozuk cache YENIDEN TARANIR (cache silinmez).
  3. Yeniden tarama da basarisizsa AI CAGRILMAZ, acik hata doner.
  4. Basarili yeniden tarama cache'i YENILER.
"""
import pytest

from app.core.site_analyzer.profile_extractor import resolve_site_content

GOOD = "Dis klinigimiz implant, ortodonti ve zirkonyum kaplama tedavileri sunar. " * 12
GOOD2 = "Yeni taranan icerik: cerrahi onkoloji, mide ve kolon kanseri tedavisi. " * 12
GARBAGE = "�� ��q9IY@v�g�" * 60


class _FakeExtractor:
    """crawl_for_profile_content cagrilarini sayan sahte extractor."""

    def __init__(self, result):
        self._result = result
        self.crawl_calls = 0

    def crawl_for_profile_content(self, company_url):
        self.crawl_calls += 1
        return self._result


def test_valid_cache_is_used_without_recrawl():
    """1. Gecerli cache kullanilir; crawl HIC cagrilmaz."""
    ex = _FakeExtractor({"site_content": GOOD2, "source_pages": [{"url": "x"}], "error": None})
    content, pages, error = resolve_site_content(ex, "https://example.test/", GOOD)

    assert error is None
    assert content == GOOD.strip(), "cache aynen (strip'li) dondurulmeli"
    assert ex.crawl_calls == 0, "gecerli cache varken yeniden crawl YAPILMAMALI"
    assert pages is None, "source_pages None olmali -> cagiran cache'i YENILEMEZ"


def test_corrupt_cache_triggers_recrawl():
    """2. Bozuk cache yeniden taranir ve taze icerik kullanilir."""
    ex = _FakeExtractor({"site_content": GOOD2, "source_pages": [{"url": "a"}], "error": None})
    content, pages, error = resolve_site_content(ex, "https://example.test/", GARBAGE)

    assert error is None
    assert ex.crawl_calls == 1, "bozuk cache yeniden crawl TETIKLEMELI"
    assert content == GOOD2, "bozuk cache DEGIL, taze icerik kullanilmali"
    assert GARBAGE not in content


def test_failed_recrawl_returns_error_and_never_calls_ai():
    """3. Yeniden tarama da basarisizsa AI'a hicbir sey gitmez."""
    ex = _FakeExtractor({"site_content": "", "source_pages": [],
                         "error": "crawl_content_unusable: binary_or_undecoded"})
    content, pages, error = resolve_site_content(ex, "https://example.test/", GARBAGE)

    assert ex.crawl_calls == 1
    assert error is not None and "crawl_content_unusable" in error
    assert content == "", "AI'a gidecek icerik BOS olmali"
    assert pages is None, "source_pages None -> eski cache KORUNUR, uzerine yazilmaz"


def test_successful_recrawl_refreshes_cache():
    """4. Basarili yeniden tarama cache yenilemesini tetikler."""
    fresh_pages = [{"url": "https://example.test/hizmetler", "title": "Hizmetler"}]
    ex = _FakeExtractor({"site_content": GOOD2, "source_pages": fresh_pages, "error": None})
    content, pages, error = resolve_site_content(ex, "https://example.test/", GARBAGE)

    assert error is None
    assert pages == fresh_pages, "source_pages dolu -> cagiran cache'i YENILER"
    assert content == GOOD2


# --------------------------------------------------------------- ek kenar durumlar
def test_empty_cache_triggers_crawl():
    ex = _FakeExtractor({"site_content": GOOD2, "source_pages": [{"url": "a"}], "error": None})
    content, pages, error = resolve_site_content(ex, "https://example.test/", "")
    assert ex.crawl_calls == 1 and error is None and content == GOOD2


def test_none_cache_triggers_crawl():
    ex = _FakeExtractor({"site_content": GOOD2, "source_pages": [{"url": "a"}], "error": None})
    content, pages, error = resolve_site_content(ex, "https://example.test/", None)
    assert ex.crawl_calls == 1 and error is None


def test_too_short_cache_is_treated_as_corrupt():
    """JS kabugu ('Yukleniyor...') cache'e yazilmis olabilir."""
    ex = _FakeExtractor({"site_content": GOOD2, "source_pages": [{"url": "a"}], "error": None})
    content, pages, error = resolve_site_content(ex, "https://example.test/", "Yukleniyor...")
    assert ex.crawl_calls == 1, "cok kisa cache yeniden crawl tetiklemeli"
    assert content == GOOD2


def test_recrawl_producing_garbage_is_also_rejected():
    """Yeniden tarama cop dondururse de AI'a gitmez."""
    ex = _FakeExtractor({"site_content": "", "source_pages": [], "error": "crawl_content_unusable"})
    content, pages, error = resolve_site_content(ex, "https://example.test/", GARBAGE)
    assert error is not None and content == ""
