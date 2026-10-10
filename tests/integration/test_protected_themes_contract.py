"""Faz B: `profile_data.protected_themes` profil sözleşmesi.

Kapsam: sanitize/editable allowlist, profil-önce kart onayı, tek yazma
kapısının (apply_profile_data_update) anchor yeniden üretimi, AI revizyonunun
alanı düşürmemesi ve eski profillerin geriye uyumluluğu.

Ücretli çağrı yok: AI yolları sahte servisle koşar.
"""
import json

from app.api.v1.brand_profile import (
    EDITABLE_LIST_FIELDS,
    _apply_profile_review_data,
    _sanitize_profile_data,
)
from app.core.policy.review import apply_profile_data_update


BASE_PROFILE = {
    "company_name": "Digitus",
    "sector": "Dijital pazarlama ajansı",
    "target_audience": "Kurumsal sanayi şirketleri",
    "products": ["Web tasarım"],
    "services": ["Teknik SEO"],
    "use_cases": ["B2B talep üretimi"],
    "problems_solved": ["Görünürlük eksikliği"],
    "brand_terms": ["digitus"],
    "exclude_themes": ["donanım ve cihaz satışı"],
}


def test_protected_themes_is_editable_field():
    assert "protected_themes" in EDITABLE_LIST_FIELDS


def test_sanitize_accepts_incoming_protected_themes():
    out = _sanitize_profile_data(
        {"protected_themes": ["e-ticaret", "ürün pazarlaması"]}, BASE_PROFILE
    )
    assert out["protected_themes"] == ["e-ticaret", "ürün pazarlaması"]


def test_profile_review_flow_accepts_protected_themes():
    out = _apply_profile_review_data(
        {"protected_themes": "e-ticaret\nürün pazarlaması"}, BASE_PROFILE
    )
    assert out["protected_themes"] == ["e-ticaret", "ürün pazarlaması"]


def test_legacy_profile_defaults_to_empty_list():
    """Alanı hiç taşımayan eski profil: hata değil, boş liste."""
    out = _sanitize_profile_data({}, BASE_PROFILE)
    assert out["protected_themes"] == []


def test_existing_protected_themes_survive_unrelated_edit():
    existing = dict(BASE_PROFILE, protected_themes=["e-ticaret"])
    out = _sanitize_profile_data({"products": ["Web tasarım", "Mobil"]}, existing)
    assert out["protected_themes"] == ["e-ticaret"]


def test_protected_themes_reach_anchor_texts():
    out = _sanitize_profile_data({"protected_themes": ["e-ticaret"]}, BASE_PROFILE)
    blob = " ".join(out["anchor_texts"]).lower()
    assert "e-ticaret" in blob
    assert "teknik seo" in blob  # services artık anchor'da (Faz D)


def test_apply_profile_data_update_bumps_anchor_version(db_session, make_workspace):
    workspace = make_workspace(profile_data=dict(BASE_PROFILE))
    # İlk yazım: anchor'lar türetilir
    apply_profile_data_update(db_session, workspace, dict(BASE_PROFILE))
    db_session.commit()
    before = int(workspace.anchor_version or 1)

    changed = apply_profile_data_update(
        db_session,
        workspace,
        dict(BASE_PROFILE, protected_themes=["e-ticaret"]),
        invalidate_outputs=False,
    )
    db_session.commit()

    assert changed is True
    assert int(workspace.anchor_version) == before + 1
    assert "e-ticaret" in " ".join(workspace.profile_data["anchor_texts"]).lower()


def test_apply_profile_data_update_is_idempotent(db_session, make_workspace):
    profile = dict(BASE_PROFILE, protected_themes=["e-ticaret"])
    workspace = make_workspace(profile_data=dict(profile))
    apply_profile_data_update(db_session, workspace, dict(profile))
    db_session.commit()
    version = int(workspace.anchor_version or 1)

    changed = apply_profile_data_update(db_session, workspace, dict(profile))
    db_session.commit()

    assert changed is False
    assert int(workspace.anchor_version) == version


class _FakeRevisionAI:
    """Profil revizyonu dönüşünde protected_themes YOK — düşmemeli."""

    def complete_json(self, *args, **kwargs):
        return json.dumps({
            "company_name": "Digitus",
            "sector": "Dijital pazarlama ajansı",
            "target_audience": "Kurumsal sanayi şirketleri",
            "products": ["Web tasarım", "Mobil uygulama"],
            "services": ["Teknik SEO"],
            "use_cases": ["B2B talep üretimi"],
            "problems_solved": ["Görünürlük eksikliği"],
            "brand_terms": ["digitus"],
            "exclude_themes": ["AI uydurdu"],
        })


def test_ai_revision_preserves_system_managed_theme_fields():
    from app.core.site_analyzer.profile_extractor import ProfileExtractor

    base = dict(BASE_PROFILE, protected_themes=["e-ticaret"])
    revised = ProfileExtractor(_FakeRevisionAI()).revise_profile_with_requirements(
        base, must_have_info="mutlaka e-ticaret"
    )

    assert revised["protected_themes"] == ["e-ticaret"]
    assert revised["exclude_themes"] == ["donanım ve cihaz satışı"]
    assert "Mobil uygulama" in revised["products"]  # AI katkısı korunur

# NOT: dispatch manifest'inin protected_themes taşıdığı,
# test_brand_exclusion_filter.py::test_manifest_carries_brand_filter_prompt_version
# içinde doğrulanır (aynı manifest, tek yerde).


# ── Codex #4: politika fingerprint'i tema listelerini kapsamalı ──


def test_policy_snapshot_includes_theme_lists(db_session, make_workspace):
    from app.core.policy.review import policy_effect_snapshot

    workspace = make_workspace(profile_data=dict(BASE_PROFILE))
    before = policy_effect_snapshot(workspace)

    workspace.profile_data = dict(BASE_PROFILE, protected_themes=["e-ticaret"])
    assert policy_effect_snapshot(workspace) != before


def test_theme_only_change_bumps_policy_version_even_if_anchor_same(
    db_session, make_workspace
):
    """Dışlama metni değişip anchor aynı kalırsa marka eleme politikası
    değişmiştir; policy_version artmalı ve çıktılar bayatlamalı."""
    workspace = make_workspace(profile_data=dict(BASE_PROFILE))
    apply_profile_data_update(db_session, workspace, dict(BASE_PROFILE))
    db_session.commit()
    anchor_before = int(workspace.anchor_version or 1)
    policy_before = int(workspace.policy_version or 1)
    anchors_before = list(workspace.profile_data["anchor_texts"])

    # Yalnız dışlama temasını daralt: hiçbir profil kalemiyle örtüşmüyor,
    # dolayısıyla anchor metinleri AYNI kalır.
    changed = apply_profile_data_update(
        db_session,
        workspace,
        dict(BASE_PROFILE, exclude_themes=["donanım ve cihaz satışı", "saç ekimi"]),
    )
    db_session.commit()

    assert changed is False  # anchor DEĞİŞMEDİ
    assert list(workspace.profile_data["anchor_texts"]) == anchors_before
    assert int(workspace.anchor_version) == anchor_before
    assert int(workspace.policy_version) == policy_before + 1


def test_competitor_review_does_not_double_bump_policy_version(
    db_session, make_workspace
):
    """apply_competitor_review kendi snapshot'ını yönetir; profil yazma kapısı
    aynı değişiklik için ikinci kez artırmamalı."""
    from app.core.policy.review import apply_competitor_review

    workspace = make_workspace(profile_data=dict(BASE_PROFILE))
    apply_profile_data_update(db_session, workspace, dict(BASE_PROFILE))
    db_session.commit()
    policy_before = int(workspace.policy_version or 1)

    apply_competitor_review(
        db_session,
        workspace,
        competitor_urls=None,
        decisions=None,
        excluded_info="kripto para",
    )
    db_session.commit()

    assert int(workspace.policy_version) == policy_before + 1
