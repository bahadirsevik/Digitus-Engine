"""15 KW teslim standardı raporu (plan_ads_15kw_standardi §4) — DB'siz birim testi.

build_standard_report: sayılar, standart altı kuralı (KW kapsam eksikliği
SAYILMAZ, K4), havuz notu, fill_keywords sırası, NULL fill; AdGroupDBSchema
serileştirmesi.
"""
import json
from datetime import datetime

from app.generators.ads.validators import build_standard_report
from app.schemas.ads import (
    AdGroupDBSchema,
    AdGroupFullSchema,
    DescriptionDBSchema,
    HeadlineDBSchema,
)

KWS = ["ucuz laptop", "oyuncu laptop", "ofis laptop", "tablet"]
IDS = [1, 2, 3, 4]
GENERIC_H = [f"Aday Başlık {i}" for i in range(15)]
GENERIC_D = ["Hemen keşfedin.", "Fırsatları kaçırmayın.", "Şimdi inceleyin.", "Sipariş verin."]


def test_counts_lists_and_below_standard():
    headlines = GENERIC_H[:14] + ["Ucuz Laptop Fırsatı"]
    descriptions = ["Oyuncu laptop modellerini inceleyin.", "Ucuz laptop için tıklayın.",
                    GENERIC_D[0], GENERIC_D[1]]
    r = build_standard_report(KWS, IDS, [3], headlines, descriptions)

    assert (r.keyword_count, r.headline_count, r.kw_description_count) == (4, 15, 2)
    assert (r.missing_headlines, r.missing_kw_descriptions) == (0, 2)
    assert r.below_standard is True
    assert r.pool_below_target is True                     # 4 < 15
    assert r.keywords_in_headlines == ["ucuz laptop"]
    assert r.keywords_description_only == ["oyuncu laptop"]
    assert r.keywords_unused == ["ofis laptop", "tablet"]
    assert r.fill_keywords == ["ofis laptop"]


def test_missing_headlines_make_group_below_standard():
    r = build_standard_report(KWS, IDS, [], GENERIC_H[:11], ["Ucuz laptop 1", "Ucuz laptop 2",
                                                            "Ucuz laptop 3", "Ucuz laptop 4"])
    assert (r.missing_headlines, r.missing_kw_descriptions) == (4, 0)
    assert r.below_standard is True


def test_incomplete_keyword_coverage_alone_is_not_below_standard():
    kws = [f"kelime {i}" for i in range(1, 16)]
    descriptions = [f"{kws[i]} seçeneklerini inceleyin." for i in range(4)]
    r = build_standard_report(kws, list(range(1, 16)), None, GENERIC_H, descriptions)

    assert (r.missing_headlines, r.missing_kw_descriptions) == (0, 0)
    assert r.below_standard is False                       # KW'ler başlıkta yok ama K4: hata değil
    assert r.pool_below_target is False                    # tam 15 KW
    assert r.keywords_in_headlines == []
    assert len(r.keywords_description_only) == 4 and len(r.keywords_unused) == 11


def test_null_or_empty_fill_gives_no_fill_keywords():
    for fill in (None, []):
        assert build_standard_report(KWS, IDS, fill, GENERIC_H, GENERIC_D).fill_keywords == []


def test_fill_keywords_follow_target_keyword_order_not_fill_order():
    r = build_standard_report(KWS, IDS, [3, 1], GENERIC_H, GENERIC_D)
    assert r.fill_keywords == ["ucuz laptop", "ofis laptop"]


def test_ids_not_in_target_are_ignored_and_empty_group_is_safe():
    assert build_standard_report(KWS, IDS, [99], [], []).fill_keywords == []
    r = build_standard_report([], [], None, [], [])
    assert (r.keyword_count, r.headline_count, r.missing_headlines) == (0, 0, 15)
    assert r.below_standard and r.pool_below_target


def _db_group(**extra):
    return AdGroupDBSchema(
        id=1, group_name="G", group_theme="t",
        target_keyword_ids=IDS, target_keywords=KWS,
        headlines=[HeadlineDBSchema(id=i, text=t, type="benefit")
                   for i, t in enumerate(GENERIC_H[:3], 1)],
        descriptions=[DescriptionDBSchema(id=1, text=GENERIC_D[0], type="value_prop")],
        negative_keywords=[],
        headlines_generated=3, headlines_eliminated=0, dki_converted_count=0,
        created_at=datetime(2026, 10, 17), **extra,
    )


def test_db_schema_serializes_fill_ids_and_report_without_percentages():
    report = build_standard_report(KWS, IDS, [3], GENERIC_H[:3], GENERIC_D[:1])
    dumped = _db_group(fill_keyword_ids=[3], standard_report=report).model_dump()

    assert dumped["fill_keyword_ids"] == [3]
    assert set(dumped["standard_report"]) == {
        "keyword_count", "headline_count", "kw_description_count", "missing_headlines",
        "missing_kw_descriptions", "below_standard", "pool_below_target",
        "keywords_in_headlines", "keywords_description_only", "keywords_unused",
        "fill_keywords",
    }
    assert dumped["standard_report"]["fill_keywords"] == ["ofis laptop"]
    assert "%" not in json.dumps(dumped["standard_report"], ensure_ascii=False)


def test_db_schema_defaults_for_legacy_rows():
    dumped = _db_group().model_dump()
    assert dumped["fill_keyword_ids"] == [] and dumped["standard_report"] is None


def test_full_schema_carries_fill_ids_unlike_shortfall_fields():
    g = AdGroupFullSchema(name="a", theme="t", keyword_ids=[1, 2], keywords=["a", "b"],
                          fill_keyword_ids=[2], headlines=[], descriptions=[],
                          negative_keywords=[], missing_headlines=3)
    dumped = g.model_dump()
    assert dumped["fill_keyword_ids"] == [2]               # kalıcı alan: serileştirilir
    assert "missing_headlines" not in dumped               # worker'a özel: hâlâ gizli
    assert AdGroupFullSchema(name="a", theme="t", keyword_ids=[], keywords=[], headlines=[],
                             descriptions=[], negative_keywords=[]).fill_keyword_ids == []
