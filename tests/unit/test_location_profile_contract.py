# -*- coding: utf-8 -*-
"""Lokasyon alanlarının profil sözleşmesi — plan_v3_lokasyon_filtresi.md §8.2.

Sanitizasyon fonksiyonları saf olduğu için DB'siz test edilir. İki giriş
yüzeyi (ilk onay / sonraki düzenleme) AYNI sonucu üretmelidir.
"""
import pytest
from fastapi import HTTPException

from app.api.v1.brand_profile import (
    _apply_profile_review_data,
    _sanitize_profile_data,
)
from app.core.policy.location_policy import (
    MODE_EXCLUDE_ALL,
    MODE_FOCUS_ONLY,
    MODE_NONE,
    enforcement_identity,
    policy_snapshot,
    read_settings,
)

BOTH_SURFACES = pytest.mark.parametrize(
    "sanitize", [_apply_profile_review_data, _sanitize_profile_data],
    ids=["ilk_onay", "sonraki_duzenleme"])


# ── Geriye uyumluluk ────────────────────────────────────────────────────
def test_legacy_profile_without_fields_reads_as_none_and_empty():
    mode, focus, exempt = read_settings({"company_name": "X"})
    assert (mode, focus, exempt) == (MODE_NONE, [], [])


def test_legacy_profile_identity_is_inert():
    assert enforcement_identity({}) == (MODE_NONE,)
    assert enforcement_identity(None) == (MODE_NONE,)


@BOTH_SURFACES
def test_missing_incoming_fields_preserve_existing(sanitize):
    existing = {"company_name": "X", "location_filter_mode": MODE_EXCLUDE_ALL,
                "focus_cities": ["İzmir"], "location_exempt_terms": ["x y"]}
    out = sanitize({}, existing)
    assert out["location_filter_mode"] == MODE_EXCLUDE_ALL
    assert out["focus_cities"] == ["İzmir"]
    assert out["location_exempt_terms"] == ["x y"]


@BOTH_SURFACES
def test_new_profile_defaults_to_none(sanitize):
    out = sanitize({}, {})
    assert out["location_filter_mode"] == MODE_NONE
    assert out["focus_cities"] == []


# ── Doğrulama ───────────────────────────────────────────────────────────
@BOTH_SURFACES
@pytest.mark.parametrize("bad", ["hepsi", "true", "1", "EXCLUDE", "hype"])
def test_invalid_mode_is_rejected_with_400(sanitize, bad):
    with pytest.raises(HTTPException) as exc:
        sanitize({"location_filter_mode": bad}, {})
    assert exc.value.status_code == 400
    assert exc.value.detail["code"] == "INVALID_LOCATION_FILTER_MODE"


@BOTH_SURFACES
def test_unsupported_city_is_not_stored_silently(sanitize):
    with pytest.raises(HTTPException) as exc:
        sanitize({"location_filter_mode": MODE_EXCLUDE_ALL,
                  "focus_cities": ["Kadıköy"]}, {})
    assert exc.value.status_code == 400
    assert exc.value.detail["code"] == "UNSUPPORTED_FOCUS_CITY"


@BOTH_SURFACES
def test_focus_only_requires_at_least_one_city(sanitize):
    with pytest.raises(HTTPException) as exc:
        sanitize({"location_filter_mode": MODE_FOCUS_ONLY,
                  "focus_cities": []}, {})
    assert exc.value.detail["code"] == "FOCUS_CITIES_REQUIRED"


@BOTH_SURFACES
def test_more_than_twenty_focus_cities_is_rejected(sanitize):
    from app.core.policy.location_policy import TURKISH_PROVINCES
    with pytest.raises(HTTPException) as exc:
        sanitize({"location_filter_mode": MODE_FOCUS_ONLY,
                  "focus_cities": list(TURKISH_PROVINCES[:21])}, {})
    assert exc.value.detail["code"] == "TOO_MANY_FOCUS_CITIES"


@BOTH_SURFACES
def test_explicit_null_mode_is_rejected_not_silently_none(sanitize):
    """Alan açıkça gönderildiyse `null` SESSİZCE `none` olmaz."""
    with pytest.raises(HTTPException) as exc:
        sanitize({"location_filter_mode": None}, {})
    assert exc.value.detail["code"] == "INVALID_LOCATION_FILTER_MODE"


@BOTH_SURFACES
@pytest.mark.parametrize("field", ["focus_cities", "location_exempt_terms"])
@pytest.mark.parametrize("bad", ["İstanbul", 5, True, {"a": 1}, None])
def test_non_list_location_field_is_rejected(sanitize, field, bad):
    """`"İstanbul"` sessizce tek elemanlı listeye DÖNÜŞMEZ."""
    with pytest.raises(HTTPException) as exc:
        sanitize({field: bad}, {})
    assert exc.value.status_code == 400
    assert exc.value.detail["code"] == "INVALID_LOCATION_LIST"


@BOTH_SURFACES
def test_exempt_terms_are_deduped_by_normalized_key(sanitize):
    """`Gaziantep Fıstığı` ile `gaziantep fistigi` AYNI terimdir."""
    out = sanitize({"location_filter_mode": MODE_EXCLUDE_ALL,
                    "location_exempt_terms": ["Gaziantep Fıstığı",
                                              "gaziantep fistigi",
                                              "GAZİANTEP FISTIĞI"]}, {})
    assert out["location_exempt_terms"] == ["Gaziantep Fıstığı"]


@BOTH_SURFACES
def test_exempt_term_length_limit_is_enforced(sanitize):
    from app.core.policy.location_policy import MAX_EXEMPT_TERM_LENGTH
    with pytest.raises(HTTPException) as exc:
        sanitize({"location_filter_mode": MODE_EXCLUDE_ALL,
                  "location_exempt_terms": ["a" * (MAX_EXEMPT_TERM_LENGTH + 1)]},
                 {})
    assert exc.value.detail["code"] == "EXEMPT_TERM_TOO_LONG"


@BOTH_SURFACES
def test_exempt_term_count_limit_is_enforced(sanitize):
    from app.core.policy.location_policy import MAX_EXEMPT_TERMS
    terms = [f"terim {i}" for i in range(MAX_EXEMPT_TERMS + 1)]
    with pytest.raises(HTTPException) as exc:
        sanitize({"location_filter_mode": MODE_EXCLUDE_ALL,
                  "location_exempt_terms": terms}, {})
    assert exc.value.detail["code"] == "TOO_MANY_EXEMPT_TERMS"


def test_lexicon_identity_is_part_of_enforcement_fingerprint():
    """Sürüm etiketi yanlışlıkla artırılmazsa bile içerik değişimi mührü bozar."""
    import app.core.policy.location_policy as lp

    active = {"location_filter_mode": MODE_EXCLUDE_ALL}
    before = lp.enforcement_identity(active)
    assert lp.lexicon_fingerprint() in str(before)

    lp.lexicon_fingerprint.cache_clear()
    original = lp.CITY_LEXICON.copy()
    try:
        lp.CITY_LEXICON["yenisehirvaryanti"] = "Ankara"   # sürüm AYNI kaldı
        assert lp.enforcement_identity(active) != before
    finally:
        lp.CITY_LEXICON.clear()
        lp.CITY_LEXICON.update(original)
        lp.lexicon_fingerprint.cache_clear()
    assert lp.enforcement_identity(active) == before


# ── Kanonikleştirme ─────────────────────────────────────────────────────
@BOTH_SURFACES
def test_cities_are_canonicalized_and_deduped(sanitize):
    out = sanitize({"location_filter_mode": MODE_FOCUS_ONLY,
                    "focus_cities": ["istanbul", "İSTANBUL", "izmir", "  "]},
                   {})
    assert out["focus_cities"] == ["İstanbul", "İzmir"]


@BOTH_SURFACES
def test_exempt_terms_drop_blanks(sanitize):
    out = sanitize({"location_filter_mode": MODE_EXCLUDE_ALL,
                    "location_exempt_terms": ["gaziantep fıstığı", "  ", ""]},
                   {})
    assert out["location_exempt_terms"] == ["gaziantep fıstığı"]


def test_both_surfaces_agree():
    incoming = {"location_filter_mode": MODE_FOCUS_ONLY,
                "focus_cities": ["ISTANBUL"],
                "location_exempt_terms": ["batman filmi"]}
    a = _apply_profile_review_data(dict(incoming), {})
    b = _sanitize_profile_data(dict(incoming), {})
    for field in ("location_filter_mode", "focus_cities",
                  "location_exempt_terms"):
        assert a[field] == b[field]


# ── Anchor sızıntısı ────────────────────────────────────────────────────
@BOTH_SURFACES
def test_focus_cities_never_leak_into_anchor_texts(sanitize):
    """Coğrafi tercih, konu ilgisiyle KARIŞTIRILMAZ (plan §4)."""
    out = sanitize({"products": ["implant"],
                    "location_filter_mode": MODE_FOCUS_ONLY,
                    "focus_cities": ["İstanbul"],
                    "location_exempt_terms": ["batman filmi"]}, {})
    blob = " ".join(out.get("anchor_texts") or []).lower()
    assert "istanbul" not in blob
    assert "batman" not in blob


# ── Enforcement kimliği / stale semantiği ───────────────────────────────
def test_mode_change_changes_identity():
    base = {"location_filter_mode": MODE_NONE}
    changed = {"location_filter_mode": MODE_EXCLUDE_ALL}
    assert enforcement_identity(base) != enforcement_identity(changed)


def test_exempt_change_changes_identity_in_active_modes():
    a = {"location_filter_mode": MODE_EXCLUDE_ALL, "location_exempt_terms": []}
    b = {"location_filter_mode": MODE_EXCLUDE_ALL,
         "location_exempt_terms": ["gaziantep fıstığı"]}
    assert enforcement_identity(a) != enforcement_identity(b)


@pytest.mark.parametrize("mode", [MODE_NONE, MODE_EXCLUDE_ALL])
def test_focus_cities_do_not_affect_identity_outside_focus_only(mode):
    """Sonucu etkilemeyen metadata değişikliği havuzu BAYATLATMAZ."""
    a = {"location_filter_mode": mode, "focus_cities": []}
    b = {"location_filter_mode": mode, "focus_cities": ["İstanbul", "İzmir"]}
    assert enforcement_identity(a) == enforcement_identity(b)


def test_focus_cities_affect_identity_in_focus_only():
    a = {"location_filter_mode": MODE_FOCUS_ONLY, "focus_cities": ["İstanbul"]}
    b = {"location_filter_mode": MODE_FOCUS_ONLY, "focus_cities": ["Ankara"]}
    assert enforcement_identity(a) != enforcement_identity(b)


def test_identity_is_stable_for_equivalent_canonical_input():
    """Aynı kanonik veri tekrar kaydedilirse gereksiz sürüm artışı OLMAZ."""
    a = {"location_filter_mode": MODE_FOCUS_ONLY,
         "focus_cities": ["İstanbul", "İzmir"],
         "location_exempt_terms": ["Gaziantep Fıstığı"]}
    b = {"location_filter_mode": MODE_FOCUS_ONLY,
         "focus_cities": ["izmir", "ISTANBUL"],
         "location_exempt_terms": ["gaziantep fistigi"]}
    assert enforcement_identity(a) == enforcement_identity(b)


# ── Manifest snapshot ───────────────────────────────────────────────────
def test_policy_snapshot_carries_focus_cities_for_audit_in_every_mode():
    snap = policy_snapshot({"location_filter_mode": MODE_EXCLUDE_ALL,
                            "focus_cities": ["istanbul"]})
    assert snap["focus_cities"] == ["İstanbul"]          # denetim için var
    # ...ama fingerprint'i etkilemez (identity testleriyle aynı sözleşme)
    other = policy_snapshot({"location_filter_mode": MODE_EXCLUDE_ALL,
                             "focus_cities": []})
    assert snap["enforcement_fingerprint"] == other["enforcement_fingerprint"]


def test_policy_snapshot_is_deterministic_and_carries_lexicon_identity():
    data = {"location_filter_mode": MODE_FOCUS_ONLY,
            "focus_cities": ["İstanbul"]}
    assert policy_snapshot(data) == policy_snapshot(data)
    assert policy_snapshot(data)["city_lexicon_version"] == "tr-provinces-v1"
    assert len(policy_snapshot(data)["city_lexicon_sha256"]) == 64
