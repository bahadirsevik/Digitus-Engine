# -*- coding: utf-8 -*-
"""Lokasyon filtresi değişikliği policy_version/freshness sözleşmesi.

Kaynak: plan_v3_lokasyon_filtresi.md §5.2 — lokasyon filtresi mod/muafiyet
değişikliği bir KABUL politikasıdır; tek yazma kapısı
(`apply_profile_data_update`) bunu görmezden gelirse mevcut ChannelPool/
ContentOutput çıktıları FRESH görünmeye devam eder. Canlı smoke testinde
doğrulanan hata: ws56'da exclude_all → none → exclude_all döngüsü
`policy_version`'ı 4'te sabit bıraktı (yalnız TEMA listeleri fingerprint'e
giriyordu, lokasyon ayarları görünmezdi).

Bu dosya `enforcement_identity`'nin `apply_profile_data_update`'e
kablolanmasını (Faz — bkz. `app/core/policy/review.py`) ve
`manage_policy_version=False` çağıranının (apply_competitor_review) çift
artış yapmadığını kanıtlar.
"""
import pytest

from app.core.policy.location_policy import (
    MODE_EXCLUDE_ALL,
    MODE_FOCUS_ONLY,
    MODE_NONE,
)
from app.core.policy.review import apply_competitor_review, apply_profile_data_update
from app.database.models import BrandProfile, ContentOutput, Keyword


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
    "location_filter_mode": MODE_NONE,
    "focus_cities": [],
    "location_exempt_terms": [],
}


# ── 1. none → exclude_all: +1 VE mevcut çıktılar bayatlar ─────────────────
def test_none_to_exclude_all_bumps_version_and_invalidates_outputs(
    db_session, make_workspace, make_scoring_run
):
    profile = dict(BASE_PROFILE, location_filter_mode=MODE_NONE)
    workspace = make_workspace(profile_data=dict(profile))
    apply_profile_data_update(db_session, workspace, dict(profile))
    db_session.commit()

    run = make_scoring_run(brand_profile_id=workspace.id, status="channel_assigned")
    kw = Keyword(keyword="lokasyon test", normalized_keyword="lokasyon test")
    db_session.add(kw)
    db_session.flush()
    output = ContentOutput(
        scoring_run_id=run.id, keyword_id=kw.id, channel="SEO",
        content_type="blog_post", content_data={},
    )
    db_session.add(output)
    db_session.commit()
    output_id = output.id
    policy_before = int(workspace.policy_version or 1)

    changed = apply_profile_data_update(
        db_session, workspace, dict(profile, location_filter_mode=MODE_EXCLUDE_ALL)
    )
    db_session.commit()

    assert changed is False  # anchor değişmedi — yalnız enforcement değişti
    assert int(workspace.policy_version) == policy_before + 1
    db_session.expire_all()
    assert db_session.get(ContentOutput, output_id).is_stale is True


# ── 2. Muafiyet terimleri değişti (etkin modda): +1 ────────────────────────
def test_exemption_terms_change_in_active_mode_bumps_version(
    db_session, make_workspace
):
    profile = dict(BASE_PROFILE, location_filter_mode=MODE_EXCLUDE_ALL)
    workspace = make_workspace(profile_data=dict(profile))
    apply_profile_data_update(db_session, workspace, dict(profile))
    db_session.commit()
    policy_before = int(workspace.policy_version or 1)

    apply_profile_data_update(
        db_session, workspace,
        dict(profile, location_exempt_terms=["gaziantep fıstığı"]),
    )
    db_session.commit()

    assert int(workspace.policy_version) == policy_before + 1


# ── 3. focus_only: odak şehir listesi değişti: +1 ──────────────────────────
def test_focus_city_list_change_in_focus_only_bumps_version(
    db_session, make_workspace
):
    profile = dict(
        BASE_PROFILE, location_filter_mode=MODE_FOCUS_ONLY,
        focus_cities=["İstanbul"],
    )
    workspace = make_workspace(profile_data=dict(profile))
    apply_profile_data_update(db_session, workspace, dict(profile))
    db_session.commit()
    policy_before = int(workspace.policy_version or 1)

    apply_profile_data_update(
        db_session, workspace, dict(profile, focus_cities=["Ankara"]),
    )
    db_session.commit()

    assert int(workspace.policy_version) == policy_before + 1


# ── 4. exclude_all / none: yalnız SAKLANAN focus_cities değişti: bump YOK ──
@pytest.mark.parametrize("mode", [MODE_NONE, MODE_EXCLUDE_ALL])
def test_focus_cities_change_outside_focus_only_does_not_bump(
    db_session, make_workspace, mode
):
    profile = dict(BASE_PROFILE, location_filter_mode=mode, focus_cities=[])
    workspace = make_workspace(profile_data=dict(profile))
    apply_profile_data_update(db_session, workspace, dict(profile))
    db_session.commit()
    policy_before = int(workspace.policy_version or 1)

    changed = apply_profile_data_update(
        db_session, workspace,
        dict(profile, focus_cities=["İstanbul", "İzmir"]),
    )
    db_session.commit()

    assert changed is False
    assert int(workspace.policy_version) == policy_before


# ── 5. Kanonik olarak eşdeğer yeniden kayıt: bump YOK ──────────────────────
def test_canonically_equivalent_resave_does_not_bump(db_session, make_workspace):
    profile = dict(
        BASE_PROFILE, location_filter_mode=MODE_FOCUS_ONLY,
        focus_cities=["İstanbul", "İzmir"],
        location_exempt_terms=["Gaziantep Fıstığı"],
    )
    workspace = make_workspace(profile_data=dict(profile))
    apply_profile_data_update(db_session, workspace, dict(profile))
    db_session.commit()
    policy_before = int(workspace.policy_version or 1)

    equivalent = dict(
        profile,
        focus_cities=["izmir", "ISTANBUL"],
        location_exempt_terms=["gaziantep fistigi"],
    )
    changed = apply_profile_data_update(db_session, workspace, equivalent)
    db_session.commit()

    assert changed is False
    assert int(workspace.policy_version) == policy_before


# ── 6. manage_policy_version=False yolu: TAM BİR bump, iki değil ──────────
def test_manage_policy_version_false_suppresses_location_bump_internally(
    db_session, make_workspace
):
    """`apply_profile_data_update`'in KENDİSİ, çağıran sürümü kendi
    yönetiyorsa (manage_policy_version=False) lokasyon değişse bile
    ARTIRMAMALI — aksi halde apply_competitor_review çift artış yapar."""
    profile = dict(BASE_PROFILE, location_filter_mode=MODE_NONE)
    workspace = make_workspace(profile_data=dict(profile))
    apply_profile_data_update(db_session, workspace, dict(profile))
    db_session.commit()
    policy_before = int(workspace.policy_version or 1)

    apply_profile_data_update(
        db_session, workspace,
        dict(profile, location_filter_mode=MODE_EXCLUDE_ALL),
        manage_policy_version=False,
    )
    db_session.commit()

    assert int(workspace.policy_version) == policy_before


def test_competitor_review_still_bumps_exactly_once_with_location_gate_wired(
    db_session, make_workspace
):
    """Gerçek `manage_policy_version=False` çağıranı: `apply_competitor_review`
    kendi `policy_effect_snapshot`'ı (lokasyon dahil) üzerinden TEK artış
    yapar; içteki `apply_profile_data_update` çağrısı ikinci bir artış
    EKLEMEMELİDİR."""
    profile = dict(BASE_PROFILE, location_filter_mode=MODE_EXCLUDE_ALL)
    workspace = make_workspace(profile_data=dict(profile))
    apply_profile_data_update(db_session, workspace, dict(profile))
    db_session.commit()
    policy_before = int(workspace.policy_version or 1)

    apply_competitor_review(
        db_session, workspace,
        competitor_urls=None, decisions=None, excluded_info="kripto para",
    )
    db_session.commit()

    assert int(workspace.policy_version) == policy_before + 1


# ── 7. Gerçek onay rotası: PUT /workspaces/{id}/confirm ────────────────────
def test_confirm_workspace_route_bumps_policy_version_on_location_change(
    db_session, make_workspace, client
):
    """Bu, UI'nin kullandığı gerçek yol ve daha önce bozuk olan yoldur."""
    profile = dict(BASE_PROFILE, location_filter_mode=MODE_NONE)
    workspace = make_workspace(profile_data=dict(profile), status="confirmed")
    apply_profile_data_update(db_session, workspace, dict(profile))
    db_session.commit()
    workspace_id = workspace.id
    policy_before = int(workspace.policy_version or 1)

    response = client.put(
        f"/api/v1/brand-profile/workspaces/{workspace_id}/confirm",
        json={"profile_data": {"location_filter_mode": MODE_EXCLUDE_ALL}},
    )

    assert response.status_code == 200, response.text
    db_session.expire_all()
    refreshed = db_session.get(BrandProfile, workspace_id)
    assert int(refreshed.policy_version) == policy_before + 1
