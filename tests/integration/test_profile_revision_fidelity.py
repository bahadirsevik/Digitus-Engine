"""Keyword yeniden uretiminin profil sadakati (plan_marka_profili_sadakati.md P0.1/P0.2).

TEK INVARIANT: `_run_keyword_suggestion_from_profile` `profile_data`'yi HIC
degistirmez. Alan-bazli "su korunur, bu yazilir" matrisi BILINCLI olarak test
EDILMEZ — profildeki her liste alani kullanici tarafindan duzenlenebilir
(`KeywordAnchorReview` EDITABLE_GROUP_FIELDS), company_name/sector kullaniciya
kilitli, dolayisiyla AI'in guvenle yazabilecegi alan yoktur.

Regresyonun canli senaryosu: kullanici bir grubu duzenler, hemen yanindaki
"Keyword onerilerini yeniden uret" butonuna basar, AI duzenlemeyi ezerdi.
Ayrica AI semasinda olmayan anahtarlar (policy_specificity) dusunce
`_profile_theme_sets` degisir, policy_version artar ve TUM workspace ciktilari
bayatlardi.

Tum AI cagrilari sahte — UCRETLI CAGRI YOK.
"""
import json

import pytest

from app.api.v1 import brand_profile as brand_profile_api


# AI semasinda OLMAYAN teknik anahtar dahil, dolu bir profil.
FULL_PROFILE = {
    "company_name": "Digitus",
    "sector": "Dijital pazarlama ajansi",
    "brand_summary": "Kullanicinin yazdigi ozet",
    "products": ["Kullanici urunu"],
    "services": ["Kullanici hizmeti"],
    "target_audience": "Kullanicinin yazdigi kitle",
    "use_cases": ["Kullanici senaryosu"],
    "problems_solved": ["Kullanici problemi"],
    "brand_terms": ["digitus"],
    "exclude_themes": ["donanim satisi"],
    "protected_themes": ["e-ticaret"],
    "anchor_texts": ["Kullanici urunu"],
    "policy_specificity": {"core_terms": ["seo"], "schema_version": 1},
}

# AI'in donduruu: HER alan farkli. Hicbiri profile islenmemeli.
AI_OVERWRITE = {
    "company_name": "AI DEGISTIRDI",
    "sector": "AI DEGISTIRDI",
    "brand_summary": "AI DEGISTIRDI",
    "products": ["AI urunu"],
    "services": ["AI hizmeti"],
    "target_audience": "AI kitlesi",
    "use_cases": ["AI senaryosu"],
    "problems_solved": ["AI problemi"],
    "brand_terms": ["ai-marka"],
    "exclude_themes": ["AI dislamasi"],
}

REVISION_MARKER = "# MEVCUT PROFIL"
SUGGESTION_MARKER = "# ONAYLI MARKA PROFILI"


class _OverwritingAI:
    """Revizyon cagrisinda profili tamamen ezmeye calisir."""

    def __init__(self, revision_response=None):
        self.collector = None
        self.prompts = []
        self._revision_response = (
            revision_response
            if revision_response is not None
            else json.dumps(AI_OVERWRITE, ensure_ascii=False)
        )

    def complete_json(self, prompt, max_tokens=None, temperature=0.3,
                      response_schema=None):
        self.prompts.append(prompt)
        if REVISION_MARKER in prompt:
            return self._revision_response
        if SUGGESTION_MARKER in prompt:
            return json.dumps({"suggested_keywords": ["dijital ajans", "seo ajansi"]})
        raise AssertionError(f"beklenmeyen prompt: {prompt[:160]!r}")


class _BrokenRevisionAI(_OverwritingAI):
    """Revizyon patlar (fail-open); keyword onerisi calismaya devam eder."""

    def complete_json(self, prompt, max_tokens=None, temperature=0.3,
                      response_schema=None):
        if REVISION_MARKER in prompt:
            raise RuntimeError("AI down")
        return super().complete_json(
            prompt, max_tokens=max_tokens, temperature=temperature,
            response_schema=response_schema,
        )


@pytest.fixture
def install_fake_ai(monkeypatch):
    """get_ai_service + UsageCollector sahtelenir; DB telemetrisi testin disinda."""

    def _install(ai):
        monkeypatch.setattr(
            "app.generators.ai_service.get_ai_service", lambda **kwargs: ai
        )
        monkeypatch.setattr(
            brand_profile_api, "UsageCollector", lambda **kwargs: None
        )
        return ai

    return _install


@pytest.fixture
def forbid_profile_write(monkeypatch):
    """Talep uzerine kurulan guard: `apply_profile_data_update` cagrilamaz.

    TALEP UZERINE, cunku ayni fonksiyon kullanicinin MESRU duzenlemesini de
    yaziyor (`approve_workspace_profile`, brand_profile.py:772). Guard yalnizca
    keyword yeniden uretim cagrisini sarmalamali — global kurulursa kullanici
    duzenlemesini de bloke eder (P0.1 birinci savunma hatti).
    """

    def _arm():
        def _boom(*args, **kwargs):
            raise AssertionError(
                "Keyword yeniden uretimi profile_data'ya yazamaz "
                "(plan_marka_profili_sadakati.md P0.1)"
            )

        monkeypatch.setattr(brand_profile_api, "apply_profile_data_update", _boom)

    return _arm


def _seeded_workspace(make_workspace, **overrides):
    defaults = dict(
        name="Sadakat WS",
        onboarding_flow="profile_first",
        status="keywords_review",
        preliminary_info="mutlaka teknik SEO olsun",
        excluded_info="donanim satisi",
        profile_data=dict(FULL_PROFILE),
        # NOT: gercekci uzunlukta olmali — cache okuma kapisi cok kisa icerigi
        # (JS kabugu / hata sayfasi gostergesi) reddedip yeniden crawl tetikler.
        crawl_content_cache=(
            "Digitus dijital pazarlama ajansi site icerigi. Web tasarim, e-ticaret "
            "sistemleri ve ozel yazilim cozumleri sunuyoruz. Arama motoru "
            "optimizasyonu, sosyal medya yonetimi ve Google Ads yonetimi "
            "hizmetlerimiz bulunmaktadir. Kurumsal firmalar, girisimler ve "
            "KOBI'ler icin dijital donusum surecleri yonetiyoruz."
        ),
    )
    defaults.update(overrides)
    return make_workspace(**defaults)


def test_keyword_regeneration_never_mutates_profile_data(
    db_session, make_workspace, install_fake_ai, forbid_profile_write,
):
    """Ana invariant: AI tum alanlari farkli dondurse bile profil aynen kalir."""
    ai = install_fake_ai(_OverwritingAI())
    workspace = _seeded_workspace(make_workspace)
    before_anchor = int(workspace.anchor_version)
    before_policy = int(workspace.policy_version)

    forbid_profile_write()
    brand_profile_api._run_keyword_suggestion_from_profile(workspace.id)

    db_session.expire_all()
    db_session.refresh(workspace)

    # 1) profile_data kanonik olarak birebir ayni (alan alan degil, dict esitligi)
    assert workspace.profile_data == FULL_PROFILE
    # 2) surumler artmaz -> invalidate_workspace_outputs tetiklenmez
    assert int(workspace.anchor_version) == before_anchor
    assert int(workspace.policy_version) == before_policy
    # 3) asil islev calisir
    assert workspace.status == "keywords_review"
    assert workspace.suggested_keywords == ["dijital ajans", "seo ajansi"]
    # 4) revizyon cagrisi GERCEKTEN yapildi (test bosluga bakmiyor)
    assert any(REVISION_MARKER in p for p in ai.prompts)


def test_policy_specificity_survives_regeneration(
    db_session, make_workspace, install_fake_ai, forbid_profile_write,
):
    """AI semasinda olmayan teknik anahtar dusmez.

    Dusseydi `_profile_theme_sets` (review.py) degisir, policy_version artar ve
    tum workspace ciktilari bayatlardi.
    """
    install_fake_ai(_OverwritingAI())
    workspace = _seeded_workspace(make_workspace)

    forbid_profile_write()
    brand_profile_api._run_keyword_suggestion_from_profile(workspace.id)

    db_session.expire_all()
    db_session.refresh(workspace)
    assert workspace.profile_data["policy_specificity"] == {
        "core_terms": ["seo"], "schema_version": 1,
    }


def test_user_edit_then_regenerate_keeps_user_values(
    client, db_session, make_workspace, install_fake_ai, forbid_profile_write,
):
    """KeywordAnchorReview akisi: duzenle (rerun=false) -> yeniden uret (rerun=true)."""
    install_fake_ai(_OverwritingAI())
    workspace = _seeded_workspace(make_workspace)

    edited = client.put(
        f"/api/v1/brand-profile/workspaces/{workspace.id}/profile/approve",
        json={
            "profile_data": {"products": ["Kullanicinin elle yazdigi urun"]},
            "rerun_keywords": False,
        },
    )
    assert edited.status_code == 200, edited.text

    db_session.expire_all()
    db_session.refresh(workspace)
    after_edit = dict(workspace.profile_data)
    assert after_edit["products"] == ["Kullanicinin elle yazdigi urun"]

    # Guard duzenlemeden SONRA kurulur: kullanici yazimi mesru, AI yazimi degil.
    forbid_profile_write()
    brand_profile_api._run_keyword_suggestion_from_profile(workspace.id)

    db_session.expire_all()
    db_session.refresh(workspace)
    assert workspace.profile_data == after_edit


def test_regeneration_fail_open_keeps_profile_and_still_suggests(
    db_session, make_workspace, install_fake_ai, forbid_profile_write,
):
    """Revizyon AI'i patlarsa profil degismez ve akis kirilmaz."""
    install_fake_ai(_BrokenRevisionAI())
    workspace = _seeded_workspace(make_workspace)

    forbid_profile_write()
    brand_profile_api._run_keyword_suggestion_from_profile(workspace.id)

    db_session.expire_all()
    db_session.refresh(workspace)
    assert workspace.profile_data == FULL_PROFILE
    assert workspace.status == "keywords_review"
    assert workspace.suggested_keywords == ["dijital ajans", "seo ajansi"]


# ── Ikinci savunma hatti: revize edicinin kendisi ────────────────────────────


def test_revision_output_preserves_locked_and_unknown_fields():
    """`revise_profile_with_requirements` allowlist disi hicbir alani tasimaz.

    Cagiran artik yazmasa da fonksiyon ileride baska bir yoldan cagrilabilir.
    """
    from app.core.site_analyzer.profile_extractor import ProfileExtractor

    revised = ProfileExtractor(_OverwritingAI()).revise_profile_with_requirements(
        dict(FULL_PROFILE), must_have_info="mutlaka teknik SEO olsun",
    )

    # Kullaniciya kilitli alanlar AI'a da kilitli
    assert revised["company_name"] == "Digitus"
    assert revised["sector"] == "Dijital pazarlama ajansi"
    # Kullanicinin duzenledigi alan
    assert revised["target_audience"] == "Kullanicinin yazdigi kitle"
    # Sistemce yonetilen tema listeleri
    assert revised["exclude_themes"] == ["donanim satisi"]
    assert revised["protected_themes"] == ["e-ticaret"]
    # AI semasinda HIC olmayan teknik anahtar
    assert revised["policy_specificity"] == {"core_terms": ["seo"], "schema_version": 1}
    # Izinli alanlarda AI katkisi uygulanir (fonksiyonun asil islevi)
    assert revised["products"] == ["AI urunu"]
    assert revised["services"] == ["AI hizmeti"]
    # anchor_texts prompt'a/ciktiya sizmaz — cagiran yeniden turetir
    assert "anchor_texts" not in revised
