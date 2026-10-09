"""
Motor v3 hard-cap config dogrulayicisi testleri
(`app/config.py::Settings.check_engine_v3_hard_cap`).

Kural: `ENGINE_V3_HARD_CAP_USD` sifir, negatif, sonlu-olmayan (NaN/inf) veya
screening payi (0.000001) ayrildiktan sonra downstream'e pozitif birakmayan
(<= 0.000001) degerlerde `Settings` kurulumunda hata firlatmali; normal
pozitif deger sorunsuz gecmeli.

Diger zorunlu env degiskenlerini bozmamak icin `Settings(...)` dogrudan
cagirilir (mevcut ortamdaki .env/docker env degerlerini miras alir) —
`tests/unit/test_screening_context_identity.py::TestConfigAuthority` ile
AYNI kalip.
"""
from __future__ import annotations

import pytest

from app.config import Settings


@pytest.mark.parametrize("bad_value", [0, 0.0, -1, -0.5])
def test_engine_v3_hard_cap_zero_or_negative_raises(bad_value):
    with pytest.raises(ValueError, match="ENGINE_V3_HARD_CAP_USD"):
        Settings(ENGINE_V3_HARD_CAP_USD=bad_value)


def test_engine_v3_hard_cap_nan_raises():
    with pytest.raises(ValueError, match="ENGINE_V3_HARD_CAP_USD"):
        Settings(ENGINE_V3_HARD_CAP_USD=float("nan"))


@pytest.mark.parametrize("inf_value", [float("inf"), float("-inf")])
def test_engine_v3_hard_cap_infinite_raises(inf_value):
    with pytest.raises(ValueError, match="ENGINE_V3_HARD_CAP_USD"):
        Settings(ENGINE_V3_HARD_CAP_USD=inf_value)


def test_engine_v3_hard_cap_too_small_for_screening_share_raises():
    """0.000001 (screening payi) ile ESIT ya da altindaki deger downstream'e
    pozitif birakmaz — fail-closed reddedilmeli."""
    with pytest.raises(ValueError, match="ENGINE_V3_HARD_CAP_USD"):
        Settings(ENGINE_V3_HARD_CAP_USD=0.000001)


def test_engine_v3_hard_cap_valid_positive_value_boots():
    s = Settings(ENGINE_V3_HARD_CAP_USD=5.0)
    assert s.ENGINE_V3_HARD_CAP_USD == 5.0


def test_engine_v3_hard_cap_small_but_valid_value_boots():
    s = Settings(ENGINE_V3_HARD_CAP_USD=0.01)
    assert s.ENGINE_V3_HARD_CAP_USD == 0.01


def test_all_active_production_models_use_gemini_38_low():
    from app.core.engine.ads.runner import ADS_MODEL, ADS_THINKING
    from app.core.engine.family.runner import FAMILY_MODEL, FAMILY_THINKING
    from app.core.engine.seo.runner import SEO_MODEL, SEO_THINKING
    from app.core.engine.social.runner import SOCIAL_MODEL, SOCIAL_THINKING

    configured = Settings()
    assert configured.GEMINI_MODEL == "gemini-3.8-flash"
    assert configured.COMPETITOR_DISCOVERY_MODEL == "gemini-3.8-flash"
    assert configured.GEMINI_THINKING_LEVEL == "low"
    assert {
        (ADS_MODEL, ADS_THINKING),
        (FAMILY_MODEL, FAMILY_THINKING),
        (SEO_MODEL, SEO_THINKING),
        (SOCIAL_MODEL, SOCIAL_THINKING),
    } == {("gemini-3.8-flash", "low")}
