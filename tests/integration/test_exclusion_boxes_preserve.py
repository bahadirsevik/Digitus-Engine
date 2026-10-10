"""plan_yapilacaklar 3.1 — iki dışlama kutusu arasında sıralı kayıt zinciri.

Kutu A ("Kaçınılacak temalar") = profile_data.exclude_themes (yumuşak, PUT
/confirm). Kutu B ("Kesin dışlama") = excluded_info (sert, PUT /policy/review);
B terimleri exclude_themes'e de birleşir. Bayat bir A listesi (B'nin yeni
terimini içermeyen) kaydedildiğinde sunucu B terimlerini YENİDEN eklemelidir.
"""
import pytest

from app.database.models import BrandProfile


def _confirm(client, ws_id, exclude_themes):
    return client.put(
        f"/api/v1/brand-profile/workspaces/{ws_id}/confirm",
        json={"profile_data": {"exclude_themes": exclude_themes}},
    )


def _review(client, ws_id, excluded_info):
    return client.put(
        f"/api/v1/brand-profile/workspaces/{ws_id}/policy/review",
        json={"excluded_info": excluded_info},
    )


@pytest.fixture
def confirmed_ws(make_workspace):
    return make_workspace(
        "Dislama WS",
        status="confirmed",
        profile_data={
            "company_name": "X",
            "sector": "finans",
            "products": ["hisse analiz yazılımı"],
            "exclude_themes": ["ai teması"],
            "anchor_texts": ["hisse analiz"],
        },
    )


def _themes(db_session, ws_id):
    db_session.expire_all()
    ws = db_session.get(BrandProfile, ws_id)
    return list((ws.profile_data or {}).get("exclude_themes", []))


def _hard_topic_terms(db_session, ws_id):
    db_session.expire_all()
    ws = db_session.get(BrandProfile, ws_id)
    entries = (ws.topic_policy or {}).get("excluded_terms", [])
    return sorted(
        (e["term"], e["status"]) for e in entries
    )


def test_a_edit_then_b_save_then_stale_a_save_keeps_both(
    client, confirmed_ws, db_session
):
    ws_id = confirmed_ws.id

    # 1) A düzenle + kaydet
    assert _confirm(client, ws_id, ["ai teması", "ucuz taklit"]).status_code == 200
    assert "ucuz taklit" in _themes(db_session, ws_id)

    # 2) B kesin dışlama ekler (exclude_themes'e de birleşir)
    assert _review(client, ws_id, "kripto para").status_code == 200
    assert "kripto para" in _themes(db_session, ws_id)
    hard_before = _hard_topic_terms(db_session, ws_id)
    assert ("kripto para", "approved") in hard_before

    # 3) A, B terimini İÇERMEYEN bayat listeyle tekrar kaydedilir
    res = _confirm(client, ws_id, ["ai teması", "ucuz taklit", "yeni tema"])
    assert res.status_code == 200

    themes = _themes(db_session, ws_id)
    assert "yeni tema" in themes          # A düzenlemesi
    assert "ucuz taklit" in themes
    assert "kripto para" in themes        # B terimi sunucuda korundu
    assert "kripto para" in res.json()["profile_data"]["exclude_themes"]
    # Sert politika değişmedi
    assert _hard_topic_terms(db_session, ws_id) == hard_before


def test_a_cannot_remove_b_term_even_if_client_drops_it(
    client, confirmed_ws, db_session
):
    ws_id = confirmed_ws.id
    assert _review(client, ws_id, "kripto para").status_code == 200

    assert _confirm(client, ws_id, []).status_code == 200  # A boşaltıldı

    themes = _themes(db_session, ws_id)
    assert "kripto para" in themes
    assert ("kripto para", "approved") in _hard_topic_terms(db_session, ws_id)


def test_b_removal_still_removes_term_from_themes(
    client, confirmed_ws, db_session
):
    """Yalnız B terimi kaldırabilir: B'den silinen terim exclude_themes'ten de düşer."""
    ws_id = confirmed_ws.id
    assert _review(client, ws_id, "kripto para").status_code == 200
    assert _confirm(client, ws_id, ["ai teması"]).status_code == 200
    assert "kripto para" in _themes(db_session, ws_id)

    assert _review(client, ws_id, "").status_code == 200

    themes = _themes(db_session, ws_id)
    assert "kripto para" not in themes
    assert "ai teması" in themes
    assert ("kripto para", "rejected") in _hard_topic_terms(db_session, ws_id)


def test_saving_unchanged_a_does_not_bump_policy_or_anchor_version(
    client, confirmed_ws, db_session
):
    ws_id = confirmed_ws.id
    assert _confirm(client, ws_id, ["ai teması", "ucuz taklit"]).status_code == 200
    assert _review(client, ws_id, "kripto para").status_code == 200

    db_session.expire_all()
    ws = db_session.get(BrandProfile, ws_id)
    policy_v, anchor_v = ws.policy_version, ws.anchor_version
    themes_before = _themes(db_session, ws_id)

    # İstemci yalnız kendi A terimlerini yollar (B çipleri hariç) — değişmemiş
    assert _confirm(client, ws_id, ["ai teması", "ucuz taklit"]).status_code == 200
    # Eski istemci tam listeyi (B dahil, farklı sırada) yollarsa da aynı sonuç
    assert _confirm(
        client, ws_id, ["ucuz taklit", "Kripto Para", "ai teması"]
    ).status_code == 200

    db_session.expire_all()
    ws = db_session.get(BrandProfile, ws_id)
    assert ws.policy_version == policy_v
    assert ws.anchor_version == anchor_v
    assert sorted(_themes(db_session, ws_id)) == sorted(themes_before)


def test_changing_a_still_bumps_policy_version(client, confirmed_ws, db_session):
    ws_id = confirmed_ws.id
    assert _review(client, ws_id, "kripto para").status_code == 200
    db_session.expire_all()
    policy_v = db_session.get(BrandProfile, ws_id).policy_version

    assert _confirm(client, ws_id, ["ai teması", "yeni tema"]).status_code == 200

    db_session.expire_all()
    assert db_session.get(BrandProfile, ws_id).policy_version == policy_v + 1
