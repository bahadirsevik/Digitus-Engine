"""Workspace kanal seed'leri — plan9 Aşama A sözleşmesi.

Kapsanan sözleşmeler:
- `channel_seeds` GÖNDERİLMEZSE bugünkü davranış BİREBİR korunur.
- `Uygun değil` tekil seçenektir; kanallarla birlikte reddedilir.
- Boş karar (ne kanal ne not_suitable) reddedilir.
- Aynı kanonik kelime iki kez gönderilemez.
- Seed'ler workspace'e ÖZELdir; başka workspace'in seed'i okunamaz.
- Form bir BÜTÜN olarak yazılır: çıkarılan seed silinir.
"""
from __future__ import annotations

import pytest

from app.core.channel_seed import (
    ChannelSeedError, has_channel_seeds, list_channel_seeds,
    normalize_seed_rows, replace_channel_seeds, seeds_by_channel,
)


# ── Saf doğrulama (DB'siz) ──────────────────────────────────────────
def test_uygun_degil_kanallarla_birlikte_REDDEDILIR():
    with pytest.raises(ChannelSeedError, match="tekil secenektir"):
        normalize_seed_rows([{"keyword": "x", "channels": ["ADS"],
                              "not_suitable": True}])


def test_bos_karar_REDDEDILIR():
    with pytest.raises(ChannelSeedError, match="en az bir kanal"):
        normalize_seed_rows([{"keyword": "x", "channels": []}])


def test_gecersiz_kanal_REDDEDILIR():
    with pytest.raises(ChannelSeedError, match="Gecersiz kanal"):
        normalize_seed_rows([{"keyword": "x", "channels": ["EMAIL"]}])


def test_kanallar_TEKILLESTIRILIR_ve_sabit_siraya_gelir():
    rows = normalize_seed_rows([
        {"keyword": "x", "channels": ["social", "ADS", "SOCIAL", "seo"]}])
    assert rows[0]["channels"] == ["ADS", "SEO", "SOCIAL"]


def test_ayni_kanonik_kelime_IKI_KEZ_gonderilemez():
    with pytest.raises(ChannelSeedError, match="ayni kelimeye"):
        normalize_seed_rows([
            {"keyword": "İSTANBUL SEO", "channels": ["ADS"]},
            {"keyword": "istanbul seo", "channels": ["SEO"]}])


def test_bos_keyword_REDDEDILIR():
    with pytest.raises(ChannelSeedError, match="keyword bos"):
        normalize_seed_rows([{"keyword": "   ", "channels": ["ADS"]}])


def test_gosterim_metni_KORUNUR_kanonik_ayri_tutulur():
    rows = normalize_seed_rows([{"keyword": "  Dijital Ajans  ",
                                 "channels": ["ADS"]}])
    assert rows[0]["keyword"] == "Dijital Ajans"
    assert rows[0]["canonical_keyword"] == "dijital ajans"


# ── DB davranışı ────────────────────────────────────────────────────
def test_seed_yazma_ve_okuma(db_session, make_workspace):
    workspace = make_workspace(name="Seed WS")
    assert has_channel_seeds(db_session, workspace.id) is False

    replace_channel_seeds(db_session, workspace.id, [
        {"keyword": "dijital ajans", "channels": ["ADS"]},
        {"keyword": "pazarlama nedir", "channels": ["SEO", "SOCIAL"]},
        {"keyword": "laptop satin al", "not_suitable": True},
    ])
    db_session.commit()

    assert has_channel_seeds(db_session, workspace.id) is True
    seeds = list_channel_seeds(db_session, workspace.id)
    assert len(seeds) == 3
    by_channel = seeds_by_channel(db_session, workspace.id)
    assert [s["keyword"] for s in by_channel["ADS"]] == ["dijital ajans"]
    assert [s["keyword"] for s in by_channel["SEO"]] == ["pazarlama nedir"]
    assert [s["keyword"] for s in by_channel["SOCIAL"]] == ["pazarlama nedir"]
    assert [s["keyword"] for s in by_channel["NOT_SUITABLE"]] == [
        "laptop satin al"]


def test_form_BUTUN_olarak_yazilir_cikarilan_silinir(db_session,
                                                     make_workspace):
    workspace = make_workspace(name="Seed Replace")
    replace_channel_seeds(db_session, workspace.id, [
        {"keyword": "a", "channels": ["ADS"]},
        {"keyword": "b", "channels": ["SEO"]}])
    db_session.commit()

    replace_channel_seeds(db_session, workspace.id, [
        {"keyword": "b", "channels": ["ADS", "SEO"]}])
    db_session.commit()

    seeds = list_channel_seeds(db_session, workspace.id)
    assert [s.canonical_keyword for s in seeds] == ["b"]
    assert seeds[0].channels == ["ADS", "SEO"]


def test_seedler_workspace_e_OZEL(db_session, make_workspace):
    first = make_workspace(name="WS Bir")
    second = make_workspace(name="WS Iki")
    replace_channel_seeds(db_session, first.id,
                          [{"keyword": "a", "channels": ["ADS"]}])
    db_session.commit()

    assert len(list_channel_seeds(db_session, first.id)) == 1
    assert list_channel_seeds(db_session, second.id) == []
    assert has_channel_seeds(db_session, second.id) is False


def test_keyword_id_evrende_varsa_doldurulur(db_session, make_workspace):
    from app.database.models import Keyword, WorkspaceKeyword

    workspace = make_workspace(name="Seed Resolve")
    keyword = Keyword(keyword="Dijital Ajans")
    db_session.add(keyword)
    db_session.flush()
    db_session.add(WorkspaceKeyword(brand_profile_id=workspace.id,
                                    keyword_id=keyword.id, monthly_volume=10))
    db_session.commit()

    replace_channel_seeds(db_session, workspace.id, [
        {"keyword": "dijital ajans", "channels": ["ADS"]},
        {"keyword": "evrende olmayan kelime", "channels": ["SEO"]}])
    db_session.commit()

    seeds = {s.canonical_keyword: s for s in
             list_channel_seeds(db_session, workspace.id)}
    assert seeds["dijital ajans"].keyword_id == keyword.id
    # Evrende olmayan kelime HATA DEGIL; keyword_id NULL kalir
    assert seeds["evrende olmayan kelime"].keyword_id is None


def test_DB_kisiti_bos_karari_da_REDDEDER(db_session, make_workspace):
    """Kisit IKI YONLU: not_suitable=false + channels=[] DB'de de yasak.

    Kod yolu bunu zaten reddediyor; bu test DOGRUDAN DB yaziminda da
    korumanin durdugunu dogrular.
    """
    import sqlalchemy as sa

    workspace = make_workspace(name="Kisit WS")
    with pytest.raises(sa.exc.IntegrityError):
        db_session.execute(sa.text(
            "INSERT INTO workspace_channel_seeds "
            "(brand_profile_id, keyword, canonical_keyword, channels, "
            " not_suitable, source) "
            "VALUES (:w, 'x', 'x', '[]'::json, false, 'test')"),
            {"w": workspace.id})
        db_session.flush()
    db_session.rollback()


def test_DB_kisiti_not_suitable_ile_kanali_REDDEDER(db_session,
                                                    make_workspace):
    import sqlalchemy as sa

    workspace = make_workspace(name="Kisit WS 2")
    with pytest.raises(sa.exc.IntegrityError):
        db_session.execute(sa.text(
            "INSERT INTO workspace_channel_seeds "
            "(brand_profile_id, keyword, canonical_keyword, channels, "
            " not_suitable, source) "
            "VALUES (:w, 'x', 'x', '[\"ADS\"]'::json, true, 'test')"),
            {"w": workspace.id})
        db_session.flush()
    db_session.rollback()


def test_guncellemede_labelled_at_YENILENIR(db_session, make_workspace):
    """Ilk kayit zamani ile SON kullanici karari ayirt edilebilmeli."""
    workspace = make_workspace(name="Zaman WS")
    replace_channel_seeds(db_session, workspace.id,
                          [{"keyword": "a", "channels": ["ADS"]}])
    db_session.commit()
    first = list_channel_seeds(db_session, workspace.id)[0]
    first_at = first.labelled_at
    assert first_at is not None

    replace_channel_seeds(db_session, workspace.id,
                          [{"keyword": "a", "channels": ["SEO"]}])
    db_session.commit()
    updated = list_channel_seeds(db_session, workspace.id)[0]

    assert updated.id == first.id                 # ayni satir guncellendi
    assert updated.channels == ["SEO"]
    assert updated.labelled_at > first_at         # timestamp YENILENDI


def test_degistirilen_kelimenin_eski_metni_KORUNUR(db_session,
                                                   make_workspace):
    workspace = make_workspace(name="Seed Replaced")
    replace_channel_seeds(db_session, workspace.id, [
        {"keyword": "yeni kelime", "channels": ["SEO"],
         "replaced_original_keyword": "eski oneri"}])
    db_session.commit()
    seed = list_channel_seeds(db_session, workspace.id)[0]
    assert seed.replaced_original_keyword == "eski oneri"


# ── API geriye uyumluluk ────────────────────────────────────────────
def _approvable(make_workspace, name="Onay WS"):
    """Profil-önce akışta onaylanabilir workspace (anchor_texts ŞART)."""
    return make_workspace(
        name=name, status="keywords_review",
        onboarding_flow="profile_first",
        suggested_keywords=["a", "b"],
        profile_data={"company_name": "X", "sector": "Y",
                      "services": ["z"], "brand_summary": "s",
                      "target_audience": "t",
                      "anchor_texts": ["X dijital pazarlama hizmetleri"]})


def test_channel_seeds_GONDERILMEZSE_davranis_DEGISMEZ(client, db_session,
                                                       make_workspace):
    workspace = _approvable(make_workspace)
    response = client.put(
        f"/api/v1/brand-profile/workspaces/{workspace.id}/keywords/approve",
        json={"keywords": ["seo ajansi", "dijital pazarlama"]})
    assert response.status_code == 200, response.text
    # Seed tablosu BOS kalir — mevcut workspace'ler etkilenmez
    assert list_channel_seeds(db_session, workspace.id) == []
    assert has_channel_seeds(db_session, workspace.id) is False


def test_channel_seeds_gonderilirse_YAZILIR(client, db_session,
                                            make_workspace):
    workspace = _approvable(make_workspace, name="Onay Seed")
    response = client.put(
        f"/api/v1/brand-profile/workspaces/{workspace.id}/keywords/approve",
        json={"keywords": ["seo ajansi", "pazarlama nedir"],
              "channel_seeds": [
                  {"keyword": "seo ajansi", "channels": ["ADS"]},
                  {"keyword": "pazarlama nedir",
                   "channels": ["SEO", "SOCIAL"]}]})
    assert response.status_code == 200, response.text
    seeds = list_channel_seeds(db_session, workspace.id)
    assert len(seeds) == 2


def test_gecersiz_seed_400_dondurur_ve_YAZMAZ(client, db_session,
                                              make_workspace):
    workspace = _approvable(make_workspace, name="Onay Gecersiz")
    response = client.put(
        f"/api/v1/brand-profile/workspaces/{workspace.id}/keywords/approve",
        json={"keywords": ["x"],
              "channel_seeds": [{"keyword": "x", "channels": ["ADS"],
                                 "not_suitable": True}]})
    assert response.status_code == 400
    assert "tekil" in response.json()["detail"]
    assert list_channel_seeds(db_session, workspace.id) == []


def test_seed_endpointleri_calisir(client, db_session, make_workspace):
    workspace = make_workspace(name="Seed API")
    url = f"/api/v1/brand-profile/workspaces/{workspace.id}/channel-seeds"

    assert client.get(url).json() == []

    response = client.put(url, json=[
        {"keyword": "dijital ajans", "channels": ["ADS", "SEO"]},
        {"keyword": "laptop", "not_suitable": True}])
    assert response.status_code == 200, response.text
    payload = response.json()
    assert len(payload) == 2
    saved = {row["canonical_keyword"]: row for row in client.get(url).json()}
    assert saved["dijital ajans"]["channels"] == ["ADS", "SEO"]
    assert saved["laptop"]["not_suitable"] is True
    assert saved["laptop"]["channels"] == []
    assert saved["dijital ajans"]["source"] == (
        "patron_onboarding_channel_seed")


def test_baska_workspace_seedi_okunamaz(client, db_session, make_workspace):
    first = make_workspace(name="Izole Bir")
    second = make_workspace(name="Izole Iki")
    client.put(
        f"/api/v1/brand-profile/workspaces/{first.id}/channel-seeds",
        json=[{"keyword": "a", "channels": ["ADS"]}])
    other = client.get(
        f"/api/v1/brand-profile/workspaces/{second.id}/channel-seeds")
    assert other.status_code == 200
    assert other.json() == []
