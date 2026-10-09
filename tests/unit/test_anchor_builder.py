"""Anchor üretimi + exclude_themes ayıklama (plan5 B4b/C1) birim testleri."""
import pytest

from app.core.site_analyzer.anchor_builder import (
    build_anchor_texts,
    filter_items_by_exclude,
    item_is_excluded,
    _theme_stem_sets,
)

# Hissefy senaryosu (run #11 profil #9)
HISSEFY = {
    "company_name": "Hissefy",
    "sector": "Finansal Teknoloji ve Borsa Analiz Yazılımları",
    "target_audience": "BIST yatırımcıları",
    "products": [
        "Hisse Senedi Analiz Platformu",
        "Portföy Takip Sistemi",
        "Temettü Takip Ekranı",   # dışlanmalı (temettü takibi)
    ],
    "use_cases": [
        "Teknik analiz araçlarıyla inceleme",
        "Hisse grafikleri gösterimi",  # dışlanmalı (hisse grafikleri)
    ],
    "problems_solved": ["Karmaşık borsa verilerinin takibi"],
    "brand_terms": ["Hissefy", "Hissefy borsa"],
    "exclude_themes": ["temettü takibi", "hisse grafikleri", "kripto para"],
}


# ── item_is_excluded ──────────────────────────────────────────────

def test_multiword_theme_requires_all_stems():
    themes = _theme_stem_sets(["temettü takibi"])
    # Meşru ürün: yalnızca "takip" ortak, "temett" yok → SİLİNMEZ
    assert not item_is_excluded("Portföy Takip Sistemi", themes)
    # Temettü + takip ikisi de var → SİLİNİR
    assert item_is_excluded("Temettü Takip Ekranı", themes)


def test_consonant_softening_prefix_overlap():
    themes = _theme_stem_sets(["hisse grafikleri"])
    # "grafik" vs "grafikleri" / "grafiği" ortak önek ≥4 → yakalanır
    assert item_is_excluded("Hisse grafiği gösterimi", themes)


def test_no_exclude_themes_keeps_all():
    assert filter_items_by_exclude(["a", "b"], []) == ["a", "b"]
    assert filter_items_by_exclude(["a", "b"], None) == ["a", "b"]


def test_filter_drops_only_overlapping():
    kept = filter_items_by_exclude(HISSEFY["products"], HISSEFY["exclude_themes"])
    assert "Hisse Senedi Analiz Platformu" in kept
    assert "Portföy Takip Sistemi" in kept       # meşru, korunur
    assert "Temettü Takip Ekranı" not in kept    # dışlandı


def test_empty_and_nonstring_items():
    assert filter_items_by_exclude(None, ["x"]) == []
    assert filter_items_by_exclude(["", "  ", "gerçek"], ["x"]) == ["gerçek"]


# ── build_anchor_texts ────────────────────────────────────────────

def test_build_anchor_texts_drops_excluded_content():
    anchors = build_anchor_texts(HISSEFY)
    blob = " ".join(anchors).lower()
    # Dışlanan temalar anchor metnine sızmamalı
    assert "temettü takip" not in blob and "temettu takip" not in blob
    assert "grafik" not in blob
    # Meşru ürünler kalmalı
    assert "portföy takip" in blob or "portfoy takip" in blob.lower()
    assert "hissefy" in blob


def test_build_anchor_texts_structure():
    anchors = build_anchor_texts(HISSEFY)
    # products, use_cases, problems, brand_terms, sector+audience → 5 anchor
    # (hisse grafikleri use_case elenince use_cases yine 1 kalem taşır)
    assert len(anchors) == 5
    assert any("BIST" in a for a in anchors)


def test_build_anchor_texts_empty_profile():
    assert build_anchor_texts({}) == []
    assert build_anchor_texts({"products": []}) == []


def test_use_case_fully_excluded_drops_anchor():
    profile = {
        "products": ["Analiz Platformu"],
        "use_cases": ["Kripto para alım satımı"],  # tek kalem, dışlanır
        "exclude_themes": ["kripto para"],
    }
    anchors = build_anchor_texts(profile)
    blob = " ".join(anchors).lower()
    assert "kripto" not in blob
    assert any("analiz" in a.lower() for a in anchors)


# ── build_anchor_groups (Marka odakları önizlemesi) ───────────────

def test_build_anchor_groups_matches_anchor_texts():
    from app.core.site_analyzer.anchor_builder import build_anchor_groups

    groups = build_anchor_groups(HISSEFY)
    assert [g["anchor"] for g in groups] == build_anchor_texts(HISSEFY)


def test_build_anchor_groups_source_mapping_and_excluded_items():
    from app.core.site_analyzer.anchor_builder import build_anchor_groups

    groups = build_anchor_groups(HISSEFY)
    by_field = {g["source_field"]: g for g in groups}

    assert set(by_field) == {
        "products", "use_cases", "problems_solved", "brand_terms", "sector_audience",
    }
    assert "Temettü Takip Ekranı" in by_field["products"]["excluded_items"]
    assert "Portföy Takip Sistemi" in by_field["products"]["kept_items"]
    assert by_field["brand_terms"]["kept_items"] == ["Hissefy", "Hissefy borsa"]
    assert all(g["label"] for g in groups)


def test_build_anchor_groups_empty_profile():
    from app.core.site_analyzer.anchor_builder import build_anchor_groups

    assert build_anchor_groups({}) == []


# ── matched_exclude_theme (import kapısı raporlaması) ─────────────

def test_matched_exclude_theme_returns_theme_text():
    from app.core.site_analyzer.theme_matcher import matched_exclude_theme

    themes = ["temettü takibi", "kripto para"]
    assert matched_exclude_theme("temettü takip programı", themes) == "temettü takibi"
    assert matched_exclude_theme("Portföy takip programı", themes) is None
    assert matched_exclude_theme("kripto para sinyalleri", themes) == "kripto para"


def test_matched_exclude_theme_handles_empty_inputs():
    from app.core.site_analyzer.theme_matcher import matched_exclude_theme

    assert matched_exclude_theme("", ["x"]) is None
    assert matched_exclude_theme("kelime", []) is None
    assert matched_exclude_theme("kelime", None) is None


# ── Faz D: services + protected_themes anchor sözleşmesi ─────────
#
# Eski fixture'ların (HISSEFY) çıktısı DEĞİŞMEDİ: bu profilde `services` ve
# `protected_themes` yok, yeni gruplar boş kalır ve listeye girmez. Değişen
# tek şey, bu alanları TAŞIYAN profillerin artık anchor üretmesidir.

DIGITUS = {
    "company_name": "Digitus",
    "sector": "Dijital pazarlama ve teknoloji ajansı",
    "target_audience": "Sanayi ve ihracat odaklı kurumsal şirketler",
    "products": ["Web tasarım", "E-ticaret sistemleri"],
    "services": ["Teknik SEO ve altyapı", "GEO", "Paid Search", "Meta Ads"],
    "use_cases": ["B2B talep üretimi"],
    "problems_solved": ["Arama motorlarında bulunamama"],
    "brand_terms": ["digitus", "digitus ajans"],
    "protected_themes": ["E-ticaret", "Ürün pazarlaması"],
    "exclude_themes": ["donanım ve cihaz satışı", "bilgisayar teknik servisi"],
}


def test_services_now_produce_anchor():
    from app.core.site_analyzer.anchor_builder import build_anchor_groups

    by_field = {g["source_field"]: g for g in build_anchor_groups(DIGITUS)}
    assert "services" in by_field
    assert "Teknik SEO ve altyapı" in by_field["services"]["kept_items"]
    assert "GEO" in by_field["services"]["anchor"]


def test_protected_themes_produce_anchor_group():
    from app.core.site_analyzer.anchor_builder import build_anchor_groups

    by_field = {g["source_field"]: g for g in build_anchor_groups(DIGITUS)}
    assert by_field["protected_themes"]["kept_items"] == [
        "E-ticaret", "Ürün pazarlaması"
    ]
    assert by_field["protected_themes"]["excluded_items"] == []


def test_anchor_group_order_is_deterministic():
    from app.core.site_analyzer.anchor_builder import build_anchor_groups

    fields = [g["source_field"] for g in build_anchor_groups(DIGITUS)]
    assert fields == [
        "products", "services", "use_cases", "problems_solved",
        "protected_themes", "brand_terms", "sector_audience",
    ]
    # Tekrar çağrı aynı sırayı üretir (fingerprint kararlılığı)
    assert fields == [g["source_field"] for g in build_anchor_groups(DIGITUS)]


def test_protected_theme_beats_broad_exclude_in_anchor():
    """GR-7 senaryosu: geniş dışlama, korunan içeriği anchor'dan SİLEMEZ."""
    from app.core.site_analyzer.anchor_builder import build_anchor_groups

    profile = {
        "products": ["Anti-Grey Saç Losyonu"],
        "use_cases": ["Saç boyası zararlarına karşı çözüm"],
        "protected_themes": ["saç boyasının zararları"],
        "exclude_themes": ["saç boyası"],
    }
    groups = {g["source_field"]: g for g in build_anchor_groups(profile)}
    assert "Saç boyası zararlarına karşı çözüm" in groups["use_cases"]["kept_items"]

    # Koruma kaldırılınca aynı kalem elenir → kural gerçekten koruma sayesinde
    without = {g["source_field"]: g for g in build_anchor_groups(
        dict(profile, protected_themes=[])
    )}
    assert "use_cases" not in without


def test_anchor_items_are_deduped_by_normalized_text():
    from app.core.site_analyzer.anchor_builder import build_anchor_groups

    profile = {"products": ["Web Tasarım", "web tasarim", "  Web Tasarım  "]}
    groups = build_anchor_groups(profile)
    assert groups[0]["kept_items"] == ["Web Tasarım"]


def test_whitespace_only_difference_does_not_change_fingerprint():
    from app.core.policy.review import canonical_anchor_fingerprint

    a = canonical_anchor_fingerprint(build_anchor_texts(DIGITUS))
    noisy = dict(DIGITUS, products=["  Web tasarım  ", "E-ticaret sistemleri"])
    b = canonical_anchor_fingerprint(build_anchor_texts(noisy))
    assert a == b


def test_adding_services_changes_fingerprint():
    from app.core.policy.review import canonical_anchor_fingerprint

    without = dict(DIGITUS, services=[])
    assert canonical_anchor_fingerprint(
        build_anchor_texts(without)
    ) != canonical_anchor_fingerprint(build_anchor_texts(DIGITUS))


def test_legacy_profile_without_new_fields_still_works():
    anchors = build_anchor_texts(HISSEFY)
    assert len(anchors) == 5  # eski davranış korunur


def test_use_case_addition_and_removal_changes_anchors():
    profile = {
        "products": ["Analiz Platformu"],
        "use_cases": ["Borsa takibi", "Portfoy izleme"],
        "exclude_themes": [],
    }
    anchors = build_anchor_texts(profile)
    blob = " ".join(anchors).lower()
    assert "borsa takibi" in blob
    assert "portfoy izleme" in blob

    profile["use_cases"] = ["Portfoy izleme"]
    anchors = build_anchor_texts(profile)
    blob = " ".join(anchors).lower()
    assert "borsa takibi" not in blob
    assert "portfoy izleme" in blob
